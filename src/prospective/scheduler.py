"""Deterministic prospective observation scheduling for frozen queries."""
from __future__ import annotations

from datetime import datetime, timedelta

from src.models import (
    EntityEpochRecord,
    ObservationOpportunityRecord,
    OpportunityStatus,
    QueryBudgetScheduleRecord,
    QueryFreezeManifestRecord,
    QueryRecord,
    QueryStatus,
    utc,
)
from src.provenance import canonical_json_hash


def materialize_due_opportunities(
    query: QueryRecord,
    freeze: QueryFreezeManifestRecord,
    schedule: QueryBudgetScheduleRecord,
    epochs: list[EntityEpochRecord],
    *,
    as_of: datetime,
    recorded_at: datetime,
) -> list[ObservationOpportunityRecord]:
    """Create one deterministic opportunity per due slot and intersecting entity epoch."""

    as_of = utc(as_of)
    recorded_at = utc(recorded_at)
    if query.status is not QueryStatus.FROZEN:
        raise ValueError("prospective schedule requires frozen query")
    if query.developed_from_split.value == "prospective_test":
        raise ValueError("query cannot be developed from prospective-test data")
    if query.query_id != freeze.query_id or query.query_hash != freeze.query_hash:
        raise ValueError("frozen query identity does not match freeze manifest")
    if schedule.schedule_id != freeze.schedule_id or schedule.design_id != freeze.design_id:
        raise ValueError("schedule identity does not match freeze manifest")
    if query.valid_for_test_from != freeze.valid_for_test_from:
        raise ValueError("query valid-for timestamp differs from freeze manifest")
    if as_of < schedule.starts_at:
        return []

    interval = timedelta(hours=schedule.interval_hours)
    count = int((as_of - schedule.starts_at) // interval) + 1
    records: list[ObservationOpportunityRecord] = []
    for index in range(count):
        due_at = schedule.starts_at + index * interval
        window_end = due_at + interval
        for epoch in sorted(epochs, key=lambda item: item.entity_epoch_id):
            if epoch.valid_from >= window_end or (epoch.valid_to and epoch.valid_to <= due_at):
                continue
            material = canonical_json_hash({
                "query_id": query.query_id,
                "query_version": query.query_version,
                "query_hash": query.query_hash,
                "schedule_id": schedule.schedule_id,
                "entity_epoch_id": epoch.entity_epoch_id,
                "due_at": due_at.isoformat(),
            })
            status = OpportunityStatus.DUE if as_of < window_end else OpportunityStatus.MISSED
            records.append(ObservationOpportunityRecord(
                opportunity_id="opportunity-" + material[:20],
                query_id=query.query_id,
                query_version=query.query_version,
                query_hash=query.query_hash,
                schedule_id=schedule.schedule_id,
                entity_epoch_id=epoch.entity_epoch_id,
                due_at=due_at,
                window_end=window_end,
                status=status,
                recorded_at=recorded_at,
                reason="schedule_window_elapsed" if status is OpportunityStatus.MISSED else None,
            ))
    return records


def execution_opportunity_status(
    opportunity: ObservationOpportunityRecord, *, executed_at: datetime, execution_status: str
) -> OpportunityStatus:
    """Map an execution ledger state to an explicit opportunity state."""

    executed_at = utc(executed_at)
    if execution_status == "failed":
        return OpportunityStatus.FAILED
    if execution_status in {"partial", "partial_max_pages"}:
        return OpportunityStatus.PARTIAL
    if execution_status != "complete":
        raise ValueError(f"unsupported prospective execution status: {execution_status}")
    return OpportunityStatus.LATE if executed_at >= opportunity.window_end else OpportunityStatus.COMPLETE
