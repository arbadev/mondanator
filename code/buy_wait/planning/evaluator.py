"""Static option checks plus the sole core-owned full-horizon replay adapter.

Returned dictionaries are options-owned diagnostic/result payloads, not new
financial dataclasses. Money, Plan, Payment, changes and all numerical state
remain owned by buy_wait.contracts / buy_wait.core.
"""
from __future__ import annotations

from datetime import date, timedelta

from buy_wait.contracts import Money, Plan, ReduceTo, Stop
from buy_wait.core import replay_financial_plan
from buy_wait.planning.generator import _money, option_completion_date


_PROVED = {"resolved_under_policy", "conservative_bound"}


def _issue(code, stage, **details):
    return {"code": code, "stage": stage, "severity": "reject", **details}


def supplied_option_rejections(option, envelope, core) -> tuple[dict, ...]:
    """Check supplied offer metadata before allocating a potentially long plan."""
    request, preferences = envelope.request, envelope.preferences
    issues = []
    oid = option.payment_option_id
    if option.request_id != request.request_id:
        issues.append(_issue("OPTION_WRONG_REQUEST", "option", option_id=oid))
    if option.payment_method not in preferences.methods or isinstance(preferences.methods, str):
        issues.append(_issue("METHOD_NOT_ACCEPTED", "eligibility", method=option.payment_method))
    try:
        last = option_completion_date(option)
        _money(option.payment_amount, "payment_amount")
        _money(option.financing_fee, "financing_fee")
        _money(option.total_payable_amount, "total_payable_amount")
        if any(amount.currency != request.requested_amount.currency for amount in (
            option.payment_amount, option.financing_fee, option.total_payable_amount
        )):
            raise ValueError("option currency differs from request currency")
    except ValueError as exc:
        issues.append(_issue("OPTION_METADATA_INVALID", "option", option_id=oid, detail=str(exc)))
        return tuple(issues)
    if option.first_payment_date < request.request_date or last > core.horizon_end:
        issues.append(_issue("OPTION_OUTSIDE_FORECAST", "option", option_id=oid,
                             first_date=option.first_payment_date, last_date=last))
    if last > request.desired_completion_date:
        issues.append(_issue("OPTION_MISSES_DEADLINE", "option", option_id=oid,
                             completion_date=last, deadline=request.desired_completion_date))
    cash = option.payment_amount.minor * option.number_of_payments
    declared = option.total_payable_amount.minor
    expected = request.requested_amount.minor + option.financing_fee.minor
    if cash != declared:
        issues.append(_issue("OPTION_TOTAL_MISMATCH", "option", option_id=oid,
                             actual_minor=cash, expected_minor=declared, delta_minor=cash - declared,
                             severity="unverifiable"))
    if declared != expected:
        issues.append(_issue("OPTION_FEE_MISMATCH", "option", option_id=oid,
                             actual_minor=declared, expected_minor=expected, delta_minor=declared - expected,
                             severity="unverifiable"))
    if option.payment_method == "installments":
        cap = preferences.max_installment_months
        if type(cap) is not int or cap <= 0:
            issues.append(_issue("INSTALLMENT_CAP_UNRESOLVED", "eligibility", cap=cap,
                                 severity="unverifiable"))
        elif option.number_of_payments > cap:
            issues.append(_issue("INSTALLMENT_CAP_EXCEEDED", "eligibility", cap=cap,
                                 payment_count=option.number_of_payments))
    return tuple(issues)


