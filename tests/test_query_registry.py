"""Q0∼Q3 query registry와 prospective gate 테스트.

목적: query 동결 전 미래 평가, hash 변조와 잘못된 상태 전이를 차단한다.
지원 RQ: RQ4·RQ5.
설계: 임시 SQLite만 사용하며 network와 외부 데이터에 의존하지 않는다.
"""
from __future__ import annotations

import tempfile
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
            query_class=QueryClass.Q2_DERIVED,
            query_text='host.services.port: 8443 and host.services.service_name: "HTTP"',
            developed_from_split=DatasetSplit.DEVELOPMENT,
            config_hash="config-hash",
            source_feature_ids=["feature-1"],
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


if __name__ == "__main__":
    unittest.main()

