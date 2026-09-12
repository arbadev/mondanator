"""Public financial-core/v1 seam, owned by one deterministic simulator."""
from dataclasses import replace

from buy_wait.contracts import CoreContext, FactBatch, FinancialInput, ForecastPolicy
from buy_wait.core.capacity import compute_capacity
from buy_wait.core.forecast import (
    context_fingerprint, explicit_occurrences, project, replay_financial_plan,
)
from buy_wait.core.normalize import normalize_events
from buy_wait.core.recurrence import infer_occurrences


def build_financial_context(data: FinancialInput, facts: FactBatch,
                            *, policy: ForecastPolicy) -> CoreContext:
    resolved, usable, issues = normalize_events(data, facts)
    anchor, explicit, issues = explicit_occurrences(data, resolved, policy, issues)
    series, occurrences, targets, recurrence_issues = infer_occurrences(
        data, resolved, usable, anchor, explicit, policy)
    issues = tuple(sorted((*issues, *recurrence_issues),
                          key=lambda i: (i.code, i.target_ids, i.source_ids, i.detail)))
    baseline = project(anchor, occurrences, policy, issues=issues)
    capacity = compute_capacity(baseline, data.requested, anchor.minimum_minor,
                                request_date=data.request_date)
    capacity = replace(capacity, policy_version=policy.version)
    core = CoreContext("", policy, data.request_id, data.user_id, data.request_date,
                       data.requested, anchor, baseline.end, resolved, series,
                       occurrences, baseline, capacity, targets, issues)
    fingerprint = context_fingerprint(core)
    return replace(core, context_hash=fingerprint,
                   capacity=replace(capacity, context_hash=fingerprint, policy_version=policy.version))


__all__ = ["build_financial_context", "compute_capacity", "replay_financial_plan"]
