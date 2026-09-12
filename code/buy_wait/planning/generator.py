"""Exact schedules and bounded candidate families using core-owned records.

This module never simulates cash or determines savings. Emitted canonical Plans
must pass static checks and the sole core replay before ranking can certify them.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from buy_wait.contracts import Money, Payment


def _positive_int(value: object, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _money(value: Money, field: str, *, allow_zero: bool = True) -> None:
    if not getattr(value, "currency", None):
        raise ValueError(f"{field} requires a currency")
    minor = getattr(value, "minor", None)
    if type(minor) is not int or minor < 0 or (minor == 0 and not allow_zero):
        raise ValueError(f"{field} must be exact nonnegative minor units")


def _day(value: date, field: str) -> None:
    # datetime is a subclass of date, but intraday values are not this contract.
    if type(value) is not date:
        raise ValueError(f"{field} must be a calendar date")


def option_completion_date(option: object) -> date:
    """Compute the supplied last date before allocating any payment tuple."""
    _day(option.first_payment_date, "first_payment_date")
    count = _positive_int(option.number_of_payments, "number_of_payments")
    if option.payment_method == "full_payment":
        if count != 1 or option.payment_frequency_days is not None:
            raise ValueError("full_payment requires one payment and no interval")
        return option.first_payment_date
    if option.payment_method != "installments":
        raise ValueError("only supplied full_payment/installments may be expanded")
    interval = _positive_int(option.payment_frequency_days, "payment_frequency_days")
    try:
        return option.first_payment_date + timedelta(days=(count - 1) * interval)
    except OverflowError as exc:
        raise ValueError("supplied schedule exceeds the calendar range") from exc


def expand_supplied_option(
    option: object, *, request_date: date, horizon_end: date
) -> tuple[Payment, ...]:
    """Expand unchanged amounts and fixed-day spacing within the known horizon.

    Fees are already represented by the supplied per-payment amount. This helper
    neither divides principal nor reconciles a malformed declared total by
    altering the final installment. Static evaluation performs that reconciliation.
    """
    from buy_wait.contracts import Payment

    _day(request_date, "request_date")
    _day(horizon_end, "horizon_end")
    if horizon_end < request_date:
        raise ValueError("forecast ends before the request")
    _money(option.payment_amount, "payment_amount")
    if not isinstance(option.payment_option_id, str) or not option.payment_option_id:
        raise ValueError("supplied payment_option_id is required")
    last = option_completion_date(option)
    if option.first_payment_date < request_date or last > horizon_end:
        raise ValueError("supplied schedule is outside the request forecast")
    interval = option.payment_frequency_days or 0
    return tuple(
        Payment(
            date=option.first_payment_date + timedelta(days=index * interval),
            amount=option.payment_amount,
            payment_id=f"{option.payment_option_id}:payment:{index + 1}",
        )
        for index in range(option.number_of_payments)
    )


def prescribed_partial_payments(
    request: object, preferences: object, capacity: object, *, horizon_end: date
) -> tuple[Payment, ...]:
    """Build the only allowed two-part schedule; no seller partial row needed.

    Invalid prerequisites raise ValueError rather than falling back to a made-up
    payment amount/date. The evaluator independently repeats the contract checks.
    """
    from buy_wait.contracts import Money, Payment

    _day(request.request_date, "request_date")
    _day(request.desired_completion_date, "desired_completion_date")
    _day(horizon_end, "horizon_end")
    _money(request.requested_amount, "requested_amount")
    if request.allows_partial_payment is not True:
        raise ValueError("request does not allow partial payment")
    if "partial_payment" not in preferences.methods:
        raise ValueError("user does not accept partial payment")
    if (capacity.proof_status not in {"resolved_under_policy", "conservative_bound"}
            or capacity.baseline_feasible is not True):
        raise ValueError("no verified feasible no-change capacity")
    safe = capacity.amount_safe_to_pay
    if safe is None:
        raise ValueError("unknown capacity is not zero")
    _money(safe, "amount_safe_to_pay", allow_zero=True)
    if safe.currency != request.requested_amount.currency:
        raise ValueError("capacity currency differs from request currency")
    if not 0 < safe.minor < request.requested_amount.minor:
        raise ValueError("partial requires 0 < safe_today < requested_amount")
    end = capacity.earliest_date_for_full_payment
    if type(end) is not date:
        raise ValueError("partial requires a known no-change full date")
    if not request.request_date < end <= min(request.desired_completion_date, horizon_end):
        raise ValueError("prescribed partial completion is outside the allowed dates")
    remainder = Money(
        currency=safe.currency, minor=request.requested_amount.minor - safe.minor
    )
    return (
        Payment(date=request.request_date, amount=safe,
                payment_id=f"{request.request_id}:partial:1"),
        Payment(date=end, amount=remainder,
                payment_id=f"{request.request_id}:partial:2"),
    )


def one_shot_payment(
    request: object, preferences: object, *, payment_date: date, horizon_end: date
) -> tuple[Payment, ...]:
    """Construct full today or a dated future one-shot, without asserting safety."""
    from buy_wait.contracts import Payment

    _day(request.request_date, "request_date")
    _day(request.desired_completion_date, "desired_completion_date")
    _day(payment_date, "payment_date")
    _day(horizon_end, "horizon_end")
    _money(request.requested_amount, "requested_amount")
    if "full_payment" not in preferences.methods:
        raise ValueError("full_payment acceptance is required, including for wait")
    if not request.request_date <= payment_date <= min(
        request.desired_completion_date, horizon_end
    ):
        raise ValueError("one-shot payment misses the request dates")
    return (Payment(date=payment_date, amount=request.requested_amount,
                    payment_id=f"{request.request_id}:one-shot:{payment_date.isoformat()}"),)


def action_families(envelope, core) -> dict:
    """Enumerate <=3 distinct-series Stop/floor-ReduceTo representatives.

    Only metadata bounds are calculated here. Core alone applies effects. Exact
    metadata-identical aliases share a serialization representative; all alias
    domains remain in the trace, without a financial preference between them.
    Covered-budget dependencies between changed occurrences invalidate the simple
    monotonicity certificate and are reported unresolved, never silently pruned.
    """
    from itertools import combinations, product
    from buy_wait.contracts import ReduceTo, Stop, canonical_hash

    by_id = {o.occurrence_id: o for o in core.occurrences}
    grouped, exclusions, seen_targets = {}, [], set()
    preferences = envelope.preferences
    for target in sorted(core.change_targets, key=lambda t: (t.series_id, t.anchor_event_id)):
        eid = target.anchor_event_id
        if eid in seen_targets:
            exclusions.append({"code": "CHANGE_TARGET_MAP_AMBIGUOUS", "event_id": eid,
                               "severity": "unverifiable"})
            continue
        seen_targets.add(eid)
        if target.category in preferences.protected_categories:
            exclusions.append({"code": "ACTION_PROTECTED", "event_id": eid, "severity": "reject"})
            continue
        occurrences = tuple(by_id.get(oid) for oid in target.eligible_occurrence_ids)
        if (not occurrences or any(o is None or not o.editable or o.direction != "debit"
                                   or o.pending_reservation_id or o.series_id != target.series_id
                                   or type(o.cash_date) is not date
                                   or not core.request_date <= o.cash_date <= core.horizon_end
                                   for o in occurrences)):
            exclusions.append({"code": "ACTION_TARGET_UNRESOLVED", "event_id": eid,
                               "severity": "unverifiable"})
            continue
        choices = grouped.setdefault(target.series_id, [])
        if ("stop" in target.allowed_actions and target.category in preferences.stoppable_categories
                and target.flexibility in {"stoppable", "reducible_or_stoppable"}):
            choices.append((Stop(eid), target, {"kind": "stop"}))
        if ("reduce_to" in target.allowed_actions and target.category in preferences.reducible_categories
                and target.flexibility in {"reducible", "reducible_or_stoppable"}):
            floor = target.floor
            if floor is None or floor.currency != core.requested.currency:
                exclusions.append({"code": "ACTION_FLOOR_UNKNOWN", "event_id": eid,
                                   "severity": "unverifiable"})
                continue
            ceiling = min(o.budget_total_minor if o.budget_total_minor is not None else o.home_amount.minor
                          for o in occurrences)
            if floor.minor > ceiling:
                exclusions.append({"code": "ACTION_FLOOR_EXCEEDS_EXPENSE", "event_id": eid,
                                   "floor_minor": floor.minor, "ceiling_minor": ceiling, "severity": "reject"})
                continue
            choices.append((ReduceTo(eid, floor), target,
                            {"kind": "reduce_to", "minimum_minor": floor.minor,
                             "maximum_minor": ceiling, "representative_minor": floor.minor}))
    groups = []
    for sid, choices in sorted(grouped.items()):
        equivalent = {}
        for change, target, domain in choices:
            signature = canonical_hash({
                "series_id": sid, "category": target.category, "flexibility": target.flexibility,
                "floor": target.floor, "allowed_actions": sorted(target.allowed_actions),
                "occurrence_ids": target.eligible_occurrence_ids, "domain": domain,
                "action": type(change).__name__, "amount": getattr(change, "new_amount", None),
            })
            equivalent.setdefault(signature, []).append((change, target, domain))
        if equivalent:
            # Input anchors were sorted above. This selects serialization only
            # within proven effect/eligibility-equivalent alias classes, not rank.
            representatives = tuple((*values[0], tuple(v[1].anchor_event_id for v in values))
                                    for values in equivalent.values())
            groups.append((sid, representatives))
    families = []
    for size in range(1, min(3, len(groups)) + 1):
        for selected_groups in combinations(groups, size):
            for choice in product(*(choices for _, choices in selected_groups)):
                changes = tuple(item[0] for item in choice)
                targets = tuple(item[1] for item in choice)
                occurrence_ids = [oid for target in targets for oid in target.eligible_occurrence_ids]
                if len(occurrence_ids) != len(set(occurrence_ids)):
                    exclusions.append({"code": "ACTION_OVERLAPPING_OCCURRENCES", "severity": "reject"})
                    continue
                changed_ids = set(occurrence_ids)
                if any(changed_ids.intersection(by_id[oid].covered_occurrence_ids) for oid in occurrence_ids):
                    exclusions.append({"code": "ACTION_MONOTONICITY_UNVERIFIED", "severity": "unverifiable",
                                       "event_ids": tuple(t.anchor_event_id for t in targets)})
                    continue
                families.append({
                    "family_id": "changes:" + canonical_hash(changes), "changes": changes,
                    "series_ids": tuple(t.series_id for t in targets),
                    "event_ids": tuple(t.anchor_event_id for t in targets),
                    "occurrence_ids": tuple(occurrence_ids), "amount_domains": tuple(item[2] for item in choice),
                    "alias_domains": tuple(item[3] for item in choice),
                    "alias_proof": "identical_core_target_metadata_serialization_only",
                    "proof": "I006_monotone_expense_only_fixed_covered_occurrences",
                    "policy_version": core.policy.version,
                })
    return {"families": tuple(families), "exclusions": tuple(exclusions),
            "coverage": "incomplete" if any(e["severity"] == "unverifiable" for e in exclusions)
            else "optimum_preserving_representatives"}


def generate_candidates(envelope, core, *, phase="no_changes", max_candidates=None) -> dict:
    """Produce canonical Plans, exclusions and explicit search-coverage evidence.

    Financial feasibility is never guessed here. No-change capacity controls the
    prescribed partial/wait grammar only; every materialized plan is evaluated
    through the sole core replay. Changes never shift a supplied offer schedule.
    """
    from buy_wait.contracts import Plan, canonical_hash
    from buy_wait.planning.evaluator import supplied_option_rejections

    if phase not in {"no_changes", "with_changes"}:
        raise ValueError("unknown generation phase")
    if max_candidates is not None and (type(max_candidates) is not int or max_candidates < 0):
        raise ValueError("candidate limit must be a nonnegative integer or None")
    request = envelope.request
    plans, exclusions, families = [], [], ()
    plan_ids = set()
    envelope_hash = envelope.context_hash
    complete = True
    if (request.request_id != core.request_id or request.user_id != core.user_id
            or request.request_date != core.request_date or request.requested_amount != core.requested):
        return {"phase": phase, "plans": (), "families": (), "coverage": "incomplete",
                "exclusions": ({"code": "CONTEXT_MISMATCH", "severity": "unverifiable"},)}
    if core.capacity.proof_status not in {"resolved_under_policy", "conservative_bound"}:
        return {"phase": phase, "plans": (), "families": (), "coverage": "incomplete",
                "exclusions": ({"code": "CORE_PROOF_UNRESOLVED", "severity": "unverifiable",
                                "issue_codes": core.capacity.issue_codes},)}

    def add(method, payments, changes=(), option_id=None, origin="rule_full", family=""):
        nonlocal complete
        if max_candidates is not None and len(plans) >= max_candidates:
            if complete:
                exclusions.append({"code": "SEARCH_LIMIT_REACHED", "severity": "unverifiable",
                                   "limit": max_candidates})
            complete = False
            return False
        identity = {"core": core.context_hash, "envelope": envelope_hash,
                    "method": method, "payments": payments, "changes": changes,
                    "option_id": option_id}
        candidate = Plan(candidate_id="candidate:" + canonical_hash(identity), method=method,
                         payments=payments, core_hash=core.context_hash, envelope_hash=envelope_hash,
                         changes=changes, payment_option_id=option_id, origin=origin, family=family,
                         evidence_ids=tuple(c.anchor_event_id for c in changes))
        if candidate.candidate_id not in plan_ids:
            plan_ids.add(candidate.candidate_id)
            plans.append(candidate)
        return True

    offered, full_rows = [], []
    for key, option in sorted(envelope.options_by_id.items()):
        if option.payment_method == "full_payment":
            full_rows.append(option)
        if key != option.payment_option_id:
            exclusions.append({"code": "OPTION_REGISTRY_ID_MISMATCH", "severity": "reject", "option_id": key})
            continue
        reasons = supplied_option_rejections(option, envelope, core)
        if reasons:
            # An independently forbidden offer cannot become eligible by fixing
            # unknown metadata. Retain the ambiguity, but it cannot affect optimum.
            certainly_ineligible = any(i["severity"] == "reject" for i in reasons)
            exclusions.extend({**i, "blocks_completeness": not certainly_ineligible} for i in reasons)
            continue
        if option.payment_method == "full_payment" and option.first_payment_date != core.request_date:
            exclusions.append({"code": "FULL_OPTION_NOT_TODAY", "severity": "reject", "option_id": key})
            continue
        offered.append((option, expand_supplied_option(option, request_date=core.request_date,
                                                       horizon_end=core.horizon_end)))
    if phase == "no_changes":
        for option, payments in offered:
            if not add(option.payment_method, payments, option_id=option.payment_option_id, origin="supplied_option"):
                break
        if complete and not full_rows and "full_payment" in envelope.preferences.methods:
            add("full_payment", one_shot_payment(request, envelope.preferences,
                payment_date=request.request_date, horizon_end=core.horizon_end))
        if complete:
            try:
                partial = prescribed_partial_payments(request, envelope.preferences, core.capacity,
                                                       horizon_end=core.horizon_end)
            except ValueError as exc:
                exclusions.append({"code": "PARTIAL_PREREQUISITE_FAILED", "severity": "reject",
                                   "detail": str(exc), "safe_today": core.capacity.amount_safe_to_pay,
                                   "full_date": core.capacity.earliest_date_for_full_payment})
            else:
                add("partial_payment", partial, origin="rule_partial")
        earliest = core.capacity.earliest_date_for_full_payment
        if (complete and earliest is not None and earliest > request.request_date
                and "full_payment" in envelope.preferences.methods):
            try:
                payment = one_shot_payment(request, envelope.preferences, payment_date=earliest,
                                           horizon_end=core.horizon_end)
            except ValueError as exc:
                exclusions.append({"code": "WAIT_MISSES_DEADLINE", "severity": "reject", "detail": str(exc),
                                   "full_date": earliest, "deadline": request.desired_completion_date})
            else:
                add("wait", payment, origin="rule_wait")
    else:
        family_result = action_families(envelope, core)
        families = family_result["families"]
        exclusions.extend(family_result["exclusions"])
        for family in families:
            if not complete:
                break
            changes = family["changes"]
            for option, payments in offered:
                if not add(option.payment_method, payments, changes, option.payment_option_id,
                           "supplied_option", family["family_id"]):
                    break
            if not complete or "full_payment" not in envelope.preferences.methods:
                continue
            last = min(request.desired_completion_date, core.horizon_end)
            # Today uses its exact supplied full offer if one exists; later one-shot
            # payments are rule_wait, with no borrowed seller-option identity.
            start = request.request_date + timedelta(days=1 if full_rows else 0)
            for offset in range(max(0, (last - start).days + 1)):
                day = start + timedelta(days=offset)
                payments = one_shot_payment(request, envelope.preferences, payment_date=day,
                                            horizon_end=core.horizon_end)
                method = "full_payment" if day == request.request_date else "wait"
                if not add(method, payments, changes, origin="rule_full" if method == "full_payment" else "rule_wait",
                           family=family["family_id"]):
                    break
    incomplete = not complete or any(e["severity"] == "unverifiable" and e.get("blocks_completeness", True)
                                     for e in exclusions)
    return {"phase": phase, "plans": tuple(plans), "families": families, "exclusions": tuple(exclusions),
            "coverage": "incomplete" if incomplete else "exhaustive" if phase == "no_changes"
            else "optimum_preserving_representatives"}
