# Phase D 완료 보고서

## 결과

Phase D Stage 5의 deterministic query composition, clause provenance, bounded development precheck, human query review, immutable budget/schedule과 freeze manifest를 구현했다. Q2/Q3 query는 Phase D manifest 없이 기존 `freeze-query`를 직접 호출해도 frozen 상태로 전이할 수 없다.

## 구현 범위

- `CTI-only`, `CTI+Derived`, `Derived-only`, `Q3 graph expansion` composition을 분리한다.
- query version과 variant를 registry identity에 포함해 primary와 sensitivity query를 별도 ID로 관리한다.
- clause마다 CTI assertion/precheck 또는 reviewed eligible feature, logical role, discovery evidence role, operator, co-occurrence scope, availability를 기록한다.
- 서로 다른 node의 required CTI clause를 자동 AND 결합하지 않는다.
- cutoff 이후 clause, review되지 않은 feature, 실제 catalog와 다른 field/value/availability를 차단한다.
- derived feature query는 Phase C eligibility를 만든 background snapshot을 design에 포함해야 한다.
- bounded precheck의 pending/partial/complete/failed 상태와 page/hit/cost/syntax/broad 판단을 보존하고 `performance_claim_allowed=false`를 강제한다.
- complete·nonzero·bounded precheck와 accepted human review가 모두 있어야 freeze할 수 있다.
- freeze manifest는 canonical query/hash, version/variant, clauses, cutoff, background, API/parser/normalizer/entity version, budget, schedule, review, valid-from을 함께 hash한다.
- frozen version 변경은 차단하고 변경된 version/variant는 새 query identity로 등록한다.
- `query-compose`, `query-register-schedule`, `query-record-precheck`, `query-review`, `query-freeze-designed`, `cti-audit-phase-d` CLI를 추가했다.

## 검증

- deterministic rendering/hash, variant 분리, cutoff leakage, composition mismatch, node-role 충돌을 검증했다.
- background snapshot 누락, bounded budget 초과, review 누락, manifest 없는 freeze를 차단하는 통합 테스트를 추가했다.
- 전체 오프라인 회귀 테스트 96개가 통과했다.
- Python compile, 신규 CLI help와 `git diff --check`를 검증했다.
- 실제 Censys 호출과 운영 데이터베이스 변경은 수행하지 않았다.

## 라우팅과 검토

라우팅은 `lead=Sol/high; builder=Terra/high; reviewer=Sol/xhigh + human gate` 기준을 적용했다. 독립 검토에서 query variant identity, feature-background 연결, 실제 source availability 일치, 기존 freeze 경로 우회 가능성을 재검사하고 보완했다. AI 검토는 사람의 query acceptance를 대체하지 않으며 persisted accepted review만 freeze 조건으로 사용한다.

## 다음 increment

다음 단계는 Phase E Stage 6–7이다. frozen query schedule을 소비하는 due scheduler, prospective observation ledger, per-record observed-time gate, candidate event와 독립 validation evidence·grade history를 구현해야 한다.
