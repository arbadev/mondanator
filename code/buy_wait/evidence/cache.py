"""Content-addressed private cache. Every load is revalidated by the extractor."""
from __future__ import annotations

import contextlib
import fcntl
import os
import re
import tempfile
from pathlib import Path

from .schema import EvidenceError, canonical, digest, strict_json

CACHE_VERSION = "bw.evidence-cache/1"


class ExtractionCache:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink():
            raise EvidenceError("invalid_cache_path")

    def _path(self, key: str, suffix: str) -> Path:
        if re.fullmatch(r"[0-9a-f]{64}", key) is None:
            raise EvidenceError("invalid_cache_key")
        path = self.root / (key + suffix)
        if path.is_symlink():
            raise EvidenceError("invalid_cache_path")
        return path

    @contextlib.contextmanager
    def locked(self, key: str):
        # All cache producers must use this across read -> inference -> save.
        fd = os.open(self._path(key, ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def load(self, key: str) -> dict | None:
        path = self._path(key, ".json")
        if not path.exists():
            return None
        try:
            raw = strict_json(path.read_bytes(), max_bytes=1048576)
            if not isinstance(raw, dict) or set(raw) != {"version", "key", "payload", "sha256"}:
                raise EvidenceError("cache_invalid")
            if raw["version"] != CACHE_VERSION or raw["key"] != key or digest(raw["payload"]) != raw["sha256"]:
                raise EvidenceError("cache_invalid")
            return raw["payload"]
        except (EvidenceError, OSError, ValueError):
            raise EvidenceError("cache_invalid") from None

    def save(self, key: str, payload: dict) -> None:
        destination = self._path(key, ".json")
        data = canonical({"version": CACHE_VERSION, "key": key, "payload": payload, "sha256": digest(payload)})
        if len(data) > 1048576:
            raise EvidenceError("oversize_cache")
        fd, name = tempfile.mkstemp(prefix=".cache-", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, destination)
        finally:
            if os.path.exists(name):
                os.unlink(name)
