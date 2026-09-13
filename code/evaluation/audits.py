"""Independent consumer checks against authoritative modules, not another simulator.

Rebuild from trusted input rows, compare native trace claims, and replay the
ACTUAL supplied plan, never replace it with a preferred one. Source identity and
templated text consistency do not prove the semantics of model-extracted facts.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping

from buy_wait import contracts as c
from buy_wait.core import replay_financial_plan
from buy_wait.data import project_request
from buy_wait.evidence import EvidenceError, EvidenceIndex, resolve_image
from buy_wait.output import structural_issues
from buy_wait.planning.evaluator import static_rejections
from buy_wait.runner import plan_request

_DIMENSIONS = ("safety", "schedule_validity", "eligibility", "action_validity", "explanation_grounding")
_PROVED = {"resolved_under_policy", "conservative_bound"}


def _unchecked(reason):
    return {"checked": False, "violations": None, "reason": reason}


def _source_identity(data, raw, financial, trace, dataset_root):
    """Use the owner's selector/decoder; only cross-check identities and hashes."""
    initial = data.financial_input_for(raw)
    base_ids = {source.source_id for source in initial.sources}
    extra = tuple(source for source in financial.sources if source.source_id not in base_ids)
    selection = EvidenceIndex.from_context(data.context_for(raw)).retrieve(
        user_id=initial.user_id, request_id=initial.request_id,
        event_ids=tuple(event.event_id for event in initial.events), as_of=initial.request_date)
    evidence = trace.get("evidence")
    violations = []
    if evidence is None:
        if extra or selection.sources:
            return extra, {"checked": False, "violations": [{"code": "EVIDENCE_TRACE_ABSENT"}], "reason": "cannot verify selected evidence"}
        return extra, {"checked": True, "violations": [], "scope": "no selected document sources; CSV recomposition only"}
    source_runs = evidence["sources"]
    if (evidence["mode"] != "cache-only" or tuple(s["source_id"] for s in source_runs) != tuple(s.source_id for s in selection.sources)
            or tuple(evidence["excluded_future_ids"]) != selection.excluded_future_ids):
        violations.append({"code": "EVIDENCE_SELECTION_MISMATCH"})
    expected = {}
    for source in selection.sources:
        content, asset_issue = None, None
        if source.kind == "message":
            content = hashlib.sha256(source.text.encode()).hexdigest()
        else:
            try:
                content = resolve_image(dataset_root, source.source_id.split(":", 1)[1]).sha256
            except EvidenceError as exc:
                asset_issue = exc.code
        metadata_id = "csv:" + source.relative_path + ":" + source.source_id.split(":", 1)[1]
        expected[metadata_id] = c.SourceRef("csv", metadata_id, source.relative_path, row_number=source.row_number,
                                           content_sha256=source.csv_sha256 or "", locator="canonical_record_sha256=" + source.row_sha256)
        path = source.relative_path if source.kind == "message" else "media/images/" + source.source_id.split(":", 1)[1] + ".png"
        expected[source.source_id] = c.SourceRef(source.kind, source.source_id, path,
            row_number=source.row_number if source.kind == "message" else None, source_type=source.source_type,
            known_at=source.known_at, content_sha256=content or "", locator="metadata=" + metadata_id)
        actual = [s for s in source_runs if s["source_id"] == source.source_id]
        values = dict(row_number=source.row_number, csv_sha256=source.csv_sha256, row_sha256=source.row_sha256,
                      content_sha256=content, asset_issue=asset_issue, reasons=source.reasons)
        if len(actual) != 1 or any(actual[0].get(key) != value for key, value in values.items()):
            violations.append({"code": "SOURCE_TRACE_IDENTITY_MISMATCH", "source_id": source.source_id})
    if len(extra) != len(expected) or {s.source_id: s for s in extra} != expected:
        violations.append({"code": "SOURCE_REFERENCE_MISMATCH"})
    return extra, {"checked": True, "violations": violations,
                   "scope": "selected source IDs, dated metadata, locations and current decoded bytes; no trusted actor overrides"}


