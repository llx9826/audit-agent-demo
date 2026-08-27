from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol, Sequence

from .scoring import DOMAIN_TERMS, bm25_scores, cosine, hashed_vector, tokenize


class DenseEncoder(Protocol):
    model_name: str

    def encode_queries(self, texts: Sequence[str]) -> list[list[float]]: ...

    def encode_documents(self, texts: Sequence[str]) -> list[list[float]]: ...


class CrossEncoderReranker(Protocol):
    model_name: str

    def score(self, query: str, documents: Sequence[str]) -> list[float]: ...


@dataclass(frozen=True, slots=True)
class ChannelScore:
    dense_score: float
    dense_rank: int | None
    bm25_score: float
    bm25_rank: int | None


class HybridChannelRetriever(Protocol):
    backend_name: str

    def score(
        self,
        query: str,
        documents: Sequence[Any],
        *,
        metadata_filter: "RetrievalScope | None" = None,
    ) -> dict[str, ChannelScore]: ...


@dataclass(frozen=True, slots=True)
class RetrievalScope:
    """Scope compiled by applicability and enforced inside the search store."""

    allowed_document_ids: tuple[str, ...]
    metadata: dict[str, Any]


class HashedDenseEncoder:
    """Small local encoder used for reproducible tests and offline demos."""

    model_name = "local-feature-hashing-v1"

    def encode_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return [hashed_vector(tokenize(f"query: {text}")) for text in texts]

    def encode_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [hashed_vector(tokenize(f"passage: {text}")) for text in texts]

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        """Compatibility alias for explicit passage indexing callers."""
        return [hashed_vector(tokenize(text)) for text in texts]


def _ranks(scores: dict[str, float]) -> dict[str, int]:
    ordered = sorted(scores, key=lambda requirement_id: (-scores[requirement_id], requirement_id))
    return {requirement_id: index + 1 for index, requirement_id in enumerate(ordered)}


def _document_id(document: Any) -> str:
    """Return the stable identifier used by the atomic-requirement corpus."""

    identifier = getattr(document, "requirement_id", None)
    if identifier is None:
        raise TypeError("retrieval document must expose requirement_id")
    return str(identifier)


class LocalDenseBM25Channels:
    backend_name = "LOCAL_RUNTIME_DENSE_BM25"

    def __init__(self, dense_encoder: DenseEncoder | None = None) -> None:
        self.dense_encoder = dense_encoder or HashedDenseEncoder()

    def score(
        self,
        query: str,
        documents: Sequence[Any],
        *,
        metadata_filter: RetrievalScope | None = None,
    ) -> dict[str, ChannelScore]:
        del metadata_filter  # Local documents have already been scoped in-process.
        if not documents:
            return {}
        texts = [document.retrieval_text for document in documents]
        query_vector = self.dense_encoder.encode_queries([query])[0]
        document_vectors = self.dense_encoder.encode_documents(texts)
        dense = {
            _document_id(document): round(cosine(query_vector, vector), 6)
            for document, vector in zip(documents, document_vectors, strict=True)
        }
        bm25_values = bm25_scores(tokenize(query), [tokenize(text) for text in texts])
        sparse = {
            _document_id(document): round(score, 6)
            for document, score in zip(documents, bm25_values, strict=True)
        }
        # 0 分表示通道未命中，不得伪造 rank 参与 RRF。
        dense_rank = _ranks({key: score for key, score in dense.items() if score > 0})
        sparse_rank = _ranks({key: score for key, score in sparse.items() if score > 0})
        return {
            _document_id(document): ChannelScore(
                dense_score=dense[_document_id(document)],
                dense_rank=dense_rank.get(_document_id(document)),
                bm25_score=sparse[_document_id(document)],
                bm25_rank=sparse_rank.get(_document_id(document)),
            )
            for document in documents
        }


