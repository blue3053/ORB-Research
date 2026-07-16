"""Bounded Q1 precheck, composite, and Q2 eligibility tests."""
from __future__ import annotations

import unittest
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.censys.paginated_collection import CollectionResult
from src.censys.query_registry import QueryRegistry
from src.cti.pivot_precheck import (
    build_cti_only_composite,
    precheck_eligibility,
    precheck_result_from_collection,
    register_precheck_definition,
)
from src.models import (
    AcceptedPivotSource,
    AssertionRole,
    DatasetSplit,
    PivotEligibilityReviewRecord,
    QueryClass,
    QueryExecutionRecord,
    QueryRecord,
    QueryStatus,
    ReviewerStatus,
)


class PivotPrecheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.query = QueryRecord(
            query_id="qry-1", query_version="1",
            query_class=QueryClass.Q1_DIRECT_PIVOT,
            query_text='host.dns.names: "relay.example.org"', query_hash="q" * 64,
            source_indicator_ids=["ioc-1"], source_assertion_ids=["assert-1"],
            source_available_at=self.t0, developed_from_split=DatasetSplit.DEVELOPMENT,
            registered_at=self.t0, config_hash="cfg", status=QueryStatus.DRAFT,
        )

    def source(self, identifier: str, role=AssertionRole.RELAY_ORB, day=0):
        return AcceptedPivotSource(
            indicator_id=f"ioc-{identifier}", assertion_id=f"assert-{identifier}",
            review_id=f"review-{identifier}", scope="domain",
            value=f"{identifier}.example.org", role=role,
            available_at=self.t0 + timedelta(days=day),
            source_confidence=0.9, extraction_confidence=0.9, role_confidence=0.9,
        )

    def test_composite_requires_compatible_role_and_time_window(self):
        composite = build_cti_only_composite(
            [self.source("a"), self.source("b", AssertionRole.STAGING)],
            node_id="node-1", window_start=self.t0,
            window_end=self.t0 + timedelta(days=1),
        )
        self.assertEqual(2, len(composite.assertion_ids))
        with self.assertRaisesRegex(ValueError, "incompatible roles"):
            build_cti_only_composite(
                [self.source("a"), self.source("c", AssertionRole.C2)],
                node_id="node-1", window_start=self.t0,
                window_end=self.t0 + timedelta(days=1),
            )

    def test_partial_and_zero_hit_never_become_q2_sources(self):
        precheck = register_precheck_definition(
            self.query, [self.source("a")], node_id="node-1",
            cutoff_at=self.t0, page_budget=1, registered_at=self.t0,
        )
        partial = precheck_result_from_collection(
            precheck,
            CollectionResult("partial_max_pages", 1, 5, "next", self.query.query_hash),
            collection_run_id="run-1", recorded_at=self.t0,
            raw_manifest_hash="a" * 64,
        )
        self.assertEqual((False, "precheck_partial_max_pages"),
                         precheck_eligibility(precheck, partial, None))
        complete_zero = precheck_result_from_collection(
            precheck,
            CollectionResult("complete", 1, 0, None, self.query.query_hash),
            collection_run_id="run-1", recorded_at=self.t0 + timedelta(hours=1),
            raw_manifest_hash="b" * 64,
        )
        self.assertEqual((False, "zero_hit_not_death_evidence"),
                         precheck_eligibility(precheck, complete_zero, None))

    def test_complete_nonzero_precheck_requires_human_review(self):
        precheck = register_precheck_definition(
            self.query, [self.source("a")], node_id="node-1",
            cutoff_at=self.t0, page_budget=2, registered_at=self.t0,
        )
        complete = precheck_result_from_collection(
            precheck,
            CollectionResult("complete", 2, 3, None, self.query.query_hash),
            collection_run_id="run-1", recorded_at=self.t0 + timedelta(hours=1),
            hit_distribution={"asn:64500": 3}, raw_manifest_hash="c" * 64,
        )
        self.assertEqual((False, "human_review_required"),
                         precheck_eligibility(precheck, complete, None))
        review = PivotEligibilityReviewRecord(
            review_id="eligibility-review-1", precheck_id=precheck.precheck_id,
            decision=ReviewerStatus.ACCEPTED, reviewer_id="human-reviewer",
            reviewed_at=self.t0 + timedelta(hours=2), reason_code="bounded_specific",
            notes_hash="d" * 64,
        )
        self.assertEqual((True, "eligible_q2_source"),
                         precheck_eligibility(precheck, complete, review))

    def test_broad_shared_flag_is_fail_closed(self):
        precheck = register_precheck_definition(
            self.query, [self.source("a")], node_id="node-1",
            cutoff_at=self.t0, page_budget=1, registered_at=self.t0,
            risk_flags=["broad_shared"],
        )
        complete = precheck_result_from_collection(
            precheck, CollectionResult("complete", 1, 5, None, self.query.query_hash),
            collection_run_id="run-1", recorded_at=self.t0,
            raw_manifest_hash="e" * 64,
        )
        self.assertEqual((False, "broad_shared"),
                         precheck_eligibility(precheck, complete, None))

    def test_persisted_eligible_precheck_is_required_for_q2(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = QueryRegistry(Path(directory) / "registry.sqlite")
            query = registry.register_query(
                query_version="1", query_class=QueryClass.Q1_DIRECT_PIVOT,
                query_text=self.query.query_text,
                developed_from_split=DatasetSplit.DEVELOPMENT,
                config_hash="cfg", source_indicator_ids=["ioc-a"],
                source_assertion_ids=["assert-a"], source_available_at=self.t0,
                registered_at=self.t0,
            )
            precheck = register_precheck_definition(
                query, [self.source("a")], node_id="node-1", cutoff_at=self.t0,
                page_budget=2, registered_at=self.t0,
            )
            registry.register_pivot_precheck(precheck)
            with self.assertRaisesRegex(ValueError, "not eligible"):
                registry.register_q2_from_prechecks(
                    query_version="1", query_text='host.services.port: 443',
                    precheck_ids=[precheck.precheck_id], config_hash="cfg",
                    registered_at=self.t0 + timedelta(hours=3),
                )
            result = precheck_result_from_collection(
                precheck, CollectionResult("complete", 2, 3, None, query.query_hash),
                collection_run_id="run-1", recorded_at=self.t0 + timedelta(hours=1),
                raw_manifest_hash="f" * 64,
            )
            registry.record_execution(QueryExecutionRecord(
                query_run_id="run-1", query_id=query.query_id,
                query_hash=query.query_hash, cutoff_time=self.t0,
                executed_at=self.t0 + timedelta(hours=1),
                dataset_split=DatasetSplit.DEVELOPMENT, result_count=3,
                result_manifest_hash="f" * 64,
                api_schema_version="fixture", status="complete",
            ))
            registry.register_pivot_precheck_result(result)
            review = PivotEligibilityReviewRecord(
                review_id="eligibility-review-db", precheck_id=precheck.precheck_id,
                decision=ReviewerStatus.ACCEPTED, reviewer_id="human-reviewer",
                reviewed_at=self.t0 + timedelta(hours=2), reason_code="bounded_specific",
                notes_hash="a" * 64,
            )
            registry.register_pivot_eligibility_review(review)
            q2 = registry.register_q2_from_prechecks(
                query_version="1", query_text='host.services.port: 443',
                precheck_ids=[precheck.precheck_id], config_hash="cfg",
                registered_at=self.t0 + timedelta(hours=3),
            )
            self.assertEqual([precheck.precheck_id], q2.source_precheck_ids)
            self.assertEqual(QueryClass.Q2_DERIVED, q2.query_class)
            report = registry.phase_b_gate_report()
            self.assertTrue(report["passed"], report["issues"])
            self.assertEqual(1, report["counts"]["eligible_q2_prechecks"])


if __name__ == "__main__":
    unittest.main()
