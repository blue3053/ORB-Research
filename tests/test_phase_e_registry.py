"""Offline Stage 6-7 additive registry integration tests."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from src.censys.feature_registry import FeatureRegistry
from src.censys.query_registry import QueryRegistry
from src.models import (
    AdjudicationStatus, CandidateAdjudicationRecord, CandidateEvidenceRecord,
    CandidateGradeEventRecord, DatasetSplit, EntityEpochRecord, EvidenceRole,
    HostObservationRecord, ObservationOpportunityRecord, ObservationTimeBasis,
    OpportunityStatus, QueryClass, QueryExecutionRecord, ValidationAvailability,
)
from src.prospective.candidate_ledger import build_candidate, build_observation_event
from src.prospective.registry import ProspectiveRegistry


class PhaseERegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "registry.sqlite"
        self.query_registry = QueryRegistry(self.db)
        self.feature_registry = FeatureRegistry(self.db)
        self.registry = ProspectiveRegistry(self.db)
        self.t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
        query = self.query_registry.register_query(
            query_version="v1", query_class=QueryClass.Q0_SEED,
            query_text="192.0.2.1", developed_from_split=DatasetSplit.DEVELOPMENT,
            config_hash="cfg", registered_at=self.t0,
        )
        self.query_registry.mark_validated(query.query_id)
        self.query = self.query_registry.freeze_query(
            query.query_id, frozen_at=self.t0 + timedelta(days=1),
            valid_for_test_from=self.t0 + timedelta(days=2),
        )
        self.execution = QueryExecutionRecord(
            query_run_id="run-e", query_id=self.query.query_id,
            query_hash=self.query.query_hash, cutoff_time=self.t0,
            executed_at=self.t0 + timedelta(days=2),
            dataset_split=DatasetSplit.PROSPECTIVE_TEST, result_count=1,
            result_manifest_hash="d" * 64, api_schema_version="fixture-v1", status="complete",
        )
        self.query_registry.record_execution(self.execution)
        self.host = HostObservationRecord(
            observation_id="obs-e", indicator_id="ioc-e",
            observed_at=self.t0 + timedelta(days=2),
            collected_at=self.t0 + timedelta(days=2, hours=1),
            observation_time_basis=ObservationTimeBasis.CENSYS_HOST_UPDATED_AT,
            host_observed=True, raw_record_hash="e" * 64, query_run_id="run-e",
        )
        self.query_registry.register_observations([self.host], [])
        self.epoch = EntityEpochRecord(
            entity_epoch_id="epoch-e", indicator_id="ioc-e", valid_from=self.t0,
            observation_ids=["obs-e"], identity_feature_ids=[], resolution_version="v1",
            available_at=self.t0 + timedelta(days=2, hours=1),
        )
        self.feature_registry.register_entity_epochs([self.epoch])
        self.opportunity = ObservationOpportunityRecord(
            opportunity_id="opp-e", query_id=self.query.query_id,
            query_version=self.query.query_version, query_hash=self.query.query_hash,
            schedule_id="schedule-e", entity_epoch_id="epoch-e",
            due_at=self.t0 + timedelta(days=2), window_end=self.t0 + timedelta(days=3),
            status=OpportunityStatus.DUE, recorded_at=self.t0 + timedelta(days=2),
        )
        self.registry.record_opportunity(self.opportunity)
        self.event = build_observation_event(
            self.host, opportunity_id="opp-e", entity_epoch_id="epoch-e",
            freeze=self._freeze(), recorded_at=self.t0 + timedelta(days=2, hours=2),
        )
        self.registry.register_observation_event(self.event)
        self.candidate = build_candidate(
            self.event, query_id=self.query.query_id, query_version=self.query.query_version,
            query_hash=self.query.query_hash, discovery_feature_ids=["feature-discovery"],
            discovery_source_family_ids=["family-discovery"],
        )
        self.registry.register_candidate(self.candidate)

    def tearDown(self):
        self.temp.cleanup()

    def _freeze(self):
        from src.models import QueryFreezeManifestRecord
        return QueryFreezeManifestRecord(
            freeze_manifest_id="freeze-e", query_id=self.query.query_id,
            design_id="design-e", query_hash=self.query.query_hash,
            source_manifest_hash="f" * 64, query_cutoff_at=self.t0,
            schedule_id="schedule-e", review_id="review-e",
            frozen_at=self.t0 + timedelta(days=1),
            valid_for_test_from=self.t0 + timedelta(days=2),
        )

    def _evidence(self, evidence_id="evidence-e", supports=True):
        return CandidateEvidenceRecord(
            evidence_id=evidence_id, candidate_id=self.candidate.candidate_id,
            role=EvidenceRole.VALIDATION, evidence_type="future_cti",
            source_id="doc-independent", source_family_id="family-independent",
            feature_ids=["feature-independent"],
            observed_at=self.t0 + timedelta(days=3),
            available_at=self.t0 + timedelta(days=3, hours=1),
            availability=ValidationAvailability.AVAILABLE, supports_candidate=supports,
            recorded_at=self.t0 + timedelta(days=3, hours=2),
        )

    def test_rerun_deduplicates_event_and_candidate(self):
        self.assertFalse(self.registry.register_observation_event(self.event))
        self.assertFalse(self.registry.register_candidate(self.candidate))
        later = self.candidate.model_copy(update={
            "first_candidate_at": self.candidate.first_candidate_at + timedelta(hours=1),
            "first_observation_event_id": "a-later-rerun-event",
        })
        self.assertFalse(self.registry.register_candidate(later))
        self.assertTrue(self.registry.phase_e_gate_report()["ok"])

    def test_same_opportunity_state_recomputed_later_is_noop(self):
        replay = self.opportunity.model_copy(update={
            "recorded_at": self.opportunity.recorded_at + timedelta(minutes=30)
        })
        self.assertFalse(self.registry.record_opportunity(replay))

    def test_partial_resume_complete_preserves_opportunity_events(self):
        partial = self.opportunity.model_copy(update={
            "status": OpportunityStatus.PARTIAL, "query_run_id": "run-e",
            "recorded_at": self.t0 + timedelta(days=2, hours=2),
        })
        complete = partial.model_copy(update={
            "status": OpportunityStatus.COMPLETE,
            "recorded_at": self.t0 + timedelta(days=2, hours=3),
        })
        self.assertTrue(self.registry.record_opportunity(partial))
        self.assertTrue(self.registry.record_opportunity(complete))
        with self.registry.connect() as connection:
            statuses = [row[0] for row in connection.execute(
                "SELECT status FROM opportunity_events WHERE opportunity_id=? ORDER BY recorded_at",
                (self.opportunity.opportunity_id,),
            )]
        self.assertEqual(["due", "partial", "complete"], statuses)

    def test_human_adjudication_and_grade_history_are_append_only(self):
        evidence = self._evidence()
        self.assertTrue(self.registry.register_evidence(evidence))
        adjudication = CandidateAdjudicationRecord(
            adjudication_id="adj-1", candidate_id=self.candidate.candidate_id,
            status=AdjudicationStatus.POSITIVE, reason_codes=["independent_future_cti"],
            evidence_ids=[evidence.evidence_id], adjudicator_id="human-reviewer",
            implementation_agent_id="implementation-agent",
            adjudicated_at=self.t0 + timedelta(days=4),
        )
        self.registry.register_adjudication(adjudication)
        first = CandidateGradeEventRecord(
            grade_event_id="grade-1", candidate_id=self.candidate.candidate_id,
            adjudication_id=adjudication.adjudication_id, grade="B",
            graded_at=self.t0 + timedelta(days=4, hours=1),
        )
        self.registry.register_grade_event(first)
        second = CandidateGradeEventRecord(
            grade_event_id="grade-2", candidate_id=self.candidate.candidate_id,
            adjudication_id=adjudication.adjudication_id, grade="A",
            previous_grade_event_id="grade-1",
            graded_at=self.t0 + timedelta(days=4, hours=2),
        )
        self.registry.register_grade_event(second)
        with self.assertRaisesRegex(ValueError, "latest grade history"):
            self.registry.register_grade_event(CandidateGradeEventRecord(
                grade_event_id="grade-bad", candidate_id=self.candidate.candidate_id,
                adjudication_id=adjudication.adjudication_id, grade="C",
                previous_grade_event_id="grade-1",
                graded_at=self.t0 + timedelta(days=4, hours=3),
            ))

    def test_same_family_validation_duplicate_is_rejected(self):
        self.registry.register_evidence(self._evidence("evidence-1"))
        with self.assertRaisesRegex(ValueError, "same source family"):
            self.registry.register_evidence(self._evidence("evidence-2"))

    def test_adjudicator_must_be_independent(self):
        with self.assertRaisesRegex(ValueError, "separate"):
            CandidateAdjudicationRecord(
                adjudication_id="adj", candidate_id=self.candidate.candidate_id,
                status=AdjudicationStatus.UNRESOLVED, reason_codes=["no_evidence"],
                adjudicator_id="same", implementation_agent_id="same",
                adjudicated_at=self.t0 + timedelta(days=4),
            )


if __name__ == "__main__":
    unittest.main()
