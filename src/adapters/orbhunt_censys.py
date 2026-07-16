"""ORB_Hunt_v5 Censys query·parser 재사용 adapter.

목적: 내부로 복사·검토한 query safety와 Censys 결과 parser 파생본을 재사용한다.
지원 RQ: RQ1 Q0 관측, RQ2 반복관측, RQ3 direct pivot, RQ4 Q2/Q3, RQ5 baseline.
재사용 원천: ORB_Hunt_v5 censys_query.py·pivot_safety.py·censys_collect.py의 pure logic.
설계: runtime은 reused/orbhunt_v5만 import하고 외부 repository는 원본 hash 검증에만 사용한다.
입력·출력: pivot·template config 또는 cached Censys response를 받아 query·정규화 record를 반환한다.
시간·provenance 통제: query 동결과 cutoff는 이 adapter가 아니라 query_registry가 강제한다.
보안·라이선스: live search를 호출하지 않으며 기존 ALLOW_LIVE_CENSYS gate를 우회하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import ipaddress
from pathlib import Path
from typing import Any

from src.models import (
    EntityRelationRecord,
    EntityRelationType,
    FingerprintRecord,
    FingerprintSensitivity,
    FingerprintType,
    HostObservationRecord,
    IndicatorRecord,
    IndicatorSensitivity,
    IndicatorType,
    NegativeReason,
    ObservationTimeBasis,
    QueryClass,
    QueryExecutionRecord,
    QueryRecord,
    ServiceObservationRecord,
    Transport,
)
from src.provenance import canonical_json_hash, sha256_file, sha256_text
from src.reused.orbhunt_v5 import censys_query, result_parser


@dataclass(frozen=True)
class NormalizedCensysBatch:
    host_observations: tuple[HostObservationRecord, ...]
    service_observations: tuple[ServiceObservationRecord, ...]
    discovered_indicators: tuple[IndicatorRecord, ...]
    raw_page_hashes: tuple[str, ...]


@dataclass(frozen=True)
class FingerprintGraphBatch:
    fingerprints: tuple[FingerprintRecord, ...]
    relations: tuple[EntityRelationRecord, ...]


def _host_payload(hit: dict[str, Any]) -> dict[str, Any]:
    if isinstance(hit.get("host"), dict):
        return hit["host"]
    host_v1 = hit.get("host_v1")
    if isinstance(host_v1, dict) and isinstance(host_v1.get("resource"), dict):
        return host_v1["resource"]
    return hit


def _nested(value: dict[str, Any], path: str):
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _optional_utc(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Censys observation timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def _normalized_text_hash(value: Any) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split()).strip().lower()
    return sha256_text(normalized) if normalized else None


class OrbhuntCensysAdapter:
    """ORB_Hunt_v5의 pure query renderer와 parser를 연결한다."""

    def __init__(self, repository: Path | None = None, expected_hashes: dict[str, str] | None = None):
        self.repository = repository.resolve() if repository else None
        self.expected_hashes = expected_hashes or {}

    def verify_reuse_files(self) -> None:
        if self.repository is None:
            raise ValueError("external ORB_Hunt_v5 repository is required only for provenance verification")
        for relative, expected in self.expected_hashes.items():
            actual = sha256_file(self.repository / relative)
            if actual.lower() != expected.lower():
                raise ValueError(f"reuse source hash mismatch: {relative}")

    def render_q1_direct_pivot(
        self, pivot_type: str, pivot_value: str, template_config: dict[str, Any]
    ) -> str:
        """기존 fail-closed template renderer로 Q1 query를 만든다."""

        return str(censys_query.build_query_for_pivot(pivot_type, pivot_value, template_config))

    def parse_cached_results(
        self,
        raw_results: list[dict[str, Any]],
        cluster_id: str,
        pivot_id: str,
        pivot_type: str,
        pivot_value: str,
        host_observations: dict[str, list[dict[str, Any]]] | None = None,
    ) -> list[dict[str, Any]]:
        """기존 parser를 사용하되 network 호출 없이 cached payload만 정규화한다."""

        return result_parser.parse_censys_results(
            raw_results=raw_results,
            orb_cluster_id=cluster_id,
            pivot_id=pivot_id,
            pivot_type=pivot_type,
            pivot_value=pivot_value,
            host_obs_map=host_observations,
        )

    def normalize_cached_pages(
        self,
        *,
        page_records: list[dict[str, Any]],
        query: QueryRecord,
        execution: QueryExecutionRecord,
        collected_at: datetime,
        extractor_version: str,
        public_id_hmac_key: bytes | None = None,
        known_ip_indicator_ids: dict[str, str] | None = None,
    ) -> NormalizedCensysBatch:
        """불변 raw page를 host/service observation과 신규 restricted IP로 변환한다."""

        collected = collected_at.astimezone(timezone.utc)
        hosts: list[HostObservationRecord] = []
        services: list[ServiceObservationRecord] = []
        discovered: dict[str, IndicatorRecord] = {}
        raw_page_hashes: list[str] = []
        hit_count = 0
        for page in page_records:
            if page.get("query_hash") != query.query_hash:
                raise ValueError("raw page query_hash does not match registry query")
            raw_page_hashes.append(canonical_json_hash(page))
            payload = page.get("response", {})
            result = payload.get("result", payload) if isinstance(payload, dict) else {}
            hits = result.get("hits", []) if isinstance(result, dict) else []
            if not isinstance(hits, list):
                raise ValueError("raw Censys page hits must be a list")
            for hit in hits:
                if not isinstance(hit, dict):
                    continue
                host = _host_payload(hit)
                ip = str(host.get("ip", ""))
                try:
                    ipaddress.ip_address(ip)
                except ValueError:
                    continue
                hit_count += 1
                raw_hash = canonical_json_hash(hit)
                observed = _optional_utc(
                    host.get("last_updated_at")
                    or host.get("last_seen_at")
                    or hit.get("last_updated_at")
                )
                if observed and observed > collected:
                    raise ValueError("Censys observed_at cannot be later than collected_at")
                time_basis = (
                    ObservationTimeBasis.CENSYS_HOST_UPDATED_AT
                    if observed else ObservationTimeBasis.UNAVAILABLE
                )
                if query.query_class is QueryClass.Q0_SEED and query.source_indicator_ids:
                    try:
                        expected_ip = str(ipaddress.ip_address(query.query_text.split("=", 1)[1].strip()))
                    except (IndexError, ValueError) as error:
                        raise ValueError("Q0 query is not an exact host.ip expression") from error
                    if ip != expected_ip:
                        raise ValueError("Q0 response IP does not match exact seed query")
                    indicator_id = query.source_indicator_ids[0]
                else:
                    indicator_id = (known_ip_indicator_ids or {}).get(ip)
                    if indicator_id is not None:
                        pass
                    else:
                        indicator_id = f"ioc-{sha256_text('censys|ip|' + ip)[:20]}"
                        if public_id_hmac_key is None or len(public_id_hmac_key) < 32:
                            raise ValueError(
                                "ORB_PUBLIC_ID_HMAC_KEY of at least 32 bytes is required for discovered IPs"
                            )
                        digest = hmac.new(
                            public_id_hmac_key, f"ip|{ip}".encode("utf-8"), hashlib.sha256
                        ).hexdigest()
                        discovered[indicator_id] = IndicatorRecord(
                            indicator_id=indicator_id,
                            indicator_type=(IndicatorType.IPV4
                                            if ipaddress.ip_address(ip).version == 4
                                            else IndicatorType.OTHER),
                            normalized_value=ip,
                            public_id=f"pub-{digest[:24]}",
                            first_ingested_at=collected,
                            sensitivity=IndicatorSensitivity.RESTRICTED,
                        )
                observation_id = f"obs-{sha256_text(execution.query_run_id + '|' + ip)[:20]}"
                flattened = self.parse_cached_results(
                    [hit], execution.query_run_id, query.query_id,
                    query.query_class.value, query.query_hash,
                )
                first = flattened[0] if flattened else {}
                raw_services = host.get("services") if isinstance(host.get("services"), list) else []
                hosts.append(HostObservationRecord(
                    observation_id=observation_id,
                    indicator_id=indicator_id,
                    observed_at=observed,
                    collected_at=collected,
                    observation_time_basis=time_basis,
                    host_observed=True,
                    asn=first.get("asn"),
                    prefix=_nested(host, "autonomous_system.bgp_prefix") or host.get("network_prefix"),
                    country=first.get("country"),
                    organization=first.get("asn_name"),
                    raw_record_hash=raw_hash,
                    query_run_id=execution.query_run_id,
                ))
                for item in flattened:
                    port = item.get("port")
                    if not isinstance(port, int) or not 1 <= port <= 65535:
                        continue
                    transport_value = str(item.get("protocol") or "tcp").lower()
                    transport = Transport.UDP if transport_value == "udp" else Transport.TCP
                    raw_service = next((
                        service for service in raw_services
                        if isinstance(service, dict) and service.get("port") == port
                    ), {})
                    service_observed = _optional_utc(
                        item.get("service_last_observed_at")
                        or raw_service.get("observed_at")
                        or raw_service.get("last_updated_at")
                    )
                    if service_observed and service_observed > collected:
                        raise ValueError(
                            "Censys service observed_at cannot be later than collected_at"
                        )
                    software_items = (
                        raw_service.get("software")
                        if isinstance(raw_service.get("software"), list) else []
                    )
                    software = next(
                        (value for value in software_items if isinstance(value, dict)), {}
                    )
                    service_material = f"{observation_id}|{port}|{transport.value}"
                    services.append(ServiceObservationRecord(
                        service_observation_id=f"svc-{sha256_text(service_material)[:20]}",
                        observation_id=observation_id,
                        port=port,
                        transport=transport,
                        protocol=str(item.get("service_name") or "unknown"),
                        observed_at=service_observed,
                        observation_time_basis=(
                            ObservationTimeBasis.CENSYS_SERVICE_OBSERVED_AT
                            if service_observed else ObservationTimeBasis.UNAVAILABLE
                        ),
                        banner_hash=_normalized_text_hash(item.get("http_banner")),
                        http_title_hash=_normalized_text_hash(item.get("http_title")),
                        cert_sha256=item.get("cert_sha256"),
                        spki_sha256=(
                            _nested(raw_service, "tls.certificates.leaf_data.spki_subject_fingerprint")
                            or _nested(raw_service, "tls.certificates.leaf_data.spki_sha256")
                            or raw_service.get("spki_sha256")
                        ),
                        jarm=item.get("jarm"),
                        ja4=(
                            _nested(raw_service, "tls.ja4")
                            or raw_service.get("ja4")
                        ),
                        ssh_key_hash=(
                            _nested(raw_service, "ssh.server_host_key.fingerprint_sha256")
                            or raw_service.get("ssh_key_hash")
                        ),
                        software_vendor=software.get("vendor"),
                        software_product=software.get("product"),
                        software_version=software.get("version"),
                        extractor_version=extractor_version,
                    ))
        if hit_count == 0 and query.query_class is QueryClass.Q0_SEED:
            if len(query.source_indicator_ids) != 1:
                raise ValueError("Q0 negative observation requires exactly one source indicator")
            observation_id = f"obs-{sha256_text(execution.query_run_id + '|not-found')[:20]}"
            hosts.append(HostObservationRecord(
                observation_id=observation_id,
                indicator_id=query.source_indicator_ids[0],
                observed_at=None,
                collected_at=collected,
                observation_time_basis=ObservationTimeBasis.UNAVAILABLE,
                host_observed=False,
                negative_reason=NegativeReason.NOT_FOUND,
                raw_record_hash=canonical_json_hash({
                    "query_run_id": execution.query_run_id, "result_count": 0,
                }),
                query_run_id=execution.query_run_id,
            ))
        return NormalizedCensysBatch(
            tuple(hosts), tuple(services), tuple(discovered.values()), tuple(raw_page_hashes)
        )

    def derive_fingerprint_graph(
        self,
        *,
        host_observations: list[HostObservationRecord],
        service_observations: list[ServiceObservationRecord],
        extractor_version: str,
    ) -> FingerprintGraphBatch:
        """정규화 service 필드에서 fingerprint와 direct observed_with 관계를 만든다."""

        hosts = {record.observation_id: record for record in host_observations}
        fingerprints: dict[str, FingerprintRecord] = {}
        relations: dict[str, EntityRelationRecord] = {}

        def add_fingerprint(
            fingerprint_type: FingerprintType,
            value: str | None,
            host: HostObservationRecord,
            source_id: str,
        ) -> None:
            if not value:
                return
            fingerprint_id = f"fp-{sha256_text(fingerprint_type.value + '|' + value)[:20]}"
            existing = fingerprints.get(fingerprint_id)
            if existing is None:
                fingerprints[fingerprint_id] = FingerprintRecord(
                    fingerprint_id=fingerprint_id,
                    fingerprint_type=fingerprint_type,
                    fingerprint_value=value,
                    extractor_version=extractor_version,
                    first_available_at=host.collected_at,
                    sensitivity=FingerprintSensitivity.RESTRICTED,
                )
            elif host.collected_at < existing.first_available_at:
                fingerprints[fingerprint_id] = existing.model_copy(
                    update={"first_available_at": host.collected_at}
                )
            valid_from = host.observed_at or host.collected_at
            for src_id in (source_id, host.indicator_id):
                material = f"{src_id}|{fingerprint_id}|observed_with|{host.query_run_id}"
                relation_id = f"rel-{sha256_text(material)[:20]}"
                relations.setdefault(relation_id, EntityRelationRecord(
                    relation_id=relation_id,
                    src_id=src_id,
                    dst_id=fingerprint_id,
                    relation_type=EntityRelationType.OBSERVED_WITH,
                    valid_from=valid_from,
                    evidence_source=f"censys:{host.query_run_id}",
                    confidence=1.0,
                    available_at=host.collected_at,
                ))

        ports_by_host: dict[str, list[str]] = {}
        for service in service_observations:
            host = hosts.get(service.observation_id)
            if host is None:
                raise ValueError("service observation has no matching host observation")
            fields = (
                (FingerprintType.CERT, service.cert_sha256),
                (FingerprintType.SPKI, service.spki_sha256),
                (FingerprintType.JARM, service.jarm),
                (FingerprintType.JA4, service.ja4),
                (FingerprintType.BANNER, service.banner_hash),
                (FingerprintType.HTTP, service.http_title_hash),
                (FingerprintType.SSH, service.ssh_key_hash),
            )
            for fingerprint_type, value in fields:
                add_fingerprint(fingerprint_type, value, host, service.service_observation_id)
            ports_by_host.setdefault(host.observation_id, []).append(
                f"{service.transport.value}/{service.port}"
            )
        for observation_id, ports in ports_by_host.items():
            host = hosts[observation_id]
            canonical = ",".join(sorted(set(ports)))
            add_fingerprint(
                FingerprintType.PORTSET,
                sha256_text(canonical),
                host,
                host.observation_id,
            )
        return FingerprintGraphBatch(tuple(fingerprints.values()), tuple(relations.values()))
