# Buy or Wait: build a financial planner, not just a purchase classifier

**Research date:** 12 September 2026  
**Scope:** the supplied HackerRank repository and illustration; algorithm choice, agent design, learning strategy, evaluation, and submission. This is a proposal, not an implemented or benchmarked solution.

## Executive summary

**Recommendation: build a hybrid agent—AI to extract financial facts from messages/images, deterministic code to calculate cash flow and choose a valid payment plan. Do not make logistic regression the final authority on whether to buy.** This is an engineering judgment based on the challenge contract and available data, not a measured algorithm winner. [L1, L2, L4]

Your logistic-regression intuition is sensible for a narrower question: “Given labeled examples, how likely is a particular outcome?” But this challenge asks “How much is safe today, how should the full payment be scheduled, and will the balance remain above the user's minimum throughout 90 days?” A class probability alone does not construct or validate that schedule. [L2, S1, S5]

The inspected dataset contains **250 evaluation requests, 25 solved public examples, 275 profiles, 25,342 financial-event rows, 790 payment options, 215 messages, and 16 images**. The 250 evaluation requests are not 250 labeled training examples. There is no supplied purchase-satisfaction or regret target. The public examples include only one partial-payment example. [L4]

The largest near-term risks are incorrect financial reconstruction, recurrence assumptions, gross-versus-net image extraction, payment-option timing, and double counting—not choosing between a sigmoid and a tree. The starter `code/main.py` is empty, and no root-level predictions existed when inspected. Prioritize a runnable, auditable end-to-end solution before adding learned preferences or a conversational interface. [L4; design inference]

**Submission:** prepare `code.zip`, root-level `output.csv`, and `chat_transcript`; include `evaluation/usage_report.md` inside the ZIP. Portal-specific verification is **BLOCKED: Access Denied** in the research browser. The local submission requirements remain available and are summarized below. [L1, L2, V1]

## 1. What the task really measures

### Affordability is not the same as “worth buying”

There are three separate questions:

1. **Financial feasibility:** can the household make all payments while protecting obligations and its minimum balance?
2. **Allowed method:** does the user accept full payment, partial payment, or installments, and does a supplied option meet the deadline?
3. **Personal value:** will the user consider the purchase worthwhile afterward?

The challenge specifies the first two, along with priorities, protected categories, spending adjustments, explanations, and deterministic plan ranking. It does not supply satisfaction/regret labels for the third. For investment requests, it explicitly excludes asset-price prediction and security selection. Do not silently turn affordability into a prediction of investment returns or subjective value. [L2, L4]

The output has eight columns, including `request_id`. It combines a number, four possible affordability statuses, five possible methods, dated payments, a capacity date, spending actions, and an explanation. A binary “buy/wait” model discards most of the evaluated contract. Even separate regressors/classifiers would still need a common financial model to keep their outputs consistent. [L2; inference]

### Important facts from the local inspection

| Finding | Consequence for the design |
|---|---|
| 25 solved examples; their users and request IDs do not overlap the evaluation set | Use them as public end-to-end fixtures, not a claimed independent large training corpus. |
| The sample CSV has 15 columns: eight request fields plus seven additional prediction fields | `request_id` is shared between input and output; do not duplicate it. |
| 16 event amounts are blank; every one links to an existing image | Image extraction is necessary for these facts. Blank is not zero. |
| 76 messages have neither a request ID nor an event ID | Retrieve relevant user-level messages too; request-ID-only retrieval loses evidence. |
| 790 options include 275 full-payment and 515 installment options across sample and evaluation requests | Generate installment schedules from supplied fields; do not invent offers. |
| Only 75 evaluation installment offers finish by the desired date before further eligibility/safety checks | A listed offer is not automatically usable. This is a structural count, not a predicted outcome. |
| Every evaluation desired date is within 86 days of its request | Reject over-deadline offers rather than extending the window to make them fit. |
| No explicit recurrence/cadence identifier is supplied | Learn patterns from supported history through explicit inference rules; do not treat every expense as recurring. |

These are measured dataset aggregates or direct schema observations, not hidden labels. Detailed commands and evidence are in the local research notes. [L4]

## 2. What your illustration gets right—and what it leaves out

