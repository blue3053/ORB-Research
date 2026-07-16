"""Additive append-only SQLite registry for Stage 6-7 artifacts."""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from src.censys.feature_registry import FeatureRegistry
from src.censys.query_freeze import QueryDesignRegistry
from src.models import (
    AdjudicationStatus,
    CandidateAdjudicationRecord,
    CandidateEvidenceRecord,
    CandidateGradeEventRecord,
    CandidateRecord,
    DatasetSplit,
    ObservationOpportunityRecord,
    OpportunityStatus,
    ProspectiveObservationEventRecord,
    QueryExecutionRecord,
)
from src.prospective.evidence import recommended_status, validate_evidence_independence
from src.provenance import canonical_json_hash


SCHEMA = """
CREATE TABLE IF NOT EXISTS observation_opportunities (
  opportunity_id TEXT PRIMARY KEY,
  query_id TEXT NOT NULL REFERENCES query_registry(query_id),
  entity_epoch_id TEXT NOT NULL REFERENCES entity_epochs(entity_epoch_id),
  due_at TEXT NOT NULL,
  status TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS opportunity_events (
  event_id TEXT PRIMARY KEY,
  opportunity_id TEXT NOT NULL REFERENCES observation_opportunities(opportunity_id),
  status TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prospective_observation_events (
  event_id TEXT PRIMARY KEY,
  opportunity_id TEXT NOT NULL REFERENCES observation_opportunities(opportunity_id),
  query_run_id TEXT NOT NULL REFERENCES query_executions(query_run_id),
  observation_id TEXT NOT NULL REFERENCES host_observations(observation_id),
  entity_epoch_id TEXT NOT NULL REFERENCES entity_epochs(entity_epoch_id),
  time_status TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candidates (
  candidate_id TEXT PRIMARY KEY,
  query_id TEXT NOT NULL REFERENCES query_registry(query_id),
  entity_epoch_id TEXT NOT NULL REFERENCES entity_epochs(entity_epoch_id),
  first_candidate_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candidate_evidence (
  evidence_id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
  role TEXT NOT NULL,
  source_family_id TEXT NOT NULL,
  available_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candidate_adjudications (
  adjudication_id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
  status TEXT NOT NULL,
  adjudicated_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candidate_grade_events (
  grade_event_id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
  adjudication_id TEXT NOT NULL REFERENCES candidate_adjudications(adjudication_id),
  previous_grade_event_id TEXT REFERENCES candidate_grade_events(grade_event_id),
  graded_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
"""


