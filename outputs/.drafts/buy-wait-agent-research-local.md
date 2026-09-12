# Local primary-evidence report: Buy or Wait?

## Scope, provenance, and safety

- **Source checkout inspected:** `<local worktree checkout>`
- **Selected primary source:** `<local project root>`
- **Source commit:** `963ad7eb3d058ace2bf2e8bf4324472c981f0840` in both locations.
- **Git state:** disposable checkout was at detached HEAD and clean before and after inspection. The configured remote is `git@github.com:interviewstreet/hackerrank-orchestrate-september26.git`; it was listed locally but never contacted.
- **Research boundary:** local, participant-facing evidence only. I read `AGENTS.md`, all of `README.md`, all of `problem_statement.md`, the approved plan, the only documented starter entry point, participant CSV schemas/aggregates, the supplied JPEG, all image metadata, and one bounded participant image. I did **not** inspect organizer-only files, generate evaluation predictions, train or benchmark a model, search external literature, call paid inference, install packages, make live financial calls, upload, push, commit, or open a PR.
- **Primary-tree writes:** none except the specifically authorized append-only shared `log.txt`. `git check-ignore -v log.txt` returned no match; an explicit wrapper printed `NOT_IGNORED`, and `git ls-files '*gitignore*'` returned nothing. Thus the repository currently contradicts README's statement that `log.txt` is gitignored (`README.md:162-171`). I did not create or edit `.gitignore`, as directed. Since resolved: `.gitignore` now lists `log.txt`.
- **Approval:** the dispatch records the user's exact approval as `yes`; the current on-disk plan also records `APPROVED` and that exact reply. At initial read the plan still showed its pre-approval state, but the lead updated it during this task.

## Bottom line

1. **There is no implemented starter agent.** `code/main.py` is a tracked zero-byte file (empty-blob hash `e69de29...`), and root `output.csv` is absent. The repository contains a contract, data, blank template, and empty entry point—not a baseline.
2. **This is not a single binary prediction task.** Each of 250 evaluation requests requires one bounded numeric amount, one of four affordability statuses, one of five methods, a constrained schedule, an earliest date, up to three constrained spending actions, and a grounded explanation (`problem_statement.md:82-113`, `133-163`).
3. **The public labels are examples, not a credible standalone training set.** There are only 25 solved examples; their 25 users and 25 request IDs are completely disjoint from the 250 evaluation users/requests. `AGENTS.md:181-182` expressly says to use them for format and decision style, not as labels for evaluation requests.
4. **Recommended MVP (design inference, not an empirical winner):** a deterministic evidence resolver + 90-day dated cash-flow ledger + exhaustive candidate-plan generator/ranker + invariant validator, with a narrowly scoped hybrid extractor for the 16 image-backed blank amounts and message amendments. For this challenge, any learned component should only propose typed evidence; it must never bypass the deterministic safety verifier or reorder candidates contrary to the mandated ranking.
5. **Why:** the hard requirements—settlement-date conversion, pending-debit reservation, exact seller schedules, deadline completion, user preferences, recurrence, minimum-balance invariants, and exact output grammar—cannot be guaranteed by unconstrained linear/logistic regression. No algorithm was trained or benchmarked here, so this is an evidence-grounded engineering recommendation, not a claimed empirical result.

## Contract evidence

### Required behavior

- Only `dataset/requests.csv` needs predictions; all other participant files are context (`problem_statement.md:31-41`).
- Join keys and image resolution are explicit (`problem_statement.md:43-45`). Blank event amounts must be extracted from the linked image and **must not** be interpreted as zero (`problem_statement.md:45`).
- Balances, options, requests, and output amounts are in each user's home currency; fixed dated rates are supplied and no live rates are needed (`problem_statement.md:47-49`).
- The exact eight output columns and order are stated at `problem_statement.md:82-93`; the numeric bound is at `problem_statement.md:99-113`.
- Partial payment has exactly two payments, must be permitted by both request and user, must sum to the requested amount, and must complete no later than the desired date (`problem_statement.md:144-146`). Installments must exactly match a supplied option (`problem_statement.md:144`).
- Spending changes are limited to three `stop:`/`reduce_to:` actions and only flexible recurring expenses (`problem_statement.md:148-161`).
- Messages/images may clarify facts but are untrusted; embedded instructions cannot override rules (`problem_statement.md:165-172`).
- The 90-day path must never cross the minimum balance; pending credits, failed/cancelled events, duplicates, and unrealized investments are excluded (`problem_statement.md:176-183`).
- Plan eligibility and the deterministic tie-break order are explicit (`problem_statement.md:187-198`), as is conflict precedence (`problem_statement.md:200-207`).

