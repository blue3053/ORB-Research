"""등록 기간을 고정하는 Brave 기반 CTI 검색 backend.

목적: CTI-Agent의 Brave 검색 방식을 논문용 custom date range와 전체 offset 수집으로 확장한다.
지원 RQ: RQ1 systematic public corpus, RQ4 prospective validation source discovery.
재사용 원천: CTI-Agent search_collector의 endpoint·환경변수·검색 결과 형태를 유지한다.
설계: 공식 count≤20·offset≤9 계약, live gate, 최소 metadata 반환을 적용한다.
입력·출력: 검색문을 받아 title·URL·선택적 게시일 metadata 목록을 반환한다.
시간·provenance 통제: freshness를 protocol의 YYYY-MM-DDtoYYYY-MM-DD로 고정한다.
보안·라이선스: API key는 환경변수에서만 읽고 원시 응답·snippet을 저장하지 않는다.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


class BraveProtocolSearchBackend:
    """SearchBackend 계약을 만족하는 기간 고정형 Brave Web Search client."""

    name = "brave-web-search-v1"

    def __init__(
        self,
        *,
        date_from: str,
        date_to: str,
        count: int = 20,
        max_pages: int = 10,
        timeout_seconds: float = 30.0,
    ):
        if os.environ.get("ALLOW_LIVE_CTI_SEARCH") != "1":
            raise RuntimeError("live CTI search requires ALLOW_LIVE_CTI_SEARCH=1")
        self.api_key = os.environ.get("BRAVE_SEARCH_API_KEY")
        if not self.api_key:
            raise RuntimeError("BRAVE_SEARCH_API_KEY is required")
        if not 1 <= count <= 20:
            raise ValueError("Brave count must be between 1 and 20")
        if not 1 <= max_pages <= 10:
            raise ValueError("Brave max_pages must be between 1 and 10")
        self.freshness = f"{date_from}to{date_to}"
        self.count = count
        self.max_pages = max_pages
        self.timeout_seconds = timeout_seconds

    def _fetch_page(self, query: str, offset: int) -> dict[str, Any]:
        params = urllib.parse.urlencode({
            "q": query,
            "count": self.count,
            "offset": offset,
            "freshness": self.freshness,
            "result_filter": "web",
            "text_decorations": "false",
        })
        request = urllib.request.Request(
            f"{BRAVE_ENDPOINT}?{params}",
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self.api_key,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read(500).decode("utf-8", errors="replace")
            raise RuntimeError(f"Brave Search HTTP {exc.code}: {detail}") from exc

    def search(self, query: str) -> list[dict[str, Any]]:
        if not query.strip() or len(query) > 400 or len(query.split()) > 50:
            raise ValueError("Brave query must contain 1-400 characters and at most 50 words")
        normalized: list[dict[str, Any]] = []
        for offset in range(self.max_pages):
            payload = self._fetch_page(query, offset)
            web = payload.get("web") if isinstance(payload.get("web"), dict) else {}
            results = web.get("results") if isinstance(web.get("results"), list) else []
            for item in results:
                if not isinstance(item, dict):
                    continue
                normalized.append({
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "published_at": item.get("page_age") or item.get("published_at"),
                    "provider_offset": offset,
                })
            if not results or not web.get("more_results_available"):
                break
        return normalized
