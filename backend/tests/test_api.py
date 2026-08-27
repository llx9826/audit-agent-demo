import json
import unittest

from fastapi.testclient import TestClient

from app.main import create_app
from app.persistence.repository import InMemoryCaseRepository
from app.runtime.checkpoint import memory_checkpointer
from app.runtime.run_manager import RunManager
from app.service import AuditService
from demo.providers import build_demo_knowledge_service, build_demo_pipeline_dependencies


def sse_data(body: str) -> list[dict]:
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


class ApiContractTests(unittest.TestCase):
    def setUp(self):
        self.service = AuditService(
            InMemoryCaseRepository(),
            checkpointer=memory_checkpointer(),
            pipeline_dependencies=build_demo_pipeline_dependencies(),
        )
        self.runs = RunManager(self.service)
        self.client_context = TestClient(create_app(
            self.service,
            self.runs,
            knowledge_service_override=build_demo_knowledge_service(),
        ))
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)

    def test_health_declares_material_completeness_scope(self):
        payload = self.client.get("/health").json()
        self.assertEqual(payload["scope"], "MATERIAL_COMPLETENESS_ONLY")
        self.assertIn("requirement_rule_engine", payload["capabilities"])
        self.assertIn("requirement_evidence_rag", payload["capabilities"])

    def test_architecture_contract_exposes_two_deciders_and_shared_recovery(self):
        payload = self.client.get("/api/architecture").json()
        boundaries = payload["agent_boundaries"]

        self.assertEqual(boundaries["case_association_agent"]["write_authority"], "ASSOCIATION_GATE")
        self.assertEqual(boundaries["material_audit_agent"]["write_authority"], "WORKFLOW_PLAN_GATE")
        self.assertEqual(boundaries["exception_recovery"]["kind"], "SHARED_SUB_AGENT")
        self.assertEqual(
            boundaries["exception_recovery"]["callers"],
            ["ASSOCIATION_GATE", "MATERIAL_MATCHER", "PLAN_GATE"],
        )
        self.assertTrue(boundaries["case_association_agent"]["prompt_version"])
        self.assertTrue(boundaries["material_audit_agent"]["prompt_version"])
        graph_nodes = set(payload["graph"]["runtime"]["stages"])
        self.assertIn("exception_recovery_agent", graph_nodes)
        self.assertIn("exception_result_gate", graph_nodes)

    def test_knowledge_query_exposes_intent_metadata_and_hybrid_trace(self):
        response = self.client.post("/api/knowledge/queries", json={
            "question": "南京公积金贷款离婚需要什么婚姻证明？有依据吗？",
        })
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["applied_filters"]["region"], "南京")
        self.assertTrue(payload["citations"])
        self.assertEqual(payload["trace"]["pipeline"][0]["stage"], "INTENT_ROUTE")
        self.assertIn("METADATA_FILTER", [item["stage"] for item in payload["trace"]["pipeline"]])

    def test_knowledge_run_streams_real_stage_events_and_result(self):
        run = self.client.post("/api/knowledge/runs", json={
            "question": "南京公积金贷款离婚需要什么婚姻证明？有依据吗？",
        }).json()

        with self.client.stream("GET", run["stream_url"]) as response:
            body = "".join(response.iter_text())
        events = sse_data(body)
        stages = [
            item["payload"]["stage"]
            for item in events
            if item["event_type"] == "KNOWLEDGE_STAGE_COMPLETED"
        ]
        completed = self.client.get(f"/api/knowledge/runs/{run['run_id']}").json()

        self.assertEqual(response.status_code, 200)
        self.assertIn("event: knowledge_event", body)
        self.assertEqual(completed["status"], "COMPLETED")
        self.assertEqual(completed["result"]["status"], "ANSWERED")
        for required in (
            "CACHE_LOOKUP", "INTENT_ROUTE", "QUERY_REWRITE", "METADATA_FILTER",
            "DENSE_BM25_RETRIEVAL", "RRF", "CROSS_ENCODER_RERANK",
            "PARENT_CONTEXT_EXPANSION", "GROUNDED_ANSWER_LLM", "CITATION_VALIDATION",
        ):
            self.assertIn(required, stages)

    def test_knowledge_runtime_failure_returns_actionable_structured_503(self):
        class FailingKnowledgeService:
            def query(self, _question):
                raise RuntimeError("provider details must not cross the API boundary")

        self.client.app.state.knowledge_service = FailingKnowledgeService()
        response = self.client.post("/api/knowledge/queries", json={"question": "北京需要什么材料？"})

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "KNOWLEDGE_RUNTIME_UNAVAILABLE")
        self.assertTrue(response.json()["detail"]["retryable"])
        self.assertNotIn("provider details", response.text)

    def test_demo_case_exposes_216_page_assets_and_six_domains(self):
        response = self.client.post("/api/demo/cases/material_completeness")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["pages"]), 216)
        self.assertEqual(payload["business_fields"]["material_manifest"]["domain_count"], 6)
        self.assertTrue(all(not person["confirmed"] for person in payload["persons"]))

    def test_background_run_streams_persisted_standard_sse_before_pause(self):
        state = self.client.post("/api/demo/cases/material_completeness").json()
        run = self.client.post(f"/api/cases/{state['case_id']}/runs").json()
        with self.client.stream("GET", run["stream_url"]) as response:
            body = "".join(response.iter_text())
        events = sse_data(body)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        self.assertIn("event: audit_event", body)
        self.assertTrue(all(f"id: {event['seq']}" in body for event in events))
        self.assertIn("AGENT_TOOL_FINISHED", {event["event_type"] for event in events})
        self.assertEqual(self.client.get(f"/api/runs/{run['run_id']}").json()["status"], "PAUSED")

    def test_last_event_id_resumes_without_duplicate_events(self):
        state = self.client.post("/api/demo/cases/material_completeness").json()
        run = self.client.post(f"/api/cases/{state['case_id']}/runs").json()
        with self.client.stream("GET", run["stream_url"]) as response:
            events = sse_data("".join(response.iter_text()))
        cursor = events[-3]["seq"]
        with self.client.stream("GET", run["stream_url"], headers={"Last-Event-ID": str(cursor)}) as response:
            resumed = sse_data("".join(response.iter_text()))
        self.assertTrue(resumed)
        self.assertTrue(all(event["seq"] > cursor for event in resumed))

    def test_resume_command_is_pydantic_validated(self):
        state = self.client.post("/api/demo/cases/material_completeness").json()
        self.client.post(f"/api/cases/{state['case_id']}/run")
        response = self.client.post(f"/api/cases/{state['case_id']}/resume-runs", json={
            "event_id": "BAD", "action": "CONFIRM_OWNER", "task_id": "TASK-UNKNOWN",
        })
        self.assertEqual(response.status_code, 422)

    def test_resume_run_keeps_same_langgraph_thread(self):
        state = self.client.post("/api/demo/cases/material_completeness").json()
        paused = self.client.post(f"/api/cases/{state['case_id']}/run").json()
        request = paused["pending_human_request"]
        run = self.client.post(f"/api/cases/{state['case_id']}/resume-runs", json={
            "event_id": "H-API-1", "action": "CONFIRM_OWNER", "task_id": request["task_id"],
            "page_id": "PAGE-021", "person_id": request["person_id"],
        }).json()
        with self.client.stream("GET", run["stream_url"]) as response:
            events = sse_data("".join(response.iter_text()))
        resumed = self.client.get(f"/api/cases/{state['case_id']}/state").json()
        self.assertEqual(resumed["thread_id"], state["thread_id"])
        self.assertEqual(resumed["pending_human_request"]["action"], "REQUEST_SUPPLEMENT")
        self.assertIn("SELECTIVE_REPLAN_APPLIED", {event["event_type"] for event in events})

    def test_feedback_endpoint_projects_candidate_impression_and_human_label(self):
        state = self.client.post("/api/demo/cases/material_completeness").json()
        paused = self.client.post(f"/api/cases/{state['case_id']}/run").json()
        request = paused["pending_human_request"]
        selected = next(
            item for item in request["candidate_options"]
            if "PAGE-021" in item["page_ids"]
            and item["proposed_person_id"] == request["person_id"]
        )

        response = self.client.post(f"/api/cases/{state['case_id']}/resume", json={
            "event_id": "H-FEEDBACK-1",
            "action": "CONFIRM_OWNER",
            "task_id": request["task_id"],
            "page_id": "PAGE-021",
            "person_id": request["person_id"],
            "material_type": request["material_type"],
            "selected_candidate_id": selected["candidate_id"],
            "reason_code": "HUMAN_CONFIRMED_OWNER",
            "operator_id": "reviewer-test",
        })
        feedback = self.client.get(f"/api/cases/{state['case_id']}/feedback").json()

        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(feedback["candidate_impressions"]), 1)
        self.assertEqual(feedback["human_feedback"][0]["selected_candidate_id"], selected["candidate_id"])
        self.assertEqual(feedback["human_feedback"][0]["operator_id"], "reviewer-test")
        self.assertEqual(feedback["hard_cases"][0]["meta"]["source"], "human_confirmed_event")

    def test_real_profile_hides_demo_routes_and_accepts_typed_case(self):
        service = AuditService(
            InMemoryCaseRepository(),
            checkpointer=memory_checkpointer(),
            pipeline_dependencies=build_demo_pipeline_dependencies(),
        )
        runs = RunManager(service)
        with TestClient(create_app(
            service,
            runs,
            profile="real",
            knowledge_service_override=build_demo_knowledge_service(),
        )) as client:
            self.assertEqual(client.post("/api/demo/cases/material_completeness").status_code, 404)
            response = client.post("/api/cases", json={
                "case_id": "CASE-REAL-001",
                "product_type": "宅抵贷",
                "channel": "DIRECT",
                "case_date": "2026-08-15",
                "persons": [{"person_id": "P01", "name": "脱敏姓名", "roles": ["BORROWER"]}],
                "pages": [{
                    "page_id": "PAGE-001", "bundle_id": "B01", "page_number": 1,
                    "domain": "身份与主体证明", "status": "PROCESSING",
                }],
            })
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["business_fields"]["material_manifest"]["image_count"], 1)


if __name__ == "__main__":
    unittest.main()
