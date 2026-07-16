# Phase E 완료 보고서

## 결과

Phase E Stage 6–7의 전향적 관측 기회, 레코드별 시간 판정, canonical candidate,
독립 validation evidence, 사람 판정과 append-only 등급 이력을 구현했다. 실제 Censys나
외부 CTI 호출 없이 fixture와 임시 SQLite만 사용해 검증했다.

## Stage 6 구현

- 동결된 query identity, freeze manifest와 budget schedule이 일치해야 scheduler가 동작한다.
- query version과 entity epoch별 관측 기회를 deterministic ID로 생성한다.
- 실행 상태는 `due`, `missed`, `late`, `partial`, `failed`, `complete`로 구분한다.
- 실행 시각만으로 전향성을 인정하지 않고 각 host record의 `observed_at`을
  `valid_for_test_from`과 비교한다.
- 동결 이전 레코드는 `pre_freeze`, 기저 시각이 없으면
  `prospective_time_unresolved`로 보존한다.
- frozen query hash, prospective-test split, raw manifest SHA-256와 frozen API schema를
  event 등록 시 재검증한다.
- candidate ID는 query ID/version/hash와 entity epoch로 결정하며 최초 발견 시각과 최초
  event는 덮어쓰지 않는다.

## Stage 7 구현

- query match와 사용 feature/source family는 discovery provenance로만 기록한다.
- validation/contradiction evidence는 discovery feature 재사용, 동일 source family,
  후보 발견 이전 evidence를 사용할 수 없다.
- evidence 가용성과 지지 방향을 보존하고 `positive`, `negative`, `contradicted`,
  `unresolved`, `unobservable`을 보수적으로 산출한다.
- 최종 판정자는 implementation agent와 달라야 하며 reason code와 사용 evidence ID를
  기록해야 한다.
- grade 변경은 이전 grade event를 가리키는 append-only chain으로만 추가한다.

## CLI와 설정

추가 명령:

- `phase-e-schedule`
- `phase-e-register-event`
- `phase-e-register-evidence`
- `phase-e-adjudicate`
- `phase-e-grade`
- `cti-audit-phase-e`

`configs/base.yaml`의 `phase_e_policy`는 observed-time, raw manifest, evidence 독립성,
별도 사람 판정자와 등급 이력 불변 조건을 fail-closed 기본값으로 선언한다.

## 검증

- 전체 테스트: `112 passed`
- Phase E 신규 테스트: scheduler 경계·missed·partial/late, host/service별 pre-freeze·unresolved time,
  rerun deduplication, 동일 family/discovery reuse/pre-candidate 차단, conflict/unobservable,
  partial→complete 이력, 사람 판정자 분리와 grade chain
- `python -m compileall -q src`: 통과
- `python -m src.cli --help`: Phase E 명령 노출 확인
- live Censys/CTI 호출: 없음

## 다음 단계

다음 단계는 Phase F Stage 8이다. 동결된 Phase E candidate/adjudication cohort를 입력으로
RQ1–RQ5 analyzer, leakage audit, 결측·민감도 보고와 manifest 기반 재현 패키지를 구현한다.
