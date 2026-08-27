import unittest

from app.rag.offline import (
    ChunkConfig,
    ContextRequest,
    DeterministicContextualizer,
    JsonContextCache,
    QwenVllmContextualizer,
    SourceArtifact,
    extract_text,
    load_contextualization_prompt,
    process_source,
)
from app.rag.offline.document_processing import split_parent_sections


class DocumentProcessingTests(unittest.TestCase):
    def test_html_text_layer_drops_script_and_preserves_visible_content(self):
        artifact = SourceArtifact(
            source_id="official-page",
            source_version="2026-01-01",
            title="办理材料",
            source_url="https://example.test/materials",
            mime_type="text/html",
            content="<html><script>ignore()</script><h1>办理材料</h1><p>申请人提供身份证明。</p></html>".encode(),
        )
        text = extract_text(artifact)
        self.assertIn("办理材料", text)
        self.assertIn("申请人提供身份证明", text)
        self.assertNotIn("ignore", text)

    def test_structure_chunk_ids_are_stable_and_parent_child_linked(self):
        artifact = SourceArtifact(
            source_id="official-guide",
            source_version="v2",
            title="贷款材料指南",
            source_url="https://example.test/guide",
            mime_type="text/markdown",
            content="# 婚姻材料\n已婚者提供结婚证。离婚者提供离婚证或判决书。\n\n# 身份材料\n申请人提供身份证。".encode(),
        )
        first = process_source(artifact)
        second = process_source(artifact)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 2)
        self.assertTrue(first[0][1])
        self.assertTrue(all(item.parent_chunk_id == first[0][0].parent_chunk_id for item in first[0][1]))
        self.assertEqual(first[0][0].source_checksum, artifact.checksum)

    def test_long_section_uses_token_overlap_with_stable_spans(self):
        text = "".join(f"材料{i}应当提交" for i in range(180))
        artifact = SourceArtifact(
            source_id="long-guide",
            source_version="v1",
            title="长文档",
            source_url="https://example.test/long",
            mime_type="text/markdown",
            content=("# 办理材料\n" + text).encode(),
        )
        built = process_source(
            artifact,
            config=ChunkConfig(max_tokens=100, overlap_tokens=15),
        )
        chunks = built[0][1]
        self.assertGreater(len(chunks), 1)
        self.assertEqual(chunks[0].overlap_tokens, 0)
        self.assertTrue(all(item.overlap_tokens == 15 for item in chunks[1:]))
        self.assertTrue(all(item.token_count <= 100 for item in chunks))
        self.assertLess(chunks[1].start_char, chunks[0].end_char)
        self.assertTrue(all(item.split_strategy == "TOKEN_WINDOW_FALLBACK" for item in chunks))

    def test_short_atomic_clause_gets_context_and_aliases_without_token_split(self):
        artifact = SourceArtifact(
            source_id="bank-guide",
            source_version="v1",
            title="个人经营贷款办理材料",
            source_url="https://bank.example/guide",
            mime_type="text/plain",
            content="办理所需材料\n1. 借款人及配偶提供婚姻状况证明。\n2. 企业提供营业执照。".encode(),
        )

        chunks = process_source(artifact)[0][1]

        self.assertEqual(len(chunks), 2)
        self.assertTrue(all(item.split_strategy == "SEMANTIC_UNIT" for item in chunks))
        self.assertTrue(all(item.overlap_tokens == 0 for item in chunks))
        self.assertIn("文档：个人经营贷款办理材料", chunks[0].contextual_text)
        self.assertIn("婚姻状况材料", chunks[0].retrieval_aliases)
        self.assertIn("经营资格文件", chunks[1].retrieval_aliases)

    def test_numbered_material_rows_remain_inside_named_material_section(self):
        artifact = SourceArtifact(
            source_id="bank-page",
            source_version="v1",
            title="办理页",
            source_url="https://bank.example/materials",
            mime_type="text/plain",
            content=b"",
        )
        parents = split_parent_sections(
            artifact,
            "办理所需材料\n一、借款人及配偶身份证明；\n二、企业营业执照。\n温馨提示\n请咨询经办网点。",
        )

        self.assertEqual([item.heading for item in parents], ["办理所需材料", "温馨提示"])
        self.assertIn("借款人及配偶身份证明", parents[0].text)
        self.assertIn("企业营业执照", parents[0].text)

    def test_contextualizer_enriches_search_text_without_rewriting_evidence(self):
        request = ContextRequest(
            source_id="official-guide",
            source_title="申请材料指南",
            publisher="住房公积金中心",
            product="住房公积金个人住房贷款",
            jurisdiction="南京",
            parent_heading="婚姻材料",
            parent_text="已婚者提供结婚证。",
            chunk_text="已婚者提供结婚证。",
            semantic_title="已婚材料",
        )

        context = DeterministicContextualizer().contextualize(request)

        self.assertIn("南京", context)
        self.assertIn("婚姻材料", context)
        self.assertEqual(request.chunk_text, "已婚者提供结婚证。")

    def test_qwen_contextualizer_uses_versioned_prompt_asset(self):
        prompt = load_contextualization_prompt()
        adapter = QwenVllmContextualizer(
            base_url="http://model.test/v1",
            model="Qwen-Test",
            cache=JsonContextCache("/tmp/audit-agent-context-test-cache.json"),
            prompt=prompt,
        )

        self.assertEqual(prompt.prompt_id, "rag.offline.contextual-retrieval")
        self.assertEqual(prompt.version, "1.0.0")
        self.assertIn("不得改写原始证据", prompt.system)
        self.assertEqual(adapter.prompt_metadata["sha256"], prompt.sha256)


if __name__ == "__main__":
    unittest.main()
