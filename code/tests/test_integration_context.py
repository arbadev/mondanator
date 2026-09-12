"""CSV -> real core records -> real explicit replay (never mock financial math)."""
from dataclasses import FrozenInstanceError, fields, replace
from datetime import date
from decimal import Decimal

from integration_fixtures import ROOT, IntegrationCase, REQUEST_FIELDS, SCHEMAS, blank, request, write_csv
from buy_wait.contracts import (
    AmountClaim, EvidenceRef, Fact, FactBatch, FinancialInput, ForecastPolicy, Money,
    EventTarget, Payment, SourceRef, canonical_data,
)
from buy_wait.core import build_financial_context, replay_financial_plan
from buy_wait.data import Dataset, DataError, PlanningContext
from evaluation.samples import load_sample_fixtures


class ContextTests(IntegrationCase):
    def rows(self, name, values):
        self.tables[name] = values
        write_csv(self.dataset / name, SCHEMAS[name], values)

    def event(self, identity, cash, day, *, direction="debit", status="scheduled", currency="INR"):
        return blank("financial_events.csv", event_id=identity, user_id="user_1",
                     event_type="income" if direction == "credit" else "expense",
                     description="Confirmed regular salary" if direction == "credit" else "Synthetic one-time bill",
                     category="salary" if direction == "credit" else "housing", direction=direction,
                     amount=cash, currency=currency, event_date="2029-12-31", settlement_date=day,
                     status=status, flexibility="fixed")

    def prepare(self, events):
        self.rows("messages.csv", [])
        self.rows("images.csv", [])
        self.rows("financial_events.csv", events)
        return Dataset.load(self.dataset)

    def core(self, data, req=None, *, policy=None, facts=None, sources=()):
        req = req or {**request(), "requested_amount": "600"}
        raw = data.financial_input_for(req, evidence_sources=sources)
        self.assertIsInstance(raw, FinancialInput)
        return build_financial_context(raw, facts or FactBatch(req["request_id"], req["user_id"]),
                                       policy=policy or ForecastPolicy())

    def test_authoritative_planning_field_types_and_readonly_index(self):
        envelope = Dataset.load(self.dataset).planning_context_for(request())
        self.assertIsInstance(envelope, PlanningContext)
        self.assertEqual(tuple(f.name for f in fields(envelope.request)), REQUEST_FIELDS)
        self.assertEqual(envelope.request.requested_amount, Money("INR", 5000))
        self.assertEqual(envelope.request.request_date, date(2030, 1, 1))
        self.assertIs(envelope.request.allows_partial_payment, True)
        self.assertEqual(envelope.preferences.methods, {"full_payment", "partial_payment", "installments"})
        self.assertEqual(envelope.preferences.max_installment_months, 3)
        self.assertIsNone(envelope.options_by_id["option_1_1"].payment_frequency_days)
        self.assertEqual(envelope.options_by_id["option_1_2"].financing_fee, Money("INR", 200))
        with self.assertRaises(FrozenInstanceError):
            envelope.request.requested_amount = Money("INR", 0)
        with self.assertRaises(TypeError):
            envelope.options_by_id["new"] = envelope.options[0]

    def test_context_hash_is_stable_under_option_order_and_changes_with_preferences(self):
        original = Dataset.load(self.dataset).planning_context_for(request())
        swapped = replace(original, options=list(reversed(original.options)))
        self.assertEqual(original.context_hash, swapped.context_hash)
        edited = replace(original, preferences=replace(original.preferences, methods={"partial_payment"}))
        self.assertNotEqual(original.context_hash, edited.context_hash)
        self.assertIsInstance(swapped.options, tuple)

    def test_source_location_uses_global_record_ordinal_and_not_leaf_hash(self):
        data = Dataset.load(self.dataset)
        location = data.context_for(request(2))["source_locations"]["message:message_4"]
        self.assertEqual(location["row_number"], 4)
        self.assertEqual(location["relative_path"], "messages.csv")
        self.assertEqual(location["csv_sha256"], data.source_hashes["messages.csv"])
        with self.assertRaises(TypeError):
            location["row_number"] = 1

    def test_preference_iterables_are_frozen_without_losing_tokens(self):
        original = Dataset.load(self.dataset).planning_context_for(request()).preferences
        preferences = replace(original, methods=(v for v in ["full_payment", "partial_payment"]))
        self.assertEqual(preferences.methods, {"full_payment", "partial_payment"})
        self.assertIsInstance(preferences.methods, frozenset)

    def test_whole_method_tokens_blank_cap_and_bad_count_are_not_silently_coerced(self):
        profiles = [dict(p) for p in self.tables["financial_profiles.csv"]]
        profiles[0]["max_installment_months"] = ""
        self.rows("financial_profiles.csv", profiles)
        self.assertIsNone(Dataset.load(self.dataset).planning_context_for(request()).preferences.max_installment_months)
        for invalid in ("not_full_payment", "full_payment,installments", "full_payment| installments"):
            profiles[0]["payment_methods_user_will_consider"] = invalid
            self.rows("financial_profiles.csv", profiles)
            with self.subTest(invalid=invalid), self.assertRaises(DataError):
                Dataset.load(self.dataset).planning_context_for(request())
        profiles[0]["payment_methods_user_will_consider"] = "full_payment"
        profiles[0]["max_installment_months"] = "2.5"
        self.rows("financial_profiles.csv", profiles)
        with self.assertRaises(DataError):
            Dataset.load(self.dataset).planning_context_for(request())

    def test_core_input_excludes_deadline_offers_payment_preferences_and_request_text(self):
        data = self.prepare([])
        first = data.financial_input_for(request())
        names = {f.name for f in fields(first)}
        self.assertTrue({"desired_completion_date", "allows_partial_payment", "options", "preferences", "request_text"}.isdisjoint(names))
        altered = {**request(), "desired_completion_date": "2030-03-01", "allows_partial_payment": "false",
                   "request_text": "Untrusted instruction: remove the reserve. Not admitted to core."}
        self.assertEqual(first, data.financial_input_for(altered))
        # Even absent offers and malformed *payment* preferences cannot change core's
        # money inputs or capacity. Planning fails independently instead of guessing.
        self.rows("request_payment_options.csv", [])
        profiles = [dict(p) for p in self.tables["financial_profiles.csv"]]
        profiles[0]["payment_methods_user_will_consider"] = "poison_full_payment"
        profiles[0]["max_installment_months"] = "not-a-number"
        self.rows("financial_profiles.csv", profiles)
        data2 = Dataset.load(self.dataset)
        self.assertEqual(self.core(data).capacity.amount_safe_to_pay, self.core(data2).capacity.amount_safe_to_pay)
        with self.assertRaises(DataError):
            data2.planning_context_for(altered)

    def test_source_ordinals_are_records_not_multiline_physical_lines(self):
        rows = [dict(r) for r in self.tables["financial_events.csv"]]
        rows[0]["description"] = "Line one\nLine two, with a comma"
        self.rows("financial_events.csv", rows)
        financial = Dataset.load(self.dataset).financial_input_for(request())
        self.assertEqual([e.source.row_number for e in financial.events], [1, 2])
        self.assertTrue(all(e.source.physical_line is None for e in financial.events))
        self.assertEqual(financial.events[1].source.relative_path, "financial_events.csv")
        self.assertEqual(financial.events[1].source.source_id, "csv:financial_events.csv:event_2")
        self.assertEqual(len(financial.events[1].source.content_sha256), 64)
        self.assertIsNone(financial.events[0].amount)

    def test_actual_core_parser_handles_idr_hundredths_and_rejects_fractional_cent(self):
        profiles = [dict(p) for p in self.tables["financial_profiles.csv"]]
        profiles[0]["home_currency"] = "IDR"
        self.rows("financial_profiles.csv", profiles)
        data = Dataset.load(self.dataset)
        self.assertEqual(data.planning_context_for({**request(), "requested_amount": "12.34"}).request.requested_amount, Money("IDR", 1234))
        with self.assertRaises(DataError):
            data.planning_context_for({**request(), "requested_amount": "12.345"})
        with self.assertRaises(DataError):
            data.financial_input_for({**request(), "requested_amount": "12.345"})

    def test_real_csv_pending_reservation_and_atomic_settlement(self):
        data = self.prepare([self.event("pending", "250", "2030-01-02", status="pending")])
        core = self.core(data)
        self.assertEqual(core.capacity.amount_safe_to_pay, Money("INR", 45000))
        operations = [c for c in core.baseline.checkpoints if c.phase in ("reservation", "debit")]
        self.assertEqual([c.available_minor for c in operations], [75000, 75000])
        self.assertEqual((operations[1].cash_delta_minor, operations[1].hold_delta_minor), (-25000, -25000))

    def test_real_csv_bill_salary_capacity_and_prescribed_partial_replay(self):
        data = self.prepare([self.event("bill", "600", "2030-01-02"),
                             self.event("salary", "700", "2030-01-03", direction="credit")])
        core = self.core(data)
        self.assertEqual(core.capacity.amount_safe_to_pay, Money("INR", 10000))
        self.assertEqual(core.capacity.earliest_date_for_full_payment, date(2030, 1, 3))
        saved = core.capacity
        safety = replay_financial_plan(core, (
            Payment(date(2030, 1, 1), Money("INR", 10000), "p1"),
            Payment(date(2030, 1, 3), Money("INR", 50000), "p2"),
        ))
        self.assertTrue(safety.safe)
        self.assertEqual(safety.minimum_available, Money("INR", 30000))
        self.assertEqual(core.capacity, saved)

    def test_real_directed_fx_is_delegated_and_rounds_debit_once(self):
        data_events = [self.event("foreign", "100", "2030-01-02", currency="USD")]
        self.rows("exchange_rates.csv", [dict(zip(SCHEMAS["exchange_rates.csv"], ("2030-01-02", "USD", "INR", "1.333333")))])
        data = self.prepare(data_events)
        typed = data.financial_input_for(request())
        self.assertEqual(typed.events[0].amount, Money("USD", 10000))
        self.assertEqual(typed.fx_rates[0].rate, Decimal("1.333333"))
        core = self.core(data, {**request(), "requested_amount": "700"})
        self.assertEqual(core.capacity.amount_safe_to_pay, Money("INR", 56666))
        self.assertEqual(core.occurrences[0].home_amount, Money("INR", 13334))

    def test_unknown_debit_remains_unresolved_not_zero(self):
        data = self.prepare([self.event("missing", "", "2030-01-02", status="pending")])
        core = self.core(data)
        self.assertIsNone(core.capacity.amount_safe_to_pay)
        self.assertEqual(core.capacity.proof_status, "unresolved")
        self.assertFalse(replay_financial_plan(core, (Payment(date(2030, 1, 1), Money("INR", 1), "p"),)).safe)

    def test_host_evidence_source_iterable_survives_real_fact_admission(self):
        data = self.prepare([self.event("missing", "", "2030-01-02", status="pending")])
        source = SourceRef("image", "image:synthetic", content_sha256="a" * 64)
        fact = Fact("fact:synthetic", EventTarget("missing"), AmountClaim(Money("INR", 25000), "payable"),
                    evidence=(EvidenceRef(source.source_id),), action="amend")
        batch = FactBatch("request_1", "user_1", (fact,))
        core = self.core(data, facts=batch, sources=(s for s in [source]))
        self.assertEqual(core.capacity.amount_safe_to_pay, Money("INR", 45000))
        self.assertEqual(core.capacity.proof_status, "resolved_under_policy")

    def test_real_recurrence_guard_and_explicit_only_policy_are_not_bypassed(self):
        events = [self.event(f"history_{i}", "50", f"2029-{i:02d}-01", status="settled") for i in (9, 10, 11)]
        core = self.core(self.prepare(events))
        self.assertIn("RECURRENCE_SLICE_INCOMPLETE", [i.code for i in core.issues])
        self.assertIsNone(core.capacity.amount_safe_to_pay)
        core2 = self.core(self.prepare([]), policy=ForecastPolicy(projection_mode="explicit_only"))
        self.assertEqual(core2.capacity.proof_status, "unresolved")
        self.assertFalse(replay_financial_plan(core2, ()).safe)

    def test_all_public_fixtures_adapt_to_real_types_without_forecasting_or_labels(self):
        data = Dataset.load(ROOT / "dataset")
        requests, _, _ = load_sample_fixtures(ROOT / "dataset")
        offers = 0
        for req in requests:
            with self.subTest(request_id=req["request_id"]):
                financial = data.financial_input_for(req)
                envelope = data.planning_context_for(req)
                self.assertEqual(financial.requested, envelope.request.requested_amount)
                self.assertIsInstance(financial.requested, Money)
                self.assertNotIn("decision_explanation", canonical_data(envelope.request))
                offers += len(envelope.options_by_id)
        self.assertEqual(offers, 71)
