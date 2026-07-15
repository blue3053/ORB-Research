# CTI 기반 Censys 캠페인 후보 추적 파이프라인 구현 설계도

| 항목 | 내용 |
|---|---|
| 문서 버전 | 1.2 |
| 기준일 | 2026-07-15 |
| 목적 | CTI의 기존 pivot과 Censys enrichment에서 얻은 신규 특징을 이용하여 캠페인 후보 노드를 전향적으로 추적하는 재현 가능한 파이프라인 구현 |
| 적용 연구질문 | RQ1∼RQ5 |
| 상위 문서 | `2026-07-13-ORB-논문-구성안.md`, `ORB-논문-구현-설계서.md` |
| 시각 자료 | `2026-07-15-CTI-Censys-파이프라인-도식.md`, `2026-07-15-CTI-Censys-파이프라인-한눈에보기.png` |
| 핵심 원칙 | 검색 일치, 기술적 유사성, 캠페인 연계를 분리하고 동결 이후 결과만 전향적 성능으로 계산 |

---

## 1. 설계 결정 요약

본 파이프라인의 목적은 Censys 쿼리 자체를 최적화하는 것이 아니다. CTI를 anchor로 사용하여 기존 IP 이외의 지속 가능한 인프라 특징을 발굴하고, 그 특징으로 생성한 동결 쿼리가 미래 캠페인 후보를 얼마나 안정적으로 포착하는지 측정하는 것이 목적이다.

최종 처리 흐름은 다음으로 고정한다.

```text
CTI corpus protocol과 원문 등록
→ CTI indicator·assertion 정규화
→ Q0 exact-IP landmark 및 continuity 평가
→ Q1 CTI direct-pivot 적격성 precheck
→ Q1 CTI-only 복합 enrichment
→ Q0/Q1 결과에서 derived feature 후보 생성
→ anchor support와 matched-background rarity 평가
→ CTI-only / CTI+Derived / Derived-only / Q3 쿼리 개발
→ bounded development precheck와 수동 검토
→ query·threshold·budget·schedule 동결
→ 미래 Censys snapshot 반복 수집
→ append-only candidate ledger 생성
→ 검색에 사용하지 않은 미래 독립 증거로 등급 갱신
→ RQ1∼RQ5별 서로 다른 분석 데이터셋 생성
```

이 설계에서 지켜야 할 해석 경계는 다음과 같다.

- Q0/Q1에서 새로 발견한 값은 우선 `derived feature candidate`이다.
- 동결 쿼리와 일치한 host는 우선 `technical similarity candidate`이다.
- 동일 쿼리에 반복해서 일치하는 사실은 persistence evidence이지 campaign validation이 아니다.
- 검색에 사용한 특징은 같은 후보의 validation evidence로 재사용하지 않는다.
- 미래 독립 증거 없이 쿼리 일치 host를 `campaign member`로 부르지 않는다.
- Censys 미관측은 소멸과 동일하지 않다.

---

## 2. 연구질문별 경계

| RQ | 전용 입력 | 핵심 처리 | 출력 | 금지되는 해석 |
|---|---|---|---|---|
| RQ1 | CTI에 공개된 Q0 Seed 전체와 landmark 시점 Censys 관측 | 현재 host/service/fingerprint 관측과 continuity 분리 | 공개 연령별 landmark 상태, continuity 등급 | 정확한 수명·소멸시간 주장 |
| RQ2 | 사전 정의된 Seed cohort와 동결 쿼리의 반복 snapshot | node/service/fingerprint/cluster interval과 churn 산출 | 등급별 생존·churn 결과 | 미검증 후보 전체를 ORB로 간주 |
| RQ3 | 적격 anchor, 전향 관측 특징, matched background | persistence·reference-set rarity·비중복성 계산 | cutoff별 feature catalog | Q1 결과 내부 빈도만으로 캠페인 특이성 주장 |
| RQ4 | cutoff 전에 동결된 Q2/Q3, append-only 후보 원장, 미래 독립 증거 | point-in-time discovery와 delayed validation | 후보 등급, validation outcome, lead time | 동결 이전·동일 검색 특징을 미래 validation으로 계산 |
| RQ5 | 동일 cutoff·universe·schedule·alert budget의 query family | baseline, ablation, 비용·검토부담 비교 | yield, precision 범위, FP/day, unresolved/day, 비용 | unresolved를 임의의 negative로 처리 |

RQ1∼RQ5는 공통 원시 데이터 계층을 공유하지만 분석용 cohort와 결과 테이블은 분리한다. 특히 Q2/Q3 검색 결과만으로 RQ1 또는 확인된 RQ2 ORB cohort를 구성하지 않는다.

---

## 3. 핵심 용어와 상태

### 3.1 Indicator와 feature

| 용어 | 정의 |
|---|---|
| CTI Seed | CTI가 캠페인 인프라라고 직접 주장한 IP |
| CTI direct pivot | CTI 원문에 직접 포함된 domain, certificate, SPKI, SSH key, JARM/JA4, HTTP·service 특징 등 |
| Derived feature | Q0/Q1 Censys 관측에서 새로 추출된 특징 |
| Anchor | 캠페인 연계 근거가 충분해 feature 개발의 기준점으로 허용된 Seed 또는 host/entity epoch |
| Background | 동일 시점·제품·서비스·provider 조건을 맞춘 비교 host 집합 |
| Discovery feature | 후보를 검색하거나 점수화하는 데 사용한 특징 |
| Validation feature | 해당 후보 검색에 사용하지 않았고 캠페인 연계를 독립적으로 평가하는 특징 |

### 3.2 Seed continuity 상태

| 상태 | 의미 | derived feature 사용 |
|---|---|---|
| `continuous` | 과거와 현재의 서비스 정체성이 강하게 이어짐 | 허용 |
| `probable` | 일부 독립 특징이 유지되며 재할당 반증이 없음 | 설정된 기준을 통과한 경우 허용 |
| `unknown` | 현재 관측과 과거 캠페인의 동일성을 판단할 자료가 부족 | campaign query에는 금지 |
| `reassigned` | 다른 운영 주체·서비스로 바뀐 정황 | 금지 |
| `contradicted` | CTI의 역할·캠페인 주장과 충돌 | 금지 |

현재 host 응답 여부와 continuity는 별도 필드로 저장한다.

### 3.3 Direct pivot 적격성 상태

| 상태 | 의미 | 쿼리 사용 |
|---|---|---|
| `eligible` | 정체·queryability·결과공간·정상 재사용성 검토 통과 | 단독 또는 조합 |
| `combination_only` | 단독으로 broad/shared지만 보조 특징으로 사용 가능 | 조합만 허용 |
| `blocked` | 잘못된 정체, 정상 기본값, 비검색 필드 또는 과도한 위험 | 금지 |
| `pending` | pagination·정체·prevalence 평가 미완료 | 동결 쿼리에 금지 |

### 3.4 Derived feature 상태

```text
observed
→ recurrent
→ discriminative_candidate
→ query_eligible
→ validated_campaign_feature 또는 blocked
```

- `observed`: 하나 이상의 Q0/Q1 결과에서 추출됨
- `recurrent`: 서로 다른 적격 anchor에서 반복됨
- `discriminative_candidate`: matched background보다 희소함
- `query_eligible`: cutoff, queryability, 정상 재사용성, provenance 게이트 통과
- `validated_campaign_feature`: 미래 독립 증거에서 캠페인 연계가 확인됨

### 3.5 Candidate evidence grade

```text
raw_hit
→ technical_similarity_candidate
→ probable_campaign_link
→ high_confidence_campaign_link
→ confirmed_campaign_member
```

