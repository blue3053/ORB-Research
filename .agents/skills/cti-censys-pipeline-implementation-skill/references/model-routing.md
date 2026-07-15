# 모델·추론·Subagent 라우팅

## 목차

1. [적용 원칙](#적용-원칙)
2. [모델 역할](#모델-역할)
3. [추론 강도](#추론-강도)
4. [라우팅 프로필](#라우팅-프로필)
5. [Stage별 라우팅](#stage별-라우팅)
6. [Phase별 팀 구성](#phase별-팀-구성)
7. [Subagent 운영](#subagent-운영)
8. [Escalation과 fallback](#escalation과-fallback)
9. [공식 근거](#공식-근거)

## 적용 원칙

이 문서는 2026-07-15 기준 capability routing snapshot이다. 모델 이름보다 작업 위험과 계약을 먼저 분류하고, 그 위험을 감당하는 가장 낮은 비용의 모델·추론 강도를 선택하라. 최신 모델 가용성이 중요한 요청에서는 공식 OpenAI 문서를 다시 확인하고, 모델이 바뀌었으면 같은 capability tier로 치환하라.

모델 선택은 권한 선택이 아니다. 사람 승인, 데이터 접근 허용, query freeze, assertion acceptance, live 비용 승인, candidate adjudication을 추론 강도로 대체하지 마라.

## 모델 역할

| 모델 | 적합한 작업 | 피해야 할 단독 사용 |
|---|---|---|
| `gpt-5.6-sol` (`gpt-5.6` alias) | 복잡하고 열린 설계, cutoff·시간·evidence 의미, 통계, cross-stage 감사 | 고정 형식 대량 변환, 사람 승인 대체 |
| `gpt-5.6-terra` | 균형 잡힌 일반 구현, 도구 사용, 테스트, 다중 모듈 통합 | 미정 연구 의미를 스스로 확정 |
| `gpt-5.6-luna` | 비용 민감한 반복 작업, 명확한 fixture·manifest·형식 변환 | schema 설계, 시간/누수 판단, 독립 최종 감사 |

모델을 직접 선택할 수 없는 실행 환경에서는 다음 capability를 요구하라.

- `frontier reasoning`: Sol 역할
- `balanced coding/tool-use`: Terra 역할
- `fast cost-efficient execution`: Luna 역할

## 추론 강도

| effort | 사용 조건 | 예시 |
|---|---|---|
| `none` | 의미 판단이 없는 결정적 전사만 | 이미 승인된 목록 정렬, 필드명 기계 복사 |
| `low` | 계약·기대값이 고정된 좁은 작업 | manifest/fixture 확장, hash snapshot |
| `medium` | 일반 로컬 구현과 집중 테스트 | adapter, CLI wiring, 단일 모듈 validator |
| `high` | 여러 계약·상태·시간을 함께 보존 | migration, resume, idempotency, cutoff gate |
| `xhigh` | 구현과 분리된 독립 감사·설계 충돌 검토 | temporal leakage, evidence independence, RQ claim audit |
| `max` | 선택 모델이 지원하고 xhigh 뒤에도 경계가 명확한 모순이 남은 경우의 제한된 읽기 전용 분석 | 두 시간 계약의 재현 가능한 반례 분석 |
| `ultra`* | 지원되는 Codex 환경에서 maximum reasoning과 proactive subagent delegation이 모두 필요한 경우 | 독립적으로 분할 가능한 큰 cross-stage 감사 |

`max`를 기본값으로 쓰지 마라. `ultra`는 모든 runtime/API에 이식 가능한 값이 아니다. 인계에는 underlying `max`와 delegation 사용 여부를 분리해 기록하고, Ultra로 사람 승인·live 권한·파일 소유권·fail-closed gate를 바꾸지 마라.

## 라우팅 프로필

| ID | Lead / effort | Builder | Reviewer | 사용 기준 |
|---|---|---|---|---|
| R0 | Luna/low | Luna/low | Terra/medium | 의미가 고정된 반복 산출물 |
| R1 | Terra/medium | Terra/medium | Terra/high | 단일 모듈 일반 구현 |
| R2 | Terra/high | Terra/high | Sol/high | migration, 상태 전이, pagination, checkpoint, idempotency |
| R3 | Sol/high | Terra/high | 별도 Sol/high | 연구 설계, cutoff, evidence 역할, query/통계 정책 |
| R4 | Sol/high | Terra/high | 별도 Sol/xhigh | cross-stage 누수, freeze, 독립성, RQ 타당성 |
| R5 | Sol/max | 없음; read-only | human | R4 뒤에도 남은 bounded contradiction; 지원 시 한 번만 분석하고 구현 자동 승인 금지 |

### 빠른 결정 규칙

```text
사람 판정·권한이 필요한가? -> 중단하고 human gate
계약이 고정된 반복 작업인가? -> R0
cross-stage 누수·freeze·독립 감사인가? -> R4
연구 의미·시간·evidence·통계 판단인가? -> R3
migration·다중 상태·resume·idempotency인가? -> R2
그 밖의 단일 모듈 일반 구현인가? -> R1
R4 이후 bounded contradiction만 남았는가? -> R5 read-only 후 human
```

작업 시작 전에 다음 한 줄을 출력하라.

```text
라우팅: lead=<model>/<effort>; builder=<model>/<effort>; reviewer=<model>/<effort 또는 human>; 근거=<선택 위험>
```

## Stage별 라우팅

| Stage | Lead | Builder | 독립 검토 | 사람 게이트 |
|---|---|---|---|---|
| 0 protocol/corpus | Sol/high | registry·hash는 Terra/medium, inventory는 Luna/low | Sol/xhigh provenance | source 포함·제외와 restricted 등급 |
| 1 CTI assertion | Terra/medium; 모호한 역할·시간은 Sol/high | 반복 추출 Luna/medium; 단순 inventory Luna/low | Sol/high | assertion acceptance |
| 2 Q0 continuity | Terra/high; continuity 기준은 Sol/high | normalizer·fixture Luna/medium | Sol/high red-team | `probable` continuity 승인 |
| 3 Q1 precheck | Terra/high; 조합 의미는 Sol/high | query fixture Luna/medium | Sol/high query semantics | pivot eligibility와 Q2 source 수동 review |
| 4 feature/background | Sol/high | extractor·graph·statistics Terra/high; fixture Luna/medium | Sol/xhigh 통계·누수 | query-eligible 승격 |
| 5 compose/freeze | Sol/high | composer·registry Terra/high; hash test Luna/low | Sol/xhigh leakage | query review·freeze |
| 6 prospective | Terra/high | adapter·schedule fixture Luna/medium; schema drift는 Sol/high | Sol/high temporal/operations | live 비용·실행 승인 |
| 7 validation | Sol/high | storage/history Terra/high; 반복 ingest Luna/medium | Sol/xhigh evidence | adjudication |
| 8 RQ analysis | Sol/high | analyzer Terra/high; 표·패키지 Luna/medium | Sol/xhigh methods | claim 범위·필요 시 통계 검토 |

## Phase별 팀 구성

| Phase | Lead | Builder | Reviewer | 완료 판단 |
|---|---|---|---|---|
| A schema/gates | Sol/high | Terra/high + Luna/medium fixture | Sol/xhigh | temporal conflict와 evidence-role 차단을 offline fixture로 증명 |
| B Q0/Q1 | Terra/high | Luna/medium normalizer·pagination | Sol/high | 모든 seed/pivot에 허용 또는 차단 사유가 존재 |
| C feature/background | Sol/high | Terra/high + Luna/medium fixture | Sol/xhigh | 분자·분모·cutoff·source가 재생 가능 |
| D composer/freeze | Sol/high | Terra/high + Luna/low manifest | Sol/xhigh + human | hash·budget·schedule·valid-from이 immutable |
| E prospective/validation | Terra/high; evidence는 Sol/high | Terra/high + Luna/medium fixture | Sol/xhigh + human | 중복 후보 없음, history append-only |
| F RQ analysis | Sol/high | Terra/high + Luna/medium packaging | Sol/xhigh | 모든 산출물이 manifest와 hash로 역추적 가능 |

`Reviewer` 열은 AI 독립 검토를 나타낸다. Stage별 human gate는 별도로 유지하라. A는 protocol/source와 assertion acceptance, B는 probable continuity와 pivot/Q2 source review, C는 query-eligible feature 승격, D는 query review/freeze, E는 live 실행과 adjudication, F는 claim 범위와 필요한 통계 검토다.

## Subagent 운영

### 병렬화 전 계약

Lead가 다음을 먼저 고정하라.

1. 입력·출력 schema와 identity key
2. 각 agent가 소유할 파일 목록
3. acceptance test와 fixture
4. 변경하지 않을 파일·데이터·live gate
5. 결과 병합 순서

### 권장 역할

| 역할 | 권한 | 산출물 |
|---|---|---|
| architect | 읽기 전용 또는 설계 문서만 | contract, dependency, failure state |
| implementer | 지정 파일만 | 최소 production patch |
| fixture/test builder | 구현과 겹치지 않는 테스트 파일 | red test, edge-case fixture |
| reviewer | 읽기 전용 | 설계도-diff-test 독립 판정 |

- 같은 파일, schema definition, migration chain, freeze writer를 동시에 편집시키지 마라.
- reviewer는 구현 agent 요약을 신뢰하지 말고 설계도, 실제 diff, 테스트 로그를 읽어라.
- read-only reviewer에게 편집 도구나 파일 소유권을 주지 말고, 단일 integration owner가 병합하라.
- Luna 생성 코드는 Terra/high 이상이 검토하라.
- 시간·evidence·통계·claim에 영향을 주는 코드는 구현과 분리된 Sol/high 이상이 검토하라.
- schema 미확정, live 실행, query freeze, assertion acceptance, candidate adjudication은 병렬 자동 결정하지 마라.

## Escalation과 fallback

다음 조건에서는 downstream 구현을 중단하라.

- 둘 이상의 Phase에서 schema 또는 시간 의미가 바뀐다.
- `published_at`, `available_at`, `observed_at`, `collected_at`, `first_candidate_at`의 뜻이 모호하다.
- discovery와 validation이 같은 evidence를 공유할 가능성이 있다.
- 설계도, 기존 코드, migration, 테스트가 서로 충돌한다.
- live Censys, credential, 비용, restricted CTI, victim 노출 가능성이 있다.
- frozen query를 수정해야 한다.
- 독립 reviewer 간 결론이 다르거나 leakage 검사가 실패한다.
- ground truth가 없어 계획 metric이나 일반 recall 주장을 정당화할 수 없다.

먼저 Sol/xhigh 읽기 전용 검토로 반례와 선택지를 좁혀라. 경계가 명확하고 재현 가능한 단일 모순만 남고 선택 모델이 지원하면 Sol/max 읽기 전용 분석을 한 번 제안할 수 있다. 결과는 human gate로 넘기고 구현 승인으로 사용하지 마라. 경계가 불명확하면 max, Ultra 또는 더 많은 agent로 강행하지 마라.

## 공식 근거

- [OpenAI Models](https://developers.openai.com/api/docs/models)
- [Codex model selection](https://developers.openai.com/codex/models)
- [GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol)
- [GPT-5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra)
- [GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna)

모델 설명과 지원 effort는 변경될 수 있다. 최신 정보가 필요한 경우 공식 OpenAI 문서만 근거로 다시 확인하라.
