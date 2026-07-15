# CTI 기반 Censys 파이프라인 도식

이 문서는 상세 구현 설계도를 빠르게 읽기 위한 시각 자료다. 상세 필드·게이트·테이블 정의는 `2026-07-15-CTI-Censys-파이프라인-구현-설계도.md`를 따른다.

## 1. 한눈에 보는 전체 설계

![CTI 기반 Censys 캠페인 후보 추적 파이프라인](./2026-07-15-CTI-Censys-파이프라인-한눈에보기.png)

핵심 흐름은 다음 한 줄로 요약된다.

```text
CTI anchor → Q0/Q1 bootstrap → 신규 특징 → 쿼리 동결 → 미래 후보 → 독립 검증 → RQ별 분석
```

## 2. 단계별 데이터 흐름

```mermaid
flowchart TB
    A["CTI 코퍼스·원문"] --> B["정규화된 Seed·direct pivot·assertion"]
    B --> Q0["Q0 exact-IP<br/>landmark + continuity"]
    B --> Q1P["Q1 singleton precheck<br/>broad·queryability·비용"]
    B --> Q1C["Q1 CTI composite<br/>실제 enrichment"]

    Q0 --> GA{"Seed continuity<br/>적격?"}
    Q1P --> GP{"Pivot identity·pagination·<br/>normal reuse 적격?"}
    Q1C --> GP

    GA -->|continuous / allowed probable| F["Derived feature 후보"]
    GP -->|eligible / combination_only| F
    GA -->|unknown / reassigned / contradicted| X1["landmark 전용 또는 차단"]
    GP -->|blocked / pending| X2["동결 query 입력 차단"]

    F --> R["Anchor support +<br/>Matched-background rarity"]
    R --> E{"query_eligible?"}
    E -->|Yes| D["캠페인 후보 query 개발"]
    E -->|No| X3["exploratory/blocked"]

    D --> P["bounded development precheck"]
    P --> H["수동 검토"]
    H --> Z["FREEZE<br/>query·threshold·budget·schedule"]
    Z --> O["전향적 반복 관측"]
    O --> C["append-only candidate ledger"]
    C --> V["미래 독립 증거 검증"]
    V --> A1["RQ1"]
    V --> A2["RQ2"]
    V --> A3["RQ3"]
    V --> A4["RQ4"]
    V --> A5["RQ5"]
```

## 3. 쿼리 계열

```mermaid
flowchart LR
    CTI["기존 CTI pivot"]
    DER["Q0/Q1 derived feature"]
    GRAPH["시점 제한 관계 그래프"]

    CTI --> M1["M1<br/>CTI direct singleton"]
    CTI --> M2["M2<br/>CTI-only composite"]
    CTI --> M3["M3<br/>CTI + Derived"]
    DER --> M3
    DER --> M4["M4<br/>Derived-only"]
    GRAPH --> M5["M5<br/>Q3 graph expansion"]

    M1 --> FREEZE["동일 기준으로 동결"]
    M2 --> FREEZE
    M3 --> FREEZE
    M4 --> FREEZE
    M5 --> FREEZE
    FREEZE --> FUTURE["미래 Censys에서<br/>동일 schedule·budget 비교"]
```

해석:

- M1·M2는 기존 CTI만 사용한 baseline이다.
- M3는 신규 derived feature의 추가효과를 검증하는 주 방법이다.
- M4는 기존 CTI pivot이 변경된 뒤에도 추적 가능한지 평가한다.
- M5는 단일 host 특징이 아니라 관계 그래프를 이용한다.
- Q0 exact-IP는 신규 host 발견 방법이 아니므로 discovery baseline과 분리한다.

## 4. 후보와 독립 검증

```mermaid
flowchart TB
    HIT["동결 query 일치"] --> RAW["raw_hit"]
    RAW --> TECH["technical_similarity_candidate"]

    TECH --> EV{"검색에 사용하지 않은<br/>독립 증거가 있는가?"}
    EV -->|일부 강한 근거| PROB["probable_campaign_link"]
    EV -->|복수 독립 근거| HIGH["high_confidence_campaign_link"]
    EV -->|독립 CTI·IR 확인| CONF["confirmed_campaign_member"]
    EV -->|정상 재사용·재할당| NEG["negative / contradicted"]
    EV -->|판정 근거 부족| UNR["unresolved"]
    EV -->|관측 실패| UNO["unobservable"]

    DISC["Discovery evidence<br/>query feature"] -. "validation으로 재사용 금지" .-> EV
```

