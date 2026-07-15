"""검증 IoC의 Q0/Q1 분류·등록 회귀 테스트.

목적: IP·domain·미지원 scope가 각각 Q0·Q1·보류로 분리되고 실행되지 않는지 검증한다.
지원 RQ: RQ1∼RQ3 및 RQ5 direct-pivot baseline.
설계: 실제 ORB_Hunt_v5 renderer와 임시 query registry를 사용하되 network는 호출하지 않는다.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.adapters.orbhunt_censys import OrbhuntCensysAdapter
from src.censys.query_registry import QueryRegistry
from src.cti.pivot_planning import register_pivot_plans
from src.models import AcceptedPivotSource, AssertionRole


def indicator(identifier, scope, value, role=AssertionRole.RELAY_ORB):
    return AcceptedPivotSource(
        indicator_id=identifier, assertion_id=f"assert-{identifier}",
        review_id=f"review-{identifier}", scope=scope, value=value, role=role,
        available_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        source_confidence=0.9, extraction_confidence=0.9, role_confidence=0.9,
    )


class PivotPlanningTests(unittest.TestCase):
    def test_registers_q0_q1_and_holds_unsupported(self):
        config = {
            "templates": {
                "domain": {"field": "host.dns.names", "template": 'host.dns.names: "{pivot_value}"'},
            },
            "safety": {
                "fail_closed_on_missing_field": True, "allow_raw_ip_query": False,
                "broad_domain_block_exact": [], "broad_domain_block_suffixes": [],
                "filelike_domain_suffixes": [],
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            registry = QueryRegistry(Path(directory) / "queries.sqlite")
            plans = register_pivot_plans(
                [
                    indicator("ioc-ip", "ip", "8.8.8.8"),
                    indicator("ioc-domain", "domain", "relay.badinfra.net"),
                    indicator("ioc-md5", "hash_md5", "a" * 32),
                ],
                registry=registry,
                censys_adapter=OrbhuntCensysAdapter(Path(r"D:\Gemini\ORB_Hunt_v5")),
                q1_template_config=config,
                registered_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
                cutoff_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                query_version="1", config_hash="cfg",
            )
            self.assertEqual("Q0_SEED", plans[0].query_class)
            self.assertEqual("Q1_DIRECT_PIVOT", plans[1].query_class)
            self.assertEqual("unsupported", plans[2].status)
            with registry.connect() as connection:
                self.assertEqual(2, connection.execute("SELECT COUNT(*) FROM query_registry").fetchone()[0])

    def test_blocks_non_pivotable_context_and_non_global_ip(self):
        config = {
            "templates": {
                "domain": {
                    "field": "host.dns.names",
                    "template": 'host.dns.names: "{pivot_value}"',
                },
            },
            "safety": {
                "fail_closed_on_missing_field": True,
                "allow_raw_ip_query": False,
                "broad_domain_block_exact": [],
                "broad_domain_block_suffixes": [],
                "filelike_domain_suffixes": [],
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            registry = QueryRegistry(Path(directory) / "queries.sqlite")
            plans = register_pivot_plans(
                [
                    indicator(
                        "ioc-legitimate", "domain", "c.speedtest.net",
                        role=AssertionRole.UNKNOWN,
                    ),
                    indicator("ioc-private", "ip", "192.168.18.111"),
                    indicator("ioc-relay", "domain", "relay.badinfra.net"),
                ],
                registry=registry,
                censys_adapter=OrbhuntCensysAdapter(Path(r"D:\Gemini\ORB_Hunt_v5")),
                q1_template_config=config,
                registered_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
                cutoff_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                query_version="1",
                config_hash="cfg",
            )
            self.assertEqual("blocked", plans[0].status)
            self.assertIn("role", plans[0].reason)
            self.assertEqual("blocked", plans[1].status)
            self.assertIn("globally routable", plans[1].reason)
            self.assertEqual("registered_not_executed", plans[2].status)
            with registry.connect() as connection:
                self.assertEqual(
                    1,
                    connection.execute("SELECT COUNT(*) FROM query_registry").fetchone()[0],
                )


if __name__ == "__main__":
    unittest.main()
