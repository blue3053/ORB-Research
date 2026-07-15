# CTI_Research 기준선 이관 보고서

## 결과

`D:\Codex\CTI_Research`의 검증된 Python 기준선을 `D:\Codex\ORB-Research`로 신규 이관했다. 기존 ORB-Research 문서와 `data/`는 변경하지 않았다.

- 이관 파일: 60개
- 원본-대상 SHA-256 불일치: 0개
- CLI help: 통과
- 집중 테스트: 16 passed
- 전체 오프라인 테스트: 56 passed
- live API 실행: 없음

## 설계도 게이트

| 범위 | 판정 | 근거 |
|---|---|---|
| 기존 구현 기준선 | passed | 소스·테스트·설정·CLI 이관 및 56개 회귀 테스트 통과 |
| Stage 0–1 / Phase A | partial | corpus/query registry 기반은 있으나 source family, 시간 정밀도, assertion review gate는 미구현 |
| Stage 2–3 / Phase B | partial | Q0, 제한된 Q1 planning, pagination 기반은 있으나 continuity와 pivot eligibility는 미구현 |
| Stage 4–8 / Phase C–F | unverified/not implemented | 설계도의 신규 feature, entity, assessment, prospective, analysis 계층이 없음 |

## 검증 명령

```powershell
& 'D:\Codex\CTI_Research\2026-07-14-python-env\Scripts\python.exe' -B -m pytest -q -p no:cacheprovider tests\test_config_validation.py tests\test_cutoff_integrity.py tests\test_query_registry.py tests\test_paginated_collection.py tests\test_internal_hash_manifest.py
& 'D:\Codex\CTI_Research\2026-07-14-python-env\Scripts\python.exe' -B -m src.cli --help
& 'D:\Codex\CTI_Research\2026-07-14-python-env\Scripts\python.exe' -B -m pytest -q -p no:cacheprovider
```

## 생성 파일

### 프로젝트 루트

- `2026-07-13-ORB-논문-구성안.md`
- `ORB-논문-구현-설계서.md`
- `README.md`
- `pyproject.toml`

### configs

- `configs/2026-07-14-ioc-extract-strict-scope-v1.md`
- `configs/2026-07-14-ioc-extract-v1.md`
- `configs/base.yaml`
- `configs/reuse_sources.yaml`

### src

- `src/__init__.py`
- `src/cli.py`
- `src/config.py`
- `src/manifests.py`
- `src/models.py`
- `src/provenance.py`
- `src/adapters/__init__.py`
- `src/adapters/cti_agent.py`
- `src/adapters/orbhunt_censys.py`
- `src/censys/__init__.py`
- `src/censys/paginated_collection.py`
- `src/censys/q0_seed.py`
- `src/censys/query_lifecycle.py`
- `src/censys/query_registry.py`
- `src/cti/__init__.py`
- `src/cti/brave_search.py`
- `src/cti/corpus_registry.py`
- `src/cti/ioc_extraction.py`
- `src/cti/pivot_planning.py`
- `src/cti/search_execution.py`
- `src/cti/search_protocol.py`
- `src/cti/snapshots.py`
- `src/cti/workflow.py`
- `src/reused/__init__.py`
- `src/reused/cti_agent/__init__.py`
- `src/reused/cti_agent/ioc_regex.py`
- `src/reused/cti_agent/search_rules.py`
- `src/reused/orbhunt_v5/__init__.py`
- `src/reused/orbhunt_v5/censys_query.py`
- `src/reused/orbhunt_v5/error_policy.py`
- `src/reused/orbhunt_v5/pivot_safety.py`
- `src/reused/orbhunt_v5/result_parser.py`

### tests

- `tests/__init__.py`
- `tests/test_brave_search.py`
- `tests/test_censys_adapter.py`
- `tests/test_cli_integration.py`
- `tests/test_config_validation.py`
- `tests/test_cti_adapter.py`
- `tests/test_cti_cli_pipeline.py`
- `tests/test_cti_ioc_extraction.py`
- `tests/test_cti_search_execution.py`
- `tests/test_cti_workflow.py`
- `tests/test_cutoff_integrity.py`
- `tests/test_internal_error_policy.py`
- `tests/test_internal_hash_manifest.py`
- `tests/test_internal_reuse.py`
- `tests/test_paginated_collection.py`
- `tests/test_pivot_planning.py`
- `tests/test_pivot_safety.py`
- `tests/test_q0_seed.py`
- `tests/test_query_registry.py`
- `tests/test_reuse_equivalence.py`

### 보고서

- `docs/2026-07-15-cti-research-이관-보고서.md`

## 제외 항목

`.env`, `.git`, `.pytest_cache`, `__pycache__`, 가상환경, `egg-info`, `tmp`, CTI_Research의 `data/`, `data_registry/`, 운영 SQLite와 raw Censys 파일은 복사하지 않았다.

## 남은 위험과 다음 increment

현재 테스트 일부는 provenance 동등성 확인을 위해 `D:\Claude\CTI-Agent`와 `D:\Gemini\ORB_Hunt_v5`가 있으면 해당 원본을 읽는다. runtime 자체는 내부 `src/reused/` 파생본을 사용한다.

다음 increment는 Phase A의 source document 시간 정밀도·source family·assertion review 계약과 fail-closed 테스트 구현이다.
