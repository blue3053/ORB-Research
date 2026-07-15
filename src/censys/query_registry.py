"""Q0∼Q3 query와 실행의 append-oriented SQLite registry.

목적: query 원문·hash·version·cutoff·동결·실행 split을 감사 가능하게 보존한다.
지원 RQ: RQ1∼RQ5, 핵심적으로 RQ4·RQ5.
재사용 원천: ORB_Hunt_v5 censys_queries.csv와 collection log schema를 확장했다.
설계: deterministic query ID, immutable query text, 명시적 상태 전이와 prospective gate를 적용한다.
입력·출력: QueryRecord·QueryExecutionRecord를 SQLite에 저장하고 복원한다.
시간·provenance 통제: prospective-test는 frozen query와 valid_for_test_from 이후 실행만 허용한다.
보안·라이선스: query text는 내부 registry이며 API secret과 raw response를 저장하지 않는다.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from src.censys.query_lifecycle import ensure_execution_allowed, validate_transition
from src.models import (
    DatasetSplit,
    EntityRelationRecord,
    EntityRelationType,
    FingerprintRecord,
    FingerprintType,
    HostObservationRecord,
    QueryClass,
    QueryExecutionRecord,
    QueryRecord,
    QueryStatus,
    ServiceObservationRecord,
)
from src.provenance import sha256_text


SCHEMA = """
CREATE TABLE IF NOT EXISTS query_registry (
  query_id TEXT PRIMARY KEY,
  query_version TEXT NOT NULL,
  query_class TEXT NOT NULL,
  query_text TEXT NOT NULL,
  query_hash TEXT NOT NULL,
  source_indicator_ids_json TEXT NOT NULL,
  source_feature_ids_json TEXT NOT NULL,
  developed_from_split TEXT NOT NULL,
  registered_at TEXT NOT NULL,
  frozen_at TEXT,
  valid_for_test_from TEXT,
  config_hash TEXT NOT NULL,
  status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS query_executions (
  query_run_id TEXT PRIMARY KEY,
  query_id TEXT NOT NULL REFERENCES query_registry(query_id),
  query_hash TEXT NOT NULL,
  cutoff_time TEXT NOT NULL,
  executed_at TEXT NOT NULL,
  dataset_split TEXT NOT NULL,
  result_count INTEGER NOT NULL CHECK(result_count >= 0),
  result_manifest_hash TEXT NOT NULL,
  api_schema_version TEXT NOT NULL,
  credits_or_bytes REAL,
  status TEXT NOT NULL,
  failure_reason TEXT
);
CREATE TABLE IF NOT EXISTS host_observations (
  observation_id TEXT PRIMARY KEY,
  query_run_id TEXT NOT NULL REFERENCES query_executions(query_run_id),
  indicator_id TEXT NOT NULL,
  host_observed INTEGER NOT NULL,
  observed_at TEXT,
  collected_at TEXT NOT NULL,
  raw_record_hash TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS service_observations (
  service_observation_id TEXT PRIMARY KEY,
  observation_id TEXT NOT NULL REFERENCES host_observations(observation_id),
  port INTEGER NOT NULL,
  transport TEXT NOT NULL,
  protocol TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fingerprints (
  fingerprint_id TEXT PRIMARY KEY,
  fingerprint_type TEXT NOT NULL,
  fingerprint_value TEXT NOT NULL,
  extractor_version TEXT NOT NULL,
  first_available_at TEXT NOT NULL,
  sensitivity TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  UNIQUE(fingerprint_type, fingerprint_value)
);
CREATE TABLE IF NOT EXISTS entity_relations (
  relation_id TEXT PRIMARY KEY,
  src_id TEXT NOT NULL,
  dst_id TEXT NOT NULL,
  relation_type TEXT NOT NULL,
  valid_from TEXT NOT NULL,
  valid_to TEXT,
  evidence_source TEXT NOT NULL,
  confidence REAL NOT NULL,
  available_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
"""


class QueryRegistry:
    """query definition과 execution ledger의 저장 API."""

    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """SQLite 연결을 transaction 종료 후 반드시 close한다."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def register_query(
        self,
        *,
        query_version: str,
        query_class: QueryClass,
        query_text: str,
        developed_from_split: DatasetSplit,
        config_hash: str,
        source_indicator_ids: list[str] | None = None,
        source_feature_ids: list[str] | None = None,
        registered_at: datetime | None = None,
    ) -> QueryRecord:
        query_text = query_text.strip()
        if not query_text:
            raise ValueError("query_text cannot be empty")
        query_hash = sha256_text(query_text)
        query_id = f"qry-{sha256_text('|'.join([query_class.value, query_version, query_hash]))[:16]}"
        record = QueryRecord(
            query_id=query_id,
            query_version=query_version,
            query_class=query_class,
            query_text=query_text,
            query_hash=query_hash,
            source_indicator_ids=source_indicator_ids or [],
            source_feature_ids=source_feature_ids or [],
            developed_from_split=developed_from_split,
            registered_at=registered_at or datetime.now(timezone.utc),
            config_hash=config_hash,
        )
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM query_registry WHERE query_id = ?", (query_id,)
            ).fetchone()
            if existing:
                restored = self._row_to_query(existing)
                if restored != record:
                    raise ValueError("query ID collision with different content")
                return restored
            connection.execute(
                "INSERT INTO query_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.query_id, record.query_version, record.query_class.value,
                    record.query_text, record.query_hash,
                    json.dumps(record.source_indicator_ids, sort_keys=True),
                    json.dumps(record.source_feature_ids, sort_keys=True),
                    record.developed_from_split.value, record.registered_at.isoformat(),
                    None, None, record.config_hash, record.status.value,
                ),
            )
        return record

    def mark_validated(self, query_id: str) -> QueryRecord:
        return self._transition(query_id, QueryStatus.VALIDATED)

    def freeze_query(
        self, query_id: str, *, frozen_at: datetime, valid_for_test_from: datetime
    ) -> QueryRecord:
        frozen_at = frozen_at.astimezone(timezone.utc)
        valid_for_test_from = valid_for_test_from.astimezone(timezone.utc)
        if valid_for_test_from < frozen_at:
            raise ValueError("valid_for_test_from cannot predate frozen_at")
        current = self.get_query(query_id)
        validate_transition(current.status, QueryStatus.FROZEN)
        with self.connect() as connection:
            connection.execute(
                "UPDATE query_registry SET status=?, frozen_at=?, valid_for_test_from=? WHERE query_id=?",
                (QueryStatus.FROZEN.value, frozen_at.isoformat(),
                 valid_for_test_from.isoformat(), query_id),
            )
        return self.get_query(query_id)

    def record_execution(self, execution: QueryExecutionRecord) -> None:
        query = self.get_query(execution.query_id)
        if query.query_hash != execution.query_hash:
            raise ValueError("execution query hash does not match frozen registry query")
        ensure_execution_allowed(
            query, execution.dataset_split, execution.executed_at, execution.cutoff_time
        )
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO query_executions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    execution.query_run_id, execution.query_id, execution.query_hash,
                    execution.cutoff_time.isoformat(), execution.executed_at.isoformat(),
                    execution.dataset_split.value, execution.result_count,
                    execution.result_manifest_hash, execution.api_schema_version,
                    execution.credits_or_bytes, execution.status, execution.failure_reason,
                ),
            )

    def get_query(self, query_id: str) -> QueryRecord:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM query_registry WHERE query_id = ?", (query_id,)
            ).fetchone()
        if row is None:
            raise KeyError(query_id)
        return self._row_to_query(row)

    def get_execution(self, query_run_id: str) -> QueryExecutionRecord:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM query_executions WHERE query_run_id = ?", (query_run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(query_run_id)
        return QueryExecutionRecord(
            query_run_id=row["query_run_id"], query_id=row["query_id"],
            query_hash=row["query_hash"], cutoff_time=datetime.fromisoformat(row["cutoff_time"]),
            executed_at=datetime.fromisoformat(row["executed_at"]),
            dataset_split=DatasetSplit(row["dataset_split"]), result_count=row["result_count"],
            result_manifest_hash=row["result_manifest_hash"],
            api_schema_version=row["api_schema_version"], credits_or_bytes=row["credits_or_bytes"],
            status=row["status"], failure_reason=row["failure_reason"],
        )

    def register_observations(
        self,
        hosts: list[HostObservationRecord],
        services: list[ServiceObservationRecord],
    ) -> dict[str, int]:
        """정규화된 host/service observation을 한 transaction에서 멱등 등록한다."""

        host_ids = {record.observation_id for record in hosts}
        if any(record.observation_id not in host_ids for record in services):
            raise ValueError("service observation references host outside batch")
        counts = {"hosts_inserted": 0, "services_inserted": 0}
        with self.connect() as connection:
            for record in hosts:
                payload = json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
                row = connection.execute(
                    "SELECT payload_json FROM host_observations WHERE observation_id = ?",
                    (record.observation_id,),
                ).fetchone()
                if row:
                    if row[0] != payload:
                        raise ValueError(f"host observation immutable ID collision: {record.observation_id}")
                    continue
                connection.execute(
                    "INSERT INTO host_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.observation_id, record.query_run_id, record.indicator_id,
                        int(record.host_observed),
                        record.observed_at.isoformat() if record.observed_at else None,
                        record.collected_at.isoformat(), record.raw_record_hash, payload,
                    ),
                )
                counts["hosts_inserted"] += 1
            for record in services:
                payload = json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
                row = connection.execute(
                    "SELECT payload_json FROM service_observations WHERE service_observation_id = ?",
                    (record.service_observation_id,),
                ).fetchone()
                if row:
                    if row[0] != payload:
                        raise ValueError(
                            f"service observation immutable ID collision: {record.service_observation_id}"
                        )
                    continue
                connection.execute(
                    "INSERT INTO service_observations VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        record.service_observation_id, record.observation_id, record.port,
                        record.transport.value, record.protocol, payload,
                    ),
                )
                counts["services_inserted"] += 1
        return counts

    def load_observations(
        self, query_run_id: str
    ) -> tuple[list[HostObservationRecord], list[ServiceObservationRecord]]:
        """특정 query run의 정규화 observation payload를 복원한다."""

        with self.connect() as connection:
            host_rows = connection.execute(
                "SELECT payload_json FROM host_observations WHERE query_run_id = ? ORDER BY observation_id",
                (query_run_id,),
            ).fetchall()
            service_rows = connection.execute(
                "SELECT s.payload_json FROM service_observations s "
                "JOIN host_observations h ON h.observation_id=s.observation_id "
                "WHERE h.query_run_id = ? ORDER BY s.service_observation_id",
                (query_run_id,),
            ).fetchall()
        return (
            [HostObservationRecord.model_validate_json(row[0]) for row in host_rows],
            [ServiceObservationRecord.model_validate_json(row[0]) for row in service_rows],
        )

    @staticmethod
    def _immutable_payload(record) -> str:
        return json.dumps(
            record.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )

    def register_fingerprint_graph(
        self,
        fingerprints: list[FingerprintRecord],
        relations: list[EntityRelationRecord],
    ) -> dict[str, int]:
        """fingerprint와 direct relation을 멱등 등록하고 earlier backfill은 거부한다."""

        counts = {"fingerprints_inserted": 0, "relations_inserted": 0}
        with self.connect() as connection:
            for record in fingerprints:
                payload = self._immutable_payload(record)
                row = connection.execute(
                    "SELECT payload_json, first_available_at FROM fingerprints WHERE fingerprint_id = ?",
                    (record.fingerprint_id,),
                ).fetchone()
                if row:
                    existing = FingerprintRecord.model_validate_json(row["payload_json"])
                    if (
                        existing.fingerprint_type != record.fingerprint_type
                        or existing.fingerprint_value != record.fingerprint_value
                        or existing.extractor_version != record.extractor_version
                        or existing.sensitivity != record.sensitivity
                    ):
                        raise ValueError(f"fingerprint immutable ID collision: {record.fingerprint_id}")
                    if record.first_available_at < existing.first_available_at:
                        raise ValueError("fingerprint backfill predates registered first_available_at")
                    continue
                connection.execute(
                    "INSERT INTO fingerprints VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.fingerprint_id, record.fingerprint_type.value,
                        record.fingerprint_value, record.extractor_version,
                        record.first_available_at.isoformat(), record.sensitivity.value, payload,
                    ),
                )
                counts["fingerprints_inserted"] += 1
            for record in relations:
                if record.available_at < record.valid_from:
                    raise ValueError("relation available_at cannot predate valid_from")
                payload = self._immutable_payload(record)
                row = connection.execute(
                    "SELECT payload_json FROM entity_relations WHERE relation_id = ?",
                    (record.relation_id,),
                ).fetchone()
                if row:
                    if row[0] != payload:
                        raise ValueError(f"relation immutable ID collision: {record.relation_id}")
                    continue
                connection.execute(
                    "INSERT INTO entity_relations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.relation_id, record.src_id, record.dst_id,
                        record.relation_type.value, record.valid_from.isoformat(),
                        record.valid_to.isoformat() if record.valid_to else None,
                        record.evidence_source, record.confidence,
                        record.available_at.isoformat(), payload,
                    ),
                )
                counts["relations_inserted"] += 1
        return counts

    def build_shared_fingerprint_relations(
        self, fingerprint_ids: list[str]
    ) -> list[EntityRelationRecord]:
        """같은 fingerprint가 직접 관측된 서로 다른 indicator 쌍을 관계로 만든다."""

        if not fingerprint_ids:
            return []
        placeholders = ",".join("?" for _ in fingerprint_ids)
        with self.connect() as connection:
            fp_rows = connection.execute(
                f"SELECT fingerprint_id, fingerprint_type FROM fingerprints "
                f"WHERE fingerprint_id IN ({placeholders})", fingerprint_ids,
            ).fetchall()
            relation_rows = connection.execute(
                f"SELECT payload_json FROM entity_relations WHERE relation_type=? "
                f"AND dst_id IN ({placeholders}) AND src_id LIKE 'ioc-%'",
                [EntityRelationType.OBSERVED_WITH.value, *fingerprint_ids],
            ).fetchall()
        types = {row["fingerprint_id"]: FingerprintType(row["fingerprint_type"]) for row in fp_rows}
        memberships: dict[str, list[EntityRelationRecord]] = {}
        for row in relation_rows:
            relation = EntityRelationRecord.model_validate_json(row["payload_json"])
            memberships.setdefault(relation.dst_id, []).append(relation)
        relation_type_map = {
            FingerprintType.CERT: EntityRelationType.SHARES_CERT,
            FingerprintType.SPKI: EntityRelationType.SHARES_SPKI,
            FingerprintType.JARM: EntityRelationType.SHARES_JARM,
            FingerprintType.BANNER: EntityRelationType.SHARES_BANNER,
        }
        output: list[EntityRelationRecord] = []
        for fingerprint_id, members in memberships.items():
            relation_type = relation_type_map.get(types.get(fingerprint_id))
            if relation_type is None:
                continue
            by_indicator: dict[str, EntityRelationRecord] = {}
            for member in members:
                existing = by_indicator.get(member.src_id)
                member_order = (
                    member.available_at, member.valid_from, member.relation_id
                )
                if existing is None or member_order < (
                    existing.available_at, existing.valid_from, existing.relation_id
                ):
                    by_indicator[member.src_id] = member
            ids = sorted(by_indicator)
            for left_index, left in enumerate(ids):
                for right in ids[left_index + 1:]:
                    available = max(
                        by_indicator[left].available_at, by_indicator[right].available_at
                    )
                    valid_from = max(
                        by_indicator[left].valid_from, by_indicator[right].valid_from
                    )
                    material = f"{left}|{right}|{relation_type.value}|{fingerprint_id}"
                    output.append(EntityRelationRecord(
                        relation_id=f"rel-{sha256_text(material)[:20]}",
                        src_id=left, dst_id=right, relation_type=relation_type,
                        valid_from=valid_from,
                        evidence_source=f"fingerprint:{fingerprint_id}",
                        confidence=1.0, available_at=available,
                    ))
        return output

    def _transition(self, query_id: str, target: QueryStatus) -> QueryRecord:
        current = self.get_query(query_id)
        validate_transition(current.status, target)
        with self.connect() as connection:
            connection.execute(
                "UPDATE query_registry SET status=? WHERE query_id=?",
                (target.value, query_id),
            )
        return self.get_query(query_id)

    @staticmethod
    def _row_to_query(row: sqlite3.Row) -> QueryRecord:
        return QueryRecord(
            query_id=row["query_id"], query_version=row["query_version"],
            query_class=QueryClass(row["query_class"]), query_text=row["query_text"],
            query_hash=row["query_hash"],
            source_indicator_ids=json.loads(row["source_indicator_ids_json"]),
            source_feature_ids=json.loads(row["source_feature_ids_json"]),
            developed_from_split=DatasetSplit(row["developed_from_split"]),
            registered_at=datetime.fromisoformat(row["registered_at"]),
            frozen_at=datetime.fromisoformat(row["frozen_at"]) if row["frozen_at"] else None,
            valid_for_test_from=(datetime.fromisoformat(row["valid_for_test_from"])
                                 if row["valid_for_test_from"] else None),
            config_hash=row["config_hash"], status=QueryStatus(row["status"]),
        )
