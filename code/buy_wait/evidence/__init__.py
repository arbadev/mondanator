"""Private evidence extraction with network-off defaults.

Integration provides loaded source/event/request rows to EvidenceIndex, resolves
image assets locally, and calls Extractor.extract(..., mode="cache-only").
ExtractionResult contains only observations/issues; it is not a financial result.

Runtime dependencies: Python>=3.10, pydantic>=2.11,<3; httpx>=0.28,<0.29;
Pillow>=11,<13. Tested on Python 3.13. Live inference needs separately authorized configuration,
an explicit key, a RunBudget and a justified per-attempt cost upper bound; this
package does not open .env or perform inference on import.

Integration order (all CSV ingestion/target resolution remains outside here):
1. Prefer EvidenceIndex.from_context(dataset.context_for(input_row)); it consumes
   the integration-owned source_locations and never reopens CSVs. Direct callers
   can supply complete source tables in original record order, or provide the
   same source_locations mapping with global record ordinals and CSV byte hashes.
   ImageAsset and SourceRef paths are relative to the dataset directory; CSV file,
   canonical record and decoded text/image leaf hashes are distinct.
2. retrieve(user_id=..., request_id=..., event_ids=..., as_of=request_date).
3. Build event_descriptors(real same-user EventRecord map, user_id=...). Resolve
   image_N with resolve_image(dataset_dir, image_id); absent/invalid is an issue.
4. Extractor.extract(source, candidate_events=descriptors, asset=asset_or_none,
   as_of=request_date, mode="cache-only") returns observations or explicit gaps.
5. adapt_extraction(result, request_id=..., user_id=..., events=real_event_map,
   resolved_targets=host_bindings) returns the sole canonical FactBatch plus
   document/CSV SourceRefs. Include those refs in FinancialInput.sources before
   passing the batch to build_financial_context. Keep unavailable/issues visible.

Series/unlinked target bindings and verified actor identities are host/core
inputs, not inferred permissions. Cadence labels without supported anchors and
absolute deltas remain issues for core resolution. A new cash group needs a bound NewOccurrenceTarget, host
new_event_fields[local_id]={"event_type": ..., "category": ...}, and explicit
amount/settlement/cash-state observations; partial evidence never invents cash.

Supply an integration-owned durable UsageSink for real runs. MemoryUsageSink is
for tests; aggregate canonical attempt IDs once, with cache-origin references,
not a second call counter. max_seconds is a dispatch/retry deadline, not a hard
interrupt of in-flight synchronous I/O (the client has separate I/O timeouts).
No live model quality/price/account-access claim follows from offline tests.

Offline test command from repository root, after installing dependencies:
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=code python -m unittest discover -s code/tests -v
"""
from .cache import ExtractionCache
from .extractor import ExtractionResult, Extractor
from .images import ImageAsset, resolve_image
from .openrouter_client import ExtractorConfig, OpenRouterClient, load_api_key
from .retrieval import EvidenceIndex, Selection, Source
from .schema import EvidenceError, ModelExtraction
from .usage import MemoryUsageSink, RunBudget, UsageEvent, UsageSink
from .adapter import AdaptedEvidence, adapt_extraction, event_descriptors

__all__ = [
    "EvidenceError", "EvidenceIndex", "ExtractionCache", "ExtractionResult", "Extractor",
    "ExtractorConfig", "ImageAsset", "MemoryUsageSink", "ModelExtraction", "OpenRouterClient",
    "RunBudget", "Selection", "Source", "UsageEvent", "UsageSink", "load_api_key", "resolve_image",
    "AdaptedEvidence", "adapt_extraction", "event_descriptors",
]
