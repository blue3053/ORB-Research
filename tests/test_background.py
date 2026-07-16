"""Matched-background statistic and eligibility gate tests."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from src.censys.background import (
    assess_feature_eligibility,
    compute_feature_stat_snapshot,
    define_reference_set,
    feature_is_query_eligible,
    materialize_reference_memberships,
)
from src.censys.features import extract_observation_features
from src.models import (
    FeatureEligibilityReviewRecord,
    FeatureEligibilityStatus,
    HostObservationRecord,
    ObservationTimeBasis,
    ReviewerStatus,
    ServiceObservationRecord,
    Transport,
)


class BackgroundTests(unittest.TestCase):
    def setUp(self) -> None:
        self.t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def host(self, suffix: str, *, day=0, observed=True):
        return HostObservationRecord(
            observation_id=f"obs-{suffix}", indicator_id=f"ioc-{suffix}",
            observed_at=self.t0 + timedelta(days=day) if observed else None,
            collected_at=self.t0 + timedelta(days=day, hours=1),
            observation_time_basis=(ObservationTimeBasis.CENSYS_HOST_UPDATED_AT
                                    if observed else ObservationTimeBasis.UNAVAILABLE),
            host_observed=observed, raw_record_hash="a" * 64,
            query_run_id=f"run-{suffix}",
        )

    def service(self, suffix: str, cert: str, *, port=443, product="relaybox"):
        return ServiceObservationRecord(
            service_observation_id=f"svc-{suffix}", observation_id=f"obs-{suffix}",
            port=port, transport=Transport.TCP, protocol="https", cert_sha256=cert,
            software_product=product, extractor_version="fixture",
        )

    def reference(self):
        return define_reference_set(
            reference_version="ref-v1", cutoff_at=self.t0 + timedelta(days=5),
            stratum={"protocol": "https", "ports": [443], "product": "relaybox"},
            sampling_frame="fixture matched services", snapshot_manifest_hash="f" * 64,
            source_query_run_ids=["run-a", "run-b", "run-c", "run-d"],
            registered_at=self.t0 + timedelta(days=6),
        )

    def test_matched_denominator_excludes_mismatch_and_after_cutoff(self):
        hosts = [self.host("a"), self.host("b"), self.host("c", day=7)]
        services = [
            self.service("a", "x" * 64), self.service("b", "y" * 64, port=80),
            self.service("c", "z" * 64),
        ]
        memberships = materialize_reference_memberships(self.reference(), hosts, services)
        self.assertEqual(1, sum(item.observable for item in memberships))
        self.assertEqual(
            {"stratum_mismatch", "after_cutoff"},
            {item.exclusion_reason for item in memberships if not item.observable},
        )

    def test_statistics_keep_numerator_denominator_cutoff_and_source(self):
        anchor_hosts = [self.host("a"), self.host("b")]
        background_hosts = [self.host("c"), self.host("d")]
        services = [
            self.service("a", "q" * 64), self.service("b", "q" * 64),
            self.service("c", "z" * 64), self.service("d", "y" * 64),
        ]
        batch = extract_observation_features(
            anchor_hosts + background_hosts, services, extractor_version="feature-v1"
        )
        feature = next(item for item in batch.features
                       if item.feature_type == "cert_sha256" and item.canonical_value == "q" * 64)
        reference = self.reference()
        memberships = materialize_reference_memberships(reference, background_hosts, services)
        stats = compute_feature_stat_snapshot(
            feature, reference, list(memberships), list(batch.observations),
            anchor_observation_ids=["obs-a", "obs-b"],
            anchor_source_ids=["continuity-a", "continuity-b"],
            computed_at=self.t0 + timedelta(days=6),
        )
        self.assertEqual((2, 2), (stats.anchor_numerator, stats.anchor_denominator))
        self.assertEqual((0, 2), (stats.background_numerator, stats.background_denominator))
        self.assertEqual(1.0, stats.anchor_support)
        self.assertEqual(0.0, stats.background_prevalence)
        self.assertEqual(64, len(stats.source_manifest_hash))
        assessment = assess_feature_eligibility(
            feature, stats, assessed_at=self.t0 + timedelta(days=6)
        )
        self.assertEqual(FeatureEligibilityStatus.CANDIDATE, assessment.status)
        self.assertFalse(feature_is_query_eligible(assessment, None))
        review = FeatureEligibilityReviewRecord(
            review_id="feature-review-1", assessment_id=assessment.assessment_id,
            decision=ReviewerStatus.ACCEPTED, reviewer_id="human-reviewer",
            reviewed_at=self.t0 + timedelta(days=7), notes_hash="a" * 64,
        )
        self.assertTrue(feature_is_query_eligible(assessment, review))

    def test_shared_default_and_missing_background_are_blocked(self):
        hosts = [self.host("a"), self.host("b")]
        services = [self.service("a", "a" * 64, product="nginx"),
                    self.service("b", "b" * 64, product="nginx")]
        batch = extract_observation_features(hosts, services, extractor_version="feature-v1")
        feature = next(item for item in batch.features if item.feature_type == "software_product")
        stats = compute_feature_stat_snapshot(
            feature, self.reference(), [], list(batch.observations),
            anchor_observation_ids=["obs-a", "obs-b"],
            anchor_source_ids=["continuity-a", "continuity-b"],
            computed_at=self.t0 + timedelta(days=6),
        )
        assessment = assess_feature_eligibility(
            feature, stats, assessed_at=self.t0 + timedelta(days=6)
        )
        self.assertEqual(FeatureEligibilityStatus.BLOCKED, assessment.status)
        self.assertIn("shared_or_default", assessment.reason_codes)
        self.assertIn("matched_background_missing", assessment.reason_codes)

    def test_global_rarity_claim_is_rejected(self):
        payload = self.reference().model_dump()
        payload["claim_scope"] = "global_rarity"
        with self.assertRaisesRegex(ValueError, "global rarity"):
            type(self.reference()).model_validate(payload)


if __name__ == "__main__":
    unittest.main()
