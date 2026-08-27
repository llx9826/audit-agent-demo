"""将官方来源 Chunk 与 Atomic Requirement 对齐，生成唯一在线索引输入。

这一层解决一个容易被忽略的问题：爬虫/Chunk 产物和在线向量库不能是
两份互不相干的数据。官方条款必须回指真实 source checksum、Parent/Child
和模型生成的检索上下文，然后才能进入 BGE-M3/Milvus。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any


_TERM = re.compile(r"[\u3400-\u9fff]|[A-Za-z0-9]+", re.UNICODE)


def _terms(text: str) -> set[str]:
    return {item.lower() for item in _TERM.findall(text) if item.strip()}


def _alignment_score(requirement: dict[str, Any], chunk: dict[str, Any]) -> float:
    """使用条款、标题和章节的词项覆盖做可重放对齐。"""

    query = _terms(" ".join([
        str(requirement.get("title", "")),
        str(requirement.get("source_section", "")),
        str(requirement.get("atomic_requirement", "")),
    ]))
    document = _terms(" ".join([
        str(chunk.get("parent_heading", "")),
        str(chunk.get("semantic_title", "")),
        str(chunk.get("text", "")),
    ]))
    if not query:
        return 0.0
    return len(query & document) / len(query)


def link_requirement_catalog(
    *,
    requirement_path: str | Path,
    chunk_path: str | Path,
    output_path: str | Path,
    manifest_path: str | Path,
) -> dict[str, Any]:
    """构建带真实 Chunk 来源的索引目录；官方记录无法对齐时直接失败。"""

    requirements = [json.loads(line) for line in Path(requirement_path).read_text(encoding="utf-8").splitlines() if line]
    chunks = [json.loads(line) for line in Path(chunk_path).read_text(encoding="utf-8").splitlines() if line]
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        by_source[str(chunk["source_id"])].append(chunk)

    linked = 0
    official = 0
    scores: list[float] = []
    output: list[dict[str, Any]] = []
    for requirement in requirements:
        metadata = dict(requirement.get("metadata") or {})
        if metadata.get("source_kind") == "OFFICIAL_PUBLIC":
            official += 1
            source_id = str(metadata.get("source_id") or "")
            candidates = by_source.get(source_id, [])
            if not candidates:
                raise ValueError(
                    f"official requirement {requirement['requirement_id']} has no source chunk for {source_id!r}"
                )
            best = max(candidates, key=lambda item: (_alignment_score(requirement, item), item["child_chunk_id"]))
            score = _alignment_score(requirement, best)
            metadata.update({
                "child_chunk_id": best["child_chunk_id"],
                "parent_chunk_id": best["parent_chunk_id"],
                "parent_title": best["parent_heading"],
                "parent_text": best["contextual_text"],
                "generated_context": best["generated_context"],
                "source_checksum": best["source_checksum"],
                "source_version": best["source_version"],
                "source_url": best["source_url"],
                "alignment_score": round(score, 6),
                "retrieval_origin": "OFFICIAL_SNAPSHOT_MODEL_CONTEXTUALIZED_CHUNK",
            })
            linked += 1
            scores.append(score)
        else:
            # 内部制度在真实部署由银行的制度库提供；本地 Case 规则仍保持独立。
            metadata.setdefault("child_chunk_id", f"CHILD-{requirement['requirement_id']}")
            metadata.setdefault("retrieval_origin", "INTERNAL_REQUIREMENT_CATALOG")
        requirement["metadata"] = metadata
        output.append(requirement)

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in output),
        encoding="utf-8",
    )
    manifest = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "input_requirement_count": len(requirements),
        "official_requirement_count": official,
        "official_linked_count": linked,
        "source_chunk_count": len(chunks),
        "minimum_alignment_score": round(min(scores), 6) if scores else None,
        "average_alignment_score": round(sum(scores) / len(scores), 6) if scores else None,
        "output": str(destination),
        "pipeline": [
            "ATOMIC_REQUIREMENT",
            "SOURCE_ID_SCOPE",
            "SOURCE_CHUNK_ALIGNMENT",
            "MODEL_CONTEXT_AND_PROVENANCE_ATTACHMENT",
            "MILVUS_INDEX_INPUT",
        ],
    }
    Path(manifest_path).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest
