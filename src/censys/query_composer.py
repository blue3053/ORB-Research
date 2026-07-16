"""Deterministic Stage 5 query composition with cutoff and provenance gates."""
from __future__ import annotations

import json
from datetime import datetime

from src.censys.query_lifecycle import ensure_features_available
from src.models import (
    ClauseOrigin,
    CooccurrenceScope,
    EvidenceRole,
    FeatureCatalogRecord,
    LogicalRole,
    QueryClass,
    QueryClauseRecord,
    QueryCompositionType,
    QueryDesignRecord,
    QueryOperator,
    QueryRecord,
    utc,
)
from src.provenance import canonical_json_hash, sha256_text


def build_query_clause(
    *,
    feature_origin: ClauseOrigin,
    logical_role: LogicalRole,
    cooccurrence_scope: CooccurrenceScope,
    query_field: str,
    canonical_value: str,
    available_at: datetime,
    canonicalizer_version: str,
    source_assertion_id: str | None = None,
    source_precheck_id: str | None = None,
    source_feature_id: str | None = None,
    node_id: str | None = None,
) -> QueryClauseRecord:
    operator = {
        LogicalRole.REQUIRED: QueryOperator.AND,
        LogicalRole.ALTERNATIVE: QueryOperator.OR,
        LogicalRole.EXCLUSION: QueryOperator.NOT,
    }.get(logical_role)
    if operator is None:
        raise ValueError("score-only clauses require a separately frozen score model")
    payload = {
        "feature_origin": feature_origin.value,
        "logical_role": logical_role.value,
        "cooccurrence_scope": cooccurrence_scope.value,
        "query_field": query_field.strip(),
        "canonical_value": canonical_value.strip(),
        "source_assertion_id": source_assertion_id,
        "source_precheck_id": source_precheck_id,
        "source_feature_id": source_feature_id,
        "node_id": node_id,
        "canonicalizer_version": canonicalizer_version,
    }
    return QueryClauseRecord(
        clause_id="query-clause-" + canonical_json_hash(payload)[:20],
        feature_origin=feature_origin,
        logical_role=logical_role,
        evidence_role=EvidenceRole.DISCOVERY,
        operator=operator,
        cooccurrence_scope=cooccurrence_scope,
        query_field=payload["query_field"],
        canonical_value=payload["canonical_value"],
        source_assertion_id=source_assertion_id,
        source_precheck_id=source_precheck_id,
        source_feature_id=source_feature_id,
        node_id=node_id,
        canonicalizer_version=canonicalizer_version,
        available_at=available_at,
    )


def _validate_composition(
    composition: QueryCompositionType,
    query_class: QueryClass,
    clauses: list[QueryClauseRecord],
) -> None:
    origins = {item.feature_origin for item in clauses}
    if query_class not in {QueryClass.Q2_DERIVED, QueryClass.Q3_CLUSTER}:
        raise ValueError("Stage 5 composer supports Q2/Q3 only")
    if query_class is QueryClass.Q3_CLUSTER and composition is not QueryCompositionType.Q3_GRAPH_EXPANSION:
        raise ValueError("Q3 query requires graph-expansion composition")
    expected = {
        QueryCompositionType.CTI_ONLY: {ClauseOrigin.CTI_DIRECT},
        QueryCompositionType.CTI_DERIVED: {ClauseOrigin.CTI_DIRECT, ClauseOrigin.DERIVED},
        QueryCompositionType.DERIVED_ONLY: {ClauseOrigin.DERIVED},
    }
    if composition is QueryCompositionType.Q3_GRAPH_EXPANSION:
        if query_class is not QueryClass.Q3_CLUSTER:
            raise ValueError("Q3 graph expansion requires Q3 query class")
        if not any(item.cooccurrence_scope is CooccurrenceScope.GRAPH_EDGE for item in clauses):
            raise ValueError("Q3 graph expansion requires graph-edge provenance")
    elif origins != expected[composition]:
        raise ValueError("query composition does not match clause origins")
    required_cti_nodes = {
        item.node_id for item in clauses
        if item.feature_origin is ClauseOrigin.CTI_DIRECT
        and item.logical_role is LogicalRole.REQUIRED
        and item.node_id
    }
    if len(required_cti_nodes) > 1:
        raise ValueError("required CTI clauses from different nodes cannot be AND-combined")


