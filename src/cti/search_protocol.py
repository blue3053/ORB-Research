"""체계적 CTI 검색 프로토콜 생성기.

목적: 검색어·출처·기간·포함/제외 규칙을 실행 전에 hash로 고정한다.
지원 RQ: RQ1 systematic corpus, RQ4 prospective validation source monitoring.
재사용 원천: CTI-Agent search_collector의 query template·whitelist 설계를 확장했다.
설계: protocol ID는 canonical content hash로 결정하며 동일 설정은 동일 ID를 만든다.
입력·출력: 검색 규칙을 받아 SearchProtocolRecord와 확장된 검색문을 반환한다.
시간·provenance 통제: 등록시각과 대상 발행기간을 분리한다.
보안·라이선스: 검색 결과의 URL은 screening 전 연구 코퍼스에 포함되지 않는다.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.models import SearchProtocolRecord
from src.provenance import canonical_json_hash


def build_search_protocol(
    *,
    version: str,
    target_date_from: str,
    target_date_to: str,
    target_publishers: list[str],
    search_terms: list[str],
    inclusion_rules: list[str],
    exclusion_rules: list[str],
    deduplication_rule: str,
    registered_at: datetime | None = None,
) -> SearchProtocolRecord:
    """검색 프로토콜의 내용 hash와 deterministic ID를 생성한다."""

    payload = {
        "protocol_version": version,
        "target_date_from": target_date_from,
        "target_date_to": target_date_to,
        "target_publishers": target_publishers,
        "search_terms": search_terms,
        "inclusion_rules": inclusion_rules,
        "exclusion_rules": exclusion_rules,
        "deduplication_rule": deduplication_rule,
    }
    protocol_hash = canonical_json_hash(payload)
    return SearchProtocolRecord(
        search_protocol_id=f"cti-search-{protocol_hash[:16]}",
        registered_at=registered_at or datetime.now(timezone.utc),
        protocol_hash=protocol_hash,
        **payload,
    )


def expand_search_terms(
    search_terms: list[str], groups: list[str], covert_networks: list[str]
) -> list[str]:
    """CTI-Agent와 동일한 두 template을 deterministic하게 확장한다."""

    expanded: list[str] = []
    for term in search_terms:
        if "{watchlist_group}" in term:
            expanded.extend(term.replace("{watchlist_group}", group) for group in groups)
        elif "{covert_network}" in term:
            expanded.extend(term.replace("{covert_network}", network) for network in covert_networks)
        else:
            expanded.append(term)
    return list(dict.fromkeys(expanded))