I inspected the supplied JPEG. It describes linear regression as predicting a number; logistic regression as predicting a class with probability; trees as asking questions; SVM as finding a wide separating boundary; and nearest neighbours as copying nearby examples. Its main captions are legible. [I1]

That is a useful introduction, but the picture assumes that the prediction target, training examples, and meaning of success are already defined. It omits time, financial constraints, label availability, and structured schedules. “We need a yes/no answer” is not sufficient evidence that logistic regression should control this agent. [I1, S1; interpretation]

For binary logistic regression, a possible future model would be:

\[
P(y=1\mid x)=\sigma(b+w^Tx),\qquad \sigma(z)=\frac{1}{1+e^{-z}}.
\]

You must first define `y`: “can pay safely now,” “prefers this feasible plan,” and “later regretted the purchase” are different targets. Coefficients are fitted from labeled examples, usually through regularized log loss. The resulting probability is not proof that every payment keeps the balance above the minimum. [S1]

**Linear regression versus linear programming:** these are not the same. Linear regression estimates a numeric target from data. Linear programming selects decision variables under constraints. A payment-planning problem can sometimes benefit from the latter without training the former. For this small number of supplied offers, simple enumeration and simulation should come first. [S1, S5; design inference]

## 3. Algorithm comparison for this hackathon

| Approach | What it could do | What it cannot establish by itself | Recommendation |
|---|---|---|---|
| Linear regression | Estimate a numeric quantity, such as future spending, if suitable histories/targets exist | Exact safe capacity, pathwise constraints, or a compliant schedule; clipping an estimate fixes bounds, not safety | Not the core decision engine |
| Logistic/multinomial regression | Estimate status or method probabilities from labeled features | Generate all dates/amounts/actions consistently or guarantee feasibility | A reasonable later supervised baseline, not needed for the MVP |
| Trained tree / gradient boosting | Model nonlinear tabular interactions when legitimate labels exist | Enforce the complete payment contract without explicit checks | Optional later benchmark, not a substitute for the ledger |
| SVM / nearest neighbours | Alternative classifiers for a defined labeled task | Solve the missing-target problem or payment scheduling; nearest neighbours also needs a meaningful feature scale | No clear reason to spend hackathon time here |
| Explicit rules + dated cash-flow simulation | Directly compute capacity and test allowed plans | Automatically resolve every ambiguous message/image or unspecified recurrence convention | Build this financial core first |
| AI evidence extraction + deterministic planner | Interpret unstructured evidence while retaining exact arithmetic and constraints | Guarantee extraction is correct merely because its JSON is well formed | Recommended overall architecture |
| Tabular foundation model | Strong modern supervised tabular candidate with labeled context | Replace task-specific evidence reconstruction or make hard safety checks unnecessary | Interesting later; not the current bottleneck |

This comparison is a task-fit analysis, not an accuracy leaderboard. The statistical-model descriptions are supported by the documentation and literature; the recommendations are explicitly our design judgments. [S1, S5, S6, S7, S8, L2, L4]

### Caveat: “trees always beat deep learning” would be wrong

A 2022 study found strong tree performance on its medium-sized tabular benchmark. The 2025 Nature TabPFN paper reports strong results for a tabular foundation model in its own benchmark setting. The original TabPFN description explicitly conditions on labeled examples. These sources disagree with a blanket dismissal of neural tabular models, but neither tells us which model would win on this challenge. No such comparison was run here. [S6, S7, S8]

The recommendation against training a purchase classifier first is therefore **not** “logistic regression is bad” or “neural models need huge datasets.” It is that the provided task is largely a constrained financial computation, the richer preference targets are absent, and the permitted examples are too sparse to support the broad performance claims the user needs. [L2, L4; inference]

## 4. How the recommended agent should work

The following is a proposed implementation, not existing functionality.

```text
Local dataset files
    -> typed records and ID-based retrieval
    -> structured fact extraction from relevant messages/images
    -> evidence conflict resolution, lifecycle normalization and dated FX
    -> conservative 90-day cash-flow projection
    -> no-change capacity calculation + candidate payment schedules
    -> financial/contract validation + exact published ranking
    -> root output.csv + explanations + final-run usage report
```

### A. Retrieve the actual user context

Read the supplied CSVs from `dataset/`; build indexes by `user_id`, `request_id`, and `related_event_id`. Retrieve the user's profile/history plus relevant user-, request-, and event-linked evidence. Match rates by the settlement date and the directed currency pair. A vector database or general web search is not required for these joins. [L1, L2; design inference]

