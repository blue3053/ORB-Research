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
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AcquisitionMode(StrEnum):
    EXISTING_CURATED = "existing_curated"
    SYSTEMATIC_PUBLIC = "systematic_public"
    COMMERCIAL = "commercial"
    PROSPECTIVE_VALIDATION = "prospective_validation"


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
    EXIT = "exit"
    MIDDLE = "middle"
    CONTROLLER = "controller"
    STAGING = "staging"
    C2 = "c2"
    SCANNER = "scanner"
    VICTIM = "victim"
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
    target_date_from: str
    target_date_to: str
    target_publishers: list[str]
    search_terms: list[str]
    inclusion_rules: list[str]
    exclusion_rules: list[str]
    deduplication_rule: str
    registered_at: datetime
    protocol_hash: str

    _registered_at_utc = field_validator("registered_at")(utc)


class SourceDocumentRecord(StrictUTCModel):
    document_id: str
    canonical_url: str
    publisher: str
    title: str
    published_at: datetime
    retrieved_at: datetime
    content_sha256: str
    acquisition_mode: AcquisitionMode
    search_protocol_id: str | None = None
    discovery_query_id: str | None = None
    source_independence: str = "unknown"

    _published_at_utc = field_validator("published_at")(utc)
    _retrieved_at_utc = field_validator("retrieved_at")(utc)


class IndicatorRecord(StrictUTCModel):
    indicator_id: str
    indicator_type: IndicatorType
    normalized_value: str
    public_id: str
    first_ingested_at: datetime
    sensitivity: IndicatorSensitivity

    _first_ingested_at_utc = field_validator("first_ingested_at")(utc)


class IndicatorAssertionRecord(StrictUTCModel):
    assertion_id: str
    indicator_id: str
    document_id: str
    campaign_id: str | None = None
    role: AssertionRole = AssertionRole.UNKNOWN
    verdict: AssertionVerdict = AssertionVerdict.CANDIDATE
    evidence_type: EvidenceType = EvidenceType.UNKNOWN
    vendor_first_seen: datetime | None = None
    vendor_last_seen: datetime | None = None
    first_public_at: datetime
    confidence: float | None = Field(default=None, ge=0, le=1)
    context_excerpt_hash: str
    reviewer_status: ReviewerStatus = ReviewerStatus.PENDING

    _vendor_first_seen_utc = field_validator("vendor_first_seen")(
        lambda value: None if value is None else utc(value)
    )
    _vendor_last_seen_utc = field_validator("vendor_last_seen")(
        lambda value: None if value is None else utc(value)
    )
    _first_public_at_utc = field_validator("first_public_at")(utc)


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
    source_feature_ids: list[str] = Field(default_factory=list)
    developed_from_split: DatasetSplit
    registered_at: datetime
    frozen_at: datetime | None = None
    valid_for_test_from: datetime | None = None
    config_hash: str
    status: QueryStatus = QueryStatus.DRAFT

    _registered_at_utc = field_validator("registered_at")(utc)
    _frozen_at_utc = field_validator("frozen_at")(lambda v: None if v is None else utc(v))
    _valid_from_utc = field_validator("valid_for_test_from")(lambda v: None if v is None else utc(v))


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
