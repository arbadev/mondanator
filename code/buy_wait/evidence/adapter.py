"""The sole private bw.extraction/1 -> canonical financial-facts/v1 adapter.

Host/core owns target/actor identity, conflict resolution, materiality, recurrence,
FX and all arithmetic beyond exact unit conversion and inclusive date bounds.
No new cash/series identity is guessed from a topic, issuer name or source_type.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, localcontext
from typing import Mapping

from buy_wait import contracts as core
from buy_wait.core.money import MoneyError, parse_day, parse_money

from . import schema as private
from .extractor import ExtractionResult


@dataclass(frozen=True)
class AdaptedEvidence:
    """Transport envelope, not another financial fact schema or simulator."""
    batch: core.FactBatch
    sources: tuple[core.SourceRef, ...]


def event_descriptors(events: Mapping[str, core.EventRecord], *, user_id: str) -> dict[str, dict]:
    """Produce the same minimal descriptors used by extraction and validation."""
    descriptors = {}
    for key, event in sorted(events.items()):
        if not isinstance(event, core.EventRecord) or event.user_id != user_id or event.event_id != key:
            raise private.EvidenceError("cross_user_event")
        descriptors[key] = dict(event_id=key, user_id=user_id, description=event.description,
                                category=event.category, direction=event.direction, currency=event.currency,
                                status=event.status, event_date=event.event_date.isoformat(),
                                settlement_date=event.settlement_date.isoformat() if event.settlement_date else None,
                                linked_event_id=event.linked_event_id)
    return descriptors


def _sources(result: ExtractionResult, actor_key: str | None) -> tuple[core.SourceRef, ...]:
    source = result.source
    metadata = core.SourceRef("csv", "csv:" + source.relative_path + ":" + source.source_id.split(":", 1)[1], source.relative_path,
                              row_number=source.row_number, content_sha256=source.csv_sha256 or "",
                              locator="canonical_record_sha256=" + source.row_sha256)
    path = source.relative_path if source.kind == "message" else "media/images/" + source.source_id.split(":", 1)[1] + ".png"
    document = core.SourceRef(source.kind, source.source_id, path,
                              row_number=source.row_number if source.kind == "message" else None,
                              source_type=source.source_type, actor_key=actor_key, known_at=source.known_at,
                              content_sha256=result.content_sha256 or "",
                              locator="metadata=" + metadata.source_id)
    return document, metadata


def _evidence(source_id, fact):
    refs = []
    for span in fact.evidence:
        locator = (f"chars:{span.char_start}:{span.char_end}" if span.char_start is not None else
                   "box:" + private.canonical(span.box).decode() if span.box is not None else "visible_text")
        refs.append(core.EvidenceRef(source_id, locator, span.quote))
    return tuple(refs)


def _bounds(fact):
    window = fact.effect_window
    lower = parse_day(window.start_date) if window.start_date else None
    upper = parse_day(window.end_date) if window.end_date else None
    payload = fact.payload
    if isinstance(payload, private.Amount) and payload.condition:
        condition = payload.condition
        if condition.basis not in {"payment_date", "settlement_date"}:
            raise private.EvidenceError("unrepresentable_date_basis")
        lo = parse_day(condition.lower) if condition.lower else None
        hi = parse_day(condition.upper) if condition.upper else None
        try:
            if lo is not None and not condition.lower_inclusive:
                lo += timedelta(days=1)
            if hi is not None and not condition.upper_inclusive:
                hi -= timedelta(days=1)
        except OverflowError:
            raise private.EvidenceError("unrepresentable_interval") from None
        lower = max(x for x in (lower, lo) if x is not None) if lower or lo else None
        upper = min(x for x in (upper, hi) if x is not None) if upper or hi else None
    if lower and upper and lower > upper:
        raise private.EvidenceError("empty_effective_interval")
    return lower, upper


def adapt_extraction(result: ExtractionResult, *, request_id: str, user_id: str,
                     events: Mapping[str, core.EventRecord],
                     resolved_targets: Mapping[str, core.FactTarget] | None = None,
                     new_event_fields: Mapping[str, Mapping[str, str]] | None = None,
                     actor_key: str | None = None) -> AdaptedEvidence:
    """Adapt observations after a host/core target handoff.

    Explicit event hints refer only to supplied same-user events and remain
    source-backed candidate facts for core. A series or unlinked observation
    requires resolved_targets[private_local_id]; no fuzzy resolver is hidden here.
    NewOccurrenceTarget groups also require host event_type/category metadata
    keyed by target.local_id, plus supported amount, settlement and cash-state
    observations. Missing pieces block; no retry, date, income or amount is made up.
    actor_key must be a host-verified identity, never a model issuer-name guess.
    """
    source = result.source
    if source.user_id != user_id or not request_id:
        raise private.EvidenceError("batch_identity_mismatch")
    descriptors = event_descriptors(events, user_id=user_id)
    sources = _sources(result, actor_key)
    facts, issues = [], []
    targets = dict(resolved_targets or {})
    metadata = new_event_fields or {}

    def issue(code, fact=None, *, severity="blocking", candidate=None, detail=None):
        target = fact.subject.event_id if fact else source.related_event_id
        issues.append(core.EvidenceIssue(code.upper(), severity, detail or code.replace("_", " "),
                                        (source.source_id,), (target,) if target else (),
                                        "none" if severity == "info" else "unresolved_evidence",
                                        (candidate,) if candidate else ()))

    if result.extraction is None:
        for code in result.issues or ("evidence_unavailable",):
            issue(code, severity="info" if code == "future_evidence" else "blocking")
        return AdaptedEvidence(core.FactBatch(request_id, user_id, issues=tuple(issues)), sources)
    if result.outcome not in {"ok", "partial"} or not result.content_sha256:
        raise private.EvidenceError("unvalidated_extraction_result")
    # Reparse so a caller cannot mutate nested private lists after validation.
    extraction = private.parse_extraction(private.canonical(result.extraction.model_dump(mode="json")))
    private.validate_support(extraction, source_id=source.source_id, text=source.text, candidate_events=descriptors)
    if source.kind == "message" and hashlib.sha256(source.text.encode()).hexdigest() != result.content_sha256:
        raise private.EvidenceError("source_content_mismatch")
    local_ids = {fact.local_id for fact in extraction.facts}
    if not set(targets).issubset(local_ids):
        raise private.EvidenceError("unknown_target_binding")
    for observed in extraction.issues:
        # Do not copy arbitrary model issue text into diagnostics/logs.
        issue(observed.code, severity="info" if observed.code == "instruction_attempt" else "blocking")
    for code in result.issues:
        if code not in {i.code for i in extraction.issues}:
            issue(code, severity="warning" if code in {"usage_unknown", "cost_bound_exceeded"} else "blocking")

    def resolve(fact):
        target = targets.get(fact.local_id)
        if target is None and fact.subject.scope == "event":
            target = core.EventTarget(fact.subject.event_id)
        if isinstance(target, core.EventTarget):
            if target.event_id not in events:
                raise private.EvidenceError("invalid_target")
            if fact.subject.event_id and fact.subject.event_id != target.event_id:
                raise private.EvidenceError("conflicting_target_binding")
            if fact.subject.scope == "series" and fact.effect_window.scope != "next_occurrence":
                raise private.EvidenceError("series_requires_series_binding")
        elif isinstance(target, (core.SeriesTarget, core.NewOccurrenceTarget)):
            if target.user_id != user_id or fact.subject.scope == "event":
                raise private.EvidenceError("cross_user_target")
            if isinstance(target, core.SeriesTarget):
                if target.currency not in core.CURRENCIES or target.direction not in {"credit", "debit", "non_cash"} or not target.category:
                    raise private.EvidenceError("invalid_series_binding")
        elif target is not None:
            raise private.EvidenceError("invalid_target_binding")
        return target

    def emit(fact, target, claim, *, suffix="", evidence=None):
        if isinstance(target, core.SeriesTarget) and fact.effect_window.scope == "unspecified":
            raise private.EvidenceError("unspecified_series_scope")
        start, end = _bounds(fact)
        identity = core.canonical_hash((source.row_sha256, result.content_sha256, fact.model_dump(mode="json") | {"evidence": []},
                                        core.canonical_data(target), core.canonical_data(claim), suffix))
        # Private geometry floats do not enter financial canonical_data: actual
        # evidence locators are canonical strings, and are also identity-bound.
        refs = evidence or _evidence(source.source_id, fact)
        identity = core.canonical_hash((identity, refs))
        facts.append(core.Fact("extracted:" + identity, target, claim, effective_on=start, valid_until=end,
                               scope="series" if isinstance(target, core.SeriesTarget) and fact.effect_window.scope not in {"once", "next_occurrence"} else "occurrence",
                               evidence=refs, action=fact.operation,
                               certainty="supported" if fact.certainty == "explicit" else "ambiguous"))

    def money(fact, target):
        payload = fact.payload
        if payload.value is None or payload.currency is None:
            raise private.EvidenceError("unknown_money")
        currency = events[target.event_id].currency if isinstance(target, core.EventTarget) else getattr(target, "currency", payload.currency)
        direction = events[target.event_id].direction if isinstance(target, core.EventTarget) else getattr(target, "direction", payload.direction)
        if payload.currency != currency or payload.direction not in {direction, "unknown"}:
            raise private.EvidenceError("money_target_mismatch")
        if payload.role in {"net_payable", "balance_due"} and direction != "debit":
            raise private.EvidenceError("money_role_direction_mismatch")
        if payload.role in {"net_received", "refund_amount", "regular_salary", "one_off_arrears", "bonus", "commission"} and direction != "credit":
            raise private.EvidenceError("money_role_direction_mismatch")
        return parse_money(payload.value, payload.currency), direction

    diagnostics = {"gross", "subtotal", "deduction", "tax", "fee", "market_value", "amount_paid", "other", "unknown"}
    new_groups = {}
    for observed in extraction.facts:
        target = resolve(observed)
        payload = observed.payload
        candidate = (core.AmountCandidate(payload.role, payload.value, payload.currency)
                     if isinstance(payload, private.Amount) and payload.value is not None else None)
        if isinstance(payload, private.Amount) and payload.role in diagnostics:
            issue("non_cash_amount_diagnostic", observed, candidate=candidate, severity="info")
            continue
        if isinstance(payload, private.DateClaim) and payload.role != "settlement":
            issue("date_role_not_settlement", observed, severity="info" if payload.role in {"document", "printed", "period_start", "period_end"} else "blocking")
            continue
        if isinstance(payload, private.State) and payload.axis == "fulfillment":
            issue("fulfillment_not_cash", observed, severity="info")
            continue
        if target is None:
            issue("unresolved_target", observed, candidate=candidate)
            continue
        if isinstance(target, core.NewOccurrenceTarget):
            new_groups.setdefault(target.local_id, (target, []))[1].append(observed)
            continue
        try:
            if isinstance(payload, private.Amount):
                value, direction = money(observed, target)
                if direction not in {"credit", "debit"}:
                    raise private.EvidenceError("non_cash_target")
                if isinstance(target, core.SeriesTarget) and payload.role in {"one_off_arrears", "bonus", "commission", "refund_amount"}:
                    raise private.EvidenceError("one_off_needs_occurrence_binding")
                claim = core.SeriesAmountClaim(value) if isinstance(target, core.SeriesTarget) else core.AmountClaim(value, "payable" if direction == "debit" else "net_cash")
                emit(observed, target, claim)
                if isinstance(target, core.EventTarget) and payload.role in {"regular_salary", "one_off_arrears", "bonus", "commission", "refund_amount"}:
                    role = "regular_salary" if payload.role == "regular_salary" else "one_off"
                    emit(observed, target, core.IncomeRoleClaim(role), suffix="income-role")
            elif isinstance(payload, private.State):
                if payload.value == "unknown":
                    raise private.EvidenceError("unknown_state")
                if payload.axis == "cash":
                    claim = core.StateClaim(payload.value)
                else:
                    if not isinstance(target, core.EventTarget):
                        raise private.EvidenceError("state_needs_known_occurrence")
                    claim = (core.StateClaim(None, approval_state=payload.value) if payload.axis == "approval"
                             else core.StateClaim(None, obligation_state=payload.value))
                emit(observed, target, claim)
            elif isinstance(payload, private.DateClaim):
                if payload.value is None:
                    raise private.EvidenceError("unresolved_settlement_date")
                emit(observed, target, core.DateClaim(parse_day(payload.value)))
            elif isinstance(payload, private.RelativeChange):
                if not isinstance(target, core.SeriesTarget) or payload.measure != "percent":
                    raise private.EvidenceError("relative_change_needs_core_resolution")
                if payload.base_role not in {"regular_salary", "net_received", "net_payable", "balance_due"}:
                    raise private.EvidenceError("unsupported_series_scale_basis")
                if ((payload.base_role in {"net_payable", "balance_due"} and target.direction != "debit")
                        or (payload.base_role in {"regular_salary", "net_received"} and target.direction != "credit")):
                    raise private.EvidenceError("money_role_direction_mismatch")
                # Unit conversion only, not base-money arithmetic. Ambient
                # Decimal precision must not alter an evidenced percentage.
                with localcontext() as context:
                    context.prec = 64
                    ratio = Decimal(payload.value) / 100
                    multiplier = 1 + ratio if payload.change == "increase" else 1 - ratio
                if multiplier <= 0:
                    raise private.EvidenceError("unsupported_series_scale")
                emit(observed, target, core.SeriesScaleClaim(multiplier))
            elif isinstance(payload, private.Pattern):
                if payload.recurrence == "one_off" and isinstance(target, core.EventTarget):
                    emit(observed, target, core.IncomeRoleClaim("one_off"))
                elif payload.recurrence == "ended" and isinstance(target, core.SeriesTarget) and observed.operation in {"end", "cancel"}:
                    emit(observed, target, core.CancelClaim())
                else:
                    # A cadence label alone has no day/anchor/history support.
                    raise private.EvidenceError("recurrence_requires_core_support")
            elif isinstance(payload, private.Relation):
                kinds = {"internal_transfer": "internal_transfer_leg", "refund_of": "refund_of",
                         "duplicate_of": "same_cash_occurrence", "retry_of": "retry_of",
                         "separate_obligations": "independent_cash_leg"}
                if not isinstance(target, core.EventTarget) or payload.relation not in kinds or not payload.other_event_ids:
                    raise private.EvidenceError("unrepresentable_relation")
                for other in sorted(set(payload.other_event_ids)):
                    if other == target.event_id:
                        raise private.EvidenceError("self_relation")
                    emit(observed, target, core.RelationClaim(other, kinds[payload.relation]), suffix=other)
        except (private.EvidenceError, MoneyError) as error:
            issue(error.code, observed, candidate=candidate)

    for local_id, (target, group) in sorted(new_groups.items()):
        fields = metadata.get(local_id, {})
        amounts = [f for f in group if isinstance(f.payload, private.Amount)]
        dates = [f for f in group if isinstance(f.payload, private.DateClaim)]
        states = [f for f in group if isinstance(f.payload, private.State) and f.payload.axis == "cash"]
        if (set(fields) != {"event_type", "category"} or any(not isinstance(v, str) or not v for v in fields.values())
                or len(amounts) != 1 or len(dates) != 1 or len(states) != 1 or len(group) != 3
                or any(f.certainty != "explicit" for f in group)):
            issue("new_occurrence_incomplete", group[0])
            continue
        amount, day, state = amounts[0], dates[0], states[0]
        try:
            value, direction = money(amount, target)
            if direction not in {"credit", "debit"} or day.payload.value is None or state.payload.value == "unknown":
                raise private.EvidenceError("new_occurrence_incomplete")
            if any(_bounds(f) != (None, None) for f in group):
                raise private.EvidenceError("conditional_new_occurrence_requires_core_resolution")
            income = "regular_salary" if amount.payload.role == "regular_salary" else "one_off" if direction == "credit" else "not_applicable"
            claim = core.NewCashClaim(fields["event_type"], fields["category"], direction, value, value.currency,
                                      parse_day(day.payload.value), state.payload.value, income_kind=income)
            refs = tuple(ref for f in group for ref in _evidence(source.source_id, f))
            emit(amount, target, claim, suffix=local_id, evidence=refs)
        except (private.EvidenceError, MoneyError) as error:
            issue(error.code, amount)
    return AdaptedEvidence(core.FactBatch(request_id, user_id, tuple(sorted(facts, key=lambda f: f.fact_id)), tuple(issues)), sources)
