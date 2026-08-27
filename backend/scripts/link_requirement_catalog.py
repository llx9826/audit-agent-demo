"""将模型上下文化的官方 Chunk 连接到 Atomic Requirement 索引目录。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.rag.offline.catalog_linking import link_requirement_catalog


ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--requirements",
        default=str(ROOT / "backend/app/rag/requirements/data/requirements.jsonl"),
    )
    parser.add_argument(
        "--chunks",
        default=str(ROOT / "backend/app/rag/offline/data/source_chunks.jsonl"),
    )
    parser.add_argument("--output", default=str(ROOT / ".data/index_requirements.jsonl"))
    parser.add_argument("--manifest", default=str(ROOT / ".data/catalog_link_manifest.json"))
    args = parser.parse_args()
    manifest = link_requirement_catalog(
        requirement_path=args.requirements,
        chunk_path=args.chunks,
        output_path=args.output,
        manifest_path=args.manifest,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
