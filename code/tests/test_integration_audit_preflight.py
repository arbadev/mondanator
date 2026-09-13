"""Behavioral audit/preflight/reproduction tests; no real model or label seeding."""
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from integration_fixtures import CODE, IntegrationCase, SAMPLE_FIELDS, SCHEMAS, blank, write_csv
from buy_wait import contracts as c
from buy_wait.data import DataError, Dataset, REQUEST_FIELDS, load_requests
from buy_wait.core import replay_financial_plan
from buy_wait.evidence import ExtractionCache, MemoryUsageSink, OpenRouterClient
from buy_wait.runner import decide_cached
from evaluation.audits import audit_decision
from evaluation.cached_run import run_cached_predictions, run_public_preflight
from evaluation.reproduction import verify_development_run
from evaluation.package import build_code_zip, usage_identity


class _CleanCase(IntegrationCase):
    def setUp(self):
        super().setUp()
        for name in ("financial_events.csv", "messages.csv", "images.csv"):
            self.rows(name, [])
        self.rows("request_payment_options.csv", [dict(row, payment_option_id=f"payment_option_{i:02d}")
                  for i, row in enumerate(self.tables["request_payment_options.csv"], 1)])
        self.run_root = self.work / "runs"

    def rows(self, name, rows):
        self.tables[name] = rows
        write_csv(self.dataset / name, SCHEMAS[name], rows)

    def trace(self):
        data = Dataset.load(self.dataset)
        request = load_requests(self.dataset)[0][0]
        result = decide_cached(data, request, dataset_root=self.dataset, cache=ExtractionCache(self.work / "cache"),
                               usage=MemoryUsageSink(), run_id="audit")
        return data, request, result

    def audit(self, data, req, trace, **kwargs):
        return audit_decision(data, req, trace, dataset_root=self.dataset, **kwargs)

    def unknown_bill(self):
        self.rows("financial_events.csv", [blank("financial_events.csv", event_id="event_1", user_id="user_1", event_type="expense",
            description="Synthetic bill", category="groceries", direction="debit", amount="", currency="INR",
            event_date="2029-12-30", settlement_date="2030-01-02", status="pending", flexibility="fixed")])
        self.rows("messages.csv", [blank("messages.csv", message_id="message_1", user_id="user_1", request_id="request_1",
            related_event_id="event_1", sent_at="2030-01-01T12:00:00Z", source_type="merchant", message_text="Bill amount needs confirmation.")])


