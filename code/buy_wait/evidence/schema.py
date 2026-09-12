"""Private extraction schema. Financial acceptance belongs to core/contracts.py.

All values are observations, even after validation. No profile/plan/balance patch
can be represented. Wire decimals remain strings and missing amounts remain None.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

VERSION = "bw.extraction/1"
Currency = Literal["INR", "ZAR", "IDR", "USD", "EUR"]
Dec = Annotated[str, StringConstraints(pattern=r"^(0|[1-9][0-9]*)(\.[0-9]+)?$", max_length=25)]
Short = Annotated[str, StringConstraints(min_length=1, max_length=240)]
Day = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")]
MoneyRole = Literal[
    "net_received", "net_payable", "gross", "subtotal", "amount_paid",
    "balance_due", "refund_amount", "regular_salary", "one_off_arrears",
    "bonus", "commission", "deduction", "tax", "fee", "market_value",
    "other", "unknown",
]
Action = Literal["observe", "confirm", "amend", "cancel", "settle", "delay", "start", "end"]


class EvidenceError(ValueError):
    """Sanitized error: never contains input text, model bodies or credentials."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def strict_json(data: str | bytes, *, max_bytes: int = 262144) -> object:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise EvidenceError("duplicate_json_key")
            result[key] = value
        return result

    def nonfinite(_):
        raise EvidenceError("nonfinite_json")

    try:
        if len(data.encode() if isinstance(data, str) else data) > max_bytes:
            raise EvidenceError("oversize_response")
        return json.loads(data, object_pairs_hook=pairs, parse_constant=nonfinite)
    except (ValueError, UnicodeError, TypeError, RecursionError) as exc:
        if isinstance(exc, EvidenceError):
            raise
        raise EvidenceError("invalid_json") from None


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    @model_validator(mode="after")
    def calendar_fields(self):
        # Calendar validation is local, not delegated to a provider's schema hint.
        for name in ("start_date", "end_date", "lower", "upper"):
            value = getattr(self, name, None)
            if value is not None:
                date.fromisoformat(value)
        return self


class Span(Closed):
    quote: Annotated[str, StringConstraints(min_length=1, max_length=600)]
    char_start: Annotated[int, Field(ge=0)] | None
    char_end: Annotated[int, Field(ge=0)] | None
    box: Annotated[list[float], Field(min_length=4, max_length=4)] | None

    @model_validator(mode="after")
    def bounds(self):
        if (self.char_start is None) != (self.char_end is None):
            raise ValueError("paired_offsets_required")
        if self.char_start is not None and self.char_end <= self.char_start:
            raise ValueError("invalid_span")
        if self.box is not None:
            x0, y0, x1, y1 = self.box
            if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                raise ValueError("invalid_box")
        return self


class Subject(Closed):
    scope: Literal["event", "series", "unlinked"]
    event_id: str | None
    topic: Literal[
        "salary", "bonus", "commission", "invoice_income", "gig_income",
        "refund", "prize", "investment", "reimbursement", "transfer", "rent",
        "childcare", "card_minimum", "bill", "purchase", "other",
    ]
    label: Annotated[str, StringConstraints(min_length=1, max_length=160)]

    @model_validator(mode="after")
    def scope_id(self):
        if (self.scope == "event") != (self.event_id is not None):
            raise ValueError("invalid_target_scope")
        return self


