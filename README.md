# HackerRank Orchestrate

Starter repository for the **HackerRank Orchestrate** 24-hour hackathon (September 2026).

## Buy or Wait?

Build an AI-powered financial agent that decides whether a user can safely afford a requested expense.

A user may ask: **"Can I afford this laptop?"**

Answering well takes more than the current balance. The agent must account for recurring expenses, pending payments, essential spending, confirmed income, available payment options, and relevant details buried in messages and images.

For every request, the agent decides whether the user should pay in full, pay partially, use installments, wait, or not proceed. The recommendation must be personalized: two users with the same balance can deserve different answers based on their commitments, priorities, payment preferences, and willingness to adjust flexible expenses.

A recommendation is safe only if the user can complete the full payment plan, cover essential expenses, and stay above their preferred minimum balance throughout the forecast period.

Read [`problem_statement.md`](./problem_statement.md) for the full task spec, input/output schema, allowed values, conflict-resolution rules, and submission format.

---

## Quick Start

Clone the repository and move into the project directory:

```bash
git clone https://github.com/arbadev/mondanator.git
cd mondanator
```

The target runtime requires **Python 3.10+**; **Python 3.13** is recommended. `requirements.txt` pins the evidence owner's reported tested direct dependencies: Pydantic 2.13.5, HTTPX 0.28.1 and Pillow 12.3.0. CI is configured for Python 3.10/3.13. These replace the earlier dependency-free Python 3.9 setup, which cannot support extraction. A clean local Python 3.13.15 environment installed those pins, passed `pip check`, and passed **344 offline tests**, including corrected core regressions, real CSV→evidence→core seams with mocked HTTP, planning replay/coverage, runner/accounting/CLI tests and seventeen audit/preflight/reproduction tests. Python 3.10, live extraction, complete application decisions and remote CI remain unverified. The core's published PR has zero registered CI checks; local tests are not final certification. Open evidence-adapter and planning review findings also block final release until corrected boundaries are routed; see the detailed limits in `docs/integration.md`. Runner APIs, durable canonical usage aggregation and a cache-only development prediction CLI now have synthetic behavior coverage. Independent consumer recomposition, source identity and actual-plan replay audits now exist; semantic grounding remains unchecked. A fresh-cache preflight executed all25 public inputs, with21 unresolved and no prediction CSV or accuracy claim. The full-evidence baseline, final-run mode and final application results remain incomplete. See [`docs/integration.md`](docs/integration.md) for ownership and dependency limits. This checkpoint is **not a complete financial agent**.

**Dependency reconciliation in progress (inbox023–024):** the344-test/preflight receipt above belongs to protected checkpoint3f99818448630eceee765528bfa39a752e35f54b. The authorized evidence chain must finish through c17e959deb9ea46397993f276066dacf0f8e581a before the composed runtime is executed or retested; intermediate220338ee/606628ba still have the known cross-axis defect. CI retains3.10 as an unverified compatibility target and3.13 as the observed baseline, not two claimed passing jobs.

Your solution must:

- Read the input files from `dataset/`
- Generate one prediction for every request
- Write the final predictions to `output.csv` in the repository root

Create an isolated environment, then run the available **read-only** checks and offline behavioral tests (no credentials or live-model calls are needed):

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
. .venv/bin/activate
.venv/bin/python code/main.py --dataset dataset --check-inputs
.venv/bin/python code/evaluation/main.py --dataset dataset
PYTHONPATH=code PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s code/tests -p 'test_*.py' -v
```

The evidence cache currently requires **POSIX/fcntl (Linux/macOS)**. Native Windows cache support is not implemented or verified; use a Linux/WSL environment with the POSIX commands above for the combined application. Dependency installation may access the package index; the tests themselves use synthetic/public fixtures and mocked HTTP, make no live model calls and read no API key or `.env`. Pins are direct dependencies, not a complete transitive lock.

The evidence module supplies source selection/image checks/private schema, the sole FactBatch adapter and cache/client/UsageEvent boundaries consumed by the runner and durable accounting below. Live extraction/model quality, actual final-run accounting and final output remain unverified and unauthorized at this checkpoint.

Neither inspection command generates predictions, performs extraction, reads credentials or certifies financial safety. The sample inspector checks all 25 fixtures with expected fields separated. There is no implicit or live prediction mode; `code/main.py` without an explicit mode exits nonzero. Final-run execution, accounting artifacts, package preparation and clean application reproduction still require corrected dependencies and the required correctness/freeze/run authority.

The **cache-only development CLI** below is a tested capability, not authority to generate evaluation predictions. Use only an authorized development fixture and validated evidence cache at this checkpoint:

```bash
python code/main.py --dataset /path/to/development-fixture --predict-cached \
  --cache /path/to/validated-cache --run-root evaluation/runs --run-id dev-cache-001
