from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from app.rag.offline.catalog_store import RAGCatalogStore


class RAGCatalogStoreTests(unittest.TestCase):
    def test_source_parent_child_build_and_index_are_traceable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RAGCatalogStore(Path(directory) / "rag.sqlite3")
            manifest = {
                "built_at": "2026-08-17T00:00:00+00:00", "source_count": 1,
                "parent_count": 1, "child_chunk_count": 1,
            }
            row = {
                "source_id": "source-1", "source_title": "南京材料规范", "publisher": "机构",
                "jurisdiction": "南京", "product": "宅抵贷", "source_url": "https://example.test/1",
                "source_version": "v1", "source_checksum": "abc", "parent_chunk_id": "parent-1",
                "parent_heading": "第一章", "parent_text": "第一章正文", "child_chunk_id": "child-1",
                "semantic_unit_id": "unit-1", "ordinal": 1, "split_strategy": "SEMANTIC_UNIT",
                "chunking_version": "v3", "token_count": 20, "overlap_tokens": 0,
                "text": "借款人提供身份证明。", "generated_context": "南京宅抵贷材料章节。",
                "embed_input": "南京宅抵贷材料章节。\n借款人提供身份证明。",
                "bm25_input": "南京 宅抵贷 身份证明", "semantic_title": "身份材料",
                "semantic_type": "CLAUSE", "retrieval_aliases": ["身份证"], "start_char": 0,
                "end_char": 11, "tokenizer": "BAAI/bge-m3",
            }
            store.publish_chunks([row], manifest)
            store.publish_index({
                "built_at": "2026-08-17T00:05:00+00:00", "backend": "MILVUS_LITE",
                "collection": "requirements", "record_count": 1, "dense_model": "BAAI/bge-m3",
                "dense_dimension": 1024, "sparse_model": "MILVUS_BM25_FUNCTION",
                "reranker_model": "BAAI/bge-reranker-base",
            })
            self.assertEqual(store.stats(), {
                "source_documents": 1, "document_versions": 1, "parent_chunks": 1,
                "child_chunks": 1, "build_runs": 1, "index_versions": 1,
            })
            store.close()


if __name__ == "__main__":
    unittest.main()
