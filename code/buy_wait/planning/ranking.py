"""Published lexicographic ranking over proved plans, never learned weights.

Plan/Money are the core's records. Result/search dictionaries are options-owned
payloads; callers may use equivalent attribute views for integration fixtures.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date
import re


_PROVED = {"resolved_under_policy", "conservative_bound"}
_OPTION_ID = re.compile(r"payment_option_([0-9]+)\Z")


def _get(record, key):
    return record[key] if isinstance(record, Mapping) else getattr(record, key)


def option_id_key(option_id: str) -> int:
    """I007's documented natural-suffix interpretation, not string ordering."""
    if not isinstance(option_id, str):
        raise ValueError("option ID must be a string")
    match = _OPTION_ID.fullmatch(option_id)
    if match is None:
        raise ValueError("unsupported option ID; do not invent an ordering")
    return int(match.group(1))


def _rankable(result) -> bool:
    return (_get(result, "outcome") == "valid"
            and _get(result, "eligible") is True
            and _get(result, "completes_by_deadline") is True
            and _get(result, "replay_performed") is True
            and _get(result, "financial_proof_status") in _PROVED)


def published_rank_key(result) -> tuple:
    """First five published components; ID applicability is handled separately."""
    amount = _get(result, "actual_total_paid")
    if not getattr(amount, "currency", None) or type(amount.minor) is not int or amount.minor < 0:
        raise ValueError("actual total must be exact nonnegative home-currency money")
    first = _get(result, "first_payment_date")
    count = _get(result, "payment_count")
    changes = _get(result, "has_spending_changes")
    if type(first) is not date:
        raise ValueError("a ranked plan needs a calendar first-payment date")
    if type(count) is not int or count <= 0:
        raise ValueError("a ranked plan needs a positive payment count")
    if type(changes) is not bool:
        raise ValueError("change presence is a Boolean, not an action-count score")
    return (not _get(result, "completes_by_deadline"), changes, amount.minor, first, count)


def best_ranked_candidates(validations: tuple) -> tuple:
    """Best known safe set; complete-search proof is checked by rank_validated."""
    valid = tuple(result for result in validations if _rankable(result))
    if not valid:
        return ()
    identities = {(_get(r, "request_id"), _get(r, "plan").core_hash,
                   _get(r, "plan").envelope_hash, _get(r, "actual_total_paid").currency) for r in valid}
    if len(identities) != 1:
        raise ValueError("cannot rank across requests, contexts or currencies")
    best_key = min(published_rank_key(r) for r in valid)
    first_five = tuple(r for r in valid if published_rank_key(r) == best_key)
    with_id = tuple(r for r in first_five if _get(r, "plan").payment_option_id is not None)
    without_id = tuple(r for r in first_five if _get(r, "plan").payment_option_id is None)
    if not with_id:
        return without_id
    lowest = min(option_id_key(_get(r, "plan").payment_option_id) for r in with_id)
    return tuple(r for r in with_id if option_id_key(_get(r, "plan").payment_option_id) == lowest) + without_id


