# ORB 논문 구성안 — CTI·Censys 파이프라인 정합 개정본

| 항목 | 내용 |
|---|---|
| 문서 성격 | 논문 집필 및 실험 결과 배치를 위한 개정 구성안 |
| 작성일 | 2026-07-15 |
| 우선 적용 문서 | `2026-07-15-CTI-Censys-파이프라인-구현-설계도.md` |
| 서사·연구 목적의 기반 | `2026-07-13-ORB-논문-구성안.md` |
| 논문 유형 | Historical landmark 측정과 prospective candidate discovery를 결합한 하이브리드 종단 연구 |
| 현재 주장 수준 | 파이프라인·실험 설계 단계이며, 미래 독립 검증 전에는 campaign discovery 성능을 입증한 것으로 쓰지 않음 |

---

## Part A. 비교·연구 의사결정

### A1. 비교 결론과 개정 원칙

#### A1.1 문서 간 우선순위

기존 구성안은 다음 요소의 상위 프레임으로 유지한다.

- ORB를 중심으로 한 문제 정의
- IP 중심 CTI의 한계라는 연구 동기
- historical landmark와 prospective study를 결합한 연구 서사
- RQ1∼RQ5의 큰 질문
- 논문의 학술적 기여와 출판 전략

구현 설계도는 다음 요소의 운영 정의로 우선 적용한다.

- 데이터 생성과 provenance
- Q0∼Q3 및 M1∼M5의 구분
- Seed continuity와 pivot eligibility
- 특징 발굴, matched background 및 cutoff별 통계
- query 개발·검토·동결
- 전향 관측의 시간 기준
- candidate ledger와 미래 독립 검증
- RQ별 cohort·분모·지표
- 시간 누수, 재현성 및 실패 차단 규칙

따라서 논문의 주제를 “Censys 쿼리 구현”으로 바꾸지 않는다. 기존 ORB 연구 질문을 유지하되, 설계도의 파이프라인을 실험 프로토콜로 채택한다.

#### A1.2 핵심 개정 사항

| 쟁점 | 기존 구성안 | 설계도 반영 후 개정 |
|---|---|---|
| 후보의 명칭 | 복수 특징 일치 cohort를 campaign-linked로 부를 여지 | query hit는 `technical-similarity candidate`; 독립 증거를 통과한 부분집합만 campaign-linked |
| 복수 특징 | 서로 “독립적인” 특징으로 표현 | 통계적 독립성이 입증되지 않으면 “비중복적·교차 feature-family 특징”으로 표현 |
| 전향 평가 시작 | 2026-07-13을 단일 \(t_0\)로 제안 | 2026-07-13은 연구 프로그램 시작일일 수 있으나 성능 평가는 query version별 `valid_for_test_from`에서 시작 |
| RQ2 대상 | 전향적으로 관측한 ORB | validated ORB, probable link, technical-similarity candidate의 churn을 분리 |
| RQ3 희소성 | 정상 인터넷에서의 희소성 | 표본 범위를 명시한 `matched-background prevalence`와 `reference-set rarity` |
| 후보 점수 | 개념적 선형 점수식 중심 | 실제 구현·동결된 score 또는 규칙만 사용하며 feature eligibility와 query-family 비교를 우선 |
| 미관측 사건 | 연속 3회 미관측 예시 | 횟수는 사전 등록하고, 사건은 마지막 양성 관측과 최초 적격 연속 미관측 사이의 구간으로 처리 |
| RQ4 lead time | 후보 alert와 독립 공개 간 차이 | append-only 원장의 `first_candidate_at`부터 후보 이후 독립 evidence의 최초 공개까지 계산 |
| RQ5 label | 미확인 후보 처리 불명확 | `unresolved`를 negative로 바꾸지 않고 precision 범위와 resolution rate를 함께 보고 |
| baseline | 피드·exact pivot·ML·graph의 폭넓은 목록 | M1∼M5를 핵심 비교축으로 고정하고 외부 baseline은 동일 시점·예산 조건 충족 시 보조 사용 |
| 재현성 | 산출물 고정 권고 | raw·manifest·candidate ledger의 append-only 보존, hash·cutoff·version 추적 및 누수 감사 |

#### A1.3 논문의 검증 사슬

논문 전반은 다음 단계를 건너뛰지 않는다.

```text
CTI 원문과 역할 assertion
→ 적격 Q0 Seed / Q1 direct pivot
→ Q0 landmark·continuity 및 Q1 precheck·enrichment
→ derived feature candidate
→ anchor support와 matched-background prevalence
→ query-eligible feature
→ 검토·동결된 M1∼M5 query
→ technical-similarity candidate
→ 검색에 사용하지 않은 미래 독립 evidence
→ probable / high-confidence / confirmed campaign-linked subset
```

검색 일치, 기술적 유사성, 캠페인 연계는 서로 다른 결과로 보고한다.

---

### A2. 최종 권고와 논문의 정체성

이 연구는 일반적인 IoC 수명 분석이나 Censys 쿼리 최적화 논문이 아니다. 다음 세 축을 결합한 **ORB 인프라 측정 및 전향적 후보 우선순위화 연구**로 구성한다.

1. 과거 공개 ORB Seed의 현재 landmark 관측성과 캠페인 continuity를 분리해 측정한다.
2. IP·service·fingerprint·cluster의 전향적 persistence와 churn을 evidence grade별로 비교한다.
3. CTI와 Censys enrichment에서 얻은 특징으로 동결 query를 구성하고, 미래 독립 evidence로 후보의 campaign linkage와 운영적 효용을 평가한다.

과거 Censys 이력이 짧기 때문에 오래된 ORB의 정확한 생성·소멸 시점을 소급해서 주장하지 않는다.

- 과거 공개 자료: **public-age-stratified landmark analysis**
- 연구 시작 이후 반복 관측: **prospective interval-censored persistence and churn**
- 동결 query 이후 신규 후보: **point-in-time candidate discovery and delayed validation**

논문의 핵심 산출물은 “더 많은 IP 목록”이 아니라 다음 네 가지다.

- 측정 가능한 주장과 측정할 수 없는 주장의 경계
- 지속성과 판별력을 함께 평가한 인프라 특징
- 시간 누수를 차단한 전향적 후보 원장
- Track A 활성화 시 동일 예산에서 query family별 yield·정밀도 범위·분석 부담 비교

현재 기본 제출 범위는 **Track B: RQ1∼RQ3 중심 persistence and churn 측정 논문**으로 둔다. RQ4∼RQ5는 다음 조건을 prospective test 시작 전에 수치로 사전 등록하고 모두 충족했을 때만 본 분석으로 활성화한다.

- 비교 가능한 M1∼M5 query version과 evaluation window가 동결됨
- 사전 정한 최소 관측기간과 실행 완결률을 충족함
- 미래 독립 evidence의 positive·resolved 사례와 observability가 사전 정한 최소량을 충족함
- 동일 alert budget에서 분석가 검토시간과 API 비용을 측정할 수 있음
- leakage audit가 fail-closed 조건을 모두 통과함

조건을 충족하지 못하면 RQ4∼RQ5는 protocol/pilot 또는 후속 연구로 보고하며, 부족한 표본을 결과에 맞춰 사후 완화하지 않는다.

---

### A3. 권장 제목

#### A3.1 Track A 제목

**From Ephemeral Indicators to Persistent Infrastructure: A Prospective Measurement and Discovery Study of Operational Relay Box Networks**

#### A3.2 Track B 기본 제목

**Tracking the Network, Not the IP: Longitudinal Measurement of Operational Relay Box Infrastructure**

#### A3.3 대안

- **Landmark Persistence and Infrastructure Churn in Operational Relay Box Networks**
- **Beyond Stale ORB Indicators: Prospective Infrastructure Measurement and Candidate Discovery**
- **From Static CTI to Persistent Infrastructure Signals: Tracking Operational Relay Box Networks over Time**

`Early Detection`은 실제 공격 발생 전 탐지를 입증한 것으로 오해될 수 있으므로 사용하지 않는다. `Discovery`, `Candidate Discovery`, `Prioritization`, `Public-Disclosure Lead Time`을 우선한다.

---

### A4. 중심 주장과 기여

#### A4.1 검증할 중심 질문

> 역할과 시간 연속성이 검토된 CTI anchor에서 발굴한 비중복적 인프라 특징을 cutoff별 matched background와 비교하고 동결 query로 전향 관측하면, 단일 CTI pivot보다 미래에 독립적으로 확인되는 ORB 캠페인 후보를 동일한 분석 예산에서 더 효과적으로 우선순위화할 수 있는가?

