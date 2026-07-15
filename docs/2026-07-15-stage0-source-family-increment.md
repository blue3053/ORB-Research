# Stage 0 source family increment

결과: 사람이 검토한 `source_families`, `source_relationships`, 문서 membership을 append-only SQLite registry에 추가했다. `cti-register-indicators`는 source family manifest를 필수로 받고, reviewed family가 없으면 assertion bundle 등록을 차단한다. 동일 family의 원문과 번역본은 독립 source family 한 개로 계산한다.

게이트: passed — 동일 family 문서 두 개는 독립 family 한 개, 별도 family를 포함하면 두 개로 계산된다. family membership이 없는 문서 또는 미등록 family를 참조하는 assertion 등록은 fail-closed다. Stage 0 전체 gate는 public/restricted 접근 및 export 경계가 남아 있어 partial이다.

라우팅: lead=frontier reasoning/high; builder=balanced coding-tool-use/medium; reviewer=self static audit, independent reviewer unverified. 사람의 family 판정은 manifest 입력으로 유지하며 자동 URL 기반 병합은 구현하지 않았다.

검증:

- 관련 CTI `unittest` 17개 통과
- `cti-register-indicators --help` 통과
- 전체 offline `unittest` 62개 통과
- 승인 범위 `git diff --check` 통과
- live API와 운영 DB 사용 없음
- 번들 Python의 미설치 `httpx`는 offline import에서 빈 모듈로만 격리했고 HTTP 경로는 호출하지 않음

파일:

- `D:\Codex\ORB-Research\src\models.py`
- `D:\Codex\ORB-Research\src\cti\corpus_registry.py`
- `D:\Codex\ORB-Research\src\cli.py`
- `D:\Codex\ORB-Research\tests\test_cti_ioc_extraction.py`
- `D:\Codex\ORB-Research\tests\test_cti_cli_pipeline.py`
- `D:\Codex\ORB-Research\tests\test_cti_workflow.py`
- `D:\Codex\ORB-Research\docs\2026-07-15-stage0-source-family-increment.md`

남은 위험: 기존 SQLite의 과거 source document와 assertion에는 family membership이 없다. 이를 자동 추정하지 않으며 독립 family 계산에서 unresolved로 차단한다. 운영 데이터 migration/backfill은 별도 사람 검토와 승인 없이는 수행하지 않는다. 독립 reviewer 감사도 아직 수행하지 않았다.

다음 increment: source document에 public/restricted access class를 고정하고 restricted 자료가 public export에 포함되지 않도록 fail-closed export validator를 추가한다.