class ProspectiveRegistry:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with QueryDesignRegistry(self.path).connect():
            pass
        with FeatureRegistry(self.path).connect():
            pass
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _payload(record) -> str:
        return json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _insert_immutable(connection, table, key_column, key, payload, sql, values) -> bool:
        row = connection.execute(
            f"SELECT payload_json FROM {table} WHERE {key_column}=?", (key,)
        ).fetchone()
        if row:
            if row[0] != payload:
                raise ValueError(f"{table} immutable ID collision: {key}")
            return False
        connection.execute(sql, values)
        return True

    def record_opportunity(self, record: ObservationOpportunityRecord) -> bool:
        payload = self._payload(record)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM observation_opportunities WHERE opportunity_id=?",
                (record.opportunity_id,),
            ).fetchone()
            changed = False
            if row is None:
                connection.execute(
                    "INSERT INTO observation_opportunities VALUES (?, ?, ?, ?, ?, ?)",
                    (record.opportunity_id, record.query_id, record.entity_epoch_id,
                     record.due_at.isoformat(), record.status.value, payload),
                )
                changed = True
            else:
                old = ObservationOpportunityRecord.model_validate_json(row[0])
                immutable = (
                    old.query_id, old.query_version, old.query_hash, old.schedule_id,
                    old.entity_epoch_id, old.due_at, old.window_end,
                )
                current = (
                    record.query_id, record.query_version, record.query_hash, record.schedule_id,
                    record.entity_epoch_id, record.due_at, record.window_end,
                )
                if immutable != current:
                    raise ValueError("observation opportunity immutable identity changed")
                if old == record or (
                    old.status is record.status
                    and old.query_run_id == record.query_run_id
                    and old.reason == record.reason
                ):
                    return False
                allowed = {
                    OpportunityStatus.DUE: {OpportunityStatus.MISSED, OpportunityStatus.PARTIAL,
                                            OpportunityStatus.FAILED, OpportunityStatus.COMPLETE,
                                            OpportunityStatus.LATE},
                    OpportunityStatus.PARTIAL: {OpportunityStatus.PARTIAL, OpportunityStatus.FAILED,
                                                OpportunityStatus.COMPLETE, OpportunityStatus.LATE},
                }
                if record.status not in allowed.get(old.status, set()):
                    raise ValueError(f"invalid opportunity transition: {old.status} -> {record.status}")
                connection.execute(
                    "UPDATE observation_opportunities SET status=?, payload_json=? WHERE opportunity_id=?",
                    (record.status.value, payload, record.opportunity_id),
                )
                changed = True
            event_id = "opportunity-event-" + canonical_json_hash({
                "opportunity_id": record.opportunity_id,
                "status": record.status.value,
                "query_run_id": record.query_run_id,
                "reason": record.reason,
            })[:20]
            connection.execute(
                "INSERT OR IGNORE INTO opportunity_events VALUES (?, ?, ?, ?, ?)",
                (event_id, record.opportunity_id, record.status.value,
                 record.recorded_at.isoformat(), payload),
            )
        return changed

    def register_observation_event(self, record: ProspectiveObservationEventRecord) -> bool:
        execution = self._execution(record.query_run_id)
        if execution.dataset_split is not DatasetSplit.PROSPECTIVE_TEST:
            raise ValueError("prospective event requires prospective_test execution")
        if not re.fullmatch(r"[0-9a-f]{64}", execution.result_manifest_hash):
            raise ValueError("prospective execution raw manifest hash is invalid")
        payload = self._payload(record)
        with self.connect() as connection:
            opportunity = connection.execute(
                "SELECT query_id, entity_epoch_id FROM observation_opportunities WHERE opportunity_id=?",
                (record.opportunity_id,),
            ).fetchone()
            if opportunity is None:
                raise ValueError("prospective event lacks observation opportunity")
            if opportunity["entity_epoch_id"] != record.entity_epoch_id:
                raise ValueError("prospective event entity epoch differs from opportunity")
            query = connection.execute(
                "SELECT query_hash, status FROM query_registry WHERE query_id=?",
                (opportunity["query_id"],),
            ).fetchone()
            if query["status"] != "frozen" or query["query_hash"] != execution.query_hash:
                raise ValueError("prospective event execution is not tied to frozen query hash")
            design = connection.execute(
                "SELECT payload_json FROM query_designs WHERE query_id=?",
                (opportunity["query_id"],),
            ).fetchone()
            if design is not None:
                from src.models import QueryDesignRecord
                frozen_design = QueryDesignRecord.model_validate_json(design[0])
                if execution.api_schema_version != frozen_design.api_schema_version:
                    raise ValueError("prospective execution API schema drifted from frozen design")
            return self._insert_immutable(
                connection, "prospective_observation_events", "event_id", record.event_id,
                payload, "INSERT INTO prospective_observation_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                (record.event_id, record.opportunity_id, record.query_run_id,
                 record.observation_id, record.entity_epoch_id, record.time_status.value, payload),
            )

    def register_candidate(self, record: CandidateRecord) -> bool:
        payload = self._payload(record)
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT payload_json FROM candidates WHERE candidate_id=?",
                (record.candidate_id,),
            ).fetchone()
            if existing is not None:
                canonical = CandidateRecord.model_validate_json(existing[0])
                identity = (canonical.query_id, canonical.query_version, canonical.query_hash,
                            canonical.entity_epoch_id, canonical.indicator_id)
                incoming = (record.query_id, record.query_version, record.query_hash,
                            record.entity_epoch_id, record.indicator_id)
                if identity != incoming:
                    raise ValueError("candidate immutable identity changed")
                if record.first_candidate_at < canonical.first_candidate_at:
                    raise ValueError("candidate rerun predates immutable first_candidate_at")
                return False
            event = connection.execute(
                "SELECT time_status FROM prospective_observation_events WHERE event_id=?",
                (record.first_observation_event_id,),
            ).fetchone()
            if event is None or event[0] != "eligible":
                raise ValueError("candidate requires persisted eligible prospective event")
            return self._insert_immutable(
                connection, "candidates", "candidate_id", record.candidate_id, payload,
                "INSERT INTO candidates VALUES (?, ?, ?, ?, ?)",
                (record.candidate_id, record.query_id, record.entity_epoch_id,
                 record.first_candidate_at.isoformat(), payload),
            )

    def get_candidate(self, candidate_id: str) -> CandidateRecord:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
        if row is None:
            raise KeyError(candidate_id)
        return CandidateRecord.model_validate_json(row[0])

    def register_evidence(self, record: CandidateEvidenceRecord) -> bool:
        candidate = self.get_candidate(record.candidate_id)
        validate_evidence_independence(candidate, record)
        payload = self._payload(record)
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT payload_json FROM candidate_evidence WHERE evidence_id=?",
                (record.evidence_id,),
            ).fetchone()
            if existing is not None:
                if existing[0] != payload:
                    raise ValueError(f"candidate_evidence immutable ID collision: {record.evidence_id}")
                return False
            same_family = connection.execute(
                "SELECT evidence_id FROM candidate_evidence WHERE candidate_id=? "
                "AND source_family_id=?",
                (record.candidate_id, record.source_family_id),
            ).fetchone()
            if same_family is not None:
                raise ValueError("same source family evidence is not independently additive")
            return self._insert_immutable(
                connection, "candidate_evidence", "evidence_id", record.evidence_id, payload,
                "INSERT INTO candidate_evidence VALUES (?, ?, ?, ?, ?, ?)",
                (record.evidence_id, record.candidate_id, record.role.value,
                 record.source_family_id, record.available_at.isoformat(), payload),
            )

    def evidence_for(self, candidate_id: str) -> list[CandidateEvidenceRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM candidate_evidence WHERE candidate_id=? "
                "ORDER BY available_at, evidence_id", (candidate_id,)
            ).fetchall()
        return [CandidateEvidenceRecord.model_validate_json(row[0]) for row in rows]

    def register_adjudication(self, record: CandidateAdjudicationRecord) -> bool:
        self.get_candidate(record.candidate_id)
        evidence = self.evidence_for(record.candidate_id)
        known_ids = {item.evidence_id for item in evidence}
        if not set(record.evidence_ids) <= known_ids:
            raise ValueError("adjudication references unknown evidence")
        selected = [item for item in evidence if item.evidence_id in record.evidence_ids]
        expected = recommended_status(selected)
        if record.status is not expected:
            raise ValueError(f"adjudication status conflicts with evidence: expected {expected.value}")
        payload = self._payload(record)
        with self.connect() as connection:
            return self._insert_immutable(
                connection, "candidate_adjudications", "adjudication_id",
                record.adjudication_id, payload,
                "INSERT INTO candidate_adjudications VALUES (?, ?, ?, ?, ?)",
                (record.adjudication_id, record.candidate_id, record.status.value,
                 record.adjudicated_at.isoformat(), payload),
            )

    def register_grade_event(self, record: CandidateGradeEventRecord) -> bool:
        payload = self._payload(record)
        with self.connect() as connection:
            adjudication = connection.execute(
                "SELECT candidate_id, adjudicated_at FROM candidate_adjudications "
                "WHERE adjudication_id=?", (record.adjudication_id,)
            ).fetchone()
            if adjudication is None or adjudication["candidate_id"] != record.candidate_id:
                raise ValueError("grade event requires candidate adjudication")
            latest = connection.execute(
                "SELECT grade_event_id, graded_at FROM candidate_grade_events "
                "WHERE candidate_id=? ORDER BY graded_at DESC, grade_event_id DESC LIMIT 1",
                (record.candidate_id,),
            ).fetchone()
            expected_previous = latest["grade_event_id"] if latest else None
            if record.previous_grade_event_id != expected_previous:
                raise ValueError("grade event must append to latest grade history")
            if latest and record.graded_at.isoformat() < latest["graded_at"]:
                raise ValueError("grade history cannot move backward in time")
            return self._insert_immutable(
                connection, "candidate_grade_events", "grade_event_id",
                record.grade_event_id, payload,
                "INSERT INTO candidate_grade_events VALUES (?, ?, ?, ?, ?, ?)",
                (record.grade_event_id, record.candidate_id, record.adjudication_id,
                 record.previous_grade_event_id, record.graded_at.isoformat(), payload),
            )

    def _execution(self, query_run_id: str) -> QueryExecutionRecord:
        from src.censys.query_registry import QueryRegistry
        return QueryRegistry(self.path).get_execution(query_run_id)

    def phase_e_gate_report(self) -> dict:
        issues: list[dict[str, str]] = []
        with self.connect() as connection:
            counts = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "observation_opportunities", "prospective_observation_events", "candidates",
                    "candidate_evidence", "candidate_adjudications", "candidate_grade_events",
                )
            }
            orphan_candidates = connection.execute(
                "SELECT candidate_id FROM candidates c WHERE NOT EXISTS ("
                "SELECT 1 FROM prospective_observation_events e "
                "WHERE e.event_id=json_extract(c.payload_json, '$.first_observation_event_id'))"
            ).fetchall()
            issues.extend({"code": "candidate_without_event", "record_id": row[0]}
                          for row in orphan_candidates)
            unresolved = connection.execute(
                "SELECT COUNT(*) FROM prospective_observation_events "
                "WHERE time_status='prospective_time_unresolved'"
            ).fetchone()[0]
        return {"ok": not issues, "counts": counts,
                "prospective_time_unresolved": unresolved, "issues": issues}