이 문장은 연구 질문이며 현재 입증된 결론이 아니다.

#### A4.2 결과 전 허용되는 중간 주장

> 본 연구는 CTI assertion, Censys 관측, 특징 provenance, query freeze 및 후보 검증을 시간적으로 분리하는 ORB 인프라 후보 추적 파이프라인과 평가 프로토콜을 설계한다.

구현과 데이터가 실제로 준비된 범위에 따라서만 “구축했다”, “관측했다”, “향상했다”로 시제를 바꾼다.

원고 작성 전 각 구성요소에 `planned`, `implemented`, `validated` 상태를 부여한다. 최소 점검 대상은 continuity·pivot-eligibility workflow, matched background, cutoff 통계, M1∼M5 composer, freeze artifact, prospective scheduler, candidate ledger, evidence-role validator, RQ analyzer, timeline materializer 및 leakage audit다. `implemented` 이전 기능은 방법의 계획으로, `validated` 이전 기능은 성능 기여가 아닌 구현 상태로만 기술한다.

#### A4.3 계획된 기여

1. 공개·제한 CTI를 출처 계보, 역할, 시간 및 evidence 수준으로 정규화하는 재현 가능한 ORB 코퍼스 프로토콜을 제시한다.
2. 현재 host 관측과 campaign continuity를 분리하고 IP·entity epoch·service·fingerprint의 지속성을 서로 다른 시간축에서 측정한다. Cluster는 membership 알고리즘과 split/merge 규칙이 동결된 경우에만 포함한다.
3. 적격 anchor와 matched background를 이용해 Censys-derived feature의 persistence와 reference-set discriminativeness를 cutoff별로 평가한다.
4. **Track A 조건부:** CTI-only, CTI+Derived, Derived-only 및 graph expansion query를 동결하고 append-only 원장에서 point-in-time 후보를 보존하는 방법을 제시한다.
5. **Track A 조건부:** 검색 특징과 독립 검증 evidence를 분리한 상태에서 validated yield, precision 범위, lead time, 미해결 부담 및 API 비용을 비교한다.

기여 문장은 결과가 확보되기 전에는 미래형 또는 방법 제시형으로 유지한다.

---

### A5. 연구질문과 운영 경계

#### RQ1. Historical landmark observability and continuity

> 과거 공개된 ORB Seed는 landmark 시점에 host·service·fingerprint 수준에서 어느 정도 관측되며, 현재 관측된 서비스가 CTI 활동 시점의 캠페인 관련 정체성을 유지한다는 evidence는 어느 수준인가?

- 분석 단위: accepted Seed indicator를 Q0 exact-IP query로 관측한 결과
- 시간축: `indicator_first_public_at`부터 `landmark_at`까지의 공개 연령
- 결과: landmark 상태와 continuity 등급을 별도 보고
- 금지: landmark 미관측을 정확한 소멸시간으로 해석

#### RQ2. Evidence-stratified prospective persistence and churn

> 전향 관측에서 IP·entity epoch·service·fingerprint의 persistence와 churn은 어떻게 다르며, 그 양상은 evidence grade에 따라 어떻게 달라지는가? 재현 가능한 membership 규칙이 동결된 경우 cluster를 추가한다.

- 분석 층: cohort entry 시점의 confirmed/high-confidence, probable, technical-similarity-only
- 사건: 마지막 양성 관측과 최초 적격 연속 미관측 사이의 interval-censored event
- 결과 명칭: `validated ORB churn`과 `candidate churn`을 분리
- 금지: 모든 query hit를 ORB로 합쳐 단일 생존곡선 작성
- 시간 편향 통제: 미래의 최종 grade를 과거 관측구간에 소급하지 않으며, 주 분석은 entry-grade 고정, 보조 분석은 cutoff 시점까지 알려진 grade를 time-varying state로 처리

#### RQ3. Persistent and discriminative infrastructure features

> 어떤 인프라 특징이 적격 anchor에서 반복·지속되며, 동일 시점과 서비스·제품·provider 조건을 맞춘 reference set보다 낮은 prevalence와 높은 lift를 보이는가?

- 분석 단위: cutoff별 feature 및 feature family
- 핵심량: anchor support, background prevalence, reference lift, temporal support
- 필수 보고: 분자·분모, 관측기회, background 정의, cutoff
- 금지: 제한된 background 결과를 `global rarity`로 표현

#### RQ4. Point-in-time candidate discovery and delayed validation — Track A 조건부

> cutoff 이전 정보로 동결한 Q2/Q3 query가 `valid_for_test_from` 이후 생성한 후보 중 얼마나 많은 후보가 검색에 사용하지 않은 미래 독립 evidence로 ORB 또는 해당 캠페인과 연결되는가?

- 분석 단위: append-only ledger에 동결 이후 처음 생성된 candidate
- 시작시각: 실제 `first_candidate_at`
- 검증: 후보 생성 이후 사용 가능해진 독립 evidence
- 금지: 과거 Censys 관측시각으로 후보 생성일을 소급하거나 discovery feature를 validation으로 재사용

#### RQ5. Operational benefit under equal budgets — Track A 조건부

> 동일 cutoff·universe·schedule·evaluation window·alert budget에서 M1∼M5는 validated yield, precision 범위, 미해결 검토부담, lead time 및 API 비용 면에서 어떻게 다른가?

- 핵심 비교: M1 eligible·frozen CTI singleton discovery, M2 CTI-only composite, M3 CTI+Derived, M4 Derived-only, M5 graph expansion
- 핵심 지표: validated yield@K, verified/conservative precision, resolution rate, confirmed FP/day, unresolved/day, cost per validated positive
- 금지: 전체 ground truth 없이 일반 recall 주장, unresolved를 임의의 negative로 처리

#### A5.1 RQ별 입력과 출력 요약

| RQ | 전용 입력 | 주 출력 | 핵심 내부 타당성 조건 |
|---|---|---|---|
| RQ1 | 공개 Seed IP와 Q0 landmark 관측 | 공개 연령별 관측 상태·continuity | 현재 응답과 과거 캠페인 정체성 분리 |
| RQ2 | 사전 정의 cohort와 반복 snapshot | 계층·등급별 interval/churn | 관측기회와 미관측 원인 분리 |
| RQ3 | 적격 anchor, feature, matched background | cutoff별 feature 통계 | 미래 정보와 Q1 내부 빈도만 사용하지 않음 |
| RQ4 | 동결 Q2/Q3, candidate ledger, 미래 evidence | validation outcome·lead time | `first_candidate_at` 및 evidence independence |
| RQ5 | 동일 조건의 M1∼M5 실행 | yield·정밀도 범위·부담·비용 | 동일 예산과 기간, unresolved 보존 |

---

### A6. 핵심 개념과 상태 정의

#### A6.1 분석 단위

- **IP**: 네트워크 주소 문자열이며 운영 주체의 지속적 정체성과 동일하지 않다.
- **Entity epoch**: 동일 IP에서도 재할당 또는 서비스 정체성 변경 전후를 분리한 관측 단위다.
- **Service**: entity epoch의 IP·port·protocol 조합이다.
- **Fingerprint**: 인증서·SPKI·SSH key·JARM/JA4·HTTP·service 특징 등이다.
- **Cluster**: cutoff 시점까지 사용 가능한 node–service–fingerprint 관계로 구성한 군집이다.

#### A6.2 데이터·특징 용어

| 용어 | 논문 내 의미 |
|---|---|
| CTI Seed | CTI가 캠페인 인프라라고 직접 주장한 IP |
| CTI direct pivot | CTI 원문에 직접 포함된 non-IP 특징이며 queryability는 별도 eligibility 단계에서 평가 |
| Derived feature | 적격 Q0/Q1 Censys 관측에서 새로 추출한 특징 |
| Anchor | feature 개발의 기준점으로 허용된 Seed 또는 entity epoch |
| Enrichment universe | 적격 Q1 query의 전체 결과로서 campaign cohort가 아님 |
| Matched background | 시점·제품·서비스·provider 조건을 맞춘 비교집단 |
| Discovery feature | query 또는 candidate score에 사용한 특징 |
| Validation feature | 해당 후보 검색에 사용하지 않은 독립 평가 특징 |

명칭은 입력 객체와 실행 단계를 구분한다. `Seed IP`와 `direct pivot`은 CTI 입력 객체이며, Q0∼Q3는 query class, M1∼M5는 RQ5 비교 method다. “Q0 Seed” 또는 “Q1 pivot”이라는 축약어를 사용할 때도 객체 자체와 이를 실행한 query를 혼동하지 않는다.

#### A6.3 Seed continuity

