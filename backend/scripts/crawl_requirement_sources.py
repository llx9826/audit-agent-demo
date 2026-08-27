"""Fetch allowlisted official sources for the OFFLINE RAG build."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from app.rag.offline.crawler import crawl_sources
from app.rag.offline.source_registry import DEFAULT_SOURCE_REGISTRY, load_source_registry


DEFAULT_SNAPSHOT_DIR = Path(__file__).resolve().parents[1] / "app" / "rag" / "offline" / "data" / "snapshots"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", default=str(DEFAULT_SOURCE_REGISTRY))
    parser.add_argument("--output", default=str(DEFAULT_SNAPSHOT_DIR))
    parser.add_argument("--delay", type=float, default=1.0)
    args = parser.parse_args()
    results = crawl_sources(
        load_source_registry(args.registry),
        output_dir=args.output,
        delay_s=args.delay,
    )
    print(json.dumps([asdict(item) for item in results], ensure_ascii=False, indent=2))
    return 1 if any(item.status == "FAILED" for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
