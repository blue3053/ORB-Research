"""CTI 검색 실행·whitelist·게시일 provenance 회귀 테스트.

목적: 검색 결과의 중복·외부 도메인 제거와 게시일 unknown 보존을 검증한다.
지원 RQ: RQ1·RQ4 CTI corpus selection.
설계: network 없는 가짜 검색 백엔드로 결정적 결과를 확인한다.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from src.cti.search_execution import execute_search_protocol, infer_publication_metadata
from src.cti.search_protocol import build_search_protocol
from src.models import AcquisitionMode, SourceAccessClass, TimePrecision


class FakeBackend:
    name = "fake"

    def search(self, query: str):
        return [
            {"title": "Report", "url": "HTTPS://Example.org/a?b=2&a=1#part"},
            {"title": "Duplicate", "url": "https://example.org/a?a=1&b=2"},
            {"title": "Outside", "url": "https://noise.invalid/x"},
            {"title": "Broken", "url": "not-a-url"},
        ]


class SearchExecutionTests(unittest.TestCase):
    def test_filters_and_preserves_unknown_publication_date(self):
        protocol = build_search_protocol(
            version="1", target_date_from="2026-01-01", target_date_to="2026-07-13",
            target_publishers=["example.org"], search_terms=["orb"],
            inclusion_rules=["technical report"], exclusion_rules=["marketing"],
            deduplication_rule="canonical_url",
            research_cutoff_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
            source_access_class=SourceAccessClass.PUBLIC,
            acquisition_mode=AcquisitionMode.SYSTEMATIC_PUBLIC,
            registered_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
        )
        result = execute_search_protocol(
            protocol, ["orb"], FakeBackend(), ["example.org"],
            executed_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
        )
        self.assertEqual(1, len(result.candidates))
        self.assertIsNone(result.candidates[0].published_at)
        self.assertEqual("unknown", result.candidates[0].published_at_basis)
        self.assertEqual(1, result.duplicate_count)
        self.assertEqual(1, result.discarded_outside_whitelist)
        self.assertEqual(1, result.discarded_invalid_url)
        self.assertEqual("public", result.candidates[0].source_access_class)
        self.assertEqual("systematic_public", result.candidates[0].acquisition_mode)
        self.assertEqual("development", result.candidates[0].corpus_purpose)
        self.assertEqual(
            protocol.search_protocol_id, result.candidates[0].search_protocol_id
        )

    def test_protocol_hash_freezes_cutoff_access_and_acquisition_mode(self):
        base = {
            "version": "1",
            "target_date_from": "2026-01-01",
            "target_date_to": "2026-07-13",
            "target_publishers": ["example.org"],
            "search_terms": ["orb"],
            "inclusion_rules": ["technical report"],
            "exclusion_rules": ["marketing"],
            "deduplication_rule": "canonical_url",
            "research_cutoff_at": datetime(2026, 7, 13, tzinfo=timezone.utc),
            "source_access_class": SourceAccessClass.PUBLIC,
            "acquisition_mode": AcquisitionMode.SYSTEMATIC_PUBLIC,
            "registered_at": datetime(2026, 7, 13, tzinfo=timezone.utc),
        }
        baseline = build_search_protocol(**base)
        variants = [
            build_search_protocol(
                **{**base, "research_cutoff_at": datetime(2026, 7, 12, tzinfo=timezone.utc)}
            ),
            build_search_protocol(
                **{**base, "source_access_class": SourceAccessClass.RESTRICTED}
            ),
            build_search_protocol(
                **{**base, "acquisition_mode": AcquisitionMode.PROSPECTIVE_VALIDATION}
            ),
        ]
        self.assertTrue(all(item.protocol_hash != baseline.protocol_hash for item in variants))

    def test_protocol_rejects_naive_research_cutoff(self):
        with self.assertRaises(ValueError):
            build_search_protocol(
                version="1", target_date_from="2026-01-01", target_date_to="2026-07-13",
                target_publishers=["example.org"], search_terms=["orb"],
                inclusion_rules=["technical report"], exclusion_rules=["marketing"],
                deduplication_rule="canonical_url",
                research_cutoff_at=datetime(2026, 7, 13),
                source_access_class=SourceAccessClass.PUBLIC,
                acquisition_mode=AcquisitionMode.SYSTEMATIC_PUBLIC,
                registered_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
            )

    def test_date_only_publication_is_not_promoted_to_exact_timestamp(self):
        metadata = infer_publication_metadata("2026-01-01")
        self.assertEqual(TimePrecision.DATE, metadata.precision)
        self.assertIsNone(metadata.exact_datetime)
        self.assertEqual("unknown", metadata.source_timezone)


if __name__ == "__main__":
    unittest.main()
