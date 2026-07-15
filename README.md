# ORB Research Control Plane

이 디렉터리는 `ORB-논문-구현-설계서.md`의 공통 통제 계층을 구현한다.

현재 단계의 범위:

- `D:\Claude\CTI-Agent`의 CTI 검색·IoC 정규화 pure logic을 검토 후 프로젝트 내부 파생본으로 재사용
- `D:\Gemini\ORB_Hunt_v5`의 Censys query·안전 정책·결과 parser를 검토 후 내부 파생본으로 재사용
- 재사용 원천 commit과 핵심 파일 SHA-256 기록
- CTI 검색 프로토콜과 screening provenance
- Q0∼Q3 query registry, 동결, cutoff 및 prospective-test 통제
- CTI 검색 결과 정규화·원문 대조 IoC 검증
- Q0 exact-IP 등록과 Censys page_token 전체 페이지 수집
- 불변 원시 페이지와 append-only checkpoint 저장
- 외부 API 호출 없는 fixture 기반 회귀 테스트

## 현재 연구 판정과 필수 검증 게이트

이 프로젝트는 `검색 결과 일치`, `기술적 유사성`, `캠페인 연계`를 서로 다른 상태로 취급한다. 현재까지 구현된 것은 CTI-derived pivot 관측과 기술적 유사성 후보 생성까지이며, campaign-linked cohort 발견은 아직 검증되지 않았다.

현재 실증 상태:

- 완결된 Q1 direct non-IP query 9개는 결과 0건이다.
- 인증서 Q1 하나는 10페이지에서 최소 100개 raw hit를 반환했지만 next page token이 남아 있다. 이는 음성이 아니라 `partial_max_pages / campaign specificity unresolved`이다.
- Q0 IP는 현재 host record를 반환했지만 CTI 활동 시점부터 현재까지 동일 캠페인이 사용했다는 서비스 연속성은 검증되지 않았다.
- Q0에서 파생한 JARM+port Q2는 신규 기술적 유사성 hit 1개를 만들었지만 campaign cohort로 계산하지 않는다.

향후 campaign-linked cohort에 포함하려면 다음 게이트를 모두 통과해야 한다.

```text
CTI 원문 역할 검증
→ Q1 pivot identity·complete pagination·prevalence 검증
   또는 Q0 historical service continuity 검증
→ 서로 독립적인 복수 IoC/feature 조합
→ 동결 query의 prospective 실행
→ 검색에 사용하지 않은 특징 또는 미래 독립 CTI로 검증
→ evidence grade 부여
```

상태 해석:

| 상태 | 의미 | campaign cohort 포함 |
|---|---|---|
| `raw_search_hit` | query와 일치한 원시 결과 | 아니오 |
| `technical_similarity_candidate` | 기술 특징 일치 | 아니오 |
| `probable_campaign_link` | 일부 강한 근거, 독립 검증 불충분 | 기본 제외 |
| `high_confidence_campaign_link` | 시간 연속성과 독립 검증 통과 | 예 |
| `confirmed_campaign_member` | 독립 CTI·IR 등 외부 확인 | 예 |
| `broad_or_shared_pivot` | 광범위하게 재사용되는 pivot | 아니오 |
| `seed_continuity_unknown` | Q0 과거-현재 동일성 미확인 | 아니오 |

세부 구현 계약과 수용 기준은 `ORB-논문-구현-설계서.md`의 2.6, 5, 6.2 및 Phase 2A를 따른다.

live Censys 수집은 기본적으로 비활성이다. 실행 시 `ALLOW_LIVE_CENSYS=1`과 Censys Platform token을 환경변수에 설정해야 하며, registry에 등록된 query만 실행할 수 있다.

## 사용자 보유 CTI

사용자가 보유한 CTI는 `data/cti/`에 원본 파일명 그대로 둔다. 날짜 접두어를 붙이거나 파일을 이동·수정하지 않는다. `data/cti/index.json`에 원본 파일의 게시일과 발행처를 등록한 뒤 다음 명령을 실행한다.

