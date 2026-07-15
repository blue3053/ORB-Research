# Stage 0 publication time increment

결과: CTI 게시일 raw 값, 정밀도, 원문 timezone을 검색 후보부터 snapshot과 source document까지 보존한다. date-only 값은 `published_at=None`으로 유지하며 UTC 자정의 exact timestamp로 승격하지 않는다. 현재 exact-only assertion schema에는 date-only source가 진입하지 못하도록 fail-closed 처리한다.

게이트: passed — `2026-01-01`은 `TimePrecision.DATE`, `exact_datetime=None`, `source_timezone=unknown`으로 보존되고, exact publication timestamp가 없는 문서의 assertion 생성과 등록이 차단된다. Stage 0 전체 gate는 source family와 접근/export 경계가 남아 있어 partial이다.

라우팅: lead=frontier reasoning/high; builder=balanced coding-tool-use/medium; reviewer=self static audit, independent reviewer unverified. 현재 환경에서 별도 모델 reviewer를 선택하거나 subagent를 위임하지 않았다.

검증:

- 관련 CTI `unittest` 20개 통과
- `cti-register-indicators --help` 통과
- 전체 offline `unittest` 61개 통과
- 승인 범위 `git diff --check` 통과
- live API와 운영 DB 사용 없음
- 번들 Python에 `httpx`가 없어 offline 테스트 import 시 빈 모듈로만 격리했으며 HTTP 경로는 호출하지 않음

파일:

- `D:\Codex\ORB-Research\src\models.py`
- `D:\Codex\ORB-Research\src\cli.py`
- `D:\Codex\ORB-Research\src\cti\search_execution.py`
- `D:\Codex\ORB-Research\src\cti\snapshots.py`
- `D:\Codex\ORB-Research\src\cti\corpus_registry.py`
- `D:\Codex\ORB-Research\src\cti\ioc_extraction.py`
- `D:\Codex\ORB-Research\tests\test_cti_search_execution.py`
- `D:\Codex\ORB-Research\tests\test_cti_workflow.py`
- `D:\Codex\ORB-Research\tests\test_cti_cli_pipeline.py`
- `D:\Codex\ORB-Research\tests\test_cti_ioc_extraction.py`
- `D:\Codex\ORB-Research\docs\2026-07-15-stage0-publication-time-increment.md`

남은 위험: date/month/year/range 게시일을 assertion의 `first_public_at` 구간으로 표현하는 공통 `TemporalValue` schema는 아직 없다. 따라서 비정밀 게시일 문서는 코퍼스 등록은 가능하지만 assertion 생성은 차단된다. 별도 독립 reviewer 감사도 아직 수행하지 않았다.

다음 increment: `source_relationships`와 `source_families`를 추가하고 동일 source family 재게시가 독립 evidence로 중복 집계되지 않음을 fixture로 증명한다.
