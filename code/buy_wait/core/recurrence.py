"""Bounded, provenance-bearing recurrence and conservative variable envelopes.

Calendar cycles and cash dates are distinct. Explicit complete bills replace
inference; partial variable spending consumes a budget, never doubles it.
"""
from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from dataclasses import replace
from datetime import timedelta

from buy_wait.contracts import (
    AmountClaim, CalendarMonth, CancelClaim, ChangeTarget, DateClaim, EventTarget,
    FixedDays, Money, Occurrence, RecurrenceClaim, RecurringSeries, SeriesAmountClaim,
    SeriesScaleClaim, SeriesTarget, StateClaim, canonical_hash,
)
from buy_wait.core.money import FxTable, MoneyError, scale_money
from buy_wait.core.normalize import applicable, issue, _choose

ESSENTIAL = frozenset(("groceries", "transport", "housing", "rent", "utilities", "healthcare",
                       "insurance", "education", "debt_repayment", "family_support"))
# A lone investment purchase does not recur, but three supported contribution
# cycles (or an explicit contract) are genuine debit commitments, not gains.
EXCLUDED_TYPES = frozenset(("refund", "investment_sale", "investment_valuation"))


def description_key(text):
    return " ".join(text.lower().split())


def month_next(day, cadence):
    month_index = day.year * 12 + day.month
    year, month0 = divmod(month_index, 12)
    month = month0 + 1
    last = monthrange(year, month)[1]
    return day.replace(year=year, month=month,
                       day=last if cadence.end_of_month else min(cadence.day, last))


def next_cycle(day, cadence):
    return month_next(day, cadence) if isinstance(cadence, CalendarMonth) else day + timedelta(days=cadence.days)


def detect_cadence(records, minimum):
    """At least three distinct, consecutive supported dates; no invented jitter."""
    days = sorted({r.event.settlement_date for r in records if r.event.settlement_date is not None})
    if len(days) < minimum:
        return None
    recent = days[-minimum:]
    monthly = all(b.year * 12 + b.month == a.year * 12 + a.month + 1 for a, b in zip(recent, recent[1:]))
    if monthly:
        eom = all(d.day == monthrange(d.year, d.month)[1] for d in recent)
        anchor_day = max(d.day for d in recent)
        if eom or all(d.day == min(anchor_day, monthrange(d.year, d.month)[1]) for d in recent):
            return CalendarMonth(anchor_day, eom)
    gaps = {(b - a).days for a, b in zip(recent, recent[1:])}
    if len(gaps) == 1 and next(iter(gaps)) in (7, 14, 21, 28):
        return FixedDays(next(iter(gaps)), recent[0])
    return None


def _category_key(record):
    e = record.event
    return e.category, e.direction, e.currency


def _may_fill_cycle(record, start):
    """A failed or pre-request attempt never stands in for a future debit cycle."""
    e = record.event
    if record.disposition != "excluded" or e.direction == "credit":
        return True
    return e.status == "cancelled" and e.settlement_date is not None and e.settlement_date >= start


def _selector_matches(target, records):
    if isinstance(target, EventTarget):
        return any(target.event_id in r.source_event_ids for r in records)
    if not isinstance(target, SeriesTarget):
        return False
    for r in records:
        e = r.event
        if (e.user_id, e.category, e.direction, e.currency) != (target.user_id, target.category, target.direction, target.currency):
            continue
        if target.description_key and description_key(e.description) != description_key(target.description_key):
            continue
        if target.actor_key and e.source.actor_key and target.actor_key != e.source.actor_key:
            continue
        return True
    return False


def _history_amount(records, count, direction):
    recent = sorted(records, key=lambda r: (r.event.settlement_date, r.event.event_id))[-count:]
    if not recent or any(r.event.amount is None for r in recent):
        return None
    return (max if direction == "debit" else min)((r.event.amount for r in recent), key=lambda a: a.minor)


