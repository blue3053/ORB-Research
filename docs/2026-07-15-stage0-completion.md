# Phase A Stage 0 완료 보고

## 결과

Phase A Stage 0의 protocol/corpus 고정 gate를 offline 구현으로 완료했다.

- research cutoff, source access class, acquisition mode가 search protocol hash에 포함된다.
- publication 원문, 정밀도, timezone, retrieved time, 원문/OCR hash가 source identity에 보존된다.
- date-only publication은 정확한 UTC timestamp로 승격되지 않는다.
- source family와 재게시·번역·요약·후속 관계를 사람이 검토한 provenance로 등록한다.
- 동일 source family는 독립 근거로 중복 계수하지 않는다.
- access class와 corpus purpose가 search candidate에서 verified extraction manifest까지 전파된다.
- prospective-validation 자료는 development pivot 계획에 들어가기 전에 차단된다.
- restricted source는 public metadata export에서 fail-closed로 거부된다.
- 저장된 protocol, source document, source-family membership, canonical document,
  assertion document 참조를 `cti-audit-stage0`로 검사할 수 있다.

## Gate

- 구현 gate: passed
- 근거: 전체 offline unittest 64개 통과, CLI help 통과, `git diff --check` 통과
- production/운영 DB audit: unverified — 운영 DB를 수정하거나 대상으로 실행하지 않았다.
- independent reviewer audit: unverified — 이 세션에는 독립 reviewer가 배정되지 않았다.
- live CTI/Censys 실행: 수행하지 않음

기존 payload에 새 provenance field가 없으면 자동 보정하지 않고 Stage 0 audit issue로
보고한다. 이는 오래된 운영 데이터를 추정 값으로 덮어쓰지 않기 위한 의도된 동작이다.

## 검증

```powershell
python -B -m unittest discover tests
python -B -m src.cli --help
git diff --check
```

실제 실행 환경에서는 `httpx`, `PyYAML`, `pytest`가 설치되지 않아, 저장소의 offline
unittest를 bundled Python과 최소 import stub으로 실행했다. 결과는 64 tests, OK였다.

## 주요 변경 파일

- `src/models.py`
- `src/config.py`
- `src/cli.py`
- `src/cti/search_protocol.py`
- `src/cti/search_execution.py`
- `src/cti/snapshots.py`
- `src/cti/ioc_extraction.py`
- `src/cti/corpus_registry.py`
- `configs/base.yaml`
- `README.md`
- `tests/test_config_validation.py`
- `tests/test_cti_search_execution.py`
- `tests/test_cti_workflow.py`
- `tests/test_cti_ioc_extraction.py`
- `tests/test_cti_cli_pipeline.py`

Prompt config는 내용 변경 없이 원래 dated 이름인
`configs/2026-07-14-ioc-extract-v1.md`와
`configs/2026-07-14-ioc-extract-strict-scope-v1.md`로 복원했다.

## 남은 위험

- 실제 corpus는 source-family human review와 metadata 보강 후 audit를 통과해야 한다.
- Stage 0 audit는 데이터 무결성 gate이며 독립적인 연구 방법론 검토를 대체하지 않는다.
- assertion의 역할 taxonomy, confidence 분리, acceptance review, cutoff 연결은 Stage 1 범위다.

## 다음 increment

Stage 1의 최소 increment는 CTI assertion acceptance gate다. Indicator, assertion,
source mention identity를 분리하고, role/source/extraction confidence 및 review 상태를
명시한 뒤 `available_at <= cutoff`를 query 등록 경로에서 fail-closed로 적용한다.
