"""从持久化事件导出人工确认 Hard Case JSONL。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.evaluation import project_feedback
from app.persistence.repository import SQLiteCaseRepository


DEFAULT_DB = Path(__file__).resolve().parents[1] / ".data" / "material_completeness_v1.sqlite3"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / ".data" / "feedback" / "material_hard_cases.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--case-id", action="append", default=[])
    args = parser.parse_args()

    repository = SQLiteCaseRepository(args.db)
    try:
        case_ids = args.case_id or sorted(repository.cases)
        hard_cases = [
            hard_case
            for case_id in case_ids
            for hard_case in project_feedback(repository.event_dicts(case_id))["hard_cases"]
        ]
    finally:
        repository.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in hard_cases),
        encoding="utf-8",
    )
    print(json.dumps({
        "case_count": len(case_ids),
        "hard_case_count": len(hard_cases),
        "output": str(args.output),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