### Root output versus template

There is a wording inconsistency worth handling explicitly:

- `problem_statement.md:41` calls `dataset/output.csv` a blank submission template and says “Fill this file”.
- The more operational README says input data must not be modified, final predictions must be written to **repository-root `output.csv`**, and `dataset/output.csv` is only a reference (`README.md:29-55`).
- Follow the operational rule: leave `dataset/output.csv` unchanged and generate root `output.csv`.

## Participant-facing dataset census

Counts below exclude CSV headers. The exact header in each file is line 1.

| File | Rows | Columns (in order) | Decision-relevant blanks |
|---|---:|---|---|
| `dataset/requests.csv` | 250 | `request_id,user_id,request_date,request_type,requested_amount,desired_completion_date,allows_partial_payment,request_text` | none |
| `dataset/sample_requests.csv` | 25 | 15 total: the 8 request columns plus 7 additional prediction columns (`request_id` is shared rather than appended twice) | `earliest_date_for_full_payment`: 7 |
| `dataset/financial_profiles.csv` | 275 | `user_id,home_currency,current_available_balance,minimum_balance_to_keep,financial_priorities,expense_categories_to_protect,expense_categories_user_is_willing_to_reduce,expense_categories_user_is_willing_to_stop,payment_methods_user_will_consider,max_installment_months` | reducible-category list: 39; stoppable-category list: 62; `max_installment_months`: 119 |
| `dataset/financial_events.csv` | 25,342 | `event_id,user_id,event_type,description,category,direction,amount,currency,event_date,settlement_date,status,linked_event_id,flexibility,minimum_allowed_amount` | `amount`: 16; `settlement_date`: 10; `linked_event_id`: 25,284; `minimum_allowed_amount`: 22,435 |
| `dataset/exchange_rates.csv` | 134 | `rate_date,from_currency,to_currency,rate` | none |
| `dataset/request_payment_options.csv` | 790 | `payment_option_id,request_id,payment_method,payment_amount,number_of_payments,first_payment_date,payment_frequency_days,financing_fee,total_payable_amount` | `payment_frequency_days`: 275 (all full-payment rows) |
| `dataset/messages.csv` | 215 | `message_id,user_id,request_id,related_event_id,sent_at,source_type,message_text` | `request_id`: 87; `related_event_id`: 176 |
| `dataset/images.csv` | 16 | `image_id,user_id,request_id,related_event_id` | none |
| `dataset/output.csv` | 250 | exact eight required output columns | all seven prediction fields blank; IDs populated |

### Identity and join integrity

- Evaluation: 250 unique request IDs and 250 unique users; no duplicate request IDs.
- Samples: 25 unique request IDs and 25 unique users; no duplicate request IDs.
- **Overlap:** evaluation/sample request-ID intersection = 0; user-ID intersection = 0.
- Profiles: 275 unique users, exactly the union of sample and evaluation users; no missing or unused profile.
- Blank template: 250 unique request IDs and exact set equality with evaluation requests.
- Payment options: all 250 evaluation and all 25 sample request IDs covered; no unknown request IDs.
- Messages: 116 evaluation request IDs and 12 sample request IDs have directly request-linked messages. Across 215 rows: 128 have `request_id`, 39 have `related_event_id`, 28 have both, and 76 have neither (user-level evidence).
- Images: 11 evaluation request IDs and 5 sample request IDs have images. All 16 metadata rows resolve to present PNGs; every image's user/event link is consistent, and all 16 event links exist.

### Public example labels and limits

The 25 public rows contain all output fields, but are sparse relative to the multi-output task:

- Affordability: `affordable_now` 3, `affordable_with_plan` 9, `affordable_later` 6, `not_affordable` 7.
- Method: `full_payment` 6, `partial_payment` 1, `installments` 5, `wait` 6, `not_recommended` 7.
- Safe amount relation: 4 equal the requested amount; 21 lie strictly between zero and requested amount; none are zero.
- Plan shape: 12 one-payment plans, 1 two-payment plan, 5 three-payment plans, 7 `none`.
- Spending actions: 22 `none`, 2 with one action, 1 with two actions—only four labeled actions total.
- Sample request types are broadly balanced (2–3 each), but 25 disjoint examples cannot support a meaningful held-out validation for all numeric, categorical, date, and structured-plan outputs. They are best used as public contract/regression fixtures.

