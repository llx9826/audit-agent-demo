"""Scoped natural-language access to the material-requirement corpus."""
from __future__ import annotations

from datetime import date
from copy import deepcopy
import json
from pathlib import Path
from threading import Lock
from typing import Any, Callable

from ..prompting import PromptRegistry
from ..rag.requirements.hybrid import HybridRequirementRetriever
from ..rag.online import OnlineRequirementRag, OnlineRetrievalRequest
from ..rag.requirements.runtime import get_requirement_retriever
from ..rag.requirements.store import SQLiteRequirementStore, get_requirement_store
from ..rag.cache import NullRagCache, RagCache, make_cache_key
from .contracts import (
    KnowledgeAnswerAdapter,
    KnowledgeCitationContext,
    KnowledgeIntentAdapter,
    KnowledgeIntentDecision,
)
from .taxonomy import resolve_material_domain


class KnowledgeService:
    """Intent → retrieve → ground → cite/refuse; this service never mutates a Case."""

    def __init__(
        self,
        *,
        intent_adapter: KnowledgeIntentAdapter,
        answer_adapter: KnowledgeAnswerAdapter,
        retriever: HybridRequirementRetriever | None = None,
        store: SQLiteRequirementStore | None = None,
        suggested_questions: list[str] | None = None,
        prompt_registry: PromptRegistry | None = None,
        query_rewriter: Any | None = None,
        cache: RagCache | None = None,
        cache_ttl_seconds: int = 900,
        cache_version: str = "requirements-v2-zh-bm25",
        cache_model_signature: str = "unconfigured-model",
    ) -> None:
        self.intent_adapter = intent_adapter
        self.answer_adapter = answer_adapter
        self.retriever = retriever or get_requirement_retriever()
        self.online_rag = OnlineRequirementRag(self.retriever, query_rewriter=query_rewriter)
        self.store = store or get_requirement_store()
        self.suggested_questions = list(suggested_questions or [])
        self.prompt_registry = prompt_registry or PromptRegistry()
        self.cache = cache or NullRagCache()
        self.cache_ttl_seconds = cache_ttl_seconds
        self.cache_version = cache_version
        # 模型或路由切换时必须自动失效旧答案，不依赖人工清缓存。
        self.cache_model_signature = cache_model_signature
        self._cache_locks: dict[str, Lock] = {}
        self._cache_locks_guard = Lock()

    def _lock_for(self, key: str) -> Lock:
        """同一问题并发查询只允许一个请求执行下游 LLM/Retrieval。"""

        with self._cache_locks_guard:
            return self._cache_locks.setdefault(key, Lock())

    @staticmethod
    def _without_cache_trace(result: dict[str, Any]) -> dict[str, Any]:
        clean = deepcopy(result)
        trace = clean.get("trace") or {}
        trace.pop("cache", None)
        trace["pipeline"] = [
            stage for stage in trace.get("pipeline", [])
            if stage.get("stage") not in {"CACHE_LOOKUP", "CACHE_HIT", "CACHE_WRITE_VERIFIED", "CACHE_WRITE_FAILED"}
        ]
        return clean

    @staticmethod
    def _decorate_cache_hit(result: dict[str, Any], read: Any) -> dict[str, Any]:
        projected = KnowledgeService._without_cache_trace(result)
        projected["trace"]["cache"] = {
            "status": "HIT", "backend": read.backend, "key_digest": read.key_digest,
        }
        # 意图是知识查询的顶层合同；命中时展示缓存中的已校验意图投影。
        projected["trace"]["pipeline"].insert(1, {
            "stage": "CACHE_HIT", "backend": read.backend, "key_digest": read.key_digest,
            "execution_mode": "REUSED_VALIDATED_RESULT",
        })
        return projected

    def query(
        self,
        question: str,
        *,
        stage_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """精确 Key 的 cache-aside；不使用近似问题答案缓存。"""

        def emit(stage: str, payload: dict[str, Any]) -> None:
            if stage_callback is not None:
                stage_callback(stage, payload)

        normalized_question = " ".join(question.split())
        key = make_cache_key("rag:knowledge:answer:v1", {
            "question": normalized_question,
            "index_version": self.cache_version,
            # Prompt 合同变化必须切断旧答案缓存；本版本收紧了意图 reason_code。
            "prompt_family": "knowledge_intent+rewrite+grounding:v2",
            "model_signature": self.cache_model_signature,
        })
        try:
            first_read = self.cache.get(key)
        except Exception:
            first_read = None
        if first_read and first_read.hit and first_read.value:
            emit("CACHE_LOOKUP", {
                "status": "HIT", "backend": first_read.backend, "key_digest": first_read.key_digest,
            })
            emit("CACHE_HIT", {
                "backend": first_read.backend, "key_digest": first_read.key_digest,
                "execution_mode": "REUSED_VALIDATED_RESULT",
            })
            return self._decorate_cache_hit(first_read.value, first_read)

        with self._lock_for(key):
            try:
                second_read = self.cache.get(key)
            except Exception:
                second_read = None
            if second_read and second_read.hit and second_read.value:
                emit("CACHE_LOOKUP", {
                    "status": "HIT", "backend": second_read.backend, "key_digest": second_read.key_digest,
                })
                emit("CACHE_HIT", {
                    "backend": second_read.backend, "key_digest": second_read.key_digest,
                    "execution_mode": "REUSED_VALIDATED_RESULT",
                })
                return self._decorate_cache_hit(second_read.value, second_read)

            backend = (second_read or first_read).backend if (second_read or first_read) else self.cache.backend_name
            key_digest = (second_read or first_read).key_digest if (second_read or first_read) else key.rsplit(":", 1)[-1][:16]
            emit("CACHE_LOOKUP", {
                "status": "MISS", "backend": backend, "key_digest": key_digest,
                "reason": (second_read or first_read).reason if (second_read or first_read) else "CACHE_READ_FAILED",
            })
            result = self._query_uncached(question, stage_callback=stage_callback)
            trace = result.get("trace") or {}
            trace["cache"] = {
                "status": "MISS",
                "backend": backend,
                "key_digest": key_digest,
                "reason": (second_read or first_read).reason if (second_read or first_read) else "CACHE_READ_FAILED",
            }
            trace.setdefault("pipeline", []).insert(1, {
                "stage": "CACHE_LOOKUP", "status": "MISS", "backend": backend, "key_digest": key_digest,
            })
            cacheable = (
                result.get("status") == "ANSWERED"
                and (result.get("citation_validation") or {}).get("status") == "ANSWERED"
            )
            if cacheable:
                try:
                    receipt = self.cache.set(
                        key,
                        self._without_cache_trace(result),
                        ttl_seconds=self.cache_ttl_seconds,
                    )
                    stage = "CACHE_WRITE_VERIFIED" if receipt.stored and receipt.verified else "CACHE_WRITE_FAILED"
                    trace["pipeline"].append({
                        "stage": stage,
                        "backend": receipt.backend,
                        "key_digest": receipt.key_digest,
                        "stored": receipt.stored,
                        "verified": receipt.verified,
                        "reason": receipt.reason,
                    })
                    trace["cache"].update({
                        "write_stored": receipt.stored,
                        "write_verified": receipt.verified,
                    })
                    emit(stage, {
                        "backend": receipt.backend,
                        "key_digest": receipt.key_digest,
                        "stored": receipt.stored,
                        "verified": receipt.verified,
                        "reason": receipt.reason,
                    })
                except Exception:
                    trace["pipeline"].append({
                        "stage": "CACHE_WRITE_FAILED", "backend": self.cache.backend_name,
                        "key_digest": key_digest, "stored": False, "verified": False,
                    })
                    trace["cache"].update({"write_stored": False, "write_verified": False})
                    emit("CACHE_WRITE_FAILED", {
                        "backend": self.cache.backend_name,
                        "key_digest": key_digest,
                        "stored": False,
                        "verified": False,
                    })
            return result

    @staticmethod
    def _empty_trace(
        intent: KnowledgeIntentDecision,
        reason: str,
        *,
        intent_model_trace: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "trace_type": "KNOWLEDGE_QUERY_RAG",
            "reason": reason,
            "original_query": None,
            "rewritten_query": None,
            "retrieval": {
                "strategy": "NOT_RUN",
                "channel_backend": None,
                "reranker": None,
                "candidate_count": 0,
                "eligible_count": 0,
            },
            "pipeline": [{
                "stage": "INTENT_ROUTE",
                "route": intent.route,
                "reason_code": intent.reason_code,
                "model_trace": intent_model_trace,
            }],
            "candidates": [],
            "selected": [],
            "final_requirements": [],
        }

    @staticmethod
    def _intent_payload(decision: KnowledgeIntentDecision) -> dict[str, Any]:
        return decision.model_dump(mode="json")

    @staticmethod
    def _route_message(intent: KnowledgeIntentDecision) -> str:
        """拒答边界由代码持有，避免模型把原始越界问题原样当作安全答复。"""

        if intent.reason_code == "OUT_OF_SCOPE":
            return "该知识库只回答进件材料要求，不判断贷款审批、额度、利率、估值或风险。"
        if intent.reason_code == "UNSAFE_OR_UNSUPPORTED":
            return "知识库不能帮助伪造材料、绕过审核或规避材料要求。"
        return intent.user_message

    @staticmethod
    def _combine_scoped_traces(scoped: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
        """Keep comparison retrievals isolated, then expose one UI-friendly trace."""

        selected_by_id: dict[str, dict[str, Any]] = {}
        candidates_by_id: dict[str, dict[str, Any]] = {}
        for scope, item in scoped:
            for candidate in item["candidates"]:
                candidate_copy = dict(candidate)
                candidate_copy["retrieval_scope"] = scope
                previous = candidates_by_id.get(candidate["requirement_id"])
                if previous is None or (candidate["eligible"] and not previous["eligible"]):
                    candidates_by_id[candidate["requirement_id"]] = candidate_copy
            for selected in item["selected"]:
                selected_copy = dict(selected)
                selected_copy["retrieval_scope"] = scope
                selected_by_id[selected["requirement_id"]] = selected_copy
        first = scoped[0][1]
        selected = list(selected_by_id.values())
        selected.sort(key=lambda value: (str(value.get("retrieval_scope")), int(value.get("rerank_rank") or 999)))
        return {
            "original_query": first["original_query"],
            "rewritten_query": first["rewritten_query"],
            "retrieval": {
                "strategy": "INDEPENDENT_SCOPE_HYBRID_RETRIEVAL",
                "channel_backend": first["retrieval"]["channel_backend"],
                "reranker": first["retrieval"]["reranker"],
                "candidate_count": len(candidates_by_id),
                "eligible_count": sum(1 for item in candidates_by_id.values() if item["eligible"]),
            },
            "pipeline": [
                {"stage": "QUERY_REWRITE", "output": first["rewritten_query"]},
                {"stage": "METADATA_FILTER", "scopes": [scope for scope, _item in scoped]},
                {"stage": "INDEPENDENT_SCOPE_RETRIEVAL", "scope_count": len(scoped)},
                {"stage": "DENSE_BM25_RETRIEVAL", "candidate_count": len(candidates_by_id)},
                {"stage": "RRF", "candidate_count": len(selected)},
                {"stage": "CROSS_ENCODER_RERANK", "candidate_count": len(selected)},
                {"stage": "REQUIREMENT_GROUNDING", "requirement_ids": list(selected_by_id)},
            ],
            "candidates": list(candidates_by_id.values()),
            "selected": selected,
            "final_requirements": list(selected_by_id),
            "scoped_traces": [
                {"scope": scope, "trace": item} for scope, item in scoped
            ],
        }

    def _query_uncached(
        self,
        question: str,
        *,
        stage_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        def emit(stage: str, payload: dict[str, Any]) -> None:
            if stage_callback is not None:
                stage_callback(stage, payload)

        intent_prompt = self.prompt_registry.render_knowledge_intent(question)
        intent = self.intent_adapter.classify_knowledge(
            prompt=intent_prompt,
            question=question,
        )
        intent_model_trace = getattr(self.intent_adapter, "last_trace", None)
        emit("INTENT_ROUTE", {
            "route": intent.route,
            "primary_intent": intent.primary_intent,
            "answer_modes": list(intent.answer_modes),
            "query_modes": list(intent.query_modes),
            "confidence": intent.confidence,
            "reason_code": intent.reason_code,
            "model_trace": intent_model_trace,
        })
        if intent.route != "ACCEPT":
            return {
                "question": question,
                "status": intent.route,
                "intent": self._intent_payload(intent),
                "applied_filters": {},
                "answer": self._route_message(intent),
                "citations": [],
                "citation_validation": {
                    "status": "NOT_APPLICABLE",
                    "cited_chunk_ids": [],
                },
                "trace": self._empty_trace(
                    intent,
                    intent.reason_code,
                    intent_model_trace=intent_model_trace,
                ),
            }

        entities = intent.entities
        if not entities.product:
            raise ValueError("accepted knowledge intent must contain a product")
        compare = "REGION_COMPARISON" in intent.query_modes
        metadata_filters: dict[str, Any] = {}
        if len(entities.regions) == 1 and not compare:
            metadata_filters["region"] = entities.regions[0]
        resolved_branches = self.store.resolve_metadata_branches(
            regions=entities.regions,
            aliases=entities.branches,
        )
        if len(resolved_branches) == 1 and not compare:
            metadata_filters["branch"] = resolved_branches[0]
        elif len(entities.branches) == 1 and not compare:
            # 未能链接到 Catalog 时继续保留严格过滤，避免误用其他分行制度。
            metadata_filters["branch"] = entities.branches[0]
        domain_resolution = resolve_material_domain(
            domain_family=entities.material_domain_code,
            material_domain=entities.material_domain,
            material_type=entities.material_type,
        )
        if domain_resolution:
            metadata_filters["domain"] = list(domain_resolution.metadata_values)
        applied_filters: dict[str, Any] = {
            "product": entities.product,
            "channel": "PUBLIC",
            "person_roles": entities.person_roles or ["APPLICANT"],
            **metadata_filters,
        }
        if domain_resolution:
            applied_filters["domain_family"] = domain_resolution.family
            applied_filters["domain_resolution"] = domain_resolution.source
        if resolved_branches:
            applied_filters["branch_resolution"] = "CATALOG_ENTITY_LINK"
        query_date = date.fromisoformat(entities.case_date) if entities.case_date else date.today()
        if compare and len(entities.regions) > 1:
            scoped = []
            for region in entities.regions:
                def scoped_emit(stage: str, payload: dict[str, Any], *, scope: str = region) -> None:
                    emit(stage, {"scope": scope, **payload})

                scoped.append((region, self.online_rag.retrieve(OnlineRetrievalRequest(
                    product=entities.product,
                    channel="PUBLIC",
                    case_date=query_date,
                    person_roles=entities.person_roles or ["APPLICANT"],
                    query=question,
                    top_k=3,
                    metadata_filters={
                        "region": region,
                        **({"domain": list(domain_resolution.metadata_values)} if domain_resolution else {}),
                    },
                ), stage_callback=scoped_emit)))
            trace = self._combine_scoped_traces(scoped)
            applied_filters["regions"] = entities.regions
        else:
            trace = self.online_rag.retrieve(OnlineRetrievalRequest(
                product=entities.product,
                channel="PUBLIC",
                case_date=query_date,
                person_roles=entities.person_roles or ["APPLICANT"],
                query=question,
                top_k=6,
                metadata_filters=metadata_filters,
            ), stage_callback=stage_callback)
        trace["trace_type"] = "KNOWLEDGE_QUERY_RAG"
        trace["intent"] = self._intent_payload(intent)
        trace["pipeline"].insert(0, {
            "stage": "INTENT_ROUTE",
            "route": intent.route,
            "primary_intent": intent.primary_intent,
            "model_trace": intent_model_trace,
        })
        trace["pipeline"].insert(-1, {
            "stage": "PARENT_CONTEXT_EXPANSION",
            "parent_chunk_ids": list(dict.fromkeys(
                str(item.get("metadata", {}).get("parent_chunk_id"))
                for item in trace["selected"]
                if item.get("metadata", {}).get("parent_chunk_id")
            )),
        })
        emit("PARENT_CONTEXT_EXPANSION", {
            "parent_chunk_ids": list(dict.fromkeys(
                str(item.get("metadata", {}).get("parent_chunk_id"))
                for item in trace["selected"]
                if item.get("metadata", {}).get("parent_chunk_id")
            )),
        })

        contexts = [KnowledgeCitationContext(
            child_chunk_id=item["child_chunk_id"],
            parent_chunk_id=item.get("metadata", {}).get("parent_chunk_id"),
            title=item["title"],
            atomic_requirement=item["atomic_requirement"],
            parent_text=item.get("metadata", {}).get("parent_text"),
            source_document=item["source_document"],
            source_section=item["source_section"],
            source_url=item.get("metadata", {}).get("source_url"),
            region=item.get("metadata", {}).get("region"),
            branch=item.get("metadata", {}).get("branch"),
        ) for item in trace["selected"]]

        if not contexts:
            answer_status = "INSUFFICIENT_EVIDENCE"
            answer_text = "当前适用范围内没有找到足够的可引用依据，请补充地区、产品或人员状态。"
            cited_ids: list[str] = []
            citation_format_repaired = False
            emit("GROUNDED_ANSWER_LLM", {
                "status": "SKIPPED_NO_CONTEXT", "execution_mode": "NOT_CALLED",
            })
        else:
            answer_prompt = self.prompt_registry.render_knowledge_grounding(question, contexts)
            grounded = self.answer_adapter.answer_knowledge(
                prompt=answer_prompt,
                question=question,
                citations=contexts,
            )
            grounding_model_trace = getattr(self.answer_adapter, "last_trace", None)
            available_ids = {item.child_chunk_id for item in contexts}
            cited_ids = list(dict.fromkeys(grounded.cited_chunk_ids))
            citation_ids_valid = (
                grounded.status == "ANSWERED"
                and bool(cited_ids)
                and set(cited_ids).issubset(available_ids)
            )
            citation_format_repaired = False
            if citation_ids_valid:
                answer_status = "ANSWERED"
                answer_text = grounded.answer
                missing_inline_ids = [
                    chunk_id for chunk_id in cited_ids
                    if f"[{chunk_id}]" not in answer_text
                ]
                if missing_inline_ids:
                    # Structured Output 已给出合法引用，只修复展示格式；绝不补造 ID。
                    answer_text = f"{answer_text.rstrip()} {' '.join(f'[{item}]' for item in missing_inline_ids)}"
                    citation_format_repaired = True
            else:
                answer_status = "INSUFFICIENT_EVIDENCE"
                answer_text = "检索结果未形成可验证的引用闭环，本次不生成材料要求结论。"
                cited_ids = []

            trace["pipeline"].append({
                "stage": "GROUNDED_ANSWER_LLM",
                "status": grounded.status,
                "citation_format_repaired": citation_format_repaired,
                "model_trace": grounding_model_trace,
            })
            emit("GROUNDED_ANSWER_LLM", {
                "status": grounded.status,
                "citation_format_repaired": citation_format_repaired,
                "model_trace": grounding_model_trace,
            })

        # 一个语义 Chunk 可以承载多个 Atomic Requirement。引用 ID 仍指向 Chunk，
        # 但展示时必须保留 Cross-Encoder 排名最高的原子要求，不能被后续同 ID 覆盖。
        by_chunk: dict[str, dict[str, Any]] = {}
        for selected_item in trace["selected"]:
            by_chunk.setdefault(selected_item["child_chunk_id"], selected_item)
        citations = [{
            "requirement_id": item["requirement_id"],
            "child_chunk_id": item["child_chunk_id"],
            "title": item["title"],
            "source_document": item["source_document"],
            "source_section": item["source_section"],
            "source_url": item.get("metadata", {}).get("source_url"),
            "region": item.get("metadata", {}).get("region"),
            "atomic_requirement": item["atomic_requirement"],
            "parent_chunk_id": item.get("metadata", {}).get("parent_chunk_id"),
            "parent_title": item.get("metadata", {}).get("parent_title"),
            "parent_text": item.get("metadata", {}).get("parent_text"),
        } for chunk_id in cited_ids if (item := by_chunk.get(chunk_id)) is not None]
        trace["pipeline"].append({
            "stage": "CITATION_VALIDATION",
            "status": answer_status,
            "cited_chunk_ids": cited_ids,
            "format_repaired": citation_format_repaired,
        })
        emit("CITATION_VALIDATION", {
            "status": answer_status,
            "cited_chunk_ids": cited_ids,
            "format_repaired": citation_format_repaired,
        })
        return {
            "question": question,
            "status": answer_status,
            "intent": self._intent_payload(intent),
            "applied_filters": applied_filters,
            "answer": answer_text,
            "citations": citations,
            "citation_validation": {
                "status": answer_status,
                "cited_chunk_ids": cited_ids,
                "format_repaired": citation_format_repaired,
            },
            "trace": trace,
        }

    def build_report(self) -> dict[str, Any]:
        stats = self.store.stats()
        manifest_path = Path(__file__).resolve().parents[1] / "rag/offline/data/build_manifest.json"
        offline_build = (
            json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_path.exists()
            else None
        )
        project_root = Path(__file__).resolve().parents[3]
        catalog_manifest_path = project_root / ".data/catalog_link_manifest.json"
        index_manifest_path = project_root / ".data/rag_index_manifest.json"
        catalog_link = (
            json.loads(catalog_manifest_path.read_text(encoding="utf-8"))
            if catalog_manifest_path.exists()
            else None
        )
        online_index = (
            json.loads(index_manifest_path.read_text(encoding="utf-8"))
            if index_manifest_path.exists()
            else None
        )
        return {
            **stats,
            "source_type": "OFFICIAL_PUBLIC_SNAPSHOTS_WITH_INTERNAL_REQUIREMENT_CATALOG",
            "chunking": "DOCUMENT > PARENT_SECTION > SEMANTIC_UNIT > OVERSIZED_TOKEN_FALLBACK",
            "indexing": "SQLite metadata + Milvus BGE-M3 Dense/BM25 + Cross-Encoder",
            "offline_build": offline_build,
            "catalog_link": catalog_link,
            "online_index": online_index,
            "stages": [
                "来源登记与版本快照",
                "文本层优先解析",
                "格式归一与正文范围选择",
                "Parent Section 结构切分",
                "条款、清单项与完整句群语义切分",
                "超长语义单元 Token overlap 兜底",
                "短条款检索上下文与同义词增强",
                "Metadata 入库",
                "Dense/BM25 索引",
            ],
            "supported_intents": [
                "MATERIAL_REQUIREMENT",
                "SOURCE_TRACE",
            ],
            "query_modes": [
                "LOOKUP", "APPLICABILITY", "WAIVER_OR_SUBSTITUTE",
                "REGION_COMPARISON", "SUPPLEMENT",
            ],
            "suggested_questions": self.suggested_questions,
            "cache": {
                "backend": self.cache.backend_name,
                "ttl_seconds": self.cache_ttl_seconds,
                "version": self.cache_version,
                "mode": "EXACT_SCOPED_KEY",
            },
        }
