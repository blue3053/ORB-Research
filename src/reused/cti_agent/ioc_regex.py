"""CTI-Agent ioc_regex.py의 dependency-free 내부 파생본.

원본: D:/Claude/CTI-Agent/src/utils/ioc_regex.py
원본 commit: 1d5e1b6b7bf294dc5b63fc3cde81f23fa4d176d9
원본 SHA-256: B576C7DBA5D958C8D26E4637ADF9DB4F92C8541F401820A0CABB7D751AF36A31
목적·지원 RQ: defang 해제·정규화·형식검증을 RQ1∼RQ4 indicator 계약으로 재사용한다.
수정·보완: 원본 pure logic을 유지하고 DB 결합 없이 독립 모듈로 고정했다.
입력·출력: scope/raw form을 받아 normalized indicator 또는 형식 판정을 반환한다.
시간·provenance: 시간 판단은 수행하지 않고 상위 IoC pipeline이 available_at을 통제한다.
보안·라이선스: 명시적 원본 라이선스가 없어 내부 연구용이며 재배포 전 권리 확인이 필요하다.
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

SCOPES = (
    "ip", "domain", "url", "cert", "jarm", "ja3",
    "hash_md5", "hash_sha1", "hash_sha256", "email", "mutex", "filepath",
)
CONTEXTS = ("malicious", "victim", "legitimate_infra", "relay_node", "unknown")

_REFANG_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\[://\]"), "://"),
    (re.compile(r"h[xX]{2}p(s?)://", re.IGNORECASE), r"http\1://"),
    (re.compile(r"\[\s*\.\s*\]|\(\s*\.\s*\)|\{\s*\.\s*\}"), "."),
    (re.compile(r"\[dot\]|\(dot\)", re.IGNORECASE), "."),
    (re.compile(r"\[:\]"), ":"),
    (re.compile(r"\[@\]|\(@\)"), "@"),
    (re.compile(r"\[at\]", re.IGNORECASE), "@"),
]


def refang(raw_form: str) -> str:
    value = raw_form.strip()
    for _ in range(3):
        before = value
        for pattern, replacement in _REFANG_RULES:
            value = pattern.sub(replacement, value)
        if value == before:
            break
    return value


def defang(value: str) -> str:
    output = re.sub(r"^http(s?)://", r"hxxp\1://", value.strip()).replace("@", "[@]")
    if "://" in output:
        scheme, rest = output.split("://", 1)
        return scheme + "://" + rest.replace(".", "[.]")
    return output.replace(".", "[.]")


def is_defanged(raw_form: str) -> bool:
    return refang(raw_form) != raw_form.strip()


_HEX = "0123456789abcdef"
_DOMAIN_RE = re.compile(
    r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}$",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@([A-Za-z0-9-]+\.)+[A-Za-z]{2,}$")
_FILEPATH_RE = re.compile(r"""^(?:[A-Za-z]:\\|\\\\|/|~/|\.{1,2}[/\\]|%[A-Za-z]+%\\?)[^\r\n]+$""")
_MUTEX_RE = re.compile(r"^[\x20-\x7e]{2,256}$")


def _is_hex(value: str, length: int) -> bool:
    return len(value) == length and all(character in _HEX for character in value.lower())


def _valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _valid_url(value: str) -> bool:
    if re.search(r"\s", value):
        return False
    try:
        parsed = urlparse(value)
        host = parsed.hostname
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https", "ftp", "ws", "wss") or not host:
        return False
    return bool(_DOMAIN_RE.match(host)) or _valid_ip(host)


def _valid_cert(value: str) -> bool:
    compact = value.replace(":", "").lower()
    return _is_hex(compact, 40) or _is_hex(compact, 64)


def _valid_jarm(value: str) -> bool:
    return _is_hex(value, 62) or bool(re.fullmatch(r"[0-9a-z]{62}", value.lower()))


_VALIDATORS = {
    "ip": _valid_ip,
    "domain": lambda value: bool(_DOMAIN_RE.match(value)) and not _valid_ip(value),
    "url": _valid_url,
    "cert": _valid_cert,
    "jarm": _valid_jarm,
    "ja3": lambda value: _is_hex(value, 32),
    "hash_md5": lambda value: _is_hex(value, 32),
    "hash_sha1": lambda value: _is_hex(value, 40),
    "hash_sha256": lambda value: _is_hex(value, 64),
    "email": lambda value: bool(_EMAIL_RE.match(value)),
    "mutex": lambda value: bool(_MUTEX_RE.match(value)),
    "filepath": lambda value: bool(_FILEPATH_RE.match(value)),
}


def validate(scope: str, value: str) -> bool:
    if scope not in SCOPES:
        raise ValueError(f"허용되지 않은 scope: {scope!r}")
    return bool(value and value == value.strip() and _VALIDATORS[scope](value))


def normalize(scope: str, raw_form: str) -> str:
    value = refang(raw_form)
    if scope in ("hash_md5", "hash_sha1", "hash_sha256", "ja3", "jarm"):
        return value.lower()
    if scope == "cert":
        return value.replace(":", "").lower()
    if scope == "domain":
        return value.rstrip(".").lower()
    if scope == "email":
        local, _, domain = value.rpartition("@")
        return f"{local}@{domain.lower()}" if domain else value
    if scope == "url":
        if "://" not in value and re.match(r"^[A-Za-z0-9.\-\[\]]+\.[A-Za-z0-9\-]{2,}(:\d+)?/", value):
            value = "http://" + value
        try:
            parsed = urlparse(value)
            hostname = parsed.hostname
        except ValueError:
            return value
        if hostname and parsed.netloc:
            host_in_netloc = parsed.netloc.rsplit("@", 1)[-1]
            lowered = parsed.netloc[: len(parsed.netloc) - len(host_in_netloc)] + host_in_netloc.lower()
            value = value.replace(parsed.netloc, lowered, 1)
        return value
    return value
