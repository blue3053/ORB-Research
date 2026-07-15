"""내부 파생본 SHA-256 고정 회귀 테스트.

목적: 검토 완료된 파생 코드가 승인 없이 변하면 즉시 탐지한다.
지원 RQ: RQ1∼RQ5 code provenance.
설계: configs/reuse_sources.yaml과 동일한 고정 hash를 프로젝트 파일에 대조한다.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from src.provenance import sha256_file


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "src/reused/cti_agent/ioc_regex.py": "47E460D91B44BDF81C6A6AC78E5E3177A6C1115E8DB529B87F6AF3ED92D2DF4E",
    "src/reused/cti_agent/search_rules.py": "DFFEB0D036A6F1FE58AA14D1BC0021548F5D9F2A1D7D82F0108880C61250A12F",
    "src/reused/orbhunt_v5/censys_query.py": "9FE5A19DE0B19B6FBF9F58492C5A0DCB8FD53A69D83D988D7C6BFB3A3D2E958F",
    "src/reused/orbhunt_v5/error_policy.py": "14BCB9C56B865B7C261866488B361611E44DD1678BFA87D507ACE33D5A1048BA",
    "src/reused/orbhunt_v5/pivot_safety.py": "B5FC2E51C06F761262EE471A2FF4F5168907F9DA2775FB87F28A6ED35CFE498D",
    "src/reused/orbhunt_v5/result_parser.py": "D4918B53EA6D4979B89CF1E010E0E1F430397D0F0BFED72F1B62AF848EA320BC",
}


class InternalHashManifestTests(unittest.TestCase):
    def test_adapted_files_match_reviewed_hashes(self):
        for relative, expected in EXPECTED.items():
            self.assertEqual(expected.lower(), sha256_file(ROOT / relative).lower(), relative)


if __name__ == "__main__":
    unittest.main()
