"""All-public-sample development harness; expected fields never enter decisions."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from pathlib import Path
from types import MappingProxyType

from buy_wait.data import Dataset, DataError, OUTPUT_FIELDS, project_request, read_table
from evaluation.metrics import compare_predictions


def load_sample_fixtures(root):
    rows, digest = read_table(root, "sample_requests.csv", sample_fixture=True)
    requests = tuple(project_request(row) for row in rows)
    if len({r["request_id"] for r in requests}) != len(requests):
        raise DataError("duplicate public fixture ID")
    expected = tuple(MappingProxyType({f: row[f] for f in OUTPUT_FIELDS}) for row in rows)
    return requests, expected, digest


def evaluate_samples(dataset_root, decide, *, audit=None):
    """Execute an injected committed decision boundary, without exposing labels.

    The integration caller supplies the eventual core/evidence/planning callable.
    Test doubles are permitted only in tests; there is no fallback classifier,
    sample-label lookup, dynamic plugin import or network path in this harness.
    """
    data = Dataset.load(dataset_root)
    requests, expected, fixture_hash = load_sample_fixtures(dataset_root)
    predictions, audits, failures = [], {}, []
    for request in requests:
        try:
            context = data.context_for(request)
            prediction = dict(decide(context))
            predictions.append(prediction)
            if audit is not None:
                audits[request["request_id"]] = audit(context, prediction)
        except Exception as exc:
            # Deliberately do not echo exception messages/model payloads/secrets.
            failures.append({"request_id": request["request_id"], "code": "DECISION_OR_AUDIT_FAILED",
                             "exception_type": type(exc).__name__})
    currencies = {r["request_id"]: data.profiles[r["user_id"]]["home_currency"] for r in requests}
    result = compare_predictions(requests, expected, predictions, currencies, audits=audits)
    result["failures"] = failures
    result["predictions"] = predictions
    result["source_hashes"] = {**data.source_hashes, "sample_requests.csv": fixture_hash}
    return result


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def persist_run(result, run_root, run_id, *, dataset_root, configuration):
    """Create an immutable development run directory, never overwrite baseline.

    `manifest.json` is written last. An interrupted directory without this marker
    remains an incomplete attempt; reusing its ID is refused, not restored.
    """
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", run_id):
        raise DataError("invalid run ID")
    directory = Path(run_root).resolve() / run_id
    if directory.is_relative_to(Path(dataset_root).resolve()):
        raise DataError("run artifacts cannot modify dataset inputs")
    directory.parent.mkdir(parents=True, exist_ok=True)
    directory.mkdir()  # exclusive; a prior or concurrent run must not be replaced
    artifacts = {
        "metrics.json": _json(result["metrics"]),
        "per_request.json": _json(result["per_request"]),
        "predictions.json": _json(result["predictions"]),
        "errors.jsonl": "".join(json.dumps(e, sort_keys=True) + "\n" for e in result["errors"]),
        "failures.json": _json(result.get("failures", [])),
        "summary.md": (
            "# Public development fixtures\n\n"
            f"Requests: {result['metrics']['denominator_requests']}\n\n"
            "See metrics.json for separate amount/date/status/method/schedule/action dimensions. "
            "Safety and explanation grounding require the trace audit adapter; CSV agreement alone is not proof. "
            "Repeated tuning is development evidence, not independent generalization.\n"
        ),
    }
    for name, text in artifacts.items():
        with (directory / name).open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
    with (directory / "predictions.csv").open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=OUTPUT_FIELDS, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(result["predictions"])
        stream.flush()
        os.fsync(stream.fileno())
    manifest = {
        "run_id": run_id, "kind": "public-development", "status": "complete",
        "configuration": configuration, "source_hashes": result["source_hashes"],
        "artifact_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(directory.iterdir())},
    }
    with (directory / "manifest.json").open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(_json(manifest))
        stream.flush()
        os.fsync(stream.fileno())
    return directory