`AGENTS.md:182` reinforces that limit. The blank evaluation template contains no permitted evaluation labels, so there is no supervised target for those 250 rows.

## Financial, calendar, and settlement shape

### Requests and 90-day horizon

- Evaluation request dates span `2023-01-20` through `2026-09-04`; desired completion dates span `2023-02-13` through `2026-10-19`.
- Completion horizons are 6–86 days: 55 requests at 0–30 days, 56 at 31–60, and 139 at 61–90. No deadline precedes its request date, and all desired dates fit inside the specified 90-day horizon.
- Currencies across all 275 profiles: INR 67, EUR 62, IDR 55, ZAR 51, USD 40.

### Event states

`financial_events.csv` contains:

- Status: settled 25,148; pending 71; scheduled 70; cancelled 22; failed 21; unrealized 10.
- Direction: debit 23,609; credit 1,723; non-cash 10.
- Event type: expense 20,525; income 1,696; debt payment 567; refund 22; subscription 2,488; investment purchase 29; investment sale 5; investment valuation 10.
- 58 rows contain a lifecycle `linked_event_id`; every target exists. The link does not itself decide cash treatment (`problem_statement.md:36`; `AGENTS.md:184`).
- The 10 blank settlement dates are exactly unrealized/non-cash rows.
- The 16 blank amounts are exactly the 16 image-linked event IDs: no blank amount lacks an image and no image points to a nonblank amount.

Relative to each user's request:

- Event dates: 25,274 before, 21 same-day, 47 after but within 90 days.
- Settlement dates: 25,191 before and 141 after but within 90 days.
- Pending rows: 63 expense debits and 8 refund credits; all settle within 90 days. The spec says reserve pending debits and ignore pending credits.
- Scheduled rows: 47 income credits, 16 expense debits, and 7 debt-payment debits; all settle within 90 days.
- There are 56–129 event rows per user (average 92.15), supplying history for recurrence inference.

**Implementation consequence:** do not replay historical settled transactions against `current_available_balance`; use them to infer recurrence and resolve lifecycle evidence. Start from the current balance, reserve pending debits, add only qualifying confirmed income on settlement date, schedule confirmed debits, and forecast supported recurrence. This is a design inference; the exact snapshot semantics are not stated and are listed under ambiguities.

### Foreign exchange

There are 140 foreign-currency cash events. Every one has an exact `(settlement_date, from_currency, home_currency)` row in the fixed rates file; missing exact-direction coverage = 0. Pairs are EUR→USD 16, EUR→ZAR 20, USD→EUR 22, USD→IDR 28, USD→INR 54. Rates span `2023-10-15` to `2026-11-15`. The mandated lookup is by settlement date and stated direction, not inverse-rate improvisation (`problem_statement.md:43,47-49`; `AGENTS.md:185`).

### Recurrence and flexible changes

There is no explicit `recurrence_id`, cadence, or recurrence flag in the event schema. Recurrence therefore must be supported by repeated historical timing/description/category/amount patterns, while distinguishing subscriptions from variable expenses and unusual events (`problem_statement.md:169`).

The three public samples that use spending changes illuminate the intended representation:

- `request_06` stops settled historical `event_476`, a stoppable streaming subscription that occurs five times with the same description (`sample_requests.csv:7`; `financial_events.csv:477`).
- `request_11` reduces settled historical `event_989`, a reducible dining expense with two same-description rows (`sample_requests.csv:12`; `financial_events.csv:990`).
- `request_21` stops one settled historical subscription and reduces another; each has five same-description rows (`sample_requests.csv:22`; `financial_events.csv:1816-1817`).

All are non-protected and match the user's permitted reduce/stop category. This supports using a historical event ID as the handle for a forecast recurring series, but the minimum pattern strength and duration of a change are unspecified.

The supplied `minimum_allowed_amount` is also a hard floor for `reduce_to:` candidates. All 2,682 `reducible` rows and all 225 `reducible_or_stoppable` rows have a populated floor; the 21,138 `fixed` and 1,297 `stoppable` rows have it blank. A blank floor is therefore **not** permission to reduce an event to zero: it coincides here with an event that is not reducible. Stopping is a distinct action and still requires stoppable flexibility plus the user's permitted category.