가장 중요한 경계는 다음과 같다.

```text
query match ≠ campaign membership
repeated query match ≠ independent validation
unresolved ≠ negative
```

## 5. 날짜 흐름

```mermaid
flowchart LR
    T1["campaign_reported_first_seen_at<br/>CTI가 주장한 활동 최초일"]
    T2["first_public_at<br/>일반 공개일"]
    T3["available_at<br/>연구 사용 가능일"]
    T4["Censys observed_at<br/>기저 관측일"]
    T5["first_candidate_at<br/>후보 원장 고정일"]
    T6["future_evidence_first_public_at<br/>미래 독립 증거 공개일"]
    T7["validated_at<br/>판정 완료일"]

    T1 --> T2 --> T3 --> T5
    T4 --> T5
    T5 --> T6 --> T7

    T5 -. "RQ4 lead time" .-> T6
```

실제 자료에서는 CTI가 과거 활동을 뒤늦게 공개하거나 연구자가 과거 Censys record를 조회할 수 있다. 따라서 활동·관측 흐름과 공개·지식 흐름을 분리하고, 모든 날짜의 출처·정밀도·하한·상한을 함께 보존한다. 전향 결과에는 별도로 `observed_at >= valid_for_test_from`을 요구한다.

RQ2의 소멸·변경 사건은 다음처럼 처리한다.

```mermaid
flowchart LR
    P["last_positive_observed_at"] --> U["실제 사건시각은 미상"] --> M["first_consecutive_missing_at"]
    P -. "interval_left" .-> U
    U -. "interval_right" .-> M
```

마지막 양성 관측일을 소멸일로 확정하지 않는다.

## 6. RQ별 데이터 연결

```mermaid
flowchart LR
    CTI["CTI assertions"] --> RQ1["RQ1<br/>landmark persistence"]
    Q0["Q0 observations"] --> RQ1

    Q0 --> RQ2["RQ2<br/>node/service/fingerprint/cluster churn"]
    PRO["prospective snapshots"] --> RQ2

    ANC["eligible anchors"] --> RQ3["RQ3<br/>persistence + rarity"]
    BG["matched background"] --> RQ3

    FROZEN["frozen Q2/Q3"] --> RQ4["RQ4<br/>point-in-time discovery"]
    EVID["future independent evidence"] --> RQ4

    METH["M1∼M5 동일 budget 결과"] --> RQ5["RQ5<br/>operational benefit"]
    LABEL["positive·negative·unresolved"] --> RQ5
    COST["API·분석 비용"] --> RQ5
```

| RQ | 한 줄 질문 | 핵심 산출물 |
|---|---|---|
| RQ1 | 과거 CTI Seed는 현재 어느 계층에서 관측되는가? | landmark 상태·continuity |
| RQ2 | 미래 관측에서 node·service·fingerprint·cluster는 어떻게 바뀌는가? | interval·churn |
| RQ3 | 어떤 특징이 지속적이고 비교집단에서 희소한가? | persistence·reference-set rarity |
| RQ4 | 동결 query 후보가 미래 독립 증거로 확인되는가? | validation outcome·lead time |
| RQ5 | derived feature가 기존 CTI-only 방법보다 운영상 유용한가? | yield·precision 범위·FP·비용 |

## 7. 구현 순서

```mermaid
flowchart LR
    A["Phase A<br/>Schema·시간·상태 gate"] --> B["Phase B<br/>Q0/Q1 bootstrap"]
    B --> C["Phase C<br/>Feature·background"]
    C --> D["Phase D<br/>Query composer·freeze"]
    D --> E["Phase E<br/>Prospective·validation"]
    E --> F["Phase F<br/>RQ 분석·논문 산출물"]
```

구현 우선순위는 `시간·provenance 강제 → Q0/Q1 적격성 → derived feature → query freeze → 미래 수집 → 독립 검증`이다.