class AuditTests(_CleanCase):
    def test_actual_plan_is_replayed_and_semantic_grounding_is_not_invented(self):
        data, req, trace = self.trace()
        audit = self.audit(data, req, trace)
        self.assertTrue(audit["integrity_passed"], audit["integrity_violations"])
        for name in ("safety", "schedule_validity", "eligibility", "action_validity"):
            self.assertTrue(audit[name]["checked"])
            self.assertEqual(audit[name]["violations"], [])
        self.assertEqual(audit["replay"].minimum_available.minor, 95000)
        self.assertTrue(audit["templated_text_consistency"]["checked"])
        self.assertFalse(audit["explanation_grounding"]["checked"])
        self.assertIsNone(audit["explanation_grounding"]["violations"])
        self.assertFalse(audit["financial_certified"])

    def test_forged_capacity_and_validation_flags_cannot_pass_recomposition(self):
        data, req, trace = self.trace()
        original = trace["core"]
        trace["core"] = replace(original, capacity=replace(original.capacity, amount_safe_to_pay=c.Money("INR", 0)))
        audit = self.audit(data, req, trace)
        self.assertFalse(audit["integrity_passed"])
        self.assertIn({"code": "TRACE_RECOMPOSITION_MISMATCH", "field": "core"}, audit["integrity_violations"])
        trace["core"] = original
        values = list(trace["validations"])
        values[0] = {**values[0], "financial_safe": not values[0]["financial_safe"]}
        trace["validations"] = tuple(values)
        self.assertFalse(self.audit(data, req, trace)["integrity_passed"])

    def test_audit_replays_actual_forged_plan_not_rebuilt_safe_replacement(self):
        data, req, trace = self.trace()
        winner = trace["ranking"]["winner"]
        plan = replace(winner["plan"], payments=(c.Payment(winner["plan"].payments[0].date, c.Money("INR", 500000), "bad"),))
        trace["ranking"] = {**trace["ranking"], "winner": {**winner, "plan": plan}}
        audit = self.audit(data, req, trace)
        self.assertFalse(audit["integrity_passed"])
        self.assertEqual(audit["replayed_plan"], plan)
        self.assertFalse(audit["replay"].safe)
        self.assertTrue(audit["safety"]["checked"])
        self.assertTrue(audit["safety"]["violations"])
        self.assertTrue(audit["schedule_validity"]["violations"])

    def test_independent_replay_violation_blocks_even_matching_recomposition(self):
        data, req, trace = self.trace()
        unsafe = replay_financial_plan(trace["core"], (c.Payment(trace["core"].request_date, c.Money("INR", 500000), "unsafe"),))
        self.assertFalse(unsafe.safe)
        # Inject a real unsafe result at the independent-check boundary, while
        # recomposition remains identical; the audit must not ignore its result.
        with patch("evaluation.audits.replay_financial_plan", return_value=unsafe):
            audit = self.audit(data, req, trace)
        self.assertTrue(audit["safety"]["violations"])
        self.assertFalse(audit["integrity_passed"])

    def test_modified_row_and_invented_explanation_are_detected(self):
        data, req, trace = self.trace()
        trace["row"] = {**trace["row"], "amount_safe_to_pay": "0", "decision_explanation": "Invented settled refund."}
        audit = self.audit(data, req, trace)
        self.assertFalse(audit["integrity_passed"])
        self.assertTrue(audit["templated_text_consistency"]["violations"])
        self.assertFalse(audit["explanation_grounding"]["checked"])

    def test_wrong_request_and_malformed_trace_never_become_checked_safety(self):
        data, req, trace = self.trace()
        wrong = load_requests(self.dataset)[0][1]
        for value, request in ((trace, wrong), (None, req), (True, req), ({}, req)):
            with self.subTest(request=request["request_id"], shape=type(value).__name__):
                audit = self.audit(data, request, value)
                self.assertFalse(audit["integrity_passed"])
                self.assertFalse(audit["safety"]["checked"])
                self.assertIsNone(audit["safety"]["violations"])

    def test_unresolved_cache_is_consistent_but_not_checked_financial_safety(self):
        self.unknown_bill()
        data, req, trace = self.trace()
        audit = self.audit(data, req, trace)
        self.assertTrue(audit["integrity_passed"], audit["integrity_violations"])
        self.assertEqual(audit["core_proof_status"], "unresolved")
        self.assertFalse(audit["safety"]["checked"])
        self.assertIsNone(audit["safety"]["violations"])
        source = {**trace["evidence"]["sources"][0], "content_sha256": "0" * 64}
        trace["evidence"] = {**trace["evidence"], "sources": (source,)}
        self.assertFalse(self.audit(data, req, trace)["integrity_passed"])

    def test_owned_cli_audit_violation_blocks_csv_without_repairing_the_row(self):
        def bad(data, request, **kwargs):
            trace = decide_cached(data, request, **kwargs)
            trace["row"] = {**trace["row"], "decision_explanation": "Invented settlement."}
            return trace
        with patch("evaluation.cached_run.decide_cached", side_effect=bad):
            result = run_cached_predictions(self.dataset, cache_root=self.work / "cache", run_root=self.run_root, run_id="bad")
        self.assertFalse(result["output_written"])
        rows = json.loads((self.run_root / "bad/candidate_rows.json").read_text())
        self.assertEqual(rows[0]["decision_explanation"], "Invented settlement.")
        self.assertIn("AUDIT_INTEGRITY_FAILED", {f["code"] for f in json.loads((self.run_root / "bad/failures.json").read_text())})