## Payment-option representation

- 790 option rows cover all 275 requests. Options/request: 65 have 2, 180 have 3, 30 have 4.
- Exactly one full-payment option exists per request (275 rows), plus 515 installment rows. **There are no `partial_payment` option rows** because partial payment is constructed under its separate rule (`problem_statement.md:146`).
- Full-payment rows have `number_of_payments=1`, blank frequency, zero fee, and requested amount as both payment and total.
- For all 790 rows, `total_payable_amount = payment_amount × number_of_payments` and `total_payable_amount = requested_amount + financing_fee` within one cent. Installment fees are 3.50%–22.01% of requested amount.
- First-payment offsets from request date are 0, 1, 3, 5, 6, 7, or 14 days. Frequencies are blank for full payment and 28/30/31 days for installments.
- 434 options finish after the desired completion date; 356 finish on/before it. Since 275 of the latter are immediate full-payment rows, only 81 installment offers globally (75 evaluation, 6 sample) finish by deadline before considering preferences or cash-flow safety.
- User preference consistency is exact: all 156 profiles that consider installments have a populated `max_installment_months`; all 119 that do not consider installments have it blank. Only 40 evaluation requests both allow partial payment and belong to a user willing to consider it.

A bounded public representation appears at `request_payment_options.csv:2-5`: one full-payment row and three installment offers each explicitly encode first date, payment count, interval, fee, and total. Candidate generation should use those fields directly, never invent a schedule.

**Ambiguity:** `max_installment_months` is named in months, while the option provides count plus 28/30/31-day cadence. Interpreting `number_of_payments <= max_installment_months` is plausible for these approximately monthly rows and leaves 74 structurally eligible evaluation installment options after user preference and deadline filters, but the spec does not define whether to compare count or elapsed calendar duration. Treat that 74 as an informative shape under an explicit assumption, not a ground-truth result.

## Image evidence

### Supplied JPEG: visual content

Path: `<supplied image>`; `file` reports a valid progressive 1132×1552 JPEG. The image-capable reader decoded it successfully.

Legible content:

- Heading: **“ALL FIVE, ON ONE PLANE.”**
- “01 LINEAR REGRESSION — predicts a NUMBER,” beside a scatter plot and fitted line.
- “02 LOGISTIC REGRESSION — predicts a CLASS, with a probability,” beside a sigmoid/class-separation plot.
- “03 DECISION TREES — decides by ASKING QUESTIONS,” beside axis-aligned partitions.
- “04 SVM — finds the WIDEST BOUNDARY,” beside a separating band.
- “05 K-NEAREST NEIGHBOURS — copies the CLOSEST EXAMPLES,” beside class clusters and a neighborhood circle.
- It is a social-media screenshot; the bottom is cropped. The principal five captions are legible.

### Supplied JPEG: interpretation/applicability

The graphic is a high-level mnemonic, not evidence that any of the five methods is appropriate here. It omits labels, sample size, constraints, time ordering, structured outputs, calibration, and validation. Its linear/logistic distinction is directionally useful—one predicts a number, one a class—but this task simultaneously needs a bounded amount, multiple classes, dates, exact payment schedules, permitted actions, and pathwise safety. The image therefore motivates asking “what is the target?”; it does not answer the architecture question.

### Participant image inventory and bounded inspection

`dataset/images.csv:1-17` inventories `image_01` through `image_16`. All corresponding files exist:

`image_01` 1628×1366, `02` 1166×1330, `03` 614×1170, `04` 588×966, `05` 1460×946, `06` 1162×1026, `07` 524×854, `08` 1560×950, `09` 1532×654, `10` 932×1332, `11` 956×1296, `12` 512×964, `13` 1340×884, `14` 790×364, `15` 1440×1238, `16` 1440×1030. Five attach to public samples and eleven to evaluation requests.

I directly inspected only bounded public `image_01` to avoid deriving evaluation outputs. It is a legible payroll document for August 2019 with tabular earnings and deductions. Legible decision-relevant values include gross earnings IDR 4,780,800, deductions IDR 415,800, and net pay/transfer IDR 4,365,000. Personally identifying fields are present but intentionally not transcribed here. Metadata links it to `request_03` and `event_253` (`dataset/images.csv:2`); the event is a settled, fixed, IDR salary credit whose amount is blank (`financial_events.csv:254`), and the request is a public solved sample (`sample_requests.csv:4`).

