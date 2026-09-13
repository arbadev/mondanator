"""Offline behavioral coverage for the opt-in canonical cache-population entrypoint."""
from __future__ import annotations

import io
import json
from contextlib import redirect_stderr
from decimal import Decimal
from unittest.mock import Mock, patch

import httpx

from integration_fixtures import IntegrationCase, write_csv
from populate_extraction_cache import main, populate_cache
from buy_wait.data import SCHEMAS, DataError, Dataset, load_requests
from buy_wait.evidence import EvidenceError, ExtractionCache, Extractor, MemoryUsageSink, OpenRouterClient
from buy_wait.runner import decide_cached


class PopulateExtractionCacheTests(IntegrationCase):
    def setUp(self):
        super().setUp()
        self.cache = self.work / "cache"
        self.receipts = self.work / "receipts.jsonl"
        self.calls = []

    def kwargs(self, **overrides):
        values = dict(cache_root=self.cache, receipt_path=self.receipts, run_id="approved-test",
                      max_calls=10, max_cost=Decimal("1"), attempt_cost_bound=Decimal("0.1"))
        values.update(overrides)
        return values

    def live_kwargs(self, **overrides):
        return self.kwargs(live=True, account_logging_verified=True, authenticated_availability_verified=True,
                           client=self.client(), **overrides)

    def client(self):
        def respond(request):
            self.calls.append(request)
            source_id = json.loads(request.content)["messages"][1]["content"][0]["text"]
            source_id = json.loads(source_id)["host_context"]["source_id"]
            extraction = {"schema_version": "bw.extraction/1", "source_id": source_id,
                          "language": "en", "disposition": "no_financial_fact",
                          "issuer_name": None, "issuer_evidence": [], "facts": [], "issues": []}
            return httpx.Response(200, json={"id": "gen-test-" + source_id.replace(":", "-"),
                "model": "openai/gpt-4.1", "provider": "Azure",
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(extraction)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cost": "0.001"}})

        return OpenRouterClient(api_key="synthetic-only", live_enabled=True, transport=httpx.MockTransport(respond))

    def cli(self, *extra):
        argv = ["--dataset", str(self.dataset), "--cache", str(self.cache), "--receipts", str(self.receipts),
                "--run-id", "approved-test", "--max-calls", "10", "--budget-usd", "1",
                "--per-attempt-bound-usd", "0.1", *extra]
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            main(argv)
        return raised.exception.code, stderr.getvalue()

    def test_disabled_path_never_resolves_key_constructs_client_or_writes(self):
        with patch("populate_extraction_cache.load_api_key", side_effect=AssertionError("key read")), \
             patch("populate_extraction_cache.OpenRouterClient", side_effect=AssertionError("client")):
            with self.assertRaisesRegex(DataError, "live extraction is disabled"):
                populate_cache(self.dataset, **self.kwargs())
        self.assertFalse(self.cache.exists())
        self.assertFalse(self.receipts.exists())

    def test_mocked_client_populates_canonical_cache_and_durable_receipts(self):
        write_csv(self.dataset / "request_payment_options.csv", SCHEMAS["request_payment_options.csv"],
                  [dict(row, payment_option_id=f"payment_option_{i:02d}")
                   for i, row in enumerate(self.tables["request_payment_options.csv"], 1)])
        result = populate_cache(self.dataset, **self.live_kwargs())
        self.assertGreater(len(self.calls), 0)
        self.assertTrue(self.receipts.is_file())
        self.assertGreater(result["source_contexts"], 0)
        self.assertEqual(result["outcomes"].get("ok"), result["source_contexts"])
        self.assertLessEqual(set(result["outcomes"]), {"ok", "unavailable"})
        self.assertTrue(result["accounting_complete"])
        self.assertEqual(result["new_usage"]["physical_attempts"], len(self.calls))
        self.assertEqual(result["consumed_cache_origins"]["physical_attempts"], 0)
        for request in self.calls:
            payload = json.loads(request.content)
            self.assertEqual(payload["provider"]["only"], ["azure"])
            self.assertFalse(payload["provider"]["allow_fallbacks"])
            self.assertTrue(payload["provider"]["zdr"])
            self.assertNotIn("tools", payload)

        data = Dataset.load(self.dataset)
        requests, _ = load_requests(self.dataset)
        usage = MemoryUsageSink()
        ok_sources = []
        for request in requests:
            trace = decide_cached(data, request, dataset_root=self.dataset, cache=ExtractionCache(self.cache),
                                  usage=usage, run_id="consume-test")
            ok_sources += [s for s in trace["evidence"]["sources"] if s["outcome"] == "ok"]
        self.assertEqual(len(ok_sources), result["outcomes"]["ok"])
        self.assertEqual(len(usage.events), len(ok_sources))
        self.assertTrue(all(event.outcome == "cache_hit" and event.cost == "0" for event in usage.events))

    def test_live_path_requires_both_manual_verification_gates_before_key_read(self):
        for logging, availability in ((False, True), (True, False)):
            with self.subTest(logging=logging, availability=availability), \
                 patch("populate_extraction_cache.load_api_key", side_effect=AssertionError("key read")):
                with self.assertRaisesRegex(DataError, "requires verified account logging"):
                    populate_cache(self.dataset, **self.kwargs(live=True, account_logging_verified=logging,
                                    authenticated_availability_verified=availability))

    def test_programmatic_usd_bounds_are_capped_before_key_read(self):
        for field in ("max_cost", "attempt_cost_bound"):
            for bad in (Decimal("5.01"), Decimal("500"), Decimal("NaN"), Decimal("-1"), Decimal("0"), 1.0):
                with self.subTest(field=field, bad=bad), \
                     patch("populate_extraction_cache.load_api_key", side_effect=AssertionError("key read")):
                    with self.assertRaisesRegex(DataError, "USD bounds"):
                        populate_cache(self.dataset, **self.kwargs(live=True, account_logging_verified=True,
                                        authenticated_availability_verified=True, **{field: bad}))
        self.assertFalse(self.cache.exists())
        self.assertFalse(self.receipts.exists())

    def test_cli_reports_gate_reason_without_reading_key(self):
        with patch("populate_extraction_cache.load_api_key", side_effect=AssertionError("key read")):
            code, stderr = self.cli()
            self.assertEqual(code, 2)
            self.assertIn("live extraction is disabled", stderr)
            code, stderr = self.cli("--live", "--account-logging-verified")
            self.assertEqual(code, 2)
            self.assertIn("requires verified account logging", stderr)

    def test_cli_unusable_receipt_path_fails_bounded_before_cache_or_dispatch(self):
        self.receipts.write_text("")
        for receipts, error in ((self.receipts, "FileExistsError"),
                                (self.work / "missing" / "receipts.jsonl", "FileNotFoundError")):
            client = Mock()
            with self.subTest(error=error), \
                 patch("populate_extraction_cache.load_api_key", return_value="synthetic-only"), \
                 patch("populate_extraction_cache.OpenRouterClient", return_value=client):
                self.receipts = receipts
                code, stderr = self.cli("--live", "--account-logging-verified", "--authenticated-availability-verified")
            self.assertEqual(code, 2)
            self.assertIn(f"cache population failed: {error}", stderr)
            self.assertNotIn("Traceback", stderr)
            self.assertFalse(self.cache.exists())
            client.send.assert_not_called()

    def test_midrun_evidence_error_is_counted_and_accounting_summary_is_kept(self):
        real_extract, seen = Extractor.extract, []

        def flaky(extractor, source, **kwargs):
            seen.append(source.source_id)
            if len(seen) == 2:
                raise EvidenceError("image_hash_mismatch")
            return real_extract(extractor, source, **kwargs)

        with patch.object(Extractor, "extract", autospec=True, side_effect=flaky):
            result = populate_cache(self.dataset, **self.live_kwargs())
        self.assertGreater(len(seen), 2)
        self.assertEqual(result["issues"].get("image_hash_mismatch"), 1)
        self.assertGreaterEqual(result["outcomes"].get("unavailable", 0), 1)
        self.assertTrue(result["accounting_complete"])
        self.assertEqual(result["new_usage"]["physical_attempts"], len(self.calls))
        self.assertGreater(len(self.calls), 0)
