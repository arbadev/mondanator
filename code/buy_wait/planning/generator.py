"""Pure schedule constructors for the canonical financial contracts.

This first planning slice does not construct a second ledger or public domain
record. Money and Payment are supplied by the core-owned contracts module.
Candidate/search-result assembly awaits that module's committed handoff.
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
