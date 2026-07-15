"""Censys page_token 전체 수집·checkpoint 회귀 테스트.

목적: 첫 페이지만 수집하는 기존 한계를 제거하고 token loop를 fail-closed 처리하는지 검증한다.
지원 RQ: RQ1∼RQ5 Censys data completeness.
설계: 가짜 fetcher로 network 없이 REST envelope와 불변 page 파일을 검사한다.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.censys.paginated_collection import (
    CensysPlatformHttpFetcher,
    CensysQ0HostLookupFetcher,
    PaginatedCensysCollector,
)


class FakeFetcher:
    def __init__(self):
        self.tokens = []

    def fetch(self, *, query, page_size, page_token, fields):
        self.tokens.append(page_token)
        pages = {
            None: {"result": {"hits": [{"host": {"ip": "192.0.2.1"}}], "links": {"next": "t2"}}},
            "t2": {"result": {"hits": [{"host": {"ip": "192.0.2.2"}}], "links": {}}},
        }
        return pages[page_token]


class LoopFetcher:
    def fetch(self, *, query, page_size, page_token, fields):
        return {"result": {"hits": [], "links": {"next": "same"}}}


class FailingFetcher:
    def fetch(self, **kwargs):
        raise AssertionError("completed collection must not call the network fetcher")


class FakeHttpxResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self.payload


class FakeHttpxClient:
    def __init__(self, response, *, assertion=None, post_assertion=None, timeout=None):
        self.response = response
        self.assertion = assertion
        self.post_assertion = post_assertion
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def get(self, url, headers):
        if self.assertion:
            self.assertion(url, headers, self.timeout)
        return self.response

    def post(self, url, *, params, headers, json):
        if self.post_assertion:
            self.post_assertion(url, params, headers, json, self.timeout)
        return self.response


class PaginatedCollectionTests(unittest.TestCase):
    def test_global_search_uses_httpx_and_preserves_page_envelope(self):
        response = FakeHttpxResponse({
            "result": {"hits": [{"host_v1": {"resource": {"ip": "8.8.8.8"}}}],
                       "next_page_token": "next-token"}
        })

        def assert_post(url, params, headers, body, timeout):
            self.assertEqual(
                "https://api.platform.censys.io/v3/global/search/query", url
            )
            self.assertEqual({"organization_id": "fixture-org"}, params)
            self.assertEqual("Bearer fixture-token", headers["Authorization"])
            self.assertEqual("host.ip = 8.8.8.8", body["query"])
            self.assertEqual(1, body["page_size"])
            self.assertEqual(30.0, timeout)

        factory = lambda **kwargs: FakeHttpxClient(
            response, post_assertion=assert_post, **kwargs
        )
        with patch.dict(os.environ, {
            "ALLOW_LIVE_CENSYS": "1", "CENSYS_TOKEN": "fixture-token",
            "CENSYS_API_ID": "fixture-org",
        }):
            payload = CensysPlatformHttpFetcher(client_factory=factory).fetch(
                query="host.ip = 8.8.8.8", page_size=1,
                page_token=None, fields=None,
            )
        self.assertEqual("8.8.8.8", payload["result"]["hits"][0]["host_v1"]["resource"]["ip"])

    def test_global_search_surfaces_http_error_detail(self):
        response = FakeHttpxResponse(
            {"detail": "API Access role required"}, status_code=403
        )
        factory = lambda **kwargs: FakeHttpxClient(response, **kwargs)
        with patch.dict(os.environ, {
            "ALLOW_LIVE_CENSYS": "1", "CENSYS_TOKEN": "fixture-token",
        }):
            with self.assertRaisesRegex(RuntimeError, "403.*API Access"):
                CensysPlatformHttpFetcher(client_factory=factory).fetch(
                    query="host.services.port = 443", page_size=1,
                    page_token=None, fields=None,
                )

    def test_q0_host_lookup_wraps_resource_as_search_hit(self):
        def assert_request(url, headers, timeout):
            self.assertIn("149.248.3.38", url)
            self.assertIn("organization_id=fixture-org", url)
            self.assertEqual(30.0, timeout)
            self.assertEqual("Bearer fixture-token", headers["Authorization"])

        response = FakeHttpxResponse({
            "result": {
                "resource": {"ip": "149.248.3.38", "services": []},
                "extensions": {"ignored": True},
            }
        })
        factory = lambda **kwargs: FakeHttpxClient(
            response, assertion=assert_request, **kwargs
        )

        with patch.dict(os.environ, {
            "ALLOW_LIVE_CENSYS": "1", "CENSYS_TOKEN": "fixture-token",
            "CENSYS_API_ID": "fixture-org",
        }):
            payload = CensysQ0HostLookupFetcher(client_factory=factory).fetch(
                query="host.ip = 149.248.3.38", page_size=10,
                page_token=None, fields=None,
            )
        hit = payload["result"]["hits"][0]
        self.assertEqual("149.248.3.38", hit["host_v1"]["resource"]["ip"])
        self.assertEqual(200, payload["q0_host_lookup"]["http_status"])

    def test_q0_host_lookup_treats_404_as_empty_and_rejects_broad_query(self):
        response = FakeHttpxResponse({"detail": "absent"}, status_code=404)
        factory = lambda **kwargs: FakeHttpxClient(response, **kwargs)

        with patch.dict(os.environ, {
            "ALLOW_LIVE_CENSYS": "1", "CENSYS_TOKEN": "fixture-token",
        }):
            fetcher = CensysQ0HostLookupFetcher(client_factory=factory)
            payload = fetcher.fetch(
                query="host.ip = 203.0.113.9", page_size=10,
                page_token=None, fields=None,
            )
            with self.assertRaisesRegex(ValueError, "exact"):
                fetcher.fetch(
                    query="host.services.port = 443", page_size=10,
                    page_token=None, fields=None,
                )
        self.assertEqual([], payload["result"]["hits"])
        self.assertEqual(404, payload["q0_host_lookup"]["http_status"])

    def test_collects_all_pages_and_writes_checkpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            fetcher = FakeFetcher()
            result = PaginatedCensysCollector(fetcher).collect(
                query="host.ip = 192.0.2.1", run_directory=Path(directory)
            )
            self.assertEqual("complete", result.status)
            self.assertEqual(2, result.page_count)
            self.assertEqual(2, result.hit_count)
            self.assertEqual([None, "t2"], fetcher.tokens)
            checkpoints = (Path(directory) / "checkpoints.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(2, len(checkpoints))
            self.assertEqual(1, json.loads(checkpoints[0])["cumulative_hits"])
            self.assertTrue((Path(directory) / "page-000002.json").is_file())

    def test_detects_token_loop(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(RuntimeError):
            PaginatedCensysCollector(LoopFetcher()).collect(
                query="host.ip = 192.0.2.1", run_directory=Path(directory), max_pages=3
            )

    def test_resumes_partial_and_completed_run_is_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            first_fetcher = FakeFetcher()
            partial = PaginatedCensysCollector(first_fetcher).collect(
                query="host.ip = 192.0.2.1", run_directory=path, max_pages=1
            )
            self.assertEqual("partial_max_pages", partial.status)
            self.assertEqual([None], first_fetcher.tokens)

            second_fetcher = FakeFetcher()
            complete = PaginatedCensysCollector(second_fetcher).collect(
                query="host.ip = 192.0.2.1", run_directory=path
            )
            self.assertEqual("complete", complete.status)
            self.assertEqual(["t2"], second_fetcher.tokens)
            self.assertEqual(2, complete.hit_count)

            replay = PaginatedCensysCollector(FailingFetcher()).collect(
                query="host.ip = 192.0.2.1", run_directory=path
            )
            self.assertEqual(2, replay.page_count)
            self.assertEqual("complete", replay.status)


if __name__ == "__main__":
    unittest.main()
