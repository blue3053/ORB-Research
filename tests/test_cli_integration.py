"""Q0 등록과 Censys 페이지 수집 CLI 통합 테스트.

목적: registry query ID가 live transport 경계와 execution ledger까지 끊김 없이 연결되는지 검증한다.
지원 RQ: RQ1∼RQ5 공통 실행 provenance.
설계: Censys transport만 가짜로 교체하고 실제 CLI·SQLite·불변 raw 저장 경로를 사용한다.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

if "yaml" not in sys.modules:
    yaml_stub = types.ModuleType("yaml")
    yaml_stub.safe_load = lambda value: {}
    sys.modules["yaml"] = yaml_stub

from src import cli
from src.censys.query_registry import QueryRegistry


class FakeLiveFetcher:
    def fetch(self, *, query, page_size, page_token, fields):
        return {"result": {"hits": [{"host": {
            "ip": "192.0.2.9", "last_updated_at": "2026-01-02T12:00:00Z",
            "autonomous_system": {"asn": 64500, "name": "Example AS", "bgp_prefix": "192.0.2.0/24"},
            "location": {"country_code": "KR"},
            "services": [{
                "port": 443, "transport_protocol": "tcp", "service_name": "HTTPS",
                "banner": "secret banner", "http": {"response": {"html_title": "Router Login"}},
                "tls": {"jarm": {"fingerprint": "a" * 62}},
            }],
        }}], "links": {}}}


class CliIntegrationTests(unittest.TestCase):
    def test_register_query_preserves_source_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "registry.sqlite"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, cli.main([
                    "register-query", "--db", str(db), "--version", "q2-v1",
                    "--class", "Q2_DERIVED",
                    "--query-text", "host.services.port=9960 and host.services.port=9961",
                    "--split", "development", "--config-hash", "cfg-q2",
                    "--source-indicator-id", "ioc-seed",
                    "--source-feature-id", "fp-portset",
                    "--source-feature-id", "fp-jarm",
                ]))

            query = QueryRegistry(db).get_query(json.loads(output.getvalue())["query_id"])

            self.assertEqual(["ioc-seed"], query.source_indicator_ids)
            self.assertEqual(["fp-portset", "fp-jarm"], query.source_feature_ids)

    def test_q0_registration_to_collection_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / "registry.sqlite"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, cli.main([
                    "register-q0", "--db", str(db), "--indicator-id", "ioc-1",
                    "--ip", "192.0.2.9", "--indicator-available-at", "2026-01-01T00:00:00Z",
                    "--registered-at", "2026-01-02T00:00:00Z", "--version", "1",
                    "--config-hash", "cfg",
                ]))
            query_id = json.loads(output.getvalue())["query_id"]
            output = io.StringIO()
            with patch.object(cli, "CensysQ0HostLookupFetcher", return_value=FakeLiveFetcher()):
                with contextlib.redirect_stdout(output):
                    self.assertEqual(0, cli.main([
                        "collect-censys", "--db", str(db), "--query-id", query_id,
                        "--raw-root", str(root / "raw"), "--split", "development",
                        "--cutoff-time", "2026-01-02T00:00:00Z",
                        "--executed-at", "2026-01-03T00:00:00Z",
                    ]))
            result = json.loads(output.getvalue())
            self.assertEqual(1, result["page_count"])
            self.assertEqual(1, result["execution"]["result_count"])
            self.assertEqual(
                "censys-platform-v3-host-lookup",
                result["execution"]["api_schema_version"],
            )
            self.assertFalse(result["idempotent_replay"])
            with QueryRegistry(db).connect() as connection:
                row = connection.execute("SELECT status FROM query_executions").fetchone()
            self.assertEqual("complete", row["status"])

            normalized_out = root / "normalized.json"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, cli.main([
                    "normalize-censys", "--db", str(db),
                    "--query-run-id", result["execution"]["query_run_id"],
                    "--raw-directory", result["raw_directory"],
                    "--collected-at", "2026-01-03T00:00:00Z",
                    "--out", str(normalized_out),
                ]))
            normalized = json.loads(output.getvalue())
            self.assertEqual(1, normalized["database_effects"]["hosts_inserted"])
            self.assertEqual(1, normalized["database_effects"]["services_inserted"])
            with QueryRegistry(db).connect() as connection:
                host = connection.execute("SELECT payload_json FROM host_observations").fetchone()
                service = connection.execute("SELECT payload_json FROM service_observations").fetchone()
            host_payload = json.loads(host["payload_json"])
            service_payload = json.loads(service["payload_json"])
            self.assertEqual("censys_host_updated_at", host_payload["observation_time_basis"])
            self.assertEqual("192.0.2.0/24", host_payload["prefix"])
            self.assertNotIn("secret banner", service["payload_json"])
            self.assertIsNotNone(service_payload["banner_hash"])
            self.assertIsNotNone(service_payload["http_title_hash"])

            fingerprint_out = root / "fingerprints.json"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, cli.main([
                    "extract-fingerprints", "--db", str(db),
                    "--query-run-id", result["execution"]["query_run_id"],
                    "--extractor-version", "fixture-v1",
                    "--out", str(fingerprint_out),
                ]))
            graph = json.loads(output.getvalue())
            self.assertEqual(4, graph["database_effects"]["fingerprints_inserted"])
            self.assertGreaterEqual(graph["database_effects"]["relations_inserted"], 4)
            with QueryRegistry(db).connect() as connection:
                fingerprint_payloads = "\n".join(
                    row[0] for row in connection.execute("SELECT payload_json FROM fingerprints")
                )
            self.assertNotIn("secret banner", fingerprint_payloads)

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, cli.main([
                    "extract-fingerprints", "--db", str(db),
                    "--query-run-id", result["execution"]["query_run_id"],
                    "--extractor-version", "fixture-v1",
                    "--out", str(fingerprint_out),
                ]))
            replay = json.loads(output.getvalue())
            self.assertEqual(0, replay["database_effects"]["fingerprints_inserted"])
            self.assertEqual(0, replay["database_effects"]["relations_inserted"])

            output = io.StringIO()
            with patch.object(cli, "CensysQ0HostLookupFetcher", return_value=FakeLiveFetcher()):
                with contextlib.redirect_stdout(output):
                    self.assertEqual(0, cli.main([
                        "collect-censys", "--db", str(db), "--query-id", query_id,
                        "--raw-root", str(root / "raw"), "--split", "development",
                        "--cutoff-time", "2026-01-02T00:00:00Z",
                        "--executed-at", "2026-01-03T00:00:00Z",
                    ]))
            self.assertTrue(json.loads(output.getvalue())["idempotent_replay"])


if __name__ == "__main__":
    unittest.main()
