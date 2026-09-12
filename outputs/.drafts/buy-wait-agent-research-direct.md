# Research notes — Buy or Wait agent

Date: 2026-09-12. Research began only after the exact user approval `yes`.

## Execution and evidence boundaries

The lead performed the general methodological research recorded here. One supervised project scout completed the participant-facing local inspection, supplied-image inspection, and implementation-feasibility report; the verified copy is `outputs/.drafts/buy-wait-agent-research-local.md`. The lead owns the final synthesis, citation pass, and critical-check pass. No dedicated `subagent`/verifier/reviewer tool is available. No prediction model has been trained, no challenge predictions generated, no local financial dataset uploaded, and no PDF fetched or parsed.

`web_search`, `fetch_content`, and `memory_remember` are unavailable. Alpha search failed authentication; built-in arXiv/OpenAlex/PubMed retrieval and connected Context7 documentation retrieval provide working alternatives. Browser inspection uses the installed `chrome-devtools-axi` through the visible shell tool, with a separate `bw-research` browser session, to verify actual HTML pages rather than invent a fetch tool.

## Exact search/query ledger

### Round 1: definitions, mechanisms, and baseline comparisons

1. `alpha_search` (semantic): `How does supervised logistic regression estimate class probabilities, and what labels and evaluation data are required to fit and calibrate it? I need evidence distinguishing a binary preference or affordability classifier from a system that guarantees time-dependent cash-flow and payment-schedule constraints.`
2. `alpha_search` (semantic): `Compare simple linear and logistic models, tree ensembles, and deep learning on small tabular datasets with limited labeled observations. Prioritize methodological evidence about label scarcity, reliable validation, and when performance comparisons cannot establish a winner for a new dataset.`
3. `alpha_search` (semantic): `How do predictive models differ from prescriptive optimization or decision-focused learning when actions must satisfy hard constraints? Find foundational work on predict-then-optimize and model-based planning relevant to selecting feasible financial payment schedules, not trading returns.`

All three were launched together before any failure was returned. Each returned `Not logged in. Run alpha login first.` No login or retry was attempted. The bare global alpha executable was not used.

4. Built-in arXiv: `arxiv_search:logistic regression probability calibration supervised learning`. Retrieved metadata/abstracts including Fonseca & Lopes, arXiv:1710.08901v1. Other citation-network, imaging, and omics results are out of scope. The credit-default study is adjacent evidence, not evidence that household affordability labels exist or that its reported performance transfers here.
5. Built-in arXiv: `arxiv_get_papers:2207.08815,2106.11959,1710.08005,1706.04599`. All four IDs were found; abstracts and identifiers read, no full text.
6. Built-in arXiv: `arxiv_search:predict then optimize decision focused learning constraints`. Relevant result arXiv:2307.05213v3 discusses decision-focused learning and uncertainty in constraints; DOI 10.1613/jair.1.19498, updated 2026-05-31. Other RL, inventory-routing, learning-curve, and RNA results were screened but not used to claim a financial-method winner.
7. Context7 library resolution: library `scikit-learn`; query `Official LogisticRegression and LinearRegression documentation: supervised targets, regularization, predict_proba, probability calibration, small-data validation and GroupKFold/TimeSeriesSplit.` Selected `/websites/scikit-learn_stable`, the official stable website. Initial tool search found no tools; connecting the configured Context7 server successfully exposed its two tools.
8. Context7 documentation query: `Logistic regression is a classifier despite its name: sigmoid probability P(y=1|X)=1/(1+exp(-Xw)), fit X y target labels, predict_proba, regularization; LinearRegression ordinary least squares squared residual objective for continuous target. Need authoritative source URLs and factual excerpts, not a generated tutorial.`
9. Context7 documentation query: `Probability calibration user guide: logistic regression can be calibrated when correctly specified but probabilities need validation; CalibratedClassifierCV independent training and calibration data; proper scoring rules log loss and Brier score. Cross validation for repeated user groups and temporal data GroupKFold and TimeSeriesSplit; prevent preprocessing leakage with Pipeline. Return source URLs and supporting excerpts.`

