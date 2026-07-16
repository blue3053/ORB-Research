"""Bounded Q1 precheck, CTI-only composite, and Q2 source eligibility gates."""
from __future__ import annotations

from datetime import datetime

from src.censys.paginated_collection import CollectionResult
from src.models import (
    AcceptedPivotSource,
    AssertionRole,
    CtiCompositeRecord,
    PivotEligibilityReviewRecord,
    PivotPrecheckRecord,
    PivotPrecheckResultRecord,
    PrecheckStatus,
    QueryRecord,
    ReviewerStatus,
    utc,
)
from src.provenance import canonical_json_hash, sha256_text


_ROLE_GROUPS = (
    {AssertionRole.RELAY_ORB, AssertionRole.STAGING},
    {AssertionRole.CONTROLLER, AssertionRole.C2},
    {AssertionRole.SCANNER},
)
_BLOCKING_FLAGS = {"broad_shared", "unsupported", "restricted", "role_conflict"}


def _roles_compatible(roles: set[AssertionRole]) -> bool:
    return bool(roles) and any(roles <= group for group in _ROLE_GROUPS)


def build_cti_only_composite(
    sources: list[AcceptedPivotSource],
    *,
    node_id: str,
    window_start: datetime,
    window_end: datetime,
) -> CtiCompositeRecord:
    if len(sources) < 2:
        raise ValueError("CTI-only composite requires at least two assertions")
    if not node_id.strip():
        raise ValueError("CTI-only composite node_id is required")
    start, end = utc(window_start), utc(window_end)
    if end < start:
        raise ValueError("CTI-only composite window is reversed")
    if any(not start <= source.available_at <= end for source in sources):
        raise ValueError("CTI-only composite sources must share the declared time window")
    roles = {source.role for source in sources}
    if not _roles_compatible(roles):
        raise ValueError("CTI-only composite contains incompatible roles")
    assertion_ids = sorted({source.assertion_id for source in sources})
    if len(assertion_ids) != len(sources):
        raise ValueError("CTI-only composite contains duplicate assertions")
    material = "|".join([node_id, start.isoformat(), end.isoformat(), *assertion_ids])
    return CtiCompositeRecord(
        composite_id=f"cti-composite-{sha256_text(material)[:20]}",
        node_id=node_id,
        assertion_ids=assertion_ids,
        roles=sorted(roles, key=lambda item: item.value),
        window_start=start,
        window_end=end,
        available_at=max(source.available_at for source in sources),
    )


def register_precheck_definition(
    query: QueryRecord,
    sources: list[AcceptedPivotSource],
    *,
    node_id: str,
    cutoff_at: datetime,
    page_budget: int,
    registered_at: datetime,
    risk_flags: list[str] | None = None,
) -> PivotPrecheckRecord:
    if not sources:
        raise ValueError("precheck requires accepted assertion sources")
    cutoff, registered = utc(cutoff_at), utc(registered_at)
    available = max(source.available_at for source in sources)
    if available > cutoff:
        raise ValueError("precheck source is available after cutoff")
    if registered < available:
        raise ValueError("precheck predates source availability")
    flags = sorted(set(risk_flags or []))
    roles = sorted({source.role for source in sources}, key=lambda item: item.value)
    if not _roles_compatible(set(roles)):
        flags = sorted(set((*flags, "role_conflict")))
    scopes = {source.scope for source in sources}
    scope = next(iter(scopes)) if len(scopes) == 1 else "composite"
    material = "|".join([
        query.query_id, query.query_hash, node_id, cutoff.isoformat(),
        str(page_budget), *sorted(source.assertion_id for source in sources),
    ])
    return PivotPrecheckRecord(
        precheck_id=f"precheck-{sha256_text(material)[:20]}",
        query_id=query.query_id,
        query_hash=query.query_hash,
        assertion_ids=sorted(source.assertion_id for source in sources),
        node_id=node_id,
        roles=roles,
        scope=scope,
        risk_flags=flags,
        cutoff_at=cutoff,
        source_available_at=available,
        page_budget=page_budget,
        registered_at=registered,
    )


def precheck_result_from_collection(
    precheck: PivotPrecheckRecord,
    collection: CollectionResult,
    *,
    collection_run_id: str,
    recorded_at: datetime,
    hit_distribution: dict[str, int] | None = None,
    raw_manifest_hash: str | None = None,
    failure_reason: str | None = None,
) -> PivotPrecheckResultRecord:
    status = PrecheckStatus(collection.status)
    distribution = dict(sorted((hit_distribution or {}).items()))
    if any(value < 0 for value in distribution.values()):
        raise ValueError("precheck hit distribution cannot be negative")
    if status in {PrecheckStatus.COMPLETE, PrecheckStatus.PARTIAL_MAX_PAGES}:
        if not raw_manifest_hash:
            raise ValueError("completed or partial precheck requires raw manifest hash")
    if status is PrecheckStatus.FAILED and not failure_reason:
        raise ValueError("failed precheck requires failure_reason")
    material = canonical_json_hash({
        "precheck_id": precheck.precheck_id,
        "collection_run_id": collection_run_id,
        "status": status.value,
        "page_count": collection.page_count,
        "hit_count": collection.hit_count,
        "raw_manifest_hash": raw_manifest_hash,
    })
    return PivotPrecheckResultRecord(
        result_id=f"precheck-result-{material[:20]}",
        precheck_id=precheck.precheck_id,
        collection_run_id=collection_run_id,
        status=status,
        page_count=collection.page_count,
        hit_count=collection.hit_count,
        hit_distribution=distribution,
        raw_manifest_hash=raw_manifest_hash,
        recorded_at=recorded_at,
        failure_reason=failure_reason,
    )


def precheck_eligibility(
    precheck: PivotPrecheckRecord,
    result: PivotPrecheckResultRecord,
    review: PivotEligibilityReviewRecord | None,
) -> tuple[bool, str]:
    if result.precheck_id != precheck.precheck_id:
        raise ValueError("precheck result does not match definition")
    flags = set(precheck.risk_flags)
    if flags & _BLOCKING_FLAGS:
        return False, sorted(flags & _BLOCKING_FLAGS)[0]
    if result.status is not PrecheckStatus.COMPLETE:
        return False, f"precheck_{result.status.value}"
    if result.hit_count == 0:
        return False, "zero_hit_not_death_evidence"
    if review is None:
        return False, "human_review_required"
    if review.precheck_id != precheck.precheck_id:
        raise ValueError("eligibility review does not match precheck")
    if review.decision is not ReviewerStatus.ACCEPTED:
        return False, "human_review_rejected"
    return True, "eligible_q2_source"


def eligible_q2_precheck_ids(
    records: list[tuple[
        PivotPrecheckRecord,
        PivotPrecheckResultRecord,
        PivotEligibilityReviewRecord | None,
    ]],
) -> list[str]:
    return sorted(
        precheck.precheck_id
        for precheck, result, review in records
        if precheck_eligibility(precheck, result, review)[0]
    )
