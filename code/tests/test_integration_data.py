import csv
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from integration_fixtures import CODE, ROOT, IntegrationCase, SAMPLE_FIELDS, SCHEMAS, prediction, write_csv
from buy_wait.data import Dataset, DataError, OUTPUT_FIELDS, REQUEST_FIELDS, load_requests, project_request, read_table
from evaluation.samples import evaluate_samples, load_sample_fixtures, persist_run


class DataTests(IntegrationCase):
    def test_input_projection_and_nested_rows_are_immutable(self):
        data = Dataset.load(self.dataset)
        requests, digest = load_requests(self.dataset)
        context = data.context_for({**requests[0], **prediction(requests[0])})
        self.assertEqual(set(context["request"]), set(REQUEST_FIELDS))
        self.assertEqual(len(digest), 64)
        with self.assertRaises(TypeError):
            context["request"]["requested_amount"] = "0"
        with self.assertRaises(TypeError):
            context["events"][0]["amount"] = "0"
        self.assertEqual(context["events"][0]["amount"], "")

    def test_user_candidates_include_unlinked_and_event_only_without_other_users(self):
        context = Dataset.load(self.dataset).context_for(self.tables["requests.csv"][0])
        self.assertEqual([r["message_id"] for r in context["message_candidates"]], ["message_1", "message_2", "message_3"])
        self.assertEqual([r["image_id"] for r in context["image_candidates"]], ["image_1"])

    def test_production_loader_does_not_read_labels_template_or_key(self):
        original = Path.read_bytes
        def guarded(path):
            if path.name in ("sample_requests.csv", "output.csv", ".env"):
                raise AssertionError("forbidden file read")
            return original(path)
        with patch.object(Path, "read_bytes", guarded):
            data = Dataset.load(self.dataset)
            rows, _ = load_requests(self.dataset)
            for row in rows:
                data.context_for(row)

    def test_all_25_public_fixtures_project_only_inputs_without_predictions(self):
        requests, expected, _ = load_sample_fixtures(ROOT / "dataset")
        self.assertEqual(len(requests), 25)
        data = Dataset.load(ROOT / "dataset")
        for req, answer in zip(requests, expected):
            with self.subTest(request_id=req["request_id"]):
                self.assertEqual(set(req), set(REQUEST_FIELDS))
                self.assertEqual(set(answer), set(OUTPUT_FIELDS))
                self.assertEqual(set(data.context_for(req)["request"]), set(REQUEST_FIELDS))

    def test_poison_all_seven_labels_cannot_change_decider_input_or_predictions(self):
        seen = []
        def decide(context):
            seen.append(dict(context["request"]))
            return prediction(context["request"])  # synthetic test double, no financial claims
        before = evaluate_samples(self.dataset, decide)
        original_inputs = list(seen)
        rows = [{**r, **prediction(r)} for r in self.tables["requests.csv"]]
        for row in rows:
            row.update(amount_safe_to_pay="0", affordability_status="not_affordable",
                       recommended_payment_method="not_recommended", payment_plan="none",
                       earliest_date_for_full_payment="", spending_changes_needed="stop:event_1",
                       decision_explanation="POISON_LABEL_ONLY")
        write_csv(self.dataset / "sample_requests.csv", SAMPLE_FIELDS, rows)
        seen.clear()
        after = evaluate_samples(self.dataset, decide)
        self.assertEqual(seen, original_inputs)
        self.assertEqual(before["predictions"], after["predictions"])
        self.assertNotEqual(before["metrics"]["counts"], after["metrics"]["counts"])

    def test_input_schema_and_boolean_calendar_checks(self):
        row = dict(self.tables["requests.csv"][0])
        for change in ({"allows_partial_payment": "yes"}, {"request_date": "2030-02-30"},
                       {"desired_completion_date": "2029-12-31"}, {"request_text": ""}):
            with self.subTest(change=change), self.assertRaises(DataError):
                project_request({**row, **change})
        path = self.dataset / "requests.csv"
        path.write_text("request_id,request_id\na,a\n")
        with self.assertRaises(DataError):
            load_requests(self.dataset)

    def test_duplicate_request_and_extra_csv_cells_rejected(self):
        requests = self.tables["requests.csv"]
        write_csv(self.dataset / "requests.csv", REQUEST_FIELDS, [requests[0], requests[0]])
        with self.assertRaises(DataError):
            load_requests(self.dataset)
        write_csv(self.dataset / "requests.csv", REQUEST_FIELDS, requests)
        with (self.dataset / "requests.csv").open("a") as stream:
            stream.write("," * len(REQUEST_FIELDS) + "\n")
        with self.assertRaises(DataError):
            load_requests(self.dataset)

    def test_csv_symlink_and_arbitrary_path_rejected_before_read(self):
        target = self.work / "synthetic.env"
        target.write_text("SYNTHETIC_NOT_A_CREDENTIAL")
        path = self.dataset / "financial_profiles.csv"
        path.unlink()
        path.symlink_to(target)
        with self.assertRaises(DataError):
            Dataset.load(self.dataset)
        with self.assertRaises(DataError):
            read_table(self.dataset, "../synthetic.env")

    def test_cross_user_and_dangling_event_links_rejected(self):
        for target in ("missing", "event_1"):
            rows = [dict(r) for r in self.tables["messages.csv"]]
            rows[3]["related_event_id"] = target
            write_csv(self.dataset / "messages.csv", SCHEMAS["messages.csv"], rows)
            with self.subTest(target=target), self.assertRaises(DataError):
                Dataset.load(self.dataset)

    def test_lifecycle_cycle_and_duplicate_rate_rejected(self):
        rows = [dict(r) for r in self.tables["financial_events.csv"]]
        rows[0]["linked_event_id"] = "event_2"
        rows[1]["linked_event_id"] = "event_1"
        write_csv(self.dataset / "financial_events.csv", SCHEMAS["financial_events.csv"], rows)
        with self.assertRaises(DataError):
            Dataset.load(self.dataset)
        write_csv(self.dataset / "financial_events.csv", SCHEMAS["financial_events.csv"], self.tables["financial_events.csv"])
        rates = self.tables["exchange_rates.csv"] * 2
        write_csv(self.dataset / "exchange_rates.csv", SCHEMAS["exchange_rates.csv"], rates)
        with self.assertRaises(DataError):
            Dataset.load(self.dataset)

    def test_cross_user_request_link_is_not_silently_dropped(self):
        rows = [dict(r) for r in self.tables["messages.csv"]]
        rows[3]["request_id"] = "request_1"
        write_csv(self.dataset / "messages.csv", SCHEMAS["messages.csv"], rows)
        data = Dataset.load(self.dataset)
        with self.assertRaises(DataError):
            data.context_for(self.tables["requests.csv"][0])

    def test_decision_failure_is_counted_without_echoing_exception_payload(self):
        def fail(_):
            raise RuntimeError("SYNTHETIC_SECRET_MUST_NOT_APPEAR")
        result = evaluate_samples(self.dataset, fail)
        self.assertEqual(result["metrics"]["denominator_requests"], 2)
        self.assertEqual(result["metrics"]["counts"]["missing_or_duplicate_rows"], 2)
        self.assertNotIn("SYNTHETIC_SECRET", json.dumps(result))

    def test_baseline_and_iteration_are_new_directories_never_overwritten(self):
        result = evaluate_samples(self.dataset, lambda ctx: prediction(ctx["request"]))
        root = self.work / "runs"
        first = persist_run(result, root, "baseline", dataset_root=self.dataset, configuration={"test": True})
        original = (first / "metrics.json").read_bytes()
        with self.assertRaises(FileExistsError):
            persist_run(result, root, "baseline", dataset_root=self.dataset, configuration={})
        self.assertEqual((first / "metrics.json").read_bytes(), original)
        other = persist_run(result, root, "iteration-1", dataset_root=self.dataset, configuration={})
        self.assertEqual(json.loads((other / "manifest.json").read_text())["status"], "complete")
        for run_root, run_id in ((root, "../escape"), (self.dataset, "bad")):
            with self.assertRaises(DataError):
                persist_run(result, run_root, run_id, dataset_root=self.dataset, configuration={})

    def test_cli_input_check_and_fixture_inspection_are_read_only(self):
        before = {p.name: p.read_bytes() for p in self.dataset.iterdir()}
        for script, extra in ((CODE / "main.py", ["--check-inputs"]), (CODE / "evaluation/main.py", [])):
            completed = subprocess.run([sys.executable, str(script), "--dataset", str(self.dataset), *extra],
                                       capture_output=True, text=True, cwd=self.work)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn(json.loads(completed.stdout)["mode"], ("input-validation-only", "fixture-inspection-only"))
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.dataset.iterdir()})
        self.assertFalse((self.work / "output.csv").exists())
