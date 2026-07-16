"""Additive SQLite registry for reproducible Stage 4 feature/background artifacts."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from src.censys.background import (
    assess_feature_eligibility,
    compute_feature_stat_snapshot,
    feature_is_query_eligible,
)
from src.censys.query_registry import QueryRegistry
from src.models import (
    EntityEpochRecord,
    FeatureCatalogRecord,
    FeatureEligibilityAssessmentRecord,
    FeatureEligibilityReviewRecord,
    FeatureEligibilityStatus,
    FeatureObservationRecord,
    FeatureStatSnapshotRecord,
    ReferenceMembershipRecord,
    ReferenceSetRecord,
    ReviewerStatus,
)
from src.provenance import canonical_json_hash


SCHEMA = """
CREATE TABLE IF NOT EXISTS feature_catalog (
  feature_id TEXT PRIMARY KEY,
  feature_family TEXT NOT NULL,
  feature_type TEXT NOT NULL,
  first_available_at TEXT NOT NULL,
  stability TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feature_observations (
  feature_observation_id TEXT PRIMARY KEY,
  feature_id TEXT NOT NULL REFERENCES feature_catalog(feature_id),
  observation_id TEXT NOT NULL REFERENCES host_observations(observation_id),
  query_run_id TEXT NOT NULL REFERENCES query_executions(query_run_id),
  available_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entity_epochs (
  entity_epoch_id TEXT PRIMARY KEY,
  indicator_id TEXT NOT NULL,
  valid_from TEXT NOT NULL,
  valid_to TEXT,
  available_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reference_sets (
  reference_set_id TEXT PRIMARY KEY,
  reference_version TEXT NOT NULL,
  cutoff_at TEXT NOT NULL,
  snapshot_manifest_hash TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reference_memberships (
  membership_id TEXT PRIMARY KEY,
  reference_set_id TEXT NOT NULL REFERENCES reference_sets(reference_set_id),
  observation_id TEXT NOT NULL REFERENCES host_observations(observation_id),
  observable INTEGER NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feature_stat_snapshots (
  stat_snapshot_id TEXT PRIMARY KEY,
  feature_id TEXT NOT NULL REFERENCES feature_catalog(feature_id),
  reference_set_id TEXT NOT NULL REFERENCES reference_sets(reference_set_id),
  cutoff_at TEXT NOT NULL,
  source_manifest_hash TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feature_eligibility_assessments (
  assessment_id TEXT PRIMARY KEY,
  feature_id TEXT NOT NULL REFERENCES feature_catalog(feature_id),
  stat_snapshot_id TEXT NOT NULL REFERENCES feature_stat_snapshots(stat_snapshot_id),
  status TEXT NOT NULL,
  assessed_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feature_eligibility_reviews (
  review_id TEXT PRIMARY KEY,
  assessment_id TEXT NOT NULL UNIQUE REFERENCES feature_eligibility_assessments(assessment_id),
  decision TEXT NOT NULL,
  reviewed_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
"""


class FeatureRegistry:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        # Ensure the Phase A/B base schema exists before Stage 4 foreign keys.
        with QueryRegistry(self.path).connect():
            pass
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _payload(record) -> str:
        return json.dumps(
            record.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )

    @staticmethod
    def _insert_immutable(
        connection: sqlite3.Connection,
        *,
        table: str,
        key_column: str,
        key: str,
        payload: str,
        sql: str,
        parameters: tuple,
    ) -> bool:
        row = connection.execute(
            f"SELECT payload_json FROM {table} WHERE {key_column}=?", (key,)
        ).fetchone()
        if row:
            if row[0] != payload:
                raise ValueError(f"{table} immutable ID collision: {key}")
            return False
        connection.execute(sql, parameters)
        return True

    def register_feature_batch(
        self,
        features: list[FeatureCatalogRecord],
        observations: list[FeatureObservationRecord],
    ) -> dict[str, int]:
        feature_ids = {item.feature_id for item in features}
        if any(item.feature_id not in feature_ids for item in observations):
            raise ValueError("feature observation references feature outside batch")
        counts = {"features_inserted": 0, "observations_inserted": 0}
        with self.connect() as connection:
            for feature in features:
                payload = self._payload(feature)
                existing_row = connection.execute(
                    "SELECT payload_json FROM feature_catalog WHERE feature_id=?",
                    (feature.feature_id,),
                ).fetchone()
                if existing_row:
                    existing = FeatureCatalogRecord.model_validate_json(existing_row[0])
                    if feature.first_available_at < existing.first_available_at:
                        raise ValueError("feature backfill predates registered first availability")
                    if feature.model_copy(update={
                        "first_available_at": existing.first_available_at
                    }) != existing:
                        raise ValueError("feature_catalog immutable ID collision: "
                                         + feature.feature_id)
                else:
                    connection.execute(
                        "INSERT INTO feature_catalog VALUES (?, ?, ?, ?, ?, ?)",
                        (feature.feature_id, feature.feature_family.value,
                         feature.feature_type, feature.first_available_at.isoformat(),
                         feature.stability.value, payload),
                    )
                    counts["features_inserted"] += 1
            for observation in observations:
                host = connection.execute(
                    "SELECT query_run_id, collected_at FROM host_observations WHERE observation_id=?",
                    (observation.observation_id,),
                ).fetchone()
                if host is None or host["query_run_id"] != observation.query_run_id:
                    raise ValueError("feature observation raw provenance mismatch")
                if observation.available_at.isoformat() != host["collected_at"]:
                    raise ValueError("feature availability must equal raw collection availability")
                payload = self._payload(observation)
                if self._insert_immutable(
                    connection, table="feature_observations",
                    key_column="feature_observation_id",
                    key=observation.feature_observation_id, payload=payload,
                    sql="INSERT INTO feature_observations VALUES (?, ?, ?, ?, ?, ?)",
                    parameters=(observation.feature_observation_id, observation.feature_id,
                                observation.observation_id, observation.query_run_id,
                                observation.available_at.isoformat(), payload),
                ):
                    counts["observations_inserted"] += 1
        return counts

    def register_entity_epochs(self, epochs: list[EntityEpochRecord]) -> int:
        inserted = 0
        with self.connect() as connection:
            for epoch in epochs:
                known = {
                    row[0] for row in connection.execute(
                        "SELECT observation_id FROM host_observations WHERE indicator_id=?",
                        (epoch.indicator_id,),
                    )
                }
                if not set(epoch.observation_ids) <= known:
                    raise ValueError("entity epoch references unknown indicator observations")
                feature_ids = {
                    row[0] for row in connection.execute(
                        "SELECT feature_id FROM feature_catalog WHERE feature_family='identity'"
                    )
                }
                if not set(epoch.identity_feature_ids) <= feature_ids:
                    raise ValueError("entity epoch references unknown identity features")
                payload = self._payload(epoch)
                if self._insert_immutable(
                    connection, table="entity_epochs", key_column="entity_epoch_id",
                    key=epoch.entity_epoch_id, payload=payload,
                    sql="INSERT INTO entity_epochs VALUES (?, ?, ?, ?, ?, ?)",
                    parameters=(epoch.entity_epoch_id, epoch.indicator_id,
                                epoch.valid_from.isoformat(),
                                epoch.valid_to.isoformat() if epoch.valid_to else None,
                                epoch.available_at.isoformat(), payload),
                ):
                    inserted += 1
        return inserted

    def register_reference_set(self, reference: ReferenceSetRecord) -> bool:
        payload = self._payload(reference)
        with self.connect() as connection:
            for query_run_id in reference.source_query_run_ids:
                execution = connection.execute(
                    "SELECT status, result_manifest_hash FROM query_executions "
                    "WHERE query_run_id=?", (query_run_id,)
                ).fetchone()
                if execution is None or execution[0] != "complete":
                    raise ValueError("reference set source run is missing or incomplete")
            execution_manifests = [
                {
                    "query_run_id": query_run_id,
                    "result_manifest_hash": connection.execute(
                        "SELECT result_manifest_hash FROM query_executions WHERE query_run_id=?",
                        (query_run_id,),
                    ).fetchone()[0],
                }
                for query_run_id in sorted(set(reference.source_query_run_ids))
            ]
            if canonical_json_hash(execution_manifests) != reference.snapshot_manifest_hash:
                raise ValueError("reference snapshot manifest is not reproducible from source runs")
            return self._insert_immutable(
                connection, table="reference_sets", key_column="reference_set_id",
                key=reference.reference_set_id, payload=payload,
                sql="INSERT INTO reference_sets VALUES (?, ?, ?, ?, ?)",
                parameters=(reference.reference_set_id, reference.reference_version,
                            reference.cutoff_at.isoformat(),
                            reference.snapshot_manifest_hash, payload),
            )

    def register_reference_memberships(
        self, memberships: list[ReferenceMembershipRecord]
    ) -> int:
        inserted = 0
        with self.connect() as connection:
            for membership in memberships:
                reference_row = connection.execute(
                    "SELECT payload_json FROM reference_sets WHERE reference_set_id=?",
                    (membership.reference_set_id,),
                ).fetchone()
                host_row = connection.execute(
                    "SELECT payload_json FROM host_observations WHERE observation_id=?",
                    (membership.observation_id,),
                ).fetchone()
                if reference_row is None or host_row is None:
                    raise ValueError("reference membership lacks set or raw observation")
                reference = ReferenceSetRecord.model_validate_json(reference_row[0])
                host_payload = json.loads(host_row[0])
                if host_payload["query_run_id"] not in reference.source_query_run_ids:
                    raise ValueError("reference membership is outside snapshot source runs")
                effective = host_payload.get("observed_at") or host_payload["collected_at"]
                if membership.observable and effective > reference.cutoff_at.isoformat():
                    raise ValueError("observable reference membership is after cutoff")
                payload = self._payload(membership)
                if self._insert_immutable(
                    connection, table="reference_memberships", key_column="membership_id",
                    key=membership.membership_id, payload=payload,
                    sql="INSERT INTO reference_memberships VALUES (?, ?, ?, ?, ?)",
                    parameters=(membership.membership_id, membership.reference_set_id,
                                membership.observation_id, int(membership.observable), payload),
                ):
                    inserted += 1
        return inserted

    def _eligible_anchor_sources(self) -> set[str]:
        registry = QueryRegistry(self.path)
        return set(registry.derived_continuity_assessment_ids()) | set(
            registry.eligible_q2_precheck_ids()
        )

    def eligible_observation_run_ids(self, source_ids: list[str]) -> set[str]:
        """Resolve eligible Q0/Q1 source decisions to their raw collection runs."""

        requested = set(source_ids)
        eligible = self._eligible_anchor_sources()
        blocked = requested - eligible
        if blocked:
            raise ValueError("ineligible feature source decisions: " + ", ".join(sorted(blocked)))
        run_ids: set[str] = set()
        with QueryRegistry(self.path).connect() as connection:
            for source_id in requested:
                assessment = connection.execute(
                    "SELECT payload_json FROM continuity_assessments WHERE assessment_id=?",
                    (source_id,),
                ).fetchone()
                if assessment:
                    payload = json.loads(assessment[0])
                    observation_ids = payload["evidence_observation_ids"]
                    if observation_ids:
                        run_ids.update(row[0] for row in connection.execute(
                            "SELECT DISTINCT query_run_id FROM host_observations WHERE observation_id IN ("
                            + ",".join("?" for _ in observation_ids) + ")", observation_ids,
                        ))
                    continue
                result = connection.execute(
                    "SELECT payload_json FROM pivot_precheck_results WHERE precheck_id=? "
                    "ORDER BY recorded_at DESC LIMIT 1", (source_id,),
                ).fetchone()
                if result:
                    run_ids.add(json.loads(result[0])["collection_run_id"])
        return run_ids

    def get_feature(self, feature_id: str) -> FeatureCatalogRecord:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM feature_catalog WHERE feature_id=?", (feature_id,)
            ).fetchone()
        if row is None:
            raise KeyError(feature_id)
        return FeatureCatalogRecord.model_validate_json(row[0])

    def get_reference_set(self, reference_set_id: str) -> ReferenceSetRecord:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM reference_sets WHERE reference_set_id=?",
                (reference_set_id,),
            ).fetchone()
        if row is None:
            raise KeyError(reference_set_id)
        return ReferenceSetRecord.model_validate_json(row[0])

    def load_reference_memberships(
        self, reference_set_id: str
    ) -> list[ReferenceMembershipRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM reference_memberships WHERE reference_set_id=? "
                "ORDER BY membership_id", (reference_set_id,),
            ).fetchall()
        return [ReferenceMembershipRecord.model_validate_json(row[0]) for row in rows]

    def load_feature_observations(self, feature_id: str) -> list[FeatureObservationRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM feature_observations WHERE feature_id=? "
                "ORDER BY feature_observation_id", (feature_id,),
            ).fetchall()
        return [FeatureObservationRecord.model_validate_json(row[0]) for row in rows]

    def register_stat_snapshot(self, snapshot: FeatureStatSnapshotRecord) -> bool:
        if not snapshot.anchor_source_ids:
            raise ValueError("feature statistics require eligible anchor source provenance")
        blocked = set(snapshot.anchor_source_ids) - self._eligible_anchor_sources()
        if blocked:
            raise ValueError("feature statistics use ineligible anchor sources: "
                             + ", ".join(sorted(blocked)))
        authorized_runs = self.eligible_observation_run_ids(snapshot.anchor_source_ids)
        with self.connect() as connection:
            if not snapshot.anchor_observation_ids:
                raise ValueError("feature statistic requires observable anchors")
            placeholders = ",".join("?" for _ in snapshot.anchor_observation_ids)
            anchor_rows = connection.execute(
                f"SELECT payload_json FROM host_observations WHERE observation_id IN ({placeholders})",
                snapshot.anchor_observation_ids,
            ).fetchall()
            if len(anchor_rows) != len(set(snapshot.anchor_observation_ids)):
                raise ValueError("feature statistic references unknown anchors")
            for row in anchor_rows:
                host = json.loads(row[0])
                effective = host.get("observed_at") or host["collected_at"]
                if (not host["host_observed"] or host["query_run_id"] not in authorized_runs
                        or effective > snapshot.cutoff_at.isoformat()
                        or host["collected_at"] > snapshot.cutoff_at.isoformat()):
                    raise ValueError("feature statistic anchor violates eligibility or cutoff")
            feature_row = connection.execute(
                "SELECT payload_json FROM feature_catalog WHERE feature_id=?",
                (snapshot.feature_id,),
            ).fetchone()
            reference_row = connection.execute(
                "SELECT payload_json FROM reference_sets WHERE reference_set_id=?",
                (snapshot.reference_set_id,),
            ).fetchone()
            if feature_row is None or reference_row is None:
                raise ValueError("feature statistic lacks feature or reference set")
            feature = FeatureCatalogRecord.model_validate_json(feature_row[0])
            reference = ReferenceSetRecord.model_validate_json(reference_row[0])
            memberships = [
                ReferenceMembershipRecord.model_validate_json(row[0])
                for row in connection.execute(
                    "SELECT payload_json FROM reference_memberships WHERE reference_set_id=?",
                    (snapshot.reference_set_id,),
                )
            ]
            observations = [
                FeatureObservationRecord.model_validate_json(row[0])
                for row in connection.execute(
                    "SELECT payload_json FROM feature_observations WHERE feature_id=?",
                    (snapshot.feature_id,),
                )
            ]
            expected = compute_feature_stat_snapshot(
                feature, reference, memberships, observations,
                anchor_observation_ids=snapshot.anchor_observation_ids,
                anchor_source_ids=snapshot.anchor_source_ids,
                computed_at=snapshot.computed_at,
            )
            if expected != snapshot:
                raise ValueError("feature statistic is not reproducible from registered sources")
            payload = self._payload(snapshot)
            return self._insert_immutable(
                connection, table="feature_stat_snapshots", key_column="stat_snapshot_id",
                key=snapshot.stat_snapshot_id, payload=payload,
                sql="INSERT INTO feature_stat_snapshots VALUES (?, ?, ?, ?, ?, ?)",
                parameters=(snapshot.stat_snapshot_id, snapshot.feature_id,
                            snapshot.reference_set_id, snapshot.cutoff_at.isoformat(),
                            snapshot.source_manifest_hash, payload),
            )

    def register_eligibility_assessment(
        self, assessment: FeatureEligibilityAssessmentRecord
    ) -> bool:
        with self.connect() as connection:
            feature_row = connection.execute(
                "SELECT payload_json FROM feature_catalog WHERE feature_id=?",
                (assessment.feature_id,),
            ).fetchone()
            stat_row = connection.execute(
                "SELECT payload_json FROM feature_stat_snapshots WHERE stat_snapshot_id=?",
                (assessment.stat_snapshot_id,),
            ).fetchone()
            if feature_row is None or stat_row is None:
                raise ValueError("feature eligibility lacks feature statistic provenance")
            stat = FeatureStatSnapshotRecord.model_validate_json(stat_row[0])
            if assessment.assessed_at < stat.computed_at:
                raise ValueError("feature eligibility predates its statistic")
            expected = assess_feature_eligibility(
                FeatureCatalogRecord.model_validate_json(feature_row[0]),
                stat,
                assessed_at=assessment.assessed_at,
                min_distinct_anchors=assessment.min_distinct_anchors,
                min_anchor_support=assessment.min_anchor_support,
                max_background_prevalence=assessment.max_background_prevalence,
            )
            if expected != assessment:
                raise ValueError("feature eligibility is not reproducible")
            payload = self._payload(assessment)
            return self._insert_immutable(
                connection, table="feature_eligibility_assessments",
                key_column="assessment_id", key=assessment.assessment_id, payload=payload,
                sql="INSERT INTO feature_eligibility_assessments VALUES (?, ?, ?, ?, ?, ?)",
                parameters=(assessment.assessment_id, assessment.feature_id,
                            assessment.stat_snapshot_id, assessment.status.value,
                            assessment.assessed_at.isoformat(), payload),
            )

    def register_eligibility_review(self, review: FeatureEligibilityReviewRecord) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM feature_eligibility_assessments WHERE assessment_id=?",
                (review.assessment_id,),
            ).fetchone()
            if row is None:
                raise ValueError("feature review references unknown assessment")
            assessment = FeatureEligibilityAssessmentRecord.model_validate_json(row[0])
            if review.reviewed_at < assessment.assessed_at:
                raise ValueError("feature review predates assessment")
            if (review.decision is ReviewerStatus.ACCEPTED
                    and assessment.status is not FeatureEligibilityStatus.CANDIDATE):
                raise ValueError("blocked feature cannot receive accepted eligibility review")
            payload = self._payload(review)
            existing = connection.execute(
                "SELECT payload_json FROM feature_eligibility_reviews WHERE assessment_id=?",
                (review.assessment_id,),
            ).fetchone()
            if existing:
                if existing[0] != payload:
                    raise ValueError("feature assessment already has immutable review")
                return False
            connection.execute(
                "INSERT INTO feature_eligibility_reviews VALUES (?, ?, ?, ?, ?)",
                (review.review_id, review.assessment_id, review.decision.value,
                 review.reviewed_at.isoformat(), payload),
            )
        return True

    def eligible_feature_ids(self) -> list[str]:
        output: list[str] = []
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT a.payload_json, r.payload_json "
                "FROM feature_eligibility_assessments a "
                "LEFT JOIN feature_eligibility_reviews r ON r.assessment_id=a.assessment_id"
            ).fetchall()
        for assessment_payload, review_payload in rows:
            assessment = FeatureEligibilityAssessmentRecord.model_validate_json(
                assessment_payload
            )
            review = (FeatureEligibilityReviewRecord.model_validate_json(review_payload)
                      if review_payload else None)
            if feature_is_query_eligible(assessment, review):
                output.append(assessment.feature_id)
        return sorted(set(output))

    def eligible_feature_reference_set_ids(
        self, feature_ids: list[str]
    ) -> dict[str, set[str]]:
        output = {feature_id: set() for feature_id in feature_ids}
        if not feature_ids:
            return output
        placeholders = ",".join("?" for _ in feature_ids)
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT a.feature_id, s.reference_set_id "
                f"FROM feature_eligibility_assessments a "
                f"JOIN feature_eligibility_reviews r ON r.assessment_id=a.assessment_id "
                f"JOIN feature_stat_snapshots s ON s.stat_snapshot_id=a.stat_snapshot_id "
                f"WHERE r.decision='accepted' AND a.status='candidate' "
                f"AND a.feature_id IN ({placeholders})",
                feature_ids,
            ).fetchall()
        for feature_id, reference_set_id in rows:
            output.setdefault(feature_id, set()).add(reference_set_id)
        return output

    def phase_c_gate_report(self) -> dict:
        specs = (
            ("feature_catalog", "feature_id", FeatureCatalogRecord),
            ("feature_observations", "feature_observation_id", FeatureObservationRecord),
            ("entity_epochs", "entity_epoch_id", EntityEpochRecord),
            ("reference_sets", "reference_set_id", ReferenceSetRecord),
            ("reference_memberships", "membership_id", ReferenceMembershipRecord),
            ("feature_stat_snapshots", "stat_snapshot_id", FeatureStatSnapshotRecord),
            ("feature_eligibility_assessments", "assessment_id", FeatureEligibilityAssessmentRecord),
            ("feature_eligibility_reviews", "review_id", FeatureEligibilityReviewRecord),
        )
        issues: list[dict[str, str]] = []
        counts: dict[str, int] = {}
        replay_checks: list[tuple[str, str, object]] = []
        with self.connect() as connection:
            for table, key_column, model in specs:
                rows = connection.execute(
                    f"SELECT {key_column}, payload_json FROM {table}"
                ).fetchall()
                counts[table] = len(rows)
                for key, payload in rows:
                    try:
                        record = model.model_validate_json(payload)
                        if table in {
                            "reference_sets", "feature_stat_snapshots",
                            "feature_eligibility_assessments",
                        }:
                            replay_checks.append((table, key, record))
                    except Exception as error:
                        issues.append({"code": f"invalid_{table}", "record_id": key,
                                       "detail": str(error)})
            accepted = [row[0] for row in connection.execute(
                "SELECT a.feature_id FROM feature_eligibility_assessments a "
                "JOIN feature_eligibility_reviews r ON r.assessment_id=a.assessment_id "
                "WHERE r.decision='accepted'"
            )]
        for table, key, record in replay_checks:
            try:
                if table == "reference_sets":
                    self.register_reference_set(record)
                elif table == "feature_stat_snapshots":
                    self.register_stat_snapshot(record)
                else:
                    self.register_eligibility_assessment(record)
            except Exception as error:
                issues.append({"code": f"non_reproducible_{table}",
                               "record_id": key, "detail": str(error)})
        eligible = set(self.eligible_feature_ids())
        for feature_id in accepted:
            if feature_id not in eligible:
                issues.append({"code": "accepted_feature_not_eligible",
                               "record_id": feature_id, "detail": "Phase D source blocked"})
        counts["eligible_features"] = len(eligible)
        return {"passed": not issues, "counts": counts, "issues": issues}
