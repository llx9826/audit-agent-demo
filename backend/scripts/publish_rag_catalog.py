"""把既有离线 Chunk/Manifest 发布到 SQLite RAG 资产目录，不重复调用 LLM。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.rag.offline.catalog_store import RAGCatalogStore


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks", default="backend/app/rag/offline/data/source_chunks.jsonl")
    parser.add_argument("--build-manifest", default="backend/app/rag/offline/data/build_manifest.json")
    parser.add_argument("--index-manifest", default=".data/rag_index_manifest.json")
    parser.add_argument("--catalog-db", default=".data/rag_catalog.sqlite3")
    args = parser.parse_args()
    store = RAGCatalogStore(args.catalog_db)
    try:
        build_id = store.publish_chunks(
            _jsonl(Path(args.chunks)),
            json.loads(Path(args.build_manifest).read_text(encoding="utf-8")),
        )
        index_id = store.publish_index(
            json.loads(Path(args.index_manifest).read_text(encoding="utf-8")),
        )
        print(json.dumps({
            "build_id": build_id,
            "index_id": index_id,
            "catalog": str(args.catalog_db),
            "counts": store.stats(),
        }, ensure_ascii=False, indent=2))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