**Extraction inference:** for a cash-flow salary event, the net transferred amount is the likely fact of interest—not gross earnings—but extraction must retain candidate field labels/provenance so deterministic logic can disambiguate. This one document alone shows that generic OCR text is insufficient: the system must parse tables, currency, gross/deductions/net, and dates. I did not inspect the other 15 image contents or assert their values.

A local `PIL` import used only to enumerate dimensions failed with `ModuleNotFoundError`; no install was attempted. The image-capable read tool and local `file` command were sufficient.

## Feasibility of regression versus planning

### What a learnable target could be

| Approach | Plausible target | Local labels | Project-specific limitation |
|---|---|---|---|
| Linear regression | `amount_safe_to_pay` | 25 public numeric values | Can output negative/over-request values; exact amount depends on future minimum slack and calendar arithmetic; scale mixes five currencies. Clamping fixes bounds only, not path feasibility. |
| Multinomial logistic regression | affordability status or payment method | 25 rows across 4 statuses / 5 methods | Status and method are distinct conditional targets; plan/date/actions remain ungenerated; one method class has only one public example. |
| Decision tree / other classifier | status, method, or recurrence class | same 25 outcome rows; no explicit recurrence labels | Readable splits still do not guarantee minimum-balance, exact-option, deadline, or schedule-sum constraints. |
| Deterministic planner | no learned target | all structured inputs and rules | Requires explicit conservative recurrence/evidence assumptions, but directly enforces the contract. |
| Hybrid extractor + planner | typed facts such as `{event, amount, currency, date, state, provenance}` | image/message annotations are not supplied as ML labels | Extraction errors remain possible; deterministic resolver/verifier and provenance are mandatory; challenge candidates retain the exact published ranking. |

For a **separately scoped future product**, a defensible supervised target could be human preference ranking among candidates that already passed feasibility, extraction field selection, or recurrence classification from separately labeled histories. Such a future preference model must not reorder this challenge's candidates, whose ranking is mandated at `problem_statement.md:187-196`. The target should not be raw “buy/wait” if the label conflates affordability, seller options, and user preference. None of those richer training labels is present here. The 25 solved rows should be kept as golden end-to-end tests rather than consumed as the only training set, because there would be almost no credible leakage-safe holdout.

### Constraints a classifier cannot enforce by itself

A model score alone cannot guarantee:

1. every projected post-event/payment balance stays at or above the user's minimum on every day;
2. pending debit reservation and pending-credit exclusion;
3. conversion at the exact settlement-date directed rate;
4. lifecycle de-duplication, cancellation/amendment precedence, or exclusion of failed/cancelled/unrealized rows;
5. exact installment schedule equality to a supplied option and exact fee-inclusive sum;
6. partial-payment two-row arithmetic, request/user permission, and deadline;
7. maximum installment preference and method eligibility;
8. no more than three permitted, non-protected, flexible recurring changes, with every reduction at or above its supplied `minimum_allowed_amount` and no missing floor treated as permission to reduce to zero;
9. deterministic candidate ranking and tie-breaking; or
10. exact CSV grammar, row coverage, allowed enums, and date ordering.

A learned module may propose a fact or preference, but a hard verifier must reject any candidate violating those invariants.

## Recommended MVP — explicitly a design inference

No empirical comparison was run. For this dataset and deadline, the lowest-risk architecture is:

1. **Load and validate schemas.** Build typed decimal/date records; verify unique IDs and exact join coverage. Never use binary float for money.
2. **Resolve evidence into provenance-bearing facts.** Structured CSV is primary. For blank amounts, locally extract candidate fields from the linked PNG; for messages, extract only factual amendments/cancellations/dates/amounts. Treat embedded instructions as data. Apply the specified conflict order.
3. **Normalize lifecycle and currency.** Mark settled/pending/scheduled/failed/cancelled/unrealized cash states, de-duplicate lifecycle rows, and convert foreign cash only with the supplied settlement-date direction.
4. **Infer supported recurrence conservatively.** Group repeated user/description/category/event-type histories, infer cadence only when repeated timing supports it, and forecast essential variable spend conservatively. Keep confidence/provenance and avoid inventing income.
5. **Build an event-driven 90-day ledger.** Start at request-date available balance; place reserved pending debits, scheduled debits, confirmed salary credits at settlement, and recurrence forecasts on dated events. Define deterministic same-day ordering conservatively.
6. **Compute no-change capacity.** If `B_t` is the base projected balance after required events and `M` is minimum balance, an immediate payment reduces every future state. A natural safe-today bound is `max(0, min_t(B_t - M))`, capped at requested amount. Apply exact Decimal rounding rules once specified.
7. **Enumerate candidates.** Full now; exact two-payment partial plan if eligible; each supplied installment option unchanged; waiting/full payment on candidate dates; and only necessary combinations of up to three permitted changes. Enforce each reduction's `minimum_allowed_amount`; never infer a zero floor from a blank. Reject plans finishing after deadline.
8. **Verify each candidate independently.** Replay the whole ledger, including fees and spending changes, and assert every hard rule. Compute `earliest_date_for_full_payment` independently of method preferences as required (`problem_statement.md:163`).
9. **Rank valid plans by the published order.** Deadline completion, no changes, lower total, earlier start, fewer payments, lowest option ID (`problem_statement.md:187-196`).
10. **Render deterministic output and grounded explanations.** Explanations should cite balance/minimum, decisive reserved/confirmed facts, and selected option/action—not free-form invented rationale. Cache any extraction outputs and record model/token/cost use honestly.

### Explicitly synthetic arithmetic example—not evaluation output

Suppose a synthetic user has current balance 1,000, a minimum reserve of 300, and asks to pay 600 today. Current balance alone suggests 1,000 − 600 = 400, apparently safe. But if an essential pending debit of 250 settles tomorrow, the path becomes 1,000 − 600 − 250 = 150, below the 300 minimum. Before optional changes, the maximum safe payment is instead 1,000 − 250 − 300 = 450. The example shows why the target is the minimum slack over the future cash-flow path, not simply current balance minus reserve.

## Material ambiguities to make explicit in implementation

These are reporting uncertainties, not requests for user decisions:

1. **Balance snapshot:** the spec does not state precisely which same-day or pending events are already reflected in `current_available_balance`. Replaying all settled history would clearly double-count; same-day ordering still needs a documented conservative convention.
2. **Pending-debit timing:** “reserve pending debits” can mean deduct capacity immediately or on settlement date. Immediate reservation is safer; the displayed ledger may still place settlement on its date.
3. **90-day inclusivity:** whether the interval is request date through day 89 or through day 90 is unstated.
4. **Earliest-full horizon:** unclear whether safety for a candidate later payment checks only the remainder of the original 90-day window or opens a new 90-day window from that candidate date. The surrounding text favors the original request-anchored forecast.
5. **Recurrence support:** no minimum occurrence count, cadence tolerance, variable-spend estimator, or conservative quantile/max rule is specified.
6. **Spending-change semantics:** public examples target settled historical event IDs, but duration and how a historical event maps to future occurrences are implicit.
7. **Installment-month cap:** compare payment count to `max_installment_months`, or compute elapsed calendar months from first to last payment? Options use 28/30/31-day intervals, but the rule is not formalized.
8. **Money precision:** output examples show both integer-like and two-decimal currencies; rounding mode and currency-specific minor units are not defined.
9. **Image field selection:** documents may show gross, deductions, net, due, paid, and totals. The schema says extract “the amount” but not a universal field-selection rule; event semantics and provenance must govern it.
10. **Conflict source identity:** “newer record from the same source” is specified, but `source_type` is coarser than a concrete sender/source ID.
11. **Template location:** `problem_statement.md:41` says fill `dataset/output.csv`, while README explicitly requires root `output.csv`; use root and preserve dataset input.

## Concrete upload checklist

Official submission URL (required exact link):

https://www.hackerrank.com/contests/hackerrank-orchestrate-september26/challenges/buy-or-wait/submission

No upload or URL-reachability check was performed.

### The three required upload artifacts

- [ ] **`code.zip`** — complete runnable solution, prompts/configuration, setup/run README, and required `evaluation/` folder (`problem_statement.md:227-234`; `README.md:177-193`).
- [ ] **root `output.csv`** — generated predictions for exactly the 250 rows in `dataset/requests.csv`; do not submit or overwrite the blank `dataset/output.csv` reference (`README.md:29-55`).
- [ ] **`chat_transcript`** — upload the root `log.txt` described at `README.md:162-185`; redact/no secrets. The current source does **not** ignore `log.txt` despite the README claim, so add an ignore rule during authorized implementation/packaging before any commit, while still retaining the file for upload.

### `code.zip` contents and reproducibility

