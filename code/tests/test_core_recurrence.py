"""Synthetic recurrence, fact-scope and expense-effect invariants."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from itertools import permutations
from decimal import Decimal
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from buy_wait.contracts import (
    AmountClaim, CalendarMonth, CancelClaim, DateClaim, EventTarget, EvidenceRef,
    Fact, FactBatch, FinancialInput, FinancialProfile, ForecastPolicy, IncomeRoleClaim,
    Payment, RecurrenceClaim, ReduceTo, RelationClaim, SeriesScaleClaim, SeriesTarget,
    StateClaim, Stop,
)
from buy_wait.core import build_financial_context, replay_financial_plan
from test_core_explicit import R, build, event, fact, money, source


def monthly(amounts=(100, 100, 100), *, direction="debit", category="rent", dom=15,
            flexible=False):
    months = (date(2025, 11, dom), date(2025, 12, dom), date(2026, 1, dom))
    result = []
    for i, (day, amount) in enumerate(zip(months, amounts)):
        record = event(f"h{i}", amount, (day - R).days, direction=direction, category=category, status="settled")
        if flexible:
            record = replace(record, flexibility="reducible_or_stoppable", minimum_allowed_amount=money(40))
        result.append(record)
    return tuple(result)


def double_monthly(amounts=(100, 100), *, direction="debit", category="rent", dom=15):
    months = (date(2025, 11, dom), date(2025, 12, dom), date(2026, 1, dom))
    result = []
    for i, day in enumerate(months):
        for suffix in ("a", "b"):
            result.append(event(f"d{suffix}{i}", amounts[i % len(amounts)],
                              (day - R).days, direction=direction, category=category, status="settled"))
    return tuple(result)


def flexible_core(events, *, balance=1000, requested=900, protect=False, facts=(), sources=()):
    profile = FinancialProfile("u", "USD", money(balance), money(300),
                               frozenset(("dining",)) if protect else frozenset(),
                               frozenset(("dining",)), frozenset(("dining",)))
    data = FinancialInput("r", "u", R, money(requested), profile, tuple(events), sources=tuple(sources))
    return build_financial_context(data, FactBatch("r", "u", tuple(facts)), policy=ForecastPolicy())


class RecurrenceTests(unittest.TestCase):
    def test_monthly_expenses_project_three_future_occurrences_without_history_replay(self):
        core = build(monthly(), requested=900)
        self.assertEqual([o.cash_date for o in core.occurrences], [date(2026, 2, 15), date(2026, 3, 15), date(2026, 4, 15)])
        self.assertEqual(core.capacity.amount_safe_to_pay, money(400))
        self.assertTrue(all(o.source_event_ids == ("h0", "h1", "h2") for o in core.occurrences))

    def test_month_end_does_not_drift_as_thirty_day_interval(self):
        history = tuple(event(str(i), 100, (d - R).days, status="settled") for i, d in enumerate(
            (date(2025, 11, 30), date(2025, 12, 31), date(2026, 1, 31))))
        core = build(history)
        self.assertEqual([o.cash_date for o in core.occurrences], [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)])
        self.assertTrue(core.series[0].cadence.end_of_month)

    def test_complete_explicit_bill_replaces_estimate_even_when_lower(self):
        for actual, expected in ((140, 360), (40, 460)):
            with self.subTest(actual=actual):
                core = build((*monthly(), event("actual", actual, 14)), requested=900)
                self.assertEqual(core.capacity.amount_safe_to_pay, money(expected))
                self.assertEqual(len(core.occurrences), 3)
                self.assertEqual(sum(o.home_amount.minor for o in core.occurrences), (actual + 200) * 100)

    def test_pending_bill_replaces_inference_and_settles_only_once(self):
        core = build((*monthly(), event("actual", 100, 14, status="pending")), requested=900)
        self.assertEqual(core.capacity.amount_safe_to_pay, money(400))
        self.assertEqual(len(core.occurrences), 3)
        settlement = next(c for c in core.baseline.checkpoints if c.operation_id == "cash:actual")
        self.assertEqual((settlement.cash_delta_minor, settlement.hold_delta_minor), (-10000, -10000))

    def test_occurrence_cancellation_does_not_cancel_later_months(self):
        core = build((*monthly(), event("cancelled", 100, 14, status="cancelled")), requested=900)
        self.assertEqual(core.capacity.amount_safe_to_pay, money(500))
        self.assertEqual(len(core.occurrences), 2)
        self.assertEqual(min(o.cash_date for o in core.occurrences), date(2026, 3, 15))

    def test_failed_or_pre_request_attempt_does_not_erase_next_cycle(self):
        for status, offset in (("failed", -3), ("cancelled", -3), ("failed", 3)):
            with self.subTest(status=status, offset=offset):
                attempt = event("attempt", 100, offset, status=status)
                core = build((*monthly(dom=5), attempt), requested=900)
                self.assertEqual([o.cash_date for o in core.occurrences], [date(2026, 2, 5), date(2026, 3, 5), date(2026, 4, 5)])
                self.assertEqual(core.capacity.amount_safe_to_pay, money(400))

    def test_unrelated_same_category_debit_stays_separate_from_description_series(self):
        def named(record, text):
            return replace(record, description=text)
        history = tuple(named(e, "Streaming Plus") for e in monthly(category="subscriptions"))
        rental = named(event("rental", 10, 13, category="subscriptions"), "Movie rental")
        core = build((*history, rental), requested=900)
        self.assertEqual(len(core.occurrences), 4)
        self.assertEqual(core.capacity.amount_safe_to_pay, money(390))
        self.assertEqual(core.capacity.proof_status, "conservative_bound")
        self.assertIn("AMBIGUOUS_EXPLICIT_CYCLE", core.capacity.issue_codes)
        matched = build((*history, named(event("bill", 40, 14, category="subscriptions"), "Streaming Plus")), requested=900)
        self.assertEqual(matched.capacity.amount_safe_to_pay, money(460))
        self.assertEqual(matched.capacity.proof_status, "resolved_under_policy")
        self.assertEqual(len(matched.occurrences), 3)

    def test_authoritative_series_end_suppresses_only_its_applicable_future_cycles(self):
        src = source()
        ended = fact("end", "h2", CancelClaim(), src, scope="series", action="end", effective_on=date(2026, 3, 1))
        core = build(monthly(), requested=900, facts=(ended,), sources=(src,))
        self.assertEqual(core.capacity.amount_safe_to_pay, money(600))
        self.assertEqual([o.cash_date for o in core.occurrences], [date(2026, 2, 15)])

    def test_next_salary_delay_does_not_shift_later_paydays(self):
        src = source()
        delayed = Fact("delay", SeriesTarget("u", "salary", "credit", "USD"), DateClaim(date(2026, 2, 20)),
                       evidence=(EvidenceRef(src.source_id),), action="delay")
        history = monthly((500, 500, 500), direction="credit", category="salary")
        core = build((*history, event("next", 500, 14, direction="credit", category="salary")),
                     balance=700, requested=900, facts=(delayed,), sources=(src,))
        self.assertEqual(sorted(o.cash_date for o in core.occurrences), [date(2026, 2, 20), date(2026, 3, 15), date(2026, 4, 15)])
        self.assertEqual(core.capacity.amount_safe_to_pay, money(400))
        self.assertEqual(core.capacity.earliest_date_for_full_payment, date(2026, 2, 20))

    def test_percent_amendment_applies_once_when_first_explicit_bill_already_reflects_it(self):
        src = source()
        amended = fact("increase", "h2", SeriesScaleClaim(Decimal("1.12")), src,
                       scope="series", action="amend", effective_on=R)
        core = build((*monthly(), event("actual", 112, 14)), requested=900, facts=(amended,), sources=(src,))
        self.assertEqual([o.home_amount for o in core.occurrences], [money(112)] * 3)
        self.assertEqual(core.capacity.amount_safe_to_pay, money(364))

    def test_regular_salary_minimum_excludes_bonus_and_is_not_a_purchase_preference(self):
        history = monthly((500, 550, 600), direction="credit", category="salary")
        bonus = replace(event("bonus", 10000, -2, direction="credit", status="settled", category="salary"), description="Annual bonus")
        core = build((*history, bonus), requested=1900)
        self.assertEqual([o.home_amount for o in core.occurrences], [money(500)] * 3)
        self.assertEqual(core.capacity.earliest_date_for_full_payment, date(2026, 4, 15))
        self.assertEqual(core.capacity.amount_safe_to_pay, money(700))

    def test_one_salary_does_not_create_indefinite_income_but_explicit_contract_can(self):
        old = event("first", 100, -17, direction="credit", status="settled", category="salary")
        self.assertEqual(build((old,), requested=2000).occurrences, ())
        src = source()
        contract = Fact("contract", SeriesTarget("u", "salary", "credit", "USD"),
                        RecurrenceClaim(CalendarMonth(15), money(500), "regular_salary"), scope="series",
                        evidence=(EvidenceRef(src.source_id),), action="start", effective_on=R)
        core = build((old,), requested=1900, facts=(contract,), sources=(src,))
        self.assertEqual([o.home_amount for o in core.occurrences], [money(500)] * 3)

    def test_category_stream_rotating_merchants_and_partial_budget_conservation(self):
        history = tuple(replace(event(f"g{i}", 100, offset, category="groceries", status="settled"), description=f"Merchant {i}")
                        for i, offset in enumerate((-15, -8, -1)))
        without = build(history, balance=5000, requested=4000)
        core = build((*history, event("fuel", 40, 6, category="groceries", status="pending")), balance=5000, requested=4000)
        self.assertEqual(core.series[0].estimator, "periodic_category_max")
        self.assertEqual(len([o for o in core.occurrences if o.origin == "inferred_periodic"]), 13)
        self.assertEqual(sum(o.home_amount.minor for o in core.occurrences), 130000)
        self.assertEqual(core.capacity.amount_safe_to_pay, money(3400))
        self.assertEqual(core.capacity.amount_safe_to_pay, without.capacity.amount_safe_to_pay)
        residual = next(o for o in core.occurrences if o.covered_occurrence_ids)
        self.assertEqual((residual.home_amount, residual.budget_total_minor), (money(60), 10000))
        self.assertNotIn("AMBIGUOUS_EXPLICIT_CYCLE", core.capacity.issue_codes)

    def test_essential_irregular_envelope_front_loads_observed_peak_weeks(self):
        # Four complete observed-span weeks, no periodic cadence; totals 70, 50, 90, 40.
        offsets_amounts = ((-35, 1), (-27, 70), (-18, 50), (-12, 90), (-2, 40))
        history = tuple(replace(event(f"g{i}", amount, offset, status="settled", category="groceries"), description=f"Merchant {i}")
                        for i, (offset, amount) in enumerate(offsets_amounts))
        core = build(history, balance=5000, requested=4000)
        self.assertEqual(core.series[0].estimator, "observed_span_weekly_max")
        self.assertEqual(len(core.occurrences), 13)
        self.assertEqual(sum(o.home_amount.minor for o in core.occurrences), 117000)
        self.assertEqual(min(o.cash_date for o in core.occurrences), R)
        self.assertEqual(core.capacity.amount_safe_to_pay, money(3530))

    def test_missing_history_amount_in_supported_essential_series_blocks_proof(self):
        history = monthly((100, None, 100))
        core = build(history)
        self.assertIsNone(core.capacity.amount_safe_to_pay)
        self.assertIn("MISSING_HISTORY_AMOUNT", core.capacity.issue_codes)

    def test_supported_investment_contributions_remain_debits_not_unrealized_gains(self):
        contributions = tuple(replace(e, event_type="investment_purchase", category="investment") for e in monthly())
        core = build(contributions, requested=900)
        self.assertEqual(core.capacity.amount_safe_to_pay, money(400))
        self.assertEqual(len(core.occurrences), 3)
        self.assertTrue(all(o.direction == "debit" for o in core.occurrences))
        self.assertEqual(build(contributions[:1], requested=900).occurrences, ())

    def test_new_contract_start_does_not_backfill_income_before_it_begins(self):
        src = source()
        contract = Fact("start", SeriesTarget("u", "salary", "credit", "USD"),
                        RecurrenceClaim(CalendarMonth(15), money(500), "regular_salary"),
                        scope="series", evidence=(EvidenceRef(src.source_id),), action="start",
                        effective_on=date(2026, 3, 15))
        core = build(monthly((500, 500, 500), direction="credit", category="salary"),
                     requested=1900, facts=(contract,), sources=(src,))
        self.assertEqual([o.cash_date for o in core.occurrences], [date(2026, 3, 15), date(2026, 4, 15)])
        self.assertIsNone(core.capacity.earliest_date_for_full_payment)

    def test_material_but_unsupported_essential_history_is_not_zero_spending(self):
        history = tuple(replace(event(str(i), 100, offset, category="groceries", status="settled"), description=f"merchant{i}")
                        for i, offset in enumerate((-20, -8, -1)))
        core = build(history)
        self.assertIsNone(core.capacity.amount_safe_to_pay)
        self.assertIn("INSUFFICIENT_VARIABLE_HISTORY", core.capacity.issue_codes)

    def test_series_restart_supersedes_prior_cancellation(self):
        old = source("message:old")
        new = source("message:new")
        cancel = Fact("cancel", SeriesTarget("u", "utilities", "debit", "USD"),
                      CancelClaim(), action="cancel", scope="series",
                      evidence=(EvidenceRef(old.source_id),))
        restart = Fact("start", SeriesTarget("u", "utilities", "debit", "USD"),
                       RecurrenceClaim(CalendarMonth(15), money(100)), scope="series",
                       evidence=(EvidenceRef(new.source_id),), action="start",
                       effective_on=date(2026, 2, 1), supersedes_fact_ids=("cancel",))
        for ordered in ("cancel", "start"), ("start", "cancel"):
            with self.subTest(order=ordered):
                label = {"cancel": cancel, "start": restart}
                core = build(monthly(category="utilities"), facts=tuple(label[name] for name in ordered), sources=(old, new))
                self.assertEqual(core.capacity.amount_safe_to_pay, money(400))
                self.assertEqual(len(core.occurrences), 3)
        future = source("message:future", known=datetime(2026, 2, 2, tzinfo=timezone.utc))
        unavailable_restart = replace(restart, evidence=(EvidenceRef(future.source_id),))
        core = build(monthly(category="utilities"), facts=(cancel, unavailable_restart), sources=(old, future))
        cancellation_only = build(monthly(category="utilities"), facts=(cancel,), sources=(old,))
        self.assertEqual(core.capacity.amount_safe_to_pay, cancellation_only.capacity.amount_safe_to_pay)
        self.assertEqual(core.occurrences, cancellation_only.occurrences)
        self.assertIn("FUTURE_EVIDENCE_EXCLUDED", core.capacity.issue_codes)

    def test_multiple_obligations_in_same_cycle_are_preserved(self):
        obligations = double_monthly((100, 100, 100), category="utilities", dom=5)
        for ordered in (obligations, tuple(reversed(obligations))):
            with self.subTest(order=tuple(e.event_id for e in ordered)):
                core = build(ordered, requested=1000)
                self.assertEqual(core.capacity.amount_safe_to_pay, money(100))
                self.assertEqual(len(core.occurrences), 6)
                self.assertEqual(sum(o.home_amount.minor for o in core.occurrences), 60000)

    def test_compound_restart_obligations_income_and_pending_replay(self):
        """Independent ledger: 2000 - 300 floor - 600 bills - 500 tuition - 200 holds."""
        old, new = source("message:old"), source("message:new")
        target = SeriesTarget("u", "utilities", "debit", "USD")
        cancel = Fact("cancel", target, CancelClaim(), scope="series", action="cancel",
                      evidence=(EvidenceRef(old.source_id),))
        restart = Fact("restart", target, RecurrenceClaim(CalendarMonth(5), money(100)),
                       scope="series", action="start", effective_on=R,
                       supersedes_fact_ids=("cancel",), evidence=(EvidenceRef(new.source_id),))
        salary = event("salary", 500, 4, direction="credit", status="scheduled", category="salary")
        regular = Fact("regular", EventTarget("salary"), IncomeRoleClaim("regular_salary"),
                       action="amend", evidence=(EvidenceRef(old.source_id),))
        bonus = Fact("bonus", EventTarget("salary"), IncomeRoleClaim("one_off"), action="amend",
                     supersedes_fact_ids=("regular",), evidence=(EvidenceRef(new.source_id),))
        first, second = event("pending-a", 100, 2, status="pending"), event("pending-b", 100, 3, status="pending")
        same = Fact("same", EventTarget("pending-b"), RelationClaim("pending-a", "same_cash_occurrence"),
                    action="amend", evidence=(EvidenceRef(old.source_id),))
        independent = Fact("independent", EventTarget("pending-b"), RelationClaim("pending-a", "independent_cash_leg"),
                           action="amend", supersedes_fact_ids=("same",), evidence=(EvidenceRef(new.source_id),))
        core = build((*double_monthly(category="utilities", dom=5), salary,
                      event("tuition", 500, 9, status="scheduled", category="education"), first, second),
                     balance=2000, requested=2000,
                     facts=(cancel, restart, regular, bonus, same, independent), sources=(old, new))
        self.assertEqual(core.capacity.amount_safe_to_pay, money(400))
        self.assertEqual(len([o for o in core.occurrences if o.origin == "inferred_periodic"]), 6)
        self.assertTrue(replay_financial_plan(core, (Payment(R, money(400), "safe"),)).safe)
        self.assertFalse(replay_financial_plan(core, (Payment(R, money(401), "unsafe"),)).safe)

    def test_series_pending_income_state_remains_unspendable(self):
        src = source()
        pending = Fact("pending", SeriesTarget("u", "salary", "credit", "USD"),
                       StateClaim("pending", approval_state="confirmed"), scope="series",
                       evidence=(EvidenceRef(src.source_id),), action="confirm")
        core = build(monthly((500, 500, 500), direction="credit", category="salary"),
                     requested=1900, facts=(pending,), sources=(src,))
        self.assertEqual(core.occurrences, ())
        self.assertEqual(core.capacity.amount_safe_to_pay, money(700))


class SpendingChangeTests(unittest.TestCase):
    def test_only_core_expands_historical_aliases_and_keeps_capacity_immutable(self):
        core = flexible_core(monthly(category="dining", flexible=True))
        self.assertEqual(core.capacity.amount_safe_to_pay, money(400))
        self.assertEqual(len(core.change_targets), 3)
        baseline = core.capacity
        changed = replay_financial_plan(core, (Payment(R, money(580), "p"),), changes=(ReduceTo("h0", money(40)),))
        self.assertTrue(changed.safe)
        self.assertEqual(changed.minimum_available, money(300))
        self.assertEqual([delta for _, delta in changed.occurrence_deltas], [-6000] * 3)
        self.assertEqual(core.capacity, baseline)
        self.assertFalse(replay_financial_plan(core, (), changes=(Stop("h0"), Stop("h1"))).safe)
        self.assertFalse(replay_financial_plan(core, (), changes=(Stop("h0"), ReduceTo("h0", money(40)))).safe)
        self.assertFalse(replay_financial_plan(core, (), changes=(ReduceTo("h0", money(39)),)).safe)

    def test_protected_and_committed_expenses_cannot_be_removed(self):
        protected = flexible_core(monthly(category="dining", flexible=True), protect=True)
        self.assertEqual(protected.change_targets, ())
        self.assertFalse(replay_financial_plan(protected, (), changes=(Stop("h0"),)).safe)
        committed = flexible_core((*monthly(category="dining", flexible=True), event("p", 100, 14, category="dining", status="pending")))
        result = replay_financial_plan(committed, (Payment(R, money(600), "p1"),), changes=(Stop("h0"),))
        self.assertTrue(result.safe)
        self.assertEqual(result.minimum_available, money(300))
        self.assertNotIn("cash:p", {oid for oid, _ in result.occurrence_deltas})

    def test_floor_monotonicity_on_real_replay_and_partial_budget_coverage(self):
        history = tuple(replace(event(f"h{i}", 100, offset, category="dining", status="settled"),
                                description=f"Merchant {i}", flexibility="reducible_or_stoppable", minimum_allowed_amount=money(20))
                        for i, offset in enumerate((-15, -8, -1)))
        core = flexible_core((*history, event("committed", 40, 6, category="dining", status="pending")), balance=5000, requested=4000)
        minima = []
        for value in (20, 40, 60, 80, 100):
            replay = replay_financial_plan(core, (), changes=(ReduceTo("h0", money(value)),))
            self.assertTrue(replay.safe)
            minima.append(replay.minimum_available.minor)
        self.assertEqual(minima, sorted(minima, reverse=True))
        floor = replay_financial_plan(core, (), changes=(ReduceTo("h0", money(20)),))
        # Existing committed40 cannot be reduced to20; other twelve future buckets become20.
        self.assertEqual(floor.minimum_available, money(4720))
        self.assertEqual(next(o for o in core.occurrences if o.covered_occurrence_ids).home_amount, money(60))


if __name__ == "__main__":
    unittest.main()
