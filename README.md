# ORB-Research

> 2026-07-16 상태: Phase B(Stage 2–3) 구현과 오프라인 회귀 검증을 완료했습니다.
> Q0 landmark/continuity와 bounded Q1 precheck, 사람 검토 기반 Q2 진입 게이트가 동작합니다.
> 상세 결과는 [Phase B 완료 보고서](docs/2026-07-16-phase-b-completion.md)를 기준으로 합니다.

> 2026-07-15 상태: Phase A(Stage 0–1)의 protocol/corpus 및 CTI assertion
> acceptance gate 구현이 완료되었습니다. 최신 결과는
> [Phase A 완료 보고서](docs/2026-07-15-phase-a-completion.md)를 기준으로 합니다.

Stage 0은 publication precision/timezone, access class, acquisition mode,
corpus purpose, source-family를 보존합니다. Prospective-validation 자료의
development pivot 역삽입과 restricted source의 public export는 fail-closed로
차단합니다.

```powershell
python -B -m src.cli cti-export-public-corpus --help
python -B -m src.cli cti-audit-stage0 --help
python -B -m src.cli cti-review-assertions --help
python -B -m src.cli cti-audit-phase-a --help
```

다음 구현 단계는 Phase B Stage 2 Q0 landmark와 continuity입니다.

CTI에 공개된 인프라 지표와 수동 Censys 관측을 결합하여, 기존 IP를 넘어서는 지속 가능한 인프라 특징을 발굴하고 미래 캠페인 후보를 전향적으로 추적하기 위한 연구용 파이프라인입니다.

이 프로젝트의 목표는 Censys 쿼리 자체를 최적화하는 것이 아닙니다. CTI를 anchor로 삼아 특징의 지속성·판별력·미래 발견 가능성을 재현 가능한 방식으로 측정하는 것이 목적입니다.

권위 있는 구현 기준은 [CTI-Censys 파이프라인 구현 설계도](CTI-Censys-파이프라인-구현-설계도.md)입니다. README는 실행과 개발을 위한 안내이며 설계도를 대체하지 않습니다.

## 핵심 연구 원칙

- 검색 일치, 기술적 유사성, 캠페인 연계를 서로 구분합니다.
- Q0의 현재 host 응답을 과거 캠페인의 연속성으로 간주하지 않습니다.
- Q1 singleton 또는 broad/shared 결과를 캠페인 cohort로 승격하지 않습니다.
- query hit는 우선 `technical_similarity_candidate`입니다.
- 검색에 사용한 특징을 동일 후보의 validation evidence로 재사용하지 않습니다.
- `pending`, `partial_max_pages`, `unresolved`를 완전 관측이나 negative로 바꾸지 않습니다.
- 마지막 양성 관측일을 인프라 소멸일로 해석하지 않습니다.
- 동결된 query version은 수정하지 않고 변경 시 새 version을 만듭니다.
- cutoff 이후 알려진 CTI·feature를 과거 query 개발에 사용하지 않습니다.

## 현재 상태

현재 코드는 설계도의 **Phase A 완료 기준선**을 제공합니다. Stage 2–8 전체가 완료된 상태는 아닙니다.

| 범위 | 상태 | 현재 제공 기능 |
|---|---|---|
| Stage 0 | 완료 | protocol/cutoff, publication precision, source family, access/corpus provenance, public export/audit |
| Stage 1 | 완료 | source mention identity, 역할·confidence·human review, cutoff-safe Q0/Q1 acceptance |
| Stage 2 | 부분 구현 | Q0 exact-IP 등록, cached host/service normalization |
| Stage 3 | 부분 구현 | 제한된 Q1 direct-pivot 계획, paginated collection |
| Stage 4 | 미구현 | feature catalog, matched background, cutoff별 통계 |
| Stage 5 | 부분 구현 | query registry, hash, 상태 전이, 기본 freeze gate |
| Stage 6 | 부분 구현 | immutable raw page, checkpoint, resume, execution ledger |
| Stage 7 | 미구현 | candidate ledger, 독립 evidence, grade history |
| Stage 8 | 미구현 | RQ1–RQ5 analyzer와 재현 패키지 |

가장 이른 미통과 게이트는 Stage 2 Q0 landmark와 continuity입니다.

기준선 이관과 검증 기록은 [2026-07-15 CTI_Research 이관 보고서](docs/2026-07-15-cti-research-이관-보고서.md)를 참고하십시오.

## 프로젝트 구조

