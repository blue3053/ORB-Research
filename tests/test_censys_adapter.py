"""ORB_Hunt_v5 Censys adapter 회귀 테스트.

목적: 기존 fail-closed query renderer를 network 없이 재사용하고 원천 hash를 확인한다.
지원 RQ: RQ3 Q1 baseline 및 이후 Q2/Q3 구성의 기반.
설계: 실제 외부 source module을 읽되 API client를 호출하지 않는다.
"""
from __future__ import annotations

import unittest
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from src.adapters.orbhunt_censys import OrbhuntCensysAdapter
from src.censys.query_registry import QueryRegistry
from src.models import (
    DatasetSplit, HostObservationRecord, ObservationTimeBasis, QueryClass,
    QueryExecutionRecord, QueryRecord, ServiceObservationRecord, Transport,
)


ORBHUNT = Path(r"D:\Gemini\ORB_Hunt_v5")
EXPECTED_HASHES = {
    "src/orbhunt/stages/censys_query.py": "DDCD516594D9975B4BEAB127CD6EEE45EF347386CDF7C9F157AFF2BF61C753B5",
    "src/orbhunt/stages/censys_collect.py": "BD4AC358CCBD5F603C9D58AB3960A39EED7769ED0F4A9B4D33E729896E64FB03",
    "src/orbhunt/schemas/models.py": "FDF31B2E4631DA200B108BEAF076677B23DEB11FB6C95A852D12E62389362979",
}


class CensysAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = OrbhuntCensysAdapter(ORBHUNT, EXPECTED_HASHES)

    def test_reuse_hashes_match(self) -> None:
        self.adapter.verify_reuse_files()

    def test_q1_renderer_reuses_fail_closed_template(self) -> None:
        config = {
            "templates": {"cert_sha256": {"field": "host.services.tls.fingerprint_sha256", "template": "host.services.tls.fingerprint_sha256: {pivot_value}"}},
            "safety": {"fail_closed_on_missing_field": True, "allow_raw_ip_query": False},
        }
        fingerprint = "a" * 64
        rendered = self.adapter.render_q1_direct_pivot("cert_sha256", fingerprint, config)
        self.assertIn(fingerprint, rendered)

    def test_q1_renderer_rejects_raw_ip(self) -> None:
        config = {"templates": {}, "safety": {"fail_closed_on_missing_field": True, "allow_raw_ip_query": False}}
        with self.assertRaises(ValueError):
            self.adapter.render_q1_direct_pivot("ip", "192.0.2.1", config)

    def test_q0_empty_page_creates_explicit_negative_observation(self) -> None:
        query = QueryRecord(
            query_id="qry-empty", query_version="1", query_class=QueryClass.Q0_SEED,
            query_text="host.ip = 192.0.2.1", query_hash="q" * 64,
            source_indicator_ids=["ioc-empty"], developed_from_split=DatasetSplit.DEVELOPMENT,
            registered_at=datetime(2026, 1, 1, tzinfo=timezone.utc), config_hash="cfg",
        )
        execution = QueryExecutionRecord(
            query_run_id="run-empty", query_id=query.query_id, query_hash=query.query_hash,
            cutoff_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            executed_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            dataset_split=DatasetSplit.DEVELOPMENT, result_count=0,
            result_manifest_hash="m" * 64, api_schema_version="v3", status="complete",
        )
        batch = self.adapter.normalize_cached_pages(
            page_records=[{"query_hash": query.query_hash, "response": {"result": {"hits": []}}}],
            query=query, execution=execution,
            collected_at=datetime(2026, 1, 2, tzinfo=timezone.utc), extractor_version="test",
        )
        self.assertEqual(1, len(batch.host_observations))
        self.assertFalse(batch.host_observations[0].host_observed)
        self.assertEqual("not_found", batch.host_observations[0].negative_reason.value)
        self.assertIsNone(batch.host_observations[0].observed_at)

    def test_q1_hit_creates_restricted_discovered_indicator(self) -> None:
        query = QueryRecord(
            query_id="qry-q1", query_version="1", query_class=QueryClass.Q1_DIRECT_PIVOT,
            query_text='host.dns.names: "relay.example.org"', query_hash="r" * 64,
            developed_from_split=DatasetSplit.DEVELOPMENT,
            registered_at=datetime(2026, 1, 1, tzinfo=timezone.utc), config_hash="cfg",
        )
        execution = QueryExecutionRecord(
            query_run_id="run-q1", query_id=query.query_id, query_hash=query.query_hash,
            cutoff_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            executed_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            dataset_split=DatasetSplit.DEVELOPMENT, result_count=1,
            result_manifest_hash="n" * 64, api_schema_version="v3", status="complete",
        )
        hit = {"host": {
            "ip": "198.51.100.7", "last_updated_at": "2026-01-01T12:00:00Z",
            "services": [{"port": 443, "transport_protocol": "tcp", "service_name": "HTTPS"}],
        }}
        batch = self.adapter.normalize_cached_pages(
            page_records=[{"query_hash": query.query_hash,
                           "response": {"result": {"hits": [hit]}}}],
            query=query, execution=execution,
            collected_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            extractor_version="test", public_id_hmac_key=b"k" * 32,
        )
        self.assertEqual(1, len(batch.discovered_indicators))
        self.assertEqual("restricted", batch.discovered_indicators[0].sensitivity.value)
        self.assertTrue(batch.discovered_indicators[0].public_id.startswith("pub-"))
        self.assertEqual(
            batch.discovered_indicators[0].indicator_id,
            batch.host_observations[0].indicator_id,
        )

    def test_q2_hit_reuses_known_ip_indicator_and_only_discovers_new_ip(self) -> None:
        query = QueryRecord(
            query_id="qry-q2", query_version="1", query_class=QueryClass.Q2_DERIVED,
            query_text="host.services.port=9960", query_hash="s" * 64,
            source_indicator_ids=["ioc-seed"], source_feature_ids=["fp-seed"],
            developed_from_split=DatasetSplit.DEVELOPMENT,
            registered_at=datetime(2026, 1, 1, tzinfo=timezone.utc), config_hash="cfg",
        )
        execution = QueryExecutionRecord(
            query_run_id="run-q2", query_id=query.query_id, query_hash=query.query_hash,
            cutoff_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            executed_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            dataset_split=DatasetSplit.DEVELOPMENT, result_count=2,
            result_manifest_hash="p" * 64, api_schema_version="v3", status="complete",
        )
        hits = [
            {"host": {"ip": "198.51.100.7", "services": []}},
            {"host": {"ip": "198.51.100.8", "services": []}},
        ]

        batch = self.adapter.normalize_cached_pages(
            page_records=[{"query_hash": query.query_hash,
                           "response": {"result": {"hits": hits}}}],
            query=query, execution=execution,
            collected_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            extractor_version="test", public_id_hmac_key=b"k" * 32,
            known_ip_indicator_ids={"198.51.100.7": "ioc-seed"},
        )

        self.assertEqual(
            {"198.51.100.8"},
            {item.normalized_value for item in batch.discovered_indicators},
        )
        self.assertEqual(
            ["ioc-seed", batch.discovered_indicators[0].indicator_id],
            [item.indicator_id for item in batch.host_observations],
        )

    def test_shared_jarm_relation_and_first_availability_are_deterministic(self) -> None:
        first = datetime(2026, 1, 1, tzinfo=timezone.utc)
        second = datetime(2026, 1, 2, tzinfo=timezone.utc)
        third = datetime(2026, 1, 3, tzinfo=timezone.utc)
        hosts = [
            HostObservationRecord(
                observation_id="obs-b", indicator_id="ioc-b", observed_at=second,
                collected_at=second, observation_time_basis=ObservationTimeBasis.CENSYS_HOST_UPDATED_AT,
                host_observed=True, raw_record_hash="b" * 64, query_run_id="run-b",
            ),
            HostObservationRecord(
                observation_id="obs-a", indicator_id="ioc-a", observed_at=first,
                collected_at=first, observation_time_basis=ObservationTimeBasis.CENSYS_HOST_UPDATED_AT,
                host_observed=True, raw_record_hash="a" * 64, query_run_id="run-a",
            ),
            HostObservationRecord(
                observation_id="obs-a-later", indicator_id="ioc-a", observed_at=third,
                collected_at=third,
                observation_time_basis=ObservationTimeBasis.CENSYS_HOST_UPDATED_AT,
                host_observed=True, raw_record_hash="c" * 64, query_run_id="run-a-later",
            ),
        ]
        services = [
            ServiceObservationRecord(
                service_observation_id="svc-b", observation_id="obs-b", port=443,
                transport=Transport.TCP, protocol="https", jarm="j" * 62,
                extractor_version="normalizer-v1",
            ),
            ServiceObservationRecord(
                service_observation_id="svc-a", observation_id="obs-a", port=443,
                transport=Transport.TCP, protocol="https", jarm="j" * 62,
                extractor_version="normalizer-v1",
            ),
            ServiceObservationRecord(
                service_observation_id="svc-a-later", observation_id="obs-a-later",
                port=443, transport=Transport.TCP, protocol="https", jarm="j" * 62,
                extractor_version="normalizer-v1",
            ),
        ]
        batch = self.adapter.derive_fingerprint_graph(
            host_observations=hosts, service_observations=services,
            extractor_version="fingerprint-v1",
        )
        jarm = next(item for item in batch.fingerprints if item.fingerprint_type.value == "jarm")
        self.assertEqual(first, jarm.first_available_at)
        with tempfile.TemporaryDirectory() as directory:
            registry = QueryRegistry(Path(directory) / "registry.sqlite")
            registry.register_fingerprint_graph(list(batch.fingerprints), list(batch.relations))
            shared = registry.build_shared_fingerprint_relations([jarm.fingerprint_id])
            self.assertEqual(1, len(shared))
            self.assertEqual("shares_jarm", shared[0].relation_type.value)
            self.assertEqual({"ioc-a", "ioc-b"}, {shared[0].src_id, shared[0].dst_id})
            self.assertEqual(second, shared[0].available_at)
            inserted = registry.register_fingerprint_graph([], shared)
            replay = registry.register_fingerprint_graph([], shared)
            self.assertEqual(1, inserted["relations_inserted"])
            self.assertEqual(0, replay["relations_inserted"])


if __name__ == "__main__":
    unittest.main()
