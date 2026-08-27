"""打印真实主 Pipeline 的入口、Stage、分支与 Agent/HITL 交接合同。"""
from __future__ import annotations

import json

from app.orchestration import describe_audit_pipeline


if __name__ == "__main__":
    print(json.dumps(describe_audit_pipeline(), ensure_ascii=False, indent=2))
