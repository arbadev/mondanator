"""Field-wise development agreement. No organizer weights or explanation match."""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from decimal import Decimal, localcontext

from buy_wait.data import DataError, iso_day
from buy_wait.output import METHODS, STATUSES, actions, amount, schedule, structural_issues

ROOT_CAUSES = (
    "retrieval/extraction", "cash-state/FX", "recurrence/forecast",
    "candidate generation", "eligibility", "ranking", "formatting",
)


def _summary(values):
    with localcontext() as ctx:
        ctx.prec = 40
        return {
            "count": len(values), "mean": str(sum(values) / Decimal(len(values))) if values else None,
            "max": str(max(values)) if values else None,
        }


def compare_predictions(requests, expected, predictions, currencies, *, audits=None):
    """Compare outputs only after inference; unknown audit evidence stays unknown.

    ``audits`` is an optional request-ID mapping from the future core/planning
    audit adapter. This function aggregates its evidence, not a second simulator.
    Without it, safety/eligibility/action-validity/grounding are NOT measured.
    """
    requests, expected = tuple(requests), tuple(expected)
    truth = {row["request_id"]: row for row in expected}
    request_ids = [row["request_id"] for row in requests]
    if len(truth) != len(expected) or len(set(request_ids)) != len(request_ids) or set(truth) != set(request_ids):
        raise DataError("expected/input ID coverage mismatch")
    by_id, duplicate_ids, unexpected = {}, set(), []
    for row in predictions:
        key = row.get("request_id")
        if not isinstance(key, str) or key not in truth:
            unexpected.append("unexpected_output_id")
        elif key in by_id:
            duplicate_ids.add(key)
        else:
            by_id[key] = row
    for key in duplicate_ids:
        by_id.pop(key, None)  # An arbitrary duplicate never receives credit.
    audits = audits or {}
    if set(audits) - set(request_ids):
        raise DataError("audit has unknown request ID")
    counts = Counter({key: 0 for key in (
        "missing_or_duplicate_rows", "structurally_invalid_rows", "affordability_status_matches",
        "recommended_payment_method_matches", "status_method_joint_matches", "amount_exact_matches",
        "amount_overestimates", "amount_underestimates", "amount_invalid_or_missing", "date_exact_matches",
        "payment_plan_reference_matches", "payment_plan_invalid_or_missing",
        "spending_changes_needed_reference_matches", "spending_changes_needed_invalid_or_missing",
    )})
    date_states = Counter({key: 0 for key in ("both_empty", "both_dates", "prediction_empty_expected_date",
                                             "prediction_date_expected_empty", "invalid_or_missing")})
    status_matrix = {s: {v: 0 for v in (*STATUSES, "<invalid>")} for s in STATUSES}
    method_matrix = {s: {v: 0 for v in (*METHODS, "<invalid>")} for s in METHODS}
    absolute_by_currency, normalized, date_absolute, date_signed = defaultdict(list), [], [], []
    per_request, errors = [], []
    for request in requests:
        key = request["request_id"]
        actual, reference = by_id.get(key), truth[key]
        fields, defects = {}, []
        def mismatch(field, got, wanted, *, cause=None, code="REFERENCE_MISMATCH"):
            errors.append({
                "request_id": key, "field": field, "actual": got, "expected": wanted,
                "code": code, "root_cause": cause, "owner": None, "evidence_ids": [],
                "candidate_id": None, "trace_step": None, "general_fix": None,
                "regression_id": None, "state": "open",
            })
        if actual is None:
            counts["missing_or_duplicate_rows"] += 1
            mismatch("row", None, "one output", cause="formatting", code="MISSING_OR_DUPLICATE")
        else:
            defects = structural_issues(actual, request)
            counts["structurally_invalid_rows"] += bool(defects)
            for defect in defects:
                mismatch(defect["field"], None, None, code=defect["code"])  # Detector stage is not a proven root cause.
        value = actual or {}
        for field, allowed, matrix in (
            ("affordability_status", STATUSES, status_matrix),
            ("recommended_payment_method", METHODS, method_matrix),
        ):
            predicted, wanted = value.get(field), reference[field]
            if wanted not in allowed:
                raise DataError("invalid expected categorical fixture")
            observed = predicted if predicted in allowed else "<invalid>"
            matrix[wanted][observed] += 1
            fields[field + "_match"] = predicted == wanted
            counts[field + "_matches"] += predicted == wanted
            if predicted != wanted:
                mismatch(field, predicted, wanted)
        counts["status_method_joint_matches"] += (fields["affordability_status_match"] and fields["recommended_payment_method_match"])
        try:
            predicted_amount = amount(value.get("amount_safe_to_pay"))
            wanted_amount = amount(reference["amount_safe_to_pay"])
            diff = predicted_amount - wanted_amount
            fields["amount_signed_error"] = str(diff)
            absolute_by_currency[currencies[key]].append(abs(diff))
            principal = amount(request["requested_amount"])
            if principal > 0:
                with localcontext() as ctx:
                    ctx.prec = 40
                    normalized.append(abs(diff) / principal)
            counts["amount_exact_matches"] += diff == 0
            counts["amount_overestimates"] += diff > 0
            counts["amount_underestimates"] += diff < 0
            if diff:
                mismatch("amount_safe_to_pay", value.get("amount_safe_to_pay"), reference["amount_safe_to_pay"])
        except DataError:
            counts["amount_invalid_or_missing"] += 1
            fields["amount_signed_error"] = None
        expected_date = iso_day(reference["earliest_date_for_full_payment"]) if reference["earliest_date_for_full_payment"] else None
        try:
            raw_date = value["earliest_date_for_full_payment"]
            got_date = iso_day(raw_date) if raw_date != "" else None
            counts["date_exact_matches"] += got_date == expected_date
            if got_date is None and expected_date is None:
                date_states["both_empty"] += 1
            elif got_date is None:
                date_states["prediction_empty_expected_date"] += 1
            elif expected_date is None:
                date_states["prediction_date_expected_empty"] += 1
            else:
                date_states["both_dates"] += 1
                days = (got_date - expected_date).days
                date_signed.append(Decimal(days))
                date_absolute.append(Decimal(abs(days)))
                fields["date_signed_days"] = days
            if got_date != expected_date:
                mismatch("earliest_date_for_full_payment", raw_date, reference["earliest_date_for_full_payment"])
        except (DataError, KeyError):
            date_states["invalid_or_missing"] += 1
        for field, parser in (("payment_plan", schedule), ("spending_changes_needed", actions)):
            try:
                got, wanted = parser(value.get(field)), parser(reference[field])
                if field == "spending_changes_needed":
                    got, wanted = sorted(got), sorted(wanted)
                matched = got == wanted
                counts[field + "_reference_matches"] += matched
                fields[field + "_reference_match"] = matched
                if not matched:
                    mismatch(field, value.get(field), reference[field])
            except DataError:
                counts[field + "_invalid_or_missing"] += 1
        audit = audits.get(key)
        # Absence is explicit. A structurally plausible CSV is not a safe plan.
        per_request.append({"request_id": key, "metrics": fields, "structural_issues": defects,
                            "audit": audit, "audit_status": ("not_checked" if audit is None else "supplied" if isinstance(audit, Mapping) else "invalid")})
    audit_rows = [a for a in audits.values() if a is not None]
    audit_dimensions = {}
    for dimension in ("safety", "schedule_validity", "eligibility", "action_validity", "explanation_grounding"):
        checked, invalid = [], 0
        for supplied in audit_rows:
            if not isinstance(supplied, Mapping):
                invalid += 1
                continue
            item = supplied.get(dimension)
            if item is None:
                continue
            if (not isinstance(item, Mapping) or type(item.get("checked")) is not bool
                    or (item["checked"] and not isinstance(item.get("violations"), (list, tuple)))):
                invalid += 1
            elif item["checked"]:
                checked.append(item)
        audit_dimensions[dimension] = {
            "checked": len(checked), "not_checked": len(requests) - len(checked), "invalid": invalid,
            "violations": sum(len(item["violations"]) for item in checked) if checked else None,
            "evidence": "supplied audit adapter; not inferred from reference agreement",
        }
    metrics = {
        "evaluation_kind": "repeatedly tuned public development fixtures; not independent generalization",
        "denominator_requests": len(requests), "counts": dict(counts),
        "coverage": {"received_unique_known_rows": len(by_id), "duplicate_ids": len(duplicate_ids),
                     "unexpected_rows": len(unexpected)},
        "amount_absolute_error_by_currency": {k: _summary(v) for k, v in sorted(absolute_by_currency.items())},
        "amount_normalized_absolute_error": _summary(normalized),
        "date_states": dict(date_states), "date_absolute_days": _summary(date_absolute),
        "date_signed_days": _summary(date_signed),
        "status_confusion": status_matrix, "method_confusion": method_matrix,
        "audits": {"supplied": len(audit_rows), "not_checked": len(requests) - sum(k in audits and audits[k] is not None for k in request_ids),
                   "safety": "not measured by CSV comparison", "action_validity": "requires planning/core audit",
                   "schedule_eligibility": "requires planning/core audit", "explanation_grounding": "requires trace audit; no literal comparison"},
        "audit_dimensions": audit_dimensions, "root_cause_categories": ROOT_CAUSES,
    }
    return {"metrics": metrics, "per_request": per_request, "errors": errors}
