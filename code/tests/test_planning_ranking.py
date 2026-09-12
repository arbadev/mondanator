"""Behavioral comparator tests use validated-result doubles, not fake replay."""
from datetime import timedelta
from itertools import permutations

from buy_wait.planning.ranking import (
    best_ranked_candidates, option_id_key, published_rank_key,
    search_rejections, serialization_representative,
)
from test_planning_support import (
    R, PlanningTestCase, Stop, codes, money, plan, search, validation,
)


class RankingTests(PlanningTestCase):
    def assert_winner(self, winner, loser):
        for ordered in ((winner, loser), (loser, winner)):
            self.assertEqual(best_ranked_candidates(ordered), (winner,))

    def test_deadline_is_hard_not_merely_a_weight(self):
        ontime = validation(actual_total_paid=money(20000))
        late = validation(completes_by_deadline=False, actual_total_paid=money(1))
        self.assertLess(published_rank_key(ontime), published_rank_key(late))
        self.assert_winner(ontime, late)
        self.assertEqual(best_ranked_candidates((late,)), ())

    def test_no_changes_beats_lower_cost(self):
        unchanged = validation(actual_total_paid=money(11000))
        changed = validation(actual_total_paid=money(10000), has_spending_changes=True)
        self.assert_winner(unchanged, changed)

    def test_cost_beats_earlier_start(self):
        cheaper = validation(actual_total_paid=money(10000), first_payment_date=R + timedelta(days=10))
        earlier = validation(actual_total_paid=money(10001))
        self.assert_winner(cheaper, earlier)

    def test_earlier_start_beats_fewer_payments(self):
        earlier = validation(payment_count=3)
        later = validation(first_payment_date=R + timedelta(days=1), payment_count=1)
        self.assert_winner(earlier, later)

    def test_fewer_payments_when_earlier_criteria_equal(self):
        one = validation(payment_count=1)
        two = validation(payment_count=2)
        self.assert_winner(one, two)

    def test_numeric_option_id_is_final_published_criterion(self):
        ninety_nine = validation(plan=plan(supplied_option_id="payment_option_99"))
        hundred = validation(plan=plan(supplied_option_id="payment_option_100"))
        self.assert_winner(ninety_nine, hundred)
        self.assertEqual(option_id_key("payment_option_0002"), 2)
        with self.assertRaises(ValueError):
            option_id_key("invented")

    def test_option_id_does_not_beat_first_five_criteria(self):
        expensive_low_id = validation(plan=plan(supplied_option_id="payment_option_01"), actual_total_paid=money(10001))
        cheap_high_id = validation(plan=plan(supplied_option_id="payment_option_99"))
        self.assert_winner(cheap_high_id, expensive_low_id)

    def test_missing_id_is_not_transitive_equality_bridge(self):
        low = validation(plan=plan(plan_id="low", supplied_option_id="payment_option_02"))
        missing = validation(plan=plan(plan_id="partial", method="partial_payment"))
        high = validation(plan=plan(plan_id="high", supplied_option_id="payment_option_03"))
        for ordered in permutations((low, missing, high)):
            result = best_ranked_candidates(ordered)
            self.assertEqual({r.plan.plan_id for r in result}, {"low", "partial"})

    def test_same_option_action_count_is_unspecified_not_new_preference(self):
        one = validation(plan=plan(plan_id="one", supplied_option_id="payment_option_01",
                                   changes=(Stop("a"),)), has_spending_changes=True)
        two = validation(plan=plan(plan_id="two", supplied_option_id="payment_option_01",
                                   changes=(Stop("a"), Stop("b"))), has_spending_changes=True)
        self.assertEqual(published_rank_key(one), published_rank_key(two))
        self.assertEqual(len(best_ranked_candidates((one, two))), 2)

    def test_minimum_balance_or_earlier_finish_is_not_new_priority(self):
        preferred = validation(actual_total_paid=money(9999))
        expensive = validation()
        expensive.minimum_available_minor = 10**9
        expensive.completion_date = R
        preferred.minimum_available_minor = 1
        preferred.completion_date = R + timedelta(days=20)
        self.assert_winner(preferred, expensive)

    def test_unverified_ineligible_or_unreplayed_never_enters_ranking(self):
        for changes in ({"outcome": "invalid"}, {"eligible": False},
                        {"replay_performed": False}, {"financial_proof_status": "unresolved"}):
            with self.subTest(changes=changes):
                self.assertEqual(best_ranked_candidates((validation(**changes),)), ())

    def test_currency_or_context_mixing_is_rejected(self):
        for other in (validation(actual_total_paid=money(10000, "EUR")),
                      validation(plan=plan(core_hash="other")),
                      validation(plan=plan(request_id="another"))):
            with self.subTest(other=other), self.assertRaises(ValueError):
                best_ranked_candidates((validation(), other))

    def test_exact_minor_costs_and_boolean_changes(self):
        self.assert_winner(validation(actual_total_paid=money(10000)), validation(actual_total_paid=money(10001)))
        with self.assertRaises(ValueError):
            published_rank_key(validation(has_spending_changes=2))

    def test_search_coverage_requires_every_materialized_candidate(self):
        results = (validation(),)
        self.assertEqual(search_rejections(search(results), results), ())
        self.assertIn("UNEVALUATED_CANDIDATES", codes(search_rejections(search(results, generated_count=2), results)))
        self.assertIn("NO_CHANGE_SEARCH_INCOMPLETE", codes(search_rejections(search(results, no_change_phase_complete=False), results)))
        self.assertIn("CHANGED_SEARCH_INCOMPLETE", codes(search_rejections(search(results, changed_phase="incomplete"), results)))
        self.assertIn("UNRESOLVED_GENERATION_EXCLUSIONS", codes(search_rejections(
            search(results, unresolved_exclusions=("unknown floor",)), results)))

    def test_p2_requires_actual_valid_on_time_no_change_witness(self):
        result = validation(plan=plan(plan_id="witness"))
        results = (result,)
        complete = search(results, changed_phase="skipped_by_P2", pruning_witness_plan_ids=("witness",))
        self.assertEqual(search_rejections(complete, results), ())
        for changes in ({"outcome": "invalid"}, {"completes_by_deadline": False},
                        {"has_spending_changes": True}, {"replay_performed": False}):
            altered = validation(plan=result.plan, **changes)
            self.assertIn("NO_VALID_P2_WITNESS", codes(search_rejections(complete, (altered,))))
        complete.pruning_witness_plan_ids = ("nonexistent",)
        self.assertIn("NO_VALID_P2_WITNESS", codes(search_rejections(complete, results)))

    def test_unverifiable_candidate_blocks_optimality_not_just_safety(self):
        results = (validation(), validation(plan=plan(plan_id="unknown"), outcome="unverifiable"))
        self.assertEqual(len(best_ranked_candidates(results)), 1)
        self.assertIn("UNVERIFIED_CANDIDATE", codes(search_rejections(search(results), results)))

    def test_coverage_is_bound_to_same_request_and_context(self):
        results = (validation(),)
        self.assertIn("SEARCH_CONTEXT_MISMATCH", codes(search_rejections(search(results, core_hash="stale"), results)))

    def test_duplicate_validations_do_not_prove_complete_search(self):
        result = validation()
        results = (result, result)
        self.assertIn("DUPLICATE_VALIDATION_PLAN", codes(search_rejections(search(results), results)))

    def test_unknown_validation_outcome_does_not_prove_no_plan(self):
        results = (validation(outcome="not-a-contract-state"),)
        self.assertIn("UNKNOWN_VALIDATION_OUTCOME", codes(search_rejections(search(results), results)))

    def test_stable_serialization_only_after_undefined_final_tie(self):
        first = validation(plan=plan(plan_id="a", changes=(Stop("a"),)), has_spending_changes=True)
        second = validation(plan=plan(plan_id="b", changes=(Stop("b"),)), has_spending_changes=True)
        selections = []
        for ordered in permutations((first, second)):
            best = best_ranked_candidates(ordered)
            winner, reason = serialization_representative(best)
            self.assertEqual(reason, "serialization_only")
            selections.append(winner.plan.plan_id)
        self.assertEqual(selections, ["a", "a"])
        self.assertEqual(serialization_representative(()), (None, "no_valid_plan"))
        self.assertEqual(serialization_representative((first,)), (first, "unique"))
