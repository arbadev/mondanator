"""Durably persist and aggregate evidence-owned UsageEvents, never count dispatches.

Physical attempts, new cache hits and consumed cache origins are separate views
of the same canonical receipts. Unknown metadata is not zero. POSIX only, like
the current evidence cache. No credentials, prompts or provider calls belong here.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import stat
from decimal import Decimal, localcontext
from pathlib import Path

from buy_wait.evidence.usage import UsageEvent

_LIMIT = 64 * 1024 * 1024
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_TOKEN_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens")


class AccountingError(ValueError):
    """Bounded accounting error; never include a raw response/configuration."""


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AccountingError("duplicate receipt JSON field")
        result[key] = value
    return result


def _cost(event):
    if event.cost is None:
        return None
    value = Decimal(event.cost)
    if value.adjusted() > 9 or value.as_tuple().exponent < -24 or len(value.as_tuple().digits) > 48:
        raise AccountingError("unbounded receipt cost representation")
    return value


def _validate(event):
    if not isinstance(event, UsageEvent):
        raise AccountingError("canonical UsageEvent required")
    for value in (event.run_id, event.attempt_id, event.source_id, event.outcome):
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise AccountingError("invalid receipt identity")
    if not isinstance(event.cache_key, str) or not _HASH.fullmatch(event.cache_key):
        raise AccountingError("invalid receipt cache identity")
    for name in ("requested_model", "returned_model", "actual_provider", "price_snapshot_id"):
        value = getattr(event, name)
        if value is None and name in ("returned_model", "actual_provider"):
            continue
        if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_./:-]{1,200}", value) is None:
            raise AccountingError("invalid bounded model/provider metadata")
    if event.cost_currency != "USD":
        raise AccountingError("unsupported billing currency; no invented conversion")
    if event.retry_of is not None and (not isinstance(event.retry_of, str) or not _ID.fullmatch(event.retry_of)):
        raise AccountingError("invalid retry identity")
    if any(not isinstance(v, str) or not _ID.fullmatch(v) for v in event.cache_origin_attempt_ids):
        raise AccountingError("invalid cache origin identity")
    cost = _cost(event)
    if event.outcome == "cache_hit":
        if (any(getattr(event, f) != 0 for f in _TOKEN_FIELDS) or cost != 0
                or not event.cache_origin_attempt_ids):
            raise AccountingError("cache hit requires zero new usage and explicit origins")
    elif event.cache_origin_attempt_ids:
        raise AccountingError("physical attempt cannot be another cache-origin reference")
    return event


def _decode(payload, run_id=None):
    if payload and not payload.endswith(b"\n"):
        raise AccountingError("incomplete receipt line; preserve the failed ledger")
    events, seen = [], set()
    try:
        for line in payload.splitlines():
            row = json.loads(line, object_pairs_hook=_object)
            if not isinstance(row, dict) or not isinstance(row.get("cache_origin_attempt_ids"), list):
                raise AccountingError("invalid receipt wire shape")
            row["cache_origin_attempt_ids"] = tuple(row["cache_origin_attempt_ids"])
            event = _validate(UsageEvent(**row))
            if event.attempt_id in seen or run_id is not None and event.run_id != run_id:
                raise AccountingError("duplicate or cross-run receipt")
            seen.add(event.attempt_id)
            events.append(event)
    except AccountingError:
        raise
    except (ValueError, TypeError, UnicodeError, KeyError, RecursionError) as exc:
        raise AccountingError("invalid canonical receipt ledger") from exc
    return tuple(events)


def _open(path, flags):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise AccountingError("symlink receipt path is forbidden")
    fd = os.open(path, flags | os.O_NOFOLLOW, 0o600)
    metadata = os.fstat(fd)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _LIMIT:
        os.close(fd)
        raise AccountingError("invalid or oversized receipt file")
    return fd


def read_usage_events(path, *, run_id=None):
    fd = _open(path, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        size = os.fstat(fd).st_size
        payload = os.pread(fd, size, 0)
        if len(payload) != size:
            raise AccountingError("receipt read was incomplete")
        return _decode(payload, run_id)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


class JsonlUsageSink:
    """Exclusive new ledger; canonical duplicate detection under an append lock.

    Reusing a run/ledger is refused. A failed/partial append remains on disk and
    prevents a success claim; callers retain returned extractor attempt receipts.
    """
    def __init__(self, path, *, run_id):
        if not isinstance(run_id, str) or not _ID.fullmatch(run_id):
            raise AccountingError("invalid usage run identity")
        self.path, self.run_id = Path(path), run_id
        fd = _open(self.path, os.O_RDWR | os.O_CREAT | os.O_EXCL)
        os.close(fd)

    def record(self, event):
        event = _validate(event)
        if event.run_id != self.run_id:
            raise AccountingError("receipt belongs to another run")
        payload = (json.dumps(event.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
        fd = _open(self.path, os.O_RDWR | os.O_APPEND)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            before = os.fstat(fd)
            original = os.pread(fd, before.st_size, 0)
            if len(original) != before.st_size or before.st_size + len(payload) > _LIMIT:
                raise AccountingError("receipt ledger size is invalid")
            prior = _decode(original, self.run_id)
            if event.attempt_id in {value.attempt_id for value in prior}:
                raise AccountingError("duplicate canonical attempt ID")
            sent = 0
            while sent < len(payload):
                size = os.write(fd, payload[sent:])
                if size <= 0:
                    raise AccountingError("receipt append failed")
                sent += size
            os.fsync(fd)
            current, named = os.fstat(fd), self.path.stat()
            if ((current.st_dev, current.st_ino) != (named.st_dev, named.st_ino)
                    or current.st_size != before.st_size + len(payload)
                    or os.pread(fd, before.st_size, 0) != original
                    or os.pread(fd, len(payload), before.st_size) != payload):
                raise AccountingError("receipt preservation could not be verified")
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def _unique(events):
    result = {}
    for event in events:
        _validate(event)
        if event.attempt_id in result and result[event.attempt_id] != event:
            raise AccountingError("conflicting canonical attempt receipts")
        result[event.attempt_id] = event
    return result


def _sum_cost(values):
    with localcontext() as context:
        context.prec = 80  # bounded receipt decimals, independent of ambient precision
        return sum(values, Decimal(0))


def _stats(events, denominator, *, missing=()):
    events = tuple(events)
    result = {"physical_attempts": None if missing else len(events), "known_physical_attempts": len(events),
              "retries": sum(e.retry_of is not None for e in events),
              "failures": sum(e.outcome != "validated" for e in events), "missing_receipt_ids": sorted(missing)}
    for field in (*_TOKEN_FIELDS, "cached_tokens", "reasoning_tokens", "cache_write_tokens"):
        unknown = sorted([e.attempt_id for e in events if getattr(e, field) is None] + list(missing))
        subtotal = sum(getattr(e, field) for e in events if getattr(e, field) is not None)
        result[field] = None if unknown else subtotal
        result["known_" + field] = subtotal
        result["unknown_" + field + "_ids"] = unknown
    unknown = sorted([e.attempt_id for e in events if e.cost is None] + list(missing))
    subtotal = _sum_cost([_cost(e) for e in events if e.cost is not None])
    result.update(cost_usd=None if unknown else format(subtotal, "f"), known_cost_usd=format(subtotal, "f"), unknown_cost_ids=unknown)
    with localcontext() as context:
        context.prec = 40
        for field in ("total_tokens", "cost_usd"):
            value = result[field]
            result["average_" + field + "_per_request"] = None if value is None else str(Decimal(value) / denominator)
    return result


def aggregate_usage(events, *, origin_usage=(), run_id, request_count):
    """Aggregate IDs, not a second dispatch counter or a guessed billing ledger."""
    if type(request_count) is not int or request_count <= 0:
        raise AccountingError("positive actual request denominator required")
    events = tuple(_validate(event) for event in events)
    if len({e.attempt_id for e in events}) != len(events):
        raise AccountingError("duplicate events in current run ledger")
    new = _unique(events)
    if any(e.run_id != run_id for e in new.values()):
        raise AccountingError("current ledger mixes runs")
    origins = _unique(origin_usage)
    if any(event.outcome == "cache_hit" for event in origins.values()):
        raise AccountingError("cache origins must be physical attempt receipts")
    all_events = _unique((*new.values(), *origins.values()))
    hits = tuple(e for e in new.values() if e.outcome == "cache_hit")
    referenced = {identity for hit in hits for identity in hit.cache_origin_attempt_ids}
    missing = referenced - all_events.keys()
    for hit in hits:
        for identity in hit.cache_origin_attempt_ids:
            origin = all_events.get(identity)
            if origin is not None and (origin.outcome == "cache_hit" or origin.cache_key != hit.cache_key or origin.source_id != hit.source_id):
                raise AccountingError("cache origin receipt identity mismatch")
    current = {key: e for key, e in all_events.items() if e.run_id == run_id and e.outcome != "cache_hit"}
    consumed = {key: all_events[key] for key in referenced if key in all_events}
    recovered = set(current) - new.keys()
    # Returned current-run origins preserve actual attempts after a sink failure.
    # Count their canonical receipts once, while marking ledger integrity unknown.
    combined = {**current, **consumed}
    problems = []
    if missing:
        problems.append("missing_cache_origin_receipts")
    if recovered:
        problems.append("current_attempts_missing_from_durable_ledger")
    if any(e.run_id != run_id and key not in referenced for key, e in origins.items()):
        problems.append("unreferenced_origin_receipts")
    for event in combined.values():
        if any(getattr(event, f) is None for f in (*_TOKEN_FIELDS, "cost", "actual_provider", "returned_model")):
            problems.append("unknown_usage_or_provider")
    groups = {}
    for event in combined.values():
        key = (event.requested_model, event.returned_model, event.actual_provider, event.price_snapshot_id)
        groups.setdefault(key, []).append(event)
    by_model = []
    for key, values in sorted(groups.items(), key=lambda item: tuple(v or "" for v in item[0])):
        by_model.append(dict(zip(("requested_model", "returned_model", "actual_provider", "price_snapshot_id"), key),
                             **_stats(values, request_count)))
    return {
        "schema_version": "buy-wait-usage-summary/v1", "run_id": run_id, "request_count": request_count,
        "accounting_complete": not problems, "unknown_reasons": sorted(set(problems)),
        "accounting_complete_scope": "core token totals, provider/model, costs and receipt coverage; optional token details may remain unknown",
        "new_cache_hits": len(hits), "recovered_current_attempt_ids": sorted(recovered),
        "new_attempt_ids": sorted(current), "consumed_origin_attempt_ids": sorted(consumed),
        "new_usage": _stats(current.values(), request_count),
        "consumed_cache_origins": _stats(consumed.values(), request_count, missing=missing),
        "combined_unique_usage": _stats(combined.values(), request_count, missing=missing),
        "by_model": by_model, "coding_assistant_usage": "excluded",
    }


def render_usage_report(summary, *, output_sha256=None):
    """Development report; a valid identity marker is not final-run permission."""
    lines = ["# Development application usage", "", "Not a final-run report or release certification.", ""]
    if output_sha256 is not None:
        if not _HASH.fullmatch(output_sha256):
            raise AccountingError("invalid output identity")
        identity = {key: summary[key] for key in ("run_id", "request_count", "accounting_complete")}
        identity["output_sha256"] = output_sha256
        lines += ["<!-- buy-wait-usage/v1 " + json.dumps(identity, sort_keys=True) + " -->", ""]
    else:
        lines += ["No complete prediction CSV exists; no final-compatible output identity is asserted.", ""]
    lines += [f"Run: `{summary['run_id']}`; requests: {summary['request_count']}; new cache hits: {summary['new_cache_hits']}.",
              f"Accounting complete: {summary['accounting_complete']}. Unknown reasons: {', '.join(summary['unknown_reasons']) or 'none'}.", "",
              "| View | Physical attempts | Input tokens | Output tokens | Total tokens | USD cost | Tokens/request | USD/request |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for label, key in (("New calls (including failures/retries)", "new_usage"), ("Consumed cache origins", "consumed_cache_origins"),
                       ("Unique union, no double billing", "combined_unique_usage")):
        values = summary[key]
        fields = [values[f] for f in ("physical_attempts", "prompt_tokens", "completion_tokens", "total_tokens", "cost_usd",
                                      "average_total_tokens_per_request", "average_cost_usd_per_request")]
        lines.append("| " + label + " | " + " | ".join("unknown" if v is None else str(v) for v in fields) + " |")
    lines += ["", "## Per-model/provider receipts", ""]
    for model in summary["by_model"]:
        lines += [f"- {model['requested_model']} → {model['returned_model'] or 'unknown'} / {model['actual_provider'] or 'unknown'}; "
                  f"snapshot {model['price_snapshot_id']}; attempts {model['physical_attempts']}; input {model['prompt_tokens']}; "
                  f"output {model['completion_tokens']}; total {model['total_tokens']}; USD {model['cost_usd']}."]
    if not summary["by_model"]:
        lines += ["No model/provider attempt receipts were consumed."]
    lines += ["", "Costs sum provider-reported canonical receipts; unknown charges are never priced at zero or filled from a guessed rate. "
              "Known subtotals, per-attempt unknown IDs, retry/failure counts and provider-cache details remain in usage_summary.json. "
              "Application cache hits have zero new cost; origin attempts are counted once in the combined view. Provider cache tokens "
              "are included in prompt tokens, not added again. Averages use the actual full request denominator, not successes. "
              "Coding-assistant usage is separate and excluded.", ""]
    return "\n".join(lines)
