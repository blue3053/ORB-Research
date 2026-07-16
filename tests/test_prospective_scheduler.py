"""Stage 6 deterministic schedule and missed-run tests."""
from datetime import datetime, timedelta, timezone
import unittest

from src.models import (
    DatasetSplit, EntityEpochRecord, ObservationOpportunityRecord, OpportunityStatus,
    QueryBudgetScheduleRecord, QueryClass, QueryFreezeManifestRecord, QueryRecord, QueryStatus,
)
from src.prospective.scheduler import execution_opportunity_status, materialize_due_opportunities
from src.provenance import sha256_text


class ProspectiveSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
        query_hash = sha256_text("host.services.port: 443")
        self.query = QueryRecord(
            query_id="qry-1", query_version="v1", query_class=QueryClass.Q3_CLUSTER,
            query_text="host.services.port: 443", query_hash=query_hash,
            developed_from_split=DatasetSplit.DEVELOPMENT, registered_at=self.t0,
            frozen_at=self.t0 + timedelta(hours=1),
            valid_for_test_from=self.t0 + timedelta(hours=2), config_hash="cfg",
            status=QueryStatus.FROZEN,
        )
        self.schedule = QueryBudgetScheduleRecord(
            schedule_id="schedule-1", design_id="design-1", interval_hours=24,
            starts_at=self.t0 + timedelta(hours=2), max_alerts_per_run=10,
            max_credits_per_run=20, max_pages_per_run=2, tie_break_rule="indicator_id",
            registered_at=self.t0 + timedelta(hours=1),
        )
        self.freeze = QueryFreezeManifestRecord(
            freeze_manifest_id="freeze-1", query_id="qry-1", design_id="design-1",
            query_hash=query_hash, source_manifest_hash="a" * 64,
            query_cutoff_at=self.t0, schedule_id="schedule-1", review_id="review-1",
            frozen_at=self.t0 + timedelta(hours=1),
            valid_for_test_from=self.t0 + timedelta(hours=2),
        )
        self.epoch = EntityEpochRecord(
            entity_epoch_id="epoch-1", indicator_id="ioc-1", valid_from=self.t0,
            observation_ids=["obs-1"], identity_feature_ids=[], resolution_version="v1",
            available_at=self.t0,
        )

    def test_due_boundary_is_inclusive_and_deterministic(self):
        first = materialize_due_opportunities(
            self.query, self.freeze, self.schedule, [self.epoch],
            as_of=self.schedule.starts_at, recorded_at=self.schedule.starts_at,
        )
        second = materialize_due_opportunities(
            self.query, self.freeze, self.schedule, [self.epoch],
            as_of=self.schedule.starts_at, recorded_at=self.schedule.starts_at,
        )
        self.assertEqual(first, second)
        self.assertEqual(OpportunityStatus.DUE, first[0].status)

    def test_elapsed_window_is_explicitly_missed(self):
        records = materialize_due_opportunities(
            self.query, self.freeze, self.schedule, [self.epoch],
            as_of=self.schedule.starts_at + timedelta(hours=24),
            recorded_at=self.schedule.starts_at + timedelta(hours=24),
        )
        self.assertEqual([OpportunityStatus.MISSED, OpportunityStatus.DUE],
                         [item.status for item in records])
        self.assertEqual("schedule_window_elapsed", records[0].reason)

    def test_partial_and_late_execution_are_distinct(self):
        opportunity = materialize_due_opportunities(
            self.query, self.freeze, self.schedule, [self.epoch],
            as_of=self.schedule.starts_at, recorded_at=self.schedule.starts_at,
        )[0]
        self.assertEqual(OpportunityStatus.PARTIAL, execution_opportunity_status(
            opportunity, executed_at=opportunity.due_at, execution_status="partial_max_pages"
        ))
        self.assertEqual(OpportunityStatus.LATE, execution_opportunity_status(
            opportunity, executed_at=opportunity.window_end, execution_status="complete"
        ))


if __name__ == "__main__":
    unittest.main()
