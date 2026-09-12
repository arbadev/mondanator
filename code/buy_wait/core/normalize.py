"""Finite cash-state/fact reconciliation, not historical balance reconstruction."""
from __future__ import annotations

from dataclasses import replace
from datetime import timezone
from typing import Tuple

from buy_wait.contracts import (
    AmountClaim, CancelClaim, DateClaim, EventRecord, EventTarget, EvidenceIssue,
    Fact, FactBatch, FinancialInput, IncomeRoleClaim, NewCashClaim,
    NewOccurrenceTarget, RelationClaim, ResolvedEvent, SeriesTarget, StateClaim,
)

EXPLICIT_ACTIONS = frozenset(("amend", "cancel", "settle", "delay", "start", "end"))


def issue(code, detail, *, target=(), source=(), severity="blocking", impact="debit"):
    return EvidenceIssue(code, severity, detail, tuple(source), tuple(target), impact)


def applicable(fact: Fact, day) -> bool:
    return ((fact.effective_on is None or fact.effective_on <= day)
            and (fact.valid_until is None or day <= fact.valid_until))


def _actor_time(fact, sources):
    refs = [sources[e.source_id] for e in fact.evidence if e.source_id in sources]
    actors = {s.actor_key for s in refs}
    actor = next(iter(actors)) if len(actors) == 1 and None not in actors else None
    times = [s.known_at for s in refs if s.known_at is not None]
    return actor, max(times) if times else None


def _choose(event, candidates, field, sources):
    """Return selected value + provenance; unknown source != source_type."""
    if not candidates:
        return getattr(event, field), ()
    explicit = [f for f in candidates if f.action in EXPLICIT_ACTIONS or f.supersedes_fact_ids]
    chosen = explicit or candidates
    # Actual supersession is a host-resolved ID relation, not a model guess.
    superseded = {x for f in chosen for x in f.supersedes_fact_ids}
    chosen = [f for f in chosen if f.fact_id not in superseded]
    if not chosen:
        raise ValueError("cyclic fact supersession")
    actor_times = [_actor_time(f, sources) for f in chosen]
    actors = {a for a, _ in actor_times}
    if len(actors) == 1 and None not in actors and all(t is not None for _, t in actor_times):
        latest = max(t for _, t in actor_times)
        chosen = [f for f, (_, t) in zip(chosen, actor_times) if t == latest]
    base = getattr(event, field)
    newer_same_source = (len(actors) == 1 and event.source.actor_key in actors
                         and event.source.actor_key is not None and event.source.known_at is not None
                         and all(t is not None and t > event.source.known_at for _, t in actor_times))
    # Same-source recency precedes settled-over-estimate; unknown identity cannot win it.
    if not explicit and not newer_same_source and event.status == "settled" and base is not None:
        return base, ()
    values = [(f.claim.value if not isinstance(f.claim, CancelClaim) else "cancelled", f) for f in chosen]
    if not explicit and not newer_same_source and base is not None:
        values.append((base, None))
    if field == "amount":
        values.sort(key=lambda pair: (pair[0].minor, pair[1].fact_id if pair[1] else ""))
        result = values[-1 if event.direction == "debit" else 0][0]
    elif field == "settlement_date":
        result = (min if event.direction == "debit" else max)(v for v, _ in values)
    elif field == "status":
        # Unsettled credit stays unavailable; a debit remains reserved/owed if unclear.
        order = ({"cancelled": 0, "failed": 1, "unrealized": 2, "pending": 3, "scheduled": 4, "settled": 5}
                 if event.direction == "credit" else
                 {"pending": 0, "scheduled": 1, "settled": 2, "failed": 3, "cancelled": 4, "unrealized": 5})
        result = min((v for v, _ in values), key=lambda value: order[value])
    else:
        result = sorted(values, key=lambda pair: pair[1].fact_id if pair[1] else "")[0][0]
    ids = tuple(sorted(f.fact_id for value, f in values if f is not None and value == result))
    return result, ids


