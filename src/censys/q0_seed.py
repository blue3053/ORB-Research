"""CTI IP seed를 Censys Q0 exact-host query로 등록하는 빌더.

목적: 알려진 ORB IP의 상태 관측을 후보 탐색과 분리된 Q0 query로 재현 가능하게 등록한다.
지원 RQ: RQ1 landmark, RQ2 반복 cohort, RQ3 특징 원재료.
재사용 원천: ORB_Hunt_v5 query hash·registry 관례와 Censys CenQL exact field syntax를 결합한다.
설계: 표준 IP만 허용하고 한 query는 한 indicator만 참조하며 Q0를 development-origin으로 명시한다.
입력·출력: indicator metadata와 QueryRegistry를 받아 등록된 QueryRecord를 반환한다.
시간·provenance 통제: indicator available_at 이후에만 등록할 수 있다.
보안·라이선스: Q0 원문은 내부 registry에만 저장하고 공개 export 대상에서 제외한다.
"""
from __future__ import annotations

from datetime import datetime
from ipaddress import ip_address

from src.censys.query_registry import QueryRegistry
from src.models import DatasetSplit, QueryClass, QueryRecord, utc


def render_q0_seed_query(ip_value: str) -> str:
    """CenQL exact equality를 사용한 단일 host IP query를 만든다."""

    normalized = str(ip_address(ip_value.strip()))
    return f"host.ip = {normalized}"


def register_q0_seed(
    registry: QueryRegistry,
    *,
    indicator_id: str,
    ip_value: str,
    indicator_available_at: datetime,
    source_assertion_id: str,
    cutoff_at: datetime,
    registered_at: datetime,
    query_version: str,
    config_hash: str,
) -> QueryRecord:
    available = utc(indicator_available_at)
    registered = utc(registered_at)
    cutoff = utc(cutoff_at)
    if not source_assertion_id.strip():
        raise ValueError("Q0 source_assertion_id is required")
    if available > cutoff:
        raise ValueError("Q0 source assertion is available after cutoff")
    if registered < available:
        raise ValueError("Q0 query cannot be registered before its source indicator is available")
    return registry.register_query(
        query_version=query_version,
        query_class=QueryClass.Q0_SEED,
        query_text=render_q0_seed_query(ip_value),
        developed_from_split=DatasetSplit.DEVELOPMENT,
        config_hash=config_hash,
        source_indicator_ids=[indicator_id],
        source_assertion_ids=[source_assertion_id],
        source_available_at=available,
        registered_at=registered,
    )