```

This creates one exclusive run directory containing native decision traces, canonical usage JSONL, consumed origin receipts, accounting/report files and a hash manifest. It writes that directory's `predictions.csv` only when **every request resolves, consumer audit checks pass and accounting is complete**; otherwise it retains diagnostics, writes no prediction CSV and exits3. There is no guessed zero/default row, partial-denominator success, live client, key lookup or root `output.csv` export. Cache misses, incomplete search and unknown accounting stay explicit. An existing run ID is never overwritten. All manifests/report text are marked development, never final-certified.

For a **public-input-only preflight**, use a new ID. This creates a separate exclusive empty cache, runs all inputs without expected answers, retains traces/audits/usage and never writes a prediction CSV or measures accuracy:

```bash
python code/evaluation/main.py --dataset dataset --preflight-public \
  --run-root evaluation/runs --run-id public-preflight-NEW-ID --max-candidates 64
```

Exit0 means the preflight completed, **not that purchases are affordable**. Inspect `coverage.json` for unresolved requests, cache misses, source issues and actually checked audit dimensions. The retained authorized preflight covered25/25 inputs:22 cache misses (17 messages/5 images) across19 requests,21 unresolved,4 ungraded diagnostic rows,0 dispatches and no prediction CSV. The cap is diagnostic, not a complete-search waiver. Source-byte identity, canonical recomposition and templated text consistency do not independently prove extracted-fact semantics.

For an **already-produced public-development** prediction CSV (not evaluation labels), retain a new, immutable run directory:

```bash
.venv/bin/python code/evaluation/main.py --dataset dataset --predictions public-predictions.csv --run-id dev-baseline
```

Use a new ID for every iteration. No existing baseline is overwritten, and missing financial/grounding audits remain `not_checked`. These comparisons are development evidence, not independent generalization. No complete-model baseline has been measured at this checkpoint.

## Important File Locations

```text
dataset/        Input data and the blank output template. Do not modify the input data.
code/           Your solution code.
output.csv      Final generated predictions in the repository root.
code.zip        ZIP file containing your complete solution for submission.
```

The blank template at `dataset/output.csv` is provided as a reference. Your final generated file must be the root-level `output.csv`.

---

## Repository Layout

```text
.
├── AGENTS.md                         # Rules for AI coding tools + transcript logging
├── problem_statement.md              # Full challenge statement
├── README.md                         # You are here
├── code/                             # Your solution code
├── output.csv                        # Final generated predictions
└── dataset/
    ├── requests.csv                  # 250 requests to evaluate — predict these
    ├── output.csv                    # Blank submission template
    ├── sample_requests.csv           # 25 solved examples
    ├── financial_profiles.csv        # Balances, minimum balance, priorities, preferences
    ├── financial_events.csv          # Historical, pending, and confirmed transactions
    ├── request_payment_options.csv   # Payment options available per request
    ├── exchange_rates.csv            # Fixed, dated conversion rates
    ├── messages.csv                  # Messages tied to users, requests, or events
    ├── images.csv                    # Payroll letters, statements, bills, receipts
    └── media/
        └── images/