| 상태 | 의미 | derived-feature source 사용 |
|---|---|---|
| `continuous` | 과거와 현재 서비스 정체성이 강하게 이어짐 | 허용 |
| `probable` | 일부 독립 특징이 유지되고 재할당 반증이 없음 | 사전 기준 통과 시 허용 |
| `unknown` | 동일성을 판정할 자료가 부족 | campaign query에 금지 |
| `reassigned` | 운영 주체 또는 서비스가 바뀐 정황 | 금지 |
| `contradicted` | CTI의 역할·캠페인 주장과 충돌 | 금지 |

#### A6.4 Direct-pivot eligibility

| 상태 | 의미 | query 사용 |
|---|---|---|
| `eligible` | identity·queryability·pagination·정상 재사용성 검토 통과 | 단독 또는 조합 |
| `combination_only` | 단독으로 broad/shared이나 보조 특징으로 사용 가능 | 조합만 허용 |
| `blocked` | 잘못된 정체·정상 기본값·비검색 필드·과도한 위험 | 금지 |
| `pending` | 결과 공간·정체·prevalence 평가 미완료 | 동결 query에 금지 |

#### A6.5 Feature와 candidate 상태

상태 namespace를 명시해 같은 단어의 의미를 구분한다. 예를 들어 `SeedContinuity.probable`은 Seed 정체성 평가이고 `CandidateGrade.probable_campaign_link`는 후보의 캠페인 evidence grade다.

Derived feature의 상태는 다음 순서를 따른다.

```text
observed
→ recurrent
→ discriminative_candidate
→ query_eligible
→ validated_campaign_feature 또는 blocked
```

Candidate evidence grade는 다음 순서를 따른다.

```text
raw_hit
→ technical_similarity_candidate
→ probable_campaign_link
→ high_confidence_campaign_link
→ confirmed_campaign_member
```

별도로 `broad_or_shared_pivot`, `contradicted`, `unresolved`, `unobservable`을 유지한다. 반복 query match는 persistence evidence일 뿐 campaign validation은 아니다.

상태 전이는 단조 상승만 허용하지 않는다. 새로운 contradiction, 재할당 또는 source 정정이 확인되면 후보 grade를 강등하거나 `contradicted`로 전이할 수 있으며, 모든 전이의 이전·이후 상태와 근거 시간을 보존한다. 내부 상태 ID `confirmed_campaign_member`는 사전 정의한 membership evidence를 충족할 때만 사용하고, 그보다 약한 결과는 원고에서 `confirmed campaign link` 이상으로 확대하지 않는다.

---

## Part B. 개정 논문 본문 목차

### 1. Introduction

#### 1.1 ORB의 운영적 위협

- ORB가 침해 엣지 장비와 VPS를 통해 공격 출처를 은폐하는 구조를 설명한다.
- ORB를 단순 악성 IP 목록이 아니라 교체되는 node와 상대적으로 지속되는 service·fingerprint·relation의 결합으로 제시한다.

#### 1.2 IP 중심 CTI의 한계

- IP 재할당, 공유 인프라, 짧은 활성기간, 공개 지연을 제시한다.
- 현재 IP 응답 여부가 과거 캠페인 연속성이나 현재 악성 사용을 보장하지 않음을 명시한다.

#### 1.3 연구 공백

- 짧은 historical telemetry로 과거 ORB의 정확한 수명을 복원할 수 없다는 한계를 밝힌다.
- 기존 연구가 landmark observability, 다계층 churn, point-in-time 후보 생성과 미래 독립 검증을 하나의 프로토콜로 결합하지 못했다는 공백을 제시한다.

#### 1.4 연구 접근

- historical landmark study와 prospective study를 결합한 전체 흐름을 한 문단과 Figure 1로 설명한다.
- `query hit → candidate → independent validation`의 단계를 서론에서부터 분리한다.
- 연구 프로그램 시작일과 query별 전향 평가 시작일이 다름을 짧게 밝힌다.

#### 1.5 연구질문과 기여

- RQ1∼RQ5를 요약한다.
- 기여는 코퍼스, 다계층 측정, feature eligibility, 전향 후보 원장, 공정한 운영 평가의 다섯 항목으로 제시한다.

**서론에서 금지할 표현**

- 결과 전 “ORB를 조기 탐지했다”
- query hit를 “새 ORB”로 지칭
- Censys first observed를 감염·활성 시작으로 해석
- 구현 설계상의 목표 기능을 이미 완성된 기여로 서술

---

### 2. Background and Problem Formulation

#### 2.1 ORB 정의와 위협 모델

```text
공격 운영자
→ 관리·스테이징 인프라
→ 중간 relay/ORB
→ 침해된 SOHO·VPN·IoT 출구 노드
→ 공격 대상
```

역할은 실제 사례에 따라 다를 수 있으며, 모든 node를 동일한 ORB 역할로 묶지 않는다.

#### 2.2 ORB·봇넷·프록시·C2의 차이

- 기술적 중첩과 운영 목적의 차이를 구분한다.
- relay infrastructure와 actor attribution을 동일시하지 않는다.

#### 2.3 분석 단위와 entity epoch

- IP, entity epoch, service, fingerprint, cluster를 정의한다.
- 동일 IP 문자열이 재할당 이후 다른 entity epoch가 될 수 있음을 설명한다.

#### 2.4 Indicator, pivot, feature, anchor, background

- CTI에 직접 존재한 값과 Censys에서 파생한 값을 분리한다.
- enrichment universe와 matched background의 역할을 구분한다.

#### 2.5 검색 일치와 캠페인 연계

- raw hit, technical similarity, probable link, high-confidence link, confirmed membership의 evidence ladder를 제시한다.
- discovery evidence와 validation evidence의 비순환성을 설명한다.

#### 2.6 시간 의미와 측정 경계

최소 다음 시간을 분리한다.

- CTI가 보고한 활동 first/last seen
- 문서 `published_at`과 사실의 `first_public_at`
- 연구의 `retrieved_at`과 `available_at`
- Censys `observed_at`과 연구 `collected_at`
- query `frozen_at`과 `valid_for_test_from`
- 후보 `first_candidate_at`
- 검증 evidence의 `published_at`, `available_at`, `first_validated_at`

날짜 정밀도가 낮은 값을 임의의 자정 timestamp로 바꾸지 않고 범위와 불확실성으로 보존한다.

---

### 3. Related Work

#### 3.1 ORB와 침해 엣지 인프라

- ORB, covert relay, compromised edge device, proxy network 관련 연구와 CTI를 정리한다.

#### 3.2 IoC decay와 feed freshness

- IP IoC의 수명, 공개 지연, stale indicator 및 feed 유지기간 연구를 검토한다.

#### 3.3 Infrastructure fingerprinting, pivoting and clustering

- 인증서·SPKI·SSH key·TLS/HTTP fingerprint, graph pivot 및 malicious infrastructure clustering 연구를 검토한다.

#### 3.4 Longitudinal internet measurement and temporal evaluation

- 반복 인터넷 관측, 관측 편향, interval censoring, point-in-time 평가와 미래정보 누수 문제를 검토한다.

#### 3.5 연구 차별점

다음 차이를 명시한다.

> 본 연구는 ORB를 대상으로 공개 연령별 landmark 관측, evidence-grade별 다계층 churn, matched-reference feature 평가, query freeze 및 미래 독립 evidence에 의한 후보 검증을 하나의 전향 프로토콜로 결합한다.

문헌 검토 결과에 따라 표현을 조정하며 “최초”라는 표현은 체계적 검토 근거 없이는 사용하지 않는다.

---

### 4. Data and Corpus Construction

#### 4.1 코퍼스 검색 프로토콜

- 검색 기간·검색어·출처·포함/제외 기준을 protocol과 hash로 고정한다.
- 기존 curated, systematic public, commercial, prospective validation 자료를 구분한다.
- 원문과 OCR sidecar의 hash를 보존한다.

#### 4.2 문서 provenance와 source independence

- 원출처, 번역, 재게시, 후속 보고서를 관계로 저장한다.
- 동일 원출처의 재인용을 하나의 source family로 묶는다.
- 재게시를 복수의 독립 증거로 계산하지 않는다.

#### 4.3 Indicator–assertion–role 정규화

- indicator 값과 캠페인·역할 주장을 분리한다.
- source, extraction, role confidence와 reviewer status를 별도로 기록한다.
- ORB, relay, controller, staging, C2, scanner, victim, sinkhole 역할을 분리한다.
- `accepted`가 아닌 assertion은 자동 query 생성에 사용하지 않는다.

#### 4.4 Seed IP와 direct pivot

