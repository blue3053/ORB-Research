"""Q0∼Q3 query registry와 prospective gate 테스트.

목적: query 동결 전 미래 평가, hash 변조와 잘못된 상태 전이를 차단한다.
지원 RQ: RQ4·RQ5.
설계: 임시 SQLite만 사용하며 network와 외부 데이터에 의존하지 않는다.
"""
from __future__ import annotations

import tempfile
import sqlite3
from contextlib import closing
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.censys.query_registry import QueryRegistry
from src.models import DatasetSplit, QueryClass, QueryExecutionRecord, QueryStatus
from src.provenance import sha256_text


class QueryRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.registry = QueryRegistry(Path(self.temp.name) / "registry.sqlite")
        self.t0 = datetime(2026, 7, 13, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _register(self):
        return self.registry.register_query(
            query_version="v1",
            query_class=QueryClass.Q0_SEED,
            query_text="192.0.2.1",
            developed_from_split=DatasetSplit.DEVELOPMENT,
            config_hash="config-hash",
            source_indicator_ids=["ioc-seed"],
            source_assertion_ids=["assert-seed"],
            source_available_at=self.t0,
            registered_at=self.t0,
        )

    def test_freeze_and_record_prospective_execution(self) -> None:
        query = self._register()
        self.registry.mark_validated(query.query_id)
        frozen = self.registry.freeze_query(
            query.query_id, frozen_at=self.t0 + timedelta(days=1),
            valid_for_test_from=self.t0 + timedelta(days=2),
        )
        self.assertEqual(QueryStatus.FROZEN, frozen.status)
        execution = QueryExecutionRecord(
            query_run_id="run-1", query_id=frozen.query_id, query_hash=frozen.query_hash,
            cutoff_time=self.t0 + timedelta(days=1),
            executed_at=self.t0 + timedelta(days=2),
            dataset_split=DatasetSplit.PROSPECTIVE_TEST,
            result_count=0, result_manifest_hash="empty-result",
            api_schema_version="fixture", status="success",
        )
        self.registry.record_execution(execution)

    def test_rejects_prospective_execution_before_freeze(self) -> None:
        query = self._register()
        execution = QueryExecutionRecord(
            query_run_id="run-2", query_id=query.query_id, query_hash=query.query_hash,
            cutoff_time=self.t0, executed_at=self.t0 + timedelta(days=1),
            dataset_split=DatasetSplit.PROSPECTIVE_TEST,
            result_count=0, result_manifest_hash="empty-result",
            api_schema_version="fixture", status="success",
        )
        with self.assertRaises(ValueError):
            self.registry.record_execution(execution)

    def test_rejects_execution_hash_mismatch(self) -> None:
        query = self._register()
        execution = QueryExecutionRecord(
            query_run_id="run-3", query_id=query.query_id,
            query_hash=sha256_text("mutated query"), cutoff_time=self.t0,
            executed_at=self.t0 + timedelta(days=1), dataset_split=DatasetSplit.DEVELOPMENT,
            result_count=0, result_manifest_hash="empty-result",
            api_schema_version="fixture", status="success",
        )
        with self.assertRaises(ValueError):
            self.registry.record_execution(execution)

    def test_cti_query_validation_requires_assertion_provenance(self) -> None:
        unsafe = self.registry.register_query(
            query_version="v1", query_class=QueryClass.Q1_DIRECT_PIVOT,
            query_text='host.dns.names: "relay.example.org"',
            developed_from_split=DatasetSplit.DEVELOPMENT,
            config_hash="config-hash", source_indicator_ids=["ioc-1"],
            registered_at=self.t0,
        )
        with self.assertRaisesRegex(ValueError, "accepted assertion provenance"):
            self.registry.mark_validated(unsafe.query_id)

    def test_execution_cutoff_cannot_predate_query_source(self) -> None:
        query = self.registry.register_query(
            query_version="v1", query_class=QueryClass.Q1_DIRECT_PIVOT,
            query_text='host.dns.names: "relay.example.net"',
            developed_from_split=DatasetSplit.DEVELOPMENT,
            config_hash="config-hash", source_indicator_ids=["ioc-2"],
            source_assertion_ids=["assert-2"],
            source_available_at=self.t0 + timedelta(days=1),
            registered_at=self.t0 + timedelta(days=1),
        )
        execution = QueryExecutionRecord(
            query_run_id="run-cutoff", query_id=query.query_id,
            query_hash=query.query_hash, cutoff_time=self.t0,
            executed_at=self.t0 + timedelta(days=2),
            dataset_split=DatasetSplit.DEVELOPMENT,
            result_count=0, result_manifest_hash="empty-result",
            api_schema_version="fixture", status="success",
        )
        with self.assertRaisesRegex(ValueError, "available after execution cutoff"):
            self.registry.record_execution(execution)

    def test_legacy_query_table_gets_additive_phase_a_columns(self) -> None:
        path = Path(self.temp.name) / "legacy.sqlite"
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("""
                CREATE TABLE query_registry (
                  query_id TEXT PRIMARY KEY, query_version TEXT NOT NULL,
                  query_class TEXT NOT NULL, query_text TEXT NOT NULL,
                  query_hash TEXT NOT NULL, source_indicator_ids_json TEXT NOT NULL,
                  source_feature_ids_json TEXT NOT NULL,
                  developed_from_split TEXT NOT NULL, registered_at TEXT NOT NULL,
                  frozen_at TEXT, valid_for_test_from TEXT, config_hash TEXT NOT NULL,
                  status TEXT NOT NULL
                )
            """)
            connection.commit()
        with QueryRegistry(path).connect() as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(query_registry)")
            }
        self.assertIn("source_assertion_ids_json", columns)
        self.assertIn("source_available_at", columns)
        self.assertIn("source_precheck_ids_json", columns)
        self.assertIn("query_variant", columns)

    def test_partial_execution_can_resume_under_same_run_id(self) -> None:
        query = self._register()
        partial = QueryExecutionRecord(
            query_run_id="run-resume", query_id=query.query_id,
            query_hash=query.query_hash, cutoff_time=self.t0,
            executed_at=self.t0 + timedelta(hours=1),
            dataset_split=DatasetSplit.DEVELOPMENT, result_count=2,
            result_manifest_hash="partial-manifest", api_schema_version="fixture",
            status="partial_max_pages",
        )
        self.assertTrue(self.registry.record_execution(partial))
        complete = partial.model_copy(update={
            "result_count": 5, "result_manifest_hash": "complete-manifest",
            "status": "complete",
        })
        self.assertTrue(self.registry.record_execution(complete))
        self.assertFalse(self.registry.record_execution(complete))
        self.assertEqual("complete", self.registry.get_execution("run-resume").status)
        with self.registry.connect() as connection:
            events = connection.execute(
                "SELECT status FROM query_execution_events WHERE query_run_id=? "
                "ORDER BY rowid", ("run-resume",),
            ).fetchall()
        self.assertEqual(["partial_max_pages", "complete"], [row[0] for row in events])


if __name__ == "__main__":
    unittest.main()
