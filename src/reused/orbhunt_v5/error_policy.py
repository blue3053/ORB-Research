"""ORB_Hunt_v5 Censys 오류 분류·재시도 정책 내부 파생본.

원본: D:/Gemini/ORB_Hunt_v5/src/orbhunt/stages/censys_collect.py
원본 commit: a9add3c272457246f19fdb073e1e1e465062732c
원본 SHA-256: BD4AC358CCBD5F603C9D58AB3960A39EED7769ED0F4A9B4D33E729896E64FB03
목적·지원 RQ: 수집 실패를 일관되게 분류해 RQ1∼RQ5 completeness를 감사한다.
수정·보완: httpx type 결합을 제거하고 표준 예외명·메시지 기반으로 dependency-free화했다.
입력·출력: Exception을 받아 category/status/retryable을 반환한다.
시간·provenance: retry 횟수와 시각 기록은 상위 collector가 담당한다.
보안·라이선스: SSL 검증 실패는 재시도하지 않으며 내부 연구용 파생본이다.
"""
from __future__ import annotations

import re

SSL_ERROR_CATEGORY = "ssl_cert_verify_failed"
RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}


def extract_http_status(message: str) -> str:
    match = re.search(r"\bStatus\s+(\d{3})\b", message)
    if not match:
        match = re.search(r"\bstatus(?: code)?[=:\s]+(\d{3})\b", message, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def classify_censys_exception(exc: Exception) -> tuple[str, str]:
    message = str(exc)
    lowered = message.lower()
    status = extract_http_status(message)
    class_name = exc.__class__.__name__.lower()
    if "certificate_verify_failed" in lowered or "certificate verify failed" in lowered:
        return SSL_ERROR_CATEGORY, status
    if status == "429":
        return "api_rate_limited", status
    if status == "504":
        return "api_504_gateway_timeout", status
    if status and int(status) >= 500:
        return "api_5xx_error", status
    if "timeout" in class_name or "timed out" in lowered or "timeout" in lowered:
        return "api_timeout", status
    if "network" in class_name or "connection" in class_name:
        return "network_error", status
    return "censys_api_error", status


def is_retryable_censys_error(category: str, http_status: str) -> bool:
    if category == SSL_ERROR_CATEGORY:
        return False
    if http_status and int(http_status) in RETRYABLE_HTTP_STATUSES:
        return True
    return category in {
        "api_rate_limited", "api_504_gateway_timeout", "api_5xx_error",
        "api_timeout", "network_error",
    }