별도로 `broad_or_shared_pivot`, `contradicted`, `unresolved`, `unobservable`을 유지한다. 상위 등급으로의 승격은 검색 특징 이외의 증거를 요구한다.

---

## 4. 쿼리 분류 체계

기존 `Q0_SEED`, `Q1_DIRECT_PIVOT`, `Q2_DERIVED`, `Q3_CLUSTER` 대분류는 유지하되, `query_variant`와 `composition_type`을 추가한다.

| Query class | Variant/composition | 특징 출처 | 목적 |
|---|---|---|---|
| Q0 | `EXACT_IP` | CTI Seed IP | landmark, continuity 원재료, Seed enrichment |
| Q1 | `SINGLETON_PREFLIGHT` | CTI direct pivot 하나 | queryability·broad/shared·비용 확인; 전체 후보 cohort로 사용하지 않음 |
| Q1 | `CTI_COMPOSITE_ENRICHMENT` | CTI direct pivot 둘 이상 | 실제 enrichment 결과 수집과 CTI-only baseline |
| Q1 | `CTI_SINGLETON_DISCOVERY` | 충분히 희소한 적격 CTI pivot 하나 | CTI-only 단독 baseline |
| Q2 | `CTI_PLUS_DERIVED` | CTI pivot 하나 이상 + derived feature 하나 이상 | 제안 방법의 주력 query family |
| Q2 | `DERIVED_ONLY` | derived feature만 사용 | 기존 CTI pivot 변경 이후 추적 가능성 평가 |
| Q3 | `GRAPH_EXPANSION` | cutoff-valid node–service–fingerprint 관계 | 제한된 depth의 cluster 확장 |

분류 규칙은 다음과 같다.

- CTI 원문에 이미 있던 값만 사용하면 Q1이다.
- 최소 하나의 Censys-derived feature를 포함하면 Q2이다.
- 관계 그래프의 경로·cluster membership을 사용하면 Q3이다.
- 단독 Q1 preflight와 실제 전향적 discovery query를 동일 method로 집계하지 않는다.

RQ5 비교 method ID는 최소 다음을 포함한다.

```text
M1 = CTI direct singleton
M2 = CTI-only composite
M3 = CTI + Derived composite
M4 = Derived-only
M5 = Q3 graph expansion
```

Q0 exact-IP는 신규 host discovery method가 아니므로 RQ5의 주 discovery baseline과 분리한다.

---

## 5. 전체 컴포넌트 설계

```text
[CTI corpus registry]
        ↓
[indicator/assertion normalizer]
        ↓
[Q0/Q1 planner] ──→ [query registry]
        ↓                    ↓
[Censys collector] ──→ [immutable raw pages + manifests]
        ↓
[host/service normalizer]
        ↓
[continuity & pivot eligibility assessor]
        ↓
[feature extractor + entity resolver + relation graph]
        ↓
[anchor/background builder]
        ↓
[feature statistics & eligibility]
        ↓
[query composer + bounded precheck + human review]
        ↓
[freeze registry]
        ↓
[prospective scheduler/collector]
        ↓
[candidate ledger]
        ↓
[independent evidence/validation registry]
        ↓
[RQ1] [RQ2] [RQ3] [RQ4] [RQ5]
```

### 5.1 권장 소스 구조

```text
src/
├─ cti/
│  ├─ corpus_registry.py              # 기존
│  ├─ ioc_extraction.py               # 기존 확장
│  ├─ assertions.py                   # 신규: role/confidence/review 분리
│  └─ source_independence.py          # 신규: 원출처·재게시 판정
├─ censys/
│  ├─ q0_seed.py                      # 기존
│  ├─ q1_planner.py                   # 신규: singleton preflight/CTI composite
│  ├─ query_composer.py               # 신규: Q2/Q3 후보 생성
│  ├─ query_precheck.py               # 신규: bounded precheck
│  ├─ query_lifecycle.py              # 기존 확장
│  ├─ query_registry.py               # 기존 확장
│  └─ paginated_collection.py         # 기존
├─ features/
│  ├─ extract.py                      # 신규
│  ├─ catalog.py                      # 신규
│  ├─ statistics.py                   # 신규
│  ├─ background.py                   # 신규
│  └─ eligibility.py                  # 신규
├─ entities/
│  ├─ resolution.py                   # 신규
│  └─ graph.py                        # 기존 graph 기능 분리·확장
├─ assessments/
│  ├─ seed_continuity.py              # 신규
│  ├─ pivot_eligibility.py            # 신규
│  └─ candidate_evidence.py           # 신규
├─ prospective/
│  ├─ schedule.py                     # 신규
│  ├─ candidate_ledger.py             # 신규
│  └─ validation.py                   # 신규
└─ analysis/
   ├─ rq1_landmark.py                 # 신규
   ├─ rq2_churn.py                    # 신규
   ├─ rq3_features.py                 # 신규
   ├─ rq4_discovery.py                # 신규
   └─ rq5_evaluation.py               # 신규
```

---

## 6. 단계별 상세 설계

### Stage 0. 연구 프로토콜과 코퍼스 고정

#### 입력

- 검색으로 수집한 공개 CTI
- 연구 시작 전 보유한 CTI
- 상용 CTI가 있다면 별도 restricted registry

#### 처리

1. 검색 기간·검색어·대상 출처·포함/제외 기준을 protocol hash로 등록한다.
2. 동일 원출처, 번역, 재게시, 후속 보고서를 구분한다.
3. ORB, relay, controller, staging, C2, scanner, victim 역할을 분리한다.
4. CTI 파일과 OCR sidecar의 SHA-256을 저장한다.
5. `existing_curated`, `systematic_public`, `commercial`, `prospective_validation`을 구분한다.
6. 문서의 `published_at`, 연구 취득시각 `retrieved_at`, 원문 시간대와 날짜 정밀도를 기록한다.

#### 출력

- `search_protocols`
- `source_documents`
- `source_relationships`
- `source_families`: 동일 원출처의 재인용을 하나의 evidence family로 묶음
- immutable CTI snapshot manifest

#### 게이트

- 출처·공개일·원문 hash가 없는 자료는 assertion 생성 금지
- 게시일 미확인을 수집일로 대체 금지
- prospective validation CTI를 과거 query 개발에 사용 금지

### Stage 1. CTI 정규화와 assertion 생성

#### 입력

- 등록된 CTI 원문

#### 처리

1. IP, domain, URL, certificate, SPKI, SSH key, JARM/JA4, port/protocol, HTTP 특징을 정규화한다.
2. indicator 값과 캠페인·역할 주장을 분리한다.
3. 원문 근거 위치와 excerpt hash를 저장한다.
4. source confidence, extraction confidence, role confidence를 구분한다.
5. 사람 검토 후 `accepted`, `rejected`, `pending`을 부여한다.
6. 사실별 `first_public_at`과 연구 사용 가능시각 `available_at`을 저장한다.
7. CTI가 주장한 `cti_reported_first_seen_at`과 `cti_reported_last_seen_at`을 공개일과 분리한다.
8. 캠페인 단위의 `campaign_reported_first_seen_at`, `campaign_reported_last_seen_at`, `campaign_first_public_at`을 원출처별로 기록한다.
9. 같은 node·service·시간 범위에서 공동 관측된 pivot만 `node_assertion_group`으로 묶는다.

#### 출력

- `indicators`
- `indicator_assertions`
- `indicator_contexts`
- `node_assertion_groups`
- `campaign_timelines`
- `indicator_reported_timelines`
- `review_decisions`

#### 게이트

