from __future__ import annotations

import atexit
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routers import cases, knowledge, runs, system
from .bootstrap.container import ApplicationContainer
from .bootstrap.settings import settings_from_env
from .knowledge import KnowledgeRunManager, KnowledgeService
from .orchestration import AuditOrchestrator
from .runtime.run_manager import RunManager
from .service import AuditService


def configured_service(profile: str) -> AuditService:
    """兼容旧调用；新代码应直接使用 ApplicationContainer。"""

    return ApplicationContainer.build(profile=profile).audit_service


def configured_knowledge_service(profile: str) -> KnowledgeService:
    """兼容旧调用；知识问答与 Agent 共用 Container 中的 ModelGateway。"""

    return ApplicationContainer.build(profile=profile).knowledge_service


default_settings = settings_from_env()
default_container = ApplicationContainer.build(profile=default_settings.profile)
atexit.register(default_container.close)
service = default_container.audit_service
default_knowledge_service = default_container.knowledge_service
default_knowledge_run_manager = default_container.knowledge_run_manager
run_manager = default_container.run_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    app.state.run_manager.close()
    app.state.knowledge_run_manager.close()
    app.state.audit_service.close()


def create_app(
    audit_service: AuditService = service,
    runs_manager: RunManager = run_manager,
    profile: str | None = None,
    knowledge_service_override: KnowledgeService | None = None,
    knowledge_runs_manager: KnowledgeRunManager | None = None,
) -> FastAPI:
    active_settings = settings_from_env(profile=profile)
    active_profile = active_settings.profile
    if active_profile not in {"demo", "real", "production"}:
        raise ValueError(f"unsupported APP_PROFILE: {active_profile}")
    application = FastAPI(
        title="Material Completeness Audit Agent",
        version="2.0.0",
        lifespan=lifespan,
    )
    application.state.audit_service = audit_service
    application.state.run_manager = runs_manager
    application.state.audit_orchestrator = AuditOrchestrator(audit_service, runs_manager)
    application.state.profile = active_profile
    application.state.knowledge_service = (
        knowledge_service_override or configured_knowledge_service(active_profile)
    )
    application.state.knowledge_run_manager = (
        knowledge_runs_manager or KnowledgeRunManager(application.state.knowledge_service)
    )
    application.state.model_settings = active_settings.model
    cors_origins = list(active_settings.cors_origins)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(system.router)
    application.include_router(cases.router)
    application.include_router(knowledge.router)
    application.include_router(runs.router)
    if active_profile == "demo":
        from demo.router import router as demo_router

        application.include_router(demo_router)
    return application


app = create_app(
    knowledge_service_override=default_knowledge_service,
    knowledge_runs_manager=default_knowledge_run_manager,
)