- [ ] Runnable terminal entry point (`python3 code/main.py` is documented at `README.md:38-44`, or clearly document an alternative).
- [ ] Dependency/setup instructions and deterministic full-run command.
- [ ] No API keys, credentials, `.env`, organizer-only data, generated caches containing private data, or hidden labels.
- [ ] Prompts/config needed to reproduce any extraction/model calls.
- [ ] `evaluation/usage_report.md` describing the **final full-dataset run**.

### Usage/cost report fields

Per `problem_statement.md:239-249` and `README.md:150-158`, include:

- [ ] model provider(s) and exact model name(s);
- [ ] model-call count;
- [ ] input tokens and output tokens;
- [ ] total and average tokens per request;
- [ ] estimated total cost and estimated cost per request;
- [ ] per-model and overall totals if more than one model is used;
- [ ] explicit, honest zero calls/tokens/cost if the final run is wholly deterministic rather than inventing a provider;
- [ ] no credentials or sensitive configuration.

### Final output validation

- [ ] Exact column order: `request_id,amount_safe_to_pay,affordability_status,recommended_payment_method,payment_plan,earliest_date_for_full_payment,spending_changes_needed,decision_explanation`.
- [ ] 250 data rows + header; unique IDs; exact ID-set equality with `dataset/requests.csv`.
- [ ] `0 <= amount_safe_to_pay <= requested_amount` for every row.
- [ ] Allowed status/method enum only.
- [ ] Chronological plan; `none` when no payment; exact sums using Decimal.
- [ ] Full-now earliest date equals request date; blank earliest only when no full payment is safe within forecast.
- [ ] Partial plan has exactly two eligible payments and completes by deadline.
- [ ] Installment plan exactly matches one supplied option, respects user/month preference, includes fee/total, and completes by deadline.
- [ ] At most three mutually compatible changes, each a permitted non-protected flexible recurring expense; every `reduce_to:` amount is at or above the supplied `minimum_allowed_amount`, and a blank floor never grants reduction-to-zero permission.
- [ ] Deterministic 90-day replay proves the minimum-balance invariant for every recommended plan.
- [ ] Explanations agree with computed facts and recommendation.
- [ ] Transcript is complete for the development session and contains no secrets.

## Starter implementation status

Exact local evidence:

```text
$ git ls-files -s code/main.py
100644 e69de29bb2d1d6434b8b29ae775ad8c2e48c5391 0 code/main.py
$ stat -f 'path=%N bytes=%z' code/main.py
path=code/main.py bytes=0
$ test -e output.csv || echo 'output.csv: ABSENT'
output.csv: ABSENT
```

Therefore starter code currently implements nothing. `README.md:29-44` only designates the empty path and run command.

## Reproducible command log and aggregate outputs

All commands were read-only except the separately required report/status/log writes.

### Source identity and inventory

```bash
pwd
git rev-parse HEAD
git branch --show-current
git status --short
find dataset -maxdepth 4 -type f -print | sort
find dataset -maxdepth 4 -type f -exec stat -f '%N|%z' {} \; | sort
wc -l AGENTS.md README.md problem_statement.md code/main.py dataset/*.csv
```

Key output:

```text
cwd=<local worktree checkout>
commit=963ad7eb3d058ace2bf2e8bf4324472c981f0840
branch=<empty: detached HEAD>
git status --short=<empty: clean>
AGENTS.md 257 lines; README.md 193; problem_statement.md 251; code/main.py 0
CSV physical lines: rates 135; events 25,343; profiles 276; images 17;
messages 216; template 251; options 791; evaluation requests 251; samples 26
16 PNG files under dataset/media/images/
```

### Exact schema/missingness census command

```bash
python3 - <<'PY'
import csv
from pathlib import Path
for p in sorted(Path('dataset').glob('*.csv')):
    with p.open(newline='', encoding='utf-8-sig') as f:
        rows=list(csv.DictReader(f))
    print(p, 'rows=', len(rows), 'columns=', list(rows[0]) if rows else [])
    print('missing=', {c:sum(not r[c] for r in rows) for c in rows[0] if sum(not x[c] for x in rows)})
PY
```

Authoritative reproduced output should be:

