"""Typed registry for allowlisted public sources used by the offline crawler."""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class SourceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(pattern=r"^[a-z0-9-]+$")
    title: str = Field(min_length=2)
    url: HttpUrl
    source_version: str = Field(min_length=1)
    publisher: str = Field(min_length=2)
    jurisdiction: str = Field(min_length=2)
    product: str = Field(min_length=2)
    expected_mime_type: str = Field(min_length=3)
    content_start: str | None = None
    content_end: str | None = None
    status: str = "ACTIVE"


DEFAULT_SOURCE_REGISTRY = Path(__file__).with_name("data") / "source_registry.json"


def load_source_registry(path: str | Path = DEFAULT_SOURCE_REGISTRY) -> list[SourceSpec]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    records = [SourceSpec.model_validate(item) for item in payload]
    ids = [item.source_id for item in records]
    if len(ids) != len(set(ids)):
        raise ValueError("source registry contains duplicate source_id values")
    return records
