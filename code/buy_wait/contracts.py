"""Shared immutable contracts (canonical execution-plan v1.1 / I011).

No CSV, model SDK, network, or payment-preference decisions live here. Money is
nonnegative dataset hundredths, including IDR; signed ledger deltas are integers.
SourceRef.row_number counts data records, excluding the header.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
from pathlib import PurePosixPath
from typing import Literal, Optional, Tuple, Union

Day = date
Currency = Literal["INR", "ZAR", "IDR", "USD", "EUR"]
Direction = Literal["debit", "credit", "non_cash"]
CashState = Literal["settled", "pending", "scheduled", "failed", "cancelled", "unrealized"]
Flexibility = Literal["fixed", "reducible", "stoppable", "reducible_or_stoppable"]
ProofStatus = Literal["resolved_under_policy", "conservative_bound", "unresolved"]
Action = Literal["observe", "confirm", "amend", "cancel", "settle", "delay", "start", "end"]
IncomeRole = Literal["regular_salary", "one_off", "prorated", "unknown", "not_applicable"]
CURRENCIES = frozenset(("INR", "ZAR", "IDR", "USD", "EUR"))
CASH_STATES = frozenset(("settled", "pending", "scheduled", "failed", "cancelled", "unrealized"))
FLEXIBILITIES = frozenset(("fixed", "reducible", "stoppable", "reducible_or_stoppable"))


def _integer(value: int, name: str, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def _choice(value: str, choices, name: str) -> None:
    if value not in choices:
        raise ValueError(f"invalid {name}: {value!r}")


def _day(value: date, name: str) -> None:
    if type(value) is not date:
        raise ValueError(f"{name} must be a date, not datetime or text")


@dataclass(frozen=True)
class Money:
    currency: Currency
    minor: int

    def __post_init__(self):
        _choice(self.currency, CURRENCIES, "currency")
        _integer(self.minor, "minor")


@dataclass(frozen=True)
class SourceRef:
    kind: Literal["csv", "message", "image"]
    source_id: str
    relative_path: str = ""
    row_number: Optional[int] = None
    source_type: Optional[str] = None
    actor_key: Optional[str] = None
    known_at: Optional[datetime] = None
    content_sha256: str = ""
    locator: Optional[str] = None
    physical_line: Optional[int] = None

    def __post_init__(self):
        _choice(self.kind, ("csv", "message", "image"), "source kind")
        if not self.source_id:
            raise ValueError("source_id is required")
        path = PurePosixPath(self.relative_path)
        if path.is_absolute() or ".." in path.parts or "\\" in self.relative_path:
            raise ValueError("source path must be a relative dataset path")
        for value in (self.row_number, self.physical_line):
            if value is not None:
                _integer(value, "source row", 1)
        if self.known_at is not None and (not isinstance(self.known_at, datetime) or self.known_at.utcoffset() is None):
            raise ValueError("known_at must be timezone-aware")
        if self.actor_key == "":
            object.__setattr__(self, "actor_key", None)


@dataclass(frozen=True)
class EvidenceRef:
    source_id: str
    locator: str = ""
    supporting_text: Optional[str] = None


@dataclass(frozen=True)
class AmountCandidate:
    """Unaccepted extraction diagnostic, e.g. a cropped subtotal; NOT Money."""
    label: str
    value: str
    currency: Optional[str] = None


@dataclass(frozen=True)
class EvidenceIssue:
    code: str
    severity: Literal["info", "warning", "blocking"]
    detail: str
    source_ids: Tuple[str, ...] = ()
    target_ids: Tuple[str, ...] = ()
    impact: str = "none"
    candidates: Tuple[AmountCandidate, ...] = ()

    def __post_init__(self):
        _choice(self.severity, ("info", "warning", "blocking"), "severity")
        for name in ("source_ids", "target_ids", "candidates"):
            object.__setattr__(self, name, tuple(getattr(self, name)))


@dataclass(frozen=True)
class CalendarMonth:
    day: int
    end_of_month: bool = False

    def __post_init__(self):
        _integer(self.day, "monthly day", 1)
        if self.day > 31:
            raise ValueError("monthly day must be <=31")


@dataclass(frozen=True)
class FixedDays:
    days: int
    anchor_date: Day

    def __post_init__(self):
        _integer(self.days, "cadence", 1)
        _day(self.anchor_date, "anchor_date")


Cadence = Union[CalendarMonth, FixedDays]


@dataclass(frozen=True)
class EventTarget:
    event_id: str


@dataclass(frozen=True)
class SeriesTarget:
    user_id: str
    category: str
    direction: Direction
    currency: Currency
    description_key: Optional[str] = None
    actor_key: Optional[str] = None

    def __post_init__(self):
        if not self.user_id or not self.category:
            raise ValueError("series target needs evidenced user and category")
        _choice(self.currency, CURRENCIES, "series target currency")
        _choice(self.direction, ("debit", "credit", "non_cash"), "series target direction")


@dataclass(frozen=True)
class NewOccurrenceTarget:
    local_id: str
    user_id: str


FactTarget = Union[EventTarget, SeriesTarget, NewOccurrenceTarget]


@dataclass(frozen=True)
class AmountClaim:
    value: Money
    role: Literal["net_cash", "payable", "balance_only"]
    kind: str = field(default="amount", init=False)

    def __post_init__(self):
        if not isinstance(self.value, Money):
            raise ValueError("accepted amount observations require exact Money")
        _choice(self.role, ("net_cash", "payable", "balance_only"), "amount role")


@dataclass(frozen=True)
class DateClaim:
    value: Day
    kind: str = field(default="settlement_date", init=False)

    def __post_init__(self):
        _day(self.value, "settlement-date claim")


@dataclass(frozen=True)
class StateClaim:
    """value=None is a metadata-only observation: it asserts no cash state."""
    value: Optional[CashState]
    approval_state: Literal["confirmed", "unconfirmed", "conditional", "unknown"] = "unknown"
    obligation_state: Literal["outstanding", "closed", "disputed", "unknown"] = "unknown"
    kind: str = field(default="cash_state", init=False)

    def __post_init__(self):
        if self.value is not None:
            _choice(self.value, CASH_STATES, "cash state")
        _choice(self.approval_state, ("confirmed", "unconfirmed", "conditional", "unknown"), "approval state")
        _choice(self.obligation_state, ("outstanding", "closed", "disputed", "unknown"), "obligation state")
        if self.value is None and self.approval_state == "unknown" and self.obligation_state == "unknown":
            raise ValueError("metadata-only state claim needs an approval or obligation observation")


@dataclass(frozen=True)
class IncomeRoleClaim:
    value: IncomeRole
    kind: str = field(default="income_role", init=False)

    def __post_init__(self):
        _choice(self.value, ("regular_salary", "one_off", "prorated", "unknown", "not_applicable"), "income role")


@dataclass(frozen=True)
class CancelClaim:
    kind: str = field(default="cancel", init=False)


@dataclass(frozen=True)
class SeriesAmountClaim:
    value: Money
    kind: str = field(default="series_amount", init=False)

    def __post_init__(self):
        if not isinstance(self.value, Money):
            raise ValueError("series amount requires exact Money")


@dataclass(frozen=True)
class SeriesScaleClaim:
    multiplier: Decimal
    kind: str = field(default="series_scale", init=False)

    def __post_init__(self):
        if not isinstance(self.multiplier, Decimal) or not self.multiplier.is_finite() or self.multiplier <= 0:
            raise ValueError("multiplier must be a positive finite Decimal")


@dataclass(frozen=True)
class RecurrenceClaim:
    cadence: Cadence
    amount: Optional[Money] = None
    income_kind: str = "not_applicable"
    kind: str = field(default="recurrence", init=False)

    def __post_init__(self):
        if not isinstance(self.cadence, (CalendarMonth, FixedDays)):
            raise ValueError("recurrence requires a supported cadence")
        if self.amount is not None and not isinstance(self.amount, Money):
            raise ValueError("recurrence amount must be Money or unknown")
        _choice(self.income_kind, ("regular_salary", "other", "not_applicable"), "recurrence income kind")


@dataclass(frozen=True)
class NewCashClaim:
    event_type: str
    category: str
    direction: Direction
    amount: Optional[Money]
    currency: Currency
    settlement_date: Optional[Day]
    state: CashState
    income_kind: str = "not_applicable"
    kind: str = field(default="new_cash", init=False)

    def __post_init__(self):
        _choice(self.currency, CURRENCIES, "new cash currency")
        _choice(self.direction, ("debit", "credit", "non_cash"), "new cash direction")
        _choice(self.state, CASH_STATES, "new cash state")
        _choice(self.income_kind, ("regular_salary", "one_off", "other", "not_applicable"), "new cash income kind")
        if not self.event_type or not self.category:
            raise ValueError("new cash needs evidenced type/category")
        if self.amount is not None and (not isinstance(self.amount, Money) or self.amount.currency != self.currency):
            raise ValueError("new cash amount currency mismatch")
        if self.settlement_date is not None:
            _day(self.settlement_date, "new cash settlement date")


@dataclass(frozen=True)
class RelationClaim:
    other_event_id: str
    relation: Literal["same_cash_occurrence", "refund_of", "valuation_of", "internal_transfer_leg", "independent_cash_leg", "retry_of"]
    kind: str = field(default="relation", init=False)


Claim = Union[AmountClaim, DateClaim, StateClaim, IncomeRoleClaim, CancelClaim,
              SeriesAmountClaim, SeriesScaleClaim, RecurrenceClaim, NewCashClaim, RelationClaim]


@dataclass(frozen=True)
class Fact:
    fact_id: str
    target: FactTarget
    claim: Claim
    effective_on: Optional[Day] = None
    valid_until: Optional[Day] = None
    scope: Literal["occurrence", "series"] = "occurrence"
    evidence: Tuple[EvidenceRef, ...] = ()
    supersedes_fact_ids: Tuple[str, ...] = ()
    certainty: Literal["supported", "ambiguous", "unreadable"] = "supported"
    action: Action = "observe"

    def __post_init__(self):
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "supersedes_fact_ids", tuple(self.supersedes_fact_ids))
        if not self.fact_id or not isinstance(self.target, (EventTarget, SeriesTarget, NewOccurrenceTarget)):
            raise ValueError("fact requires an ID and typed target")
        if not isinstance(self.claim, (AmountClaim, DateClaim, StateClaim, IncomeRoleClaim, CancelClaim,
                                       SeriesAmountClaim, SeriesScaleClaim, RecurrenceClaim, NewCashClaim, RelationClaim)):
            raise ValueError("fact requires a closed typed claim, never an arbitrary patch")
        _choice(self.action, ("observe", "confirm", "amend", "cancel", "settle", "delay", "start", "end"), "fact action")
        _choice(self.scope, ("occurrence", "series"), "fact scope")
        _choice(self.certainty, ("supported", "ambiguous", "unreadable"), "fact certainty")
        if self.effective_on is not None:
            _day(self.effective_on, "effective_on")
        if self.valid_until is not None:
            _day(self.valid_until, "valid_until")
        if self.effective_on and self.valid_until and self.effective_on > self.valid_until:
            raise ValueError("inclusive applicability bounds are reversed")


@dataclass(frozen=True)
class FactBatch:
    request_id: str
    user_id: str
    facts: Tuple[Fact, ...] = ()
    issues: Tuple[EvidenceIssue, ...] = ()
    schema_version: str = "financial-facts/v1"

    def __post_init__(self):
        if self.schema_version != "financial-facts/v1":
            raise ValueError("unsupported FactBatch schema")
        object.__setattr__(self, "facts", tuple(self.facts))
        object.__setattr__(self, "issues", tuple(self.issues))


@dataclass(frozen=True)
class FinancialProfile:
    user_id: str
    home_currency: Currency
    available: Money
    minimum: Money
    protected_categories: frozenset = frozenset()
    reducible_categories: frozenset = frozenset()
    stoppable_categories: frozenset = frozenset()

    def __post_init__(self):
        if self.available.currency != self.home_currency or self.minimum.currency != self.home_currency:
            raise ValueError("profile amounts must be home currency")
        for name in ("protected_categories", "reducible_categories", "stoppable_categories"):
            object.__setattr__(self, name, frozenset(getattr(self, name)))


@dataclass(frozen=True)
class EventRecord:
    event_id: str
    user_id: str
    event_type: str
    description: str
    category: str
    direction: Direction
    amount: Optional[Money]
    currency: Currency
    event_date: Day
    settlement_date: Optional[Day]
    status: CashState
    source: SourceRef
    linked_event_id: Optional[str] = None
    flexibility: Flexibility = "fixed"
    minimum_allowed_amount: Optional[Money] = None

    def __post_init__(self):
        _choice(self.direction, ("debit", "credit", "non_cash"), "direction")
        _choice(self.status, CASH_STATES, "status")
        _choice(self.currency, CURRENCIES, "currency")
        _choice(self.flexibility, FLEXIBILITIES, "flexibility")
        _day(self.event_date, "event_date")
        if self.settlement_date is not None:
            _day(self.settlement_date, "settlement_date")
        for amount in (self.amount, self.minimum_allowed_amount):
            if amount is not None and amount.currency != self.currency:
                raise ValueError("event amount/floor currency mismatch")


@dataclass(frozen=True)
class FxRate:
    rate_date: Day
    from_currency: Currency
    to_currency: Currency
    rate: Decimal
    source: SourceRef

    def __post_init__(self):
        _day(self.rate_date, "rate_date")
        _choice(self.from_currency, CURRENCIES, "from_currency")
        _choice(self.to_currency, CURRENCIES, "to_currency")
        if not isinstance(self.rate, Decimal) or not self.rate.is_finite() or self.rate <= 0:
            raise ValueError("FX rate must be a positive finite Decimal")


@dataclass(frozen=True)
class FinancialInput:
    request_id: str
    user_id: str
    request_date: Day
    requested: Money
    profile: FinancialProfile
    events: Tuple[EventRecord, ...] = ()
    fx_rates: Tuple[FxRate, ...] = ()
    sources: Tuple[SourceRef, ...] = ()
    included_hold_ids: Tuple[str, ...] = ()

    def __post_init__(self):
        _day(self.request_date, "request_date")
        if self.profile.user_id != self.user_id or self.requested.currency != self.profile.home_currency:
            raise ValueError("request/profile identity or currency mismatch")
        for name in ("events", "fx_rates", "sources", "included_hold_ids"):
            object.__setattr__(self, name, tuple(getattr(self, name)))


@dataclass(frozen=True)
class ForecastPolicy:
    version: str = "financial-policy/v1"
    horizon_days: int = 90
    include_end: bool = True
    money_scale: int = 2
    same_day: str = "mandatory_debits_then_confirmed_credits_then_request_payments"
    settled_on_anchor: str = "included"
    pending_in_anchor: str = "not_included_unless_evidenced"
    history_days: int = 180
    periodic_min_occurrences: int = 3
    income_min_occurrences: int = 3
    recent_amount_occurrences: int = 6
    irregular_budget_weeks: int = 12
    irregular_min_full_bins: int = 4
    irregular_min_nonempty_bins: int = 3
    round_debits: str = "ceil"
    round_credits: str = "floor"
    # A development slice must never certify a full forecast with inference off.
    projection_mode: Literal["full", "explicit_only"] = "full"

    def __post_init__(self):
        if (self.horizon_days != 90 or not self.include_end or self.money_scale != 2
                or self.same_day != "mandatory_debits_then_confirmed_credits_then_request_payments"
                or self.settled_on_anchor != "included"
                or self.round_debits != "ceil" or self.round_credits != "floor"):
            raise ValueError("unsupported safety-policy boundary convention")
        for name in ("history_days", "periodic_min_occurrences", "income_min_occurrences",
                     "recent_amount_occurrences", "irregular_budget_weeks", "irregular_min_full_bins",
                     "irregular_min_nonempty_bins"):
            _integer(getattr(self, name), name, 1)
        _choice(self.projection_mode, ("full", "explicit_only"), "projection mode")


@dataclass(frozen=True)
class BalanceAnchor:
    date: Day
    supplied_available: Money
    gross_cash_minor: int
    holds_already_included_minor: int
    minimum_minor: int
    included_hold_ids: Tuple[str, ...] = ()
    convention: str = "request_snapshot_settled_included"
    source: Optional[SourceRef] = None


@dataclass(frozen=True)
class ResolvedEvent:
    canonical_cash_id: str
    source_event_ids: Tuple[str, ...]
    event: EventRecord
    disposition: str
    reason_code: str
    fact_ids: Tuple[str, ...] = ()
    evidence_ids: Tuple[str, ...] = ()
    income_role: IncomeRole = "not_applicable"
    approval_state: str = "unknown"
    obligation_state: str = "unknown"


@dataclass(frozen=True)
class RecurringSeries:
    series_id: str
    user_id: str
    category: str
    direction: Direction
    currency: Currency
    cadence: Optional[Cadence]
    estimator: str
    support_event_ids: Tuple[str, ...]
    confidence_basis: str
    active_from: Day
    amount: Optional[Money]
    active_until: Optional[Day] = None
    flexibility: Flexibility = "fixed"
    floor: Optional[Money] = None


@dataclass(frozen=True)
class Occurrence:
    occurrence_id: str
    cash_date: Optional[Day]
    home_amount: Money
    direction: Literal["debit", "credit"]
    canonical_cash_id: Optional[str] = None
    series_id: Optional[str] = None
    nominal_cycle_date: Optional[Day] = None
    origin: str = "explicit"
    coverage: str = "separate_obligation"
    native_amount: Optional[Money] = None
    fx_source_id: Optional[str] = None
    pending_reservation_id: Optional[str] = None
    source_event_ids: Tuple[str, ...] = ()
    fact_ids: Tuple[str, ...] = ()
    evidence_ids: Tuple[str, ...] = ()
    editable: bool = False
    # Budget residuals reference real covered occurrences, never arbitrary savings.
    covered_occurrence_ids: Tuple[str, ...] = ()
    budget_total_minor: Optional[int] = None


@dataclass(frozen=True)
class Checkpoint:
    checkpoint_id: str
    date: Day
    phase: str
    operation_id: str
    cash_delta_minor: int
    hold_delta_minor: int
    cash_minor: int
    holds_minor: int
    available_minor: int
    provenance_ids: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Forecast:
    start: Day
    end: Day
    home_currency: Currency
    checkpoints: Tuple[Checkpoint, ...]
    baseline_feasible: Optional[bool]
    minimum_available_minor: Optional[int]
    binding_checkpoint_ids: Tuple[str, ...]
    proof_status: ProofStatus
    issues: Tuple[EvidenceIssue, ...] = ()


@dataclass(frozen=True)
class CapacityMetrics:
    amount_safe_to_pay: Optional[Money]
    earliest_date_for_full_payment: Optional[Day]
    baseline_feasible: Optional[bool]
    proof_status: ProofStatus
    binding_checkpoint_ids: Tuple[str, ...] = ()
    issue_codes: Tuple[str, ...] = ()
    context_hash: str = ""
    policy_version: str = "financial-policy/v1"


@dataclass(frozen=True)
class ChangeTarget:
    anchor_event_id: str
    series_id: str
    category: str
    flexibility: Flexibility
    floor: Optional[Money]
    allowed_actions: Tuple[str, ...]
    eligible_occurrence_ids: Tuple[str, ...]
    explanation: str = ""


@dataclass(frozen=True)
class CoreContext:
    context_hash: str
    policy: ForecastPolicy
    request_id: str
    user_id: str
    request_date: Day
    requested: Money
    anchor: BalanceAnchor
    horizon_end: Day
    resolved_events: Tuple[ResolvedEvent, ...]
    series: Tuple[RecurringSeries, ...]
    occurrences: Tuple[Occurrence, ...]
    baseline: Forecast
    capacity: CapacityMetrics
    change_targets: Tuple[ChangeTarget, ...] = ()
    issues: Tuple[EvidenceIssue, ...] = ()
    schema_version: str = "financial-core/v1"


@dataclass(frozen=True)
class Payment:
    date: Day
    amount: Money
    payment_id: str

    def __post_init__(self):
        _day(self.date, "payment date")
        if not isinstance(self.amount, Money):
            raise ValueError("payment amount must be exact Money")
        if not self.payment_id:
            raise ValueError("payment_id is required")


@dataclass(frozen=True)
class Stop:
    anchor_event_id: str


@dataclass(frozen=True)
class ReduceTo:
    anchor_event_id: str
    new_amount: Money


SpendingChange = Union[Stop, ReduceTo]


@dataclass(frozen=True)
class Plan:
    """Candidate identity/effects only; eligibility, totals and rank are derived."""
    candidate_id: str
    method: Literal["full_payment", "partial_payment", "installments", "wait"]
    payments: Tuple[Payment, ...]
    core_hash: str
    envelope_hash: str
    changes: Tuple[SpendingChange, ...] = ()
    payment_option_id: Optional[str] = None
    origin: str = "generated"
    family: str = ""
    evidence_ids: Tuple[str, ...] = ()

    def __post_init__(self):
        _choice(self.method, ("full_payment", "partial_payment", "installments", "wait"), "plan method")
        for name in ("payments", "changes", "evidence_ids"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if any(not isinstance(c, (Stop, ReduceTo)) for c in self.changes):
            raise ValueError("plan effects must be Stop/ReduceTo handles")


@dataclass(frozen=True)
class PlanSafety:
    safe: bool
    proof_status: ProofStatus
    minimum_available: Optional[Money]
    first_violation_checkpoint_id: Optional[str]
    reason_codes: Tuple[str, ...]
    forecast: Optional[Forecast]
    context_hash: str = ""
    occurrence_deltas: Tuple[Tuple[str, int], ...] = ()


def canonical_data(value):
    """Stable wire/provenance view, with no binary floating-point numbers."""
    if isinstance(value, Money):
        return {"currency": value.currency, "amount": f"{value.minor // 100}.{value.minor % 100:02d}"}
    if is_dataclass(value):
        return {f.name: canonical_data(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("nonfinite decimal cannot be serialized")
        return str(value)
    if isinstance(value, (tuple, list)):
        return [canonical_data(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(canonical_data(item) for item in value)
    if isinstance(value, dict):
        return {key: canonical_data(item) for key, item in sorted(value.items())}
    if isinstance(value, float):
        raise ValueError("binary float is forbidden in financial serialization")
    return value


def canonical_hash(value) -> str:
    payload = json.dumps(canonical_data(value), ensure_ascii=True, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