def normalize_events(data: FinancialInput, batch: FactBatch):
    if (batch.request_id, batch.user_id) != (data.request_id, data.user_id):
        raise ValueError("FactBatch identity differs from FinancialInput")
    events = {}
    sources = {}
    issues = list(batch.issues)
    for source in (*data.sources, *(e.source for e in data.events)):
        if source.source_id in sources and sources[source.source_id] != source:
            raise ValueError("conflicting source identity")
        sources[source.source_id] = source
    for event in data.events:
        if event.user_id != data.user_id:
            raise ValueError("cross-user financial event")
        if event.event_id in events:
            if events[event.event_id] != event:
                raise ValueError("conflicting duplicate event ID")
            continue
        events[event.event_id] = event
    facts = {}
    usable = []
    uncertain = set()
    for fact in batch.facts:
        if fact.fact_id in facts:
            if facts[fact.fact_id] != fact:
                raise ValueError("conflicting duplicate fact ID")
            continue
        facts[fact.fact_id] = fact
        target = fact.target
        if isinstance(target, (SeriesTarget, NewOccurrenceTarget)) and target.user_id != data.user_id:
            raise ValueError("cross-user fact target")
        if not fact.evidence or any(e.source_id not in sources for e in fact.evidence):
            issues.append(issue("UNVERIFIABLE_FACT", "fact lacks a known source", target=(fact.fact_id,)))
            continue
        refs = [sources[e.source_id] for e in fact.evidence]
        if any(s.known_at and s.known_at.astimezone(timezone.utc).date() > data.request_date for s in refs):
            issues.append(issue("FUTURE_EVIDENCE_EXCLUDED", "source was not known by request day",
                                target=(fact.fact_id,), severity="info", impact="none"))
            continue
        if isinstance(target, EventTarget) and target.event_id not in events:
            issues.append(issue("UNKNOWN_EVENT_TARGET", "fact references an unsupplied event", target=(target.event_id,)))
            continue
        if fact.certainty != "supported":
            if isinstance(target, EventTarget):
                uncertain.add(target.event_id)
            issues.append(issue("UNRESOLVED_EVIDENCE", "ambiguous/unreadable observation not accepted as cash",
                                target=(fact.fact_id,), severity="warning", impact="recurrence"))
            continue
        if isinstance(target, NewOccurrenceTarget) and isinstance(fact.claim, NewCashClaim):
            c = fact.claim
            event_id = "fact:" + target.local_id
            if event_id in events:
                raise ValueError("duplicate new occurrence ID")
            source = refs[0]
            events[event_id] = EventRecord(event_id, data.user_id, c.event_type, "Evidence-defined occurrence",
                                          c.category, c.direction, c.amount, c.currency, data.request_date,
                                          c.settlement_date, c.state, source)
            fact = replace(fact, target=EventTarget(event_id))
        usable.append(fact)

    # Supersession IDs are a host-resolved relation, not arbitrary priority tokens.
    for fact in usable:
        visiting = set()
        def visit(fid):
            if fid in visiting:
                raise ValueError("cyclic fact supersession")
            visiting.add(fid)
            for previous in facts[fid].supersedes_fact_ids:
                if previous not in facts or facts[previous].target != facts[fid].target or facts[previous].scope != facts[fid].scope:
                    raise ValueError("supersession must identify an existing same-target/scope fact")
                visit(previous)
            visiting.remove(fid)
        visit(fact.fact_id)

    # Validate lifecycle links without treating entire linked components as one cash leg.
    for event in events.values():
        seen = {event.event_id}
        linked = event.linked_event_id
        while linked:
            if linked in seen:
                raise ValueError("cyclic financial lifecycle")
            if linked not in events:
                raise ValueError("missing linked financial event")
            seen.add(linked)
            linked = events[linked].linked_event_id

    result = []
    for event in sorted(events.values(), key=lambda e: e.event_id):
        matched = [f for f in usable if isinstance(f.target, EventTarget)
                   and f.target.event_id == event.event_id and f.scope == "occurrence"]
        dates = [f for f in matched if isinstance(f.claim, DateClaim)
                 and applicable(f, event.settlement_date or data.request_date)]
        settlement, date_ids = _choose(event, dates, "settlement_date", sources)
        event = replace(event, settlement_date=settlement)
        active = [f for f in matched if applicable(f, settlement or data.request_date)]
        monetary = [f for f in active if isinstance(f.claim, AmountClaim)
                    and f.claim.role == ("payable" if event.direction == "debit" else "net_cash")]
        if any(f.claim.value.currency != event.currency for f in monetary):
            issues.append(issue("FACT_CURRENCY_MISMATCH", "amount fact currency differs from event", target=(event.event_id,)))
            monetary = []
        amount, amount_ids = _choose(event, monetary, "amount", sources)
        if event.direction == "debit" and any(f.effective_on or f.valid_until for f in monetary):
            issues.append(issue("SETTLEMENT_APPLICABILITY_PROXY", "inclusive bill tiers resolved using supported settlement date; no earlier paid-date evidence",
                                target=(event.event_id,), severity="info", impact="none"))
        states = [f for f in active if isinstance(f.claim, (StateClaim, CancelClaim))]
        status, state_ids = _choose(event, states, "status", sources)
        event = replace(event, amount=amount, status=status)
        approval = "unknown"
        obligation = "unknown"
        metadata_facts = [f for f in states if isinstance(f.claim, StateClaim)]
        explicit_metadata = [f for f in metadata_facts if f.action in EXPLICIT_ACTIONS or f.supersedes_fact_ids]
        metadata_facts = explicit_metadata or metadata_facts
        metadata_times = [_actor_time(f, sources) for f in metadata_facts]
        if (metadata_times and len({a for a, _ in metadata_times}) == 1
                and all(a is not None and t is not None for a, t in metadata_times)):
            latest_time = max(t for _, t in metadata_times)
            metadata_facts = [f for f, (_, t) in zip(metadata_facts, metadata_times) if t == latest_time]
        state_observations = [f.claim for f in metadata_facts]
        # Outstanding/disputed obligation survives a failed cash attempt (I011 C3).
        if any(c.obligation_state in ("outstanding", "disputed") for c in state_observations):
            obligation = "outstanding" if any(c.obligation_state == "outstanding" for c in state_observations) else "disputed"
        elif any(c.obligation_state == "closed" for c in state_observations):
            obligation = "closed"
        approvals = {c.approval_state for c in state_observations}
        if "conditional" in approvals:
            approval = "conditional"
        elif "unconfirmed" in approvals:
            approval = "unconfirmed"
        elif "confirmed" in approvals:
            approval = "confirmed"
        role = "regular_salary" if event.event_type == "income" and event.category == "salary" else "not_applicable"
        lower = event.description.lower()
        if any(token in lower for token in ("bonus", "commission", "prize", "lottery", "one-off", "one time")):
            role = "one_off"
        elif "prorat" in lower:
            role = "prorated"
        new_cash = [f.claim for f in active if isinstance(f.claim, NewCashClaim)]
        if new_cash:
            role = new_cash[-1].income_kind
        roles = [f for f in active if isinstance(f.claim, IncomeRoleClaim)]
        if roles:
            role = sorted(roles, key=lambda f: (f.action in EXPLICIT_ACTIONS, f.fact_id))[-1].claim.value
        if event.direction == "non_cash" or status == "unrealized":
            disposition, reason = "excluded", "NON_CASH"
        elif status in ("failed", "cancelled"):
            disposition, reason = "excluded", "FAILED_OR_CANCELLED_CASH"
            if event.direction == "debit" and obligation in ("outstanding", "disputed"):
                issues.append(issue("OUTSTANDING_OBLIGATION_UNSCHEDULED", "failed/cancelled attempt does not close the evidenced bill; retry timing is unknown", target=(event.event_id,)))
        elif status == "settled" and settlement is not None and settlement <= data.request_date:
            disposition, reason = "anchored_history", "SETTLED_ALREADY_IN_SNAPSHOT"
        elif event.event_id in uncertain:
            disposition, reason = "unresolved", "UNRESOLVED_AMOUNT_EVIDENCE"
            issues.append(issue(reason, "material cash evidence remains unresolved", target=(event.event_id,),
                                severity="blocking" if event.direction == "debit" else "warning",
                                impact=event.direction))
        elif status == "settled":
            disposition, reason = "unresolved", "FUTURE_SETTLED_CONFLICT"
            issues.append(issue(reason, "future-dated settled cash is not request-time cash", target=(event.event_id,),
                                severity="blocking" if event.direction == "debit" else "warning", impact=event.direction))
        elif event.direction == "credit" and (status == "pending" or role != "regular_salary" or approval in ("conditional", "unconfirmed")):
            disposition, reason = "excluded", "UNSETTLED_CREDIT_EXCLUDED"
        elif event.direction == "debit" and status == "pending":
            disposition, reason = "reserve", "PENDING_DEBIT_RESERVED_ONCE"
        elif status == "scheduled":
            disposition, reason = "future_cash", "CONFIRMED_SALARY" if event.direction == "credit" else "SCHEDULED_DEBIT"
        else:
            disposition, reason = "unresolved", "UNSUPPORTED_CASH_STATE"
        ids = tuple(sorted(set((*date_ids, *amount_ids, *state_ids, *(f.fact_id for f in roles)))))
        evidence_ids = tuple(sorted({event.source.source_id, *(e.source_id for f in active for e in f.evidence)}))
        result.append(ResolvedEvent(event.event_id, (event.event_id,), event, disposition, reason, ids,
                                    evidence_ids, role, approval, obligation))

    # Only explicit same-cash relations or authorization -> actual posting merge.
    parents = {r.event.event_id: r.event.event_id for r in result}
    def root(key):
        while parents[key] != key:
            key = parents[key]
        return key
    relations = {}
    for fact in usable:
        if isinstance(fact.target, EventTarget) and isinstance(fact.claim, RelationClaim):
            a, b = fact.target.event_id, fact.claim.other_event_id
            if b not in events:
                issues.append(issue("UNKNOWN_RELATION_TARGET", "relation target missing", target=(a, b)))
                continue
            relations[frozenset((a, b))] = fact.claim.relation
    by_id = {r.event.event_id: r for r in result}
    for record in result:
        event = record.event
        candidates = set()
        if event.linked_event_id:
            candidates.add(event.linked_event_id)
        for pair, relation in relations.items():
            if event.event_id in pair and relation == "same_cash_occurrence":
                candidates.update(pair - {event.event_id})
        for other_id in candidates:
            other = by_id[other_id].event
            relation = relations.get(frozenset((event.event_id, other_id)))
            auto = (relation is None and event.status == "settled"
                    and other.status in ("pending", "cancelled")
                    and event.event_type == other.event_type == "expense"
                    and event.direction == other.direction == "debit")
            if relation == "same_cash_occurrence" or auto:
                if event.direction != other.direction or event.currency != other.currency:
                    raise ValueError("same-cash relation has contradictory currency/direction")
                a, b = sorted((root(event.event_id), root(other_id)))
                parents[b] = a
    groups = {}
    for record in result:
        groups.setdefault(root(record.event.event_id), []).append(record)
    merged = []
    for cash_id, group in sorted(groups.items()):
        preference = {"settled": 5, "pending": 4, "scheduled": 3, "cancelled": 2, "failed": 1, "unrealized": 0}
        selected = max(group, key=lambda r: (preference[r.event.status], r.event.settlement_date or data.request_date, r.event.event_id))
        merged.append(replace(selected, canonical_cash_id=cash_id,
                              source_event_ids=tuple(sorted(r.event.event_id for r in group)),
                              fact_ids=tuple(sorted({f for r in group for f in r.fact_ids})),
                              evidence_ids=tuple(sorted({s for r in group for s in r.evidence_ids}))))
    return tuple(merged), tuple(usable), tuple(issues)
