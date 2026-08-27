from __future__ import annotations

from dataclasses import asdict
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException

from ...domain.models import CaseState, PageAsset, PersonRole
from ...evaluation import project_feedback
from ...service import AuditService
from ..contracts import CaseCreateCommand, HumanResumeCommand
from ..dependencies import audit_service, case_or_404


router = APIRouter(prefix="/api/cases", tags=["cases"])


@router.post("")
def create_case(command: CaseCreateCommand, service: AuditService = Depends(audit_service)) -> dict:
    state = CaseState(
        case_id=command.case_id,
        thread_id=command.thread_id or f"THREAD-{uuid4().hex[:12].upper()}",
        persons=[
            PersonRole(**{
                **person.model_dump(),
                "confirmed": False,
                "source": "UPSTREAM_SEED",
            })
            for person in command.persons
        ],
        pages=[PageAsset(**page.model_dump()) for page in command.pages],
        business_fields={
            "product_type": command.product_type,
            "channel": command.channel,
            "case_date": command.case_date,
            "material_manifest": {
                "image_count": len(command.pages),
                "bundle_count": len({page.bundle_id for page in command.pages}),
                "domain_count": len({page.domain for page in command.pages}),
                "domains": [
                    {"name": domain, "count": sum(1 for page in command.pages if page.domain == domain)}
                    for domain in sorted({page.domain for page in command.pages})
                ],
            },
        },
        status="READY",
    )
    return service.create_case(state).to_dict()


@router.post("/{case_id}/run")
def run_case(case_id: str, service: AuditService = Depends(audit_service)) -> dict:
    case_or_404(service, case_id)
    return service.run(case_id).to_dict()


@router.get("/{case_id}")
@router.get("/{case_id}/state")
def get_case(case_id: str, service: AuditService = Depends(audit_service)) -> dict:
    return case_or_404(service, case_id).to_dict()


@router.get("/{case_id}/plan")
def get_plan(case_id: str, service: AuditService = Depends(audit_service)) -> list[dict]:
    return [asdict(task) for task in case_or_404(service, case_id).audit_plan]


@router.get("/{case_id}/events")
def get_events(case_id: str, after: int = 0, service: AuditService = Depends(audit_service)) -> list[dict]:
    case_or_404(service, case_id)
    return service.repo.event_dicts(case_id, after=max(0, after))


@router.get("/{case_id}/feedback")
def get_feedback(case_id: str, service: AuditService = Depends(audit_service)) -> dict:
    """从 Event Log 重建候选曝光、人工标签和 Hard Case。"""

    case_or_404(service, case_id)
    return project_feedback(service.repo.event_dicts(case_id))


@router.get("/{case_id}/checkpoints")
def get_checkpoints(case_id: str, service: AuditService = Depends(audit_service)) -> list[str]:
    case_or_404(service, case_id)
    return list(service.repo.checkpoints.get(case_id, {}))


@router.post("/{case_id}/resume")
def resume_case(
    case_id: str,
    command: HumanResumeCommand,
    service: AuditService = Depends(audit_service),
) -> dict:
    case_or_404(service, case_id)
    try:
        return service.supplement(case_id, command.model_dump(exclude_none=True)).to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{case_id}/replay/{checkpoint_id}")
def replay(case_id: str, checkpoint_id: str, service: AuditService = Depends(audit_service)) -> dict:
    case_or_404(service, case_id)
    try:
        return service.replay(case_id, checkpoint_id).to_dict()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="checkpoint not found") from exc


@router.get("/{case_id}/evidence")
def evidence(case_id: str, service: AuditService = Depends(audit_service)) -> list[dict]:
    return [asdict(item) for item in case_or_404(service, case_id).evidence_ledger]


@router.get("/{case_id}/rag-trace")
def rag_trace(case_id: str, service: AuditService = Depends(audit_service)) -> dict:
    case_or_404(service, case_id)
    return service.rag_trace(case_id)
