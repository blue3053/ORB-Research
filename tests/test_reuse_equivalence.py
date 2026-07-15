"""내부 파생본과 외부 pure 원본의 핵심 동등성 회귀 테스트.

목적: 복사·수정 과정에서 CTI normalize와 ORB domain 차단 의미가 바뀌지 않았는지 검증한다.
지원 RQ: RQ1∼RQ5 재현성·provenance.
설계: 외부 파일이 있을 때만 파일 경로로 pure module을 격리 로드해 fixture 출력을 비교한다.
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from src.reused.cti_agent import ioc_regex
from src.reused.orbhunt_v5 import pivot_safety


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReuseEquivalenceTests(unittest.TestCase):
    def test_cti_normalization_matches_upstream(self):
        path = Path(r"D:\Claude\CTI-Agent\src\utils\ioc_regex.py")
        if not path.is_file():
            self.skipTest("upstream CTI-Agent unavailable; runtime does not require it")
        upstream = load_module("upstream_cti_ioc_regex", path)
        fixtures = [
            ("domain", "Relay[.]Example[.]ORG"),
            ("ip", "192.0.2.5"),
            ("cert", "AA:" * 31 + "AA"),
            ("url", "hxxps://Relay[.]Example[.]ORG/path"),
        ]
        for scope, raw in fixtures:
            self.assertEqual(upstream.normalize(scope, raw), ioc_regex.normalize(scope, raw))

    def test_domain_safety_matches_upstream(self):
        path = Path(r"D:\Gemini\ORB_Hunt_v5\src\orbhunt\stages\pivot_safety.py")
        if not path.is_file():
            self.skipTest("upstream ORB_Hunt_v5 unavailable; runtime does not require it")
        upstream = load_module("upstream_orb_pivot_safety", path)
        for domain in ("relay.example.org", "relay.badinfra.net", "payload.exe", "localhost"):
            self.assertEqual(
                upstream.domain_query_block_reason(domain, {}),
                pivot_safety.domain_query_block_reason(domain, {}),
            )


if __name__ == "__main__":
    unittest.main()
