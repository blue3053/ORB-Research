"""ORB_Hunt_v5 pivot_safety.py의 내부 파생본.

원본: D:/Gemini/ORB_Hunt_v5/src/orbhunt/stages/pivot_safety.py
원본 commit: a9add3c272457246f19fdb073e1e1e465062732c
원본 SHA-256: 284AA93287AD96BA10071D5A1382388FA94D0C4696FF2807BABE0928B571FE24
목적·지원 RQ: broad/reference/file-like domain을 차단해 RQ3·RQ5 Q1 precision을 보호한다.
수정·보완: pure deny policy만 복사하고 외부 package 결합을 제거했다.
입력·출력: domain과 safety config를 받아 차단 reason 또는 빈 문자열을 반환한다.
시간·provenance: query version과 cutoff는 상위 registry가 통제한다.
보안·라이선스: 명시적 원본 라이선스가 없어 내부 연구용이며 재배포 전 권리 확인이 필요하다.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

DEFAULT_BROAD_DOMAIN_EXACT = {
    "blog.sekoia.io", "blog.talosintelligence.com", "cloud.google.com", "pastebin",
    "pastebin.com", "securityscorecard.com", "sentinelone.com", "www", "www.cisa.gov",
    "www.example.com", "www.lumen.com",
}
DEFAULT_BROAD_DOMAIN_SUFFIXES = {
    "bitsight.com", "cisa.gov", "example.com", "example.org", "example.net",
    "humansecurity.com", "lumen.com", "sekoia.io", "securityscorecard.com",
    "sentinelone.com", "talosintelligence.com", "trendmicro.com", "us-cert.gov",
}
DEFAULT_FILELIKE_DOMAIN_SUFFIXES = {
    "7z", "bat", "bin", "cfg", "conf", "dat", "dll", "elf", "exe", "ini", "jar",
    "json", "lnk", "log", "msi", "pdf", "ps1", "scr", "sh", "sys", "tar", "txt",
    "xml", "zip",
}
DEFAULT_MALWARE_LABEL_TOKENS = {
    "agent", "foundstone", "generic", "genome", "hacktool", "kryptik", "malware",
    "riskware", "scanline", "shellcoderunner", "trojan", "win32", "wingo",
}
DOMAIN_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _normalize_domain(value: str) -> str:
    normalized = str(value or "").strip().lower().strip(".")
    return normalized[2:] if normalized.startswith("*.") else normalized


def _configured_values(safety: dict[str, Any] | None, key: str) -> set[str]:
    if not safety or safety.get(key) is None:
        return set()
    value = safety[key]
    values: Iterable[Any] = [value] if isinstance(value, str) or not isinstance(value, Iterable) else value
    return {_normalize_domain(str(item)) for item in values if _normalize_domain(str(item))}


def domain_query_block_reason(value: str, safety: dict[str, Any] | None = None) -> str:
    domain = _normalize_domain(value)
    if not domain:
        return "empty_domain"
    if "." not in domain:
        return "single_label_domain"
    labels = domain.split(".")
    if any(not DOMAIN_LABEL_RE.match(label) for label in labels):
        return "invalid_domain_syntax"
    exact = DEFAULT_BROAD_DOMAIN_EXACT | _configured_values(safety, "broad_domain_block_exact")
    if domain in exact:
        return "broad_or_reference_domain"
    suffixes = DEFAULT_BROAD_DOMAIN_SUFFIXES | _configured_values(safety, "broad_domain_block_suffixes")
    if any(domain == suffix or domain.endswith("." + suffix) for suffix in suffixes):
        return "broad_or_reference_domain"
    filelike = DEFAULT_FILELIKE_DOMAIN_SUFFIXES | _configured_values(safety, "filelike_domain_suffixes")
    if labels[-1] in filelike:
        return "filelike_domain"
    malware = DEFAULT_MALWARE_LABEL_TOKENS | _configured_values(safety, "malware_label_tokens")
    if any(label in malware for label in labels):
        return "malware_label_domain"
    return ""
