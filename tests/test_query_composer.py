"""Deterministic Phase D composition and cutoff tests."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from src.censys.query_composer import (
    build_query_clause,
    build_query_design,
    render_query,
)
from src.models import (
    ClauseOrigin,
    CooccurrenceScope,
    DatasetSplit,
    FeatureCatalogRecord,
    FeatureFamily,
    FeatureStability,
    LogicalRole,
    QueryClass,
    QueryCompositionType,
    QueryRecord,
)
from src.provenance import sha256_text


class QueryComposerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.feature = FeatureCatalogRecord(
            feature_id="feature-1", feature_family=FeatureFamily.IDENTITY,
            feature_type="cert_sha256", canonical_value="a" * 64,
            canonical_value_hash=sha256_text("a" * 64),
            query_field="host.services.tls.certificates.leaf_data.fingerprint",
            canonicalizer_version="v1", extractor_version="v1",
            first_available_at=self.t0, stability=FeatureStability.STABLE,
        )

    def derived_clause(self, *, available=None):
        return build_query_clause(
            feature_origin=ClauseOrigin.DERIVED,
            logical_role=LogicalRole.REQUIRED,
            cooccurrence_scope=CooccurrenceScope.CERTIFICATE,
            query_field=self.feature.query_field,
            canonical_value=self.feature.canonical_value,
            available_at=available or self.t0,
            canonicalizer_version="v1", source_feature_id=self.feature.feature_id,
        )

    def test_render_and_design_are_deterministic_and_variant_separated(self):
        clause = self.derived_clause()
        rendered = render_query(
            [clause], composition_type=QueryCompositionType.DERIVED_ONLY,
            query_class=QueryClass.Q2_DERIVED,
            cutoff_at=self.t0 + timedelta(days=1), accepted_assertion_ids=set(),
            eligible_precheck_ids=set(), eligible_features={self.feature.feature_id: self.feature},
        )
        self.assertEqual(rendered, render_query(
            [clause], composition_type=QueryCompositionType.DERIVED_ONLY,
            query_class=QueryClass.Q2_DERIVED,
            cutoff_at=self.t0 + timedelta(days=1), accepted_assertion_ids=set(),
            eligible_precheck_ids=set(), eligible_features={self.feature.feature_id: self.feature},
        ))
        query = QueryRecord(
            query_id="qry-1", query_version="1", query_class=QueryClass.Q2_DERIVED,
            query_text=rendered, query_hash=sha256_text(rendered),
            source_feature_ids=[self.feature.feature_id],
            developed_from_split=DatasetSplit.DEVELOPMENT,
            registered_at=self.t0 + timedelta(days=2), config_hash="cfg",
        )
        first = build_query_design(
            query, [clause], variant="primary",
            composition_type=QueryCompositionType.DERIVED_ONLY,
            cutoff_at=self.t0 + timedelta(days=1), background_snapshot_ids=["ref-1"],
            api_schema_version="api-v1", parser_version="parser-v1",
            normalizer_version="normalizer-v1", entity_resolution_version="entity-v1",
            registered_at=self.t0 + timedelta(days=2),
        )
        sensitivity_query = query.model_copy(update={
            "query_id": "qry-2", "query_variant": "sensitivity"
        })
        second = build_query_design(
            sensitivity_query, [clause], variant="sensitivity",
            composition_type=QueryCompositionType.DERIVED_ONLY,
            cutoff_at=self.t0 + timedelta(days=1), background_snapshot_ids=["ref-1"],
            api_schema_version="api-v1", parser_version="parser-v1",
            normalizer_version="normalizer-v1", entity_resolution_version="entity-v1",
            registered_at=self.t0 + timedelta(days=2),
        )
        self.assertNotEqual(first.design_id, second.design_id)

    def test_unavailable_feature_and_wrong_composition_are_blocked(self):
        with self.assertRaisesRegex(ValueError, "feature leakage"):
            render_query(
                [self.derived_clause(available=self.t0 + timedelta(days=2))],
                composition_type=QueryCompositionType.DERIVED_ONLY,
                query_class=QueryClass.Q2_DERIVED, cutoff_at=self.t0,
                accepted_assertion_ids=set(), eligible_precheck_ids=set(),
                eligible_features={self.feature.feature_id: self.feature},
            )
        with self.assertRaisesRegex(ValueError, "composition"):
            render_query(
                [self.derived_clause()], composition_type=QueryCompositionType.CTI_ONLY,
                query_class=QueryClass.Q2_DERIVED,
                cutoff_at=self.t0 + timedelta(days=1), accepted_assertion_ids=set(),
                eligible_precheck_ids=set(), eligible_features={self.feature.feature_id: self.feature},
            )

    def test_different_cti_nodes_cannot_be_required_and_combined(self):
        clauses = [build_query_clause(
            feature_origin=ClauseOrigin.CTI_DIRECT,
            logical_role=LogicalRole.REQUIRED, cooccurrence_scope=CooccurrenceScope.HOST,
            query_field="host.dns.names", canonical_value=f"{node}.example",
            available_at=self.t0, canonicalizer_version="v1",
            source_assertion_id=f"assert-{node}", node_id=node,
        ) for node in ("node-a", "node-b")]
        with self.assertRaisesRegex(ValueError, "different nodes"):
            render_query(
                clauses, composition_type=QueryCompositionType.CTI_ONLY,
                query_class=QueryClass.Q2_DERIVED, cutoff_at=self.t0,
                accepted_assertion_ids={"assert-node-a", "assert-node-b"},
                eligible_precheck_ids=set(), eligible_features={},
            )


if __name__ == "__main__":
    unittest.main()
