"""Synthetic CSV -> authoritative Dataset/envelope/core -> actual planning.

No alternate loader, public domain type, financial simulator or label lookup.
Temporary CSVs stay under the integration fixture's checkout-local .test-tmp/.
Known upstream R1-R3 findings are not claimed fixed by these integration cases.
"""
from dataclasses import replace
from datetime import date

from integration_fixtures import IntegrationCase, SCHEMAS, blank, request, write_csv
from buy_wait.contracts import CoreContext, FactBatch, FinancialInput, ForecastPolicy, Money, Plan
from buy_wait.core import build_financial_context
from buy_wait.data import Dataset, PlanningContext, load_requests
from buy_wait.planning.evaluator import evaluate_candidate
from test_planning_replay import run_phases


class PlanningContextTests(IntegrationCase):
    def setUp(self):
        super().setUp()
        self.req = {**request(), "requested_amount": "600", "desired_completion_date": "2030-03-12"}
        for table in ("financial_events.csv", "messages.csv", "images.csv", "exchange_rates.csv"):
            self.rows(table, [])
        self.options()

    def rows(self, table, rows):
        self.tables[table] = rows
        write_csv(self.dataset / table, SCHEMAS[table], rows)

    def profile(self, **updates):
        profiles = [dict(p) for p in self.tables["financial_profiles.csv"]]
        profiles[0].update(updates)
        self.rows("financial_profiles.csv", profiles)

    def options(self, *, amount="600", installment="310", total="620", fee="20"):
        self.rows("request_payment_options.csv", [
            blank("request_payment_options.csv", payment_option_id="payment_option_1", request_id="request_1",
                  payment_method="full_payment", payment_amount=amount, number_of_payments="1",
                  first_payment_date="2030-01-01", financing_fee="0", total_payable_amount=amount),
            blank("request_payment_options.csv", payment_option_id="payment_option_2", request_id="request_1",
                  payment_method="installments", payment_amount=installment, number_of_payments="2",
                  first_payment_date="2030-01-01", payment_frequency_days="30", financing_fee=fee,
                  total_payable_amount=total),
        ])

    def event(self, eid, amount, day, *, direction="debit", status="scheduled", currency="INR", **fields):
        result = blank(
            "financial_events.csv", event_id=eid, user_id="user_1",
            event_type="income" if direction == "credit" else "expense",
            description="Confirmed regular salary" if direction == "credit" else "Synthetic housing bill",
            category="salary" if direction == "credit" else "housing", direction=direction,
            amount=amount, currency=currency, event_date="2029-12-31", settlement_date=day,
            status=status, flexibility="fixed",
        )
        result.update(fields)
        return result

    def contexts(self):
        self.rows("requests.csv", [self.req])
        requests, _ = load_requests(self.dataset)
        dataset = Dataset.load(self.dataset)
        financial = dataset.financial_input_for(requests[0])
        envelope = dataset.planning_context_for(requests[0])
        self.assertIsInstance(financial, FinancialInput)
        self.assertIsInstance(envelope, PlanningContext)
        core = build_financial_context(financial, FactBatch(financial.request_id, financial.user_id),
                                       policy=ForecastPolicy())
        self.assertIsInstance(core, CoreContext)
        return financial, envelope, core

    def test_csv_bill_salary_selects_exact_partial_with_real_context_hashes(self):
        self.rows("financial_events.csv", [self.event("bill", "600", "2030-01-02"),
                                           self.event("salary", "700", "2030-01-03", direction="credit")])
        financial, envelope, core = self.contexts()
        _, validations, _, ranked, status = run_phases(core, envelope)
        selected = ranked["winner"]["plan"]
        self.assertIsInstance(selected, Plan)
        self.assertEqual((selected.core_hash, selected.envelope_hash), (core.context_hash, envelope.context_hash))
        self.assertEqual(selected.method, "partial_payment")
        self.assertEqual([(p.date, p.amount.minor) for p in selected.payments],
                         [(date(2030, 1, 1), 10000), (date(2030, 1, 3), 50000)])
        self.assertEqual(ranked["winner"]["minimum_available"], Money("INR", 30000))
        self.assertIs(status["amount_safe_to_pay"], core.capacity.amount_safe_to_pay)
        self.assertEqual(status["affordability_status"], "affordable_with_plan")
        self.assertTrue(all(v["capacity"] is core.capacity for v in validations))
        self.assertEqual(financial.events[0].source.relative_path, "financial_events.csv")

    def test_csv_changes_are_replayed_once_without_changing_original_capacity(self):
        self.profile(expense_categories_user_is_willing_to_stop="housing",
                     payment_methods_user_will_consider="full_payment")
        self.req["desired_completion_date"] = "2030-01-01"
        self.rows("financial_events.csv", [self.event(f"history_{month}", "50", f"2029-{month:02d}-15",
                  status="settled", flexibility="stoppable") for month in (9, 10, 11)])
        _, envelope, core = self.contexts()
        saved = core.capacity
        _, _, _, ranked, status = run_phases(core, envelope)
        self.assertEqual(status["recommended_payment_method"], "full_payment")
        self.assertEqual(status["affordability_status"], "affordable_with_plan")
        self.assertEqual(status["amount_safe_to_pay"], Money("INR", 55000))
        self.assertIsNone(status["earliest_date_for_full_payment"])
        self.assertEqual([delta for _, delta in ranked["winner"]["safety"].occurrence_deltas], [-5000] * 3)
        self.assertEqual(core.capacity, saved)

    def test_csv_installment_only_preference_does_not_lower_full_capacity(self):
        self.profile(payment_methods_user_will_consider="installments")
        _, envelope, core = self.contexts()
        _, _, _, ranked, status = run_phases(core, envelope)
        self.assertEqual(ranked["winner"]["actual_total_paid"], Money("INR", 62000))
        self.assertEqual(status["recommended_payment_method"], "installments")
        self.assertEqual(status["amount_safe_to_pay"], Money("INR", 60000))
        self.assertEqual(status["earliest_date_for_full_payment"], date(2030, 1, 1))
        self.assertEqual(ranked["winner"]["plan"].payment_option_id, "payment_option_2")

    def test_real_preference_hash_change_invalidates_old_plan_without_touching_capacity(self):
        _, envelope, core = self.contexts()
        _, _, _, ranked, _ = run_phases(core, envelope)
        changed = replace(envelope, preferences=replace(envelope.preferences, methods={"installments"}))
        saved = core.capacity
        result = evaluate_candidate(ranked["winner"]["plan"], changed, core)
        self.assertEqual(result["outcome"], "invalid")
        self.assertIn("CONTEXT_HASH_MISMATCH", {issue["code"] for issue in result["issues"]})
        self.assertFalse(result["replay_performed"])
        self.assertEqual(core.capacity, saved)

    def test_csv_missing_debit_remains_unresolved_through_rank_and_status(self):
        self.rows("financial_events.csv", [self.event("unknown", "", "2030-01-02", status="pending")])
        financial, envelope, core = self.contexts()
        self.assertIsNone(financial.events[0].amount)
        _, _, _, ranked, status = run_phases(core, envelope)
        self.assertEqual(ranked["resolution"], "incomplete_evidence_or_search")
        self.assertIsNone(status["amount_safe_to_pay"])
        self.assertIsNone(status["affordability_status"])
        self.assertEqual(status["issues"][0]["code"], "DECISION_UNRESOLVED")

    def test_csv_directed_fx_is_used_by_core_not_recomputed_in_planning(self):
        self.rows("financial_events.csv", [self.event("foreign", "100", "2030-01-02", currency="USD")])
        self.rows("exchange_rates.csv", [{"rate_date": "2030-01-02", "from_currency": "USD",
                                          "to_currency": "INR", "rate": "1.333333"}])
        financial, envelope, core = self.contexts()
        _, _, _, _, status = run_phases(core, envelope)
        self.assertEqual(financial.events[0].amount, Money("USD", 10000))
        self.assertEqual(core.occurrences[0].home_amount, Money("INR", 13334))
        self.assertEqual(status["amount_safe_to_pay"], Money("INR", 56666))
        self.assertEqual(status["affordability_status"], "not_affordable")

    def test_zero_request_csv_and_supplied_offer_keep_zero_payment_legal(self):
        self.req["requested_amount"] = "0"
        self.options(amount="0", installment="0", total="0", fee="0")
        _, envelope, core = self.contexts()
        _, _, _, ranked, status = run_phases(core, envelope)
        self.assertEqual(ranked["winner"]["plan"].payments[0].amount, Money("INR", 0))
        self.assertEqual(status["amount_safe_to_pay"], Money("INR", 0))
        self.assertEqual(status["affordability_status"], "affordable_now")
