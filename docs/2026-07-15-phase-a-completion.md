# Phase A 완료 보고

## 결과

Phase A의 Stage 0 protocol/corpus gate와 Stage 1 CTI assertion acceptance gate를
offline 구현으로 완료했다.

### Stage 0

- research cutoff, access class, acquisition mode를 protocol hash에 고정한다.
- publication 원문, precision, timezone, retrieved time, 원문/OCR hash를 보존한다.
- date-only publication을 정확한 UTC timestamp로 승격하지 않는다.
- 사람이 검토한 source family와 재게시·번역·요약 계보를 보존한다.
- prospective-validation 자료의 development 역삽입과 restricted public export를 차단한다.

### Stage 1

- indicator identity를 문서 identity에서 분리해 동일 indicator가 여러 source mention과
  assertion을 가질 수 있다.
- source mention, assertion, immutable human review를 별도 identity와 SQLite table로
  저장한다.
- relay/ORB, controller, staging, C2, scanner, victim, sinkhole, unknown 역할을 보존한다.
- source, extraction, role confidence를 분리한다.
- accepted/rejected/pending 경계를 분리하고 accepted human review만 pivot source가 된다.
- victim, sinkhole, unknown 역할과 pending/rejected assertion은 Q0/Q1에서 차단된다.
- domain, cert/SPKI, SSH key, JARM/JA4, port/protocol, HTTP/device hint scope를 extraction
  contract에 포함한다.
- assertion `available_at`을 query registration, validation, freeze, execution cutoff에
  연결한다.
- 과거 flat verified manifest와 수동 Q0/Q1 CLI 등록은 acceptance provenance가 없으면
  fail-closed로 거부한다.

## SQLite 호환 전략

- 기존 table과 payload를 삭제하거나 추정 값으로 덮어쓰지 않는다.
- `indicator_assertions`와 `query_registry`에는 nullable/default additive column만 추가한다.
- 기존 payload에 source mention, review, source availability가 없으면 Phase A audit issue로
  보고한다.
- 새 query에는 `source_assertion_ids`와 `source_available_at`을 보존한다.

## CLI

- `cti-review-assertions`
- `cti-plan-pivots --accepted-assertions ... --cutoff-at ...`
- `register-q0 --source-assertion-id ... --cutoff-at ...`
- `cti-audit-phase-a`

`register-query`를 이용한 Q0/Q1 우회 등록은 거부한다.

## Gate

- 구현 gate: passed
- 전체 offline regression: passed
- CLI parser/help: passed
- diff whitespace 검사: passed
- production/운영 DB audit: unverified — 운영 DB는 대상으로 실행하지 않았다.
- independent reviewer audit: unverified — 독립 reviewer가 배정되지 않았다.
- human assertion acceptance: fixture reviewer로 경계만 검증했으며 실제 연구 판단을
  자동 생성하지 않았다.
- live CTI/Censys 실행: 수행하지 않음

## 검증 범위

- 동일 indicator의 다중 source identity
- date-only publication의 exact assertion 차단
- SPKI extraction과 sinkhole 역할 보존
- pending, rejected, victim/sinkhole/unknown 차단
- accepted assertion의 Q1 등록
- assertion availability 이후 cutoff에서만 query 등록·실행 허용
- 기존 SQLite query table additive migration
- source-family 및 public/restricted export 회귀
- 전체 CLI search → snapshot → extraction → registration → review → pivot 흐름

## 변경 파일

- `README.md`
- `configs/base.yaml`
- `configs/2026-07-14-ioc-extract-v1.md`
- `configs/2026-07-14-ioc-extract-strict-scope-v1.md`
- `src/models.py`
- `src/config.py`
- `src/cli.py`
- `src/cti/search_protocol.py`
- `src/cti/search_execution.py`
- `src/cti/snapshots.py`
- `src/cti/corpus_registry.py`
- `src/cti/ioc_extraction.py`
- `src/cti/pivot_planning.py`
- `src/censys/q0_seed.py`
- `src/censys/query_registry.py`
- `src/censys/query_lifecycle.py`
- 관련 offline test 파일

## 다음 increment

다음 단계는 Phase B Stage 2 Q0 landmark/continuity다. Cached host/service 결과의
fingerprint와 observation time을 보존하고, append-only Q0 timeline 위에서
continuous/probable/unknown/reassigned/contradicted를 판정하며 review된 continuity만
derived pivot source로 허용해야 한다.