- `reviewer_status=accepted`가 아닌 indicator는 자동 query 등록 금지
- victim/scanner/sinkhole은 Seed·pivot에서 제외하거나 별도 분석층으로 격리
- IP와 non-IP pivot을 동일 query class로 등록하지 않음
- `published_at`, `first_public_at`, CTI 보고 first/last seen을 하나의 `first_seen`으로 통합하지 않음
- 날짜만 알려진 값을 임의의 자정 timestamp로 확정하지 않고 정밀도·하한·상한을 보존

### Stage 2. Q0 Seed landmark와 enrichment

#### 입력

- accepted CTI Seed IP
- CTI 시점에 공개된 service/fingerprint evidence

#### 처리

1. `host.ip = <seed>` exact query를 등록·실행한다.
2. immutable raw page와 실행 manifest를 저장한다.
3. host/service observation을 정규화한다.
4. landmark 상태와 continuity 상태를 별도로 평가한다.
5. `continuous` 또는 허용된 `probable` Seed만 derived feature source로 표시한다.
6. IP·entity epoch·service·fingerprint별 연구 관측 최초·최종시각을 원시 observation에서 파생한다.

#### landmark 출력

- `host_observed`
- `service_observed`
- `fingerprint_observed`
- `not_found`, `not_scanned`, `no_response`, `api_error`

#### continuity 평가 입력

- certificate/SPKI/SSH key
- banner/HTTP hash
- portset/protocol
- device/product
- ASN/prefix/provider
- 관측 공백과 독립 CTI 재확인

#### 출력

- `host_observations`
- `service_observations`
- `seed_continuity_assessments`
- Q0-derived `feature_observations`
- `ip_observation_timelines`
- `service_observation_timelines`
- `fingerprint_observation_timelines`

#### 게이트

- 현재 응답만으로 `continuous` 판정 금지
- historical evidence가 없으면 기본 `unknown`
- `unknown`, `reassigned`, `contradicted`의 특징은 Q2 campaign-candidate query에 금지
- `censys_last_observed_at`은 마지막 양성 관측일이지 소멸일이 아님

### Stage 3. Q1 direct pivot precheck와 CTI-only enrichment

#### 3.1 Singleton precheck

CTI direct pivot 각각에 대해 제한된 precheck를 수행한다.

수집 항목:

- query syntax/queryability
- 반환 존재 여부
- bounded hit/page count
- next page token 존재 여부
- ASN·제품·서비스의 제한 표본 분포
- 비용 추정

`partial_max_pages`는 다음 의미만 가진다.

```text
positive raw observation + prevalence unresolved
```

전체 prevalence 또는 cohort 규모로 사용하지 않는다.

#### 3.2 CTI-only composite 생성

singleton이 broad/shared이면 동일 node role·시간·서비스 관계에서 공존 가능한 CTI pivot을 조합한다.

```text
(동일 family의 대체값은 OR)
AND
(서로 다른 family의 공존 특징)
```

예:

```text
(cert X OR cert Y) AND port 8443
SPKI Z AND protocol SSH
CTI domain D AND certificate X
JARM J AND non-standard port AND CTI HTTP pattern H
```

같은 보고서에 나왔다는 이유만으로 서로 다른 node role의 pivot을 AND로 결합하지 않는다.

#### 3.3 실제 enrichment 수집

- 적격 singleton 또는 CTI composite query를 전체 pagination으로 수집한다.
- 결과는 `campaign member`가 아니라 enrichment universe로 저장한다.
- host/service/fingerprint/relation을 추출한다.

#### 출력

- `pivot_eligibility_assessments`
- `query_prechecks`
- Q1 query registry records
- Q1 enrichment observations
- CTI-only baseline method records

#### 게이트

- `pending` 또는 `partial_max_pages` pivot은 Q2 source로 금지
- Q1 0-hit을 pivot 소멸 또는 캠페인 소멸로 판정 금지
- Q1 결과 전체에서 흔한 특징을 곧바로 캠페인 특징으로 승격 금지

### Stage 4. Derived feature 발굴과 적격성 평가

#### 4.1 분석 집합

| 집합 | 구성 |
|---|---|
| Anchor A | continuity-eligible Q0 Seed와 독립 근거를 가진 Q1 host/entity epoch |
| Enrichment P | 적격 Q1 singleton/composite의 전체 결과 |
| Background B | 동일 시점·제품·서비스·provider 조건을 맞춘 비교집단 |

#### 4.2 특징 생성

지원 feature family:

- identity: certificate, SPKI, SSH key
- TLS: JARM, JA4
- HTTP: title/banner/header/body-derived hash
- service: port, protocol, extended service, portset
- device: vendor, product, version class
- network: ASN, prefix, provider class
- temporal: 동시 출현, 변화 순서, 재관측 간격
- relation: shared fingerprint, reported-with, resolves-to, co-observed

가변 nonce, timestamp, request ID, 공백 등은 canonicalization에서 제거하고 원문 hash와 정규화 hash를 함께 보존한다.

#### 4.3 통계량

특징 또는 조합 `f`에 대해 cutoff별로 계산한다.

```text
anchor_support(f)       = anchors_with_f / observable_anchors
background_prevalence  = background_with_f / observable_background
reference_lift(f)      = anchor_support / max(background_prevalence, epsilon)
q1_retention(f)        = Q1_hosts_with_f / observable_Q1_hosts
temporal_support(f)    = 관측 기회 중 feature 재관측 비율
```

전체 인터넷을 대표하지 않는 background를 사용하면 `global rarity`라고 부르지 않고 `matched-background prevalence` 또는 `reference-set rarity`라고 기록한다.

#### 4.4 query eligibility

구체 임계값은 파일럿 development 결과와 비용 제약을 이용해 사전 등록하며 민감도 분석을 수행한다. 최소 논리 게이트는 다음과 같다.

- 서로 다른 적격 anchor에서 반복됨
- 단일 host 고유 우연값이 아님
- matched background에서 broad/shared가 아님
- 정상 제품 기본값 또는 CDN/shared hosting 특성이 아님
- query field와 canonical representation이 안정적임
- feature `first_available_at <= query cutoff`
- source Seed/pivot이 query input으로 허용됨

#### 출력

- `feature_catalog`
- `feature_observations`
- `feature_stat_snapshots`
- `reference_sets`
- `feature_eligibility_assessments`

### Stage 5. 캠페인 후보 검색 쿼리 개발·검토·동결

#### 5.1 query family 생성

1. CTI-only singleton/composite
2. CTI+Derived
3. Derived-only
4. Q3 graph expansion

권장 조합은 non-redundant cross-family feature를 사용한다. 서로 다른 family라는 이유만으로 통계적 독립성을 주장하지 않는다.

#### 5.2 bounded development precheck

허용 목적:

- 문법과 field 지원 확인
- 0-result 여부 확인
- broad query 차단
- 결과 규모·비용 상한 확인
- pagination 실행 가능성 확인
- 안전·라이선스 검토

precheck 결과로 허용되는 변경은 development 내 새 query version 생성이다. 수정된 query는 새 hash와 provenance를 가져야 한다. precheck 결과는 전향 precision 또는 discovery 성능으로 계산하지 않는다.

#### 5.3 수동 검토 체크리스트

- 모든 feature가 cutoff 이전에 available했는가
- source Seed continuity와 pivot eligibility를 통과했는가
- CTI pivot과 derived feature의 출처가 구분되는가
- 서로 다른 node role을 잘못 AND로 결합하지 않았는가
- 검색 특징과 예정된 validation 특징이 분리되는가
- query가 active victim을 불필요하게 노출하지 않는가
- 예정된 실행 주기와 비용 상한이 현실적인가

#### 5.4 동결

동결 대상:

- query text/hash/version/class/variant/composition type
- source indicator IDs와 feature IDs
- query cutoff
- Censys dataset/API/schema version
- parser·normalizer·entity-resolution version
- background snapshot/version
- score function과 threshold
- alert budget과 tie-breaking rule
- 실행 주기
- `frozen_at`, `valid_for_test_from`
- 허용 validation evidence family

동결 이후 동일 version에서 금지되는 변경:

- 조건 추가·삭제
- threshold·가중치·K 변경
- 특정 ASN·국가·제품의 사후 제외
- 결과를 본 뒤 실행 주기 변경

변경이 필요하면 새 version을 등록하고 새 `valid_for_test_from` 이후 결과만 해당 version의 전향 성능으로 계산한다.

#### 출력

- frozen query registry
- query feature provenance
- freeze manifest
- prospective schedule plan

### Stage 6. 전향적 반복 관측

#### 실행 조건

- `status=frozen`
- `executed_at >= valid_for_test_from`
- 전향 평가에 포함할 각 레코드의 Censys 기저 `observed_at >= valid_for_test_from`
- query hash 일치
- dataset split은 `prospective_test`

API 호출시각만 동결 이후이고 반환된 host/service 관측시각이 동결 이전이면 해당 레코드는 전향 발견으로 계산하지 않는다. Censys가 관측시각을 제공하지 않아 이를 판별할 수 없으면 `prospective_time_unresolved`로 분리한다.

#### 권장 기본 주기

| 대상 | 기본 주기 | 비고 |
|---|---|---|
| Q0 confirmed Seed | 매일 | RQ1 landmark와 RQ2 tracking 분리 기록 |
| high-confidence candidate | 매일 | 비용 상한 적용 |
| 일반 candidate query | 3일 또는 7일 | protocol에 사전 고정 |
| 전체 query result snapshot | 매주 | pagination 완료 필요 |
| matched background | 월별 또는 고정 주기 | 동일 cutoff 정책 적용 |

실제 주기는 파일럿에서 API 비용과 변화율을 근거로 고정한다. 실행 누락은 음성 관측으로 보간하지 않고 `observation opportunity missing`으로 기록한다.

#### 처리

1. raw page와 checkpoint를 append-only로 저장한다.
2. normalization과 schema drift 검사를 실행한다.
3. host/service/fingerprint를 entity epoch에 연결한다.
4. 처음 등장한 query–entity 쌍에 candidate를 생성한다.
5. 이후 재관측은 같은 candidate의 observation event로 추가한다.
6. query result에서 사라진 사실을 즉시 node death로 처리하지 않는다.
7. 예정된 관측기회, 마지막 양성 관측, 최초 연속 미관측을 구분하여 interval-censored event 경계를 만든다.

#### 출력

- `query_executions`
- immutable raw manifests
- prospective observations
- `entity_epochs`
- `candidate_ledger`
- candidate observation events
- IP/entity/service/fingerprint/cluster timeline snapshots
- `observation_opportunities`와 interval-censoring event bounds

### Stage 7. 미래 독립 증거와 후보 등급 갱신

#### discovery/validation 분리

각 evidence에는 `evidence_role`을 저장한다.

- `discovery`: query 또는 후보 점수에 사용
- `validation`: 검색에 사용되지 않은 독립 근거
- `contradiction`: 정상 재사용, 재할당, 무관 캠페인 근거

검증 가능 증거:

- 후보 생성 이후 공개된 독립 CTI
- query에 사용하지 않은 certificate/SPKI/SSH key/domain
- malware configuration 또는 IR evidence
- 독립 공급자의 campaign/role 확인
- 정상 제품 기본값·재할당·공유 인프라라는 반증

동일 원문의 재게시를 독립 validation으로 세지 않는다. 동일 공급자의 후속 보고서는 `partially_independent`로 표시한다.

미래 선행발견과 lead time 근거로 사용할 evidence는 원칙적으로 `evidence.available_at > candidate.first_candidate_at`을 만족해야 한다. 후보 생성 전에 이미 공개된 근거는 사후 분류 보조에는 사용할 수 있지만 미래 선행발견의 validation으로 계산하지 않는다.

#### verdict

- `positive`
- `negative`
- `contradicted`
- `unresolved`
- `unobservable`

`unresolved`는 negative가 아니다. label 변경 이력과 근거 시간을 append-only로 보존한다.

#### 출력

- `candidate_evidence`
- `candidate_validations`
- evidence grade history
- future CTI linkage
- `evidence_published_at`, `evidence_available_at`, `evidence_attached_at`, `validated_at`

### Stage 8. RQ별 분석 데이터 생성

#### RQ1

- 분석 단위: CTI Seed indicator
- 출력: landmark host/service/fingerprint 상태와 continuity 등급
- 현재 미관측률과 campaign continuity 비율을 분리
- `public_age_at_landmark = landmark_at - first_public_at`을 사용하고 CTI 활동 최초일을 공개일로 대체하지 않음

#### RQ2

- 분석 단위: node/entity epoch, service, fingerprint, cluster
- `confirmed/high-confidence`, `probable`, `technical-similarity-only`를 별도 층으로 분석
- 결과 명칭도 `validated ORB churn`과 `signature-matching candidate churn`으로 분리
- 마지막 양성 관측과 최초 연속 미관측 사이를 사건 구간으로 사용

#### RQ3

- 분석 단위: cutoff별 feature와 feature family
- persistence, matched-background prevalence, anchor support, lift
- 전체 미래 기간 정보를 과거 cutoff feature score에 사용 금지
- feature 최초·최종 관측과 관측기회 수를 함께 보고하여 단순 calendar span과 구분

#### RQ4

- 분석 단위: 동결 이후 최초 생성된 candidate
- candidate 생성 이전에 공개된 evidence는 future validation이 아님
- positive에 한해 `lead_time = future_evidence_first_public_at - first_candidate_at` 계산
- retrospective Censys timestamp가 아니라 실제 연구 시스템의 `first_candidate_at`을 lead-time 시작점으로 사용

#### RQ5

- 동일 cutoff·universe·schedule·alert budget에서 M1∼M5 비교
- method별 raw alerts, unique entities, analyst review burden, API 비용 기록
- 전체 모집단 ground truth가 없으면 일반 recall을 주장하지 않고 held-out seed recovery 또는 validated yield를 사용

권장 지표:

```text
validated_yield@K
verified_precision       = positive / (positive + negative)
conservative_precision   = positive / (positive + negative + unresolved)
resolution_rate          = (positive + negative) / all_reviewed
confirmed_FP_per_day
unresolved_per_day
alerts_per_day
cost_per_validated_positive
lead_time_for_future_confirmed_cases
```

---

## 7. 데이터 모델

현재 모델을 유지하면서 다음 필드·테이블을 추가한다.

### 7.1 기존 테이블 확장

#### `source_documents`

추가·명시 필드:

- `published_at`, `retrieved_at`
- `published_time_precision`, `source_timezone`
- `source_family_id`
- `superseded_at`

#### `indicator_assertions`

추가 권장 필드:

- `source_confidence`
- `extraction_confidence`
- `role_confidence`
- `first_public_at`
- `available_at`
- `cti_reported_first_seen_at`
- `cti_reported_last_seen_at`
- `temporal_value_ids`
- `node_group_id`
- `service_context`

기존 `vendor_first_seen/vendor_last_seen`은 호환용으로 읽되 공급자 주장 시각이라는 의미를 유지하고, 일반화된 논문 필드는 `cti_reported_first_seen_at/last_seen_at`으로 materialize한다.

#### `node_assertion_groups`

동일 보고서라는 이유만으로 서로 다른 역할의 pivot을 조합하지 않도록 공동 관측 범위를 저장한다.

