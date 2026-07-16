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


class FeatureFamily(StrEnum):
    IDENTITY = "identity"
    TLS = "tls"
    HTTP = "http"
    SERVICE = "service"
    DEVICE = "device"
    NETWORK = "network"
    RELATION = "relation"


class FeatureStability(StrEnum):
    STABLE = "stable"
    UNSTABLE = "unstable"
    UNAVAILABLE = "unavailable"


class FeatureEligibilityStatus(StrEnum):
    CANDIDATE = "candidate"
    BLOCKED = "blocked"


class QueryCompositionType(StrEnum):
    CTI_ONLY = "cti_only"
    CTI_DERIVED = "cti_derived"
    DERIVED_ONLY = "derived_only"
    Q3_GRAPH_EXPANSION = "q3_graph_expansion"


class ClauseOrigin(StrEnum):
    CTI_DIRECT = "cti_direct"
    DERIVED = "derived"


class LogicalRole(StrEnum):
    REQUIRED = "required"
    ALTERNATIVE = "alternative"
    EXCLUSION = "exclusion"
    SCORE_ONLY = "score_only"


class EvidenceRole(StrEnum):
    DISCOVERY = "discovery"
    VALIDATION = "validation"
    CONTRADICTION = "contradiction"


class OpportunityStatus(StrEnum):
    DUE = "due"
    MISSED = "missed"
    LATE = "late"
    PARTIAL = "partial"
    FAILED = "failed"
    COMPLETE = "complete"


class ProspectiveTimeStatus(StrEnum):
    ELIGIBLE = "eligible"
    PRE_FREEZE = "pre_freeze"
    UNRESOLVED = "prospective_time_unresolved"


