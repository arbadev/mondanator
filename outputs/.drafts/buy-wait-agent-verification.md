# Buy or Wait — citation and critical-check record

Date: 2026-09-12. Reviewer: the lead (`pi`), sequential self-review. There was one local-evidence scout, but no independent verifier or reviewer agent. This is a research-document check, not executed software validation.

**Verification: BLOCKED** for complete external/access verification: the official submission page returned Access Denied; a later attempt to reopen the supplied Downloads JPEG returned `EPERM`. Earlier successful image inspection is recorded in the local report and session history. Neither blocker is bypassed. The core financial/algorithm recommendation can still be delivered with these limitations.

## Critical claim-to-evidence checks

| Claim | Evidence checked | Finding |
|---|---|---|
| Output is constrained and multi-field, not binary | Full README and problem statement; exact output and 90-day clauses | Supported directly by authoritative local contract |
| 250 evaluation rows versus 25 solved public examples | README, sample header/rows, complete scout aggregate command/results | Supported; the evaluation rows are not labeled training cases |
| Sparse examples do not establish classifier performance | Sample class distribution, official supervised-model/evaluation docs | Supported as a limited inference, not a claim that no model can ever fit 25 observations |
| There is no implemented starter | Zero-byte tracked `code/main.py` and missing root output in local report | Supported at the inspected commit; not a benchmark failure |
| Logistic versus linear regression | Official scikit-learn HTML and Context7 excerpts, exact URLs in direct notes | Supported; no probability-as-safety claim retained |
| Trees are not universally best | Historical tabular benchmark plus Nature/TabPFN metadata and HTML abstracts | Conflicting-scope evidence represented; no challenge winner inferred |
| Calibration and leakage caveats | Official calibration, cross-validation and common-pitfalls HTML | Brier/log loss correctly labeled probability-quality metrics, not isolated calibration scores |
| Safe-today formula | Algebra from the pathwise minimum-balance rule; synthetic 1,000/300/600/250 example | Correct under the stated resolved feasible baseline; not a forecast implementation or test result |
| Required upload artifacts | README and problem statement submission/usage sections | Local requirements supported; portal UI/limits blocked |
| Supplied illustration | Complete investigator description and earlier recorded lead view; later read attempt | Initial findings usable with provenance; repeat direct view BLOCKED by EPERM |
| Source authenticity | Official HTML URLs; exact Nature DOI via OpenAlex and PubMed API | Wrong Crossref CRAN record rejected; PubMed HTML 403 distinguished from API success |

## Findings requiring a revised candidate

The cited draft is preserved unchanged. The review found more than three substantive clarifications, so a complete `buy-wait-agent-revised.md` was created and used, rather than the cited draft, as the publication candidate.

| ID | Severity | Issue in cited draft | Required correction | Disk verification |
|---|---|---|---|---|
| R1 | Material | Full-payment eligibility could be read as requiring no-change capacity, excluding the full-with-changes plans shown in public samples | Explicitly generate change-enabled full payments and label them affordable_with_plan, not affordable_now | PASS — exact revised text checked |
| R2 | Material | Wait bullet did not itself require completion by the desired deadline | Make the deadline apply to every recommended plan including wait; preserve independent capacity-date semantics | PASS — exact revised text checked |
| R3 | Material | “Flexible” alone is too broad to distinguish stopping from reducing | Match each action to its flexibility subtype and user category; retain minimum_allowed_amount and distinct-action constraints | PASS — exact revised text checked |
| R4 | Material | Double-counting protection named lifecycle and reservation but not an explicit future occurrence plus its inferred recurrence | State that an explicit occurrence replaces its matching forecast instance | PASS — exact revised text checked |
| R5 | Clarity | The safe-today equation uses r without defining it | Define r as request_date | PASS — exact revised text checked |
| R6 | Accounting | Warm-cache final runs could hide the model work that produced cached facts | Distinguish final-run incurred usage and cache provenance; recommend a clearly accounted cold-cache final run | PASS — exact revised text checked |
| R7 | Evidence precision | Generic “no upload” wording and image source note omit the final read limitation | Distinguish reading bounded content in this coding session from separate uploads/API calls; document image EPERM | PASS — exact revised text checked |
| R8 | Contract completeness | Exact CSV order is referenced but not rendered as one complete header | Include exact eight-column header and explicit status/payment grammars in the report | PASS — exact revised text checked |
| R9 | Money handling | “Exact fee-inclusive sum” could imply changing supplied installments to eliminate cent-level input rounding differences | Preserve supplied installments, compare declared totals, document rounding discrepancies; never loosen the minimum-balance rule | PASS — exact revised text checked |

Public sample cross-check: `request_06`, `request_11`, and `request_21` explicitly show full payment enabled by spending changes with safe_today below the request amount. Their earliest-full dates are no-change dates and can fall after the desired deadline. This supports R1 and the distinction in R2 without deriving any evaluation prediction. Only public rows were used.

## Known limits, not claims of fixes

- No implementation, predictions, training, scoring, paid application-model call, ZIP or submission was performed.
- Only one participant image's content was inspected; all 16 paths and links were inventoried.
- Alpha authentication was unavailable. Working scientific metadata/documentation/HTML retrieval substituted without changing login state.
- A PubMed HTML 403 and HackerRank Access Denied are access failures, not proof of nonexistent sources or a closed contest.
- A later supplied-JPEG read failed with EPERM. Do not claim that repeat check passed.
- Recurrence thresholds, balance snapshots, horizon inclusivity, installment-month semantics and money rounding remain documented implementation assumptions.
- The known unignored transcript was not repaired; no .gitignore or source code change is authorized by this research. Since resolved: `.gitignore` now lists `log.txt`.

## Publication checks

Completed publication checks:

- Complete revised candidate written; R1–R9 exact text assertions all passed; superseded ambiguous text is absent.
- Final `outputs/buy-wait-agent.md` equals `outputs/.drafts/buy-wait-agent-revised.md` byte-for-byte.
- Final/revised SHA-256: `ff535cefdce574bfbd653058144219851d4b14b760963abc22f032b111280322`.
- Fourteen unique footnotes; every reference resolves; no old citation placeholders remain. The nine primary external URLs match the source/access ledger; the portal is explicitly blocked rather than counted as verified.
- All nine artifacts in the provenance manifest were checked on disk and are nonempty after the plan update. Final/revised identity, source-URL coverage and final/provenance/verification/plan status consistency passed in that final check.
- Local evidence copy equals the durable scout report byte-for-byte; copy SHA-256 `a13a2a865aa010bedc63ba3235e1275674fc536375dc056aa09357b60f819d77`.
- Cited local source paths exist; tracked participant docs/code/data have no diff. The starter is still zero bytes and no root output CSV exists.
- No software tests or benchmark were claimed. The overall access-verification label remains BLOCKED; this does not invalidate the supported core research outcome.
