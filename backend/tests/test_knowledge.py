import unittest
from datetime import date
from threading import Event

from demo.providers import build_demo_knowledge_service
from pydantic import ValidationError

from app.knowledge.adapters import GatewayQueryRewriter, KnowledgeModelRouteError
from app.knowledge.contracts import GroundedAnswerDecision, KnowledgeIntentDecision
from app.knowledge.run_manager import KnowledgeRunManager
from app.knowledge.taxonomy import resolve_material_domain
from app.prompting import PromptRegistry
from app.rag.online import OnlineRetrievalRequest


class FreeTextDomainIntentAdapter:
    """模拟真实模型返回用户词汇，而不是索引标签。"""

    def __init__(self, delegate):
        self.delegate = delegate

    def classify_knowledge(self, *, prompt, question):
        decision = self.delegate.classify_knowledge(prompt=prompt, question=question)
        entities = decision.entities.model_copy(update={
            "material_domain_code": None,
            "material_domain": "婚姻证明",
            "material_type": "婚姻电子证照",
        })
        return decision.model_copy(update={"entities": entities})


class ShortBranchIntentAdapter:
    """模拟真实模型提取银行简称，而不是索引中的分行标准值。"""

    def __init__(self, delegate):
        self.delegate = delegate

    def classify_knowledge(self, *, prompt, question):
        decision = self.delegate.classify_knowledge(prompt=prompt, question=question)
        entities = decision.entities.model_copy(update={"branches": ["建行"]})
        return decision.model_copy(update={"entities": entities})


class MissingInlineCitationAnswerAdapter:
    """模拟模型在结构化字段返回合法 ID、但正文漏写方括号的格式错误。"""

    def answer_knowledge(self, *, prompt, question, citations):
        del prompt, question
        return GroundedAnswerDecision(
            status="ANSWERED",
            answer="可以依据当前检索条款办理。",
            cited_chunk_ids=[citations[0].child_chunk_id],
        )


class FailingKnowledgeService:
    def __init__(self, trace=None):
        self.trace = trace or {
            "route": "knowledge_intent",
            "selected_endpoint": None,
            "attempts": [{
                "endpoint": "primary",
                "provider": "test",
                "model": "test-model",
                "status": "SCHEMA_ERROR",
                "error_code": "STRUCTURED_OUTPUT_INVALID",
            }],
        }

    def query(self, question, *, stage_callback=None):
        del question, stage_callback
        raise KnowledgeModelRouteError(self.trace["route"], self.trace)


class BlockingKnowledgeService:
    def __init__(self):
        self.started = Event()
        self.release = Event()

    def query(self, question, *, stage_callback=None):
        del question, stage_callback
        self.started.set()
        self.release.wait(timeout=1)
        return {"status": "ANSWERED", "citations": []}


class UnavailableRewriteAdapter:
    def rewrite_knowledge_query(self, *, prompt):
        del prompt
        raise KnowledgeModelRouteError("query_rewrite", {
            "route": "query_rewrite",
            "selected_endpoint": None,
            "attempts": [{
                "endpoint": "primary",
                "provider": "test",
                "model": "test-model",
                "status": "TRANSIENT_ERROR",
                "error_code": "TRANSPORT_ERROR",
            }],
        })


