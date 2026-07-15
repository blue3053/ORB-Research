"""CTI-Agent search_collector의 pure 검색 규칙 내부 파생본.

원본: D:/Claude/CTI-Agent/src/collectors/search_collector.py
원본 commit: 1d5e1b6b7bf294dc5b63fc3cde81f23fa4d176d9
원본 SHA-256: C960866D2B246195F878CFE9D5B8CA6A65825542EF1270297C568ADA73AF7F4A
목적·지원 RQ: 검색식 전개와 whitelist 필터를 RQ1·RQ4 CTI 수집에 재사용한다.
수정·보완: requests/DB/CLI와 고정 freshness=pw를 제거하고 pure 함수만 복사했다.
입력·출력: query/watchlist/URL을 받아 전개 query 또는 whitelist 판정을 반환한다.
시간·provenance: 검색 기간은 상위 BraveProtocolSearchBackend가 등록 protocol로 고정한다.
보안·라이선스: 명시적 원본 라이선스가 없어 내부 연구용이며 재배포 전 권리 확인이 필요하다.
"""
from __future__ import annotations

from urllib.parse import urlparse


def expand_queries(queries: list[str], watchlist: dict) -> list[str]:
    """watchlist template을 priority 오름차순 canonical 이름으로 전개한다."""

    groups = sorted(watchlist.get("groups", []), key=lambda g: g.get("priority", 3))
    networks = sorted(watchlist.get("covert_networks", []), key=lambda n: n.get("priority", 3))
    output: list[str] = []
    for query in queries:
        if "{watchlist_group}" in query:
            output.extend(query.replace("{watchlist_group}", group["canonical"]) for group in groups)
        elif "{covert_network}" in query:
            output.extend(query.replace("{covert_network}", network["canonical"]) for network in networks)
        else:
            output.append(query)
    return output


def in_whitelist(url: str, whitelist: list[str]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    domains = [domain.strip().lower().strip(".") for domain in whitelist]
    return any(host == domain or host.endswith("." + domain) for domain in domains if domain)


def filter_whitelist(results: list[dict], whitelist: list[str]) -> list[dict]:
    return [result for result in results if in_whitelist(result.get("url", ""), whitelist)]