def _days_for_series(records, cadence, start, end, series_facts):
    historical = [r.event.settlement_date for r in records if r.disposition == "anchored_history" and r.event.settlement_date]
    if historical:
        cursor = next_cycle(max(historical), cadence)
    else:
        explicit = [r.event.settlement_date for r in records if r.event.settlement_date and r.event.settlement_date >= start]
        starts = [f.effective_on for f in series_facts if isinstance(f.claim, RecurrenceClaim) and f.effective_on]
        if explicit or starts:
            cursor = min((*explicit, *starts))
        elif isinstance(cadence, FixedDays):
            cursor = cadence.anchor_date
        else:
            last = monthrange(start.year, start.month)[1]
            cursor = start.replace(day=last if cadence.end_of_month else min(cadence.day, last))
    # Do not reconstruct/replay old cash; retain nominal cadence while advancing.
    while cursor < start:
        cursor = next_cycle(cursor, cadence)
    result = []
    while cursor <= end:
        result.append(cursor)
        cursor = next_cycle(cursor, cadence)
    return result


def _amount_for_cycle(base, records, facts, nominal, first_cycle, sources, issues, series_id):
    scoped = [f for f in facts if f.scope == "series" or nominal == first_cycle]
    synthetic = replace(records[-1].event, amount=base, status="scheduled", settlement_date=nominal)
    dates = [f for f in scoped if isinstance(f.claim, DateClaim) and f.scope == "occurrence" and applicable(f, nominal)]
    cash_day, date_ids = _choose(synthetic, dates, "settlement_date", sources)
    # Resolve a delayed payment date before selecting inclusive payable tiers.
    active = [f for f in scoped if applicable(f, cash_day)]
    if any(isinstance(f.claim, CancelClaim) or f.action in ("cancel", "end") for f in active):
        return None, cash_day, tuple(f.fact_id for f in active), True
    cash_states = [f.claim for f in active if isinstance(f.claim, StateClaim)]
    if any(c.obligation_state in ("outstanding", "disputed") and c.value in ("failed", "cancelled") for c in cash_states):
        issues.append(issue("OUTSTANDING_OBLIGATION_UNSCHEDULED", "failed series attempt does not establish a new retry date", target=(series_id,)))
    if any(c.value in ("failed", "cancelled", "unrealized") or
           (records[0].event.direction == "credit" and (c.value == "pending" or c.approval_state in ("unconfirmed", "conditional")))
           for c in cash_states):
        return None, cash_day, tuple(f.fact_id for f in active), True
    if any(isinstance(f.claim, DateClaim) and f.scope == "series" for f in active):
        issues.append(issue("UNSUPPORTED_SERIES_DATE_SHIFT", "a permanent payday change needs an explicit cadence; no date drift inferred", target=(series_id,), impact="recurrence"))
    amounts = []
    for f in active:
        if isinstance(f.claim, (SeriesAmountClaim, AmountClaim)):
            if isinstance(f.claim, AmountClaim) and f.claim.role != ("payable" if records[0].event.direction == "debit" else "net_cash"):
                continue
            amounts.append(replace(f, claim=AmountClaim(f.claim.value, "payable" if records[0].event.direction == "debit" else "net_cash")))
        elif isinstance(f.claim, RecurrenceClaim) and f.claim.amount is not None:
            amounts.append(replace(f, claim=AmountClaim(f.claim.amount, "payable" if records[0].event.direction == "debit" else "net_cash")))
        elif isinstance(f.claim, SeriesScaleClaim):
            before = [r for r in records if r.disposition == "anchored_history"
                      and (f.effective_on is None or r.event.settlement_date < f.effective_on)]
            basis = _history_amount(before, 1, records[0].event.direction)
            if basis is None:
                issues.append(issue("UNKNOWN_PERCENT_BASE", "percentage amendment lacks a pre-amendment cash basis",
                                    target=(series_id, f.fact_id), impact="recurrence"))
                continue
            amount = scale_money(basis, f.claim.multiplier, direction=records[0].event.direction)
            amounts.append(replace(f, claim=AmountClaim(amount, "payable" if records[0].event.direction == "debit" else "net_cash")))
    if any(f.claim.value.currency != synthetic.currency for f in amounts):
        issues.append(issue("SERIES_CURRENCY_MISMATCH", "series amendment currency differs from contractual cash", target=(series_id,)))
        return None, nominal, (), False
    # Accepted contractual terms replace an inferred estimate. Competing explicit
    # cash rows are reconciled separately below, rather than overwritten blindly.
    amount, ids = _choose(replace(synthetic, amount=None) if amounts else synthetic,
                          amounts, "amount", sources)
    return amount, cash_day, tuple(sorted((*ids, *date_ids))), False


