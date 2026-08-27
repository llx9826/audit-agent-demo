"""Parse snapshots and build Parent/Child token chunks for OFFLINE indexing."""
from __future__ import annotations

import argparse
from pathlib import Path

from app.rag.offline.build import build_source_chunks
from app.rag.offline.chunking import ChunkConfig
from app.rag.offline.contextualization import configured_contextualizer
from app.rag.offline.source_registry import DEFAULT_SOURCE_REGISTRY, load_source_registry


DATA_DIR = Path(__file__).resolve().parents[1] / "app" / "rag" / "offline" / "data"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", default=str(DEFAULT_SOURCE_REGISTRY))
    parser.add_argument("--snapshots", default=str(DATA_DIR / "snapshots"))
    parser.add_argument("--output", default=str(DATA_DIR / "source_chunks.jsonl"))
    parser.add_argument("--manifest", default=str(DATA_DIR / "build_manifest.json"))
    parser.add_argument("--profile", choices=["demo", "real"], default="real")
    parser.add_argument("--max-tokens", type=int, default=384)
    parser.add_argument("--overlap-tokens", type=int, default=48)
    parser.add_argument("--contextualizer", choices=["deterministic", "model"], default="model")
    parser.add_argument("--context-cache", default=str(DATA_DIR / "context_cache.json"))
    parser.add_argument("--catalog-db", default=".data/rag_catalog.sqlite3")
    args = parser.parse_args()
    manifest = build_source_chunks(
        load_source_registry(args.registry),
        snapshot_dir=args.snapshots,
        output_path=args.output,
        manifest_path=args.manifest,
        config=ChunkConfig(
            max_tokens=args.max_tokens,
            overlap_tokens=args.overlap_tokens,
        ),
        tokenizer_profile=args.profile,
        contextualizer=configured_contextualizer(
            args.contextualizer,
            cache_path=args.context_cache,
        ),
        catalog_path=args.catalog_db,
    )
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
