"""CTI 검색 프로토콜 실행과 후보 문헌 정규화.

목적: 사전 등록한 검색식을 백엔드에 실행하고 whitelist·중복 제거 결과를 감사 가능한 레코드로 만든다.
지원 RQ: RQ1 systematic public corpus, RQ4 prospective validation corpus.
재사용 원천: CTI-Agent search_collector의 query expansion과 domain whitelist 원칙을 계승한다.
설계: 검색 백엔드는 주입하며, 게시일이 없을 때 수집일로 대체하지 않고 명시적으로 unknown을 보존한다.
입력·출력: SearchProtocolRecord·검색 백엔드 결과를 받아 SearchExecutionResult를 반환한다.
시간·provenance 통제: 실행시각, 원 검색문, canonical URL, 검색 결과 hash를 각각 기록한다.
보안·라이선스: 검색 API 자격증명과 원문은 이 계층에 저장하지 않으며 공개 URL metadata만 다룬다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import re
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.models import (
    AcquisitionMode, CorpusPurpose, SearchProtocolRecord, SourceAccessClass,
    TimePrecision, utc,
)
from src.provenance import canonical_json_hash, sha256_text


class SearchBackend(Protocol):
    """검색 제공자별 network 구현이 만족해야 하는 최소 계약."""

    name: str

    def search(self, query: str) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class PublicationMetadata:
    raw: str | None
    exact_datetime: datetime | None
    precision: TimePrecision
    source_timezone: str


def infer_publication_metadata(
    value: Any, source_timezone: str | None = None
) -> PublicationMetadata:
    """게시일 문자열의 정밀도를 분류하되 비정밀 값을 timestamp로 승격하지 않는다."""

    if value is None or not str(value).strip():
        return PublicationMetadata(None, None, TimePrecision.UNKNOWN, "unknown")
    raw = str(value).strip()
    timezone_label = str(source_timezone or "unknown").strip() or "unknown"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return PublicationMetadata(raw, None, TimePrecision.DATE, timezone_label)
    if re.fullmatch(r"\d{4}-\d{2}", raw):
        return PublicationMetadata(raw, None, TimePrecision.MONTH, timezone_label)
    if re.fullmatch(r"\d{4}", raw):
        return PublicationMetadata(raw, None, TimePrecision.YEAR, timezone_label)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return PublicationMetadata(raw, None, TimePrecision.UNKNOWN, timezone_label)
    if parsed.tzinfo is None:
        return PublicationMetadata(raw, None, TimePrecision.UNKNOWN, timezone_label)
    exact = utc(parsed)
    if timezone_label == "unknown":
        timezone_label = "UTC" if raw.endswith("Z") else parsed.strftime("%z") or "unknown"
    return PublicationMetadata(
        raw, exact, TimePrecision.EXACT_TIMESTAMP, timezone_label
    )


@dataclass(frozen=True)
class SearchCandidate:
    candidate_id: str
    query_text: str
    title: str
    url: str
    publisher: str
    published_at: str | None
    published_at_basis: str
    published_time_precision: str
    source_timezone: str
    rank: int
    discovered_at: str
    search_protocol_id: str
    source_access_class: str
    acquisition_mode: str
    corpus_purpose: str


@dataclass(frozen=True)
class SearchExecutionResult:
    search_run_id: str
    backend: str
    executed_at: str
    candidates: tuple[SearchCandidate, ...]
    discarded_outside_whitelist: int
    discarded_invalid_url: int
    duplicate_count: int
    result_manifest_hash: str


def canonicalize_url(url: str) -> str:
    """HTTP(S) URL을 fragment 제거·host 소문자화·query 정렬 형태로 정규화한다."""

    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ValueError("candidate URL must be absolute HTTP(S)")
    host = parts.hostname.lower()
    if parts.port:
        host = f"{host}:{parts.port}"
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return urlunsplit((parts.scheme.lower(), host, parts.path or "/", query, ""))


def host_allowed(url: str, whitelist: list[str]) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == domain.lower() or host.endswith("." + domain.lower()) for domain in whitelist)


def execute_search_protocol(
    protocol: SearchProtocolRecord,
    expanded_queries: list[str],
    backend: SearchBackend,
    domain_whitelist: list[str],
    *,
    executed_at: datetime | None = None,
) -> SearchExecutionResult:
    """프로토콜에 속한 검색문을 실행하고 결정적 후보 목록과 manifest hash를 만든다."""

    executed = (executed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    seen: set[str] = set()
    candidates: list[SearchCandidate] = []
    outside = invalid = duplicates = 0
    for query in expanded_queries:
        for rank, raw in enumerate(backend.search(query), start=1):
            try:
                url = canonicalize_url(str(raw.get("url", "")))
            except ValueError:
                invalid += 1
                continue
            if not host_allowed(url, domain_whitelist):
                outside += 1
                continue
            if url in seen:
                duplicates += 1
                continue
            seen.add(url)
            published = raw.get("published_at") or raw.get("published_date")
            publication = infer_publication_metadata(
                published, raw.get("source_timezone")
            )
            publisher = (urlsplit(url).hostname or "unknown").lower()
            candidate_id = f"cti-candidate-{sha256_text(url)[:16]}"
            candidates.append(SearchCandidate(
                candidate_id=candidate_id,
                query_text=query,
                title=str(raw.get("title") or url),
                url=url,
                publisher=publisher,
                published_at=publication.raw,
                published_at_basis="search_provider" if published else "unknown",
                published_time_precision=publication.precision.value,
                source_timezone=publication.source_timezone,
                rank=rank,
                discovered_at=executed.isoformat(),
                search_protocol_id=protocol.search_protocol_id,
                source_access_class=protocol.source_access_class.value,
                acquisition_mode=protocol.acquisition_mode.value,
                corpus_purpose=(
                    CorpusPurpose.PROSPECTIVE_VALIDATION.value
                    if protocol.acquisition_mode is AcquisitionMode.PROSPECTIVE_VALIDATION
                    else CorpusPurpose.DEVELOPMENT.value
                ),
            ))
    payload = [asdict(candidate) for candidate in candidates]
    manifest_hash = canonical_json_hash(payload)
    run_material = f"{protocol.search_protocol_id}|{backend.name}|{executed.isoformat()}|{manifest_hash}"
    return SearchExecutionResult(
        search_run_id=f"cti-search-run-{sha256_text(run_material)[:16]}",
        backend=backend.name,
        executed_at=executed.isoformat(),
        candidates=tuple(candidates),
        discarded_outside_whitelist=outside,
        discarded_invalid_url=invalid,
        duplicate_count=duplicates,
        result_manifest_hash=manifest_hash,
    )
