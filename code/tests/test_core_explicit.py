"""Executable analytic/I011 regressions; no sample or evaluation answer lookup."""
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from itertools import permutations
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from buy_wait.contracts import (
    AmountCandidate, AmountClaim, CancelClaim, ChangeTarget, DateClaim, EvidenceIssue,
    EvidenceRef, EventRecord, EventTarget, Fact, FactBatch, FinancialInput,
    FinancialProfile, ForecastPolicy, FxRate, IncomeRoleClaim, Money, Payment, ReduceTo,
    RelationClaim, SourceRef, StateClaim, Stop, canonical_data,
)
from buy_wait.core import build_financial_context, compute_capacity, replay_financial_plan
from buy_wait.core.forecast import context_fingerprint
from buy_wait.core.money import FxTable, MoneyError, format_money, parse_day, parse_money, parse_optional_money

R = date(2026, 2, 1)
POLICY = ForecastPolicy()


def money(value, currency="USD"):
    return parse_money(str(value), currency)


def event(eid, amount, day, *, direction="debit", status="scheduled", category="rent",
          currency="USD", linked=None, event_type=None):
    return EventRecord(eid, "u", event_type or ("income" if direction == "credit" else "expense"),
                       "Regular salary" if direction == "credit" else "Recurring bill", category,
                       direction, money(amount, currency) if amount is not None else None, currency,
                       R - timedelta(days=1), R + timedelta(days=day) if day is not None else None,
                       status, SourceRef("csv", "csv:" + eid), linked_event_id=linked)


def build(events=(), *, balance="1000", minimum="300", requested="600", facts=(), sources=(),
          rates=(), issues=(), included=(), policy=POLICY):
    profile = FinancialProfile("u", "USD", money(balance), money(minimum))
    data = FinancialInput("r", "u", R, money(requested), profile, tuple(events), tuple(rates), tuple(sources), tuple(included))
    batch = FactBatch("r", "u", tuple(facts), tuple(issues))
    return build_financial_context(data, batch, policy=policy)


def source(sid="message:test", *, actor="issuer", known=None, kind="message"):
    return SourceRef(kind, sid, actor_key=actor,
                     known_at=known or (datetime(2026, 2, 1, tzinfo=timezone.utc) if kind != "image" else None))


def fact(fid, eid, claim, src, **kwargs):
    return Fact(fid, EventTarget(eid), claim, evidence=(EvidenceRef(src.source_id, "amount"),), **kwargs)