```powershell
& $python -m src.cli cti-import-existing `
  --db data_registry/orb_research.sqlite `
  --source-root data/cti --index data/cti/index.json `
  --imported-at 2026-07-14T00:00:00+09:00 `
  --manifest data_registry/existing-cti-import.json
```

`index.json` 항목 예시는 다음과 같다.

```json
[{"file":"원본파일명.pdf","text_file":"원본파일명-OCR.txt","title":"보고서 제목","publisher":"발행기관","published_at":"2026-01-01T00:00:00Z","source_url":"https://원출처.example/report"}]
```

텍스트 레이어가 있는 PDF는 `text_file` 없이 `pypdf`로 직접 추출한다. 스캔 PDF는 같은 `data/cti/` 디렉터리에 UTF-8 OCR TXT를 두고 `text_file`에 원본 파일명을 기록한다. 암호화 PDF와 텍스트가 없는 PDF는 검증 없이 통과시키지 않는다. PDF 원본과 OCR sidecar는 모두 SHA-256으로 등록하며 수정하지 않는다.

검색으로 수집한 CTI만 `data/raw/cti/`에 `YYYY-MM-DD-설명적이름-고유ID` 형식으로 저장한다. 날짜는 검색 문서의 실제 취득일이다.

기존 CTI import manifest에 문서가 여러 개 있으면 추출할 PDF의 `document_id`를 지정한다.

```powershell
& $python -m src.cli cti-extract-iocs `
  --snapshot-metadata data_registry/existing-cti-import.json `
  --document-id cti-doc-문서ID `
  --cti-agent D:/Claude/CTI-Agent `
  --prompt configs/2026-07-14-ioc-extract-v1.md --model claude-sonnet-4-6 `
  --available-at 2026-07-14T00:00:00+09:00 `
  --out data_registry/verified-iocs.json
```

## 실행 환경 준비

Python 3.11 이상을 사용한다. 프로젝트 루트에서 다음 명령으로 날짜가 포함된 연구용 가상환경을 만들고 개발 의존성까지 설치한다.

```powershell
python -m venv 2026-07-14-python-env
& .\2026-07-14-python-env\Scripts\python.exe -m pip install --upgrade pip
& .\2026-07-14-python-env\Scripts\python.exe -m pip install -e ".[dev]"
$env:PYTHONPATH=(Get-Location).Path
```

이후 예시의 `python`은 다음 실행 파일을 의미한다.

```powershell
$python = ".\2026-07-14-python-env\Scripts\python.exe"
& $python -m src.cli --help
```

## Q0 등록

```powershell
$env:PYTHONPATH=(Get-Location).Path
& $python -m src.cli register-q0 `
  --db data_registry/orb_research.sqlite `
  --indicator-id ioc-example `
  --ip 192.0.2.10 `
  --indicator-available-at 2026-07-13T00:00:00+09:00 `
  --registered-at 2026-07-13T01:00:00+09:00 `
  --version 1 --config-hash CONFIG_SHA256
```

## Censys 전체 페이지 수집

Q0 등록 결과의 `query_id`를 사용한다. `--executed-at`은 연구 실행시각이며 `--cutoff-time` 이후에 사용할 수 있게 된 정보만 query에 포함되어야 한다.

```powershell
$env:PYTHONPATH=(Get-Location).Path
$env:ALLOW_LIVE_CENSYS='1'
$env:CENSYS_TOKEN='개인 액세스 토큰'
$env:CENSYS_API_ID='조직 ID'
& $python -m src.cli collect-censys `
  --db data_registry/orb_research.sqlite `
  --query-id qry-example `
  --raw-root data/raw/censys `
  --split development `
  --cutoff-time 2026-07-13T01:00:00+09:00 `
  --executed-at 2026-07-13T02:00:00+09:00 `
  --page-size 100