```text
exchange_rates rows=134; blanks={}
financial_events rows=25342; blanks={amount:16, settlement_date:10, linked_event_id:25284, minimum_allowed_amount:22435}
financial_profiles rows=275; blanks={expense_categories_user_is_willing_to_reduce:39, expense_categories_user_is_willing_to_stop:62, max_installment_months:119}
images rows=16; blanks={}
messages rows=215; blanks={request_id:87, related_event_id:176}
output template rows=250; each of seven prediction fields blank
request_payment_options rows=790; blanks={payment_frequency_days:275}
requests rows=250; blanks={}
sample_requests rows=25; blanks={earliest_date_for_full_payment:7}
```

### Core aggregate checks

The following standard-library-only pattern was used for bounded aggregates (no row-level evaluation predictions):

```bash
python3 - <<'PY'
import csv
from collections import Counter
from pathlib import Path
L=lambda n:list(csv.DictReader(open('dataset/'+n,newline='',encoding='utf-8-sig')))
req,sam,prof,evt,opt,msg,img,out=map(L,['requests.csv','sample_requests.csv','financial_profiles.csv','financial_events.csv','request_payment_options.csv','messages.csv','images.csv','output.csv'])
R,S={r['request_id'] for r in req},{r['request_id'] for r in sam}
RU,SU={r['user_id'] for r in req},{r['user_id'] for r in sam}
print(len(req),len(R),len(RU),len(sam),len(S),len(SU),len(R&S),len(RU&SU))
print(Counter(r['affordability_status'] for r in sam))
print(Counter(r['recommended_payment_method'] for r in sam))
print(Counter(Counter(r['request_id'] for r in opt).values()))
print(Counter(r['payment_method'] for r in opt))
print(Counter(r['status'] for r in evt))
print(len({r['event_id'] for r in evt if not r['amount']}),len({r['related_event_id'] for r in img}))
print(sum(Path('dataset/media/images',r['image_id']+'.png').is_file() for r in img))
print(len(out),{r['request_id'] for r in out}==R)
PY
```

Key output:

```text
evaluation: 250 rows/IDs/users; samples: 25 rows/IDs/users; intersections: 0 IDs, 0 users
affordability Counter({affordable_with_plan:9, not_affordable:7, affordable_later:6, affordable_now:3})
method Counter({not_recommended:7, full_payment:6, wait:6, installments:5, partial_payment:1})
options/request Counter({3:180, 2:65, 4:30})
methods Counter({installments:515, full_payment:275})
status Counter({settled:25148, pending:71, scheduled:70, cancelled:22, failed:21, unrealized:10})
blank amount event IDs=16; image event IDs=16; exact sets equal
image files present=16
blank template rows=250; exact evaluation request-ID set=True
```

The reduction-floor cross-check was:

```bash
python3 - <<'PY'
import csv
from collections import Counter
with open('dataset/financial_events.csv',newline='',encoding='utf-8-sig') as f:
    rows=list(csv.DictReader(f))
print(dict(sorted(Counter((r['flexibility'], 'floor_present' if r['minimum_allowed_amount'] else 'floor_blank') for r in rows).items())))
PY
```

```text
{('fixed', 'floor_blank'): 21138,
 ('reducible', 'floor_present'): 2682,
 ('reducible_or_stoppable', 'floor_present'): 225,
 ('stoppable', 'floor_blank'): 1297}
```

### Image commands

```bash
file '<supplied image>' dataset/media/images/*.png
# Then used the image-capable local read tool on the supplied JPEG and dataset/media/images/image_01.png.
```

The supplied JPEG and bounded PNG both decoded. A separate optional `from PIL import Image` dimension check failed because Pillow is not installed; no installation followed.

## Checks not performed / non-authorization

- No organizer-only file discovery or content inspection.
- No evaluation prediction or request-level affordability conclusion.
- No training, sample scoring, benchmark, cross-validation, or claim that one ML algorithm wins.
- No external literature search; firstmate is handling that track.
- No paid/local model inference, OCR service, third-party upload, package install, or live exchange/banking/market call.
- No inspection of the other 15 dataset image contents; only their local inventory/dimensions/link integrity.
- No validation of the official portal's reachability or portal-specific UI.
- No code/package implementation, root output generation, commit, push, PR, or remote network contact.
- No alteration of selected source files, `.gitignore`, or plan. The organizer remote remains read-only and this report does not authorize future publication there.
- No independent review of firstmate's future synthesis.

## Evidence-quality note

The repository contract and aggregate dataset facts are primary local evidence at the pinned commit. Architecture recommendations and interpretations are labeled as inferences. The report deliberately makes no empirical performance claim and no claim about hidden evaluation labels.
