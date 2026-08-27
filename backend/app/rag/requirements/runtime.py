from __future__ import annotations

from functools import lru_cache
import os

from ..adapters import (
    BGEM3DenseEncoder,
    MilvusHybridSearchAdapter,
    SentenceTransformersCrossEncoder,
)
from .hybrid import HybridRequirementRetriever
from .store import get_requirement_store


@lru_cache(maxsize=1)
def get_requirement_retriever() -> HybridRequirementRetriever:
    requirements = get_requirement_store().list_all()
    backend = os.getenv("REQUIREMENT_RAG_BACKEND", "milvus").strip().lower()
    if backend != "milvus":
        raise ValueError(
            "the application RAG path requires REQUIREMENT_RAG_BACKEND=milvus; "
            "dependency-free retrievers are test doubles only"
        )
    uri = os.getenv("REQUIREMENT_RAG_MILVUS_URI", "").strip()
    if not uri:
        raise ValueError("REQUIREMENT_RAG_MILVUS_URI is required for the Milvus backend")
    encoder = BGEM3DenseEncoder(
        os.getenv("REQUIREMENT_RAG_BGE_MODEL", "BAAI/bge-m3"),
        use_fp16=os.getenv("REQUIREMENT_RAG_USE_FP16", "true").lower() == "true",
        device=os.getenv("REQUIREMENT_RAG_MODEL_DEVICE") or None,
    )
    channels = MilvusHybridSearchAdapter(
        uri=uri,
        token=os.getenv("REQUIREMENT_RAG_MILVUS_TOKEN") or None,
        collection_name=os.getenv("REQUIREMENT_RAG_MILVUS_COLLECTION", "material_atomic_requirements"),
        dense_encoder=encoder,
        dense_field=os.getenv("REQUIREMENT_RAG_MILVUS_DENSE_FIELD", "dense_vector"),
        bm25_field=os.getenv("REQUIREMENT_RAG_MILVUS_BM25_FIELD", "sparse_vector"),
        id_field=os.getenv("REQUIREMENT_RAG_MILVUS_ID_FIELD", "requirement_id"),
        candidate_limit=int(os.getenv("REQUIREMENT_RAG_MILVUS_CANDIDATE_LIMIT", "100")),
    )
    reranker = SentenceTransformersCrossEncoder(
        os.getenv("REQUIREMENT_RAG_RERANKER_MODEL", "BAAI/bge-reranker-base"),
        device=os.getenv("REQUIREMENT_RAG_MODEL_DEVICE") or None,
    )
    return HybridRequirementRetriever(
        requirements,
        channel_retriever=channels,
        reranker=reranker,
        rrf_k=int(os.getenv("REQUIREMENT_RAG_RRF_K", "60")),
    )


def reset_requirement_retriever_cache() -> None:
    get_requirement_retriever.cache_clear()
