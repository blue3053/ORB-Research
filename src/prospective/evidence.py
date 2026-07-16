"""Independent post-candidate evidence and adjudication policy."""
from __future__ import annotations

from src.models import (
    AdjudicationStatus,
    CandidateEvidenceRecord,
    CandidateRecord,
    EvidenceRole,
    ValidationAvailability,
)


def validate_evidence_independence(
    candidate: CandidateRecord, evidence: CandidateEvidenceRecord
) -> None:
    """Reject discovery reuse, same-family evidence, and evidence predating discovery."""

    if evidence.candidate_id != candidate.candidate_id:
        raise ValueError("evidence references a different candidate")
    if evidence.role is EvidenceRole.DISCOVERY:
        raise ValueError("query-match discovery evidence cannot validate a candidate")
    if evidence.availability is not ValidationAvailability.AVAILABLE:
        return
    if evidence.source_family_id in candidate.discovery_source_family_ids:
        raise ValueError("same source family cannot independently validate candidate")
    reused = sorted(set(evidence.feature_ids) & set(candidate.discovery_feature_ids))
    if reused:
        raise ValueError("discovery features cannot be reused for validation: " + ", ".join(reused))
    if evidence.available_at < candidate.first_candidate_at:
        raise ValueError("pre-candidate evidence cannot independently validate candidate")
    if evidence.observed_at and evidence.observed_at < candidate.first_candidate_at:
        raise ValueError("pre-candidate observation cannot independently validate candidate")


def recommended_status(evidence: list[CandidateEvidenceRecord]) -> AdjudicationStatus:
    """Return a conservative status; a human must still persist the adjudication."""

    available = [item for item in evidence if item.availability is ValidationAvailability.AVAILABLE]
    if any(item.role is EvidenceRole.CONTRADICTION for item in available):
        return AdjudicationStatus.CONTRADICTED
    supporting = [
        item for item in available
        if item.role is EvidenceRole.VALIDATION and item.supports_candidate is True
    ]
    opposing = [
        item for item in available
        if item.role is EvidenceRole.VALIDATION and item.supports_candidate is False
    ]
    if supporting and opposing:
        return AdjudicationStatus.CONTRADICTED
    if supporting:
        return AdjudicationStatus.POSITIVE
    if opposing:
        return AdjudicationStatus.NEGATIVE
    if evidence and all(
        item.availability is ValidationAvailability.UNAVAILABLE for item in evidence
    ):
        return AdjudicationStatus.UNOBSERVABLE
    return AdjudicationStatus.UNRESOLVED