- `Seed IP`는 CTI가 캠페인 인프라라고 직접 주장한 입력 객체이며 이를 조회하는 exact-IP query class가 Q0다.
- `Direct pivot`은 CTI 원문에 직접 포함된 domain, certificate, SPKI, SSH key, JARM/JA4, HTTP 또는 service 특징이며 이를 precheck·enrichment하는 query class가 Q1이다.
- certificate, SPKI 및 malware/file hash의 의미를 혼동하지 않는다.

#### 4.5 인터넷 관측 자료

- Censys host/service snapshot
- 인증서 또는 CT 자료
- 허용되는 경우 passive DNS
- ASN·BGP·RDAP
- 독립 CTI 피드와 후속 보고서

각 소스에 coverage, 관측시각 의미, 접근 등급, 라이선스 및 결측 유형을 표로 제시한다.

#### 4.6 Reference-set 후보 풀

- matched background를 만들기 위한 동시점·제품·서비스·provider 후보 풀의 출처를 설명한다.
- reference set이 전체 인터넷 모집단을 대표하지 않는다는 점을 명시한다.

#### 4.7 공개·상용·restricted 자료

- Public-only subset: 재현성 평가
- Commercial-only subset: 제한된 외적 검증
- Combined subset: 운영 coverage 분석
- Active victim 또는 민감 feature: restricted 저장 및 집계·HMAC 식별자 중심 공개

비공개 commercial CTI에는 공개 개념이 적용되지 않으므로 `first_public_at`을 임의 생성하지 않는다. access class, 계약상 이용 가능해진 `available_at`과 공급자가 보고한 활동시각을 분리해 기록하며, public-age landmark와 public-disclosure lead time에서는 제외하거나 별도 층으로 보고한다.

#### 4.8 데이터 흐름과 attrition

원문 수, assertion 수, accepted indicator 수, Q0/Q1 수, continuity·eligibility 통과 수를 흐름도로 제시한다. 이후 결과 장의 gate 통과율과 같은 분모를 사용한다.

---

### 5. Study Design and Measurement Protocol

#### 5.1 Stage 0∼8 연구 흐름

Figure 2에서 다음 흐름을 제시한다.

```text
Stage 0  protocol·corpus freeze
Stage 1  indicator/assertion normalization
Stage 2  Q0 landmark·continuity
Stage 3  Q1 precheck·CTI-only enrichment
Stage 4  derived-feature eligibility
Stage 5  query review·freeze
Stage 6  prospective collection·candidate ledger
Stage 7  independent evidence·adjudication
Stage 8  RQ-specific datasets
```

CLI, 소스 디렉터리 및 전체 데이터베이스 스키마는 본문이 아니라 부록·artifact 문서로 이동한다.

#### 5.2 공통 raw layer와 RQ별 cohort

- RQ1∼RQ5는 immutable raw observation을 공유한다.
- 각 RQ는 서로 다른 분석 단위, eligibility, 분모, cutoff 및 결과 테이블을 가진다.
- Q2/Q3 hit로 RQ1 Seed cohort나 confirmed RQ2 cohort를 구성하지 않는다.

#### 5.3 RQ1 historical landmark design

공개 후 연령 구간을 반개구간으로 사전 정의한다.

- \([0,1)\)개월
- \([1,3)\)개월
- \([3,6)\)개월
- \([6,12)\)개월
- \([12,24)\)개월
- \([24,\infty)\)개월

`first_public_at`의 정밀도 구간이 둘 이상의 공개 연령 bin에 걸치면 단일 bin에 임의 배정하지 않는다. 주 분석에서는 `age_bin_uncertain`으로 분리하고, 가능한 최저·최고 bin 배정을 sensitivity analysis로 보고한다. 비공개 commercial assertion처럼 `first_public_at`이 적용되지 않는 자료는 public-age RQ1에서 제외하거나 별도 access-class 층으로 분석한다.

landmark 결과는 중첩 가능한 관측 flag와 배타적인 수집 상태를 분리한다.

- 계층별 Boolean flag: host observed, service observed, fingerprint observed
- 배타적 collection status: success, not found, not scanned, no response, API error, time unresolved

continuity는 별도 판정표로 보고한다. RQ1 결과를 “과거 ORB 생존시간”이라고 부르지 않는다.

#### 5.4 전향 수집과 query별 time origin

- 연구 프로토콜 시작일과 query version별 전향 평가 시작일을 분리한다.
- `executed_at >= valid_for_test_from`이어야 한다.
- 전향 성능에 포함되는 기저 Censys `observed_at`은 반드시 `valid_for_test_from` 이후여야 한다.
- 실행은 동결 이후이지만 기저 관측이 동결 이전이면 operational candidate로 기록할 수 있으나 `pre_freeze_observation`으로 표시하고 RQ4·RQ5 prospective performance에서 제외한다.
- 기저 관측시각을 알 수 없으면 `prospective_time_unresolved`로 분리하고 전향 성능에서 제외한다.

수집 주기는 파일럿에서 비용과 변화율을 근거로 정하고, query·budget과 함께 동결한다.

#### 5.5 Observation opportunity와 결측

다음을 서로 다른 상태로 기록한다.

- 예정 실행에서의 적격 음성 관측
- 실행 누락
- API 오류
- Censys 미스캔 또는 not-scanned
- pagination 미완료
- 관측시각 불명

실행 누락과 API 오류를 host 음성으로 보간하지 않는다.

#### 5.6 Entity epoch와 사건 정의

- IP 재할당이나 서비스 정체성 변경 시 새 entity epoch를 만든다.
- 마지막 양성 관측은 소멸시각이 아니다.
- 단순 미관측 사건은 `last_positive_observed_at`과 `first_consecutive_missing_at` 사이의 구간으로 처리한다.
- 연속 미관측 횟수와 허용 공백은 분석 전 사전 등록한다.
- host 미관측, service 종료, certificate 변경, port 변경, 재할당, fingerprint 이동을 구분한다.

#### 5.7 시간 모델과 cutoff materialization

모든 파생 timeline은 cutoff별 raw event에서 재생성한다.

- IP observation timeline
- entity-epoch timeline
- service timeline
- fingerprint timeline
- cluster membership timeline
- candidate and validation timeline

각 materialization에 input manifest hash, code version, config hash와 computed time을 보존한다.

Cluster 분석을 유지하려면 prospective test 전에 다음을 동결한다.

- node·service·fingerprint graph의 허용 node와 edge family
- edge의 `available_at`, `valid_from`, `valid_to` 사용 규칙
- membership 또는 connected-component 알고리즘과 parameter
- 최대 expansion depth, cycle 및 중복 제거 규칙
- 안정적 cluster ID, split·merge 및 종료 처리
- cluster membership 사건의 관측기회와 interval 정의

이 정의와 구현이 준비되지 않으면 cluster persistence는 RQ2의 주 추론에서 제외하고 탐색적 기술 결과 또는 후속 연구로 낮춘다.

#### 5.8 Evidence-grade별 분석 cohort

RQ2는 최소 세 층으로 나눈다.

1. confirmed/high-confidence campaign link
2. probable campaign link
3. technical-similarity-only candidate

분석 단위와 evidence policy를 결과표마다 명시한다. 주 분석은 cohort entry 당시 이용 가능했던 grade로 고정한다. 보조 분석은 각 cutoff까지 알려진 grade를 time-varying state로 갱신할 수 있으나, 최종 validation grade를 과거 exposure 기간에 소급 적용하지 않는다. 서로 다른 층을 합친 결과는 보조 민감도 분석으로만 제시한다.

#### 5.9 통계 분석

- RQ1: 공개 연령별 상태 비율과 불확실성
- RQ2: interval-censored persistence/churn과 competing event
- RQ3: cutoff별 feature effect size와 관측기회
- RQ4: delayed validation outcome과 lead-time 분포
- RQ5: query family별 paired 또는 동일-window 비교
- campaign·ASN 등 군집 구조를 고려한 불확실성
- campaign-level bootstrap 또는 사전 지정한 적절한 재표집

구체적 추정법과 표본 충분성 판단 규칙은 development/pilot split에서만 선택하고 prospective test의 `valid_for_test_from` 이전에 동결한다. test unlock 이후 변경은 새 query/analysis version 또는 명시적 protocol amendment로 기록하며 주 결과를 교체하지 않고 sensitivity result로 병기한다.

---

### 6. CTI-Guided Infrastructure Candidate Discovery

#### 6.1 Q0∼Q3 및 M1∼M5 분류