class MoneyTests(unittest.TestCase):
    def test_exact_parse_format_idr_and_immutable_money(self):
        self.assertEqual(money("12.34", "IDR"), Money("IDR", 1234))
        self.assertEqual(format_money(money("12.3400")), "12.34")
        self.assertEqual(canonical_data(money("0")), {"currency": "USD", "amount": "0.00"})
        with self.assertRaises(FrozenInstanceError):
            money("1").minor = 200
        for invalid in ("", "NaN", "Infinity", "-1", "0.001", "1e2", "1,000"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                money(invalid)
        with self.assertRaises(ValueError):
            Money("USD", True)
        self.assertIsNone(parse_optional_money("", "USD"))

    def test_date_is_strict(self):
        self.assertEqual(parse_day("2026-02-01"), R)
        for invalid in ("2026-2-1", "2026-02-30", "2026-02-01T00:00:00"):
            with self.assertRaises(ValueError):
                parse_day(invalid)

    def test_directed_dated_fx_rounds_once_without_decimal_context_dependence(self):
        fx = FxTable((FxRate(R, "EUR", "USD", Decimal("1.333333"), SourceRef("csv", "fx")),))
        with localcontext() as context:
            context.prec = 2
            debit, ref = fx.convert(money("100", "EUR"), "USD", R, direction="debit")
            credit, _ = fx.convert(money("100", "EUR"), "USD", R, direction="credit")
        self.assertEqual((debit, credit, ref), (money("133.34"), money("133.33"), "fx"))
        with self.assertRaises(MoneyError):
            fx.convert(money("100", "USD"), "EUR", R, direction="debit")
        with self.assertRaises(MoneyError):
            fx.convert(money("100", "EUR"), "USD", R + timedelta(days=1), direction="debit")

    def test_duplicate_rates_are_not_silently_overwritten(self):
        row = FxRate(R, "EUR", "USD", Decimal("1.1"), SourceRef("csv", "fx"))
        with self.assertRaises(MoneyError):
            FxTable((row, row))


class ExplicitLedgerTests(unittest.TestCase):
    def test_pending_debit_once_and_atomic_settlement(self):
        core = build((event("p", 250, 1, status="pending"),))
        self.assertEqual(core.capacity.amount_safe_to_pay, money(450))
        self.assertIsNone(core.capacity.earliest_date_for_full_payment)
        operations = [c for c in core.baseline.checkpoints if c.phase in ("reservation", "debit")]
        self.assertEqual([c.available_minor for c in operations], [75000, 75000])
        self.assertEqual((operations[1].cash_delta_minor, operations[1].hold_delta_minor), (-25000, -25000))
        self.assertEqual(core.context_hash, context_fingerprint(core))

    def test_already_included_hold_is_not_reserved_again(self):
        core = build((event("p", 250, 1, status="pending"),), balance=750, included=("p",))
        self.assertEqual(core.capacity.amount_safe_to_pay, money(450))
        self.assertFalse(any(c.phase == "reservation" for c in core.baseline.checkpoints))
        self.assertEqual(core.baseline.checkpoints[0].available_minor, 75000)

    def test_unknown_pending_home_settlement_reserves_without_inventing_a_date(self):
        core = build((event("p", 250, None, status="pending"),))
        self.assertEqual(core.capacity.amount_safe_to_pay, money(450))
        self.assertIsNone(core.occurrences[0].cash_date)
        self.assertFalse(any(c.phase == "debit" for c in core.baseline.checkpoints))
        self.assertEqual(core.baseline.checkpoints[-1].holds_minor, 25000)

    def test_historical_cash_is_not_replayed(self):
        core = build((event("salary", 500, -2, direction="credit", category="salary", status="settled"),
                      event("bill", 100, -1, status="settled")), requested=800)
        self.assertEqual(core.capacity.amount_safe_to_pay, money(700))
        self.assertEqual(core.occurrences, ())
        self.assertTrue(all(r.disposition == "anchored_history" for r in core.resolved_events))

    def test_bill_before_salary_and_exact_partial_coherence(self):
        core = build((event("bill", 600, 1), event("salary", 700, 2, direction="credit", category="salary")))
        self.assertEqual(core.capacity.amount_safe_to_pay, money(100))
        self.assertEqual(core.capacity.earliest_date_for_full_payment, R + timedelta(days=2))
        saved = core.capacity
        partial = replay_financial_plan(core, (Payment(R, money(100), "p1"), Payment(R + timedelta(days=2), money(500), "p2")))
        self.assertTrue(partial.safe)
        self.assertEqual(partial.minimum_available, money(300))
        self.assertEqual(core.capacity, saved)

    def test_same_day_slot_uses_posted_income_not_pre_slot_purchase_subtraction(self):
        flows = (event("bill", 100, 0), event("salary", 400, 0, direction="credit", category="salary"))
        core = build(flows, balance=600, requested=500)
        self.assertEqual(core.capacity.amount_safe_to_pay, money(500))
        self.assertEqual(core.capacity.earliest_date_for_full_payment, R)
        self.assertTrue(replay_financial_plan(core, (Payment(R, money(500), "p"),)).safe)
        unsafe = build(flows, balance=350, requested=500)
        self.assertEqual(unsafe.capacity.amount_safe_to_pay, money(0))
        self.assertFalse(unsafe.capacity.baseline_feasible)
        self.assertIsNone(unsafe.capacity.earliest_date_for_full_payment)

    def test_baseline_shortfall_is_not_cured_by_later_recovery(self):
        core = build((event("bill", 100, 1), event("salary", 300, 1, direction="credit", category="salary")), balance=300, requested=100)
        self.assertEqual(core.capacity.amount_safe_to_pay, money(0))
        self.assertIsNone(core.capacity.earliest_date_for_full_payment)
        self.assertFalse(replay_financial_plan(core, (Payment(R + timedelta(days=2), money(1), "p"),)).safe)

    def test_day_90_included_day_91_excluded_but_pending_still_reserved(self):
        self.assertEqual(build((event("bill", 100, 90),), requested=700).capacity.amount_safe_to_pay, money(600))
        self.assertEqual(build((event("bill", 100, 91),), requested=700).capacity.amount_safe_to_pay, money(700))
        self.assertEqual(build((event("bill", 100, 91, status="pending"),), requested=700).capacity.amount_safe_to_pay, money(600))

    def test_pending_credits_and_unrealized_values_are_never_spendable(self):
        core = build((event("refund", 500, 1, direction="credit", status="pending", category="shopping", event_type="refund"),
                      event("value", 900, None, direction="non_cash", status="unrealized", category="investment", event_type="investment_valuation")), balance=500)
        self.assertEqual(core.capacity.amount_safe_to_pay, money(200))
        self.assertEqual(core.occurrences, ())

    def test_missing_debit_is_unknown_not_zero(self):
        core = build((event("bill", None, 1),))
        self.assertEqual(core.capacity.proof_status, "unresolved")
        self.assertIsNone(core.capacity.amount_safe_to_pay)
        self.assertIsNone(core.capacity.baseline_feasible)
        self.assertFalse(replay_financial_plan(core, (Payment(R, money(1), "p"),)).safe)

    def test_missing_foreign_salary_fx_is_a_conservative_bound_not_unknown_debit(self):
        credit = event("salary", 500, 1, direction="credit", category="salary", currency="EUR")
        core = build((credit,))
        self.assertEqual(core.capacity.proof_status, "conservative_bound")
        self.assertEqual(core.capacity.amount_safe_to_pay, money(600))
        self.assertTrue(replay_financial_plan(core, (Payment(R, money(600), "p"),)).safe)

    def test_plan_money_sums_three_3500_not_double_charged_fee(self):
        core = build(requested=105)
        safety = replay_financial_plan(core, tuple(Payment(R + timedelta(days=i), Money("USD", 3500), str(i)) for i in range(3)))
        self.assertTrue(safety.safe)
        self.assertEqual(safety.minimum_available, money(895))
        self.assertEqual(sum(c.cash_delta_minor for c in safety.forecast.checkpoints if c.phase == "payment"), -10500)

    def test_stale_context_wrong_currency_and_late_plans_fail(self):
        core = build()
        stale = replace(core, requested=money(1))
        self.assertIn("STALE_CORE_CONTEXT", replay_financial_plan(stale, ()).reason_codes)
        self.assertFalse(replay_financial_plan(core, (Payment(R, money(1, "EUR"), "x"),)).safe)
        self.assertFalse(replay_financial_plan(core, (Payment(R + timedelta(days=91), money(1), "x"),)).safe)
        with self.assertRaises(ValueError):
            compute_capacity(core.baseline, money(1, "EUR"), 30000, request_date=R)


class FactCompatibilityTests(unittest.TestCase):
    def test_explicit_amendment_needs_no_predecessor_fact_id(self):
        src = source()
        amended = fact("amend", "bill", AmountClaim(money(120), "payable"), src, action="amend")
        core = build((event("bill", 100, 1),), requested=900, facts=(amended,), sources=(src,))
        self.assertEqual(core.capacity.amount_safe_to_pay, money(580))
        self.assertIn("amend", core.resolved_events[0].fact_ids)

    def test_both_inclusive_bill_tiers_survive_until_date_resolution(self):
        src = source(kind="image")
        early = fact("early", "phone", AmountClaim(money("704.05"), "payable"), src, valid_until=date(2026, 2, 6))
        later = fact("later", "phone", AmountClaim(money("822.05"), "payable"), src, effective_on=date(2026, 2, 7))
        core = build((event("phone", None, 8, status="pending"),), minimum=100, requested=900,
                     facts=(early, later), sources=(src,))
        self.assertEqual(core.capacity.amount_safe_to_pay, money("77.95"))
        self.assertEqual(core.occurrences[0].home_amount, money("822.05"))
        self.assertEqual(core.resolved_events[0].fact_ids, ("later",))
        boundary = build((event("phone", None, 5, status="pending"),), minimum=100, requested=900,
                         facts=(early, later), sources=(src,))
        self.assertEqual(boundary.occurrences[0].home_amount, money("704.05"))

    def test_unknown_payment_date_reserves_highest_live_tier_not_request_day_tier(self):
        src = source(kind="image")
        expired = fact("expired", "phone", AmountClaim(money("900.00"), "payable"), src, valid_until=date(2026, 1, 31))
        early = fact("early", "phone", AmountClaim(money("704.05"), "payable"), src, valid_until=date(2026, 2, 6))
        later = fact("later", "phone", AmountClaim(money("822.05"), "payable"), src, effective_on=date(2026, 2, 7))
        core = build((event("phone", None, None, status="pending"),), minimum=100, requested=900,
                     facts=(expired, early, later), sources=(src,))
        self.assertIsNone(core.occurrences[0].cash_date)
        self.assertEqual(core.occurrences[0].home_amount, money("822.05"))
        self.assertEqual(core.capacity.amount_safe_to_pay, money("77.95"))
        self.assertEqual(core.capacity.proof_status, "conservative_bound")
        self.assertIn("UNKNOWN_DATE_TIER_BOUND", core.capacity.issue_codes)
        self.assertEqual(core.resolved_events[0].fact_ids, ("later",))
        self.assertIn(src.source_id, core.resolved_events[0].evidence_ids)
        self.assertEqual(sum(1 for c in core.baseline.checkpoints if c.phase == "reservation"), 1)

    def test_unknown_date_bound_resolves_precedence_only_within_overlapping_windows(self):
        old = source("message:old", known=datetime(2026, 1, 30, tzinfo=timezone.utc))
        new = source("message:new", known=datetime(2026, 2, 1, tzinfo=timezone.utc))

        def phone(day, *facts, amount=None):
            return build((event("phone", amount, day, status="pending"),), minimum=100, requested=900,
                         facts=facts, sources=(old, new))

        amend = fact("amend", "phone", AmountClaim(money(600), "payable"), new, action="amend", valid_until=date(2026, 2, 6))
        core = phone(None, amend, amount=800)
        self.assertEqual(core.occurrences[0].home_amount, money(800))
        self.assertEqual(core.capacity.amount_safe_to_pay, money(100))
        self.assertEqual(core.capacity.proof_status, "conservative_bound")

        later = fact("later", "phone", AmountClaim(money("822.05"), "payable"), old, effective_on=date(2026, 2, 7))
        early = fact("early", "phone", AmountClaim(money("704.05"), "payable"), new, valid_until=date(2026, 2, 6))
        core = phone(None, later, early)
        self.assertEqual(core.occurrences[0].home_amount, money("822.05"))
        self.assertEqual(core.capacity.amount_safe_to_pay, money("77.95"))
        self.assertEqual(core.resolved_events[0].fact_ids, ("later",))
        self.assertEqual(phone(5, later, early).occurrences[0].home_amount, money("704.05"))
        self.assertEqual(phone(8, later, early).occurrences[0].home_amount, money("822.05"))

        uncovered = phone(None, early)
        self.assertIsNone(uncovered.capacity.amount_safe_to_pay)
        self.assertEqual(uncovered.capacity.proof_status, "unresolved")
        self.assertIn("MISSING_AMOUNT", uncovered.capacity.issue_codes)

        stale = fact("stale", "phone", AmountClaim(money(900), "payable"), old, valid_until=date(2026, 2, 6))
        fresh = fact("fresh", "phone", AmountClaim(money(650), "payable"), new, valid_until=date(2026, 2, 6))
        tier = fact("tier", "phone", AmountClaim(money(700), "payable"), new, effective_on=date(2026, 2, 7))
        overlapping = phone(None, stale, fresh, tier)
        self.assertEqual(overlapping.occurrences[0].home_amount, money(700))
        self.assertEqual(overlapping.capacity.amount_safe_to_pay, money(200))

    def test_approved_pending_income_is_not_settled(self):
        src = source()
        approved = fact("approved", "salary", StateClaim("pending", approval_state="confirmed"), src, action="confirm")
        core = build((event("salary", 500, 1, direction="credit", status="pending", category="salary"),),
                     balance=500, facts=(approved,), sources=(src,))
        self.assertEqual(core.capacity.amount_safe_to_pay, money(200))
        self.assertEqual(core.resolved_events[0].approval_state, "confirmed")
        self.assertEqual(core.occurrences, ())

    def test_failed_cash_attempt_does_not_close_outstanding_obligation(self):
        src = source()
        opened = fact("owed", "bill", StateClaim("failed", obligation_state="outstanding"), src)
        core = build((event("bill", 100, -1, status="failed"),), facts=(opened,), sources=(src,))
        self.assertEqual(core.capacity.proof_status, "unresolved")
        self.assertIn("OUTSTANDING_OBLIGATION_UNSCHEDULED", core.capacity.issue_codes)
        self.assertEqual(core.occurrences, ())  # No invented retry date/charge.

    def test_cropped_subtotal_is_a_diagnostic_not_cash(self):
        diagnostic = EvidenceIssue("CROPPED_DOCUMENT", "warning", "final total missing",
                                   candidates=(AmountCandidate("Item Bill", "2854.00", "USD"),))
        core = build((event("bill", None, 1),), issues=(diagnostic,))
        self.assertIsNone(core.capacity.amount_safe_to_pay)
        self.assertIn("MISSING_AMOUNT", core.capacity.issue_codes)
        self.assertEqual(core.occurrences, ())

    def test_settlement_amendment_changes_directed_fx_lookup_date(self):
        src = source()
        delay = fact("delay", "bill", DateClaim(R + timedelta(days=3)), src, action="delay")
        rates = (FxRate(R + timedelta(days=2), "EUR", "USD", Decimal("2"), SourceRef("csv", "old-fx")),
                 FxRate(R + timedelta(days=3), "EUR", "USD", Decimal("1.333333"), SourceRef("csv", "new-fx")))
        core = build((event("bill", 100, 2, currency="EUR"),), facts=(delay,), sources=(src,), rates=rates, requested=900)
        self.assertEqual(core.capacity.amount_safe_to_pay, money("566.66"))
        self.assertEqual(core.occurrences[0].fx_source_id, "new-fx")

    def test_later_evidence_and_embedded_policy_text_cannot_change_cash(self):
        future = source(known=datetime(2026, 2, 2, tzinfo=timezone.utc))
        claim = fact("future", "bill", AmountClaim(money(0), "payable"), future, action="amend")
        core = build((event("bill", 100, 1),), requested=900, facts=(claim,), sources=(future,))
        self.assertEqual(core.capacity.amount_safe_to_pay, money(600))
        injected = source("message:injection")
        observation = fact("text", "bill", AmountClaim(money(9999), "balance_only"), injected)
        core = build((event("bill", 100, 1),), requested=900, facts=(observation,), sources=(injected,))
        self.assertEqual(core.capacity.amount_safe_to_pay, money(600))

    def test_distinct_identical_charges_retained_authorization_posting_merged(self):
        core = build((event("a", 100, 1), event("b", 100, 1)), requested=900)
        self.assertEqual(core.capacity.amount_safe_to_pay, money(500))
        posted = build((event("auth", 100, 1, status="pending"), event("post", 100, -1, status="settled", linked="auth")), requested=900)
        self.assertEqual(posted.capacity.amount_safe_to_pay, money(700))
        self.assertEqual(len(posted.resolved_events), 1)
        self.assertEqual(posted.resolved_events[0].source_event_ids, ("auth", "post"))

    def test_retry_relation_is_not_automatic_cash_identity(self):
        src = source()
        relation = fact("retry", "post", RelationClaim("auth", "retry_of"), src)
        core = build((event("auth", 100, 1, status="pending"), event("post", 100, -1, status="settled", linked="auth")),
                     facts=(relation,), sources=(src,), requested=900)
        self.assertEqual(len(core.resolved_events), 2)
        self.assertEqual(core.capacity.amount_safe_to_pay, money(600))

    def test_latest_income_role_claim_overrides_older_salary_classification(self):
        old = source("message:old", known=datetime(2026, 1, 15, tzinfo=timezone.utc))
        new = source("message:new", known=datetime(2026, 1, 31, tzinfo=timezone.utc))
        regular = Fact("z-regular", EventTarget("salary"), IncomeRoleClaim("regular_salary"),
                      evidence=(EvidenceRef(old.source_id),), action="amend")
        one_off = Fact("a-one-off", EventTarget("salary"), IncomeRoleClaim("one_off"),
                      evidence=(EvidenceRef(new.source_id),), action="amend", supersedes_fact_ids=("z-regular",))
        flows = (event("salary", 500, 1, direction="credit", status="scheduled", category="salary"),
                 event("tuition", 500, 9, status="scheduled", category="education"))
        for ordered in ("regular", "one_off"), ("one_off", "regular"):
            with self.subTest(order=ordered):
                labeled = {"regular": regular, "one_off": one_off}
                core = build(flows, balance=900, requested=600, facts=tuple(labeled[name] for name in ordered),
                             sources=(old, new))
                self.assertEqual(core.capacity.amount_safe_to_pay, money(100))

    def test_conflicting_relation_supersedes_still_keep_separate_cash_legs(self):
        old = source("message:old", known=datetime(2026, 1, 15, tzinfo=timezone.utc))
        new = source("message:new", known=datetime(2026, 1, 31, tzinfo=timezone.utc))
        same = Fact("same", EventTarget("bill2"), RelationClaim("bill1", "same_cash_occurrence"),
                    evidence=(EvidenceRef(old.source_id),), action="amend")
        independent = Fact("independent", EventTarget("bill2"), RelationClaim("bill1", "independent_cash_leg"),
                          evidence=(EvidenceRef(new.source_id),), action="amend",
                          supersedes_fact_ids=("same",))
        events = (event("bill1", 300, 1, status="pending"), event("bill2", 300, 2, status="pending"))
        for ordered in permutations(("same", "independent")):
            with self.subTest(order=ordered):
                labeled = {"same": same, "independent": independent}
                core = build(events, balance=1000, requested=600,
                             facts=tuple(labeled[name] for name in ordered), sources=(old, new))
                self.assertEqual(core.capacity.amount_safe_to_pay, money(100))

    def test_event_row_order_is_irrelevant(self):
        a, b = event("a", 600, 1), event("b", 700, 2, direction="credit", category="salary")
        self.assertEqual(build((a, b)), build((b, a)))

    def test_explicit_only_development_mode_never_certifies_full_safety(self):
        core = build(policy=ForecastPolicy(projection_mode="explicit_only"))
        self.assertEqual(core.capacity.proof_status, "unresolved")
        self.assertIn("EXPLICIT_ONLY_NOT_FULL_PROOF", core.capacity.issue_codes)


if __name__ == "__main__":
    unittest.main()
