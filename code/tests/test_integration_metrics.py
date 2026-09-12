import json
import subprocess
import sys
from decimal import Decimal

from integration_fixtures import CODE, IntegrationCase, OUTPUT_FIELDS, prediction, request, write_csv
from buy_wait.data import DataError
from evaluation.metrics import compare_predictions


class MetricTests(IntegrationCase):
    def compare(self, predictions, *, expected=None, audits=None):
        requests = [request(1), request(2)]
        return compare_predictions(requests, expected or [prediction(r) for r in requests], predictions,
                                   {"request_1": "INR", "request_2": "USD"}, audits=audits)

    def test_amount_errors_are_signed_normalized_and_currency_separated(self):
        rows = [{**prediction(request(1)), "amount_safe_to_pay": "40"},
                {**prediction(request(2)), "amount_safe_to_pay": "100.25"}]
        result = self.compare(rows)
        metrics = result["metrics"]
        self.assertEqual(metrics["denominator_requests"], 2)
        self.assertEqual(metrics["counts"]["amount_overestimates"], 1)
        self.assertEqual(metrics["counts"]["amount_underestimates"], 1)
        self.assertEqual(Decimal(metrics["amount_absolute_error_by_currency"]["INR"]["mean"]), Decimal(10))
        self.assertEqual(Decimal(metrics["amount_absolute_error_by_currency"]["USD"]["mean"]), Decimal("0.25"))
        self.assertEqual(Decimal(metrics["amount_normalized_absolute_error"]["mean"]), Decimal("0.10125"))
        self.assertEqual(result["per_request"][0]["metrics"]["amount_signed_error"], "-10")

    def test_invalid_or_missing_amount_never_becomes_zero_error(self):
        result = self.compare([{**prediction(request(1)), "amount_safe_to_pay": "NaN"}])
        self.assertEqual(result["metrics"]["counts"]["amount_invalid_or_missing"], 2)
        self.assertIsNone(result["metrics"]["amount_normalized_absolute_error"]["mean"])
        self.assertEqual(result["metrics"]["counts"]["status_method_joint_matches"], 1)
        self.assertEqual(result["metrics"]["counts"]["missing_or_duplicate_rows"], 1)

    def test_dates_keep_empty_and_day_error_dimensions_distinct(self):
        expected = [prediction(request(1)), {**prediction(request(2)), "earliest_date_for_full_payment": ""}]
        actual = [{**prediction(request(1)), "earliest_date_for_full_payment": "2030-01-03"}, prediction(request(2))]
        metrics = self.compare(actual, expected=expected)["metrics"]
        self.assertEqual(metrics["date_states"]["both_dates"], 1)
        self.assertEqual(metrics["date_states"]["prediction_date_expected_empty"], 1)
        self.assertEqual(Decimal(metrics["date_absolute_days"]["mean"]), Decimal(2))
        self.assertEqual(metrics["date_absolute_days"]["count"], 1)
        actual[0]["earliest_date_for_full_payment"] = "2030-02-30"
        self.assertEqual(self.compare(actual, expected=expected)["metrics"]["date_states"]["invalid_or_missing"], 1)

    def test_duplicates_and_unexpected_ids_receive_no_credit(self):
        rows = [prediction(request(1)), prediction(request(1)), prediction(request(3))]
        metrics = self.compare(rows)["metrics"]
        self.assertEqual(metrics["coverage"], {"received_unique_known_rows": 0, "duplicate_ids": 1, "unexpected_rows": 1})
        self.assertEqual(metrics["counts"]["status_method_joint_matches"], 0)
        self.assertEqual(metrics["status_confusion"]["affordable_now"]["<invalid>"], 2)

    def test_numeric_schedule_equality_action_order_and_no_literal_explanation_match(self):
        expected = [prediction(request(1)), prediction(request(2))]
        expected[0]["spending_changes_needed"] = "stop:event_1|reduce_to:event_2:10"
        actual = [dict(r) for r in expected]
        actual[0].update(payment_plan="2030-01-01:50.00", spending_changes_needed="reduce_to:event_2:10.00|stop:event_1",
                         decision_explanation="Entirely different wording, deliberately not scored literally.")
        counts = self.compare(actual, expected=expected)["metrics"]["counts"]
        self.assertEqual(counts["payment_plan_reference_matches"], 2)
        self.assertEqual(counts["spending_changes_needed_reference_matches"], 2)
        self.assertNotIn("decision_explanation_matches", counts)

    def test_reference_agreement_never_implies_safety_or_grounding(self):
        rows = [prediction(request(1)), prediction(request(2))]
        metrics = self.compare(rows)["metrics"]
        self.assertEqual(metrics["counts"]["status_method_joint_matches"], 2)
        for dimension in metrics["audit_dimensions"].values():
            self.assertEqual(dimension["checked"], 0)
            self.assertEqual(dimension["not_checked"], 2)

    def test_audit_adapter_evidence_is_distinct_from_csv_agreement(self):
        rows = [prediction(request(1)), prediction(request(2))]
        audits = {"request_1": {"safety": {"checked": True, "violations": ["synthetic reserve violation"]},
                                "explanation_grounding": {"checked": True, "violations": ["synthetic unsupported claim"]}}}
        metrics = self.compare(rows, audits=audits)["metrics"]
        self.assertEqual(metrics["audit_dimensions"]["safety"]["violations"], 1)
        self.assertEqual(metrics["audit_dimensions"]["safety"]["not_checked"], 1)
        self.assertEqual(metrics["audit_dimensions"]["explanation_grounding"]["violations"], 1)
        with self.assertRaises(DataError):
            self.compare(rows, audits={"unknown": {}})

    def test_mismatch_detector_does_not_invent_financial_root_cause(self):
        rows = [{**prediction(request(1)), "amount_safe_to_pay": "40"}, prediction(request(2))]
        errors = self.compare(rows)["errors"]
        numeric = [e for e in errors if e["field"] == "amount_safe_to_pay"]
        self.assertTrue(numeric)
        self.assertIsNone(numeric[0]["root_cause"])
        self.assertIsNone(numeric[0]["general_fix"])
        self.assertIsNone(numeric[0]["regression_id"])
        self.assertEqual(numeric[0]["state"], "open")

    def test_cli_persists_metrics_for_existing_synthetic_predictions(self):
        path = self.work / "predictions.csv"
        write_csv(path, OUTPUT_FIELDS, [prediction(request(1)), prediction(request(2))])
        command = [sys.executable, str(CODE / "evaluation/main.py"), "--dataset", str(self.dataset),
                   "--predictions", str(path), "--run-root", str(self.work / "runs"), "--run-id", "baseline"]
        completed = subprocess.run(command, capture_output=True, text=True, cwd=self.work)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["metrics"]["denominator_requests"], 2)
        self.assertTrue((self.work / "runs/baseline/errors.jsonl").is_file())
        again = subprocess.run(command, capture_output=True, text=True, cwd=self.work)
        self.assertNotEqual(again.returncode, 0)
