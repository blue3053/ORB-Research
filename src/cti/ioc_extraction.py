"""CTI 원문 기반 IoC 후보 검증·정규화 파이프라인.

목적: 추출기가 제안한 IoC를 원문 대조와 CTI-Agent 형식검증을 모두 통과한 경우에만 연구 seed로 만든다.
지원 RQ: RQ1 seed 관측, RQ2 cohort, RQ3 non-IP pivot, RQ4 독립 검증.
재사용 원천: CTI-Agent ioc_extract의 형식검증→원문대조 fail-closed 순서를 재사용한다.
설계: LLM 호출은 주입 가능한 Extractor로 격리하고 검증 결과는 결정적 indicator ID와 단계별 통계를 갖는다.
입력·출력: 원문 bytes·SourceDocumentRecord·후보 목록을 받아 VerifiedIndicator와 rejection 통계를 반환한다.
시간·provenance 통제: observed_at과 available_at을 분리하고 미래 관측일은 거부한다.
보안·라이선스: 원문은 반환하거나 로그에 복제하지 않고 evidence는 짧은 문맥과 source hash만 보존한다.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import hmac
from html.parser import HTMLParser
from io import BytesIO
import ipaddress
import os
from pathlib import Path
from typing import Any, Protocol

from src.adapters.cti_agent import CtiAgentAdapter
from src.models import (
    AssertionRole,
    AssertionVerdict,
    EvidenceType,
    IndicatorAssertionRecord,
    IndicatorRecord,
    IndicatorSensitivity,
    IndicatorType,
    ReviewerStatus,
    SourceDocumentRecord,
    utc,
)
from src.provenance import sha256_text
from src.reused.cti_agent.ioc_regex import CONTEXTS, SCOPES


EXTRACT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["iocs"],
    "properties": {
        "iocs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "raw_form", "scope", "context", "context_evidence", "observed_at"
                ],
                "properties": {
                    "raw_form": {"type": "string", "minLength": 3},
                    "scope": {"enum": list(SCOPES)},
                    "context": {"enum": list(CONTEXTS)},
                    "context_evidence": {"type": "string"},
                    "observed_at": {"type": ["string", "null"]},
                },
            },
        }
    },
}


class IndicatorExtractor(Protocol):
    def extract(self, document_text: str) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class DocumentText:
    text: str
    document_format: str
    text_extractor: str
    page_count: int | None
    text_sha256: str
    text_chars: int
    used_ocr_sidecar: bool


class _TextCollector(HTMLParser):
    """실행 불가 markup을 제외하고 article/main 본문을 우선 수집한다."""

    IGNORED_TAGS = {"script", "style", "noscript", "template", "svg"}
    MAIN_TAGS = {"article", "main"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.main_parts: list[str] = []
        self._ignored_depth = 0
        self._main_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        normalized = tag.lower()
        if normalized in self.IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth == 0 and normalized in self.MAIN_TAGS:
            self._main_depth += 1

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in self.IGNORED_TAGS:
            if self._ignored_depth > 0:
                self._ignored_depth -= 1
            return
        if self._ignored_depth == 0 and normalized in self.MAIN_TAGS:
            if self._main_depth > 0:
                self._main_depth -= 1

    def handle_data(self, data: str) -> None:
        stripped = data.strip()
        if self._ignored_depth == 0 and stripped:
            self.parts.append(stripped)
            if self._main_depth > 0:
                self.main_parts.append(stripped)


def extract_document_text(
    snapshot_bytes: bytes,
    *,
    ocr_sidecar_text: str | None = None,
    max_pdf_pages: int = 500,
    max_document_chars: int = 2_000_000,
) -> DocumentText:
    """HTML/text/PDF를 실행 없이 text로 변환하고 추출 provenance를 반환한다."""

    if max_pdf_pages < 1 or max_document_chars < 1:
        raise ValueError("PDF page and document character limits must be positive")
    if snapshot_bytes.lstrip().startswith(b"%PDF-"):
        try:
            from pypdf import PdfReader
        except ImportError as error:
            raise RuntimeError("PDF IoC extraction requires the pypdf package") from error
        try:
            reader = PdfReader(BytesIO(snapshot_bytes), strict=True)
        except Exception as error:
            raise ValueError(f"invalid or unsupported PDF: {type(error).__name__}") from error
        if reader.is_encrypted:
            raise ValueError("encrypted PDF is not accepted for IoC extraction")
        page_count = len(reader.pages)
        if page_count > max_pdf_pages:
            raise ValueError(f"PDF exceeds max_pdf_pages={max_pdf_pages}")
        if ocr_sidecar_text is not None:
            text = ocr_sidecar_text
            extractor = "user-provided-ocr-sidecar"
            used_sidecar = True
        else:
            pages: list[str] = []
            for number, page in enumerate(reader.pages, start=1):
                try:
                    page_text = page.extract_text() or ""
                except Exception as error:
                    raise ValueError(f"PDF page {number} text extraction failed") from error
                pages.append(f"[PDF page {number}]\n{page_text}")
            text = "\n".join(pages)
            extractor = "pypdf-text-layer"
            used_sidecar = False
        if not text.strip():
            raise ValueError(
                "PDF has no extractable text; add text_file OCR sidecar to data/cti/index.json"
            )
        if len(text) > max_document_chars:
            raise ValueError(f"PDF extracted text exceeds max_document_chars={max_document_chars}")
        return DocumentText(
            text=text,
            document_format="pdf",
            text_extractor=extractor,
            page_count=page_count,
            text_sha256=sha256_text(text),
            text_chars=len(text),
            used_ocr_sidecar=used_sidecar,
        )

    decoded = snapshot_bytes.decode("utf-8", errors="replace")
    parser = _TextCollector()
    try:
        parser.feed(decoded)
    except Exception:
        text = decoded
        text_extractor = "utf8-text-fallback"
    else:
        selected = parser.main_parts or parser.parts
        text = "\n".join(selected) if selected else decoded
        text_extractor = "stdlib-html-main-content-v2"
    if len(text) > max_document_chars:
        raise ValueError(f"document text exceeds max_document_chars={max_document_chars}")
    return DocumentText(
        text=text,
        document_format="html_or_text",
        text_extractor=text_extractor,
        page_count=None,
        text_sha256=sha256_text(text),
        text_chars=len(text),
        used_ocr_sidecar=False,
    )


def snapshot_to_text(snapshot_bytes: bytes) -> str:
    """기존 caller용 wrapper. PDF도 text layer가 있으면 자동 처리한다."""

    return extract_document_text(snapshot_bytes).text


class AnthropicIndicatorExtractor:
    """CTI-Agent의 structured tool 추출 방식을 연구용 live gate 뒤에서 재사용한다."""

    def __init__(
        self,
        *,
        model: str,
        prompt_path: Path,
        max_input_chars: int = 200_000,
        max_tokens: int = 8192,
    ) -> None:
        if os.environ.get("ALLOW_LIVE_CTI_EXTRACTION") != "1":
            raise RuntimeError(
                "live CTI extraction requires ALLOW_LIVE_CTI_EXTRACTION=1"
            )
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY is required")
        if max_input_chars < 1 or max_tokens < 1:
            raise ValueError("max_input_chars and max_tokens must be positive")
        self.model = model
        self.prompt_path = prompt_path
        self.prompt = prompt_path.read_text(encoding="utf-8")
        self.max_input_chars = max_input_chars
        self.max_tokens = max_tokens
        self.last_provenance: dict[str, Any] = {}

    def extract(self, document_text: str) -> list[dict[str, Any]]:
        """JSON schema tool output을 1회 재시도하고 후보 배열만 반환한다."""

        import anthropic
        import jsonschema

        client = anthropic.Anthropic(max_retries=2)
        tool = {
            "name": "emit_iocs",
            "description": "CTI 원문에서 추출한 IoC 후보를 제출한다.",
            "input_schema": EXTRACT_SCHEMA,
        }
        text = document_text[: self.max_input_chars]
        last_error = "unknown structured output error"
        for attempt in range(2):
            with client.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                system=self.prompt,
                messages=[{"role": "user", "content": text}],
                tools=[tool],
                tool_choice={"type": "tool", "name": "emit_iocs"},
            ) as stream:
                response = stream.get_final_message()
            block = next(
                (item for item in response.content if item.type == "tool_use"), None
            )
            if response.stop_reason == "max_tokens":
                last_error = "structured output was truncated at max_tokens"
                continue
            if block is None:
                last_error = "tool_use block is absent"
                continue
            try:
                jsonschema.validate(block.input, EXTRACT_SCHEMA)
            except jsonschema.ValidationError as error:
                last_error = f"schema violation: {error.message[:200]}"
                continue
            self.last_provenance = {
                "backend": "anthropic-structured-tool",
                "model": self.model,
                "prompt_path": self.prompt_path.as_posix(),
                "prompt_sha256": sha256_text(self.prompt),
                "schema_sha256": sha256_text(str(EXTRACT_SCHEMA)),
                "attempt": attempt + 1,
                "input_chars": len(text),
                "input_truncated": len(document_text) > len(text),
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }
            return list(block.input["iocs"])
        raise RuntimeError(f"IoC structured extraction failed: {last_error}")


@dataclass(frozen=True)
class VerifiedIndicator:
    indicator_id: str
    scope: str
    value: str
    raw_form: str
    source_document_id: str
    observed_at: str
    available_at: str
    time_basis: str
    context: str
    context_evidence: str


@dataclass(frozen=True)
class ExtractionResult:
    indicators: tuple[VerifiedIndicator, ...]
    candidate_count: int
    format_rejected: int
    source_mismatch_rejected: int
    future_time_rejected: int
    duplicate_count: int
    extraction_provenance: dict[str, Any] | None = None


def _parse_observed(value: Any, fallback: datetime) -> tuple[datetime, str]:
    if not value:
        return fallback, "publication_date_fallback"
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return utc(parsed), "observed_in_report"


def verify_indicator_candidates(
    candidates: list[dict[str, Any]],
    snapshot_bytes: bytes,
    document: SourceDocumentRecord,
    adapter: CtiAgentAdapter,
    *,
    available_at: datetime,
    source_text: str | None = None,
) -> ExtractionResult:
    """후보를 형식·원문·시간 순으로 검증하여 검증된 indicator만 반환한다."""

    available = utc(available_at)
    verified: list[VerifiedIndicator] = []
    comparable_text = source_text if source_text is not None else snapshot_to_text(snapshot_bytes)
    seen: set[tuple[str, str]] = set()
    format_rejected = mismatch = future = duplicates = 0
    for candidate in candidates:
        scope = str(candidate.get("scope", ""))
        raw_form = str(candidate.get("raw_form", ""))
        if str(candidate.get("context", "unknown")) not in CONTEXTS:
            format_rejected += 1
            continue
        try:
            value = adapter.normalize_indicator(scope, raw_form)
        except (KeyError, TypeError, ValueError):
            format_rejected += 1
            continue
        if (
            raw_form.encode("utf-8") not in snapshot_bytes
            and raw_form not in comparable_text
        ):
            mismatch += 1
            continue
        try:
            observed, basis = _parse_observed(candidate.get("observed_at"), document.published_at)
        except ValueError:
            format_rejected += 1
            continue
        if observed > available:
            future += 1
            continue
        key = (scope, value)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        material = f"{document.document_id}|{scope}|{value}"
        verified.append(VerifiedIndicator(
            indicator_id=f"ioc-{sha256_text(material)[:20]}",
            scope=scope,
            value=value,
            raw_form=raw_form,
            source_document_id=document.document_id,
            observed_at=observed.isoformat(),
            available_at=available.isoformat(),
            time_basis=basis,
            context=str(candidate.get("context", "unknown")),
            context_evidence=str(candidate.get("context_evidence", ""))[:500],
        ))
    return ExtractionResult(
        indicators=tuple(verified),
        candidate_count=len(candidates),
        format_rejected=format_rejected,
        source_mismatch_rejected=mismatch,
        future_time_rejected=future,
        duplicate_count=duplicates,
    )


def extract_and_verify_indicators(
    snapshot_bytes: bytes,
    document: SourceDocumentRecord,
    adapter: CtiAgentAdapter,
    extractor: IndicatorExtractor,
    *,
    available_at: datetime,
    ocr_sidecar_text: str | None = None,
    max_pdf_pages: int = 500,
    max_document_chars: int = 2_000_000,
) -> ExtractionResult:
    """원문을 후보로 추출한 뒤 동일 호출 안에서 fail-closed 검증까지 완료한다."""

    document_text = extract_document_text(
        snapshot_bytes,
        ocr_sidecar_text=ocr_sidecar_text,
        max_pdf_pages=max_pdf_pages,
        max_document_chars=max_document_chars,
    )
    candidates = extractor.extract(document_text.text)
    result = verify_indicator_candidates(
        candidates,
        snapshot_bytes,
        document,
        adapter,
        available_at=available_at,
        source_text=document_text.text,
    )
    provenance = getattr(extractor, "last_provenance", None)
    return replace(result, extraction_provenance={
        **dict(provenance or {}),
        "document_format": document_text.document_format,
        "text_extractor": document_text.text_extractor,
        "page_count": document_text.page_count,
        "text_sha256": document_text.text_sha256,
        "text_chars": document_text.text_chars,
        "used_ocr_sidecar": document_text.used_ocr_sidecar,
    })


def _indicator_type(scope: str, value: str) -> IndicatorType:
    if scope == "ip":
        return IndicatorType.IPV4 if ipaddress.ip_address(value).version == 4 else IndicatorType.OTHER
    return {
        "domain": IndicatorType.DOMAIN,
        "url": IndicatorType.URL,
        "cert": IndicatorType.CERT,
    }.get(scope, IndicatorType.OTHER)


def build_indicator_records(
    result: ExtractionResult,
    document: SourceDocumentRecord,
    *,
    ingested_at: datetime,
    public_id_hmac_key: bytes,
    campaign_id: str | None = None,
) -> tuple[list[IndicatorRecord], list[IndicatorAssertionRecord]]:
    """검증 결과를 제한 indicator와 출처별 보수적 assertion 레코드로 변환한다."""

    if len(public_id_hmac_key) < 32:
        raise ValueError("ORB_PUBLIC_ID_HMAC_KEY must be at least 32 bytes")
    ingested = utc(ingested_at)
    indicators: list[IndicatorRecord] = []
    assertions: list[IndicatorAssertionRecord] = []
    for item in result.indicators:
        material = f"{item.scope}|{item.value}".encode("utf-8")
        public_digest = hmac.new(public_id_hmac_key, material, hashlib.sha256).hexdigest()
        sensitivity = (
            IndicatorSensitivity.ACTIVE_VICTIM
            if item.context == "victim"
            else IndicatorSensitivity.RESTRICTED
        )
        indicators.append(IndicatorRecord(
            indicator_id=item.indicator_id,
            indicator_type=_indicator_type(item.scope, item.value),
            normalized_value=item.value,
            public_id=f"pub-{public_digest[:24]}",
            first_ingested_at=ingested,
            sensitivity=sensitivity,
        ))
        role = {
            "relay_node": AssertionRole.MIDDLE,
            "victim": AssertionRole.VICTIM,
        }.get(item.context, AssertionRole.UNKNOWN)
        evidence_hash = sha256_text(item.context_evidence)
        assertion_material = (
            f"{document.document_id}|{item.indicator_id}|{role.value}|{evidence_hash}"
        )
        assertions.append(IndicatorAssertionRecord(
            assertion_id=f"assert-{sha256_text(assertion_material)[:20]}",
            indicator_id=item.indicator_id,
            document_id=document.document_id,
            campaign_id=campaign_id,
            role=role,
            verdict=AssertionVerdict.CANDIDATE,
            evidence_type=EvidenceType.CLAIM,
            vendor_first_seen=datetime.fromisoformat(item.observed_at),
            first_public_at=document.published_at,
            context_excerpt_hash=evidence_hash,
            reviewer_status=ReviewerStatus.PENDING,
        ))
    return indicators, assertions
