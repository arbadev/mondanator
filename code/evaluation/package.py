"""Explicit, hash-bound packaging capability; never uploads or grants release authority.

The final runner must supply its completed validation/accounting manifest. This
module checks artifact integrity, not financial truth or captain permissions.
No real final package is produced by the offline synthetic unit tests.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import stat
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from buy_wait.data import CONTEXT_FILES, DataError, OUTPUT_FIELDS, load_requests
from buy_wait.output import structural_issues

USAGE_MARKER = "<!-- buy-wait-usage/v1 "
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_CREDENTIAL = re.compile(
    rb"(?:sk-(?:or-v1-|proj-)?[A-Za-z0-9_-]{32,}|gh[pousr]_[A-Za-z0-9]{30,}|"
    rb"github_pat_[A-Za-z0-9_]{30,}|AKIA[A-Z0-9]{16}|"
    rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----)"
)
_REQUIRED = frozenset(("README.md", "requirements.txt", "code/main.py", "evaluation/usage_report.md"))


class ArtifactError(ValueError):
    """Secret-safe artifact validation error."""


def sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def _relative(value):
    if not isinstance(value, str) or "\\" in value or ":" in value:
        raise ArtifactError("artifact paths must be canonical relative POSIX paths")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or path.as_posix() != value:
        raise ArtifactError("artifact path is not canonical/relative")
    if any(p.startswith(".") or p in ("..", ".") for p in path.parts):
        raise ArtifactError("hidden/traversal paths are not permitted")
    return path


def _read(root, relative):
    path = _relative(relative)
    candidate = root
    for part in path.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ArtifactError("symlink artifact paths are not permitted")
    try:
        metadata = candidate.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 64 * 1024 * 1024:
            raise ArtifactError("artifact must be a regular file of at most 64 MiB")
        return candidate.read_bytes()
    except OSError as exc:
        raise ArtifactError("required artifact is unavailable") from exc


def _digest(expected, payload):
    if not isinstance(expected, str) or not _HASH.fullmatch(expected) or sha256(payload) != expected:
        raise ArtifactError("artifact hash differs from frozen manifest")


def _allowed_member(relative):
    path = _relative(relative)
    blocked = {"dataset", "node_modules", "venv", "cache", "caches", "runs", "outputs", "organizer"}
    if any(part.casefold() in blocked for part in path.parts):
        raise ArtifactError("archive includes an excluded directory")
    if re.search(r"(?:^|[_\-.])(credentials?|secrets?|tokens?|passwords?)(?:$|[_\-.])", path.name, re.I):
        raise ArtifactError("archive includes a credential-like filename")
    if path.name in ("log.txt", "chat_transcript") or path.name.startswith("chat_transcript."):
        raise ArtifactError("transcript/log is a separate authorized artifact")
    if path.name == "usage_report.md" and relative != "evaluation/usage_report.md":
        raise ArtifactError("only one archive-root usage report is permitted")
    allowed = (relative in _REQUIRED
               or (path.parts[0] == "code" and path.suffix in (".py", ".json", ".txt", ".md"))
               or (path.parts[0] == "docs" and path.suffix == ".md")
               or (path.parts[0] == "reproduction" and path.suffix == ".json"))
    if not allowed:
        raise ArtifactError("archive member is outside the explicit allowed surface")


def _check_credentials(payload):
    # A bounded guard, not a claim to recognize every possible secret format.
    if _CREDENTIAL.search(payload):
        raise ArtifactError("recognizable credential/private-key content blocks packaging")
    try:
        value = json.loads(payload)
    except (ValueError, UnicodeError):
        return
    sensitive = {"apikey", "authorization", "accesstoken", "refreshtoken", "clientsecret", "password", "cookie", "privatekey"}
    def visit(node):
        if isinstance(node, dict):
            for key, child in node.items():
                normalized = re.sub(r"[^a-z]", "", key.lower())
                if (normalized in sensitive or normalized.endswith("apikey")) and child not in (None, ""):
                    raise ArtifactError("credential-bearing JSON configuration blocks packaging")
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)
    visit(value)


def usage_identity(report_bytes):
    """Machine-readable identity within the one human-readable usage report."""
    try:
        markers = [line for line in report_bytes.decode("utf-8").splitlines() if line.startswith(USAGE_MARKER)]
        if len(markers) != 1 or not markers[0].endswith(" -->"):
            raise ValueError
        identity = json.loads(markers[0][len(USAGE_MARKER):-4])
        if (not isinstance(identity, dict)
                or set(identity) != {"run_id", "request_count", "output_sha256", "accounting_complete"}
                or not isinstance(identity["run_id"], str) or not identity["run_id"].strip()
                or type(identity["request_count"]) is not int or identity["request_count"] <= 0
                or type(identity["accounting_complete"]) is not bool
                or not isinstance(identity["output_sha256"], str) or not _HASH.fullmatch(identity["output_sha256"])):
            raise ValueError
    except (UnicodeError, ValueError) as exc:
        raise ArtifactError("usage report needs exactly one valid run/output identity marker") from exc
    return identity


def build_code_zip(root, manifest_relative, destination="code.zip"):
    """Build only explicitly listed frozen files, after all local checks.

    Manifest: schema_version=buy-wait-final-artifacts/v1, phase=final, run_id,
    request_count, output_sha256, dataset_hashes, archive_files (path->hash),
    critical_issues=0, proof_status_by_request (complete supported proof states),
    accounting_complete=true. The runner supplies these after actual validation;
    setting fields by hand is not financial certification or spend approval.
    """
    root = Path(root).resolve()
    if PurePosixPath(manifest_relative).suffix != ".json":
        raise ArtifactError("final artifact manifest must be JSON")
    try:
        manifest = json.loads(_read(root, manifest_relative))
    except (ValueError, UnicodeError) as exc:
        raise ArtifactError("invalid final artifact manifest") from exc
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != "buy-wait-final-artifacts/v1"
            or manifest.get("phase") != "final" or not isinstance(manifest.get("run_id"), str) or not manifest["run_id"]
            or type(manifest.get("critical_issues")) is not int or manifest["critical_issues"] != 0
            or manifest.get("accounting_complete") is not True):
        raise ArtifactError("final validation/accounting is incomplete")
    dataset_hashes = manifest.get("dataset_hashes")
    required_inputs = set(CONTEXT_FILES) | {"requests.csv"}
    if not isinstance(dataset_hashes, dict) or not required_inputs <= dataset_hashes.keys():
        raise ArtifactError("missing participant input pins")
    for relative, digest in dataset_hashes.items():
        if relative not in required_inputs and not re.fullmatch(r"media/images/image_[0-9]+\.png", relative):
            raise ArtifactError("unapproved dataset pin; sample answers/organizer files are forbidden")
        _digest(digest, _read(root, "dataset/" + relative))
    requests, request_hash = load_requests(root / "dataset")
    if request_hash != dataset_hashes["requests.csv"]:
        raise ArtifactError("requests changed during preflight")
    if type(manifest.get("request_count")) is not int or manifest["request_count"] != len(requests):
        raise ArtifactError("final request denominator mismatch")
    proofs = manifest.get("proof_status_by_request")
    if (not isinstance(proofs, dict) or set(proofs) != {r["request_id"] for r in requests}
            or any(not isinstance(v, str) or v not in ("resolved_under_policy", "conservative_bound") for v in proofs.values())):
        raise ArtifactError("unknown/incomplete request proof inventory")
    output = _read(root, "output.csv")
    _digest(manifest.get("output_sha256"), output)
    _check_credentials(output)
    try:
        reader = csv.DictReader(io.StringIO(output.decode("utf-8-sig"), newline=""), strict=True)
        if tuple(reader.fieldnames or ()) != OUTPUT_FIELDS:
            raise DataError("invalid output header")
        rows = list(reader)
        if len(rows) != len(requests) or any(structural_issues(row, req) for row, req in zip(rows, requests)):
            raise DataError("invalid ordered output coverage/structure")
    except (DataError, UnicodeError, csv.Error) as exc:
        raise ArtifactError("final output is structurally invalid") from exc
    entries = manifest.get("archive_files")
    if not isinstance(entries, dict) or not _REQUIRED <= entries.keys():
        raise ArtifactError("archive manifest omits required files")
    # Validate every name BEFORE opening a single member, including a forbidden .env.
    for relative in entries:
        _allowed_member(relative)
    payloads = {}
    for relative, digest in sorted(entries.items()):
        payload = _read(root, relative)
        _digest(digest, payload)
        _check_credentials(payload)
        payloads[relative] = payload
    identity = usage_identity(payloads["evaluation/usage_report.md"])
    if any(identity.get(key) != manifest[key] for key in ("run_id", "request_count", "output_sha256", "accounting_complete")):
        raise ArtifactError("usage report does not describe this exact final output/run")
    target_relative = _relative(destination)
    if target_relative.suffix != ".zip" or target_relative.parts[0] in ("dataset", "code", "reproduction"):
        raise ArtifactError("package destination must be a separate ZIP artifact")
    target = root.joinpath(*target_relative.parts)
    for parent in (target, *target.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise ArtifactError("package destination cannot be a symlink")
    if target.exists():
        raise ArtifactError("existing package is retained; choose a new destination")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".bw-package-", suffix=".zip", dir=target.parent)
    try:
        with os.fdopen(fd, "w+b") as stream:
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
                for relative, payload in sorted(payloads.items()):
                    info = zipfile.ZipInfo(relative, date_time=(2000, 1, 1, 0, 0, 0))
                    info.create_system = 3
                    info.external_attr = (stat.S_IFREG | 0o644) << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, payload)
            stream.flush()
            os.fsync(stream.fileno())
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None or set(archive.namelist()) != set(payloads):
                raise ArtifactError("archive round-trip integrity failed")
            if any(sha256(archive.read(name)) != entries[name] for name in archive.namelist()):
                raise ArtifactError("archive member hash mismatch")
        # Hard-link publish is exclusive/atomic on the same filesystem: no race
        # can replace a previous final package between the preflight and publish.
        os.link(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--manifest", required=True, help="relative frozen final-artifacts JSON, not a development run")
    parser.add_argument("--output", default="code.zip")
    args = parser.parse_args(argv)
    try:
        result = build_code_zip(args.root, args.manifest, args.output)
        print(json.dumps({"artifact": str(result), "sha256": sha256(result.read_bytes()), "uploaded": False}, sort_keys=True))
        return 0
    except (ArtifactError, OSError) as exc:
        parser.exit(2, f"packaging failed: {str(exc) if isinstance(exc, ArtifactError) else type(exc).__name__}\n")


if __name__ == "__main__":
    raise SystemExit(main())