| Query class | Variant | 논문 내 역할 |
|---|---|---|
| Q0 | exact IP | landmark, continuity, Seed enrichment; 신규 발견 baseline 아님 |
| Q1 | singleton preflight | identity·queryability·broadness·비용 확인 |
| Q1 | CTI-only composite/singleton discovery | CTI-only enrichment와 M1/M2 baseline |
| Q2 | CTI+Derived | 제안 방법의 주력 M3 |
| Q2 | Derived-only | CTI pivot 변경 이후 추적 가능성을 보는 M4 |
| Q3 | graph expansion | cutoff-valid relation에 기반한 M5 |

방법 ID는 다음처럼 고정한다.

```text
M1 = eligible·frozen CTI singleton discovery
M2 = CTI-only composite
M3 = CTI + Derived composite
M4 = Derived-only
M5 = Q3 graph expansion
```

M1은 `SINGLETON_PREFLIGHT` 실행이 아니다. identity·queryability·pagination·normal-reuse 검토를 통과하고 별도 version으로 동결된 `CTI_SINGLETON_DISCOVERY`만 의미하며, bounded precheck hit는 M1 성능 계산에서 제외한다.

#### 6.2 Q0 landmark와 Seed continuity

- exact-IP 결과에서 host/service observation을 정규화한다.
- 현재 응답과 continuity를 별도 판정한다.
- `continuous` 또는 사전 기준을 통과한 `probable`만 campaign query용 derived-feature source로 허용한다.
- `unknown`, `reassigned`, `contradicted`는 baseline 분석에는 남기되 Q2 source에서 차단한다.

#### 6.3 Q1 singleton precheck와 CTI-only enrichment

singleton precheck는 다음만 확인한다.

- query syntax와 field 지원
- 제한된 반환 존재 여부
- bounded page/hit와 next token
- 제한 표본의 ASN·제품·서비스 분포
- pagination 가능성과 비용 상한
- 정상 제품 기본값 또는 shared 특성 여부

`partial_max_pages`는 “양성 raw observation, prevalence 미확정”으로 해석한다. 전체 cohort 규모나 성능 결과로 사용하지 않는다. full pagination과 eligibility 판정이 끝날 때까지 `pending`으로 유지하고 derived-feature source, Q2 composition 또는 frozen prospective query에 사용할 수 없다. Q1 0-hit 역시 pivot 또는 캠페인 소멸의 evidence로 해석하지 않는다.

broad/shared pivot은 동일 node role·시간·service에서 공존 가능한 CTI 특징과 composite로 구성한다. 같은 보고서에 등장했다는 이유만으로 다른 node role의 특징을 AND 결합하지 않는다.

#### 6.4 Feature extraction과 canonicalization

지원 family는 다음과 같다.

- identity: certificate, SPKI, SSH key
- TLS: JARM, JA4
- HTTP: title, banner, header, body-derived hash
- service: port, protocol, extended service, portset
- device: vendor, product, version class
- network: ASN, prefix, provider class
- temporal: 동시 출현, 변화 순서, 재관측 간격
- relation: shared fingerprint, reported-with, resolves-to, co-observed

nonce, timestamp, request ID, 공백 등 가변 요소를 canonicalization에서 제거하되 원문 hash와 정규화 hash를 함께 보존한다.

#### 6.5 Anchor, enrichment 및 matched background

| 집합 | 구성 |
|---|---|
| Anchor A | continuity-eligible Q0 Seed와 독립 근거가 있는 Q1 entity epoch |
| Enrichment P | 적격 Q1 singleton/composite의 완결 결과 |
| Background B | 동일 시점·제품·서비스·provider 조건을 맞춘 reference set |

Anchor, P, B의 구성 규칙과 observability 분모를 cutoff별로 보존한다.

#### 6.6 Feature 통계

특징 \(f\)에 대해 다음을 계산한다.

```text
anchor_support(f)      = anchors_with_f / observable_anchors
background_prevalence = background_with_f / observable_background
reference_lift(f)     = anchor_support / max(background_prevalence, epsilon)
q1_retention(f)       = Q1_hosts_with_f / observable_Q1_hosts
temporal_support(f)   = feature가 실제 재관측된 적격 기회 수
                        / feature를 관측할 수 있었던 전체 적격 재관측 기회 수
```

`temporal_support`는 feature의 최초 관측 이후 예정된 적격 재관측 기회만 분모로 사용하고, 그중 feature가 실제 양성 관측된 기회를 분자로 사용한다. 실행 누락·API 오류·not-scanned·time-unresolved 기회는 분모에서 제외하되 제외 수를 함께 보고한다. 이 정의를 `feature_stat_snapshots.temporal_support_num/den`과 동일하게 구현하며 장기간 수집 공백을 feature persistence로 계산하지 않는다.

#### 6.7 Derived-feature eligibility

최소 게이트는 다음과 같다.

- 서로 다른 적격 anchor에서 반복
- 단일 host 우연값이 아님
- matched background에서 broad/shared가 아님
- 정상 제품 기본값, CDN 또는 shared hosting 특성이 아님
- query field와 canonical value가 안정적
- `first_available_at <= query cutoff`
- source Seed/pivot이 query input으로 허용됨

구체 임계값은 development 결과와 비용 제약을 이용해 사전 등록하고 sensitivity analysis를 수행한다.

#### 6.8 Query composition

- 서로 다른 feature family를 선호하되 statistical independence를 자동으로 주장하지 않는다.
- CTI-only, CTI+Derived, Derived-only, graph expansion을 분리한다.
- query clause의 AND/OR/NOT, co-occurrence scope, feature origin을 재현 가능하게 저장한다.
- 개념적 score 식은 실제 구현한 경우에만 사용한다. 규칙 기반 query만으로 연구가 성립하면 불필요한 모델을 추가하지 않는다.

M5/Q3는 cutoff 시점에 사용 가능한 edge만 사용한다. 허용 edge family, edge `available_at/valid_from/valid_to`, 최대 expansion depth, cycle·중복 제거, entity-epoch 후보 정의, expansion/alert budget, cluster ID 및 split·merge 규칙을 query version과 함께 동결한다. 이 계약을 구현하지 못하면 M5는 RQ5의 주 비교에서 제외하고 탐색적 graph analysis로만 보고한다.

#### 6.9 Bounded development precheck와 수동 검토

precheck는 문법, 0-result, broad query, 비용, pagination 및 안전성 확인에만 사용한다. precheck hit를 prospective precision이나 discovery 결과로 계산하지 않는다.

수동 검토 항목:

- 모든 feature가 cutoff 이전에 사용 가능했는가
- Seed continuity와 pivot eligibility를 통과했는가
- CTI와 derived origin이 구분되는가
- node role을 잘못 결합하지 않았는가
- discovery와 validation feature가 분리되는가
- active victim을 불필요하게 노출하지 않는가
- 실행 주기·alert budget·비용 상한이 현실적인가

#### 6.10 Query freeze

다음을 함께 동결한다.

- query text, hash, version, class, variant, composition
- source indicator와 feature IDs
- cutoff와 dataset/API/schema version
- parser, normalizer, entity-resolution version
- background snapshot
- score, threshold, K 및 tie-breaking
- alert budget과 실행 주기
- `frozen_at`, `valid_for_test_from`
- 허용 validation evidence family

결과를 본 뒤 조건·threshold·ASN·국가·제품 제외·주기를 바꾸지 않는다. 변경 시 새 version과 새 전향 평가기간을 만든다.

#### 6.11 Prospective execution과 candidate ledger

- 실행 적격성은 `status=frozen`, query hash 일치, `dataset_split=prospective_test`, `executed_at >= valid_for_test_from`, 기저 `observed_at >= valid_for_test_from`을 모두 요구한다.
- 응답 저장 후 schema-drift와 normalization 검사를 통과해야 한다.
- 전체 결과 snapshot의 pagination이 미완료이면 partial 상태와 checkpoint를 보존하되 RQ4·RQ5의 동일-universe 비교에서 제외하거나 사전 정의한 partial-run sensitivity에만 포함한다.
- raw pages, checkpoint 및 manifest를 append-only로 저장한다.
- 최초 query–entity 쌍에 candidate를 생성하고 이후 재관측을 event로 추가한다.
- query result에서 사라진 사실을 즉시 node death로 처리하지 않는다.
- candidate의 최초 score, discovery feature, source query와 `first_candidate_at`을 불변으로 보존한다.
- grade 변경과 evidence attachment는 이력으로 추가한다.

---

### 7. Independent Validation and Evaluation Protocol

#### 7.1 Evidence role 분리

각 evidence는 다음 중 하나의 role을 가진다.

