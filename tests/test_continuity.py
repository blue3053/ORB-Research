"""Offline Q0 landmark timeline and continuity gate tests."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from src.censys.continuity import (
    assess_continuity,
    derived_pivot_allowed,
    materialize_q0_timeline,
)
from src.models import (
    AssertionRole,
    ContinuityReviewRecord,
    ContinuityStatus,
    HostObservationRecord,
    NegativeReason,
    ObservationTimeBasis,
    Q0LandmarkRecord,
    ReviewerStatus,
    ServiceObservationRecord,
    Transport,
)


class ContinuityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.landmark = Q0LandmarkRecord(
            landmark_id="landmark-1", indicator_id="ioc-1", assertion_id="assert-1",
            query_id="qry-1", landmark_reason="accepted_cti_seed",
            observation_window_start=self.t0,
            observation_window_end=self.t0 + timedelta(days=30),
            source_available_at=self.t0,
            registered_at=self.t0 + timedelta(hours=1),
        )

    def host(self, suffix: str, day: int, *, observed=True, negative=None):
        return HostObservationRecord(
            observation_id=f"obs-{suffix}", indicator_id="ioc-1",
            observed_at=(self.t0 + timedelta(days=day) if observed else None),
            collected_at=self.t0 + timedelta(days=day, hours=1),
            observation_time_basis=(ObservationTimeBasis.CENSYS_HOST_UPDATED_AT
                                    if observed else ObservationTimeBasis.UNAVAILABLE),
            host_observed=observed, negative_reason=negative,
            raw_record_hash=suffix * 64, query_run_id=f"run-{suffix}",
        )

    def service(self, suffix: str, cert: str | None):
        return ServiceObservationRecord(
            service_observation_id=f"svc-{suffix}", observation_id=f"obs-{suffix}",
            port=443, transport=Transport.TCP, protocol="https",
            observed_at=self.t0, observation_time_basis=(
                ObservationTimeBasis.CENSYS_SERVICE_OBSERVED_AT
            ), cert_sha256=cert, extractor_version="fixture",
        )

    def test_current_response_alone_is_unknown(self):
        entries = materialize_q0_timeline(
            self.landmark, [self.host("a", 2)], [self.service("a", "a" * 64)]
        )
        assessment = assess_continuity(
            self.landmark, list(entries), assessed_at=self.t0 + timedelta(days=3)
        )
        self.assertEqual(ContinuityStatus.UNKNOWN, assessment.status)
        self.assertFalse(assessment.derived_pivot_allowed)

    def test_out_of_order_stable_history_is_continuous(self):
        hosts = [self.host("b", 10), self.host("a", 2)]
        services = [self.service("b", "c" * 64), self.service("a", "c" * 64)]
        entries = materialize_q0_timeline(self.landmark, hosts, services)
        self.assertEqual(["obs-a", "obs-b"], [item.observation_id for item in entries])
        assessment = assess_continuity(
            self.landmark, list(entries), assessed_at=self.t0 + timedelta(days=11)
        )
        self.assertEqual(ContinuityStatus.CONTINUOUS, assessment.status)
        self.assertTrue(derived_pivot_allowed(assessment))

    def test_missing_scan_is_not_extinction_and_probable_needs_review(self):
        hosts = [
            self.host("a", 2), self.host("b", 10),
            self.host("m", 12, observed=False, negative=NegativeReason.NOT_SCANNED),
        ]
        entries = materialize_q0_timeline(self.landmark, hosts, [])
        assessment = assess_continuity(
            self.landmark, list(entries), assessed_at=self.t0 + timedelta(days=13)
        )
        self.assertEqual(ContinuityStatus.PROBABLE, assessment.status)
        self.assertIsNone(assessment.current_response)
        self.assertEqual(1, assessment.missing_scan_count)
        self.assertEqual(self.t0 + timedelta(days=10), assessment.last_positive_at)
        self.assertFalse(derived_pivot_allowed(assessment))
        review = ContinuityReviewRecord(
            review_id="review-1", assessment_id=assessment.assessment_id,
            decision=ReviewerStatus.ACCEPTED, reviewer_id="human-reviewer",
            reviewed_at=self.t0 + timedelta(days=13), allow_probable=True,
            notes_hash="f" * 64,
        )
        self.assertTrue(derived_pivot_allowed(assessment, review))

    def test_disjoint_strong_fingerprints_are_reassigned(self):
        entries = materialize_q0_timeline(
            self.landmark,
            [self.host("a", 2), self.host("b", 10)],
            [self.service("a", "a" * 64), self.service("b", "b" * 64)],
        )
        assessment = assess_continuity(
            self.landmark, list(entries), assessed_at=self.t0 + timedelta(days=11)
        )
        self.assertEqual(ContinuityStatus.REASSIGNED, assessment.status)
        self.assertFalse(derived_pivot_allowed(assessment))


if __name__ == "__main__":
    unittest.main()
