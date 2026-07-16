# Phase B 완료 보고서

## 범위와 결과

Phase B Stage 2–3의 구현과 오프라인 검증을 완료했다. Q0 관측은 landmark 기준의 append-only 시간축으로 보존되며, 연속성은 `continuous`, `probable`, `unknown`, `reassigned`, `contradicted`로 구분된다. 현재 응답, 과거 양성 관측, 미관측 스캔, 마지막 양성 시각은 서로 다른 필드로 유지한다.

Q1 pivot은 실행 전 bounded precheck 정의를 등록하고 실행 결과를 `pending`, `partial_max_pages`, `complete`, `failed`로 보존한다. `partial_max_pages` 실행은 동일 run ID에서 카운터가 감소하지 않는 경우에만 재개할 수 있다. broad/shared, unsupported, restricted, role conflict, partial, failed, zero-hit 결과는 Q2 근거로 승격되지 않는다. complete·nonzero 결과도 사람의 accepted review 없이는 Q2 등록이 차단된다.

## 주요 구현

- Censys service 관측에서 cert/SPKI, SSH key, JARM/JA4, software, banner/title와 개별 `observed_at`을 보존한다.
- Q0 landmark, timeline, continuity assessment/review를 SQLite에 불변 레코드로 저장한다.
- CTI-only composite는 같은 node와 time window, 호환되는 역할을 요구한다.
- precheck 정의·결과·검토와 execution event history를 저장한다.
- `register-q2`는 현재 eligible precheck ID만 허용한다.
- `cti-audit-phase-b`는 저장 payload, Q0 landmark 누락, accepted-but-ineligible precheck, Q2 provenance를 감사한다.
- 기본 설정에 `phase_b_policy`를 추가했다.

## 검증

- 집중 테스트: continuity, precheck, query registry, adapter, pagination 통과.
- 전체 오프라인 회귀 테스트: 80개 통과.
- Python bytecode compile 검사와 28개 CLI command parser 로딩 통과.
- 실제 Censys 네트워크 호출은 수행하지 않았다.

## 남은 경계와 다음 단계

Phase C Stage 4가 다음 단계다. cutoff 이전 데이터만 사용하는 feature/background materialization, 분자·분모와 source lineage, feature availability를 구현하고, 해당 feature에서 생성되는 Q2 provenance를 현재 Phase B 게이트와 연결해야 한다.
