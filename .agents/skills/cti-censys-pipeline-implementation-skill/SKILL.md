---
name: cti-censys-pipeline-implementation-skill
description: Implement, diagnose, test, plan, or audit this workspace's CTI/Censys research pipeline against the 2026-07-15 implementation blueprint. Use for Stage 0-8 or Phase A-F code, schema, CLI, test, model-routing, provenance, cutoff, query-freeze, prospective-collection, independent-evidence, and RQ-analysis work. Do not use for general Censys syntax, ad hoc live IP lookups, CTI document summaries, or paper editing unrelated to repository implementation.
---

# CTI/Censys 파이프라인 구현

## 목표

설계도와 현재 코드를 다시 대조한 뒤, 가장 이른 미통과 게이트를 통과시키는 최소 수직 increment 하나를 구현하라. 계획된 목표와 이미 구현된 사실을 구분하고, 시간 누수·출처 혼합·동결 위반·불완전 수집을 fail-closed로 처리하라.

## 1. 권한과 작업 범위를 먼저 고정하라

1. 저장소 루트와 가장 가까운 `AGENTS.md`를 찾아 전체 지침을 읽어라.
2. `../../../CTI-Censys-파이프라인-구현-설계도.md`를 기준 설계로 읽어라. 대상 Stage의 입력·출력·상태·시간·provenance·테스트·완료 게이트를 추출하라.
3. 사용자 요청을 `assess`, `plan`, `implement`, `audit` 중 하나로 분류하라. 설명·진단·검토 요청은 명시적 변경 요청 없이 파일을 수정하지 마라. 사용자가 `no-write`를 지정했거나 모드가 `assess/plan/audit`이면 정적 source 검사만 기본으로 하는 읽기 전용 분기로 들어가라.
4. 다단계 변경 전에는 수정 경로, 최소 increment, 테스트, 정지 조건을 보여주고 저장소 지침이 요구하는 승인을 기다려라. 기존 파일의 삭제·덮어쓰기·이름 변경은 예상 diff를 먼저 제시하라.
5. 작업 폴더 밖을 수정하지 말고 `.env`, 실제 credential, 운영 SQLite, `data/raw`를 읽거나 바꾸지 마라. 사용자가 명시적으로 범위에 넣은 경우에도 비밀값을 출력하지 마라.
6. 새 문서·데이터 산출물에는 저장소의 날짜 접두사 규칙을 적용하라. import 가능한 Python 모듈·테스트 이름과 날짜/하이픈 규칙이 충돌하면 임의로 깨진 이름을 만들지 말고, 제안 경로와 이유를 보여준 뒤 명시적 예외 승인을 받아라.

## 2. 매번 현재 구현을 재감사하라

- `src/`, `tests/`, `configs/`, `pyproject.toml`, CLI parser/entry point와 migration 상태를 직접 확인하라. 읽기 전용 분기에서는 CLI를 실행하지 말고 source만 검사하라.
- 설계도의 “현재 구현” 표와 [단계별 플레이북](references/stage-playbook.md)은 2026-07-15 기준 힌트로만 사용하라. 파일 존재만으로 완료 판정하지 마라.
- 실패 중인 테스트, 미연결 validator, 상태 전이, 저장소 제약을 확인하라. Git 저장소가 정상이라고 가정하거나 reset/worktree 복구를 시도하지 마라.
- 기존 parser, adapter, model, fixture를 재사용하고 이미 구현된 기능을 다시 만들지 마라.
- 대상 Stage의 entry gate가 충족됐음을 코드와 테스트로 증명하지 못하면 downstream 구현을 시작하지 마라.

## 3. 가장 작은 다음 게이트를 선택하라

기본 production 의존성은 `Phase A → B → C → D → E → F` 순서다. 사용자가 뒤 단계를 지정해도 선행 계약을 읽기 전용으로 검사하고, 미충족이면 그 사실과 필요한 최소 선행 increment를 보고하라. 단, RQ1-RQ3의 분석 increment는 각각 Seed/landmark, 반복 timeline, feature/background entry gate가 통과하면 시작할 수 있다. RQ4-RQ5는 Phase E의 전향 데이터와 독립 판정 전에는 산출하지 마라.

| Stage | Phase | 핵심 산출물 | 기본 라우팅 |
|---|---|---|---|
| 0 | A | protocol, corpus, source family, provenance | Sol/high |
| 1 | A | CTI assertion, 시간·역할·review gate | Terra/medium + Sol/high |
| 2 | B | Q0 landmark, timeline, continuity | Terra/high + Sol/high |
| 3 | B | Q1 precheck, CTI composite, eligibility | Terra/high + Sol/high |
| 4 | C | feature catalog, entity graph, background 통계 | Sol/high + Terra/high |
| 5 | D | query composer, review, immutable freeze | Sol/high + Terra/high |
| 6 | E | due schedule, prospective ledger, candidate event | Terra/high |
| 7 | E | 독립 evidence, adjudication, grade history | Sol/high + Terra/high |
| 8 | F | RQ cohort, metric, sensitivity, 재현 패키지 | Sol/high + Terra/high |

