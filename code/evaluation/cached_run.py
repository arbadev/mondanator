"""Immutable cache-only development prediction runs. Never a final release run."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
from importlib.metadata import version
from pathlib import Path

from buy_wait import contracts as c
from buy_wait.data import Dataset, DataError, load_requests, project_request, read_table
from buy_wait.evidence import ExtractionCache
from buy_wait.output import write_output
from buy_wait.runner import decide_cached, trace_wire
from evaluation.audits import audit_decision
from evaluation.usage import JsonlUsageSink, aggregate_usage, read_usage_events, render_usage_report
from collections import Counter


def _bytes(path, payload):
    with Path(path).open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _json(path, value):
    _bytes(path, (json.dumps(value, sort_keys=True, ensure_ascii=True, indent=2, allow_nan=False) + "\n").encode())


def _code_hashes():
    code = Path(__file__).resolve().parents[1]
    files = [code / "main.py"]
    for package in (code / "buy_wait", code / "evaluation"):
        for path in package.rglob("*"):
            if path.is_symlink():
                raise DataError("runtime source tree must not contain symlinks")
            if path.suffix in (".py", ".txt") and "__pycache__" not in path.parts:
                files.append(path)
    result = {}
    for path in sorted(files):
        if path.is_symlink() or not path.is_file():
            raise DataError("runtime source must be a regular non-symlink file")
        result[path.relative_to(code.parent).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def run_cached_predictions(dataset_root, *, cache_root, run_root, run_id, max_candidates=None):
    """Write per-request traces, canonical usage and an all-or-nothing dev CSV.

    Existing run IDs, dataset destinations and overlapping cache/artifact trees
    are refused. Any unresolved/failed request or incomplete accounting prevents
    a prediction CSV. All partial traces/receipts remain, with an explicit failed
    manifest; an interrupted attempt with no manifest is not a completed run.
    No public expected answers, credential lookup or network dispatch occurs.
    """
    return _run_cached(dataset_root, cache_root=cache_root, run_root=run_root, run_id=run_id,
                       max_candidates=max_candidates, public_preflight=False)


def run_public_preflight(dataset_root, *, run_root, run_id, max_candidates=64):
    """Input-only public fixture diagnostics; exclusive EMPTY cache, never CSV.

    No expected-answer tuple, accuracy metric, cache seeding or live path exists.
    The cap is diagnostic: incomplete search stays visible, not certified.
    """
    return _run_cached(dataset_root, cache_root=Path(run_root) / (str(run_id) + ".preflight-cache"),
                       run_root=run_root, run_id=run_id, max_candidates=max_candidates, public_preflight=True)


def _run_cached(dataset_root, *, cache_root, run_root, run_id, max_candidates, public_preflight):
    if not isinstance(run_id, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", run_id) is None:
        raise DataError("invalid development run ID")
    if max_candidates is not None and (type(max_candidates) is not int or max_candidates < 1):
        raise DataError("candidate diagnostic cap must be a positive integer")
    dataset_root, cache_root, run_root = (Path(p).resolve() for p in (dataset_root, cache_root, run_root))
    directory = run_root / run_id
    if directory.is_relative_to(dataset_root) or cache_root.is_relative_to(dataset_root):
        raise DataError("cache/run artifacts cannot modify dataset inputs")
    if directory.is_relative_to(cache_root) or cache_root.is_relative_to(directory):
        raise DataError("cache and run artifact directories must be separate")
    if public_preflight and cache_root.exists():
        raise DataError("public preflight requires a new exclusive empty cache")
    data = Dataset.load(dataset_root)
    request_file = "sample_requests.csv" if public_preflight else "requests.csv"
    if public_preflight:
        raw_rows, request_hash = read_table(dataset_root, request_file, sample_fixture=True)
        requests = tuple(project_request(row) for row in raw_rows)
        del raw_rows  # expected fields are never retained or passed into decisions
    else:
        requests, request_hash = load_requests(dataset_root)
    if len({r["request_id"] for r in requests}) != len(requests):
        raise DataError("duplicate input request identity")
    if not requests:
        raise DataError("development prediction run needs at least one request")
    for request in requests:
        data.financial_input_for(request)
        data.planning_context_for(request)
    source_hashes = {**data.source_hashes, request_file: request_hash}
    code_hashes = _code_hashes()
    run_root.mkdir(parents=True, exist_ok=True)
    directory.mkdir(mode=0o700)  # exclusive; no overwrite, reset or recovery
    (directory / "traces").mkdir()
    (directory / "audits").mkdir()
    usage = JsonlUsageSink(directory / "usage_events.jsonl", run_id=run_id)
    if public_preflight:
        cache_root.mkdir(mode=0o700)  # exclusive; never clear/reuse/seed a cache
    cache = ExtractionCache(cache_root)
    rows, failures, index, origins = [], [], [], []
    cache_references, diagnostics = [], []
    for ordinal, request in enumerate(requests, 1):
        relative = f"traces/{ordinal:06d}.json"
        try:
            trace = decide_cached(data, request, dataset_root=dataset_root, cache=cache,
                                  usage=usage, run_id=run_id, max_candidates=max_candidates)
            for source in trace["evidence"]["sources"]:
                origins.extend(source["origin_usage"])
                cache_references.append({key: source[key] for key in ("source_id", "cache_key", "content_sha256", "row_sha256")})
            _json(directory / relative, trace_wire(trace))
            audit = audit_decision(data, request, trace, dataset_root=dataset_root, max_candidates=max_candidates)
            _json(directory / f"audits/{ordinal:06d}.json", c.canonical_data(audit))
            if not audit["integrity_passed"]:
                failures.append({"request_id": request["request_id"], "code": "AUDIT_INTEGRITY_FAILED",
                                 "violations": audit["integrity_violations"]})
            sources = trace["evidence"]["sources"]
            diagnostics.append({"request_id": request["request_id"], "execution": "completed",
                "row_available": trace["row"] is not None, "core_proof_status": trace["core"].capacity.proof_status,
                "audit_integrity_passed": audit["integrity_passed"], "recomposition_checked": audit["recomposition_checked"],
                "audit_dimensions_checked": {key: audit[key]["checked"] for key in ("safety", "schedule_validity", "eligibility", "action_validity", "explanation_grounding")},
                "selected_source_ids": [s["source_id"] for s in sources],
                "cache_miss_source_ids": [s["source_id"] for s in sources if "cache_miss" in s["issues"]],
                "asset_issue_codes": [s["asset_issue"] for s in sources if s["asset_issue"]],
                "source_issue_counts": dict(Counter(code for s in sources for code in s["issues"])),
                "core_issue_codes": sorted({i.code for i in trace["core"].issues}),
                "recommendation_issue_codes": sorted({i["code"] for i in trace["recommendation"]["issues"]}),
                "ranking_issue_codes": sorted({i["code"] for i in trace["ranking"]["issues"]}),
                "generated_count": trace["search"]["generated_count"], "evaluated_count": trace["search"]["evaluated_count"]})
            if trace["row"] is None:
                failures.append({"request_id": request["request_id"], "code": "DECISION_UNRESOLVED",
                                 "recommendation_issues": trace["recommendation"]["issues"],
                                 "formatting_issues": trace["formatting_issues"],
                                 "core_issue_codes": sorted({issue.code for issue in trace["core"].issues}),
                                 "ranking_issues": trace["ranking"]["issues"]})
            else:
                rows.append(trace["row"])
            index.append({"request_id": request["request_id"], "trace": relative,
                          "row_available": trace["row"] is not None, "core_hash": trace["core"].context_hash,
                          "envelope_hash": trace["envelope"].context_hash, "facts_sha256": c.canonical_hash(trace["facts"])})
        except Exception as exc:
            # Keep the request denominator and error class, not a sensitive repr.
            failures.append({"request_id": request["request_id"], "code": "DECISION_EXECUTION_FAILED", "exception_type": type(exc).__name__})
            index.append({"request_id": request["request_id"], "trace": relative if (directory / relative).exists() else None,
                          "row_available": False})
            diagnostics.append({"request_id": request["request_id"], "execution": "failed", "exception_type": type(exc).__name__})
    _json(directory / "trace_index.json", index)
    _json(directory / "preflight_requests.json", diagnostics)
    _json(directory / "candidate_rows.json", rows)  # not a partial final CSV
    _json(directory / "origin_usage.json", [event.to_dict() for event in origins])
    if _code_hashes() != code_hashes:
        failures.append({"code": "RUNTIME_CODE_CHANGED_DURING_RUN"})
    # Pin the parsed source bytes, and reject changes while generating the run.
    for name, expected in source_hashes.items():
        path = dataset_root / name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            failures.append({"code": "INPUT_CHANGED_DURING_RUN", "file": name})
    accounting = None
    try:
        accounting = aggregate_usage(read_usage_events(directory / "usage_events.jsonl", run_id=run_id),
                                     origin_usage=origins, run_id=run_id, request_count=len(requests))
        _json(directory / "usage_summary.json", accounting)
        if not accounting["accounting_complete"]:
            failures.append({"code": "ACCOUNTING_INCOMPLETE", "reasons": accounting["unknown_reasons"]})
    except Exception as exc:
        failures.append({"code": "ACCOUNTING_FAILED", "exception_type": type(exc).__name__})
    if public_preflight and (any(cache_root.rglob("*.json")) or origins
                             or accounting is not None and accounting["new_cache_hits"] != 0):
        failures.append({"code": "FRESH_CACHE_PREFLIGHT_USED_UNEXPECTED_CACHE_DATA"})
    output_hash = None
    if not public_preflight and not failures and len(rows) == len(requests):
        write_output(directory / "predictions.csv", rows, requests, dataset_root=dataset_root)
        output_hash = hashlib.sha256((directory / "predictions.csv").read_bytes()).hexdigest()
    if accounting is not None:
        _bytes(directory / "usage_report.md", render_usage_report(accounting, output_sha256=output_hash).encode())
    else:
        _bytes(directory / "usage_report.md", b"# Incomplete development usage\n\nCanonical accounting failed; costs/tokens are unknown, not zero. See failures.json.\n")
    _json(directory / "failures.json", c.canonical_data(failures))
    completed = [item for item in diagnostics if item["execution"] == "completed"]
    preflight_complete = public_preflight and not any(f["code"] != "DECISION_UNRESOLVED" for f in failures)
    coverage = {"kind": "public-input-preflight" if public_preflight else "development-predictions",
        "denominator_requests": len(requests), "executed_requests": len(completed),
        "execution_failed_requests": len(requests) - len(completed),
        "requests_with_diagnostic_rows": len(rows), "unresolved_requests": sum(not d["row_available"] for d in completed),
        "requests_with_cache_misses": sum(bool(d["cache_miss_source_ids"]) for d in completed),
        "selected_source_references": sum(len(d["selected_source_ids"]) for d in completed),
        "unique_selected_source_ids": len({s for d in completed for s in d["selected_source_ids"]}),
        "cache_miss_source_references": sum(len(d["cache_miss_source_ids"]) for d in completed),
        "unique_cache_miss_source_ids": len({s for d in completed for s in d["cache_miss_source_ids"]}),
        "source_issue_counts": dict(sum((Counter(d["source_issue_counts"]) for d in completed), Counter())),
        "asset_issue_counts": dict(Counter(code for d in completed for code in d["asset_issue_codes"])),
        "core_proof_counts": dict(Counter(d["core_proof_status"] for d in completed)),
        "core_issue_request_counts": dict(Counter(code for d in completed for code in d["core_issue_codes"])),
        "ranking_issue_request_counts": dict(Counter(code for d in completed for code in d["ranking_issue_codes"])),
        "audit_integrity_passes": sum(d["audit_integrity_passed"] for d in completed),
        "audit_dimensions_checked": {key: sum(d["audit_dimensions_checked"][key] for d in completed)
             for key in ("safety", "schedule_validity", "eligibility", "action_validity", "explanation_grounding")},
        "expected_answers_used": False, "accuracy_measured": False, "financial_certified": False}
    _json(directory / "coverage.json", coverage)
    configuration = {"mode": "cache-only", "max_candidates": max_candidates,
                     "request_input_kind": "public-input-only" if public_preflight else "production-shaped",
                     "fresh_empty_cache": public_preflight, "prediction_csv_enabled": not public_preflight,
                     "forecast_policy": c.canonical_data(c.ForecastPolicy()),
                     "python": platform.python_version(),
                     "dependencies": {name: version(name) for name in ("pydantic", "httpx", "Pillow")}}
    manifest = {
        "schema_version": "buy-wait-development-run/v1", "run_id": run_id, "phase": "development",
        "status": ("preflight_complete" if preflight_complete else "preflight_incomplete") if public_preflight else ("complete_predictions" if output_hash else "unresolved"),
        "request_count": len(requests), "request_inputs_sha256": c.canonical_hash([dict(r) for r in requests]),
        "resolved_rows": len(rows), "failure_count": len(failures), "output_sha256": output_hash,
        "accounting_complete": accounting["accounting_complete"] if accounting is not None else False,
        "source_hashes": source_hashes, "code_hashes": code_hashes, "configuration": configuration,
        "configuration_sha256": c.canonical_hash(configuration),
        "cache_references": cache_references, "cache_reference_sha256": c.canonical_hash(cache_references),
        "cache_bytes_frozen": False, "final_certified": False,
        "artifact_hashes": {path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                            for path in sorted(directory.rglob("*")) if path.is_file()},
    }
    _json(directory / "manifest.json", manifest)  # completion/incomplete-status marker LAST
    return {"run_directory": str(directory), "request_count": len(requests), "resolved_rows": len(rows),
            "failure_count": len(failures), "output_written": output_hash is not None,
            "accounting_complete": manifest["accounting_complete"], "phase": "development", "final_certified": False,
            "preflight_complete": preflight_complete, "coverage": coverage}
