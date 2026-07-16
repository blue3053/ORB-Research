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
    ContinuityAssessmentRecord,
    ContinuityReviewRecord,
    CtiCompositeRecord,
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
    PivotEligibilityReviewRecord,
    PivotPrecheckRecord,
    PivotPrecheckResultRecord,
    PrecheckStatus,
    Q0LandmarkRecord,
    Q0TimelineEntryRecord,
    ServiceObservationRecord,
)
from src.provenance import sha256_text


SCHEMA = """
CREATE TABLE IF NOT EXISTS query_registry (
  query_id TEXT PRIMARY KEY,
  query_version TEXT NOT NULL,
  query_variant TEXT NOT NULL DEFAULT 'primary',
  query_class TEXT NOT NULL,
  query_text TEXT NOT NULL,
  query_hash TEXT NOT NULL,
  source_indicator_ids_json TEXT NOT NULL,
  source_assertion_ids_json TEXT NOT NULL DEFAULT '[]',
  source_available_at TEXT,
  source_feature_ids_json TEXT NOT NULL,
  source_precheck_ids_json TEXT NOT NULL DEFAULT '[]',
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
CREATE TABLE IF NOT EXISTS query_execution_events (
  event_id TEXT PRIMARY KEY,
  query_run_id TEXT NOT NULL REFERENCES query_executions(query_run_id),
  status TEXT NOT NULL,
  result_count INTEGER NOT NULL,
  result_manifest_hash TEXT NOT NULL,
  payload_json TEXT NOT NULL
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
CREATE TABLE IF NOT EXISTS q0_landmarks (
  landmark_id TEXT PRIMARY KEY,
  indicator_id TEXT NOT NULL,
  assertion_id TEXT NOT NULL,
  query_id TEXT NOT NULL REFERENCES query_registry(query_id),
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS q0_timeline_entries (
  timeline_entry_id TEXT PRIMARY KEY,
  landmark_id TEXT NOT NULL REFERENCES q0_landmarks(landmark_id),
  observation_id TEXT NOT NULL REFERENCES host_observations(observation_id),
  collected_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS continuity_assessments (
  assessment_id TEXT PRIMARY KEY,
  landmark_id TEXT NOT NULL REFERENCES q0_landmarks(landmark_id),
  status TEXT NOT NULL,
  assessed_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS continuity_reviews (
  review_id TEXT PRIMARY KEY,
  assessment_id TEXT NOT NULL UNIQUE REFERENCES continuity_assessments(assessment_id),
  decision TEXT NOT NULL,
  reviewed_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cti_composites (
  composite_id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL,
  available_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pivot_prechecks (
  precheck_id TEXT PRIMARY KEY,
  query_id TEXT NOT NULL REFERENCES query_registry(query_id),
  node_id TEXT NOT NULL,
  cutoff_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pivot_precheck_results (
  result_id TEXT PRIMARY KEY,
  precheck_id TEXT NOT NULL REFERENCES pivot_prechecks(precheck_id),
  status TEXT NOT NULL,
  page_count INTEGER NOT NULL,
  hit_count INTEGER NOT NULL,
  recorded_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pivot_eligibility_reviews (
  review_id TEXT PRIMARY KEY,
  precheck_id TEXT NOT NULL UNIQUE REFERENCES pivot_prechecks(precheck_id),
  decision TEXT NOT NULL,
  reviewed_at TEXT NOT NULL,
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
        self._migrate_phase_a_columns(connection)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _migrate_phase_a_columns(connection: sqlite3.Connection) -> None:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(query_registry)")}
        if "source_assertion_ids_json" not in columns:
            connection.execute(
                "ALTER TABLE query_registry ADD COLUMN source_assertion_ids_json "
                "TEXT NOT NULL DEFAULT '[]'"
            )
        if "source_available_at" not in columns:
            connection.execute(
                "ALTER TABLE query_registry ADD COLUMN source_available_at TEXT"
            )
        if "source_precheck_ids_json" not in columns:
            connection.execute(
                "ALTER TABLE query_registry ADD COLUMN source_precheck_ids_json "
                "TEXT NOT NULL DEFAULT '[]'"
            )
        if "query_variant" not in columns:
            connection.execute(
                "ALTER TABLE query_registry ADD COLUMN query_variant "
                "TEXT NOT NULL DEFAULT 'primary'"
            )

    def register_query(
        self,
        *,
        query_version: str,
        query_variant: str = "primary",
        query_class: QueryClass,
        query_text: str,
        developed_from_split: DatasetSplit,
        config_hash: str,
        source_indicator_ids: list[str] | None = None,
        source_assertion_ids: list[str] | None = None,
        source_available_at: datetime | None = None,
        source_feature_ids: list[str] | None = None,
        source_precheck_ids: list[str] | None = None,
        registered_at: datetime | None = None,
    ) -> QueryRecord:
        query_text = query_text.strip()
        if not query_text:
            raise ValueError("query_text cannot be empty")
        query_hash = sha256_text(query_text)
        query_id = f"qry-{sha256_text('|'.join([
            query_class.value, query_version, query_variant, query_hash
        ]))[:16]}"
        record = QueryRecord(
            query_id=query_id,
            query_version=query_version,
            query_variant=query_variant,
            query_class=query_class,
            query_text=query_text,
            query_hash=query_hash,
            source_indicator_ids=source_indicator_ids or [],
            source_assertion_ids=source_assertion_ids or [],
            source_available_at=source_available_at,
            source_feature_ids=source_feature_ids or [],
            source_precheck_ids=source_precheck_ids or [],
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
                "INSERT INTO query_registry "
                "(query_id, query_version, query_class, query_text, query_hash, "
                "query_variant, "
                "source_indicator_ids_json, source_assertion_ids_json, source_available_at, "
                "source_feature_ids_json, source_precheck_ids_json, developed_from_split, registered_at, frozen_at, "
                "valid_for_test_from, config_hash, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.query_id, record.query_version, record.query_class.value,
                    record.query_text, record.query_hash, record.query_variant,
                    json.dumps(record.source_indicator_ids, sort_keys=True),
                    json.dumps(record.source_assertion_ids, sort_keys=True),
                    (record.source_available_at.isoformat()
                     if record.source_available_at else None),
                    json.dumps(record.source_feature_ids, sort_keys=True),
                    json.dumps(record.source_precheck_ids, sort_keys=True),
                    record.developed_from_split.value, record.registered_at.isoformat(),
                    None, None, record.config_hash, record.status.value,
                ),
            )
        return record

    def register_q2_from_prechecks(
        self,
        *,
        query_version: str,
        query_variant: str = "primary",
        query_text: str,
        precheck_ids: list[str],
        config_hash: str,
        registered_at: datetime | None = None,
    ) -> QueryRecord:
        """Register Q2 only from complete, reviewed, eligible Q1 prechecks."""

        unique_ids = sorted(set(precheck_ids))
        if not unique_ids:
            raise ValueError("Q2 requires at least one eligible precheck")
        eligible = set(self.eligible_q2_precheck_ids())
        blocked = [item for item in unique_ids if item not in eligible]
        if blocked:
            raise ValueError("Q2 source prechecks are not eligible: " + ", ".join(blocked))
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM pivot_prechecks WHERE precheck_id IN ("
                + ",".join("?" for _ in unique_ids) + ")",
                unique_ids,
            ).fetchall()
        prechecks = [PivotPrecheckRecord.model_validate_json(row[0]) for row in rows]
        source_available_at = max(item.source_available_at for item in prechecks)
        return self.register_query(
            query_version=query_version,
            query_variant=query_variant,
            query_class=QueryClass.Q2_DERIVED,
            query_text=query_text,
            developed_from_split=DatasetSplit.DEVELOPMENT,
            config_hash=config_hash,
            source_assertion_ids=sorted({value for item in prechecks for value in item.assertion_ids}),
            source_indicator_ids=[],
            source_available_at=source_available_at,
            source_precheck_ids=unique_ids,
            registered_at=registered_at,
        )

    def mark_validated(self, query_id: str) -> QueryRecord:
        self._require_cti_assertion_provenance(self.get_query(query_id))
        return self._transition(query_id, QueryStatus.VALIDATED)

    @staticmethod
    def _require_cti_assertion_provenance(query: QueryRecord) -> None:
        if query.query_class not in {QueryClass.Q0_SEED, QueryClass.Q1_DIRECT_PIVOT}:
            return
        if query.source_indicator_ids and (
            not query.source_assertion_ids or query.source_available_at is None
        ):
            raise ValueError("CTI-derived query lacks accepted assertion provenance")

    def freeze_query(
        self, query_id: str, *, frozen_at: datetime, valid_for_test_from: datetime
    ) -> QueryRecord:
        frozen_at = frozen_at.astimezone(timezone.utc)
        valid_for_test_from = valid_for_test_from.astimezone(timezone.utc)
        if valid_for_test_from < frozen_at:
            raise ValueError("valid_for_test_from cannot predate frozen_at")
        current = self.get_query(query_id)
        self._require_cti_assertion_provenance(current)
        if current.query_class in {QueryClass.Q2_DERIVED, QueryClass.Q3_CLUSTER}:
            from src.censys.query_freeze import QueryDesignRegistry

            QueryDesignRegistry(self.path).assert_freeze_ready(
                query_id, frozen_at=frozen_at, valid_for_test_from=valid_for_test_from
            )
        if current.source_available_at and current.source_available_at > frozen_at:
            raise ValueError("query source was not available by frozen_at")
        validate_transition(current.status, QueryStatus.FROZEN)
        with self.connect() as connection:
            connection.execute(
                "UPDATE query_registry SET status=?, frozen_at=?, valid_for_test_from=? WHERE query_id=?",
                (QueryStatus.FROZEN.value, frozen_at.isoformat(),
                 valid_for_test_from.isoformat(), query_id),
            )
        return self.get_query(query_id)

    def record_execution(self, execution: QueryExecutionRecord) -> bool:
        query = self.get_query(execution.query_id)
        if query.query_hash != execution.query_hash:
            raise ValueError("execution query hash does not match frozen registry query")
        ensure_execution_allowed(
            query, execution.dataset_split, execution.executed_at, execution.cutoff_time
        )
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM query_executions WHERE query_run_id = ?",
                (execution.query_run_id,),
            ).fetchone()
            if existing is None:
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
                changed = True
            else:
                immutable = {
                    "query_id": execution.query_id,
                    "query_hash": execution.query_hash,
                    "cutoff_time": execution.cutoff_time.isoformat(),
                    "executed_at": execution.executed_at.isoformat(),
                    "dataset_split": execution.dataset_split.value,
                    "api_schema_version": execution.api_schema_version,
                }
                mismatched = [
                    name for name, value in immutable.items() if existing[name] != value
                ]
                if mismatched:
                    raise ValueError(
                        "query execution immutable fields changed: " + ", ".join(mismatched)
                    )
                same = (
                    existing["result_count"] == execution.result_count
                    and existing["result_manifest_hash"] == execution.result_manifest_hash
                    and existing["status"] == execution.status
                    and existing["failure_reason"] == execution.failure_reason
                )
                if same:
                    return False
                if existing["status"] != "partial_max_pages":
                    raise ValueError("only partial_max_pages execution can be resumed")
                if execution.status not in {"partial_max_pages", "complete"}:
                    raise ValueError("partial execution has invalid resume transition")
                if execution.result_count < existing["result_count"]:
                    raise ValueError("resumed execution result_count cannot decrease")
                connection.execute(
                    "UPDATE query_executions SET result_count=?, result_manifest_hash=?, "
                    "status=?, failure_reason=? WHERE query_run_id=?",
                    (
                        execution.result_count, execution.result_manifest_hash,
                        execution.status, execution.failure_reason, execution.query_run_id,
                    ),
                )
                changed = True
            event_payload = json.dumps(
                execution.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            )
            event_id = "execution-event-" + sha256_text(
                execution.query_run_id + "|" + execution.status + "|"
                + execution.result_manifest_hash
            )[:20]
            connection.execute(
                "INSERT OR IGNORE INTO query_execution_events VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event_id, execution.query_run_id, execution.status,
                    execution.result_count, execution.result_manifest_hash, event_payload,
                ),
            )
        return changed

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

    def load_indicator_observations(
        self, indicator_id: str
    ) -> tuple[list[HostObservationRecord], list[ServiceObservationRecord]]:
        """Load all append-only observations for one indicator across query runs."""

        with self.connect() as connection:
            host_rows = connection.execute(
                "SELECT payload_json FROM host_observations WHERE indicator_id=? "
                "ORDER BY collected_at, observation_id", (indicator_id,),
            ).fetchall()
            service_rows = connection.execute(
                "SELECT s.payload_json FROM service_observations s "
                "JOIN host_observations h ON h.observation_id=s.observation_id "
                "WHERE h.indicator_id=? ORDER BY s.service_observation_id", (indicator_id,),
            ).fetchall()
        return (
            [HostObservationRecord.model_validate_json(row[0]) for row in host_rows],
            [ServiceObservationRecord.model_validate_json(row[0]) for row in service_rows],
        )

    def get_pivot_precheck(self, precheck_id: str) -> PivotPrecheckRecord:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM pivot_prechecks WHERE precheck_id=?", (precheck_id,)
            ).fetchone()
        if row is None:
            raise KeyError(precheck_id)
        return PivotPrecheckRecord.model_validate_json(row[0])

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

    @staticmethod
    def _model_payload(record) -> str:
        return json.dumps(
            record.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )

    def register_q0_landmark(self, landmark: Q0LandmarkRecord) -> bool:
        query = self.get_query(landmark.query_id)
        if query.query_class is not QueryClass.Q0_SEED:
            raise ValueError("Q0 landmark requires a Q0 seed query")
        if landmark.indicator_id not in query.source_indicator_ids:
            raise ValueError("landmark indicator is not query provenance")
        if landmark.assertion_id not in query.source_assertion_ids:
            raise ValueError("landmark assertion is not query provenance")
        payload = self._model_payload(landmark)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM q0_landmarks WHERE landmark_id=?",
                (landmark.landmark_id,),
            ).fetchone()
            if row:
                if row[0] != payload:
                    raise ValueError("Q0 landmark immutable ID collision")
                return False
            connection.execute(
                "INSERT INTO q0_landmarks VALUES (?, ?, ?, ?, ?)",
                (landmark.landmark_id, landmark.indicator_id, landmark.assertion_id,
                 landmark.query_id, payload),
            )
        return True

    def register_q0_timeline(self, entries: list[Q0TimelineEntryRecord]) -> int:
        inserted = 0
        with self.connect() as connection:
            for entry in entries:
                landmark = connection.execute(
                    "SELECT indicator_id FROM q0_landmarks WHERE landmark_id=?", (entry.landmark_id,)
                ).fetchone()
                observation = connection.execute(
                    "SELECT indicator_id FROM host_observations WHERE observation_id=?",
                    (entry.observation_id,),
                ).fetchone()
                if landmark is None or observation is None:
                    raise ValueError("timeline entry lacks landmark or raw observation")
                if landmark[0] != observation[0]:
                    raise ValueError("timeline entry observation does not match landmark indicator")
                payload = self._model_payload(entry)
                row = connection.execute(
                    "SELECT payload_json FROM q0_timeline_entries WHERE timeline_entry_id=?",
                    (entry.timeline_entry_id,),
                ).fetchone()
                if row:
                    if row[0] != payload:
                        raise ValueError("timeline entry immutable ID collision")
                    continue
                connection.execute(
                    "INSERT INTO q0_timeline_entries VALUES (?, ?, ?, ?, ?)",
                    (entry.timeline_entry_id, entry.landmark_id, entry.observation_id,
                     entry.collected_at.isoformat(), payload),
                )
                inserted += 1
        return inserted

    def register_continuity_assessment(
        self, assessment: ContinuityAssessmentRecord
    ) -> bool:
        payload = self._model_payload(assessment)
        with self.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM q0_landmarks WHERE landmark_id=?", (assessment.landmark_id,)
            ).fetchone() is None:
                raise ValueError("continuity assessment lacks Q0 landmark")
            known = {
                row[0] for row in connection.execute(
                    "SELECT observation_id FROM q0_timeline_entries WHERE landmark_id=?",
                    (assessment.landmark_id,),
                )
            }
            if not set(assessment.evidence_observation_ids) <= known:
                raise ValueError("continuity assessment references unknown timeline evidence")
            row = connection.execute(
                "SELECT payload_json FROM continuity_assessments WHERE assessment_id=?",
                (assessment.assessment_id,),
            ).fetchone()
            if row:
                if row[0] != payload:
                    raise ValueError("continuity assessment immutable ID collision")
                return False
            connection.execute(
                "INSERT INTO continuity_assessments VALUES (?, ?, ?, ?, ?)",
                (assessment.assessment_id, assessment.landmark_id,
                 assessment.status.value, assessment.assessed_at.isoformat(), payload),
            )
        return True

    def register_continuity_review(self, review: ContinuityReviewRecord) -> bool:
        payload = self._model_payload(review)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM continuity_assessments WHERE assessment_id=?",
                (review.assessment_id,),
            ).fetchone()
            if row is None:
                raise ValueError("continuity review references unknown assessment")
            assessment = ContinuityAssessmentRecord.model_validate_json(row[0])
            if review.reviewed_at < assessment.assessed_at:
                raise ValueError("continuity review predates assessment")
            if review.allow_probable and assessment.status.value != "probable":
                raise ValueError("allow_probable applies only to probable continuity")
            existing = connection.execute(
                "SELECT payload_json FROM continuity_reviews WHERE assessment_id=?",
                (review.assessment_id,),
            ).fetchone()
            if existing:
                if existing[0] != payload:
                    raise ValueError("continuity assessment already has immutable review")
                return False
            connection.execute(
                "INSERT INTO continuity_reviews VALUES (?, ?, ?, ?, ?)",
                (review.review_id, review.assessment_id, review.decision.value,
                 review.reviewed_at.isoformat(), payload),
            )
        return True

    def derived_continuity_assessment_ids(self) -> list[str]:
        from src.censys.continuity import derived_pivot_allowed

        allowed: list[str] = []
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT a.payload_json, r.payload_json FROM continuity_assessments a "
                "LEFT JOIN continuity_reviews r ON r.assessment_id=a.assessment_id"
            ).fetchall()
        for assessment_payload, review_payload in rows:
            assessment = ContinuityAssessmentRecord.model_validate_json(assessment_payload)
            review = (ContinuityReviewRecord.model_validate_json(review_payload)
                      if review_payload else None)
            if derived_pivot_allowed(assessment, review):
                allowed.append(assessment.assessment_id)
        return sorted(allowed)

    def register_cti_composite(self, composite: CtiCompositeRecord) -> bool:
        payload = self._model_payload(composite)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM cti_composites WHERE composite_id=?",
                (composite.composite_id,),
            ).fetchone()
            if row:
                if row[0] != payload:
                    raise ValueError("CTI composite immutable ID collision")
                return False
            connection.execute(
                "INSERT INTO cti_composites VALUES (?, ?, ?, ?)",
                (composite.composite_id, composite.node_id,
                 composite.available_at.isoformat(), payload),
            )
        return True

    def register_pivot_precheck(self, precheck: PivotPrecheckRecord) -> bool:
        query = self.get_query(precheck.query_id)
        if query.query_class is not QueryClass.Q1_DIRECT_PIVOT:
            raise ValueError("pivot precheck requires Q1 query")
        if query.query_hash != precheck.query_hash:
            raise ValueError("pivot precheck query hash mismatch")
        if not set(precheck.assertion_ids) <= set(query.source_assertion_ids):
            raise ValueError("pivot precheck assertions are not query provenance")
        payload = self._model_payload(precheck)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM pivot_prechecks WHERE precheck_id=?",
                (precheck.precheck_id,),
            ).fetchone()
            if row:
                if row[0] != payload:
                    raise ValueError("pivot precheck immutable ID collision")
                return False
            connection.execute(
                "INSERT INTO pivot_prechecks VALUES (?, ?, ?, ?, ?)",
                (precheck.precheck_id, precheck.query_id, precheck.node_id,
                 precheck.cutoff_at.isoformat(), payload),
            )
        return True

    def register_pivot_precheck_result(
        self, result: PivotPrecheckResultRecord
    ) -> bool:
        payload = self._model_payload(result)
        with self.connect() as connection:
            precheck_row = connection.execute(
                "SELECT payload_json FROM pivot_prechecks WHERE precheck_id=?", (result.precheck_id,)
            ).fetchone()
            if precheck_row is None:
                raise ValueError("precheck result references unknown definition")
            if result.status in {PrecheckStatus.COMPLETE, PrecheckStatus.PARTIAL_MAX_PAGES}:
                precheck = PivotPrecheckRecord.model_validate_json(precheck_row[0])
                execution = connection.execute(
                    "SELECT * FROM query_executions WHERE query_run_id=?",
                    (result.collection_run_id,),
                ).fetchone()
                if execution is None:
                    raise ValueError("precheck result lacks collection execution provenance")
                if (
                    execution["query_id"] != precheck.query_id
                    or execution["cutoff_time"] != precheck.cutoff_at.isoformat()
                    or execution["status"] != result.status.value
                    or execution["result_count"] != result.hit_count
                    or execution["result_manifest_hash"] != result.raw_manifest_hash
                ):
                    raise ValueError("precheck result does not match collection execution")
            existing = connection.execute(
                "SELECT payload_json FROM pivot_precheck_results WHERE result_id=?",
                (result.result_id,),
            ).fetchone()
            if existing:
                if existing[0] != payload:
                    raise ValueError("precheck result immutable ID collision")
                return False
            previous = connection.execute(
                "SELECT payload_json FROM pivot_precheck_results WHERE precheck_id=? "
                "ORDER BY recorded_at DESC LIMIT 1", (result.precheck_id,),
            ).fetchone()
            if previous:
                prior = PivotPrecheckResultRecord.model_validate_json(previous[0])
                if prior.status is PrecheckStatus.COMPLETE:
                    raise ValueError("complete precheck is terminal")
                if result.page_count < prior.page_count or result.hit_count < prior.hit_count:
                    raise ValueError("resumed precheck counters cannot decrease")
                if result.recorded_at < prior.recorded_at:
                    raise ValueError("precheck result history is out of order")
            connection.execute(
                "INSERT INTO pivot_precheck_results VALUES (?, ?, ?, ?, ?, ?, ?)",
                (result.result_id, result.precheck_id, result.status.value,
                 result.page_count, result.hit_count, result.recorded_at.isoformat(), payload),
            )
        return True

    def register_pivot_eligibility_review(
        self, review: PivotEligibilityReviewRecord
    ) -> bool:
        from src.cti.pivot_precheck import precheck_eligibility

        payload = self._model_payload(review)
        with self.connect() as connection:
            precheck_row = connection.execute(
                "SELECT payload_json FROM pivot_prechecks WHERE precheck_id=?",
                (review.precheck_id,),
            ).fetchone()
            result_row = connection.execute(
                "SELECT payload_json FROM pivot_precheck_results WHERE precheck_id=? "
                "ORDER BY recorded_at DESC LIMIT 1", (review.precheck_id,),
            ).fetchone()
            if precheck_row is None or result_row is None:
                raise ValueError("eligibility review lacks precheck result")
            precheck = PivotPrecheckRecord.model_validate_json(precheck_row[0])
            result = PivotPrecheckResultRecord.model_validate_json(result_row[0])
            if review.reviewed_at < result.recorded_at:
                raise ValueError("eligibility review predates precheck result")
            if review.decision.value == "accepted":
                eligible, reason = precheck_eligibility(precheck, result, review)
                if not eligible:
                    raise ValueError("accepted eligibility review is invalid: " + reason)
            existing = connection.execute(
                "SELECT payload_json FROM pivot_eligibility_reviews WHERE precheck_id=?",
                (review.precheck_id,),
            ).fetchone()
            if existing:
                if existing[0] != payload:
                    raise ValueError("precheck already has immutable eligibility review")
                return False
            connection.execute(
                "INSERT INTO pivot_eligibility_reviews VALUES (?, ?, ?, ?, ?)",
                (review.review_id, review.precheck_id, review.decision.value,
                 review.reviewed_at.isoformat(), payload),
            )
        return True

    def eligible_q2_precheck_ids(self) -> list[str]:
        from src.cti.pivot_precheck import precheck_eligibility

        output: list[str] = []
        with self.connect() as connection:
            prechecks = connection.execute(
                "SELECT precheck_id, payload_json FROM pivot_prechecks"
            ).fetchall()
            for precheck_id, payload in prechecks:
                result_row = connection.execute(
                    "SELECT payload_json FROM pivot_precheck_results WHERE precheck_id=? "
                    "ORDER BY recorded_at DESC LIMIT 1", (precheck_id,),
                ).fetchone()
                review_row = connection.execute(
                    "SELECT payload_json FROM pivot_eligibility_reviews WHERE precheck_id=?",
                    (precheck_id,),
                ).fetchone()
                if result_row is None:
                    continue
                precheck = PivotPrecheckRecord.model_validate_json(payload)
                result = PivotPrecheckResultRecord.model_validate_json(result_row[0])
                review = (PivotEligibilityReviewRecord.model_validate_json(review_row[0])
                          if review_row else None)
                if precheck_eligibility(precheck, result, review)[0]:
                    output.append(precheck_id)
        return sorted(output)

    def phase_b_gate_report(self) -> dict:
        """Audit persisted Phase B timelines, state histories, and eligibility gates."""

        issues: list[dict[str, str]] = []
        specs = (
            ("q0_landmarks", "landmark_id", Q0LandmarkRecord),
            ("q0_timeline_entries", "timeline_entry_id", Q0TimelineEntryRecord),
            ("continuity_assessments", "assessment_id", ContinuityAssessmentRecord),
            ("continuity_reviews", "review_id", ContinuityReviewRecord),
            ("cti_composites", "composite_id", CtiCompositeRecord),
            ("pivot_prechecks", "precheck_id", PivotPrecheckRecord),
            ("pivot_precheck_results", "result_id", PivotPrecheckResultRecord),
            ("pivot_eligibility_reviews", "review_id", PivotEligibilityReviewRecord),
        )
        counts: dict[str, int] = {}
        with self.connect() as connection:
            for table, key_column, model in specs:
                rows = connection.execute(
                    f"SELECT {key_column}, payload_json FROM {table}"
                ).fetchall()
                counts[table] = len(rows)
                for key, payload in rows:
                    try:
                        model.model_validate_json(payload)
                    except Exception as error:
                        issues.append({
                            "code": f"invalid_{table}", "record_id": key,
                            "detail": str(error),
                        })
            q0_queries = connection.execute(
                "SELECT query_id FROM query_registry WHERE query_class='Q0_SEED'"
            ).fetchall()
            for (query_id,) in q0_queries:
                if connection.execute(
                    "SELECT 1 FROM q0_landmarks WHERE query_id=?", (query_id,)
                ).fetchone() is None:
                    issues.append({"code": "q0_query_lacks_landmark",
                                   "record_id": query_id, "detail": "Q0_SEED"})
            accepted_prechecks = connection.execute(
                "SELECT precheck_id FROM pivot_eligibility_reviews WHERE decision='accepted'"
            ).fetchall()
            q2_rows = connection.execute(
                "SELECT query_id, source_precheck_ids_json FROM query_registry "
                "WHERE query_class='Q2_DERIVED'"
            ).fetchall()
        eligible = set(self.eligible_q2_precheck_ids())
        for (precheck_id,) in accepted_prechecks:
            if precheck_id not in eligible:
                issues.append({"code": "accepted_precheck_not_eligible",
                               "record_id": precheck_id, "detail": "Q2 source blocked"})
        for query_id, source_ids_json in q2_rows:
            source_ids = set(json.loads(source_ids_json))
            if not source_ids:
                issues.append({"code": "q2_lacks_precheck_provenance",
                               "record_id": query_id, "detail": "Q2 source blocked"})
            elif not source_ids <= eligible:
                issues.append({"code": "q2_has_ineligible_precheck",
                               "record_id": query_id,
                               "detail": ",".join(sorted(source_ids - eligible))})
        counts["eligible_q2_prechecks"] = len(eligible)
        return {"passed": not issues, "counts": counts, "issues": issues}

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
            query_variant=row["query_variant"],
            query_class=QueryClass(row["query_class"]), query_text=row["query_text"],
            query_hash=row["query_hash"],
            source_indicator_ids=json.loads(row["source_indicator_ids_json"]),
            source_assertion_ids=json.loads(row["source_assertion_ids_json"]),
            source_available_at=(
                datetime.fromisoformat(row["source_available_at"])
                if row["source_available_at"] else None
            ),
            source_feature_ids=json.loads(row["source_feature_ids_json"]),
            source_precheck_ids=json.loads(row["source_precheck_ids_json"]),
            developed_from_split=DatasetSplit(row["developed_from_split"]),
            registered_at=datetime.fromisoformat(row["registered_at"]),
            frozen_at=datetime.fromisoformat(row["frozen_at"]) if row["frozen_at"] else None,
            valid_for_test_from=(datetime.fromisoformat(row["valid_for_test_from"])
                                 if row["valid_for_test_from"] else None),
            config_hash=row["config_hash"], status=QueryStatus(row["status"]),
        )