def render_query(
    clauses: list[QueryClauseRecord],
    *,
    composition_type: QueryCompositionType,
    query_class: QueryClass,
    cutoff_at: datetime,
    accepted_assertion_ids: set[str],
    eligible_precheck_ids: set[str],
    eligible_features: dict[str, FeatureCatalogRecord],
) -> str:
    if not clauses or len({item.clause_id for item in clauses}) != len(clauses):
        raise ValueError("query clauses are empty or duplicated")
    _validate_composition(composition_type, query_class, clauses)
    cutoff = utc(cutoff_at)
    ensure_features_available([item.available_at for item in clauses], cutoff)
    for clause in clauses:
        if clause.feature_origin is ClauseOrigin.CTI_DIRECT:
            assertion_ok = (
                clause.source_assertion_id is not None
                and clause.source_assertion_id in accepted_assertion_ids
            )
            precheck_ok = (
                clause.source_precheck_id is not None
                and clause.source_precheck_id in eligible_precheck_ids
            )
            if not assertion_ok and not precheck_ok:
                raise ValueError("query uses unaccepted CTI clause provenance")
        else:
            feature = eligible_features.get(clause.source_feature_id or "")
            if feature is None:
                raise ValueError("query uses feature without accepted eligibility review")
            if feature.query_field != clause.query_field:
                raise ValueError("derived clause query field differs from feature catalog")
            if feature.canonical_value != clause.canonical_value:
                raise ValueError("derived clause value differs from feature catalog")
            if feature.first_available_at != clause.available_at:
                raise ValueError("derived clause availability differs from feature catalog")
            ensure_features_available([feature.first_available_at], cutoff)
    required = sorted(
        (item for item in clauses if item.logical_role is LogicalRole.REQUIRED),
        key=lambda item: item.clause_id,
    )
    alternatives = sorted(
        (item for item in clauses if item.logical_role is LogicalRole.ALTERNATIVE),
        key=lambda item: item.clause_id,
    )
    exclusions = sorted(
        (item for item in clauses if item.logical_role is LogicalRole.EXCLUSION),
        key=lambda item: item.clause_id,
    )
    if not required and not alternatives:
        raise ValueError("query requires a positive clause")

    def expression(item: QueryClauseRecord) -> str:
        return f"{item.query_field}: {json.dumps(item.canonical_value, ensure_ascii=False)}"

    parts = [expression(item) for item in required]
    if alternatives:
        parts.append("(" + " OR ".join(expression(item) for item in alternatives) + ")")
    parts.extend("NOT (" + expression(item) + ")" for item in exclusions)
    return " AND ".join(parts)


def build_query_design(
    query: QueryRecord,
    clauses: list[QueryClauseRecord],
    *,
    variant: str,
    composition_type: QueryCompositionType,
    cutoff_at: datetime,
    background_snapshot_ids: list[str],
    api_schema_version: str,
    parser_version: str,
    normalizer_version: str,
    entity_resolution_version: str,
    registered_at: datetime,
) -> QueryDesignRecord:
    if query.query_hash != sha256_text(query.query_text):
        raise ValueError("registered query text/hash mismatch")
    if query.query_variant != variant:
        raise ValueError("query registry variant differs from design variant")
    material = canonical_json_hash({
        "query_id": query.query_id,
        "variant": variant,
        "composition_type": composition_type.value,
        "clause_ids": sorted(item.clause_id for item in clauses),
        "cutoff_at": utc(cutoff_at).isoformat(),
        "background_snapshot_ids": sorted(set(background_snapshot_ids)),
        "versions": [api_schema_version, parser_version, normalizer_version,
                     entity_resolution_version],
    })
    return QueryDesignRecord(
        design_id="query-design-" + material[:20],
        query_id=query.query_id,
        query_version=query.query_version,
        variant=variant,
        query_class=query.query_class,
        composition_type=composition_type,
        clause_ids=sorted(item.clause_id for item in clauses),
        rendered_query=query.query_text,
        query_hash=query.query_hash,
        cutoff_at=cutoff_at,
        background_snapshot_ids=sorted(set(background_snapshot_ids)),
        api_schema_version=api_schema_version,
        parser_version=parser_version,
        normalizer_version=normalizer_version,
        entity_resolution_version=entity_resolution_version,
        config_hash=query.config_hash,
        registered_at=registered_at,
    )