def static_rejections(plan: Plan, envelope, core) -> tuple[dict, ...]:
    """Static contract only; no empty-issues result may stand in for cash safety."""
    issues = []

    def reject(code, stage, **details):
        issues.append(_issue(code, stage, **details))

    request, preferences = envelope.request, envelope.preferences
    # Canonical Plan has no request_id: the core hash binds the financial request.
    if (request.request_id != core.request_id or request.user_id != core.user_id
            or request.request_date != core.request_date):
        reject("REQUEST_IDENTITY_MISMATCH", "identity")
    if plan.core_hash != core.context_hash or plan.envelope_hash != envelope.context_hash:
        reject("CONTEXT_HASH_MISMATCH", "identity")
    if core.capacity.proof_status not in _PROVED or core.capacity.amount_safe_to_pay is None:
        reject("CAPACITY_UNRESOLVED", "capacity", severity="unverifiable")
    if any(type(day) is not date for day in (
        request.request_date, request.desired_completion_date, core.horizon_end
    )):
        reject("INVALID_CONTEXT_DATE", "identity")
        return tuple(issues)
    try:
        _money(request.requested_amount, "requested_amount")
        _money(core.requested, "core.requested")
    except ValueError as exc:
        reject("INVALID_REQUEST_MONEY", "identity", detail=str(exc))
        return tuple(issues)
    currency = request.requested_amount.currency
    if core.requested != request.requested_amount:
        reject("CORE_REQUEST_MISMATCH", "identity")
    methods = preferences.methods
    if isinstance(methods, str):
        reject("METHOD_PREFERENCES_MALFORMED", "eligibility")
        methods = ()
    accepted = "full_payment" if plan.method == "wait" else plan.method
    if plan.method not in {"full_payment", "partial_payment", "installments", "wait"}:
        reject("UNKNOWN_PAYMENT_METHOD", "eligibility", method=plan.method)
    elif accepted not in methods:
        reject("METHOD_NOT_ACCEPTED", "eligibility", method=accepted)

    payments = plan.payments
    if not payments:
        reject("EMPTY_PAYMENT_PLAN", "schedule")
    seen_ids = set()
    valid_payments = True
    previous_date = None
    for index, payment in enumerate(payments):
        if not isinstance(payment.payment_id, str) or not payment.payment_id:
            reject("PAYMENT_ID_MISSING", "schedule", payment_index=index)
        elif payment.payment_id in seen_ids:
            reject("PAYMENT_ID_DUPLICATE", "schedule", payment_index=index)
        else:
            seen_ids.add(payment.payment_id)
        if type(payment.date) is not date:
            reject("INVALID_PAYMENT_DATE", "schedule", payment_index=index)
            valid_payments = False
            continue
        if previous_date is not None and payment.date < previous_date:
            reject("NONCHRONOLOGICAL", "schedule", payment_index=index, date=payment.date)
        previous_date = payment.date
        if payment.date < request.request_date:
            reject("BEFORE_REQUEST_DATE", "schedule", payment_index=index, date=payment.date)
        if payment.date > core.horizon_end:
            reject("OUTSIDE_FORECAST", "schedule", payment_index=index, date=payment.date)
        if payment.date > request.desired_completion_date:
            reject("MISSES_DEADLINE", "schedule", payment_index=index, date=payment.date,
                   deadline=request.desired_completion_date)
        try:
            _money(payment.amount, "payment.amount")
        except ValueError as exc:
            reject("INVALID_PAYMENT_AMOUNT", "schedule", payment_index=index, detail=str(exc))
            valid_payments = False
            continue
        if payment.amount.currency != currency:
            reject("PAYMENT_CURRENCY_MISMATCH", "schedule", payment_index=index)
            valid_payments = False
    if valid_payments and payments:
        principal = request.requested_amount.minor
        paid = sum(payment.amount.minor for payment in payments)
        if plan.method in {"full_payment", "partial_payment", "wait"} and paid != principal:
            reject("PRINCIPAL_SUM_MISMATCH", "schedule", actual_minor=paid, expected_minor=principal)
        if plan.method == "full_payment" and (len(payments) != 1 or payments[0].date != request.request_date):
            reject("FULL_PAYMENT_GRAMMAR_MISMATCH", "schedule")
        if plan.method == "wait":
            if len(payments) != 1 or payments[0].date <= request.request_date:
                reject("WAIT_GRAMMAR_MISMATCH", "schedule")
            if not plan.changes:
                earliest = core.capacity.earliest_date_for_full_payment
                if type(earliest) is not date or payments[0].date < earliest:
                    reject("WAIT_FULL_DATE_UNAVAILABLE", "capacity", expected_date=earliest)
                elif earliest <= request.request_date:
                    reject("WAIT_NOT_LATER_CAPACITY", "eligibility", expected_date=earliest)
        if plan.method == "partial_payment":
            if core.capacity.baseline_feasible is not True:
                reject("PARTIAL_BASELINE_UNVERIFIED", "capacity")
            if request.allows_partial_payment is not True:
                reject("REQUEST_PARTIAL_DISABLED", "eligibility")
            safe, earliest = core.capacity.amount_safe_to_pay, core.capacity.earliest_date_for_full_payment
            safe_valid = False
            if safe is not None:
                try:
                    _money(safe, "safe_today")
                    safe_valid = safe.currency == currency and 0 < safe.minor < principal
                except ValueError:
                    pass
            if not safe_valid:
                reject("PARTIAL_CAPACITY_OUT_OF_RANGE", "capacity")
            if (type(earliest) is not date or earliest <= request.request_date
                    or earliest > min(request.desired_completion_date, core.horizon_end)):
                reject("PARTIAL_DATE_UNAVAILABLE", "capacity", expected_date=earliest)
            if safe_valid and type(earliest) is date:
                expected = ((request.request_date, safe.minor), (earliest, principal - safe.minor))
                actual = tuple((p.date, p.amount.minor) for p in payments)
                if actual != expected:
                    reject("PARTIAL_GRAMMAR_MISMATCH", "schedule", expected=expected, actual=actual)

    oid = plan.payment_option_id
    if plan.method == "installments" and not oid:
        reject("SUPPLIED_OPTION_REQUIRED", "option")
    if plan.method in {"partial_payment", "wait"} and oid is not None:
        reject("RULE_PLAN_BORROWED_OPTION_ID", "option", option_id=oid)
    if oid is not None:
        option = envelope.options_by_id.get(oid)
        if option is None:
            reject("UNKNOWN_OPTION", "option", option_id=oid)
        else:
            if option.payment_option_id != oid:
                reject("OPTION_REGISTRY_ID_MISMATCH", "option", option_id=oid)
            if option.payment_method != plan.method:
                reject("OPTION_METHOD_MISMATCH", "option", option_id=oid)
            option_issues = supplied_option_rejections(option, envelope, core)
            issues.extend(option_issues)
            if not any(i["code"] == "OPTION_METADATA_INVALID" for i in option_issues):
                mismatch = len(payments) != option.number_of_payments or not valid_payments
                if not mismatch:
                    interval = option.payment_frequency_days or 0
                    mismatch = any(
                        p.date != option.first_payment_date + timedelta(days=i * interval)
                        or p.amount != option.payment_amount for i, p in enumerate(payments)
                    )
                if mismatch:
                    reject("OPTION_SCHEDULE_MISMATCH", "option", option_id=oid)

    if len(plan.changes) > 3:
        reject("ACTION_LIMIT_EXCEEDED", "action", actual_count=len(plan.changes))
    targets = {}
    for target in core.change_targets:
        if target.anchor_event_id in targets:
            reject("CHANGE_TARGET_MAP_AMBIGUOUS", "identity", event_id=target.anchor_event_id)
        targets[target.anchor_event_id] = target
    seen_events, seen_series, seen_occurrences = set(), set(), set()
    for change in plan.changes:
        if isinstance(change, Stop):
            kind = "stop"
        elif isinstance(change, ReduceTo):
            kind = "reduce_to"
        else:
            reject("UNKNOWN_ACTION_TYPE", "action")
            continue
        event_id = change.anchor_event_id
        if event_id in seen_events:
            reject("ACTION_CONFLICT", "action", event_id=event_id)
        seen_events.add(event_id)
        target = targets.get(event_id)
        if target is None:
            reject("ACTION_TARGET_UNRESOLVED", "action", event_id=event_id)
            continue
        if target.series_id in seen_series:
            reject("ACTION_SERIES_CONFLICT", "action", event_id=event_id, series_id=target.series_id)
        seen_series.add(target.series_id)
        if seen_occurrences.intersection(target.eligible_occurrence_ids):
            reject("ACTION_OVERLAPPING_OCCURRENCES", "action", event_id=event_id)
        seen_occurrences.update(target.eligible_occurrence_ids)
        if not target.eligible_occurrence_ids:
            reject("ACTION_NO_FUTURE_EFFECT", "action", event_id=event_id)
        if target.category in preferences.protected_categories:
            reject("ACTION_PROTECTED", "action", event_id=event_id)
        categories = preferences.stoppable_categories if kind == "stop" else preferences.reducible_categories
        if target.category not in categories or kind not in target.allowed_actions:
            reject("ACTION_NOT_ALLOWED", "action", event_id=event_id, action=kind)
        subtypes = ({"stoppable", "reducible_or_stoppable"} if kind == "stop"
                    else {"reducible", "reducible_or_stoppable"})
        if target.flexibility not in subtypes:
            reject("ACTION_SUBTYPE_FORBIDDEN", "action", event_id=event_id, action=kind)
        if kind == "reduce_to":
            floor = target.floor
            if floor is None:
                reject("ACTION_FLOOR_UNKNOWN", "action", event_id=event_id)
                continue
            try:
                _money(floor, "floor")
                _money(change.new_amount, "new_amount")
                if floor.currency != currency or change.new_amount.currency != currency:
                    raise ValueError("action currency differs from request currency")
            except ValueError as exc:
                reject("ACTION_AMOUNT_INVALID", "action", event_id=event_id, detail=str(exc))
                continue
            if change.new_amount.minor < floor.minor:
                reject("ACTION_BELOW_FLOOR", "action", event_id=event_id,
                       actual_minor=change.new_amount.minor, expected_minor=floor.minor)
    return tuple(issues)


