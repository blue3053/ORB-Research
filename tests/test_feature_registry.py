"""Stage 4 registry reproducibility and human-promotion integration tests."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.censys.background import (
    assess_feature_eligibility,
    compute_feature_stat_snapshot,
    define_reference_set,
    materialize_reference_memberships,
)
from src.censys.continuity import assess_continuity, materialize_q0_timeline
from src.censys.feature_registry import FeatureRegistry
from src.censys.features import build_entity_epochs, extract_observation_features
from src.censys.query_registry import QueryRegistry
from src.censys.query_composer import build_query_clause, build_query_design, render_query
from src.censys.query_freeze import QueryDesignRegistry
from src.models import (
    DatasetSplit,
    ClauseOrigin,
    CooccurrenceScope,
    FeatureEligibilityReviewRecord,
    HostObservationRecord,
    ObservationTimeBasis,
    LogicalRole,
    Q0LandmarkRecord,
    QueryClass,
    QueryCompositionType,
    QueryExecutionRecord,
    ReviewerStatus,
    ServiceObservationRecord,
    Transport,
)
from src.provenance import canonical_json_hash


class FeatureRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "registry.sqlite"
        self.query_registry = QueryRegistry(self.db)
        self.feature_registry = FeatureRegistry(self.db)
        self.t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _host(self, suffix: str, indicator: str, day: int):
        return HostObservationRecord(
            observation_id=f"obs-{suffix}", indicator_id=indicator,
            observed_at=self.t0 + timedelta(days=day),
            collected_at=self.t0 + timedelta(days=day, hours=1),
            observation_time_basis=ObservationTimeBasis.CENSYS_HOST_UPDATED_AT,
            host_observed=True, raw_record_hash=suffix[0] * 64,
            query_run_id=f"run-{suffix}",
        )

    def _service(self, suffix: str, cert: str):
        return ServiceObservationRecord(
            service_observation_id=f"svc-{suffix}", observation_id=f"obs-{suffix}",
            port=443, transport=Transport.TCP, protocol="https", cert_sha256=cert,
            software_product="relaybox", extractor_version="fixture",
        )

    def _record_run(self, query, suffix: str, day: int):
        self.query_registry.record_execution(QueryExecutionRecord(
            query_run_id=f"run-{suffix}", query_id=query.query_id,
            query_hash=query.query_hash, cutoff_time=self.t0,
            executed_at=self.t0 + timedelta(days=day, hours=2),
            dataset_split=DatasetSplit.DEVELOPMENT, result_count=1,
            result_manifest_hash=(suffix[0] * 64), api_schema_version="fixture",
            status="complete",
        ))

    def test_reproducible_stat_and_review_are_required_for_eligible_feature(self):
        q0 = self.query_registry.register_query(
            query_version="1", query_class=QueryClass.Q0_SEED,
            query_text="192.0.2.1", developed_from_split=DatasetSplit.DEVELOPMENT,
            config_hash="cfg", source_indicator_ids=["ioc-anchor"],
            source_assertion_ids=["assert-anchor"], source_available_at=self.t0,
            registered_at=self.t0,
        )
        background_query = self.query_registry.register_query(
            query_version="1", query_class=QueryClass.Q3_CLUSTER,
            query_text="host.services.port: 443",
            developed_from_split=DatasetSplit.DEVELOPMENT,
            config_hash="cfg", registered_at=self.t0,
        )
        for suffix, query, day in (
            ("a", q0, 1), ("b", q0, 2),
            ("c", background_query, 1), ("d", background_query, 2),
        ):
            self._record_run(query, suffix, day)
        hosts = [
            self._host("a", "ioc-anchor", 1), self._host("b", "ioc-anchor", 2),
            self._host("c", "ioc-bg-c", 1), self._host("d", "ioc-bg-d", 2),
        ]
        services = [
            self._service("a", "q" * 64), self._service("b", "q" * 64),
            self._service("c", "x" * 64), self._service("d", "y" * 64),
        ]
        self.query_registry.register_observations(hosts, services)
        landmark = Q0LandmarkRecord(
            landmark_id="landmark-anchor", indicator_id="ioc-anchor",
            assertion_id="assert-anchor", query_id=q0.query_id,
            landmark_reason="accepted_cti_seed", observation_window_start=self.t0,
            observation_window_end=self.t0 + timedelta(days=4),
            source_available_at=self.t0, registered_at=self.t0,
        )
        self.query_registry.register_q0_landmark(landmark)
        timeline = list(materialize_q0_timeline(landmark, hosts[:2], services[:2]))
        self.query_registry.register_q0_timeline(timeline)
        continuity = assess_continuity(
            landmark, timeline, assessed_at=self.t0 + timedelta(days=4)
        )
        self.query_registry.register_continuity_assessment(continuity)
        self.assertIn(
            continuity.assessment_id,
            self.query_registry.derived_continuity_assessment_ids(),
        )

        batch = extract_observation_features(hosts, services, extractor_version="feature-v1")
        effects = self.feature_registry.register_feature_batch(
            list(batch.features), list(batch.observations)
        )
        self.assertGreater(effects["features_inserted"], 0)
        later_batch = extract_observation_features(
            [hosts[1]], [services[1]], extractor_version="feature-v1"
        )
        replay_effects = self.feature_registry.register_feature_batch(
            list(later_batch.features), list(later_batch.observations)
        )
        self.assertEqual(0, replay_effects["features_inserted"])
        epochs = build_entity_epochs(
            hosts, list(batch.observations), list(batch.features),
            resolution_version="epoch-v1",
        )
        self.assertEqual(3, self.feature_registry.register_entity_epochs(list(epochs)))
        reference = define_reference_set(
            reference_version="ref-v1", cutoff_at=self.t0 + timedelta(days=4),
            stratum={"protocol": "https", "ports": [443], "product": "relaybox"},
            sampling_frame="fixture background", snapshot_manifest_hash=canonical_json_hash([
                {"query_run_id": "run-c", "result_manifest_hash": "c" * 64},
                {"query_run_id": "run-d", "result_manifest_hash": "d" * 64},
            ]),
            source_query_run_ids=["run-c", "run-d"],
            registered_at=self.t0 + timedelta(days=4, hours=1),
        )
        self.feature_registry.register_reference_set(reference)
        memberships = materialize_reference_memberships(reference, hosts[2:], services[2:])
        self.feature_registry.register_reference_memberships(list(memberships))
        feature = next(item for item in batch.features
                       if item.feature_type == "cert_sha256" and item.canonical_value == "q" * 64)
        snapshot = compute_feature_stat_snapshot(
            feature, reference, list(memberships), list(batch.observations),
            anchor_observation_ids=["obs-a", "obs-b"],
            anchor_source_ids=[continuity.assessment_id],
            computed_at=self.t0 + timedelta(days=4, hours=1),
        )
        tampered = snapshot.model_copy(update={"anchor_numerator": 1})
        with self.assertRaisesRegex(ValueError, "not reproducible"):
            self.feature_registry.register_stat_snapshot(tampered)
        self.feature_registry.register_stat_snapshot(snapshot)
        assessment = assess_feature_eligibility(
            feature, snapshot, assessed_at=self.t0 + timedelta(days=4, hours=2)
        )
        self.feature_registry.register_eligibility_assessment(assessment)
        self.assertEqual([], self.feature_registry.eligible_feature_ids())
        review = FeatureEligibilityReviewRecord(
            review_id="feature-review-db", assessment_id=assessment.assessment_id,
            decision=ReviewerStatus.ACCEPTED, reviewer_id="human-reviewer",
            reviewed_at=self.t0 + timedelta(days=4, hours=3), notes_hash="a" * 64,
        )
        self.feature_registry.register_eligibility_review(review)
        self.assertEqual([feature.feature_id], self.feature_registry.eligible_feature_ids())
        report = self.feature_registry.phase_c_gate_report()
        self.assertTrue(report["passed"], report["issues"])

        clause = build_query_clause(
            feature_origin=ClauseOrigin.DERIVED,
            logical_role=LogicalRole.REQUIRED,
            cooccurrence_scope=CooccurrenceScope.CERTIFICATE,
            query_field=feature.query_field,
            canonical_value=feature.canonical_value,
            available_at=feature.first_available_at,
            canonicalizer_version=feature.canonicalizer_version,
            source_feature_id=feature.feature_id,
        )
        rendered = render_query(
            [clause], composition_type=QueryCompositionType.DERIVED_ONLY,
            query_class=QueryClass.Q2_DERIVED, cutoff_at=reference.cutoff_at,
            accepted_assertion_ids=set(), eligible_precheck_ids=set(),
            eligible_features={feature.feature_id: feature},
        )
        derived_query = self.query_registry.register_query(
            query_version="derived-v1", query_class=QueryClass.Q2_DERIVED,
            query_text=rendered, developed_from_split=DatasetSplit.DEVELOPMENT,
            config_hash="cfg", source_feature_ids=[feature.feature_id],
            source_available_at=feature.first_available_at,
            registered_at=self.t0 + timedelta(days=4, hours=4),
        )
        missing_background = build_query_design(
            derived_query, [clause], variant="primary",
            composition_type=QueryCompositionType.DERIVED_ONLY,
            cutoff_at=reference.cutoff_at, background_snapshot_ids=[],
            api_schema_version="api-v1", parser_version="parser-v1",
            normalizer_version="normalizer-v1", entity_resolution_version="epoch-v1",
            registered_at=self.t0 + timedelta(days=4, hours=4),
        )
        with self.assertRaisesRegex(ValueError, "background snapshot"):
            QueryDesignRegistry(self.db).register_design(missing_background, [clause])
        with_background = build_query_design(
            derived_query, [clause], variant="primary",
            composition_type=QueryCompositionType.DERIVED_ONLY,
            cutoff_at=reference.cutoff_at,
            background_snapshot_ids=[reference.reference_set_id],
            api_schema_version="api-v1", parser_version="parser-v1",
            normalizer_version="normalizer-v1", entity_resolution_version="epoch-v1",
            registered_at=self.t0 + timedelta(days=4, hours=4),
        )
        self.assertTrue(QueryDesignRegistry(self.db).register_design(with_background, [clause]))


if __name__ == "__main__":
    unittest.main()