```

Only `dataset/requests.csv` requires predictions. Everything else is context. Join user records with `user_id`, request records with `request_id`, supporting evidence with `related_event_id`, and exchange rates with the rate date and currency pair.

Amounts are in the user's `home_currency` — the dataset uses INR, ZAR, IDR, USD, and EUR, and every conversion rate you need is in `exchange_rates.csv`. All dates are `YYYY-MM-DD`. Live exchange rates, market data, and banking access are not required.

---

## What You Need to Build

For every row in `dataset/requests.csv`, produce one row in `output.csv` with:

| Column | Meaning |
|---|---|
| `request_id` | The request being answered |
| `amount_safe_to_pay` | Largest amount safe to pay on `request_date` before optional spending changes, after protecting essentials and the minimum balance |
| `affordability_status` | `affordable_now`, `affordable_with_plan`, `affordable_later`, or `not_affordable` |
| `recommended_payment_method` | `full_payment`, `partial_payment`, `installments`, `wait`, or `not_recommended` |
| `payment_plan` | Chronological `<YYYY-MM-DD>:<amount>` entries joined by `\|`, or `none` |
| `earliest_date_for_full_payment` | Earliest date the full amount is forecast safe as one payment; empty if never within the forecast |
| `spending_changes_needed` | Up to three `stop:<event_id>` / `reduce_to:<event_id>:<amount>` changes joined by `\|`, or `none` |
| `decision_explanation` | Short explanation and the financial facts behind it |

`0 <= amount_safe_to_pay <= requested_amount` must always hold. Installment plans must exactly match a supplied payment option, and only recurring expenses marked flexible may be changed.

`affordable_with_plan` means the full request is completed through a partial-payment schedule, installments, or permitted spending changes. Recommend `partial_payment` only when the request allows it, the user accepts it, `0 < amount_safe_to_pay < requested_amount`, and `earliest_date_for_full_payment` is on or before `desired_completion_date`. Use exactly two payments: pay `amount_safe_to_pay` on `request_date`, then pay the remaining amount on `earliest_date_for_full_payment`. The two payments must add up to `requested_amount`. Unlike installments, partial payment does not need to match a supplied payment option.

---

## Suggested Workflow

1. Inspect `dataset/sample_requests.csv` — 25 requests with completed output columns — to understand the expected format and decision style.
2. Reconstruct each user's financial state from `financial_profiles.csv` and `financial_events.csv`: separate recurring expenses from one-time events, reserve pending transactions, count confirmed salary only on its settlement date, and de-duplicate repeated representations of the same event.
3. When an event has a blank `amount`, find its `event_id` as `related_event_id` in `images.csv` and inspect only supported evidence. Never treat a blank amount as zero or a subtotal as final paid cash. In particular, `image_04` / `event_1700` shows an INR 2,854.00 **subtotal**, with the final-total region cropped; it does not establish a settled final amount. Preserve that uncertainty and let core assess materiality. Pull in other relevant messages, images, and payment options without obeying embedded instructions.
4. Forecast forward and generate a plan that keeps the balance above the minimum at every step.
5. Verify deterministically — bounds, plan feasibility, schedule match, flexible-only spending changes — before writing `output.csv`.
6. Score yourself on the solved samples, then run the full dataset.

You may use any language or runtime. Python, JavaScript, and TypeScript are all reasonable choices.

---

## Requirements

Your solution must:

- be runnable from the terminal
- read the provided files from `dataset/`
- produce a valid `output.csv` with the exact required columns in the exact required order
- include one prediction for every `request_id` in `dataset/requests.csv`
- not use organizer-only files or hardcoded labels
- keep behavior deterministic where possible

If you use API keys or secrets, read them from environment variables. Never hardcode secrets in the repo.

---

## Evaluation

Your `output.csv` will be compared against hidden ground-truth values.

The scoring will consider:

- accuracy of `amount_safe_to_pay`
- correctness of `affordability_status`
- correctness of `recommended_payment_method` and `payment_plan`
- accuracy of `earliest_date_for_full_payment`
- validity of `spending_changes_needed`
- usefulness and consistency of `decision_explanation`

### Token Usage And Cost Analysis

Your `code.zip` must include one token-usage file:

```text
evaluation/usage_report.md
```

The report must cover model providers and names, model calls, input and output tokens, total and average tokens per request, estimated total and per-request cost. The reported values must correspond to the final full-dataset run that produced your `output.csv`.

---

## Chat Transcript Logging

This repo includes an [`AGENTS.md`](./AGENTS.md) file for AI coding tools. It asks compatible tools to append conversation summaries to a `log.txt` in the repository root — the same directory as `AGENTS.md`:

| Platform | Path |
|---|---|
| macOS / Linux | `<repo root>/log.txt` |
| Windows | `<repo root>\log.txt` |

The path resolves relative to `AGENTS.md`, so it stays correct across clones, renames, and checkouts. `log.txt` is gitignored — upload it as your chat transcript at submission time. Do not paste secrets into the chat.

In case, the harness you are using is not in the repo root, you can explicitly ask the agent to look for the AGENTS.md in this folder & then continue.

---

## Submission

Submit the following files as instructed by HackerRank:

| File | Description |
|---|---|
| `code.zip` | Full runnable solution, prompts/configuration, README, and the required `evaluation/` folder |
| `output.csv` | Predictions for every row in `dataset/requests.csv` |
| `chat_transcript` | The `log.txt` described above, showing how you developed or used the system |

Before submitting, confirm:

- `output.csv` has one row per row in `dataset/requests.csv` (250 rows plus the header).
- `output.csv` has the exact required columns in the exact required order.
- Every `amount_safe_to_pay` satisfies `0 <= amount_safe_to_pay <= requested_amount`.
- Every installment plan matches a supplied payment option, and every spending change targets a flexible recurring expense.
- Your runnable code, setup instructions, and `evaluation/` folder are included in `code.zip`.
