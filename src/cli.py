"""ORB 연구 통제 계층 CLI.

목적: 외부 코드 hash 검증, Q0∼Q3 수명주기와 통제된 Censys 수집을 작은 명령으로 제공한다.
지원 RQ: RQ1∼RQ5 공통, 특히 RQ4·RQ5 query lifecycle.
재사용 원천: ORB_Hunt_v5의 stage-oriented CLI 설계를 따른다.
설계: 각 명령은 명시적 입출력 계약을 가지며 live 수집은 registry query와 환경변수 gate를 요구한다.
입력·출력: YAML·SQLite와 stdout JSON을 사용한다.
시간·provenance 통제: 모든 입력 시각은 ISO-8601 timezone을 요구한다.
보안·라이선스: API secret을 인자로 받지 않고 passive API 조회만 허용하며 active scan·자동 차단은 제공하지 않는다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import yaml

from src.adapters.cti_agent import CtiAgentAdapter
from src.adapters.orbhunt_censys import OrbhuntCensysAdapter
from src.censys.paginated_collection import (
    CensysPlatformHttpFetcher,
    CensysQ0HostLookupFetcher,
    PaginatedCensysCollector,
)
from src.censys.q0_seed import register_q0_seed
from src.censys.query_lifecycle import ensure_execution_allowed
from src.censys.query_registry import QueryRegistry
from src.censys.continuity import assess_continuity, materialize_q0_timeline
from src.censys.feature_registry import FeatureRegistry
from src.censys.features import build_entity_epochs, extract_observation_features
from src.censys.background import (
    assess_feature_eligibility,
    compute_feature_stat_snapshot,
    materialize_reference_memberships,
)
from src.censys.query_composer import build_query_design, render_query
from src.censys.query_freeze import QueryDesignRegistry, build_budget_schedule
from src.prospective.registry import ProspectiveRegistry
from src.prospective.scheduler import materialize_due_opportunities
from src.models import DatasetSplit, QueryClass, QueryExecutionRecord
from src.cti.brave_search import BraveProtocolSearchBackend
from src.cti.corpus_registry import CorpusRegistry
from src.cti.ioc_extraction import (
    AnthropicIndicatorExtractor,
    ExtractionResult,
    VerifiedIndicator,
    build_indicator_records,
    extract_and_verify_indicators,
    verify_indicator_candidates,
)
from src.cti.pivot_planning import register_pivot_plans
from src.cti.pivot_precheck import build_cti_only_composite, register_precheck_definition
from src.cti.search_execution import (
    SearchCandidate,
    SearchExecutionResult,
    infer_publication_metadata,
)
from src.cti.snapshots import (
    ImmutableSnapshotStore,
    PassiveDocumentFetcher,
    import_existing_cti,
)
from src.cti.workflow import (
    ManualScreeningInput,
    apply_manual_screening,
    run_search_stage,
    snapshot_included_candidates,
)
from src.manifests import write_immutable_json
from src.models import (
    AcquisitionMode,
    AssertionReviewRecord,
    CorpusPurpose,
    ScreeningDecision,
    SearchProtocolRecord,
    SourceDocumentRecord,
    SourceFamilyRecord,
    SourceRelationshipRecord,
    SourceAccessClass,
    TimePrecision,
    ContinuityReviewRecord,
    PivotEligibilityReviewRecord,
    PivotPrecheckResultRecord,
    Q0LandmarkRecord,
    FeatureEligibilityReviewRecord,
    ReferenceSetRecord,
    QueryBudgetScheduleRecord,
    QueryClauseRecord,
    QueryCompositionType,
    QueryDesignPrecheckRecord,
    QueryDesignReviewRecord,
    CandidateAdjudicationRecord,
    CandidateEvidenceRecord,
    CandidateGradeEventRecord,
    CandidateRecord,
    EntityEpochRecord,
    ProspectiveObservationEventRecord,
    QueryFreezeManifestRecord,
)
from src.provenance import sha256_file, sha256_text


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include timezone")
    return parsed


def _configure_stdout_utf8() -> None:
    """Windows CP949 콘솔에서도 JSON Unicode 출력이 실패하지 않게 한다."""

    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="backslashreplace")
    except (OSError, ValueError):
        return


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _load_snapshot_metadata(path: Path, document_id: str | None = None) -> dict:
    """검색 단일 metadata 또는 existing_curated 목록에서 대상 문서를 선택한다."""

    value = _load_json(path)
    if isinstance(value, dict):
        if document_id and value.get("document_id") != document_id:
            raise KeyError(f"document_id not found in metadata: {document_id}")
        return value
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError("snapshot metadata must be an object or an array of objects")
    if document_id:
        matches = [item for item in value if item.get("document_id") == document_id]
        if len(matches) != 1:
            raise KeyError(f"document_id not found or duplicated: {document_id}")
        return matches[0]
    if len(value) != 1:
        raise ValueError("metadata contains multiple documents; provide --document-id")
    return value[0]


def _verified_snapshot_bytes(metadata: dict) -> bytes:
    path = Path(metadata["snapshot_path"])
    if sha256_file(path) != metadata["content_sha256"]:
        raise ValueError("CTI source file hash does not match registered metadata")
    return path.read_bytes()


def _load_search_result(path: Path) -> SearchExecutionResult:
    data = _load_json(path)
    return SearchExecutionResult(
        search_run_id=data["search_run_id"],
        backend=data["backend"],
        executed_at=data["executed_at"],
        candidates=tuple(SearchCandidate(**item) for item in data["candidates"]),
        discarded_outside_whitelist=data["discarded_outside_whitelist"],
        discarded_invalid_url=data["discarded_invalid_url"],
        duplicate_count=data["duplicate_count"],
        result_manifest_hash=data["result_manifest_hash"],
    )


def _document_from_snapshot_metadata(
    metadata: dict,
    published_at: str | None,
    published_time_precision: str | None,
    source_timezone: str | None,
    source_family_id: str | None = None,
) -> SourceDocumentRecord:
    published_raw = published_at or metadata.get("published_at")
    precision_value = published_time_precision or metadata.get("published_time_precision")
    timezone_value = source_timezone or metadata.get("source_timezone")
    if not published_raw:
        raise ValueError(
            "published_at is absent; provide --published-at after manual verification"
        )
    if not precision_value or not timezone_value:
        raise ValueError(
            "published_time_precision and source_timezone are required"
        )
    precision = TimePrecision(str(precision_value))
    publication = infer_publication_metadata(published_raw, str(timezone_value))
    if precision in {
        TimePrecision.EXACT_TIMESTAMP,
        TimePrecision.DATE,
        TimePrecision.MONTH,
        TimePrecision.YEAR,
    } and publication.precision is not precision:
        raise ValueError("published_at does not match published_time_precision")
    return SourceDocumentRecord(
        document_id=metadata["document_id"],
        canonical_url=metadata["final_url"],
        publisher=metadata["publisher"],
        title=metadata["title"],
        published_at=(
            publication.exact_datetime
            if precision is TimePrecision.EXACT_TIMESTAMP
            else None
        ),
        published_at_raw=str(published_raw),
        published_time_precision=precision,
        source_timezone=str(timezone_value),
        retrieved_at=_datetime(metadata["retrieved_at"]),
        content_sha256=metadata["content_sha256"],
        text_content_sha256=metadata.get("text_content_sha256"),
        acquisition_mode=AcquisitionMode(metadata["acquisition_mode"]),
        source_access_class=SourceAccessClass(metadata["source_access_class"]),
        corpus_purpose=CorpusPurpose(metadata["corpus_purpose"]),
        search_protocol_id=metadata.get("search_protocol_id"),
        discovery_query_id=metadata.get("candidate_id"),
        source_independence=str(metadata.get("source_independence", "unknown")),
        source_family_id=source_family_id or metadata.get("source_family_id"),
    )


def _ocr_sidecar_text(metadata: dict) -> str | None:
    path_value = metadata.get("text_snapshot_path")
    if not path_value:
        return None
    path = Path(path_value)
    expected_hash = metadata.get("text_content_sha256")
    if not expected_hash or sha256_file(path) != expected_hash:
        raise ValueError("OCR sidecar hash does not match registered metadata")
    return path.read_text(encoding="utf-8")


def _verify_reuse(path: Path) -> int:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    sources = data.get("sources", {})
    cti = sources["cti_agent"]
    orb = sources["orbhunt_v5"]
    CtiAgentAdapter(Path(cti["path"]), cti.get("files", {})).verify_reuse_files()
    OrbhuntCensysAdapter(Path(orb["path"]), orb.get("files", {})).verify_reuse_files()
    print(json.dumps({"status": "ok", "verified_sources": ["cti_agent", "orbhunt_v5"]}))
    return 0


def _record_execution_idempotent(
    registry: QueryRegistry, execution: QueryExecutionRecord
) -> bool:
    """동일 execution ledger가 이미 있으면 검증 후 no-op으로 처리한다."""

    return not registry.record_execution(execution)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="orb-research")
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify = subparsers.add_parser("verify-reuse", help="verify reused source file hashes")
    verify.add_argument("--reuse-config", type=Path, required=True)

    register = subparsers.add_parser("register-query", help="register an immutable Q0-Q3 query")
    register.add_argument("--db", type=Path, required=True)
    register.add_argument("--version", required=True)
    register.add_argument("--variant", default="primary")
    register.add_argument("--class", dest="query_class", choices=[v.value for v in QueryClass], required=True)
    register.add_argument("--query-text", required=True)
    register.add_argument("--split", choices=[v.value for v in DatasetSplit], required=True)
    register.add_argument("--config-hash", required=True)
    register.add_argument(
        "--source-indicator-id", action="append", default=[],
        help="source indicator provenance; repeat for multiple indicators",
    )
    register.add_argument(
        "--source-feature-id", action="append", default=[],
        help="source fingerprint provenance; repeat for multiple features",
    )

    q0 = subparsers.add_parser("register-q0", help="register an exact-IP Q0 seed query")
    q0.add_argument("--db", type=Path, required=True)
    q0.add_argument("--source-assertion-id", required=True)
    q0.add_argument("--cutoff-at", type=_datetime, required=True)
    q0.add_argument("--registered-at", type=_datetime, required=True)
    q0.add_argument("--version", required=True)
    q0.add_argument("--config-hash", required=True)

    validate = subparsers.add_parser("validate-query", help="mark a draft query validated")
    validate.add_argument("--db", type=Path, required=True)
    validate.add_argument("--query-id", required=True)

    freeze = subparsers.add_parser("freeze-query", help="freeze a validated query")
    freeze.add_argument("--db", type=Path, required=True)
    freeze.add_argument("--query-id", required=True)
    freeze.add_argument("--frozen-at", type=_datetime, required=True)
    freeze.add_argument("--valid-for-test-from", type=_datetime, required=True)

    collect = subparsers.add_parser(
        "collect-censys", help="collect every Censys page for a registered query"
    )
    collect.add_argument("--db", type=Path, required=True)
    collect.add_argument("--query-id", required=True)
    collect.add_argument("--raw-root", type=Path, required=True)
    collect.add_argument("--split", choices=[v.value for v in DatasetSplit], required=True)
    collect.add_argument("--cutoff-time", type=_datetime, required=True)
    collect.add_argument("--executed-at", type=_datetime, required=True)
    collect.add_argument("--page-size", type=int, default=100)
    collect.add_argument("--max-pages", type=int)
    collect.add_argument("--fields", nargs="*")

    normalize = subparsers.add_parser(
        "normalize-censys", help="normalize immutable Censys raw pages without a network call"
    )
    normalize.add_argument("--db", type=Path, required=True)
    normalize.add_argument("--query-run-id", required=True)
    normalize.add_argument("--raw-directory", type=Path, required=True)
    normalize.add_argument("--collected-at", type=_datetime, required=True)
    normalize.add_argument("--extractor-version", default="orbhunt-v5-adapter-1")
    normalize.add_argument("--out", type=Path, required=True)

    fingerprints = subparsers.add_parser(
        "extract-fingerprints",
        help="derive fingerprints and entity relations from normalized observations",
    )
    fingerprints.add_argument("--db", type=Path, required=True)
    fingerprints.add_argument("--query-run-id", required=True)
    fingerprints.add_argument("--extractor-version", default="fingerprint-v1")
    fingerprints.add_argument("--out", type=Path, required=True)

    cti_search = subparsers.add_parser(
        "cti-search", help="run a registered CTI search protocol with Brave"
    )
    cti_search.add_argument("--db", type=Path, required=True)
    cti_search.add_argument("--protocol", type=Path, required=True)
    cti_search.add_argument("--watchlist", type=Path, required=True)
    cti_search.add_argument("--cti-agent", type=Path, required=True)
    cti_search.add_argument("--manifest", type=Path, required=True)
    cti_search.add_argument("--whitelist", nargs="+", required=True)
    cti_search.add_argument("--executed-at", type=_datetime, required=True)
    cti_search.add_argument("--count", type=int, default=20)
    cti_search.add_argument("--max-pages", type=int, default=10)

    cti_import = subparsers.add_parser(
        "cti-import-existing",
        help="register user-owned CTI from data/cti without renaming source files",
    )
    cti_import.add_argument("--db", type=Path, required=True)
    cti_import.add_argument("--source-root", type=Path, default=Path("data/cti"))
    cti_import.add_argument("--index", type=Path, default=Path("data/cti/index.json"))
    cti_import.add_argument("--imported-at", type=_datetime, required=True)
    cti_import.add_argument("--manifest", type=Path, required=True)

    cti_screen = subparsers.add_parser(
        "cti-screen", help="record manual INCLUDE/EXCLUDE decisions"
    )
    cti_screen.add_argument("--db", type=Path, required=True)
    cti_screen.add_argument("--search-manifest", type=Path, required=True)
    cti_screen.add_argument("--decisions", type=Path, required=True)
    cti_screen.add_argument("--reviewed-at", type=_datetime, required=True)
    cti_screen.add_argument("--out", type=Path, required=True)

    cti_snapshot = subparsers.add_parser(
        "cti-snapshot", help="fetch immutable snapshots for included CTI candidates"
    )
    cti_snapshot.add_argument("--search-manifest", type=Path, required=True)
    cti_snapshot.add_argument("--decision-map", type=Path, required=True)
    cti_snapshot.add_argument("--snapshot-root", type=Path, required=True)
    cti_snapshot.add_argument("--manifest", type=Path, required=True)
    cti_snapshot.add_argument("--whitelist", nargs="+", required=True)
    cti_snapshot.add_argument("--retrieved-at", type=_datetime, required=True)
    cti_snapshot.add_argument("--max-bytes", type=int, default=25 * 1024 * 1024)

    cti_verify = subparsers.add_parser(
        "cti-verify-iocs", help="verify extracted IoCs against an immutable snapshot"
    )
    cti_verify.add_argument("--snapshot-metadata", type=Path, required=True)
    cti_verify.add_argument("--document-id")
    cti_verify.add_argument("--candidates", type=Path, required=True)
    cti_verify.add_argument("--cti-agent", type=Path, required=True)
    cti_verify.add_argument("--published-at")
    cti_verify.add_argument(
        "--published-time-precision", choices=[item.value for item in TimePrecision]
    )
    cti_verify.add_argument("--source-timezone")
    cti_verify.add_argument("--available-at", type=_datetime, required=True)
    cti_verify.add_argument("--max-pdf-pages", type=int, default=500)
    cti_verify.add_argument("--max-document-chars", type=int, default=2_000_000)
    cti_verify.add_argument("--out", type=Path, required=True)

    cti_extract = subparsers.add_parser(
        "cti-extract-iocs",
        help="extract structured IoC candidates and verify them against a snapshot",
    )
    cti_extract.add_argument("--snapshot-metadata", type=Path, required=True)
    cti_extract.add_argument("--document-id")
    cti_extract.add_argument("--cti-agent", type=Path, required=True)
    cti_extract.add_argument("--prompt", type=Path, required=True)
    cti_extract.add_argument("--model", required=True)
    cti_extract.add_argument("--published-at")
    cti_extract.add_argument(
        "--published-time-precision", choices=[item.value for item in TimePrecision]
    )
    cti_extract.add_argument("--source-timezone")
    cti_extract.add_argument("--available-at", type=_datetime, required=True)
    cti_extract.add_argument("--max-input-chars", type=int, default=200_000)
    cti_extract.add_argument("--max-tokens", type=int, default=8192)
    cti_extract.add_argument("--max-pdf-pages", type=int, default=500)
    cti_extract.add_argument("--max-document-chars", type=int, default=2_000_000)
    cti_extract.add_argument("--out", type=Path, required=True)

    cti_register = subparsers.add_parser(
        "cti-register-indicators",
        help="persist verified indicators and source assertions in the restricted registry",
    )
    cti_register.add_argument("--verified-manifest", type=Path, required=True)
    cti_register.add_argument("--snapshot-metadata", type=Path, required=True)
    cti_register.add_argument("--source-family-manifest", type=Path, required=True)
    cti_register.add_argument("--document-id")
    cti_register.add_argument("--db", type=Path, required=True)
    cti_register.add_argument("--published-at")
    cti_register.add_argument(
        "--published-time-precision", choices=[item.value for item in TimePrecision]
    )
    cti_register.add_argument("--source-timezone")
    cti_register.add_argument("--ingested-at", type=_datetime, required=True)
    cti_register.add_argument("--campaign-id")
    cti_register.add_argument("--out", type=Path, required=True)

    cti_review = subparsers.add_parser(
        "cti-review-assertions", help="persist immutable human assertion decisions"
    )
    cti_review.add_argument("--db", type=Path, required=True)
    cti_review.add_argument("--reviews", type=Path, required=True)
    cti_review.add_argument("--out", type=Path, required=True)

    cti_plan = subparsers.add_parser(
        "cti-plan-pivots", help="register Q0/Q1 plans without executing Censys"
    )
    cti_plan.add_argument("--accepted-assertions", type=Path, required=True)
    cti_plan.add_argument("--cutoff-at", type=_datetime, required=True)
    cti_plan.add_argument("--db", type=Path, required=True)
    cti_plan.add_argument("--orbhunt", type=Path, required=True)
    cti_plan.add_argument("--template-config", type=Path, required=True)
    cti_plan.add_argument("--registered-at", type=_datetime, required=True)
    cti_plan.add_argument("--version", required=True)
    cti_plan.add_argument("--config-hash", required=True)
    cti_plan.add_argument("--out", type=Path, required=True)
    cti_export = subparsers.add_parser(
        "cti-export-public-corpus", help="export validated public source metadata"
    )
    cti_export.add_argument("--db", type=Path, required=True)
    cti_export.add_argument("--document-ids", type=Path, required=True)
    cti_export.add_argument("--out", type=Path, required=True)
    cti_audit = subparsers.add_parser(
        "cti-audit-stage0", help="audit persisted Stage 0 provenance gates"
    )
    cti_audit.add_argument("--db", type=Path, required=True)
    cti_audit.add_argument("--out", type=Path, required=True)
    phase_a_audit = subparsers.add_parser(
        "cti-audit-phase-a", help="audit Stage 0 and Stage 1 acceptance provenance"
    )
    phase_a_audit.add_argument("--db", type=Path, required=True)
    phase_a_audit.add_argument("--out", type=Path, required=True)

    q0_assess = subparsers.add_parser(
        "q0-assess-continuity", help="materialize a Q0 landmark timeline and assess continuity"
    )
    q0_assess.add_argument("--db", type=Path, required=True)
    q0_assess.add_argument("--landmark", type=Path, required=True)
    q0_assess.add_argument("--assessed-at", type=_datetime, required=True)
    q0_assess.add_argument("--contradiction-observation-id", action="append", default=[])
    q0_assess.add_argument("--out", type=Path, required=True)

    continuity_review = subparsers.add_parser(
        "q0-review-continuity", help="persist an immutable continuity review"
    )
    continuity_review.add_argument("--db", type=Path, required=True)
    continuity_review.add_argument("--review", type=Path, required=True)
    continuity_review.add_argument("--out", type=Path, required=True)

    composite = subparsers.add_parser(
        "cti-register-composite", help="register a same-node, same-window CTI-only composite"
    )
    composite.add_argument("--db", type=Path, required=True)
    composite.add_argument("--accepted-assertions", type=Path, required=True)
    composite.add_argument("--node-id", required=True)
    composite.add_argument("--window-start", type=_datetime, required=True)
    composite.add_argument("--window-end", type=_datetime, required=True)
    composite.add_argument("--out", type=Path, required=True)

    precheck = subparsers.add_parser(
        "cti-register-precheck", help="register a bounded Q1 singleton/composite precheck"
    )
    precheck.add_argument("--db", type=Path, required=True)
    precheck.add_argument("--query-id", required=True)
    precheck.add_argument("--accepted-assertions", type=Path, required=True)
    precheck.add_argument("--node-id", required=True)
    precheck.add_argument("--cutoff-at", type=_datetime, required=True)
    precheck.add_argument("--page-budget", type=int, required=True)
    precheck.add_argument("--registered-at", type=_datetime, required=True)
    precheck.add_argument("--risk-flag", action="append", default=[])
    precheck.add_argument("--out", type=Path, required=True)

    precheck_result = subparsers.add_parser(
        "cti-record-precheck-result", help="persist a pending/partial/complete/failed precheck result"
    )
    precheck_result.add_argument("--db", type=Path, required=True)
    precheck_result.add_argument("--result", type=Path, required=True)
    precheck_result.add_argument("--out", type=Path, required=True)

    precheck_review = subparsers.add_parser(
        "cti-review-precheck", help="persist immutable human Q2 eligibility review"
    )
    precheck_review.add_argument("--db", type=Path, required=True)
    precheck_review.add_argument("--review", type=Path, required=True)
    precheck_review.add_argument("--out", type=Path, required=True)

    q2 = subparsers.add_parser(
        "register-q2", help="register Q2 only from eligible reviewed prechecks"
    )
    q2.add_argument("--db", type=Path, required=True)
    q2.add_argument("--version", required=True)
    q2.add_argument("--variant", default="primary")
    q2.add_argument("--query-text", required=True)
    q2.add_argument("--precheck-id", action="append", required=True)
    q2.add_argument("--config-hash", required=True)
    q2.add_argument("--registered-at", type=_datetime, required=True)

    phase_b_audit = subparsers.add_parser(
        "cti-audit-phase-b", help="audit Stage 2/3 continuity and Q2 eligibility gates"
    )
    phase_b_audit.add_argument("--db", type=Path, required=True)
    phase_b_audit.add_argument("--out", type=Path, required=True)

    feature_build = subparsers.add_parser(
        "feature-build", help="extract deterministic features and entity epochs from eligible runs"
    )
    feature_build.add_argument("--db", type=Path, required=True)
    feature_build.add_argument("--query-run-id", action="append", required=True)
    feature_build.add_argument("--source-eligibility-id", action="append", required=True)
    feature_build.add_argument("--extractor-version", default="feature-v1")
    feature_build.add_argument("--resolution-version", default="entity-resolution-v1")
    feature_build.add_argument("--out", type=Path, required=True)

    background_build = subparsers.add_parser(
        "feature-build-background", help="materialize a cutoff-matched reference snapshot"
    )
    background_build.add_argument("--db", type=Path, required=True)
    background_build.add_argument("--reference", type=Path, required=True)
    background_build.add_argument("--query-run-id", action="append", required=True)
    background_build.add_argument("--out", type=Path, required=True)

    feature_assess = subparsers.add_parser(
        "feature-assess", help="compute reproducible statistics and eligibility candidate status"
    )
    feature_assess.add_argument("--db", type=Path, required=True)
    feature_assess.add_argument("--feature-id", required=True)
    feature_assess.add_argument("--reference-set-id", required=True)
    feature_assess.add_argument("--anchor-observation-id", action="append", required=True)
    feature_assess.add_argument("--anchor-source-id", action="append", required=True)
    feature_assess.add_argument("--computed-at", type=_datetime, required=True)
    feature_assess.add_argument("--min-distinct-anchors", type=int, default=2)
    feature_assess.add_argument("--min-anchor-support", type=float, default=0.5)
    feature_assess.add_argument("--max-background-prevalence", type=float, default=0.1)
    feature_assess.add_argument("--out", type=Path, required=True)

    feature_review = subparsers.add_parser(
        "feature-review", help="persist immutable human feature eligibility review"
    )
    feature_review.add_argument("--db", type=Path, required=True)
    feature_review.add_argument("--review", type=Path, required=True)
    feature_review.add_argument("--out", type=Path, required=True)

    phase_c_audit = subparsers.add_parser(
        "cti-audit-phase-c", help="audit feature/background/cutoff/source reproducibility gates"
    )
    phase_c_audit.add_argument("--db", type=Path, required=True)
    phase_c_audit.add_argument("--out", type=Path, required=True)

    compose = subparsers.add_parser(
        "query-compose", help="compose and register a provenance-gated Q2/Q3 design"
    )
    compose.add_argument("--db", type=Path, required=True)
    compose.add_argument("--clauses", type=Path, required=True)
    compose.add_argument("--version", required=True)
    compose.add_argument("--variant", default="primary")
    compose.add_argument("--class", dest="query_class", choices=[
        QueryClass.Q2_DERIVED.value, QueryClass.Q3_CLUSTER.value
    ], required=True)
    compose.add_argument("--composition", choices=[
        item.value for item in QueryCompositionType
    ], required=True)
    compose.add_argument("--cutoff-at", type=_datetime, required=True)
    compose.add_argument("--registered-at", type=_datetime, required=True)
    compose.add_argument("--config-hash", required=True)
    compose.add_argument("--source-indicator-id", action="append", default=[])
    compose.add_argument("--background-snapshot-id", action="append", default=[])
    compose.add_argument("--api-schema-version", required=True)
    compose.add_argument("--parser-version", required=True)
    compose.add_argument("--normalizer-version", required=True)
    compose.add_argument("--entity-resolution-version", required=True)
    compose.add_argument("--out", type=Path, required=True)

    schedule = subparsers.add_parser(
        "query-register-schedule", help="register immutable query budget and schedule"
    )
    schedule.add_argument("--db", type=Path, required=True)
    schedule.add_argument("--design-id", required=True)
    schedule.add_argument("--interval-hours", type=int, required=True)
    schedule.add_argument("--starts-at", type=_datetime, required=True)
    schedule.add_argument("--max-alerts-per-run", type=int, required=True)
    schedule.add_argument("--max-credits-per-run", type=float, required=True)
    schedule.add_argument("--max-pages-per-run", type=int, required=True)
    schedule.add_argument("--tie-break-rule", required=True)
    schedule.add_argument("--registered-at", type=_datetime, required=True)
    schedule.add_argument("--out", type=Path, required=True)

    design_precheck = subparsers.add_parser(
        "query-record-precheck", help="record bounded development precheck state"
    )
    design_precheck.add_argument("--db", type=Path, required=True)
    design_precheck.add_argument("--precheck", type=Path, required=True)
    design_precheck.add_argument("--out", type=Path, required=True)

    design_review = subparsers.add_parser(
        "query-review", help="persist immutable human query design review"
    )
    design_review.add_argument("--db", type=Path, required=True)
    design_review.add_argument("--review", type=Path, required=True)
    design_review.add_argument("--out", type=Path, required=True)

    designed_freeze = subparsers.add_parser(
        "query-freeze-designed", help="freeze a reviewed design and immutable schedule"
    )
    designed_freeze.add_argument("--db", type=Path, required=True)
    designed_freeze.add_argument("--design-id", required=True)
    designed_freeze.add_argument("--frozen-at", type=_datetime, required=True)
    designed_freeze.add_argument("--valid-for-test-from", type=_datetime, required=True)
    designed_freeze.add_argument("--out", type=Path, required=True)

    phase_d_audit = subparsers.add_parser(
        "cti-audit-phase-d", help="audit query composition, review, budget, and freeze gates"
    )
    phase_d_audit.add_argument("--db", type=Path, required=True)
    phase_d_audit.add_argument("--out", type=Path, required=True)

    phase_e_schedule = subparsers.add_parser(
        "phase-e-schedule", help="materialize due prospective opportunities offline"
    )
    phase_e_schedule.add_argument("--db", type=Path, required=True)
    phase_e_schedule.add_argument("--query-id", required=True)
    phase_e_schedule.add_argument("--freeze", type=Path, required=True)
    phase_e_schedule.add_argument("--schedule", type=Path, required=True)
    phase_e_schedule.add_argument("--epochs", type=Path, required=True)
    phase_e_schedule.add_argument("--as-of", type=_datetime, required=True)
    phase_e_schedule.add_argument("--recorded-at", type=_datetime, required=True)
    phase_e_schedule.add_argument("--out", type=Path, required=True)

    phase_e_event = subparsers.add_parser(
        "phase-e-register-event", help="persist a prospective event and optional candidate"
    )
    phase_e_event.add_argument("--db", type=Path, required=True)
    phase_e_event.add_argument("--event", type=Path, required=True)
    phase_e_event.add_argument("--candidate", type=Path)
    phase_e_event.add_argument("--out", type=Path, required=True)

    phase_e_evidence = subparsers.add_parser(
        "phase-e-register-evidence", help="persist independent candidate evidence"
    )
    phase_e_evidence.add_argument("--db", type=Path, required=True)
    phase_e_evidence.add_argument("--evidence", type=Path, required=True)
    phase_e_evidence.add_argument("--out", type=Path, required=True)

    phase_e_adjudicate = subparsers.add_parser(
        "phase-e-adjudicate", help="persist an independent human candidate adjudication"
    )
    phase_e_adjudicate.add_argument("--db", type=Path, required=True)
    phase_e_adjudicate.add_argument("--adjudication", type=Path, required=True)
    phase_e_adjudicate.add_argument("--out", type=Path, required=True)

    phase_e_grade = subparsers.add_parser(
        "phase-e-grade", help="append a candidate grade history event"
    )
    phase_e_grade.add_argument("--db", type=Path, required=True)
    phase_e_grade.add_argument("--grade", type=Path, required=True)
    phase_e_grade.add_argument("--out", type=Path, required=True)

    phase_e_audit = subparsers.add_parser(
        "cti-audit-phase-e", help="audit prospective and independent-evidence gates"
    )
    phase_e_audit.add_argument("--db", type=Path, required=True)
    phase_e_audit.add_argument("--out", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdout_utf8()
    args = build_parser().parse_args(argv)
    if args.command == "verify-reuse":
        return _verify_reuse(args.reuse_config)
    if args.command == "cti-search":
        protocol = SearchProtocolRecord.model_validate(_load_json(args.protocol))
        watchlist = _load_json(args.watchlist)
        adapter = CtiAgentAdapter(args.cti_agent)
        expanded = adapter.expand_search_queries(protocol.search_terms, watchlist)
        backend = BraveProtocolSearchBackend(
            date_from=protocol.target_date_from,
            date_to=protocol.target_date_to,
            count=args.count,
            max_pages=args.max_pages,
        )
        result = run_search_stage(
            protocol=protocol,
            expanded_queries=expanded,
            backend=backend,
            domain_whitelist=args.whitelist,
            registry=CorpusRegistry(args.db),
            manifest_path=args.manifest,
            executed_at=args.executed_at,
        )
        print(json.dumps(asdict(result), ensure_ascii=False))
        return 0
    if args.command == "cti-import-existing":
        records = import_existing_cti(
            source_root=args.source_root,
            index_path=args.index,
            registry=CorpusRegistry(args.db),
            imported_at=args.imported_at,
        )
        payload = [{key: value for key, value in record.items() if key != "registered"}
                   for record in records]
        write_immutable_json(args.manifest, payload)
        print(json.dumps({
            "documents": payload,
            "database_effects": {
                "documents_inserted": sum(1 for record in records if record["registered"])
            },
        }, ensure_ascii=False))
        return 0
    if args.command == "cti-screen":
        search_result = _load_search_result(args.search_manifest)
        decisions = [ManualScreeningInput(
            candidate_id=item["candidate_id"],
            decision=ScreeningDecision(item["decision"]),
            reason_code=item["reason_code"],
            reviewer_id=item["reviewer_id"],
            notes=item.get("notes", ""),
        ) for item in _load_json(args.decisions)]
        decision_map = apply_manual_screening(
            search_result=search_result,
            decisions=decisions,
            registry=CorpusRegistry(args.db),
            reviewed_at=args.reviewed_at,
        )
        payload = {key: value.value for key, value in decision_map.items()}
        write_immutable_json(args.out, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.command == "cti-snapshot":
        search_result = _load_search_result(args.search_manifest)
        decision_map = {
            key: ScreeningDecision(value) for key, value in _load_json(args.decision_map).items()
        }
        snapshots = snapshot_included_candidates(
            search_result=search_result,
            decision_map=decision_map,
            fetcher=PassiveDocumentFetcher(args.whitelist, max_bytes=args.max_bytes),
            store=ImmutableSnapshotStore(args.snapshot_root),
            retrieved_at=args.retrieved_at,
        )
        payload = [asdict(record) for record in snapshots]
        write_immutable_json(args.manifest, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.command == "cti-verify-iocs":
        metadata = _load_snapshot_metadata(args.snapshot_metadata, args.document_id)
        document = _document_from_snapshot_metadata(
            metadata, args.published_at, args.published_time_precision,
            args.source_timezone,
        )
        snapshot_bytes = _verified_snapshot_bytes(metadata)
        result = verify_indicator_candidates(
            _load_json(args.candidates), snapshot_bytes, document,
            CtiAgentAdapter(args.cti_agent), available_at=args.available_at,
            source_text=(
                _ocr_sidecar_text(metadata)
                if metadata.get("text_snapshot_path")
                else None
            ),
        )
        write_immutable_json(args.out, asdict(result))
        print(json.dumps(asdict(result), ensure_ascii=False))
        return 0
    if args.command == "cti-extract-iocs":
        metadata = _load_snapshot_metadata(args.snapshot_metadata, args.document_id)
        document = _document_from_snapshot_metadata(
            metadata, args.published_at, args.published_time_precision,
            args.source_timezone,
        )
        snapshot_bytes = _verified_snapshot_bytes(metadata)
        extractor = AnthropicIndicatorExtractor(
            model=args.model,
            prompt_path=args.prompt,
            max_input_chars=args.max_input_chars,
            max_tokens=args.max_tokens,
        )
        result = extract_and_verify_indicators(
            snapshot_bytes,
            document,
            CtiAgentAdapter(args.cti_agent),
            extractor,
            available_at=args.available_at,
            ocr_sidecar_text=_ocr_sidecar_text(metadata),
            max_pdf_pages=args.max_pdf_pages,
            max_document_chars=args.max_document_chars,
        )
        write_immutable_json(args.out, asdict(result))
        print(json.dumps(asdict(result), ensure_ascii=False))
        return 0
    if args.command == "cti-register-indicators":
        metadata = _load_snapshot_metadata(args.snapshot_metadata, args.document_id)
        source_family_manifest = _load_json(args.source_family_manifest)
        source_family = SourceFamilyRecord.model_validate(
            source_family_manifest["source_family"]
        )
        source_relationships = [
            SourceRelationshipRecord.model_validate(item)
            for item in source_family_manifest.get("relationships", [])
        ]
        document = _document_from_snapshot_metadata(
            metadata, args.published_at, args.published_time_precision,
            args.source_timezone, source_family.source_family_id,
        )
        verified = _load_json(args.verified_manifest)
        result = ExtractionResult(
            indicators=tuple(VerifiedIndicator(**item) for item in verified["indicators"]),
            candidate_count=verified["candidate_count"],
            format_rejected=verified["format_rejected"],
            source_mismatch_rejected=verified["source_mismatch_rejected"],
            future_time_rejected=verified["future_time_rejected"],
            duplicate_count=verified["duplicate_count"],
            source_access_class=verified["source_access_class"],
            source_acquisition_mode=verified["source_acquisition_mode"],
            source_corpus_purpose=verified["source_corpus_purpose"],
            extraction_provenance=verified.get("extraction_provenance"),
        )
        secret = os.environ.get("ORB_PUBLIC_ID_HMAC_KEY")
        if not secret:
            raise RuntimeError("ORB_PUBLIC_ID_HMAC_KEY is required")
        indicators, mentions, assertions = build_indicator_records(
            result, document, ingested_at=args.ingested_at,
            public_id_hmac_key=secret.encode("utf-8"), campaign_id=args.campaign_id,
        )
        registry = CorpusRegistry(args.db)
        source_family_effects = registry.register_source_family(
            source_family, source_relationships
        )
        database_effects = registry.register_indicator_bundle(
            document=document, indicators=indicators, mentions=mentions,
            assertions=assertions,
        )
        payload = {
            "document_id": document.document_id,
            "source_family_id": source_family.source_family_id,
            "indicator_count": len(indicators),
            "assertion_count": len(assertions),
            "source_mention_count": len(mentions),
            "public_indicator_ids": [record.public_id for record in indicators],
            "assertion_ids": [record.assertion_id for record in assertions],
            "source_mention_ids": [record.mention_id for record in mentions],
        }
        write_immutable_json(args.out, payload)
        print(json.dumps({
            **payload,
            "source_family_effects": source_family_effects,
            "database_effects": database_effects,
        }, ensure_ascii=False))
        return 0
    if args.command == "cti-review-assertions":
        raw_reviews = _load_json(args.reviews)
        if not isinstance(raw_reviews, list):
            raise ValueError("assertion reviews manifest must be a JSON array")
        reviews = [AssertionReviewRecord.model_validate(item) for item in raw_reviews]
        inserted = CorpusRegistry(args.db).register_assertion_reviews(reviews)
        payload = {
            "review_ids": [item.review_id for item in reviews],
            "reviews_inserted": inserted,
        }
        write_immutable_json(args.out, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.command == "cti-plan-pivots":
        accepted_manifest = _load_json(args.accepted_assertions)
        if not isinstance(accepted_manifest, list) or not all(
            isinstance(item, str) for item in accepted_manifest
        ):
            raise ValueError("accepted assertions manifest must be a JSON string array")
        indicators = CorpusRegistry(args.db).accepted_pivot_sources(
            accepted_manifest, cutoff_at=args.cutoff_at
        )
        template_config = yaml.safe_load(args.template_config.read_text(encoding="utf-8")) or {}
        plans = register_pivot_plans(
            indicators,
            registry=QueryRegistry(args.db),
            censys_adapter=OrbhuntCensysAdapter(args.orbhunt),
            q1_template_config=template_config,
            registered_at=args.registered_at,
            cutoff_at=args.cutoff_at,
            query_version=args.version,
            config_hash=args.config_hash,
        )
        payload = [asdict(plan) for plan in plans]
        write_immutable_json(args.out, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.command == "cti-export-public-corpus":
        document_ids = _load_json(args.document_ids)
        if not isinstance(document_ids, list) or not all(
            isinstance(item, str) for item in document_ids
        ):
            raise ValueError("document IDs manifest must be a JSON string array")
        payload = CorpusRegistry(args.db).public_source_manifest(document_ids)
        write_immutable_json(args.out, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.command == "cti-audit-stage0":
        report = CorpusRegistry(args.db).stage0_gate_report()
        write_immutable_json(args.out, report)
        print(json.dumps(report, ensure_ascii=False))
        if not report["passed"]:
            raise RuntimeError("Stage 0 audit failed; inspect the report issues")
        return 0
    if args.command == "cti-audit-phase-a":
        report = CorpusRegistry(args.db).phase_a_gate_report()
        write_immutable_json(args.out, report)
        print(json.dumps(report, ensure_ascii=False))
        if not report["passed"]:
            raise RuntimeError("Phase A audit failed; inspect the report issues")
        return 0
    if args.command == "q0-assess-continuity":
        registry = QueryRegistry(args.db)
        landmark = Q0LandmarkRecord.model_validate(_load_json(args.landmark))
        registry.register_q0_landmark(landmark)
        hosts, services = registry.load_indicator_observations(landmark.indicator_id)
        timeline = list(materialize_q0_timeline(landmark, hosts, services))
        registry.register_q0_timeline(timeline)
        assessment = assess_continuity(
            landmark, timeline, assessed_at=args.assessed_at,
            explicit_contradiction_observation_ids=args.contradiction_observation_id,
        )
        registry.register_continuity_assessment(assessment)
        payload = {
            "landmark": landmark.model_dump(mode="json"),
            "timeline": [item.model_dump(mode="json") for item in timeline],
            "assessment": assessment.model_dump(mode="json"),
        }
        write_immutable_json(args.out, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.command == "q0-review-continuity":
        registry = QueryRegistry(args.db)
        review = ContinuityReviewRecord.model_validate(_load_json(args.review))
        inserted = registry.register_continuity_review(review)
        payload = {"review": review.model_dump(mode="json"), "inserted": inserted}
        write_immutable_json(args.out, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.command in {"cti-register-composite", "cti-register-precheck"}:
        assertion_ids = _load_json(args.accepted_assertions)
        if not isinstance(assertion_ids, list) or not all(
            isinstance(item, str) for item in assertion_ids
        ):
            raise ValueError("accepted assertions manifest must be a JSON string array")
        cutoff = args.window_end if args.command == "cti-register-composite" else args.cutoff_at
        sources = CorpusRegistry(args.db).accepted_pivot_sources(
            assertion_ids, cutoff_at=cutoff
        )
        registry = QueryRegistry(args.db)
        if args.command == "cti-register-composite":
            record = build_cti_only_composite(
                sources, node_id=args.node_id, window_start=args.window_start,
                window_end=args.window_end,
            )
            inserted = registry.register_cti_composite(record)
        else:
            record = register_precheck_definition(
                registry.get_query(args.query_id), sources, node_id=args.node_id,
                cutoff_at=args.cutoff_at, page_budget=args.page_budget,
                registered_at=args.registered_at, risk_flags=args.risk_flag,
            )
            inserted = registry.register_pivot_precheck(record)
        payload = {"record": record.model_dump(mode="json"), "inserted": inserted}
        write_immutable_json(args.out, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.command == "cti-record-precheck-result":
        registry = QueryRegistry(args.db)
        result = PivotPrecheckResultRecord.model_validate(_load_json(args.result))
        registry.get_pivot_precheck(result.precheck_id)
        inserted = registry.register_pivot_precheck_result(result)
        payload = {"result": result.model_dump(mode="json"), "inserted": inserted}
        write_immutable_json(args.out, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.command == "cti-review-precheck":
        registry = QueryRegistry(args.db)
        review = PivotEligibilityReviewRecord.model_validate(_load_json(args.review))
        inserted = registry.register_pivot_eligibility_review(review)
        payload = {"review": review.model_dump(mode="json"), "inserted": inserted}
        write_immutable_json(args.out, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.command == "cti-audit-phase-b":
        report = QueryRegistry(args.db).phase_b_gate_report()
        write_immutable_json(args.out, report)
        print(json.dumps(report, ensure_ascii=False))
        if not report["passed"]:
            raise RuntimeError("Phase B audit failed; inspect the report issues")
        return 0
    if args.command == "feature-build":
        query_registry = QueryRegistry(args.db)
        feature_registry = FeatureRegistry(args.db)
        authorized_runs = feature_registry.eligible_observation_run_ids(
            args.source_eligibility_id
        )
        requested_runs = set(args.query_run_id)
        if not requested_runs <= authorized_runs:
            raise ValueError(
                "feature runs are not covered by eligible Q0/Q1 decisions: "
                + ", ".join(sorted(requested_runs - authorized_runs))
            )
        hosts, services = [], []
        for query_run_id in sorted(requested_runs):
            execution = query_registry.get_execution(query_run_id)
            if execution.status != "complete":
                raise ValueError("only complete eligible executions can produce features")
            run_hosts, run_services = query_registry.load_observations(query_run_id)
            hosts.extend(run_hosts)
            services.extend(run_services)
        batch = extract_observation_features(
            hosts, services, extractor_version=args.extractor_version
        )
        effects = feature_registry.register_feature_batch(
            list(batch.features), list(batch.observations)
        )
        epochs = build_entity_epochs(
            hosts, list(batch.observations), list(batch.features),
            resolution_version=args.resolution_version,
        )
        epoch_count = feature_registry.register_entity_epochs(list(epochs))
        payload = {
            "feature_ids": [item.feature_id for item in batch.features],
            "feature_observation_ids": [item.feature_observation_id for item in batch.observations],
            "entity_epoch_ids": [item.entity_epoch_id for item in epochs],
            "source_eligibility_ids": sorted(set(args.source_eligibility_id)),
            "database_effects": {**effects, "epochs_inserted": epoch_count},
        }
        write_immutable_json(args.out, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.command == "feature-build-background":
        query_registry = QueryRegistry(args.db)
        feature_registry = FeatureRegistry(args.db)
        reference = ReferenceSetRecord.model_validate(_load_json(args.reference))
        if set(args.query_run_id) != set(reference.source_query_run_ids):
            raise ValueError("background CLI runs must match reference snapshot source runs")
        hosts, services = [], []
        for query_run_id in sorted(set(args.query_run_id)):
            execution = query_registry.get_execution(query_run_id)
            if execution.status != "complete":
                raise ValueError("background requires complete collection executions")
            run_hosts, run_services = query_registry.load_observations(query_run_id)
            hosts.extend(run_hosts)
            services.extend(run_services)
        memberships = materialize_reference_memberships(reference, hosts, services)
        reference_inserted = feature_registry.register_reference_set(reference)
        membership_count = feature_registry.register_reference_memberships(list(memberships))
        payload = {
            "reference_set": reference.model_dump(mode="json"),
            "membership_ids": [item.membership_id for item in memberships],
            "observable_denominator": sum(item.observable for item in memberships),
            "database_effects": {
                "reference_inserted": reference_inserted,
                "memberships_inserted": membership_count,
            },
        }
        write_immutable_json(args.out, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.command == "feature-assess":
        feature_registry = FeatureRegistry(args.db)
        feature = feature_registry.get_feature(args.feature_id)
        reference = feature_registry.get_reference_set(args.reference_set_id)
        snapshot = compute_feature_stat_snapshot(
            feature, reference,
            feature_registry.load_reference_memberships(args.reference_set_id),
            feature_registry.load_feature_observations(args.feature_id),
            anchor_observation_ids=args.anchor_observation_id,
            anchor_source_ids=args.anchor_source_id,
            computed_at=args.computed_at,
        )
        feature_registry.register_stat_snapshot(snapshot)
        assessment = assess_feature_eligibility(
            feature, snapshot, assessed_at=args.computed_at,
            min_distinct_anchors=args.min_distinct_anchors,
            min_anchor_support=args.min_anchor_support,
            max_background_prevalence=args.max_background_prevalence,
        )
        feature_registry.register_eligibility_assessment(assessment)
        payload = {
            "stat_snapshot": snapshot.model_dump(mode="json"),
            "eligibility_assessment": assessment.model_dump(mode="json"),
            "query_eligible": False,
            "human_review_required": assessment.status.value == "candidate",
        }
        write_immutable_json(args.out, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.command == "feature-review":
        feature_registry = FeatureRegistry(args.db)
        review = FeatureEligibilityReviewRecord.model_validate(_load_json(args.review))
        inserted = feature_registry.register_eligibility_review(review)
        payload = {
            "review": review.model_dump(mode="json"), "inserted": inserted,
            "eligible_feature_ids": feature_registry.eligible_feature_ids(),
        }
        write_immutable_json(args.out, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.command == "cti-audit-phase-c":
        report = FeatureRegistry(args.db).phase_c_gate_report()
        write_immutable_json(args.out, report)
        print(json.dumps(report, ensure_ascii=False))
        if not report["passed"]:
            raise RuntimeError("Phase C audit failed; inspect the report issues")
        return 0
    if args.command == "query-compose":
        raw_clauses = _load_json(args.clauses)
        if not isinstance(raw_clauses, list):
            raise ValueError("query clauses manifest must be a JSON array")
        clauses = [QueryClauseRecord.model_validate(item) for item in raw_clauses]
        query_registry = QueryRegistry(args.db)
        design_registry = QueryDesignRegistry(args.db)
        feature_registry = FeatureRegistry(args.db)
        eligible_feature_ids = set(feature_registry.eligible_feature_ids())
        feature_ids = sorted({
            item.source_feature_id for item in clauses if item.source_feature_id
        })
        eligible_features = {
            feature_id: feature_registry.get_feature(feature_id)
            for feature_id in feature_ids if feature_id in eligible_feature_ids
        }
        precheck_ids = sorted({
            item.source_precheck_id for item in clauses if item.source_precheck_id
        })
        assertion_ids = {
            item.source_assertion_id for item in clauses if item.source_assertion_id
        }
        for precheck_id in precheck_ids:
            assertion_ids.update(query_registry.get_pivot_precheck(precheck_id).assertion_ids)
        query_class = QueryClass(args.query_class)
        composition = QueryCompositionType(args.composition)
        rendered = render_query(
            clauses, composition_type=composition, query_class=query_class,
            cutoff_at=args.cutoff_at,
            accepted_assertion_ids=design_registry._accepted_assertion_ids(),
            eligible_precheck_ids=set(query_registry.eligible_q2_precheck_ids()),
            eligible_features=eligible_features,
        )
        record = query_registry.register_query(
            query_version=args.version, query_variant=args.variant,
            query_class=query_class, query_text=rendered,
            developed_from_split=DatasetSplit.DEVELOPMENT,
            config_hash=args.config_hash,
            source_indicator_ids=args.source_indicator_id,
            source_assertion_ids=sorted(assertion_ids),
            source_available_at=max(item.available_at for item in clauses),
            source_feature_ids=feature_ids, source_precheck_ids=precheck_ids,
            registered_at=args.registered_at,
        )
        design = build_query_design(
            record, clauses, variant=args.variant, composition_type=composition,
            cutoff_at=args.cutoff_at,
            background_snapshot_ids=args.background_snapshot_id,
            api_schema_version=args.api_schema_version,
            parser_version=args.parser_version,
            normalizer_version=args.normalizer_version,
            entity_resolution_version=args.entity_resolution_version,
            registered_at=args.registered_at,
        )
        inserted = design_registry.register_design(design, clauses)
        payload = {
            "query": record.model_dump(mode="json"),
            "design": design.model_dump(mode="json"),
            "clause_ids": design.clause_ids, "inserted": inserted,
        }
        write_immutable_json(args.out, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.command == "query-register-schedule":
        registry = QueryDesignRegistry(args.db)
        design = registry.get_design(args.design_id)
        schedule = build_budget_schedule(
            design, interval_hours=args.interval_hours, starts_at=args.starts_at,
            max_alerts_per_run=args.max_alerts_per_run,
            max_credits_per_run=args.max_credits_per_run,
            max_pages_per_run=args.max_pages_per_run,
            tie_break_rule=args.tie_break_rule, registered_at=args.registered_at,
        )
        inserted = registry.register_schedule(schedule)
        payload = {"schedule": schedule.model_dump(mode="json"), "inserted": inserted}
        write_immutable_json(args.out, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.command == "query-record-precheck":
        registry = QueryDesignRegistry(args.db)
        precheck = QueryDesignPrecheckRecord.model_validate(_load_json(args.precheck))
        inserted = registry.register_precheck(precheck)
        payload = {"precheck": precheck.model_dump(mode="json"), "inserted": inserted}
        write_immutable_json(args.out, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.command == "query-review":
        registry = QueryDesignRegistry(args.db)
        review = QueryDesignReviewRecord.model_validate(_load_json(args.review))
        inserted = registry.register_review(review)
        payload = {"review": review.model_dump(mode="json"), "inserted": inserted}
        write_immutable_json(args.out, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.command == "query-freeze-designed":
        design_registry = QueryDesignRegistry(args.db)
        design = design_registry.get_design(args.design_id)
        manifest = design_registry.prepare_freeze_manifest(
            args.design_id, frozen_at=args.frozen_at,
            valid_for_test_from=args.valid_for_test_from,
        )
        design_registry.register_freeze_manifest(manifest)
        query = QueryRegistry(args.db).freeze_query(
            design.query_id, frozen_at=args.frozen_at,
            valid_for_test_from=args.valid_for_test_from,
        )
        payload = {
            "query": query.model_dump(mode="json"),
            "freeze_manifest": manifest.model_dump(mode="json"),
        }
        write_immutable_json(args.out, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.command == "cti-audit-phase-d":
        report = QueryDesignRegistry(args.db).phase_d_gate_report()
        write_immutable_json(args.out, report)
        print(json.dumps(report, ensure_ascii=False))
        if not report["passed"]:
            raise RuntimeError("Phase D audit failed; inspect the report issues")
        return 0
    if args.command == "phase-e-schedule":
        query = QueryRegistry(args.db).get_query(args.query_id)
        freeze = QueryFreezeManifestRecord.model_validate(_load_json(args.freeze))
        schedule = QueryBudgetScheduleRecord.model_validate(_load_json(args.schedule))
        raw_epochs = _load_json(args.epochs)
        if not isinstance(raw_epochs, list):
            raise ValueError("entity epochs manifest must be a JSON array")
        epochs = [EntityEpochRecord.model_validate(item) for item in raw_epochs]
        opportunities = materialize_due_opportunities(
            query, freeze, schedule, epochs, as_of=args.as_of, recorded_at=args.recorded_at
        )
        registry = ProspectiveRegistry(args.db)
        inserted = sum(registry.record_opportunity(item) for item in opportunities)
        payload = {"opportunities": [item.model_dump(mode="json") for item in opportunities],
                   "inserted": inserted}
        write_immutable_json(args.out, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.command == "phase-e-register-event":
        registry = ProspectiveRegistry(args.db)
        event = ProspectiveObservationEventRecord.model_validate(_load_json(args.event))
        payload = {"event_inserted": registry.register_observation_event(event)}
        if args.candidate:
            candidate = CandidateRecord.model_validate(_load_json(args.candidate))
            payload["candidate_inserted"] = registry.register_candidate(candidate)
        write_immutable_json(args.out, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.command == "phase-e-register-evidence":
        registry = ProspectiveRegistry(args.db)
        evidence = CandidateEvidenceRecord.model_validate(_load_json(args.evidence))
        payload = {"evidence": evidence.model_dump(mode="json"),
                   "inserted": registry.register_evidence(evidence)}
        write_immutable_json(args.out, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.command == "phase-e-adjudicate":
        registry = ProspectiveRegistry(args.db)
        record = CandidateAdjudicationRecord.model_validate(_load_json(args.adjudication))
        payload = {"adjudication": record.model_dump(mode="json"),
                   "inserted": registry.register_adjudication(record)}
        write_immutable_json(args.out, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.command == "phase-e-grade":
        registry = ProspectiveRegistry(args.db)
        record = CandidateGradeEventRecord.model_validate(_load_json(args.grade))
        payload = {"grade_event": record.model_dump(mode="json"),
                   "inserted": registry.register_grade_event(record)}
        write_immutable_json(args.out, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.command == "cti-audit-phase-e":
        report = ProspectiveRegistry(args.db).phase_e_gate_report()
        write_immutable_json(args.out, report)
        print(json.dumps(report, ensure_ascii=False))
        if not report["ok"]:
            raise RuntimeError("Phase E audit failed; inspect the report issues")
        return 0
    if args.command == "normalize-censys":
        registry = QueryRegistry(args.db)
        execution = registry.get_execution(args.query_run_id)
        if execution.status != "complete":
            raise ValueError("only complete Censys executions can be normalized")
        query = registry.get_query(execution.query_id)
        page_paths = sorted(args.raw_directory.glob("page-*.json"))
        if not page_paths:
            raise FileNotFoundError("no immutable Censys page files found")
        pages = [_load_json(path) for path in page_paths]
        secret = os.environ.get("ORB_PUBLIC_ID_HMAC_KEY")
        corpus_registry = CorpusRegistry(args.db)
        batch = OrbhuntCensysAdapter().normalize_cached_pages(
            page_records=pages, query=query, execution=execution,
            collected_at=args.collected_at, extractor_version=args.extractor_version,
            public_id_hmac_key=secret.encode("utf-8") if secret else None,
            known_ip_indicator_ids=corpus_registry.ipv4_indicator_id_map(),
        )
        discovered_inserted = corpus_registry.register_indicators(
            list(batch.discovered_indicators)
        ) if batch.discovered_indicators else 0
        effects = registry.register_observations(
            list(batch.host_observations), list(batch.service_observations)
        )
        payload = {
            "query_run_id": execution.query_run_id,
            "host_observation_ids": [item.observation_id for item in batch.host_observations],
            "service_observation_ids": [
                item.service_observation_id for item in batch.service_observations
            ],
            "discovered_public_ids": [item.public_id for item in batch.discovered_indicators],
            "raw_page_hashes": list(batch.raw_page_hashes),
            "extractor_version": args.extractor_version,
        }
        write_immutable_json(args.out, payload)
        print(json.dumps({
            **payload,
            "database_effects": {**effects, "discovered_indicators_inserted": discovered_inserted},
        }, ensure_ascii=False))
        return 0
    if args.command == "extract-fingerprints":
        registry = QueryRegistry(args.db)
        execution = registry.get_execution(args.query_run_id)
        if execution.status != "complete":
            raise ValueError("only complete Censys executions can produce fingerprints")
        hosts, services = registry.load_observations(args.query_run_id)
        batch = OrbhuntCensysAdapter().derive_fingerprint_graph(
            host_observations=hosts,
            service_observations=services,
            extractor_version=args.extractor_version,
        )
        effects = registry.register_fingerprint_graph(
            list(batch.fingerprints), list(batch.relations)
        )
        shared = registry.build_shared_fingerprint_relations(
            [record.fingerprint_id for record in batch.fingerprints]
        )
        shared_effects = registry.register_fingerprint_graph([], shared)
        payload = {
            "query_run_id": args.query_run_id,
            "extractor_version": args.extractor_version,
            "fingerprint_ids": [record.fingerprint_id for record in batch.fingerprints],
            "direct_relation_ids": [record.relation_id for record in batch.relations],
            "shared_relation_ids": [record.relation_id for record in shared],
        }
        write_immutable_json(args.out, payload)
        print(json.dumps({
            **payload,
            "database_effects": {
                **effects,
                "shared_relations_inserted": shared_effects["relations_inserted"],
            },
        }, ensure_ascii=False))
        return 0
    registry = QueryRegistry(args.db)
    if args.command == "register-q0":
        accepted = CorpusRegistry(args.db).accepted_pivot_sources(
            [args.source_assertion_id], cutoff_at=args.cutoff_at
        )[0]
        if accepted.scope != "ip":
            raise ValueError("register-q0 requires an accepted IP assertion")
        record = register_q0_seed(
            registry,
            indicator_id=accepted.indicator_id,
            ip_value=accepted.value,
            indicator_available_at=accepted.available_at,
            source_assertion_id=args.source_assertion_id,
            cutoff_at=args.cutoff_at,
            registered_at=args.registered_at,
            query_version=args.version,
            config_hash=args.config_hash,
        )
    elif args.command == "register-query":
        query_class = QueryClass(args.query_class)
        if query_class in {QueryClass.Q0_SEED, QueryClass.Q1_DIRECT_PIVOT, QueryClass.Q2_DERIVED}:
            raise ValueError(
                "Q0/Q1/Q2 registration requires its provenance-gated workflow"
            )
        record = registry.register_query(
            query_version=args.version,
            query_variant=args.variant,
            query_class=query_class,
            query_text=args.query_text,
            developed_from_split=DatasetSplit(args.split),
            config_hash=args.config_hash,
            source_indicator_ids=args.source_indicator_id,
            source_feature_ids=args.source_feature_id,
        )
    elif args.command == "register-q2":
        record = registry.register_q2_from_prechecks(
            query_version=args.version, query_text=args.query_text,
            query_variant=args.variant,
            precheck_ids=args.precheck_id, config_hash=args.config_hash,
            registered_at=args.registered_at,
        )
    elif args.command == "validate-query":
        record = registry.mark_validated(args.query_id)
    elif args.command == "freeze-query":
        record = registry.freeze_query(
            args.query_id, frozen_at=args.frozen_at,
            valid_for_test_from=args.valid_for_test_from,
        )
    elif args.command == "collect-censys":
        query = registry.get_query(args.query_id)
        split = DatasetSplit(args.split)
        ensure_execution_allowed(query, split, args.executed_at, args.cutoff_time)
        if query.query_class is QueryClass.Q0_SEED:
            fetcher = CensysQ0HostLookupFetcher()
            api_schema_version = "censys-platform-v3-host-lookup"
        else:
            fetcher = CensysPlatformHttpFetcher()
            api_schema_version = "censys-platform-v3-search"
        run_material = "|".join([
            query.query_id, query.query_hash, split.value, args.executed_at.isoformat()
        ])
        query_run_id = f"censys-run-{sha256_text(run_material)[:16]}"
        run_directory = args.raw_root / query_run_id
        result = PaginatedCensysCollector(fetcher).collect(
            query=query.query_text,
            run_directory=run_directory,
            page_size=args.page_size,
            max_pages=args.max_pages,
            fields=args.fields,
        )
        execution = QueryExecutionRecord(
            query_run_id=query_run_id,
            query_id=query.query_id,
            query_hash=query.query_hash,
            cutoff_time=args.cutoff_time,
            executed_at=args.executed_at,
            dataset_split=split,
            result_count=result.hit_count,
            result_manifest_hash=sha256_file(run_directory / "checkpoints.jsonl"),
            api_schema_version=api_schema_version,
            status=result.status,
        )
        idempotent_replay = _record_execution_idempotent(registry, execution)
        print(json.dumps({
            "execution": execution.model_dump(mode="json"),
            "page_count": result.page_count,
            "raw_directory": str(run_directory),
            "idempotent_replay": idempotent_replay,
        }, ensure_ascii=False))
        return 0
    else:
        raise AssertionError(args.command)
    print(record.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
