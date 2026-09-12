"""Participant-only CSV I/O and immutable indexes (no financial inference).

Money stays source text until the core's sole Money parser admits it. This module
never reads sample answers, predictions, images, .env, or organizer-only files in
its production loader. Evidence selection and image decoding belong to evidence.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Optional, Tuple

from buy_wait.contracts import (
    EventRecord, FinancialInput, FinancialProfile, FxRate, Money, SourceRef, canonical_hash,
)
from buy_wait.core.money import parse_money, parse_optional_money

REQUEST_FIELDS = (
    "request_id", "user_id", "request_date", "request_type", "requested_amount",
    "desired_completion_date", "allows_partial_payment", "request_text",
)
OUTPUT_FIELDS = (
    "request_id", "amount_safe_to_pay", "affordability_status",
    "recommended_payment_method", "payment_plan", "earliest_date_for_full_payment",
    "spending_changes_needed", "decision_explanation",
)
SAMPLE_FIELDS = REQUEST_FIELDS + OUTPUT_FIELDS[1:]
SCHEMAS = {
    "requests.csv": REQUEST_FIELDS,
    "financial_profiles.csv": (
        "user_id", "home_currency", "current_available_balance", "minimum_balance_to_keep",
        "financial_priorities", "expense_categories_to_protect",
        "expense_categories_user_is_willing_to_reduce",
        "expense_categories_user_is_willing_to_stop",
        "payment_methods_user_will_consider", "max_installment_months",
    ),
    "financial_events.csv": (
        "event_id", "user_id", "event_type", "description", "category", "direction",
        "amount", "currency", "event_date", "settlement_date", "status",
        "linked_event_id", "flexibility", "minimum_allowed_amount",
    ),
    "exchange_rates.csv": ("rate_date", "from_currency", "to_currency", "rate"),
    "request_payment_options.csv": (
        "payment_option_id", "request_id", "payment_method", "payment_amount",
        "number_of_payments", "first_payment_date", "payment_frequency_days",
        "financing_fee", "total_payable_amount",
    ),
    "messages.csv": (
        "message_id", "user_id", "request_id", "related_event_id", "sent_at",
        "source_type", "message_text",
    ),
    "images.csv": ("image_id", "user_id", "request_id", "related_event_id"),
}
CONTEXT_FILES = tuple(name for name in SCHEMAS if name != "requests.csv")
_REQUEST_TYPES = frozenset((
    "purchase", "travel", "education", "family_transfer", "debt_repayment",
    "investment", "housing", "emergency_expense", "other",
))


class DataError(ValueError):
    """Invalid input; messages identify a field/record, never echo financial text."""


def iso_day(value: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise DataError("invalid ISO date")
    try:
        result = date.fromisoformat(value)
    except ValueError as exc:
        raise DataError("invalid calendar date") from exc
    return result


def read_table(root: Path, filename: str, *, sample_fixture: bool = False):
    """Read one allowlisted CSV; the explicit sample route is harness-only.

    Returns immutable source-string rows and the hash of the exact parsed bytes.
    No path supplied by a CSV row can select a file. Reject symlinks before read.
    """
    if filename == "sample_requests.csv" and sample_fixture:
        fields = SAMPLE_FIELDS
    elif filename in SCHEMAS and not sample_fixture:
        fields = SCHEMAS[filename]
    else:
        raise DataError("CSV filename is not allowed for this loader")
    path = Path(root).resolve() / filename
    if path.is_symlink() or not path.is_file():
        raise DataError(f"missing or unsafe participant file: {filename}")
    if path.stat().st_size > 64 * 1024 * 1024:
        raise DataError(f"participant file exceeds 64 MiB: {filename}")
    payload = path.read_bytes()
    try:
        reader = csv.reader(io.StringIO(payload.decode("utf-8-sig"), newline=""), strict=True)
        if tuple(next(reader, ())) != fields:
            raise DataError(f"unexpected CSV header: {filename}")
        rows = []
        for record_number, values in enumerate(reader, 1):
            if len(values) != len(fields):
                raise DataError(f"wrong CSV field count: {filename}, record {record_number}")
            rows.append(MappingProxyType(dict(zip(fields, values))))
    except (UnicodeError, csv.Error) as exc:
        raise DataError(f"invalid UTF-8/CSV: {filename}") from exc
    return tuple(rows), hashlib.sha256(payload).hexdigest()


def project_request(row):
    """Allowlist, never subtract a presumed list of labels from a sample row."""
    try:
        result = {field: row[field] for field in REQUEST_FIELDS}
    except KeyError as exc:
        raise DataError("missing request input field") from exc
    if any(not isinstance(v, str) or not v.strip() for v in result.values()):
        raise DataError("request fields must be nonempty source strings")
    if result["allows_partial_payment"] not in ("true", "false"):
        raise DataError("invalid allows_partial_payment token")
    if result["request_type"] not in _REQUEST_TYPES:
        raise DataError("invalid request_type token")
    if iso_day(result["desired_completion_date"]) < iso_day(result["request_date"]):
        raise DataError("completion deadline precedes request")
    return MappingProxyType(result)


def _unique(rows, key, table):
    found = {}
    for number, row in enumerate(rows, 1):
        value = row[key]
        if not value or value in found:
            raise DataError(f"blank/duplicate identity: {table}, record {number}, {key}")
        found[value] = row
    return MappingProxyType(found)


def _group(rows, key):
    result = {}
    for row in rows:
        result.setdefault(row[key], []).append(row)
    return MappingProxyType({k: tuple(v) for k, v in result.items()})


def load_requests(root: Path):
    rows, digest = read_table(root, "requests.csv")
    requests = tuple(project_request(row) for row in rows)
    _unique(requests, "request_id", "requests.csv")
    return requests, digest


@dataclass(frozen=True)
class RequestInput:
    """The sole integration-owned eight-field typed purchase-request view."""
    request_id: str
    user_id: str
    request_date: date
    request_type: str
    requested_amount: Money
    desired_completion_date: date
    allows_partial_payment: bool
    request_text: str

    def __post_init__(self):
        if type(self.request_date) is not date or type(self.desired_completion_date) is not date:
            raise DataError("typed request requires calendar dates")
        if self.desired_completion_date < self.request_date or type(self.allows_partial_payment) is not bool:
            raise DataError("invalid typed request deadline/partial flag")
        if not isinstance(self.requested_amount, Money) or self.request_type not in _REQUEST_TYPES:
            raise DataError("typed request requires canonical Money and request type")
        if any(not isinstance(v, str) or not v.strip() for v in (self.request_id, self.user_id, self.request_text)):
            raise DataError("typed request identifiers/text must be nonempty")


@dataclass(frozen=True)
class PlanningPreferences:
    methods: frozenset
    max_installment_months: Optional[int]
    protected_categories: frozenset
    reducible_categories: frozenset
    stoppable_categories: frozenset
    financial_priorities: Tuple[str, ...]

    def __post_init__(self):
        for field in ("methods", "protected_categories", "reducible_categories", "stoppable_categories"):
            values = getattr(self, field)
            if isinstance(values, str):
                raise DataError("typed preferences require collections of complete tokens")
            values = tuple(values)
            if any(not isinstance(v, str) or not v for v in values):
                raise DataError("typed preferences require collections of complete tokens")
            object.__setattr__(self, field, frozenset(values))
        if not self.methods <= {"full_payment", "partial_payment", "installments"}:
            raise DataError("unknown exact payment preference token")
        cap = self.max_installment_months
        if cap is not None and (type(cap) is not int or cap <= 0):
            raise DataError("installment cap must be None or a positive integer")
        if isinstance(self.financial_priorities, str):
            raise DataError("priorities require an ordered token collection")
        object.__setattr__(self, "financial_priorities", tuple(self.financial_priorities))


@dataclass(frozen=True)
class SuppliedOption:
    payment_option_id: str
    request_id: str
    payment_method: str
    payment_amount: Money
    number_of_payments: int
    first_payment_date: date
    payment_frequency_days: Optional[int]
    financing_fee: Money
    total_payable_amount: Money

    def __post_init__(self):
        if not all(isinstance(v, Money) for v in (self.payment_amount, self.financing_fee, self.total_payable_amount)):
            raise DataError("supplied amounts require canonical Money")
        if self.payment_method not in ("full_payment", "installments") or type(self.first_payment_date) is not date:
            raise DataError("invalid typed option method/date")
        if type(self.number_of_payments) is not int or self.number_of_payments < 1:
            raise DataError("option count must be a positive integer")
        interval = self.payment_frequency_days
        if interval is not None and (type(interval) is not int or interval <= 0):
            raise DataError("option interval must be None or a positive integer")
        if any(not isinstance(v, str) or not v for v in (self.payment_option_id, self.request_id)):
            raise DataError("option identifiers must be nonempty")


@dataclass(frozen=True)
class PlanningContext:
    """CSV/envelope owner only; no core balances, capacity or plan/result clone.

    options are stored once, in stable ID order. The index is an immutable view;
    the hash covers the typed request/preferences/options, not mutable CSV order.
    """
    request: RequestInput
    preferences: PlanningPreferences
    options: Tuple[SuppliedOption, ...]

    def __post_init__(self):
        if not isinstance(self.request, RequestInput) or not isinstance(self.preferences, PlanningPreferences):
            raise DataError("planning context requires the authoritative typed views")
        options = tuple(self.options)
        if not 2 <= len(options) <= 4 or any(not isinstance(o, SuppliedOption) for o in options):
            raise DataError("planning context requires two to four typed supplied options")
        if len({o.payment_option_id for o in options}) != len(options):
            raise DataError("duplicate typed option ID")
        if any(o.request_id != self.request.request_id for o in options):
            raise DataError("typed option belongs to another request")
        object.__setattr__(self, "options", tuple(sorted(options, key=lambda o: o.payment_option_id)))

    @property
    def options_by_id(self) -> Mapping[str, SuppliedOption]:
        return MappingProxyType({o.payment_option_id: o for o in self.options})

    @property
    def context_hash(self) -> str:
        return canonical_hash({"schema": "planning-context/v1", "request": self.request,
                               "preferences": self.preferences, "options": self.options})


def _tokens(value):
    if value in ("", "none"):
        return frozenset()
    values = value.split("|")
    if any(not v or v != v.strip() for v in values):
        raise DataError("malformed pipe-delimited preference/category list")
    return frozenset(values)


def _optional_count(value):
    if value == "":
        return None
    if not re.fullmatch(r"[1-9][0-9]*", value):
        raise DataError("count/interval must be blank or a positive integer")
    return int(value)


def _preferences(profile):
    methods = _tokens(profile["payment_methods_user_will_consider"])
    if not methods <= {"full_payment", "partial_payment", "installments"}:
        raise DataError("unknown exact payment preference token")
    priorities = profile["financial_priorities"]
    return PlanningPreferences(
        methods, _optional_count(profile["max_installment_months"]),
        _tokens(profile["expense_categories_to_protect"]),
        _tokens(profile["expense_categories_user_is_willing_to_reduce"]),
        _tokens(profile["expense_categories_user_is_willing_to_stop"]),
        tuple(priorities.split("|")) if priorities and priorities != "none" else (),
    )


class Dataset:
    """Integration-owned table/index container; core alone owns financial records."""

    @classmethod
    def load(cls, root: Path):
        tables, hashes = {}, {}
        for name in CONTEXT_FILES:
            tables[name], hashes[name] = read_table(root, name)
        return cls(tables, hashes)

    def __init__(self, tables, hashes):
        # Freeze again for callers constructing synthetic in-memory tables.
        self.tables = MappingProxyType({
            name: tuple(MappingProxyType(dict(row)) for row in tables[name])
            for name in CONTEXT_FILES
        })
        self.source_hashes = MappingProxyType(dict(hashes))
        self._ordinals = {name: {id(row): i for i, row in enumerate(rows, 1)}
                          for name, rows in self.tables.items()}
        self.profiles = _unique(self.tables["financial_profiles.csv"], "user_id", "profiles")
        self.events = _unique(self.tables["financial_events.csv"], "event_id", "events")
        self.options = _unique(self.tables["request_payment_options.csv"], "payment_option_id", "options")
        for name, key in (("messages.csv", "message_id"), ("images.csv", "image_id")):
            _unique(self.tables[name], key, name)
        for name in ("financial_events.csv", "messages.csv", "images.csv"):
            for row in self.tables[name]:
                if row["user_id"] not in self.profiles:
                    raise DataError(f"unknown profile link: {name}")
        for row in self.events.values():
            parent = row["linked_event_id"]
            if parent and (parent not in self.events or self.events[parent]["user_id"] != row["user_id"]):
                raise DataError("invalid same-user lifecycle link")
        completed = set()
        for event_id in self.events:
            seen = set()
            node = event_id
            while node and node not in completed:
                if node in seen:
                    raise DataError("cyclic event lifecycle")
                seen.add(node)
                node = self.events[node]["linked_event_id"]
            completed.update(seen)
        for name in ("messages.csv", "images.csv"):
            for row in self.tables[name]:
                target = row["related_event_id"]
                if target and (target not in self.events or self.events[target]["user_id"] != row["user_id"]):
                    raise DataError(f"invalid same-user event link: {name}")
        rate_keys = set()
        for row in self.tables["exchange_rates.csv"]:
            key = (row["rate_date"], row["from_currency"], row["to_currency"])
            if key in rate_keys:
                raise DataError("duplicate directed dated exchange rate")
            iso_day(key[0])
            rate_keys.add(key)
        self.events_by_user = _group(self.events.values(), "user_id")
        self.options_by_request = _group(self.options.values(), "request_id")
        self.messages_by_user = _group(self.tables["messages.csv"], "user_id")
        self.images_by_user = _group(self.tables["images.csv"], "user_id")
        self._message_requests = _group(self.tables["messages.csv"], "request_id")
        self._image_requests = _group(self.tables["images.csv"], "request_id")

    def source_location(self, table, row):
        """Host-only CSV locator; csv_sha256 is NOT an image/message leaf hash."""
        if table not in self._ordinals or id(row) not in self._ordinals[table]:
            raise DataError("locator requires an original indexed participant row")
        return MappingProxyType({"relative_path": table, "row_number": self._ordinals[table][id(row)],
                                 "csv_sha256": self.source_hashes[table]})

    def _source(self, table, row, identity):
        locator = self.source_location(table, row)
        return SourceRef(kind="csv", source_id=f"csv:{table}:{identity}", relative_path=table,
                         row_number=locator["row_number"], content_sha256=locator["csv_sha256"])

    def planning_context_for(self, row) -> PlanningContext:
        """Authoritative planning adapter; monetary admission calls core only."""
        context = self.context_for(row)
        request, profile = context["request"], context["profile"]
        currency = profile["home_currency"]
        try:
            typed = RequestInput(
                request["request_id"], request["user_id"], iso_day(request["request_date"]), request["request_type"],
                parse_money(request["requested_amount"], currency), iso_day(request["desired_completion_date"]),
                request["allows_partial_payment"] == "true", request["request_text"],
            )
            options = []
            for option in sorted(context["options"], key=lambda o: o["payment_option_id"]):
                count = _optional_count(option["number_of_payments"])
                if count is None or option["payment_method"] not in ("full_payment", "installments"):
                    raise DataError("invalid supplied option count/method")
                options.append(SuppliedOption(
                    option["payment_option_id"], option["request_id"], option["payment_method"],
                    parse_money(option["payment_amount"], currency), count, iso_day(option["first_payment_date"]),
                    _optional_count(option["payment_frequency_days"]), parse_money(option["financing_fee"], currency),
                    parse_money(option["total_payable_amount"], currency),
                ))
            # Schedule arithmetic, fees, deadline and acceptance are planner checks,
            # not permission to silently drop an inconvenient supplied option here.
            return PlanningContext(typed, _preferences(profile), tuple(options))
        except ValueError as exc:
            if isinstance(exc, DataError):
                raise
            raise DataError("invalid planning scalar in participant context") from exc

    def financial_input_for(self, row, *, evidence_sources=()) -> FinancialInput:
        """Narrow core view: no deadline, offers, request text or payment preferences.

        Only category protection/adjustability affects this profile. In particular,
        invalid payment preferences cannot alter/erase no-change capacity inputs.
        Unknown event amounts/dates remain None; no amount or hold is invented.
        Extraction supplies host-built message/image SourceRefs after selection.
        """
        request = project_request(row)
        profile = self.profiles.get(request["user_id"])
        if profile is None:
            raise DataError("request has no profile")
        currency = profile["home_currency"]
        evidence_sources = tuple(evidence_sources)
        sources = [self._source("financial_profiles.csv", profile, profile["user_id"])]
        try:
            financial_profile = FinancialProfile(
                profile["user_id"], currency, parse_money(profile["current_available_balance"], currency),
                parse_money(profile["minimum_balance_to_keep"], currency),
                _tokens(profile["expense_categories_to_protect"]),
                _tokens(profile["expense_categories_user_is_willing_to_reduce"]),
                _tokens(profile["expense_categories_user_is_willing_to_stop"]),
            )
            events = []
            for event in self.events_by_user.get(request["user_id"], ()):
                source = self._source("financial_events.csv", event, event["event_id"])
                sources.append(source)
                events.append(EventRecord(
                    event["event_id"], event["user_id"], event["event_type"], event["description"], event["category"],
                    event["direction"], parse_optional_money(event["amount"], event["currency"]), event["currency"],
                    iso_day(event["event_date"]), iso_day(event["settlement_date"]) if event["settlement_date"] else None,
                    event["status"], source, event["linked_event_id"] or None, event["flexibility"],
                    parse_optional_money(event["minimum_allowed_amount"], event["currency"]),
                ))
            rates = []
            for rate in self.tables["exchange_rates.csv"]:
                key = f"{rate['rate_date']}:{rate['from_currency']}:{rate['to_currency']}"
                source = self._source("exchange_rates.csv", rate, key)
                sources.append(source)
                rates.append(FxRate(iso_day(rate["rate_date"]), rate["from_currency"], rate["to_currency"],
                                    Decimal(rate["rate"]), source))
            if any(not isinstance(source, SourceRef) for source in evidence_sources):
                raise DataError("evidence sources must be the core's host-owned SourceRefs")
            return FinancialInput(
                request["request_id"], request["user_id"], iso_day(request["request_date"]),
                parse_money(request["requested_amount"], currency), financial_profile,
                tuple(events), tuple(rates), tuple(sources) + tuple(evidence_sources),
            )
        except (ValueError, InvalidOperation) as exc:
            if isinstance(exc, DataError):
                raise
            raise DataError("invalid financial scalar/state in participant context") from exc

    def context_for(self, row):
        """Raw input-only envelope; evidence owner selects from source candidates.

        The typed adapters above construct actual core records and the one owned
        planning envelope. This raw view never performs cash-state/FX arithmetic.
        """
        request = project_request(row)
        user, request_id = request["user_id"], request["request_id"]
        if user not in self.profiles:
            raise DataError("request has no profile")
        options = self.options_by_request.get(request_id, ())
        if not 2 <= len(options) <= 4:
            raise DataError("request requires two to four supplied options")
        for index in (self._message_requests, self._image_requests):
            if any(source["user_id"] != user for source in index.get(request_id, ())):
                raise DataError("request evidence crosses user boundary")
        messages = self.messages_by_user.get(user, ())
        images = self.images_by_user.get(user, ())
        locations = {f"message:{row['message_id']}": self.source_location("messages.csv", row) for row in messages}
        locations.update({f"image:{row['image_id']}": self.source_location("images.csv", row) for row in images})
        return MappingProxyType({
            "request": request, "profile": self.profiles[user],
            "events": self.events_by_user.get(user, ()), "options": options,
            "fx_rates": self.tables["exchange_rates.csv"],
            "message_candidates": messages, "image_candidates": images,
            "source_locations": MappingProxyType(locations), "source_hashes": self.source_hashes,
        })