class Window(Closed):
    scope: Literal["unspecified", "once", "next_occurrence", "from_date", "date_range", "until_further_notice"]
    start_date: Day | None
    end_date: Day | None
    anchor_quote: Short | None

    @model_validator(mode="after")
    def interval(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("reversed_window")
        if self.scope == "from_date" and self.start_date is None:
            raise ValueError("missing_effective_date")
        if self.scope == "date_range" and (self.start_date is None or self.end_date is None):
            raise ValueError("missing_window_bound")
        return self


class Condition(Closed):
    basis: Literal["payment_date", "settlement_date", "event_date", "unspecified"]
    lower: Day | None
    lower_inclusive: bool
    upper: Day | None
    upper_inclusive: bool

    @model_validator(mode="after")
    def interval(self):
        if self.lower and self.upper:
            if self.lower > self.upper or (self.lower == self.upper and not (self.lower_inclusive and self.upper_inclusive)):
                raise ValueError("empty_condition")
        return self


class Amount(Closed):
    kind: Literal["amount"]
    value: Dec | None
    raw: Annotated[str, StringConstraints(min_length=1, max_length=120)] | None
    currency: Currency | None
    currency_basis: Literal["document", "linked_event", "unknown"]
    role: MoneyRole
    direction: Literal["credit", "debit", "non_cash", "unknown"]
    condition: Condition | None

    @model_validator(mode="after")
    def known_value(self):
        if self.value is not None and (self.raw is None or len(self.value.replace(".", "")) > 24):
            raise ValueError("unsupported_amount")
        if (self.currency is None) != (self.currency_basis == "unknown"):
            raise ValueError("currency_basis_mismatch")
        return self


class RelativeChange(Closed):
    kind: Literal["relative_change"]
    measure: Literal["percent", "absolute_delta"]
    value: Dec
    currency: Currency | None
    change: Literal["increase", "decrease"]
    base_role: MoneyRole

    @model_validator(mode="after")
    def unit(self):
        if (self.measure == "percent") != (self.currency is None):
            raise ValueError("invalid_change_unit")
        return self


class DateClaim(Closed):
    kind: Literal["date"]
    role: Literal["event", "settlement", "payroll_release", "due", "effective", "document", "printed", "period_start", "period_end"]
    value: Day | None
    raw: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    interpretation: Literal["explicit_iso", "unambiguous_local", "relative", "ambiguous"]
    alternatives: Annotated[list[Day], Field(max_length=3)]

    @model_validator(mode="after")
    def dates(self):
        for value in self.alternatives + ([self.value] if self.value else []):
            date.fromisoformat(value)
        if self.interpretation == "ambiguous" and self.value is not None:
            raise ValueError("ambiguous_date_has_value")
        return self


class State(Closed):
    kind: Literal["state"]
    axis: Literal["cash", "approval", "obligation", "fulfillment"]
    value: Literal[
        "settled", "pending", "scheduled", "failed", "cancelled", "unrealized",
        "confirmed", "unconfirmed", "conditional", "outstanding", "closed",
        "disputed", "delivered", "not_delivered", "unknown",
    ]

    @model_validator(mode="after")
    def axis_value(self):
        valid = {
            "cash": {"settled", "pending", "scheduled", "failed", "cancelled", "unrealized", "unknown"},
            "approval": {"confirmed", "unconfirmed", "conditional", "unknown"},
            "obligation": {"outstanding", "closed", "disputed", "unknown"},
            "fulfillment": {"delivered", "not_delivered", "unknown"},
        }
        if self.value not in valid[self.axis]:
            raise ValueError("state_axis_mismatch")
        return self


class Pattern(Closed):
    kind: Literal["pattern"]
    recurrence: Literal["recurring", "one_off", "ended", "resumed", "unknown"]
    cadence: Literal["weekly", "monthly", "quarterly", "yearly", "unknown"]


class Relation(Closed):
    kind: Literal["relation"]
    relation: Literal["internal_transfer", "refund_of", "reimbursement_of", "reversal_of", "duplicate_of", "retry_of", "sale_proceeds_of", "separate_obligations"]
    other_event_ids: Annotated[list[str], Field(max_length=8)]
    other_subject_label: Annotated[str, StringConstraints(max_length=160)] | None


class Fact(Closed):
    local_id: Annotated[str, StringConstraints(pattern=r"^f[1-9][0-9]*$")]
    subject: Subject
    operation: Action
    certainty: Literal["explicit", "ambiguous"]
    effect_window: Window
    evidence: Annotated[list[Span], Field(min_length=1, max_length=4)]
    payload: Amount | RelativeChange | DateClaim | State | Pattern | Relation


class Issue(Closed):
    code: Literal[
        "ambiguous_amount", "ambiguous_currency", "ambiguous_date", "ambiguous_target",
        "missing_amount", "cropped_document", "unreadable_region", "conflicting_values",
        "instruction_attempt", "unsupported_content",
    ]
    fact_local_ids: Annotated[list[str], Field(max_length=32)]
    detail: Short


class ModelExtraction(Closed):
    schema_version: Literal["bw.extraction/1"]
    source_id: str
    language: Literal["en", "id", "other", "mixed", "unknown"]
    disposition: Literal["facts", "no_financial_fact", "unreadable", "ambiguous"]
    issuer_name: Annotated[str, StringConstraints(min_length=1, max_length=120)] | None
    issuer_evidence: Annotated[list[Span], Field(max_length=2)]
    facts: Annotated[list[Fact], Field(max_length=32)]
    issues: Annotated[list[Issue], Field(max_length=16)]

    @model_validator(mode="after")
    def coherence(self):
        ids = [f.local_id for f in self.facts]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate_fact_id")
        if any(not set(i.fact_local_ids).issubset(ids) for i in self.issues):
            raise ValueError("unknown_issue_fact")
        if self.disposition == "facts" and not self.facts:
            raise ValueError("empty_facts")
        if self.disposition in {"no_financial_fact", "unreadable"} and self.facts:
            raise ValueError("disposition_mismatch")
        if self.disposition in {"ambiguous", "unreadable"} and not self.issues:
            raise ValueError("missing_issue")
        if self.issuer_name and not any(self.issuer_name in s.quote for s in self.issuer_evidence):
            raise ValueError("unsupported_issuer")
        for fact in self.facts:
            attached = {issue.code for issue in self.issues if not issue.fact_local_ids or fact.local_id in issue.fact_local_ids}
            if isinstance(fact.payload, Amount):
                if fact.payload.value is None and not attached.intersection({"missing_amount", "ambiguous_amount", "unreadable_region"}):
                    raise ValueError("unknown_amount_requires_issue")
                if fact.payload.currency is None and "ambiguous_currency" not in attached:
                    raise ValueError("unknown_currency_requires_issue")
            if isinstance(fact.payload, DateClaim) and fact.payload.value is None and "ambiguous_date" not in attached:
                raise ValueError("unknown_date_requires_issue")
            if fact.certainty == "ambiguous" and not attached:
                raise ValueError("ambiguous_fact_requires_issue")
        return self


def parse_extraction(raw: str | bytes) -> ModelExtraction:
    try:
        return ModelExtraction.model_validate(strict_json(raw))
    except EvidenceError:
        raise
    except (ValueError, TypeError):
        # Pydantic's default exception contains source values: never propagate it.
        raise EvidenceError("schema_invalid") from None


def _readable_numbers(raw: str) -> set[Decimal]:
    """Conservative candidates for explicit plain/western/Indian/decimal-comma text.

    Ambiguous grouping returns all readings; validation only accepts a unique
    interpretation. Currency symbols are kept out of numeric raw fields.
    """
    if not re.fullmatch(r"[0-9][0-9.,]*", raw):
        return set()
    results = set()
    if re.fullmatch(r"\d+(?:\.\d+)?", raw):
        results.add(Decimal(raw))
    if re.fullmatch(r"(?:\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})*,\d{3})(?:\.\d+)?", raw):
        results.add(Decimal(raw.replace(",", "")))
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", raw):
        results.add(Decimal(raw.replace(".", "").replace(",", ".")))
    if re.fullmatch(r"\d+,\d{1,2}", raw):
        results.add(Decimal(raw.replace(",", ".")))
    return results


def _date_readings(raw: str) -> set[str]:
    """Bounded document-date parsing; ambiguous month/day stays a set."""
    readings = set()
    formats = ("%Y-%m-%d", "%d-%b-%Y", "%d %b %Y", "%d %B %Y", "%d/%m/%Y", "%m/%d/%Y")
    for fmt in formats:
        try:
            readings.add(datetime.strptime(raw, fmt).date().isoformat())
        except ValueError:
            pass
    return readings


def _quoted_dates(spans: list[Span]) -> set[str]:
    # Separate scans prevent a fragment such as "05 through 2026" from
    # consuming the year before the ISO date in "704.05 through 2026-02-06".
    patterns = (r"\b\d{4}-\d{2}-\d{2}\b", r"\b\d{1,2}[- ][A-Za-z]+[- ]\d{4}\b", r"\b\d{1,2}/\d{1,2}/\d{4}\b")
    dates = set()
    for span in spans:
        for pattern in patterns:
            for raw in re.findall(pattern, span.quote):
                readings = _date_readings(raw)
                if len(readings) == 1:
                    dates.update(readings)
    return dates


def validate_support(extraction: ModelExtraction, *, source_id: str,
                     text: str | None, candidate_events: dict[str, dict]) -> None:
    """Check host identity, numeric evidence, IDs and exact message quotations.

    Image transcription remains an untrusted vision assertion, not independently
    verified OCR. caller supplies only already same-user validated descriptors.
    """
    from decimal import Decimal
    if extraction.source_id != source_id:
        raise EvidenceError("source_id_mismatch")
    for span in extraction.issuer_evidence + [s for f in extraction.facts for s in f.evidence]:
        if text is not None:
            if span.box is not None or span.char_start is None or text[span.char_start:span.char_end] != span.quote:
                raise EvidenceError("unsupported_quote")
        elif span.char_start is not None:
            raise EvidenceError("image_text_offset")
    for fact in extraction.facts:
        target = fact.subject.event_id
        if target is not None and target not in candidate_events:
            raise EvidenceError("invalid_target")
        p = fact.payload
        if isinstance(p, Relation) and not set(p.other_event_ids).issubset(candidate_events):
            raise EvidenceError("invalid_target")
        quoted_dates = _quoted_dates(fact.evidence)
        for bound in (fact.effect_window.start_date, fact.effect_window.end_date):
            if bound is not None and bound not in quoted_dates:
                raise EvidenceError("unsupported_effective_date")
        if isinstance(p, Amount):
            if p.condition is not None:
                if p.condition.basis == "unspecified" or not (p.condition.lower or p.condition.upper):
                    raise EvidenceError("ambiguous_date_condition")
                if any(bound is not None and bound not in quoted_dates for bound in (p.condition.lower, p.condition.upper)):
                    raise EvidenceError("unsupported_date_condition")
            if p.currency_basis == "linked_event" and (target is None or candidate_events[target].get("currency") != p.currency):
                raise EvidenceError("currency_mismatch")
            labels = " ".join(span.quote for span in fact.evidence).casefold()
            if p.role in {"net_received", "net_payable", "balance_due", "refund_amount", "regular_salary", "bonus", "commission", "one_off_arrears"}:
                components = r"\b(item bill|subtotal|gross|total earnings)\b"
                cash = r"\b(net pay|net amount|net received|balance due|amount due|cash paid|refund)\b"
                component_label, cash_label = re.search(components, labels), re.search(cash, labels)
                raw_pattern = r"[^0-9]{0,50}" + re.escape(p.raw or "")
                component_value = p.raw and re.search(components + raw_pattern, labels)
                cash_value = p.raw and re.search(cash + raw_pattern, labels)
                if (component_label and not cash_label) or (component_value and not cash_value):
                    raise EvidenceError("component_as_cash")
            if p.value is not None:
                numbers = _readable_numbers(p.raw)
                token = r"(?<![0-9+-])(?<![0-9][.,])" + re.escape(p.raw) + r"(?![0-9]|[.,][0-9])"
                if numbers != {Decimal(p.value)} or not any(re.search(token, s.quote) for s in fact.evidence):
                    raise EvidenceError("unsupported_numeric_value")
        if isinstance(p, DateClaim):
            if not any(p.raw in s.quote for s in fact.evidence):
                raise EvidenceError("unsupported_date")
            if p.value is not None and _date_readings(p.raw) != {p.value}:
                raise EvidenceError("ambiguous_or_unsupported_date")
        if isinstance(p, RelativeChange):
            tokens = [t for s in fact.evidence for t in re.findall(r"(?<![\w.,])[0-9]+(?:\.[0-9]+)?(?![\w.,])", s.quote)]
            if not any(Decimal(t) == Decimal(p.value) for t in tokens):
                raise EvidenceError("unsupported_change")
            if p.measure == "percent":
                rates = [t for s in fact.evidence for t in re.findall(r"(?<![\w.,])([0-9]+(?:\.[0-9]+)?)\s*(?:%|percent\b|per cent\b|persen\b)", s.quote, re.IGNORECASE)]
                if not any(Decimal(t) == Decimal(p.value) for t in rates):
                    raise EvidenceError("unsupported_percent_unit")


def response_format() -> dict:
    return {"type": "json_schema", "json_schema": {
        "name": "bw_extraction_v1", "strict": True, "schema": ModelExtraction.model_json_schema(),
    }}
