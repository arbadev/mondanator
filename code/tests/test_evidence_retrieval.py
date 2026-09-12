from __future__ import annotations

import unittest
from datetime import date

from buy_wait.evidence.retrieval import EvidenceIndex
from buy_wait.evidence.schema import EvidenceError


def message(number, *, user="u1", request="", event="", sent="2026-01-01T12:00:00Z", text="Bill pending"):
    return dict(message_id=f"message_{number}", user_id=user, request_id=request, related_event_id=event,
                sent_at=sent, source_type="bank", message_text=text)


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.events = {"e1": {"user_id": "u1", "linked_event_id": ""},
                       "e2": {"user_id": "u1", "linked_event_id": "e1"}, "e3": {"user_id": "u2"}}
        self.requests = {"r1": {"user_id": "u1"}, "r2": {"user_id": "u1"}, "r3": {"user_id": "u2"}}
        self.rows = [message(1), message(2, request="r1"), message(3, event="e1"),
                     message(4, request="r1", event="e1"), message(5, user="u2", request="r3", event="e3"),
                     message(6, request="r2", event="e1")]

    def index(self, rows=None):
        return EvidenceIndex(messages=self.rows if rows is None else rows, images=[], events=self.events, requests=self.requests)

    def test_optional_ids_union_and_dedup(self):
        selected = self.index().retrieve(user_id="u1", request_id="r1", event_ids=["e1"])
        self.assertEqual([s.source_id for s in selected.sources], ["message:message_1", "message:message_2", "message:message_3", "message:message_4", "message:message_6"])
        both = selected.sources[3]
        self.assertEqual(both.reasons, ("request", "event"))
        self.assertEqual(selected.sources[-1].request_id, "r2")

    def test_each_selector_works_independently(self):
        index = self.index()
        self.assertEqual(len(index.retrieve(user_id="u1").sources), 5)
        self.assertEqual(len(index.retrieve(user_id="u1", request_id="r1").sources), 3)
        self.assertEqual(len(index.retrieve(user_id="u1", event_ids=["e1"]).sources), 4)

    def test_wrong_user_never_leaks_even_with_matching_optional_id(self):
        for selector in [dict(request_id="r3"), dict(event_ids=["e3"])]:
            with self.assertRaises(EvidenceError):
                self.index().retrieve(user_id="u1", **selector)
        with self.assertRaises(EvidenceError):
            self.index([message(1, event="e3")])

    def test_day_granular_availability_not_effective_date(self):
        index = self.index([message(1, sent="2026-01-01T23:59:59Z", text="Salary confirmed for 2026-02-15"),
                            message(2, sent="2026-01-02T00:00:00Z")])
        selected = index.retrieve(user_id="u1", as_of=date(2026, 1, 1))
        self.assertEqual(len(selected.sources), 1)
        self.assertEqual(selected.excluded_future_ids, ("message:message_2",))
        self.assertIn("2026-02-15", selected.sources[0].text)

    def test_image_time_unknown_and_data_row_number(self):
        image = dict(image_id="image_1", user_id="u1", request_id="", related_event_id="e1")
        index = EvidenceIndex(messages=[], images=[image], events=self.events, requests=self.requests)
        source = index.retrieve(user_id="u1", event_ids=["e1"], as_of=date(1900, 1, 1)).sources[0]
        self.assertIsNone(source.known_at)
        self.assertEqual(source.row_number, 1)
        self.assertEqual(source.source_id, "image:image_1")

    def test_lifecycle_expansion_and_cycle_detection(self):
        source = self.index().retrieve(user_id="u1", event_ids=["e2"]).sources[1]
        self.assertIn("lifecycle", source.reasons)
        self.events["e1"]["linked_event_id"] = "e2"
        with self.assertRaisesRegex(EvidenceError, "lifecycle_cycle"):
            self.index().retrieve(user_id="u1", event_ids=["e2"])

    def test_duplicate_source_ids_and_naive_time_rejected(self):
        with self.assertRaisesRegex(EvidenceError, "duplicate_source_id"):
            self.index([message(1), message(1)])
        with self.assertRaisesRegex(EvidenceError, "invalid_source_time"):
            self.index([message(1, sent="2026-01-01T12:00:00")])

    def test_deterministic_order_and_input_snapshot(self):
        rows = [message(10), message(2)]
        index = self.index(rows)
        rows[0]["message_text"] = "MUTATED"
        self.assertEqual([s.source_id for s in index.retrieve(user_id="u1").sources], ["message:message_2", "message:message_10"])
        self.assertNotIn("MUTATED", str(index.retrieve(user_id="u1")))
        self.assertEqual(index.retrieve(user_id="u1").sources[-1].text, "Bill pending")


if __name__ == "__main__":
    unittest.main()
