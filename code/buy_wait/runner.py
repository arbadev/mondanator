"""Deterministic module composition, not a model-driven tool loop or release gate.

Only core owns financial truth and only planning selects/statuses plans. This
module retains their actual records/dictionaries and renders their conclusions.
Cache-only extraction has no client, credential lookup, budget or live mode.
Known dependency review limits still require a corrected, authorized release.
"""
from __future__ import annotations

from pathlib import Path

from buy_wait import contracts as c
from buy_wait.core import build_financial_context
from buy_wait.core.money import format_money
from buy_wait.data import Dataset, DataError, project_request
from buy_wait.evidence import (
    EvidenceError, EvidenceIndex, ExtractionCache, Extractor, UsageSink,
    adapt_extraction, event_descriptors, resolve_image,
)
from buy_wait.output import structural_issues
from buy_wait.planning.evaluator import evaluate_candidate
from buy_wait.planning.generator import generate_candidates
from buy_wait.planning.ranking import (
    best_ranked_candidates, build_search_coverage, rank_validated, recommendation_status,
)


def _render(recommendation, ranking, core):
    """Format existing conclusions only; never infer a fallback financial status."""
    if recommendation["issues"] or recommendation["affordability_status"] is None:
        return None
    amount = recommendation["amount_safe_to_pay"]
    if amount is None:
        return None
    winner = ranking["winner"]
    plan = winner["plan"] if winner is not None else None
    payments = "|".join(f"{p.date.isoformat()}:{format_money(p.amount)}" for p in plan.payments) if plan else "none"
    actions = []
    for change in plan.changes if plan else ():
        if isinstance(change, c.Stop):
            actions.append(f"stop:{change.anchor_event_id}")
        elif isinstance(change, c.ReduceTo):
            actions.append(f"reduce_to:{change.anchor_event_id}:{format_money(change.new_amount)}")
        else:
            raise DataError("unrecognized canonical spending action")
    earliest = recommendation["earliest_date_for_full_payment"]
    minimum = format_money(c.Money(amount.currency, core.anchor.minimum_minor))
    explanation = f"No-change safe amount today: {amount.currency} {format_money(amount)}; protected minimum {minimum}. "
    if winner is not None:
        option = f" via {plan.payment_option_id}" if plan.payment_option_id else ""
        explanation += (f"Selected {plan.method}{option}: {winner['payment_count']} payment(s), total "
                        f"{format_money(winner['actual_total_paid'])}, completes {winner['completion_date'].isoformat()}. "
                        f"Replay preserves the minimum through {core.horizon_end.isoformat()}; complete search uses the published ordering.")
    else:
        explanation += f"No eligible on-time plan in the complete conservative search through {core.horizon_end.isoformat()}."
    if core.capacity.proof_status == "conservative_bound":
        explanation += " Capacity uses a conservative bound, not exact resolution of every source."
    return {
        "request_id": core.request_id, "amount_safe_to_pay": format_money(amount),
        "affordability_status": recommendation["affordability_status"],
        "recommended_payment_method": recommendation["recommended_payment_method"],
        "payment_plan": payments, "earliest_date_for_full_payment": earliest.isoformat() if earliest else "",
        "spending_changes_needed": "|".join(actions) or "none", "decision_explanation": explanation,
    }


def plan_request(data: Dataset, request, *, facts: c.FactBatch, evidence_sources=(),
                 policy: c.ForecastPolicy | None = None, max_candidates: int | None = None):
    """Actual CSV/FactBatch -> core -> complete planning phases -> trace/optional row.

    An optional candidate cap is diagnostic only: incomplete search cannot emit a
    row. Source identity and financial admission stay in the canonical modules.
    No trace/result domain class or second replay/financial classifier is defined.
    """
    raw = project_request(request)
    if not isinstance(facts, c.FactBatch) or (facts.request_id, facts.user_id) != (raw["request_id"], raw["user_id"]):
        raise DataError("FactBatch identity differs from request")
    envelope = data.planning_context_for(raw)
    financial = data.financial_input_for(raw, evidence_sources=evidence_sources)
    core = build_financial_context(financial, facts, policy=policy or c.ForecastPolicy())
    first = generate_candidates(envelope, core, phase="no_changes", max_candidates=max_candidates)
    validations = tuple(evaluate_candidate(plan, envelope, core) for plan in first["plans"])
    witnesses = tuple(result["plan"].candidate_id for result in best_ranked_candidates(validations)
                      if not result["has_spending_changes"])
    batches = (first,)
    if not witnesses:
        changed = generate_candidates(envelope, core, phase="with_changes", max_candidates=max_candidates)
        batches += (changed,)
        validations += tuple(evaluate_candidate(plan, envelope, core) for plan in changed["plans"])
    search = build_search_coverage(batches, validations, envelope=envelope, core=core,
                                   pruning_witness_plan_ids=witnesses)
    ranking = rank_validated(validations, search=search)
    recommendation = recommendation_status(ranking, core)
    row = _render(recommendation, ranking, core)
    formatting_issues = structural_issues(row, raw) if row is not None else ()
    if formatting_issues:
        row = None  # Retain the defect, never silently change a financial conclusion.
    return {
        "schema_version": "buy-wait-decision-trace/v1", "request": dict(raw),
        "source_hashes": dict(data.source_hashes), "financial_input": financial,
        "facts": facts, "envelope": envelope, "core": core, "batches": batches,
        "validations": validations, "search": search, "ranking": ranking,
        "recommendation": recommendation, "formatting_issues": formatting_issues, "row": row,
    }