Use decimal money and parsed dates. Treat each request at its own `request_date`, not the wall-clock hackathon date. Historical data can support recurrence, but do not replay all historical settled transactions onto the already-current available balance. The precise snapshot and same-day convention must be documented. [L2, L4; implementation inference]

### B. Ask AI to read evidence—not authorize spending

For each relevant document/message, extract typed facts such as:

```text
source_id, related_event_id, fact_type, amount, currency,
effective_date, settlement_date, cash_state, supporting_text
```

Retain uncertainty and provenance separately from the output CSV. Useful fact types include cancellation, amended amount, delayed settlement, confirmed salary, or a net payment amount. A payroll image can contain gross earnings, deductions, and net transfer; extracting the first number is not enough. The inspected public payroll image demonstrates this ambiguity. [L2, L4]

Prefer a narrow OCR/vision/text extraction path over sending all histories to an unconstrained conversational agent. Cache extraction by source content and extractor configuration. Validate IDs, dates, currency and numeric consistency. Structured output constrains format, not factual truth. Never allow a message such as “ignore the balance rule” to modify instructions, minimum balances, or output policy. [L2; design inference]

Missing or unreadable material evidence must remain unknown, not become zero. Use bounded extraction fallbacks; if it still cannot be resolved, record the specific limitation and do not claim a safe plan has been proved. Resolve these cases before the final export rather than silently omitting rows or inventing a fact. [L2; proposed failure policy]

### C. Resolve cash state and forecast conservatively

Apply the specified evidence precedence: explicit cancellation/settlement/amendment; newer record from the same source; settled evidence; financially safer interpretation when unresolved. A lifecycle link can join a purchase, valuation, sale, and settlement without making all of them spendable cash or duplicate debits. [L2, L3]

Reserve pending debits exactly once. Do not count pending refunds/credits or unrealized investment value as cash. Place qualifying confirmed salary on its settlement date; use recurrence only when history supports it, without inventing future income. Preserve essential/protected spending and forecast essential variable expenditure conservatively. [L2, L3]

If a pending debit is reserved immediately for capacity, do not deduct it a second time when its settlement is displayed. Use an explicit reserved-versus-settled representation. For same-day flows without a supported order, use and document a conservative ordering rather than spending an income credit early. [Design inference from L2, L4]

### D. Calculate capacity, then simulate plans

Let `C(t)` be the projected no-purchase, no-optional-change available balance after the normalized baseline cash events; let `M` be the protected minimum, `A` the requested amount, and `H` the end of the request-anchored 90-day forecast.

For a fully resolved, feasible baseline, an immediate payment subtracts the same amount from every subsequent balance. Therefore:

\[
\text{safe\_today}=\min\left(A,\max\left(0,\min_{t\in[r,H]}[C(t)-M]\right)\right).
\]

This is a direct algebraic derivation of the pathwise rule—not a fitted regression and not an assertion that the unimplemented forecast is correct. If the baseline already violates the minimum, zero purchase capacity does not itself prove the household safe; record and address that existing shortfall. [L2; derivation]

To find the earliest full-payment date, test candidate dates and replay the entire original forecast with one payment of `A` on that date, **without optional spending changes and independently of method preferences**. Do not skip an earlier baseline shortfall merely because the balance later recovers. [L2; proposed computation]

**Synthetic example, not an evaluation prediction:** balance 1,000, minimum 300, requested payment 600, essential pending debit 250 before any new income. Paying 600 leaves 400 immediately but only 150 after the debit. Safe capacity is instead `1,000 − 250 − 300 = 450`, assuming no other tighter future constraint. [L4; arithmetic derivation]

### E. Enumerate eligible schedules and enforce exact rules

