"""Stage 7 independent evidence and conservative status tests."""
from datetime import datetime, timedelta, timezone
import unittest

from src.models import (
    AdjudicationStatus, CandidateEvidenceRecord, CandidateRecord, EvidenceRole,
    ValidationAvailability,
)
from src.prospective.evidence import recommended_status, validate_evidence_independence


class CandidateEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
        self.candidate = CandidateRecord(
            candidate_id="candidate", query_id="qry", query_version="v1",
            query_hash="a" * 64, entity_epoch_id="epoch", indicator_id="ioc",
            first_candidate_at=self.t0, first_observation_event_id="event",
            discovery_feature_ids=["f-discovery"],
            discovery_source_family_ids=["family-discovery"],
        )

    def evidence(self, **changes):
        values = dict(
            evidence_id="evidence", candidate_id="candidate", role=EvidenceRole.VALIDATION,
            evidence_type="future_cti", source_id="doc", source_family_id="family-independent",
            feature_ids=["f-independent"], observed_at=self.t0 + timedelta(hours=1),
            available_at=self.t0 + timedelta(hours=2),
            availability=ValidationAvailability.AVAILABLE, supports_candidate=True,
            recorded_at=self.t0 + timedelta(hours=3),
        )
        values.update(changes)
        return CandidateEvidenceRecord(**values)

    def test_discovery_feature_and_same_family_reuse_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "discovery features"):
            validate_evidence_independence(
                self.candidate, self.evidence(feature_ids=["f-discovery"])
            )
        with self.assertRaisesRegex(ValueError, "same source family"):
            validate_evidence_independence(
                self.candidate, self.evidence(source_family_id="family-discovery")
            )

    def test_pre_candidate_evidence_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "pre-candidate"):
            validate_evidence_independence(self.candidate, self.evidence(
                observed_at=self.t0 - timedelta(hours=2),
                available_at=self.t0 - timedelta(hours=1),
            ))

    def test_conflict_unresolved_and_unobservable_are_explicit(self):
        positive = self.evidence(evidence_id="positive")
        negative = self.evidence(evidence_id="negative", supports_candidate=False)
        unavailable = self.evidence(
            evidence_id="unavailable", observed_at=None, supports_candidate=None,
            availability=ValidationAvailability.UNAVAILABLE,
        )
        self.assertEqual(AdjudicationStatus.CONTRADICTED,
                         recommended_status([positive, negative]))
        self.assertEqual(AdjudicationStatus.UNRESOLVED, recommended_status([]))
        self.assertEqual(AdjudicationStatus.UNOBSERVABLE, recommended_status([unavailable]))


if __name__ == "__main__":
    unittest.main()
