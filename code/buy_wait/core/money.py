"""Exact input/output money and directed settlement-date conversion."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import re
from typing import Iterable, Optional, Tuple

from buy_wait.contracts import FxRate, Money


class MoneyError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def parse_money(value: str, currency: str) -> Money:
    """Parse a nonnegative, cent-exact decimal string; blank is NOT zero."""
    if not isinstance(value, str) or not value.strip():
        raise MoneyError("MISSING_AMOUNT", "cash amount must be a nonblank decimal string")
    value = value.strip()
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value):
        raise MoneyError("INVALID_AMOUNT", "cash amount must be a nonnegative decimal magnitude")
    try:
        numerator, denominator = Decimal(value).as_integer_ratio()
    except (InvalidOperation, ValueError) as error:
        raise MoneyError("INVALID_AMOUNT", "invalid decimal amount") from error
    scaled, remainder = divmod(numerator * 100, denominator)
    if remainder:
        raise MoneyError("FRACTIONAL_CENT", "supplied cash is not cent-exact")
    return Money(currency, scaled)


def parse_optional_money(value: Optional[str], currency: str) -> Optional[Money]:
    return None if value is None or value.strip() == "" else parse_money(value, currency)


def format_money(value: Money) -> str:
    return f"{value.minor // 100}.{value.minor % 100:02d}"


def parse_day(value: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("date must be YYYY-MM-DD")
    return date.fromisoformat(value)


def scale_money(value: Money, multiplier: Decimal, *, direction: str) -> Money:
    if not isinstance(multiplier, Decimal) or not multiplier.is_finite() or multiplier <= 0:
        raise MoneyError("INVALID_RATE", "multiplier must be a positive finite Decimal")
    if direction not in ("debit", "credit"):
        raise MoneyError("INVALID_DIRECTION", "only cash debits and credits may be scaled")
    numerator, denominator = multiplier.as_integer_ratio()
    product = value.minor * numerator
    minor = ((product + denominator - 1) // denominator if direction == "debit"
             else product // denominator)
    return Money(value.currency, minor)


class FxTable:
    """Read-only exact-key rate index; no nearest, inverse or live fallback."""
    def __init__(self, rates: Iterable[FxRate] = ()):
        table = {}
        for row in rates:
            key = (row.rate_date, row.from_currency, row.to_currency)
            if key in table:
                raise MoneyError("DUPLICATE_FX", "duplicate dated directed FX key")
            table[key] = row
        self._rates = table

    def convert(self, value: Money, home_currency: str, settlement_date: date,
                *, direction: str) -> Tuple[Money, Optional[str]]:
        if type(settlement_date) is not date:
            raise MoneyError("MISSING_SETTLEMENT_DATE", "FX requires an exact settlement date")
        if direction not in ("debit", "credit"):
            raise MoneyError("INVALID_DIRECTION", "noncash records cannot be converted into cash")
        if value.currency == home_currency:
            return value, None
        key = (settlement_date, value.currency, home_currency)
        row = self._rates.get(key)
        if row is None:
            raise MoneyError("MISSING_FX", "no exact settlement-date directed rate")
        scaled = scale_money(value, row.rate, direction=direction)
        return Money(home_currency, scaled.minor), row.source.source_id
