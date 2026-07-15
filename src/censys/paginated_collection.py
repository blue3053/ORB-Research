"""Censys Platform API 커서 페이지 수집과 불변 원시 응답 저장.

목적: ORB_Hunt_v5의 첫 페이지 제한을 보완해 page_token이 끝날 때까지 수집하고 페이지별 복구 지점을 남긴다.
지원 RQ: Q0/Q1을 사용하는 RQ1∼RQ3 및 이후 Q2/Q3의 RQ4∼RQ5.
재사용 원천: ORB_Hunt_v5의 live gate·retry/fail-fast 철학과 parser 입력 형식을 유지한다.
설계: PageFetcher 주입, token loop 탐지, max-pages 안전장치, 불변 page JSON, append-only checkpoint를 사용한다.
입력·출력: query·run directory를 받아 페이지/host 수와 최종 상태를 반환한다.
시간·provenance 통제: 각 요청 시작·종료시각, query hash, request/next token hash를 페이지 manifest에 기록한다.
보안·라이선스: bearer token은 환경변수에서만 읽고 저장하지 않으며 raw 결과 디렉터리는 restricted로 취급한다.
"""
from __future__ import annotations

import json
import ipaddress
import os
import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

import httpx

from src.manifests import append_jsonl, load_jsonl, write_immutable_json
from src.provenance import canonical_json_hash, sha256_text


PLATFORM_SEARCH_ENDPOINT = "https://api.platform.censys.io/v3/global/search/query"


class PageFetcher(Protocol):
    def fetch(
        self, *, query: str, page_size: int, page_token: str | None, fields: list[str] | None
    ) -> Mapping[str, Any]: ...


class CensysPlatformHttpFetcher:
    """Cloudflare와 호환되는 httpx 기반 Censys Platform v3 POST transport."""

    def __init__(self, *, timeout_seconds: float = 30.0, client_factory=None):
        if os.environ.get("ALLOW_LIVE_CENSYS") != "1":
            raise RuntimeError("live Censys collection requires ALLOW_LIVE_CENSYS=1")
        self.token = os.environ.get("CENSYS_TOKEN") or os.environ.get("CENSYS_API_SECRET")
        self.organization_id = os.environ.get("CENSYS_API_ID")
        self.timeout_seconds = timeout_seconds
        self.client_factory = client_factory or httpx.Client
        if not self.token:
            raise RuntimeError("CENSYS_TOKEN or CENSYS_API_SECRET is required")

    def fetch(
        self, *, query: str, page_size: int, page_token: str | None, fields: list[str] | None
    ) -> Mapping[str, Any]:
        body: dict[str, Any] = {"query": query, "page_size": page_size}
        if page_token:
            body["page_token"] = page_token
        if fields:
            body["fields"] = fields
        endpoint = PLATFORM_SEARCH_ENDPOINT
        params = {"organization_id": self.organization_id} if self.organization_id else None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        with self.client_factory(timeout=self.timeout_seconds) as client:
            response = client.post(endpoint, params=params, headers=headers, json=body)
        if response.status_code >= 400:
            raise RuntimeError(f"Censys HTTP {response.status_code}: {response.text[:500]}")
        return response.json()


