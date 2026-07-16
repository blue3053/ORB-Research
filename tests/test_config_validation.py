"""연구 YAML 구조와 strict 설정 모델 회귀 테스트.

목적: CTI·Censys 설정이 실제 모델에 연결되고 오타가 조용히 무시되지 않는지 검증한다.
지원 RQ: RQ1∼RQ5 실행 재현성과 안전 정책.
설계: network·파일 변경 없이 Pydantic 모델 검증 경로를 직접 사용한다.
"""
from __future__ import annotations

import sys
import types
import unittest

from pydantic import ValidationError

if "yaml" not in sys.modules:
    yaml_stub = types.ModuleType("yaml")
    yaml_stub.safe_load = lambda value: {}
    sys.modules["yaml"] = yaml_stub

from src.config import ProjectConfig


def valid_config():
    return {
        "project_timezone": "Asia/Seoul",
        "research_start_at": "2026-07-13T00:00:00+09:00",
        "source_projects": {
            "cti_agent": {"path": "D:/Claude/CTI-Agent", "read_only": True},
            "orbhunt_v5": {"path": "D:/Gemini/ORB_Hunt_v5", "read_only": True},
        },
        "storage": {
            "registry_db": "data_registry/a.sqlite", "manifests": "data_registry/manifests",
            "raw": "data/raw", "curated": "data/curated",
        },
        "security": {},
        "query_policy": {
            "classes": ["Q0_SEED"], "splits": ["development"],
        },
        "cti_search": {"domain_whitelist": ["Example.org", "example.org"]},
        "cti_corpus": {
            "require_source_family": True,
            "require_source_access_class": True,
            "public_export_allowed_access_classes": ["public"],
            "development_excluded_acquisition_modes": ["prospective_validation"],
        },
        "cti_ioc_extraction": {
            "model": "fixture-model",
            "prompt_path": "configs/2026-07-14-ioc-extract-v1.md",
        },
        "censys_collection": {
            "endpoint": "https://api.platform.censys.io/v3/global/search/query"
        },
    }


class ConfigValidationTests(unittest.TestCase):
    def test_cti_and_censys_settings_are_loaded(self):
        config = ProjectConfig.model_validate(valid_config())
        self.assertEqual(["example.org"], config.cti_search.domain_whitelist)
        self.assertTrue(config.cti_ioc_extraction.require_raw_form_source_match)
        self.assertEqual(
            ["prospective_validation"],
            config.cti_corpus.development_excluded_acquisition_modes,
        )
        self.assertEqual(100, config.censys_collection.page_size)
        self.assertTrue(config.phase_b_policy.require_eligible_precheck_for_q2)
        self.assertEqual(2, config.phase_b_policy.precheck_page_budget)

    def test_rejects_unknown_and_unsafe_values(self):
        unknown = valid_config()
        unknown["censys_collection"]["page_sze"] = 10
        with self.assertRaises(ValidationError):
            ProjectConfig.model_validate(unknown)
        invalid_page = valid_config()
        invalid_page["censys_collection"]["page_size"] = 101
        with self.assertRaises(ValidationError):
            ProjectConfig.model_validate(invalid_page)


if __name__ == "__main__":
    unittest.main()
