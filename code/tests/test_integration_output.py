import csv
from decimal import Decimal

from integration_fixtures import IntegrationCase, OUTPUT_FIELDS, prediction, request
from buy_wait.data import DataError
from buy_wait.output import actions, amount, schedule, structural_issues, write_output


class OutputTests(IntegrationCase):
    def test_decimal_grammar_is_exact_and_rejects_nonfinite_float_and_negatives(self):
        self.assertEqual(amount("123.40"), Decimal("123.4"))
        for value in ("NaN", "Infinity", "-1", "1e2", "1.001", " 1", 1.0, None):
            with self.subTest(value=value), self.assertRaises(DataError):
                amount(value)

    def test_chronological_schedule_and_action_grammar(self):
        self.assertEqual(len(schedule("2030-01-01:10|2030-01-04:40.00")), 2)
        self.assertEqual(actions("reduce_to:event_1:0|stop:event_2"), (("reduce_to", "event_1", Decimal(0)), ("stop", "event_2", None)))
        for value in ("", "2030-01-02:1|2030-01-01:1", "2030-01-01:-1", "2030-01-01:1:2"):
            with self.subTest(value=value), self.assertRaises(DataError):
                schedule(value)
        for value in ("", "stop:event_1|reduce_to:event_1:2", "stop:event_1:2", "reduce_to:event_1:-1",
                      "stop:e1|stop:e2|stop:e3|stop:e4", "stop:../escape"):
            with self.subTest(value=value), self.assertRaises(DataError):
                actions(value)

    def test_zero_request_does_not_gain_an_invented_positive_minimum(self):
        req = {**request(), "requested_amount": "0"}
        self.assertEqual(structural_issues(prediction(req), req), [])
        self.assertEqual(schedule("2030-01-01:0")[0][1], Decimal(0))

    def test_partial_is_prescribed_two_payment_plan_not_equal_split(self):
        req = request()
        row = {**prediction(req), "amount_safe_to_pay": "10", "affordability_status": "affordable_with_plan",
               "recommended_payment_method": "partial_payment", "payment_plan": "2030-01-01:10|2030-01-05:40",
               "earliest_date_for_full_payment": "2030-01-05"}
        self.assertEqual(structural_issues(row, req), [])
        for change in ({"payment_plan": "2030-01-01:25|2030-01-05:25"},
                       {"payment_plan": "2030-01-01:10|2030-01-04:10|2030-01-05:30"},
                       {"earliest_date_for_full_payment": "2030-01-21"}, {"amount_safe_to_pay": "0"}):
            with self.subTest(change=change):
                self.assertTrue(structural_issues({**row, **change}, req))
        self.assertTrue(structural_issues(row, {**req, "allows_partial_payment": "false"}))

    def test_installment_fee_is_not_forced_to_principal(self):
        req = request()
        row = {**prediction(req), "affordability_status": "affordable_with_plan", "recommended_payment_method": "installments",
               "payment_plan": "2030-01-01:26|2030-01-06:26"}
        self.assertEqual(structural_issues(row, req), [])
        # This says nothing about supplied-option matching or acceptance: planner owns those.
        self.assertTrue(structural_issues({**row, "payment_plan": "2030-01-01:20|2030-01-06:20"}, req))

    def test_invalid_status_bounds_dates_explanation_and_one_shot_changes(self):
        req = request()
        for change in ({"amount_safe_to_pay": "51"}, {"earliest_date_for_full_payment": "2030-04-02"},
                       {"affordability_status": "maybe"}, {"recommended_payment_method": "credit_card"},
                       {"decision_explanation": " "}, {"payment_plan": "none"},
                       {"affordability_status": "affordable_with_plan"}, {"spending_changes_needed": "stop:event_1"}):
            with self.subTest(change=change):
                self.assertTrue(structural_issues({**prediction(req), **change}, req))

    def test_safe_later_fallback_has_no_plan_or_spending_changes(self):
        req = request()
        row = {**prediction(req), "amount_safe_to_pay": "0", "affordability_status": "affordable_later",
               "recommended_payment_method": "not_recommended", "payment_plan": "none",
               "earliest_date_for_full_payment": "2030-01-25"}
        self.assertEqual(structural_issues(row, req), [])
        self.assertTrue(structural_issues({**row, "spending_changes_needed": "stop:event_1"}, req))

    def test_output_is_exact_header_input_order_quoted_and_atomic(self):
        requests = [request(2), request(1)]
        rows = [prediction(r) for r in reversed(requests)]
        rows[0]["decision_explanation"] = 'Synthetic, "quoted" explanation\nsecond line'
        path = self.work / "output.csv"
        write_output(path, rows, requests, dataset_root=self.dataset)
        with path.open(newline="") as stream:
            reader = csv.DictReader(stream)
            self.assertEqual(tuple(reader.fieldnames), OUTPUT_FIELDS)
            reread = list(reader)
        self.assertEqual([r["request_id"] for r in reread], ["request_2", "request_1"])
        self.assertEqual(reread[1]["decision_explanation"], rows[0]["decision_explanation"])
        original = path.read_bytes()
        rows[0]["amount_safe_to_pay"] = "999"
        with self.assertRaises(DataError):
            write_output(path, rows, requests, dataset_root=self.dataset)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(self.work.glob(".bw-output-*")), [])

    def test_duplicates_missing_extra_and_dataset_overwrite_are_rejected(self):
        requests = [request(1), request(2)]
        rows = [prediction(r) for r in requests]
        for invalid in (rows[:1], [rows[0], rows[0]], [rows[0], prediction(request(3))]):
            with self.subTest(invalid=invalid), self.assertRaises(DataError):
                write_output(self.work / "output.csv", invalid, requests, dataset_root=self.dataset)
        original = (self.dataset / "output.csv").read_bytes()
        with self.assertRaises(DataError):
            write_output(self.dataset / "output.csv", rows, requests, dataset_root=self.dataset)
        self.assertEqual((self.dataset / "output.csv").read_bytes(), original)
        alias = self.work / "alias.csv"
        alias.symlink_to(self.dataset / "output.csv")
        with self.assertRaises(DataError):
            write_output(alias, rows, requests, dataset_root=self.dataset)
