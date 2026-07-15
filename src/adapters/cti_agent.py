"""CTI-Agent 검색·IoC 정규화 재사용 adapter.

목적: CTI-Agent에서 복사·검토한 defang·형식검증·검색어 전개 내부 파생본을 호출한다.
지원 RQ: RQ1 seed corpus, RQ2 cohort seed, RQ3 non-IP pivot, RQ4 validation provenance.
재사용 원천: D:/Claude/CTI-Agent의 ioc_regex.py와 search_collector.py를 reused/에 내부화했다.
설계: runtime은 내부 pure module만 사용하고 외부 repository는 원본 hash 검증에만 사용한다.
입력·출력: raw IoC·scope 또는 검색 template을 받아 정규화값·확장 query를 반환한다.
시간·provenance 통제: published date를 추정하지 않으며 adapter 결과에는 caller가 cutoff를 부여한다.
보안·라이선스: 내부 연구용 파생본이며 외부 저장소·network를 runtime에 호출하지 않는다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from src.provenance import sha256_file
from src.reused.cti_agent import ioc_regex, search_rules


class CtiAgentAdapter:
    """CTI-Agent의 pure function을 읽기 전용으로 연결한다."""

    def __init__(self, repository: Path | None = None, expected_hashes: dict[str, str] | None = None):
        self.repository = repository.resolve() if repository else None
        self.expected_hashes = expected_hashes or {}

    def verify_reuse_files(self) -> None:
        if self.repository is None:
            raise ValueError("external CTI-Agent repository is required only for provenance verification")
        for relative, expected in self.expected_hashes.items():
            actual = sha256_file(self.repository / relative)
            if actual.lower() != expected.lower():
                raise ValueError(f"reuse source hash mismatch: {relative}")

    def normalize_indicator(self, scope: str, raw_form: str) -> str:
        value = ioc_regex.normalize(scope, raw_form)
        if not ioc_regex.validate(scope, value):
            raise ValueError(f"invalid {scope} indicator after CTI-Agent normalization")
        return value

    def validate_indicator(self, scope: str, normalized_value: str) -> bool:
        return bool(ioc_regex.validate(scope, normalized_value))

    def expand_search_queries(self, queries: list[str], watchlist: dict[str, Any]) -> list[str]:
        """내부화한 CTI-Agent pure query expansion을 실행한다."""

        return list(search_rules.expand_queries(queries, watchlist))
