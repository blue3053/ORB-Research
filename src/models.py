"""연구 공통 데이터 모델.

목적: CTI 획득 provenance와 Q0∼Q3 query 생명주기를 엄격한 schema로 정의한다.
지원 RQ: RQ1∼RQ5 공통, 특히 RQ4·RQ5의 시간 누수 방지.
재사용 원천: ORB_Hunt_v5 Pydantic/UTC 모델 구조를 설계 참고로 재사용한다.
설계: UTC 정규화, 명시적 enum, extra field 금지를 적용한다.
입력·출력: registry·manifest가 교환하는 Pydantic record.
시간·provenance 통제: 모든 평가 시간은 timezone-aware UTC로 정규화한다.
보안·라이선스: 원시 IoC 값이나 API secret을 모델의 공개 필드로 요구하지 않는다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AcquisitionMode(StrEnum):
    EXISTING_CURATED = "existing_curated"
    SYSTEMATIC_PUBLIC = "systematic_public"
    COMMERCIAL = "commercial"
    PROSPECTIVE_VALIDATION = "prospective_validation"


class SourceAccessClass(StrEnum):
    PUBLIC = "public"
    RESTRICTED = "restricted"


class CorpusPurpose(StrEnum):
    DEVELOPMENT = "development"
    PROSPECTIVE_VALIDATION = "prospective_validation"


class TimePrecision(StrEnum):
    EXACT_TIMESTAMP = "exact_timestamp"
    DATE = "date"
    DAY = "day"
    MONTH = "month"
    YEAR = "year"
    RANGE = "range"
    UNKNOWN = "unknown"


class SourceRelationshipType(StrEnum):
    ORIGINAL = "original"
    REPUBLISH = "republish"
    TRANSLATION = "translation"
    SUMMARY = "summary"
    FOLLOW_UP = "follow_up"


class QueryClass(StrEnum):
    Q0_SEED = "Q0_SEED"
    Q1_DIRECT_PIVOT = "Q1_DIRECT_PIVOT"
    Q2_DERIVED = "Q2_DERIVED"
    Q3_CLUSTER = "Q3_CLUSTER"


class DatasetSplit(StrEnum):
    DEVELOPMENT = "development"
    VALIDATION = "validation"
    PROSPECTIVE_TEST = "prospective_test"
    OPERATIONAL = "operational"


class QueryStatus(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    FROZEN = "frozen"
    RETIRED = "retired"


class ScreeningDecision(StrEnum):
    INCLUDE = "include"
    EXCLUDE = "exclude"
    DUPLICATE = "duplicate"
    PENDING = "pending"


class IndicatorType(StrEnum):
    IPV4 = "ipv4"
    DOMAIN = "domain"
    URL = "url"
    CERT = "cert"
    SPKI = "spki"
    SSH_KEY = "ssh_key"
    OTHER = "other"


class IndicatorSensitivity(StrEnum):
    PUBLIC = "public"
    RESTRICTED = "restricted"
    ACTIVE_VICTIM = "active_victim"


class AssertionRole(StrEnum):
    RELAY_ORB = "relay_orb"
    EXIT = "exit"
    MIDDLE = "middle"
    CONTROLLER = "controller"
    STAGING = "staging"
    C2 = "c2"
    SCANNER = "scanner"
    VICTIM = "victim"
    SINKHOLE = "sinkhole"
    UNKNOWN = "unknown"


class AssertionVerdict(StrEnum):
    CONFIRMED = "confirmed"
    HIGH_CONFIDENCE = "high_confidence"
    CANDIDATE = "candidate"
    BENIGN = "benign"
    EXCLUDED = "excluded"


class EvidenceType(StrEnum):
    INCIDENT_RESPONSE = "incident_response"
    MALWARE = "malware"
    TELEMETRY = "telemetry"
    PIVOT = "pivot"
    CLAIM = "claim"
    UNKNOWN = "unknown"


class ReviewerStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class NegativeReason(StrEnum):
    NOT_SCANNED = "not_scanned"
    NO_RESPONSE = "no_response"
    API_ERROR = "api_error"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"


class Transport(StrEnum):
    TCP = "tcp"
    UDP = "udp"


class ObservationTimeBasis(StrEnum):
    CENSYS_HOST_UPDATED_AT = "censys_host_updated_at"
    CENSYS_SERVICE_OBSERVED_AT = "censys_service_observed_at"
    UNAVAILABLE = "unavailable"


class ContinuityStatus(StrEnum):
    CONTINUOUS = "continuous"
    PROBABLE = "probable"
    UNKNOWN = "unknown"
    REASSIGNED = "reassigned"
    CONTRADICTED = "contradicted"


class TimelineObservationKind(StrEnum):
    POSITIVE = "positive"
    NOT_FOUND = "not_found"
    MISSING_SCAN = "missing_scan"
    NO_RESPONSE = "no_response"
    API_ERROR = "api_error"


class PrecheckStatus(StrEnum):
    PENDING = "pending"
    COMPLETE = "complete"
    PARTIAL_MAX_PAGES = "partial_max_pages"
    FAILED = "failed"


class FingerprintType(StrEnum):
    CERT = "cert"
    SPKI = "spki"
    JARM = "jarm"
    JA4 = "ja4"
    BANNER = "banner"
    HTTP = "http"
    SSH = "ssh"
    PORTSET = "portset"
    COMPOSITE = "composite"


class FingerprintSensitivity(StrEnum):
    PUBLIC = "public"
    RESTRICTED = "restricted"


class EntityRelationType(StrEnum):
    OBSERVED_WITH = "observed_with"
    RESOLVES_TO = "resolves_to"
    SHARES_CERT = "shares_cert"
    SHARES_SPKI = "shares_spki"
    SHARES_BANNER = "shares_banner"
    SHARES_JARM = "shares_jarm"
    SAME_PREFIX = "same_prefix"
    REPORTED_WITH = "reported_with"


def utc(value: datetime) -> datetime:
    """timezone-aware datetime을 UTC로 정규화한다."""

    if value.tzinfo is None:
        raise ValueError("timezone-aware datetime required")
    return value.astimezone(timezone.utc)


class StrictUTCModel(BaseModel):
    """추가 필드를 거부하는 UTC 기반 record."""

    model_config = ConfigDict(extra="forbid")


class SearchProtocolRecord(StrictUTCModel):
    search_protocol_id: str
    protocol_version: str
    research_cutoff_at: datetime
    source_access_class: SourceAccessClass
    acquisition_mode: AcquisitionMode
    target_date_from: str
    target_date_to: str
    target_publishers: list[str]
    search_terms: list[str]
    inclusion_rules: list[str]
    exclusion_rules: list[str]
    deduplication_rule: str
    registered_at: datetime
    protocol_hash: str

    _research_cutoff_at_utc = field_validator("research_cutoff_at")(utc)
    _registered_at_utc = field_validator("registered_at")(utc)


class SourceFamilyRecord(StrictUTCModel):
    source_family_id: str
    canonical_document_id: str
    reviewer_id: str
    reviewed_at: datetime

    _reviewed_at_utc = field_validator("reviewed_at")(utc)


class SourceRelationshipRecord(StrictUTCModel):
    relationship_id: str
    source_family_id: str
    document_id: str
    related_document_id: str
    relationship_type: SourceRelationshipType
    reviewer_id: str
    reviewed_at: datetime

    _reviewed_at_utc = field_validator("reviewed_at")(utc)


class SourceDocumentRecord(StrictUTCModel):
    document_id: str
    canonical_url: str
    publisher: str
    title: str
    published_at: datetime | None
    published_at_raw: str
    published_time_precision: TimePrecision
    source_timezone: str
    retrieved_at: datetime
    content_sha256: str
    text_content_sha256: str | None = None
    acquisition_mode: AcquisitionMode
    source_access_class: SourceAccessClass
    corpus_purpose: CorpusPurpose
    search_protocol_id: str | None = None
    discovery_query_id: str | None = None
    source_independence: str = "unknown"
    source_family_id: str | None = None

    _published_at_utc = field_validator("published_at")(
        lambda value: None if value is None else utc(value)
    )
    _retrieved_at_utc = field_validator("retrieved_at")(utc)

    @model_validator(mode="after")
    def _validate_publication_time(self) -> "SourceDocumentRecord":
        for name in ("document_id", "canonical_url", "publisher", "title"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must be nonempty")
        for name in ("content_sha256", "text_content_sha256"):
            value = getattr(self, name)
            if value is not None and not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
        if not self.published_at_raw.strip():
            raise ValueError("published_at_raw must be nonempty")
        if not self.source_timezone.strip():
            raise ValueError("source_timezone must be nonempty")
        is_exact = self.published_time_precision is TimePrecision.EXACT_TIMESTAMP
        if is_exact != (self.published_at is not None):
            raise ValueError(
                "published_at is allowed exactly when precision is exact_timestamp"
            )
        if self.published_at is not None and self.published_at > self.retrieved_at:
            raise ValueError("published_at must not be after retrieved_at")
        if (
            self.acquisition_mode is AcquisitionMode.SYSTEMATIC_PUBLIC
            and self.source_access_class is not SourceAccessClass.PUBLIC
        ):
            raise ValueError("systematic_public acquisition requires public access")
        if (
            self.acquisition_mode is AcquisitionMode.COMMERCIAL
            and self.source_access_class is not SourceAccessClass.RESTRICTED
        ):
            raise ValueError("commercial acquisition requires restricted access")
        prospective = self.acquisition_mode is AcquisitionMode.PROSPECTIVE_VALIDATION
        if prospective != (self.corpus_purpose is CorpusPurpose.PROSPECTIVE_VALIDATION):
            raise ValueError(
                "prospective_validation acquisition and corpus purpose must match"
            )
        return self


class IndicatorRecord(StrictUTCModel):
    indicator_id: str
    indicator_type: IndicatorType
    normalized_value: str
    public_id: str
    first_ingested_at: datetime
    sensitivity: IndicatorSensitivity

    _first_ingested_at_utc = field_validator("first_ingested_at")(utc)


class SourceMentionRecord(StrictUTCModel):
    mention_id: str
    indicator_id: str
    document_id: str
    source_family_id: str
    scope: str
    raw_form_hash: str
    context_excerpt_hash: str
    observed_at: datetime
    available_at: datetime

    _observed_at_utc = field_validator("observed_at")(utc)
    _available_at_utc = field_validator("available_at")(utc)

    @model_validator(mode="after")
    def _validate_hashes_and_time(self) -> "SourceMentionRecord":
        for name in ("raw_form_hash", "context_excerpt_hash"):
            if not re.fullmatch(r"[0-9a-f]{64}", getattr(self, name)):
                raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
        if not self.scope.strip():
            raise ValueError("source mention scope is required")
        if self.observed_at > self.available_at:
            raise ValueError("source mention observed_at cannot exceed available_at")
        return self


class IndicatorAssertionRecord(StrictUTCModel):
    assertion_id: str
    indicator_id: str
    document_id: str
    source_mention_id: str
    campaign_id: str | None = None
    role: AssertionRole = AssertionRole.UNKNOWN
    verdict: AssertionVerdict = AssertionVerdict.CANDIDATE
    evidence_type: EvidenceType = EvidenceType.UNKNOWN
    vendor_first_seen: datetime | None = None
    vendor_last_seen: datetime | None = None
    first_public_at: datetime
    available_at: datetime
    source_confidence: float | None = Field(default=None, ge=0, le=1)
    extraction_confidence: float | None = Field(default=None, ge=0, le=1)
    role_confidence: float | None = Field(default=None, ge=0, le=1)
    context_excerpt_hash: str
    reviewer_status: ReviewerStatus = ReviewerStatus.PENDING

    _vendor_first_seen_utc = field_validator("vendor_first_seen")(
        lambda value: None if value is None else utc(value)
    )
    _vendor_last_seen_utc = field_validator("vendor_last_seen")(
        lambda value: None if value is None else utc(value)
    )
    _first_public_at_utc = field_validator("first_public_at")(utc)
    _available_at_utc = field_validator("available_at")(utc)


class AssertionReviewRecord(StrictUTCModel):
    review_id: str
    assertion_id: str
    decision: ReviewerStatus
    reviewer_id: str
    reviewed_at: datetime
    reviewed_role: AssertionRole
    source_confidence: float = Field(ge=0, le=1)
    extraction_confidence: float = Field(ge=0, le=1)
    role_confidence: float = Field(ge=0, le=1)
    notes_hash: str

    _reviewed_at_utc = field_validator("reviewed_at")(utc)

    @model_validator(mode="after")
    def _validate_review(self) -> "AssertionReviewRecord":
        if self.decision is ReviewerStatus.PENDING:
            raise ValueError("persisted assertion review must be accepted or rejected")
        if not self.reviewer_id.strip():
            raise ValueError("assertion reviewer_id is required")
        if not re.fullmatch(r"[0-9a-f]{64}", self.notes_hash):
            raise ValueError("notes_hash must be a lowercase SHA-256 hex digest")
        return self


class AcceptedPivotSource(StrictUTCModel):
    indicator_id: str
    assertion_id: str
    review_id: str
    scope: str
    value: str
    role: AssertionRole
    available_at: datetime
    source_confidence: float = Field(ge=0, le=1)
    extraction_confidence: float = Field(ge=0, le=1)
    role_confidence: float = Field(ge=0, le=1)

    _available_at_utc = field_validator("available_at")(utc)


class HostObservationRecord(StrictUTCModel):
    observation_id: str
    indicator_id: str
    observed_at: datetime | None = None
    collected_at: datetime
    observation_time_basis: ObservationTimeBasis
    source: str = "censys"
    host_observed: bool
    negative_reason: NegativeReason | None = None
    asn: int | None = None
    prefix: str | None = None
    country: str | None = None
    organization: str | None = None
    raw_record_hash: str
    query_run_id: str

    _observed_at_utc = field_validator("observed_at")(
        lambda value: None if value is None else utc(value)
    )
    _collected_at_utc = field_validator("collected_at")(utc)
class ServiceObservationRecord(StrictUTCModel):
    service_observation_id: str
    observation_id: str
    port: int = Field(ge=1, le=65535)
    transport: Transport
    protocol: str
    observed_at: datetime | None = None
    observation_time_basis: ObservationTimeBasis = ObservationTimeBasis.UNAVAILABLE
    extended_service: str | None = None
    banner_hash: str | None = None
    http_title_hash: str | None = None
    cert_sha256: str | None = None
    spki_sha256: str | None = None
    jarm: str | None = None
    ja4: str | None = None
    ssh_key_hash: str | None = None
    software_vendor: str | None = None
    software_product: str | None = None
    software_version: str | None = None
    extractor_version: str

    _observed_at_utc = field_validator("observed_at")(
        lambda value: None if value is None else utc(value)
    )


class Q0LandmarkRecord(StrictUTCModel):
    landmark_id: str
    indicator_id: str
    assertion_id: str
    query_id: str
    landmark_reason: str
    observation_window_start: datetime
    observation_window_end: datetime
    source_available_at: datetime
    registered_at: datetime

    _window_start_utc = field_validator("observation_window_start")(utc)
    _window_end_utc = field_validator("observation_window_end")(utc)
    _source_available_utc = field_validator("source_available_at")(utc)
    _landmark_registered_utc = field_validator("registered_at")(utc)

    @model_validator(mode="after")
    def _validate_landmark_window(self) -> "Q0LandmarkRecord":
        if self.observation_window_end < self.observation_window_start:
            raise ValueError("landmark observation window is reversed")
        if self.source_available_at > self.registered_at:
            raise ValueError("landmark predates source availability")
        return self


class Q0TimelineEntryRecord(StrictUTCModel):
    timeline_entry_id: str
    landmark_id: str
    observation_id: str
    observation_kind: TimelineObservationKind
    observed_at: datetime | None = None
    collected_at: datetime
    host_observed: bool
    negative_reason: NegativeReason | None = None
    fingerprint_ids: list[str] = Field(default_factory=list)
    raw_record_hash: str

    _timeline_observed_utc = field_validator("observed_at")(
        lambda value: None if value is None else utc(value)
    )
    _timeline_collected_utc = field_validator("collected_at")(utc)


class ContinuityAssessmentRecord(StrictUTCModel):
    assessment_id: str
    landmark_id: str
    status: ContinuityStatus
    assessed_at: datetime
    window_start: datetime
    window_end: datetime
    current_response: bool | None
    historical_positive_count: int = Field(ge=0)
    missing_scan_count: int = Field(ge=0)
    last_positive_at: datetime | None = None
    stable_fingerprint_ids: list[str] = Field(default_factory=list)
    conflicting_fingerprint_ids: list[str] = Field(default_factory=list)
    evidence_observation_ids: list[str] = Field(default_factory=list)
    derived_pivot_allowed: bool = False

    _continuity_assessed_utc = field_validator("assessed_at")(utc)
    _continuity_start_utc = field_validator("window_start")(utc)
    _continuity_end_utc = field_validator("window_end")(utc)
    _last_positive_utc = field_validator("last_positive_at")(
        lambda value: None if value is None else utc(value)
    )


class ContinuityReviewRecord(StrictUTCModel):
    review_id: str
    assessment_id: str
    decision: ReviewerStatus
    reviewer_id: str
    reviewed_at: datetime
    allow_probable: bool = False
    notes_hash: str

    _continuity_reviewed_utc = field_validator("reviewed_at")(utc)

    @model_validator(mode="after")
    def _validate_continuity_review(self) -> "ContinuityReviewRecord":
        if self.decision is ReviewerStatus.PENDING:
            raise ValueError("continuity review must be accepted or rejected")
        if not self.reviewer_id.strip() or not re.fullmatch(r"[0-9a-f]{64}", self.notes_hash):
            raise ValueError("continuity review provenance is invalid")
        return self


class PivotPrecheckRecord(StrictUTCModel):
    precheck_id: str
    query_id: str
    query_hash: str
    assertion_ids: list[str]
    node_id: str
    roles: list[AssertionRole]
    scope: str
    risk_flags: list[str] = Field(default_factory=list)
    cutoff_at: datetime
    source_available_at: datetime
    page_budget: int = Field(ge=1)
    registered_at: datetime

    _precheck_cutoff_utc = field_validator("cutoff_at")(utc)
    _precheck_source_utc = field_validator("source_available_at")(utc)
    _precheck_registered_utc = field_validator("registered_at")(utc)

    @model_validator(mode="after")
    def _validate_precheck_time(self) -> "PivotPrecheckRecord":
        if not self.assertion_ids or not self.node_id.strip() or not self.scope.strip():
            raise ValueError("precheck assertion, node, and scope are required")
        if self.source_available_at > self.cutoff_at:
            raise ValueError("precheck source is available after cutoff")
        if self.source_available_at > self.registered_at:
            raise ValueError("precheck predates source availability")
        return self


class PivotPrecheckResultRecord(StrictUTCModel):
    result_id: str
    precheck_id: str
    collection_run_id: str
    status: PrecheckStatus
    page_count: int = Field(ge=0)
    hit_count: int = Field(ge=0)
    hit_distribution: dict[str, int] = Field(default_factory=dict)
    raw_manifest_hash: str | None = None
    recorded_at: datetime
    failure_reason: str | None = None

    _precheck_recorded_utc = field_validator("recorded_at")(utc)


class CtiCompositeRecord(StrictUTCModel):
    composite_id: str
    node_id: str
    assertion_ids: list[str]
    roles: list[AssertionRole]
    window_start: datetime
    window_end: datetime
    available_at: datetime

    _composite_start_utc = field_validator("window_start")(utc)
    _composite_end_utc = field_validator("window_end")(utc)
    _composite_available_utc = field_validator("available_at")(utc)

    @model_validator(mode="after")
    def _validate_composite(self) -> "CtiCompositeRecord":
        if len(set(self.assertion_ids)) < 2:
            raise ValueError("CTI composite requires two distinct assertions")
        if self.window_end < self.window_start:
            raise ValueError("CTI composite window is reversed")
        if not self.window_start <= self.available_at <= self.window_end:
            raise ValueError("CTI composite availability is outside its window")
        return self


class PivotEligibilityReviewRecord(StrictUTCModel):
    review_id: str
    precheck_id: str
    decision: ReviewerStatus
    reviewer_id: str
    reviewed_at: datetime
    reason_code: str
    notes_hash: str

    _eligibility_reviewed_utc = field_validator("reviewed_at")(utc)

    @model_validator(mode="after")
    def _validate_eligibility_review(self) -> "PivotEligibilityReviewRecord":
        if self.decision is ReviewerStatus.PENDING:
            raise ValueError("eligibility review must be accepted or rejected")
        if not self.reason_code.strip() or not self.reviewer_id.strip():
            raise ValueError("eligibility review reason and reviewer are required")
        if not re.fullmatch(r"[0-9a-f]{64}", self.notes_hash):
            raise ValueError("eligibility notes_hash must be SHA-256")
        return self


class FingerprintRecord(StrictUTCModel):
    fingerprint_id: str
    fingerprint_type: FingerprintType
    fingerprint_value: str
    extractor_version: str
    first_available_at: datetime
    sensitivity: FingerprintSensitivity = FingerprintSensitivity.RESTRICTED

    _first_available_at_utc = field_validator("first_available_at")(utc)


class EntityRelationRecord(StrictUTCModel):
    relation_id: str
    src_id: str
    dst_id: str
    relation_type: EntityRelationType
    valid_from: datetime
    valid_to: datetime | None = None
    evidence_source: str
    confidence: float = Field(ge=0, le=1)
    available_at: datetime

    _valid_from_utc = field_validator("valid_from")(utc)
    _valid_to_utc = field_validator("valid_to")(
        lambda value: None if value is None else utc(value)
    )
    _available_at_utc = field_validator("available_at")(utc)


class QueryRecord(StrictUTCModel):
    query_id: str
    query_version: str
    query_class: QueryClass
    query_text: str
    query_hash: str
    source_indicator_ids: list[str] = Field(default_factory=list)
    source_assertion_ids: list[str] = Field(default_factory=list)
    source_available_at: datetime | None = None
    source_feature_ids: list[str] = Field(default_factory=list)
    source_precheck_ids: list[str] = Field(default_factory=list)
    developed_from_split: DatasetSplit
    registered_at: datetime
    frozen_at: datetime | None = None
    valid_for_test_from: datetime | None = None
    config_hash: str
    status: QueryStatus = QueryStatus.DRAFT

    _registered_at_utc = field_validator("registered_at")(utc)
    _source_available_at_utc = field_validator("source_available_at")(
        lambda v: None if v is None else utc(v)
    )
    _frozen_at_utc = field_validator("frozen_at")(lambda v: None if v is None else utc(v))
    _valid_from_utc = field_validator("valid_for_test_from")(lambda v: None if v is None else utc(v))

    @model_validator(mode="after")
    def _validate_source_provenance_time(self) -> "QueryRecord":
        if (
            self.source_assertion_ids
            and not self.source_indicator_ids
            and self.query_class is not QueryClass.Q2_DERIVED
        ):
            raise ValueError("source assertions require source indicators")
        if self.source_available_at and self.source_available_at > self.registered_at:
            raise ValueError("query cannot predate source availability")
        if self.source_precheck_ids and self.query_class is not QueryClass.Q2_DERIVED:
            raise ValueError("source prechecks are valid only for Q2 queries")
        return self


class QueryExecutionRecord(StrictUTCModel):
    query_run_id: str
    query_id: str
    query_hash: str
    cutoff_time: datetime
    executed_at: datetime
    dataset_split: DatasetSplit
    result_count: int = Field(ge=0)
    result_manifest_hash: str
    api_schema_version: str
    credits_or_bytes: float | None = Field(default=None, ge=0)
    status: str
    failure_reason: str | None = None

    _cutoff_utc = field_validator("cutoff_time")(utc)
    _executed_utc = field_validator("executed_at")(utc)


class ReuseSourceRecord(StrictUTCModel):
    name: str
    path: str
    commit: str
    dirty_at_audit: bool
    files: dict[str, str]


class RunManifest(StrictUTCModel):
    run_id: str
    rq: str
    started_at: datetime
    completed_at: datetime | None = None
    code_version: str
    config_hash: str
    input_manifest_hash: str
    random_seed: int
    status: str
    output_manifest_hash: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    _started_at_utc = field_validator("started_at")(utc)
    _completed_at_utc = field_validator("completed_at")(lambda v: None if v is None else utc(v))
