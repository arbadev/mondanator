"""Private evidence extraction with network-off defaults.

Integration provides loaded source/event/request rows to EvidenceIndex, resolves
image assets locally, and calls Extractor.extract(..., mode="cache-only").
ExtractionResult contains only observations/issues; it is not a financial result.

Runtime dependencies: pydantic>=2.11,<3; httpx>=0.28,<0.29; Pillow>=11,<13.
Tested on Python 3.13. Live inference needs separately authorized configuration,
an explicit key, a RunBudget and a justified per-attempt cost upper bound; this
package does not open .env or perform inference on import.
"""
from .cache import ExtractionCache
from .extractor import ExtractionResult, Extractor
from .images import ImageAsset, resolve_image
from .openrouter_client import ExtractorConfig, OpenRouterClient, load_api_key
from .retrieval import EvidenceIndex, Selection, Source
from .schema import EvidenceError, ModelExtraction
from .usage import MemoryUsageSink, RunBudget, UsageEvent, UsageSink

__all__ = [
    "EvidenceError", "EvidenceIndex", "ExtractionCache", "ExtractionResult", "Extractor",
    "ExtractorConfig", "ImageAsset", "MemoryUsageSink", "ModelExtraction", "OpenRouterClient",
    "RunBudget", "Selection", "Source", "UsageEvent", "UsageSink", "load_api_key", "resolve_image",
]
