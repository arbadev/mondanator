"""Read-only inspection or explicit cache-only predictions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from buy_wait.data import Dataset, DataError, load_requests


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path(__file__).resolve().parents[1] / "dataset")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-inputs", action="store_true", help="validate CSV/index/request joins without reading images, credentials or labels")
    mode.add_argument("--predict-cached", action="store_true", help="development-only predictions from validated cached evidence; no live client or final certification")
    mode.add_argument("--extraction-mode", choices=("cache-only",),
                      help="explicit extraction mode name; only cache-only is supported")
    parser.add_argument("--cache", type=Path, help="explicit cache directory outside dataset, required for cache-only prediction modes")
    parser.add_argument("--run-root", type=Path, default=Path("evaluation/runs"))
    parser.add_argument("--run-id", help="new immutable development run ID, required for cache-only prediction execution")
    parser.add_argument("--max-candidates", type=int, help="diagnostic cap; incomplete search cannot emit a prediction CSV")
    parser.add_argument("--output", type=Path,
                        help="optional external development CSV path for cache-only execution; never final certification")
    args = parser.parse_args(argv)
    using_cached = bool(args.predict_cached or args.extraction_mode)
    if using_cached and (not args.run_id or args.cache is None):
        parser.error("cache-only execution requires --run-id and --cache; there is no live/final-run mode")
    try:
        if using_cached:
            from evaluation.cached_run import run_cached_predictions
            kwargs = {
                "cache_root": args.cache,
                "run_root": args.run_root,
                "run_id": args.run_id,
                "max_candidates": args.max_candidates,
            }
            if args.output is not None:
                kwargs["output_path"] = args.output
            result = run_cached_predictions(args.dataset, **kwargs)
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0 if result["output_written"] else 3
        data = Dataset.load(args.dataset)
        requests, request_hash = load_requests(args.dataset)
        for request in requests:
            data.financial_input_for(request)
            data.planning_context_for(request)
        print(json.dumps({
            "mode": "input-validation-only", "requests": len(requests),
            "profiles": len(data.profiles), "events": len(data.events),
            "blank_event_amounts": sum(not r["amount"] for r in data.events.values()),
            "source_hashes": {**data.source_hashes, "requests.csv": request_hash},
            "financial_safety": "not evaluated", "output_written": False, "model_calls": 0,
        }, sort_keys=True, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        # Only owned DataError strings are field/record-only; other errors may
        # contain untrusted source text, provider payloads or sensitive paths.
        parser.exit(2, f"offline run failed: {str(exc) if isinstance(exc, DataError) else type(exc).__name__}\n")


if __name__ == "__main__":
    raise SystemExit(main())
