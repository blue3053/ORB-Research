"""CTI 검색·screening provenance SQLite registry.

목적: 검색·screening provenance와 제한 계층 indicator·출처별 assertion을 감사 가능하게 저장한다.
지원 RQ: RQ1 표본 선택, RQ4 독립 validation, RQ5 source selection bias 보고.
재사용 원천: CTI-Agent reports DB를 대체하지 않고 논문용 획득 provenance를 보강한다.
설계: protocol은 immutable, search run과 screening은 append-only 행으로 기록한다.
입력·출력: 검색 metadata, SourceDocumentRecord, IndicatorRecord, IndicatorAssertionRecord를 SQLite에 기록한다.
시간·provenance 통제: search execution과 publication time을 별도 계층에서 유지한다.
보안·라이선스: 원문과 HMAC secret은 저장하지 않으며 normalized IoC가 있는 DB는 restricted로 취급한다.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from src.models import (
    IndicatorAssertionRecord,
    IndicatorRecord,
    ScreeningDecision,
    SearchProtocolRecord,
    SourceDocumentRecord,
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS cti_search_protocols (
  search_protocol_id TEXT PRIMARY KEY,
  protocol_hash TEXT NOT NULL UNIQUE,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cti_search_runs (
  search_run_id TEXT PRIMARY KEY,
  search_protocol_id TEXT NOT NULL REFERENCES cti_search_protocols(search_protocol_id),
  executed_at TEXT NOT NULL,
  search_engine TEXT NOT NULL,
  query_text TEXT NOT NULL,
  result_count INTEGER NOT NULL CHECK(result_count >= 0),
  result_manifest_hash TEXT NOT NULL,
  status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cti_screening_decisions (
  screening_id TEXT PRIMARY KEY,
  search_run_id TEXT NOT NULL REFERENCES cti_search_runs(search_run_id),
  candidate_url TEXT NOT NULL,
  canonical_document_id TEXT,
  decision TEXT NOT NULL,
  reason_code TEXT NOT NULL,
  reviewer_id TEXT NOT NULL,
  reviewed_at TEXT NOT NULL,
  notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS source_documents (
  document_id TEXT PRIMARY KEY,
  content_sha256 TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS indicators (
  indicator_id TEXT PRIMARY KEY,
  indicator_type TEXT NOT NULL,
  normalized_value TEXT NOT NULL,
  public_id TEXT NOT NULL UNIQUE,
  first_ingested_at TEXT NOT NULL,
  sensitivity TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  UNIQUE(indicator_type, normalized_value)
);
CREATE TABLE IF NOT EXISTS indicator_assertions (
  assertion_id TEXT PRIMARY KEY,
  indicator_id TEXT NOT NULL REFERENCES indicators(indicator_id),
  document_id TEXT NOT NULL REFERENCES source_documents(document_id),
  campaign_id TEXT,
  role TEXT NOT NULL,
  verdict TEXT NOT NULL,
  evidence_type TEXT NOT NULL,
  first_public_at TEXT NOT NULL,
  reviewer_status TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
"""


