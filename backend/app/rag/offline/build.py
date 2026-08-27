"""Build auditable source chunks consumed by offline embedding/index jobs."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import time

from .chunking import ChunkConfig, HuggingFaceTokenSpanTokenizer, UnicodeTokenSpanTokenizer
from .contextualization import ContextRequest, DeterministicContextualizer, RetrievalContextualizer
from .document_processing import SourceArtifact, process_source
from .source_registry import SourceSpec


def _contextualize_with_checkpoint_retry(
    contextualizer: RetrievalContextualizer,
    request: ContextRequest,
) -> str:
    """在 Gateway 的单请求重试之上，提供离线 Chunk 级恢复。

    每个成功结果都由 Contextualizer 立即写入 Cache，因此进程失败后
    重跑不会重复计费，也不会降级为本地模板。
    """

    attempts = int(os.getenv("OFFLINE_CONTEXT_JOB_RETRIES", "4"))
    for attempt in range(attempts + 1):
        try:
            return contextualizer.contextualize(request)
        except Exception:  # noqa: BLE001 - batch boundary retries provider + output validation
            if attempt >= attempts:
                raise
            time.sleep(min(8.0, 1.0 * (2**attempt)))
    raise RuntimeError("unreachable contextualization retry state")


def _artifact(source: SourceSpec, snapshot_dir: Path) -> SourceArtifact:
    source_dir = snapshot_dir / source.source_id
    metadata = json.loads((source_dir / "snapshot.json").read_text(encoding="utf-8"))
    candidates = list(source_dir.glob("source.*"))
    if len(candidates) != 1:
        raise ValueError(f"{source.source_id} must have exactly one source artifact")
    return SourceArtifact(
        source_id=source.source_id,
        source_version=source.source_version,
        title=source.title,
        source_url=str(source.url),
        mime_type=str(metadata["content_type"]),
        content=candidates[0].read_bytes(),
        content_start=source.content_start,
        content_end=source.content_end,
    )


def build_source_chunks(
    sources: list[SourceSpec],
    *,
    snapshot_dir: str | Path,
    output_path: str | Path,
    manifest_path: str | Path,
    config: ChunkConfig | None = None,
    tokenizer_profile: str = "demo",
    tokenizer_model: str = "BAAI/bge-m3",
    contextualizer: RetrievalContextualizer | None = None,
    catalog_path: str | Path | None = None,
) -> dict:
    resolved_config = config or ChunkConfig()
    tokenizer = (
        HuggingFaceTokenSpanTokenizer(tokenizer_model)
        if tokenizer_profile == "real"
        else UnicodeTokenSpanTokenizer()
    )
    resolved_contextualizer = contextualizer or DeterministicContextualizer()
    rows: list[dict] = []
    prepared: list[tuple[dict, ContextRequest, str, str]] = []
    parents = 0
    built_sources: set[str] = set()
    semantic_unit_ids: set[str] = set()
    token_fallback_chunks = 0
    root = Path(snapshot_dir)
    for source in sources:
        if not (root / source.source_id / "snapshot.json").exists():
            continue
        artifact = _artifact(source, root)
        for parent, chunks in process_source(
            artifact,
            config=resolved_config,
            tokenizer=tokenizer,
        ):
            parents += 1
            built_sources.add(source.source_id)
            for chunk in chunks:
                semantic_unit_ids.add(chunk.semantic_unit_id)
                if chunk.split_strategy == "TOKEN_WINDOW_FALLBACK":
                    token_fallback_chunks += 1
                context_request = ContextRequest(
                    source_id=source.source_id,
                    source_title=source.title,
                    publisher=source.publisher,
                    product=source.product,
                    jurisdiction=source.jurisdiction,
                    parent_heading=parent.heading,
                    parent_text=parent.text,
                    chunk_text=chunk.text,
                    semantic_title=chunk.semantic_title,
                )
                aliases = " ".join(chunk.retrieval_aliases)
                prepared.append(({
                    **asdict(chunk),
                    "source_id": source.source_id,
                    "source_title": source.title,
                    "source_version": source.source_version,
                    "source_checksum": artifact.checksum,
                    "source_url": str(source.url),
                    "publisher": source.publisher,
                    "jurisdiction": source.jurisdiction,
                    "product": source.product,
                    "parent_heading": parent.heading,
                    "parent_text": parent.text,
                    "offline_stage": "SEMANTIC_CHILD_CHUNK",
                }, context_request, aliases, chunk.text))

    # 上下文生成是网络 I/O 密集型任务；小并发能显著缩短首次建库，
    # 工作线程数保持可配置，避免压垮内部 vLLM 或触发云端限流。
    workers = max(1, int(os.getenv("OFFLINE_CONTEXT_WORKERS", "3")))
    requests = [item[1] for item in prepared]
    if workers == 1:
        contexts = [
            _contextualize_with_checkpoint_retry(resolved_contextualizer, request)
            for request in requests
        ]
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="rag-context") as executor:
            contexts = list(executor.map(
                lambda request: _contextualize_with_checkpoint_retry(resolved_contextualizer, request),
                requests,
            ))
    for (row, _request, aliases, chunk_text), generated_context in zip(
        prepared,
        contexts,
        strict=True,
    ):
        row.update({
            "generated_context": generated_context,
            "embed_input": f"{generated_context}\n{chunk_text}",
            "bm25_input": f"{generated_context} {aliases} {chunk_text}".strip(),
        })
        rows.append(row)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    manifest = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "offline_pipeline": [
            "SOURCE_REGISTRY",
            "ROBOTS_AWARE_FETCH",
            "TEXT_LAYER_EXTRACTION",
            "FORMAT_NORMALIZATION_AND_CONTENT_SCOPING",
            "PARENT_SECTION_SPLIT",
            "SEMANTIC_CLAUSE_AND_SENTENCE_GROUPING",
            "OVERSIZED_UNIT_TOKEN_FALLBACK_WITH_OVERLAP",
            "CONTEXTUAL_RETRIEVAL_TEXT_AND_ALIAS_BUILD",
            "DENSE_AND_BM25_INDEX_INPUT",
        ],
        "source_count": len(built_sources),
        "parent_count": parents,
        "semantic_unit_count": len(semantic_unit_ids),
        "child_chunk_count": len(rows),
        "token_fallback_chunk_count": token_fallback_chunks,
        "contextualized_chunk_count": len(rows),
        "contextualization_workers": workers,
        "chunk_config": asdict(resolved_config),
        "tokenizer": tokenizer.name,
        "contextualizer": resolved_contextualizer.name,
        "contextualization_prompt": getattr(
            resolved_contextualizer,
            "prompt_metadata",
            None,
        ),
        "output": str(destination),
    }
    Path(manifest_path).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    if catalog_path is not None:
        from .catalog_store import RAGCatalogStore

        catalog = RAGCatalogStore(catalog_path)
        try:
            manifest["catalog_build_id"] = catalog.publish_chunks(rows, manifest)
        finally:
            catalog.close()
        Path(manifest_path).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return manifest
