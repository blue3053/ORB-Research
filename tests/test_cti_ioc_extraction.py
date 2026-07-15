"""CTI IoC 원문대조·시간 cutoff 회귀 테스트.

목적: 원문에 없는 후보와 미래 관측 후보가 seed로 유입되지 않는지 확인한다.
지원 RQ: RQ1∼RQ4 CTI seed integrity.
설계: 실제 CTI-Agent 정규화 모듈을 읽기 전용으로 재사용한다.
"""
from __future__ import annotations

import unittest
import tempfile
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path

from src.adapters.cti_agent import CtiAgentAdapter
from src.cti.ioc_extraction import (
    ExtractionResult,
    VerifiedIndicator,
    build_indicator_records,
    extract_document_text,
    extract_and_verify_indicators,
    verify_indicator_candidates,
    ensure_development_source_manifest,
)
from src.cti.corpus_registry import CorpusRegistry
from src.models import (
    AssertionReviewRecord, AssertionRole, IndicatorRecord, IndicatorSensitivity,
    IndicatorType, ReviewerStatus,
)
from src.models import (
    AcquisitionMode,
    CorpusPurpose,
    SourceAccessClass,
    SourceDocumentRecord,
    SourceFamilyRecord,
    TimePrecision,
)


class IocExtractionTests(unittest.TestCase):
    def test_stage1_spki_and_sinkhole_are_preserved_but_not_queryable(self):
        document = SourceDocumentRecord(
            document_id="doc-spki", canonical_url="https://example.org/spki",
            publisher="example.org", title="SPKI",
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            published_at_raw="2026-01-01T00:00:00Z",
            published_time_precision=TimePrecision.EXACT_TIMESTAMP,
            source_timezone="UTC",
            retrieved_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            content_sha256="3" * 64,
            acquisition_mode=AcquisitionMode.SYSTEMATIC_PUBLIC,
            source_access_class=SourceAccessClass.PUBLIC,
            corpus_purpose=CorpusPurpose.DEVELOPMENT,
            source_family_id="family-spki",
        )
        raw_spki = "a" * 64
        result = verify_indicator_candidates(
            [{
                "scope": "spki", "raw_form": raw_spki,
                "observed_at": "2026-01-01T00:00:00Z",
                "context": "sinkhole", "context_evidence": "Defensive sinkhole.",
            }],
            raw_spki.encode("ascii"), document,
            CtiAgentAdapter(Path(r"D:\Claude\CTI-Agent")),
            available_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        self.assertEqual("spki", result.indicators[0].scope)
        _, mentions, assertions = build_indicator_records(
            result, document, ingested_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            public_id_hmac_key=b"k" * 32,
        )
        self.assertEqual("spki", mentions[0].scope)
        self.assertEqual(AssertionRole.SINKHOLE, assertions[0].role)

    def test_same_indicator_keeps_identity_across_source_documents(self):
        first = SourceDocumentRecord(
            document_id="doc-a", canonical_url="https://a.example/report",
            publisher="a.example", title="A",
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            published_at_raw="2026-01-01T00:00:00Z",
            published_time_precision=TimePrecision.EXACT_TIMESTAMP,
            source_timezone="UTC",
            retrieved_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            content_sha256="1" * 64,
            acquisition_mode=AcquisitionMode.SYSTEMATIC_PUBLIC,
            source_access_class=SourceAccessClass.PUBLIC,
            corpus_purpose=CorpusPurpose.DEVELOPMENT,
            source_family_id="family-a",
        )
        second = first.model_copy(update={
            "document_id": "doc-b", "canonical_url": "https://b.example/report",
            "publisher": "b.example", "title": "B", "content_sha256": "2" * 64,
            "source_family_id": "family-b",
        })
        candidate = [{
            "scope": "domain", "raw_form": "relay[.]example[.]org",
            "observed_at": "2026-01-01T00:00:00Z", "context": "relay_node",
            "context_evidence": "Relay node.",
        }]
        adapter = CtiAgentAdapter(Path(r"D:\Claude\CTI-Agent"))
        results = [
            verify_indicator_candidates(
                candidate, b"relay[.]example[.]org", document, adapter,
                available_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            )
            for document in (first, second)
        ]
        self.assertEqual(
            results[0].indicators[0].indicator_id,
            results[1].indicators[0].indicator_id,
        )
        self.assertNotEqual(
            results[0].indicators[0].source_document_id,
            results[1].indicators[0].source_document_id,
        )

    def test_prospective_manifest_cannot_feed_development_pivots(self):
        with self.assertRaisesRegex(ValueError, "cannot feed development pivots"):
            ensure_development_source_manifest({
                "source_access_class": "public",
                "source_acquisition_mode": "prospective_validation",
                "source_corpus_purpose": "prospective_validation",
            })
        with self.assertRaisesRegex(ValueError, "lacks source provenance"):
            ensure_development_source_manifest({})

    @staticmethod
    def _pdf_bytes(text: str) -> bytes:
        from reportlab.pdfgen import canvas

        output = BytesIO()
        pdf = canvas.Canvas(output)
        pdf.drawString(72, 720, text)
        pdf.save()
        return output.getvalue()

    def test_pdf_text_layer_is_extracted_and_used_for_source_match(self):
        pdf_bytes = self._pdf_bytes("Observed relay[.]example[.]org as an ORB relay")
        extracted = extract_document_text(pdf_bytes)
        self.assertEqual("pdf", extracted.document_format)
        self.assertEqual("pypdf-text-layer", extracted.text_extractor)
        self.assertEqual(1, extracted.page_count)
        self.assertIn("relay[.]example[.]org", extracted.text)
        document = SourceDocumentRecord(
            document_id="doc-pdf", canonical_url="local://pdf",
            publisher="example.org", title="PDF",
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            published_at_raw="2026-01-01T00:00:00Z",
            published_time_precision=TimePrecision.EXACT_TIMESTAMP,
            source_timezone="UTC",
            retrieved_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            content_sha256="d" * 64, acquisition_mode=AcquisitionMode.EXISTING_CURATED,
            source_access_class=SourceAccessClass.RESTRICTED,
            corpus_purpose=CorpusPurpose.DEVELOPMENT,
        )
        result = verify_indicator_candidates(
            [{"scope": "domain", "raw_form": "relay[.]example[.]org",
              "context": "relay_node", "context_evidence": "ORB relay"}],
            pdf_bytes, document, CtiAgentAdapter(),
            available_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        self.assertEqual(1, len(result.indicators))

    def test_html_prefers_main_content_and_excludes_non_visible_nodes(self):
        html = b"""
        <html><head><style>.x { color: red; }</style>
        <script>const hidden = '198.51.100.10';</script></head>
        <body><nav>navigation.example</nav><main>
        <p>Observed relay[.]badinfra[.]net as an ORB relay.</p>
        <noscript>hidden.example</noscript>
        </main><footer>footer.example</footer></body></html>
        """
        extracted = extract_document_text(html)
        self.assertEqual("stdlib-html-main-content-v2", extracted.text_extractor)
        self.assertIn("relay[.]badinfra[.]net", extracted.text)
        self.assertNotIn("198.51.100.10", extracted.text)
        self.assertNotIn("navigation.example", extracted.text)
        self.assertNotIn("hidden.example", extracted.text)
        self.assertNotIn("footer.example", extracted.text)

    def test_encrypted_pdf_is_rejected_and_ocr_sidecar_is_supported(self):
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        writer.encrypt("secret")
        output = BytesIO()
        writer.write(output)
        with self.assertRaisesRegex(ValueError, "encrypted PDF"):
            extract_document_text(output.getvalue())
        scanned = self._pdf_bytes("")
        extracted = extract_document_text(
            scanned, ocr_sidecar_text="relay[.]example[.]org"
        )
        self.assertTrue(extracted.used_ocr_sidecar)
        self.assertEqual("user-provided-ocr-sidecar", extracted.text_extractor)

    def test_builds_hmac_public_ids_and_conservative_assertions(self):
        document = SourceDocumentRecord(
            document_id="doc-record", canonical_url="https://example.org/record",
            publisher="example.org", title="Record",
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            published_at_raw="2026-01-01T00:00:00Z",
            published_time_precision=TimePrecision.EXACT_TIMESTAMP,
            source_timezone="UTC",
            retrieved_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            content_sha256="c" * 64, acquisition_mode=AcquisitionMode.SYSTEMATIC_PUBLIC,
            source_access_class=SourceAccessClass.PUBLIC,
            corpus_purpose=CorpusPurpose.DEVELOPMENT,
            source_family_id="family-record",
        )
        result = ExtractionResult(
            indicators=(VerifiedIndicator(
                indicator_id="ioc-record", scope="domain", value="relay.example.org",
                raw_form="relay[.]example[.]org", source_document_id="doc-record",
                observed_at="2026-01-01T00:00:00+00:00",
                available_at="2026-01-02T00:00:00+00:00",
                time_basis="observed_in_report", context="relay_node",
                context_evidence="Compromised relay node.",
            ),),
            candidate_count=1, format_rejected=0, source_mismatch_rejected=0,
            future_time_rejected=0, duplicate_count=0,
            source_access_class="public",
            source_acquisition_mode="systematic_public",
            source_corpus_purpose="development",
        )
        indicators, mentions, assertions = build_indicator_records(
            result, document, ingested_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            public_id_hmac_key=b"k" * 32,
        )
        self.assertTrue(indicators[0].public_id.startswith("pub-"))
        self.assertNotIn("relay.example.org", indicators[0].public_id)
        self.assertEqual(IndicatorSensitivity.RESTRICTED, indicators[0].sensitivity)
        self.assertEqual(AssertionRole.RELAY_ORB, assertions[0].role)
        self.assertEqual(mentions[0].mention_id, assertions[0].source_mention_id)
        self.assertEqual("candidate", assertions[0].verdict.value)
        self.assertEqual("pending", assertions[0].reviewer_status.value)

        with tempfile.TemporaryDirectory() as directory:
            registry = CorpusRegistry(Path(directory) / "registry.sqlite")
            family = SourceFamilyRecord(
                source_family_id="family-record",
                canonical_document_id="doc-record",
                reviewer_id="human-reviewer",
                reviewed_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            )
            registry.register_source_family(family, [])
            first = registry.register_indicator_bundle(
                document=document, indicators=indicators, mentions=mentions,
                assertions=assertions,
            )
            replay = registry.register_indicator_bundle(
                document=document, indicators=indicators, mentions=mentions,
                assertions=assertions,
            )
            self.assertEqual(1, first["indicators_inserted"])
            self.assertEqual(0, replay["indicators_inserted"])
            conflicting = indicators[0].model_copy(
                update={"normalized_value": "different.example.org"}
            )
            with self.assertRaises(ValueError):
                registry.register_indicator_bundle(
                    document=document, indicators=[conflicting], mentions=mentions,
                    assertions=assertions,
                )

            with self.assertRaisesRegex(ValueError, "missing accepted review"):
                registry.accepted_pivot_sources(
                    [assertions[0].assertion_id],
                    cutoff_at=datetime(2026, 1, 2, 1, tzinfo=timezone.utc),
                )

            review = AssertionReviewRecord(
                review_id="review-record", assertion_id=assertions[0].assertion_id,
                decision=ReviewerStatus.ACCEPTED, reviewer_id="human-reviewer",
                reviewed_at=datetime(2026, 1, 2, 1, tzinfo=timezone.utc),
                reviewed_role=AssertionRole.RELAY_ORB,
                source_confidence=0.9, extraction_confidence=0.9,
                role_confidence=0.8, notes_hash="f" * 64,
            )
            with self.assertRaisesRegex(ValueError, "role is not queryable"):
                registry.register_assertion_reviews([
                    review.model_copy(update={
                        "review_id": "review-victim",
                        "reviewed_role": AssertionRole.VICTIM,
                    })
                ])
            self.assertEqual(1, registry.register_assertion_reviews([review]))
            accepted = registry.accepted_pivot_sources(
                [assertions[0].assertion_id],
                cutoff_at=datetime(2026, 1, 2, 1, tzinfo=timezone.utc),
            )
            self.assertEqual(AssertionRole.RELAY_ORB, accepted[0].role)
            self.assertEqual("domain", accepted[0].scope)
            with self.assertRaisesRegex(ValueError, "available after cutoff"):
                registry.accepted_pivot_sources(
                    [assertions[0].assertion_id],
                    cutoff_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                )
            rejected_mention = mentions[0].model_copy(update={
                "mention_id": "mention-rejected",
                "raw_form_hash": "e" * 64,
            })
            rejected_assertion = assertions[0].model_copy(update={
                "assertion_id": "assert-rejected",
                "source_mention_id": rejected_mention.mention_id,
            })
            registry.register_indicator_bundle(
                document=document, indicators=indicators,
                mentions=[rejected_mention], assertions=[rejected_assertion],
            )
            registry.register_assertion_reviews([AssertionReviewRecord(
                review_id="review-rejected", assertion_id="assert-rejected",
                decision=ReviewerStatus.REJECTED, reviewer_id="human-reviewer",
                reviewed_at=datetime(2026, 1, 2, 1, tzinfo=timezone.utc),
                reviewed_role=AssertionRole.UNKNOWN,
                source_confidence=0.2, extraction_confidence=0.2,
                role_confidence=0.0, notes_hash="d" * 64,
            )])
            with self.assertRaisesRegex(ValueError, "not accepted"):
                registry.accepted_pivot_sources(
                    ["assert-rejected"],
                    cutoff_at=datetime(2026, 1, 2, 1, tzinfo=timezone.utc),
                )

        with tempfile.TemporaryDirectory() as directory:
            registry = CorpusRegistry(Path(directory) / "registry.sqlite")
            with self.assertRaisesRegex(ValueError, "reviewed source family"):
                registry.register_indicator_bundle(
                    document=document.model_copy(update={"source_family_id": None}),
                    indicators=indicators,
                    mentions=mentions,
                    assertions=assertions,
                )

    def test_ipv4_indicator_map_reads_sqlite_tuple_rows(self):
        indicator = IndicatorRecord(
            indicator_id="ioc-ip", indicator_type=IndicatorType.IPV4,
            normalized_value="198.51.100.7", public_id="pub-fixture",
            first_ingested_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            sensitivity=IndicatorSensitivity.RESTRICTED,
        )
        with tempfile.TemporaryDirectory() as directory:
            registry = CorpusRegistry(Path(directory) / "registry.sqlite")
            registry.register_indicators([indicator])

            self.assertEqual(
                {"198.51.100.7": "ioc-ip"}, registry.ipv4_indicator_id_map()
            )

    def test_date_only_document_cannot_create_exact_time_assertion(self):
        document = SourceDocumentRecord(
            document_id="doc-date", canonical_url="https://example.org/date",
            publisher="example.org", title="Date-only",
            published_at=None, published_at_raw="2026-01-01",
            published_time_precision=TimePrecision.DATE,
            source_timezone="unknown",
            retrieved_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            content_sha256="e" * 64,
            acquisition_mode=AcquisitionMode.SYSTEMATIC_PUBLIC,
            source_access_class=SourceAccessClass.PUBLIC,
            corpus_purpose=CorpusPurpose.DEVELOPMENT,
        )
        result = ExtractionResult(
            indicators=(VerifiedIndicator(
                indicator_id="ioc-date", scope="domain", value="relay.example.org",
                raw_form="relay[.]example[.]org", source_document_id="doc-date",
                observed_at="2026-01-01T00:00:00+00:00",
                available_at="2026-01-02T00:00:00+00:00",
                time_basis="observed_in_report", context="relay_node",
                context_evidence="Relay node.",
            ),),
            candidate_count=1, format_rejected=0, source_mismatch_rejected=0,
            future_time_rejected=0, duplicate_count=0,
            source_access_class="public",
            source_acquisition_mode="systematic_public",
            source_corpus_purpose="development",
        )

        with self.assertRaisesRegex(ValueError, "exact publication timestamp"):
            build_indicator_records(
                result, document,
                ingested_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                public_id_hmac_key=b"k" * 32,
            )

    def test_fail_closed_source_and_time_checks(self):
        document = SourceDocumentRecord(
            document_id="doc-1", canonical_url="https://example.org/report",
            publisher="example.org", title="Report",
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            published_at_raw="2026-01-01T00:00:00Z",
            published_time_precision=TimePrecision.EXACT_TIMESTAMP,
            source_timezone="UTC",
            retrieved_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            content_sha256="a" * 64, acquisition_mode=AcquisitionMode.SYSTEMATIC_PUBLIC,
            source_access_class=SourceAccessClass.PUBLIC,
            corpus_purpose=CorpusPurpose.DEVELOPMENT,
        )
        candidates = [
            {"scope": "domain", "raw_form": "relay[.]example[.]org", "context": "malicious"},
            {"scope": "domain", "raw_form": "absent[.]example[.]org"},
            {"scope": "ip", "raw_form": "192.0.2.5", "observed_at": "2027-01-01T00:00:00Z"},
        ]
        snapshot = b"Infrastructure used relay[.]example[.]org and 192.0.2.5."
        result = verify_indicator_candidates(
            candidates, snapshot, document, CtiAgentAdapter(Path(r"D:\Claude\CTI-Agent")),
            available_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        self.assertEqual(1, len(result.indicators))
        self.assertEqual("relay.example.org", result.indicators[0].value)
        self.assertEqual(1, result.source_mismatch_rejected)
        self.assertEqual(1, result.future_time_rejected)

    def test_injected_extractor_is_followed_by_source_and_time_verification(self):
        class FakeExtractor:
            last_provenance = {"backend": "fixture", "model": "none"}

            def extract(self, document_text):
                self.document_text = document_text
                return [
                    {
                        "scope": "domain", "raw_form": "relay[.]example[.]org",
                        "context": "relay_node", "context_evidence": "relay node",
                        "observed_at": "2026-01-01",
                    },
                    {
                        "scope": "domain", "raw_form": "invented[.]example[.]org",
                        "context": "malicious", "context_evidence": "invented",
                        "observed_at": None,
                    },
                ]

        document = SourceDocumentRecord(
            document_id="doc-2", canonical_url="https://example.org/report-2",
            publisher="example.org", title="Report 2",
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            published_at_raw="2026-01-01T00:00:00Z",
            published_time_precision=TimePrecision.EXACT_TIMESTAMP,
            source_timezone="UTC",
            retrieved_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            content_sha256="b" * 64, acquisition_mode=AcquisitionMode.SYSTEMATIC_PUBLIC,
            source_access_class=SourceAccessClass.PUBLIC,
            corpus_purpose=CorpusPurpose.DEVELOPMENT,
        )
        extractor = FakeExtractor()
        result = extract_and_verify_indicators(
            b"<html><body>Observed relay[.]example[.]org as a relay node.</body></html>",
            document, CtiAgentAdapter(), extractor,
            available_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        self.assertIn("relay[.]example[.]org", extractor.document_text)
        self.assertEqual(1, len(result.indicators))
        self.assertEqual("relay_node", result.indicators[0].context)
        self.assertEqual(1, result.source_mismatch_rejected)
        self.assertEqual("fixture", result.extraction_provenance["backend"])


if __name__ == "__main__":
    unittest.main()
