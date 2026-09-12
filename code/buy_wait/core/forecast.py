"""The single monetary simulator: cash/holds, dated checkpoints and plan replay."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from buy_wait.contracts import (
    BalanceAnchor, Checkpoint, CoreContext, Forecast, Money, Occurrence, PlanSafety,
    ReduceTo, Stop, canonical_hash,
)
from buy_wait.core.money import FxTable, MoneyError
from buy_wait.core.normalize import issue


def explicit_occurrences(data, resolved, policy, inherited_issues=()):
    end = data.request_date + timedelta(days=policy.horizon_days)
    issues = list(inherited_issues)
    try:
        fx = FxTable(data.fx_rates)
    except MoneyError as error:
        issues.append(issue(error.code, str(error), impact="conversion"))
        fx = None
    occurrences = []
    for record in resolved:
        event = record.event
        if record.disposition not in ("reserve", "future_cash"):
            continue
        day = event.settlement_date
        pending = record.disposition == "reserve"
        if not pending and day is not None and day > end:
            continue
        if event.amount is None:
            issues.append(issue("MISSING_AMOUNT", "cash amount remains unknown, never zero", target=record.source_event_ids,
                                severity="blocking" if event.direction == "debit" else "warning", impact=event.direction))
            continue
        if day is None and not pending:
            issues.append(issue("MISSING_SETTLEMENT_DATE", "scheduled cash has no supported date", target=record.source_event_ids,
                                severity="blocking" if event.direction == "debit" else "warning", impact=event.direction))
            continue
        if day is not None and day < data.request_date and not pending:
            issues.append(issue("OVERDUE_SCHEDULE", "past scheduled cash is not proven settled; timing needs reconciliation",
                                target=record.source_event_ids, severity="blocking" if event.direction == "debit" else "warning", impact=event.direction))
            continue
        try:
            if event.currency == data.profile.home_currency:
                amount, fx_source = event.amount, None
            elif fx is None:
                continue
            else:
                amount, fx_source = fx.convert(event.amount, data.profile.home_currency, day, direction=event.direction)
        except MoneyError as error:
            issues.append(issue(error.code, str(error), target=record.source_event_ids,
                                severity="blocking" if event.direction == "debit" else "warning", impact="conversion"))
            continue
        occurrences.append(Occurrence(
            "cash:" + record.canonical_cash_id, day, amount, event.direction,
            canonical_cash_id=record.canonical_cash_id, native_amount=event.amount,
            fx_source_id=fx_source, pending_reservation_id=record.canonical_cash_id if pending else None,
            source_event_ids=record.source_event_ids, fact_ids=record.fact_ids,
            evidence_ids=record.evidence_ids,
        ))
    aliases = {alias: o.pending_reservation_id for o in occurrences if o.pending_reservation_id
               for alias in (*o.source_event_ids, o.pending_reservation_id)}
    included = []
    for supplied in data.included_hold_ids:
        canonical = aliases.get(supplied)
        if canonical is None or canonical in included:
            raise ValueError("included hold must uniquely identify an active pending debit")
        included.append(canonical)
    held_amount = sum(o.home_amount.minor for o in occurrences if o.pending_reservation_id in included)
    anchor = BalanceAnchor(data.request_date, data.profile.available,
                           data.profile.available.minor + held_amount, held_amount,
                           data.profile.minimum.minor, tuple(sorted(included)))
    return anchor, tuple(occurrences), tuple(issues)


def project(anchor, occurrences, policy, *, issues=(), payments=()):
    """Replay every operation, including the prefix before controllable payments.

    Pending settlement is atomic: (-cash, -hold) does not change availability.
    A pending occurrence with no settlement date reserves throughout the horizon.
    """
    start = anchor.date
    end = start + timedelta(days=policy.horizon_days)
    home = anchor.supplied_available.currency
    occurrences = tuple(occurrences)
    ids = [o.occurrence_id for o in occurrences]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate occurrence IDs")
    if any(o.home_amount.currency != home or o.direction not in ("debit", "credit") for o in occurrences):
        raise ValueError("occurrence currency/direction mismatch")
    holds = {}
    for o in occurrences:
        if o.pending_reservation_id:
            if o.direction != "debit" or o.pending_reservation_id in holds:
                raise ValueError("pending hold must identify one debit exactly once")
            holds[o.pending_reservation_id] = o.home_amount.minor
    if any(x not in holds for x in anchor.included_hold_ids):
        raise ValueError("anchor references an unknown hold")
    if sum(holds[x] for x in anchor.included_hold_ids) != anchor.holds_already_included_minor:
        raise ValueError("anchor hold inclusion amount mismatch")
    if anchor.gross_cash_minor - anchor.holds_already_included_minor != anchor.supplied_available.minor:
        raise ValueError("anchor available cash mismatch")
    payment_ids = set()
    previous = start
    for payment in payments:
        if not start <= payment.date <= end or payment.date < previous:
            raise ValueError("payment dates must be chronological inside original horizon")
        if payment.amount.currency != home or payment.amount.minor < 0:
            raise ValueError("payments must be nonnegative home-currency amounts")
        if payment.payment_id in payment_ids:
            raise ValueError("duplicate payment ID")
        payment_ids.add(payment.payment_id)
        previous = payment.date
    cash, reserved = anchor.gross_cash_minor, anchor.holds_already_included_minor
    checkpoints = []
    active_holds = set(anchor.included_hold_ids)

    def record(day, phase, operation, cash_delta=0, hold_delta=0, provenance=()):
        nonlocal cash, reserved
        cash += cash_delta
        reserved += hold_delta
        if reserved < 0:
            raise ValueError("hold released more than once")
        checkpoints.append(Checkpoint(f"{day}:{phase}:{operation}", day, phase, operation,
                                      cash_delta, hold_delta, cash, reserved, cash - reserved, tuple(provenance)))
    record(start, "anchor", "profile")
    for o in sorted(occurrences, key=lambda x: x.occurrence_id):
        hold_id = o.pending_reservation_id
        if hold_id and hold_id not in active_holds:
            record(start, "reservation", hold_id, hold_delta=o.home_amount.minor, provenance=o.evidence_ids)
            active_holds.add(hold_id)
    by_day = {}
    for o in occurrences:
        if o.cash_date is not None and start <= o.cash_date <= end:
            by_day.setdefault(o.cash_date, []).append(o)
    for offset in range(policy.horizon_days + 1):
        day = start + timedelta(days=offset)
        for o in sorted(by_day.get(day, ()), key=lambda x: (x.direction != "debit", x.occurrence_id)):
            amount = o.home_amount.minor
            if o.direction == "debit":
                release = 0
                if o.pending_reservation_id:
                    if o.pending_reservation_id not in active_holds:
                        raise ValueError("settlement has no active reservation")
                    active_holds.remove(o.pending_reservation_id)
                    release = -amount
                record(day, "debit", o.occurrence_id, -amount, release, o.evidence_ids)
            else:
                record(day, "credit", o.occurrence_id, amount, provenance=o.evidence_ids)
        for payment in payments:
            if payment.date == day:
                record(day, "payment", payment.payment_id, -payment.amount.minor)
        record(day, "close", "day")
    unresolved = any(i.severity == "blocking" for i in issues)
    bound = any(i.severity == "warning" for i in issues)
    minimum = min(c.available_minor for c in checkpoints)
    binding = tuple(c.checkpoint_id for c in checkpoints if c.available_minor == minimum)
    return Forecast(start, end, home, tuple(checkpoints),
                    None if unresolved else minimum >= anchor.minimum_minor,
                    None if unresolved else minimum, binding,
                    "unresolved" if unresolved else "conservative_bound" if bound else "resolved_under_policy",
                    tuple(issues))


def context_fingerprint(core: CoreContext):
    """Content identity excluding only the self-referential hash fields."""
    return canonical_hash(replace(core, context_hash="", capacity=replace(core.capacity, context_hash="")))


def _changed_occurrences(core, changes):
    if len(changes) > 3:
        raise ValueError("at most three changes are allowed")
    targets = {t.anchor_event_id: t for t in core.change_targets}
    by_id = {o.occurrence_id: o for o in core.occurrences}
    original = dict(by_id)
    used_series = set()
    used_occurrences = set()
    for change in changes:
        if not isinstance(change, (Stop, ReduceTo)):
            raise ValueError("only Stop/ReduceTo handles may control changes")
        target = targets.get(change.anchor_event_id)
        if target is None or target.series_id in used_series:
            raise ValueError("unknown/protected change target or duplicate series alias")
        mode = "stop" if isinstance(change, Stop) else "reduce_to"
        if mode not in target.allowed_actions:
            raise ValueError("action not allowed for this target")
        used_series.add(target.series_id)
        if isinstance(change, ReduceTo):
            if change.new_amount.currency != core.requested.currency or target.floor is None:
                raise ValueError("reduction needs home currency and an evidenced floor")
            if target.floor.currency != change.new_amount.currency or change.new_amount.minor < target.floor.minor:
                raise ValueError("reduction violates currency/floor")
        for oid in target.eligible_occurrence_ids:
            if oid in used_occurrences or oid not in by_id:
                raise ValueError("overlapping or unknown occurrence effects")
            used_occurrences.add(oid)
            old = by_id[oid]
            if not old.editable or old.direction != "debit" or old.pending_reservation_id:
                raise ValueError("committed/noneditable occurrence cannot be changed")
            if old.series_id != target.series_id:
                raise ValueError("change target cannot widen to another series")
            new = 0 if isinstance(change, Stop) else change.new_amount.minor
            ceiling = old.budget_total_minor if old.budget_total_minor is not None else old.home_amount.minor
            if new > ceiling:
                raise ValueError("reduce_to cannot increase expense")
            if old.budget_total_minor is not None:
                covered = sum(by_id[x].home_amount.minor for x in old.covered_occurrence_ids)
                residual = max(0, new - covered)
                by_id[oid] = replace(old, home_amount=Money(old.home_amount.currency, residual), budget_total_minor=new)
            else:
                by_id[oid] = replace(old, home_amount=Money(old.home_amount.currency, new))
    deltas = tuple((oid, by_id[oid].home_amount.minor - old.home_amount.minor)
                   for oid, old in sorted(original.items()) if by_id[oid] != old)
    return tuple(by_id[o.occurrence_id] for o in core.occurrences), deltas


def replay_financial_plan(core: CoreContext, payments, *, changes=()):
    if core.context_hash != context_fingerprint(core) or core.capacity.context_hash != core.context_hash:
        return PlanSafety(False, "unresolved", None, None, ("STALE_CORE_CONTEXT",), None, core.context_hash)
    try:
        occurrences, deltas = _changed_occurrences(core, tuple(changes))
        forecast = project(core.anchor, occurrences, core.policy, issues=core.issues, payments=tuple(payments))
    except (ValueError, KeyError) as error:
        return PlanSafety(False, "unresolved", None, None, ("INVALID_FINANCIAL_PLAN", str(error)), None, core.context_hash)
    violations = [c for c in forecast.checkpoints if c.available_minor < core.anchor.minimum_minor]
    minimum = forecast.minimum_available_minor
    # A negative balance is retained exactly in Forecast checkpoints (Money is a magnitude).
    as_money = Money(core.requested.currency, minimum) if minimum is not None and minimum >= 0 else None
    reasons = (("UNRESOLVED_BASELINE",) if forecast.proof_status == "unresolved"
               else ("MINIMUM_BALANCE_BREACH",) if violations else ())
    return PlanSafety(not reasons, forecast.proof_status, as_money,
                      violations[0].checkpoint_id if violations else None, reasons, forecast,
                      core.context_hash, deltas)
