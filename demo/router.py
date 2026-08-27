"""FastAPI routes installed only when APP_PROFILE=demo."""
from __future__ import annotations

from html import escape

from fastapi import APIRouter, Depends, Response

from app.api.dependencies import audit_service
from app.service import AuditService

from .fixtures import create_demo_case


router = APIRouter(prefix="/api/demo", tags=["demo"])


@router.post("/cases/{scenario}")
def create_case(scenario: str, service: AuditService = Depends(audit_service)) -> dict:
    state = create_demo_case(scenario)
    return service.create_case(
        state,
        source="DEMO_FIXTURE",
        metadata={"scenario": scenario},
    ).to_dict()


def _svg(page_id: str, *, compact: bool) -> str:
    width, height = (120, 156) if compact else (720, 940)
    accent = ["#2563eb", "#0f766e", "#7c3aed", "#b45309"][sum(map(ord, page_id)) % 4]
    safe_id = escape(page_id)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 720 940">
      <rect width="720" height="940" rx="24" fill="#f8fafc"/>
      <rect x="48" y="44" width="624" height="852" rx="14" fill="white" stroke="#cbd5e1" stroke-width="4"/>
      <rect x="82" y="88" width="210" height="28" rx="8" fill="{accent}" opacity=".92"/>
      <text x="84" y="165" font-family="system-ui,sans-serif" font-size="30" font-weight="700" fill="#0f172a">{safe_id}</text>
      <rect x="84" y="205" width="236" height="168" rx="12" fill="#e2e8f0"/>
      <circle cx="202" cy="267" r="38" fill="#94a3b8"/>
      <path d="M132 352c12-58 128-58 140 0" fill="#94a3b8"/>
      <g fill="#cbd5e1"><rect x="352" y="216" width="264" height="18" rx="9"/><rect x="352" y="258" width="218" height="18" rx="9"/><rect x="352" y="300" width="246" height="18" rx="9"/><rect x="352" y="342" width="188" height="18" rx="9"/></g>
      <g fill="#e2e8f0"><rect x="84" y="426" width="532" height="16" rx="8"/><rect x="84" y="468" width="498" height="16" rx="8"/><rect x="84" y="510" width="524" height="16" rx="8"/><rect x="84" y="552" width="455" height="16" rx="8"/><rect x="84" y="638" width="532" height="16" rx="8"/><rect x="84" y="680" width="482" height="16" rx="8"/><rect x="84" y="722" width="516" height="16" rx="8"/></g>
      <rect x="450" y="772" width="166" height="68" rx="34" fill="none" stroke="{accent}" stroke-width="6" opacity=".55"/>
      <text x="533" y="815" text-anchor="middle" font-family="system-ui,sans-serif" font-size="22" fill="{accent}">影像件</text>
    </svg>'''


@router.get("/pages/{page_id}/thumbnail")
def thumbnail(page_id: str) -> Response:
    return Response(_svg(page_id, compact=True), media_type="image/svg+xml")


@router.get("/pages/{page_id}/preview")
def preview(page_id: str) -> Response:
    return Response(_svg(page_id, compact=False), media_type="image/svg+xml")