대상 Stage를 고른 뒤 [단계별 플레이북](references/stage-playbook.md)의 해당 절만 추가로 읽어라. 한 increment는 하나의 entry gate, 하나의 observable outcome, 집중 테스트, 명시적 exit gate를 가져야 한다.

## 4. 모델과 추론 강도를 명시하라

작업 전 [모델 라우팅 기준](references/model-routing.md)을 읽고 아래 형식으로 선언하라.

```text
라우팅: lead=<model>/<effort>; builder=<model>/<effort>; reviewer=<model>/<effort 또는 human>; 근거=<위험과 계약>
```

- `gpt-5.6-luna` / `low`: 계약이 고정된 목록화, fixture, 반복 변환. 의미 판단이 전혀 없을 때만 `none`을 허용하라.
- `gpt-5.6-terra` / `medium`: 일반 로컬 구현과 집중 테스트의 기본값으로 사용하라.
- `gpt-5.6-terra` / `high`: migration, 다중 모듈 상태 전이, pagination/resume, idempotency에 사용하라.
- `gpt-5.6-sol` / `high`: 연구 설계, cutoff, 시간 의미, evidence 역할, query 정책, 통계·claim 범위에 사용하라.
- 별도 `gpt-5.6-sol` / `xhigh`: 구현에 참여하지 않은 agent의 누수·불변식 감사를 위해 사용하라.
- `max`는 xhigh 이후에도 남은 경계가 명확한 모순을 읽기 전용으로 분석할 때만 제안하라. 사람 승인, 정책 결정, live 권한을 대체하지 마라.

현재 실행 환경에서 지정 모델을 선택할 수 없으면 지원되는 동급 capability tier와 effort를 제안하고 사용자에게 알리라. 연구 타당성에 영향을 주는 검토를 조용히 하향하지 마라. 지원되는 Codex 환경에서 `ultra`는 maximum reasoning과 proactive subagent delegation을 함께 요청하는 `model_reasoning_effort`다. 독립적으로 분할할 수 있는 큰 작업에만 제안하고 모든 runtime/API에 이 값이 있다고 가정하지 마라. 인계에는 underlying `max`와 delegation 사용 여부를 분리해 기록하고, Ultra로 사람 승인·live 권한·파일 소유권·fail-closed gate를 바꾸지 마라.

## 5. 통제된 구현 루프를 실행하라

아래 전체 루프는 `implement` 모드에만 적용하라. `assess/plan/audit` 또는 명시적 `no-write`에서는 Contract, 정적 감사, Gate report만 수행하고 Red test, Patch, Regression을 건너뛰어라. 실행 없이는 판정할 수 없는 항목은 `unverified`로 보고하라.

1. **Contract** — 입력, 출력, 상태 전이, 식별자, 시간 필드, provenance, 실패 상태, 담당 파일을 한 화면에 요약하라.
2. **Red test** — 가장 작은 pure-function 또는 offline fixture 테스트로 누수·충돌·부분 상태를 먼저 재현하라.
3. **Patch** — 승인된 경로만 `apply_patch`로 수정하라. raw/ledger/evidence history는 append-only로 유지하고 frozen version은 새 version 없이 바꾸지 마라.
4. **Focused verify** — 대상 테스트와 CLI dry-run을 실행하라. live API를 검증 수단으로 사용하지 마라.
5. **Independent audit** — 시간·evidence·통계·freeze에 영향이 있으면 구현과 파일 소유권이 분리된 reviewer가 설계도, diff, 테스트를 직접 대조하게 하라.
6. **Regression** — 집중 테스트가 통과한 뒤 전체 offline suite를 실행하라.
7. **Gate report** — 통과·미통과·미검증을 구분하고 다음 increment 하나만 제안하라.

Subagent를 사용할 때 lead가 schema, 파일 소유권, acceptance test를 먼저 고정하라. 같은 파일이나 migration chain을 동시에 수정시키지 마라. 권장 역할은 `architect`, `implementer`, `fixture/test builder`, `read-only reviewer`다. reviewer에게 편집 파일이나 편집 임무를 주지 말고, 단일 integration owner가 병합하라. Luna 산출 코드는 Terra/high 이상으로 검토하고, 연구 결론에 영향을 주는 코드는 별도 Sol/high 이상으로 검토하라.

