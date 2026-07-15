"""CTI search→screening→snapshot workflow 회귀 테스트.

목적: INCLUDE 결정만 원문 저장으로 이어지고 검색·검토 provenance가 보존되는지 검증한다.
지원 RQ: RQ1·RQ4 CTI corpus integrity.
설계: network 없는 backend/fetcher와 임시 SQLite·snapshot 디렉터리를 사용한다.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.cti.corpus_registry import CorpusRegistry
from src.cti.search_protocol import build_search_protocol
from src.cti.snapshots import FetchedDocument, ImmutableSnapshotStore
from src.cti.snapshots import import_existing_cti
from src.cti.workflow import (
    ManualScreeningInput, apply_manual_screening, run_search_stage,
    snapshot_included_candidates,
)
from src.models import (
    AcquisitionMode,
    CorpusPurpose,
    ScreeningDecision,
    SourceAccessClass,
    SourceDocumentRecord,
    SourceFamilyRecord,
    SourceRelationshipRecord,
    SourceRelationshipType,
    TimePrecision,
)


class Backend:
    name = "fixture"

    def search(self, query):
        return [
            {"title": "Include", "url": "https://example.org/a", "published_at": "2026-01-01"},
            {"title": "Exclude", "url": "https://example.org/b", "published_at": "2026-01-02"},
        ]


class Fetcher:
    def __init__(self):
        self.urls = []

    def fetch(self, url):
        self.urls.append(url)
        return FetchedDocument(url, "text/html", b"<html>relay[.]example[.]org</html>")


class CtiWorkflowTests(unittest.TestCase):
    def test_public_export_and_stage0_audit_fail_closed(self):
        now = datetime(2026, 7, 13, tzinfo=timezone.utc)
        family = SourceFamilyRecord(
            source_family_id="family-public", canonical_document_id="doc-public",
            reviewer_id="reviewer", reviewed_at=now,
        )
        public = SourceDocumentRecord(
            document_id="doc-public", canonical_url="https://example.org/public",
            publisher="example.org", title="Public", published_at=now,
            published_at_raw="2026-07-13T00:00:00Z",
            published_time_precision=TimePrecision.EXACT_TIMESTAMP,
            source_timezone="UTC", retrieved_at=now, content_sha256="d" * 64,
            acquisition_mode=AcquisitionMode.SYSTEMATIC_PUBLIC,
            source_access_class=SourceAccessClass.PUBLIC,
            corpus_purpose=CorpusPurpose.DEVELOPMENT,
            source_family_id=family.source_family_id,
        )
        restricted_family = family.model_copy(update={
            "source_family_id": "family-restricted",
            "canonical_document_id": "doc-restricted",
        })
        restricted = public.model_copy(update={
            "document_id": "doc-restricted",
            "canonical_url": "local://restricted/report",
            "content_sha256": "e" * 64,
            "acquisition_mode": AcquisitionMode.EXISTING_CURATED,
            "source_access_class": SourceAccessClass.RESTRICTED,
            "source_family_id": restricted_family.source_family_id,
        })
        with tempfile.TemporaryDirectory() as directory:
            registry = CorpusRegistry(Path(directory) / "registry.sqlite")
            registry.register_source_family(family, [])
            registry.register_source_family(restricted_family, [])
            registry.register_document(public)
            registry.register_document(restricted)

            exported = registry.public_source_manifest([public.document_id])
            self.assertEqual("public", exported[0]["source_access_class"])
            self.assertNotIn("normalized_value", exported[0])
            with self.assertRaisesRegex(ValueError, "restricted source"):
                registry.public_source_manifest([restricted.document_id])
            self.assertTrue(registry.stage0_gate_report()["passed"])

    def test_source_family_collapses_republication_to_one_independent_source(self):
        reviewed_at = datetime(2026, 7, 13, tzinfo=timezone.utc)
        family = SourceFamilyRecord(
            source_family_id="family-example",
            canonical_document_id="doc-original",
            reviewer_id="human-reviewer",
            reviewed_at=reviewed_at,
        )
        relationship = SourceRelationshipRecord(
            relationship_id="relationship-translation",
            source_family_id=family.source_family_id,
            document_id="doc-translation",
            related_document_id="doc-original",
            relationship_type=SourceRelationshipType.TRANSLATION,
            reviewer_id="human-reviewer",
            reviewed_at=reviewed_at,
        )
        documents = [
            SourceDocumentRecord(
                document_id=document_id,
                canonical_url=f"https://example.org/{document_id}",
                publisher="example.org", title=document_id,
                published_at=reviewed_at,
                published_at_raw="2026-07-13T00:00:00Z",
                published_time_precision=TimePrecision.EXACT_TIMESTAMP,
                source_timezone="UTC", retrieved_at=reviewed_at,
                content_sha256=content_hash,
                acquisition_mode=AcquisitionMode.SYSTEMATIC_PUBLIC,
                source_access_class=SourceAccessClass.PUBLIC,
                corpus_purpose=CorpusPurpose.DEVELOPMENT,
                source_family_id=family.source_family_id,
            )
            for document_id, content_hash in (
                ("doc-original", "a" * 64),
                ("doc-translation", "b" * 64),
            )
        ]
        with tempfile.TemporaryDirectory() as directory:
            registry = CorpusRegistry(Path(directory) / "registry.sqlite")
            effects = registry.register_source_family(family, [relationship])
            for document in documents:
                registry.register_document(document)

            self.assertEqual(1, effects["relationships_inserted"])
            self.assertEqual(
                {"family-example"},
                registry.independent_source_family_ids(
                    ["doc-original", "doc-translation"]
                ),
            )
            other_family = family.model_copy(update={
                "source_family_id": "family-independent",
                "canonical_document_id": "doc-independent",
            })
            registry.register_source_family(other_family, [])
            registry.register_document(documents[0].model_copy(update={
                "document_id": "doc-independent",
                "canonical_url": "https://other.example/doc-independent",
                "content_sha256": "c" * 64,
                "source_family_id": other_family.source_family_id,
            }))
            self.assertEqual(
                {"family-example", "family-independent"},
                registry.independent_source_family_ids(
                    ["doc-original", "doc-translation", "doc-independent"]
                ),
            )
            with self.assertRaisesRegex(ValueError, "lack reviewed source family"):
                registry.independent_source_family_ids(["doc-unreviewed"])

    def test_only_included_candidate_is_snapshotted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = CorpusRegistry(root / "corpus.sqlite")
            now = datetime(2026, 7, 13, tzinfo=timezone.utc)
            protocol = build_search_protocol(
                version="1", target_date_from="2026-01-01", target_date_to="2026-07-13",
                target_publishers=["example.org"], search_terms=["orb"],
                inclusion_rules=["technical"], exclusion_rules=["marketing"],
                deduplication_rule="canonical_url", research_cutoff_at=now,
                source_access_class=SourceAccessClass.PUBLIC,
                acquisition_mode=AcquisitionMode.SYSTEMATIC_PUBLIC,
                registered_at=now,
            )
            result = run_search_stage(
                protocol=protocol, expanded_queries=["orb"], backend=Backend(),
                domain_whitelist=["example.org"], registry=registry,
                manifest_path=root / "search.json", executed_at=now,
            )
            decisions = apply_manual_screening(
                search_result=result,
                decisions=[
                    ManualScreeningInput(result.candidates[0].candidate_id, ScreeningDecision.INCLUDE, "relevant", "r1"),
                    ManualScreeningInput(result.candidates[1].candidate_id, ScreeningDecision.EXCLUDE, "irrelevant", "r1"),
                ],
                registry=registry, reviewed_at=now,
            )
            fetcher = Fetcher()
            snapshots = snapshot_included_candidates(
                search_result=result, decision_map=decisions, fetcher=fetcher,
                store=ImmutableSnapshotStore(root / "snapshots"), retrieved_at=now,
            )
            self.assertEqual(1, len(snapshots))
            self.assertEqual([result.candidates[0].url], fetcher.urls)
            self.assertTrue(Path(snapshots[0].snapshot_path).is_file())
            self.assertRegex(
                Path(snapshots[0].snapshot_path).name,
                r"^2026-07-13-include-cti-doc-[0-9a-f]{20}\.html$",
            )
            self.assertRegex(
                Path(snapshots[0].metadata_path).name,
                r"^2026-07-13-include-cti-doc-[0-9a-f]{20}-metadata\.json$",
            )

    def test_existing_cti_keeps_original_filename_and_blocks_path_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "data" / "cti"
            root.mkdir(parents=True)
            source = root / "My Original Report.pdf"
            original = b"fixture-pdf-content"
            source.write_bytes(original)
            sidecar = root / "My Original Report OCR.txt"
            sidecar.write_text("relay[.]example[.]org", encoding="utf-8")
            index = root / "index.json"
            index.write_text(
                '[{"file":"My Original Report.pdf","text_file":"My Original Report OCR.txt",'
                '"title":"Original Report",'
                '"publisher":"Example","published_at":"2026-01-01T00:00:00Z",'
                '"published_time_precision":"exact_timestamp","source_timezone":"UTC",'
                '"source_access_class":"restricted","corpus_purpose":"development",'
                '"source_independence":"commercial_cti_research"}]',
                encoding="utf-8",
            )
            records = import_existing_cti(
                source_root=root, index_path=index,
                registry=CorpusRegistry(Path(directory) / "registry.sqlite"),
                imported_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
            )
            self.assertEqual("My Original Report.pdf", records[0]["original_filename"])
            self.assertEqual(original, source.read_bytes())
            self.assertEqual("existing_curated", records[0]["acquisition_mode"])
            self.assertEqual("exact_timestamp", records[0]["published_time_precision"])
            self.assertEqual("UTC", records[0]["source_timezone"])
            self.assertEqual(
                "commercial_cti_research", records[0]["source_independence"]
            )
            self.assertEqual(str(sidecar.resolve()), records[0]["text_snapshot_path"])
            self.assertIsNotNone(records[0]["text_content_sha256"])
            index.write_text(
                '[{"file":"../outside.pdf","publisher":"Example",'
                '"published_at":"2026-01-01T00:00:00Z"}]', encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                import_existing_cti(
                    source_root=root, index_path=index,
                    registry=CorpusRegistry(Path(directory) / "registry-2.sqlite"),
                    imported_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
                )


if __name__ == "__main__":
    unittest.main()
