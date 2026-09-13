"""Explicit, opt-in cache population using the canonical evidence components.

This command is intentionally not imported by the cache-only prediction CLI. It
has no default live path: a caller must provide both an approved bounded budget
and recorded account logging/authentication verification before it reads a key
from the process environment. It never opens a dotenv file.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

from buy_wait.data import DataError, Dataset, load_requests, project_request
from buy_wait.evidence import (
    EvidenceError, EvidenceIndex, ExtractionCache, Extractor, ExtractorConfig,
    OpenRouterClient, RunBudget, event_descriptors, load_api_key, resolve_image,
)
from evaluation.usage import AccountingError, JsonlUsageSink, aggregate_usage, read_usage_events

MAX_USD = Decimal("5")


def _bounded_usd(amount) -> Decimal:
    if not isinstance(amount, Decimal) or not amount.is_finite() or amount <= 0 or amount > MAX_USD:
        raise DataError("USD bounds must be finite Decimals greater than zero and no more than 5")
    return amount


def _usd(value: str) -> Decimal:
    try:
        return _bounded_usd(Decimal(value))
    except (InvalidOperation, ValueError):
        raise argparse.ArgumentTypeError("USD budget must be greater than zero and no more than 5") from None


def _outside_dataset(path: Path, dataset_root: Path, *, label: str) -> Path:
    resolved = Path(path).resolve()
    if resolved.is_relative_to(dataset_root):
        raise DataError(f"{label} must be outside immutable dataset inputs")
    return resolved


def populate_cache(dataset_root, *, cache_root, receipt_path, run_id: str,
                   max_calls: int, max_cost: Decimal, attempt_cost_bound: Decimal,
                   live: bool = False, account_logging_verified: bool = False,
                   authenticated_availability_verified: bool = False,
                   environ=None, client: OpenRouterClient | None = None):
    """Populate/reuse canonical extraction entries and durable UsageEvent receipts.

    `live=False` fails before credential resolution or construction of an HTTP
    client. Tests can inject a MockTransport-backed canonical client; production
    calls use only the existing OpenRouterClient and Extractor APIs.
    """
    if not live:
        raise DataError("live extraction is disabled; pass explicit approval switches")
    if not account_logging_verified or not authenticated_availability_verified:
        raise DataError("live extraction requires verified account logging and authenticated availability")
    if type(max_calls) is not int or max_calls < 1:
        raise DataError("max calls must be a positive integer")
    _bounded_usd(max_cost)
    _bounded_usd(attempt_cost_bound)
    dataset_root = Path(dataset_root).resolve()
    cache_root = _outside_dataset(Path(cache_root), dataset_root, label="cache")
    receipt_path = _outside_dataset(Path(receipt_path), dataset_root, label="receipt ledger")
    if receipt_path.is_relative_to(cache_root) or cache_root.is_relative_to(receipt_path):
        raise DataError("cache and receipt ledger must be separate")
    if client is not None and not client.live_enabled:
        raise DataError("injected extraction client must be explicitly live-enabled")

    data = Dataset.load(dataset_root)
    requests, _ = load_requests(dataset_root)
    prepared = []
    for request in requests:
        raw = project_request(request)
        financial = data.financial_input_for(raw)
        descriptors = event_descriptors({event.event_id: event for event in financial.events}, user_id=financial.user_id)
        selection = EvidenceIndex.from_context(data.context_for(raw)).retrieve(
            user_id=financial.user_id, request_id=financial.request_id,
            event_ids=tuple(descriptors), as_of=financial.request_date,
        )
        prepared.append((financial, descriptors, selection))

    usage = JsonlUsageSink(receipt_path, run_id=run_id)
    if client is None:
        key = load_api_key(os.environ if environ is None else environ)
        client = OpenRouterClient(api_key=key, live_enabled=True)
    cache = ExtractionCache(cache_root)
    extractor = Extractor(cache=cache, usage=usage, run_id=run_id, client=client,
                          config=ExtractorConfig(), budget=RunBudget(max_calls=max_calls, max_cost=max_cost))
    seen, origins, outcomes, issues = set(), [], Counter(), Counter()
    for financial, descriptors, selection in prepared:
        for source in selection.sources:
            # Each source is extracted once per source+context cache key. The
            # existing cache lock provides producer deduplication; receipts retain
            # cache origins for later unique accounting.
            asset = None
            if source.kind == "image":
                try:
                    asset = resolve_image(dataset_root, source.source_id.split(":", 1)[1])
                except EvidenceError as exc:
                    outcomes["unavailable"] += 1
                    issues[exc.code] += 1
                    continue
            try:
                result = extractor.extract(source, candidate_events=descriptors, asset=asset,
                                           as_of=financial.request_date, mode="live",
                                           cost_upper_bound=attempt_cost_bound)
            except (EvidenceError, OSError) as exc:
                outcomes["unavailable"] += 1
                issues[exc.code if isinstance(exc, EvidenceError) else "cache_io_error"] += 1
                continue
            seen.add((source.source_id, result.cache_key))
            outcomes[result.outcome] += 1
            issues.update(result.issues)
            origins.extend(result.origin_usage)
    summary = aggregate_usage(read_usage_events(receipt_path, run_id=run_id), origin_usage=origins,
                              run_id=run_id, request_count=len(requests))
    return {"mode": "approved-live-cache-population", "run_id": run_id,
            "request_count": len(requests), "source_contexts": len(seen),
            "outcomes": dict(sorted(outcomes.items())), "issues": dict(sorted(issues.items())),
            "receipt_path": str(receipt_path), "accounting_complete": summary["accounting_complete"],
            "new_usage": summary["new_usage"], "consumed_cache_origins": summary["consumed_cache_origins"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path(__file__).resolve().parents[1] / "dataset")
    parser.add_argument("--cache", type=Path, required=True, help="external canonical extraction cache")
    parser.add_argument("--receipts", type=Path, required=True, help="new exclusive external UsageEvent JSONL path")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--max-calls", type=int, required=True)
    parser.add_argument("--budget-usd", type=_usd, required=True)
    parser.add_argument("--per-attempt-bound-usd", type=_usd, required=True)
    parser.add_argument("--live", action="store_true", help="permit existing client dispatch after approval")
    parser.add_argument("--account-logging-verified", action="store_true")
    parser.add_argument("--authenticated-availability-verified", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = populate_cache(args.dataset, cache_root=args.cache, receipt_path=args.receipts,
                                run_id=args.run_id, max_calls=args.max_calls, max_cost=args.budget_usd,
                                attempt_cost_bound=args.per_attempt_bound_usd, live=args.live,
                                account_logging_verified=args.account_logging_verified,
                                authenticated_availability_verified=args.authenticated_availability_verified)
    except (DataError, EvidenceError, ValueError, OSError) as exc:
        if isinstance(exc, EvidenceError):
            detail = f": {exc.code}"
        elif isinstance(exc, (DataError, AccountingError)):
            detail = f": {exc}"
        elif isinstance(exc, OSError) and exc.strerror:
            detail = f": {exc.strerror}"
        else:
            detail = ""
        parser.exit(2, f"cache population failed: {type(exc).__name__}{detail}\n")
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result["accounting_complete"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