## 6. 모든 Stage에서 불변식을 강제하라

- 모든 report, assertion, feature, query, execution, candidate, evidence에 provenance와 재현 식별자를 남겨라.
- `published_at`, `retrieved_at`, `available_at`, `registered_at`, `frozen_at`, `valid_for_test_from`, `observed_at`, `collected_at`, `executed_at`, `first_candidate_at`, `evidence_available_at`과 날짜 정밀도를 서로 합치지 마라.
- cutoff 이후 알려진 CTI·feature를 과거 query 개발에 사용하지 마라.
- Q0의 현재 응답을 campaign continuity로, 마지막 양성 관측을 소멸시각으로 해석하지 마라.
- 같은 보고서에 있다는 이유만으로 서로 다른 node role의 pivot을 AND 결합하지 마라.
- Q1 singleton 또는 broad/shared hit를 campaign cohort나 성능 평가로 승격하지 마라.
- `pending`과 `partial_max_pages`를 완전 관측·prevalence·Q2 source로 처리하지 마라.
- matched background 없이 `global rarity`를 주장하지 마라.
- query hit를 campaign membership 또는 validation evidence로 재사용하지 마라.
- 동결 query version을 수정하지 말고, hash·cutoff·budget·schedule·`valid_for_test_from`을 함께 고정하라.
- prospective 여부를 API `executed_at`만으로 판정하지 말고 각 결과의 기저 `observed_at`도 검사하라.
- 동일 source family 재게시를 독립 evidence로 중복 계산하지 마라.
- `unresolved`를 negative로, missing scan을 host death로 바꾸지 마라.
- query/entity 재실행은 후보를 중복 생성하지 않아야 하며 raw page, checkpoint, candidate event, evidence, grade history는 감사 가능해야 한다.
- RQ1-RQ5는 cohort, 분모, timeline basis, unresolved 처리, manifest를 명시하라. M1-M5 비교는 같은 cutoff, universe, schedule, alert budget과 API/query cost 조건을 사용하라.

불변식 위반이나 시간 의미 미확정은 경고가 아니라 downstream 차단 사유로 기록하라.

## 7. Live·민감 작업은 별도 승인 게이트로 막아라

- 기본 실행은 cached page, fixture, pure function, 임시 DB만 사용하라.
- Censys live 수집은 사용자가 명시적으로 요청하고, 저장소 live gate와 `ALLOW_LIVE_CENSYS=1`, 등록·동결 상태, query hash, 비용·page budget을 모두 확인한 경우에만 수행하라.
- token을 인자, 로그, manifest, fixture, 응답에 기록하지 마라.
- active scanning, victim 접근, restricted CTI 공개, credential 취급은 자동화하지 말고 사람 승인을 요청하라.
- live 실행·freeze·assertion acceptance·candidate adjudication은 높은 reasoning effort만으로 승인하지 마라.

## 8. 검증과 인계를 표준화하라

프로젝트가 지정한 Python을 우선 사용하고, 필요하면 `PYTHONPATH=src`를 설정하라. `implement` 모드에서는 먼저 대상 테스트, 다음 CLI help/dry-run, 마지막으로 전체 offline suite를 실행하라. production DB와 live endpoint를 테스트에 사용하지 마라.

명시적 `no-write`에서는 pytest·CLI·import 실행을 기본적으로 생략하라. `-p no:cacheprovider`만으로 bytecode, 임시 DB, 임시 파일 생성을 막을 수 있다고 가정하지 마라. 사용자가 제한적 실행까지 허용한 경우에만 `PYTHONDONTWRITEBYTECODE=1`과 `python -B`를 사용하고, 임시 파일·DB를 만드는 테스트는 여전히 건너뛴 뒤 미실행 항목을 `unverified`로 적어라.

```powershell
& $python -m pytest -q -p no:cacheprovider tests/<target_test>.py
& $python -m pytest -q -p no:cacheprovider
& $python -m src.cli --help
```

완료 보고에는 다음을 포함하고 `../../../docs/yyyy-mm-dd-설명.md`로 저장하라.

```text
결과: <observable outcome>
게이트: <passed | failed | unverified> — <근거>
라우팅: <실제로 사용한 model/effort/reviewer>
검증: <명령과 결과>
파일: <생성/수정한 절대 경로>
남은 위험: <없음 또는 구체적 위험>
다음 increment: <하나만>
```

## 참고 자료

- [모델·추론·subagent 라우팅](references/model-routing.md): 작업 시작 전 모델과 reviewer를 고를 때 읽어라.
- [Stage 0-8 / Phase A-F 플레이북](references/stage-playbook.md): 대상 Stage의 entry/exit gate, 현재 재사용점, 집중 테스트를 고를 때 읽어라.
