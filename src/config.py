"""연구 설정 로더와 경로 검증.

목적: 외부 재사용 저장소와 연구 저장 위치를 한 설정에서 로드한다.
지원 RQ: RQ1∼RQ5 공통.
재사용 원천: 없음. 외부 코드를 호출하기 전의 독립 통제 계층이다.
설계: YAML을 로드하고 외부 저장소는 읽기 전용 경로로만 검증한다.
입력·출력: YAML 경로를 입력받아 ProjectConfig를 반환한다.
시간·provenance 통제: 연구 시작시각은 timezone-aware timestamp만 허용한다.
보안·라이선스: 설정에 API key·token을 기록하지 않는다.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictConfigModel(BaseModel):
    """오타나 미지원 설정을 조용히 무시하지 않는 설정 기반 모델."""

    model_config = ConfigDict(extra="forbid")


class SourceProjectConfig(StrictConfigModel):
    """읽기 전용 재사용 원천 저장소 설정."""

    path: Path
    read_only: bool = True


class StorageConfig(StrictConfigModel):
    """연구 산출물의 논리 저장 위치."""

    registry_db: Path
    manifests: Path
    raw: Path
    curated: Path
    existing_cti: Path = Path("data/cti")


class SecurityConfig(StrictConfigModel):
    """passive-data 연구 금지선."""

    passive_data_only: bool = True
    allow_active_scanning: bool = False
    allow_automatic_blocking: bool = False
    require_live_censys_gate: bool = True
    live_censys_gate_name: str = "ALLOW_LIVE_CENSYS"


class QueryPolicyConfig(StrictConfigModel):
    """허용 query class와 dataset split."""

    classes: list[str]
    splits: list[str]
    require_freeze_for_prospective_test: bool = True


class CtiSearchConfig(StrictConfigModel):
    """systematic CTI 검색과 screening의 fail-closed 정책."""

    require_registered_protocol: bool = True
    require_domain_whitelist: bool = True
    publication_date_fallback: Literal["prohibited"] = "prohibited"
    require_manual_screening: bool = True
    domain_whitelist: list[str]
    backend: Literal["brave-web-search-v1"] = "brave-web-search-v1"
    count: int = Field(default=20, ge=1, le=20)
    max_pages: int = Field(default=10, ge=1, le=10)
    snapshot_max_bytes: int = Field(default=25 * 1024 * 1024, ge=1024)
    search_live_gate_name: str = "ALLOW_LIVE_CTI_SEARCH"
    snapshot_live_gate_name: str = "ALLOW_CTI_SNAPSHOT_FETCH"

    @field_validator("domain_whitelist")
    @classmethod
    def require_nonempty_whitelist(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(item.strip().lower() for item in value if item.strip()))
        if not normalized:
            raise ValueError("CTI domain_whitelist cannot be empty")
        return normalized


class CtiCorpusConfig(StrictConfigModel):
    require_source_family: Literal[True] = True
    require_source_access_class: Literal[True] = True
    public_export_allowed_access_classes: list[Literal["public"]] = ["public"]
    development_excluded_acquisition_modes: list[Literal["prospective_validation"]] = [
        "prospective_validation"
    ]


class CensysCollectionConfig(StrictConfigModel):
    """Censys pagination·raw 저장·부분 실행 제외 정책."""

    api_surface: Literal["censys-platform-v3"] = "censys-platform-v3"
    endpoint: str
    page_size: int = Field(default=100, ge=1, le=100)
    max_pages: int | None = Field(default=None, ge=1)
    fields: list[str] = Field(default_factory=list)
    raw_page_pattern: str = "page-{page_number:06d}.json"
    checkpoint_file: str = "checkpoints.jsonl"
    exclude_partial_runs_from_analysis: bool = True


class PhaseBPolicyConfig(StrictConfigModel):
    """Stage 2/3 continuity and bounded-precheck fail-closed policy."""

    continuity_statuses: list[Literal[
        "continuous", "probable", "unknown", "reassigned", "contradicted"
    ]] = ["continuous", "probable", "unknown", "reassigned", "contradicted"]
    require_review_for_probable: Literal[True] = True
    precheck_page_budget: int = Field(default=2, ge=1)
    exclude_partial_prechecks_from_q2: Literal[True] = True
    require_eligible_precheck_for_q2: Literal[True] = True


class BackgroundPolicyConfig(StrictConfigModel):
    require_matched_background: Literal[True] = True
    allowed_strata: list[Literal["protocol", "ports", "product", "time_window"]] = [
        "protocol", "ports", "product", "time_window"
    ]
    claim_scope: Literal["matched_background"] = "matched_background"


class FeatureEligibilityConfig(StrictConfigModel):
    min_distinct_anchors: int = Field(default=2, ge=1)
    min_anchor_support: float = Field(default=0.5, ge=0, le=1)
    max_background_prevalence: float = Field(default=0.1, ge=0, le=1)
    require_human_review: Literal[True] = True
    block_shared_or_default: Literal[True] = True
    block_unstable_or_unavailable: Literal[True] = True


class QueryFreezePolicyConfig(StrictConfigModel):
    require_human_review: Literal[True] = True
    require_passing_bounded_precheck: Literal[True] = True
    performance_claim_allowed_in_precheck: Literal[False] = False
    max_precheck_pages: int = Field(default=2, ge=1)
    default_interval_hours: int = Field(default=168, ge=1)
    max_alerts_per_run: int = Field(default=20, ge=1)
    max_credits_per_run: float = Field(default=100, gt=0)


class PhaseEPolicyConfig(StrictConfigModel):
    require_record_observed_at: Literal[True] = True
    unresolved_time_status: Literal["prospective_time_unresolved"] = "prospective_time_unresolved"
    require_raw_manifest_hash: Literal[True] = True
    require_independent_source_family: Literal[True] = True
    block_discovery_feature_reuse: Literal[True] = True
    block_pre_candidate_evidence: Literal[True] = True
    require_separate_human_adjudicator: Literal[True] = True
    append_only_grade_history: Literal[True] = True


class CtiIocExtractionConfig(StrictConfigModel):
    """CTI 원문 structured extraction의 모델·입력 상한·live gate 정책."""

    backend: Literal["anthropic-structured-tool"] = "anthropic-structured-tool"
    model: str = Field(min_length=1)
    prompt_path: Path
    max_input_chars: int = Field(default=200_000, ge=1)
    max_tokens: int = Field(default=8192, ge=1)
    max_pdf_pages: int = Field(default=500, ge=1)
    max_document_chars: int = Field(default=2_000_000, ge=1)
    live_gate_name: Literal["ALLOW_LIVE_CTI_EXTRACTION"] = "ALLOW_LIVE_CTI_EXTRACTION"
    require_raw_form_source_match: Literal[True] = True


class ProjectConfig(StrictConfigModel):
    """전체 연구 설정."""

    project_timezone: str
    research_start_at: datetime
    source_projects: dict[str, SourceProjectConfig]
    storage: StorageConfig
    security: SecurityConfig
    query_policy: QueryPolicyConfig
    cti_search: CtiSearchConfig
    cti_corpus: CtiCorpusConfig
    cti_ioc_extraction: CtiIocExtractionConfig
    censys_collection: CensysCollectionConfig
    phase_b_policy: PhaseBPolicyConfig = Field(default_factory=PhaseBPolicyConfig)
    background_policy: BackgroundPolicyConfig = Field(default_factory=BackgroundPolicyConfig)
    feature_eligibility: FeatureEligibilityConfig = Field(
        default_factory=FeatureEligibilityConfig
    )
    query_freeze_policy: QueryFreezePolicyConfig = Field(
        default_factory=QueryFreezePolicyConfig
    )
    phase_e_policy: PhaseEPolicyConfig = Field(default_factory=PhaseEPolicyConfig)
    config_path: Path | None = Field(default=None, exclude=True)

    @field_validator("research_start_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("research_start_at must include a timezone")
        return value

    def assert_safe(self) -> None:
        if not self.security.passive_data_only:
            raise ValueError("passive_data_only must remain true")
        if self.security.allow_active_scanning:
            raise ValueError("active scanning is outside the authorized research scope")
        if self.security.allow_automatic_blocking:
            raise ValueError("automatic blocking is outside the authorized research scope")
        for name, source in self.source_projects.items():
            if not source.read_only:
                raise ValueError(f"source project must be read-only: {name}")
            if not source.path.is_dir():
                raise FileNotFoundError(f"source project not found: {source.path}")


def load_config(path: Path) -> ProjectConfig:
    """UTF-8 YAML을 읽어 안전 정책을 검증한 ProjectConfig를 반환한다."""

    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    config = ProjectConfig.model_validate({**raw, "config_path": path.resolve()})
    config.assert_safe()
    return config