- `discovery`: query 또는 후보 점수에 사용
- `validation`: 검색에 사용하지 않은 독립 근거
- `contradiction`: 정상 재사용·재할당·무관 캠페인을 지지하는 근거

같은 evidence를 discovery와 validation에 동시에 사용하지 않는다.

#### 7.2 Source independence

- 동일 원문의 재게시를 독립 validation으로 세지 않는다.
- 동일 공급자의 후속 보고서는 필요하면 `partially_independent`로 분리한다.
- 후보 이후 공개된 CTI, query에 쓰지 않은 certificate/SPKI/SSH key/domain, malware·IR evidence, 독립 공급자 확인 등을 validation 후보로 사용한다.
- 후보 생성 전에 이미 공개된 evidence는 사후 adjudication의 보조 근거로 연결할 수 있으나 RQ4의 미래 validation, prospective positive 또는 public-disclosure lead time에는 포함하지 않는다.
- RQ4 positive는 source independence, `evidence.first_public_at > first_candidate_at` 및 `evidence.available_at > first_candidate_at`을 모두 충족해야 한다. 비공개 evidence에는 public-disclosure lead time을 계산하지 않는다.

#### 7.3 Verdict와 evidence-grade 전이

verdict는 최소 다음을 포함한다.

- positive
- negative
- contradicted
- unresolved
- unobservable

사람 판정 기준과 blind review 가능 여부를 사전 정의한다. grade 변경 전후, 근거, 판정자와 시간을 append-only로 남긴다.

| Verdict | Candidate grade 처리 | RQ4·RQ5 count |
|---|---|---|
| `positive` | evidence rubric에 따라 probable/high-confidence/confirmed로 승격 가능 | `N_pos` |
| `negative` | campaign-link grade를 제거하고 resolved non-positive로 유지 | `N_neg` |
| `contradicted` | 명시적 반증과 함께 강등·차단 | `N_neg`, 사유 별도 보고 |
| `unresolved` | 현재 grade를 확정 label로 승격하지 않음 | `N_unres` |
| `unobservable` | 검증 가능성 부족 상태로 분리 | `N_unobs`, precision 분모 제외 |

독립 evidence 부재만으로 `negative`를 부여하지 않는다. 동일 후보가 여러 evidence를 가질 때의 우선순위와 충돌 해결 규칙은 adjudication rubric에서 동결한다.

#### 7.4 시간 누수 통제

핵심 불변식:

```text
max(feature.available_at) <= query.cutoff_time
query.cutoff_time <= query.registered_at
query.registered_at <= query.frozen_at
query.frozen_at <= query.valid_for_test_from
prospective_execution.executed_at >= query.valid_for_test_from
prospective_record.observed_at >= query.valid_for_test_from
future_validation.available_at > candidate.first_candidate_at
```

Appendix F의 누수 감사에는 다음 시간 순서도 포함한다.

```text
source_document.published_at <= source_document.retrieved_at
assertion.first_public_at <= assertion.available_at
cti_reported_first_seen_at <= cti_reported_last_seen_at
observation.observed_at <= observation.collected_at
censys/service/fingerprint first_observed_at <= last_observed_at
candidate.first_match_observed_at <= candidate.first_candidate_at
candidate.first_match_observed_at <= candidate.last_match_observed_at
candidate.first_candidate_at <= candidate.last_match_recorded_at
evaluation_window_start < evaluation_window_end
```

day/month/range 정밀도는 단일 timestamp로 강제하지 않고 가능한 `time_start/time_end` 구간의 순서를 검사한다. 순서를 확정할 수 없으면 `temporal_order_unresolved`, 서로 모순되면 `temporal_conflict`로 격리한다.

추가 차단:

- 미래 CTI를 development corpus로 역삽입
- 후보 이후 evidence로 과거 score 재계산
- 동일 query match를 validation으로 연결
- 전체 기간 rarity를 과거 cutoff에 적용
- 방법별 다른 evaluation window나 alert budget 적용
- 과거 Censys timestamp를 `first_candidate_at`으로 소급

감사 실패 시 RQ4·RQ5 분석을 fail-closed로 중단하고 실패 원인을 보고한다.

#### 7.5 Baseline과 비교 조건

핵심 비교는 동일 조건의 M1∼M5다. 다음 보조 baseline은 point-in-time 정보와 예산을 맞출 수 있을 때만 포함한다.

- 공개 CTI feed 및 feed union
- TTL 적용 feed
- certificate/JARM/banner exact match
- 단일 rule pivot
- nearest-neighbor 또는 단순 ML
- 별도 graph baseline

Q0 exact-IP는 신규 host discovery method가 아니므로 RQ5의 주 비교에서 제외한다.

#### 7.6 평가 지표

평가 시 다음 count를 먼저 고정한다.

```text
N_pos   = positive
N_neg   = adjudicated negative + contradicted
N_unres = unresolved
N_unobs = unobservable
```

`contradicted`는 campaign-link 목표에 대한 resolved non-positive로 `N_neg`에 포함하되 별도 사유 분포도 보고한다. `unobservable`은 precision과 resolution 분모에서 제외하고 observability로 별도 보고한다.

```text
validated_yield@K      = 상위 K개 unique entity-epoch 후보 중 N_pos
verified_precision     = N_pos / (N_pos + N_neg)
conservative_precision = N_pos / (N_pos + N_neg + N_unres)
resolution_rate        = (N_pos + N_neg) / (N_pos + N_neg + N_unres)
observability_rate     = (N_pos + N_neg + N_unres)
                         / (N_pos + N_neg + N_unres + N_unobs)
confirmed_FP_per_day   = N_neg / evaluation days
unresolved_per_day
alerts_per_day
cost_per_validated_positive
```

`validated_yield@K`는 raw hit가 아니라 source query 간 중복을 제거한 unique candidate/entity epoch를 단위로 한다. K, score tie-breaking, 동일 entity의 복수 query attribution 규칙은 query freeze 시 고정한다.

미래 positive 사례에는 두 시간 지표를 구분한다.

\[
PublicDisclosureLeadTime_i =
t^{(i)}_{future\ independent\ evidence\ first\ public}
- t^{(i)}_{first\ candidate}
\]

\[
ValidationAvailabilityLag_i =
t^{(i)}_{future\ independent\ evidence\ available}
- t^{(i)}_{first\ candidate}
\]

첫 지표는 공개 CTI보다 먼저 후보화한 시간이고, 둘째는 연구 시스템이 실제 검증 evidence를 사용할 수 있기까지의 지연이다. 두 evidence 시간이 모두 후보 이후인 positive에만 계산한다. 날짜 정밀도가 day/month/range이면 scalar가 아니라 가능한 lead-time interval을 보고하고 사건 순서가 확정되지 않으면 `temporal_order_unresolved`로 제외한다. 어느 지표도 공격 전 탐지시간으로 해석하지 않는다.

전체 모집단 ground truth가 없으면 일반 recall을 주장하지 않는다. held-out seed recovery 또는 validated yield를 제한된 보조 지표로 사용한다.

#### 7.7 Temporal 및 campaign-disjoint 평가

- rolling-origin temporal analysis
- campaign-disjoint evaluation
- 기존 캠페인의 신규 node 추적
- 표본이 허용하면 unseen-campaign 일반화

모든 split은 해당 시점의 정보 가용성과 query cutoff를 보존한다.

#### 7.8 Ablation과 sensitivity

- M1→M2: singleton 대비 CTI composite 효과
- M2→M3: derived feature 추가 효과
- M3↔M4: CTI pivot 의존성
- M3/M4→M5: relation graph 확장 효과
- feature family별 제거
- threshold, K, alert budget, 관측주기
- continuity와 evidence-grade 정책
- missingness와 unresolved 처리
- background matching 정의

사후적으로 유리한 설정만 선택하지 않고 사전 정의 범위 전체를 보고한다.

#### 7.9 운영 부담과 비용

- raw alert와 unique entity 수
- analyst review 수와 소요시간
- unresolved backlog
- API request·page·credit 비용
- validated positive당 비용
- query 실행 실패와 pagination 재개 부담

정확도뿐 아니라 동일 분석 예산에서의 효용을 결과로 제시한다.

---

### 8. Results

결과는 방법 설명과 분리하고, 각 절 마지막에 해당 RQ에 대한 한두 문장의 제한된 답을 제시한다.

#### 8.1 Corpus flow와 gate attrition

필수 결과:

- source document와 source family 수
- assertion·indicator·Seed·pivot 수
- accepted/rejected/pending 비율
- Q0 continuity 분포
- Q1 eligibility와 pagination 상태
- derived feature 상태별 수
- 동결 query와 prospective run 수
- candidate의 grade·verdict·observability 분포

