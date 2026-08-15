const OFFICIAL_PERSONAL_LOAN_URL =
  "https://www.nfra.gov.cn/cn/view/pages/ItemDetail.html?docId=1151064&itemId=861";
const OFFICIAL_COST_URL =
  "https://www.nfra.gov.cn/cn/view/pages/governmentDetail.html?docId=1251479&generaltype=1&itemId=861";

function result(taskId: string, conclusion: string, ruleRefs: string[] = []) {
  return {
    task_id: taskId,
    status: "SUCCESS",
    conclusion,
    confidence: 0.96,
    evidence_refs: [`E-${taskId}`],
    rule_refs: ruleRefs,
    case_version: 2,
    plan_version: 2,
  };
}

function task(taskId: string, taskType: string, conclusion: string, ruleRefs: string[] = []) {
  return {
    task_id: taskId,
    task_type: taskType,
    status: "SUCCESS",
    depends_on: [],
    result: result(taskId, conclusion, ruleRefs),
  };
}

export function createRecordedWaitingState() {
  return {
    case_id: "CASE-ZD-042",
    case_version: 1,
    plan_version: 1,
    documents: [
      { document_id: "DOC-ID-01", type: "IDENTITY", status: "VERIFIED", fields: { name: "张三" } },
      { document_id: "DOC-HH-01", type: "HOUSEHOLD_REGISTER", status: "CONFLICT", fields: { name: "张叁", confidence: 0.61 } },
    ],
    entities: {
      borrower: { name: "张三", entity_id: "P-001" },
      mortgagor: { name: "李四", entity_id: "P-002" },
    },
    business_fields: {
      application_amount: 2_800_000,
      display_amount: 2_800_000,
      loan_term_months: 60,
      loan_purpose: "企业流动资金",
      company_age_months: 10,
      property_holding_months: 8,
      relation: "UNKNOWN",
      case_date: "2026-08-15",
    },
    audit_plan: [
      task("T01", "BORROWER_IDENTITY", "身份信息已通过双源复核"),
      task("T02", "MORTGAGOR_TITLE", "抵押物权属主体为李四"),
      { task_id: "T03", task_type: "RELATION_REVIEW", status: "WAITING_HUMAN", depends_on: ["T01", "T02"], result: null },
      { task_id: "T04", task_type: "RELATION_DOCUMENT", status: "WAITING_HUMAN", depends_on: ["T03"], result: null },
      { task_id: "T05", task_type: "POLICY_REVIEW", status: "PENDING", depends_on: ["T03"], result: null },
      task("T08", "BUSINESS_AUTHENTICITY", "经营背景与资金需求已完成规则核查"),
      task("T09", "PROPERTY_HOLDING", "短期持有事实已进入控制措施"),
      task("T10", "VALUATION_REVIEW", "估值偏离已完成审查"),
      task("T11", "ENTRUSTED_PAYMENT", "大额提款采用受托支付"),
    ],
    task_results: {
      T01: result("T01", "身份信息已通过双源复核"),
      T02: result("T02", "抵押物权属主体为李四"),
      T08: result("T08", "经营背景与资金需求已完成规则核查"),
      T09: result("T09", "短期持有事实已进入控制措施"),
      T10: result("T10", "估值偏离已完成审查"),
      T11: result("T11", "大额提款采用受托支付"),
    },
    evidence_ledger: [
      { evidence_id: "E-T01", source_type: "DOCUMENT", source_id: "DOC-ID-01", value: "张三", confidence: 0.99 },
      { evidence_id: "E-OCR-RESOLVED", source_type: "AGENT_RESULT", source_id: "EXC-001", value: "张三", confidence: 0.97 },
    ],
    changed_facts: [],
    dirty_tasks: [],
    invalidated_tasks: [],
    pending_human_request: {
      request_type: "SUPPLEMENT_DOCUMENT",
      required_documents: ["婚姻关系证明", "共有人同意抵押文件"],
      reason_code: "RELATION_EVIDENCE_GAP",
    },
    status: "WAITING_HUMAN",
  };
}

