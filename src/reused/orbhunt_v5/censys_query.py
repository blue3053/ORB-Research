"""ORB_Hunt_v5 censys_query.py의 pure renderer 내부 파생본.

원본: D:/Gemini/ORB_Hunt_v5/src/orbhunt/stages/censys_query.py
원본 commit: a9add3c272457246f19fdb073e1e1e465062732c
원본 SHA-256: DDCD516594D9975B4BEAB127CD6EEE45EF347386CDF7C9F157AFF2BF61C753B5
목적·지원 RQ: fail-closed Q1 query 렌더링을 RQ3·RQ5 baseline에 재사용한다.
수정·보완: pandas/CSV/CLI를 제거하고 build_query_for_pivot dependency closure만 복사했다.
입력·출력: pivot type/value/template config를 받아 CenQL 문자열을 반환한다.
시간·provenance: query 동결과 split은 상위 QueryRegistry가 통제한다.
보안·라이선스: live API를 호출하지 않으며 명시적 원본 라이선스가 없어 내부 연구용이다.
"""
from __future__ import annotations

import ipaddress
from typing import Any

from src.reused.orbhunt_v5.pivot_safety import domain_query_block_reason

PIVOT_TEMPLATE_MAP = {
    "cert_sha256": "cert_sha256", "jarm": "jarm", "domain": "domain",
    "banner_token": "banner_token", "product": "product", "port": "port",
}


def _looks_like_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _template_name_for_pivot(pivot_type: str, template_config: dict[str, Any]) -> str | None:
    if pivot_type == "ip" and not template_config.get("safety", {}).get("allow_raw_ip_query", False):
        return None
    return PIVOT_TEMPLATE_MAP.get(pivot_type)


def build_query_for_pivot(pivot_type: str, pivot_value: str, template_config: dict[str, Any]) -> str:
    templates = template_config.get("templates", {})
    safety = template_config.get("safety", {})
    fail_closed = safety.get("fail_closed_on_missing_field", True)
    if _looks_like_ip(str(pivot_value)) and not safety.get("allow_raw_ip_query", False):
        if fail_closed:
            raise ValueError("Raw IP Censys query is disabled by safety.allow_raw_ip_query")
        return ""
    template_key = _template_name_for_pivot(pivot_type, template_config)
    if not template_key or template_key not in templates:
        if fail_closed:
            raise ValueError(f"No valid template found for pivot type: {pivot_type} (mapped: {template_key})")
        return ""
    template_spec = templates[template_key] or {}
    if fail_closed and not template_spec.get("field"):
        raise ValueError(f"Template field is empty for key: {template_key}")
    template = str(template_spec.get("template") or "")
    if not template:
        if fail_closed:
            raise ValueError(f"Template string is empty for key: {template_key}")
        return ""
    value = str(pivot_value).strip()
    if template_key == "domain" and value.startswith("*."):
        value = value[2:]
    if template_key == "domain":
        reason = domain_query_block_reason(value, safety)
        if reason:
            if fail_closed:
                raise ValueError(f"Domain pivot blocked by safety: {reason}")
            return ""
    rendered = template.replace("{pivot_value}", value)
    max_length = int(safety.get("max_rendered_query_length", 2000) or 2000)
    if len(rendered) > max_length:
        if fail_closed:
            raise ValueError(f"Rendered query length ({len(rendered)}) exceeds safety limit ({max_length})")
        return ""
    return rendered
