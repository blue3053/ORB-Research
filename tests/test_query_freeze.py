"""Pure budget, precheck, and freeze-manifest tests."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from src.censys.query_freeze import build_budget_schedule, build_freeze_manifest
from src.models import (
    DesignPrecheckStatus,
    QueryBudgetScheduleRecord,
    QueryClass,
    QueryCompositionType,
    QueryDesignPrecheckRecord,
    QueryDesignRecord,
    QueryDesignReviewRecord,
    ReviewerStatus,
)


class QueryFreezeTests(unittest.TestCase):
    def setUp(self):
        self.t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.design = QueryDesignRecord(
            design_id="design-1", query_id="qry-1", query_version="1",
            variant="primary", query_class=QueryClass.Q2_DERIVED,
            composition_type=QueryCompositionType.CTI_ONLY,
            clause_ids=["clause-1"], rendered_query='host.dns.names: "a.example"',
            query_hash="a" * 64, cutoff_at=self.t0,
            background_snapshot_ids=[], api_schema_version="api-v1",
            parser_version="parser-v1", normalizer_version="normalizer-v1",
            entity_resolution_version="entity-v1", config_hash="cfg",
            registered_at=self.t0 + timedelta(hours=1),
        )

    def test_budget_schedule_and_freeze_manifest_are_deterministic(self):
        schedule = build_budget_schedule(
            self.design, interval_hours=24, starts_at=self.t0 + timedelta(days=3),
            max_alerts_per_run=20, max_credits_per_run=100, max_pages_per_run=2,
            tie_break_rule="score_then_public_id", registered_at=self.t0 + timedelta(hours=2),
        )
        self.assertIsInstance(schedule, QueryBudgetScheduleRecord)
        precheck = QueryDesignPrecheckRecord(
            precheck_id="design-precheck-1", design_id=self.design.design_id,
            status=DesignPrecheckStatus.COMPLETE, page_count=1, hit_count=2,
            syntax_valid=True, raw_manifest_hash="b" * 64,
            recorded_at=self.t0 + timedelta(hours=3),
        )
        review = QueryDesignReviewRecord(
            review_id="design-review-1", design_id=self.design.design_id,
            precheck_id=precheck.precheck_id, decision=ReviewerStatus.ACCEPTED,
            reviewer_id="human", reviewed_at=self.t0 + timedelta(hours=4),
            notes_hash="c" * 64,
        )
        manifest = build_freeze_manifest(
            self.design, schedule, precheck, review, [],
            frozen_at=self.t0 + timedelta(days=1),
            valid_for_test_from=self.t0 + timedelta(days=3),
        )
        self.assertEqual(manifest, build_freeze_manifest(
            self.design, schedule, precheck, review, [],
            frozen_at=self.t0 + timedelta(days=1),
            valid_for_test_from=self.t0 + timedelta(days=3),
        ))

    def test_performance_claim_is_forbidden(self):
        with self.assertRaisesRegex(ValueError, "performance claims"):
            QueryDesignPrecheckRecord(
                precheck_id="bad", design_id="design-1",
                status=DesignPrecheckStatus.PENDING,
                performance_claim_allowed=True, recorded_at=self.t0,
            )


if __name__ == "__main__":
    unittest.main()