- **Full payment:** require safe full capacity and acceptance of `full_payment`.
- **Partial payment:** both request and user must permit it; require `0 < safe_today < A`; pay exactly `safe_today` on the request date and the remainder on `earliest_date_for_full_payment`, no later than the desired date. No seller partial-payment offer is required.
- **Installments:** construct the supplied offer unchanged from `first_payment_date`, `payment_frequency_days`, `number_of_payments`, and `payment_amount`. Include the fee-inclusive total; respect user preference and installment limit; reject late or unsafe offers. A 30-day interval is not necessarily a calendar month.
- **Spending changes:** consider at most three permitted, non-protected, flexible recurring changes; respect `minimum_allowed_amount` when supplied. A blank floor is not automatically permission to reduce spending to zero. Stopping and reducing the same event are mutually exclusive. Re-simulate all affected future occurrences, not a refund of historical spending.
- **Wait:** eligible when a later safe full payment exists and the user accepts full payment. Do not describe a date after the requested deadline as meeting the deadline.
- **Fallback:** use `not_recommended` when no safe eligible payment exists; keep financial-capacity fields distinct from preference eligibility. [L2, L3, L4]

For every candidate payment schedule `p`, replay:

\[
C_p(t)=C(t)+\text{permitted savings through }t-\sum_{d\le t}p(d),
\qquad C_p(t)\ge M.
\]

Savings must be attributable to permitted future reductions, not fabricated credits. Validate every event/payment state, payment sum, deadline and grammar. All considered offers are few enough to start with enumeration; use a small constrained solver only if continuous reduction choices create a concrete need. [L2, L4; proposed design]

Among safe eligible plans, apply the exact published order: deadline completion, no spending changes, lower total payment, earlier start, fewer payments, lowest `payment_option_id`. **Do not replace this ranking with a learned preference score in the challenge.** [L2]

### F. Write consistent output and explain the binding constraint

`amount_safe_to_pay` is always the no-change capacity today, even if the chosen plan needs spending changes. The earliest-full date is also no-change capacity, not the date the final installment happens to end. It can equal today even if installments are recommended because full payment is not an accepted method. [L2]

Render explanations from the validated ledger: what is reserved, which confirmed income matters, which option is selected, the lowest projected balance, and any allowed change. The explanation should be a readable summary of actual calculations, not a second AI decision that can contradict them. [L2; design inference]

## 5. Three concrete proposals

### Proposal A — deterministic planner with local OCR

**Build:** the ledger, recurrence rules, candidate enumerator, verifier and templated explanations; extract document fields through generic OCR plus parsers.

**Advantages:** small financial core, explicit rules, reproducible arithmetic, no need to train a decision model. **Main risk:** heterogeneous documents and natural-language amendments may outgrow simple parsers. A purely rule-based system may also need clearer justification of its AI component under an “AI-powered agent” challenge. Do not assume rules-only eligibility has been confirmed by the organizers. [L1, L2; design judgment]

### Proposal B — hybrid extraction plus deterministic planner — recommended

**Build:** Proposal A's financial core, with narrowly scoped pretrained text/vision extraction where needed. Reuse extracted evidence across relevant requests; keep prompts, schemas and usage accounting reproducible.

**Advantages:** directly addresses both structured and unstructured inputs without asking a language model to perform all arithmetic. **Main risk:** extraction errors; required controls are provenance, typed validation and exact financial replay. Model/provider selection remains open until its actual accuracy, availability, data policy and cost are tested. No provider-specific performance or budget is promised here. [L2, L4; design judgment]

### Proposal C — learned preference/forecasting layer — later research, not the submission core

**Build later:** collect appropriate feedback or annotated histories; compare a regularized logistic baseline against a modest tree model and, if warranted, a tabular foundation model.

**Possible targets:** preference between already-safe plans, independently annotated recurrence, or later observed variable expenditure. **Not supplied:** a reliable purchase-worth/regret label. **Main risk:** selection bias, weak labels, and confusing user clicks with financial wellbeing. Keep required financial constraints outside the model; a post-hackathon preference ranker must not alter the hackathon's mandatory ranking. [L4, S1, S3, S7, S8; proposal]

**Recommended sequence:** get A's financial core working, add B's extraction path where evidence requires it, and defer C until it has real labels and a separate evaluation question.

## 6. How it can learn responsibly

“Learning user context” need not mean retraining a classifier every time a file arrives. There are distinct mechanisms:

1. **Context reconstruction now:** refresh the ledger and explicit preferences from supplied evidence. This is state estimation, not a supervised training claim.
2. **Pretrained interpretation now:** use an existing text/vision model to extract facts. Its general capabilities come from prior training; this project need not fine-tune it.
3. **Supervised adaptation later:** learn a narrowly defined target from permissioned, quality-controlled labels. [L2, S1, S8; design distinction]