export function createRecordedFinalState() {
  const plan = [
    task("T01", "BORROWER_IDENTITY", "原身份结果复用"),
    task("T02", "MORTGAGOR_TITLE", "原权属结果复用"),
    task("T03", "RELATION_REVIEW", "张三与李四关系确认为 SPOUSE"),
    task("T04", "RELATION_DOCUMENT", "婚姻关系证明已验证"),
    task("T05", "POLICY_REVIEW", "关系变化后的制度结论已重新审核", ["NFRA-2024-PERSONAL-LOAN"]),
    task("T06", "SPOUSE_IDENTITY", "配偶身份已核验"),
    task("T07", "COOWNER_CONSENT", "同意抵押文件已验证"),
    task("T08", "BUSINESS_AUTHENTICITY", "经营背景与资金需求匹配"),
    task("T09", "PROPERTY_HOLDING", "短期持有风险已纳入贷后控制"),
    task("T10", "VALUATION_REVIEW", "估值差异已完成复核"),
    task("T11", "ENTRUSTED_PAYMENT", "采购款采用受托支付"),
    task("T12", "COST_DISCLOSURE", "签约前明示综合融资成本", ["NFRA-2026-COST-01"]),
  ];
  const taskResults = Object.fromEntries(plan.map((item) => [item.task_id, item.result]));

  return {
    case_id: "CASE-ZD-042",
    case_version: 2,
    plan_version: 2,
    documents: [
      { document_id: "DOC-ID-01", type: "IDENTITY", status: "VERIFIED", fields: { name: "张三" } },
      { document_id: "DOC-MARRIAGE-01", type: "MARRIAGE_CERTIFICATE", status: "VERIFIED", fields: { husband: "张三", wife: "李四", registered_at: "2022-06-18" } },
      { document_id: "DOC-CONSENT-01", type: "COOWNER_CONSENT", status: "VERIFIED", fields: { signer: "李四", signed_at: "2026-08-14" } },
    ],
    entities: {
      borrower: { name: "张三", entity_id: "P-001" },
      mortgagor: { name: "李四", entity_id: "P-002" },
    },
    business_fields: {
      application_amount: 2_800_000,
      display_amount: 2_800_000,
      loan_term_months: 60,
      loan_purpose: "企业流动资金",
      company_age_months: 10,
      property_holding_months: 8,
      relation: "SPOUSE",
      case_date: "2026-08-15",
      final_decision: "PASS_WITH_CONTROLS",
      controls: [
        { control_id: "C01", code: "REGISTRATION_BEFORE_DISBURSEMENT", title: "抵押登记完成前不放款", status: "REQUIRED" },
        { control_id: "C02", code: "ENTRUSTED_PAYMENT", title: "采购款执行受托支付", status: "REQUIRED" },
        { control_id: "C03", code: "ENHANCED_POST_LOAN_REVIEW", title: "增强贷后用途核查", status: "REQUIRED" },
        { control_id: "C04", code: "TOTAL_COST_DISCLOSURE", title: "明示综合融资成本并取得确认", status: "REQUIRED" },
      ],
    },
    audit_plan: plan,
    task_results: taskResults,
    evidence_ledger: [
      { evidence_id: "E-T01", source_type: "DOCUMENT", source_id: "DOC-ID-01", value: "张三", confidence: 0.99 },
      { evidence_id: "E-T03", source_type: "DOCUMENT", source_id: "DOC-MARRIAGE-01", value: "SPOUSE", confidence: 0.99 },
      { evidence_id: "E-T07", source_type: "DOCUMENT", source_id: "DOC-CONSENT-01", value: "同意抵押", confidence: 0.99 },
      { evidence_id: "E-RULE-COST-2026", source_type: "POLICY", source_id: "NFRA-2026-COST-01", value: "签约前明示综合融资成本", rule_id: "NFRA-2026-COST-01", confidence: 1 },
    ],
    changed_facts: ["documents.marriage_certificate", "business_fields.relation"],
    dirty_tasks: [],
    invalidated_tasks: ["T03", "T05"],
    pending_human_request: null,
    status: "COMPLETED",
  };
}

export function createRecordedEvents() {
  return [
    {
      event_id: "REC-001",
      seq: 1,
      event_type: "PLAN_CREATED",
      actor: "workflow",
      payload: { node: "plan", title: "确定性任务图已编译", task_count: 9 },
    },
    {
      event_id: "REC-002",
      seq: 2,
      event_type: "ROUTE_EVALUATED",
      actor: "workflow",
      payload: {
        node: "audit_route",
        route: {
          predicate: "ocr_conflict == true",
          actual_value: true,
          selected_edge: "exception_recovery",
          rejected_edges: ["relation_review"],
          reason_code: "OCR_IDENTITY_CONFLICT",
        },
      },
    },
    {
      event_id: "REC-003",
      seq: 3,
      event_type: "HANDOFF_CREATED",
      actor: "workflow",
      payload: {
        node: "exception_handoff",
        handoff: {
          exception_type: "OCR_IDENTITY_CONFLICT",
          context_refs: ["DOC-ID-01.name", "DOC-HH-01.name"],
          allowed_tools: ["ocr_retry", "vlm_extract", "document_search"],
          step_budget: 3,
        },
      },
    },
    ...["ocr_retry", "vlm_extract", "document_search"].map((tool, index) => ({
      event_id: `REC-00${index + 4}`,
      seq: index + 4,
      event_type: "AGENT_TOOL_FINISHED",
      actor: "exception_agent",
      payload: {
        node: "execute_tool",
        tool,
        observation: index === 2 ? "可信材料检索与 VLM 提取一致：张三" : "局部字段已重新提取",
        remaining_budget: 2 - index,
        adapter: "OFFLINE_DETERMINISTIC_TOOLS",
      },
    })),
    {
      event_id: "REC-007",
      seq: 7,
      event_type: "AGENT_RETURNED",
      actor: "exception_agent",
      payload: {
        node: "exception_recovery",
        agent_result: { status: "RESOLVED", stop_reason: "DUAL_SOURCE_MATCH", evidence_delta: ["E-OCR-RESOLVED"] },
      },
    },
    {
      event_id: "REC-008",
      seq: 8,
      event_type: "STATE_PATCH_APPLIED",
      actor: "workflow",
      payload: { node: "apply_exception_result", state_diff: { "documents.DOC-HH-01.status": ["CONFLICT", "RESOLVED"] } },
    },
    {
      event_id: "REC-009",
      seq: 9,
      event_type: "RELATION_REVIEWED",
      actor: "audit_agent",
      payload: {
        node: "relation_review",
        observation: "抵押人与借款人不同，且缺少可验证关系材料",
        audit_decision: {
          relation: "UNKNOWN",
          relation_hypothesis: "POSSIBLE_SPOUSE",
          task_intents: ["VERIFY_RELATION", "VERIFY_DISPOSAL_CONSENT"],
          write_authority: "PROPOSAL_ONLY",
        },
      },
    },
    {
      event_id: "REC-010",
      seq: 10,
      event_type: "ROUTE_EVALUATED",
      actor: "workflow",
      payload: {
        node: "relation_route",
        route: {
          predicate: "relation_evidence_complete == true",
          actual_value: false,
          selected_edge: "provisional_policy_review",
          rejected_edges: ["final_validator"],
          reason_code: "RELATION_EVIDENCE_GAP",
        },
      },
    },
    {
      event_id: "REC-011",
      seq: 11,
      event_type: "HITL_REQUESTED",
      actor: "workflow",
      checkpoint_id: "CP-CASE-ZD-042-V1",
      payload: { node: "human_review", reason_code: "RELATION_EVIDENCE_GAP", status: "WAITING_HUMAN" },
    },
  ];
}

