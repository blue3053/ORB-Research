# CTI/Censys Stage 0-8 구현 플레이북

## 목차

1. [사용법과 기준선](#사용법과-기준선)
2. [Phase 개관](#phase-개관)
3. [Stage 0 — Protocol과 corpus 고정](#stage-0--protocol과-corpus-고정)
4. [Stage 1 — CTI 정규화와 assertion](#stage-1--cti-정규화와-assertion)
5. [Stage 2 — Q0 landmark와 continuity](#stage-2--q0-landmark와-continuity)
6. [Stage 3 — Q1 precheck와 CTI-only enrichment](#stage-3--q1-precheck와-cti-only-enrichment)
7. [Stage 4 — Derived feature와 background](#stage-4--derived-feature와-background)
8. [Stage 5 — Query compose, review, freeze](#stage-5--query-compose-review-freeze)
9. [Stage 6 — Prospective 반복 관측](#stage-6--prospective-반복-관측)
10. [Stage 7 — 독립 evidence와 grade](#stage-7--독립-evidence와-grade)
11. [Stage 8 — RQ 분석 데이터](#stage-8--rq-분석-데이터)
12. [검증 순서와 인계](#검증-순서와-인계)

## 사용법과 기준선

설계도 `2026-07-15-CTI-Censys-파이프라인-구현-설계도.md`가 권위 있는 목표다. 이 문서는 작업 순서와 2026-07-15 코드 기준선을 압축한 실행 보조 자료다. 매 invocation에서 실제 파일·CLI·테스트·schema를 다시 감사하고, “존재”, “부분 구현”, “완료”를 구분하라.

2026-07-15 offline 기준선은 56개 테스트 통과다. 현재 재사용점은 다음과 같다.

| 영역 | 재사용 가능한 기반 | 주요 공백 |
|---|---|---|
| CTI | corpus registry, search/import/screen/snapshot, IOC extraction, pivot planning | source family, 시간 정밀도, assertion review, node timeline |
| Censys | Q0 등록, query lifecycle, paginated collection, cached normalization, fingerprint graph | continuity, Q1 precheck/eligibility, 필드 보존 완성, resume 통합 |
| Query | register/validate/freeze, hash, prospective 실행 gate | composer, clause/feature provenance, freeze manifest, cutoff wiring |
| Prospective | live env gate, immutable page, append checkpoint, execution ledger | scheduler, record-level time gate, candidate ledger, evidence history |
| Analysis | `query_supports_rq`, `RunManifest` skeleton | Stage 7 validation과 Stage 8 analyzer 대부분 |

`src/features`, `src/entities`, `src/assessments`, `src/prospective`, `src/analysis`는 기준선에서 아직 없다. 새 Python 경로가 필요하면 저장소의 날짜 접두사 규칙과 import 가능 이름이 충돌하므로 파일을 만들기 전에 명시적 이름 예외 승인을 요청하라.

## Phase 개관

| Phase | Stage | 목적 | 완료 게이트 |
|---|---|---|---|
| A | 0-1 | schema, 시간, provenance, evidence-role 계약 | live 없이 상태 전이와 fail-closed validator를 fixture로 증명 |
| B | 2-3 | Q0/Q1 bootstrap | 모든 seed/pivot에 `derived_pivot_allowed` 또는 차단 사유 존재 |
| C | 4 | feature/background | 적격 feature의 분자·분모·cutoff·source 재생 가능 |
| D | 5 | composer/freeze | 모든 미래 query에 immutable hash·budget·schedule·valid-from 존재 |
| E | 6-7 | prospective/validation | 중복 후보 없음, raw/checkpoint/evidence/grade history 감사 가능 |
| F | 8 | RQ analysis | 표·그림이 run manifest와 input/output hash로 역추적 가능 |

Phase F scaffold는 일찍 만들 수 있어도 RQ4-RQ5 결과 산출은 Phase E의 전향 데이터와 독립 판정 뒤에만 허용하라.

## Stage 0 — Protocol과 corpus 고정

**Entry gate**

- 연구 cutoff, source 포함·제외, 공개/제한 등급, 수집 모드가 명시되어야 한다.
- 기존 `src/cti/search_protocol.py`, `search_execution.py`, `workflow.py`, `snapshots.py`, `corpus_registry.py`, `src/manifests.py`를 먼저 감사하라.

**최소 구현 순서**

1. source document의 원문 hash, canonical URL, publisher, `published_at`, `retrieved_at`, precision, timezone, acquisition mode를 계약으로 고정하라.
2. `source_relationships`와 `source_families`로 원문·재게시·번역·요약 계보를 표현하라.
3. public/restricted 자료에 논리적 또는 물리적 접근 경계와 export 경계를 적용하라.
4. 같은 source family가 독립 근거로 중복 집계되지 않는 red test를 추가하라.

**집중 검증**

- 기존 `test_cti_workflow.py`, `test_cti_search_execution.py`, `test_cti_cli_pipeline.py`와 새 provenance/source-family fixture를 실행하라.
- date-only를 UTC 자정의 확정 시각으로 바꾸는 `_published_datetime` 경로를 반드시 점검하라.

**Exit gate / 정지 조건**

- source·공개일·정밀도·원문 hash가 없으면 assertion 생성을 차단하라.
- prospective CTI가 development corpus에 섞이거나 게시일을 수집일로 대체하면 실패다.

**라우팅**: 정책은 R3, registry/hash는 R1, provenance 독립 감사는 R4와 human protocol owner.

## Stage 1 — CTI 정규화와 assertion

**Entry gate**

- Stage 0 문서가 immutable source identity와 시간 정밀도를 가져야 한다.
- `src/cti/ioc_extraction.py`, `src/adapters/cti_agent.py`, `src/reused/cti_agent/ioc_regex.py`, `CorpusRegistry` assertion schema를 감사하라.

**최소 구현 순서**

1. IP Seed와 non-IP CTI direct pivot을 분리하고 domain, cert/SPKI, SSH key, JARM/JA4, port/protocol, HTTP/device hint의 extraction scope를 명시하라.
2. indicator, assertion, source mention을 별도 identity로 만들고 한 indicator가 여러 문서·assertion을 가질 수 있게 하라.
3. node role을 `relay/ORB`, `controller`, `staging`, `C2`, `scanner`, `victim`, `unknown` 등으로 보존하고, sinkhole은 격리·제외 상태로 처리하며 queryable 역할만 승격하라.
4. `source_confidence`, `extraction_confidence`, `role_confidence`와 `accepted/rejected/pending` review를 분리하라.
5. `available_at <= cutoff`와 evidence-role validator를 query 등록·review·freeze 경로에 연결하라.

**집중 검증**

- 기존 `test_cti_ioc_extraction.py`, `test_cti_adapter.py`를 유지하라.
- date precision, 동일 indicator 다중 source, victim 차단, pending 차단, 미래 assertion 차단 fixture를 추가하라.
- `publication_date_fallback`이 관측시각을 꾸며내지 않는지 점검하라.

**Exit gate / 정지 조건**

- `accepted`가 아니거나 역할·시간·source가 모호한 assertion은 Q0/Q1 등록에 사용하지 마라.
- schema migration과 기존 SQLite/flat CLI 호환 전략이 없으면 Stage 2 확장을 시작하지 마라.

**라우팅**: 일반 normalization은 R1, schema/time/evidence 계약은 R3-R4, acceptance는 human.

## Stage 2 — Q0 landmark와 continuity

**Entry gate**

- 허용된 Seed assertion과 cutoff-safe source가 있어야 한다.
- `src/censys/q0_seed.py`, `paginated_collection.py`, `query_registry.py`, `src/adapters/orbhunt_censys.py`를 재사용하라.

**최소 구현 순서**

1. cached result가 cert/SPKI, SSH key, JARM/JA4, software, banner/title과 각 `observed_at`을 모델까지 보존하는지 확인하라.
2. Q0 observation을 append-only timeline으로 materialize하고 landmark reason과 observation window를 기록하라.
3. continuity를 `continuous`, `probable`, `unknown`, `reassigned`, `contradicted` 상태와 근거로 평가하라.
4. 현재 응답, historical evidence, missing scan, last positive를 각각 분리하라.
5. `derived_pivot_allowed`를 continuity 판정과 human override에 연결하라.

**집중 검증**

- 기존 `test_q0_seed.py`, `test_paginated_collection.py`, `test_censys_adapter.py`, `test_cli_integration.py`를 유지하라.
- normalizer 필드 보존, out-of-order timeline, missing scan, probable approval, unknown 차단 fixture를 추가하라.

**Exit gate / 정지 조건**

- historical evidence가 없으면 `unknown`이다. 현재 응답만으로 continuity를 선언하지 마라.
- `continuous` 또는 사전 등록 기준을 통과하고 review된 `probable`만 derived source가 될 수 있다.
- 마지막 양성일을 소멸일로 변환하면 실패다.

**라우팅**: observation/timeline은 R2, fixture는 R0, continuity 의미와 검토는 R3-R4.

## Stage 3 — Q1 precheck와 CTI-only enrichment

**Entry gate**

- accepted CTI assertion, node role, `available_at <= cutoff`, queryable non-IP pivot가 있어야 한다.
- `src/cti/pivot_planning.py`, renderer/safety, pagination/checkpoint를 재사용하라.

**최소 구현 순서**

1. 각 singleton pivot에 bounded precheck를 실행하고 hit 분포, page budget, complete/partial 상태를 artifact로 남겨라.
2. broad/shared, zero-hit, unsupported, restricted, role-conflict 사유를 명시하라.
3. 같은 node·같은 시간창·호환 역할의 CTI-only composite만 생성하라.
4. pagination을 끝까지 수집하거나 `pending/partial_max_pages/failed`로 보존하라.
5. pivot eligibility table과 human review를 Q2 source gate에 연결하라.

**집중 검증**

- 기존 `test_pivot_planning.py`, `test_pivot_safety.py`, `test_paginated_collection.py`를 유지하라.
- singleton budget, incompatible-role AND 차단, available-at gate, resume, partial prevalence 차단 fixture를 추가하라.
- CLI partial run을 같은 run ID로 재개해 complete가 되는 통합 경로를 점검하라.

**Exit gate / 정지 조건**

- `pending`과 `partial_max_pages`는 Q2 source가 아니다.
- precheck는 성능 평가가 아니며 Q1 hit는 campaign cohort가 아니다.
- 0-hit를 infrastructure death로 해석하지 마라.

**라우팅**: planner/pagination/idempotency는 R2, 조합 의미는 R3, query-semantics 독립 검토는 R4.

## Stage 4 — Derived feature와 background

**Entry gate**

- Stage 2-3의 eligible Q0/Q1 observation이 있고, 동일 cutoff를 적용할 reference sampling frame과 snapshot 정책이 동결되어야 한다.
- 기존 `derive_fingerprint_graph()`와 fingerprints/entity relations를 재사용하되 완성된 feature catalog로 간주하지 마라.

**최소 구현 순서**

1. feature catalog와 observation identity를 정의하고 source query/host/service/time을 추적하라.
2. cert/SPKI, SSH, JARM/JA4, HTTP/device/software feature extractor를 안정성·queryability 계약과 함께 구현하라.
3. entity resolution과 epoch를 raw observation과 분리하라.
4. matched reference/background를 protocol, port, product, time window 등 사전 정의 strata로 구성하라.
5. anchor numerator, observable denominator, background rate, uncertainty, cutoff, source를 feature statistics로 저장하라.
6. stability, uniqueness, support, shared/default, cutoff 기준으로 query eligibility를 평가하라.

**집중 검증**

- deterministic feature ID, 동일 관측 재실행, epoch split/merge, unavailable feature, matched-background denominator, shared/default 차단 fixture를 추가하라.
- 필요 지표가 확정되기 전 통계 dependency를 무분별하게 추가하지 마라.

**Exit gate / 정지 조건**

- 분자·분모·background·cutoff·source를 재생할 수 없는 feature를 query-eligible로 승격하지 마라.
- `available_at > cutoff`, unstable field, shared/default feature는 fail-closed다.
- matched background 없이 `global rarity`를 주장하지 마라.

**라우팅**: 통계·background 설계는 R3-R4, extractor/graph/statistics 구현은 R2, 반복 fixture는 R0.

## Stage 5 — Query compose, review, freeze

**Entry gate**

- accepted CTI clause와 eligible derived feature에 provenance, cutoff, availability가 있어야 한다.
- `src/censys/query_registry.py`, `query_lifecycle.py`, generic register/validate/freeze CLI를 재사용하라.

**최소 구현 순서**

1. query, version, variant, clause, feature, dataset split을 분리하라.
2. `CTI-only`, `CTI+Derived`, `Derived-only`, `Q3 graph expansion`을 같은 composition contract로 생성하라.
3. `ensure_features_available()` 같은 cutoff validator를 register, review, freeze의 실제 경로에 연결하라.
4. bounded precheck 결과와 review artifact를 저장하되 campaign validation과 상태명을 혼동하지 마라.
5. canonical query/hash, source hashes, cutoff, budget, schedule, validation family, `valid_for_test_from`을 freeze manifest에 기록하라.

**집중 검증**

- deterministic render/hash, unavailable clause 차단, variant separation, review-state separation, frozen mutation 차단, new-version 허용 fixture를 추가하라.
- 기존 flat CLI는 호환 alias로 유지할지 명시적으로 결정하라.

**Exit gate / 정지 조건**

- 필수 provenance·hash·cutoff·budget·schedule·valid-from 중 하나라도 없으면 freeze하지 마라.
- frozen version은 수정하지 말고 새 version만 만들라.
- human query review 전 prospective schedule을 활성화하지 마라.

**라우팅**: policy/freeze 의미는 R3-R4, composer/registry는 R2, hash/manifest fixture는 R0, 최종 review는 human.

## Stage 6 — Prospective 반복 관측

**Entry gate**

- frozen query version, valid hash, dataset split, schedule, budget, `valid_for_test_from`이 있어야 한다.
- 기존 live env gate, immutable raw page, append checkpoint, execution ledger를 재사용하라.

**최소 구현 순서**

1. due scheduler와 missed/late/partial/failed 상태를 명시하라.
2. query version과 entity epoch별 observation opportunity를 생성하라.
3. collector resume와 execution ledger의 identity/status 전이가 idempotent한지 통합 검증하라.
4. API `executed_at`뿐 아니라 각 result의 기저 `observed_at >= valid_for_test_from`을 검사하라.
5. 불명확한 schema/time은 `prospective_time_unresolved`로 격리하라.
6. 처음 등장한 frozen query identity(`query_id` 또는 `query_hash`)–`entity_epoch_id` 쌍에 candidate를 하나 생성하라. query identity–version–entity epoch–execution/observed-time identity로 observation event를 append하고 재실행 중복을 막아라.

**집중 검증**

- due boundary, missed run, schema drift, observed-before-freeze, duplicate rerun, partial-resume-complete, raw/checkpoint immutability fixture를 추가하라.
- 기본 검증은 cached page와 임시 DB만 사용하라.

**Exit gate / 정지 조건**

- frozen 상태·hash·split·execution/observed time·raw manifest가 모두 유효해야 prospective 평가에 포함하라.
- live 실행은 명시적 사용자 승인, `ALLOW_LIVE_CENSYS=1`, 비용/page budget을 별도로 요구한다.

**라우팅**: scheduler/collector/ledger는 R2, schema/time ambiguity는 R3-R4, live는 human operations gate.

## Stage 7 — 독립 evidence와 grade

**Entry gate**

- immutable candidate event, `first_candidate_at`, discovery feature set, source-family graph가 있어야 한다.

**최소 구현 순서**

1. discovery evidence와 validation evidence의 role을 schema와 validator로 분리하라.
2. 검색에 쓰지 않은 cert/SPKI/SSH/domain, 후보 이후 미래 독립 CTI, malware/IR, 독립 공급자 확인, 반증을 provenance와 source family로 저장하라.
3. 동일 source family 재게시와 discovery feature 재사용을 독립 검증에서 제외하라.
4. `positive`, `negative`, `contradicted`, `unresolved`, `unobservable` adjudication과 reason을 보존하라.
5. grade 변경을 overwrite하지 말고 append-only history로 기록하라.

**집중 검증**

- discovery reuse, same-family duplicate, pre-candidate evidence, unresolved preservation, conflicting evidence, grade history fixture를 추가하라.
- 재사용 parser의 run-scoped `candidate_id`는 source-event provenance로 보존하라. canonical ledger ID는 `query_id` 또는 `query_hash`와 `entity_epoch_id`를 포함한 별도 결정적 identity로 생성하라.

**Exit gate / 정지 조건**

- 독립성 또는 시간 조건이 불명확하면 `unresolved`다.
- query match 자체를 campaign membership이나 validation evidence로 사용하지 마라.
- human adjudicator와 구현 agent를 분리하라.

**라우팅**: evidence/grading 정책은 R3-R4, storage/history는 R2, 반복 ingest는 R0, 판정은 human.

## Stage 8 — RQ 분석 데이터

**Entry gate**

- RQ1은 Seed/landmark registry, RQ2는 사전 cohort와 반복 timeline, RQ3는 feature/background snapshot이 있어야 한다.
- RQ4-RQ5는 frozen query, complete하고 prospective-time eligible한 execution/record, candidate/evidence/grade history, cohort contract가 있어야 한다. missed/partial/failed execution은 observation-opportunity와 운영 실패로 별도 보고하고 성능 입력으로 사용하지 마라.
- `query_supports_rq()`와 `RunManifest` skeleton을 재사용하되 analyzer 구현으로 간주하지 마라.

**최소 구현 순서**

1. RQ별 estimand, cohort, denominator, timeline basis, censoring, unresolved 정책을 명시하라.
2. RQ1-3의 corpus·feature·continuity 기술 통계를 먼저 구현하라.
3. RQ4의 prospective yield와 validation 결과는 complete하고 prospective-time eligible한 실행만으로 계산하라. missed/partial/failed 상태는 운영·관측기회 지표로 분리하라.
4. RQ5 M1-M5를 같은 cutoff, universe, schedule, alert budget과 API/query cost 조건으로 비교하라.
5. uncertainty, sensitivity, failure slice를 산출하고 ground truth 범위 밖 claim을 차단하라.
6. code/config/input/output hash를 가진 run manifest와 public/restricted export를 생성하라.

**집중 검증**

- fixed cohort, zero denominator, unresolved, RQ2 interval censoring, partial/failed 성능 제외, same-budget comparison, leakage sentinel, deterministic export fixture를 추가하라.
- 전체 ground truth가 없으면 일반 recall 대신 관측 가능한 제한된 metric만 보고하라.

**Exit gate / 정지 조건**

- cohort·분모·timeline·unresolved 정책이 없으면 표를 만들지 마라.
- leakage audit 실패 시 RQ4-RQ5를 즉시 중단하라.
- 모든 표·그림은 manifest와 hash로 역추적 가능해야 한다.

**라우팅**: estimand/metric/claim은 R3-R4, analyzer는 R2, 표·재현 패키지는 R0, 필요 시 human statistician.

## 검증 순서와 인계

### 테스트 순서

1. pure-function red test
2. 대상 module fixture
3. CLI dry-run 또는 임시 DB integration
4. cross-stage leakage/freeze/idempotency audit
5. 전체 offline regression

대형 suite나 live test부터 시작하지 마라. 실제 `data_registry/*.sqlite`, `data/raw`, credential을 검증 fixture로 사용하지 마라.

### 공통 인계 체크리스트

- observable outcome과 Stage/Phase를 적었는가?
- entry gate와 exit gate를 코드·테스트 근거로 판정했는가?
- 사용한 모델·effort·reviewer를 적었는가?
- 생성·수정 파일을 절대 경로로 모두 나열했는가?
- partial, unresolved, unverified를 성공으로 꾸미지 않았는가?
- 다음 increment를 하나만 제안했는가?

### 즉시 사람에게 올릴 조건

- schema/time 의미가 Phase 경계를 넘어 바뀐다.
- frozen query 수정, live 수집, 비용, credential, restricted/victim 데이터가 관련된다.
- discovery/validation evidence가 겹친다.
- 설계도와 코드·테스트가 충돌한다.
- 독립 reviewer가 누수 또는 claim 타당성에 동의하지 않는다.
