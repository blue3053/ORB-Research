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
from typing import Any, Iterator

from src.models import (
    AcceptedPivotSource,
    AssertionReviewRecord,
    AssertionRole,
    IndicatorAssertionRecord,
    IndicatorRecord,
    ScreeningDecision,
    SearchProtocolRecord,
    SourceDocumentRecord,
    SourceFamilyRecord,
    SourceRelationshipRecord,
    SourceMentionRecord,
    ReviewerStatus,
    SourceAccessClass,
    TimePrecision,
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
CREATE TABLE IF NOT EXISTS source_families (
  source_family_id TEXT PRIMARY KEY,
  canonical_document_id TEXT NOT NULL,
  reviewer_id TEXT NOT NULL,
  reviewed_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_relationships (
  relationship_id TEXT PRIMARY KEY,
  source_family_id TEXT NOT NULL REFERENCES source_families(source_family_id),
  document_id TEXT NOT NULL,
  related_document_id TEXT NOT NULL,
  relationship_type TEXT NOT NULL,
  reviewer_id TEXT NOT NULL,
  reviewed_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_family_memberships (
  document_id TEXT PRIMARY KEY REFERENCES source_documents(document_id),
  source_family_id TEXT NOT NULL REFERENCES source_families(source_family_id)
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
CREATE TABLE IF NOT EXISTS source_mentions (
  mention_id TEXT PRIMARY KEY,
  indicator_id TEXT NOT NULL REFERENCES indicators(indicator_id),
  document_id TEXT NOT NULL REFERENCES source_documents(document_id),
  source_family_id TEXT NOT NULL REFERENCES source_families(source_family_id),
  scope TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  available_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS indicator_assertions (
  assertion_id TEXT PRIMARY KEY,
  indicator_id TEXT NOT NULL REFERENCES indicators(indicator_id),
  document_id TEXT NOT NULL REFERENCES source_documents(document_id),
  source_mention_id TEXT REFERENCES source_mentions(mention_id),
  campaign_id TEXT,
  role TEXT NOT NULL,
  verdict TEXT NOT NULL,
  evidence_type TEXT NOT NULL,
  first_public_at TEXT NOT NULL,
  available_at TEXT,
  source_confidence REAL,
  extraction_confidence REAL,
  role_confidence REAL,
  reviewer_status TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assertion_reviews (
  review_id TEXT PRIMARY KEY,
  assertion_id TEXT NOT NULL UNIQUE REFERENCES indicator_assertions(assertion_id),
  decision TEXT NOT NULL,
  reviewer_id TEXT NOT NULL,
  reviewed_at TEXT NOT NULL,
  reviewed_role TEXT NOT NULL,
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
        self._migrate_stage1_columns(connection)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _migrate_stage1_columns(connection: sqlite3.Connection) -> None:
        """Add nullable Stage 1 columns; legacy payloads remain invalid until reviewed."""

        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(indicator_assertions)")
        }
        additions = {
            "source_mention_id": "TEXT",
            "available_at": "TEXT",
            "source_confidence": "REAL",
            "extraction_confidence": "REAL",
            "role_confidence": "REAL",
        }
        for name, sql_type in additions.items():
            if name not in columns:
                connection.execute(
                    f"ALTER TABLE indicator_assertions ADD COLUMN {name} {sql_type}"
                )

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

    @staticmethod
    def _require_source_family(
        connection: sqlite3.Connection, source_family_id: str | None
    ) -> str:
        if not source_family_id:
            raise ValueError("reviewed source family is required")
        row = connection.execute(
            "SELECT 1 FROM source_families WHERE source_family_id = ?",
            (source_family_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"reviewed source family is not registered: {source_family_id}")
        return source_family_id

    @staticmethod
    def _register_source_family_membership(
        connection: sqlite3.Connection, document_id: str, source_family_id: str
    ) -> None:
        row = connection.execute(
            "SELECT source_family_id FROM source_family_memberships WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        if row is not None and row[0] != source_family_id:
            raise ValueError(
                f"document already belongs to another source family: {document_id}"
            )
        connection.execute(
            "INSERT OR IGNORE INTO source_family_memberships VALUES (?, ?)",
            (document_id, source_family_id),
        )

    def register_source_family(
        self,
        family: SourceFamilyRecord,
        relationships: list[SourceRelationshipRecord],
    ) -> dict[str, int]:
        """사람이 검토한 source family와 계보 관계를 불변 레코드로 등록한다."""

        if not family.source_family_id.strip():
            raise ValueError("source_family_id is required")
        if not family.canonical_document_id.strip():
            raise ValueError("canonical_document_id is required")
        if not family.reviewer_id.strip():
            raise ValueError("source family reviewer_id is required")
        if any(item.source_family_id != family.source_family_id for item in relationships):
            raise ValueError("relationship source_family_id does not match family")
        if any(not item.reviewer_id.strip() for item in relationships):
            raise ValueError("source relationship reviewer_id is required")
        if any(
            not item.relationship_id.strip()
            or not item.document_id.strip()
            or not item.related_document_id.strip()
            for item in relationships
        ):
            raise ValueError("source relationship identifiers are required")
        effects = {"families_inserted": 0, "relationships_inserted": 0}
        with self.connect() as connection:
            family_payload = self._payload(family)
            if not self._assert_same_payload(
                connection, table="source_families",
                key_column="source_family_id", key=family.source_family_id,
                payload=family_payload,
            ):
                connection.execute(
                    "INSERT INTO source_families VALUES (?, ?, ?, ?, ?)",
                    (
                        family.source_family_id, family.canonical_document_id,
                        family.reviewer_id, family.reviewed_at.isoformat(), family_payload,
                    ),
                )
                effects["families_inserted"] += 1
            for relationship in relationships:
                payload = self._payload(relationship)
                if self._assert_same_payload(
                    connection, table="source_relationships",
                    key_column="relationship_id", key=relationship.relationship_id,
                    payload=payload,
                ):
                    continue
                connection.execute(
                    "INSERT INTO source_relationships VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        relationship.relationship_id, relationship.source_family_id,
                        relationship.document_id, relationship.related_document_id,
                        relationship.relationship_type.value, relationship.reviewer_id,
                        relationship.reviewed_at.isoformat(), payload,
                    ),
                )
                effects["relationships_inserted"] += 1
        return effects

    def independent_source_family_ids(self, document_ids: list[str]) -> set[str]:
        """문서 목록을 중복 제거된 source family ID 집합으로 변환한다."""

        requested = set(document_ids)
        if not requested:
            return set()
        placeholders = ",".join("?" for _ in requested)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT document_id, source_family_id FROM source_family_memberships "
                f"WHERE document_id IN ({placeholders})",
                tuple(sorted(requested)),
            ).fetchall()
        found = {row[0] for row in rows}
        missing = sorted(requested - found)
        if missing:
            raise ValueError(
                "documents lack reviewed source family membership: " + ", ".join(missing)
            )
        return {row[1] for row in rows}

    def register_indicator_bundle(
        self,
        *,
        document: SourceDocumentRecord,
        indicators: list[IndicatorRecord],
        mentions: list[SourceMentionRecord],
        assertions: list[IndicatorAssertionRecord],
    ) -> dict[str, int]:
        """문서·indicator·assertion을 한 transaction에서 멱등 등록한다."""

        if (
            document.published_at is None
            or document.published_time_precision is not TimePrecision.EXACT_TIMESTAMP
        ):
            raise ValueError(
                "exact publication timestamp is required for assertion registration"
            )
        if not document.source_family_id:
            raise ValueError("reviewed source family is required for assertion registration")
        indicator_ids = {record.indicator_id for record in indicators}
        mention_ids = {record.mention_id for record in mentions}
        if any(record.document_id != document.document_id for record in mentions):
            raise ValueError("source mention document_id does not match bundle document")
        if any(record.indicator_id not in indicator_ids for record in mentions):
            raise ValueError("source mention references indicator outside bundle")
        if any(record.source_family_id != document.source_family_id for record in mentions):
            raise ValueError("source mention family does not match bundle document")
        if any(record.document_id != document.document_id for record in assertions):
            raise ValueError("assertion document_id does not match bundle document")
        if any(record.indicator_id not in indicator_ids for record in assertions):
            raise ValueError("assertion references indicator outside bundle")
        if any(record.source_mention_id not in mention_ids for record in assertions):
            raise ValueError("assertion references source mention outside bundle")
        counts = {
            "documents_inserted": 0,
            "indicators_inserted": 0,
            "mentions_inserted": 0,
            "assertions_inserted": 0,
        }
        with self.connect() as connection:
            source_family_id = self._require_source_family(
                connection, document.source_family_id
            )
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
            self._register_source_family_membership(
                connection, document.document_id, source_family_id
            )
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
            for mention in mentions:
                payload = self._payload(mention)
                if self._assert_same_payload(
                    connection, table="source_mentions", key_column="mention_id",
                    key=mention.mention_id, payload=payload,
                ):
                    continue
                connection.execute(
                    "INSERT INTO source_mentions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        mention.mention_id, mention.indicator_id, mention.document_id,
                        mention.source_family_id, mention.scope,
                        mention.observed_at.isoformat(),
                        mention.available_at.isoformat(), payload,
                    ),
                )
                counts["mentions_inserted"] += 1
            for assertion in assertions:
                payload = self._payload(assertion)
                if self._assert_same_payload(
                    connection, table="indicator_assertions", key_column="assertion_id",
                    key=assertion.assertion_id, payload=payload,
                ):
                    continue
                connection.execute(
                    "INSERT INTO indicator_assertions "
                    "(assertion_id, indicator_id, document_id, source_mention_id, campaign_id, "
                    "role, verdict, evidence_type, first_public_at, available_at, "
                    "source_confidence, extraction_confidence, role_confidence, "
                    "reviewer_status, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        assertion.assertion_id, assertion.indicator_id,
                        assertion.document_id, assertion.source_mention_id,
                        assertion.campaign_id,
                        assertion.role.value, assertion.verdict.value,
                        assertion.evidence_type.value, assertion.first_public_at.isoformat(),
                        assertion.available_at.isoformat(), assertion.source_confidence,
                        assertion.extraction_confidence, assertion.role_confidence,
                        assertion.reviewer_status.value, payload,
                    ),
                )
                counts["assertions_inserted"] += 1
        return counts

    def register_assertion_reviews(
        self, reviews: list[AssertionReviewRecord]
    ) -> int:
        """Persist one immutable human decision per assertion."""

        inserted = 0
        blocked_roles = {
            AssertionRole.UNKNOWN, AssertionRole.VICTIM, AssertionRole.SINKHOLE
        }
        with self.connect() as connection:
            for review in reviews:
                row = connection.execute(
                    "SELECT payload_json FROM indicator_assertions WHERE assertion_id = ?",
                    (review.assertion_id,),
                ).fetchone()
                if row is None:
                    raise ValueError(f"assertion review references unknown assertion: {review.assertion_id}")
                assertion = IndicatorAssertionRecord.model_validate_json(row[0])
                if review.reviewed_at < assertion.available_at:
                    raise ValueError("assertion cannot be reviewed before it is available")
                if review.decision is ReviewerStatus.ACCEPTED:
                    if review.reviewed_role in blocked_roles:
                        raise ValueError("accepted assertion role is not queryable")
                    if min(
                        review.source_confidence,
                        review.extraction_confidence,
                        review.role_confidence,
                    ) <= 0:
                        raise ValueError("accepted assertion confidence must be positive")
                payload = self._payload(review)
                if self._assert_same_payload(
                    connection, table="assertion_reviews", key_column="review_id",
                    key=review.review_id, payload=payload,
                ):
                    continue
                existing = connection.execute(
                    "SELECT review_id FROM assertion_reviews WHERE assertion_id = ?",
                    (review.assertion_id,),
                ).fetchone()
                if existing is not None:
                    raise ValueError("assertion already has an immutable review")
                connection.execute(
                    "INSERT INTO assertion_reviews VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        review.review_id, review.assertion_id, review.decision.value,
                        review.reviewer_id, review.reviewed_at.isoformat(),
                        review.reviewed_role.value, payload,
                    ),
                )
                inserted += 1
        return inserted

    def accepted_pivot_sources(
        self, assertion_ids: list[str], *, cutoff_at: datetime
    ) -> list[AcceptedPivotSource]:
        """Resolve accepted, cutoff-safe assertion provenance into pivot inputs."""

        cutoff = cutoff_at.astimezone(timezone.utc)
        sources: list[AcceptedPivotSource] = []
        with self.connect() as connection:
            for assertion_id in assertion_ids:
                row = connection.execute(
                    "SELECT a.payload_json, r.payload_json, m.payload_json, i.payload_json "
                    "FROM indicator_assertions a "
                    "JOIN assertion_reviews r ON r.assertion_id = a.assertion_id "
                    "JOIN source_mentions m ON m.mention_id = a.source_mention_id "
                    "JOIN indicators i ON i.indicator_id = a.indicator_id "
                    "WHERE a.assertion_id = ?",
                    (assertion_id,),
                ).fetchone()
                if row is None:
                    raise ValueError(
                        f"assertion is missing accepted review or source mention: {assertion_id}"
                    )
                assertion = IndicatorAssertionRecord.model_validate_json(row[0])
                review = AssertionReviewRecord.model_validate_json(row[1])
                mention = SourceMentionRecord.model_validate_json(row[2])
                indicator = IndicatorRecord.model_validate_json(row[3])
                if review.decision is not ReviewerStatus.ACCEPTED:
                    raise ValueError(f"assertion is not accepted: {assertion_id}")
                if mention.available_at > cutoff or assertion.available_at > cutoff:
                    raise ValueError(f"assertion is available after cutoff: {assertion_id}")
                membership = connection.execute(
                    "SELECT source_family_id FROM source_family_memberships WHERE document_id = ?",
                    (assertion.document_id,),
                ).fetchone()
                if membership is None or membership[0] != mention.source_family_id:
                    raise ValueError(f"assertion source family is not reviewed: {assertion_id}")
                sources.append(AcceptedPivotSource(
                    indicator_id=indicator.indicator_id,
                    assertion_id=assertion.assertion_id,
                    review_id=review.review_id,
                    scope=mention.scope,
                    value=indicator.normalized_value,
                    role=review.reviewed_role,
                    available_at=mention.available_at,
                    source_confidence=review.source_confidence,
                    extraction_confidence=review.extraction_confidence,
                    role_confidence=review.role_confidence,
                ))
        return sources

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
            source_family_id = None
            if document.source_family_id:
                source_family_id = self._require_source_family(
                    connection, document.source_family_id
                )
            if self._assert_same_payload(
                connection, table="source_documents", key_column="document_id",
                key=document.document_id, payload=payload,
            ):
                inserted = False
            else:
                connection.execute(
                    "INSERT INTO source_documents VALUES (?, ?, ?)",
                    (document.document_id, document.content_sha256, payload),
                )
                inserted = True
            if source_family_id:
                self._register_source_family_membership(
                    connection, document.document_id, source_family_id
                )
        return inserted

    def public_source_manifest(self, document_ids: list[str]) -> list[dict[str, Any]]:
        """Return only public source metadata; restricted or malformed rows fail closed."""

        requested = list(dict.fromkeys(document_ids))
        if not requested:
            raise ValueError("at least one document_id is required")
        placeholders = ",".join("?" for _ in requested)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT document_id, payload_json FROM source_documents "
                f"WHERE document_id IN ({placeholders})",
                tuple(requested),
            ).fetchall()
        payloads = {row[0]: row[1] for row in rows}
        missing = [item for item in requested if item not in payloads]
        if missing:
            raise ValueError("source documents not found: " + ", ".join(missing))
        exported: list[dict[str, Any]] = []
        for document_id in requested:
            document = SourceDocumentRecord.model_validate_json(payloads[document_id])
            if document.source_access_class is not SourceAccessClass.PUBLIC:
                raise ValueError(f"restricted source cannot be exported: {document_id}")
            exported.append({
                "document_id": document.document_id,
                "canonical_url": document.canonical_url,
                "publisher": document.publisher,
                "title": document.title,
                "published_at": document.published_at_raw,
                "published_time_precision": document.published_time_precision.value,
                "source_timezone": document.source_timezone,
                "retrieved_at": document.retrieved_at.isoformat(),
                "content_sha256": document.content_sha256,
                "text_content_sha256": document.text_content_sha256,
                "acquisition_mode": document.acquisition_mode.value,
                "source_access_class": document.source_access_class.value,
                "corpus_purpose": document.corpus_purpose.value,
                "search_protocol_id": document.search_protocol_id,
                "source_family_id": document.source_family_id,
            })
        return exported

    def stage0_gate_report(self) -> dict[str, Any]:
        """Audit persisted Stage 0 protocol, provenance, and source-family invariants."""

        issues: list[dict[str, str]] = []
        with self.connect() as connection:
            protocols = connection.execute(
                "SELECT search_protocol_id, payload_json FROM cti_search_protocols"
            ).fetchall()
            documents = connection.execute(
                "SELECT document_id, payload_json FROM source_documents"
            ).fetchall()
            families = connection.execute(
                "SELECT source_family_id, canonical_document_id FROM source_families"
            ).fetchall()
            memberships = connection.execute(
                "SELECT document_id, source_family_id FROM source_family_memberships"
            ).fetchall()
            assertions = connection.execute(
                "SELECT assertion_id, document_id FROM indicator_assertions"
            ).fetchall()
        for key, payload in protocols:
            try:
                SearchProtocolRecord.model_validate_json(payload)
            except Exception as error:
                issues.append({"code": "invalid_search_protocol", "record_id": key,
                               "detail": str(error)})
        valid_documents: dict[str, SourceDocumentRecord] = {}
        for key, payload in documents:
            try:
                valid_documents[key] = SourceDocumentRecord.model_validate_json(payload)
            except Exception as error:
                issues.append({"code": "invalid_source_document", "record_id": key,
                               "detail": str(error)})
        membership_map = {document_id: family_id for document_id, family_id in memberships}
        family_map = {family_id: canonical_id for family_id, canonical_id in families}
        for document_id, document in valid_documents.items():
            if not document.source_family_id:
                issues.append({"code": "missing_source_family", "record_id": document_id,
                               "detail": "source_family_id is absent"})
            elif membership_map.get(document_id) != document.source_family_id:
                issues.append({"code": "invalid_source_family_membership",
                               "record_id": document_id,
                               "detail": "payload and membership do not match"})
        for family_id, canonical_id in family_map.items():
            if canonical_id not in valid_documents:
                issues.append({"code": "missing_canonical_document", "record_id": family_id,
                               "detail": canonical_id})
            elif membership_map.get(canonical_id) != family_id:
                issues.append({"code": "invalid_canonical_membership", "record_id": family_id,
                               "detail": canonical_id})
        for assertion_id, document_id in assertions:
            if document_id not in valid_documents:
                issues.append({"code": "invalid_assertion_document",
                               "record_id": assertion_id, "detail": document_id})
        return {
            "passed": not issues,
            "counts": {
                "search_protocols": len(protocols),
                "source_documents": len(documents),
                "source_families": len(families),
                "source_family_memberships": len(memberships),
                "indicator_assertions": len(assertions),
            },
            "issues": issues,
        }

    def phase_a_gate_report(self) -> dict[str, Any]:
        """Audit Stage 0 plus Stage 1 mention, review, and query provenance gates."""

        report = self.stage0_gate_report()
        issues = list(report["issues"])
        with self.connect() as connection:
            mention_rows = connection.execute(
                "SELECT mention_id, payload_json FROM source_mentions"
            ).fetchall()
            assertion_rows = connection.execute(
                "SELECT assertion_id, payload_json FROM indicator_assertions"
            ).fetchall()
            review_rows = connection.execute(
                "SELECT review_id, payload_json FROM assertion_reviews"
            ).fetchall()
            has_queries = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='query_registry'"
            ).fetchone()
            query_rows = connection.execute(
                "SELECT query_id, query_class, source_indicator_ids_json, "
                "source_assertion_ids_json, source_available_at, registered_at "
                "FROM query_registry"
            ).fetchall() if has_queries else []
        mentions: dict[str, SourceMentionRecord] = {}
        assertions: dict[str, IndicatorAssertionRecord] = {}
        reviews_by_assertion: dict[str, AssertionReviewRecord] = {}
        for key, payload in mention_rows:
            try:
                mentions[key] = SourceMentionRecord.model_validate_json(payload)
            except Exception as error:
                issues.append({"code": "invalid_source_mention", "record_id": key,
                               "detail": str(error)})
        for key, payload in assertion_rows:
            try:
                assertion = IndicatorAssertionRecord.model_validate_json(payload)
                assertions[key] = assertion
                mention = mentions.get(assertion.source_mention_id)
                if mention is None:
                    raise ValueError("source mention is missing")
                if (
                    mention.indicator_id != assertion.indicator_id
                    or mention.document_id != assertion.document_id
                    or mention.available_at != assertion.available_at
                ):
                    raise ValueError("assertion and source mention provenance do not match")
            except Exception as error:
                issues.append({"code": "invalid_indicator_assertion", "record_id": key,
                               "detail": str(error)})
        for key, payload in review_rows:
            try:
                review = AssertionReviewRecord.model_validate_json(payload)
                if review.assertion_id not in assertions:
                    raise ValueError("reviewed assertion is missing or invalid")
                reviews_by_assertion[review.assertion_id] = review
            except Exception as error:
                issues.append({"code": "invalid_assertion_review", "record_id": key,
                               "detail": str(error)})
        for query_id, query_class, indicator_json, assertion_json, available, registered in query_rows:
            if query_class not in {"Q0_SEED", "Q1_DIRECT_PIVOT"}:
                continue
            indicator_ids = json.loads(indicator_json)
            assertion_ids = json.loads(assertion_json)
            if indicator_ids and (not assertion_ids or not available):
                issues.append({"code": "query_lacks_assertion_provenance",
                               "record_id": query_id, "detail": query_class})
                continue
            for assertion_id in assertion_ids:
                review = reviews_by_assertion.get(assertion_id)
                assertion = assertions.get(assertion_id)
                if review is None or review.decision is not ReviewerStatus.ACCEPTED:
                    issues.append({"code": "query_uses_unaccepted_assertion",
                                   "record_id": query_id, "detail": assertion_id})
                if assertion and assertion.available_at > datetime.fromisoformat(registered):
                    issues.append({"code": "query_predates_assertion_availability",
                                   "record_id": query_id, "detail": assertion_id})
        report["counts"].update({
            "source_mentions": len(mention_rows),
            "assertion_reviews": len(review_rows),
            "queries_checked": len(query_rows),
        })
        report["issues"] = issues
        report["passed"] = not issues
        return report
