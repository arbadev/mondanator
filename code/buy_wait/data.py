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
from datetime import date
from pathlib import Path
from types import MappingProxyType

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


class Dataset:
    """Integration-owned raw table/index container, not a core financial model."""

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

    def context_for(self, row):
        """Raw input-only envelope; evidence owner selects from source candidates.

        Core's eventual committed adapter constructs FinancialInput using its own
        Money/types. No snapshot arithmetic, source conflict decision or schema
        claiming to be FinancialInput is invented by this loader.
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
        return MappingProxyType({
            "request": request, "profile": self.profiles[user],
            "events": self.events_by_user.get(user, ()), "options": options,
            "fx_rates": self.tables["exchange_rates.csv"],
            "message_candidates": self.messages_by_user.get(user, ()),
            "image_candidates": self.images_by_user.get(user, ()),
            "source_hashes": self.source_hashes,
        })
