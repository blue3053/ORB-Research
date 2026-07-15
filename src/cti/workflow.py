"""CTI 검색·screening·snapshot artifact를 연결하는 단계형 workflow.

목적: 사람이 검토해야 하는 지점을 유지하면서 각 단계를 불변 artifact와 SQLite provenance로 연결한다.
지원 RQ: RQ1 source selection, RQ4 prospective validation, RQ5 corpus bias audit.
재사용 원천: CTI-Agent의 source isolation과 본 프로젝트 CorpusRegistry를 결합한다.
설계: search와 screening/snapshot을 분리하며 INCLUDE 결정이 없는 후보는 fetch하지 않는다.
입력·출력: protocol·backend·decision을 받아 manifest, screening ledger, SnapshotRecord를 만든다.
시간·provenance 통제: 검색·검토·원문 회수 시각을 서로 다른 필드로 저장한다.
보안·라이선스: 자동 포함을 금지하고 fetcher의 whitelist·passive gate를 우회하지 않는다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from src.cti.corpus_registry import CorpusRegistry
from src.cti.search_execution import (
    SearchBackend,
    SearchExecutionResult,
    execute_search_protocol,
)
from src.cti.snapshots import ImmutableSnapshotStore, PassiveDocumentFetcher, SnapshotRecord
from src.manifests import write_immutable_json
from src.models import ScreeningDecision, SearchProtocolRecord
from src.provenance import sha256_text


@dataclass(frozen=True)
class ManualScreeningInput:
    candidate_id: str
    decision: ScreeningDecision
    reason_code: str
    reviewer_id: str
    notes: str = ""


def run_search_stage(
    *,
    protocol: SearchProtocolRecord,
    expanded_queries: list[str],
    backend: SearchBackend,
    domain_whitelist: list[str],
    registry: CorpusRegistry,
    manifest_path: Path,
    executed_at: datetime,
) -> SearchExecutionResult:
    registry.register_protocol(protocol)
    result = execute_search_protocol(
        protocol, expanded_queries, backend, domain_whitelist, executed_at=executed_at
    )
    write_immutable_json(manifest_path, asdict(result))
    registry.record_search_run(
        search_run_id=result.search_run_id,
        search_protocol_id=protocol.search_protocol_id,
        search_engine=result.backend,
        query_text="\n".join(expanded_queries),
        result_count=len(result.candidates),
        result_manifest_hash=result.result_manifest_hash,
        status="complete",
        executed_at=executed_at,
    )
    return result


def apply_manual_screening(
    *,
    search_result: SearchExecutionResult,
    decisions: list[ManualScreeningInput],
    registry: CorpusRegistry,
    reviewed_at: datetime,
) -> dict[str, ScreeningDecision]:
    candidates = {candidate.candidate_id: candidate for candidate in search_result.candidates}
    if len({decision.candidate_id for decision in decisions}) != len(decisions):
        raise ValueError("duplicate screening decision for candidate")
    decision_map: dict[str, ScreeningDecision] = {}
    for item in decisions:
        if item.candidate_id not in candidates:
            raise KeyError(f"screening candidate not found: {item.candidate_id}")
        candidate = candidates[item.candidate_id]
        screening_id = "screen-" + sha256_text(
            f"{search_result.search_run_id}|{item.candidate_id}|{item.reviewer_id}"
        )[:20]
        registry.record_screening(
            screening_id=screening_id,
            search_run_id=search_result.search_run_id,
            candidate_url=candidate.url,
            decision=item.decision,
            reason_code=item.reason_code,
            reviewer_id=item.reviewer_id,
            reviewed_at=reviewed_at,
            notes=item.notes,
        )
        decision_map[item.candidate_id] = item.decision
    return decision_map


def snapshot_included_candidates(
    *,
    search_result: SearchExecutionResult,
    decision_map: dict[str, ScreeningDecision],
    fetcher: PassiveDocumentFetcher,
    store: ImmutableSnapshotStore,
    retrieved_at: datetime,
) -> tuple[SnapshotRecord, ...]:
    snapshots: list[SnapshotRecord] = []
    for candidate in search_result.candidates:
        if decision_map.get(candidate.candidate_id) is not ScreeningDecision.INCLUDE:
            continue
        snapshots.append(store.store(
            candidate, fetcher.fetch(candidate.url), retrieved_at=retrieved_at
        ))
    return tuple(snapshots)
