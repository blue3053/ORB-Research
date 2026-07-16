"""Matched-reference construction, reproducible statistics, and feature eligibility."""
from __future__ import annotations

from datetime import datetime
from math import sqrt

from src.models import (
    FeatureCatalogRecord,
    FeatureEligibilityAssessmentRecord,
    FeatureEligibilityReviewRecord,
    FeatureEligibilityStatus,
    FeatureObservationRecord,
    FeatureStatSnapshotRecord,
    FeatureStability,
    HostObservationRecord,
    ReferenceMembershipRecord,
    ReferenceSetRecord,
    ReviewerStatus,
    ServiceObservationRecord,
    utc,
)
from src.provenance import canonical_json_hash, sha256_text


def define_reference_set(
    *,
    reference_version: str,
    cutoff_at: datetime,
    stratum: dict,
    sampling_frame: str,
    snapshot_manifest_hash: str,
    source_query_run_ids: list[str],
    registered_at: datetime,
) -> ReferenceSetRecord:
    cutoff, registered = utc(cutoff_at), utc(registered_at)
    material = canonical_json_hash({
        "reference_version": reference_version,
        "cutoff_at": cutoff.isoformat(),
        "stratum": stratum,
        "sampling_frame": sampling_frame,
        "snapshot_manifest_hash": snapshot_manifest_hash,
        "source_query_run_ids": sorted(set(source_query_run_ids)),
    })
    return ReferenceSetRecord(
        reference_set_id="reference-set-" + material[:20],
        reference_version=reference_version,
        cutoff_at=cutoff,
        stratum=stratum,
        sampling_frame=sampling_frame,
        snapshot_manifest_hash=snapshot_manifest_hash,
        source_query_run_ids=sorted(set(source_query_run_ids)),
        registered_at=registered,
    )


def materialize_reference_memberships(
    reference: ReferenceSetRecord,
    hosts: list[HostObservationRecord],
    services: list[ServiceObservationRecord],
) -> tuple[ReferenceMembershipRecord, ...]:
    services_by_host: dict[str, list[ServiceObservationRecord]] = {}
    for service in services:
        services_by_host.setdefault(service.observation_id, []).append(service)
    required_protocol = str(reference.stratum.get("protocol", "")).lower() or None
    required_ports = {int(value) for value in reference.stratum.get("ports", [])}
    required_product = str(reference.stratum.get("product", "")).lower() or None
    window = reference.stratum.get("time_window")
    window_start = window_end = None
    if window:
        window_start = datetime.fromisoformat(str(window["start"]).replace("Z", "+00:00"))
        window_end = datetime.fromisoformat(str(window["end"]).replace("Z", "+00:00"))
    output: list[ReferenceMembershipRecord] = []
    for host in sorted(hosts, key=lambda item: item.observation_id):
        effective = host.observed_at or host.collected_at
        reason: str | None = None
        if host.collected_at > reference.cutoff_at or effective > reference.cutoff_at:
            reason = "after_cutoff"
        elif not host.host_observed:
            reason = "not_observable"
        elif window_start and not window_start <= effective <= window_end:
            reason = "time_window_mismatch"
        else:
            candidates = services_by_host.get(host.observation_id, [])
            matched = any(
                (required_protocol is None or service.protocol.lower() == required_protocol)
                and (not required_ports or service.port in required_ports)
                and (required_product is None or (service.software_product or "").lower() == required_product)
                for service in candidates
            )
            if not matched:
                reason = "stratum_mismatch"
        membership_id = "reference-membership-" + sha256_text(
            reference.reference_set_id + "|" + host.observation_id
        )[:20]
        output.append(ReferenceMembershipRecord(
            membership_id=membership_id,
            reference_set_id=reference.reference_set_id,
            observation_id=host.observation_id,
            observable=reason is None,
            exclusion_reason=reason,
            matched_at=reference.registered_at,
        ))
    return tuple(output)


def _wilson_interval(numerator: int, denominator: int) -> tuple[float, float]:
    if denominator == 0:
        return 0.0, 1.0
    z = 1.959963984540054
    p = numerator / denominator
    denominator_term = 1 + z * z / denominator
    centre = (p + z * z / (2 * denominator)) / denominator_term
    margin = z * sqrt((p * (1 - p) + z * z / (4 * denominator)) / denominator)
    margin /= denominator_term
    return max(0.0, centre - margin), min(1.0, centre + margin)