- `node_group_id`
- `campaign_id`, `node_role`
- `indicator_ids`
- `cooccurrence_scope`: same_host/same_service/reported_relation/unknown
- `valid_from`, `valid_to`, `available_at`
- `evidence_assertion_ids`

#### `query_registry`

추가 필드:

- `query_variant`
- `composition_type`
- `cutoff_time`
- `precheck_status`
- `reviewed_at`
- `reviewer_id`
- `alert_budget`
- `score_config_hash`
- `schedule_config_hash`

현재 `QueryStatus.VALIDATED`는 캠페인 validation으로 오해될 수 있다. 구현 호환성을 유지하려면 의미를 `development precheck reviewed`로 엄격히 문서화하고, 차기 schema에서는 `PRECHECKED` 또는 `REVIEWED`로 이름을 변경한다.

#### `host_observations`와 `service_observations`

- 원시 `observed_at`, `collected_at`, `observation_time_basis` 유지
- `observed_time_precision`, `observed_time_source`
- `scheduled_opportunity_id`
- `prospective_time_status`: eligible/pre_freeze/unresolved
- service row의 최초·최종 관측값을 직접 갱신하지 않고 timeline view에서 파생

### 7.2 신규 핵심 테이블

#### `seed_continuity_assessments`

| 필드 | 설명 |
|---|---|
| `assessment_id` | 결정적 ID |
| `seed_indicator_id` | CTI Seed |
| `landmark_observed` | 현재 host 관측 여부 |
| `continuity_status` | continuous/probable/unknown/reassigned/contradicted |
| `derived_pivot_allowed` | Q2 입력 허용 여부 |
| `supporting_evidence_ids` | 근거 |
| `contradicting_evidence_ids` | 반증 |
| `assessed_at` | 평가시각 |
| `reviewer_status` | 수동 검토 상태 |

#### `pivot_eligibility_assessments`

| 필드 | 설명 |
|---|---|
| `pivot_assessment_id` | 평가 ID |
| `indicator_id` | CTI direct pivot |
| `identity_status` | CTI 값과 Censys field 의미 일치 여부 |
| `queryability_status` | 지원 여부 |
| `pagination_status` | complete/partial/error |
| `result_count` | 완결된 경우만 확정값 |
| `normal_reuse_status` | rare/shared/default/unknown |
| `eligibility` | eligible/combination_only/blocked/pending |
| `evidence_ids` | 근거 |

#### `feature_catalog`

| 필드 | 설명 |
|---|---|
| `feature_id` | canonical feature ID |
| `feature_family` | identity/TLS/HTTP/service/device/network/temporal/relation |
| `feature_type` | SPKI, JARM, portset 등 |
| `canonical_value` | restricted 저장 값 또는 HMAC public ID |
| `origin` | CTI_DIRECT/Q0_ENRICHMENT/Q1_ENRICHMENT/GRAPH_DERIVED |
| `first_available_at` | 최초 사용 가능 시각 |
| `source_indicator_ids` | CTI 출처 |
| `source_observation_ids` | Censys 출처 |
| `state` | observed∼validated_campaign_feature |
| `sensitivity` | public/restricted/active-victim |

#### `feature_stat_snapshots`

| 필드 | 설명 |
|---|---|
| `feature_id` | 특징 |
| `cutoff_time` | 계산 cutoff |
| `anchor_set_id` | anchor 정의 |
| `background_set_id` | background 정의 |
| `anchor_support_num/den` | 분자·분모 |
| `background_prevalence_num/den` | 분자·분모 |
| `reference_lift` | 효과크기 |
| `temporal_support_num/den` | 재관측 분자·분모 |
| `status` | complete/partial/insufficient |

#### `query_features`

| 필드 | 설명 |
|---|---|
| `query_id` | query |
| `feature_id` | 구성 특징 |
| `feature_origin` | CTI_DIRECT 또는 derived origin |
| `logical_role` | required/alternative/exclusion/score-only |
| `evidence_role` | discovery 고정 |
| `available_at` | cutoff 검사 |

#### `query_clauses`

query의 논리구조를 재현하기 위한 테이블이다.

- `query_id`, `clause_id`, `parent_clause_id`
- `feature_id`, `query_field`
- `operator`: AND/OR/NOT
- `logical_role`: required/alternative/exclusion/score-only
- `cooccurrence_scope`: host/service/certificate/name/graph-edge
- `feature_origin`, `feature_family`
- `canonicalizer_version`

#### `query_prechecks`

- query ID/version/hash
- 실행 시각과 development cutoff
- bounded page/hit limit
- observed minimum result count
- next token 여부
- broad/cost/syntax 판정
- `performance_claim_allowed=false` 고정

#### `candidate_ledger`

| 필드 | 설명 |
|---|---|
| `candidate_id` | 결정적 후보 ID |
| `entity_epoch_id` | 후보 entity |
| `first_candidate_at` | 최초 생성시각 |
| `source_query_id/hash` | 동결 query |
| `source_query_run_id` | 최초 실행 |
| `discovery_feature_ids` | 검색 특징 |
| `initial_score` | 동결 score |
| `initial_grade` | raw_hit/technical_similarity_candidate |
| `current_grade` | 최신 등급 |
| `sensitivity` | 공개 정책 |

#### `candidate_evidence`와 `candidate_validations`

- evidence source/document/observation
- evidence role
- available_at
- source independence
- verdict
- analyst/reviewer
- grade before/after
- 변경 사유

### 7.3 논문 분석용 시간 모델

#### 7.3.1 시간 필드 공통 형식

모든 시간 값은 timezone-aware UTC로 저장하되 원문 표현과 불확실성을 잃지 않는다. 날짜만 알려진 값을 `00:00:00Z`로 확정하여 정렬하면 미래정보 누수와 잘못된 lead time이 발생하므로 각 시간 값은 다음 공통 메타데이터를 가진다.

| 필드 | 설명 |
|---|---|
| `time_start` | 가능한 가장 이른 시각 또는 정확한 시각 |
| `time_end` | 가능한 가장 늦은 시각; 정확한 시각이면 `time_start`와 동일 |
| `time_precision` | exact_timestamp/date/day/month/year/range/unknown |
| `time_basis` | CTI_REPORTED/CENSYS_OBSERVED/STUDY_COLLECTED/STUDY_DERIVED/EXTERNAL_EVIDENCE |
| `source_id` | 원문·query run·observation·evidence ID |
| `source_timezone` | 원문 시간대; 미표기 시 unknown |
| `is_inferred` | 원문 직접 기재인지 파생값인지 |
| `inference_method` | min/max/interval-censoring/manual 등 |
| `confidence` | 시간 해석의 신뢰도 |
| `computed_at` | 파생 시간 계산시각 |

정확한 timestamp convenience column을 사용하는 테이블도 원본 `temporal_value_id`를 함께 참조한다. `unknown`은 null과 상태코드로 보존하며 다른 날짜로 대체하지 않는다.

#### 7.3.2 CTI 문서와 공개 시간

| 필드 | 의미 | 파생 여부 |
|---|---|---|
| `published_at` | 해당 CTI 문서가 발행된 시각 | 원출처 |
| `retrieved_at` | 연구 시스템이 문서를 취득한 시각 | 연구 관측 |
| `first_public_at` | 해당 assertion/indicator가 일반에 공개된 것으로 확인되는 최초 시각 | 원출처 집계 가능 |
| `available_at` | 검토를 거쳐 연구 query/feature에 실제 사용할 수 있게 된 시각 | 연구 파생 |
| `superseded_at` | 정정·철회·후속 버전으로 대체된 시각 | 선택 |

시간 의미는 다음과 같이 분리한다.

