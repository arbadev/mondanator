"""Test-only doubles while Firstmate arranges the committed core handoff.

No fake contracts are installed by production code. When canonical contracts are
present, these tests use their Money/Payment/Stop/ReduceTo classes instead. The
namespace fixtures exercise static planning and comparison, not financial safety.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import importlib
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch


try:
    CONTRACTS = importlib.import_module("buy_wait.contracts")
    USING_TEST_CONTRACTS = False
except ModuleNotFoundError as exc:
    if exc.name != "buy_wait.contracts":
        raise
    USING_TEST_CONTRACTS = True
    CONTRACTS = ModuleType("buy_wait.contracts")

    @dataclass(frozen=True)
    class TestMoney:
        currency: str
        minor: int

    @dataclass(frozen=True)
    class TestPayment:
        date: date
        amount: TestMoney
        payment_id: str

    @dataclass(frozen=True)
    class TestStop:
        anchor_event_id: str

    @dataclass(frozen=True)
    class TestReduceTo:
        anchor_event_id: str
        new_amount: TestMoney

    CONTRACTS.Money = TestMoney
    CONTRACTS.Payment = TestPayment
    CONTRACTS.Stop = TestStop
    CONTRACTS.ReduceTo = TestReduceTo

Money = CONTRACTS.Money
Payment = CONTRACTS.Payment
Stop = CONTRACTS.Stop
ReduceTo = CONTRACTS.ReduceTo
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
    fields = dict(request_id="req", user_id="user", request_date=R, requested=money(10000),
                  context_hash="core-hash", horizon_end=H, change_targets=(),
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
    fields = dict(plan_id="plan", request_id="req", core_hash="core-hash",
                  envelope_hash="env-hash", method="full_payment", payments=(payment(),),
                  changes=(), supplied_option_id=None)
    fields.update(overrides)
    return NS(**fields)


def target(**overrides):
    fields = dict(anchor_event_id="event", series_id="series", category="streaming",
                  flexibility="reducible_or_stoppable", floor=money(500),
                  allowed_actions=("stop", "reduce_to"), eligible_occurrence_ids=("future",))
    fields.update(overrides)
    return NS(**fields)


def validation(**overrides):
    fields = dict(plan=plan(), outcome="valid", eligible=True, completes_by_deadline=True,
                  replay_performed=True, financial_proof_status="resolved_under_policy",
                  actual_total_paid=money(10000), first_payment_date=R, payment_count=1,
                  has_spending_changes=False)
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
    def setUp(self):
        super().setUp()
        # Scoped replacement is exclusively a test fixture, never a runtime fallback.
        if USING_TEST_CONTRACTS:
            guard = patch.dict("sys.modules", {"buy_wait.contracts": CONTRACTS})
            guard.start()
            self.addCleanup(guard.stop)