def audit_decision(data, request, trace, *, dataset_root, policy=None, max_candidates=None):
    """Fail-closed audit payload compatible with evaluation.metrics dimensions.

    Recomposition is an independent invocation, NOT independent financial/model
    truth: it deliberately uses the single canonical core and planning owners.
    A known unresolved result remains unchecked safety, never zero violations.
    """
    raw = project_request(request)
    result = {"schema_version": "buy-wait-trace-audit/v1", "request_id": raw["request_id"],
              "recomposition_checked": False, "integrity_passed": False, "integrity_violations": [],
              "source_identity": _unchecked("not reached"), "templated_text_consistency": _unchecked("not reached"),
              "financial_certified": False,
              **{name: _unchecked("no proved payment plan audited") for name in _DIMENSIONS}}
    result["explanation_grounding"] = _unchecked("independent semantic grounding of extracted facts is not performed; text/identity checks are separate")
    try:
        if not isinstance(trace, Mapping) or trace.get("schema_version") != "buy-wait-decision-trace/v1":
            raise ValueError("native decision trace required")
        extra, identity = _source_identity(data, raw, trace["financial_input"], trace, dataset_root)
        result["source_identity"] = identity
        result["integrity_violations"].extend(identity["violations"] or ())
        rebuilt = plan_request(data, raw, facts=trace["facts"], evidence_sources=extra,
                               policy=policy or c.ForecastPolicy(), max_candidates=max_candidates)
        result["recomposition_checked"] = True
        result["recomputed_core_hash"] = rebuilt["core"].context_hash
        result["core_proof_status"] = rebuilt["core"].capacity.proof_status
        for field in ("request", "source_hashes", "financial_input", "envelope", "core", "batches", "validations", "search", "ranking",
                      "recommendation", "formatting_issues", "row"):
            if c.canonical_hash(trace[field]) != c.canonical_hash(rebuilt[field]):
                result["integrity_violations"].append({"code": "TRACE_RECOMPOSITION_MISMATCH", "field": field})
        row = trace["row"]
        if row is not None:
            defects = structural_issues(row, raw)
            result["integrity_violations"].extend(defects)
            text_ok = rebuilt["row"] is not None and row.get("decision_explanation") == rebuilt["row"]["decision_explanation"]
            result["templated_text_consistency"] = {"checked": True, "violations": [] if text_ok else [{"code": "TEMPLATED_TEXT_MISMATCH"}]}
        winner = trace["ranking"]["winner"]
        if winner is not None and row is not None:
            plan = winner["plan"]  # audit this plan; never substitute rebuilt winner
            core, envelope = rebuilt["core"], rebuilt["envelope"]
            static = static_rejections(plan, envelope, core)
            for name, stages in (("schedule_validity", {"schedule", "option"}), ("eligibility", {"eligibility", "identity", "option"}),
                                 ("action_validity", {"action"})):
                result[name] = {"checked": True, "violations": [issue for issue in static if issue["stage"] in stages]}
            safety = replay_financial_plan(core, plan.payments, changes=plan.changes)
            result["replayed_plan"] = plan
            result["replay"] = safety
            if safety.proof_status in _PROVED and safety.forecast is not None:
                result["safety"] = {"checked": True, "violations": [] if safety.safe else [{"code": "PAYMENT_REPLAY_UNSAFE", "reason_codes": safety.reason_codes}]}
            else:
                result["safety"] = _unchecked("canonical replay proof remains unresolved")
                result["integrity_violations"].append({"code": "SELECTED_PLAN_REPLAY_UNRESOLVED"})
        for dimension in (*_DIMENSIONS, "templated_text_consistency"):
            if result[dimension]["checked"] and result[dimension]["violations"]:
                result["integrity_violations"].append({"code": "AUDIT_DIMENSION_VIOLATION", "dimension": dimension})
        result["integrity_passed"] = result["recomposition_checked"] and not result["integrity_violations"]
    except Exception as exc:
        result["integrity_violations"].append({"code": "AUDIT_EXECUTION_FAILED", "exception_type": type(exc).__name__})
        result["integrity_passed"] = False
    return result
