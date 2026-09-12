"""Analytic planning integration: real contracts, recurrence and the sole replay.

Only the not-yet-routed integration envelope has a test-only attribute view.
No financial simulator/contract is stubbed; wraps= below counts real calls.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from itertools import permutations
import unittest
from unittest.mock import patch

from buy_wait.contracts import (
    FactBatch, FinancialInput, FinancialProfile, ForecastPolicy, Money, Payment,
    Plan, ReduceTo, SourceRef, Stop,
)
from buy_wait.core import build_financial_context, replay_financial_plan
from buy_wait.core.forecast import context_fingerprint
from buy_wait.planning.evaluator import evaluate_candidate
from buy_wait.planning.generator import action_families, generate_candidates, one_shot_payment
from buy_wait.planning.ranking import (
    best_ranked_candidates, build_search_coverage, rank_validated, recommendation_status,
)
from test_core_explicit import R, build, event, money
from test_core_recurrence import flexible_core, monthly
from test_planning_support import envelope, option, preferences, request


def view(core, *, methods=("full_payment", "partial_payment", "installments"),
         options=(), deadline=None, allows_partial=True, cap=3, **prefs):
    return envelope(
        request=request(request_id=core.request_id, user_id=core.user_id, request_date=core.request_date,
                        requested_amount=core.requested, desired_completion_date=deadline or core.request_date + timedelta(days=70),
                        allows_partial_payment=allows_partial),
        preferences=preferences(methods=frozenset(methods), max_installment_months=cap, **prefs),
        options_by_id={o.payment_option_id: o for o in options},
    )


def offer(core, *, oid="payment_option_01", fee=0, count=3, interval=30, first=None):
    total = core.requested.minor + fee
    assert total % count == 0
    return option(payment_option_id=oid, request_id=core.request_id, payment_amount=Money("USD", total // count),
                  number_of_payments=count, first_payment_date=first or core.request_date,
                  payment_frequency_days=interval, financing_fee=Money("USD", fee),
                  total_payable_amount=Money("USD", total))


def full_plan(core, env, **fields):
    base = dict(candidate_id="analytic", method="full_payment", core_hash=core.context_hash,
                envelope_hash=env.context_hash, payments=one_shot_payment(
                    env.request, env.preferences, payment_date=core.request_date, horizon_end=core.horizon_end))
    base.update(fields)
    return Plan(**base)


def run_phases(core, env, *, limit=None):
    """Test driver demonstrating integration's intended orchestration boundary."""
    first = generate_candidates(env, core, max_candidates=limit)
    validations = tuple(evaluate_candidate(p, env, core) for p in first["plans"])
    witnesses = tuple(v["plan"].candidate_id for v in best_ranked_candidates(validations))
    batches = (first,)
    if not witnesses:
        changed = generate_candidates(env, core, phase="with_changes", max_candidates=limit)
        batches += (changed,)
        validations += tuple(evaluate_candidate(p, env, core) for p in changed["plans"])
    coverage = build_search_coverage(batches, validations, envelope=env, core=core,
                                     pruning_witness_plan_ids=witnesses)
    ranked = rank_validated(validations, search=coverage)
    return batches, validations, coverage, ranked, recommendation_status(ranked, core)


