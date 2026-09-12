"""Bounded private extraction: facts and issues, never accepted money.

Default cache-only mode is a hard network boundary. Offline missing evidence is
an explicit result, not a zero amount and not a request to call the provider.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Mapping

from .cache import CACHE_VERSION, ExtractionCache
from .images import ImageAsset, validate_png
from .openrouter_client import ExtractorConfig, OpenRouterClient, Response, build_payload
from .retrieval import Source
from .schema import EvidenceError, ModelExtraction, canonical, digest, parse_extraction, validate_support
from .usage import RunBudget, UsageEvent, UsageSink, usage_fields

EVENT_KEYS = ("event_id", "user_id", "description", "category", "direction", "currency", "status", "event_date", "settlement_date", "linked_event_id")


@dataclass(frozen=True)
class ExtractionResult:
    source: Source
    outcome: str
    extraction: ModelExtraction | None
    issues: tuple[str, ...]
    cache_key: str
    attempt_ids: tuple[str, ...]
    origin_usage: tuple[UsageEvent, ...] = ()
    content_sha256: str | None = None


def make_context(source: Source, events: Mapping[str, Mapping], as_of: date | None) -> dict:
    candidates = []
    for key in sorted(events):
        row = events[key]
        if row.get("user_id") != source.user_id or row.get("event_id", key) != key:
            raise EvidenceError("cross_user_event")
        item = {field: row.get(field) for field in EVENT_KEYS}
        item["event_id"] = key
        # Free text is data; only JSON primitives of bounded size are accepted.
        if any(value is not None and (not isinstance(value, str) or len(value) > 1000) for value in item.values()):
            raise EvidenceError("invalid_event_descriptor")
        candidates.append(item)
    if len(candidates) > 256:
        raise EvidenceError("oversize_context")
    return dict(source_id=source.source_id, user_id=source.user_id,
                request_id=source.request_id, related_event_id=source.related_event_id,
                source_type=source.source_type, sent_at=source.known_at.isoformat() if source.known_at else None,
                candidate_events=candidates, as_of=as_of.isoformat() if as_of else None)


def _completion(response: Response) -> str:
    if response.status != 200:
        codes = {400: "unsupported_configuration", 401: "authentication", 402: "insufficient_credit",
                 403: "refusal", 404: "no_matching_provider", 408: "transport_error", 429: "rate_limit"}
        raise EvidenceError(codes.get(response.status, "provider_unavailable" if response.status >= 500 else "http_error"))
    if response.body.get("error") is not None:
        raise EvidenceError("provider_body_error")
    try:
        choices = response.body["choices"]
        if not isinstance(choices, list) or len(choices) != 1:
            raise EvidenceError("invalid_response")
        choice = choices[0]
        message = choice["message"]
        if message.get("refusal") or message.get("tool_calls"):
            raise EvidenceError("refusal")
        if choice.get("finish_reason") != "stop":
            raise EvidenceError("truncated" if choice.get("finish_reason") == "length" else "invalid_response")
        if not isinstance(message.get("content"), str):
            raise EvidenceError("invalid_response")
        return message["content"]
    except (KeyError, TypeError, AttributeError):
        raise EvidenceError("invalid_response") from None


class Extractor:
    def __init__(self, *, cache: ExtractionCache, usage: UsageSink, run_id: str,
                 config: ExtractorConfig | None = None, client: OpenRouterClient | None = None,
                 budget: RunBudget | None = None, sleep=time.sleep, monotonic=time.monotonic):
        self.cache, self.usage, self.run_id = cache, usage, run_id
        self.config, self.client, self.budget = config or ExtractorConfig(), client, budget
        self.sleep, self.monotonic = sleep, monotonic
        self._fatal: str | None = None
        self.prompt = (Path(__file__).parent / "prompts" / "extract_v1.txt").read_text(encoding="utf-8")

    def extract(self, source: Source, *, candidate_events: Mapping[str, Mapping],
                asset: ImageAsset | None = None, as_of: date | None = None,
                mode: str = "cache-only", cost_upper_bound: Decimal | None = None,
                max_seconds: float = 180) -> ExtractionResult:
        import math
        if mode not in {"cache-only", "live", "refresh"}:
            raise EvidenceError("invalid_extraction_mode")
        if isinstance(max_seconds, bool) or not isinstance(max_seconds, (int, float)) or not math.isfinite(max_seconds) or max_seconds <= 0:
            raise EvidenceError("invalid_time_budget")
        missing_key = digest({"source": source.row_sha256, "missing": True})
        if as_of is not None and source.known_at is not None and source.known_at.date() > as_of:
            return ExtractionResult(source, "unavailable", None, ("future_evidence",), missing_key, ())
        if source.kind == "image" and asset is None:
            return ExtractionResult(source, "unavailable", None, ("missing_image",), missing_key, ())
        if source.kind not in {"image", "message"} or (source.kind == "message" and source.text is None):
            raise EvidenceError("invalid_document")
        if source.kind == "image":
            if source.source_id != "image:" + asset.image_id:
                raise EvidenceError("image_id_mismatch")
            validate_png(asset.content)
            if hashlib.sha256(asset.content).hexdigest() != asset.sha256:
                raise EvidenceError("image_hash_mismatch")
        elif asset is not None:
            raise EvidenceError("unexpected_image")
        text = source.text if source.kind == "message" else None
        image = asset.content if asset else None
        content_hash = hashlib.sha256(image if image is not None else text.encode()).hexdigest()
        context = make_context(source, candidate_events, as_of)
        payload = build_payload(config=self.config, system_prompt=self.prompt, context=context, text=text, image=image)
        key = digest({"cache_version": CACHE_VERSION, "config": asdict(self.config),
                      "row_sha256": source.row_sha256, "content_sha256": content_hash,
                      "payload_sha256": digest(payload), "as_of_policy": "inclusive-UTC-date-v1"})
        with self.cache.locked(key):
            if mode != "refresh":
                try:
                    cached = self.cache.load(key)
                    if cached is not None:
                        if set(cached) != {"extraction", "origin_usage", "content_sha256"} or cached["content_sha256"] != content_hash:
                            raise EvidenceError("cache_invalid")
                        extraction = parse_extraction(canonical(cached["extraction"]))
                        validate_support(extraction, source_id=source.source_id, text=text, candidate_events=candidate_events)
                        originals = tuple(UsageEvent(**dict(x, cache_origin_attempt_ids=tuple(x["cache_origin_attempt_ids"]))) for x in cached["origin_usage"])
                        if not originals or any(x.source_id != source.source_id or x.cache_key != key for x in originals):
                            raise EvidenceError("cache_invalid")
                        hit = UsageEvent(self.run_id, str(uuid.uuid4()), source.source_id, key, self.config.model,
                                         originals[-1].returned_model, originals[-1].actual_provider, None, "cache_hit", None,
                                         0, 0, 0, "0", cache_origin_attempt_ids=tuple(x.attempt_id for x in originals))
                        self.usage.record(hit)
                        issues = tuple(i.code for i in extraction.issues)
                        if any(x.cost is None or x.prompt_tokens is None or x.completion_tokens is None or x.total_tokens is None for x in originals):
                            issues += ("usage_unknown",)
                        return ExtractionResult(source, "partial" if issues else "ok", extraction, issues, key, (), originals, content_hash)
                except (EvidenceError, TypeError, KeyError, ValueError):
                    return ExtractionResult(source, "invalid", None, ("cache_invalid",), key, (), content_sha256=content_hash)
            if mode == "cache-only":
                return ExtractionResult(source, "unavailable", None, ("cache_miss",), key, (), content_sha256=content_hash)
            if self._fatal:
                return ExtractionResult(source, "unavailable", None, (self._fatal,), key, (), content_sha256=content_hash)
            if self.client is None or not self.client.live_enabled or self.budget is None:
                return ExtractionResult(source, "unavailable", None, ("live_inference_disabled",), key, (), content_sha256=content_hash)
            return self._call(source, candidate_events, text, payload, key, content_hash, cost_upper_bound, max_seconds)

    def _call(self, source, candidates, text, payload, key, content_hash, cost_bound, max_seconds):
        started = self.monotonic()
        attempts = []
        correction_used = False
        final_error = "attempts_exhausted"
        transient = {"transport_error", "rate_limit", "provider_unavailable", "provider_body_error"}
        fatal = {"authentication", "insufficient_credit", "unsupported_configuration", "no_matching_provider"}
        for ordinal in range(self.config.max_attempts):
            if self.monotonic() - started >= max_seconds:
                final_error = "time_budget_exhausted"
                break
            attempt_id = str(uuid.uuid4())
            try:
                self.budget.reserve(attempt_id, cost_bound)
            except EvidenceError as exc:
                final_error = exc.code
                break
            response, extraction, error = None, None, None
            attempt_started = datetime.now(timezone.utc).isoformat()
            try:
                response = self.client.send(payload)
                extraction = parse_extraction(_completion(response))
                validate_support(extraction, source_id=source.source_id, text=text, candidate_events=candidates)
                model = response.body.get("model")
                provider = response.body.get("provider")
                if model not in {self.config.model, self.config.canonical_model} or provider not in {"Azure", "azure", "azure/swedencentral"}:
                    raise EvidenceError("provider_identity_unverified")
            except EvidenceError as exc:
                extraction, error = None, exc.code
            except Exception:
                # Preserve an attempted dispatch even for an unexpected transport
                # failure; never propagate its potentially secret-bearing repr.
                extraction, error = None, "internal_error"
            metadata = usage_fields(response.body if response is not None else {})
            event = UsageEvent(run_id=self.run_id, attempt_id=attempt_id, source_id=source.source_id,
                               cache_key=key, requested_model=self.config.model, outcome=error or "validated",
                               request_sha256=digest(payload), started_at=attempt_started,
                               finished_at=datetime.now(timezone.utc).isoformat(), http_status=response.status if response else None,
                               retry_of=attempts[-1].attempt_id if attempts else None, **metadata)
            self.budget.reconcile(attempt_id, event.cost)
            self.usage.record(event)
            attempts.append(event)
            if extraction is not None:
                self.cache.save(key, {"extraction": extraction.model_dump(mode="json"),
                                      "origin_usage": [x.to_dict() for x in attempts], "content_sha256": content_hash})
                issues = tuple(i.code for i in extraction.issues)
                if any(x.cost is None or x.prompt_tokens is None or x.completion_tokens is None or x.total_tokens is None for x in attempts):
                    issues += ("usage_unknown",)
                if self.budget.overrun:
                    issues += ("cost_bound_exceeded",)
                    self._fatal = "cost_bound_exceeded"
                return ExtractionResult(source, "partial" if issues else "ok", extraction, issues, key,
                                        tuple(x.attempt_id for x in attempts), tuple(attempts), content_hash)
            final_error = error
            if error in fatal:
                self._fatal = error
                break
            if error in {"refusal", "live_inference_disabled", "provider_identity_unverified", "truncated", "internal_error"}:
                break
            if error not in transient:
                if correction_used:
                    break
                correction_used = True
                # Fixed retry instruction, never the untrusted response/error body.
                payload = dict(payload, messages=[payload["messages"][0], *payload["messages"][1:], {
                    "role": "user", "content": "The previous response failed local evidence validation. Re-extract only supported fields; use null and issues instead of guessing."}])
            delay = response.retry_after if response is not None and response.retry_after is not None else min(2 ** ordinal, 8)
            if ordinal + 1 == self.config.max_attempts:
                break
            if self.monotonic() - started + delay >= max_seconds:
                final_error = "time_budget_exhausted"
                break
            self.sleep(delay)
        return ExtractionResult(source, "unavailable", None, (final_error,), key,
                                tuple(x.attempt_id for x in attempts), tuple(attempts), content_hash)
