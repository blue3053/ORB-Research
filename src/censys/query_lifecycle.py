"""Q0∼Q3 상태 전이와 시간 누수 검사.

목적: query 개발·검증·동결·전향적 시험의 허용 관계를 pure function으로 검증한다.
지원 RQ: RQ3 feature availability, RQ4 prospective discovery, RQ5 fair comparison.
재사용 원천: ORB_Hunt_v5 query review gate를 시간 기반 연구 통제로 확장했다.
설계: dataset split, status transition, cutoff 조건을 fail-closed 검사한다.
입력·출력: QueryRecord·timestamp를 입력받아 검증하거나 ValueError를 발생시킨다.
시간·provenance 통제: available_at과 valid_for_test_from을 엄격히 비교한다.
보안·라이선스: query 실행이나 network 기능은 없다.
"""
from __future__ import annotations

from datetime import datetime

from src.models import DatasetSplit, QueryRecord, QueryStatus, utc


ALLOWED_TRANSITIONS = {
    QueryStatus.DRAFT: {QueryStatus.VALIDATED, QueryStatus.RETIRED},
    QueryStatus.VALIDATED: {QueryStatus.FROZEN, QueryStatus.RETIRED},
    QueryStatus.FROZEN: {QueryStatus.RETIRED},
    QueryStatus.RETIRED: set(),
}


def validate_transition(current: QueryStatus, target: QueryStatus) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"invalid query status transition: {current} -> {target}")


def ensure_features_available(feature_times: list[datetime], cutoff_time: datetime) -> None:
    cutoff = utc(cutoff_time)
    future = [utc(value) for value in feature_times if utc(value) > cutoff]
    if future:
        raise ValueError(f"feature leakage: {len(future)} feature(s) available after cutoff")


def ensure_execution_allowed(
    query: QueryRecord, dataset_split: DatasetSplit, executed_at: datetime, cutoff_time: datetime
) -> None:
    executed = utc(executed_at)
    cutoff = utc(cutoff_time)
    if cutoff > executed:
        raise ValueError("cutoff_time cannot be after executed_at")
    if dataset_split is DatasetSplit.PROSPECTIVE_TEST:
        if query.status is not QueryStatus.FROZEN:
            raise ValueError("prospective-test requires a frozen query")
        if query.frozen_at is None or query.valid_for_test_from is None:
            raise ValueError("frozen query is missing prospective eligibility timestamps")
        if executed < query.valid_for_test_from:
            raise ValueError("query execution predates valid_for_test_from")


def query_supports_rq(query: QueryRecord, rq: str) -> bool:
    mapping = {
        "RQ1": {"Q0_SEED"},
        "RQ2": {"Q0_SEED"},
        "RQ3": {"Q0_SEED", "Q1_DIRECT_PIVOT"},
        "RQ4": {"Q2_DERIVED", "Q3_CLUSTER"},
        "RQ5": {"Q1_DIRECT_PIVOT", "Q2_DERIVED", "Q3_CLUSTER"},
    }
    return query.query_class.value in mapping.get(rq.upper(), set())

