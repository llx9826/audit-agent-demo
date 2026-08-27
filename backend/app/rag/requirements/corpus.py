from __future__ import annotations

import json
from pathlib import Path

from .models import AtomicRequirementRecord, requirement_from_mapping


DEFAULT_REQUIREMENT_CORPUS = Path(__file__).with_name("data") / "requirements.jsonl"


def load_requirement_corpus(path: str | Path = DEFAULT_REQUIREMENT_CORPUS) -> list[AtomicRequirementRecord]:
    source = Path(path)
    records: list[AtomicRequirementRecord] = []
    seen: set[str] = set()
    with source.open(encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            record = requirement_from_mapping(json.loads(raw))
            if record.requirement_id in seen:
                raise ValueError(f"{source}:{line_number} duplicate requirement_id")
            seen.add(record.requirement_id)
            records.append(record)
    if not records:
        raise ValueError(f"requirement corpus is empty: {source}")
    return records
