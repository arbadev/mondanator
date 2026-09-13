"""Inspect fixtures, run fresh-cache input-only preflight, or compare a dev CSV."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from buy_wait.data import Dataset, DataError, OUTPUT_FIELDS
from evaluation.metrics import compare_predictions
from evaluation.samples import load_sample_fixtures, persist_run


def read_predictions(path):
    path = Path(path)
    if path.suffix != ".csv" or path.is_symlink() or not path.is_file():
        raise DataError("predictions must be a regular non-symlink CSV")
    if path.stat().st_size > 64 * 1024 * 1024:
        raise DataError("prediction CSV exceeds size limit")
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        if tuple(reader.fieldnames or ()) != OUTPUT_FIELDS:
            raise DataError("unexpected prediction header")
        result = []
        for row in reader:
            if None in row or any(v is None for v in row.values()):
                raise DataError("wrong prediction field count")
            result.append(row)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path(__file__).resolve().parents[2] / "dataset")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--predictions", type=Path, help="existing public-sample predictions; never evaluation labels")
    mode.add_argument("--preflight-public", action="store_true", help="fresh-cache public INPUTS only; no accuracy, seeding, live call or prediction CSV")
    parser.add_argument("--max-candidates", type=int, default=64, help="preflight diagnostic cap per phase; incompleteness remains explicit")
    parser.add_argument("--run-root", type=Path, default=Path("evaluation/runs"))
    parser.add_argument("--run-id", help="new immutable development run ID, required with predictions")
    args = parser.parse_args(argv)
    if (args.predictions or args.preflight_public) and not args.run_id:
        parser.error("--run-id is required with --predictions or --preflight-public")
    try:
        if args.preflight_public:
            from evaluation.cached_run import run_public_preflight
            result = run_public_preflight(args.dataset, run_root=args.run_root, run_id=args.run_id, max_candidates=args.max_candidates)
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0 if result["preflight_complete"] else 3
        data = Dataset.load(args.dataset)
        requests, expected, sample_hash = load_sample_fixtures(args.dataset)
        for request in requests:
            data.context_for(request)
        if not args.predictions:
            print(json.dumps({"mode": "fixture-inspection-only", "public_samples": len(requests),
                              "expected_fields_separate": True, "sample_sha256": sample_hash,
                              "inference_run": False, "metrics_measured": False}, sort_keys=True))
            return 0
        predictions = read_predictions(args.predictions)
        currencies = {r["request_id"]: data.profiles[r["user_id"]]["home_currency"] for r in requests}
        result = compare_predictions(requests, expected, predictions, currencies)
        result.update(predictions=predictions, source_hashes={**data.source_hashes, "sample_requests.csv": sample_hash})
        directory = persist_run(result, args.run_root, args.run_id, dataset_root=args.dataset, configuration={
            "mode": "compare-existing-public-predictions", "model_calls": 0,
            "prediction_sha256": hashlib.sha256(args.predictions.read_bytes()).hexdigest(),
            "financial_audit": "not performed; requires committed core/planning adapter",
        })
        print(json.dumps({"run_directory": str(directory), "metrics": result["metrics"]}, sort_keys=True, indent=2))
        return 0
    except (ValueError, OSError, UnicodeError, csv.Error) as exc:
        parser.exit(2, f"evaluation failed: {str(exc) if isinstance(exc, DataError) else type(exc).__name__}\n")


if __name__ == "__main__":
    raise SystemExit(main())
