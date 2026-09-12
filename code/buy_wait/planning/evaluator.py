"""Static eligibility/contract checks, deliberately separate from cash replay.

``static_rejections`` returns options-owned diagnostic payloads, NOT financial
approval or a second public PlanValidation dataclass. The committed core handoff
will supply the sole replay/record adapter; an empty tuple here proves only the
static contract. No balance, capacity or occurrence is mutated by these checks.
"""
from __future__ import annotations

from datetime import date, timedelta

from buy_wait.planning.generator import _money, option_completion_date


def static_rejections(plan: object, envelope: object, core: object) -> tuple[dict, ...]:
    """Return all independently checkable static reasons with relevant values.

    Inputs follow the canonical envelope/core/plan field contract. Malformed
    money/date/count values produce reasons rather than repaired schedules.
    Missing required record fields remain a shared-schema error at ingestion.
    """
    from buy_wait.contracts import ReduceTo, Stop

    issues = []

    def reject(code, stage, **details):
        issues.append({"code": code, "stage": stage, **details})

    request, preferences = envelope.request, envelope.preferences
    if (plan.request_id != request.request_id or request.request_id != core.request_id
            or request.user_id != core.user_id):
        reject("REQUEST_IDENTITY_MISMATCH", "identity")
    if plan.core_hash != core.context_hash or plan.envelope_hash != envelope.context_hash:
        reject("CONTEXT_HASH_MISMATCH", "identity")
    if (core.capacity.proof_status not in {"resolved_under_policy", "conservative_bound"}
            or core.capacity.amount_safe_to_pay is None):
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
    accepted_method = "full_payment" if plan.method == "wait" else plan.method
    if plan.method not in {"full_payment", "partial_payment", "installments", "wait"}:
        reject("UNKNOWN_PAYMENT_METHOD", "eligibility", method=plan.method)
    elif accepted_method not in methods:
        reject("METHOD_NOT_ACCEPTED", "eligibility", method=accepted_method)

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
            reject("PRINCIPAL_SUM_MISMATCH", "schedule", actual_minor=paid,
                   expected_minor=principal)
        if plan.method == "full_payment":
            if len(payments) != 1 or payments[0].date != request.request_date:
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
            safe = core.capacity.amount_safe_to_pay
            earliest = core.capacity.earliest_date_for_full_payment
            safe_valid = False
            if safe is not None:
                try:
                    _money(safe, "safe_today", allow_zero=True)
                    safe_valid = safe.currency == currency and 0 < safe.minor < principal
                except ValueError:
                    pass
            if not safe_valid:
                reject("PARTIAL_CAPACITY_OUT_OF_RANGE", "capacity")
            if (type(earliest) is not date or earliest <= request.request_date
                    or earliest > min(request.desired_completion_date, core.horizon_end)):
                reject("PARTIAL_DATE_UNAVAILABLE", "capacity", expected_date=earliest)
            if safe_valid and type(earliest) is date:
                expected = ((request.request_date, safe.minor),
                            (earliest, principal - safe.minor))
                actual = tuple((payment.date, payment.amount.minor) for payment in payments)
                if actual != expected:
                    reject("PARTIAL_GRAMMAR_MISMATCH", "schedule", expected=expected, actual=actual)

    supplied_id = plan.supplied_option_id
    if plan.method == "installments" and not supplied_id:
        reject("SUPPLIED_OPTION_REQUIRED", "option")
    if plan.method in {"partial_payment", "wait"} and supplied_id is not None:
        reject("RULE_PLAN_BORROWED_OPTION_ID", "option", option_id=supplied_id)
    if supplied_id is not None:
        option = envelope.options_by_id.get(supplied_id)
        if option is None:
            reject("UNKNOWN_OPTION", "option", option_id=supplied_id)
        else:
            if option.payment_option_id != supplied_id or option.request_id != request.request_id:
                reject("OPTION_WRONG_REQUEST", "option", option_id=supplied_id)
            if option.payment_method != plan.method:
                reject("OPTION_METHOD_MISMATCH", "option", option_id=supplied_id)
            option_valid = True
            try:
                last = option_completion_date(option)
                _money(option.payment_amount, "payment_amount")
                _money(option.financing_fee, "financing_fee", allow_zero=True)
                _money(option.total_payable_amount, "total_payable_amount")
                if any(amount.currency != currency for amount in (
                    option.payment_amount, option.financing_fee, option.total_payable_amount
                )):
                    raise ValueError("option currency differs from request currency")
            except ValueError as exc:
                reject("OPTION_METADATA_INVALID", "option", option_id=supplied_id, detail=str(exc))
                option_valid = False
            if option_valid:
                if option.first_payment_date < request.request_date or last > core.horizon_end:
                    reject("OPTION_OUTSIDE_FORECAST", "option", option_id=supplied_id,
                           first_date=option.first_payment_date, last_date=last)
                if last > request.desired_completion_date:
                    reject("OPTION_MISSES_DEADLINE", "option", option_id=supplied_id,
                           completion_date=last, deadline=request.desired_completion_date)
                cash = option.payment_amount.minor * option.number_of_payments
                declared = option.total_payable_amount.minor
                if cash != declared:
                    reject("OPTION_TOTAL_MISMATCH", "option", option_id=supplied_id,
                           actual_minor=cash, expected_minor=declared, delta_minor=cash - declared)
                expected_total = request.requested_amount.minor + option.financing_fee.minor
                if declared != expected_total:
                    reject("OPTION_FEE_MISMATCH", "option", option_id=supplied_id,
                           actual_minor=declared, expected_minor=expected_total,
                           delta_minor=declared - expected_total)
                # Compare lazily: a forged huge count must not allocate an enormous schedule.
                mismatch = len(payments) != option.number_of_payments or not valid_payments
                if not mismatch:
                    interval = option.payment_frequency_days or 0
                    mismatch = any(
                        payment.date != option.first_payment_date + timedelta(days=index * interval)
                        or payment.amount != option.payment_amount
                        for index, payment in enumerate(payments)
                    )
                if mismatch:
                    reject("OPTION_SCHEDULE_MISMATCH", "option", option_id=supplied_id)
                if plan.method == "installments":
                    cap = preferences.max_installment_months
                    if type(cap) is not int or cap <= 0:
                        reject("INSTALLMENT_CAP_UNRESOLVED", "eligibility", cap=cap)
                    elif option.number_of_payments > cap:
                        reject("INSTALLMENT_CAP_EXCEEDED", "eligibility", cap=cap,
                               payment_count=option.number_of_payments)

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
        permitted_categories = (preferences.stoppable_categories if kind == "stop"
                                else preferences.reducible_categories)
        if target.category not in permitted_categories or kind not in target.allowed_actions:
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
                _money(floor, "floor", allow_zero=True)
                _money(change.new_amount, "new_amount", allow_zero=True)
                if floor.currency != currency or change.new_amount.currency != currency:
                    raise ValueError("action currency differs from request currency")
            except ValueError as exc:
                reject("ACTION_AMOUNT_INVALID", "action", event_id=event_id, detail=str(exc))
                continue
            if change.new_amount.minor < floor.minor:
                reject("ACTION_BELOW_FLOOR", "action", event_id=event_id,
                       actual_minor=change.new_amount.minor, expected_minor=floor.minor)
    return tuple(issues)
