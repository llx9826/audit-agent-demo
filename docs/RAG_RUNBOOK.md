# Requirement RAG 运行手册

## 离线与在线边界

离线任务负责 Source Registry、官方页面快照、正文归一、章节 Parent、语义 Child、超长语义单元 Token overlap 兜底、LLM Contextual Retrieval、Catalog Link、Embedding、Milvus Index 和评测。在线请求严禁抓取、切段或写文档向量，只执行受约束改写、Metadata Filter、Dense/BM25、RRF、Cross-Encoder、Parent Context、Grounding、引用校验或拒答。

短条款不会为了凑固定 Token 数被打碎。主切分顺序是文档结构 → 章/节 → 条款/清单项/完整句群；仅当一个完整语义单元超过 384 Token 时，才用 BGE-M3 Tokenizer 做 48 Token overlap 的兜底窗口。LLM 生成的检索上下文和同义词只增强召回，最终证据仍引用原始 Atomic Requirement 与 Child ID。

## 首次构建

```bash
make rag-install
PYTHONPATH=backend .venv/bin/python backend/scripts/crawl_requirement_sources.py
make rag-build
make rag-catalog-publish
make rag-eval
```

已有 Chunk 和 Milvus 索引时，`make rag-catalog-publish` 只补建 SQLite 资产目录，不重复调用模型或重新 Embedding。

## 在线验证

```bash
make doctor
make rag-online-smoke
make agent-live-smoke
```

`rag-online-smoke` 必须经过真实模型意图识别、Query Rewrite、Milvus BGE-M3/Milvus BM25、RRF、Cross-Encoder、LLM Grounding 与引用校验。`agent-live-smoke` 固定的只有 Demo Case/Tool Observation，Audit 与 Exception 决策仍通过统一 ModelGateway，且使用与 SSE 相同的流式执行路径。

## 资产与门禁

- `.data/rag_catalog.sqlite3`：离线资产元数据；
- `.data/material_requirements.milvus.db`：Milvus Lite 本地索引；
- `.data/rag_index_manifest.json`：Embedding、维度、Metric、Collection 与 Reranker 合同；
- `backend/evals/requirement_retrieval.jsonl`：50 条 Golden Set；
- 门禁：HitRate@5 ≥ 0.95、Recall@5 ≥ 0.95、MRR ≥ 0.60、NDCG@5 ≥ 0.75。

模型文件应在部署前下载。运行时设置 `HF_HUB_OFFLINE=1` 和 `TRANSFORMERS_OFFLINE=1`，避免缓存探测把外网故障带入在线查询。

