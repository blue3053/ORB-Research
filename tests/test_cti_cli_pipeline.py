"""CTI 검색부터 Q1 계획까지 단계형 CLI 통합 테스트.

목적: 불변 artifact가 search·screening·snapshot·IoC 검증·pivot 등록을 연결하는지 검증한다.
지원 RQ: RQ1∼RQ4 CTI provenance와 Q0/Q1 분리.
설계: live backend/fetcher만 fixture로 교체하고 실제 CLI·SQLite·외부 pure renderer를 사용한다.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sqlite3
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
from src.cti.search_protocol import build_search_protocol
from src.cti.snapshots import FetchedDocument


class SearchBackend:
    name = "fixture-brave"

    def __init__(self, **kwargs):
        pass

    def search(self, query):
        return [{
            "title": "Threat report", "url": "https://research.badinfra.net/report",
            "published_at": "2026-01-01",
        }]


class SnapshotFetcher:
    def __init__(self, whitelist, **kwargs):
        pass

    def fetch(self, url):
        return FetchedDocument(url, "text/html", b"Uses relay[.]badinfra[.]net for control.")


class StructuredExtractor:
    def __init__(self, **kwargs):
        self.last_provenance = {
            "backend": "fixture-structured-tool",
            "model": kwargs["model"],
            "prompt_sha256": "f" * 64,
        }

    def extract(self, document_text):
        return [{
            "scope": "domain",
            "raw_form": "relay[.]badinfra[.]net",
            "observed_at": "2026-01-01",
            "context": "relay_node",
            "context_evidence": "Uses relay infrastructure.",
        }]


class CtiCliPipelineTests(unittest.TestCase):
    def test_stdout_is_reconfigured_to_utf8_when_supported(self):
        class ReconfigurableOutput:
            def __init__(self):
                self.settings = None

            def reconfigure(self, **kwargs):
                self.settings = kwargs

        output = ReconfigurableOutput()
        with patch.object(cli.sys, "stdout", output):
            cli._configure_stdout_utf8()
        self.assertEqual(
            {"encoding": "utf-8", "errors": "backslashreplace"}, output.settings
        )

    def test_existing_manifest_document_selection_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "existing.json"
            path.write_text(json.dumps([
                {"document_id": "doc-a"}, {"document_id": "doc-b"},
            ]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "provide --document-id"):
                cli._load_snapshot_metadata(path)
            self.assertEqual(
                "doc-b", cli._load_snapshot_metadata(path, "doc-b")["document_id"]
            )

    def test_existing_metadata_preserves_source_independence(self):
        metadata = {
            "document_id": "cti-doc-fixture",
            "final_url": "https://research.example/report",
            "publisher": "Research Example",
            "title": "Fixture report",
            "published_at": "2026-01-01T00:00:00Z",
            "retrieved_at": "2026-07-14T00:00:00Z",
            "content_sha256": "a" * 64,
            "acquisition_mode": "existing_curated",
            "source_independence": "commercial_cti_research",
        }

        document = cli._document_from_snapshot_metadata(metadata, None)

        self.assertEqual("commercial_cti_research", document.source_independence)

    def test_structured_extraction_cli_verifies_and_records_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot_path = root / "source.html"
            snapshot_path.write_bytes(
                b"<html><body>Uses relay[.]badinfra[.]net for control.</body></html>"
            )
            metadata = {
                "document_id": "cti-doc-fixture",
                "final_url": "https://research.badinfra.net/report",
                "publisher": "research.badinfra.net",
                "title": "Threat report",
                "published_at": "2026-01-01",
                "retrieved_at": "2026-07-13T02:00:00Z",
                "content_sha256": hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
                "snapshot_path": str(snapshot_path),
            }
            metadata_path = root / "metadata.json"
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            prompt_path = root / "prompt.md"
            prompt_path.write_text("fixture prompt", encoding="utf-8")
            output_path = root / "verified.json"
            with patch.object(cli, "AnthropicIndicatorExtractor", StructuredExtractor):
                cli.main([
                    "cti-extract-iocs",
                    "--snapshot-metadata", str(metadata_path),
                    "--cti-agent", r"D:\Claude\CTI-Agent",
                    "--prompt", str(prompt_path),
                    "--model", "fixture-model",
                    "--available-at", "2026-07-13T02:00:00Z",
                    "--out", str(output_path),
                ])
            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual("relay_node", result["indicators"][0]["context"])
            self.assertEqual(
                "fixture-structured-tool",
                result["extraction_provenance"]["backend"],
            )

    def test_artifacts_connect_search_to_q1_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / "registry.sqlite"
            protocol = build_search_protocol(
                version="1", target_date_from="2026-01-01", target_date_to="2026-07-13",
                target_publishers=["badinfra.net"], search_terms=["ORB report"],
                inclusion_rules=["technical"], exclusion_rules=["marketing"],
                deduplication_rule="canonical_url",
                registered_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
            )
            (root / "protocol.json").write_text(protocol.model_dump_json(), encoding="utf-8")
            (root / "watchlist.json").write_text('{"groups":[],"covert_networks":[]}', encoding="utf-8")
            with patch.object(cli, "BraveProtocolSearchBackend", SearchBackend):
                cli.main([
                    "cti-search", "--db", str(db), "--protocol", str(root / "protocol.json"),
                    "--watchlist", str(root / "watchlist.json"), "--cti-agent", r"D:\Claude\CTI-Agent",
                    "--manifest", str(root / "search.json"), "--whitelist", "badinfra.net",
                    "--executed-at", "2026-07-13T00:00:00Z",
                ])
            search = json.loads((root / "search.json").read_text(encoding="utf-8"))
            candidate_id = search["candidates"][0]["candidate_id"]
            decisions = [{
                "candidate_id": candidate_id, "decision": "include",
                "reason_code": "technical", "reviewer_id": "r1",
            }]
            (root / "decisions.json").write_text(json.dumps(decisions), encoding="utf-8")
            cli.main([
                "cti-screen", "--db", str(db), "--search-manifest", str(root / "search.json"),
                "--decisions", str(root / "decisions.json"),
                "--reviewed-at", "2026-07-13T01:00:00Z", "--out", str(root / "decision-map.json"),
            ])
            with patch.object(cli, "PassiveDocumentFetcher", SnapshotFetcher):
                cli.main([
                    "cti-snapshot", "--search-manifest", str(root / "search.json"),
                    "--decision-map", str(root / "decision-map.json"),
                    "--snapshot-root", str(root / "snapshots"), "--manifest", str(root / "snapshots.json"),
                    "--whitelist", "badinfra.net", "--retrieved-at", "2026-07-13T02:00:00Z",
                ])
            snapshot = json.loads((root / "snapshots.json").read_text(encoding="utf-8"))[0]
            candidates = [{
                "scope": "domain", "raw_form": "relay[.]badinfra[.]net",
                "observed_at": "2026-01-01T00:00:00Z", "context": "malicious",
                "context_evidence": "control endpoint",
            }]
            (root / "candidates.json").write_text(json.dumps(candidates), encoding="utf-8")
            cli.main([
                "cti-verify-iocs", "--snapshot-metadata", snapshot["metadata_path"],
                "--candidates", str(root / "candidates.json"), "--cti-agent", r"D:\Claude\CTI-Agent",
                "--available-at", "2026-07-13T02:00:00Z", "--out", str(root / "verified.json"),
            ])
            with patch.dict(os.environ, {"ORB_PUBLIC_ID_HMAC_KEY": "k" * 32}):
                cli.main([
                    "cti-register-indicators",
                    "--verified-manifest", str(root / "verified.json"),
                    "--snapshot-metadata", snapshot["metadata_path"],
                    "--db", str(db), "--ingested-at", "2026-07-13T02:30:00Z",
                    "--out", str(root / "indicator-registration.json"),
                ])
            registration = json.loads(
                (root / "indicator-registration.json").read_text(encoding="utf-8")
            )
            self.assertEqual(1, registration["indicator_count"])
            self.assertTrue(registration["public_indicator_ids"][0].startswith("pub-"))
            with patch.dict(os.environ, {"ORB_PUBLIC_ID_HMAC_KEY": "k" * 32}):
                cli.main([
                    "cti-register-indicators",
                    "--verified-manifest", str(root / "verified.json"),
                    "--snapshot-metadata", snapshot["metadata_path"],
                    "--db", str(db), "--ingested-at", "2026-07-13T02:30:00Z",
                    "--out", str(root / "indicator-registration.json"),
                ])
            with contextlib.closing(sqlite3.connect(db)) as connection:
                self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM indicators").fetchone()[0])
                self.assertEqual(
                    "candidate",
                    connection.execute("SELECT verdict FROM indicator_assertions").fetchone()[0],
                )
            template = {
                "templates": {"domain": {
                    "field": "host.dns.names", "template": 'host.dns.names: "{pivot_value}"'
                }},
                "safety": {"fail_closed_on_missing_field": True, "allow_raw_ip_query": False},
            }
            (root / "templates.yaml").write_text("fixture", encoding="utf-8")
            with patch.object(cli.yaml, "safe_load", return_value=template):
                cli.main([
                    "cti-plan-pivots", "--verified-manifest", str(root / "verified.json"),
                    "--db", str(db), "--orbhunt", r"D:\Gemini\ORB_Hunt_v5",
                    "--template-config", str(root / "templates.yaml"),
                    "--registered-at", "2026-07-13T03:00:00Z", "--version", "1",
                    "--config-hash", "cfg", "--out", str(root / "plans.json"),
                ])
            plans = json.loads((root / "plans.json").read_text(encoding="utf-8"))
            self.assertEqual("Q1_DIRECT_PIVOT", plans[0]["query_class"])
            self.assertEqual("registered_not_executed", plans[0]["status"])


if __name__ == "__main__":
    unittest.main()
