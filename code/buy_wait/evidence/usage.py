"""One canonical per-attempt accounting stream; no second inference counter.

Unknown billing stays unknown. A timed-out attempt retains its full reserved
upper bound until reconciliation; it never returns free budget automatically.
"""
from __future__ import annotations

import threading
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol

from .schema import EvidenceError


@dataclass(frozen=True)
class UsageEvent:
    run_id: str
    attempt_id: str
    source_id: str
    cache_key: str
    requested_model: str
    returned_model: str | None
    actual_provider: str | None
    generation_id: str | None
    outcome: str
    retry_of: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    cost: str | None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None
    cache_write_tokens: int | None = None
    cache_origin_attempt_ids: tuple[str, ...] = ()
    price_snapshot_id: str = "openrouter-public-2026-09-12-gpt-4.1-2-8"
    cost_currency: str = "USD"
    request_sha256: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    http_status: int | None = None

    def __post_init__(self):
        for name in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens", "reasoning_tokens", "cache_write_tokens"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise EvidenceError("invalid_usage_count")
        if self.cost is not None:
            try:
                amount = Decimal(self.cost)
                if not isinstance(self.cost, str) or not amount.is_finite() or amount < 0:
                    raise ValueError()
            except (ValueError, InvalidOperation, TypeError):
                raise EvidenceError("invalid_usage_cost") from None
        if self.prompt_tokens is not None and self.completion_tokens is not None and self.total_tokens is not None:
            if self.prompt_tokens + self.completion_tokens != self.total_tokens:
                raise EvidenceError("inconsistent_usage_total")

    def to_dict(self):
        result = asdict(self)
        result["cache_origin_attempt_ids"] = list(self.cache_origin_attempt_ids)
        return result


class UsageSink(Protocol):
    def record(self, event: UsageEvent) -> None: ...


class MemoryUsageSink:
    def __init__(self):
        self.events: list[UsageEvent] = []
        self._ids: set[str] = set()

    def record(self, event: UsageEvent) -> None:
        if event.attempt_id in self._ids:
            raise EvidenceError("duplicate_usage_event")
        self.events.append(event)
        self._ids.add(event.attempt_id)


def usage_fields(body: dict) -> dict:
    """Bounded metadata only: no error strings, headers, prompts or raw bodies."""
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}

    def count(name, parent=usage):
        value = parent.get(name)
        return value if type(value) is int and value >= 0 else None

    def text(name):
        value = body.get(name)
        # Never retain arbitrary model/provider strings that could echo secrets.
        import re
        if name == "provider":
            return value if value in ("Azure", "azure", "azure/swedencentral") else None
        if name == "model":
            return value if value in ("openai/gpt-4.1", "openai/gpt-4.1-2025-04-14") else None
        return value if isinstance(value, str) and re.fullmatch(r"gen-[A-Za-z0-9_-]{1,156}", value) else None

    cost = usage.get("cost")
    try:
        if isinstance(cost, bool) or not isinstance(cost, (str, int, float, Decimal)):
            raise ValueError()
        value = Decimal(str(cost))
        if not value.is_finite() or value < 0:
            raise ValueError()
        cost = format(value, "f")
    except (ValueError, InvalidOperation):
        cost = None
    prompt = usage.get("prompt_tokens_details")
    output = usage.get("completion_tokens_details")
    prompt = prompt if isinstance(prompt, dict) else {}
    output = output if isinstance(output, dict) else {}
    total = count("total_tokens")
    if count("prompt_tokens") is not None and count("completion_tokens") is not None and total is not None:
        if count("prompt_tokens") + count("completion_tokens") != total:
            total = None
    return dict(returned_model=text("model"), actual_provider=text("provider"), generation_id=text("id"),
                prompt_tokens=count("prompt_tokens"), completion_tokens=count("completion_tokens"),
                total_tokens=total, cost=cost,
                cached_tokens=count("cached_tokens", prompt), cache_write_tokens=count("cache_write_tokens", prompt),
                reasoning_tokens=count("reasoning_tokens", output))


class RunBudget:
    """Trusted runner provides a justified upper bound before each dispatch.

    This module does not pretend estimated text/image token counts are certified
    bounds. Missing bounds prevent dispatch. The future approved runner must
    establish the bound from its actual input and endpoint billing contract.
    """

    def __init__(self, *, max_calls: int, max_cost: Decimal):
        if type(max_calls) is not int or max_calls < 1 or not isinstance(max_cost, Decimal) or not max_cost.is_finite() or max_cost <= 0:
            raise EvidenceError("invalid_budget")
        self.max_calls, self.max_cost = max_calls, max_cost
        self._amounts: dict[str, Decimal] = {}
        self._lock = threading.Lock()
        self.overrun = False

    @property
    def committed(self) -> Decimal:
        return sum(self._amounts.values(), Decimal(0))

    def reserve(self, attempt_id: str, bound: Decimal | None):
        with self._lock:
            if not isinstance(bound, Decimal) or not bound.is_finite() or bound <= 0:
                raise EvidenceError("unbounded_attempt_cost")
            if attempt_id in self._amounts:
                raise EvidenceError("duplicate_attempt")
            if self.overrun or len(self._amounts) >= self.max_calls or self.committed + bound > self.max_cost:
                raise EvidenceError("budget_exhausted")
            self._amounts[attempt_id] = bound

    def reconcile(self, attempt_id: str, reported_cost: str | None):
        if reported_cost is None:
            return
        with self._lock:
            cost = Decimal(reported_cost)
            if cost > self._amounts[attempt_id]:
                self.overrun = True
            self._amounts[attempt_id] = cost