If the future target is plan preference, collect request-time features, the feasible alternatives shown, the selected action, and separately defined later feedback. A click or purchase is not automatically a “good financial decision” label. Use only features available when the decision was made; an already-known future salary schedule is legitimate context, but a later-realized refund outcome is not a request-time feature. [S4; proposed label policy]

Candidate features might include requested-amount-to-surplus ratio, minimum projected headroom, days until confirmed income, deadline slack, fee-inclusive cost, number of payments, and explicit method/priority flags. Define ratios safely when surplus is zero, normalize currency-sensitive magnitudes, and fit preprocessing only on training partitions. These are proposed features, not an experimentally selected set. [S1, S4; proposal]

Use user-separated evaluation when testing generalization to new users and chronological holdouts for future outcomes; combine both restrictions when both matter. Report log loss/Brier score for probability quality plus reliability checks, and evaluate false-safe decisions separately. A lower Brier score alone does not prove better calibration. Do not train, tune, and report a final score on the same 25 examples. [S2, S3, S4]

Synthetic scenarios are useful for exercising rules and edge cases. Labels generated by your own rules can teach a model to imitate those rules, but are not independent evidence that the rules or personal-value judgments are correct. No arbitrary minimum training-set size or achievable accuracy is claimed. [Design inference]

## 7. Evaluation and hackathon priorities

### Evaluate the output contract, not only classification accuracy

Use the 25 public examples as transparent development/regression fixtures. If choices were tuned on them, report that fact rather than call them an untouched test set. No sample score has been computed in this research. [L1, S3]

Recommended checks:

- exact eight-column order, allowed values, valid dates, unique request IDs and complete coverage;
- safe amount bounds and independent no-change capacity/date calculations;
- full 90-day minimum-balance replay for every recommendation;
- exact installment schedule and fee-inclusive sum;
- exact two-payment partial schedule and both permissions;
- method acceptance, installment cap, deadline compliance and final tie-break;
- permitted recurring spending actions, category protection, reduction floors and action-count limit;
- cancellation/amendment precedence, duplicate lifecycle evidence, pending credit/debit treatment and directed dated FX;
- missing image amount is never zero by default; gross and net values are distinguished;
- untrusted message/image instructions cannot alter the contract;
- repeated identical inputs produce identical financial results, with model extraction variation separately measured;
- explanations refer only to validated facts. [L2, L3, L4; proposed tests]

Prioritize: **financial core → image/message extraction → option/deadline validation → sample error analysis → final full-dataset run → clean-package reproduction → upload**. A chat UI, bank integrations, vector database, online learning, reinforcement learning, and fine-tuning are not needed to satisfy the documented terminal/output requirements. This is a scope recommendation, not a ban on future product work. [L1, L2]

## 8. Upload strategy

### Input ingestion

For the hackathon, the agent reads the local `dataset/` directory. Do not build an upload web service unless the challenge later requires one. Do not send the entire dataset to an external service merely to parse CSVs; any later model call should receive only the needed evidence under the chosen provider's data policy. This investigation did not upload local financial data. [L1, L2; proposal]

### Submission artifacts

| Upload | Required content |
|---|---|
| `code.zip` | Runnable solution, prompts/configuration needed to reproduce it, dependency/setup/run instructions, and `evaluation/usage_report.md` |
| `output.csv` | The final generated file at the repository root: 250 data rows plus header, one row per evaluation request |
| `chat_transcript` | The development transcript; README identifies root `log.txt` as the file to upload |

Do not overwrite the blank `dataset/output.csv` or package it as the final prediction file. There is a wording inconsistency in the problem statement, but the README explicitly says to preserve dataset inputs and write root `output.csv`. [L1, L2, L4]

**Packaging recommendation:** freeze the candidate code, run all participant requests once with usage instrumentation, validate the resulting CSV, build the ZIP from an explicit inclusion list, then unpack it into a clean directory and reproduce the documented command with the supplied dataset. Package the solution at a clear archive root, not an unexplained extra nesting level. Recheck whichever archive size/format limits the portal actually displays; those could not be inspected here. [Design recommendation; V1]