class CensysQ0HostLookupFetcher:
    """Global Search 권한 없이 exact-IP Q0를 host lookup으로 수집한다."""

    _Q0_PATTERN = re.compile(r"\s*host\.ip\s*=\s*([^\s]+)\s*", re.IGNORECASE)

    def __init__(self, *, timeout_seconds: float = 30.0, client_factory=None):
        if os.environ.get("ALLOW_LIVE_CENSYS") != "1":
            raise RuntimeError("live Censys collection requires ALLOW_LIVE_CENSYS=1")
        self.token = os.environ.get("CENSYS_TOKEN") or os.environ.get("CENSYS_API_SECRET")
        self.organization_id = os.environ.get("CENSYS_API_ID")
        self.timeout_seconds = timeout_seconds
        self.client_factory = client_factory or httpx.Client
        if not self.token:
            raise RuntimeError("CENSYS_TOKEN or CENSYS_API_SECRET is required")

    @classmethod
    def _exact_ip(cls, query: str) -> str:
        match = cls._Q0_PATTERN.fullmatch(query)
        if match is None:
            raise ValueError("Q0 host lookup requires exact 'host.ip = IP' query")
        try:
            return ipaddress.ip_address(match.group(1)).compressed
        except ValueError as error:
            raise ValueError("Q0 host lookup query contains invalid IP") from error

    @staticmethod
    def _search_envelope(payload: Mapping[str, Any], *, http_status: int) -> dict[str, Any]:
        result = payload.get("result") if isinstance(payload.get("result"), Mapping) else {}
        resource = result.get("resource") if isinstance(result, Mapping) else None
        hits = [{"host_v1": {"resource": dict(resource)}}] if isinstance(resource, Mapping) else []
        return {
            "result": {"hits": hits, "links": {}},
            "q0_host_lookup": {
                "http_status": http_status,
                "lookup_response": dict(payload),
            },
        }

    def fetch(
        self, *, query: str, page_size: int, page_token: str | None, fields: list[str] | None
    ) -> Mapping[str, Any]:
        del page_size, fields
        if page_token:
            raise ValueError("Q0 host lookup does not support page_token")
        ip_value = self._exact_ip(query)
        endpoint = (
            "https://api.platform.censys.io/v3/global/asset/host/"
            + urllib.parse.quote(ip_value, safe="")
        )
        if self.organization_id:
            endpoint += "?organization_id=" + urllib.parse.quote(self.organization_id, safe="")
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.censys.api.v3.host.v1+json",
        }
        with self.client_factory(timeout=self.timeout_seconds) as client:
            response = client.get(endpoint, headers=headers)
        if response.status_code == 404:
            return self._search_envelope(
                {"result": {}, "not_found_detail": response.text[:500]}, http_status=404
            )
        if response.status_code >= 400:
            raise RuntimeError(f"Censys HTTP {response.status_code}: {response.text[:500]}")
        return self._search_envelope(response.json(), http_status=response.status_code)


@dataclass(frozen=True)
class ParsedPage:
    hits: tuple[dict[str, Any], ...]
    next_page_token: str | None


@dataclass(frozen=True)
class CollectionResult:
    status: str
    page_count: int
    hit_count: int
    final_page_token: str | None
    query_hash: str


@dataclass(frozen=True)
class CheckpointState:
    page_count: int
    hit_count: int
    next_page_token: str | None
    complete: bool
    requested_tokens: tuple[str, ...]


def load_checkpoint_state(run_directory: Path, query_hash: str) -> CheckpointState:
    """기존 page/checkpoint 쌍을 순서·hash·query 기준으로 검증하고 복구 상태를 반환한다."""

    checkpoint_path = run_directory / "checkpoints.jsonl"
    if not checkpoint_path.exists():
        return CheckpointState(0, 0, None, False, ())
    checkpoints = list(load_jsonl(checkpoint_path))
    if not checkpoints:
        raise ValueError("checkpoint file exists but is empty")
    prior_next_tokens: list[str] = []
    previous_hits = 0
    for expected_page, checkpoint in enumerate(checkpoints, start=1):
        if checkpoint.get("page_number") != expected_page:
            raise ValueError("checkpoint page numbers are not contiguous")
        page_path = run_directory / f"page-{expected_page:06d}.json"
        if not page_path.is_file():
            raise ValueError(f"checkpoint page file is missing: {page_path.name}")
        page_record = json.loads(page_path.read_text(encoding="utf-8"))
        if page_record.get("query_hash") != query_hash:
            raise ValueError("checkpoint query hash does not match requested query")
        if canonical_json_hash(page_record) != checkpoint.get("page_hash"):
            raise ValueError(f"checkpoint page hash mismatch: {page_path.name}")
        cumulative_hits = checkpoint.get("cumulative_hits")
        if not isinstance(cumulative_hits, int) or cumulative_hits < previous_hits:
            raise ValueError("checkpoint cumulative hit count is invalid")
        previous_hits = cumulative_hits
        token = checkpoint.get("next_page_token")
        if token is not None and not isinstance(token, str):
            raise ValueError("checkpoint next_page_token must be string or null")
        prior_next_tokens.append(token)
    final_token = prior_next_tokens[-1]
    requested = tuple(token for token in prior_next_tokens[:-1] if token)
    return CheckpointState(
        page_count=len(checkpoints),
        hit_count=previous_hits,
        next_page_token=final_token,
        complete=final_token is None,
        requested_tokens=requested,
    )


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "dict"):
        return value.dict()
    raise TypeError("Censys response must be mapping-like")