```text
ORB-Research/
├─ src/
│  ├─ adapters/       # 내부화한 CTI-Agent·ORB_Hunt 로직의 adapter
│  ├─ censys/         # query registry, lifecycle, Q0, pagination
│  ├─ cti/            # protocol, 검색, screening, snapshot, IoC, pivot planning
│  ├─ reused/         # hash와 provenance가 고정된 pure logic 파생본
│  ├─ cli.py
│  ├─ config.py
│  ├─ manifests.py
│  ├─ models.py
│  └─ provenance.py
├─ tests/             # offline unit·integration·reproducibility 기준선
├─ configs/           # 연구·보안·저장소·외부 재사용 설정
├─ data/
│  ├─ cti/            # 연구자가 보유한 CTI 원문
│  └─ raw/            # append-only 원시 수집물; 일반 테스트 입력으로 사용 금지
├─ docs/              # 날짜가 붙은 감사·구현 보고서
├─ pyproject.toml
└─ CTI-Censys-파이프라인-구현-설계도.md
```

설계도에 따른 향후 production 계층은 다음 순서로 추가합니다.

```text
src/assessments → src/features → src/entities
                → src/prospective → src/analysis
```

선행 Stage의 entry/exit gate가 통과하기 전에는 downstream 계층을 완료로 간주하지 않습니다.

## 개발 환경

요구사항:

- Python 3.11 이상
- PowerShell 예시는 Windows 기준
- 라이브 기능을 사용하지 않는 기본 개발에는 API credential이 필요하지 않음

가상환경을 만들고 개발 의존성을 설치합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

credential은 코드, 설정, CLI 인자, manifest, fixture 또는 로그에 기록하지 마십시오.

## 기본 검증

CLI 진입점을 먼저 확인합니다.

```powershell
python -B -m src.cli --help
```

