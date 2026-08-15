---
name: exception_resolution
exception_types: [OCR_CONFLICT, LOW_CONFIDENCE, EVIDENCE_GAP]
allowed_tools: [ocr_retry, vlm_extract, document_search]
max_steps: 3
---

# Exception resolution

按 OCR Retry → VLM Extract → Evidence Cross Check 的顺序处理。连续两次相同动作且状态未变化时立即触发 Loop Guard，不继续消耗步骤。超过预算或证据仍冲突时返回 `NEED_HUMAN`。
