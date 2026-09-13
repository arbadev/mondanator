"""Offline behavioral coverage for the opt-in canonical cache-population entrypoint."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import httpx

from integration_fixtures import IntegrationCase
from populate_extraction_cache import populate_cache
from buy_wait.data import DataError
from buy_wait.evidence import OpenRouterClient


class PopulateExtractionCacheTests(IntegrationCase):
    def setUp(self):
        super().setUp()
        self.cache = self.work / "cache"
        self.receipts = self.work / "receipts.jsonl"

    def kwargs(self, **overrides):
        values = dict(cache_root=self.cache, receipt_path=self.receipts, run_id="approved-test",
                      max_calls=10, max_cost=Decimal("1"), attempt_cost_bound=Decimal("0.1"))
        values.update(overrides)
        return values

    def test_disabled_path_never_resolves_key_constructs_client_or_writes(self):
        with patch("populate_extraction_cache.load_api_key", side_effect=AssertionError("key read")), \
             patch("populate_extraction_cache.OpenRouterClient", side_effect=AssertionError("client")):
            with self.assertRaisesRegex(DataError, "live extraction is disabled"):
                populate_cache(self.dataset, **self.kwargs())
        self.assertFalse(self.cache.exists())
        self.assertFalse(self.receipts.exists())

    def test_mocked_client_populates_canonical_cache_and_durable_receipts(self):
        calls = []

        def respond(request):
            calls.append(request)
            source_id = json.loads(request.content)["messages"][1]["content"][0]["text"]
            source_id = json.loads(source_id)["host_context"]["source_id"]
            extraction = {"schema_version": "bw.extraction/1", "source_id": source_id,
                          "language": "en", "disposition": "no_financial_fact",
                          "issuer_name": None, "issuer_evidence": [], "facts": [], "issues": []}
            return httpx.Response(200, json={"id": "gen-test-" + source_id.replace(":", "-"),
                "model": "openai/gpt-4.1", "provider": "Azure",
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(extraction)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cost": "0.001"}})

        client = OpenRouterClient(api_key="synthetic-only", live_enabled=True, transport=httpx.MockTransport(respond))
        result = populate_cache(self.dataset, **self.kwargs(live=True, account_logging_verified=True,
                                authenticated_availability_verified=True, client=client))
        self.assertGreater(len(calls), 0)
        self.assertTrue(self.receipts.is_file())
        self.assertTrue(self.cache.is_dir())
        self.assertTrue(result["accounting_complete"])
        self.assertEqual(result["new_usage"]["physical_attempts"], len(calls))
        self.assertEqual(result["consumed_cache_origins"]["physical_attempts"], 0)
        for request in calls:
            payload = json.loads(request.content)
            self.assertEqual(payload["provider"]["only"], ["azure"])
            self.assertFalse(payload["provider"]["allow_fallbacks"])
            self.assertTrue(payload["provider"]["zdr"])
            self.assertNotIn("tools", payload)

    def test_live_path_requires_both_manual_verification_gates_before_key_read(self):
        for logging, availability in ((False, True), (True, False)):
            with self.subTest(logging=logging, availability=availability), \
                 patch("populate_extraction_cache.load_api_key", side_effect=AssertionError("key read")):
                with self.assertRaisesRegex(DataError, "requires verified account logging"):
                    populate_cache(self.dataset, **self.kwargs(live=True, account_logging_verified=logging,
                                    authenticated_availability_verified=availability))
