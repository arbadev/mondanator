"""Executable development CLI and immutable artifacts on synthetic data only."""
import csv
import hashlib
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import httpx

from integration_fixtures import CODE, IntegrationCase, SCHEMAS, blank, write_csv
from buy_wait.data import DataError, Dataset, OUTPUT_FIELDS, load_requests
from buy_wait.evidence import EvidenceIndex, ExtractionCache, Extractor, MemoryUsageSink, OpenRouterClient, RunBudget
from buy_wait.evidence.adapter import event_descriptors
from buy_wait.runner import decide_cached
from evaluation.cached_run import run_cached_predictions
from evaluation.package import usage_identity
from evaluation.usage import AccountingError
from test_evidence_client_cache_usage import success
from test_evidence_schema import observation


class CachedCliTests(IntegrationCase):
    def setUp(self):
        super().setUp()
        for name in ("financial_events.csv", "messages.csv", "images.csv"):
            self.rows(name, [])
        self.rows("request_payment_options.csv", [dict(row, payment_option_id=f"payment_option_{i:02d}")
                  for i, row in enumerate(self.tables["request_payment_options.csv"], 1)])
        self.cache_root, self.run_root = self.work / "cache", self.work / "runs"

    def rows(self, name, rows):
        self.tables[name] = rows
        write_csv(self.dataset / name, SCHEMAS[name], rows)

    def run_development(self, run_id="dev", **kwargs):
        return run_cached_predictions(self.dataset, cache_root=self.cache_root, run_root=self.run_root, run_id=run_id, **kwargs)

    def cli(self, *args):
        return subprocess.run([sys.executable, str(CODE / "main.py"), "--dataset", str(self.dataset), *args],
                              cwd=self.work, text=True, capture_output=True)

    def message_fixture(self):
        text = "Revised bill amount due INR 120.00."
        self.rows("financial_events.csv", [blank("financial_events.csv", event_id="event_1", user_id="user_1", event_type="expense",
                    description="Synthetic bill", category="groceries", direction="debit", amount="", currency="INR",
                    event_date="2029-12-30", settlement_date="2030-01-02", status="pending", flexibility="fixed")])
        self.rows("messages.csv", [blank("messages.csv", message_id="message_1", user_id="user_1", request_id="request_1",
                    related_event_id="event_1", sent_at="2030-01-01T12:00:00Z", source_type="merchant", message_text=text)])
        return text

    def test_callable_preserves_full_coverage_hashes_and_run_output_accounting_identity(self):
        result = self.run_development()
        self.assertTrue(result["output_written"])
        self.assertEqual(result["resolved_rows"], 2)
        self.assertFalse(result["final_certified"])
        directory = Path(result["run_directory"])
        manifest = json.loads((directory / "manifest.json").read_text())
        self.assertEqual(manifest["phase"], "development")
        self.assertEqual(manifest["status"], "complete_predictions")
        self.assertNotIn("sample_requests.csv", manifest["source_hashes"])
        for name, digest in manifest["artifact_hashes"].items():
            self.assertEqual(hashlib.sha256((directory / name).read_bytes()).hexdigest(), digest)
        with (directory / "predictions.csv").open(newline="") as stream:
            reader = csv.DictReader(stream)
            self.assertEqual(tuple(reader.fieldnames), OUTPUT_FIELDS)
            self.assertEqual([row["request_id"] for row in reader], ["request_1", "request_2"])
        identity = usage_identity((directory / "usage_report.md").read_bytes())
        self.assertEqual(identity["output_sha256"], manifest["output_sha256"])
        self.assertEqual(identity["request_count"], 2)
        self.assertEqual(len(json.loads((directory / "trace_index.json").read_text())), 2)
        self.assertEqual(json.loads((directory / "usage_summary.json").read_text())["new_usage"]["physical_attempts"], 0)
        self.assertFalse((self.work / "output.csv").exists())

    def test_cli_success_has_explicit_development_mode_and_immutable_run_id(self):
        args = ("--predict-cached", "--cache", str(self.cache_root), "--run-root", str(self.run_root), "--run-id", "cli")
        result = self.cli(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["phase"], "development")
        self.assertFalse(summary["final_certified"])
        before = {p.relative_to(self.run_root): p.read_bytes() for p in self.run_root.rglob("*") if p.is_file()}
        again = self.cli(*args)
        self.assertEqual(again.returncode, 2)
        self.assertEqual(before, {p.relative_to(self.run_root): p.read_bytes() for p in self.run_root.rglob("*") if p.is_file()})
        self.assertEqual(self.cli("--predict-cached").returncode, 2)
        self.assertEqual(self.cli("--check-inputs").returncode, 0)

    def test_submission_mode_writes_external_output_csv_without_affecting_cache_behavior(self):
        output_path = self.work / "submission.csv"
        result = self.cli("--predict-cached", "--cache", str(self.cache_root), "--run-root", str(self.run_root),
                          "--run-id", "submission", "--output", str(output_path))
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertTrue(summary["output_written"])
        self.assertTrue(output_path.is_file())
        with output_path.open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual([row["request_id"] for row in rows], ["request_1", "request_2"])
        directory = Path(summary["run_directory"])
        self.assertTrue((directory / "predictions.csv").exists())
        bad = self.cli("--predict-cached", "--cache", str(self.dataset), "--run-root", str(self.run_root), "--run-id", "bad", "--output", str(self.work / "bad-output.csv"))
        self.assertEqual(bad.returncode, 2)
        self.assertIn("cache/run artifacts", bad.stderr)

    def test_cache_mode_with_extraction_mode_cli_alias_and_submission_output(self):
        output_path = self.work / "alias-output.csv"
        result = self.cli("--extraction-mode", "cache-only", "--cache", str(self.cache_root), "--run-root", str(self.run_root),
                          "--run-id", "alias", "--output", str(output_path))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(output_path.exists())

    def test_cache_miss_keeps_partial_traces_but_no_partial_prediction_csv(self):
        self.message_fixture()
        result = self.cli("--predict-cached", "--cache", str(self.cache_root), "--run-root", str(self.run_root), "--run-id", "missing")
        self.assertEqual(result.returncode, 3, result.stderr)
        summary = json.loads(result.stdout)
        self.assertFalse(summary["output_written"])
        self.assertEqual(summary["resolved_rows"], 1)
        directory = self.run_root / "missing"
        self.assertFalse((directory / "predictions.csv").exists())
        failures = json.loads((directory / "failures.json").read_text())
        self.assertEqual(failures[0]["request_id"], "request_1")
        self.assertIn("CACHE_MISS", failures[0]["core_issue_codes"])
        self.assertEqual(len(json.loads((directory / "trace_index.json").read_text())), 2)
        with self.assertRaises(ValueError):
            usage_identity((directory / "usage_report.md").read_bytes())

    def test_diagnostic_truncation_is_unresolved_not_a_shortened_success_denominator(self):
        result = self.run_development(max_candidates=1)
        self.assertFalse(result["output_written"])
        self.assertEqual(result["request_count"], 2)
        self.assertEqual(result["failure_count"], 2)
        manifest = json.loads((self.run_root / "dev/manifest.json").read_text())
        self.assertEqual(manifest["status"], "unresolved")

    def test_dataset_and_overlapping_destinations_are_rejected_without_writes(self):
        before = {p.name: p.read_bytes() for p in self.dataset.iterdir() if p.is_file()}
        for cache, runs in ((self.dataset / "cache", self.run_root), (self.cache_root, self.dataset / "runs"),
                            (self.run_root, self.run_root)):
            with self.subTest(cache=cache, runs=runs), self.assertRaises(DataError):
                run_cached_predictions(self.dataset, cache_root=cache, run_root=runs, run_id="bad")
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.dataset.iterdir() if p.is_file()})
        self.assertFalse((self.dataset / "cache").exists())
        self.assertFalse((self.dataset / "runs").exists())

    def test_cache_only_external_output_never_reads_labels_template_credentials_or_dispatches(self):
        original = Path.read_bytes
        output_path = self.work / "development-output.csv"
        def guarded(path):
            if path.name in ("sample_requests.csv", "output.csv", ".env"):
                self.fail("forbidden production read")
            return original(path)
        with patch.object(Path, "read_bytes", guarded), patch.object(OpenRouterClient, "send", side_effect=AssertionError("dispatch")):
            result = self.run_development(output_path=output_path)
        self.assertTrue(result["output_written"])
        self.assertTrue(output_path.is_file())
        self.assertFalse(result["final_certified"])

    def test_oversized_trace_becomes_a_sanitized_request_failure_without_partial_file(self):
        with patch("evaluation.cached_run._MAX_ARTIFACT_BYTES", 16 * 1024), \
             patch("evaluation.cached_run.trace_wire", return_value={"payload": "x" * (17 * 1024)}):
            result = self.run_development()
        self.assertFalse(result["output_written"])
        directory = self.run_root / "dev"
        failures = json.loads((directory / "failures.json").read_text())
        self.assertEqual([failure["code"] for failure in failures], ["DECISION_EXECUTION_FAILED"] * 2)
        self.assertTrue(all(failure["exception_type"] == "DataError" for failure in failures))
        self.assertFalse(any((directory / "traces").iterdir()))

    def test_request_execution_error_is_sanitized_retained_and_prevents_export(self):
        def execute(data, req, **kwargs):
            if req["request_id"] == "request_1":
                raise RuntimeError("SYNTHETIC_DO_NOT_ECHO")
            return decide_cached(data, req, **kwargs)
        with patch("evaluation.cached_run.decide_cached", side_effect=execute):
            result = self.run_development()
        self.assertFalse(result["output_written"])
        failure = json.loads((self.run_root / "dev/failures.json").read_text())[0]
        self.assertEqual(failure["exception_type"], "RuntimeError")
        for path in (self.run_root / "dev").rglob("*"):
            if path.is_file():
                self.assertNotIn(b"SYNTHETIC_DO_NOT_ECHO", path.read_bytes())

    def test_accounting_failure_cannot_be_reported_as_zero_cost_success(self):
        with patch("evaluation.cached_run.aggregate_usage", side_effect=AccountingError("SYNTHETIC_DO_NOT_ECHO")):
            result = self.run_development()
        self.assertFalse(result["output_written"])
        self.assertFalse(result["accounting_complete"])
        manifest = json.loads((self.run_root / "dev/manifest.json").read_text())
        self.assertIsNone(manifest["output_sha256"])
        self.assertFalse((self.run_root / "dev/usage_summary.json").exists())
        self.assertEqual(json.loads((self.run_root / "dev/failures.json").read_text())[-1]["code"], "ACCOUNTING_FAILED")

    def test_real_cached_origin_flows_to_durable_report_without_new_dispatch(self):
        text = self.message_fixture()
        data = Dataset.load(self.dataset)
        req = load_requests(self.dataset)[0][0]
        financial = data.financial_input_for(req)
        events = {event.event_id: event for event in financial.events}
        source = EvidenceIndex.from_context(data.context_for(req)).retrieve(user_id="user_1", request_id="request_1",
                    event_ids=tuple(events), as_of=financial.request_date).sources[0]
        raw = observation(text=text, value="120.00", raw="120.00", role="net_payable")
        raw["facts"][0]["payload"].update(currency="INR", direction="debit")
        raw["facts"][0]["operation"] = "amend"
        calls = []
        def respond(http_request):
            calls.append(http_request)
            return httpx.Response(200, json=success(raw))
        seed = Extractor(cache=ExtractionCache(self.cache_root), usage=MemoryUsageSink(), run_id="synthetic-origin",
                         client=OpenRouterClient(api_key="synthetic-only", live_enabled=True, transport=httpx.MockTransport(respond)),
                         budget=RunBudget(max_calls=1, max_cost=Decimal("0.1")))
        seed.extract(source, candidate_events=event_descriptors(events, user_id="user_1"), as_of=financial.request_date,
                     mode="live", cost_upper_bound=Decimal("0.1"))
        with patch.object(OpenRouterClient, "send", side_effect=AssertionError("second dispatch")):
            result = self.run_development()
        self.assertTrue(result["output_written"])
        self.assertEqual(len(calls), 1)
        summary = json.loads((self.run_root / "dev/usage_summary.json").read_text())
        self.assertEqual(summary["new_cache_hits"], 1)
        self.assertEqual(summary["new_usage"]["physical_attempts"], 0)
        self.assertEqual(summary["consumed_cache_origins"]["physical_attempts"], 1)
        self.assertEqual(summary["combined_unique_usage"]["total_tokens"], 120)
        self.assertEqual(summary["combined_unique_usage"]["cost_usd"], "0.002")
        self.assertEqual(summary["combined_unique_usage"]["average_cost_usd_per_request"], "0.001")
