"""Published lexicographic comparison, coverage checks and final-tie handling.

Helpers return existing validated candidates and small diagnostic payloads;
there are no learned weights, new shared dataclasses, or financial simulation.
Canonical RankingResult assembly belongs in the next shared-contract handoff.
"""
from __future__ import annotations

from datetime import date
import re


_PROVED = {"resolved_under_policy", "conservative_bound"}
_OPTION_ID = re.compile(r"payment_option_([0-9]+)\Z")


def option_id_key(option_id: str) -> int:
    """I007 interpretation: natural numeric suffix, preserving the original ID."""
    if not isinstance(option_id, str):
        raise ValueError("option ID must be a string")
    match = _OPTION_ID.fullmatch(option_id)
    if match is None:
        raise ValueError("unsupported option ID; do not invent an ordering")
    return int(match.group(1))


def _rankable(validation: object) -> bool:
    return (
        validation.outcome == "valid"
        and validation.eligible is True
        and validation.completes_by_deadline is True
        and validation.replay_performed is True
        and validation.financial_proof_status in _PROVED
    )


def published_rank_key(validation: object) -> tuple:
    """First five published criteria only; option applicability is a partial order."""
    amount = validation.actual_total_paid
    if not getattr(amount, "currency", None) or type(amount.minor) is not int or amount.minor < 0:
        raise ValueError("actual total must be exact nonnegative home-currency money")
    if type(validation.first_payment_date) is not date:
        raise ValueError("a ranked plan needs a calendar first-payment date")
    if type(validation.payment_count) is not int or validation.payment_count <= 0:
        raise ValueError("a ranked plan needs a positive payment count")
    if type(validation.has_spending_changes) is not bool:
        raise ValueError("change presence is a Boolean, not an action-count score")
    return (
        not validation.completes_by_deadline,
        validation.has_spending_changes,
        amount.minor,
        validation.first_payment_date,
        validation.payment_count,
    )


def best_ranked_candidates(validations: tuple) -> tuple:
    """Return the undominated best set among the supplied proved candidates.

    Call ``search_rejections`` before certifying this as a globally optimal
    recommendation: individually safe candidates do not prove complete search.
    Late, ineligible, unreplayed and unresolved candidates never enter ranking.
    No-ID candidates are incomparable at criterion six, not transitive ties
    that let a worse supplied ID survive through an equality bridge.
    """
    valid = tuple(result for result in validations if _rankable(result))
    if not valid:
        return ()
    identities = {
        (result.plan.request_id, result.plan.core_hash, result.plan.envelope_hash,
         result.actual_total_paid.currency)
        for result in valid
    }
    if len(identities) != 1:
        raise ValueError("cannot rank across requests, contexts or currencies")
    best_key = min(published_rank_key(result) for result in valid)
    first_five = tuple(result for result in valid if published_rank_key(result) == best_key)
    with_id = tuple(result for result in first_five if result.plan.supplied_option_id is not None)
    without_id = tuple(result for result in first_five if result.plan.supplied_option_id is None)
    if not with_id:
        return without_id
    lowest = min(option_id_key(result.plan.supplied_option_id) for result in with_id)
    best_offers = tuple(result for result in with_id
                        if option_id_key(result.plan.supplied_option_id) == lowest)
    return best_offers + without_id


def search_rejections(search: object, validations: tuple) -> tuple[dict, ...]:
    """Explicit search completeness checks, including a real P2 witness.

    Static hard exclusions do not count as generated candidates. Unresolved
    exclusions must be resolved or proven dominated by the generator before
    removal from this record; they cannot be ignored merely to return a winner.
    """
    issues = []
    if search.no_change_phase_complete is not True:
        issues.append({"code": "NO_CHANGE_SEARCH_INCOMPLETE"})
    if search.changed_phase not in {"complete", "skipped_by_P2"}:
        issues.append({"code": "CHANGED_SEARCH_INCOMPLETE"})
    if search.unresolved_exclusions:
        issues.append({"code": "UNRESOLVED_GENERATION_EXCLUSIONS",
                       "count": len(search.unresolved_exclusions)})
    if (type(search.generated_count) is not int or type(search.evaluated_count) is not int
            or search.generated_count < 0 or search.evaluated_count < 0
            or search.generated_count != search.evaluated_count
            or search.evaluated_count != len(validations)):
        issues.append({"code": "UNEVALUATED_CANDIDATES"})
    seen_ids = set()
    for result in validations:
        if result.plan.plan_id in seen_ids:
            issues.append({"code": "DUPLICATE_VALIDATION_PLAN", "plan_id": result.plan.plan_id})
        seen_ids.add(result.plan.plan_id)
        if result.outcome not in {"valid", "invalid", "unverifiable"}:
            issues.append({"code": "UNKNOWN_VALIDATION_OUTCOME", "plan_id": result.plan.plan_id})
        if (result.plan.request_id != search.request_id
                or result.plan.core_hash != search.core_hash
                or result.plan.envelope_hash != search.envelope_hash):
            issues.append({"code": "SEARCH_CONTEXT_MISMATCH", "plan_id": result.plan.plan_id})
        if (result.outcome == "unverifiable"
                or result.outcome == "valid" and not _rankable(result)):
            issues.append({"code": "UNVERIFIED_CANDIDATE", "plan_id": result.plan.plan_id})
    if search.changed_phase == "skipped_by_P2":
        witnesses = set(search.pruning_witness_plan_ids)
        if not any(_rankable(result) and not result.has_spending_changes
                   and result.plan.plan_id in witnesses for result in validations):
            issues.append({"code": "NO_VALID_P2_WITNESS"})
    return tuple(issues)


def serialization_representative(finalists: tuple) -> tuple[object | None, str]:
    """I007: deterministic representative ONLY after best-set computation.

    This is explicitly not an additional financial preference. It must never be
    applied to all candidates before the published comparator/coverage checks.
    """
    if not finalists:
        return None, "no_valid_plan"
    if len(finalists) == 1:
        return finalists[0], "unique"

    def canonical(result):
        plan = result.plan
        payments = tuple((p.date.isoformat(), p.amount.currency, p.amount.minor)
                         for p in plan.payments)
        changes = tuple(sorted(
            (type(c).__name__, c.anchor_event_id,
             getattr(getattr(c, "new_amount", None), "currency", ""),
             getattr(getattr(c, "new_amount", None), "minor", -1))
            for c in plan.changes
        ))
        return (plan.method, plan.supplied_option_id or "", payments, changes, plan.plan_id)

    return min(finalists, key=canonical), "serialization_only"
