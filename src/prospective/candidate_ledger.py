"""Prospective time classification and append-only candidate materialization."""
from __future__ import annotations

from datetime import datetime

from src.models import (
    CandidateRecord,
    HostObservationRecord,
    ProspectiveObservationEventRecord,
    ProspectiveTimeStatus,
    QueryFreezeManifestRecord,
    ServiceObservationRecord,
)
from src.provenance import canonical_json_hash


def classify_observation_time(
    observed_at: datetime | None, valid_for_test_from: datetime
) -> ProspectiveTimeStatus:
    if observed_at is None:
        return ProspectiveTimeStatus.UNRESOLVED
    if observed_at < valid_for_test_from:
        return ProspectiveTimeStatus.PRE_FREEZE
    return ProspectiveTimeStatus.ELIGIBLE


def build_observation_event(
    host: HostObservationRecord,
    *,
    opportunity_id: str,
    entity_epoch_id: str,
    freeze: QueryFreezeManifestRecord,
    recorded_at: datetime,
    services: list[ServiceObservationRecord] | None = None,
) -> ProspectiveObservationEventRecord:
    services = services or []
    if any(item.observation_id != host.observation_id for item in services):
        raise ValueError("service record does not belong to prospective host observation")
    record_times = {host.observation_id: host.observed_at}
    record_times.update({item.service_observation_id: item.observed_at for item in services})
    record_statuses = {
        record_id: classify_observation_time(value, freeze.valid_for_test_from)
        for record_id, value in record_times.items()
    }
    if ProspectiveTimeStatus.UNRESOLVED in record_statuses.values():
        status = ProspectiveTimeStatus.UNRESOLVED
        effective_observed_at = None
    else:
        effective_observed_at = min(value for value in record_times.values() if value is not None)
        status = classify_observation_time(effective_observed_at, freeze.valid_for_test_from)
    material = canonical_json_hash({
        "opportunity_id": opportunity_id,
        "query_run_id": host.query_run_id,
        "observation_id": host.observation_id,
        "entity_epoch_id": entity_epoch_id,
        "raw_record_hash": host.raw_record_hash,
    })
    return ProspectiveObservationEventRecord(
        event_id="prospective-event-" + material[:20],
        opportunity_id=opportunity_id,
        query_run_id=host.query_run_id,
        observation_id=host.observation_id,
        entity_epoch_id=entity_epoch_id,
        indicator_id=host.indicator_id,
        observed_at=effective_observed_at,
        collected_at=host.collected_at,
        time_status=status,
        record_time_statuses=record_statuses,
        raw_record_hash=host.raw_record_hash,
        recorded_at=recorded_at,
    )


def build_candidate(
    event: ProspectiveObservationEventRecord,
    *,
    query_id: str,
    query_version: str,
    query_hash: str,
    discovery_feature_ids: list[str],
    discovery_source_family_ids: list[str],
) -> CandidateRecord:
    if event.time_status is not ProspectiveTimeStatus.ELIGIBLE:
        raise ValueError("only prospectively eligible observations can create candidates")
    if not event.observed_at:
        raise ValueError("eligible candidate event requires observed_at")
    material = canonical_json_hash({
        "query_id": query_id,
        "query_version": query_version,
        "query_hash": query_hash,
        "entity_epoch_id": event.entity_epoch_id,
    })
    return CandidateRecord(
        candidate_id="candidate-" + material[:20],
        query_id=query_id,
        query_version=query_version,
        query_hash=query_hash,
        entity_epoch_id=event.entity_epoch_id,
        indicator_id=event.indicator_id,
        first_candidate_at=event.observed_at,
        first_observation_event_id=event.event_id,
        discovery_feature_ids=sorted(set(discovery_feature_ids)),
        discovery_source_family_ids=sorted(set(discovery_source_family_ids)),
    )