### Round 2: disconfirming evidence and recent comparisons

10. Built-in arXiv: `arxiv_search:ti:"TabPFN" AND all:"small" max_results=5`. Retrieved the original TabPFN paper (2207.01848v6), a 2026 context-sampling preprint (2607.26628v1), Real-TabPFN (2507.03971v1), nanoTabPFN (2511.03634v2), and FT-TabPFN (2406.06891v1). These prevent the unsupported generalization that deep learning is always inferior on small tables. No speed/accuracy numbers are transferred to this challenge.
11. Crossref query: `10.1038/s41586-024-08328-6`. This generic query returned the **wrong** record, CRAN package DOI `10.32614/cran.package.tabpfn`. Rejected as a DOI verification. Do not cite it as the Nature article.
12. Built-in arXiv: `arxiv_search:ti:"tabular" AND all:"benchmark" date_from=2025-01-01 date_to=2026-09-12 max_results=5`. Failed with `400 Bad Request`; not repeated unchanged.
13. OpenAlex: `openalex_search_works:Accurate predictions on small data with a tabular foundation model year_from=2025`. Exact matching Nature article found as W4406170795, DOI 10.1038/s41586-024-08328-6, PMID 39780007. The service returned 50 records despite the requested tool limit of 3; unrelated works were not accepted as evidence. Anonymous access worked although the tool warned OPENALEX_API_KEY was missing.
14. Built-in arXiv, reformulated without quoted field syntax: `arxiv_search:tabular foundation model benchmark date_from=2025-01-01 date_to=2026-09-12`. Succeeded. Screened relevant abstracts 2512.03307v1 (Robust Tabular Foundation Models), 2507.07829v1 (tables with text), and 2608.17957v1 (in-context generalization). They still describe prediction from labeled context, not automatic enforcement of a household payment contract. EEG results are unrelated; optimizer benchmarks are beyond the recommended MVP scope.
15. PubMed exact metadata lookup: `pmid:39780007`. Confirmed title, Nature 637(8045):319–326 (2025), DOI 10.1038/s41586-024-08328-6, PMCID PMC11711098, authors and abstract. The paper reports strong small-tabular benchmark performance; it is not an experiment on the Buy or Wait dataset.

## Accepted methodological evidence and limits

### S1 — scikit-learn linear-model documentation

- URL: https://scikit-learn.org/stable/modules/linear_model.html#logistic-regression
- Retrieved through Context7 and **directly opened as HTML** through browser inspection. Browser title: `1.1. Linear Models — scikit-learn 1.9.1 documentation`.
- Browser DOM inspection read the logistic-regression and ordinary-least-squares sections.
- Supported facts: linear regression fits numeric targets by minimizing squared residuals; logistic regression models class probabilities with a logistic function, expects categorical targets, supports binary/multinomial forms, and offers regularization. The binary probability is `sigmoid(w0 + x·w)`; regularized fitting minimizes a weighted log-loss plus a penalty.
- Inference, not a published benchmark: a probability score is not itself a dated cash-flow feasibility proof or a compliant payment schedule. A learned model can be wrapped in explicit constraints, but then the planner/validator remains necessary.
- Additional returned source: https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.LinearRegression.html (Context7 excerpt read; separate HTML opening not yet performed).

### S2 — calibration documentation

- URLs returned: https://scikit-learn.org/stable/modules/generated/sklearn.calibration.CalibratedClassifierCV.html and https://scikit-learn.org/stable/auto_examples/calibration/plot_compare_calibration.html
- Context7 excerpts read. They require disjoint fitting/calibration data for already fitted estimators and warn that small datasets or misspecification can impair logistic calibration. Brier score and log loss are useful probability-quality measures, not safety guarantees.
- Do not recommend elaborate calibration on a handful of public examples or invent a sufficient minimum label count.

