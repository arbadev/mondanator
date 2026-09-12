"""One direct OpenRouter client. Network is disabled unless explicitly enabled.

Constructing configuration never reads .env or the process environment. The
runner must supply authorized configuration, a key and a justified attempt bound.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Mapping

import httpx

from .schema import EvidenceError, response_format, strict_json

URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "openai/gpt-4.1"
CANONICAL_MODEL = "openai/gpt-4.1-2025-04-14"


@dataclass(frozen=True)
class ExtractorConfig:
    model: str = MODEL
    canonical_model: str = CANONICAL_MODEL
    max_completion_tokens: int = 4096
    max_attempts: int = 3
    prompt_version: str = "extract-v1"
    build_version: str = "evidence-v1"
    preprocessing_version: str = "original-bytes-v1"

    def __post_init__(self):
        if self.model != MODEL or self.canonical_model != CANONICAL_MODEL:
            raise EvidenceError("unsupported_model_configuration")
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 3:
            raise EvidenceError("invalid_attempt_limit")
        if type(self.max_completion_tokens) is not int or not 1 <= self.max_completion_tokens <= 8192:
            raise EvidenceError("invalid_output_limit")

    def provider(self):
        return {"only": ["azure"], "allow_fallbacks": False, "require_parameters": True,
                "data_collection": "deny", "zdr": True, "max_price": {"prompt": 2, "completion": 8}}


def load_api_key(environ: Mapping[str, str]) -> str:
    """Only the two named process-environment assignments; caller supplies map.

    This function is not invoked during offline extraction. It never opens a
    dotenv file, shells out, enumerates environment entries or prints a value.
    """
    preferred = environ.get("OPEN_ROUTER_API_KEY", "")
    alias = environ.get("OPENROUTER_API_KEY", "")
    if any(not isinstance(v, str) or (v and (v != v.strip() or "\n" in v or "\r" in v)) for v in (preferred, alias)):
        raise EvidenceError("invalid_credential_format")
    if preferred and alias and preferred != alias:
        raise EvidenceError("conflicting_credentials")
    if not preferred and not alias:
        raise EvidenceError("missing_credential")
    return preferred or alias


def build_payload(*, config: ExtractorConfig, system_prompt: str, context: dict,
                  text: str | None, image: bytes | None) -> dict:
    from .schema import canonical
    if (text is None) == (image is None):
        raise EvidenceError("one_document_required")
    allowed_context = {"source_id", "user_id", "request_id", "related_event_id", "source_type", "sent_at", "candidate_events", "as_of"}
    allowed_event = {"event_id", "user_id", "description", "category", "direction", "currency", "status", "event_date", "settlement_date", "linked_event_id"}
    if set(context) != allowed_context or not isinstance(context["candidate_events"], list):
        raise EvidenceError("invalid_host_context")
    if any(not isinstance(event, dict) or not set(event).issubset(allowed_event) for event in context["candidate_events"]):
        raise EvidenceError("invalid_host_context")
    content = [{"type": "text", "text": canonical({"host_context": context, "untrusted_document_text": text}).decode()}]
    if image is not None:
        content.append({"type": "image_url", "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(image).decode("ascii"), "detail": "high"}})
    return {"model": config.model, "stream": False, "temperature": 0,
            "max_completion_tokens": config.max_completion_tokens, "provider": config.provider(),
            "response_format": response_format(), "messages": [
                {"role": "system", "content": system_prompt}, {"role": "user", "content": content}]}


@dataclass(frozen=True)
class Response:
    status: int
    body: dict = field(repr=False)
    retry_after: float | None = None


def retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        import math
        delay = float(value)
        if math.isfinite(delay):
            return max(0.0, delay)
        return None
    except ValueError:
        try:
            then = parsedate_to_datetime(value)
            if then.tzinfo is None:
                return None
            return max(0.0, (then - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return None


class OpenRouterClient:
    def __init__(self, *, api_key: str = "", live_enabled: bool = False,
                 transport: httpx.BaseTransport | None = None):
        self._key = api_key
        self.live_enabled = live_enabled
        self._transport = transport

    def send(self, payload: dict) -> Response:
        if not self.live_enabled:
            raise EvidenceError("live_inference_disabled")
        if not self._key:
            raise EvidenceError("missing_credential")
        if (payload.get("model") != MODEL or payload.get("provider") != ExtractorConfig().provider()
                or payload.get("stream") is not False or "tools" in payload or "plugins" in payload
                or payload.get("response_format") != response_format()):
            raise EvidenceError("unsupported_configuration")
        try:
            with httpx.Client(transport=self._transport, trust_env=False, follow_redirects=False,
                              timeout=httpx.Timeout(60, connect=10, write=30, pool=10)) as client:
                with client.stream("POST", URL, headers={"Authorization": "Bearer " + self._key}, json=payload) as response:
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > 1048576:
                            raise EvidenceError("oversize_response")
                        chunks.append(chunk)
                    try:
                        body = strict_json(b"".join(chunks), max_bytes=1048576)
                        if not isinstance(body, dict):
                            body = {}
                    except EvidenceError:
                        body = {}  # status survives; invalid 200 is rejected upstream
                    return Response(response.status_code, body, retry_after(response.headers.get("Retry-After")))
        except EvidenceError:
            raise
        except (httpx.HTTPError, ValueError):
            # Exception reprs can contain requests/headers. Only expose a code.
            raise EvidenceError("transport_error") from None