def compute_feature_stat_snapshot(
    feature: FeatureCatalogRecord,
    reference: ReferenceSetRecord,
    memberships: list[ReferenceMembershipRecord],
    feature_observations: list[FeatureObservationRecord],
    *,
    anchor_observation_ids: list[str],
    anchor_source_ids: list[str] | None = None,
    computed_at: datetime,
    epsilon: float = 1e-9,
) -> FeatureStatSnapshotRecord:
    cutoff = reference.cutoff_at
    anchor_ids = sorted(set(anchor_observation_ids))
    observable_memberships = sorted(
        (item for item in memberships if item.reference_set_id == reference.reference_set_id
         and item.observable), key=lambda item: item.membership_id,
    )
    background_ids = {item.observation_id for item in observable_memberships}
    observed_with_feature = {
        item.observation_id for item in feature_observations
        if item.feature_id == feature.feature_id
        and item.available_at <= cutoff
        and (item.observed_at is None or item.observed_at <= cutoff)
    }
    anchor_num = len(set(anchor_ids) & observed_with_feature)
    anchor_den = len(anchor_ids)
    background_num = len(background_ids & observed_with_feature)
    background_den = len(background_ids)
    anchor_support = anchor_num / anchor_den if anchor_den else 0.0
    prevalence = background_num / background_den if background_den else 0.0
    lift = anchor_support / max(prevalence, epsilon)
    anchor_low, anchor_high = _wilson_interval(anchor_num, anchor_den)
    low, high = _wilson_interval(background_num, background_den)
    source_payload = {
        "feature_id": feature.feature_id,
        "reference_set_id": reference.reference_set_id,
        "cutoff_at": cutoff.isoformat(),
        "anchor_observation_ids": anchor_ids,
        "anchor_source_ids": sorted(set(anchor_source_ids or [])),
        "background_membership_ids": [item.membership_id for item in observable_memberships],
        "feature_observation_ids": sorted(
            item.feature_observation_id for item in feature_observations
            if item.feature_id == feature.feature_id
            and item.available_at <= cutoff
            and item.observation_id in (set(anchor_ids) | background_ids)
        ),
    }
    source_hash = canonical_json_hash(source_payload)
    snapshot_id = sha256_text(source_hash + "|" + utc(computed_at).isoformat())
    return FeatureStatSnapshotRecord(
        stat_snapshot_id="feature-stat-" + snapshot_id[:20],
        feature_id=feature.feature_id,
        reference_set_id=reference.reference_set_id,
        cutoff_at=cutoff,
        anchor_observation_ids=anchor_ids,
        anchor_source_ids=sorted(set(anchor_source_ids or [])),
        background_membership_ids=[item.membership_id for item in observable_memberships],
        anchor_numerator=anchor_num,
        anchor_denominator=anchor_den,
        background_numerator=background_num,
        background_denominator=background_den,
        anchor_support=anchor_support,
        background_prevalence=prevalence,
        reference_lift=lift,
        anchor_ci_low=anchor_low,
        anchor_ci_high=anchor_high,
        background_ci_low=low,
        background_ci_high=high,
        source_manifest_hash=source_hash,
        computed_at=computed_at,
    )


def assess_feature_eligibility(
    feature: FeatureCatalogRecord,
    stats: FeatureStatSnapshotRecord,
    *,
    assessed_at: datetime,
    min_distinct_anchors: int = 2,
    min_anchor_support: float = 0.5,
    max_background_prevalence: float = 0.1,
) -> FeatureEligibilityAssessmentRecord:
    reasons: list[str] = []
    if feature.first_available_at > stats.cutoff_at:
        reasons.append("available_after_cutoff")
    if feature.stability is FeatureStability.UNSTABLE:
        reasons.append("unstable_field")
    if feature.stability is FeatureStability.UNAVAILABLE or not feature.query_field:
        reasons.append("query_field_unavailable")
    if feature.shared_or_default:
        reasons.append("shared_or_default")
    if stats.anchor_numerator < min_distinct_anchors:
        reasons.append("insufficient_distinct_anchors")
    if stats.anchor_support < min_anchor_support:
        reasons.append("insufficient_anchor_support")
    if stats.background_denominator == 0:
        reasons.append("matched_background_missing")
    elif stats.background_prevalence > max_background_prevalence:
        reasons.append("background_too_common")
    status = (
        FeatureEligibilityStatus.BLOCKED if reasons
        else FeatureEligibilityStatus.CANDIDATE
    )
    material = canonical_json_hash({
        "feature_id": feature.feature_id,
        "stat_snapshot_id": stats.stat_snapshot_id,
        "reasons": sorted(reasons),
        "min_distinct_anchors": min_distinct_anchors,
        "min_anchor_support": min_anchor_support,
        "max_background_prevalence": max_background_prevalence,
        "assessed_at": utc(assessed_at).isoformat(),
    })
    return FeatureEligibilityAssessmentRecord(
        assessment_id="feature-eligibility-" + material[:20],
        feature_id=feature.feature_id,
        stat_snapshot_id=stats.stat_snapshot_id,
        status=status,
        reason_codes=sorted(reasons),
        min_distinct_anchors=min_distinct_anchors,
        min_anchor_support=min_anchor_support,
        max_background_prevalence=max_background_prevalence,
        assessed_at=assessed_at,
    )


def feature_is_query_eligible(
    assessment: FeatureEligibilityAssessmentRecord,
    review: FeatureEligibilityReviewRecord | None,
) -> bool:
    if review is not None and review.assessment_id != assessment.assessment_id:
        raise ValueError("feature review does not match assessment")
    return bool(
        assessment.status is FeatureEligibilityStatus.CANDIDATE
        and review is not None
        and review.decision is ReviewerStatus.ACCEPTED
        and review.reviewed_at >= assessment.assessed_at
    )