class ReplayIntegrationTests(unittest.TestCase):
    def test_one_actual_replay_and_original_capacity_hash_unchanged(self):
        core = build()
        env = view(core)
        saved = deepcopy(core)
        with patch("buy_wait.planning.evaluator.replay_financial_plan", wraps=replay_financial_plan) as replay:
            result = evaluate_candidate(full_plan(core, env), env, core)
        self.assertEqual(replay.call_count, 1)
        self.assertEqual(result["outcome"], "valid")
        self.assertEqual(result["minimum_available"], money(400))
        self.assertIs(result["capacity"], core.capacity)
        self.assertEqual(core, saved)
        self.assertEqual(core.context_hash, context_fingerprint(core))
        self.assertEqual(result["safety"].forecast.end, R + timedelta(days=90))

    def test_static_rejection_does_not_run_cash_engine(self):
        core = build()
        env = view(core)
        with patch("buy_wait.planning.evaluator.replay_financial_plan", wraps=replay_financial_plan) as replay:
            result = evaluate_candidate(full_plan(core, env, envelope_hash="stale"), env, core)
        self.assertEqual(replay.call_count, 0)
        self.assertEqual(result["outcome"], "invalid")
        self.assertFalse(result["eligible"])

    def test_same_day_debit_breach_before_income_and_payment_cannot_be_erased(self):
        core = build((event("bill", 100, 0), event("salary", 400, 0, direction="credit", category="salary")),
                     balance=350, requested=100)
        env = view(core)
        result = evaluate_candidate(full_plan(core, env), env, core)
        self.assertEqual(result["outcome"], "invalid")
        self.assertTrue(result["first_breach"]["verified"])
        self.assertEqual(result["first_breach"]["date"], R)
        self.assertEqual(result["first_breach"]["phase"], "debit")
        self.assertEqual(result["first_breach"]["balance_minor"], 25000)
        self.assertEqual(result["first_breach"]["shortfall_minor"], 5000)
        self.assertGreater(result["safety"].forecast.checkpoints[-1].available_minor, 30000)

    def test_safe_same_day_payment_can_use_income_after_required_debits(self):
        core = build((event("bill", 100, 0), event("salary", 400, 0, direction="credit", category="salary")),
                     balance=600, requested=500)
        env = view(core)
        result = evaluate_candidate(full_plan(core, env), env, core)
        self.assertEqual(result["outcome"], "valid")
        phases = [p.phase for p in result["safety"].forecast.checkpoints if p.date == R]
        self.assertLess(phases.index("debit"), phases.index("credit"))
        self.assertLess(phases.index("credit"), phases.index("payment"))
        self.assertEqual(result["minimum_available"], money(400))

    def test_day_90_expense_is_checked_after_purchase_completion(self):
        core = build((event("late", 200, 90),))
        env = view(core)
        result = evaluate_candidate(full_plan(core, env), env, core)
        self.assertEqual(result["completion_date"], R)
        self.assertEqual(result["first_breach"]["date"], R + timedelta(days=90))
        self.assertEqual(result["minimum_available_minor"], 20000)
        self.assertEqual(result["outcome"], "invalid")

    def test_prescribed_partial_is_replayed_and_beats_later_one_shot(self):
        core = build((event("bill", 600, 1), event("salary", 700, 2, direction="credit", category="salary")))
        env = view(core)
        batches, results, coverage, ranked, status = run_phases(core, env)
        self.assertEqual(len(batches), 1)
        self.assertEqual(coverage["changed_phase"], "skipped_by_P2")
        self.assertEqual([v["outcome"] for v in results], ["invalid", "valid", "valid"])
        selected = ranked["winner"]["plan"]
        self.assertEqual(selected.method, "partial_payment")
        self.assertEqual([(p.date, p.amount) for p in selected.payments], [(R, money(100)), (R + timedelta(days=2), money(500))])
        self.assertEqual(status["affordability_status"], "affordable_with_plan")
        self.assertEqual(status["earliest_date_for_full_payment"], R + timedelta(days=2))

    def test_fee_is_paid_exactly_once_on_real_replay(self):
        core = build()
        env = view(core, methods=("installments",), options=(offer(core, fee=3000),))
        _, _, _, ranked, status = run_phases(core, env)
        result = ranked["winner"]
        self.assertEqual(result["actual_total_paid"], money(630))
        self.assertEqual(result["minimum_available"], money(370))
        self.assertEqual(sum(p.cash_delta_minor for p in result["safety"].forecast.checkpoints if p.phase == "payment"), -63000)
        self.assertEqual(status["affordability_status"], "affordable_with_plan")
        self.assertEqual(status["earliest_date_for_full_payment"], R)
        self.assertEqual(status["amount_safe_to_pay"], money(600))

    def test_zero_request_remains_nonnegative_not_an_invented_positive_minimum(self):
        core = build(requested=0)
        env = view(core)
        _, _, _, ranked, status = run_phases(core, env)
        self.assertEqual(ranked["winner"]["actual_total_paid"], money(0))
        self.assertEqual(status["affordability_status"], "affordable_now")
        self.assertEqual(status["amount_safe_to_pay"], money(0))

    def test_unknown_debit_is_unverifiable_not_zero_or_no_plan(self):
        core = build((event("missing", None, 1),))
        env = view(core)
        result = evaluate_candidate(full_plan(core, env), env, core)
        self.assertEqual(result["outcome"], "unverifiable")
        self.assertIsNone(result["capacity"].amount_safe_to_pay)
        self.assertIn("MISSING_AMOUNT", {i["code"] for i in result["issues"]})
        _, _, _, ranked, status = run_phases(core, env)
        self.assertEqual(ranked["resolution"], "incomplete_evidence_or_search")
        self.assertIsNone(status["amount_safe_to_pay"])
        self.assertIsNone(status["affordability_status"])

    def test_explicit_only_never_certifies_a_complete_forecast(self):
        core = build(policy=ForecastPolicy(projection_mode="explicit_only"))
        _, _, _, ranked, status = run_phases(core, view(core))
        self.assertIn("EXPLICIT_ONLY_NOT_FULL_PROOF", core.capacity.issue_codes)
        self.assertIsNone(ranked["winner"])
        self.assertIsNone(status["affordability_status"])

    def test_stale_core_hash_is_reported_by_actual_replay(self):
        core = build()
        env = view(core)
        stale = replace(core, anchor=replace(core.anchor, minimum_minor=1))
        result = evaluate_candidate(full_plan(core, env), env, stale)
        self.assertEqual(result["outcome"], "unverifiable")
        self.assertIn("STALE_CORE_CONTEXT", {i["code"] for i in result["issues"]})

    def test_incoherent_partial_metrics_fail_closed_instead_of_inventing_a_schedule(self):
        core = build((event("bill", 600, 1), event("salary", 700, 2, direction="credit", category="salary")))
        # Fault injection: preserve a valid content fingerprint but deliberately
        # supply S=200 where the independent analytic bound is S=100.
        wrong = replace(core, capacity=replace(core.capacity, amount_safe_to_pay=money(200)))
        fingerprint = context_fingerprint(wrong)
        wrong = replace(wrong, context_hash=fingerprint, capacity=replace(wrong.capacity, context_hash=fingerprint))
        env = view(wrong)
        partial = next(p for p in generate_candidates(env, wrong)["plans"] if p.method == "partial_payment")
        result = evaluate_candidate(partial, env, wrong)
        self.assertEqual(result["outcome"], "unverifiable")
        self.assertIn("PARTIAL_CAPACITY_INCOHERENCE", {i["code"] for i in result["issues"]})
        self.assertEqual(partial.payments[0].amount, money(200))  # No silent repair.

    def test_conservative_credit_exclusion_is_still_a_proof(self):
        core = build((event("foreign_salary", 1000, 1, direction="credit", category="salary", currency="EUR"),))
        env = view(core)
        result = evaluate_candidate(full_plan(core, env), env, core)
        self.assertEqual(result["outcome"], "valid")
        self.assertEqual(result["financial_proof_status"], "conservative_bound")
        self.assertTrue(any(i["severity"] == "warning" for i in result["issues"]))


