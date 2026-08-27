import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from app.rag.adapters import ChannelScore, HashedDenseEncoder, MilvusHybridSearchAdapter, RetrievalScope
from app.rag.evaluation import hit_rate_at_k, ndcg_at_k, recall_at_k, reciprocal_rank
from app.rag.requirements.corpus import load_requirement_corpus
from app.rag.requirements.hybrid import HybridRequirementRetriever
from app.rag.requirements.runtime import (
    get_requirement_retriever,
    reset_requirement_retriever_cache,
)


def trace(retriever):
    return retriever.trace(
        product="宅抵贷",
        channel="DIRECT",
        case_date=date(2026, 8, 15),
        person_roles=["BORROWER", "MORTGAGOR", "SPOUSE", "BUSINESS_OPERATOR"],
        query="根据人员角色生成进件材料清单",
        top_k=20,
    )


class RequirementRagTests(unittest.TestCase):
    def test_local_trace_exposes_every_grounding_stage_and_score(self):
        retriever = HybridRequirementRetriever(load_requirement_corpus())
        result = trace(retriever)

        self.assertEqual(
            [stage["stage"] for stage in result["pipeline"]],
            [
                "QUERY_REWRITE",
                "METADATA_FILTER",
                "DENSE_BM25_RETRIEVAL",
                "RRF",
                "CROSS_ENCODER_RERANK",
                "REQUIREMENT_GROUNDING",
            ],
        )
        selected = {item["requirement_id"]: item for item in result["selected"]}
        self.assertIn("REQ-SPOUSE-CONSENT", selected)
        self.assertGreater(selected["REQ-SPOUSE-CONSENT"]["rrf_score"], 0)
        self.assertIsNotNone(selected["REQ-SPOUSE-CONSENT"]["rerank_score"])
        self.assertEqual(selected["REQ-SPOUSE-CONSENT"]["evidence_id"], "REQ-EV-REQ-SPOUSE-CONSENT")
        self.assertEqual(selected["REQ-SPOUSE-CONSENT"]["child_chunk_id"], "CHILD-REQ-SPOUSE-CONSENT")

    def test_applicability_filters_product_version_and_effective_date(self):
        result = trace(HybridRequirementRetriever(load_requirement_corpus()))
        candidates = {item["requirement_id"]: item for item in result["candidates"]}

        self.assertIn("VERSION_INACTIVE", candidates["REQ-OLD-SPOUSE-CONSENT"]["filter_reasons"])
        self.assertIn("EXPIRED", candidates["REQ-OLD-SPOUSE-CONSENT"]["filter_reasons"])
        self.assertIn("PRODUCT_MISMATCH", candidates["REQ-CONSUMER-LOAN-ID"]["filter_reasons"])
        self.assertFalse(candidates["REQ-CONSUMER-LOAN-ID"]["selected"])

    def test_problem_scope_is_filtered_before_retrieval(self):
        result = HybridRequirementRetriever(load_requirement_corpus()).trace(
            product="宅抵贷",
            channel="DIRECT",
            case_date=date(2026, 8, 15),
            person_roles=["SPOUSE"],
            query="缺少配偶同意抵押声明，召回补件依据",
            required_requirement_ids=["REQ-SPOUSE-CONSENT"],
            top_k=1,
        )
        self.assertEqual(result["final_requirements"], ["REQ-SPOUSE-CONSENT"])
        out_of_scope = next(item for item in result["candidates"] if item["requirement_id"] == "REQ-SPOUSE-ID")
        self.assertIn("PROBLEM_SCOPE_MISMATCH", out_of_scope["filter_reasons"])

    def test_corpus_rejects_duplicate_requirement_ids(self):
        record = Path(
            Path(__file__).resolve().parents[1]
            / "app/rag/requirements/data/requirements.jsonl"
        ).read_text(encoding="utf-8").splitlines()[0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requirements.jsonl"
            path.write_text(f"{record}\n{record}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate requirement_id"):
                load_requirement_corpus(path)

    def test_runtime_selects_milvus_bge_m3_and_cross_encoder_without_connecting(self):
        reset_requirement_retriever_cache()
        with patch.dict(os.environ, {
            "REQUIREMENT_RAG_BACKEND": "milvus",
            "REQUIREMENT_RAG_MILVUS_URI": "http://milvus.local:19530",
            "REQUIREMENT_RAG_MILVUS_COLLECTION": "material_atomic_requirements",
        }, clear=False):
            retriever = get_requirement_retriever()
            self.assertEqual(retriever.channel_retriever.backend_name, "MILVUS_BGE_M3_BM25")
            self.assertEqual(retriever.channel_retriever.id_field, "requirement_id")
            self.assertEqual(retriever.channel_retriever.dense_encoder.model_name, "BAAI/bge-m3")
            self.assertEqual(retriever.reranker.model_name, "BAAI/bge-reranker-base")
        reset_requirement_retriever_cache()

    def test_offline_metric_contracts(self):
        ranked = ["REQ-A", "REQ-B", "REQ-C"]
        relevant = {"REQ-B": 2.0, "REQ-C": 1.0}
        self.assertEqual(hit_rate_at_k(ranked, relevant, 2), 1.0)
        self.assertEqual(reciprocal_rank(ranked, relevant), .5)
        self.assertEqual(recall_at_k(ranked, relevant, 2), .5)
        self.assertGreater(ndcg_at_k(ranked, relevant, 3), 0)
        self.assertLessEqual(ndcg_at_k(ranked, relevant, 3), 1)

    def test_milvus_enforces_scope_filter_in_both_dense_and_bm25_searches(self):
        calls = []

        class FakeClient:
            def search(self, **kwargs):
                calls.append(kwargs)
                return [[]]

        adapter = MilvusHybridSearchAdapter(
            uri="http://milvus.local:19530",
            collection_name="requirements",
            dense_encoder=HashedDenseEncoder(),
        )
        adapter._client_instance = FakeClient()
        documents = load_requirement_corpus()[:2]
        scope = RetrievalScope(
            allowed_document_ids=tuple(item.requirement_id for item in documents),
            metadata={"product": "宅抵贷"},
        )
        adapter.score("身份证明", documents, metadata_filter=scope)

        self.assertEqual(len(calls), 2)
        self.assertTrue(all("filter" in item for item in calls))
        self.assertTrue(all("REQ-BORROWER-ID" in item["filter"] for item in calls))
        self.assertEqual(calls[0]["search_params"]["metric_type"], "COSINE")
        self.assertEqual(calls[1]["search_params"]["metric_type"], "BM25")

    def test_milvus_client_loads_persisted_collection_before_search(self):
        lifecycle_calls = []

        class FakeMilvusClient:
            def __init__(self, **kwargs):
                lifecycle_calls.append(("connect", kwargs))

            def load_collection(self, **kwargs):
                lifecycle_calls.append(("load", kwargs))

        adapter = MilvusHybridSearchAdapter(
            uri=".data/test.milvus.db",
            collection_name="material_atomic_requirements",
            dense_encoder=HashedDenseEncoder(),
        )
        with patch("pymilvus.MilvusClient", FakeMilvusClient):
            first = adapter._client()
            second = adapter._client()

        self.assertIs(first, second)
        self.assertEqual(
            lifecycle_calls,
            [
                ("connect", {"uri": ".data/test.milvus.db"}),
                ("load", {"collection_name": "material_atomic_requirements"}),
            ],
        )

    def test_no_hit_channel_has_no_rank_and_contributes_nothing_to_rrf(self):
        records = [item for item in load_requirement_corpus() if item.product == "宅抵贷" and item.status == "ACTIVE"][:2]

        class SparseHitsOnlyFirst:
            backend_name = "TEST_CHANNELS"

            def score(self, _query, documents, *, metadata_filter=None):
                del metadata_filter
                return {
                    documents[0].requirement_id: ChannelScore(.8, 1, 0.0, None),
                    documents[1].requirement_id: ChannelScore(0.0, None, 0.0, None),
                }

        result = HybridRequirementRetriever(records, channel_retriever=SparseHitsOnlyFirst()).trace(
            product="宅抵贷",
            channel="DIRECT",
            case_date=date(2026, 8, 15),
            person_roles=list({item.person_role for item in records}),
            query="精确词",
            top_k=2,
        )
        candidates = {item["requirement_id"]: item for item in result["candidates"]}
        self.assertIsNone(candidates[records[1].requirement_id]["bm25_rank"])
        self.assertEqual(candidates[records[1].requirement_id]["rrf_score"], 0.0)
        self.assertNotIn(records[1].requirement_id, result["final_requirements"])


if __name__ == "__main__":
    unittest.main()
