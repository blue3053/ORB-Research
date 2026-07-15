"""수동 포함 문헌의 passive fetch와 불변 원문 snapshot 저장.

목적: screening에서 포함된 공개 CTI 문헌만 수동 승인된 도메인에서 내려받아 원문 증거로 보존한다.
지원 RQ: RQ1 corpus provenance, RQ4 독립 validation evidence.
재사용 원천: CTI-Agent base.fetch_article_snapshot의 원본 우선 저장 원칙을 계승한다.
설계: redirect 후 host 재검증, 크기 제한, live gate, content hash 기반 immutable 저장을 적용한다.
입력·출력: SearchCandidate URL을 받아 SnapshotRecord와 snapshot 파일을 생성한다.
시간·provenance 통제: retrieved_at과 provider 게시일을 분리하며 게시일을 추정하지 않는다.
보안·라이선스: HTTP(S) passive GET만 허용하고 script 실행·active scan·외부 파일 scheme을 금지한다.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import unicodedata
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.cti.search_execution import (
    SearchCandidate,
    host_allowed,
    infer_publication_metadata,
)
from src.manifests import write_immutable_json
from src.provenance import sha256_file, sha256_text
from src.models import (
    AcquisitionMode, CorpusPurpose, SourceAccessClass, SourceDocumentRecord,
    TimePrecision,
)
from src.cti.corpus_registry import CorpusRegistry


@dataclass(frozen=True)
class FetchedDocument:
    final_url: str
    content_type: str
    body: bytes


@dataclass(frozen=True)
class SnapshotRecord:
    document_id: str
    candidate_id: str
    source_url: str
    final_url: str
    publisher: str
    title: str
    published_at: str | None
    published_at_basis: str
    published_time_precision: str
    source_timezone: str
    retrieved_at: str
    content_type: str
    content_sha256: str
    snapshot_path: str
    metadata_path: str
    search_protocol_id: str
    source_access_class: str
    acquisition_mode: str
    corpus_purpose: str


class PassiveDocumentFetcher:
    def __init__(self, whitelist: list[str], *, max_bytes: int = 25 * 1024 * 1024, timeout=60.0):
        if os.environ.get("ALLOW_CTI_SNAPSHOT_FETCH") != "1":
            raise RuntimeError("CTI snapshot fetch requires ALLOW_CTI_SNAPSHOT_FETCH=1")
        self.whitelist = whitelist
        self.max_bytes = max_bytes
        self.timeout = timeout

    def fetch(self, url: str) -> FetchedDocument:
        if not host_allowed(url, self.whitelist):
            raise ValueError("snapshot URL is outside the registered whitelist")
        request = urllib.request.Request(url, headers={
            "User-Agent": "ORB-Research/0.1 passive-cti-snapshot",
            "Accept": "text/html,application/pdf,text/plain;q=0.9,*/*;q=0.1",
        })
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            final_url = response.geturl()
            if not host_allowed(final_url, self.whitelist):
                raise ValueError("snapshot redirect escaped the registered whitelist")
            length = response.headers.get("Content-Length")
            if length and int(length) > self.max_bytes:
                raise ValueError("snapshot exceeds configured size limit")
            body = response.read(self.max_bytes + 1)
            if len(body) > self.max_bytes:
                raise ValueError("snapshot exceeds configured size limit")
            content_type = response.headers.get_content_type() or "application/octet-stream"
            return FetchedDocument(final_url, content_type, body)


class ImmutableSnapshotStore:
    def __init__(self, root: Path):
        self.root = root

    @staticmethod
    def _extension(content_type: str) -> str:
        return {
            "text/html": "html",
            "application/pdf": "pdf",
            "text/plain": "txt",
        }.get(content_type.lower(), "bin")

    @staticmethod
    def _slug(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).strip().lower()
        slug = re.sub(r"[^\w가-힣-]+", "-", normalized, flags=re.UNICODE).strip("-_")
        return slug[:60] or "cti-report"

    def store(
        self,
        candidate: SearchCandidate,
        fetched: FetchedDocument,
        *,
        retrieved_at: datetime | None = None,
    ) -> SnapshotRecord:
        retrieved = (retrieved_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        content_hash = hashlib.sha256(fetched.body).hexdigest()
        document_id = f"cti-doc-{sha256_text(candidate.url + '|' + content_hash)[:20]}"
        self.root.mkdir(parents=True, exist_ok=True)
        basename = f"{retrieved.date().isoformat()}-{self._slug(candidate.title)}-{document_id}"
        snapshot_path = self.root / f"{basename}.{self._extension(fetched.content_type)}"
        metadata_path = self.root / f"{basename}-metadata.json"
        if snapshot_path.exists():
            if sha256_file(snapshot_path) != hashlib.sha256(fetched.body).hexdigest():
                raise FileExistsError("immutable snapshot exists with different content")
        else:
            with snapshot_path.open("xb") as handle:
                handle.write(fetched.body)
                handle.flush()
                os.fsync(handle.fileno())
        record = SnapshotRecord(
            document_id=document_id,
            candidate_id=candidate.candidate_id,
            source_url=candidate.url,
            final_url=fetched.final_url,
            publisher=candidate.publisher,
            title=candidate.title,
            published_at=candidate.published_at,
            published_at_basis=candidate.published_at_basis,
            published_time_precision=candidate.published_time_precision,
            source_timezone=candidate.source_timezone,
            retrieved_at=retrieved.isoformat(),
            content_type=fetched.content_type,
            content_sha256=sha256_file(snapshot_path),
            snapshot_path=str(snapshot_path),
            metadata_path=str(metadata_path),
            search_protocol_id=candidate.search_protocol_id,
            source_access_class=candidate.source_access_class,
            acquisition_mode=candidate.acquisition_mode,
            corpus_purpose=candidate.corpus_purpose,
        )
        write_immutable_json(metadata_path, asdict(record))
        return record


def import_existing_cti(
    *,
    source_root: Path,
    index_path: Path,
    registry: CorpusRegistry,
    imported_at: datetime,
) -> tuple[dict, ...]:
    """data/cti 원본 파일명을 유지한 채 hash와 existing_curated metadata만 등록한다."""

    root = source_root.resolve()
    if index_path.resolve().parent != root:
        raise ValueError("existing CTI index must be directly inside source_root")
    entries = json.loads(index_path.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError("existing CTI index must be a JSON array")
    imported = imported_at.astimezone(timezone.utc)
    records: list[dict] = []
    seen_files: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("existing CTI index entries must be objects")
        relative = str(entry.get("file", ""))
        if not relative or relative in seen_files:
            raise ValueError("existing CTI file must be nonempty and unique")
        seen_files.add(relative)
        source_path = (root / relative).resolve()
        if root not in source_path.parents or not source_path.is_file():
            raise ValueError(f"existing CTI file is missing or escapes source_root: {relative}")
        content_hash = sha256_file(source_path)
        text_path: Path | None = None
        text_hash: str | None = None
        if entry.get("text_file"):
            text_path = (root / str(entry["text_file"])).resolve()
            if root not in text_path.parents or not text_path.is_file():
                raise ValueError(f"OCR text_file is missing or escapes source_root: {entry['text_file']}")
            text_hash = sha256_file(text_path)
        document_id = f"cti-doc-{sha256_text(relative + '|' + content_hash)[:20]}"
        if "published_time_precision" not in entry or "source_timezone" not in entry:
            raise ValueError(
                f"published_time_precision and source_timezone are required: {relative}"
            )
        if "source_access_class" not in entry or "corpus_purpose" not in entry:
            raise ValueError(
                f"source_access_class and corpus_purpose are required: {relative}"
            )
        published_raw = str(entry["published_at"])
        precision = TimePrecision(str(entry["published_time_precision"]))
        source_timezone = str(entry["source_timezone"]).strip()
        publication = infer_publication_metadata(published_raw, source_timezone)
        if precision in {
            TimePrecision.EXACT_TIMESTAMP,
            TimePrecision.DATE,
            TimePrecision.MONTH,
            TimePrecision.YEAR,
        } and publication.precision is not precision:
            raise ValueError(
                f"published_at does not match published_time_precision: {relative}"
            )
        document = SourceDocumentRecord(
            document_id=document_id,
            canonical_url=str(entry.get("source_url") or f"local://existing_curated/{relative}"),
            publisher=str(entry["publisher"]),
            title=str(entry.get("title") or source_path.stem),
            published_at=publication.exact_datetime,
            published_at_raw=published_raw,
            published_time_precision=precision,
            source_timezone=source_timezone,
            retrieved_at=imported,
            content_sha256=content_hash,
            text_content_sha256=text_hash,
            acquisition_mode=AcquisitionMode.EXISTING_CURATED,
            source_access_class=SourceAccessClass(str(entry["source_access_class"])),
            corpus_purpose=CorpusPurpose(str(entry["corpus_purpose"])),
            source_independence=str(entry.get("source_independence", "unknown")),
        )
        inserted = registry.register_document(document)
        records.append({
            "document_id": document_id,
            "final_url": document.canonical_url,
            "publisher": document.publisher,
            "title": document.title,
            "published_at": document.published_at_raw,
            "published_time_precision": document.published_time_precision.value,
            "source_timezone": document.source_timezone,
            "retrieved_at": document.retrieved_at.isoformat(),
            "content_sha256": content_hash,
            "snapshot_path": str(source_path),
            "content_type": mimetypes.guess_type(source_path.name)[0] or "application/octet-stream",
            "text_snapshot_path": str(text_path) if text_path else None,
            "text_content_sha256": text_hash,
            "acquisition_mode": AcquisitionMode.EXISTING_CURATED.value,
            "source_access_class": document.source_access_class.value,
            "corpus_purpose": document.corpus_purpose.value,
            "source_independence": document.source_independence,
            "original_filename": relative,
            "registered": inserted,
        })
    return tuple(records)