def decide_cached(data: Dataset, request, *, dataset_root: Path, cache: ExtractionCache,
                  usage: UsageSink, run_id: str, policy: c.ForecastPolicy | None = None,
                  max_candidates: int | None = None, bindings_by_source=None):
    """Hard cache-only composition using the actual evidence-owned APIs.

    Optional bindings are trusted host/core inputs to the existing adapter, not
    a new target classifier. Unbound series/new occurrences remain explicit
    adapter issues. Callers must bind dataset_root to the loaded participant tree.
    No answer-derived cache is constructed and no unresolved amount becomes zero.
    """
    raw = project_request(request)
    initial = data.financial_input_for(raw)
    events = {event.event_id: event for event in initial.events}
    descriptors = event_descriptors(events, user_id=initial.user_id)
    selection = EvidenceIndex.from_context(data.context_for(raw)).retrieve(
        user_id=initial.user_id, request_id=initial.request_id,
        event_ids=tuple(events), as_of=initial.request_date,
    )
    bindings = dict(bindings_by_source or {})
    if not set(bindings) <= {source.source_id for source in selection.sources}:
        raise DataError("host binding names an unselected source")
    extractor = Extractor(cache=cache, usage=usage, run_id=run_id)  # no client/budget/key
    facts, issues, sources, source_runs = [], [], {}, []
    for source in selection.sources:
        asset, asset_issue = None, None
        if source.kind == "image":
            try:
                asset = resolve_image(dataset_root, source.source_id.split(":", 1)[1])
            except EvidenceError as exc:
                asset_issue = exc.code  # Missing/invalid stays visible beside adapter's gap.
        result = extractor.extract(source, candidate_events=descriptors, asset=asset,
                                   as_of=initial.request_date, mode="cache-only")
        binding = dict(bindings.get(source.source_id, {}))
        if not set(binding) <= {"resolved_targets", "new_event_fields", "actor_key"}:
            raise DataError("host binding exceeds the canonical adapter surface")
        adapted = adapt_extraction(result, request_id=initial.request_id, user_id=initial.user_id,
                                   events=events, **binding)
        facts.extend(adapted.batch.facts)
        issues.extend(adapted.batch.issues)
        for reference in adapted.sources:
            if reference.source_id in sources and sources[reference.source_id] != reference:
                raise DataError("conflicting source reference identity")
            sources[reference.source_id] = reference
        source_runs.append({
            "source_id": source.source_id, "reasons": source.reasons, "row_number": source.row_number,
            "csv_sha256": source.csv_sha256, "row_sha256": source.row_sha256,
            "content_sha256": result.content_sha256, "cache_key": result.cache_key,
            "outcome": result.outcome, "issues": result.issues, "asset_issue": asset_issue,
            "attempt_ids": result.attempt_ids, "origin_usage": result.origin_usage,
        })
    batch = c.FactBatch(initial.request_id, initial.user_id, tuple(facts), tuple(issues))
    trace = plan_request(data, raw, facts=batch, evidence_sources=tuple(sources.values()),
                         policy=policy, max_candidates=max_candidates)
    trace["evidence"] = {"mode": "cache-only", "run_id": run_id, "sources": tuple(source_runs),
                         "excluded_future_ids": selection.excluded_future_ids}
    return trace


def trace_wire(trace):
    """Use the core's canonical wire serializer for retained financial records."""
    return c.canonical_data(trace)
