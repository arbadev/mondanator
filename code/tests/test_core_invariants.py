"""Independent integer replay and boundary/failure regressions."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from buy_wait.contracts import (
    AmountClaim, DateClaim, EventTarget, EvidenceRef, Fact, FactBatch, Money,
    NewCashClaim, NewOccurrenceTarget, Payment, SeriesTarget, StateClaim,
)
from buy_wait.core import replay_financial_plan
from test_core_explicit import R, build, event, fact, money, source
from test_core_recurrence import monthly


class IndependentCapacityTests(unittest.TestCase):
    @staticmethod
    def safe_reference(opening, minimum, rows, day, amount):
        # Independent available-cash model: pending debits are removed up front
        # and make no further available-cash delta when they settle.
        balance = opening - sum(a for _, direction, status, a in rows if direction == "debit" and status == "pending")
        if balance < minimum:
            return False
        for current in range(91):
            for d, direction, status, a in rows:
                if d == current and direction == "debit" and status == "scheduled":
                    balance -= a
                    if balance < minimum:
                        return False
            for d, direction, status, a in rows:
                if d == current and direction == "credit" and status == "scheduled":
                    balance += a
            if day == current:
                balance -= amount
                if balance < minimum:
                    return False
        return True

    def test_suffix_capacity_equals_independent_exhaustive_replay(self):
        rng = random.Random(82411)
        for case in range(60):
            opening, minimum, requested = rng.randrange(40, 181), rng.randrange(0, 41), rng.randrange(0, 41)
            rows = [(rng.randrange(0, 91), rng.choice(("debit", "credit")), rng.choice(("pending", "scheduled")), rng.randrange(1, 31)) for _ in range(7)]
            events = tuple(replace(event(str(i), 0, day, direction=direction, status=status,
                                         category="salary" if direction == "credit" else "rent"), amount=Money("USD", amount))
                           for i, (day, direction, status, amount) in enumerate(rows))
            core = build(events, balance=f"{opening // 100}.{opening % 100:02d}",
                         minimum=f"{minimum // 100}.{minimum % 100:02d}",
                         requested=f"{requested // 100}.{requested % 100:02d}")
            safe = max((a for a in range(requested + 1) if self.safe_reference(opening, minimum, rows, 0, a)), default=0)
            earliest = next((R + timedelta(days=d) for d in range(91)
                             if self.safe_reference(opening, minimum, rows, d, requested)), None)
            with self.subTest(case=case):
                self.assertEqual(core.capacity.amount_safe_to_pay.minor, safe)
                self.assertEqual(core.capacity.earliest_date_for_full_payment, earliest)
                if earliest is not None:
                    self.assertTrue(replay_financial_plan(core, (Payment(earliest, Money("USD", requested), "full"),)).safe)

    def test_nonnegative_zero_request_has_safe_zero_payment_without_invented_minimum(self):
        core = build(requested=0)
        self.assertEqual(core.capacity.amount_safe_to_pay, money(0))
        self.assertEqual(core.capacity.earliest_date_for_full_payment, R)
        self.assertTrue(replay_financial_plan(core, (Payment(R, money(0), "zero"),)).safe)

    def test_fixed_forecast_debit_minimum_and_credit_monotonicity(self):
        baseline = (event("bill", 100, 10),)
        original = build(baseline, requested=900).capacity.amount_safe_to_pay.minor
        self.assertLessEqual(build((*baseline, event("another", 10, 20)), requested=900).capacity.amount_safe_to_pay.minor, original)
        self.assertLessEqual(build(baseline, requested=900, minimum=400).capacity.amount_safe_to_pay.minor, original)
        self.assertGreaterEqual(build((*baseline, event("salary", 200, 0, direction="credit", category="salary")), requested=900).capacity.amount_safe_to_pay.minor, original)


class BoundaryRegressionTests(unittest.TestCase):
    def test_conditional_approval_prevents_scheduled_salary_from_becoming_spendable(self):
        src = source()
        conditional = fact("conditional", "salary", StateClaim("scheduled", approval_state="conditional"), src)
        core = build((event("salary", 1000, 1, direction="credit", category="salary"),), requested=1000,
                     facts=(conditional,), sources=(src,))
        self.assertIsNone(core.capacity.earliest_date_for_full_payment)
        self.assertEqual(core.capacity.amount_safe_to_pay, money(700))

    def test_same_issuer_new_confirmation_can_resolve_old_conditional_approval(self):
        earlier = source("old", known=datetime(2026, 1, 30, tzinfo=timezone.utc))
        later = source("new", known=datetime(2026, 1, 31, tzinfo=timezone.utc))
        conditional = fact("conditional", "salary", StateClaim("scheduled", approval_state="conditional"), earlier)
        approved = fact("approved", "salary", StateClaim("scheduled", approval_state="confirmed"), later)
        core = build((event("salary", 1000, 1, direction="credit", category="salary"),), requested=1000,
                     facts=(conditional, approved), sources=(earlier, later))
        self.assertEqual(core.capacity.earliest_date_for_full_payment, R + timedelta(days=1))

    def test_fact_batch_freezes_lists_and_reapplication_is_idempotent(self):
        src = source()
        amended = fact("amend", "bill", AmountClaim(money(100), "payable"), src, action="amend")
        mutable = [amended]
        batch = FactBatch("r", "u", mutable)
        mutable.clear()
        self.assertEqual(batch.facts, (amended,))
        self.assertEqual(build((event("bill", 90, 1),), facts=(amended,), sources=(src,)),
                         build((event("bill", 90, 1),), facts=(amended, amended), sources=(src,)))

    def test_untyped_financial_patch_and_unknown_supersession_are_rejected(self):
        with self.assertRaises(ValueError):
            Fact("bad", EventTarget("bill"), {"minimum": 0})
        with self.assertRaises(ValueError):
            AmountClaim(money(10), "subtotal")
        src = source()
        bogus = fact("bogus", "bill", AmountClaim(money(10), "payable"), src, supersedes_fact_ids=("imaginary",))
        with self.assertRaises(ValueError):
            build((event("bill", 100, 1),), facts=(bogus,), sources=(src,))

    def test_new_one_off_salary_credit_is_not_promoted_to_recurring_income(self):
        src = source()
        observed = Fact("new", NewOccurrenceTarget("bonus", "u"),
                        NewCashClaim("income", "salary", "credit", money(1000), "USD", R + timedelta(days=1), "scheduled", "one_off"),
                        evidence=(EvidenceRef(src.source_id),), action="confirm")
        core = build(requested=1000, facts=(observed,), sources=(src,))
        self.assertIsNone(core.capacity.earliest_date_for_full_payment)
        self.assertEqual(core.occurrences, ())

    def test_series_bill_tier_is_selected_after_delay_not_nominal_cycle_date(self):
        src = source()
        target = SeriesTarget("u", "utilities", "debit", "USD")
        def observation(fid, claim, **kwargs):
            return Fact(fid, target, claim, evidence=(EvidenceRef(src.source_id),), **kwargs)
        delayed = observation("delay", DateClaim(R + timedelta(days=8)), action="delay")
        early = observation("early", AmountClaim(money("704.05"), "payable"), valid_until=R + timedelta(days=5))
        late = observation("late", AmountClaim(money("822.05"), "payable"), effective_on=R + timedelta(days=6))
        core = build(monthly(category="utilities", dom=5), balance=2000, requested=1900,
                     facts=(early, late, delayed), sources=(src,))
        payment = min(core.occurrences, key=lambda o: o.cash_date)
        self.assertEqual(payment.cash_date, R + timedelta(days=8))
        self.assertEqual(payment.home_amount, money("822.05"))
        self.assertEqual(core.capacity.amount_safe_to_pay, money("677.95"))


if __name__ == "__main__":
    unittest.main()