class LexicalCrossEncoderReranker:
    """Dependency-free pair scorer behind the production reranker interface.

    It scores a query/document pair jointly and is deliberately separate from
    both first-stage retrieval channels. Production swaps this adapter for a
    trained Cross-Encoder without changing the pipeline.
    """

    model_name = "local-pairwise-reranker-v1"

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        query_tokens = set(tokenize(query))
        domain_terms = {term for term in DOMAIN_TERMS if term in query}
        output: list[float] = []
        for document in documents:
            document_tokens = set(tokenize(document))
            coverage = len(query_tokens & document_tokens) / max(1, len(query_tokens))
            exact_domain = sum(1 for term in domain_terms if term in document) / max(1, len(domain_terms))
            output.append(round(0.72 * coverage + 0.28 * exact_domain, 6))
        return output


class BGEM3DenseEncoder:
    """Optional production BGE-M3 adapter; FlagEmbedding is imported lazily."""

    def __init__(
        self,
        model_name: str = "BAAI/bge-m3",
        *,
        use_fp16: bool = True,
        device: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.use_fp16 = use_fp16
        self.device = device
        self._model: Any | None = None

    def _load(self) -> Any:
        if self._model is None:
            try:
                from FlagEmbedding import BGEM3FlagModel
            except ImportError as exc:  # pragma: no cover - optional production dependency
                raise RuntimeError(
                    "BGEM3DenseEncoder requires the optional FlagEmbedding package"
                ) from exc
            kwargs: dict[str, Any] = {"use_fp16": self.use_fp16}
            if self.device:
                kwargs["devices"] = self.device
            self._model = BGEM3FlagModel(self.model_name, **kwargs)
        return self._model

    def _encode(self, texts: Sequence[str]) -> list[list[float]]:
        result = self._load().encode(
            list(texts),
            return_dense=True,
            return_sparse=False,
            return_colbert_vecs=False,
        )
        return [list(map(float, vector)) for vector in result["dense_vecs"]]

    def encode_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return self._encode([f"query: {text}" for text in texts])

    def encode_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._encode([f"passage: {text}" for text in texts])

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        """Compatibility alias; indexes passage representations."""
        return self.encode_documents(texts)


class SentenceTransformersCrossEncoder:
    """Optional production Cross-Encoder adapter with lazy model loading."""

    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        *,
        device: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self._model: Any | None = None

    def _load(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder
            except ImportError as exc:  # pragma: no cover - optional production dependency
                raise RuntimeError(
                    "SentenceTransformersCrossEncoder requires sentence-transformers"
                ) from exc
            self._model = CrossEncoder(self.model_name, device=self.device)
        return self._model

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        predictions = self._load().predict([(query, document) for document in documents])
        return [float(score) for score in predictions]


class CallableCrossEncoder:
    """Test/integration adapter for an existing reranking service client."""

    def __init__(self, scorer: Callable[[str, Sequence[str]], Sequence[float]], name: str = "callable") -> None:
        self._scorer = scorer
        self.model_name = name

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        return [float(score) for score in self._scorer(query, documents)]


class MilvusHybridSearchAdapter:
    """Optional Milvus dense + BM25 channel adapter.

    The collection is expected to contain the same ``requirement_id`` values as the
    external corpus, a dense vector field, and a sparse field produced by a
    Milvus BM25 function. Applicability remains auditable in the application
    layer, and the resulting scope is also enforced by Milvus before top-k.
    """

    backend_name = "MILVUS_BGE_M3_BM25"

    def __init__(
        self,
        *,
        uri: str,
        collection_name: str,
        dense_encoder: DenseEncoder,
        token: str | None = None,
        dense_field: str = "dense_vector",
        bm25_field: str = "sparse_vector",
        id_field: str = "requirement_id",
        candidate_limit: int = 100,
    ) -> None:
        self.uri = uri
        self.token = token
        self.collection_name = collection_name
        self.dense_encoder = dense_encoder
        self.dense_field = dense_field
        self.bm25_field = bm25_field
        self.id_field = id_field
        self.candidate_limit = candidate_limit
        self._client_instance: Any | None = None

    def _client(self) -> Any:
        if self._client_instance is None:
            try:
                from pymilvus import MilvusClient
            except ImportError as exc:  # pragma: no cover - optional production dependency
                raise RuntimeError("MilvusHybridSearchAdapter requires pymilvus") from exc
            kwargs = {"uri": self.uri}
            if self.token:
                kwargs["token"] = self.token
            client = MilvusClient(**kwargs)
            # Milvus Lite 会把已持久化的 Collection 以 released 状态重新打开。
            # 查询适配器拥有读侧生命周期，因此必须在第一次真实搜索前显式加载；
            # 不能依赖建库进程曾经 load，因为两个进程的 Collection 状态不共享。
            client.load_collection(collection_name=self.collection_name)
            self._client_instance = client
        return self._client_instance

    def _parse_hits(self, hits: Any) -> tuple[dict[str, float], dict[str, int]]:
        rows = hits[0] if hits and isinstance(hits[0], list) else hits
        scores: dict[str, float] = {}
        ranks: dict[str, int] = {}
        for rank, hit in enumerate(rows or [], start=1):
            entity = hit.get("entity", {}) if isinstance(hit, dict) else {}
            requirement_id = str(entity.get(self.id_field) or hit.get(self.id_field) or hit.get("id"))
            scores[requirement_id] = float(hit.get("distance", hit.get("score", 0.0)))
            ranks[requirement_id] = rank
        return scores, ranks

    @staticmethod
    def _escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def _filter_expression(self, scope: RetrievalScope | None) -> str:
        if scope is None or not scope.allowed_document_ids:
            return f'{self.id_field} == "__NO_ELIGIBLE_DOCUMENT__"'
        values = ", ".join(
            f'"{self._escape(value)}"' for value in scope.allowed_document_ids
        )
        clauses = [f"{self.id_field} in [{values}]"]
        metadata = scope.metadata
        if product := metadata.get("product"):
            clauses.append(f'product == "{self._escape(str(product))}"')
        if channel := metadata.get("channel"):
            value = self._escape(str(channel))
            clauses.append(f'(channel == "{value}" or channel == "ALL")')
        roles = metadata.get("person_roles")
        if roles:
            role_values = ", ".join(f'"{self._escape(str(role))}"' for role in roles)
            clauses.append(f"person_role in [{role_values}]")
        if case_date := metadata.get("case_date"):
            effective = int(str(case_date).replace("-", ""))
            clauses.extend([
                "status == \"ACTIVE\"",
                f"effective_from <= {effective}",
                f"effective_to >= {effective}",
            ])
        for field in ("region", "branch"):
            if expected := metadata.get(field):
                value = self._escape(str(expected))
                clauses.append(f'({field} == "{value}" or {field} == "ALL")')
        return " and ".join(clauses)

    def score(
        self,
        query: str,
        documents: Sequence[Any],
        *,
        metadata_filter: RetrievalScope | None = None,
    ) -> dict[str, ChannelScore]:
        if not documents:
            return {}
        filter_expression = self._filter_expression(metadata_filter)
        # Apple Silicon 上先初始化 PyTorch/BGE，再启动 Milvus Lite gRPC，
        # 避免两个 native runtime 同时初始化导致底层崩溃。
        dense_vector = self.dense_encoder.encode_queries([query])[0]
        client = self._client()
        dense_hits = client.search(
            collection_name=self.collection_name,
            data=[dense_vector],
            anns_field=self.dense_field,
            limit=self.candidate_limit,
            search_params={"metric_type": "COSINE", "params": {}},
            output_fields=[self.id_field],
            filter=filter_expression,
        )
        # Milvus BM25 Function collections accept raw query text for the sparse
        # output field. Index/schema construction remains deployment-owned.
        sparse_hits = client.search(
            collection_name=self.collection_name,
            data=[query],
            anns_field=self.bm25_field,
            limit=self.candidate_limit,
            search_params={"metric_type": "BM25", "params": {}},
            output_fields=[self.id_field],
            filter=filter_expression,
        )
        dense_scores, dense_ranks = self._parse_hits(dense_hits)
        sparse_scores, sparse_ranks = self._parse_hits(sparse_hits)
        return {
            _document_id(document): ChannelScore(
                dense_score=dense_scores.get(_document_id(document), 0.0),
                dense_rank=dense_ranks.get(_document_id(document)),
                bm25_score=sparse_scores.get(_document_id(document), 0.0),
                bm25_rank=sparse_ranks.get(_document_id(document)),
            )
            for document in documents
        }
