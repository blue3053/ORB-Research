"""ORB 논문 연구 통제 패키지.

목적: CTI-Agent와 ORB_Hunt_v5의 검증된 로직을 재사용하면서 RQ1∼RQ5에 필요한
provenance, query version, cutoff, 동결 및 재현성 통제를 제공한다.
지원 RQ: 공통 기반으로 RQ1∼RQ5 전체를 지원한다.
보안: passive data만 처리하며 active scan·host interaction·자동 차단 기능을 제공하지 않는다.
"""

__version__ = "0.1.0"