```text
published_at       = 문서의 발행
first_public_at    = 사실 또는 IoC의 최초 공개
retrieved_at       = 연구자의 문서 취득
available_at       = 연구 파이프라인에서 사용 가능
```

일반적으로 `first_public_at <= available_at`이어야 한다. 단, 비공개 상용 CTI처럼 공개 개념이 적용되지 않는 경우 `first_public_at`을 억지로 생성하지 않고 access class와 `available_at`만 기록한다.

#### 7.3.3 캠페인 시간

generic `campaign_first_seen_at`을 사용하지 않고 다음을 분리한다.

| 필드 | 의미 |
|---|---|
| `campaign_reported_first_seen_at` | CTI 공급자가 주장한 캠페인 활동 최초 관측 |
| `campaign_reported_last_seen_at` | CTI 공급자가 주장한 캠페인 활동 최종 관측 |
| `campaign_first_public_at` | 캠페인 또는 본 연구 대상 활동이 최초 공개된 시각 |
| `campaign_study_first_observed_at` | 연구 telemetry에서 캠페인 연계가 확인된 node가 최초 관측된 시각 |
| `campaign_study_last_observed_at` | 연구 telemetry에서 캠페인 연계가 확인된 node가 마지막으로 관측된 시각 |
| `campaign_candidate_first_observed_at` | 동결 query의 후보가 최초 등장한 시각 |
| `campaign_candidate_last_observed_at` | 후보가 마지막으로 관측된 시각 |
| `campaign_first_validated_at` | 사전 정의된 증거 기준으로 최초 campaign link가 성립한 시각 |

`campaign_study_*`는 어떤 evidence grade를 캠페인 구성원으로 인정했는지 `membership_policy_id`와 함께 계산한다. confirmed/high-confidence와 technical-similarity-only의 캠페인 timeline을 합치지 않는다.

#### 7.3.4 Indicator와 IP 시간

| 필드 | 의미 |
|---|---|
| `cti_reported_first_seen_at` | CTI가 해당 indicator/IP를 처음 관측했다고 주장한 시각 |
| `cti_reported_last_seen_at` | CTI가 해당 indicator/IP를 마지막으로 관측했다고 주장한 시각 |
| `indicator_first_public_at` | indicator가 최초 공개된 시각 |
| `indicator_available_at` | 연구 query에 사용 가능해진 시각 |
| `censys_first_observed_at` | 본 연구에 포함된 Censys 원시 관측 중 최초 양성 관측 |
| `censys_last_observed_at` | 본 연구에 포함된 Censys 원시 관측 중 최종 양성 관측 |
| `first_query_match_at` | 동결 query에 최초로 일치한 실행시각 |
| `last_query_match_at` | 동결 query에 마지막으로 일치한 실행시각 |
| `last_positive_observed_at` | 사건 전 마지막 양성 관측 |
| `first_consecutive_missing_at` | 사전 정의된 연속 미관측 조건의 첫 관측기회 |

IP 문자열이 같아도 재할당 또는 서비스 정체성 변경이 있으면 `entity_epoch`를 새로 만든다. 따라서 IP-level timeline과 entity-epoch timeline을 함께 저장한다.

#### 7.3.5 Host/entity epoch, service, fingerprint와 cluster 시간

| 대상 | 필수 시간 필드 |
|---|---|
| Host observation | `observed_at`, `collected_at`, `observation_time_basis` |
| Entity epoch | `entity_epoch_started_at`, `entity_epoch_last_observed_at`, `entity_epoch_closed_at` |
| Service | `service_first_observed_at`, `service_last_observed_at` |
| Fingerprint | `fingerprint_first_observed_at`, `fingerprint_last_observed_at` |
| Relation | `valid_from`, `valid_to`, `available_at` |
| Cluster membership | `membership_first_observed_at`, `membership_last_observed_at` |
| Churn event | `interval_left`, `interval_right`, `event_recorded_at` |

`entity_epoch_closed_at`은 명시적 재할당·정체성 변경 근거가 있을 때만 확정한다. 단순 미관측이면 `interval_left=last_positive_observed_at`, `interval_right=first_consecutive_missing_at`의 interval-censored 사건으로 유지한다.

#### 7.3.6 수집과 관측기회 시간

| 필드 | 의미 |
|---|---|
| `scheduled_for` | protocol상 실행 예정시각 |
| `request_started_at` | API 요청 시작시각 |
| `response_received_at` | 응답 완료시각 |
| `executed_at` | query 실행 대표시각 |
| `observed_at` | Censys가 host/service를 실제 관측한 시각 |
| `collected_at` | 연구 시스템이 응답을 저장한 시각 |
| `checkpointed_at` | pagination checkpoint 저장시각 |
| `completed_at` | 전체 pagination 완료시각 |
| `missed_at` | 예정 실행이 수행되지 못한 시각 |

`executed_at` 또는 `collected_at`을 Censys `observed_at`으로 대체하지 않는다. 예정 실행 누락·API 오류·Censys 미스캔은 모두 host 음성과 분리한다.

#### 7.3.7 Query lifecycle 시간

| 필드 | 의미 |
|---|---|
| `registered_at` | query registry 등록시각 |
| `prechecked_at` | bounded development precheck 완료시각 |
| `reviewed_at` | 수동 검토 승인시각 |
| `frozen_at` | query·threshold·budget·schedule 동결시각 |
| `valid_for_test_from` | 전향 평가에 사용할 수 있는 최초시각 |
| `retired_at` | 신규 실행을 중단한 시각 |
| `evaluation_window_start/end` | 해당 version의 사전 정의 평가기간 |

#### 7.3.8 Candidate와 독립 검증 시간

| 필드 | 의미 |
|---|---|
| `first_match_observed_at` | 후보와 연결된 최초 Censys 기저 관측시각 |
| `first_candidate_at` | 연구 시스템이 후보를 최초 생성·원장에 고정한 시각 |
| `last_match_observed_at` | 후보가 마지막으로 일치했을 때의 Censys 기저 관측시각 |
| `last_match_recorded_at` | 연구 시스템이 마지막 query match를 원장에 기록한 시각 |
| `evidence_published_at` | 외부 검증 문서가 발행된 시각 |
| `evidence_available_at` | 검증 증거가 연구에 사용 가능해진 시각 |
| `evidence_attached_at` | 후보에 증거를 연결한 시각 |
| `adjudicated_at` | 분석가 판정시각 |
| `first_validated_at` | 최초 positive/probable 기준을 충족한 시각 |
| `grade_changed_at` | evidence grade 변경시각 |

RQ4의 선행발견에는 `first_candidate_at`을 사용한다. 과거 Censys record를 동결 이후 조회해서 얻은 `first_match_observed_at`을 후보 생성일로 소급하지 않는다.

#### 7.3.9 파생 timeline 테이블

원시 observation은 append-only로 유지하고 최초·최종 관측값은 다음 materialized table/view로 계산한다.

| 테이블 | 분석 단위 | 주요 필드 |
|---|---|---|
| `campaign_timelines` | campaign × evidence-grade policy | reported/study/candidate first·last, first public, first validated |
| `indicator_reported_timelines` | indicator × CTI source family | reported first·last, first public |
| `ip_observation_timelines` | IP × data source × cutoff | first·last positive, first missing, opportunities |
| `entity_epoch_timelines` | entity epoch | start, last positive, event interval, close reason |
| `service_observation_timelines` | entity epoch × service | first·last observed, opportunity count |
| `fingerprint_observation_timelines` | fingerprint × entity/campaign | first·last observed, persistence span |
| `cluster_timelines` | cluster/membership | membership first·last, add/remove interval |
| `candidate_timelines` | candidate | first candidate, last match, first validation, grade history |

