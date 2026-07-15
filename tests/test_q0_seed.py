"""Q0 exact-IP query 등록과 시간 누수 회귀 테스트.

목적: Q0가 정확한 IP query와 source indicator provenance를 갖는지 검증한다.
지원 RQ: RQ1∼RQ3 Q0 seed observation.
설계: 임시 SQLite registry로 실제 등록 경로를 테스트한다.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.censys.q0_seed import register_q0_seed, render_q0_seed_query
from src.censys.query_registry import QueryRegistry
from src.models import QueryClass


class Q0SeedTests(unittest.TestCase):
    def test_registers_exact_q0(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = QueryRegistry(Path(directory) / "registry.sqlite")
            record = register_q0_seed(
                registry, indicator_id="ioc-1", ip_value="192.0.2.7",
                indicator_available_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                registered_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                query_version="1", config_hash="cfg",
            )
            self.assertEqual(QueryClass.Q0_SEED, record.query_class)
            self.assertEqual("host.ip = 192.0.2.7", record.query_text)
            self.assertEqual(["ioc-1"], record.source_indicator_ids)

    def test_rejects_non_ip_and_future_source(self):
        with self.assertRaises(ValueError):
            render_q0_seed_query("example.org")
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
            register_q0_seed(
                QueryRegistry(Path(directory) / "registry.sqlite"), indicator_id="ioc-1",
                ip_value="192.0.2.7",
                indicator_available_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
                registered_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                query_version="1", config_hash="cfg",
            )


if __name__ == "__main__":
    unittest.main()
