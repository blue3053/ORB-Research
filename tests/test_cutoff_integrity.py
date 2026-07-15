"""Feature availability와 RQ별 query class 무결성 테스트.

목적: cutoff 이후 feature 사용과 RQ1·RQ2 표본의 Q2/Q3 의존을 차단한다.
지원 RQ: RQ1∼RQ5.
설계: pure lifecycle function만 테스트한다.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from src.censys.query_lifecycle import ensure_features_available, query_supports_rq
from src.models import DatasetSplit, QueryClass, QueryRecord
from src.provenance import sha256_text


class CutoffIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cutoff = datetime(2026, 7, 13, tzinfo=timezone.utc)

    def _query(self, query_class: QueryClass) -> QueryRecord:
        text = "fixture query"
        return QueryRecord(
            query_id="query-fixture", query_version="v1", query_class=query_class,
            query_text=text, query_hash=sha256_text(text),
            developed_from_split=DatasetSplit.DEVELOPMENT,
            registered_at=self.cutoff, config_hash="config-hash",
        )

    def test_accepts_features_available_at_or_before_cutoff(self) -> None:
        ensure_features_available(
            [self.cutoff - timedelta(days=1), self.cutoff], self.cutoff
        )

    def test_rejects_future_feature(self) -> None:
        with self.assertRaises(ValueError):
            ensure_features_available([self.cutoff + timedelta(seconds=1)], self.cutoff)

    def test_rq1_uses_q0_not_q2(self) -> None:
        self.assertTrue(query_supports_rq(self._query(QueryClass.Q0_SEED), "RQ1"))
        self.assertFalse(query_supports_rq(self._query(QueryClass.Q2_DERIVED), "RQ1"))


if __name__ == "__main__":
    unittest.main()

