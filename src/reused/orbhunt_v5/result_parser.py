"""ORB_Hunt_v5 parse_censys_results의 dict 기반 내부 파생본.

원본: D:/Gemini/ORB_Hunt_v5/src/orbhunt/stages/censys_collect.py
원본 commit: a9add3c272457246f19fdb073e1e1e465062732c
원본 SHA-256: BD4AC358CCBD5F603C9D58AB3960A39EED7769ED0F4A9B4D33E729896E64FB03
목적·지원 RQ: Censys host/service를 RQ1∼RQ3 관측 record로 정규화한다.
수정·보완: pandas/Pydantic 결합을 제거하고 Platform v3 host envelope와 안정 ID를 추가했다.
입력·출력: raw hit와 pivot provenance를 받아 JSON 직렬화 가능한 observation dict를 반환한다.
시간·provenance: service history range가 있을 때만 first/last observed를 계산한다.
보안·라이선스: network 호출 없이 cached/raw payload만 처리하며 내부 연구용 파생본이다.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any


def _nested(value: dict[str, Any], path: str):
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _stable_candidate_id(ip: str, port: Any, cluster_id: str) -> str:
    digest = hashlib.sha256(f"{cluster_id}|{ip}|{port}".encode()).hexdigest()
    return f"cand-{digest[:16]}"


def _host_from_hit(hit: dict[str, Any]) -> dict[str, Any]:
    if isinstance(hit.get("host"), dict):
        return hit["host"]
    host_v1 = hit.get("host_v1")
    if isinstance(host_v1, dict) and isinstance(host_v1.get("resource"), dict):
        return host_v1["resource"]
    return hit


def parse_censys_results(
    raw_results: list[dict[str, Any]],
    orb_cluster_id: str,
    pivot_id: str,
    pivot_type: str,
    pivot_value: str,
    host_obs_map: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for raw_hit in raw_results:
        hit = _host_from_hit(raw_hit)
        ip = hit.get("ip")
        if not ip:
            continue
        dns_names = _nested(hit, "dns.names") or hit.get("names") or []
        if isinstance(dns_names, str):
            dns_names = [dns_names]
        asn = _nested(hit, "autonomous_system.asn")
        asn_name = _nested(hit, "autonomous_system.name")
        country = _nested(hit, "location.country_code") or _nested(hit, "location.country")
        services = hit.get("services") or []
        if not services:
            services = [{}]
        for service in services:
            port = service.get("port")
            cert = (
                _nested(service, "tls.certificate.parsed.fingerprint_sha256")
                or _nested(service, "tls.certificates.leaf_data.fingerprint")
                or _nested(service, "tls.fingerprint_sha256")
                or _nested(service, "cert.fingerprint_sha256")
                or hit.get("fingerprint_sha256")
            )
            jarm_value = _nested(service, "tls.jarm.fingerprint") or service.get("jarm")
            jarm = jarm_value.get("fingerprint") if isinstance(jarm_value, dict) else jarm_value
            first_seen = last_seen = None
            source = "unavailable"
            ranges = [
                item for item in (host_obs_map or {}).get(ip, [])
                if str(item.get("port")) == str(port)
            ]
            starts: list[datetime] = []
            ends: list[datetime] = []
            for item in ranges:
                try:
                    if item.get("start_time"):
                        starts.append(datetime.fromisoformat(item["start_time"].replace("Z", "+00:00")))
                    if item.get("end_time"):
                        ends.append(datetime.fromisoformat(item["end_time"].replace("Z", "+00:00")))
                except ValueError:
                    continue
            if starts and ends:
                first_seen, last_seen, source = min(starts), max(ends), "v3_observations_api"
            observations.append({
                "candidate_id": _stable_candidate_id(ip, port or 0, orb_cluster_id),
                "orb_cluster_id": orb_cluster_id,
                "pivot_id": pivot_id,
                "pivot_type": pivot_type,
                "pivot_value": pivot_value,
                "ip": ip,
                "port": port,
                "protocol": service.get("transport_protocol") or "tcp",
                "service_name": service.get("service_name") or service.get("protocol"),
                "dns_names": list(dns_names),
                "cert_sha256": cert,
                "jarm": jarm,
                "favicon_hash": _nested(service, "http.response.favicon.hash"),
                "http_title": (
                    _nested(service, "http.response.html_title")
                    or _nested(service, "http.response.title")
                    or _nested(service, "endpoints.http.html_title")
                ),
                "http_banner": service.get("banner") or _nested(service, "endpoints.banner"),
                "asn": asn,
                "asn_name": asn_name,
                "country": country,
                "service_first_observed_at": first_seen.isoformat() if first_seen else None,
                "service_last_observed_at": last_seen.isoformat() if last_seen else None,
                "service_history_window_days": (
                    (last_seen - first_seen).days if first_seen and last_seen else None
                ),
                "service_observed_at_source": source,
            })
    return observations