The required usage report must correspond to the final run that produced the submitted `output.csv`: exact provider/model names, calls, input/output tokens, totals and per-request averages, estimated total/per-request costs, and per-model plus overall totals if multiple models are used. Include retries and shared extraction calls in full-run totals; allocate shared cost transparently. Keep research/coding-assistant usage separate from application inference. If the final application uses no token-billed models, report that honestly rather than inventing token usage. [L2; accounting proposal]

**Current packaging issues:** `code/main.py` is empty; predictions do not yet exist; `log.txt` was not ignored by Git at inspection despite the README saying it is (since resolved: `.gitignore` now lists `log.txt`). Before any future commit, ensure the transcript is excluded from Git while retaining it for submission, and confirm it covers the development conversation rather than assuming the scout's entries cover earlier turns. No ignore-file or implementation fix was made by this research. [L4]

Exclude credentials, `.env`, `.git`, organizer-only files, hidden labels, unnecessary financial-data copies, and private research/operational artifacts. If dataset assets are required inside the ZIP, include only permitted participant assets and document their placement; portal-specific inclusion and size requirements remain unverified. [L2; packaging recommendation]

**Exact submission link:**

https://www.hackerrank.com/contests/hackerrank-orchestrate-september26/challenges/buy-or-wait/submission

The research browser received **Access Denied**. This does not establish that the contest is closed or that your own browser cannot submit; it means portal-specific verification is blocked. No login, bypass, or upload was attempted. [V1]

## 9. Caveats, disagreements and open questions

**High confidence:** a dated, constraint-checking financial core is necessary; the output is not binary; public examples are sparse; blank amounts require evidence extraction; model outputs cannot override the contract. [L1–L4]

**Moderate confidence/design judgment:** hybrid extraction plus deterministic planning is the best allocation of hackathon effort. No alternatives were implemented or benchmarked, and only one public dataset image's content was inspected by the scout. Other images may require additional extraction handling. [L4]

Document and test these boundary conventions during implementation:

- whether the balance snapshot already includes same-day events or held debits;
- whether the 90-day interval includes day 90, and whether later-payment safety is evaluated over the original window (recommended interpretation) rather than a rolling new window;
- recurrence grouping, cadence tolerance, essential-variable forecast conservatism, and support for recurring income;
- mapping a historical spending-change event ID to future occurrences and respecting unknown reduction floors;
- whether the installment-month cap denotes payment count or elapsed calendar duration;
- currency precision/rounding and same-day debit/credit ordering;
- handling a financially possible full-payment date that misses the request deadline without labeling the request on-time;
- exact portal upload constraints and whether a wholly rule-based entry satisfies the organizer's AI expectation. [L2, L4, V1]

These are uncertainties to document, not assertions that the rules permit arbitrary choices. The 25 examples can expose inconsistent interpretations, but tuning to them is not evidence of generalization.

**Bottom line:** keep the intelligence in extracting and reconciling context, and keep the money decisions in auditable constrained calculations. Logistic regression is a valid tool for a later well-defined learned component—not the foundation this submission currently needs.

## Source map for citation pass

- L1: `README.md`, especially quick start/output location, suggested workflow, evaluation, token usage and submission.
- L2: `problem_statement.md`, full document; especially output semantics, 90-day safety, plan ranking and submission.
- L3: `AGENTS.md`, participant data, financial rules, logging and mandatory submission URL.
- L4: supervised local evidence report at commit `963ad7eb3d058ace2bf2e8bf4324472c981f0840`; final local research-note copy pending.
- I1: supplied JPEG, inspected by scout and lead; file path recorded in plan/local evidence.
- S1: https://scikit-learn.org/stable/modules/linear_model.html#logistic-regression
- S2: https://scikit-learn.org/stable/modules/calibration.html
- S3: https://scikit-learn.org/stable/modules/cross_validation.html
- S4: https://scikit-learn.org/stable/common_pitfalls.html
- S5: https://arxiv.org/abs/1710.08005v5
- S6: https://arxiv.org/abs/2207.08815v1
- S7: https://doi.org/10.1038/s41586-024-08328-6 ; PMID 39780007, PMCID PMC11711098, OpenAlex W4406170795.
- S8: https://arxiv.org/abs/2207.01848v6
- V1: direct browser verification log in `buy-wait-agent-research-direct.md`; portal returned Access Denied and PubMed HTML returned 403, although PubMed API metadata succeeded.
