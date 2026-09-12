"""Exact CSV serialization and structural checks, not a financial safety proof.

The eventual runner must obtain core/planning certification before calling this
writer. Passing these checks alone never certifies affordability or grounding.
"""
from __future__ import annotations

import csv
import os
import re
import tempfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from buy_wait.data import OUTPUT_FIELDS, DataError, iso_day

STATUSES = ("affordable_now", "affordable_with_plan", "affordable_later", "not_affordable")
METHODS = ("full_payment", "partial_payment", "installments", "wait", "not_recommended")
_AMOUNT = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?\Z")
_ID = re.compile(r"[A-Za-z0-9_]+\Z")


def amount(value):
    if not isinstance(value, str) or not _AMOUNT.fullmatch(value):
        raise DataError("amount must be a nonnegative plain decimal with at most two places")
    return Decimal(value)


def schedule(value):
    if value == "none":
        return ()
    if not isinstance(value, str) or not value:
        raise DataError("invalid payment plan")
    result = []
    for part in value.split("|"):
        fields = part.split(":")
        if len(fields) != 2:
            raise DataError("invalid payment entry")
        day, cash = iso_day(fields[0]), amount(fields[1])
        # Shared Money permits zero; a zero request must not acquire an invented
        # positive minimum. Principal/partial/option checks constrain the plan.
        if result and day < result[-1][0]:
            raise DataError("payments must be chronological")
        result.append((day, cash))
    return tuple(result)


def actions(value):
    if value == "none":
        return ()
    if not isinstance(value, str) or not value:
        raise DataError("invalid spending changes")
    result, seen = [], set()
    for part in value.split("|"):
        fields = part.split(":")
        if len(fields) not in (2, 3) or not _ID.fullmatch(fields[1]):
            raise DataError("invalid action entry")
        kind, event = fields[:2]
        if event in seen:
            raise DataError("duplicate/conflicting event actions")
        seen.add(event)
        if kind == "stop" and len(fields) == 2:
            result.append((kind, event, None))
        elif kind == "reduce_to" and len(fields) == 3:
            result.append((kind, event, amount(fields[2])))
        else:
            raise DataError("invalid action grammar")
    if len(result) > 3:
        raise DataError("at most three spending changes")
    return tuple(result)


