"""Pure source selection over integration-loaded records. No CSV I/O."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from types import MappingProxyType
from typing import Mapping, Sequence

from .schema import EvidenceError, digest


@dataclass(frozen=True)
class Source:
    """Private host provenance. Text is never included in repr/usage records."""
    source_id: str
    kind: str
    user_id: str
    request_id: str | None
    related_event_id: str | None
    source_type: str | None
    known_at: datetime | None
    relative_path: str
    row_number: int
    row_sha256: str
    reasons: tuple[str, ...]
    text: str | None = field(default=None, repr=False)
    csv_sha256: str | None = None


@dataclass(frozen=True)
class Selection:
    sources: tuple[Source, ...]
    excluded_future_ids: tuple[str, ...]


def _nullable(value):
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise EvidenceError("invalid_link")
    return value


def _instant(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError()
        return result.astimezone(timezone.utc)
    except (TypeError, ValueError, AttributeError):
        raise EvidenceError("invalid_source_time") from None


def _natural(source):
    import re
    return (source.kind, tuple((1, int(s)) if s.isdigit() else (0, s)
                              for s in re.split(r"(\d+)", source.source_id)))


class EvidenceIndex:
    """Read-only selection view; integration owns loading and general indexes.

    No record needs both optional IDs. event/request maps are provided by the
    integration layer. Constructor snapshots required source fields only, never
    serializes request outputs or arbitrary additional profile fields.
    """

    @classmethod
    def from_context(cls, context: Mapping):
        """Consume Dataset.context_for output without loading or parsing CSVs."""
        request = context["request"]
        return cls(messages=context["message_candidates"], images=context["image_candidates"],
                   events={row["event_id"]: row for row in context["events"]},
                   requests={request["request_id"]: {"user_id": request["user_id"]}},
                   source_locations=context["source_locations"])

    def __init__(self, *, messages: Sequence[Mapping], images: Sequence[Mapping],
                 events: Mapping[str, Mapping], requests: Mapping[str, Mapping],
                 source_locations: Mapping[str, Mapping] | None = None):
        self.events = MappingProxyType({k: MappingProxyType({"user_id": v.get("user_id"), "linked_event_id": v.get("linked_event_id")}) for k, v in events.items()})
        self.requests = MappingProxyType({k: MappingProxyType({"user_id": v.get("user_id")}) for k, v in requests.items()})
        sources = []
        seen = set()
        for kind, rows, id_key in (("message", messages, "message_id"), ("image", images, "image_id")):
            for number, row in enumerate(rows, 1):
                try:
                    raw_id, user = row[id_key], row["user_id"]
                    if not isinstance(raw_id, str) or not raw_id or not isinstance(user, str) or not user:
                        raise EvidenceError("invalid_source_id")
                    source_id = kind + ":" + raw_id
                    if source_id in seen:
                        raise EvidenceError("duplicate_source_id")
                    seen.add(source_id)
                    request_id, event_id = _nullable(row.get("request_id")), _nullable(row.get("related_event_id"))
                    # The production request file intentionally omits public
                    # samples and may not contain another request's metadata ID.
                    # Unknown IDs remain unmatched selectors, not a reason to
                    # read labels or manufacture a second request catalogue.
                    if request_id in requests and requests[request_id]["user_id"] != user:
                        raise EvidenceError("cross_user_request")
                    if event_id and (event_id not in events or events[event_id]["user_id"] != user):
                        raise EvidenceError("cross_user_event")
                    text = row["message_text"] if kind == "message" else None
                    if text is not None and (not isinstance(text, str) or len(text.encode()) > 32768):
                        raise EvidenceError("invalid_message")
                    source_type = _nullable(row.get("source_type")) if kind == "message" else None
                    known = _instant(row["sent_at"]) if kind == "message" else None
                    trusted_row = {id_key: raw_id, "user_id": user, "request_id": request_id, "related_event_id": event_id}
                    if kind == "message":
                        trusted_row.update(message_text=text, source_type=source_type, sent_at=known.isoformat())
                    csv_hash = None
                    if source_locations is not None:
                        import re
                        location = source_locations.get(source_id)
                        if location is None or set(location) != {"relative_path", "row_number", "csv_sha256"}:
                            raise EvidenceError("missing_source_location")
                        if (location["relative_path"] != f"{kind}s.csv" or type(location["row_number"]) is not int
                                or location["row_number"] < 1 or not isinstance(location["csv_sha256"], str)
                                or not re.fullmatch(r"[0-9a-f]{64}", location["csv_sha256"])):
                            raise EvidenceError("invalid_source_location")
                        number, csv_hash = location["row_number"], location["csv_sha256"]
                    sources.append(Source(source_id, kind, user, request_id, event_id, source_type,
                                          known, f"{kind}s.csv", number, digest(trusted_row), (), text, csv_hash))
                except KeyError:
                    raise EvidenceError("missing_source_field") from None
        self.sources = tuple(sorted(sources, key=_natural))

    def retrieve(self, *, user_id: str, request_id: str | None = None,
                 event_ids: Sequence[str] = (), as_of: date | None = None) -> Selection:
        from dataclasses import replace
        if not isinstance(user_id, str) or not user_id:
            raise EvidenceError("missing_user_id")
        if request_id and (request_id not in self.requests or self.requests[request_id]["user_id"] != user_id):
            raise EvidenceError("cross_user_request")
        explicit = set(event_ids)
        expanded = set()
        visiting = set()

        def visit(key):
            if key in visiting:
                raise EvidenceError("lifecycle_cycle")
            if key in expanded:
                return
            event = self.events.get(key)
            if event is None or event.get("user_id") != user_id:
                raise EvidenceError("cross_user_event")
            visiting.add(key)
            parent = _nullable(event.get("linked_event_id"))
            if parent:
                visit(parent)
            visiting.remove(key)
            expanded.add(key)

        for key in sorted(explicit):
            visit(key)
        result, future = [], []
        user_only_query = request_id is None and not explicit
        for source in self.sources:
            if source.user_id != user_id:
                continue
            reasons = []
            if user_only_query or (source.request_id is None and source.related_event_id is None):
                reasons.append("user")
            if request_id is not None and source.request_id == request_id:
                reasons.append("request")
            if source.related_event_id in expanded:
                reasons.append("event" if source.related_event_id in explicit else "lifecycle")
            if not reasons:
                continue
            if as_of is not None and source.known_at is not None and source.known_at.date() > as_of:
                future.append(source.source_id)
                continue
            result.append(replace(source, reasons=tuple(reasons)))
        return Selection(tuple(result), tuple(future))