class PreflightTests(_CleanCase):
    def test_fresh_cache_diagnostics_keep_complete_denominator_and_unknowns(self):
        self.unknown_bill()
        with patch.object(OpenRouterClient, "send", side_effect=AssertionError("dispatch")) as send:
            result = run_public_preflight(self.dataset, run_root=self.run_root, run_id="public")
        send.assert_not_called()
        self.assertTrue(result["preflight_complete"])
        self.assertFalse(result["output_written"])
        coverage = result["coverage"]
        self.assertEqual(coverage["denominator_requests"], 2)
        self.assertEqual(coverage["executed_requests"], 2)
        self.assertEqual(coverage["requests_with_cache_misses"], 1)
        self.assertEqual(coverage["cache_miss_source_references"], 1)
        self.assertEqual(coverage["unresolved_requests"], 1)
        self.assertFalse(coverage["accuracy_measured"])
        directory = self.run_root / "public"
        self.assertEqual((directory / "usage_events.jsonl").read_bytes(), b"")
        self.assertEqual(json.loads((directory / "origin_usage.json").read_text()), [])
        self.assertFalse((directory / "predictions.csv").exists())
        self.assertEqual(list((self.run_root / "public.preflight-cache").rglob("*.json")), [])

    def test_expected_answer_poison_cannot_change_inference_inputs_or_preflight(self):
        seen = []
        def spy(data, req, **kwargs):
            seen.append(dict(req))
            self.assertEqual(set(req), set(REQUEST_FIELDS))
            return decide_cached(data, req, **kwargs)
        with patch("evaluation.cached_run.decide_cached", side_effect=spy):
            first = run_public_preflight(self.dataset, run_root=self.run_root, run_id="first")
        before_inputs = seen[:]
        import csv
        with (self.dataset / "sample_requests.csv").open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        for row in rows:
            for key in set(SAMPLE_FIELDS) - set(REQUEST_FIELDS):
                row[key] = "POISON_EXPECTED_ONLY"
        write_csv(self.dataset / "sample_requests.csv", SAMPLE_FIELDS, rows)
        seen.clear()
        with patch("evaluation.cached_run.decide_cached", side_effect=spy):
            second = run_public_preflight(self.dataset, run_root=self.run_root, run_id="second")
        self.assertEqual(seen, before_inputs)
        self.assertEqual(first["coverage"], second["coverage"])
        a = json.loads((self.run_root / "first/manifest.json").read_text())
        b = json.loads((self.run_root / "second/manifest.json").read_text())
        self.assertEqual(a["request_inputs_sha256"], b["request_inputs_sha256"])
        self.assertNotEqual(a["source_hashes"]["sample_requests.csv"], b["source_hashes"]["sample_requests.csv"])

    def test_existing_cache_or_run_is_refused_without_modification(self):
        cache = self.run_root / "prior.preflight-cache"
        cache.mkdir(parents=True)
        marker = cache / "do-not-read"
        marker.write_text("SYNTHETIC_EXISTING_CACHE_SENTINEL")
        with self.assertRaises(DataError):
            run_public_preflight(self.dataset, run_root=self.run_root, run_id="prior")
        self.assertEqual(marker.read_text(), "SYNTHETIC_EXISTING_CACHE_SENTINEL")
        run_public_preflight(self.dataset, run_root=self.run_root, run_id="new")
        before = (self.run_root / "new/manifest.json").read_bytes()
        with self.assertRaises((DataError, FileExistsError)):
            run_public_preflight(self.dataset, run_root=self.run_root, run_id="new")
        self.assertEqual((self.run_root / "new/manifest.json").read_bytes(), before)

    def test_diagnostic_cap_is_recorded_and_not_claimed_as_complete_search(self):
        result = run_public_preflight(self.dataset, run_root=self.run_root, run_id="cap", max_candidates=1)
        self.assertTrue(result["preflight_complete"])
        self.assertEqual(result["coverage"]["unresolved_requests"], 2)
        self.assertEqual(result["coverage"]["audit_dimensions_checked"]["safety"], 0)
        self.assertIn("NO_CHANGE_SEARCH_INCOMPLETE", result["coverage"]["ranking_issue_request_counts"])
        self.assertFalse(result["output_written"])

    def test_executable_public_preflight_never_writes_predictions_or_scores(self):
        run = subprocess.run([sys.executable, str(CODE / "evaluation/main.py"), "--dataset", str(self.dataset),
              "--preflight-public", "--run-root", str(self.run_root), "--run-id", "cli"],
              cwd=self.work, env={"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}, text=True, capture_output=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        self.assertTrue(result["preflight_complete"])
        self.assertFalse(result["coverage"]["expected_answers_used"])
        self.assertFalse((self.run_root / "cli/predictions.csv").exists())
        self.assertFalse((self.run_root / "cli/metrics.json").exists())


class ReproductionTests(_CleanCase):
    def prepared(self):
        run_public_preflight(self.dataset, run_root=self.run_root, run_id="original")
        directory = self.run_root / "original"
        digest = hashlib.sha256((directory / "manifest.json").read_bytes()).hexdigest()
        return directory, digest

    def test_complete_artifact_round_trip_and_data_identity_are_not_financial_truth(self):
        original, digest = self.prepared()
        copy_to = self.work / "copied"
        shutil.copytree(original, copy_to)
        result = verify_development_run(copy_to, dataset_root=self.dataset, expected_manifest_sha256=digest)
        self.assertTrue(result["artifact_integrity_verified"])
        self.assertTrue(result["manifest_pin_verified"])
        self.assertFalse(result["financial_certified"])
        self.assertFalse(result["source_semantics_verified"])

    def test_mutated_artifact_input_inventory_and_pin_are_rejected(self):
        directory, digest = self.prepared()
        file = directory / "coverage.json"
        old = file.read_bytes()
        file.write_bytes(old + b"\n")
        with self.assertRaises(DataError):
            verify_development_run(directory, dataset_root=self.dataset, expected_manifest_sha256=digest)
        file.write_bytes(old)
        with self.assertRaises(DataError):
            verify_development_run(directory, expected_manifest_sha256="0" * 64)
        extra = directory / "extra.txt"
        extra.write_text("unexpected")
        with self.assertRaises(DataError):
            verify_development_run(directory)
        extra.unlink()
        source = self.dataset / "financial_events.csv"
        source.write_bytes(source.read_bytes() + b"\n")
        with self.assertRaises(DataError):
            verify_development_run(directory, dataset_root=self.dataset)

    def test_actual_packaged_application_reproduces_synthetic_prediction_bytes(self):
        # A real application, not the earlier toy program. Same installed pinned
        # interpreter/dependencies; this is NOT a fresh dependency installation.
        original = run_cached_predictions(self.dataset, cache_root=self.work / "cache", run_root=self.run_root, run_id="application")
        self.assertTrue(original["output_written"])
        run_dir = Path(original["run_directory"])
        staging = self.work / "synthetic-bundle"
        staging.mkdir()
        shutil.copytree(self.dataset, staging / "dataset")  # fixture input, NOT an archive member
        (staging / "output.csv").write_bytes((run_dir / "predictions.csv").read_bytes())
        sources = [CODE / "main.py", CODE.parent / "README.md", CODE.parent / "requirements.txt"]
        for package in (CODE / "buy_wait", CODE / "evaluation"):
            sources.extend(p for p in package.rglob("*") if p.is_file() and p.suffix in (".py", ".txt") and "__pycache__" not in p.parts)
        members = {}
        for path in sources:
            relative = path.relative_to(CODE.parent).as_posix()
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = path.read_bytes()
            target.write_bytes(payload)
            members[relative] = hashlib.sha256(payload).hexdigest()
        report = (run_dir / "usage_report.md").read_bytes()
        (staging / "evaluation").mkdir()
        (staging / "evaluation/usage_report.md").write_bytes(report)
        members["evaluation/usage_report.md"] = hashlib.sha256(report).hexdigest()
        dev_manifest = json.loads((run_dir / "manifest.json").read_text())
        # Synthetic packaging guard fixture only, not a real final-run assertion.
        manifest = {"schema_version": "buy-wait-final-artifacts/v1", "phase": "final", **usage_identity(report),
            "critical_issues": 0, "dataset_hashes": dev_manifest["source_hashes"], "archive_files": members,
            "proof_status_by_request": {entry["request_id"]: json.loads((run_dir / entry["trace"]).read_text())["core"]["capacity"]["proof_status"]
                for entry in json.loads((run_dir / "trace_index.json").read_text())}}
        (staging / "synthetic-release.json").write_text(json.dumps(manifest))
        archive = build_code_zip(staging, "synthetic-release.json", "synthetic-financial-app.zip")
        restored = self.work / "isolated-source"
        with zipfile.ZipFile(archive) as reader:
            self.assertEqual(set(reader.namelist()), set(members))
            reader.extractall(restored)  # trusted strict-builder members
        run = subprocess.run([sys.executable, "code/main.py", "--dataset", str(staging / "dataset"), "--predict-cached",
            "--cache", str(restored / "cache"), "--run-root", str(restored / "runs"), "--run-id", "reproduced"], cwd=restored,
            env={"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}, text=True, capture_output=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        reproduced = Path(json.loads(run.stdout)["run_directory"])
        self.assertEqual((run_dir / "predictions.csv").read_bytes(), (reproduced / "predictions.csv").read_bytes())
        check = verify_development_run(reproduced, dataset_root=staging / "dataset")
        self.assertTrue(check["artifact_integrity_verified"])
        self.assertFalse(check["financial_certified"])
        again = json.loads((reproduced / "manifest.json").read_text())
        self.assertEqual(dev_manifest["code_hashes"], again["code_hashes"])
        self.assertEqual(dev_manifest["request_inputs_sha256"], again["request_inputs_sha256"])
        self.assertFalse((restored / "dataset").exists())

    def test_unsafe_manifest_paths_are_refused_before_opening_private_names(self):
        directory, _ = self.prepared()
        manifest = json.loads((directory / "manifest.json").read_text())
        manifest["artifact_hashes"][".env"] = "0" * 64
        (directory / "manifest.json").write_text(json.dumps(manifest))
        original = Path.read_bytes
        def guarded(path):
            if path.name == ".env":
                self.fail("private read")
            return original(path)
        with patch.object(Path, "read_bytes", guarded), self.assertRaises(DataError):
            verify_development_run(directory)
