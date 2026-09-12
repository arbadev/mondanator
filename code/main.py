"""Offline integration entry point. Financial inference waits for module handoff."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from buy_wait.data import Dataset, DataError, load_requests


def main(argv=None):
    parser = argparse.ArgumentParser(description="Buy or Wait participant input validation (no inference)")
    parser.add_argument("--dataset", type=Path, default=Path(__file__).resolve().parents[1] / "dataset")
    parser.add_argument("--check-inputs", action="store_true", help="validate CSV/index/request joins without reading images, credentials or labels")
    args = parser.parse_args(argv)
    if not args.check_inputs:
        parser.error("this integration boundary currently supports --check-inputs; financial inference awaits the committed module handoff")
    try:
        data = Dataset.load(args.dataset)
        requests, request_hash = load_requests(args.dataset)
        for request in requests:
            data.context_for(request)
        print(json.dumps({
            "mode": "input-validation-only", "requests": len(requests),
            "profiles": len(data.profiles), "events": len(data.events),
            "blank_event_amounts": sum(not r["amount"] for r in data.events.values()),
            "source_hashes": {**data.source_hashes, "requests.csv": request_hash},
            "financial_safety": "not evaluated", "output_written": False, "model_calls": 0,
        }, sort_keys=True, indent=2))
        return 0
    except (DataError, OSError) as exc:
        # DataError strings are field/record-only; OSError may carry sensitive paths.
        parser.exit(2, f"input validation failed: {str(exc) if isinstance(exc, DataError) else type(exc).__name__}\n")


if __name__ == "__main__":
    raise SystemExit(main())
