"""Translate a demo scenario document into the production CaseState contract."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

from app.domain.models import CaseState, PageAsset, PersonRole


DEMO_ROOT = Path(__file__).resolve().parent


def _scenario_path(scenario: str) -> Path:
    aliases = json.loads((DEMO_ROOT / "scenario_aliases.json").read_text(encoding="utf-8"))
    scenario_file = aliases.get(scenario, aliases["default"])
    return DEMO_ROOT / "scenarios" / scenario_file


def _pages(payload: dict) -> list[PageAsset]:
    pages: list[PageAsset] = []
    cursor = 1
    for domain in payload["page_domains"]:
        for _ in range(int(domain["count"])):
            pages.append(PageAsset(
                page_id=f"PAGE-{cursor:03d}",
                bundle_id=f"BUNDLE-{((cursor - 1) // 6) + 1:02d}",
                page_number=cursor,
                domain=domain["name"],
                material_type="supporting_page",
                status="VERIFIED",
                confidence=.98,
                thumbnail_url=f"/api/demo/pages/PAGE-{cursor:03d}/thumbnail",
                preview_url=f"/api/demo/pages/PAGE-{cursor:03d}/preview",
            ))
            cursor += 1
    by_id = {page.page_id: page for page in pages}
    for override in payload.get("featured_pages", []):
        page = by_id[override["page_id"]]
        for key, value in override.items():
            if key != "page_id":
                setattr(page, key, deepcopy(value))
    return pages


def create_demo_case(scenario: str) -> CaseState:
    payload = json.loads(_scenario_path(scenario).read_text(encoding="utf-8"))
    return CaseState(
        case_id=payload["case_id"],
        thread_id=f"THREAD-{uuid4().hex[:12].upper()}",
        # Demo 人名只用于让页级结构化字段可读，进入业务状态时仍是未确认 Seed。
        # 角色必须经过与 Real 相同的 Case Association Agent + Gate 才能展示/规划。
        persons=[PersonRole(**{
            **item,
            "confirmed": False,
            "source": "DEMO_UPSTREAM_SEED",
        }) for item in payload["persons"]],
        pages=_pages(payload),
        completeness_status="NOT_STARTED",
        business_fields={
            "product_type": payload["product"],
            "channel": payload["channel"],
            "case_date": payload["case_date"],
            "scenario": scenario,
            "material_manifest": payload["material_manifest"],
        },
        status="READY",
    )