def search_rejections(search, validations: tuple) -> tuple[dict, ...]:
    """No truncated search, unresolved exclusion, duplicate or fake P2 witness."""
    issues = []
    if _get(search, "no_change_phase_complete") is not True:
        issues.append({"code": "NO_CHANGE_SEARCH_INCOMPLETE"})
    changed_phase = _get(search, "changed_phase")
    if changed_phase not in {"complete", "skipped_by_P2"}:
        issues.append({"code": "CHANGED_SEARCH_INCOMPLETE"})
    exclusions = _get(search, "unresolved_exclusions")
    if exclusions:
        issues.append({"code": "UNRESOLVED_GENERATION_EXCLUSIONS", "count": len(exclusions)})
    generated, evaluated = _get(search, "generated_count"), _get(search, "evaluated_count")
    if (type(generated) is not int or type(evaluated) is not int or generated < 0 or evaluated < 0
            or generated != evaluated or evaluated != len(validations)):
        issues.append({"code": "UNEVALUATED_CANDIDATES"})
    seen = set()
    for result in validations:
        plan = _get(result, "plan")
        if plan.candidate_id in seen:
            issues.append({"code": "DUPLICATE_VALIDATION_PLAN", "candidate_id": plan.candidate_id})
        seen.add(plan.candidate_id)
        outcome = _get(result, "outcome")
        if outcome not in {"valid", "invalid", "unverifiable"}:
            issues.append({"code": "UNKNOWN_VALIDATION_OUTCOME", "candidate_id": plan.candidate_id})
        if (_get(result, "request_id") != _get(search, "request_id")
                or plan.core_hash != _get(search, "core_hash")
                or plan.envelope_hash != _get(search, "envelope_hash")):
            issues.append({"code": "SEARCH_CONTEXT_MISMATCH", "candidate_id": plan.candidate_id})
        if outcome == "unverifiable" or outcome == "valid" and not _rankable(result):
            issues.append({"code": "UNVERIFIED_CANDIDATE", "candidate_id": plan.candidate_id})
    coverage_defects = (search.get("coverage_defects", ()) if isinstance(search, Mapping)
                        else getattr(search, "coverage_defects", ()))
    issues.extend(coverage_defects)
    if changed_phase == "skipped_by_P2":
        witnesses = set(_get(search, "pruning_witness_plan_ids"))
        if not any(_rankable(r) and not _get(r, "has_spending_changes")
                   and _get(r, "plan").candidate_id in witnesses for r in validations):
            issues.append({"code": "NO_VALID_P2_WITNESS"})
    return tuple(issues)


def serialization_representative(finalists: tuple) -> tuple[object | None, str]:
    """I007 stable serialization ONLY among already-best finalists, not a score."""
    if not finalists:
        return None, "no_valid_plan"
    if len(finalists) == 1:
        return finalists[0], "unique"

    def canonical(result):
        plan = _get(result, "plan")
        payments = tuple((p.date.isoformat(), p.amount.currency, p.amount.minor) for p in plan.payments)
        changes = tuple(sorted(
            (type(c).__name__, c.anchor_event_id,
             getattr(getattr(c, "new_amount", None), "currency", ""),
             getattr(getattr(c, "new_amount", None), "minor", -1)) for c in plan.changes
        ))
        return plan.method, plan.payment_option_id or "", payments, changes, plan.candidate_id

    return min(finalists, key=canonical), "serialization_only"


def build_search_coverage(batches, validations, *, envelope, core,
                          pruning_witness_plan_ids=()) -> dict:
    """Bind complete phases, exact evaluated Plans and genuine P2 witnesses.

    Integration calls this after evaluating all emitted candidates. A missing
    changed phase is permitted only with named, validated no-change witnesses;
    merely generating a no-change plan never licenses pruning.
    """
    batches, validations = tuple(batches), tuple(validations)
    no_change = tuple(b for b in batches if b["phase"] == "no_changes")
    changed = tuple(b for b in batches if b["phase"] == "with_changes")
    defects = []
    if len(no_change) != 1 or len(changed) > 1 or len(batches) != len(no_change) + len(changed):
        defects.append({"code": "GENERATION_PHASE_SET_INVALID"})
    plans = tuple(p for b in batches for p in b["plans"])
    by_id = {p.candidate_id: p for p in plans}
    if len(by_id) != len(plans):
        defects.append({"code": "DUPLICATE_GENERATED_PLAN"})
    if (set(by_id) != {_get(v, "plan").candidate_id for v in validations}
            or any(by_id.get(_get(v, "plan").candidate_id) != _get(v, "plan") for v in validations)):
        defects.append({"code": "GENERATED_VALIDATED_PLAN_MISMATCH"})
    witnesses = tuple(pruning_witness_plan_ids)
    no_change_ids = {p.candidate_id for b in no_change for p in b["plans"]}
    if any(witness not in no_change_ids for witness in witnesses):
        defects.append({"code": "P2_WITNESS_NOT_IN_NO_CHANGE_PHASE"})
    if changed:
        changed_phase = "complete" if changed[0]["coverage"] in {
            "exhaustive", "optimum_preserving_representatives"} else "incomplete"
    else:
        changed_phase = "skipped_by_P2" if witnesses else "incomplete"
    return {
        "request_id": core.request_id, "core_hash": core.context_hash,
        "envelope_hash": envelope.context_hash,
        "no_change_phase_complete": len(no_change) == 1 and no_change[0]["coverage"] == "exhaustive",
        "changed_phase": changed_phase, "generated_count": len(plans), "evaluated_count": len(validations),
        "unresolved_exclusions": tuple(e for b in batches for e in b["exclusions"]
                                       if e.get("severity") == "unverifiable" and e.get("blocks_completeness", True)),
        "pruning_witness_plan_ids": witnesses, "coverage_defects": tuple(defects),
    }


