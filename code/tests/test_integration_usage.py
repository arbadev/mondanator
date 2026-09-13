"""Canonical receipt persistence/accounting, using synthetic events only."""
import json
from dataclasses import replace
from decimal import localcontext
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from integration_fixtures import IntegrationCase
from buy_wait.evidence.usage import UsageEvent
from evaluation.package import usage_identity
from evaluation.usage import AccountingError, JsonlUsageSink, aggregate_usage, read_usage_events, render_usage_report


def receipt(identity="a1", **changes):
    fields = dict(run_id="dev", attempt_id=identity, source_id="message:message_1", cache_key="a" * 64,
                  requested_model="openai/gpt-4.1", returned_model="openai/gpt-4.1-2025-04-14", actual_provider="Azure",
                  generation_id="gen-synthetic", outcome="validated", retry_of=None,
                  prompt_tokens=100, completion_tokens=10, total_tokens=110, cost="0.000288",
                  cached_tokens=20, reasoning_tokens=0, cache_write_tokens=0)
    fields.update(changes)
    return UsageEvent(**fields)


def hit(identity, origins, **changes):
    return receipt(identity, outcome="cache_hit", prompt_tokens=0, completion_tokens=0, total_tokens=0,
                   cost="0", cache_origin_attempt_ids=origins, **changes)


