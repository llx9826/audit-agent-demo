"""Build or refresh the production Milvus requirement index."""
from __future__ import annotations

from datetime import datetime, timezone
import gc
import json
import os
from pathlib import Path
from typing import Sequence

import numpy as np

from app.bootstrap.settings import settings_from_env
from app.rag.adapters import BGEM3DenseEncoder
from app.rag.requirements.milvus_index import MilvusIndexConfig, MilvusRequirementIndexer
from app.rag.requirements.store import SQLiteRequirementStore
from app.rag.offline.catalog_store import RAGCatalogStore


class PrecomputedPassageEncoder:
    """使索引发布阶段不需同时持有 PyTorch 和 Milvus Lite native runtime。"""

    def __init__(self, model_name: str, texts: Sequence[str], vectors: Sequence[Sequence[float]]) -> None:
        self.model_name = model_name
        self._by_text = {
            text: list(map(float, vector))
            for text, vector in zip(texts, vectors, strict=True)
        }

    def encode_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._by_text[text] for text in texts]

    def encode_queries(self, texts: Sequence[str]) -> list[list[float]]:  # pragma: no cover - index only
        raise RuntimeError("precomputed passage encoder cannot encode queries")


def main() -> int:
    # 离线 Job 与应用运行时使用同一份 .env，避免建库和查询指向两个集合。
    settings_from_env(profile="real")
    try:
        from pymilvus import MilvusClient
    except ImportError as exc:
        raise RuntimeError("Install backend/requirements-integrations.txt before indexing") from exc
    uri = os.getenv("REQUIREMENT_RAG_MILVUS_URI", "").strip()
    if not uri:
        raise ValueError("REQUIREMENT_RAG_MILVUS_URI is required")
    records = SQLiteRequirementStore().list_all()
    texts = [record.retrieval_text for record in records]
    encoder = BGEM3DenseEncoder(
        os.getenv("REQUIREMENT_RAG_BGE_MODEL", "BAAI/bge-m3"),
        use_fp16=os.getenv("REQUIREMENT_RAG_USE_FP16", "true").lower() == "true",
        device=os.getenv("REQUIREMENT_RAG_MODEL_DEVICE") or None,
    )
    vectors = encoder.encode_documents(texts)
    embedding_artifact = Path(
        os.getenv("REQUIREMENT_RAG_EMBEDDING_ARTIFACT", ".data/requirement_embeddings.npz")
    )
    embedding_artifact.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        embedding_artifact,
        requirement_ids=np.asarray([record.requirement_id for record in records]),
        vectors=np.asarray(vectors, dtype=np.float32),
    )
    precomputed_encoder = PrecomputedPassageEncoder(encoder.model_name, texts, vectors)
    dense_model_name = encoder.model_name
    del encoder, vectors
    gc.collect()

    if not uri.startswith(("http://", "https://")):
        Path(uri).parent.mkdir(parents=True, exist_ok=True)
    client = MilvusClient(uri=uri, token=os.getenv("REQUIREMENT_RAG_MILVUS_TOKEN") or None)
    indexer = MilvusRequirementIndexer(
        client=client,
        encoder=precomputed_encoder,
        config=MilvusIndexConfig(
            collection_name=os.getenv("REQUIREMENT_RAG_MILVUS_COLLECTION", "material_atomic_requirements"),
            batch_size=int(os.getenv("REQUIREMENT_RAG_INDEX_BATCH_SIZE", "128")),
            bm25_tokenizer=os.getenv("REQUIREMENT_RAG_BM25_TOKENIZER", "jieba"),
            index_version=os.getenv("REQUIREMENT_RAG_INDEX_VERSION", "requirements-v2-zh-bm25"),
        ),
    )
    count = indexer.upsert(records)
    manifest = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "backend": "MILVUS_LITE" if not uri.startswith(("http://", "https://")) else "MILVUS_SERVER",
        "collection": indexer.config.collection_name,
        "record_count": count,
        "dense_model": dense_model_name,
        "dense_dimension": indexer.config.dense_dimension,
        "embedding_artifact": str(embedding_artifact),
        "sparse_model": "MILVUS_BM25_FUNCTION",
        "bm25_tokenizer": indexer.config.bm25_tokenizer,
        "index_version": indexer.config.index_version,
        "reranker_model": os.getenv("REQUIREMENT_RAG_RERANKER_MODEL", "BAAI/bge-reranker-base"),
        "source_catalog": os.getenv("REQUIREMENT_CORPUS_PATH"),
    }
    manifest_path = Path(os.getenv("REQUIREMENT_RAG_INDEX_MANIFEST", ".data/rag_index_manifest.json"))
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    catalog = RAGCatalogStore(os.getenv("RAG_CATALOG_DB_PATH", ".data/rag_catalog.sqlite3"))
    try:
        manifest["catalog_index_id"] = catalog.publish_index(manifest)
    finally:
        catalog.close()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