def rank_validated(validations, *, search, option_id_order="natural_suffix") -> dict:
    """Canonical ranking boundary: no winner is certified on incomplete evidence."""
    if option_id_order != "natural_suffix":
        raise ValueError("only canonical I007 natural_suffix ordering is supported")
    validations = tuple(validations)
    issues = search_rejections(search, validations)
    try:
        best = best_ranked_candidates(validations)
        rank_keys = tuple((_get(r, "plan").candidate_id, published_rank_key(r))
                          for r in validations if _rankable(r))
    except ValueError as exc:
        issues += ({"code": "RANKING_INPUT_UNRESOLVED", "detail": str(exc)},)
        best, rank_keys = (), ()
    if issues:
        winner, resolution = None, "incomplete_evidence_or_search"
        tie = None
    else:
        winner, tie = serialization_representative(best)
        resolution = "no_valid_plan" if winner is None else "selected"
    return {"winner": winner, "best_set": best, "resolution": resolution,
            "tie_resolution": tie, "issues": issues, "rank_keys": rank_keys}


def recommendation_status(ranking: dict, core) -> dict:
    """I008 status projection; copy original capacity, never infer acceptance.

    Caller must resolve issues before CSV export. No-plan and unknown evidence
    are different: neither an incomplete search nor unknown money becomes zero.
    """
    capacity = core.capacity
    base = {"amount_safe_to_pay": capacity.amount_safe_to_pay,
            "earliest_date_for_full_payment": capacity.earliest_date_for_full_payment,
            "affordability_status": None, "recommended_payment_method": None, "issues": ()}
    if (ranking["resolution"] == "incomplete_evidence_or_search" or capacity.proof_status not in _PROVED
            or capacity.amount_safe_to_pay is None):
        return {**base, "issues": ({"code": "DECISION_UNRESOLVED"},)}
    winner = ranking["winner"]
    if winner is None:
        earliest = capacity.earliest_date_for_full_payment
        if earliest == core.request_date:
            return {**base, "recommended_payment_method": "not_recommended",
                    "issues": ({"code": "FALLBACK_STATUS_UNSPECIFIED"},)}
        return {**base, "recommended_payment_method": "not_recommended",
                "affordability_status": "affordable_later" if earliest is not None else "not_affordable"}
    if not _rankable(winner):
        raise ValueError("status projection requires a proved eligible selected plan")
    plan = _get(winner, "plan")
    if (plan.core_hash != core.context_hash or _get(winner, "request_id") != core.request_id):
        raise ValueError("selected plan does not belong to the capacity context")
    if _get(winner, "has_spending_changes") or plan.method in {"partial_payment", "installments"}:
        status = "affordable_with_plan"
    elif plan.method == "wait":
        status = "affordable_later"
    else:
        if capacity.earliest_date_for_full_payment != core.request_date:
            return {**base, "issues": ({"code": "CAPACITY_PLAN_INCOHERENCE"},)}
        status = "affordable_now"
    return {**base, "affordability_status": status, "recommended_payment_method": plan.method}
