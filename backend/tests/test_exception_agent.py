import unittest
import json

from app.agents.contracts import AgentToolCall, CallToolDecision
from app.agents.exception_recovery import ExceptionRecoveryAgent, ExceptionTask
from app.providers.decision_adapters import QwenVllmAdapter
from demo.providers import DemoExceptionDecisionAdapter, build_demo_tool_registry


def task() -> ExceptionTask:
    return ExceptionTask(
        exception_type="MATERIAL_IMAGE_LOW_CONFIDENCE",
        source_task_id="TASK-BORROWER-ID-P01",
        problem="材料影像置信度不足，无法稳定匹配材料类型与所属人",
        evidence_refs=["E-PAGE-01"],
        context_refs=["PAGE-001", "REQ-BORROWER-ID"],
    )


def resolve(agent):
    return agent.resolve(
        task(),
        vlm_value="OWNER-P01",
        trusted_document_value="OWNER-P01",
    )


def demo_agent(max_steps=3, **kwargs):
    return ExceptionRecoveryAgent(
        max_steps=max_steps,
        registry=build_demo_tool_registry(),
        **kwargs,
    )


class ToolChoiceAdapter:
    def __init__(self, tool):
        self.tool = tool

    def decide(self, **_kwargs):
        return CallToolDecision(
            action="CALL_TOOL",
            tool_call=AgentToolCall(name=self.tool),
            reason_code="NEGATIVE_CONTRACT_TEST",
            rationale_summary="Exercise one bounded control-flow guard.",
        )


