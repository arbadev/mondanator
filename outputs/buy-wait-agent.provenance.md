# Buy or Wait — research provenance

- **Deliverable:** `outputs/buy-wait-agent.md`
- **Research date:** 2026-09-12
- **Publication verification snapshot:** 2026-09-12T13:42:09+00:00
- **Verification: BLOCKED** for complete access verification. The submission portal returned Access Denied and a repeat supplied-image read returned EPERM. Core source/contract checks and the sequential self-review were completed; this is not an implemented, tested, or submitted solution.
- **Approved scope:** the user's exact reply `yes` approved the existing research plan. No evidence gathering occurred before that approval. Scope remained investigation, architecture/learning/evaluation proposals, and submission guidance—not implementation, training, paid application inference, data upload, or submission.

## Outcome and confidence

The report recommends **bounded AI evidence extraction plus a deterministic, dated financial planner and validator**, rather than an unconstrained buy/wait classifier. This is a project-fit inference from the contract and available labels, not an empirical algorithm ranking.

High-confidence primary facts include the eight-field output, 90-day minimum-balance constraints, exact payment/deadline/preference rules, 250 evaluation requests versus 25 public solved examples, 16 image-linked blank amounts, and an empty starter entry point. The detailed architecture, feature proposals, learning roadmap, recurrence conventions, cost-accounting design and implementation priority are recommendations. No model's accuracy, run time, token budget, cost or benchmark performance on this dataset was measured.

## Execution and ownership

- The lead (`pi`) created the plan and waited for approval, researched general methodology, wrote the draft and cited draft, performed the critical-check pass, wrote a complete revised candidate, and published the final report.
- One supervised local-evidence scout (`pi`) inspected only participant-facing project materials, schemas/aggregates, the supplied illustration and one public sample image. It wrote a self-contained durable report. No second research survey, verifier or reviewer agent was run.
- The absence of a `subagent` tool was not concealed. The supported project-supervision launcher was used for the single scout; no invented dispatcher or question tool was called.
- Independent verification/review agents were unavailable. The review was sequential and lead-owned. The scout did not independently review the final synthesis.
- The primary tracked README/specification/AGENTS/code/dataset were not changed. The source stayed at commit `963ad7eb3d058ace2bf2e8bf4324472c981f0840`. Only the approved research artifacts and required append-only transcript logging were written in the selected project.
- The organizer remote was not adopted as a publication destination. No commit, push, PR, model training, generated predictions, ZIP, live financial call or submission was performed.

## Local primary evidence

Project root: `/Users/and3/And3 working/challenger/mondanator`.

| Source | What was inspected | Use and limitation |
|---|---|---|
| `README.md` | Complete file | Terminal entry point, root output path, sample/evaluation sizes, deterministic verification, submission/transcript instructions |
| `problem_statement.md` | Complete file | Input/output semantics, cash state, 90-day safety, supplied offers, partial schedules, spending changes, exact ranking, evaluation and usage requirements |
| `AGENTS.md` | Complete file | Participant-only boundary, logging, deadline, required URL and financial contract |
| `dataset/*.csv` | Headers, row/missingness/join aggregates and bounded public examples | No organizer-only labels; no evaluation affordability predictions |
| `dataset/sample_requests.csv` | All 25 public examples | Format and edge-case evidence; not an untouched score or independent training dataset |
| `dataset/images.csv` and `dataset/media/images/*.png` | All 16 paths, dimensions and event/user links | File availability does not mean all image contents were inspected |
| `dataset/media/images/image_01.png` | One public payroll image | Gross/deductions/net distinction; sensitive personal fields were not transcribed |
| Supplied Downloads JPEG | Earlier decoded view and investigator description of all five captions | Repeat lead read during the final check returned EPERM; the report discloses this |
| `code/main.py` and root `output.csv` | Tracked empty-file evidence and existence check | Main file remained zero bytes; predictions remained absent |
| `log.txt` ignore state | Read-only Git ignore check in local report | Not ignored despite README wording; not repaired by research |

The local investigator's disposable copy matched the selected source commit. Its temporary location is an execution detail, not a durable dependency for the report. The copied evidence file below survives cleanup and includes exact aggregate commands/results.

- Durable original: `/Users/and3/.firstmate-projects/742179949d618839b3b66086996316e808b313edf513422608293fc75d87aae4/data/bw-evidence-k7/report.md`
- Project copy: `outputs/.drafts/buy-wait-agent-research-local.md`
- Byte comparison between original and project copy: **PASS**.
- Copy SHA-256: `a13a2a865aa010bedc63ba3235e1275674fc536375dc056aa09357b60f819d77`.

