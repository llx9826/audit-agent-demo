"""ARGUS 后端统一命令入口：`python -m app <command>`。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Callable


def _describe() -> int:
    from .orchestration import describe_audit_pipeline

    print(json.dumps(describe_audit_pipeline(), ensure_ascii=False, indent=2))
    return 0


def _doctor() -> int:
    """只做只读配置检查，不调用模型、不修改索引。"""

    from .bootstrap.settings import settings_from_env

    settings = settings_from_env()
    uri = os.getenv("REQUIREMENT_RAG_MILVUS_URI", "").strip()
    corpus = Path(os.getenv("REQUIREMENT_CORPUS_PATH", ".data/index_requirements.jsonl"))
    manifest = Path(os.getenv("REQUIREMENT_RAG_INDEX_MANIFEST", ".data/rag_index_manifest.json"))
    local_vector_ready = bool(uri and not uri.startswith(("http://", "https://")) and Path(uri).exists())
    report = {
        "profile": settings.profile,
        "model_gateway": bool(settings.model and settings.model.endpoints),
        "rag": {
            "backend": os.getenv("REQUIREMENT_RAG_BACKEND", "milvus"),
            "corpus": corpus.exists(),
            "index_manifest": manifest.exists(),
            "vector_store": local_vector_ready or uri.startswith(("http://", "https://")),
        },
        "secrets": {"llm_api_key_configured": bool(os.getenv("LLM_API_KEY", "").strip())},
    }
    report["ready"] = bool(
        report["model_gateway"]
        and all(report["rag"][key] for key in ("corpus", "index_manifest", "vector_store"))
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ready"] else 1


def _serve(host: str, port: int, reload: bool) -> int:
    import uvicorn

    uvicorn.run("app.main:app", host=host, port=port, reload=reload)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("describe", help="打印唯一主 Pipeline 的真实拓扑")
    subparsers.add_parser("doctor", help="检查模型、语料与向量索引配置")
    serve = subparsers.add_parser("serve", help="启动 FastAPI 后端")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    handlers: dict[str, Callable[[], int]] = {"describe": _describe, "doctor": _doctor}
    if args.command == "serve":
        return _serve(args.host, args.port, args.reload)
    return handlers[args.command]()


if __name__ == "__main__":
    raise SystemExit(main())