class ActionFamilyIntegrationTests(unittest.TestCase):
    def flexible(self, requested=600, **kwargs):
        return flexible_core(monthly(category="dining", flexible=True), requested=requested, **kwargs)

    def env(self, core, **prefs):
        return view(core, deadline=R, reducible_categories=frozenset({"dining"}),
                    stoppable_categories=frozenset({"dining"}), **prefs)

    def test_all_one_two_three_series_supports_exist_but_never_four(self):
        categories = frozenset(f"subscription_{i}" for i in range(4))
        events = tuple(replace(e, event_id=f"s{k}-{i}", category=f"subscription_{k}",
                               description=f"Service {k}", source=SourceRef("csv", f"s{k}-{i}"))
                       for k in range(4) for i, e in enumerate(monthly(flexible=True)))
        profile = FinancialProfile("u", "USD", money(3000), money(300),
                                   reducible_categories=categories, stoppable_categories=categories)
        data = FinancialInput("r", "u", R, money(2000), profile, events)
        core = build_financial_context(data, FactBatch("r", "u"), policy=ForecastPolicy())
        env = view(core, reducible_categories=categories, stoppable_categories=categories)
        result = action_families(env, core)
        self.assertEqual(len(core.series), 4)
        # Four series, each with three aliases x two modes: 4*6 + 6*36 + 4*216.
        self.assertEqual(len(result["families"]), 1104)
        self.assertEqual({len(f["changes"]) for f in result["families"]}, {1, 2, 3})
        self.assertTrue(all(len(f["series_ids"]) == len(set(f["series_ids"])) for f in result["families"]))
        self.assertEqual(len({f["family_id"] for f in result["families"]}), 1104)

    def test_later_changed_one_shot_has_with_plan_wait_status_and_unchanged_full_date(self):
        core = flexible_core((*monthly(category="dining", flexible=True),
                              event("salary", 400, 2, direction="credit", category="salary")),
                             balance=600, requested=600)
        env = view(core, deadline=R + timedelta(days=2), reducible_categories=frozenset({"dining"}),
                   stoppable_categories=frozenset({"dining"}))
        _, _, _, ranked, status = run_phases(core, env)
        self.assertEqual(ranked["winner"]["first_payment_date"], R + timedelta(days=2))
        self.assertEqual(status["recommended_payment_method"], "wait")
        self.assertEqual(status["affordability_status"], "affordable_with_plan")
        self.assertEqual(status["amount_safe_to_pay"], money(300))
        self.assertIsNone(status["earliest_date_for_full_payment"])

    def test_no_change_wait_dominates_a_verified_earlier_changed_payment(self):
        # Feb 2, Mar 2, Apr 2 AND May 2 (inclusive day 90) each cost 400.
        core = flexible_core((*monthly((400, 400, 400), category="dining", dom=2, flexible=True),
                              event("salary", 1400, 5, direction="credit", category="salary")),
                             balance=1500, requested=900)
        env = view(core, methods=("full_payment",), allows_partial=False,
                   reducible_categories=frozenset({"dining"}), stoppable_categories=frozenset({"dining"}))
        batches, _, coverage, ranked, _ = run_phases(core, env)
        early = evaluate_candidate(full_plan(core, env, changes=(Stop("h0"),)), env, core)
        self.assertEqual(early["outcome"], "valid")
        self.assertEqual(early["first_payment_date"], R)
        self.assertEqual(ranked["winner"]["first_payment_date"], R + timedelta(days=5))
        self.assertEqual(best_ranked_candidates((early, ranked["winner"])), (ranked["winner"],))
        self.assertEqual(len(batches), 1)
        self.assertEqual(coverage["changed_phase"], "skipped_by_P2")

    def test_aliases_and_amount_domains_are_preserved_without_double_saving(self):
        core = self.flexible()
        families = action_families(self.env(core), core)
        self.assertEqual(families["coverage"], "optimum_preserving_representatives")
        self.assertEqual(len(families["families"]), 6)  # 3 real aliases x 2 legal modes.
        self.assertTrue(all(len(f["changes"]) == 1 for f in families["families"]))
        reductions = [f for f in families["families"] if isinstance(f["changes"][0], ReduceTo)]
        self.assertEqual({f["amount_domains"][0]["minimum_minor"] for f in reductions}, {4000})
        self.assertEqual({f["amount_domains"][0]["maximum_minor"] for f in reductions}, {10000})

    def test_changed_full_plan_uses_real_negative_expense_deltas_and_original_capacity(self):
        core = self.flexible()
        saved = deepcopy(core)
        batches, _, coverage, ranked, status = run_phases(core, self.env(core))
        self.assertEqual(len(batches), 2)
        self.assertEqual(coverage["changed_phase"], "complete")
        winner = ranked["winner"]
        self.assertEqual(winner["outcome"], "valid")
        self.assertEqual(winner["plan"].changes, (Stop("h0"),))
        self.assertEqual([d for _, d in winner["safety"].occurrence_deltas], [-10000] * 3)
        self.assertEqual(winner["minimum_available"], money(400))
        self.assertEqual(status["affordability_status"], "affordable_with_plan")
        self.assertEqual(status["recommended_payment_method"], "full_payment")
        self.assertEqual(status["amount_safe_to_pay"], money(400))
        self.assertIsNone(status["earliest_date_for_full_payment"])
        self.assertEqual(core, saved)

    def test_floor_reduction_is_a_feasible_representative_not_a_claim_about_all_amounts(self):
        core = self.flexible(requested=580)
        env = view(core, deadline=R, reducible_categories=frozenset({"dining"}), stoppable_categories=frozenset())
        _, _, _, ranked, _ = run_phases(core, env)
        winner = ranked["winner"]
        self.assertEqual(winner["plan"].changes, (ReduceTo("h0", money(40)),))
        self.assertEqual(winner["minimum_available"], money(300))
        larger = replace(winner["plan"], changes=(ReduceTo("h0", money(41)),))
        self.assertEqual(evaluate_candidate(larger, env, core)["outcome"], "invalid")

    def test_protected_and_pending_occurrences_do_not_enter_change_effects(self):
        protected = self.flexible(protect=True)
        self.assertEqual(action_families(self.env(protected), protected)["families"], ())
        core = flexible_core((*monthly(category="dining", flexible=True),
                              event("committed", 100, 14, category="dining", status="pending")), requested=600)
        _, _, _, ranked, _ = run_phases(core, self.env(core))
        self.assertNotIn("cash:committed", {oid for oid, _ in ranked["winner"]["safety"].occurrence_deltas})
        self.assertEqual(ranked["winner"]["minimum_available"], money(300))

    def test_partial_budget_floor_uses_core_residual_not_a_retroactive_refund(self):
        history = tuple(replace(event(f"h{i}", 100, offset, category="dining", status="settled"),
                                description=f"Merchant {i}", flexibility="reducible_or_stoppable",
                                minimum_allowed_amount=money(20)) for i, offset in enumerate((-15, -8, -1)))
        core = flexible_core((*history, event("committed", 40, 6, category="dining", status="pending")),
                             balance=5000, requested=4400)
        env = view(core, deadline=R, reducible_categories=frozenset({"dining"}), stoppable_categories=frozenset())
        _, _, _, ranked, _ = run_phases(core, env)
        winner = ranked["winner"]
        self.assertEqual(winner["minimum_available"], money(320))
        self.assertEqual(sorted(delta for _, delta in winner["safety"].occurrence_deltas), [-8000] * 12 + [-6000])
        self.assertNotIn("cash:committed", {oid for oid, _ in winner["safety"].occurrence_deltas})


