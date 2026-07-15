"""Brave CTI 기간 고정·offset pagination 회귀 테스트.

목적: 등록 기간과 공식 페이지 한계가 지켜지고 최소 metadata만 다음 단계로 전달되는지 검증한다.
지원 RQ: RQ1·RQ4 systematic/prospective CTI source discovery.
설계: HTTP 호출을 override한 fixture backend로 API key·network 없이 검색 계약을 검사한다.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from src.cti.brave_search import BraveProtocolSearchBackend


class FixtureBraveBackend(BraveProtocolSearchBackend):
    def __init__(self):
        super().__init__(date_from="2026-01-01", date_to="2026-07-13", count=20, max_pages=3)
        self.offsets = []

    def _fetch_page(self, query, offset):
        self.offsets.append(offset)
        if offset == 0:
            return {"web": {"results": [{
                "title": "Report", "url": "https://example.org/report",
                "page_age": "2026-01-02", "description": "must not be persisted",
            }], "more_results_available": True}}
        return {"web": {"results": [], "more_results_available": False}}


class BraveSearchTests(unittest.TestCase):
    def test_fixed_window_pagination_and_minimal_metadata(self):
        with patch.dict(os.environ, {
            "ALLOW_LIVE_CTI_SEARCH": "1", "BRAVE_SEARCH_API_KEY": "fixture-key"
        }):
            backend = FixtureBraveBackend()
            results = backend.search("ORB threat report")
        self.assertEqual("2026-01-01to2026-07-13", backend.freshness)
        self.assertEqual([0, 1], backend.offsets)
        self.assertEqual(1, len(results))
        self.assertNotIn("description", results[0])
        self.assertEqual("2026-01-02", results[0]["published_at"])

    def test_live_gate_is_required(self):
        with patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": "fixture-key"}, clear=True):
            with self.assertRaises(RuntimeError):
                BraveProtocolSearchBackend(date_from="2026-01-01", date_to="2026-01-02")


if __name__ == "__main__":
    unittest.main()
