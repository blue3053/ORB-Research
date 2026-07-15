"""Q1 reference/broad domain 차단 회귀 테스트.

목적: 자동 분류가 ORB_Hunt_v5 안전 차단을 우회하지 않고 blocked 계획으로 남기는지 검증한다.
지원 RQ: RQ3·RQ5 direct-pivot precision과 안전성.
설계: reference domain을 입력하고 query registry에 등록되지 않았음을 확인한다.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.adapters.orbhunt_censys import OrbhuntCensysAdapter
from src.censys.query_registry import QueryRegistry
from src.cti.ioc_extraction import VerifiedIndicator
from src.cti.pivot_planning import register_pivot_plans


class PivotSafetyTests(unittest.TestCase):
    def test_reference_domain_remains_blocked(self):
        indicator = VerifiedIndicator(
            "ioc-ref", "domain", "relay.example.org", "relay.example.org", "doc-1",
            "2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00",
            "observed_in_report", "unknown", "evidence",
        )
        config = {
            "templates": {"domain": {
                "field": "host.dns.names", "template": 'host.dns.names: "{pivot_value}"'
            }},
            "safety": {"fail_closed_on_missing_field": True, "allow_raw_ip_query": False},
        }
        with tempfile.TemporaryDirectory() as directory:
            registry = QueryRegistry(Path(directory) / "queries.sqlite")
            plans = register_pivot_plans(
                [indicator], registry=registry,
                censys_adapter=OrbhuntCensysAdapter(Path(r"D:\Gemini\ORB_Hunt_v5")),
                q1_template_config=config,
                registered_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
                query_version="1", config_hash="cfg",
            )
            self.assertEqual("blocked", plans[0].status)
            with registry.connect() as connection:
                count = connection.execute("SELECT COUNT(*) FROM query_registry").fetchone()[0]
            self.assertEqual(0, count)


if __name__ == "__main__":
    unittest.main()