class AdjudicationStatus(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    CONTRADICTED = "contradicted"
    UNRESOLVED = "unresolved"
    UNOBSERVABLE = "unobservable"


class ValidationAvailability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    NOT_CHECKED = "not_checked"


class QueryOperator(StrEnum):
    AND = "AND"
    OR = "OR"
    NOT = "NOT"


class CooccurrenceScope(StrEnum):
    HOST = "host"
    SERVICE = "service"
    CERTIFICATE = "certificate"
    NAME = "name"
    GRAPH_EDGE = "graph_edge"


class DesignPrecheckStatus(StrEnum):
    PENDING = "pending"
    COMPLETE = "complete"
    PARTIAL_MAX_PAGES = "partial_max_pages"
    FAILED = "failed"


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


class FeatureCatalogRecord(StrictUTCModel):
    feature_id: str
    feature_family: FeatureFamily
    feature_type: str
    canonical_value: str
    canonical_value_hash: str
    query_field: str | None = None
    canonicalizer_version: str
    extractor_version: str
    first_available_at: datetime
    stability: FeatureStability
    shared_or_default: bool = False
    source_fingerprint_id: str | None = None

    _feature_available_utc = field_validator("first_available_at")(utc)

    @model_validator(mode="after")
    def _validate_feature(self) -> "FeatureCatalogRecord":
        if not self.feature_type.strip() or not self.canonical_value.strip():
            raise ValueError("feature type and canonical value are required")
        if not re.fullmatch(r"[0-9a-f]{64}", self.canonical_value_hash):
            raise ValueError("canonical_value_hash must be SHA-256")
        if self.stability is FeatureStability.STABLE and not self.query_field:
            raise ValueError("stable feature requires a query field")
        return self


class FeatureObservationRecord(StrictUTCModel):
    feature_observation_id: str
    feature_id: str
    observation_id: str
    service_observation_id: str | None = None
    query_run_id: str
    observed_at: datetime | None = None
    available_at: datetime

    _feature_observed_utc = field_validator("observed_at")(
        lambda value: None if value is None else utc(value)
    )
    _feature_observation_available_utc = field_validator("available_at")(utc)

    @model_validator(mode="after")
    def _validate_feature_observation_time(self) -> "FeatureObservationRecord":
        if self.observed_at and self.observed_at > self.available_at:
            raise ValueError("feature observation cannot be available before observation")
        return self


class EntityEpochRecord(StrictUTCModel):
    entity_epoch_id: str
    indicator_id: str
    valid_from: datetime
    valid_to: datetime | None = None
    observation_ids: list[str]
    identity_feature_ids: list[str]
    resolution_version: str
    available_at: datetime

    _epoch_from_utc = field_validator("valid_from")(utc)
    _epoch_to_utc = field_validator("valid_to")(
        lambda value: None if value is None else utc(value)
    )
    _epoch_available_utc = field_validator("available_at")(utc)

    @model_validator(mode="after")
    def _validate_epoch(self) -> "EntityEpochRecord":
        if not self.observation_ids:
            raise ValueError("entity epoch requires observations")
        if self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("entity epoch interval is reversed")
        if self.available_at < self.valid_from:
            raise ValueError("entity epoch availability predates validity")
        return self


class ReferenceSetRecord(StrictUTCModel):
    reference_set_id: str
    reference_version: str
    cutoff_at: datetime
    stratum: dict[str, Any]
    sampling_frame: str
    snapshot_manifest_hash: str
    source_query_run_ids: list[str]
    registered_at: datetime
    claim_scope: str = "matched_background"

    _reference_cutoff_utc = field_validator("cutoff_at")(utc)
    _reference_registered_utc = field_validator("registered_at")(utc)

    @model_validator(mode="after")
    def _validate_reference_set(self) -> "ReferenceSetRecord":
        if not self.stratum or not self.sampling_frame.strip():
            raise ValueError("reference stratum and sampling frame are required")
        if not self.source_query_run_ids:
            raise ValueError("reference set requires source query runs")
        unknown = set(self.stratum) - {"protocol", "ports", "product", "time_window"}
        if unknown:
            raise ValueError("reference set contains unsupported strata")
        if not re.fullmatch(r"[0-9a-f]{64}", self.snapshot_manifest_hash):
            raise ValueError("reference snapshot manifest hash must be SHA-256")
        if self.claim_scope == "global_rarity":
            raise ValueError("matched reference set cannot claim global rarity")
        if self.registered_at < self.cutoff_at:
            raise ValueError("reference set cannot be registered before cutoff")
        window = self.stratum.get("time_window")
        if window is not None:
            if not isinstance(window, dict) or set(window) != {"start", "end"}:
                raise ValueError("reference time_window requires start and end")
            start = datetime.fromisoformat(str(window["start"]).replace("Z", "+00:00"))
            end = datetime.fromisoformat(str(window["end"]).replace("Z", "+00:00"))
            if start.tzinfo is None or end.tzinfo is None or end < start:
                raise ValueError("reference time_window is invalid")
        ports = self.stratum.get("ports", [])
        if (not isinstance(ports, list)
                or any(not isinstance(port, int) or not 1 <= port <= 65535 for port in ports)):
            raise ValueError("reference ports stratum is invalid")
        return self


class ReferenceMembershipRecord(StrictUTCModel):
    membership_id: str
    reference_set_id: str
    observation_id: str
    observable: bool
    exclusion_reason: str | None = None
    matched_at: datetime

    _membership_matched_utc = field_validator("matched_at")(utc)

    @model_validator(mode="after")
    def _validate_membership(self) -> "ReferenceMembershipRecord":
        if self.observable == bool(self.exclusion_reason):
            raise ValueError("observable membership and exclusion reason disagree")
        return self


class FeatureStatSnapshotRecord(StrictUTCModel):
    stat_snapshot_id: str
    feature_id: str
    reference_set_id: str
    cutoff_at: datetime
    anchor_observation_ids: list[str]
    anchor_source_ids: list[str]
    background_membership_ids: list[str]
    anchor_numerator: int = Field(ge=0)
    anchor_denominator: int = Field(ge=0)
    background_numerator: int = Field(ge=0)
    background_denominator: int = Field(ge=0)
    anchor_support: float = Field(ge=0, le=1)
    background_prevalence: float = Field(ge=0, le=1)
    reference_lift: float = Field(ge=0)
    anchor_ci_low: float = Field(ge=0, le=1)
    anchor_ci_high: float = Field(ge=0, le=1)
    background_ci_low: float = Field(ge=0, le=1)
    background_ci_high: float = Field(ge=0, le=1)
    source_manifest_hash: str
    computed_at: datetime

    _stat_cutoff_utc = field_validator("cutoff_at")(utc)
    _stat_computed_utc = field_validator("computed_at")(utc)

    @model_validator(mode="after")
    def _validate_stat_counts(self) -> "FeatureStatSnapshotRecord":
        if self.anchor_numerator > self.anchor_denominator:
            raise ValueError("anchor numerator exceeds denominator")
        if self.background_numerator > self.background_denominator:
            raise ValueError("background numerator exceeds denominator")
        if not re.fullmatch(r"[0-9a-f]{64}", self.source_manifest_hash):
            raise ValueError("feature statistic source manifest must be SHA-256")
        if self.background_ci_low > self.background_ci_high:
            raise ValueError("background confidence interval is reversed")
        if self.anchor_ci_low > self.anchor_ci_high:
            raise ValueError("anchor confidence interval is reversed")
        if self.computed_at < self.cutoff_at:
            raise ValueError("feature statistic cannot be computed before cutoff")
        return self


class FeatureEligibilityAssessmentRecord(StrictUTCModel):
    assessment_id: str
    feature_id: str
    stat_snapshot_id: str
    status: FeatureEligibilityStatus
    reason_codes: list[str]
    min_distinct_anchors: int = Field(default=2, ge=1)
    min_anchor_support: float = Field(default=0.5, ge=0, le=1)
    max_background_prevalence: float = Field(default=0.1, ge=0, le=1)
    assessed_at: datetime

    _feature_assessed_utc = field_validator("assessed_at")(utc)

    @model_validator(mode="after")
    def _validate_eligibility_status(self) -> "FeatureEligibilityAssessmentRecord":
        if (self.status is FeatureEligibilityStatus.CANDIDATE) == bool(self.reason_codes):
            raise ValueError("feature eligibility status and reasons disagree")
        return self


class FeatureEligibilityReviewRecord(StrictUTCModel):
    review_id: str
    assessment_id: str
    decision: ReviewerStatus
    reviewer_id: str
    reviewed_at: datetime
    notes_hash: str

    _feature_reviewed_utc = field_validator("reviewed_at")(utc)

    @model_validator(mode="after")
    def _validate_feature_review(self) -> "FeatureEligibilityReviewRecord":
        if self.decision is ReviewerStatus.PENDING:
            raise ValueError("feature eligibility review must be accepted or rejected")
        if not self.reviewer_id.strip() or not re.fullmatch(r"[0-9a-f]{64}", self.notes_hash):
            raise ValueError("feature eligibility review provenance is invalid")
        return self


class QueryClauseRecord(StrictUTCModel):
    clause_id: str
    parent_clause_id: str | None = None
    feature_origin: ClauseOrigin
    logical_role: LogicalRole
    evidence_role: EvidenceRole = EvidenceRole.DISCOVERY
    operator: QueryOperator
    cooccurrence_scope: CooccurrenceScope
    query_field: str
    canonical_value: str
    source_assertion_id: str | None = None
    source_precheck_id: str | None = None
    source_feature_id: str | None = None
    node_id: str | None = None
    canonicalizer_version: str
    available_at: datetime

    _clause_available_utc = field_validator("available_at")(utc)

    @model_validator(mode="after")
    def _validate_clause_source(self) -> "QueryClauseRecord":
        if not self.query_field.strip() or not self.canonical_value.strip():
            raise ValueError("query clause field and value are required")
        if self.evidence_role is not EvidenceRole.DISCOVERY:
            raise ValueError("query clauses can use discovery evidence only")
        cti_sources = bool(self.source_assertion_id or self.source_precheck_id)
        derived_source = bool(self.source_feature_id)
        if self.feature_origin is ClauseOrigin.CTI_DIRECT:
            if not cti_sources or derived_source:
                raise ValueError("CTI clause provenance is invalid")
        elif not derived_source or cti_sources:
            raise ValueError("derived clause provenance is invalid")
        expected_operator = {
            LogicalRole.REQUIRED: QueryOperator.AND,
            LogicalRole.ALTERNATIVE: QueryOperator.OR,
            LogicalRole.EXCLUSION: QueryOperator.NOT,
        }.get(self.logical_role)
        if expected_operator is None or self.operator is not expected_operator:
            raise ValueError("query clause logical role and operator disagree")
        if self.parent_clause_id is not None:
            raise ValueError("nested query clauses are not supported in Stage 5")
        return self


class QueryDesignRecord(StrictUTCModel):
    design_id: str
    query_id: str
    query_version: str
    variant: str
    query_class: QueryClass
    composition_type: QueryCompositionType
    clause_ids: list[str]
    rendered_query: str
    query_hash: str
    cutoff_at: datetime
    background_snapshot_ids: list[str]
    api_schema_version: str
    parser_version: str
    normalizer_version: str
    entity_resolution_version: str
    config_hash: str
    registered_at: datetime

    _design_cutoff_utc = field_validator("cutoff_at")(utc)
    _design_registered_utc = field_validator("registered_at")(utc)

    @model_validator(mode="after")
    def _validate_design(self) -> "QueryDesignRecord":
        if not self.variant.strip() or not self.clause_ids:
            raise ValueError("query design variant and clauses are required")
        if self.registered_at < self.cutoff_at:
            raise ValueError("query design cannot be registered before cutoff")
        if not re.fullmatch(r"[0-9a-f]{64}", self.query_hash):
            raise ValueError("query design hash must be SHA-256")
        return self


class QueryBudgetScheduleRecord(StrictUTCModel):
    schedule_id: str
    design_id: str
    interval_hours: int = Field(ge=1)
    starts_at: datetime
    max_alerts_per_run: int = Field(ge=1)
    max_credits_per_run: float = Field(gt=0)
    max_pages_per_run: int = Field(ge=1)
    tie_break_rule: str
    registered_at: datetime

    _schedule_start_utc = field_validator("starts_at")(utc)
    _schedule_registered_utc = field_validator("registered_at")(utc)

    @model_validator(mode="after")
    def _validate_schedule(self) -> "QueryBudgetScheduleRecord":
        if not self.tie_break_rule.strip():
            raise ValueError("query schedule tie-break rule is required")
        if self.starts_at < self.registered_at:
            raise ValueError("query schedule cannot start before registration")
        return self


class QueryDesignPrecheckRecord(StrictUTCModel):
    precheck_id: str
    design_id: str
    status: DesignPrecheckStatus
    page_count: int = Field(default=0, ge=0)
    hit_count: int = Field(default=0, ge=0)
    next_token_present: bool = False
    syntax_valid: bool = False
    broad_or_shared: bool = False
    cost_exceeded: bool = False
    performance_claim_allowed: bool = False
    raw_manifest_hash: str | None = None
    recorded_at: datetime
    failure_reason: str | None = None

    _design_precheck_recorded_utc = field_validator("recorded_at")(utc)

    @model_validator(mode="after")
    def _validate_design_precheck(self) -> "QueryDesignPrecheckRecord":
        if self.performance_claim_allowed:
            raise ValueError("development precheck cannot support performance claims")
        if self.status in {DesignPrecheckStatus.COMPLETE, DesignPrecheckStatus.PARTIAL_MAX_PAGES}:
            if not self.raw_manifest_hash:
                raise ValueError("complete or partial precheck requires raw manifest hash")
        if self.status is DesignPrecheckStatus.FAILED and not self.failure_reason:
            raise ValueError("failed design precheck requires failure reason")
        if self.raw_manifest_hash and not re.fullmatch(r"[0-9a-f]{64}", self.raw_manifest_hash):
            raise ValueError("design precheck raw manifest must be SHA-256")
        return self


class QueryDesignReviewRecord(StrictUTCModel):
    review_id: str
    design_id: str
    precheck_id: str
    decision: ReviewerStatus
    reviewer_id: str
    reviewed_at: datetime
    notes_hash: str

    _design_reviewed_utc = field_validator("reviewed_at")(utc)

    @model_validator(mode="after")
    def _validate_design_review(self) -> "QueryDesignReviewRecord":
        if self.decision is ReviewerStatus.PENDING:
            raise ValueError("query design review must be accepted or rejected")
        if not self.reviewer_id.strip() or not re.fullmatch(r"[0-9a-f]{64}", self.notes_hash):
            raise ValueError("query design review provenance is invalid")
        return self


class QueryFreezeManifestRecord(StrictUTCModel):
    freeze_manifest_id: str
    query_id: str
    design_id: str
    query_hash: str
    source_manifest_hash: str
    query_cutoff_at: datetime
    schedule_id: str
    review_id: str
    frozen_at: datetime
    valid_for_test_from: datetime

    _manifest_cutoff_utc = field_validator("query_cutoff_at")(utc)
    _manifest_frozen_utc = field_validator("frozen_at")(utc)
    _manifest_valid_utc = field_validator("valid_for_test_from")(utc)

    @model_validator(mode="after")
    def _validate_freeze_manifest(self) -> "QueryFreezeManifestRecord":
        if self.valid_for_test_from < self.frozen_at:
            raise ValueError("freeze manifest valid-from predates freeze")
        for name in ("query_hash", "source_manifest_hash"):
            if not re.fullmatch(r"[0-9a-f]{64}", getattr(self, name)):
                raise ValueError(f"{name} must be SHA-256")
        return self


class QueryRecord(StrictUTCModel):
    query_id: str
    query_version: str
    query_variant: str = "primary"
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
        if not self.query_variant.strip():
            raise ValueError("query variant is required")
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


class ObservationOpportunityRecord(StrictUTCModel):
    opportunity_id: str
    query_id: str
    query_version: str
    query_hash: str
    schedule_id: str
    entity_epoch_id: str
    due_at: datetime
    window_end: datetime
    status: OpportunityStatus
    query_run_id: str | None = None
    recorded_at: datetime
    reason: str | None = None

    _opportunity_due_utc = field_validator("due_at")(utc)
    _opportunity_end_utc = field_validator("window_end")(utc)
    _opportunity_recorded_utc = field_validator("recorded_at")(utc)

    @model_validator(mode="after")
    def _validate_opportunity(self) -> "ObservationOpportunityRecord":
        if self.window_end <= self.due_at:
            raise ValueError("observation opportunity window must be positive")
        if self.status in {OpportunityStatus.PARTIAL, OpportunityStatus.FAILED,
                           OpportunityStatus.COMPLETE, OpportunityStatus.LATE}:
            if not self.query_run_id:
                raise ValueError("executed opportunity status requires query_run_id")
        if self.status in {OpportunityStatus.MISSED, OpportunityStatus.FAILED} and not self.reason:
            raise ValueError("missed or failed opportunity requires reason")
        return self


class ProspectiveObservationEventRecord(StrictUTCModel):
    event_id: str
    opportunity_id: str
    query_run_id: str
    observation_id: str
    entity_epoch_id: str
    indicator_id: str
    observed_at: datetime | None = None
    collected_at: datetime
    time_status: ProspectiveTimeStatus
    record_time_statuses: dict[str, ProspectiveTimeStatus] = Field(default_factory=dict)
    raw_record_hash: str
    recorded_at: datetime

    _prospective_observed_utc = field_validator("observed_at")(
        lambda value: None if value is None else utc(value)
    )
    _prospective_collected_utc = field_validator("collected_at")(utc)
    _prospective_recorded_utc = field_validator("recorded_at")(utc)

    @model_validator(mode="after")
    def _validate_prospective_time(self) -> "ProspectiveObservationEventRecord":
        if self.time_status is ProspectiveTimeStatus.UNRESOLVED and self.observed_at is not None:
            raise ValueError("unresolved prospective time must not carry observed_at")
        if self.time_status is not ProspectiveTimeStatus.UNRESOLVED and self.observed_at is None:
            raise ValueError("resolved prospective time requires observed_at")
        return self


class CandidateRecord(StrictUTCModel):
    candidate_id: str
    query_id: str
    query_version: str
    query_hash: str
    entity_epoch_id: str
    indicator_id: str
    first_candidate_at: datetime
    first_observation_event_id: str
    discovery_feature_ids: list[str] = Field(default_factory=list)
    discovery_source_family_ids: list[str] = Field(default_factory=list)

    _first_candidate_utc = field_validator("first_candidate_at")(utc)


class CandidateEvidenceRecord(StrictUTCModel):
    evidence_id: str
    candidate_id: str
    role: EvidenceRole
    evidence_type: str
    source_id: str
    source_family_id: str
    feature_ids: list[str] = Field(default_factory=list)
    observed_at: datetime | None = None
    available_at: datetime
    availability: ValidationAvailability = ValidationAvailability.AVAILABLE
    supports_candidate: bool | None = None
    recorded_at: datetime

    _evidence_observed_utc = field_validator("observed_at")(
        lambda value: None if value is None else utc(value)
    )
    _candidate_evidence_available_utc = field_validator("available_at")(utc)
    _candidate_evidence_recorded_utc = field_validator("recorded_at")(utc)

    @model_validator(mode="after")
    def _validate_candidate_evidence(self) -> "CandidateEvidenceRecord":
        if not self.source_family_id.strip() or not self.source_id.strip():
            raise ValueError("candidate evidence source provenance is required")
        if self.observed_at and self.observed_at > self.available_at:
            raise ValueError("candidate evidence observed_at cannot exceed available_at")
        if self.role is EvidenceRole.DISCOVERY and self.supports_candidate is not True:
            raise ValueError("discovery evidence must support the candidate")
        if self.role is EvidenceRole.CONTRADICTION and self.supports_candidate is not False:
            raise ValueError("contradiction evidence must oppose the candidate")
        return self


class CandidateAdjudicationRecord(StrictUTCModel):
    adjudication_id: str
    candidate_id: str
    status: AdjudicationStatus
    reason_codes: list[str]
    evidence_ids: list[str] = Field(default_factory=list)
    adjudicator_id: str
    implementation_agent_id: str
    adjudicated_at: datetime

    _adjudicated_utc = field_validator("adjudicated_at")(utc)

    @model_validator(mode="after")
    def _validate_adjudication(self) -> "CandidateAdjudicationRecord":
        if not self.reason_codes:
            raise ValueError("candidate adjudication requires reason codes")
        if not self.adjudicator_id.strip() or self.adjudicator_id == self.implementation_agent_id:
            raise ValueError("human adjudicator must be separate from implementation agent")
        return self


class CandidateGradeEventRecord(StrictUTCModel):
    grade_event_id: str
    candidate_id: str
    adjudication_id: str
    grade: str
    previous_grade_event_id: str | None = None
    graded_at: datetime

    _graded_utc = field_validator("graded_at")(utc)


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
