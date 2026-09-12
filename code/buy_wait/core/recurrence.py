"""Recurrence boundary for the explicit-ledger handoff.

The first implementation slice is intentionally not a complete household
forecaster. Until inference lands, potentially recurring history/series claims
block proof instead of silently certifying an explicit-only forecast as safe.
"""
from buy_wait.contracts import SeriesTarget
from buy_wait.core.normalize import issue


def infer_occurrences(data, resolved, facts, anchor, explicit, policy):
    history = [r for r in resolved if r.disposition == "anchored_history"]
    needed = len(history) >= policy.periodic_min_occurrences or any(
        f.scope == "series" or isinstance(f.target, SeriesTarget) for f in facts)
    if needed or policy.projection_mode == "explicit_only":
        return (), tuple(explicit), (), (issue(
            "RECURRENCE_SLICE_INCOMPLETE", "explicit-ledger slice cannot yet certify history-based recurrence",
            impact="recurrence"),)
    return (), tuple(explicit), (), ()
