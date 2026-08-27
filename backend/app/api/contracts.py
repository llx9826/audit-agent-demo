from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HumanResumeCommand(StrictCommand):
    """Typed command used to resume the durable LangGraph interrupt."""

    event_id: str = Field(min_length=1)
    action: Literal[
        "CONFIRM_ASSOCIATION",
        "RESOLVE_ASSOCIATION_EVIDENCE",
        "CONFIRM_OWNER",
        "REVIEW_IMAGE",
        "REQUEST_SUPPLEMENT",
        "SUPPLEMENT_RECEIVED",
    ]
    task_id: str = Field(min_length=1)
    page_id: str | None = None
    person_id: str | None = None
    person_name: str | None = None
    roles: list[str] = Field(default_factory=list)
    material_type: str | None = None
    page: dict[str, Any] | None = None
    selected_candidate_id: str | None = None
    selected_candidate_ids: list[str] = Field(default_factory=list)
    reason_code: str | None = None
    operator_id: str | None = None

    @model_validator(mode="after")
    def validate_action_payload(self) -> "HumanResumeCommand":
        if self.action == "CONFIRM_ASSOCIATION" and not self.selected_candidate_ids:
            raise ValueError("CONFIRM_ASSOCIATION requires selected_candidate_ids")
        if self.action == "RESOLVE_ASSOCIATION_EVIDENCE":
            if not self.page_id or not self.person_id or not self.person_name or not self.roles:
                raise ValueError(
                    "RESOLVE_ASSOCIATION_EVIDENCE requires page_id, person_id, person_name and roles"
                )
        if self.action in {"CONFIRM_OWNER", "REVIEW_IMAGE"} and not self.page_id:
            raise ValueError(f"{self.action} requires page_id")
        if self.action == "SUPPLEMENT_RECEIVED" and not self.page:
            raise ValueError("SUPPLEMENT_RECEIVED requires page")
        return self


# Stable import name retained for HTTP clients during the API migration.
SupplementReceived = HumanResumeCommand


class PersonInput(StrictCommand):
    person_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    roles: list[str] = Field(min_length=1)
    confirmed: bool = False
    source: str = "CASE_INPUT"


class PageInput(StrictCommand):
    page_id: str = Field(min_length=1)
    bundle_id: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    domain: str = Field(min_length=1)
    material_type: str | None = None
    owner_person_id: str | None = None
    status: str = "PROCESSING"
    thumbnail_url: str | None = None
    preview_url: str | None = None
    extracted_fields: dict[str, Any] = Field(default_factory=dict)
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence_refs: list[str] = Field(default_factory=list)


class CaseCreateCommand(StrictCommand):
    case_id: str = Field(min_length=1)
    thread_id: str | None = None
    product_type: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    case_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    # 只作旧上游兼容 Seed；真实人员/角色由页级证据关联链确认。
    persons: list[PersonInput] = Field(default_factory=list)
    pages: list[PageInput] = Field(min_length=1)
