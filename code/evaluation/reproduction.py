"""Verify immutable development artifact identities, not financial certification."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
from pathlib import Path

from buy_wait import contracts as c
from buy_wait.data import CONTEXT_FILES, DataError, project_request, read_table

_FIXED = {"trace_index.json", "candidate_rows.json", "origin_usage.json", "usage_events.jsonl", "usage_summary.json",
          "usage_report.md", "failures.json", "coverage.json", "preflight_requests.json", "predictions.csv"}
_REQUIRED = _FIXED - {"predictions.csv", "usage_summary.json"}
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_TRACE_UNCOMPRESSED_LIMIT = 256 * 1024 * 1024


def _read(root, name):
    path = Path(root) / name
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise DataError("artifact must be a regular non-symlink file")
    if path.stat().st_size > 64 * 1024 * 1024:
        raise DataError("artifact exceeds verification size bound")
    return path.read_bytes()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DataError("duplicate artifact JSON field")
        result[key] = value
    return result


def _json(payload):
    try:
        return json.loads(payload, object_pairs_hook=_object)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise DataError("invalid artifact JSON") from exc


def _trace_json(name, payload):
    if not name.endswith(".json.gz"):
        return _json(payload)
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(payload)) as stream:
            decoded = stream.read(_TRACE_UNCOMPRESSED_LIMIT + 1)
    except (OSError, EOFError) as exc:
        raise DataError("invalid compressed trace artifact") from exc
    if len(decoded) > _TRACE_UNCOMPRESSED_LIMIT:
        raise DataError("compressed trace exceeds expansion limit")
    return _json(decoded)


def verify_development_run(directory, *, dataset_root=None, expected_manifest_sha256=None):
    """Check complete inventory, trusted optional manifest pin, inputs and hashes.

    An unpinned manifest is a self-consistency check, not proof of authorship.
    No financial truth, source semantic grounding or release permission follows.
    """
    root = Path(directory)
    payload = _read(root, "manifest.json")
    manifest_hash = hashlib.sha256(payload).hexdigest()
    if expected_manifest_sha256 is not None and expected_manifest_sha256 != manifest_hash:
        raise DataError("trusted development manifest hash mismatch")
    manifest = _json(payload)
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != "buy-wait-development-run/v1"
            or manifest.get("phase") != "development" or manifest.get("final_certified") is not False
            or type(manifest.get("request_count")) is not int or manifest["request_count"] <= 0):
        raise DataError("unsupported development run identity")
    members = manifest.get("artifact_hashes")
    if not isinstance(members, dict) or not _REQUIRED <= members.keys():
        raise DataError("incomplete development artifact inventory")
    # Validate ALL paths before opening any named member. No .env/private reads.
    for name, digest in members.items():
        if (not isinstance(name, str) or name not in _FIXED and re.fullmatch(r"(?:traces|audits)/[0-9]{6,}\.json(?:\.gz)?", name) is None
                or not isinstance(digest, str) or not _HASH.fullmatch(digest)):
            raise DataError("unsafe development artifact inventory")
    actual = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise DataError("symlink in development artifact tree")
        if path.is_file() and path != root / "manifest.json":
            actual.add(path.relative_to(root).as_posix())
    if actual != set(members):
        raise DataError("development artifact set changed")
    contents = {}
    for name, digest in members.items():
        contents[name] = _read(root, name)
        if hashlib.sha256(contents[name]).hexdigest() != digest:
            raise DataError("development artifact hash mismatch")
    configuration = manifest.get("configuration")
    if not isinstance(configuration, dict) or c.canonical_hash(configuration) != manifest.get("configuration_sha256"):
        raise DataError("development configuration hash mismatch")
    if c.canonical_hash(manifest.get("cache_references")) != manifest.get("cache_reference_sha256"):
        raise DataError("cache reference inventory hash mismatch")
    output = contents.get("predictions.csv")
    if manifest.get("output_sha256") != (hashlib.sha256(output).hexdigest() if output is not None else None):
        raise DataError("development output identity mismatch")
    index = _json(contents["trace_index.json"])
    if not isinstance(index, list) or len(index) != manifest["request_count"]:
        raise DataError("development trace index denominator mismatch")
    if any(not isinstance(entry, dict) or not isinstance(entry.get("request_id"), str) or not entry["request_id"]
           or "trace" not in entry for entry in index):
        raise DataError("invalid development trace index record")
    ids = [entry["request_id"] for entry in index]
    if len(set(ids)) != len(ids):
        raise DataError("duplicate development trace identity")
    for entry in index:
        name = entry["trace"]
        if name is not None:
            if name not in contents or not name.startswith("traces/"):
                raise DataError("unlisted trace reference")
            trace = _trace_json(name, contents[name])
            if trace.get("request", {}).get("request_id") != entry["request_id"]:
                raise DataError("trace/request identity mismatch")
    if dataset_root is not None:
        source_hashes = manifest.get("source_hashes")
        request_file = "sample_requests.csv" if configuration["request_input_kind"] == "public-input-only" else "requests.csv"
        if not isinstance(source_hashes, dict) or set(source_hashes) != {*CONTEXT_FILES, request_file}:
            raise DataError("unsupported participant source inventory")
        for name, digest in source_hashes.items():
            if not isinstance(digest, str) or not _HASH.fullmatch(digest) or hashlib.sha256(_read(dataset_root, name)).hexdigest() != digest:
                raise DataError("participant source snapshot mismatch")
        rows, _ = read_table(dataset_root, request_file, sample_fixture=request_file == "sample_requests.csv")
        inputs = [dict(project_request(row)) for row in rows]
        if [row["request_id"] for row in inputs] != ids or c.canonical_hash(inputs) != manifest["request_inputs_sha256"]:
            raise DataError("projected input/request identity mismatch")
    return {"artifact_integrity_verified": True, "manifest_sha256": manifest_hash,
            "manifest_pin_verified": expected_manifest_sha256 is not None, "request_count": len(ids),
            "dataset_csv_verified": dataset_root is not None, "source_semantics_verified": False,
            "runtime_source_verified": False, "image_bytes_reverified": False,
            "cache_bytes_frozen": False, "financial_certified": False}
