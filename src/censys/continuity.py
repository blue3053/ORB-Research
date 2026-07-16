"""Q0 landmark timeline and continuity assessment without network access."""
from __future__ import annotations

from collections import Counter
from datetime import datetime

from src.models import (
    ContinuityAssessmentRecord,
    ContinuityReviewRecord,
    ContinuityStatus,
    HostObservationRecord,
    NegativeReason,
    Q0LandmarkRecord,
    Q0TimelineEntryRecord,
    ReviewerStatus,
    ServiceObservationRecord,
    TimelineObservationKind,
    utc,
)
from src.provenance import sha256_text


def _service_fingerprint_ids(service: ServiceObservationRecord) -> set[str]:
    values = {
        "cert": service.cert_sha256,
        "spki": service.spki_sha256,
        "jarm": service.jarm,
        "ja4": service.ja4,
        "ssh": service.ssh_key_hash,
        "banner": service.banner_hash,
        "http": service.http_title_hash,
    }
    if service.software_vendor or service.software_product or service.software_version:
        values["software"] = "|".join(filter(None, (
            service.software_vendor,
            service.software_product,
            service.software_version,
        )))
    return {
        f"fp-{sha256_text(kind + '|' + value)[:20]}"
        for kind, value in values.items() if value
    }


def materialize_q0_timeline(
    landmark: Q0LandmarkRecord,
    hosts: list[HostObservationRecord],
    services: list[ServiceObservationRecord],
) -> tuple[Q0TimelineEntryRecord, ...]:
    """Create deterministic timeline entries while leaving raw observations untouched."""

    services_by_observation: dict[str, list[ServiceObservationRecord]] = {}
    for service in services:
        services_by_observation.setdefault(service.observation_id, []).append(service)
    entries: list[Q0TimelineEntryRecord] = []
    for host in hosts:
        if host.indicator_id != landmark.indicator_id:
            continue
        effective = host.observed_at or host.collected_at
        if not landmark.observation_window_start <= effective <= landmark.observation_window_end:
            continue
        if host.host_observed:
            kind = TimelineObservationKind.POSITIVE
        else:
            kind = {
                NegativeReason.NOT_FOUND: TimelineObservationKind.NOT_FOUND,
                NegativeReason.NOT_SCANNED: TimelineObservationKind.MISSING_SCAN,
                NegativeReason.NO_RESPONSE: TimelineObservationKind.NO_RESPONSE,
                NegativeReason.API_ERROR: TimelineObservationKind.API_ERROR,
            }.get(host.negative_reason, TimelineObservationKind.MISSING_SCAN)
        fingerprints: set[str] = set()
        for service in services_by_observation.get(host.observation_id, []):
            fingerprints.update(_service_fingerprint_ids(service))
        material = f"{landmark.landmark_id}|{host.observation_id}"
        entries.append(Q0TimelineEntryRecord(
            timeline_entry_id=f"timeline-{sha256_text(material)[:20]}",
            landmark_id=landmark.landmark_id,
            observation_id=host.observation_id,
            observation_kind=kind,
            observed_at=host.observed_at,
            collected_at=host.collected_at,
            host_observed=host.host_observed,
            negative_reason=host.negative_reason,
            fingerprint_ids=sorted(fingerprints),
            raw_record_hash=host.raw_record_hash,
        ))
    return tuple(sorted(
        entries,
        key=lambda item: (item.observed_at or item.collected_at, item.collected_at,
                          item.observation_id),
    ))


def assess_continuity(
    landmark: Q0LandmarkRecord,
    entries: list[Q0TimelineEntryRecord],
    *,
    assessed_at: datetime,
    explicit_contradiction_observation_ids: list[str] | None = None,
) -> ContinuityAssessmentRecord:
    """Assess continuity conservatively; a current response alone remains unknown."""

    assessed = utc(assessed_at)
    ordered = sorted(
        entries,
        key=lambda item: (item.observed_at or item.collected_at, item.collected_at,
                          item.observation_id),
    )
    positives = [item for item in ordered if item.host_observed]
    latest = max(ordered, key=lambda item: item.collected_at) if ordered else None
    current_response = (
        None
        if latest is None or latest.observation_kind in {
            TimelineObservationKind.MISSING_SCAN, TimelineObservationKind.API_ERROR
        }
        else latest.host_observed
    )
    historical_positive_count = sum(
        1 for item in positives if latest is not None and item.observation_id != latest.observation_id
    )
    positive_sets = [set(item.fingerprint_ids) for item in positives if item.fingerprint_ids]
    stable = set.intersection(*positive_sets) if len(positive_sets) >= 2 else set()
    fingerprint_counts = Counter(
        fingerprint for item in positives for fingerprint in item.fingerprint_ids
    )
    conflicting = {
        fingerprint for fingerprint, count in fingerprint_counts.items()
        if count == 1 and len(positive_sets) >= 2 and not stable
    }
    contradictions = set(explicit_contradiction_observation_ids or [])
    evidence_ids = [item.observation_id for item in ordered]
    if not contradictions <= set(evidence_ids):
        raise ValueError("explicit contradiction references unknown timeline evidence")
    if contradictions:
        status = ContinuityStatus.CONTRADICTED
    elif historical_positive_count == 0:
        status = ContinuityStatus.UNKNOWN
    elif stable:
        status = ContinuityStatus.CONTINUOUS
    elif conflicting:
        status = ContinuityStatus.REASSIGNED
    else:
        status = ContinuityStatus.PROBABLE
    last_positive = max(
        ((item.observed_at or item.collected_at) for item in positives), default=None
    )
    assessment_material = "|".join([
        landmark.landmark_id,
        status.value,
        assessed.isoformat(),
        ",".join(evidence_ids),
    ])
    return ContinuityAssessmentRecord(
        assessment_id=f"continuity-{sha256_text(assessment_material)[:20]}",
        landmark_id=landmark.landmark_id,
        status=status,
        assessed_at=assessed,
        window_start=landmark.observation_window_start,
        window_end=landmark.observation_window_end,
        current_response=current_response,
        historical_positive_count=historical_positive_count,
        missing_scan_count=sum(
            1 for item in ordered
            if item.observation_kind is TimelineObservationKind.MISSING_SCAN
        ),
        last_positive_at=last_positive,
        stable_fingerprint_ids=sorted(stable),
        conflicting_fingerprint_ids=sorted(conflicting),
        evidence_observation_ids=evidence_ids,
        derived_pivot_allowed=status is ContinuityStatus.CONTINUOUS,
    )


def derived_pivot_allowed(
    assessment: ContinuityAssessmentRecord,
    review: ContinuityReviewRecord | None = None,
) -> bool:
    if review is not None and review.assessment_id != assessment.assessment_id:
        raise ValueError("continuity review does not match assessment")
    if review is not None and review.decision is ReviewerStatus.REJECTED:
        return False
    if assessment.status is ContinuityStatus.CONTINUOUS:
        return True
    return bool(
        assessment.status is ContinuityStatus.PROBABLE
        and review is not None
        and review.decision is ReviewerStatus.ACCEPTED
        and review.allow_probable
    )