class SearchAndStatusIntegrationTests(unittest.TestCase):
    def test_ambiguous_eligible_offer_blocks_optimum_but_forbidden_offer_does_not(self):
        core = build()
        broken = offer(core)
        broken.total_payable_amount = money(601)
        env = view(core, options=(broken,))
        _, _, _, ranked, status = run_phases(core, env)
        self.assertTrue(ranked["best_set"])
        self.assertIsNone(ranked["winner"])
        self.assertIsNone(status["affordability_status"])
        forbidden = view(core, methods=("full_payment",), options=(broken,))
        batches, _, coverage, ranked, status = run_phases(core, forbidden)
        self.assertEqual(batches[0]["coverage"], "exhaustive")
        self.assertEqual(coverage["unresolved_exclusions"], ())
        self.assertEqual(status["affordability_status"], "affordable_now")

    def test_limit_is_incomplete_search_not_unaffordability(self):
        core = build()
        _, _, coverage, ranked, status = run_phases(core, view(core), limit=0)
        self.assertEqual(coverage["generated_count"], 0)
        self.assertEqual(ranked["resolution"], "incomplete_evidence_or_search")
        self.assertIsNone(status["affordability_status"])
        self.assertEqual(status["amount_safe_to_pay"], money(600))

    def test_same_count_different_validated_plan_is_not_complete_coverage(self):
        core = build()
        env = view(core)
        batch = generate_candidates(env, core)
        original = batch["plans"][0]
        changed = replace(original, candidate_id="different")
        results = (evaluate_candidate(changed, env, core),)
        coverage = build_search_coverage((batch,), results, envelope=env, core=core,
                                         pruning_witness_plan_ids=(original.candidate_id,))
        ranked = rank_validated(results, search=coverage)
        self.assertIn("GENERATED_VALIDATED_PLAN_MISMATCH", {i["code"] for i in ranked["issues"]})
        self.assertIsNone(ranked["winner"])

    def test_no_change_generation_without_a_validated_witness_cannot_skip_changes(self):
        core = build()
        env = view(core)
        batch = generate_candidates(env, core)
        coverage = build_search_coverage((batch,), (), envelope=env, core=core,
                                         pruning_witness_plan_ids=(batch["plans"][0].candidate_id,))
        ranked = rank_validated((), search=coverage)
        self.assertIn("NO_VALID_P2_WITNESS", {i["code"] for i in ranked["issues"]})
        self.assertIsNone(ranked["winner"])

    def test_natural_offer_order_and_selection_are_permutation_invariant(self):
        core = build()
        offers = tuple(offer(core, oid=f"payment_option_{i}") for i in (100, 99, 101))
        selections, batches = [], []
        for permuted in permutations(offers):
            env = view(core, methods=("installments",), options=permuted)
            generated, _, _, ranked, _ = run_phases(core, env)
            batches.append(tuple(p.candidate_id for p in generated[0]["plans"]))
            selections.append(ranked["winner"]["plan"].payment_option_id)
        self.assertEqual(set(selections), {"payment_option_99"})
        self.assertTrue(all(batch == batches[0] for batch in batches))

    def test_full_date_after_deadline_is_preserved_but_no_wait_recommended(self):
        core = build((event("salary", 700, 10, direction="credit", category="salary"),), balance=500)
        env = view(core, deadline=R + timedelta(days=2), methods=("full_payment",))
        _, _, _, ranked, status = run_phases(core, env)
        self.assertEqual(ranked["resolution"], "no_valid_plan")
        self.assertEqual(status["affordability_status"], "affordable_later")
        self.assertEqual(status["recommended_payment_method"], "not_recommended")
        self.assertEqual(status["earliest_date_for_full_payment"], R + timedelta(days=10))

    def test_today_capacity_without_accepted_plan_is_an_explicit_vocabulary_gap(self):
        core = build()
        env = view(core, methods=("installments",))  # No supplied installment exists.
        _, _, _, ranked, status = run_phases(core, env)
        self.assertEqual(ranked["resolution"], "no_valid_plan")
        self.assertIsNone(status["affordability_status"])
        self.assertEqual(status["earliest_date_for_full_payment"], R)
        self.assertEqual(status["issues"][0]["code"], "FALLBACK_STATUS_UNSPECIFIED")

    def test_known_no_capacity_is_not_affordable_without_inventing_unknowns(self):
        core = build(balance=300)
        _, _, _, ranked, status = run_phases(core, view(core))
        self.assertEqual(ranked["resolution"], "no_valid_plan")
        self.assertEqual(status["affordability_status"], "not_affordable")
        self.assertEqual(status["amount_safe_to_pay"], money(0))


if __name__ == "__main__":
    unittest.main()
