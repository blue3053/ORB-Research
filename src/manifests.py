"""불변 JSON manifest와 append-only JSONL 기록기.

목적: 동일 입력·설정·query에서 생성된 연구 실행을 재구성할 수 있게 한다.
지원 RQ: RQ1∼RQ5 공통, 특히 RQ4 후보 ledger와 RQ5 비교평가.
재사용 원천: ORB_Hunt_v5 run_id/config_hash 계약을 확장했다.
설계: 기존 파일과 내용이 같으면 no-op, 다르면 덮어쓰지 않고 실패한다.
입력·출력: serializable record를 immutable JSON 또는 append-only JSONL로 기록한다.
시간·provenance 통제: caller가 제공한 cutoff·hash를 그대로 보존한다.
보안·라이선스: secret과 raw active-victim indicator를 기록하지 않는 상위 schema가 전제다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

from src.provenance import canonical_json_hash


def _json_payload(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def write_immutable_json(path: Path, value: Any) -> str:
    """새 manifest를 만들거나 동일 내용의 기존 manifest를 확인한다."""

    payload = _json_payload(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing != payload:
            raise FileExistsError(f"immutable manifest already exists with different content: {path}")
        return canonical_json_hash(json.loads(existing))
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return canonical_json_hash(json.loads(payload))


def append_jsonl(path: Path, value: Any) -> None:
    """후보·실행 원장에 한 레코드를 append하고 flush한다."""

    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    line = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def load_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc

