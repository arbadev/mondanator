"""Executable schedule tests; public CSV use is structural, not prediction."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as NS
import csv

from buy_wait.planning.generator import (
    expand_supplied_option, one_shot_payment, option_completion_date,
    prescribed_partial_payments,
)
from test_planning_support import (
    H, R, PlanningTestCase, core, money, option, preferences, request,
)


class ScheduleTests(PlanningTestCase):
    def test_supplied_schedule_preserves_fees_and_amounts(self):
        offer = option()
        result = expand_supplied_option(offer, request_date=R, horizon_end=H)
        self.assertEqual([p.date for p in result], [R, R + timedelta(days=30), R + timedelta(days=60)])
        self.assertEqual([p.amount.minor for p in result], [3500, 3500, 3500])
        self.assertEqual(sum(p.amount.minor for p in result), 10500)
        self.assertEqual(len({p.payment_id for p in result}), 3)
        self.assertTrue(all(p.amount is offer.payment_amount for p in result))

    def test_31_days_are_not_calendar_month_anniversaries(self):
        offer = option(first_payment_date=date(2026, 4, 19), payment_frequency_days=31)
        result = expand_supplied_option(offer, request_date=date(2026, 4, 5),
                                        horizon_end=date(2026, 7, 4))
        self.assertEqual([p.date for p in result], [date(2026, 4, 19), date(2026, 5, 20), date(2026, 6, 20)])

    def test_rounding_discrepancy_does_not_change_last_payment(self):
        offer = option(payment_amount=money(3333), financing_fee=money(0), total_payable_amount=money(10000))
        result = expand_supplied_option(offer, request_date=R, horizon_end=H)
        self.assertEqual([p.amount.minor for p in result], [3333, 3333, 3333])
        self.assertEqual(sum(p.amount.minor for p in result), 9999)

    def test_day90_is_included_and_day91_is_rejected(self):
        offer = option(number_of_payments=2, payment_frequency_days=90)
        self.assertEqual(option_completion_date(offer), H)
        self.assertEqual(expand_supplied_option(offer, request_date=R, horizon_end=H)[-1].date, H)
        offer.payment_frequency_days = 91
        with self.assertRaises(ValueError):
            expand_supplied_option(offer, request_date=R, horizon_end=H)

    def test_metadata_validation_before_schedule_allocation(self):
        for overrides in (
            {"number_of_payments": 0}, {"number_of_payments": True},
            {"number_of_payments": 10**12}, {"payment_frequency_days": 0},
            {"payment_frequency_days": -1}, {"payment_method": "partial_payment"},
            {"first_payment_date": R - timedelta(days=1)},
            {"first_payment_date": datetime(2030, 1, 1)},
            {"payment_amount": NS(currency="USD", minor=Decimal("1.5"))},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                expand_supplied_option(option(**overrides), request_date=R, horizon_end=H)

    def test_supplied_full_row_requires_exact_single_shape(self):
        offer = option(payment_method="full_payment", number_of_payments=1,
                       payment_frequency_days=None, payment_amount=money(10000))
        self.assertEqual(len(expand_supplied_option(offer, request_date=R, horizon_end=H)), 1)
        offer.payment_frequency_days = 30
        with self.assertRaises(ValueError):
            option_completion_date(offer)

    def test_partial_needs_no_seller_row_and_exactly_uses_capacity(self):
        req, pref, capacity = request(), preferences(), core().capacity
        result = prescribed_partial_payments(req, pref, capacity, horizon_end=H)
        self.assertEqual([(p.date, p.amount.minor) for p in result],
                         [(R, 4000), (R + timedelta(days=10), 6000)])
        self.assertEqual(sum(p.amount.minor for p in result), 10000)

    def test_partial_permissions_are_separate(self):
        for allowed in (False, True):
            for accepted in (False, True):
                pref = preferences(methods=frozenset({"partial_payment"}) if accepted else frozenset())
                with self.subTest(allowed=allowed, accepted=accepted):
                    if allowed and accepted:
                        self.assertEqual(len(prescribed_partial_payments(
                            request(allows_partial_payment=allowed), pref, core().capacity, horizon_end=H)), 2)
                    else:
                        with self.assertRaises(ValueError):
                            prescribed_partial_payments(request(allows_partial_payment=allowed), pref,
                                                         core().capacity, horizon_end=H)

    def test_partial_zero_full_and_unknown_capacity_are_not_schedules(self):
        for value in (money(0), money(10000), None, money(4000, "EUR")):
            capacity = core().capacity
            capacity.amount_safe_to_pay = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                prescribed_partial_payments(request(), preferences(), capacity, horizon_end=H)

    def test_partial_known_baseline_shortfall_or_unresolved_proof_is_not_safe(self):
        for field, value in (("baseline_feasible", False), ("proof_status", "unresolved"),
                             ("proof_status", "invented_proof")):
            capacity = core().capacity
            setattr(capacity, field, value)
            with self.subTest(field=field), self.assertRaises(ValueError):
                prescribed_partial_payments(request(), preferences(), capacity, horizon_end=H)

    def test_partial_second_date_must_be_prescribed_and_on_time(self):
        req = request(desired_completion_date=R + timedelta(days=10))
        self.assertEqual(prescribed_partial_payments(req, preferences(), core().capacity, horizon_end=H)[1].date,
                         req.desired_completion_date)
        for end in (None, R, R + timedelta(days=11), H + timedelta(days=1)):
            capacity = core().capacity
            capacity.earliest_date_for_full_payment = end
            with self.subTest(end=end), self.assertRaises(ValueError):
                prescribed_partial_payments(req, preferences(), capacity, horizon_end=H)

    def test_one_shot_does_not_infer_acceptance_from_money(self):
        with self.assertRaises(ValueError):
            one_shot_payment(request(), preferences(methods=frozenset({"installments"})),
                             payment_date=R, horizon_end=H)
        with self.assertRaises(ValueError):
            one_shot_payment(request(), preferences(methods=frozenset({"wait"})),
                             payment_date=R + timedelta(days=1), horizon_end=H)

    def test_one_shot_deadline_boundary(self):
        req = request()
        result = one_shot_payment(req, preferences(), payment_date=req.desired_completion_date, horizon_end=H)
        self.assertEqual(result[0].amount, req.requested_amount)
        for day in (R - timedelta(days=1), req.desired_completion_date + timedelta(days=1)):
            with self.subTest(day=day), self.assertRaises(ValueError):
                one_shot_payment(req, preferences(), payment_date=day, horizon_end=H)

    def test_zero_one_shot_amount_is_not_changed_by_an_invented_positive_floor(self):
        result = one_shot_payment(request(requested_amount=money(0)), preferences(),
                                  payment_date=R, horizon_end=H)
        self.assertEqual(result[0].amount.minor, 0)

    def test_all_71_public_offers_reconcile_and_32_in_horizon_expand_exactly(self):
        dataset = Path(__file__).resolve().parents[2] / "dataset"
        with (dataset / "financial_profiles.csv").open(newline="", encoding="utf-8-sig") as f:
            currencies = {r["user_id"]: r["home_currency"] for r in csv.DictReader(f)}
        with (dataset / "sample_requests.csv").open(newline="", encoding="utf-8-sig") as f:
            # Only input fields are selected; solved prediction fields are never passed.
            requests = {r["request_id"]: (date.fromisoformat(r["request_date"]),
                        Decimal(r["requested_amount"]), currencies[r["user_id"]]) for r in csv.DictReader(f)}
        with (dataset / "request_payment_options.csv").open(newline="", encoding="utf-8-sig") as f:
            rows = [r for r in csv.DictReader(f) if r["request_id"] in requests]
        self.assertEqual(len(rows), 71)
        expanded = 0
        for row in rows:
            start, principal, currency = requests[row["request_id"]]
            values = {}
            for key in ("payment_amount", "financing_fee", "total_payable_amount"):
                minor = Decimal(row[key]) * 100
                self.assertEqual(minor, int(minor), "public source must be cent-exact")
                values[key] = money(int(minor), currency)
            offer = option(**values, payment_option_id=row["payment_option_id"],
                           request_id=row["request_id"], payment_method=row["payment_method"],
                           number_of_payments=int(row["number_of_payments"]),
                           first_payment_date=date.fromisoformat(row["first_payment_date"]),
                           payment_frequency_days=int(row["payment_frequency_days"])
                           if row["payment_frequency_days"] else None)
            self.assertEqual(offer.payment_amount.minor * offer.number_of_payments,
                             offer.total_payable_amount.minor)
            self.assertEqual(int(principal * 100) + offer.financing_fee.minor, offer.total_payable_amount.minor)
            if option_completion_date(offer) <= start + timedelta(days=90):
                result = expand_supplied_option(offer, request_date=start, horizon_end=start + timedelta(days=90))
                self.assertEqual(sum(p.amount.minor for p in result), offer.total_payable_amount.minor)
                expanded += 1
        self.assertEqual(expanded, 32)
