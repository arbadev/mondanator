"""Real canonical financial records plus test-only envelope/result views.

No production contract or replay is replaced. Namespace views stand in only for
integration-owned request/preferences/options and derived comparison fixtures.
"""
from datetime import date, timedelta
from types import SimpleNamespace as NS
import unittest

from buy_wait.contracts import Money, Payment, Plan, ReduceTo, Stop

USING_TEST_CONTRACTS = False
R = date(2030, 1, 1)
H = R + timedelta(days=90)


def money(minor, currency="USD"):
    return Money(currency=currency, minor=minor)


def payment(day=R, minor=10000, payment_id="p1", currency="USD"):
    return Payment(date=day, amount=money(minor, currency), payment_id=payment_id)


def request(**overrides):
    fields = dict(request_id="req", user_id="user", request_date=R,
                  requested_amount=money(10000), desired_completion_date=R + timedelta(days=70),
                  allows_partial_payment=True, request_type="purchase", request_text="Synthetic")
    fields.update(overrides)
    return NS(**fields)


def preferences(**overrides):
    fields = dict(methods=frozenset({"full_payment", "partial_payment", "installments"}),
                  max_installment_months=3, protected_categories=frozenset({"rent"}),
                  reducible_categories=frozenset({"streaming", "dining"}),
                  stoppable_categories=frozenset({"streaming", "cloud"}))
    fields.update(overrides)
    return NS(**fields)


def option(**overrides):
    fields = dict(payment_option_id="payment_option_01", request_id="req",
                  payment_method="installments", payment_amount=money(3500),
                  number_of_payments=3, first_payment_date=R, payment_frequency_days=30,
                  financing_fee=money(500), total_payable_amount=money(10500))
    fields.update(overrides)
    return NS(**fields)


def core(**overrides):
    """Static-check view only; actual replay tests build a genuine CoreContext."""
    fields = dict(request_id="req", user_id="user", request_date=R, requested=money(10000),
                  context_hash="core-hash", horizon_end=H, change_targets=(), issues=(),
                  capacity=NS(amount_safe_to_pay=money(4000),
                              earliest_date_for_full_payment=R + timedelta(days=10),
                              baseline_feasible=True, proof_status="resolved_under_policy"))
    fields.update(overrides)
    return NS(**fields)


def envelope(**overrides):
    fields = dict(request=request(), preferences=preferences(), options_by_id={}, context_hash="env-hash")
    fields.update(overrides)
    return NS(**fields)


def plan(**overrides):
    # Fixture keyword aliases preserve earlier case descriptions, never domain types.
    aliases = {"plan_id": "candidate_id", "supplied_option_id": "payment_option_id"}
    overrides = {aliases.get(key, key): value for key, value in overrides.items()}
    fields = dict(candidate_id="plan", core_hash="core-hash", envelope_hash="env-hash",
                  method="full_payment", payments=(payment(),), changes=(), payment_option_id=None)
    fields.update(overrides)
    return Plan(**fields)


def target(**overrides):
    fields = dict(anchor_event_id="event", series_id="series", category="streaming",
                  flexibility="reducible_or_stoppable", floor=money(500),
                  allowed_actions=("stop", "reduce_to"), eligible_occurrence_ids=("future",))
    fields.update(overrides)
    return NS(**fields)


def validation(**overrides):
    fields = dict(plan=plan(), request_id="req", outcome="valid", eligible=True,
                  completes_by_deadline=True, replay_performed=True,
                  financial_proof_status="resolved_under_policy", actual_total_paid=money(10000),
                  first_payment_date=R, payment_count=1, has_spending_changes=False)
    fields.update(overrides)
    return NS(**fields)


def search(results, **overrides):
    fields = dict(request_id="req", core_hash="core-hash", envelope_hash="env-hash",
                  no_change_phase_complete=True, changed_phase="complete",
                  generated_count=len(results), evaluated_count=len(results),
                  unresolved_exclusions=(), pruning_witness_plan_ids=())
    fields.update(overrides)
    return NS(**fields)


def codes(issues):
    return {issue["code"] for issue in issues}


class PlanningTestCase(unittest.TestCase):
    pass