export function createRecordedFinalEvents() {
  return [
    ...createRecordedEvents(),
    { event_id: "REC-012", seq: 12, event_type: "SUPPLEMENT_INGESTED", actor: "workflow", payload: { node: "supplement_ingest", document_id: "DOC-MARRIAGE-01" } },
    { event_id: "REC-013", seq: 13, event_type: "STATE_RECONCILED", actor: "workflow", payload: { node: "reconcile", state_diff: { relation: ["UNKNOWN", "SPOUSE"], case_version: [1, 2] } } },
    { event_id: "REC-014", seq: 14, event_type: "PLAN_PATCH_APPLIED", actor: "workflow", payload: { node: "selective_replan", added: ["T06", "T07", "T12"], rerun: ["T03", "T05"], kept: ["T01", "T02"] } },
    { event_id: "REC-015", seq: 15, event_type: "RESULT_GROUNDED", actor: "workflow", payload: { node: "policy_grounding", rule_id: "NFRA-2026-COST-01", evidence_id: "E-RULE-COST-2026" } },
    { event_id: "REC-016", seq: 16, event_type: "FINAL_VALIDATED", actor: "workflow", payload: { node: "final_validator", status: "COMPLETED", decision: "PASS_WITH_CONTROLS" } },
  ];
}

export function createRecordedRagTrace() {
  return {
    original_query: "CASE-ZD-042 在 2026-08-15 签约时，应适用哪项个人经营贷款成本明示规则？",
    rewritten_query: "个人经营贷款 2026 综合融资成本 签约前 明示 生效日期",
    candidates: [
      {
        rule_id: "DEMO-COST-2025-RETIRED",
        title: "历史成本披露演示策略（已停用）",
        version: 1,
        dense_score: 0.82425,
        bm25_score: 1,
        rrf_score: 0.032522,
        valid: false,
        reason: "VERSION_INACTIVE",
        effective_date: "2025-01-01",
        status: "INACTIVE",
        source_type: "DEMO_RETIRED_POLICY",
      },
      {
        rule_id: "NFRA-2026-COST-01",
        title: "个人贷款业务明示综合融资成本规定",
        version: 1,
        dense_score: 0.471344,
        bm25_score: 0.377434,
        rrf_score: 0.032266,
        valid: true,
        reason: "产品、状态与生效日期均匹配",
        effective_date: "2026-08-01",
        status: "ACTIVE",
        issuer: "国家金融监督管理总局、中国人民银行",
        article: "金规〔2026〕2号",
        source_url: OFFICIAL_COST_URL,
        source_type: "OFFICIAL_POLICY",
      },
      {
        rule_id: "NFRA-2024-PERSONAL-LOAN",
        title: "个人贷款管理办法",
        version: 1,
        dense_score: 0.037762,
        bm25_score: 0,
        rrf_score: 0.015873,
        valid: true,
        reason: "基础个人贷款管理规则仍适用",
        effective_date: "2024-07-01",
        status: "ACTIVE",
        issuer: "国家金融监督管理总局",
        article: "国家金融监督管理总局令2024年第3号",
        source_url: OFFICIAL_PERSONAL_LOAN_URL,
        source_type: "OFFICIAL_POLICY",
      },
    ],
    final_rule: "NFRA-2026-COST-01",
    final_evidence_id: "E-RULE-COST-2026",
    clause: "签订贷款合同前，应以明显方式向借款人明示年化综合融资成本。",
    retrieval: { strategy: "HASHED DENSE + BM25 + RRF + APPLICABILITY GATE", score_source: "RECORDED_LOCAL_CORPUS_RUNTIME" },
  };
}