`unknown`, `pending`, `unobservable`, `unresolved`를 누락하지 않는다.

#### 8.2 RQ1: Landmark observability and continuity

- 공개 연령별 host/service/fingerprint 관측률
- landmark 상태와 continuity 교차표
- 재할당·판정불가 사례
- Censys 미관측 원인 분해

허용 결론은 “landmark 시점의 observable persistence”이며 정확한 과거 수명은 아니다.

#### 8.3 RQ2: Evidence-stratified persistence and churn

- entity epoch, service, fingerprint, cluster별 interval 결과
- evidence-grade별 곡선 또는 요약량
- competing event 구성
- validated ORB와 technical candidate의 차이
- 관측주기·사건 기준 sensitivity

#### 8.4 RQ3: Feature persistence and discriminativeness

- feature family별 anchor support와 matched-background prevalence
- reference lift와 temporal support
- query-eligible/blocked 사유
- 정상 재사용과 broad/shared feature 사례
- cutoff별 안정성과 ranking 변화

#### 8.5 RQ4: Point-in-time candidates and delayed validation — Track A 조건부

- 동결 이후 최초 후보 수와 first-candidate timeline
- evidence-grade 전이와 time-to-validation
- positive, negative, contradicted, unresolved, unobservable 분포
- 미래 독립 positive의 public-disclosure lead time
- retrospective match와 prospective candidate의 차이

미래 positive가 적으면 precision을 과도하게 일반화하지 않고 yield와 resolution process를 중심으로 보고한다.

#### 8.6 RQ5: M1∼M5 operational comparison — Track A 조건부

- 동일 window와 alert budget의 raw/unique alert
- validated yield@K
- verified 및 conservative precision
- confirmed FP/day와 unresolved/day
- analyst burden과 API 비용
- 미래 confirmed 사례의 lead time

외부 feed나 ML baseline은 동일 조건을 만족한 경우 별도 패널로 제시한다.

#### 8.7 Ablation, sensitivity and leakage audit

- feature·query-family ablation
- threshold·budget·schedule 민감도
- background와 evidence policy 민감도
- 모든 leakage audit의 통과·실패 수
- 실패 레코드의 제외 이유와 영향

#### 8.8 Negative and null findings

- Q1 대부분이 broad/shared인 경우
- query-eligible derived feature가 적은 경우
- 미래 독립 positive가 부족한 경우
- Censys 가시성이 낮은 경우

이 결과도 파이프라인의 gate와 연구 범위에 대한 실증 결과로 보고한다.

---

### 9. Case Studies

#### 9.1 사례 선정 기준

- 정량 결과를 대표하는 성공 사례
- broad/shared pivot 또는 재할당으로 차단된 실패 사례
- 독립 검증 없이 unresolved로 남은 경계 사례
- 서로 다른 ORB 구조를 보여주는 2∼3개 사례

후보 사례:

- LapDogs/UAT-7810
- JDY·KV-botnet 계열
- GobRAT/Bulbature 또는 Raptor Train

#### 9.2 사례별 공통 서술 순서

1. CTI 원문, assertion, 공개시각과 역할
2. Q0 continuity 또는 Q1 pivot eligibility
3. enrichment와 derived feature provenance
4. anchor/background 통계와 query eligibility
5. 동결 query, cutoff와 `valid_for_test_from`
6. `first_candidate_at`과 이후 관측
7. 검색에 사용하지 않은 미래 evidence
8. 최종 verdict와 evidence-grade 변화
9. 오탐·불확실성·공개 제한

사례 연구는 정량 결과를 대체하지 않으며 actor attribution의 증거로 확대 해석하지 않는다.

---

### 10. Discussion

#### 10.1 IP 차단에서 인프라 추적으로

- IP, service, fingerprint, cluster가 서로 다른 지속성을 가질 때 방어 운영이 어떻게 달라지는지 논의한다.

#### 10.2 기술적 유사성과 캠페인 귀속의 경계

- 동일 fingerprint나 cluster membership이 동일 actor 또는 campaign을 자동으로 의미하지 않는 이유를 설명한다.

#### 10.3 Shared infrastructure와 정상 재사용

- CDN, shared hosting, 범용 장비 기본값 및 broad certificate가 만드는 false association을 논의한다.

#### 10.4 공격자 적응과 Censys 비가시성

- 지문 변경, 스캔 회피, 비표준 프로토콜, 일시적 노출이 method에 미치는 영향을 논의한다.

#### 10.5 SOC·CTI 운영 적용

- blocklist가 아니라 analyst prioritization으로 활용하는 방법을 제시한다.
- alert budget, unresolved backlog, 비용과 검토 SLA를 함께 고려한다.

#### 10.6 결과별 허용 범위 조정

| 상황 | 허용되는 결론 |
|---|---|
| Q0 continuity 부족 | 현재 landmark 결과만 보고하고 historical campaign continuity 주장 축소 |
| Q1 대부분 broad/shared | CTI singleton의 한계를 보고하고 composite·background 결과에 집중 |
| eligible derived feature 부족 | discovery 성능 주장을 줄이고 RQ1∼RQ3 측정 논문으로 전환 |
| 미래 independent positive 부족 | precision 확정 대신 yield·resolution rate·후보 churn·사례 보고 |
| Censys visibility 부족 | Censys 관측 범위로 결론 제한 |
| unresolved 비율이 높음 | precision 범위와 검토부담을 중심으로 보고 |

이 표는 결과를 본 뒤 임의로 주장을 바꾸기 위한 것이 아니라 사전 정의된 scope-decision rule로 사용한다.

---

### 11. Limitations, Ethics, and Reproducibility

#### 11.1 Censys coverage와 관측 편향

- Censys visibility는 실제 악성 사용·전체 인터넷·정확한 생존을 대표하지 않는다.
- 스캔 공백, observed-time 지연, schema 변경 및 비노출 서비스가 있다.

#### 11.2 CTI 선택 편향과 source dependence

- 공개된 캠페인 중심 표본, 공급자별 coverage와 재게시 의존성을 설명한다.
- 미래 CTI 공개량이 validation 표본을 결정하는 delayed-label 문제를 명시한다.

#### 11.3 시간 정밀도와 사건 불확실성

- reported, published, retrieved, available, observed, collected, candidate, validated 시간을 대체하지 않는다.
- 미관측 사건을 interval로 처리하고 정확한 소멸일을 주장하지 않는다.

#### 11.4 Matched background의 대표성

- reference set은 통제된 비교집단이지 전체 인터넷이 아니다.
- matching 정의와 잔여 교란의 영향을 sensitivity analysis로 제시한다.

#### 11.5 Label과 ground-truth 한계

- technical similarity는 악성 또는 campaign membership의 충분조건이 아니다.
- unresolved와 unobservable을 보존한다.
- 일반 recall과 완전한 false-negative rate를 주장하지 않는다.

#### 11.6 윤리와 공개 정책

- active victim IP와 악용 가능한 민감 feature의 원시 공개를 제한한다.
- public, restricted, active-victim sensitivity 등급을 적용한다.
- 코드·스키마·집계 통계·비식별 군집·허가된 비활성 사례를 우선 공개한다.
- API 약관, CTI 라이선스와 책임 있는 공개 절차를 따른다.

#### 11.7 재현성 패키지

공개 가능한 범위에서 다음을 제공한다.

- corpus search protocol과 screening flow
- source·assertion provenance manifest
- 데이터 스키마와 상태 사전
- query hash, clause provenance와 freeze manifest
- code·config·parser·normalizer version
- 공개 CTI subset과 집계·비식별 결과
- RQ별 cohort 생성 규칙
- leakage audit, 테스트 및 수용 기준
- input/output hash가 포함된 figure/table run manifest

raw Censys response와 상용 CTI는 라이선스에 따라 hash·schema·재생성 절차 또는 제한 artifact로 대체한다.

---

### 12. Conclusion

결론은 결과로 뒷받침된 다음 항목만 회수한다.

1. 공개 ORB IP의 landmark 관측성과 continuity의 차이
2. IP·service·fingerprint·cluster의 상대적 persistence와 churn
3. matched reference set을 통과한 feature의 범위
4. point-in-time 후보 생성과 미래 독립 validation의 실제 성과
5. 동일 예산에서 M1∼M5의 운영적 trade-off

미래 validation이 부족하면 4∼5번을 연구 설계 또는 제한된 관측 결과로 낮추고, RQ1∼RQ3 중심 측정 논문으로 결론을 재구성한다.

---

## Part C. 집필·출판 가이드

