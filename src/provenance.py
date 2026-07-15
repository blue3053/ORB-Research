"""파일·설정·재사용 원천 provenance 유틸리티.

목적: 외부 코드와 연구 입력의 commit·SHA-256을 실행 manifest에 연결한다.
지원 RQ: RQ1∼RQ5 공통.
재사용 원천: CTI-Agent snapshot hash와 ORB_Hunt_v5 config_hash 설계를 통합했다.
설계: canonical JSON과 streaming file hash를 사용하며 외부 저장소는 읽기만 한다.
입력·출력: 파일·객체·repository 경로를 받아 hash 또는 metadata dict를 반환한다.
시간·provenance 통제: dirty 상태를 기록해 commit만으로 재현되지 않는 경우를 드러낸다.
보안·라이선스: 파일 내용과 시크릿은 manifest에 넣지 않고 hash만 기록한다.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Iterable


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(payload)


def hash_files(root: Path, relative_paths: Iterable[str]) -> dict[str, str]:
    """root 밖으로 탈출하지 않는 파일 목록의 SHA-256을 계산한다."""

    root = root.resolve()
    result: dict[str, str] = {}
    for relative in relative_paths:
        candidate = (root / relative).resolve()
        if root not in candidate.parents:
            raise ValueError(f"path escapes reuse root: {relative}")
        if not candidate.is_file():
            raise FileNotFoundError(candidate)
        result[Path(relative).as_posix()] = sha256_file(candidate)
    return result


def git_metadata(repository: Path) -> dict[str, Any]:
    """repository를 변경하지 않고 HEAD와 dirty 상태를 읽는다."""

    repository = repository.resolve()
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, check=True,
        capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repository, check=True,
        capture_output=True, text=True, encoding="utf-8",
    ).stdout
    return {"path": str(repository), "commit": commit, "dirty": bool(status.strip())}

