"""Independent metadata precedence through private extraction and real replay."""
from __future__ import annotations

import copy
import hashlib
from dataclasses import replace
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from buy_wait import contracts as c
from buy_wait.core import replay_financial_plan
from buy_wait.evidence.adapter import AdaptedEvidence, adapt_extraction, event_descriptors
from buy_wait.evidence.extractor import ExtractionResult
from buy_wait.evidence.retrieval import EvidenceIndex
from buy_wait.evidence.schema import canonical, parse_extraction
from test_evidence_adapter import R, adapt, build, event, raw_amount
from test_evidence_retrieval import message


OLD = "2026-01-01T12:00:00Z"
NEW = "2026-01-02T12:00:00Z"


def documents(events, records, *, numbers=None, actor_key="verified:provider"):
    """Each tuple is a real synthetic source: text, axis, value, action, sent_at."""
    numbers = tuple(range(1, len(records) + 1)) if numbers is None else numbers
    rows = [message(number, text=text, event="event_1", sent=sent)
            for number, (text, _, _, _, sent) in zip(numbers, records)]
    index = EvidenceIndex(messages=rows, images=[],
                          events=event_descriptors(events, user_id="u1"), requests={})
    sources = {source.source_id: source for source in index.retrieve(user_id="u1").sources}
    facts, refs, issues = [], [], []
    for number, (text, axis, value, action, _) in zip(numbers, records):
        raw = raw_amount(text)
        raw["source_id"] = f"message:message_{number}"
        raw["facts"][0].update(operation=action, payload=dict(kind="state", axis=axis, value=value))
        result = ExtractionResult(sources[raw["source_id"]], "ok", parse_extraction(canonical(raw)),
                                  (), f"fixture-{number}", (),
                                  content_sha256=hashlib.sha256(text.encode()).hexdigest())
        adapted = adapt_extraction(result, request_id="r1", user_id="u1", events=events,
                                   actor_key=actor_key)
        facts.extend(adapted.batch.facts)
        refs.extend(adapted.sources)
        issues.extend(adapted.batch.issues)
    return AdaptedEvidence(c.FactBatch("r1", "u1", tuple(facts), tuple(issues)), tuple(refs))