각 materialization에는 `cutoff_time`, `input_manifest_hash`, `code_version`, `config_hash`, `computed_at`을 저장한다. 동일 raw event로 언제든 재생성할 수 있어야 한다.

#### 7.3.10 RQ별 날짜 계산

| RQ | 날짜 계산 | 주의사항 |
|---|---|---|
| RQ1 | `public_age_at_landmark = landmark_at - indicator_first_public_at` | campaign first seen과 공개일을 혼용하지 않음 |
| RQ2 | `interval_left=last_positive_observed_at`, `interval_right=first_consecutive_missing_at` | 마지막 관측을 소멸일로 간주하지 않음 |
| RQ3 | feature first/last observation, observation opportunities, persistence span | 장기간 수집 공백을 지속성으로 계산하지 않음 |
| RQ4 | `lead_time = future_evidence_first_public_at - first_candidate_at` | positive이고 후보 이후 공개된 독립 evidence만 포함 |
| RQ5 | 고정된 `evaluation_window_start/end` 내 alerts·labels·비용 | method별 평가기간을 다르게 설정하지 않음 |

---

## 8. 시간·누수 통제

모든 query `q`에 대해 다음 불변식을 강제한다.

```text
max(feature.available_at for feature in q) <= q.cutoff_time
q.registered_at >= q.cutoff_time
q.frozen_at >= q.registered_at
q.valid_for_test_from >= q.frozen_at
prospective_execution.executed_at >= q.valid_for_test_from
prospective_record.observed_at >= q.valid_for_test_from
```

모든 원문·관측·후보 timeline에는 다음 불변식을 추가한다.

```text
source_document.published_at <= source_document.retrieved_at
assertion.first_public_at <= assertion.available_at
cti_reported_first_seen_at <= cti_reported_last_seen_at           # 둘 다 알려진 경우
observation.observed_at <= observation.collected_at               # observed_at이 알려진 경우
censys_first_observed_at <= censys_last_observed_at
service_first_observed_at <= service_last_observed_at
fingerprint_first_observed_at <= fingerprint_last_observed_at
candidate.first_match_observed_at <= candidate.first_candidate_at # 과거 record 소급 생성 금지
candidate.first_match_observed_at <= candidate.last_match_observed_at
candidate.first_candidate_at <= candidate.last_match_recorded_at
future_validation.evidence_available_at > candidate.first_candidate_at
evaluation_window_start < evaluation_window_end
```

원출처의 날짜 정밀도가 day/month/range이면 단일 timestamp 불변식 대신 `time_start/time_end` 구간의 가능한 순서를 검사한다. 순서가 충돌하면 임의 수정하지 않고 `temporal_conflict`로 격리한다.

추가 금지 규칙:

- prospective 결과를 보고 같은 query version 수정
- candidate 생성 이후 evidence를 과거 feature score에 포함
- future CTI를 development corpus로 역삽입
- 동일 query match를 validation evidence로 연결
- 전체 기간 rarity를 과거 cutoff에 적용
- 쿼리별로 다른 evaluation window 또는 alert budget 적용
- 기저 관측시각을 알 수 없는 레코드를 임의로 동결 이후 관측으로 처리
- CTI 문서 발행일을 campaign/IP 활동 최초 관측일로 대체
- `collected_at` 또는 API 실행시각을 Censys `observed_at`으로 대체
- 마지막 양성 관측일을 node/service 소멸일로 확정
- 날짜 정밀도가 낮은 값을 임의의 자정으로 변환해 사건 순서를 확정

누수 검사 실패 시 RQ4·RQ5 분석 실행을 fail-closed로 중단한다.

---

## 9. CLI 계약

권장 명령 체계:

```text
orb-research cti register-protocol
orb-research cti ingest
orb-research cti extract
orb-research cti review-assertions

orb-research query plan-q0
orb-research query plan-q1-precheck
orb-research query build-q1-composite
orb-research query execute-development
orb-research query resume-pagination

orb-research assess seed-continuity
orb-research assess pivot-eligibility

orb-research feature extract
orb-research feature build-background
orb-research feature score --cutoff <timestamp>
orb-research feature review-eligibility

orb-research query compose --family cti-only|cti-derived|derived-only|graph
orb-research query precheck
orb-research query review
orb-research query freeze

orb-research prospective due
orb-research prospective execute
orb-research candidate materialize
orb-research candidate attach-evidence
orb-research candidate adjudicate

orb-research analyze rq1
orb-research analyze rq2
orb-research analyze rq3
orb-research analyze rq4
orb-research analyze rq5
orb-research audit leakage
```

네트워크 실행 명령은 환경변수 기반 live gate를 요구하고 API token을 CLI 인자·manifest·로그에 기록하지 않는다.

---

## 10. 설정 파일

예시 구조:

```yaml
research:
  timezone: UTC
  prospective_start: "2026-08-01T00:00:00Z"

q1:
  singleton_precheck:
    max_pages: 2
    performance_claim_allowed: false
  composite:
    require_same_campaign: true
    require_role_compatibility: true
    require_temporal_overlap: true

feature_eligibility:
  min_distinct_anchors: 2
  require_matched_background: true
  allowed_seed_continuity: [continuous, probable]
  cross_family_preferred: true

query_precheck:
  max_pages: 2
  max_estimated_alerts_per_day: 20
  max_estimated_credits: 100

prospective:
  default_interval_days: 7
  high_confidence_interval_days: 1
  require_frozen_query: true

validation:
  disallow_discovery_feature_reuse: true
  unresolved_is_negative: false
  duplicate_source_is_independent: false
```

숫자 임계값은 예시이며 파일럿 development 이전 또는 명시된 protocol amendment로 고정한다. 최종 논문에는 임계값 민감도 분석을 포함한다.

---

## 11. 테스트와 수용 기준

### 11.1 단위 테스트

- indicator canonicalization과 defang
- naive datetime 거부와 UTC 정규화
- 날짜 정밀도·시간대·구간 보존 및 임의 자정 변환 차단
- CTI reported/public/study-observed 날짜 의미 혼용 차단
- first/last 및 published/retrieved/available 시간 순서 검사
- feature ID 결정성
- query hash 불변성
- CTI-only/Q2/Q3 분류 규칙
- feature cutoff gate
- discovery/validation feature 중복 차단
- 동일 source family 재인용을 복수 독립 evidence로 계산하지 않음
- 공동 관측 근거 없는 pivot의 자동 AND 결합 차단
- unresolved를 negative로 변환하지 않음

### 11.2 통합 테스트

- CTI assertion → Q0/Q1 query provenance 왕복
- Q0/Q1 raw → observation → feature catalog
- continuity/pivot eligibility → query composer 차단·허용
- partial pagination 재개와 완결 결과 동일성
- query freeze → prospective execution gate
- 동결 후 API 실행이더라도 기저 observed_at이 동결 이전이면 prospective 제외
- prospective hit → candidate ledger idempotency
- candidate → 독립 evidence → grade history
- raw observation → IP/entity/service/fingerprint timeline 재생성
- 마지막 양성 관측과 최초 연속 미관측으로 interval-censored 사건 생성
- 과거 Censys observed_at을 first_candidate_at으로 소급하지 않음

### 11.3 재현성 테스트

- 동일 raw·config·code version에서 동일 feature/query/candidate ID
- raw hash 변조 탐지
- schema drift fixture 처리
- 중단 후 checkpoint 재개 결과와 단일 실행 결과 동등성
- 공개 가능 subset에서 집계 결과 재생성
- 동일 cutoff·manifest에서 timeline materialization 결과 동일성

### 11.4 단계별 완료 게이트