def infer_occurrences(data, resolved, facts, anchor, explicit, policy):
    if policy.projection_mode == "explicit_only":
        return (), tuple(explicit), (), (issue("EXPLICIT_ONLY_NOT_FULL_PROOF", "development-only projection excludes inferred commitments", impact="recurrence"),)
    start, end = data.request_date, data.request_date + timedelta(days=policy.horizon_days)
    cutoff = start - timedelta(days=policy.history_days)
    issues = []
    sources = {s.source_id: s for s in (*data.sources, *(r.event.source for r in resolved))}
    try:
        fx = FxTable(data.fx_rates)
    except MoneyError:
        # Explicit projection already reports invalid rate keys; do not add guessed flows.
        return (), tuple(explicit), (), ()
    transfers = {f.target.event_id for f in facts if isinstance(f.target, EventTarget)
                 and getattr(f.claim, "relation", None) == "internal_transfer_leg"}
    transfers.update(f.claim.other_event_id for f in facts if getattr(f.claim, "relation", None) == "internal_transfer_leg")
    history = [r for r in resolved if r.disposition == "anchored_history"
               and r.event.settlement_date is not None and cutoff <= r.event.settlement_date <= start
               and r.event.event_type not in EXCLUDED_TYPES and r.event.direction != "non_cash"
               and not set(r.source_event_ids).intersection(transfers)
               and (r.event.direction == "debit" or r.income_role == "regular_salary")]
    history.sort(key=lambda r: (r.event.settlement_date, r.event.event_id))
    series_facts = [f for f in facts if f.scope == "series" or isinstance(f.target, SeriesTarget)]
    # Resolve supersession in series contracts before scheduling so newer instructions dominate.
    all_series_ids = {f.fact_id for f in series_facts}
    superseded_series = {superseded
                        for fact in series_facts
                        for superseded in fact.supersedes_fact_ids
                        if superseded in all_series_ids}
    if superseded_series:
        series_facts = [f for f in series_facts if f.fact_id not in superseded_series]
    groups = []
    assigned = set()
    descriptive = defaultdict(list)
    for record in history:
        e = record.event
        descriptive[(*_category_key(record), description_key(e.description), e.source.actor_key)].append(record)
    for key, members in sorted(descriptive.items(), key=lambda item: repr(item[0])):
        minimum = policy.income_min_occurrences if key[1] == "credit" else policy.periodic_min_occurrences
        cadence = detect_cadence(members, minimum)
        if cadence:
            groups.append({"records": members, "cadence": cadence, "category_stream": False, "facts": []})
            assigned.update(r.canonical_cash_id for r in members)
    categories = defaultdict(list)
    already = {_category_key(g["records"][0]) for g in groups}
    for record in history:
        if record.canonical_cash_id not in assigned and record.event.direction == "debit" and _category_key(record) not in already:
            e = record.event
            categories[(*_category_key(record), e.flexibility, e.minimum_allowed_amount)].append(record)
    for key, members in sorted(categories.items(), key=lambda item: repr(item[0])):
        cadence = detect_cadence(members, policy.periodic_min_occurrences)
        if cadence and len({r.event.settlement_date for r in members}) == len(members):
            groups.append({"records": members, "cadence": cadence, "category_stream": True, "facts": []})
            assigned.update(r.canonical_cash_id for r in members)

    # An evidenced contract can establish cadence with fewer historical observations.
    for f in series_facts:
        if not isinstance(f.claim, RecurrenceClaim):
            continue
        matches = [g for g in groups if _selector_matches(f.target, g["records"])]
        if not matches:
            candidates = [r for r in resolved if _selector_matches(f.target, (r,))
                          and r.event.direction != "non_cash" and r.event.event_type not in EXCLUDED_TYPES]
            if not candidates:
                issues.append(issue("UNKNOWN_SERIES_TARGET", "recurrence contract has no identifiable supplied/new cash context", target=(f.fact_id,), impact="recurrence"))
            elif len({_category_key(r) for r in candidates}) != 1:
                issues.append(issue("AMBIGUOUS_SERIES_TARGET", "recurrence contract spans different cash streams", target=(f.fact_id,), impact="recurrence"))
            else:
                candidates.sort(key=lambda r: (r.event.settlement_date or start, r.event.event_id))
                groups.append({"records": candidates, "cadence": f.claim.cadence, "category_stream": False, "facts": []})
                assigned.update(r.canonical_cash_id for r in candidates)
    for f in series_facts:
        matches = [g for g in groups if _selector_matches(f.target, g["records"])]
        if len(matches) == 1:
            matches[0]["facts"].append(f)
        else:
            issues.append(issue("AMBIGUOUS_SERIES_TARGET" if matches else "UNSUPPORTED_SERIES_TARGET",
                                "series observation must resolve to one supported stream", target=(f.fact_id,), impact="recurrence"))
    for group in groups:
        explicit_cadences = [f.claim.cadence for f in group["facts"] if isinstance(f.claim, RecurrenceClaim)]
        if explicit_cadences:
            if len(set(explicit_cadences)) != 1:
                issues.append(issue("CONFLICTING_CADENCE", "multiple recurring cadences lack a unique resolution", impact="recurrence"))
            else:
                group["cadence"] = explicit_cadences[0]

    occurrences = {o.occurrence_id: o for o in explicit}
    original = {e.event_id: e for e in data.events}
    grouped_explicit = defaultdict(list)
    for record in resolved:
        if record.disposition == "anchored_history" or not _may_fill_cycle(record, start):
            continue
        stream = [i for i, g in enumerate(groups) if _category_key(record) == _category_key(g["records"][0])]
        exact = [i for i in stream if groups[i]["category_stream"] or any(
            description_key(record.event.description) == description_key(r.event.description) for r in groups[i]["records"])]
        matches = exact or stream
        if len(matches) == 1:
            grouped_explicit[matches[0]].append((record, bool(exact)))
        elif len(matches) > 1 and record.disposition in ("reserve", "future_cash"):
            # An ambiguous debit remains as an explicit reserve while every
            # supported recurrence remains projected.  That may over-reserve,
            # but is a usable conservative bound; an ambiguous credit must not
            # be counted twice or used to certify capacity.
            issues.append(issue("AMBIGUOUS_EXPLICIT_CYCLE", "future cash cannot be assigned uniquely to recurring series",
                                target=record.source_event_ids, impact="recurrence",
                                severity="warning" if record.event.direction == "debit" else "blocking"))
    result_series = []
    changes = []
    covered_history = set(assigned)
    for index, group in enumerate(groups):
        records = group["records"]
        first = records[0].event
        latest = max(records, key=lambda r: (r.event.settlement_date or start, r.event.event_id)).event
        cadence = group["cadence"]
        sfacts = sorted(group["facts"], key=lambda f: f.fact_id)
        support = tuple(sorted({eid for r in records for eid in r.source_event_ids}))
        series_id = "series:" + canonical_hash((data.user_id, first.category, first.direction, first.currency, support))[:20]
        historical = [r for r in records if r.disposition == "anchored_history"
                      and (first.direction == "debit" or r.income_role == "regular_salary")]
        base = _history_amount(historical, policy.recent_amount_occurrences, first.direction)
        if base is None:
            stated = [f.claim.amount for f in sfacts if isinstance(f.claim, RecurrenceClaim) and f.claim.amount is not None]
            stated.extend(f.claim.value for f in sfacts if isinstance(f.claim, SeriesAmountClaim))
            if stated:
                base = (max if first.direction == "debit" else min)(stated, key=lambda m: m.minor)
        if base is None:
            issues.append(issue("MISSING_HISTORY_AMOUNT", "supported recurrence amount has a material unresolved source",
                                target=support, severity="blocking" if first.direction == "debit" else "warning", impact="recurrence"))
        days = _days_for_series(records, cadence, start, end, sfacts)
        starts = [f.effective_on for f in sfacts if f.action == "start" and f.effective_on is not None]
        active_start = max(start, min(starts)) if starts else start
        days = [day for day in days if day >= active_start]
        if historical:
            next_expected = next_cycle(max(r.event.settlement_date for r in historical), cadence)
            stale = next_cycle(next_expected, cadence) < start
            continuing = any(isinstance(f.claim, RecurrenceClaim) or f.action in ("confirm", "start", "amend") for f in sfacts)
            if stale and not continuing:
                issues.append(issue("STALE_RECURRENCE", "a missed cycle needs continuation evidence, not historical cash reconstruction",
                                    target=support, severity="blocking" if first.direction == "debit" else "warning", impact="recurrence"))
                days = []
        estimator = ("periodic_category_max" if group["category_stream"] else "regular_salary_min" if first.direction == "credit" else "periodic_expense_max")
        series = RecurringSeries(series_id, data.user_id, first.category, first.direction, first.currency,
                                 cadence, estimator, support, "explicit contract" if any(isinstance(f.claim, RecurrenceClaim) for f in sfacts) else "three consecutive supported cycles",
                                 active_start, base, flexibility=latest.flexibility, floor=latest.minimum_allowed_amount)
        result_series.append(series)
        assignments = defaultdict(list)
        for record, identified in grouped_explicit[index]:
            e = record.event
            replaces = identified or first.direction == "credit"
            old = next((original[x] for x in record.source_event_ids if x in original), None)
            match_day = old.settlement_date if old and old.settlement_date else e.settlement_date
            if match_day is None or not days:
                continue
            distances = sorted(((abs((d - match_day).days), d) for d in days))
            distance, nominal = distances[0]
            gap_days = (next_cycle(nominal, cadence) - nominal).days
            if 2 * distance < gap_days and (len(distances) == 1 or distances[1][0] != distance):
                if replaces:
                    assignments[nominal].append(record)
                if not identified:
                    issues.append(issue("AMBIGUOUS_EXPLICIT_CYCLE", "same-category cash lacks the series description; cycle identity is unproven",
                                        target=record.source_event_ids, severity="warning", impact="recurrence"))
            elif replaces and e.settlement_date and start <= e.settlement_date <= end and record.disposition in ("reserve", "future_cash"):
                issues.append(issue("UNMATCHED_EXPLICIT_CYCLE", "explicit cash may overlap inference but its nominal cycle is not identified", target=record.source_event_ids, impact="recurrence",
                                    severity="warning" if record.event.direction == "debit" else "blocking"))
        # Distinct resolved cash legs on the same historical cycle are distinct
        # obligations.  Normalization has already merged only supported duplicate
        # representations, so grouping by description must not collapse the
        # remaining same-day legs into one future estimate.
        historical_multiplicity = max(
            (sum(1 for r in records if r.event.settlement_date == day and r.event.amount is not None)
             for day in {r.event.settlement_date for r in records if r.event.settlement_date is not None}),
            default=1,
        )
        editable_ids = []
        for nominal in days:
            amount, cash_day, fact_ids, cancelled = _amount_for_cycle(base, records, sfacts, nominal, days[0], sources, issues, series_id)
            replacements = assignments[nominal]
            if not group["category_stream"] and len(replacements) > 1:
                issues.append(issue("MULTIPLE_EXPLICIT_CYCLE", "multiple distinct cash occurrences claim one contractual cycle", target=tuple(r.event.event_id for r in replacements), impact="recurrence"))
            if replacements and not group["category_stream"]:
                # Even delayed/cancelled/unavailable explicit occurrences suppress matching inference.
                for r in replacements:
                    oid = "cash:" + r.canonical_cash_id
                    if oid not in occurrences:
                        continue
                    old_occurrence = occurrences[oid]
                    replacement_amount = old_occurrence.native_amount
                    # Apply series amendments relative to historical basis, never scale the already-raised row.
                    if fact_ids and amount is not None:
                        corrections = [replace(f, claim=AmountClaim(amount, "payable" if first.direction == "debit" else "net_cash"))
                                       for f in sfacts if f.fact_id in fact_ids and not isinstance(f.claim, DateClaim)]
                        replacement_amount, _ = _choose(r.event, corrections, "amount", sources)
                    if cancelled:
                        if old_occurrence.pending_reservation_id:
                            issues.append(issue("COMMITTED_SERIES_CANCELLATION", "ending a series does not prove an existing held charge is released", target=r.source_event_ids))
                        else:
                            occurrences.pop(oid, None)
                        continue
                    if replacement_amount is None:
                        continue
                    effective_day = cash_day if cash_day != nominal else old_occurrence.cash_date
                    try:
                        home_amount, fx_source = fx.convert(replacement_amount, data.profile.home_currency, effective_day, direction=first.direction)
                    except MoneyError as error:
                        issues.append(issue(error.code, str(error), target=r.source_event_ids, severity="blocking" if first.direction == "debit" else "warning", impact="conversion"))
                        if first.direction == "credit":
                            occurrences.pop(oid, None)
                        continue
                    occurrences[oid] = replace(old_occurrence, series_id=series_id, nominal_cycle_date=nominal,
                                               cash_date=effective_day, home_amount=home_amount,
                                               native_amount=replacement_amount, fx_source_id=fx_source,
                                               coverage="complete_cycle", fact_ids=tuple(sorted(set((*old_occurrence.fact_ids, *fact_ids)))))
                continue
            if cancelled or amount is None:
                continue
            if cash_day < start or cash_day > end:
                continue
            # `records` is historical support; each historical same-day leg
            # establishes one conservative future obligation of the series base.
            # Explicit future cash is handled through `replacements` above.
            for ordinal in range(historical_multiplicity):
                estimated = amount
                try:
                    home_amount, fx_source = fx.convert(estimated, data.profile.home_currency, cash_day, direction=first.direction)
                except MoneyError as error:
                    issues.append(issue(error.code, str(error), target=(series_id,), severity="blocking" if first.direction == "debit" else "warning", impact="conversion"))
                    continue
                covered = tuple(sorted("cash:" + r.canonical_cash_id for r in replacements if "cash:" + r.canonical_cash_id in occurrences))
                total = home_amount.minor if group["category_stream"] else None
                if covered:
                    for oid in covered:
                        occurrences[oid] = replace(occurrences[oid], series_id=series_id, nominal_cycle_date=nominal, coverage="partial_budget")
                    home_amount = Money(home_amount.currency, max(0, home_amount.minor - sum(occurrences[oid].home_amount.minor for oid in covered)))
                suffix = f":{ordinal}" if historical_multiplicity > 1 else ""
                oid = f"{series_id}:cycle:{nominal}{suffix}"
                editable = first.direction == "debit" and first.currency == data.profile.home_currency
                occurrences[oid] = Occurrence(oid, cash_day, home_amount, first.direction, series_id=series_id,
                                              nominal_cycle_date=nominal, origin="inferred_periodic",
                                              coverage="partial_budget" if group["category_stream"] else "complete_cycle",
                                              native_amount=estimated, fx_source_id=fx_source, source_event_ids=support,
                                              fact_ids=fact_ids, evidence_ids=tuple(sorted({r.event.source.source_id for r in records})),
                                              editable=editable, covered_occurrence_ids=covered, budget_total_minor=total)
                if editable:
                    editable_ids.append(oid)
        changes.extend(_targets(data, series, records, editable_ids))

    # Residual essential variable expenditure: observed-span max seven-day envelope.
    leftovers = defaultdict(list)
    essential = ESSENTIAL | data.profile.protected_categories
    for r in history:
        if r.canonical_cash_id not in covered_history and r.event.direction == "debit" and r.event.category in essential:
            leftovers[r.event.category].append(r)
    all_days = [r.event.settlement_date for r in history]
    observed_start = min(all_days) if all_days else start
    for category, members in sorted(leftovers.items()):
        bins = []
        included = []
        missing = False
        for week in range(policy.irregular_budget_weeks):
            high = start - timedelta(days=week * 7)
            low = high - timedelta(days=7)
            if low < observed_start:
                continue
            total = 0
            bucket = [r for r in members if low <= r.event.settlement_date < high]
            for r in bucket:
                included.append(r)
                if r.event.amount is None:
                    missing = True
                    continue
                try:
                    converted, _ = fx.convert(r.event.amount, data.profile.home_currency, r.event.settlement_date, direction="debit")
                    total += converted.minor
                except MoneyError:
                    missing = True
            bins.append((total, bool(bucket)))
        if len(bins) < policy.irregular_min_full_bins or sum(nonempty for _, nonempty in bins) < policy.irregular_min_nonempty_bins:
            material_pattern = len(members) >= policy.periodic_min_occurrences
            issues.append(issue("INSUFFICIENT_VARIABLE_HISTORY", "no supported cadence/envelope; do not invent expense recurrence",
                                target=tuple(r.event.event_id for r in members),
                                severity="blocking" if material_pattern else "info", impact="recurrence"))
            continue
        if missing:
            issues.append(issue("UNKNOWN_VARIABLE_BUDGET", "a material observed weekly envelope amount/FX is missing", target=tuple(r.event.event_id for r in included), impact="recurrence"))
            continue
        weekly = max(total for total, _ in bins)
        support = tuple(sorted({r.event.event_id for r in included}))
        sid = "budget:" + canonical_hash((data.user_id, category, support))[:20]
        result_series.append(RecurringSeries(sid, data.user_id, category, "debit", data.profile.home_currency,
                                             FixedDays(7, start), "observed_span_weekly_max", support,
                                             "max of complete observed-span bins; completeness not independently guaranteed",
                                             start, Money(data.profile.home_currency, weekly)))
        for offset in range(0, policy.horizon_days + 1, 7):
            day = start + timedelta(days=offset)
            # Only residual explicit variable spending not already assigned to another series can cover this bucket.
            covered = tuple(sorted(o.occurrence_id for o in occurrences.values() if o.origin == "explicit" and o.series_id is None
                                   and o.cash_date is not None and day <= o.cash_date < day + timedelta(days=7)
                                   and any(r.event.category == category and r.canonical_cash_id == o.canonical_cash_id for r in resolved)))
            for oid in covered:
                occurrences[oid] = replace(occurrences[oid], series_id=sid, nominal_cycle_date=day, coverage="partial_budget")
            remaining = max(0, weekly - sum(occurrences[oid].home_amount.minor for oid in covered))
            oid = f"{sid}:week:{day}"
            occurrences[oid] = Occurrence(oid, day, Money(data.profile.home_currency, remaining), "debit",
                                          series_id=sid, nominal_cycle_date=day, origin="inferred_budget", coverage="partial_budget",
                                          source_event_ids=support, evidence_ids=tuple(sorted({r.event.source.source_id for r in included})),
                                          covered_occurrence_ids=covered, budget_total_minor=weekly)
    return (tuple(sorted(result_series, key=lambda s: s.series_id)),
            tuple(sorted(occurrences.values(), key=lambda o: o.occurrence_id)),
            tuple(sorted(changes, key=lambda c: c.anchor_event_id)), tuple(issues))


def _targets(data, series, records, occurrence_ids):
    if series.category in data.profile.protected_categories or not occurrence_ids or series.direction != "debit":
        return ()
    allowed = []
    if (series.flexibility in ("stoppable", "reducible_or_stoppable")
            and series.category in data.profile.stoppable_categories):
        allowed.append("stop")
    if (series.flexibility in ("reducible", "reducible_or_stoppable") and series.floor is not None
            and series.floor.currency == data.profile.home_currency and series.category in data.profile.reducible_categories):
        allowed.append("reduce_to")
    if not allowed:
        return ()
    return tuple(ChangeTarget(eid, series.series_id, series.category, series.flexibility, series.floor,
                              tuple(allowed), tuple(occurrence_ids), "unique historical series alias; future uncommitted occurrences only")
                 for eid in sorted({eid for r in records for eid in r.source_event_ids if not eid.startswith("fact:")}))
