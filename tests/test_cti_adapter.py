"""CTI-Agent adapter 회귀 테스트.

목적: 외부 원천 hash와 IoC defang·형식검증 재사용을 확인한다.
지원 RQ: RQ1∼RQ4 CTI seed·pivot.
설계: network 호출 없이 실제 읽기 전용 CTI-Agent pure module만 사용한다.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from src.adapters.cti_agent import CtiAgentAdapter


CTI_AGENT = Path(r"D:\Claude\CTI-Agent")
EXPECTED_HASHES = {
    "src/collectors/search_collector.py": "C960866D2B246195F878CFE9D5B8CA6A65825542EF1270297C568ADA73AF7F4A",
    "src/collectors/base.py": "271C069849BF827B334F074C913DB5AB2F5AB20E42C7F585E5EA38453485899D",
    "src/pipeline/ioc_extract.py": "85304CF1EA78054A49F6345201F0D7F70AA6A853DE6FA459DE4B1E46C3DEA645",
    "src/utils/ioc_regex.py": "B576C7DBA5D958C8D26E4637ADF9DB4F92C8541F401820A0CABB7D751AF36A31",
}


class CtiAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = CtiAgentAdapter(CTI_AGENT, EXPECTED_HASHES)

    def test_reuse_hashes_match(self) -> None:
        self.adapter.verify_reuse_files()

    def test_normalizes_defanged_domain(self) -> None:
        self.assertEqual("relay.example.org", self.adapter.normalize_indicator("domain", "relay[.]example[.]org"))

    def test_rejects_invalid_indicator(self) -> None:
        with self.assertRaises(ValueError):
            self.adapter.normalize_indicator("domain", "not-a-domain")


if __name__ == "__main__":
    unittest.main()