class ExceptionRecoveryAgentTests(unittest.TestCase):
    def test_versioned_prompt_is_rendered_for_every_observation_driven_decision(self):
        adapter = DemoExceptionDecisionAdapter()
        result = resolve(demo_agent(max_steps=3, model_adapter=adapter))

        self.assertEqual(result.status, "RESOLVED")
        self.assertEqual(len(adapter.invocations), 3)
        first = adapter.invocations[0]
        last = adapter.invocations[-1]
        self.assertEqual(first["prompt"].metadata.prompt_id, "exception.next_action")
        self.assertEqual(first["prompt"].metadata.version, "1.0.0")
        self.assertIn("MATERIAL_IMAGE_LOW_CONFIDENCE", first["prompt"].user)
        self.assertIn("allowed_tools", first["prompt"].user)
        self.assertIn("input_schema", first["prompt"].user)
        self.assertEqual(len(first["context"].observations), 0)
        self.assertEqual(len(last["context"].observations), 2)
        self.assertEqual(last["context"].observations[-1].tool, "vlm_extract")
        self.assertEqual(len(result.decision_trace), 3)
        self.assertTrue(all(item["prompt_sha256"] for item in result.decision_trace))

    def test_custom_model_adapter_changes_tool_order_from_current_observations(self):
        class ObservationAwareAdapter:
            def __init__(self):
                self.contexts = []

            def decide(self, *, prompt, context):
                self.contexts.append((prompt, context.model_copy(deep=True)))
                tool = "vlm_extract" if not context.observations else "document_search"
                return CallToolDecision(
                    action="CALL_TOOL",
                    tool_call=AgentToolCall(name=tool),
                    reason_code="OBSERVATION_AWARE_TEST",
                    rationale_summary="Select the missing independent evidence channel.",
                )

        adapter = ObservationAwareAdapter()
        result = resolve(demo_agent(max_steps=3, model_adapter=adapter))

        self.assertEqual(result.status, "RESOLVED")
        self.assertEqual([item["tool"] for item in result.actions], ["vlm_extract", "document_search"])
        self.assertEqual(len(adapter.contexts), 2)
        self.assertEqual(adapter.contexts[1][1].observations[0].tool, "vlm_extract")
        self.assertIn("vlm_extract", adapter.contexts[1][0].user)

    def test_qwen_vllm_adapter_uses_openai_compatible_structured_output(self):
        captured = {}

        def transport(http_request, timeout):
            captured["url"] = http_request.full_url
            captured["timeout"] = timeout
            captured["body"] = json.loads(http_request.data)
            return json.dumps({
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "action": "CALL_TOOL",
                            "tool_call": {"name": "vlm_extract", "arguments": {}},
                            "reason_code": "LOW_CONFIDENCE",
                            "rationale_summary": "Use VLM to obtain a stronger observation.",
                            "expected_state_delta": ["normalized_values"],
                        })
                    }
                }]
            }).encode()

        qwen = QwenVllmAdapter(
            base_url="http://qwen.local/v1",
            model="Qwen/Qwen3-8B",
            api_key="test-token",
            timeout=4.0,
            transport=transport,
        )
        seed = DemoExceptionDecisionAdapter()
        agent = demo_agent(model_adapter=seed)
        context = agent._decision_context({
            "task": task(),
            "max_steps": 3,
            "allowed_tools": ["vlm_extract"],
            "steps_used": 0,
            "actions": [],
            "evidence_refs": ["E-DOC-01"],
            "normalized_values": {},
        }, agent.registry.names)
        prompt = agent.prompt_registry.render_exception_next_action(context)
        decision = qwen.decide(prompt=prompt, context=context)

        self.assertEqual(decision.action, "CALL_TOOL")
        self.assertEqual(decision.tool_call.name, "vlm_extract")
        self.assertEqual(captured["url"], "http://qwen.local/v1/chat/completions")
        self.assertEqual(captured["timeout"], 4.0)
        self.assertEqual(captured["body"]["response_format"]["type"], "json_schema")
        self.assertEqual(captured["body"]["messages"][0]["role"], "system")

    def test_subgraph_emits_live_custom_events_for_decide_execute_and_evaluate(self):
        agent = demo_agent(max_steps=3, model_adapter=DemoExceptionDecisionAdapter())
        initial = {
            "task": task(),
            "max_steps": 3,
            "allowed_tools": list(agent.allowed_tools),
            "tool_context": {"vlm_value": "OWNER-P01", "trusted_document_value": "OWNER-P01"},
        }
        chunks = list(agent._graph.stream(initial, stream_mode=["updates", "custom"]))
        custom = [chunk for mode, chunk in chunks if mode == "custom"]
        event_types = [item["event_type"] for item in custom]

        self.assertEqual(event_types.count("EXCEPTION_CANDIDATES_BUILT"), 3)
        self.assertEqual(event_types.count("EXCEPTION_DECISION_MADE"), 3)
        self.assertEqual(event_types.count("EXCEPTION_TOOL_GATE_EVALUATED"), 3)
        self.assertEqual(event_types.count("TOOL_STARTED"), 3)
        self.assertEqual(event_types.count("EXCEPTION_TOOL_OBSERVED"), 3)
        self.assertEqual(event_types.count("COMPLETION_EVALUATED"), 3)
        self.assertEqual(custom[-1]["payload"]["status"], "RESOLVED")
        candidate_events = [
            item["payload"] for item in custom
            if item["event_type"] == "EXCEPTION_CANDIDATES_BUILT"
        ]
        self.assertIn("ocr_retry", candidate_events[0]["candidate_tools"])
        self.assertNotIn("ocr_retry", candidate_events[1]["candidate_tools"])
        self.assertEqual(candidate_events[1]["blocked_tools"]["ocr_retry"], "NO_STATE_CHANGE")
        self.assertNotIn("vlm_extract", candidate_events[2]["candidate_tools"])

    def test_registered_allowlisted_tool_loop_resolves_on_completion_condition(self):
        result = resolve(demo_agent(max_steps=3, model_adapter=DemoExceptionDecisionAdapter()))

        self.assertEqual(result.status, "RESOLVED")
        self.assertEqual(result.stop_reason, "COMPLETION_CONDITION_MET")
        self.assertEqual(result.steps_used, 3)
        self.assertEqual([item["tool"] for item in result.actions], [
            "ocr_retry", "vlm_extract", "document_search",
        ])
        self.assertTrue(all(item["allowed"] and item["registered"] and item["executed"] for item in result.actions))
        self.assertIn("E-VLM-01", result.evidence_refs)
        self.assertFalse(result.loop_guard_triggered)

    def test_step_budget_is_enforced_by_control_flow(self):
        result = resolve(demo_agent(max_steps=2, model_adapter=DemoExceptionDecisionAdapter()))

        self.assertEqual(result.status, "NEED_HUMAN")
        self.assertEqual(result.stop_reason, "BUDGET_EXHAUSTED")
        self.assertEqual(result.steps_used, 2)
        self.assertEqual(len(result.actions), 2)

    def test_repeated_action_without_state_change_triggers_loop_guard(self):
        result = resolve(demo_agent(
            max_steps=3,
            model_adapter=ToolChoiceAdapter("ocr_retry"),
        ))

        self.assertEqual(result.status, "NEED_HUMAN")
        self.assertEqual(result.stop_reason, "LOOP_GUARD")
        self.assertTrue(result.loop_guard_triggered)
        self.assertEqual(result.steps_used, 2)
        self.assertEqual(len(result.actions), 2)

    def test_disallowed_tool_is_blocked_before_execution(self):
        result = resolve(demo_agent(
            max_steps=3,
            allowed_tools=["ocr_retry"],
            model_adapter=ToolChoiceAdapter("vlm_extract"),
        ))

        self.assertEqual(result.status, "NEED_HUMAN")
        self.assertEqual(result.stop_reason, "TOOL_NOT_ALLOWED")
        self.assertFalse(result.actions[0]["executed"])
        self.assertFalse(result.actions[0]["allowed"])
        self.assertTrue(result.actions[0]["registered"])

    def test_allowlisted_but_unregistered_tool_is_blocked(self):
        result = resolve(demo_agent(
            max_steps=3,
            allowed_tools=["external_lookup"],
            model_adapter=ToolChoiceAdapter("external_lookup"),
        ))

        self.assertEqual(result.status, "NEED_HUMAN")
        self.assertEqual(result.stop_reason, "NO_CANDIDATE_TOOL")
        self.assertEqual(result.actions, [])


if __name__ == "__main__":
    unittest.main()
