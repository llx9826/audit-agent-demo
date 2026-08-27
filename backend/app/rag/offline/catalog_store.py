"""RAG 离线资产目录库。

SQLite 保存 Source/Version/Parent/Child/Build/Index 的可追溯元数据；Milvus
只保存面向在线召回的向量与稀疏索引。它与业务规则库职责不同，不能用召回
排名替代动态材料清单的适用性判断。
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable


class RAGCatalogStore:
    """发布并查询离线构建资产；所有写入在单个事务中完成。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS source_documents(
                source_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                publisher TEXT NOT NULL,
                jurisdiction TEXT NOT NULL,
                product TEXT NOT NULL,
                source_url TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS document_versions(
                source_id TEXT NOT NULL,
                source_version TEXT NOT NULL,
                checksum TEXT NOT NULL,
                built_at TEXT NOT NULL,
                PRIMARY KEY(source_id, source_version),
                FOREIGN KEY(source_id) REFERENCES source_documents(source_id)
            );
            CREATE TABLE IF NOT EXISTS parent_chunks(
                parent_chunk_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                source_version TEXT NOT NULL,
                heading TEXT NOT NULL,
                text TEXT NOT NULL,
                FOREIGN KEY(source_id, source_version)
                    REFERENCES document_versions(source_id, source_version)
            );
            CREATE TABLE IF NOT EXISTS child_chunks(
                child_chunk_id TEXT PRIMARY KEY,
                parent_chunk_id TEXT NOT NULL,
                semantic_unit_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                split_strategy TEXT NOT NULL,
                chunking_version TEXT NOT NULL,
                token_count INTEGER NOT NULL,
                overlap_tokens INTEGER NOT NULL,
                text TEXT NOT NULL,
                generated_context TEXT NOT NULL,
                embed_input TEXT NOT NULL,
                bm25_input TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                FOREIGN KEY(parent_chunk_id) REFERENCES parent_chunks(parent_chunk_id)
            );
            CREATE TABLE IF NOT EXISTS build_runs(
                build_id TEXT PRIMARY KEY,
                built_at TEXT NOT NULL,
                status TEXT NOT NULL,
                source_count INTEGER NOT NULL,
                parent_count INTEGER NOT NULL,
                child_count INTEGER NOT NULL,
                manifest_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS index_versions(
                index_id TEXT PRIMARY KEY,
                built_at TEXT NOT NULL,
                backend TEXT NOT NULL,
                collection_name TEXT NOT NULL,
                record_count INTEGER NOT NULL,
                dense_model TEXT NOT NULL,
                dense_dimension INTEGER NOT NULL,
                sparse_model TEXT NOT NULL,
                reranker_model TEXT NOT NULL,
                manifest_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_child_parent ON child_chunks(parent_chunk_id);
            CREATE INDEX IF NOT EXISTS idx_source_scope
                ON source_documents(product, jurisdiction, publisher);
            """
        )

    @staticmethod
    def _id(prefix: str, payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return f"{prefix}-{sha256(raw).hexdigest()[:16].upper()}"

    def publish_chunks(self, rows: Iterable[dict[str, Any]], manifest: dict[str, Any]) -> str:
        """原子发布一次 Source → Parent → Child 构建及其 Manifest。"""

        materialized = list(rows)
        with self._db:
            for row in materialized:
                self._db.execute(
                    """INSERT INTO source_documents VALUES(?,?,?,?,?,?)
                    ON CONFLICT(source_id) DO UPDATE SET
                    title=excluded.title,publisher=excluded.publisher,
                    jurisdiction=excluded.jurisdiction,product=excluded.product,
                    source_url=excluded.source_url""",
                    (
                        row["source_id"], row.get("source_title") or row["source_id"],
                        row["publisher"], row["jurisdiction"], row["product"], row["source_url"],
                    ),
                )
                self._db.execute(
                    """INSERT INTO document_versions VALUES(?,?,?,?)
                    ON CONFLICT(source_id,source_version) DO UPDATE SET
                    checksum=excluded.checksum,built_at=excluded.built_at""",
                    (
                        row["source_id"], row["source_version"], row["source_checksum"],
                        manifest["built_at"],
                    ),
                )
                self._db.execute(
                    """INSERT INTO parent_chunks VALUES(?,?,?,?,?)
                    ON CONFLICT(parent_chunk_id) DO UPDATE SET
                    heading=excluded.heading,text=excluded.text""",
                    (
                        row["parent_chunk_id"], row["source_id"], row["source_version"],
                        row["parent_heading"], row.get("parent_text") or row["text"],
                    ),
                )
                metadata = {
                    key: row.get(key)
                    for key in (
                        "source_id", "source_version", "publisher", "jurisdiction", "product",
                        "parent_heading", "semantic_title", "semantic_type", "retrieval_aliases",
                        "start_char", "end_char", "tokenizer",
                    )
                }
                self._db.execute(
                    """INSERT INTO child_chunks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(child_chunk_id) DO UPDATE SET
                    text=excluded.text,generated_context=excluded.generated_context,
                    embed_input=excluded.embed_input,bm25_input=excluded.bm25_input,
                    metadata_json=excluded.metadata_json""",
                    (
                        row["child_chunk_id"], row["parent_chunk_id"], row["semantic_unit_id"],
                        int(row["ordinal"]), row["split_strategy"], row["chunking_version"],
                        int(row["token_count"]), int(row["overlap_tokens"]), row["text"],
                        row["generated_context"], row["embed_input"], row["bm25_input"],
                        json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    ),
                )
            build_id = self._id("BUILD", manifest)
            self._db.execute(
                "INSERT OR REPLACE INTO build_runs VALUES(?,?,?,?,?,?,?)",
                (
                    build_id, manifest["built_at"], "PUBLISHED", int(manifest["source_count"]),
                    int(manifest["parent_count"]), int(manifest["child_chunk_count"]),
                    json.dumps(manifest, ensure_ascii=False, sort_keys=True),
                ),
            )
        return build_id

    def publish_index(self, manifest: dict[str, Any]) -> str:
        """记录已发布 Milvus Collection 的模型、维度与版本合同。"""

        index_id = self._id("INDEX", manifest)
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO index_versions VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    index_id, manifest["built_at"], manifest["backend"], manifest["collection"],
                    int(manifest["record_count"]), manifest["dense_model"],
                    int(manifest["dense_dimension"]), manifest["sparse_model"],
                    manifest["reranker_model"],
                    json.dumps(manifest, ensure_ascii=False, sort_keys=True),
                ),
            )
        return index_id

    def stats(self) -> dict[str, int]:
        """为 doctor/验收返回稳定资产计数，不暴露文档正文。"""

        tables = (
            "source_documents", "document_versions", "parent_chunks", "child_chunks",
            "build_runs", "index_versions",
        )
        return {
            table: int(self._db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }

    def close(self) -> None:
        self._db.close()

