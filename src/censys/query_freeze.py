"""Stage 5 design registry, human review, schedule, and immutable freeze manifest."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from src.censys.feature_registry import FeatureRegistry
from src.censys.query_composer import build_query_design, render_query
from src.censys.query_registry import QueryRegistry
from src.cti.corpus_registry import CorpusRegistry
from src.models import (
    DesignPrecheckStatus,
    FeatureCatalogRecord,
    QueryBudgetScheduleRecord,
    QueryClauseRecord,
    QueryDesignPrecheckRecord,
    QueryDesignRecord,
    QueryDesignReviewRecord,
    QueryFreezeManifestRecord,
    QueryStatus,
    ReviewerStatus,
    utc,
)
from src.provenance import canonical_json_hash, sha256_text


SCHEMA = """
CREATE TABLE IF NOT EXISTS query_clauses (
  clause_id TEXT PRIMARY KEY,
  feature_origin TEXT NOT NULL,
  available_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS query_designs (
  design_id TEXT PRIMARY KEY,
  query_id TEXT NOT NULL UNIQUE REFERENCES query_registry(query_id),
  query_version TEXT NOT NULL,
  variant TEXT NOT NULL,
  composition_type TEXT NOT NULL,
  cutoff_at TEXT NOT NULL,
  query_hash TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  UNIQUE(query_id, variant)
);
CREATE TABLE IF NOT EXISTS query_design_clauses (
  design_id TEXT NOT NULL REFERENCES query_designs(design_id),
  clause_id TEXT NOT NULL REFERENCES query_clauses(clause_id),
  PRIMARY KEY(design_id, clause_id)
);
CREATE TABLE IF NOT EXISTS query_budget_schedules (
  schedule_id TEXT PRIMARY KEY,
  design_id TEXT NOT NULL UNIQUE REFERENCES query_designs(design_id),
  starts_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS query_design_prechecks (
  precheck_id TEXT PRIMARY KEY,
  design_id TEXT NOT NULL REFERENCES query_designs(design_id),
  status TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS query_design_reviews (
  review_id TEXT PRIMARY KEY,
  design_id TEXT NOT NULL UNIQUE REFERENCES query_designs(design_id),
  precheck_id TEXT NOT NULL REFERENCES query_design_prechecks(precheck_id),
  decision TEXT NOT NULL,
  reviewed_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS query_freeze_manifests (
  freeze_manifest_id TEXT PRIMARY KEY,
  query_id TEXT NOT NULL UNIQUE REFERENCES query_registry(query_id),
  design_id TEXT NOT NULL UNIQUE REFERENCES query_designs(design_id),
  query_hash TEXT NOT NULL,
  frozen_at TEXT NOT NULL,
  valid_for_test_from TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
"""


def build_budget_schedule(
    design: QueryDesignRecord,
    *,
    interval_hours: int,
    starts_at: datetime,
    max_alerts_per_run: int,
    max_credits_per_run: float,
    max_pages_per_run: int,
    tie_break_rule: str,
    registered_at: datetime,
) -> QueryBudgetScheduleRecord:
    material = canonical_json_hash({
        "design_id": design.design_id,
        "interval_hours": interval_hours,
        "starts_at": utc(starts_at).isoformat(),
        "max_alerts_per_run": max_alerts_per_run,
        "max_credits_per_run": max_credits_per_run,
        "max_pages_per_run": max_pages_per_run,
        "tie_break_rule": tie_break_rule,
    })
    return QueryBudgetScheduleRecord(
        schedule_id="query-schedule-" + material[:20], design_id=design.design_id,
        interval_hours=interval_hours, starts_at=starts_at,
        max_alerts_per_run=max_alerts_per_run,
        max_credits_per_run=max_credits_per_run,
        max_pages_per_run=max_pages_per_run, tie_break_rule=tie_break_rule,
        registered_at=registered_at,
    )


def build_freeze_manifest(
    design: QueryDesignRecord,
    schedule: QueryBudgetScheduleRecord,
    precheck: QueryDesignPrecheckRecord,
    review: QueryDesignReviewRecord,
    clauses: list[QueryClauseRecord],
    *,
    frozen_at: datetime,
    valid_for_test_from: datetime,
) -> QueryFreezeManifestRecord:
    if schedule.starts_at < utc(valid_for_test_from):
        raise ValueError("prospective schedule starts before valid_for_test_from")
    if utc(frozen_at) < max(
        design.registered_at, schedule.registered_at,
        precheck.recorded_at, review.reviewed_at,
    ):
        raise ValueError("freeze predates a required design artifact")
    source_hash = canonical_json_hash({
        "design": design.model_dump(mode="json"),
        "clauses": [item.model_dump(mode="json") for item in sorted(
            clauses, key=lambda item: item.clause_id
        )],
        "schedule": schedule.model_dump(mode="json"),
        "precheck": precheck.model_dump(mode="json"),
        "review": review.model_dump(mode="json"),
    })
    material = canonical_json_hash({
        "query_id": design.query_id, "design_id": design.design_id,
        "source_manifest_hash": source_hash,
        "frozen_at": utc(frozen_at).isoformat(),
        "valid_for_test_from": utc(valid_for_test_from).isoformat(),
    })
    return QueryFreezeManifestRecord(
        freeze_manifest_id="query-freeze-" + material[:20],
        query_id=design.query_id, design_id=design.design_id,
        query_hash=design.query_hash, source_manifest_hash=source_hash,
        query_cutoff_at=design.cutoff_at, schedule_id=schedule.schedule_id,
        review_id=review.review_id, frozen_at=frozen_at,
        valid_for_test_from=valid_for_test_from,
    )


class QueryDesignRegistry:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
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
        return json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _immutable(connection, table, key_column, key, payload, sql, parameters) -> bool:
        row = connection.execute(
            f"SELECT payload_json FROM {table} WHERE {key_column}=?", (key,)
        ).fetchone()
        if row:
            if row[0] != payload:
                raise ValueError(f"{table} immutable ID collision: {key}")
            return False
        connection.execute(sql, parameters)
        return True

    def _accepted_assertion_ids(self) -> set[str]:
        with QueryRegistry(self.path).connect() as connection:
            try:
                return {row[0] for row in connection.execute(
                    "SELECT assertion_id FROM assertion_reviews WHERE decision='accepted'"
                )}
            except sqlite3.OperationalError:
                return set()

    def register_design(
        self, design: QueryDesignRecord, clauses: list[QueryClauseRecord]
    ) -> bool:
        query = QueryRegistry(self.path).get_query(design.query_id)
        if (query.query_version != design.query_version or query.query_class is not design.query_class
                or query.query_hash != design.query_hash or query.query_text != design.rendered_query
                or query.config_hash != design.config_hash):
            raise ValueError("query design does not match immutable query registry")
        if sorted(item.clause_id for item in clauses) != design.clause_ids:
            raise ValueError("query design clause identity mismatch")
        feature_registry = FeatureRegistry(self.path)
        eligible_feature_ids = set(feature_registry.eligible_feature_ids())
        feature_ids = {
            item.source_feature_id for item in clauses if item.source_feature_id is not None
        }
        features: dict[str, FeatureCatalogRecord] = {
            feature_id: feature_registry.get_feature(feature_id)
            for feature_id in feature_ids if feature_id in eligible_feature_ids
        }
        rendered = render_query(
            clauses, composition_type=design.composition_type,
            query_class=design.query_class, cutoff_at=design.cutoff_at,
            accepted_assertion_ids=self._accepted_assertion_ids(),
            eligible_precheck_ids=set(QueryRegistry(self.path).eligible_q2_precheck_ids()),
            eligible_features=features,
        )
        if rendered != design.rendered_query or sha256_text(rendered) != design.query_hash:
            raise ValueError("query design rendering is not reproducible")
        expected_design = build_query_design(
            query, clauses, variant=design.variant,
            composition_type=design.composition_type, cutoff_at=design.cutoff_at,
            background_snapshot_ids=design.background_snapshot_ids,
            api_schema_version=design.api_schema_version,
            parser_version=design.parser_version,
            normalizer_version=design.normalizer_version,
            entity_resolution_version=design.entity_resolution_version,
            registered_at=design.registered_at,
        )
        if expected_design != design:
            raise ValueError("query design identity is not reproducible")
        for reference_id in design.background_snapshot_ids:
            feature_registry.get_reference_set(reference_id)
        reference_map = feature_registry.eligible_feature_reference_set_ids(
            sorted(feature_ids)
        )
        required_references = set().union(*reference_map.values()) if reference_map else set()
        if not required_references <= set(design.background_snapshot_ids):
            raise ValueError("derived query design omits eligible feature background snapshot")
        if sorted(feature_ids) != sorted(query.source_feature_ids):
            raise ValueError("query feature provenance differs from design clauses")
        assertion_ids = sorted({
            item.source_assertion_id for item in clauses if item.source_assertion_id
        })
        precheck_ids = sorted({
            item.source_precheck_id for item in clauses if item.source_precheck_id
        })
        effective_assertions = set(assertion_ids)
        if precheck_ids:
            with QueryRegistry(self.path).connect() as connection:
                for precheck_id in precheck_ids:
                    row = connection.execute(
                        "SELECT payload_json FROM pivot_prechecks WHERE precheck_id=?",
                        (precheck_id,),
                    ).fetchone()
                    if row:
                        effective_assertions.update(json.loads(row[0])["assertion_ids"])
        if sorted(effective_assertions) != sorted(query.source_assertion_ids):
            raise ValueError("query assertion provenance differs from design clauses")
        if precheck_ids != sorted(query.source_precheck_ids):
            raise ValueError("query precheck provenance differs from design clauses")
        if design.registered_at < query.registered_at:
            raise ValueError("query design predates query registration")
        if query.source_available_at != max(item.available_at for item in clauses):
            raise ValueError("query source availability differs from design clauses")
        direct_assertion_ids = sorted({
            item.source_assertion_id for item in clauses if item.source_assertion_id
        })
        if direct_assertion_ids:
            accepted_sources = {
                item.assertion_id: item for item in CorpusRegistry(self.path).accepted_pivot_sources(
                    direct_assertion_ids, cutoff_at=design.cutoff_at
                )
            }
            for clause in clauses:
                if clause.source_assertion_id:
                    source = accepted_sources[clause.source_assertion_id]
                    if clause.available_at != source.available_at:
                        raise ValueError("CTI clause availability differs from accepted assertion")
                    if clause.canonical_value != source.value:
                        raise ValueError("CTI clause value differs from accepted assertion")
        for clause in clauses:
            if clause.source_precheck_id:
                precheck = QueryRegistry(self.path).get_pivot_precheck(
                    clause.source_precheck_id
                )
                if clause.available_at != precheck.source_available_at:
                    raise ValueError("CTI clause availability differs from pivot precheck")
        payload = self._payload(design)
        with self.connect() as connection:
            for clause in clauses:
                clause_payload = self._payload(clause)
                self._immutable(
                    connection, "query_clauses", "clause_id", clause.clause_id,
                    clause_payload, "INSERT INTO query_clauses VALUES (?, ?, ?, ?)",
                    (clause.clause_id, clause.feature_origin.value,
                     clause.available_at.isoformat(), clause_payload),
                )
            inserted = self._immutable(
                connection, "query_designs", "design_id", design.design_id, payload,
                "INSERT INTO query_designs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (design.design_id, design.query_id, design.query_version, design.variant,
                 design.composition_type.value, design.cutoff_at.isoformat(),
                 design.query_hash, payload),
            )
            for clause_id in design.clause_ids:
                connection.execute(
                    "INSERT OR IGNORE INTO query_design_clauses VALUES (?, ?)",
                    (design.design_id, clause_id),
                )
        return inserted

    def get_design(self, design_id: str) -> QueryDesignRecord:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM query_designs WHERE design_id=?", (design_id,)
            ).fetchone()
        if row is None:
            raise KeyError(design_id)
        return QueryDesignRecord.model_validate_json(row[0])

    def load_clauses(self, design_id: str) -> list[QueryClauseRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT c.payload_json FROM query_clauses c JOIN query_design_clauses d "
                "ON d.clause_id=c.clause_id WHERE d.design_id=? ORDER BY c.clause_id",
                (design_id,),
            ).fetchall()
        return [QueryClauseRecord.model_validate_json(row[0]) for row in rows]

    def register_schedule(self, schedule: QueryBudgetScheduleRecord) -> bool:
        design = self.get_design(schedule.design_id)
        if schedule.registered_at < design.registered_at:
            raise ValueError("query schedule predates design")
        payload = self._payload(schedule)
        with self.connect() as connection:
            return self._immutable(
                connection, "query_budget_schedules", "schedule_id", schedule.schedule_id,
                payload, "INSERT INTO query_budget_schedules VALUES (?, ?, ?, ?)",
                (schedule.schedule_id, schedule.design_id, schedule.starts_at.isoformat(), payload),
            )

    def register_precheck(self, precheck: QueryDesignPrecheckRecord) -> bool:
        design = self.get_design(precheck.design_id)
        if precheck.recorded_at < design.registered_at:
            raise ValueError("query precheck predates design")
        payload = self._payload(precheck)
        with self.connect() as connection:
            prior = connection.execute(
                "SELECT payload_json FROM query_design_prechecks WHERE design_id=? "
                "ORDER BY recorded_at DESC LIMIT 1", (precheck.design_id,),
            ).fetchone()
            if prior:
                previous = QueryDesignPrecheckRecord.model_validate_json(prior[0])
                if previous.status is DesignPrecheckStatus.COMPLETE:
                    raise ValueError("complete query design precheck is terminal")
                if precheck.recorded_at < previous.recorded_at:
                    raise ValueError("query design precheck history is out of order")
                if precheck.page_count < previous.page_count or precheck.hit_count < previous.hit_count:
                    raise ValueError("resumed query design precheck counters cannot decrease")
            return self._immutable(
                connection, "query_design_prechecks", "precheck_id", precheck.precheck_id,
                payload, "INSERT INTO query_design_prechecks VALUES (?, ?, ?, ?, ?)",
                (precheck.precheck_id, precheck.design_id, precheck.status.value,
                 precheck.recorded_at.isoformat(), payload),
            )

    @staticmethod
    def _precheck_passes(precheck: QueryDesignPrecheckRecord) -> bool:
        return bool(
            precheck.status is DesignPrecheckStatus.COMPLETE
            and precheck.syntax_valid and precheck.hit_count > 0
            and not precheck.next_token_present
            and not precheck.broad_or_shared and not precheck.cost_exceeded
        )

    def register_review(self, review: QueryDesignReviewRecord) -> bool:
        with self.connect() as connection:
            design = connection.execute(
                "SELECT payload_json FROM query_designs WHERE design_id=?", (review.design_id,)
            ).fetchone()
            precheck = connection.execute(
                "SELECT payload_json FROM query_design_prechecks WHERE precheck_id=?",
                (review.precheck_id,),
            ).fetchone()
            if design is None or precheck is None:
                raise ValueError("query review lacks design or precheck")
            precheck_record = QueryDesignPrecheckRecord.model_validate_json(precheck[0])
            if precheck_record.design_id != review.design_id:
                raise ValueError("query review precheck belongs to another design")
            if review.reviewed_at < precheck_record.recorded_at:
                raise ValueError("query review predates precheck")
            if review.decision is ReviewerStatus.ACCEPTED and not self._precheck_passes(precheck_record):
                raise ValueError("accepted query review requires a bounded passing precheck")
            schedule_row = connection.execute(
                "SELECT payload_json FROM query_budget_schedules WHERE design_id=?",
                (review.design_id,),
            ).fetchone()
            if review.decision is ReviewerStatus.ACCEPTED:
                if schedule_row is None:
                    raise ValueError("accepted query review requires a budget schedule")
                schedule = QueryBudgetScheduleRecord.model_validate_json(schedule_row[0])
                if precheck_record.page_count > schedule.max_pages_per_run:
                    raise ValueError("query precheck exceeds frozen page budget")
            payload = self._payload(review)
            existing = connection.execute(
                "SELECT payload_json FROM query_design_reviews WHERE design_id=?",
                (review.design_id,),
            ).fetchone()
            if existing:
                if existing[0] != payload:
                    raise ValueError("query design already has immutable review")
                return False
            connection.execute(
                "INSERT INTO query_design_reviews VALUES (?, ?, ?, ?, ?, ?)",
                (review.review_id, review.design_id, review.precheck_id,
                 review.decision.value, review.reviewed_at.isoformat(), payload),
            )
        return True

    def _freeze_inputs(self, design_id: str):
        with self.connect() as connection:
            schedule_row = connection.execute(
                "SELECT payload_json FROM query_budget_schedules WHERE design_id=?", (design_id,)
            ).fetchone()
            review_row = connection.execute(
                "SELECT payload_json FROM query_design_reviews WHERE design_id=?", (design_id,)
            ).fetchone()
            if schedule_row is None or review_row is None:
                raise ValueError("freeze requires budget schedule and human query review")
            schedule = QueryBudgetScheduleRecord.model_validate_json(schedule_row[0])
            review = QueryDesignReviewRecord.model_validate_json(review_row[0])
            precheck_row = connection.execute(
                "SELECT payload_json FROM query_design_prechecks WHERE precheck_id=?",
                (review.precheck_id,),
            ).fetchone()
        if precheck_row is None:
            raise ValueError("freeze review lacks precheck")
        return schedule, QueryDesignPrecheckRecord.model_validate_json(precheck_row[0]), review

    def register_freeze_manifest(self, manifest: QueryFreezeManifestRecord) -> bool:
        design = self.get_design(manifest.design_id)
        clauses = self.load_clauses(design.design_id)
        schedule, precheck, review = self._freeze_inputs(design.design_id)
        if review.decision is not ReviewerStatus.ACCEPTED or not self._precheck_passes(precheck):
            raise ValueError("freeze requires accepted review of passing bounded precheck")
        expected = build_freeze_manifest(
            design, schedule, precheck, review, clauses,
            frozen_at=manifest.frozen_at,
            valid_for_test_from=manifest.valid_for_test_from,
        )
        if expected != manifest:
            raise ValueError("freeze manifest is not reproducible")
        query = QueryRegistry(self.path).get_query(manifest.query_id)
        if query.status not in {QueryStatus.VALIDATED, QueryStatus.FROZEN}:
            raise ValueError("freeze manifest requires validated query")
        payload = self._payload(manifest)
        with self.connect() as connection:
            return self._immutable(
                connection, "query_freeze_manifests", "freeze_manifest_id",
                manifest.freeze_manifest_id, payload,
                "INSERT INTO query_freeze_manifests VALUES (?, ?, ?, ?, ?, ?, ?)",
                (manifest.freeze_manifest_id, manifest.query_id, manifest.design_id,
                 manifest.query_hash, manifest.frozen_at.isoformat(),
                 manifest.valid_for_test_from.isoformat(), payload),
            )

    def assert_freeze_ready(
        self, query_id: str, *, frozen_at: datetime, valid_for_test_from: datetime
    ) -> None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM query_freeze_manifests WHERE query_id=?", (query_id,)
            ).fetchone()
        if row is None:
            raise ValueError("composed Q2/Q3 query lacks Phase D freeze manifest")
        manifest = QueryFreezeManifestRecord.model_validate_json(row[0])
        if manifest.frozen_at != utc(frozen_at) or manifest.valid_for_test_from != utc(valid_for_test_from):
            raise ValueError("freeze timestamps differ from immutable manifest")

    def prepare_freeze_manifest(
        self, design_id: str, *, frozen_at: datetime, valid_for_test_from: datetime
    ) -> QueryFreezeManifestRecord:
        design = self.get_design(design_id)
        schedule, precheck, review = self._freeze_inputs(design_id)
        return build_freeze_manifest(
            design, schedule, precheck, review, self.load_clauses(design_id),
            frozen_at=frozen_at, valid_for_test_from=valid_for_test_from,
        )

    def phase_d_gate_report(self) -> dict:
        specs = (
            ("query_clauses", "clause_id", QueryClauseRecord),
            ("query_designs", "design_id", QueryDesignRecord),
            ("query_budget_schedules", "schedule_id", QueryBudgetScheduleRecord),
            ("query_design_prechecks", "precheck_id", QueryDesignPrecheckRecord),
            ("query_design_reviews", "review_id", QueryDesignReviewRecord),
            ("query_freeze_manifests", "freeze_manifest_id", QueryFreezeManifestRecord),
        )
        issues, counts = [], {}
        manifests: list[QueryFreezeManifestRecord] = []
        with self.connect() as connection:
            for table, key_column, model in specs:
                rows = connection.execute(
                    f"SELECT {key_column}, payload_json FROM {table}"
                ).fetchall()
                counts[table] = len(rows)
                for key, payload in rows:
                    try:
                        record = model.model_validate_json(payload)
                        if table == "query_freeze_manifests":
                            manifests.append(record)
                    except Exception as error:
                        issues.append({"code": f"invalid_{table}", "record_id": key,
                                       "detail": str(error)})
            manifest_query_ids = {
                row[0] for row in connection.execute(
                    "SELECT query_id FROM query_freeze_manifests"
                )
            }
            for (query_id,) in connection.execute(
                "SELECT query_id FROM query_registry WHERE query_class IN "
                "('Q2_DERIVED','Q3_CLUSTER') AND status='frozen'"
            ):
                if query_id not in manifest_query_ids:
                    issues.append({"code": "frozen_query_lacks_manifest",
                                   "record_id": query_id, "detail": "Phase E blocked"})
        for manifest in manifests:
            try:
                self.register_freeze_manifest(manifest)
            except Exception as error:
                issues.append({"code": "non_reproducible_freeze_manifest",
                               "record_id": manifest.freeze_manifest_id,
                               "detail": str(error)})
        return {"passed": not issues, "counts": counts, "issues": issues}
