"""외부 작업 폴더 없는 내부 파생본 runtime 회귀 테스트.

목적: CTI/ORB adapter가 provenance 경로 없이도 pure logic을 실행하는지 검증한다.
지원 RQ: RQ1∼RQ5 실행 재현성.
설계: repository=None으로 adapter를 만들고 정규화·검색식·Q1·Platform host parsing을 검사한다.
"""
from __future__ import annotations

import unittest

from src.adapters.cti_agent import CtiAgentAdapter
from src.adapters.orbhunt_censys import OrbhuntCensysAdapter


class InternalReuseTests(unittest.TestCase):
    def test_cti_runtime_has_no_external_repository_dependency(self):
        adapter = CtiAgentAdapter()
        self.assertEqual("relay.badinfra.net", adapter.normalize_indicator(
            "domain", "relay[.]badinfra[.]net"
        ))
        self.assertEqual(
            ["APT-X report"],
            adapter.expand_search_queries(
                ["{watchlist_group} report"],
                {"groups": [{"canonical": "APT-X", "priority": 1}]},
            ),
        )

    def test_orb_runtime_has_no_external_repository_dependency(self):
        adapter = OrbhuntCensysAdapter()
        query = adapter.render_q1_direct_pivot("domain", "relay.badinfra.net", {
            "templates": {"domain": {
                "field": "host.dns.names", "template": 'host.dns.names: "{pivot_value}"'
            }},
            "safety": {"fail_closed_on_missing_field": True, "allow_raw_ip_query": False},
        })
        self.assertIn("relay.badinfra.net", query)
        parsed = adapter.parse_cached_results(
            [{"host": {"ip": "192.0.2.20", "services": [{"port": 443, "protocol": "HTTPS"}]}}],
            "cluster-1", "pivot-1", "domain", "relay.badinfra.net",
        )
        self.assertEqual("192.0.2.20", parsed[0]["ip"])
        self.assertEqual(443, parsed[0]["port"])


if __name__ == "__main__":
    unittest.main()