def structural_issues(row, request):
    """Return field-specific defects; eligibility/replay/grounding are separate."""
    issues = []
    def fail(field, code):
        issues.append({"field": field, "code": code})
    if set(row) != set(OUTPUT_FIELDS) or any(not isinstance(v, str) for v in row.values()):
        return [{"field": "row", "code": "OUTPUT_SCHEMA"}]
    if row["request_id"] != request["request_id"]:
        fail("request_id", "REQUEST_ID_MISMATCH")
    if row["affordability_status"] not in STATUSES:
        fail("affordability_status", "INVALID_STATUS")
    if row["recommended_payment_method"] not in METHODS:
        fail("recommended_payment_method", "INVALID_METHOD")
    if not row["decision_explanation"].strip():
        fail("decision_explanation", "EMPTY_EXPLANATION")
    try:
        requested, safe = amount(request["requested_amount"]), amount(row["amount_safe_to_pay"])
        if safe > requested:
            fail("amount_safe_to_pay", "AMOUNT_OUT_OF_BOUNDS")
    except DataError:
        fail("amount_safe_to_pay", "INVALID_AMOUNT")
        requested = safe = None
    start, deadline = iso_day(request["request_date"]), iso_day(request["desired_completion_date"])
    earliest = None
    if row["earliest_date_for_full_payment"]:
        try:
            earliest = iso_day(row["earliest_date_for_full_payment"])
            if not start <= earliest <= start + timedelta(days=90):
                fail("earliest_date_for_full_payment", "DATE_OUTSIDE_FORECAST")
        except DataError:
            fail("earliest_date_for_full_payment", "INVALID_DATE")
    try:
        payments = schedule(row["payment_plan"])
    except DataError:
        payments = None
        fail("payment_plan", "INVALID_SCHEDULE")
    try:
        changes = actions(row["spending_changes_needed"])
    except DataError:
        changes = None
        fail("spending_changes_needed", "INVALID_ACTIONS")
    status, method = row["affordability_status"], row["recommended_payment_method"]
    if payments is not None:
        if any(d < start or d > deadline or d > start + timedelta(days=90) for d, _ in payments):
            fail("payment_plan", "PAYMENT_DATE_INELIGIBLE")
        total = sum((p for _, p in payments), Decimal(0))
        if method == "not_recommended":
            if payments or changes:
                fail("payment_plan", "FALLBACK_HAS_PAYMENTS_OR_CHANGES")
            if status not in ("not_affordable", "affordable_later"):
                fail("affordability_status", "FALLBACK_STATUS")
        else:
            if not payments:
                fail("payment_plan", "MISSING_PAYMENTS")
            if requested is not None and method != "installments" and total != requested:
                fail("payment_plan", "INCOMPLETE_PRINCIPAL")
        if method in ("full_payment", "wait"):
            if len(payments) != 1 or (payments and (payments[0][0] != start if method == "full_payment" else payments[0][0] <= start)):
                fail("payment_plan", "ONE_SHOT_GRAMMAR")
        if method == "partial_payment":
            valid = (requested is not None and safe is not None and Decimal(0) < safe < requested
                     and earliest is not None and earliest > start
                     and request["allows_partial_payment"] == "true"
                     and payments == ((start, safe), (earliest, requested - safe)))
            if not valid:
                fail("payment_plan", "PARTIAL_GRAMMAR")
            if status != "affordable_with_plan":
                fail("affordability_status", "PARTIAL_STATUS")
        if method == "installments":
            if len(payments) < 2 or (requested is not None and total < requested):
                fail("payment_plan", "INSTALLMENT_STRUCTURE")
            if status != "affordable_with_plan":
                fail("affordability_status", "INSTALLMENT_STATUS")
    if status == "affordable_now":
        if method != "full_payment" or safe != requested or earliest != start or changes:
            fail("affordability_status", "AFFORDABLE_NOW_INCONSISTENT")
    if status == "affordable_later":
        if method not in ("wait", "not_recommended") or earliest is None or earliest <= start or changes:
            fail("affordability_status", "AFFORDABLE_LATER_INCONSISTENT")
        if method == "wait" and payments and payments[0][0] != earliest:
            fail("payment_plan", "WAIT_NOT_EARLIEST")
    if status == "affordable_with_plan" and method in ("full_payment", "wait") and not changes:
        fail("spending_changes_needed", "ONE_SHOT_PLAN_REQUIRES_CHANGES")
    if status == "not_affordable" and method != "not_recommended":
        fail("affordability_status", "NOT_AFFORDABLE_WITH_PAYMENT")
    return issues


def write_output(path: Path, rows, requests, *, dataset_root: Path):
    """Validate all structural rows before atomic CSV replacement.

    Does not run/invent a core proof. Intended only for a certified runner or
    synthetic tests. Dataset paths (including symlinks into them) are forbidden.
    """
    path, protected = Path(path), Path(dataset_root).resolve()
    resolved = path.resolve()
    if resolved.is_relative_to(protected) or path.suffix != ".csv" or path.is_symlink():
        raise DataError("output must be a CSV outside immutable dataset inputs")
    requests, rows = tuple(requests), tuple(rows)
    expected = [r["request_id"] for r in requests]
    if len(set(expected)) != len(expected):
        raise DataError("duplicate input request ID")
    if len(rows) != len(requests):
        raise DataError("output row coverage mismatch")
    by_id = {}
    for row in rows:
        key = row.get("request_id")
        if not isinstance(key, str) or key in by_id or key not in expected:
            raise DataError("unknown/duplicate output request ID")
        by_id[key] = row
    for request in requests:
        if structural_issues(by_id[request["request_id"]], request):
            raise DataError("output has structural defects; no file written")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".bw-output-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=OUTPUT_FIELDS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(by_id[key] for key in expected)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
