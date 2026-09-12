"""No-change capacity, independent of offers, methods, deadlines and changes."""
from __future__ import annotations

from datetime import timedelta
from buy_wait.contracts import CapacityMetrics, Money


def compute_capacity(baseline, requested: Money, minimum_minor: int, *, request_date):
    if baseline.home_currency != requested.currency or baseline.start != request_date:
        raise ValueError("capacity request does not match forecast")
    if type(minimum_minor) is not int or minimum_minor < 0:
        raise ValueError("minimum must be nonnegative integer minor units")
    if baseline.end != request_date + timedelta(days=90):
        raise ValueError("capacity requires the original inclusive 90-day horizon")
    if any(c.phase == "payment" for c in baseline.checkpoints):
        raise ValueError("capacity may only consume an unmodified baseline")
    codes = tuple(sorted({i.code for i in baseline.issues}))
    if baseline.proof_status == "unresolved":
        return CapacityMetrics(None, None, None, "unresolved", issue_codes=codes)
    points = baseline.checkpoints
    if not points:
        raise ValueError("forecast has no checkpoints")
    if any(c.available_minor < minimum_minor for c in points):
        return CapacityMetrics(Money(requested.currency, 0), None, False,
                               baseline.proof_status, baseline.binding_checkpoint_ids,
                               tuple(sorted(set((*codes, "BASELINE_SHORTFALL")))))
    suffix = [0] * len(points)
    suffix[-1] = points[-1].available_minor
    for i in range(len(points) - 2, -1, -1):
        suffix[i] = min(points[i].available_minor, suffix[i + 1])
    # In the no-payment baseline a close checkpoint is the after-posting payment slot.
    slots = [(i, c) for i, c in enumerate(points) if c.phase == "close"]
    expected = tuple(request_date + timedelta(days=d) for d in range(91))
    if tuple(c.date for _, c in slots) != expected:
        raise ValueError("forecast must contain one chronological close per date")
    today_index = slots[0][0]
    headroom = suffix[today_index] - minimum_minor
    safe = min(requested.minor, max(0, headroom))
    earliest = next((c.date for i, c in slots if suffix[i] - minimum_minor >= requested.minor), None)
    binding = tuple(c.checkpoint_id for c in points[today_index:]
                    if c.available_minor == suffix[today_index])
    return CapacityMetrics(Money(requested.currency, safe), earliest, True,
                           baseline.proof_status, binding, codes)