## Research rounds and source selection

Exact queries, failed calls, accepted/rejected results, endpoints and HTML checks are preserved in `outputs/.drafts/buy-wait-agent-research-direct.md`.

1. **Definitions and assumptions:** supervised targets, logistic probabilities and regularization, linear regression, calibration and leakage. Three distinct semantic Alpha queries were dispatched together; all failed authentication. Built-in arXiv retrieval and connected official documentation retrieval provided alternatives.
2. **Mechanisms and objectives:** prediction versus constrained decisions, cash-flow feasibility and predict-then-optimize. The source does not establish that decision-focused model training is required here.
3. **Practical comparisons and disconfirmation:** historical tabular benchmarks, modern TabPFN counterevidence, labeled-context requirements, and recent metadata through 2026. No external benchmark was transferred to the challenge.
4. **Direct HTML checks and citation pass:** official scikit-learn sections, arXiv abstracts and Nature publisher abstract were actually read. The official submission page and PubMed HTML had access failures; PubMed's exact metadata API succeeded.
5. **Critical pass and complete revision:** the lead checked the full local report and contract, re-read the public examples, recorded nine findings, wrote a complete revised candidate and verified each correction by exact on-disk assertions. No dedicated reviewer was represented as having run.

### Sources accepted into the final report

| Final citation | Primary URL / identifier | Retrieved evidence and allowed claim |
|---|---|---|
| S1 | https://scikit-learn.org/stable/modules/linear_model.html#logistic-regression | Official HTML and Context7 excerpts: supervised targets, squared residual versus log-loss objectives, sigmoid probabilities and regularization |
| S2 | https://scikit-learn.org/stable/modules/calibration.html | Official HTML: calibration checks and independence; Brier/log loss are not calibration-only measures |
| S3 | https://scikit-learn.org/stable/modules/cross_validation.html | Official HTML: evaluation separation, groups and temporal structure |
| S4 | https://scikit-learn.org/stable/common_pitfalls.html | Official HTML: leakage, prediction-time availability and train-only preprocessing |
| S5 | https://arxiv.org/abs/1710.08005v5 | Elmachtoub & Grigas, Smart “Predict, then Optimize”; metadata and HTML abstract; conceptual prediction/decision distinction |
| S6 | https://arxiv.org/abs/2207.08815v1 | Grinsztajn, Oyallon & Varoquaux; metadata and HTML abstract; benchmark-specific tree results, not a universal 2026 ranking |
| S7 | https://doi.org/10.1038/s41586-024-08328-6 | Hollmann et al., Nature 637:319–326 (2025); publisher abstract and exact metadata; modern counterexample to blanket dismissal of deep learning on small tables |
| S8 | https://arxiv.org/abs/2207.01848v6 | Original TabPFN; metadata and HTML abstract; supervised prediction uses labeled context |

Nature metadata was confirmed through **OpenAlex W4406170795**, DOI **10.1038/s41586-024-08328-6**, **PMID 39780007**, **PMCID PMC11711098**, venue **S137773608**. The DOI resolved to https://www.nature.com/articles/s41586-024-08328-6 . OpenAlex reported hybrid OA and a CC-BY abstract license; moving citation/reference counts and author identifiers are retained in the direct notes for provenance, not used to judge algorithm fit. No licensed full text or PDF was fetched.

### Screened, corroborating, rejected or unused evidence

- arXiv `2106.11959v5` is a corroborating tabular comparison with no universal winner; not required for a final numerical claim.
- arXiv `1706.04599` was returned in the metadata batch; final calibration guidance is grounded in directly read official documentation.
- arXiv `1710.08901v1` concerns an adjacent credit-default task, not provided purchase-value labels; not used to recommend household-budget classification.
- arXiv `2307.05213v3` (DOI `10.1613/jair.1.19498`) was screened for decision-focused learning and uncertain constraints, not implemented or used as a financial performance result.
- TabPFN-related IDs `2607.26628v1`, `2507.03971v1`, `2511.03634v2`, `2406.06891v1`, `2512.03307v1`, `2507.07829v1`, and `2608.17957v1` were screened as metadata/abstract context. No unreplicated accuracy/speed number was adopted.
- A generic Crossref query for the Nature DOI returned CRAN DOI **10.32614/cran.package.tabpfn**, the wrong research object. It was rejected; exact Nature metadata was verified through OpenAlex/PubMed instead.
- Unrelated imaging, omics, citation-network, EEG, RNA, reinforcement-learning and routing results were not treated as evidence for this challenge.