class UsageTests(IntegrationCase):
    def test_durable_readback_duplicates_cross_run_and_nonoverwrite(self):
        path = self.work / "usage.jsonl"
        sink = JsonlUsageSink(path, run_id="dev")
        event = receipt()
        sink.record(event)
        before = path.read_bytes()
        self.assertEqual(read_usage_events(path, run_id="dev"), (event,))
        with self.assertRaises(AccountingError):
            sink.record(event)
        with self.assertRaises(AccountingError):
            sink.record(receipt("foreign", run_id="other"))
        with self.assertRaises(FileExistsError):
            JsonlUsageSink(path, run_id="dev")
        self.assertEqual(path.read_bytes(), before)

    def test_threaded_distinct_receipts_are_whole_and_not_lost(self):
        path = self.work / "usage.jsonl"
        sink = JsonlUsageSink(path, run_id="dev")
        values = [receipt(f"attempt-{i}") for i in range(12)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(sink.record, values))
        self.assertEqual(set(read_usage_events(path)), set(values))
        self.assertEqual(len(path.read_bytes().splitlines()), 12)

    def test_failed_sync_retains_the_dispatched_receipt_not_free_budget(self):
        path = self.work / "usage.jsonl"
        sink = JsonlUsageSink(path, run_id="dev")
        with patch("evaluation.usage.os.fsync", side_effect=OSError("SYNTHETIC_FAILURE")), self.assertRaises(OSError):
            sink.record(receipt())
        self.assertEqual(read_usage_events(path), (receipt(),))

    def test_partial_or_duplicate_json_ledger_is_not_repaired(self):
        path = self.work / "bad.jsonl"
        partial = b'{"run_id":"dev"'
        path.write_bytes(partial)
        with self.assertRaises(AccountingError):
            read_usage_events(path)
        self.assertEqual(path.read_bytes(), partial)
        path.write_text('{"run_id":"dev","run_id":"other"}\n')
        with self.assertRaises(AccountingError):
            read_usage_events(path)

    def test_symlinks_are_refused_before_ledger_read(self):
        target = self.work / "synthetic-target.jsonl"
        target.write_text("SYNTHETIC_NOT_A_KEY")
        alias = self.work / "alias.jsonl"
        alias.symlink_to(target)
        with self.assertRaises(AccountingError):
            read_usage_events(alias)
        with self.assertRaises(AccountingError):
            JsonlUsageSink(alias, run_id="dev")
        self.assertEqual(target.read_text(), "SYNTHETIC_NOT_A_KEY")

    def test_retries_failures_and_shared_cache_origins_are_counted_once(self):
        first = receipt("origin-1", run_id="prior", outcome="rate_limit", cost="0.000100")
        second = receipt("origin-2", run_id="prior", retry_of="origin-1", cost="0.000200")
        events = (receipt("new-1"), hit("hit-1", ("origin-1", "origin-2")), hit("hit-2", ("origin-1", "origin-2")))
        summary = aggregate_usage(events, origin_usage=(first, second, first, second), run_id="dev", request_count=4)
        self.assertTrue(summary["accounting_complete"])
        self.assertEqual(summary["new_cache_hits"], 2)
        self.assertEqual(summary["new_usage"]["physical_attempts"], 1)
        self.assertEqual(summary["consumed_cache_origins"]["physical_attempts"], 2)
        self.assertEqual(summary["combined_unique_usage"]["physical_attempts"], 3)
        self.assertEqual(summary["combined_unique_usage"]["retries"], 1)
        self.assertEqual(summary["combined_unique_usage"]["failures"], 1)
        self.assertEqual(summary["combined_unique_usage"]["total_tokens"], 330)
        self.assertEqual(summary["combined_unique_usage"]["average_total_tokens_per_request"], "82.5")
        self.assertEqual(summary["combined_unique_usage"]["cost_usd"], "0.000588")
        self.assertEqual(summary["combined_unique_usage"]["cached_tokens"], 60)  # included, not another60 total

    def test_same_run_cache_origin_does_not_double_new_physical_attempt(self):
        event = receipt()
        summary = aggregate_usage((event, hit("hit", (event.attempt_id,))), origin_usage=(event,), run_id="dev", request_count=2)
        self.assertEqual(summary["new_usage"]["physical_attempts"], 1)
        self.assertEqual(summary["consumed_cache_origins"]["physical_attempts"], 1)
        self.assertEqual(summary["combined_unique_usage"]["physical_attempts"], 1)
        self.assertTrue(summary["accounting_complete"])

    def test_missing_and_unknown_origins_stay_null_with_known_subtotals(self):
        unknown = receipt("old", run_id="prior", cost=None, prompt_tokens=None, completion_tokens=None, total_tokens=None,
                          actual_provider=None, returned_model=None)
        summary = aggregate_usage((hit("hit", ("old", "missing")),), origin_usage=(unknown,), run_id="dev", request_count=250)
        self.assertFalse(summary["accounting_complete"])
        self.assertEqual(summary["request_count"], 250)
        values = summary["combined_unique_usage"]
        self.assertIsNone(values["cost_usd"])
        self.assertIsNone(values["total_tokens"])
        self.assertIsNone(values["physical_attempts"])
        self.assertEqual(values["known_total_tokens"], 0)
        self.assertEqual(set(values["unknown_cost_ids"]), {"old", "missing"})
        self.assertIn("missing_cache_origin_receipts", summary["unknown_reasons"])
        self.assertIn("unknown_usage_or_provider", summary["unknown_reasons"])

    def test_origin_conflict_and_cross_source_reference_cannot_be_aggregated(self):
        one = receipt("old", run_id="prior")
        with self.assertRaises(AccountingError):
            aggregate_usage((hit("hit", ("old",)),), origin_usage=(one, replace(one, cost="1")), run_id="dev", request_count=1)
        with self.assertRaises(AccountingError):
            aggregate_usage((hit("hit", ("old",)),), origin_usage=(replace(one, source_id="image:image_1"),), run_id="dev", request_count=1)
        with self.assertRaises(AccountingError):
            aggregate_usage((receipt(), receipt()), run_id="dev", request_count=1)
        with self.assertRaises(AccountingError):
            aggregate_usage((), origin_usage=(hit("nested", ("old",)),), run_id="dev", request_count=1)

    def test_lost_sink_receipt_is_recovered_once_but_ledger_is_not_certified(self):
        event = receipt()
        summary = aggregate_usage((), origin_usage=(event,), run_id="dev", request_count=2)
        self.assertEqual(summary["new_usage"]["physical_attempts"], 1)
        self.assertEqual(summary["recovered_current_attempt_ids"], [event.attempt_id])
        self.assertFalse(summary["accounting_complete"])
        self.assertIn("current_attempts_missing_from_durable_ledger", summary["unknown_reasons"])

    def test_cost_precision_and_multiple_models_are_separate(self):
        one = receipt("a", cost="0.123456789012")
        two = receipt("b", requested_model="fixture/other", returned_model="fixture/other", cost="0.000000000009")
        with localcontext() as context:
            context.prec = 3
            summary = aggregate_usage((one, two), run_id="dev", request_count=3)
        self.assertEqual(summary["combined_unique_usage"]["cost_usd"], "0.123456789021")
        self.assertEqual(len(summary["by_model"]), 2)
        with self.assertRaises(AccountingError):
            aggregate_usage((receipt(cost="1e1000000"),), run_id="dev", request_count=1)

    def test_empty_usage_report_identity_and_strict_denominator(self):
        summary = aggregate_usage((), run_id="dev", request_count=2)
        report = render_usage_report(summary, output_sha256="b" * 64)
        identity = usage_identity(report.encode())
        self.assertEqual(identity, dict(run_id="dev", request_count=2, output_sha256="b" * 64, accounting_complete=True))
        self.assertEqual(summary["combined_unique_usage"]["total_tokens"], 0)
        self.assertEqual(summary["by_model"], [])
        self.assertEqual(summary["coding_assistant_usage"], "excluded")
        json.dumps(summary, allow_nan=False)
        for denominator in (0, True, 1.0):
            with self.subTest(denominator=denominator), self.assertRaises(AccountingError):
                aggregate_usage((), run_id="dev", request_count=denominator)
