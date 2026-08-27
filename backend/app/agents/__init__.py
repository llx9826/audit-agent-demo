"""业务 Agent 的稳定公共入口；使用惰性导出避免 Provider/Agent 循环导入。"""
from __future__ import annotations

from typing import Any


__all__ = [
    "CaseAssociationAgent",
    "CaseAssociationAssignment",
    "CaseAssociationRun",
    "ExceptionRecoveryAgent",
    "ExceptionResult",
    "ExceptionTask",
    "MaterialAuditAgent",
    "MaterialAuditAssignment",
    "MaterialAuditRun",
]


def __getattr__(name: str) -> Any:
    if name in {"ExceptionRecoveryAgent", "ExceptionResult", "ExceptionTask"}:
        from . import exception_recovery

        return getattr(exception_recovery, name)
    if name in {"CaseAssociationAgent", "CaseAssociationAssignment", "CaseAssociationRun"}:
        from . import case_association

        return getattr(case_association, name)
    if name in {"MaterialAuditAgent", "MaterialAuditAssignment", "MaterialAuditRun"}:
        from . import material_audit

        return getattr(material_audit, name)
    raise AttributeError(name)