## Tool and access limitations

| Capability / attempt | Actual result | Consequence |
|---|---|---|
| `web_search`, `fetch_content`, `memory_remember`, dedicated `subagent` | Not present in the visible tool set | No invented calls; plan not saved to nonexistent memory |
| Three parallel `alpha_search` calls | `Not logged in. Run alpha login first.` | No login change; working metadata/documentation routes substituted |
| One quoted-field arXiv query | HTTP 400 | Reformulated query succeeded; no unchanged retry loop |
| Context7 initial discovery | No tools until the configured server was connected | Connection exposed official library resolution/query tools |
| OpenAlex | Anonymous retrieval worked; missing API-key warning and more rows than requested | Relevant exact record selected; unrelated excess results rejected |
| Optional local Pillow import by scout | `ModuleNotFoundError` | No install; image reader and `file` supplied the needed inspection/dimensions |
| https://pubmed.ncbi.nlm.nih.gov/39780007/ | HTML 403 | Web check blocked; exact PubMed API metadata succeeded |
| Official submission URL below | Access Denied | Upload interface, limits, account access and acceptance window remain unverified |
| Repeat `read` of supplied Downloads JPEG | EPERM | Repeat visual check blocked; prior inspection retained with explicit provenance |
| PDFs / `alpha_get_paper` | Intentionally excluded | No PDF download, parsing or full-paper audit claimed |

Official submission URL, mandated by local instructions:

https://www.hackerrank.com/contests/hackerrank-orchestrate-september26/challenges/buy-or-wait/submission

Access Denied is not evidence that the contest is closed. No login, upload or access-control bypass was attempted. The documented deadline is **13 September 2026, 18:00 IST**, not an independently verified portal acceptance window.

## Confirmed corrections and checks

Detailed evidence: `outputs/.drafts/buy-wait-agent-verification.md`.

- Local report corrections were verified before copying: approval was recorded as `yes`; the sample has 15 total columns; learned preferences cannot replace the challenge ranking; reductions respect supplied floors and action eligibility.
- R1–R9 in the revised synthesis passed exact text checks: full-payment-with-changes status; deadline for waiting; spending-action flexibility types/floors; inferred/explicit recurrence de-duplication; definition of `r`; cached-model usage provenance; precise upload/image limitations; exact output header/grammar; and preservation of installment amounts despite potential rounding discrepancies.
- The obsolete no-change-only full-payment wording and overly broad no-upload statement are absent from the revised candidate.
- All **14** footnote definitions are unique; every reference resolves; no draft citation placeholders remain.
- All cited local authority paths exist. The final report contains nine distinct primary external URLs, all represented in the actual HTML-check ledger, including the explicitly blocked portal URL.
- Final report is byte-identical to the revised candidate, not the earlier cited draft.
- Final/revised SHA-256: `ff535cefdce574bfbd653058144219851d4b14b760963abc22f032b111280322`.
- A scoped Git diff check confirmed participant docs, code and dataset unchanged. Current `code/main.py` is still zero bytes; root `output.csv` is still absent.

These are document/artifact checks. They do not establish that a future implementation handles all images, recurrence, accounting or output cases correctly.

## Artifact manifest

All paths are project-root relative.

| Artifact | Role |
|---|---|
| `outputs/.plans/buy-wait-agent.md` | Approved plan and completed task/verification ledger |
| `outputs/.drafts/buy-wait-agent-research-direct.md` | Exact queries, source screening, endpoints, direct HTML results and final limitations |
| `outputs/.drafts/buy-wait-agent-research-local.md` | Complete copied local-primary-evidence report |
| `outputs/.drafts/buy-wait-agent-draft.md` | Initial synthesis, retained as an intermediate artifact |
| `outputs/.drafts/buy-wait-agent-cited.md` | Cited pre-review draft, retained with its superseded wording |
| `outputs/.drafts/buy-wait-agent-verification.md` | Lead-owned review findings, fixes and verification limits |
| `outputs/.drafts/buy-wait-agent-revised.md` | Complete post-review candidate; authoritative for publication |
| `outputs/buy-wait-agent.md` | Final report copied from the revised candidate |
| `outputs/buy-wait-agent.provenance.md` | This provenance sidecar |

No `code.zip`, completed prediction CSV, application usage report or submission was generated. Creating them requires a separate implementation/packaging instruction. The research has no unresolved choice that prevents its delivery; choosing whether to implement a proposal is separate, not assumed approval.
