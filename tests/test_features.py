"""Stage 4 deterministic feature and entity-epoch tests."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from src.censys.features import build_entity_epochs, extract_observation_features
from src.models import (
    FeatureStability,
    HostObservationRecord,
    ObservationTimeBasis,
    ServiceObservationRecord,
    Transport,
)


class FeatureExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def host(self, suffix: str, day: int = 0) -> HostObservationRecord:
        return HostObservationRecord(
            observation_id=f"obs-{suffix}", indicator_id="ioc-1",
            observed_at=self.t0 + timedelta(days=day),
            collected_at=self.t0 + timedelta(days=day, hours=1),
            observation_time_basis=ObservationTimeBasis.CENSYS_HOST_UPDATED_AT,
            host_observed=True, asn=64500, prefix="192.0.2.0/24",
            raw_record_hash="a" * 64, query_run_id=f"run-{suffix}",
        )

    def service(self, suffix: str, cert: str, *, product="relaybox"):
        return ServiceObservationRecord(
            service_observation_id=f"svc-{suffix}", observation_id=f"obs-{suffix}",
            port=443, transport=Transport.TCP, protocol="https",
            observed_at=self.t0, observation_time_basis=(
                ObservationTimeBasis.CENSYS_SERVICE_OBSERVED_AT
            ), cert_sha256=cert, jarm="1" * 62,
            banner_hash="b" * 64, software_product=product,
            software_version="1.2.3", extractor_version="fixture",
        )

    def test_feature_ids_are_deterministic_and_unqueryable_hash_is_preserved(self):
        hosts = [self.host("a")]
        services = [self.service("a", "c" * 64)]
        first = extract_observation_features(hosts, services, extractor_version="feature-v1")
        second = extract_observation_features(hosts, services, extractor_version="feature-v1")
        self.assertEqual(first, second)
        banner = next(item for item in first.features if item.feature_type == "banner_hash")
        self.assertEqual(FeatureStability.UNAVAILABLE, banner.stability)
        self.assertIsNone(banner.query_field)
        self.assertTrue(any(item.source_fingerprint_id for item in first.features
                            if item.feature_type == "cert_sha256"))

    def test_identity_change_splits_epoch_and_same_identity_merges(self):
        hosts = [self.host("a", 0), self.host("b", 1), self.host("c", 2)]
        services = [
            self.service("a", "a" * 64), self.service("b", "a" * 64),
            self.service("c", "c" * 64),
        ]
        batch = extract_observation_features(hosts, services, extractor_version="feature-v1")
        epochs = build_entity_epochs(
            hosts, list(batch.observations), list(batch.features),
            resolution_version="epoch-v1",
        )
        self.assertEqual(2, len(epochs))
        self.assertEqual(["obs-a", "obs-b"], epochs[0].observation_ids)
        self.assertEqual(["obs-c"], epochs[1].observation_ids)

    def test_common_product_is_marked_shared_default(self):
        batch = extract_observation_features(
            [self.host("a")], [self.service("a", "d" * 64, product="nginx")],
            extractor_version="feature-v1",
        )
        product = next(item for item in batch.features if item.feature_type == "software_product")
        self.assertTrue(product.shared_or_default)


if __name__ == "__main__":
    unittest.main()