전체 오프라인 테스트:

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
python -B -m pytest -q -p no:cacheprovider
```

`-p no:cacheprovider`와 `-B`는 pytest cache와 bytecode 생성을 줄이기 위한 설정입니다. production DB나 `data/raw`를 fixture로 사용해도 된다는 의미는 아닙니다.

기준선 이관 시점인 2026-07-15에는 전체 오프라인 테스트 56개가 통과했습니다. 코드 변경 후에는 대상 테스트와 전체 suite를 다시 실행해야 합니다.

## 현재 CLI

현재 구현된 명령은 다음 범위를 다룹니다.

```text
verify-reuse
register-query
register-q0
validate-query
freeze-query
collect-censys
normalize-censys
extract-fingerprints
cti-search
cti-import-existing
cti-screen
cti-snapshot
cti-verify-iocs
cti-extract-iocs
cti-register-indicators
cti-review-assertions
cti-plan-pivots
cti-export-public-corpus
cti-audit-stage0
cti-audit-phase-a
q0-assess-continuity
q0-review-continuity
cti-register-composite
cti-register-precheck
cti-record-precheck-result
cti-review-precheck
register-q2
cti-audit-phase-b
```

각 명령의 현재 인자는 CLI help를 기준으로 확인합니다.

```powershell
python -B -m src.cli <command> --help
```

설계도에 기재된 `assess`, `feature`, `prospective`, `candidate`, `analyze`, `audit leakage` 명령은 목표 계약이며 아직 모두 구현되지 않았습니다.

## 구현 로드맵

구현은 Phase와 Stage 순서를 따릅니다.

| Phase | Stage | 목표 | 완료 조건 |
|---|---|---|---|
| A | 0–1 | schema, 시간, provenance, evidence-role | live 없이 fail-closed validator를 fixture로 증명 |
| B | 2–3 | Q0/Q1 bootstrap | 모든 Seed/pivot에 허용 또는 차단 사유 존재 |
| C | 4 | feature와 background | 분자·분모·cutoff·source 재생 가능 |
| D | 5 | query composer와 freeze | hash·budget·schedule·valid-from 불변 |
| E | 6–7 | prospective와 validation | 중복 후보 없이 append-only history 보존 |
| F | 8 | RQ 분석 | 모든 결과가 manifest와 hash로 역추적 가능 |

RQ4–RQ5 결과는 Phase E의 전향 데이터와 독립 판정이 준비되기 전에 산출하지 않습니다.

## 변경 절차

모든 변경은 하나의 최소 increment로 진행합니다.

1. **Contract** — 입력, 출력, 상태, 시간, provenance와 실패 조건을 고정합니다.
2. **Red test** — 가장 작은 offline fixture로 현재 미통과 게이트를 재현합니다.
3. **Patch** — 승인된 파일만 최소한으로 수정합니다.
4. **Focused verify** — 대상 테스트를 실행합니다.
5. **CLI verify** — 관련 CLI help 또는 dry-run을 확인합니다.
6. **Independent audit** — 시간·evidence·통계·freeze 영향을 설계도와 재대조합니다.
7. **Regression** — 전체 오프라인 suite를 실행합니다.
8. **Gate report** — passed, failed, unverified를 구분하고 다음 increment 하나만 제안합니다.

기존 파일을 삭제·덮어쓰기·이름 변경하기 전에는 예상 변경 내용을 먼저 검토합니다.

## 시간과 누수 통제

다음 시간은 서로 다른 의미를 가지며 하나의 `first_seen` 또는 임의의 timestamp로 합치지 않습니다.

```text
published_at
retrieved_at
first_public_at
available_at
registered_at
frozen_at
valid_for_test_from
observed_at
collected_at
executed_at
first_candidate_at
evidence_available_at
```

전향 평가에는 query 실행시각뿐 아니라 각 Censys 레코드의 기저 `observed_at`도 `valid_for_test_from` 이후인지 확인해야 합니다. 기저 시각을 판별할 수 없으면 `prospective_time_unresolved`로 격리합니다.

## Query lifecycle

현재 기본 상태 전이는 다음과 같습니다.

```text
draft → validated → frozen → retired
```

현재 `validated`는 캠페인 연계 검증이 아니라 development precheck/review에 가까운 상태입니다. 향후 schema에서는 `prechecked` 또는 `reviewed` 의미로 분리해야 합니다.

동결 시 최종적으로 보존해야 할 항목:

- canonical query, hash, version, class와 variant
- source indicator와 feature provenance
- cutoff와 `frozen_at`, `valid_for_test_from`
- dataset/API/parser/normalizer version
- alert budget, schedule, score와 threshold
- 허용 validation evidence family

## Pagination과 raw 데이터

- raw page와 checkpoint는 append-only로 취급합니다.
- `partial_max_pages`는 양성 raw 관측과 미완료 prevalence를 뜻합니다.
- partial run은 Q2 source나 성능 분석에 사용할 수 없습니다.
- checkpoint resume 후 token이 소진되어야 complete입니다.
- raw hash 불일치는 자동 수정하지 않고 수집을 차단합니다.
- Censys 미관측은 host death 또는 infrastructure death가 아닙니다.

## Live 실행과 보안

기본 개발과 검증은 cached page, pure function, fixture와 임시 DB만 사용합니다.

라이브 Censys 실행에는 최소한 다음 조건이 필요합니다.

- 사용자의 명시적 승인
- `ALLOW_LIVE_CENSYS=1`
- 등록·review·freeze가 완료된 query
- query hash 일치
- page·비용 budget
- dataset split과 `valid_for_test_from`

CTI 검색·snapshot·LLM extraction에도 각각 설정된 live gate가 필요합니다. active scanning, victim 접근, restricted CTI 공개, 자동 차단은 이 프로젝트의 기본 범위가 아닙니다.

## 데이터와 파일 규칙

- `.env`와 credential은 버전 관리 및 산출물에서 제외합니다.
- 운영 SQLite와 `data/raw`를 테스트 대상으로 사용하지 않습니다.
- raw, checkpoint, candidate event, evidence와 grade history는 append-only로 유지합니다.
- 일반 문서와 데이터 산출물은 `YYYY-MM-DD-설명적이름` 형식을 사용합니다.
- Python 모듈, pytest 파일, migration, `README.md`, `pyproject.toml` 등 도구가 요구하는 파일은 표준 이름을 유지합니다.
- public export에서는 restricted indicator 원문값과 credential을 제거합니다.

## 외부 코드 재사용

CTI-Agent와 ORB_Hunt_v5에서 재사용한 pure logic은 `src/reused/`에 내부화되어 있습니다. runtime은 외부 저장소에 의존하지 않으며, 외부 경로는 provenance와 동등성 검증에만 사용합니다.

재사용 시 다음을 기록합니다.

- 원본 repository와 commit
- 감사 시 dirty 상태
- 원본 및 파생 파일 SHA-256
- 라이선스와 배포 범위
- adapter가 보존하거나 변경한 의미

관련 설정은 [configs/reuse_sources.yaml](configs/reuse_sources.yaml)에 있습니다.

## 주요 문서

- [구현 설계도](CTI-Censys-파이프라인-구현-설계도.md)
- [파이프라인 도식](CTI-Censys-파이프라인-도식.md)
- [논문 구성안 개정본](ORB-논문-구성안-개정.md)
- [기준선 이관 보고서](docs/2026-07-15-cti-research-이관-보고서.md)
- [기본 설정](configs/base.yaml)
- [외부 코드 재사용 provenance](configs/reuse_sources.yaml)

## 다음 increment

다음 구현 단위는 Phase B Stage 2의 Q0 landmark/continuity contract입니다.

- cached host/service 결과의 fingerprint와 `observed_at` 보존
- Q0 append-only observation timeline과 landmark reason
- continuous/probable/unknown/reassigned/contradicted 판정
- current response, historical evidence, missing scan, last positive 분리
- reviewed continuity만 derived pivot source로 승격