class KnowledgeServiceTests(unittest.TestCase):
    def test_intent_prompt_and_schema_require_a_controlled_non_empty_reason_code(self):
        prompt = PromptRegistry().render_knowledge_intent("对比南京和北京的公积金贷款婚姻材料")
        self.assertEqual(prompt.metadata.version, "1.1.0")
        self.assertIn("reason_code 必须且只能输出一次", prompt.system)
        with self.assertRaises(ValidationError):
            KnowledgeIntentDecision(
                route="ACCEPT",
                primary_intent="MATERIAL_REQUIREMENT",
                answer_modes=["ANSWER_REQUIREMENT"],
                query_modes=["REGION_COMPARISON"],
                confidence=.9,
                reason_code=None,
                user_message="已识别。",
                router="test",
            )

    def test_async_run_persists_safe_model_failure_stage_and_trace(self):
        manager = KnowledgeRunManager(FailingKnowledgeService())
        try:
            run = manager.start("测试材料问题")
            manager._tasks[run.run_id].result(timeout=1)
            failed = manager.get(run.run_id)
            self.assertEqual(failed.status, "FAILED")
            self.assertEqual(failed.error_code, "KNOWLEDGE_MODEL_ROUTE_EXHAUSTED")
            self.assertEqual(failed.failed_stage, "knowledge_intent")
            self.assertNotIn("http", failed.error.lower())
            self.assertEqual(failed.model_trace["route"], "knowledge_intent")
        finally:
            manager.close()

    def test_mixed_transport_and_fallback_auth_failure_is_not_reported_as_primary_auth(self):
        manager = KnowledgeRunManager(FailingKnowledgeService({
            "route": "knowledge_grounding",
            "selected_endpoint": None,
            "attempts": [
                {"status": "TRANSIENT_ERROR", "error_code": "TRANSPORT_ERROR"},
                {"status": "PERMANENT_ERROR", "error_code": "HTTP_401"},
            ],
        }))
        try:
            run = manager.start("测试材料问题")
            manager._tasks[run.run_id].result(timeout=1)
            self.assertIn("暂时不可达", manager.get(run.run_id).error)
            self.assertIn("备用端点鉴权", manager.get(run.run_id).error)
        finally:
            manager.close()

    def test_active_identical_question_reuses_one_backend_run(self):
        service = BlockingKnowledgeService()
        manager = KnowledgeRunManager(service)
        try:
            first = manager.start("南京 公积金材料")
            self.assertTrue(service.started.wait(timeout=1))
            second = manager.start(" 南京   公积金材料 ")
            self.assertEqual(first.run_id, second.run_id)
            self.assertEqual(len(manager.runs), 1)
            service.release.set()
            manager._tasks[first.run_id].result(timeout=1)
        finally:
            service.release.set()
            manager.close()

    def test_query_rewrite_transport_failure_keeps_original_query(self):
        rewriter = GatewayQueryRewriter(UnavailableRewriteAdapter())
        request = OnlineRetrievalRequest(
            product="个人经营抵押贷款",
            channel="PUBLIC",
            case_date=date(2026, 8, 17),
            person_roles=["APPLICANT"],
            query="广西建行个人经营抵押贷款需要哪些抵押物材料？",
            metadata_filters={"region": "广西"},
        )

        rewritten = rewriter.rewrite(request)

        self.assertEqual(rewritten["query"], request.query)
        self.assertEqual(rewritten["strategy"], "MODEL_REWRITE_UNAVAILABLE_ORIGINAL_QUERY")
        self.assertTrue(rewritten["degraded"])
        self.assertEqual(rewritten["model_trace"]["route"], "query_rewrite")

    def test_domain_alias_is_normalized_before_metadata_filter(self):
        resolution = resolve_material_domain(
            domain_family=None,
            material_domain="婚姻证明",
            material_type="婚姻电子证照",
        )
        self.assertIsNotNone(resolution)
        self.assertEqual(resolution.family, "MARRIAGE_FAMILY")
        self.assertIn("婚姻与家庭关系", resolution.metadata_values)

    def test_beijing_free_text_domain_alias_keeps_correct_evidence(self):
        service = build_demo_knowledge_service()
        service.intent_adapter = FreeTextDomainIntentAdapter(service.intent_adapter)

        result = service.query("北京公积金贷款婚姻电子证照可以免交纸质件吗？")

        self.assertEqual(result["status"], "ANSWERED")
        self.assertEqual(result["applied_filters"]["region"], "北京")
        self.assertEqual(result["applied_filters"]["domain_family"], "MARRIAGE_FAMILY")
        self.assertIn(
            "KB-BJ-E-CERTIFICATE",
            {item["requirement_id"] for item in result["trace"]["selected"]},
        )
        self.assertTrue(result["citations"])
        self.assertEqual(result["citations"][0]["requirement_id"], "KB-BJ-E-CERTIFICATE")
        self.assertTrue(all(item["region"] == "北京" for item in result["citations"]))

    def test_valid_structured_citation_repairs_missing_inline_marker(self):
        service = build_demo_knowledge_service()
        service.answer_adapter = MissingInlineCitationAnswerAdapter()

        result = service.query("北京公积金贷款婚姻电子证照可以免交纸质件吗？")

        self.assertEqual(result["status"], "ANSWERED")
        self.assertTrue(result["citation_validation"]["format_repaired"])
        cited_id = result["citation_validation"]["cited_chunk_ids"][0]
        self.assertIn(f"[{cited_id}]", result["answer"])

    def test_nanjing_query_extracts_intent_filters_and_citations(self):
        result = build_demo_knowledge_service().query("南京公积金贷款离婚需要什么婚姻证明？有依据吗？")
        self.assertEqual(result["applied_filters"]["region"], "南京")
        self.assertIn("TRACE_SOURCE", result["intent"]["answer_modes"])
        self.assertEqual(result["intent"]["primary_intent"], "MATERIAL_REQUIREMENT")
        self.assertEqual(result["status"], "ANSWERED")
        self.assertTrue(result["citations"])
        self.assertTrue(all(item["region"] == "南京" for item in result["citations"]))
        self.assertTrue(all(item["source_url"] for item in result["citations"]))
        self.assertTrue(all(item["child_chunk_id"] in result["answer"] for item in result["citations"]))
        self.assertIn("PARENT_CONTEXT_EXPANSION", [item["stage"] for item in result["trace"]["pipeline"]])

    def test_build_report_exposes_real_sqlite_catalog(self):
        report = build_demo_knowledge_service().build_report()
        self.assertEqual(report["backend"], "SQLITE")
        self.assertGreaterEqual(report["record_count"], 77)
        self.assertIn("南京", report["regions"])
        self.assertEqual(len(report["supported_intents"]), 2)
        self.assertEqual(report["offline_build"]["source_count"], 12)
        self.assertEqual(report["offline_build"]["semantic_unit_count"], 187)
        self.assertEqual(report["offline_build"]["chunk_config"]["overlap_tokens"], 48)

    def test_operating_mortgage_query_uses_region_and_branch_filters(self):
        result = build_demo_knowledge_service().query(
            "广西建行个人经营抵押贷款需要哪些抵押物材料？"
        )

        self.assertEqual(result["status"], "ANSWERED")
        self.assertEqual(result["applied_filters"]["region"], "广西")
        self.assertEqual(result["applied_filters"]["branch"], "广西区分行")
        self.assertTrue(result["citations"])
        self.assertTrue(all(item["region"] == "广西" for item in result["citations"]))

    def test_bank_abbreviation_links_to_catalog_branch_before_strict_filter(self):
        service = build_demo_knowledge_service()
        service.intent_adapter = ShortBranchIntentAdapter(service.intent_adapter)

        result = service.query("广西建行个人经营抵押贷款需要哪些抵押物材料？")

        self.assertEqual(result["status"], "ANSWERED")
        self.assertEqual(result["applied_filters"]["region"], "广西")
        self.assertEqual(result["applied_filters"]["branch"], "广西区分行")
        self.assertEqual(result["applied_filters"]["branch_resolution"], "CATALOG_ENTITY_LINK")
        self.assertIn(
            "KB-CCB-GX-PROPERTY",
            {item["requirement_id"] for item in result["trace"]["selected"]},
        )

    def test_out_of_scope_and_unsafe_queries_refuse_before_retrieval(self):
        service = build_demo_knowledge_service()
        for question, reason in [
            ("帮我判断这笔贷款能批多少额度", "OUT_OF_SCOPE"),
            ("怎么伪造婚姻材料绕过审核", "UNSAFE_OR_UNSUPPORTED"),
        ]:
            result = service.query(question)
            self.assertEqual(result["status"], "REFUSE")
            self.assertEqual(result["intent"]["reason_code"], reason)
            self.assertEqual(result["trace"]["retrieval"]["strategy"], "NOT_RUN")
            self.assertFalse(result["citations"])

    def test_missing_product_clarifies_before_retrieval(self):
        result = build_demo_knowledge_service().query("南京离婚需要提交什么证明？")
        self.assertEqual(result["status"], "CLARIFY")
        self.assertEqual(result["intent"]["reason_code"], "PRODUCT_REQUIRED")
        self.assertEqual(result["trace"]["pipeline"][0]["stage"], "INTENT_ROUTE")

    def test_region_comparison_runs_one_filtered_trace_per_region(self):
        result = build_demo_knowledge_service().query(
            "对比南京、北京、广州的公积金贷款婚姻状况材料要求"
        )
        self.assertEqual(result["status"], "ANSWERED")
        self.assertEqual(len(result["trace"]["scoped_traces"]), 3)
        self.assertEqual(
            {item["scope"] for item in result["trace"]["scoped_traces"]},
            {"南京", "北京", "广州"},
        )
        for item in result["trace"]["scoped_traces"]:
            self.assertEqual(item["trace"]["pipeline"][1]["filters"]["region"], item["scope"])


if __name__ == "__main__":
    unittest.main()
