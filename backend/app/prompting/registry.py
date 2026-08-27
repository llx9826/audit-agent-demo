"""Load immutable, versioned prompt assets and render typed Agent context."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from ..agents.contracts import (
    ExceptionDecisionContext,
    MaterialAuditAssignment,
    PromptMetadata,
    RenderedPrompt,
)
from ..agents.case_association.contracts import CaseAssociationAssignment

if TYPE_CHECKING:
    from ..knowledge.contracts import KnowledgeCitationContext


CURRENT_PROMPT_VERSIONS = {
    "case_association": "v3",
    "exception_next_action": "v1",
    "knowledge_grounding": "v1",
    "knowledge_intent": "v1",
    "material_audit": "v4",
    "query_rewrite": "v1",
}


class _PromptManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_id: str
    version: str
    system_file: str


class PromptRegistry:
    """Filesystem prompt registry with content hashes for traceability."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else Path(__file__).resolve().parents[1] / "prompts"
        self.validate_current_assets()

    def validate_current_assets(self) -> None:
        """Fail during composition when a configured Prompt asset is incomplete."""

        for prompt_name, version in CURRENT_PROMPT_VERSIONS.items():
            directory = self.root / prompt_name / version
            manifest_path = directory / "prompt.json"
            manifest = _PromptManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
            system_path = directory / manifest.system_file
            if not system_path.is_file():
                raise FileNotFoundError(
                    f"Prompt system asset is missing: {system_path}"
                )

    def render_exception_next_action(
        self,
        context: ExceptionDecisionContext,
        *,
        version: str = CURRENT_PROMPT_VERSIONS["exception_next_action"],
    ) -> RenderedPrompt:
        directory = self.root / "exception_next_action" / version
        manifest_path = directory / "prompt.json"
        manifest = _PromptManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        system = (directory / manifest.system_file).read_text(encoding="utf-8").strip()
        digest = sha256(
            (manifest_path.read_bytes() + b"\0" + system.encode("utf-8"))
        ).hexdigest()
        user = json.dumps(
            context.model_dump(exclude={"decision_script"}),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        return RenderedPrompt(
            metadata=PromptMetadata(
                prompt_id=manifest.prompt_id,
                version=manifest.version,
                sha256=digest,
            ),
            system=system,
            user=user,
        )

    def render_material_audit(
        self,
        assignment: MaterialAuditAssignment,
        *,
        version: str = CURRENT_PROMPT_VERSIONS["material_audit"],
    ) -> RenderedPrompt:
        directory = self.root / "material_audit" / version
        manifest_path = directory / "prompt.json"
        manifest = _PromptManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        system = (directory / manifest.system_file).read_text(encoding="utf-8").strip()
        digest = sha256(
            (manifest_path.read_bytes() + b"\0" + system.encode("utf-8"))
        ).hexdigest()
        return RenderedPrompt(
            metadata=PromptMetadata(
                prompt_id=manifest.prompt_id,
                version=manifest.version,
                sha256=digest,
            ),
            system=system,
            user=json.dumps(assignment.model_dump(), ensure_ascii=False, sort_keys=True, indent=2),
        )

    def render_case_association(
        self,
        assignment: CaseAssociationAssignment,
        *,
        version: str = CURRENT_PROMPT_VERSIONS["case_association"],
    ) -> RenderedPrompt:
        """渲染最小关联合同，不携带原始影像或证件原文。"""

        return self._render_payload(
            "case_association",
            version,
            assignment.model_dump(mode="json"),
        )

    def _render_payload(self, prompt_name: str, version: str, payload: dict) -> RenderedPrompt:
        directory = self.root / prompt_name / version
        manifest_path = directory / "prompt.json"
        manifest = _PromptManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        system = (directory / manifest.system_file).read_text(encoding="utf-8").strip()
        digest = sha256(manifest_path.read_bytes() + b"\0" + system.encode("utf-8")).hexdigest()
        return RenderedPrompt(
            metadata=PromptMetadata(
                prompt_id=manifest.prompt_id,
                version=manifest.version,
                sha256=digest,
            ),
            system=system,
            user=json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
        )

    def render_knowledge_intent(
        self,
        question: str,
        *,
        version: str = CURRENT_PROMPT_VERSIONS["knowledge_intent"],
    ) -> RenderedPrompt:
        return self._render_payload("knowledge_intent", version, {"question": question})

    def render_knowledge_grounding(
        self,
        question: str,
        citations: list[KnowledgeCitationContext],
        *,
        version: str = CURRENT_PROMPT_VERSIONS["knowledge_grounding"],
    ) -> RenderedPrompt:
        return self._render_payload(
            "knowledge_grounding",
            version,
            {
                "question": question,
                "retrieved_chunks": [item.model_dump(mode="json") for item in citations],
            },
        )

    def render_query_rewrite(
        self,
        *,
        question: str,
        entities: dict,
        version: str = CURRENT_PROMPT_VERSIONS["query_rewrite"],
    ) -> RenderedPrompt:
        return self._render_payload(
            "query_rewrite",
            version,
            {"question": question, "verified_entities": entities},
        )
