"""Milvus collection lifecycle and batch indexing for atomic requirements."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Sequence

from ..adapters import DenseEncoder
from .models import AtomicRequirementRecord


@dataclass(frozen=True, slots=True)
class MilvusIndexConfig:
    collection_name: str = "material_atomic_requirements"
    dense_dimension: int = 1024
    dense_metric: str = "COSINE"
    hnsw_m: int = 16
    hnsw_ef_construction: int = 200
    batch_size: int = 128
    bm25_tokenizer: str = "jieba"
    index_version: str = "requirements-v2-zh-bm25"


class MilvusRequirementIndexer:
    """Own schema/index creation and idempotent batch upsert for one collection."""

    def __init__(
        self,
        *,
        client: Any,
        encoder: DenseEncoder,
        config: MilvusIndexConfig | None = None,
    ) -> None:
        self.client = client
        self.encoder = encoder
        self.config = config or MilvusIndexConfig()

    def ensure_collection(self) -> None:
        if self.client.has_collection(collection_name=self.config.collection_name):
            return
        try:
            from pymilvus import DataType, Function, FunctionType
        except ImportError as exc:  # pragma: no cover - production integration guard
            raise RuntimeError("Milvus schema creation requires pymilvus") from exc

        schema = self.client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("requirement_id", DataType.VARCHAR, is_primary=True, max_length=128)
        schema.add_field(
            "content",
            DataType.VARCHAR,
            max_length=8192,
            enable_analyzer=True,
            analyzer_params={"tokenizer": self.config.bm25_tokenizer},
        )
        schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=self.config.dense_dimension)
        schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
        for name in ("product", "channel", "person_role", "status", "region", "branch"):
            schema.add_field(name, DataType.VARCHAR, max_length=256)
        schema.add_field("effective_from", DataType.INT64)
        schema.add_field("effective_to", DataType.INT64)
        schema.add_function(Function(
            name="content_bm25",
            function_type=FunctionType.BM25,
            input_field_names=["content"],
            output_field_names=["sparse_vector"],
        ))
        index = self.client.prepare_index_params()
        index.add_index(
            field_name="dense_vector",
            index_type="HNSW",
            metric_type=self.config.dense_metric,
            params={"M": self.config.hnsw_m, "efConstruction": self.config.hnsw_ef_construction},
        )
        index.add_index(
            field_name="sparse_vector",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="BM25",
        )
        self.client.create_collection(
            collection_name=self.config.collection_name,
            schema=schema,
            index_params=index,
        )

    @staticmethod
    def _epoch(value: date | None, fallback: int) -> int:
        return int(value.strftime("%Y%m%d")) if value else fallback

    def _rows(self, records: Sequence[AtomicRequirementRecord]) -> list[dict[str, Any]]:
        texts = [record.retrieval_text for record in records]
        vectors = self.encoder.encode_documents(texts)
        return [{
            "requirement_id": record.requirement_id,
            "content": text,
            "dense_vector": vector,
            "product": record.product,
            "channel": record.channel,
            "person_role": record.person_role,
            "status": record.status,
            "region": str(record.metadata.get("region", "ALL")),
            "branch": str(record.metadata.get("branch", "ALL")),
            "effective_from": self._epoch(record.effective_from, 0),
            "effective_to": self._epoch(record.effective_to, 99991231),
        } for record, text, vector in zip(records, texts, vectors, strict=True)]

    def upsert(self, records: Sequence[AtomicRequirementRecord]) -> int:
        self.ensure_collection()
        written = 0
        for start in range(0, len(records), self.config.batch_size):
            batch = records[start:start + self.config.batch_size]
            if not batch:
                continue
            self.client.upsert(
                collection_name=self.config.collection_name,
                data=self._rows(batch),
            )
            written += len(batch)
        return written