### S3 — Smart “Predict, then Optimize”

- Adam N. Elmachtoub and Paul Grigas; arXiv:1710.08005v5; first published 2017-10-22, updated 2020-11-19.
- URL: https://arxiv.org/abs/1710.08005v5
- Abstract retrieved from official arXiv API. It separates prediction loss from downstream decision loss and incorporates an optimization objective and constraints in training.
- Accepted for the conceptual distinction only. The paper's shortest-path/portfolio results do not prove superior household-budget outcomes. SPO training is not required for an MVP with explicit rules and limited decision labels.

### S4 — historical tabular comparison

- Léo Grinsztajn, Edouard Oyallon, Gaël Varoquaux; arXiv:2207.08815v1, 2022-07-18.
- URL: https://arxiv.org/abs/2207.08815v1
- Abstract describes a multi-dataset, tuned comparison in which trees performed strongly on medium-sized tabular tasks. Dataset conditions differ from this challenge.
- Contrasting source: Yury Gorishniy et al., arXiv:2106.11959v5, https://arxiv.org/abs/2106.11959v5; abstract explicitly concludes no universally superior solution across its comparison.
- Do not use these older studies to declare trees universally best in 2026.

### S5 — modern counterexample: TabPFN

- Noah Hollmann et al.; `Accurate predictions on small data with a tabular foundation model`, Nature 637:319–326, 2025-01-08.
- DOI: https://doi.org/10.1038/s41586-024-08328-6
- PubMed: https://pubmed.ncbi.nlm.nih.gov/39780007/
- PMCID: PMC11711098; OpenAlex W4406170795; venue S137773608.
- OpenAlex metadata reported OA hybrid, abstract license CC-BY, 888 citations and 43 references at retrieval. Those moving counts are recorded for provenance only, not used to rank suitability. Author identity examples: Noah Hollmann A5081550394 / ORCID 0000-0001-8556-518X; Frank Hutter A5031002895 / ORCID 0000-0002-2037-3694.
- Original TabPFN mechanism source: arXiv:2207.01848v6, https://arxiv.org/abs/2207.01848v6. Its abstract explicitly describes labeled `(x, f(x))` context examples and no downstream parameter updates.
- Recent context evidence: arXiv:2608.17957v1, published 2026-08-18; https://arxiv.org/abs/2608.17957v1. Abstract describes labeled examples at inference. Accepted only for that limited framing, not for unreplicated numerical claims.
- Practical inference: small-data foundation models deserve consideration when a legitimate supervised target exists; they do not remove the need for labels, a grounded ledger, or hard plan validation.

## Endpoint provenance

- arXiv metadata batch: https://export.arxiv.org/api/query?id_list=2207.08815%2C2106.11959%2C1710.08005%2C1706.04599&max_results=4
- arXiv search API: https://export.arxiv.org/api/query ; exact search strings above identify each request; default tool output returned five records for several smaller requested limits.
- OpenAlex endpoint: https://api.openalex.org/works?search=Accurate+predictions+on+small+data+with+a+tabular+foundation+model&filter=publication_year%3A%3E2024&sort=relevance_score%3Adesc&per-page=50
- PubMed endpoint: https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id=39780007&retmode=xml&tool=feynman
- Context7: configured `context7` MCP server, tools `context7_resolve-library-id` and `context7_query-docs`, library `/websites/scikit-learn_stable`; primary URLs preserved above.

## Round 3: direct HTML verification and disconfirming checks

Direct browser inspection used `CHROME_DEVTOOLS_AXI_SESSION=bw-research chrome-devtools-axi open <url>` followed by a read-only DOM query returning URL, document title, and relevant paragraphs/abstract. These are actual navigations, not a claim that a URL is reachable because it appeared in search metadata.