def parse_search_page(payload: Mapping[str, Any]) -> ParsedPage:
    """SDK/REST envelope 차이를 흡수해 hits와 다음 page token만 추출한다."""

    current = _as_mapping(payload)
    for _ in range(3):
        if isinstance(current.get("hits"), list):
            break
        nested = current.get("result")
        if not isinstance(nested, Mapping):
            break
        current = nested
    raw_hits = current.get("hits") or []
    if not isinstance(raw_hits, list):
        raise ValueError("Censys response hits must be a list")
    links = current.get("links") if isinstance(current.get("links"), Mapping) else {}
    next_token = (
        current.get("next_page_token")
        or links.get("next_page_token")
        or links.get("next")
    )
    return ParsedPage(
        hits=tuple(dict(_as_mapping(hit)) for hit in raw_hits),
        next_page_token=str(next_token) if next_token else None,
    )


class PaginatedCensysCollector:
    def __init__(self, fetcher: PageFetcher):
        self.fetcher = fetcher

    def collect(
        self,
        *,
        query: str,
        run_directory: Path,
        page_size: int = 100,
        max_pages: int | None = None,
        fields: list[str] | None = None,
        initial_page_token: str | None = None,
    ) -> CollectionResult:
        """page_token 소진·max_pages 도달·오류 중 하나까지 순차 수집한다."""

        if not 1 <= page_size <= 100:
            raise ValueError("Censys page_size must be between 1 and 100")
        if max_pages is not None and max_pages < 1:
            raise ValueError("max_pages must be positive or None")
        query_hash = sha256_text(query)
        run_directory.mkdir(parents=True, exist_ok=True)
        state = load_checkpoint_state(run_directory, query_hash)
        if state.complete:
            return CollectionResult(
                "complete", state.page_count, state.hit_count, None, query_hash
            )
        if state.page_count and initial_page_token and initial_page_token != state.next_page_token:
            raise ValueError("initial_page_token conflicts with persisted checkpoint")
        token = state.next_page_token if state.page_count else initial_page_token
        seen_tokens: set[str] = set(state.requested_tokens)
        page_count = state.page_count
        hit_count = state.hit_count
        if max_pages is not None and page_count >= max_pages:
            return CollectionResult(
                "partial_max_pages", page_count, hit_count, token, query_hash
            )
        while True:
            if token:
                if token in seen_tokens:
                    raise RuntimeError("Censys pagination token loop detected")
                seen_tokens.add(token)
            started = datetime.now(timezone.utc)
            payload = self.fetcher.fetch(
                query=query, page_size=page_size, page_token=token, fields=fields
            )
            finished = datetime.now(timezone.utc)
            parsed = parse_search_page(payload)
            page_count += 1
            hit_count += len(parsed.hits)
            page_record = {
                "page_number": page_count,
                "query_hash": query_hash,
                "request_token_hash": sha256_text(token) if token else None,
                "next_page_token_hash": (
                    sha256_text(parsed.next_page_token) if parsed.next_page_token else None
                ),
                "request_started_at": started.isoformat(),
                "request_finished_at": finished.isoformat(),
                "hit_count": len(parsed.hits),
                "response": payload,
            }
            page_path = run_directory / f"page-{page_count:06d}.json"
            page_hash = write_immutable_json(page_path, page_record)
            append_jsonl(run_directory / "checkpoints.jsonl", {
                "page_number": page_count,
                "page_hash": page_hash,
                "next_page_token": parsed.next_page_token,
                "cumulative_hits": hit_count,
            })
            token = parsed.next_page_token
            if not token:
                status = "complete"
                break
            if max_pages is not None and page_count >= max_pages:
                status = "partial_max_pages"
                break
        return CollectionResult(status, page_count, hit_count, token, query_hash)
