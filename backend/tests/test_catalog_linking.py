import json
from pathlib import Path
import tempfile
import unittest

from app.rag.offline.catalog_linking import link_requirement_catalog


class CatalogLinkingTests(unittest.TestCase):
    def test_official_requirement_consumes_contextualized_source_chunk(self):
        requirement = {
            "requirement_id": "KB-1",
            "title": "南京婚姻证明",
            "source_section": "婚姻状况证明",
            "atomic_requirement": "离婚提供离婚证。",
            "metadata": {"source_kind": "OFFICIAL_PUBLIC", "source_id": "nj"},
        }
        chunk = {
            "source_id": "nj",
            "source_version": "v1",
            "source_checksum": "abc",
            "source_url": "https://example.test/nj",
            "child_chunk_id": "CHILD-REAL",
            "parent_chunk_id": "PARENT-REAL",
            "parent_heading": "婚姻材料",
            "semantic_title": "婚姻状况证明",
            "text": "离婚职工需要提供离婚证。",
            "contextual_text": "官方办事指南 > 婚姻材料",
            "generated_context": "该段说明南京贷款婚姻材料。",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            requirement_path = root / "requirements.jsonl"
            chunk_path = root / "chunks.jsonl"
            output_path = root / "index.jsonl"
            manifest_path = root / "manifest.json"
            requirement_path.write_text(json.dumps(requirement, ensure_ascii=False) + "\n", encoding="utf-8")
            chunk_path.write_text(json.dumps(chunk, ensure_ascii=False) + "\n", encoding="utf-8")

            manifest = link_requirement_catalog(
                requirement_path=requirement_path,
                chunk_path=chunk_path,
                output_path=output_path,
                manifest_path=manifest_path,
            )

            linked = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(linked["metadata"]["child_chunk_id"], "CHILD-REAL")
            self.assertEqual(linked["metadata"]["generated_context"], chunk["generated_context"])
            self.assertEqual(manifest["official_linked_count"], 1)


if __name__ == "__main__":
    unittest.main()
