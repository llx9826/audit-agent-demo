from __future__ import annotations

from fastapi import HTTPException, Request

from ..runtime.run_manager import RunManager
from ..service import AuditService


def audit_service(request: Request) -> AuditService:
    return request.app.state.audit_service


def run_manager(request: Request) -> RunManager:
    return request.app.state.run_manager


def case_or_404(service: AuditService, case_id: str):
    try:
        return service.repo.get(case_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="case not found") from exc