| URL | Actual result | Claim boundary |
|---|---|---|
| https://scikit-learn.org/stable/modules/linear_model.html#logistic-regression | HTML loaded; logistic and ordinary-least-squares sections read | Sigmoid formula, supervised targets and loss verified |
| https://scikit-learn.org/stable/modules/calibration.html | HTML loaded; relevant paragraphs read | Independent calibration data; correct model specification; Brier/log-loss caveat verified |
| https://scikit-learn.org/stable/modules/cross_validation.html | HTML loaded; evaluation and GroupKFold paragraphs read | Same-data evaluation is invalid; group separation guards against user-specific overfitting |
| https://scikit-learn.org/stable/common_pitfalls.html | HTML loaded; leakage/Pipeline paragraphs read | Prediction-time availability and fitting transformations on training data only |
| https://arxiv.org/abs/1710.08005v5 | HTML loaded; title and abstract read | Prediction-vs-decision objective distinction; not a full-paper audit |
| https://arxiv.org/abs/2207.08815v1 | HTML loaded; title and abstract read | Historical benchmark is context-specific, not universal superiority |
| https://arxiv.org/abs/2207.01848v6 | HTML loaded; title and abstract read | TabPFN requires labeled context for its supervised classification use |
| https://doi.org/10.1038/s41586-024-08328-6 | Redirected successfully to https://www.nature.com/articles/s41586-024-08328-6 ; publisher abstract read | Modern counterexample to blanket anti-deep-learning claims |
| https://pubmed.ncbi.nlm.nih.gov/39780007/ | Browser title `403`; no article body | Web-page reachability BLOCKED; exact PubMed API metadata lookup succeeded |
| https://www.hackerrank.com/contests/hackerrank-orchestrate-september26/challenges/buy-or-wait/submission | Browser title and heading `Access Denied` | Portal rules/upload flow could not be verified; URL still mandated by local instructions |

**Important calibration correction before drafting:** the official calibration guide explicitly says Brier score and log loss combine calibration, discrimination, and inherent uncertainty. A lower Brier score alone does not prove better calibration. Recommend them as probability-quality measures alongside reliability checks, not as isolated calibration guarantees.

**Additional accepted sources:** https://scikit-learn.org/stable/modules/cross_validation.html and https://scikit-learn.org/stable/common_pitfalls.html . Official pages were directly read. Only the requested relevant passages are used, not arbitrary API defaults in retrieved snippets.

## Final evidence / verification outcome

- The complete scout report was read and copied to `outputs/.drafts/buy-wait-agent-research-local.md`; a byte comparison against the durable original passed. The earlier ENOENT occurred while that report was being written and is not an unresolved missing deliverable.
- The local report's stale approval sentence, sample-column count, scope of learned ranking, and reduction-floor rules were corrected by its author and checked on disk before copying.
- The lead wrote the draft, cited draft, and complete revised candidate. A sequential claim-to-source and critical-check pass found nine clarifications, now present in the revised candidate; final `outputs/buy-wait-agent.md` is byte-identical to that candidate. Details are in `outputs/.drafts/buy-wait-agent-verification.md`.
- During the final review, the lead re-read the full problem statement, README, all 25 public sample rows, and the complete local report. This corroborated the full-payment-with-changes cases without deriving evaluation outputs.
- A later image-tool call, `read('/Users/and3/Downloads/WhatsApp Image 2026-09-12 at 07.59.23.jpeg')`, returned `EPERM: operation not permitted, open`. Earlier successful inspection is recorded in the scout report and prior session tool history. Repeat visual verification is BLOCKED; no alternate access route or permission change was attempted.
- Independent verifier/reviewer tools remain unavailable. The citation and critical-check passes were lead-owned, not independent multi-agent reviews.
- Portal-specific verification remains BLOCKED; no login, upload, or access-control bypass was attempted. This is not evidence that the contest is closed.
- Final and revised report SHA-256 at the publication check: `ff535cefdce574bfbd653058144219851d4b14b760963abc22f032b111280322`.