class MetadataPrecedenceTests(unittest.TestCase):
    def assert_outstanding_control(self, actual, control):
        self.assertEqual(control.capacity.proof_status, "unresolved")
        self.assertIsNone(control.capacity.amount_safe_to_pay)
        self.assertIn("OUTSTANDING_OBLIGATION_UNSCHEDULED", control.capacity.issue_codes)
        self.assertEqual(replace(actual.capacity, context_hash=""),
                         replace(control.capacity, context_hash=""))
        self.assertEqual(actual.resolved_events[0].obligation_state, "outstanding")
        self.assertEqual(actual.resolved_events[0].event.status, "failed")
        self.assertFalse(replay_financial_plan(actual, (c.Payment(R, c.Money("USD", 1), "p"),)).safe)

    def test_approval_amendment_preserves_outstanding_bill_report19(self):
        text = "The bill remains outstanding. This amends the approval to confirmed."
        events = {"event_1": event(status="failed")}
        raw = raw_amount(text)
        obligation = raw["facts"][0]
        obligation.update(operation="observe", payload=dict(kind="state", axis="obligation", value="outstanding"))
        control = build(adapt(raw, events), events)
        approval = copy.deepcopy(obligation)
        approval.update(local_id="f2", operation="amend", payload=dict(kind="state", axis="approval", value="confirmed"))
        for order in ((0, 1), (1, 0)):
            for ids in (("f1", "f2"), ("f2", "f1")):
                with self.subTest(order=order, ids=ids):
                    observations = copy.deepcopy([obligation, approval])
                    for fact, local_id in zip(observations, ids):
                        fact["local_id"] = local_id
                    raw["facts"] = [observations[i] for i in order]
                    adapted = adapt(raw, events)
                    actual = build(adapted, events)
                    self.assert_outstanding_control(actual, control)
                    self.assertEqual(actual.resolved_events[0].approval_state, "confirmed")
                    self.assertEqual(set(actual.resolved_events[0].fact_ids),
                                     {fact.fact_id for fact in adapted.batch.facts})

    def test_same_source_recency_is_independent_for_each_axis(self):
        events = {"event_1": event(status="failed")}
        outstanding = ("The bill remains outstanding.", "obligation", "outstanding", "observe", OLD)
        control = build(documents(events, [outstanding]), events)
        # Change availability alone: same-time control versus newer approval.
        for sent in (OLD, NEW):
            approval = ("Approval is confirmed.", "approval", "confirmed", "observe", sent)
            for order in ((0, 1), (1, 0)):
                for numbers in ((1, 2), (2, 1)):
                    with self.subTest(sent=sent, order=order, numbers=numbers):
                        records = [outstanding, approval]
                        adapted = documents(events, [records[i] for i in order], numbers=numbers)
                        actual = build(adapted, events)
                        self.assert_outstanding_control(actual, control)
                        self.assertEqual(actual.resolved_events[0].approval_state, "confirmed")
                        self.assertEqual(set(actual.resolved_events[0].fact_ids),
                                         {fact.fact_id for fact in adapted.batch.facts})
                        self.assertEqual({ref.source_id for ref in adapted.sources if ref.kind == "message"},
                                         {"message:message_1", "message:message_2"})

    def test_obligation_amendment_does_not_confirm_conditional_salary(self):
        events = {"event_1": event(status="scheduled", direction="credit", category="salary", day=5)}
        conditional = ("Salary approval is conditional.", "approval", "conditional", "observe", OLD)
        closure = ("This amends the obligation to closed.", "obligation", "closed", "amend", NEW)
        control = build(documents(events, [conditional]), events)
        actual = build(documents(events, [conditional, closure]), events)
        self.assertEqual(control.occurrences, ())
        self.assertEqual(actual.occurrences, control.occurrences)
        self.assertEqual(actual.resolved_events[0].approval_state, "conditional")
        self.assertEqual(actual.resolved_events[0].obligation_state, "closed")
        self.assertEqual(actual.resolved_events[0].event.status, "scheduled")
        self.assertEqual(replace(actual.capacity, context_hash=""), replace(control.capacity, context_hash=""))

    def test_same_axis_explicit_amendment_and_closure_still_apply(self):
        events = {"event_1": event(status="failed")}
        outstanding = ("The bill remains outstanding.", "obligation", "outstanding", "observe", OLD)
        conditional = ("Approval is conditional.", "approval", "conditional", "observe", OLD)
        confirmed = ("This amends approval to confirmed.", "approval", "confirmed", "amend", OLD)
        closed = ("This amends the waived bill to closed.", "obligation", "closed", "amend", OLD)
        actual = build(documents(events, [outstanding, conditional, confirmed]), events)
        self.assert_outstanding_control(actual, build(documents(events, [outstanding]), events))
        self.assertEqual(actual.resolved_events[0].approval_state, "confirmed")
        actual = build(documents(events, [outstanding, conditional, confirmed, closed]), events)
        self.assertEqual((actual.resolved_events[0].approval_state, actual.resolved_events[0].obligation_state),
                         ("confirmed", "closed"))
        self.assertEqual(actual.resolved_events[0].event.status, "failed")
        self.assertNotIn("OUTSTANDING_OBLIGATION_UNSCHEDULED", actual.capacity.issue_codes)
        self.assertEqual((actual.capacity.proof_status, actual.capacity.amount_safe_to_pay),
                         ("resolved_under_policy", c.Money("USD", 90000)))

    def test_same_axis_recency_and_explicit_priority_controls(self):
        events = {"event_1": event(status="failed")}
        confirmed = ("Approval is confirmed.", "approval", "confirmed", "observe", NEW)
        old = ("The bill remains outstanding.", "obligation", "outstanding", "observe", OLD)
        closed = ("The bill is closed.", "obligation", "closed", "observe", NEW)
        actual = build(documents(events, [confirmed, old, closed]), events)
        self.assertEqual((actual.resolved_events[0].approval_state, actual.resolved_events[0].obligation_state),
                         ("confirmed", "closed"))
        self.assertEqual(actual.capacity.proof_status, "resolved_under_policy")
        # An older explicit same-axis amendment outranks a newer observation.
        explicit = ("This amends the bill to outstanding.", "obligation", "outstanding", "amend", OLD)
        actual = build(documents(events, [confirmed, explicit, closed]), events)
        self.assert_outstanding_control(actual, build(documents(events, [explicit]), events))
        self.assertEqual(actual.resolved_events[0].approval_state, "confirmed")
        # An unknown actor must not gain same-source recency precedence.
        actual = build(documents(events, [old, closed], actor_key=None), events)
        self.assert_outstanding_control(actual, build(documents(events, [old], actor_key=None), events))

    def test_selected_metadata_provenance_survives_a_different_cash_winner(self):
        events = {"event_1": event(status="pending", direction="credit", category="salary", day=-1)}
        pending = ("Salary pending; approval conditional.", "approval", "conditional", "observe", OLD)
        settled = ("Salary settled.", "cash", "settled", "settle", "2026-02-01T00:00:00Z")
        adapted = documents(events, [pending, settled])
        # The existing canonical type also permits a source asserting both axes.
        combined = replace(adapted.batch.facts[0], fact_id="combined-source-state",
                           claim=c.StateClaim("pending", approval_state="conditional"))
        adapted = replace(adapted, batch=replace(adapted.batch,
                          facts=(combined, adapted.batch.facts[1])))
        actual = build(adapted, events)
        record = actual.resolved_events[0]
        self.assertEqual((record.event.status, record.disposition), ("settled", "anchored_history"))
        self.assertEqual(record.approval_state, "conditional")
        self.assertEqual(set(record.fact_ids), {fact.fact_id for fact in adapted.batch.facts})
        self.assertEqual(actual.occurrences, ())  # settled credit is already in the snapshot


if __name__ == "__main__":
    unittest.main()