```

API token은 설정 파일·명령행·manifest에 기록하지 않는다. `data/raw`의 상세 host 결과는 research-restricted 데이터로 취급하며 공개 산출물에는 직접 포함하지 않는다. `partial_max_pages` 실행은 raw positive observation으로 보존하되 cohort 규모·prevalence·campaign specificity가 미확정이므로 normalization과 campaign-linked 분석 입력에서 제외한다. checkpoint의 next token으로 수집을 재개해 page token 소진을 확인한 뒤에만 완결 cohort로 평가한다.

수집 완료 후 raw page를 host/service observation으로 정규화한다. 이 명령은 network를 호출하지 않으며 banner와 HTTP title 원문 대신 SHA-256만 저장한다.

```powershell
& $python -m src.cli normalize-censys `
  --db data_registry/orb_research.sqlite `
  --query-run-id censys-run-example `
  --raw-directory data/raw/censys/censys-run-example `
  --collected-at 2026-07-14T02:00:00+09:00 `
  --out data_registry/censys-normalization.json
```

정규화 관측에서 fingerprint와 entity relation을 생성한다. 이 단계도 외부 API를 호출하지 않는다.

```powershell
& $python -m src.cli extract-fingerprints `
  --db data_registry/orb_research.sqlite `
  --query-run-id censys-run-example `
  --extractor-version fingerprint-v1 `
  --out data_registry/fingerprint-graph.json
```

## CTI 단계형 파이프라인

CTI 파이프라인은 사람의 screening을 건너뛰지 않도록 단계별 명령으로 분리되어 있다. 자동 structured extraction을 사용할 때는 `cti-verify-iocs` 대신 `cti-extract-iocs`를 실행한다.

```text
cti-search → cti-screen → cti-snapshot → cti-verify-iocs → cti-register-indicators → cti-plan-pivots
                                            └ cti-extract-iocs ┘
```

### 1. 기간 고정 검색

`protocol.json`은 `SearchProtocolRecord`, `watchlist.json`은 CTI-Agent의 groups/covert_networks 구조를 사용한다.

```powershell
$env:ALLOW_LIVE_CTI_SEARCH='1'
$env:BRAVE_SEARCH_API_KEY='Brave API key'
& $python -m src.cli cti-search `
  --db data_registry/orb_research.sqlite `
  --protocol data_registry/protocol.json --watchlist data_registry/watchlist.json `
  --cti-agent D:/Claude/CTI-Agent --manifest data_registry/search-result.json `
  --whitelist mandiant.com microsoft.com unit42.paloaltonetworks.com `
  --executed-at 2026-07-13T09:00:00+09:00
```

Brave 결과 저장 권한은 사용 중인 API 요금제·계약을 별도로 확인해야 한다. 이 구현은 snippet과 원시 응답을 제외한 최소 검색 metadata만 artifact에 저장한다.

### 2. 수동 screening

`decisions.json`은 다음 배열 형식이다.

```json
[{"candidate_id":"cti-candidate-...","decision":"include","reason_code":"technical_evidence","reviewer_id":"reviewer-1","notes":""}]
```

```powershell
& $python -m src.cli cti-screen --db data_registry/orb_research.sqlite `
  --search-manifest data_registry/search-result.json --decisions data_registry/decisions.json `
  --reviewed-at 2026-07-13T10:00:00+09:00 --out data_registry/decision-map.json
```

### 3. 포함 문헌 snapshot

```powershell
$env:ALLOW_CTI_SNAPSHOT_FETCH='1'
& $python -m src.cli cti-snapshot `
  --search-manifest data_registry/search-result.json --decision-map data_registry/decision-map.json `
  --snapshot-root data/raw/cti --manifest data_registry/snapshots.json `
  --whitelist mandiant.com microsoft.com unit42.paloaltonetworks.com `
  --retrieved-at 2026-07-13T11:00:00+09:00
```

### 4. IoC 원문 검증

`candidates.json`은 CTI-Agent extraction 결과와 동일하게 `scope`, `raw_form`, `observed_at`, `context`, `context_evidence`를 갖는 배열이다. 게시일이 검색 결과에 없으면 사람이 확인한 값을 `--published-at`으로 반드시 제공한다.

```powershell
& $python -m src.cli cti-verify-iocs `
  --snapshot-metadata data/raw/cti/cti-doc-.../metadata.json `
  --candidates data_registry/candidates.json --cti-agent D:/Claude/CTI-Agent `
  --published-at 2026-07-01T00:00:00Z --available-at 2026-07-13T11:00:00+09:00 `
  --out data_registry/verified-iocs.json