| 단계 | 완료 조건 |
|---|---|
| Stage 0∼1 | 모든 사용 CTI가 source·time·role·review provenance 보유 |
| Stage 2 | 모든 Q0 Seed가 landmark와 continuity를 별도 기록 |
| Stage 3 | 모든 Q1 source pivot이 eligibility와 pagination 상태 보유 |
| Stage 4 | 모든 query-eligible derived feature가 anchor/background/cutoff 근거 보유 |
| Stage 5 | 모든 prospective query가 source feature, hash, budget, schedule과 함께 동결 |
| Stage 6 | 모든 실행이 immutable raw, checkpoint, manifest와 연결 |
| Stage 7 | 모든 후보가 discovery evidence와 validation evidence를 분리 |
| Stage 8 | RQ별 입력 cohort·분모·timeline basis가 명시되고 unresolved가 보존됨 |

---

## 12. 현재 구현과 목표 구현의 차이

### 이미 존재하는 기반

- CTI 검색 protocol, screening, snapshot, IoC 검증 흐름
- Q0 exact-IP 등록
- 제한된 Q1 direct-pivot 계획
- Q0∼Q3 query registry와 query hash
- freeze 및 `valid_for_test_from` prospective gate
- page-token 기반 raw 수집과 checkpoint
- host/service 정규화와 일부 fingerprint graph
- 원시 page·manifest provenance

### 우선 보완할 기능

1. CTI extraction에서 SPKI, SSH key, JA4, port/protocol, HTTP·device 특징 지원
2. Seed continuity assessment의 저장·수동 검토 workflow
3. Q1 singleton precheck와 CTI composite planner
4. pivot identity·pagination·normal reuse 기반 eligibility evaluator
5. feature catalog와 observation provenance
6. anchor/background snapshot과 cutoff별 통계량
7. CTI-only/CTI+Derived/Derived-only/Q3 query composer
8. bounded precheck와 query review artifact
9. prospective schedule runner와 candidate ledger
10. discovery/validation evidence 분리 validator
11. RQ1∼RQ5 analyzer와 public export
12. campaign/IP/entity/service/fingerprint/candidate timeline materializer와 시간 정밀도 모델

추가로 현재 코드에서 먼저 교정할 연결부는 다음과 같다.

- Q1 등록 경로에도 `indicator.available_at <= registered_at/cutoff` 검사를 강제
- 존재하는 `ensure_features_available()`를 query 등록·review·freeze 경로에서 실제 호출
- SPKI·SSH key·JA4·software 필드를 모델뿐 아니라 Censys normalizer에서도 채움
- HTTP·portset의 restricted queryable canonical value와 공개용 hash를 분리
- parser가 산출하는 candidate identity를 버리지 않고 candidate ledger에 영속화
- Q3에 SSH/JA4/HTTP/portset 관계, expansion depth, discovery-edge 표시 추가
- `QueryStatus.VALIDATED`를 campaign validation과 구분되는 `PRECHECKED/REVIEWED` 의미로 교정
- RQ2 query 지원 규칙을 Q0뿐 아니라 사전 동결된 prospective cohort query까지 확장
- indicator ID를 값 중심으로 안정화하고 복수 CTI assertion이 같은 indicator를 참조하도록 정리
- 현재 `vendor_first_seen/vendor_last_seen`, `observed_at`, `collected_at`을 논문용 campaign/IP/service timeline과 연결하되 원시값과 파생값을 분리

---

## 13. 구현 순서

### Phase A. Schema와 연구 게이트

- 모델 enum·테이블 확장
- 공통 `TemporalValue`와 timeline materialized view 구현
- seed/pivot/feature/candidate 상태 구현
- time leakage와 evidence-role validator
- migration과 fixture 테스트

완료 기준: live Censys 없이 모든 상태 전이와 차단 규칙을 fixture로 검증한다.

### Phase B. Q0/Q1 bootstrap

- CTI feature scope 확장
- continuity assessor
- Q1 singleton precheck와 CTI composite planner
- 전체 pagination eligibility workflow

완료 기준: 각 Seed/pivot에 `derived_pivot_allowed` 또는 명시적 차단 사유가 존재한다.

### Phase C. Feature와 background

- canonical feature extractor
- entity epoch와 relation graph
- matched background builder
- cutoff별 feature statistics와 eligibility review

완료 기준: query-eligible feature마다 분자·분모·cutoff·source가 재생 가능하다.

### Phase D. Query composer와 freeze

- 네 query family 생성
- precheck, human review, freeze manifest
- query version·schedule·budget 동결

완료 기준: 미래 실행에 필요한 모든 query가 immutable hash와 `valid_for_test_from`을 가진다.

### Phase E. Prospective와 validation

- due-query scheduler
- idempotent collector와 candidate ledger
- evidence attachment와 adjudication
- delayed CTI linkage

완료 기준: 같은 query/entity의 재실행이 후보를 중복 생성하지 않고 evidence history를 보존한다.

### Phase F. RQ 분석

- RQ별 cohort materialization
- metric·CI·sensitivity analysis
- baseline/ablation과 비용 분석
- 공개 가능한 재현 패키지

완료 기준: 각 논문 표·그림이 run manifest, code version, config hash, input/output hash로 추적된다.

---

## 14. 실패 시 연구 범위 조정

| 상황 | 허용되는 결론 |
|---|---|
| Q0 historical continuity 부족 | 현재 landmark 측정만 보고하고 campaign continuity 주장을 제한 |
| Q1 대부분 broad/shared | CTI-only 한계를 결과로 보고하고 조합·background 분석에 집중 |
| query-eligible derived feature 부족 | Q2 discovery 성능 주장 축소, RQ1∼RQ3 측정 논문으로 전환 |
| 미래 independent positive 부족 | precision 확정 대신 yield, resolution rate, 후보 churn과 사례 연구 보고 |
| Censys 가시성 부족 | Censys 관측 범위의 결과로 제한하고 다른 telemetry는 별도 확장 연구로 분리 |
| unresolved 비율이 높음 | unresolved를 보존하고 precision 범위와 검토부담 중심으로 보고 |

---

## 15. 최종 구현 불변식

구현 완료 후 다음 조건은 항상 참이어야 한다.

1. 모든 indicator·feature·query·candidate는 원문 또는 관측 provenance로 역추적된다.
2. Q0 현재 관측과 campaign continuity는 서로 다른 상태다.
3. Q1 singleton broad 결과는 campaign cohort가 아니다.
4. Q2는 기존 CTI pivot을 버리지 않으며 CTI+Derived와 Derived-only를 분리한다.
5. 쿼리 일치는 campaign membership이 아니라 technical similarity candidate를 만든다.
6. 동결 이후 동일 query version은 수정되지 않는다.
7. 모든 prospective 실행은 `valid_for_test_from` 이후다.
8. discovery evidence는 같은 후보의 validation evidence가 될 수 없다.
9. unresolved는 negative가 아니다.
10. RQ2의 validated ORB churn과 candidate churn은 분리된다.
11. RQ3 rarity는 기준집단과 cutoff를 명시한다.
12. RQ5 비교는 동일 cutoff·universe·schedule·alert budget을 사용한다.
13. CTI 공개일, CTI 보고 활동일, Censys 관측일, 연구 후보 생성일, 검증일은 서로 대체되지 않는다.
14. 최초·최종 관측일은 append-only 원시 event에서 cutoff별로 재생성 가능해야 한다.
15. 마지막 양성 관측일은 소멸일이 아니며 사건은 관측기회 사이의 구간으로 처리한다.

이 불변식이 지켜질 때 본 파이프라인은 단순한 Censys 쿼리 생성기가 아니라, CTI에서 파생한 인프라 특징의 지속성·판별력·미래 발견 가능성을 객관적으로 평가하는 논문용 실험 시스템이 된다.
