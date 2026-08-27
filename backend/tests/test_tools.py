import time
import unittest

from pydantic import BaseModel, ConfigDict

from app.agents.exception_recovery import ExceptionRecoveryAgent, ExceptionTask
from app.tools import (
    ToolAccessError,
    ToolCallRequest,
    ToolObservation,
    ToolRegistry,
    ToolRuntimeContext,
    ToolSpec,
    ToolVisibilityPolicy,
)
from app.tools.local import build_local_tool_registry
from app.tools.mcp import LazyMcpToolAdapter
from demo.providers import DemoExceptionDecisionAdapter, build_demo_tool_registry


class LookupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str


class ToolArchitectureTests(unittest.TestCase):
    def test_registry_validates_typed_arguments_and_provider_metadata(self):
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="case.lookup",
                version="1.0.0",
                description="Lookup a scoped case document.",
                provider_type="LOCAL",
                provider_name="case-service",
                supported_intents=["MATCH_CASE_MATERIAL"],
            ),
            lambda arguments, _runtime: ToolObservation(
                result=f"found:{arguments.document_id}",
                evidence_refs=["E-LOOKUP-01"],
            ),
            input_model=LookupInput,
        )
        call = ToolCallRequest(
            name="case.lookup",
            arguments={"document_id": "DOC-05"},
            task_id="T04",
            task_intent="MATCH_CASE_MATERIAL",
            allowed_tools=["case.lookup"],
        )

        observation = registry.invoke(call, ToolRuntimeContext(task_id="T04"))

        self.assertEqual(observation.result, "found:DOC-05")
        self.assertEqual(observation.provider_type, "LOCAL")
        self.assertEqual(observation.provider_name, "case-service")
        self.assertIn("document_id", registry.spec("case.lookup").input_schema["properties"])
        with self.assertRaises(ToolAccessError) as invalid:
            registry.invoke(call.model_copy(update={"arguments": {"unexpected": True}}), ToolRuntimeContext())
        self.assertEqual(invalid.exception.code, "TOOL_ARGUMENTS_INVALID")

    def test_visibility_exposes_only_tools_needed_by_current_task_intent(self):
        registry = build_local_tool_registry()
        policy = ToolVisibilityPolicy()

        material_tools = policy.visible_names(
            registry,
            task_intents=["EXCEPTION:MATERIAL_IMAGE_LOW_CONFIDENCE"],
        )
        exception_tools = policy.visible_names(
            registry,
            task_intents=["EXCEPTION:MATERIAL_IMAGE_LOW_CONFIDENCE"],
        )

        self.assertEqual(material_tools, ["ocr_retry", "vlm_extract", "document_search"])
        self.assertEqual(exception_tools, ["ocr_retry", "vlm_extract", "document_search"])
        self.assertEqual(
            [spec.provider_name for spec in registry.specs()],
            [
                "ocr-service", "vlm-service", "case-material-service",
                "case-material-service", "page-integrity-service", "document-service",
            ],
        )

    def test_exception_agent_uses_unified_local_registry_without_api_break(self):
        agent = ExceptionRecoveryAgent(
            max_steps=3,
            registry=build_demo_tool_registry(),
            model_adapter=DemoExceptionDecisionAdapter(),
        )
        result = agent.resolve(ExceptionTask(
            exception_type="MATERIAL_IMAGE_LOW_CONFIDENCE",
            source_task_id="TASK-BORROWER-ID-P01",
            problem="material image confidence is too low",
            evidence_refs=["E-PAGE-01"],
        ), vlm_value="OWNER-P01", trusted_document_value="OWNER-P01")

        self.assertEqual(result.status, "RESOLVED")
        self.assertEqual(result.allowed_tools, ["ocr_retry", "vlm_extract", "document_search"])
        self.assertTrue(all(action["provider_type"] == "LOCAL" for action in result.actions))
        self.assertTrue(all(action["provider_name"] == "demo-material-provider" for action in result.actions))

    def test_mcp_client_is_lazy_and_uses_same_registry_contract(self):
        calls = {"factory": 0, "tools": []}

        class FakeMcpClient:
            def call_tool(self, name, arguments):
                calls["tools"].append((name, arguments))
                return {"result": "registry-hit", "evidence_refs": ["E-MCP-01"]}

        def factory():
            calls["factory"] += 1
            return FakeMcpClient()

        registry = ToolRegistry()
        adapter = LazyMcpToolAdapter(server_name="credit-registry", client_factory=factory)
        adapter.register(
            registry,
            name="mcp.credit_registry.lookup",
            remote_name="lookup_material_index",
            description="Read a case material index from the shared registry.",
            supported_intents=["MATCH_CASE_MATERIAL"],
        )
        self.assertEqual(calls["factory"], 0)
        call = ToolCallRequest(
            name="mcp.credit_registry.lookup",
            arguments={"person_id": "P-001"},
            task_id="T06",
            task_intent="MATCH_CASE_MATERIAL",
            allowed_tools=["mcp.credit_registry.lookup"],
        )

        observation = registry.invoke(call, ToolRuntimeContext(task_id="T06"))

        self.assertEqual(calls["factory"], 1)
        self.assertEqual(calls["tools"], [("lookup_material_index", {"person_id": "P-001"})])
        self.assertEqual(observation.provider_type, "MCP")
        self.assertEqual(observation.provider_name, "credit-registry")
        self.assertEqual(observation.evidence_refs, ["E-MCP-01"])

    def test_mcp_registration_without_sdk_does_not_import_or_connect(self):
        registry = ToolRegistry()
        LazyMcpToolAdapter(server_name="optional-server").register(
            registry,
            name="mcp.optional.lookup",
            remote_name="lookup",
            description="Optional lookup.",
            supported_intents=["MATCH_CASE_MATERIAL"],
        )

        self.assertEqual(registry.names, ("mcp.optional.lookup",))

    def test_registry_retries_transient_failed_observation_then_succeeds(self):
        calls = {"count": 0}
        registry = ToolRegistry()

        def transient_handler(_arguments, _runtime):
            calls["count"] += 1
            if calls["count"] == 1:
                return ToolObservation(
                    status="FAILED",
                    result="temporary provider failure",
                    error_code="PROVIDER_BUSY",
                )
            return ToolObservation(result="recovered")

        registry.register(
            ToolSpec(
                name="transient.lookup",
                version="1.0.0",
                description="A provider that recovers after one transient failure.",
                provider_type="LOCAL",
                provider_name="transient-provider",
                supported_intents=["VERIFY_TRANSIENT"],
                timeout_ms=100,
                max_retries=1,
            ),
            transient_handler,
        )
        call = ToolCallRequest(
            name="transient.lookup",
            task_id="T-TRANSIENT",
            task_intent="VERIFY_TRANSIENT",
            allowed_tools=["transient.lookup"],
        )

        observation = registry.invoke(call, ToolRuntimeContext(task_id="T-TRANSIENT"))

        self.assertEqual(observation.status, "SUCCESS")
        self.assertEqual(observation.result, "recovered")
        self.assertEqual(calls["count"], 2)
        self.assertEqual(observation.metadata["attempt"], 2)
        self.assertEqual(observation.metadata["max_attempts"], 2)

    def test_registry_enforces_timeout_and_raises_after_bounded_retries(self):
        calls = {"count": 0}
        registry = ToolRegistry()

        def slow_handler(_arguments, _runtime):
            calls["count"] += 1
            time.sleep(.03)
            return ToolObservation(result="too late")

        registry.register(
            ToolSpec(
                name="slow.lookup",
                version="1.0.0",
                description="A provider that exceeds its execution deadline.",
                provider_type="LOCAL",
                provider_name="slow-provider",
                supported_intents=["VERIFY_TIMEOUT"],
                timeout_ms=5,
                max_retries=1,
            ),
            slow_handler,
        )
        call = ToolCallRequest(
            name="slow.lookup",
            task_id="T-TIMEOUT",
            task_intent="VERIFY_TIMEOUT",
            allowed_tools=["slow.lookup"],
        )

        with self.assertRaises(ToolAccessError) as exhausted:
            registry.invoke(call, ToolRuntimeContext(task_id="T-TIMEOUT"))

        self.assertEqual(exhausted.exception.code, "TOOL_TIMEOUT_EXHAUSTED")
        self.assertEqual(exhausted.exception.status, "TIMEOUT")
        self.assertEqual(exhausted.exception.attempts, 2)
        self.assertEqual(calls["count"], 2)

    def test_exception_agent_converts_exhausted_tool_failure_to_need_human(self):
        calls = {"count": 0}
        registry = ToolRegistry()

        def failed_handler(_arguments, _runtime):
            calls["count"] += 1
            raise RuntimeError("provider unavailable")

        registry.register(
            ToolSpec(
                name="ocr_retry",
                version="1.0.0",
                description="Retry OCR through a deliberately unavailable provider.",
                provider_type="LOCAL",
                provider_name="failing-ocr-provider",
                supported_intents=["EXCEPTION:MATERIAL_IMAGE_LOW_CONFIDENCE"],
                timeout_ms=100,
                max_retries=1,
            ),
            failed_handler,
        )
        agent = ExceptionRecoveryAgent(
            max_steps=3,
            registry=registry,
            allowed_tools=["ocr_retry"],
            model_adapter=DemoExceptionDecisionAdapter(),
        )

        result = agent.resolve(ExceptionTask(
            exception_type="MATERIAL_IMAGE_LOW_CONFIDENCE",
            source_task_id="TASK-FAILED-TOOL",
            problem="material image confidence is too low",
            evidence_refs=["E-PAGE-04"],
        ), vlm_value="OWNER-P01", trusted_document_value="OWNER-P01")

        self.assertEqual(result.status, "NEED_HUMAN")
        self.assertEqual(result.stop_reason, "TOOL_FAILED_EXHAUSTED")
        self.assertEqual(result.steps_used, 1)
        self.assertEqual(calls["count"], 2)
        self.assertEqual(result.actions[0]["error_code"], "TOOL_FAILED_EXHAUSTED")
        self.assertEqual(result.actions[0]["tool_status"], "FAILED")
        self.assertEqual(result.actions[0]["tool_attempts"], 2)


if __name__ == "__main__":
    unittest.main()
