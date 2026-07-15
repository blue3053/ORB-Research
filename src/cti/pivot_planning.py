"""검증된 CTI IoC를 실행 전 Q0/Q1 계획으로 분류·등록.

목적: IP seed는 Q0, 해석 가능한 non-IP pivot은 Q1로 분리하고 지원되지 않는 scope를 명시적으로 보류한다.
지원 RQ: RQ1∼RQ3 Q0/Q1 관측 및 RQ5 direct-pivot baseline.
재사용 원천: CTI-Agent scope와 ORB_Hunt_v5 cert_sha256·jarm·domain query renderer를 연결한다.
설계: query registry 등록까지만 수행하고 Censys network 실행은 절대 호출하지 않는다.
입력·출력: VerifiedIndicator 목록을 받아 query ID 또는 보류 사유를 포함한 PivotPlan을 반환한다.
시간·provenance 통제: indicator available_at 이전 등록을 금지하고 query version·config hash를 기록한다.
보안·라이선스: raw IP는 Q1 renderer로 보내지 않으며 broad/unsupported pivot은 자동 실행하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import ipaddress
from typing import Any

from src.adapters.orbhunt_censys import OrbhuntCensysAdapter
from src.censys.q0_seed import register_q0_seed
from src.censys.query_registry import QueryRegistry
from src.cti.ioc_extraction import VerifiedIndicator
from src.models import DatasetSplit, QueryClass


Q1_SCOPE_MAP = {"cert": "cert_sha256", "jarm": "jarm", "domain": "domain"}
PIVOTABLE_CONTEXTS = {"malicious", "relay_node"}


@dataclass(frozen=True)
class PivotPlan:
    indicator_id: str
    scope: str
    query_class: str | None
    pivot_type: str | None
    query_id: str | None
    status: str
    reason: str | None


def register_pivot_plans(
    indicators: list[VerifiedIndicator],
    *,
    registry: QueryRegistry,
    censys_adapter: OrbhuntCensysAdapter,
    q1_template_config: dict[str, Any],
    registered_at: datetime,
    query_version: str,
    config_hash: str,
) -> tuple[PivotPlan, ...]:
    """지원 indicator를 Q0/Q1 query로 등록하고 network 미실행 계획을 반환한다."""

    plans: list[PivotPlan] = []
    for indicator in indicators:
        available_at = datetime.fromisoformat(indicator.available_at)
        if indicator.context not in PIVOTABLE_CONTEXTS:
            plans.append(PivotPlan(
                indicator.indicator_id, indicator.scope, None, None, None,
                "blocked", f"context is not pivotable: {indicator.context}",
            ))
            continue
        if indicator.scope == "ip":
            try:
                address = ipaddress.ip_address(indicator.value)
            except ValueError:
                plans.append(PivotPlan(
                    indicator.indicator_id, indicator.scope, QueryClass.Q0_SEED.value,
                    "ip", None, "blocked", "IP value is invalid",
                ))
                continue
            if not address.is_global:
                plans.append(PivotPlan(
                    indicator.indicator_id, indicator.scope, QueryClass.Q0_SEED.value,
                    "ip", None, "blocked", "IP is not globally routable",
                ))
                continue
            query = register_q0_seed(
                registry,
                indicator_id=indicator.indicator_id,
                ip_value=indicator.value,
                indicator_available_at=available_at,
                registered_at=registered_at,
                query_version=query_version,
                config_hash=config_hash,
            )
            plans.append(PivotPlan(
                indicator.indicator_id, indicator.scope, QueryClass.Q0_SEED.value,
                "ip", query.query_id, "registered_not_executed", None,
            ))
            continue
        pivot_type = Q1_SCOPE_MAP.get(indicator.scope)
        if pivot_type is None:
            plans.append(PivotPlan(
                indicator.indicator_id, indicator.scope, None, None, None,
                "unsupported", "scope has no direct Censys Q1 template",
            ))
            continue
        try:
            query_text = censys_adapter.render_q1_direct_pivot(
                pivot_type, indicator.value, q1_template_config
            )
        except ValueError as exc:
            plans.append(PivotPlan(
                indicator.indicator_id, indicator.scope, QueryClass.Q1_DIRECT_PIVOT.value,
                pivot_type, None, "blocked", str(exc),
            ))
            continue
        query = registry.register_query(
            query_version=query_version,
            query_class=QueryClass.Q1_DIRECT_PIVOT,
            query_text=query_text,
            developed_from_split=DatasetSplit.DEVELOPMENT,
            config_hash=config_hash,
            source_indicator_ids=[indicator.indicator_id],
            registered_at=registered_at,
        )
        plans.append(PivotPlan(
            indicator.indicator_id, indicator.scope, QueryClass.Q1_DIRECT_PIVOT.value,
            pivot_type, query.query_id, "registered_not_executed", None,
        ))
    return tuple(plans)