```

### 5. Q0/Q1 계획 등록

자동 추출을 사용할 경우 API key와 별도 live gate를 환경변수에만 설정한다. 추출 결과도 형식·원문·시간 검증을 통과해야 한다.

```powershell
$env:ANTHROPIC_API_KEY='개인 API key'
$env:ALLOW_LIVE_CTI_EXTRACTION='1'
& $python -m src.cli cti-extract-iocs `
  --snapshot-metadata data/raw/cti/cti-doc-.../metadata.json `
  --cti-agent D:/Claude/CTI-Agent `
  --prompt configs/2026-07-14-ioc-extract-v1.md --model claude-sonnet-4-6 `
  --available-at 2026-07-14T00:00:00+09:00 `
  --out data_registry/verified-iocs.json
```

검증된 IoC는 제한 계층 DB에 등록한다. HMAC key는 manifest나 DB에 기록되지 않는다.

```powershell
$env:ORB_PUBLIC_ID_HMAC_KEY='32바이트 이상의 연구별 비밀값'
& $python -m src.cli cti-register-indicators `
  --verified-manifest data_registry/verified-iocs.json `
  --snapshot-metadata data/raw/cti/cti-doc-.../metadata.json `
  --db data_registry/orb_research.sqlite `
  --ingested-at 2026-07-14T01:00:00+09:00 `
  --out data_registry/indicator-registration.json
```

```powershell
& $python -m src.cli cti-plan-pivots `
  --verified-manifest data_registry/verified-iocs.json `
  --db data_registry/orb_research.sqlite --orbhunt D:/Gemini/ORB_Hunt_v5 `
  --template-config D:/Gemini/ORB_Hunt_v5/configs/query_templates.platform.example.yaml `
  --registered-at 2026-07-13T12:00:00+09:00 --version 1 --config-hash CONFIG_SHA256 `
  --out data_registry/pivot-plans.json
```

마지막 명령은 Q0/Q1을 registry에 등록할 뿐 Censys를 호출하지 않는다. 실제 조회는 별도의 `collect-censys` 명령과 live gate를 사용한다.

## 내부 파생본 재사용 정책

runtime은 `D:/Claude/CTI-Agent` 또는 `D:/Gemini/ORB_Hunt_v5`를 import하지 않는다. 필요한 pure logic은 다음 위치에 복사·검토·수정되어 있다.

```text
src/reused/
├── cti_agent/
│   ├── ioc_regex.py
│   └── search_rules.py
└── orbhunt_v5/
    ├── pivot_safety.py
    ├── censys_query.py
    ├── error_policy.py
    └── result_parser.py
```

외부 저장소 경로는 `verify-reuse`에서 원본 commit과 SHA-256을 확인할 때만 필요하다. 각 내부 파일 머리말과 `configs/reuse_sources.yaml`은 원본 경로·commit·원본 hash·수정본 hash·변경 목적을 기록한다.

복사 과정에서 제외한 결합:

- CTI-Agent: requests, DB DAO, LLM client, CLI, 고정 `freshness=pw`
- ORB_Hunt_v5: pandas CSV pipeline, httpx/SDK live client, 외부 Pydantic schema

보완한 부분:

- 검색 기간을 등록 protocol의 custom date range로 고정
- 게시일 미확인 상태를 수집일로 대체하지 않음
- Censys Platform v3의 `host` envelope parsing
- page token checkpoint·재개와 query cutoff 통제
- 외부 작업 폴더가 없어도 runtime과 테스트가 동작

두 원본 저장소에는 감사 시점에 명시적 LICENSE/COPYING/NOTICE 파일이 확인되지 않았다. 따라서 내부 파생본은 논문 연구용으로만 사용하며 공개 코드 배포 전 별도 권리 확인과 필요 시 재구현을 수행한다.

## 테스트

```powershell
$env:PYTHONPATH=(Get-Location).Path
& $python -m unittest discover -s tests -v
& $python -m pytest -q
```

## 코드 머리말 규칙

모든 Python 모듈은 파일 첫 부분의 module docstring에 목적, 지원 RQ, 재사용 원천, 설계, 입력·출력, 시간 통제와 보안 제한을 기록한다.
"# ORB-Research" 
