"""Synthetic test fixtures only; never imported by production decision code."""
import csv
import sys
import tempfile
import unittest
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
ROOT = CODE.parent
sys.path.insert(0, str(CODE))
from buy_wait.data import OUTPUT_FIELDS, REQUEST_FIELDS, SAMPLE_FIELDS, SCHEMAS


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def blank(table, **fields):
    return {**dict.fromkeys(SCHEMAS[table], ""), **fields}


def request(index=1):
    return dict(zip(REQUEST_FIELDS, (
        f"request_{index}", f"user_{index}", "2030-01-01", "purchase", str(50 * index),
        "2030-01-20", "true", "Can I make this synthetic payment?",
    )))


def prediction(req):
    return dict(zip(OUTPUT_FIELDS, (
        req["request_id"], req["requested_amount"], "affordable_now", "full_payment",
        f"{req['request_date']}:{req['requested_amount']}", req["request_date"], "none",
        "Synthetic fixture only; no financial model is asserted.",
    )))


def fixture_dataset(root):
    requests = [request(1), request(2)]
    tables = {name: [] for name in SCHEMAS}
    tables["requests.csv"] = requests
    for i in (1, 2):
        tables["financial_profiles.csv"].append(blank(
            "financial_profiles.csv", user_id=f"user_{i}", home_currency="INR" if i == 1 else "USD",
            current_available_balance="1000", minimum_balance_to_keep="300",
            payment_methods_user_will_consider="full_payment|partial_payment|installments", max_installment_months="3",
        ))
        for n in (1, 2):
            tables["request_payment_options.csv"].append(blank(
                "request_payment_options.csv", payment_option_id=f"option_{i}_{n}", request_id=f"request_{i}",
                payment_method="full_payment" if n == 1 else "installments", payment_amount=str(50 * i) if n == 1 else str(25 * i + 1),
                number_of_payments=str(n), first_payment_date="2030-01-01", payment_frequency_days="" if n == 1 else "5",
                financing_fee="0" if n == 1 else "2", total_payable_amount=str(50 * i) if n == 1 else str(50 * i + 2),
            ))
    for i in (1, 2):
        tables["financial_events.csv"].append(blank(
            "financial_events.csv", event_id=f"event_{i}", user_id="user_1", event_type="expense", description="Synthetic historic bill",
            category="groceries", direction="debit", amount="" if i == 1 else "25.00", currency="INR", event_date="2029-12-01",
            settlement_date="2029-12-02", status="settled", flexibility="fixed",
        ))
    for i, req, event, user in ((1, "", "", "user_1"), (2, "request_1", "", "user_1"),
                                 (3, "", "event_1", "user_1"), (4, "request_2", "", "user_2")):
        tables["messages.csv"].append(blank("messages.csv", message_id=f"message_{i}", user_id=user,
                                          request_id=req, related_event_id=event, sent_at="2029-12-30T10:00:00Z",
                                          source_type="merchant", message_text="Synthetic factual note."))
    tables["images.csv"] = [blank("images.csv", image_id="image_1", user_id="user_1", request_id="request_1", related_event_id="event_1")]
    tables["exchange_rates.csv"] = [dict(zip(SCHEMAS["exchange_rates.csv"], ("2029-12-02", "USD", "INR", "80")))]
    for name, rows in tables.items():
        write_csv(root / name, SCHEMAS[name], rows)
    write_csv(root / "sample_requests.csv", SAMPLE_FIELDS, [{**r, **prediction(r)} for r in requests])
    write_csv(root / "output.csv", OUTPUT_FIELDS, [dict.fromkeys(OUTPUT_FIELDS, "")])
    return tables


class IntegrationCase(unittest.TestCase):
    def setUp(self):
        # Harness task confinement: all temporary test writes stay in this checkout.
        parent = ROOT / ".test-tmp"
        parent.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="integration-", dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.dataset = self.work / "dataset"
        self.tables = fixture_dataset(self.dataset)
