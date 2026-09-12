"""Public participant input boundary fixtures; no model accuracy is asserted.

The expected financial readings below are hand-authored public-image observations
from the scope report, only in tests. The application never imports this module.
"""
from __future__ import annotations

import csv
import unittest
from pathlib import Path

from buy_wait.evidence.images import resolve_image
from buy_wait.evidence.retrieval import EvidenceIndex
from buy_wait.evidence.schema import canonical, parse_extraction, validate_support
from test_evidence_schema import observation

DATASET = Path(__file__).resolve().parents[2] / "dataset"


def rows(name):
    with (DATASET / name).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


class PublicBoundaryTests(unittest.TestCase):
    def test_public_user_only_amendment_is_retrieved_without_answer_fields(self):
        # Test harness alone uses samples to identify public users; production
        # evidence retrieval receives only loaded source/event/request fields.
        public = rows("sample_requests.csv")
        request_fields = {"request_id", "user_id", "request_date", "request_type", "requested_amount",
                          "desired_completion_date", "allows_partial_payment", "request_text"}
        requests = {r["request_id"]: {k: r[k] for k in request_fields} for r in public}
        users = {r["user_id"] for r in public}
        events = {r["event_id"]: r for r in rows("financial_events.csv") if r["user_id"] in users}
        messages = [r for r in rows("messages.csv") if r["user_id"] in users]
        images = [r for r in rows("images.csv") if r["user_id"] in users]
        index = EvidenceIndex(messages=messages, images=images, events=events, requests=requests)
        source = next(s for s in index.retrieve(user_id="user_02", request_id="request_02").sources if s.source_id == "message:message_01")
        self.assertIsNone(source.related_event_id)
        self.assertIsNone(source.request_id)
        self.assertEqual(source.reasons, ("user",))
        raw = observation(source=source.source_id, text=source.text, role="regular_salary", value="42750000", raw="42750000")
        raw["language"] = "id"
        raw["facts"][0]["subject"].update(scope="series", event_id=None)
        raw["facts"][0]["operation"] = "amend"
        raw["facts"][0]["payload"].update(currency="IDR")
        raw["facts"][0]["effect_window"] = dict(scope="from_date", start_date="2025-08-15", end_date=None, anchor_quote="berlaku mulai 2025-08-15")
        parsed = parse_extraction(canonical(raw))
        validate_support(parsed, source_id=source.source_id, text=source.text, candidate_events={})
        self.assertEqual(parsed.facts[0].operation, "amend")
        self.assertEqual(parsed.facts[0].payload.value, "42750000")

    def test_five_public_image_paths_decode_and_blank_events_are_not_zero(self):
        public_users = {r["user_id"] for r in rows("sample_requests.csv")}
        images = [r for r in rows("images.csv") if r["user_id"] in public_users]
        events = {r["event_id"]: r for r in rows("financial_events.csv") if r["user_id"] in public_users}
        self.assertEqual(len(images), 5)
        for row in images:
            with self.subTest(image=row["image_id"]):
                self.assertEqual(events[row["related_event_id"]]["amount"], "")
                asset = resolve_image(DATASET, row["image_id"])
                self.assertGreater(asset.width * asset.height, 0)
                self.assertEqual(len(asset.sha256), 64)
        # This tests supplied image availability, not successful vision inference.

    def test_public_outstanding_rent_role_is_not_receipt_total(self):
        raw = observation(source="image:image_02", text="Balance Due: 1,00,000.00", role="balance_due", value="100000.00", raw="1,00,000.00")
        fact = raw["facts"][0]
        fact["subject"].update(event_id="event_1442", topic="rent", label="Outstanding rent balance")
        fact["payload"].update(currency="INR", currency_basis="linked_event", direction="debit")
        parsed = parse_extraction(canonical(raw))
        validate_support(parsed, source_id="image:image_02", text=None, candidate_events={"event_1442": {"currency": "INR"}})
        self.assertEqual(parsed.facts[0].payload.role, "balance_due")
        self.assertEqual(parsed.facts[0].payload.value, "100000.00")


if __name__ == "__main__":
    unittest.main()