### C1. 논문–파이프라인 대응표

| 논문 장 | 설계도 단계 | 중심 RQ | 대표 artifact |
|---|---|---|---|
| 1–3 | 문제 정의 | RQ1∼RQ5 | 위협 모델, 선행연구 비교표 |
| 4 | Stage 0–1 | 전체 | protocol, source family, assertion registry |
| 5 | Stage 2, 6, 8 | RQ1–RQ2 | observation opportunity, continuity, timelines |
| 6 | Stage 2–6 | RQ3–RQ4 | feature stats, query registry, freeze manifest, ledger |
| 7 | Stage 7–8 | RQ4–RQ5 | evidence registry, adjudication, leakage audit |
| 8 | Stage 8 | RQ1∼RQ5 | RQ별 cohort·결과 테이블 |
| 9–10 | 전체 | 해석 | 사례 timeline, scope-decision 결과 |
| 11 | 전체 | 타당성 | 공개정책, hashes, 재현성 manifest |

---

### C2. 필수 그림과 표

#### C2.1 그림

본문은 최대 6개를 권장한다.

1. **ORB 위협 모델과 분석 계층**: actor–staging–ORB–exit–target 및 IP/entity/service/fingerprint/cluster
2. **Stage 0∼8 전체 연구 흐름**: corpus, Q0∼Q3/M1∼M5 및 RQ별 dataset
3. **시간 및 누수 경계**: reported/public/available/frozen/observed/candidate/validated timeline
4. **Corpus·eligibility·candidate attrition flow**
5. **RQ1–RQ2 landmark·continuity·persistence 결과**: 다중 패널
6. **활성화된 RQ3–RQ5 핵심 결과**: feature 효과와, Track A인 경우 discovery–cost trade-off

세부 taxonomy, campaign timeline, 개별 sensitivity plot은 부록으로 이동한다.

#### C2.2 표

본문은 6∼8개를 권장한다.

1. ORB campaign·source family·데이터 소스·시간 범위·라이선스
2. 핵심 상태·사건·evidence grade의 operational definition
3. RQ별 cohort·분모·cutoff·허용 주장
4. Q0 continuity와 Q1 pivot eligibility 분포
5. RQ1 landmark 및 RQ2 interval/churn 결과
6. feature family별 support·prevalence·lift·temporal support
7. Track A 활성화 시 M1∼M5 동결 조건과 RQ4/RQ5 verdict·precision 범위·비용
8. 주요 ablation·sensitivity·leakage audit

세부 campaign 결과, 전체 상태표, 데이터 스키마, 공개 범위와 추가 sensitivity는 부록으로 이동한다.

---

### C3. 초록의 권장 논리

초록은 결과가 확보된 뒤 다음 7문장 구조로 작성한다.

1. ORB가 공격 출처를 은폐하지만 방어자는 주로 정적 IP IoC에 의존한다.
2. IP의 교체·공유와 공개 지연 때문에 현재 관측, 캠페인 continuity 및 실제 후보 발견을 분리해야 한다.
3. 본 연구는 public-age landmark 분석과 query-version별 prospective observation을 결합한다.
4. CTI Seed/direct pivot을 검토하고 적격 anchor에서 발굴한 특징을 matched background와 비교한다.
5. CTI-only, CTI+Derived, Derived-only 및 graph query를 동결하고 후보를 append-only 원장에 저장한다.
6. 검색에 사용하지 않은 미래 evidence로 후보를 판정하며 다계층 persistence, validated yield, precision 범위, lead time, 부담과 비용을 측정한다.
7. 실제 수치와 통계적 불확실성을 이용해 검증된 범위의 결론만 제시한다.

수치 전에는 “최초”, “대규모”, “크게 향상”, “조기 탐지”를 사용하지 않는다.

Track B 원고에서는 5∼6번을 prospective protocol과 후보 원장 구축 범위로 축소하고, 검증되지 않은 yield·precision·lead-time 성능 문장은 초록에서 제외한다.

---

### C4. 권장 분량 배분

| 부분 | 권장 비율 |
|---|---:|
| Introduction | 8% |
| Background and Related Work | 13% |
| Data and Corpus | 10% |
| Study Design | 11% |
| Candidate Discovery Method | 14% |
| Validation and Evaluation | 12% |
| Results | 21% |
| Case Studies | 4% |
| Discussion·Limitations·Ethics·Reproducibility | 6% |
| Conclusion | 1% |

결과가 최소 20%를 차지하도록 한다. CLI·테이블 스키마·구현 Phase 설명이 본문을 압도하면 시스템 구현 보고서처럼 보이므로 부록으로 이동한다.

---

### C5. 피해야 할 주장과 대체 표현

| 피해야 할 주장 | 대체 표현 |
|---|---|
| “Censys 최초 관측일은 ORB 감염일이다.” | “본 연구 범위에서 확인된 최초 Censys 양성 관측이다.” |
| “Censys에서 사라져 ORB가 종료됐다.” | “마지막 양성과 이후 적격 미관측 사이에 관측 중단 사건이 구간 검열되었다.” |
| “현재 Q0 IP가 응답하므로 같은 캠페인이 계속 사용한다.” | “host는 관측됐으나 campaign continuity는 별도 evidence로 평가했다.” |
| “Q1 query 결과가 campaign cohort다.” | “Q1 결과는 feature 발굴용 enrichment universe다.” |
| “복수 feature가 일치해 ORB를 발견했다.” | “비중복 feature에 일치하는 technical-similarity candidate를 생성했다.” |
| “서로 다른 feature family는 독립적이다.” | “서로 다른 feature family의 비중복 특징을 조합했다.” |
| “reference set에서 희소하므로 인터넷 전체에서 희소하다.” | “사전 정의한 matched background에서 낮은 prevalence를 보였다.” |
| “반복 query match로 campaign linkage가 검증됐다.” | “반복 일치는 persistence evidence이며 linkage는 독립 evidence로 평가했다.” |
| “미래 CTI에서 찾은 feature로 과거 검색해 조기 발견했다.” | “동결 이전 feature로 실제 후보를 생성하고 이후 공개된 evidence와 비교했다.” |
| “보고서에 없는 후보는 false positive다.” | “독립 evidence가 부족해 unresolved로 유지했다.” |
| “precision이 X%다.” | resolved-only와 unresolved 포함 보수적 precision을 함께 제시한다. |
| “ORB 전체 recall을 측정했다.” | 전체 ground truth가 없어 validated yield 또는 held-out recovery를 보고한다. |

---

### C6. 결과 성숙도에 따른 출판 전략

#### Track A. 완성형 prospective discovery 논문

조건:

- 충분한 동결 query 실행기간
- 다수의 미래 독립 positive와 negative
- M1∼M5 공정 비교
- 안정적인 비용·분석 부담 측정

중심: RQ1∼RQ5 전체.

#### Track B. ORB persistence and churn 측정 논문

조건:

- 미래 validation 표본은 부족하지만 Q0 continuity와 전향 계층별 관측이 충분

중심: RQ1∼RQ3. RQ4∼RQ5는 pilot 또는 protocol 결과로 축소한다.

#### Track C. CTI pivot reliability and measurement protocol 논문

조건:

- Q1 broad/shared 비율이 높거나 derived-feature eligibility가 제한적

중심: CTI pivot identity, pagination, 정상 재사용, continuity와 측정 오류 방지. “발견 성능” 주장을 하지 않는다.

어느 track을 선택하더라도 null result, unresolved와 관측 한계를 숨기지 않는다.

---

### C7. 권장 부록

- **Appendix A**: 전체 용어·상태·evidence-grade 사전
- **Appendix B**: 시간 필드, 정밀도 및 사건 정의
- **Appendix C**: 데이터 스키마와 provenance 관계
- **Appendix D**: query clause, freeze manifest와 M1∼M5 상세
- **Appendix E**: evidence adjudication rubric와 source-independence 규칙
- **Appendix F**: leakage 불변식, 단위·통합·재현성 테스트
- **Appendix G**: 추가 campaign·feature·sensitivity 결과
- **Appendix H**: 공개·restricted artifact 목록과 재생성 절차

---

## 문서 정보

- 작성일: 2026-07-15
- 기준 프로젝트: `D:\\Codex\\CTI_Research`
- 비교 문서:
  - `2026-07-15-CTI-Censys-파이프라인-구현-설계도.md`
  - `2026-07-13-ORB-논문-구성안.md`
- 적용 원칙: 연구 목적과 논문 서사는 기존 구성안에서 계승하고, 데이터·방법·시간·평가는 구현 설계도를 우선 적용
- 기존 두 파일은 변경하지 않음
