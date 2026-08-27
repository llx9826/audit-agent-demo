"""Typed SQLite catalog for atomic material requirements.

The JSONL file is an auditable seed artifact. Runtime rule resolution and RAG
both read the same relational catalog so the workflow never derives its
checklist from retrieval rank.
"""
from __future__ import annotations

from datetime import date
from functools import lru_cache
import atexit
import json
import os
from pathlib import Path
import sqlite3
from threading import RLock

from .corpus import DEFAULT_REQUIREMENT_CORPUS, load_requirement_corpus
from .models import AtomicRequirementRecord, requirement_from_mapping


_INSTITUTION_ALIAS_GROUPS = (
    ("建行", "建设银行", "中国建设银行", "ccb"),
    ("中行", "中国银行", "boc"),
    ("工行", "工商银行", "中国工商银行", "icbc"),
    ("农行", "农业银行", "中国农业银行", "abc"),
)


class SQLiteRequirementStore:
    def __init__(
        self,
        path: str | Path | None = None,
        *,
        seed_path: str | Path | None = None,
    ) -> None:
        configured = path or os.getenv("REQUIREMENT_DB_PATH")
        db_path = Path(configured) if configured else Path(__file__).resolve().parents[3] / ".data" / "requirement_catalog_v1.sqlite3"
        if str(db_path) != ":memory:":
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self.path = db_path
        self._lock = RLock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS atomic_requirements(
                requirement_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                product TEXT NOT NULL,
                channel TEXT NOT NULL,
                checklist_version INTEGER NOT NULL,
                effective_from TEXT NOT NULL,
                effective_to TEXT,
                person_role TEXT NOT NULL,
                material_type TEXT NOT NULL,
                source_document TEXT NOT NULL,
                source_section TEXT NOT NULL,
                atomic_requirement TEXT NOT NULL,
                condition_expression TEXT NOT NULL,
                required_pages INTEGER NOT NULL,
                status TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_requirement_scope
            ON atomic_requirements(product, channel, person_role, status, effective_from, effective_to);
            """
        )
        configured_seed = seed_path or os.getenv("REQUIREMENT_CORPUS_PATH") or DEFAULT_REQUIREMENT_CORPUS
        self.sync_seed(configured_seed)

    def sync_seed(self, seed_path: str | Path) -> None:
        records = load_requirement_corpus(seed_path)
        with self._lock:
            self._db.executemany(
                """
                INSERT INTO atomic_requirements(
                    requirement_id,title,product,channel,checklist_version,
                    effective_from,effective_to,person_role,material_type,
                    source_document,source_section,atomic_requirement,
                    condition_expression,required_pages,status,metadata_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(requirement_id) DO UPDATE SET
                    title=excluded.title, product=excluded.product, channel=excluded.channel,
                    checklist_version=excluded.checklist_version,
                    effective_from=excluded.effective_from, effective_to=excluded.effective_to,
                    person_role=excluded.person_role, material_type=excluded.material_type,
                    source_document=excluded.source_document, source_section=excluded.source_section,
                    atomic_requirement=excluded.atomic_requirement,
                    condition_expression=excluded.condition_expression,
                    required_pages=excluded.required_pages, status=excluded.status,
                    metadata_json=excluded.metadata_json
                """,
                [
                    (
                        item.requirement_id, item.title, item.product, item.channel,
                        item.checklist_version, item.effective_from.isoformat(),
                        item.effective_to.isoformat() if item.effective_to else None,
                        item.person_role, item.material_type, item.source_document,
                        item.source_section, item.atomic_requirement,
                        item.condition_expression, item.required_pages, item.status,
                        json.dumps(item.metadata, ensure_ascii=False, sort_keys=True),
                    )
                    for item in records
                ],
            )
            self._db.commit()

    @staticmethod
    def _record(row: sqlite3.Row) -> AtomicRequirementRecord:
        payload = dict(row)
        payload["metadata"] = json.loads(payload.pop("metadata_json"))
        return requirement_from_mapping(payload)

    def list_all(self) -> list[AtomicRequirementRecord]:
        rows = self._db.execute(
            "SELECT * FROM atomic_requirements ORDER BY requirement_id"
        ).fetchall()
        return [self._record(row) for row in rows]

    @staticmethod
    def _branch_alias_terms(alias: str) -> set[str]:
        normalized = "".join(alias.lower().split())
        terms = {normalized}
        for group in _INSTITUTION_ALIAS_GROUPS:
            if any(term in normalized for term in group):
                terms.update(group)
        return terms

    def resolve_metadata_branches(
        self,
        *,
        regions: list[str],
        aliases: list[str],
    ) -> list[str]:
        """把自然语言机构简称链接到当前 Catalog 中真实存在的 branch 值。

        匹配证据来自相同 Requirement Catalog 的标题、来源和 source_id；不把
        LLM 自由文本直接塞进向量库严格过滤，也不在业务服务中硬编码省份。
        """

        if not aliases:
            return []
        region_scope = set(regions)
        alias_terms = set().union(*(self._branch_alias_terms(item) for item in aliases))
        resolved: set[str] = set()
        for record in self.list_all():
            metadata = record.metadata
            region = str(metadata.get("region") or "")
            branch = str(metadata.get("branch") or "")
            if not branch or branch == "ALL" or (region_scope and region not in region_scope):
                continue
            haystack = " ".join((
                branch,
                record.title,
                record.source_document,
                record.source_section,
                str(metadata.get("source_id") or ""),
            )).lower()
            if any(term and term in haystack for term in alias_terms):
                resolved.add(branch)
        return sorted(resolved)

    def resolve_applicable(
        self,
        *,
        product: str,
        channel: str,
        case_date: date,
        person_roles: list[str],
    ) -> list[AtomicRequirementRecord]:
        if not person_roles:
            return []
        role_slots = ",".join("?" for _ in person_roles)
        sql = f"""
            SELECT * FROM atomic_requirements
            WHERE product = ?
              AND channel IN (?, 'ALL')
              AND person_role IN ({role_slots})
              AND status = 'ACTIVE'
              AND effective_from <= ?
              AND (effective_to IS NULL OR effective_to >= ?)
            ORDER BY checklist_version DESC, person_role, requirement_id
        """
        params = [product, channel, *person_roles, case_date.isoformat(), case_date.isoformat()]
        rows = self._db.execute(sql, params).fetchall()
        return [self._record(row) for row in rows]

    def stats(self) -> dict[str, object]:
        total = int(self._db.execute("SELECT COUNT(*) FROM atomic_requirements").fetchone()[0])
        products = [row[0] for row in self._db.execute(
            "SELECT DISTINCT product FROM atomic_requirements ORDER BY product"
        )]
        regions = [row[0] for row in self._db.execute(
            "SELECT DISTINCT json_extract(metadata_json, '$.region') FROM atomic_requirements "
            "WHERE json_extract(metadata_json, '$.region') IS NOT NULL ORDER BY 1"
        )]
        return {"backend": "SQLITE", "record_count": total, "products": products, "regions": regions}

    def close(self) -> None:
        with self._lock:
            self._db.close()


@lru_cache(maxsize=1)
def get_requirement_store() -> SQLiteRequirementStore:
    return SQLiteRequirementStore()


def reset_requirement_store_cache() -> None:
    cached = get_requirement_store.cache_info().currsize
    if cached:
        get_requirement_store().close()
    get_requirement_store.cache_clear()


atexit.register(reset_requirement_store_cache)