def evaluate_candidate(plan: Plan, envelope, core) -> dict:
    """Evaluate one canonical Plan with exactly one authoritative numerical replay.

    Definitive static failures are not replayed. A material unresolved baseline
    remains unresolved even if its displayed explicit-only path looks healthy.
    The original capacity object is returned by reference, never recomputed.
    """
    issues = list(static_rejections(plan, envelope, core))
    eligible = not any(i["stage"] != "capacity" for i in issues)
    safety = None
    if not issues or all(i["code"] == "CAPACITY_UNRESOLVED" for i in issues):
        safety = replay_financial_plan(core, plan.payments, changes=plan.changes)
    dates_valid = bool(plan.payments) and all(type(p.date) is date for p in plan.payments)
    amounts_valid = all(isinstance(p.amount, Money) and p.amount.currency == core.requested.currency
                        for p in plan.payments)
    total = Money(core.requested.currency, sum(p.amount.minor for p in plan.payments)) if amounts_valid else None
    completion = max(p.date for p in plan.payments) if dates_valid else None
    first_breach = None
    minimum_minor = None
    if safety is not None:
        if safety.reason_codes[:1] == ("INVALID_FINANCIAL_PLAN",):
            issues.append(_issue("INVALID_FINANCIAL_PLAN", "replay", severity="unverifiable",
                                 detail="; ".join(safety.reason_codes[1:])))
        else:
            for code in safety.reason_codes:
                issues.append(_issue(code, "replay", severity="unverifiable"
                                     if safety.proof_status == "unresolved" else "reject"))
        if (plan.method == "partial_payment" and not plan.changes and not safety.safe
                and safety.proof_status in _PROVED):
            issues.append(_issue("PARTIAL_CAPACITY_INCOHERENCE", "capacity", severity="unverifiable",
                                 safe_today=core.capacity.amount_safe_to_pay,
                                 full_date=core.capacity.earliest_date_for_full_payment))
        for issue in core.issues:
            issues.append(_issue(issue.code, "evidence", detail=issue.detail,
                                 source_ids=issue.source_ids, target_ids=issue.target_ids,
                                 severity="unverifiable" if issue.severity == "blocking" else "warning"))
        if safety.forecast is not None:
            minimum_minor = safety.forecast.minimum_available_minor
            for point in safety.forecast.checkpoints:
                if point.checkpoint_id == safety.first_violation_checkpoint_id:
                    first_breach = _issue(
                        "MINIMUM_BALANCE_BREACH", "replay", checkpoint_id=point.checkpoint_id,
                        date=point.date, phase=point.phase, operation_id=point.operation_id,
                        balance_minor=point.available_minor, minimum_minor=core.anchor.minimum_minor,
                        shortfall_minor=core.anchor.minimum_minor - point.available_minor,
                        verified=safety.proof_status in _PROVED,
                    )
                    issues.append(first_breach)
                    break
        if plan.changes and safety.safe and not any(delta < 0 for _, delta in safety.occurrence_deltas):
            issues.append(_issue("ACTION_NO_EFFECT", "action"))
    blockers = [i for i in issues if i["severity"] != "warning"]
    if any(i["severity"] == "unverifiable" for i in blockers):
        outcome = "unverifiable"
    elif blockers:
        outcome = "invalid"
    elif safety is not None and safety.safe and safety.forecast is not None and safety.proof_status in _PROVED:
        outcome = "valid"
    else:
        outcome = "unverifiable"
    return {
        "plan": plan, "request_id": core.request_id, "outcome": outcome,
        "eligible": eligible, "replay_performed": safety is not None,
        "financial_safe": safety.safe if safety is not None else None,
        "financial_proof_status": safety.proof_status if safety is not None else "not_replayed",
        "actual_total_paid": total, "first_payment_date": plan.payments[0].date if dates_valid else None,
        "completion_date": completion, "payment_count": len(plan.payments),
        "has_spending_changes": bool(plan.changes),
        "completes_by_deadline": (completion is not None
                                  and type(envelope.request.desired_completion_date) is date
                                  and completion <= envelope.request.desired_completion_date),
        "minimum_available": safety.minimum_available if safety is not None else None,
        "minimum_available_minor": minimum_minor, "first_breach": first_breach,
        "issues": tuple(issues), "safety": safety, "capacity": core.capacity,
        "core_hash": core.context_hash, "envelope_hash": envelope.context_hash,
    }
