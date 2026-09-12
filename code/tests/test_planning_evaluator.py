"""Static contract behavior only: no mock balance result is engine evidence."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace as NS

from buy_wait.planning.evaluator import static_rejections
from buy_wait.planning.generator import expand_supplied_option, prescribed_partial_payments
from test_planning_support import (
    H, R, PlanningTestCase, ReduceTo, Stop, codes, core, envelope, money,
    option, payment, plan, preferences, request, target,
)


class StaticEvaluatorTests(PlanningTestCase):
    def installment(self, offer=None, **plan_fields):
        offer = offer or option()
        env = envelope(options_by_id={offer.payment_option_id: offer})
        schedule = expand_supplied_option(offer, request_date=R, horizon_end=H)
        candidate = plan(method="installments", supplied_option_id=offer.payment_option_id,
                         payments=schedule, **plan_fields)
        return candidate, env

    def test_empty_rejections_are_only_static_success_not_affordability(self):
        # Full request exceeds S, but static validity is not replay safety.
        self.assertLess(core().capacity.amount_safe_to_pay.minor, 10000)
        self.assertEqual(static_rejections(plan(), envelope(), core()), ())

    def test_full_now_and_wait_need_full_acceptance(self):
        env = envelope(preferences=preferences(methods=frozenset({"installments"})))
        for candidate in (plan(), plan(method="wait", payments=(payment(R + timedelta(days=10)),))):
            with self.subTest(method=candidate.method):
                self.assertIn("METHOD_NOT_ACCEPTED", codes(static_rejections(candidate, env, core())))

    def test_substring_is_not_method_acceptance(self):
        env = envelope(preferences=preferences(methods="not_full_payment"))
        result = codes(static_rejections(plan(), env, core()))
        self.assertIn("METHOD_PREFERENCES_MALFORMED", result)
        self.assertIn("METHOD_NOT_ACCEPTED", result)

    def test_hash_request_user_and_currency_identity(self):
        result = codes(static_rejections(plan(core_hash="stale"), envelope(), core()))
        self.assertIn("CONTEXT_HASH_MISMATCH", result)
        result = codes(static_rejections(plan(request_id="other"), envelope(), core()))
        self.assertIn("REQUEST_IDENTITY_MISMATCH", result)
        result = codes(static_rejections(plan(payments=(payment(currency="EUR"),)), envelope(), core()))
        self.assertIn("PAYMENT_CURRENCY_MISMATCH", result)

    def test_partial_exact_schedule_and_both_permissions(self):
        c = core()
        schedule = prescribed_partial_payments(request(), preferences(), c.capacity, horizon_end=H)
        candidate = plan(method="partial_payment", payments=schedule)
        self.assertEqual(static_rejections(candidate, envelope(), c), ())
        self.assertIn("REQUEST_PARTIAL_DISABLED", codes(static_rejections(
            candidate, envelope(request=request(allows_partial_payment=False)), c)))
        self.assertIn("METHOD_NOT_ACCEPTED", codes(static_rejections(
            candidate, envelope(preferences=preferences(methods=frozenset({"full_payment"}))), c)))

    def test_partial_does_not_need_or_borrow_full_option_id(self):
        c = core()
        schedule = prescribed_partial_payments(request(), preferences(), c.capacity, horizon_end=H)
        candidate = plan(method="partial_payment", payments=schedule, supplied_option_id="payment_option_01")
        self.assertIn("RULE_PLAN_BORROWED_OPTION_ID", codes(static_rejections(candidate, envelope(), c)))

    def test_partial_bad_amount_date_and_third_payment(self):
        cases = (
            (payment(R, 5000, "a"), payment(R + timedelta(days=10), 5000, "b")),
            (payment(R, 4000, "a"), payment(R + timedelta(days=11), 6000, "b")),
            (payment(R, 4000, "a"), payment(R + timedelta(days=10), 3000, "b"),
             payment(R + timedelta(days=11), 3000, "c")),
        )
        for schedule in cases:
            with self.subTest(schedule=schedule):
                self.assertIn("PARTIAL_GRAMMAR_MISMATCH", codes(static_rejections(
                    plan(method="partial_payment", payments=schedule), envelope(), core())))

    def test_partial_sum_and_capacity_bounds(self):
        schedule = (payment(R, 4000, "a"), payment(R + timedelta(days=10), 5999, "b"))
        self.assertIn("PRINCIPAL_SUM_MISMATCH", codes(static_rejections(
            plan(method="partial_payment", payments=schedule), envelope(), core())))
        for safe in (money(0), money(10000), None):
            c = core()
            c.capacity.amount_safe_to_pay = safe
            self.assertIn("PARTIAL_CAPACITY_OUT_OF_RANGE", codes(static_rejections(
                plan(method="partial_payment", payments=schedule), envelope(), c)))

    def test_deadline_is_inclusive_and_no_late_wait_recommended(self):
        end = request().desired_completion_date
        self.assertEqual(static_rejections(plan(method="wait", payments=(payment(end),)), envelope(), core()), ())
        result = static_rejections(plan(method="wait", payments=(payment(end + timedelta(days=1)),)), envelope(), core())
        breach = next(r for r in result if r["code"] == "MISSES_DEADLINE")
        self.assertEqual(breach["date"], end + timedelta(days=1))
        self.assertEqual(breach["deadline"], end)

    def test_unresolved_capacity_is_distinct_from_zero_and_bound(self):
        c = core()
        c.capacity.amount_safe_to_pay = None
        c.capacity.proof_status = "unresolved"
        self.assertIn("CAPACITY_UNRESOLVED", codes(static_rejections(plan(), envelope(), c)))
        c = core()
        c.capacity.proof_status = "conservative_bound"
        self.assertNotIn("CAPACITY_UNRESOLVED", codes(static_rejections(plan(), envelope(), c)))
        c.capacity.amount_safe_to_pay = money(0)
        c.capacity.baseline_feasible = False
        self.assertNotIn("CAPACITY_UNRESOLVED", codes(static_rejections(plan(), envelope(), c)))

    def test_fee_once_and_installments_need_not_equal_principal(self):
        candidate, env = self.installment()
        self.assertEqual(sum(p.amount.minor for p in candidate.payments), 10500)
        self.assertEqual(static_rejections(candidate, env, core()), ())

    def test_rounding_and_fee_mismatches_report_exact_deltas_without_mutation(self):
        offer = option(payment_amount=money(3333), financing_fee=money(0), total_payable_amount=money(10000))
        candidate, env = self.installment(offer)
        before = deepcopy((candidate, env))
        issues = static_rejections(candidate, env, core())
        issue = next(i for i in issues if i["code"] == "OPTION_TOTAL_MISMATCH")
        self.assertEqual((issue["actual_minor"], issue["expected_minor"], issue["delta_minor"]), (9999, 10000, -1))
        self.assertEqual((candidate, env), before)
        offer.total_payable_amount = money(9999)
        issue = next(i for i in static_rejections(candidate, env, core()) if i["code"] == "OPTION_FEE_MISMATCH")
        self.assertEqual(issue["delta_minor"], -1)

    def test_altered_installment_last_payment_is_rejected(self):
        candidate, env = self.installment()
        old = candidate.payments
        candidate.payments = old[:-1] + (payment(old[-1].date, 3499, "changed"),)
        self.assertIn("OPTION_SCHEDULE_MISMATCH", codes(static_rejections(candidate, env, core())))

    def test_offer_identity_and_required_id(self):
        candidate, env = self.installment()
        candidate.supplied_option_id = None
        self.assertIn("SUPPLIED_OPTION_REQUIRED", codes(static_rejections(candidate, env, core())))
        candidate.supplied_option_id = "missing"
        self.assertIn("UNKNOWN_OPTION", codes(static_rejections(candidate, env, core())))
        candidate.supplied_option_id = "payment_option_01"
        env.options_by_id["payment_option_01"].request_id = "other-request"
        self.assertIn("OPTION_WRONG_REQUEST", codes(static_rejections(candidate, env, core())))

    def test_cap_is_payment_count_not_calendar_duration(self):
        offer = option(number_of_payments=4, payment_frequency_days=28,
                       payment_amount=money(2500), financing_fee=money(0), total_payable_amount=money(10000))
        candidate, env = self.installment(offer)
        env.request.desired_completion_date = H
        result = codes(static_rejections(candidate, env, core()))
        self.assertIn("INSTALLMENT_CAP_EXCEEDED", result)
        env.preferences.max_installment_months = 4
        self.assertNotIn("INSTALLMENT_CAP_EXCEEDED", codes(static_rejections(candidate, env, core())))
        for cap in (None, 0, -1, True):
            env.preferences.max_installment_months = cap
            self.assertIn("INSTALLMENT_CAP_UNRESOLVED", codes(static_rejections(candidate, env, core())))

    def test_malformed_schedule_is_not_sorted_or_repaired(self):
        schedule = (payment(R + timedelta(days=1), 5000, "a"), payment(R, 5000, "b"))
        candidate = plan(payments=schedule)
        self.assertIn("NONCHRONOLOGICAL", codes(static_rejections(candidate, envelope(), core())))
        self.assertEqual(candidate.payments, schedule)
        bad = NS(date=R, amount=NS(currency="USD", minor=-1), payment_id="negative")
        self.assertIn("INVALID_PAYMENT_AMOUNT", codes(static_rejections(plan(payments=(bad,)), envelope(), core())))

    def test_action_subtype_permission_and_protection_independently_checked(self):
        candidate = plan(changes=(Stop("event"),))
        for changes, expected in (({"category": "rent"}, "ACTION_PROTECTED"),
                                  ({"flexibility": "reducible"}, "ACTION_SUBTYPE_FORBIDDEN"),
                                  ({"allowed_actions": ("reduce_to",)}, "ACTION_NOT_ALLOWED")):
            with self.subTest(changes=changes):
                self.assertIn(expected, codes(static_rejections(candidate, envelope(), core(change_targets=(target(**changes),)))))

    def test_reduce_floor_is_exact_and_blank_is_not_zero(self):
        c = core(change_targets=(target(),))
        self.assertEqual(static_rejections(plan(changes=(ReduceTo("event", money(500)),)), envelope(), c), ())
        self.assertIn("ACTION_BELOW_FLOOR", codes(static_rejections(
            plan(changes=(ReduceTo("event", money(499)),)), envelope(), c)))
        c.change_targets[0].floor = None
        self.assertIn("ACTION_FLOOR_UNKNOWN", codes(static_rejections(
            plan(changes=(ReduceTo("event", money(0)),)), envelope(), c)))

    def test_four_changes_and_alias_overlap_are_rejected(self):
        targets = tuple(target(anchor_event_id=f"e{i}", series_id=f"s{i}", eligible_occurrence_ids=(f"o{i}",)) for i in range(4))
        candidate = plan(changes=tuple(Stop(t.anchor_event_id) for t in targets))
        self.assertIn("ACTION_LIMIT_EXCEEDED", codes(static_rejections(candidate, envelope(), core(change_targets=targets))))
        aliases = (target(anchor_event_id="a"), target(anchor_event_id="b"))
        result = codes(static_rejections(plan(changes=(Stop("a"), Stop("b"))), envelope(), core(change_targets=aliases)))
        self.assertIn("ACTION_SERIES_CONFLICT", result)
        self.assertIn("ACTION_OVERLAPPING_OCCURRENCES", result)

    def test_stop_and_reduce_same_event_conflict_and_no_widening(self):
        candidate = plan(changes=(Stop("event"), ReduceTo("event", money(500))))
        self.assertIn("ACTION_CONFLICT", codes(static_rejections(candidate, envelope(), core(change_targets=(target(),)))))
        self.assertIn("ACTION_TARGET_UNRESOLVED", codes(static_rejections(plan(changes=(Stop("invented"),)), envelope(), core())))

    def test_missing_or_duplicate_payment_ids_are_structured_rejections(self):
        malformed = NS(date=R, amount=money(10000), payment_id=[])
        self.assertIn("PAYMENT_ID_MISSING", codes(static_rejections(
            plan(payments=(malformed,)), envelope(), core())))
        duplicate = (payment(R, 4000, "same"), payment(R + timedelta(days=10), 6000, "same"))
        self.assertIn("PAYMENT_ID_DUPLICATE", codes(static_rejections(
            plan(method="partial_payment", payments=duplicate), envelope(), core())))

    def test_now_capacity_is_not_later_wait_eligibility(self):
        c = core()
        c.capacity.amount_safe_to_pay = money(10000)
        c.capacity.earliest_date_for_full_payment = R
        self.assertIn("WAIT_NOT_LATER_CAPACITY", codes(static_rejections(
            plan(method="wait", payments=(payment(R + timedelta(days=1)),)), envelope(), c)))

    def test_candidate_checks_do_not_mutate_capacity_or_core(self):
        c = core(change_targets=(target(),))
        env = envelope()
        original = deepcopy((c, env))
        changed = plan(changes=(Stop("event"),))
        self.assertEqual(static_rejections(changed, env, c), ())
        self.assertEqual((c, env), original)
        self.assertEqual(static_rejections(plan(), env, c), static_rejections(plan(), envelope(), core()))