class CorpusRegistry:
    """검색 protocol·run·screening 판정의 작은 트랜잭션 registry."""

    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """SQLite 연결을 transaction 종료 후 반드시 close한다."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def register_protocol(self, protocol: SearchProtocolRecord) -> None:
        payload = json.dumps(protocol.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT payload_json FROM cti_search_protocols WHERE search_protocol_id = ?",
                (protocol.search_protocol_id,),
            ).fetchone()
            if existing and existing[0] != payload:
                raise ValueError("search protocol ID collision with different content")
            connection.execute(
                "INSERT OR IGNORE INTO cti_search_protocols VALUES (?, ?, ?)",
                (protocol.search_protocol_id, protocol.protocol_hash, payload),
            )

    def record_search_run(
        self,
        *,
        search_run_id: str,
        search_protocol_id: str,
        search_engine: str,
        query_text: str,
        result_count: int,
        result_manifest_hash: str,
        status: str,
        executed_at: datetime | None = None,
    ) -> None:
        executed = (executed_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO cti_search_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (search_run_id, search_protocol_id, executed, search_engine, query_text,
                 result_count, result_manifest_hash, status),
            )

    def record_screening(
        self,
        *,
        screening_id: str,
        search_run_id: str,
        candidate_url: str,
        decision: ScreeningDecision,
        reason_code: str,
        reviewer_id: str,
        canonical_document_id: str | None = None,
        reviewed_at: datetime | None = None,
        notes: str = "",
    ) -> None:
        reviewed = (reviewed_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO cti_screening_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (screening_id, search_run_id, candidate_url, canonical_document_id,
                 decision.value, reason_code, reviewer_id, reviewed, notes),
            )

    @staticmethod
    def _payload(record) -> str:
        return json.dumps(
            record.model_dump(mode="json"), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _assert_same_payload(
        connection: sqlite3.Connection,
        *,
        table: str,
        key_column: str,
        key: str,
        payload: str,
    ) -> bool:
        row = connection.execute(
            f"SELECT payload_json FROM {table} WHERE {key_column} = ?", (key,)
        ).fetchone()
        if row is None:
            return False
        if row[0] != payload:
            raise ValueError(f"{table} immutable ID collision: {key}")
        return True

    def register_indicator_bundle(
        self,
        *,
        document: SourceDocumentRecord,
        indicators: list[IndicatorRecord],
        assertions: list[IndicatorAssertionRecord],
    ) -> dict[str, int]:
        """문서·indicator·assertion을 한 transaction에서 멱등 등록한다."""

        indicator_ids = {record.indicator_id for record in indicators}
        if any(record.document_id != document.document_id for record in assertions):
            raise ValueError("assertion document_id does not match bundle document")
        if any(record.indicator_id not in indicator_ids for record in assertions):
            raise ValueError("assertion references indicator outside bundle")
        counts = {"documents_inserted": 0, "indicators_inserted": 0, "assertions_inserted": 0}
        with self.connect() as connection:
            document_payload = self._payload(document)
            if not self._assert_same_payload(
                connection, table="source_documents", key_column="document_id",
                key=document.document_id, payload=document_payload,
            ):
                connection.execute(
                    "INSERT INTO source_documents VALUES (?, ?, ?)",
                    (document.document_id, document.content_sha256, document_payload),
                )
                counts["documents_inserted"] += 1
            for indicator in indicators:
                payload = self._payload(indicator)
                if self._assert_same_payload(
                    connection, table="indicators", key_column="indicator_id",
                    key=indicator.indicator_id, payload=payload,
                ):
                    continue
                connection.execute(
                    "INSERT INTO indicators VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        indicator.indicator_id, indicator.indicator_type.value,
                        indicator.normalized_value, indicator.public_id,
                        indicator.first_ingested_at.isoformat(), indicator.sensitivity.value,
                        payload,
                    ),
                )
                counts["indicators_inserted"] += 1
            for assertion in assertions:
                payload = self._payload(assertion)
                if self._assert_same_payload(
                    connection, table="indicator_assertions", key_column="assertion_id",
                    key=assertion.assertion_id, payload=payload,
                ):
                    continue
                connection.execute(
                    "INSERT INTO indicator_assertions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        assertion.assertion_id, assertion.indicator_id,
                        assertion.document_id, assertion.campaign_id,
                        assertion.role.value, assertion.verdict.value,
                        assertion.evidence_type.value, assertion.first_public_at.isoformat(),
                        assertion.reviewer_status.value, payload,
                    ),
                )
                counts["assertions_inserted"] += 1
        return counts

    def register_indicators(self, indicators: list[IndicatorRecord]) -> int:
        """CTI assertion 없이 Censys에서 처음 발견된 restricted IP를 멱등 등록한다."""

        inserted = 0
        with self.connect() as connection:
            for indicator in indicators:
                payload = self._payload(indicator)
                if self._assert_same_payload(
                    connection, table="indicators", key_column="indicator_id",
                    key=indicator.indicator_id, payload=payload,
                ):
                    continue
                connection.execute(
                    "INSERT INTO indicators VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        indicator.indicator_id, indicator.indicator_type.value,
                        indicator.normalized_value, indicator.public_id,
                        indicator.first_ingested_at.isoformat(), indicator.sensitivity.value,
                        payload,
                    ),
                )
                inserted += 1
        return inserted

    def ipv4_indicator_id_map(self) -> dict[str, str]:
        """기존 IPv4 normalized value를 canonical indicator ID에 연결한다."""

        with self.connect() as connection:
            rows = connection.execute(
                "SELECT normalized_value, indicator_id FROM indicators "
                "WHERE indicator_type = ? ORDER BY normalized_value",
                ("ipv4",),
            ).fetchall()
        return {row[0]: row[1] for row in rows}

    def register_document(self, document: SourceDocumentRecord) -> bool:
        """existing_curated 문서를 원본 내용 변경 없이 멱등 등록한다."""

        payload = self._payload(document)
        with self.connect() as connection:
            if self._assert_same_payload(
                connection, table="source_documents", key_column="document_id",
                key=document.document_id, payload=payload,
            ):
                return False
            connection.execute(
                "INSERT INTO source_documents VALUES (?, ?, ?)",
                (document.document_id, document.content_sha256, payload),
            )
        return True
