"""End-to-end offline Phase D design/review/freeze registry test."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.censys.query_composer import build_query_clause, build_query_design, render_query
from src.censys.query_freeze import (
    QueryDesignRegistry,
    build_budget_schedule,
    build_freeze_manifest,
)
from src.censys.query_registry import QueryRegistry
from src.models import (
    AssertionRole,
    ClauseOrigin,
    CooccurrenceScope,
    DatasetSplit,
    DesignPrecheckStatus,
    LogicalRole,
    PivotEligibilityReviewRecord,
    PivotPrecheckRecord,
    PivotPrecheckResultRecord,
    PrecheckStatus,
    QueryClass,
    QueryCompositionType,
    QueryDesignPrecheckRecord,
    QueryDesignReviewRecord,
    QueryExecutionRecord,
    QueryStatus,
    ReviewerStatus,
)


class PhaseDRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "registry.sqlite"
        self.registry = QueryRegistry(self.db)
        self.design_registry = QueryDesignRegistry(self.db)
        self.t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp.cleanup()

    def _eligible_precheck(self):
        q1 = self.registry.register_query(
            query_version="1", query_class=QueryClass.Q1_DIRECT_PIVOT,
            query_text='host.dns.names: "relay.example"',
            developed_from_split=DatasetSplit.DEVELOPMENT, config_hash="cfg",
            source_indicator_ids=["ioc-a"], source_assertion_ids=["assert-a"],
            source_available_at=self.t0, registered_at=self.t0,
        )
        precheck = PivotPrecheckRecord(
            precheck_id="precheck-a", query_id=q1.query_id, query_hash=q1.query_hash,
            assertion_ids=["assert-a"], node_id="node-a", roles=[AssertionRole.RELAY_ORB],
            scope="domain", cutoff_at=self.t0, source_available_at=self.t0,
            page_budget=2, registered_at=self.t0,
        )
        self.registry.register_pivot_precheck(precheck)
        execution = QueryExecutionRecord(
            query_run_id="run-precheck-a", query_id=q1.query_id, query_hash=q1.query_hash,
            cutoff_time=self.t0, executed_at=self.t0 + timedelta(hours=1),
            dataset_split=DatasetSplit.DEVELOPMENT, result_count=2,
            result_manifest_hash="a" * 64, api_schema_version="fixture", status="complete",
        )
        self.registry.record_execution(execution)
        result = PivotPrecheckResultRecord(
            result_id="precheck-result-a", precheck_id=precheck.precheck_id,
            collection_run_id=execution.query_run_id, status=PrecheckStatus.COMPLETE,
            page_count=1, hit_count=2, raw_manifest_hash="a" * 64,
            recorded_at=self.t0 + timedelta(hours=1),
        )
        self.registry.register_pivot_precheck_result(result)
        review = PivotEligibilityReviewRecord(
            review_id="precheck-review-a", precheck_id=precheck.precheck_id,
            decision=ReviewerStatus.ACCEPTED, reviewer_id="human",
            reviewed_at=self.t0 + timedelta(hours=2), reason_code="bounded_specific",
            notes_hash="b" * 64,
        )
        self.registry.register_pivot_eligibility_review(review)
        return precheck

    def test_design_review_manifest_and_freeze_gate(self):
        source = self._eligible_precheck()
        clause = build_query_clause(
            feature_origin=ClauseOrigin.CTI_DIRECT,
            logical_role=LogicalRole.REQUIRED, cooccurrence_scope=CooccurrenceScope.HOST,
            query_field="host.dns.names", canonical_value="relay.example",
            available_at=self.t0, canonicalizer_version="v1",
            source_precheck_id=source.precheck_id, node_id="node-a",
        )
        rendered = render_query(
            [clause], composition_type=QueryCompositionType.CTI_ONLY,
            query_class=QueryClass.Q2_DERIVED, cutoff_at=self.t0,
            accepted_assertion_ids=set(), eligible_precheck_ids={source.precheck_id},
            eligible_features={},
        )
        query = self.registry.register_q2_from_prechecks(
            query_version="1", query_text=rendered, precheck_ids=[source.precheck_id],
            config_hash="cfg", registered_at=self.t0 + timedelta(hours=3),
        )
        design = build_query_design(
            query, [clause], variant="primary",
            composition_type=QueryCompositionType.CTI_ONLY, cutoff_at=self.t0,
            background_snapshot_ids=[], api_schema_version="api-v1",
            parser_version="parser-v1", normalizer_version="normalizer-v1",
            entity_resolution_version="entity-v1",
            registered_at=self.t0 + timedelta(hours=3),
        )
        self.design_registry.register_design(design, [clause])
        schedule = build_budget_schedule(
            design, interval_hours=24, starts_at=self.t0 + timedelta(days=3),
            max_alerts_per_run=20, max_credits_per_run=100, max_pages_per_run=2,
            tie_break_rule="score_then_public_id",
            registered_at=self.t0 + timedelta(hours=4),
        )
        self.design_registry.register_schedule(schedule)
        design_precheck = QueryDesignPrecheckRecord(
            precheck_id="design-precheck-a", design_id=design.design_id,
            status=DesignPrecheckStatus.COMPLETE, page_count=1, hit_count=2,
            syntax_valid=True, raw_manifest_hash="c" * 64,
            recorded_at=self.t0 + timedelta(hours=5),
        )
        self.design_registry.register_precheck(design_precheck)
        design_review = QueryDesignReviewRecord(
            review_id="design-review-a", design_id=design.design_id,
            precheck_id=design_precheck.precheck_id, decision=ReviewerStatus.ACCEPTED,
            reviewer_id="human", reviewed_at=self.t0 + timedelta(hours=6),
            notes_hash="d" * 64,
        )
        self.design_registry.register_review(design_review)
        self.registry.mark_validated(query.query_id)
        frozen_at = self.t0 + timedelta(days=1)
        valid_from = self.t0 + timedelta(days=3)
        manifest = build_freeze_manifest(
            design, schedule, design_precheck, design_review, [clause],
            frozen_at=frozen_at, valid_for_test_from=valid_from,
        )
        self.design_registry.register_freeze_manifest(manifest)
        frozen = self.registry.freeze_query(
            query.query_id, frozen_at=frozen_at, valid_for_test_from=valid_from
        )
        self.assertEqual(QueryStatus.FROZEN, frozen.status)
        report = self.design_registry.phase_d_gate_report()
        self.assertTrue(report["passed"], report["issues"])

        new_version = self.registry.register_q2_from_prechecks(
            query_version="2", query_text=rendered, precheck_ids=[source.precheck_id],
            config_hash="cfg", registered_at=self.t0 + timedelta(days=2),
        )
        self.registry.mark_validated(new_version.query_id)
        with self.assertRaisesRegex(ValueError, "freeze manifest"):
            self.registry.freeze_query(
                new_version.query_id, frozen_at=self.t0 + timedelta(days=2),
                valid_for_test_from=self.t0 + timedelta(days=3),
            )


if __name__ == "__main__":
    unittest.main()
