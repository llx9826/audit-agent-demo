/**
 * 面试展示层的统一中文词典。
 *
 * 业务合同继续使用稳定英文枚举；组件只通过本文件把它们翻译为中文，避免
 * LangGraph Node ID、SSE Event Type 与持久化状态因为展示文案发生变化。
 */
export const roleLabels: Record<string, string> = {
  BORROWER: "借款人",
  MORTGAGOR: "抵押人",
  SPOUSE: "配偶",
  BUSINESS_OPERATOR: "经营主体",
};

export const materialLabels: Record<string, string> = {
  identity_document: "身份证明",
  property_certificate: "不动产权属证明",
  marriage_certificate: "婚姻证明",
  spouse_consent: "配偶同意抵押声明",
  business_license: "经营主体登记证明",
  supporting_page: "其他影像页",
};

export const statusLabels: Record<string, string> = {
  MATCHED: "已匹配",
  MISSING: "缺件",
  AMBIGUOUS: "语义待仲裁",
  UNREADABLE: "影像不可读",
  WAITING_HUMAN: "等待人工",
  WAITING_SUPPLEMENT: "等待补件",
  DIRTY: "等待重新执行",
  INVALIDATED: "旧结果已失效",
  PENDING: "等待审核",
  RUNNING: "正在执行",
  SKIPPED: "已跳过",
  SUCCESS: "已完成",
  FAILED: "执行失败",
  COMPLETE: "材料已齐套",
  COMPLETED: "已完成",
  PAUSED: "流程已暂停",
  INCOMPLETE: "材料未齐套",
  READY: "等待启动",
};

export const actionLabels: Record<string, string> = {
  CONFIRM_ASSOCIATION: "确认人员、角色与材料归属",
  RESOLVE_ASSOCIATION_EVIDENCE: "补充人员与角色证据",
  APPLY_CANDIDATES: "应用关联候选",
  APPLY_CANDIDATE: "应用材料候选",
  APPLY_SELECTED: "应用选中候选",
  REQUEST_RECOVERY: "请求补充机器证据",
  REQUEST_HUMAN: "请求人工确认",
  CALL_TOOL: "调用 Tool",
  RESOLVE: "提交恢复结果",
  ESCALATE: "升级人工介入",
  CONFIRM_OWNER: "确认材料所属人",
  REVIEW_IMAGE: "确认影像识别结果",
  REQUEST_SUPPLEMENT: "发起补件",
  SUPPLEMENT_RECEIVED: "登记补件到件",
  KEEP: "保留原结果",
  RERUN: "重新执行",
};

export const toolLabels: Record<string, string> = {
  ocr_retry: "OCR 复识",
  vlm_recheck: "VLM 图文复核",
  vlm_extract: "VLM 字段提取",
  document_search: "进件材料检索",
  neighbor_page_search: "相邻页检索",
  page_integrity_check: "缺页与重复页检查",
  document_reload: "重新加载文档",
};

export const executorLabels: Record<string, string> = {
  MATERIAL_MATCH_WORKER: "材料匹配 Worker",
  WORKFLOW_WORKER: "Workflow Worker",
  AUDIT_AGENT: "材料语义仲裁 Agent",
  EXCEPTION_AGENT: "异常取证恢复子 Agent",
};

export const executionGroupLabels: Record<string, string> = {
  MATERIAL_MATCH: "材料匹配并发组",
  ASSOCIATION_EVIDENCE: "关联证据并发组",
};

export const exceptionTypeLabels: Record<string, string> = {
  OCR_LOW_CONFIDENCE: "OCR 低置信度",
  VLM_LOW_CONFIDENCE: "VLM 低置信度",
  CROSS_PAGE_CONFLICT: "跨页证据冲突",
  OWNER_AMBIGUITY: "材料归属歧义",
  OWNER_ASSIGNMENT_AMBIGUOUS: "材料所属人证据存在歧义",
  MATERIAL_TYPE_AMBIGUITY: "材料类型歧义",
  MISSING_PAGE: "材料缺页",
  DUPLICATE_PAGE: "材料重复页",
  TOOL_FAILURE: "Tool 调用失败",
};

export const reasonLabels: Record<string, string> = {
  RESOLVED: "已获得足够证据",
  NEED_HUMAN: "需要人工介入",
  COMPLETION_CONDITION_MET: "完成条件已满足",
  PREMATURE_RESOLVE: "尚未满足完成条件",
  EVIDENCE_UNRESOLVED: "证据仍未收敛",
  NO_STATE_CHANGE: "State 连续无变化",
  OBSERVATION_ALREADY_COLLECTED: "相同 Observation 已采集",
  REQUEST_SUPPLEMENT: "发起缺件补件",
  CONFIRM_OWNER: "确认材料所属人",
  REVIEW_IMAGE: "确认影像识别结果",
  CONFIRM_ASSOCIATION: "确认人员、角色与材料归属",
  RESOLVE_ASSOCIATION_EVIDENCE: "补充人员与角色证据",
  SUPPLEMENT_RECEIVED: "登记补件到件",
};

export const nodeLabels: Record<string, string> = {
  ingest_case: "登记 200+ 页影像",
  select_association_pages: "筛选人员与角色证据页",
  extract_association_page: "Send 并行提取页级证据",
  extract_association_evidence: "汇聚人员与角色证据",
  build_association_candidates: "构建封闭关联候选",
  case_association_agent: "进件事实关联 Agent",
  association_gate: "关联事实校验门",
  resolve_requirements: "应交清单规则引擎",
  compile_checklist: "编译人员 × 应交材料项",
  resolve_ready_tasks: "解析可执行任务",
  match_task_worker: "Send 并行匹配 Worker",
  match_materials: "并行结果汇聚门",
  prepare_association_recovery: "构造关联异常 Handoff",
  prepare_matcher_recovery: "构造匹配异常 Handoff",
  prepare_material_recovery: "构造材料异常 Handoff",
  exception_recovery_agent: "异常取证恢复子 Agent",
  exception_result_gate: "恢复结果校验与回程",
  validate_completeness: "材料齐套校验",
  material_agent_review: "材料语义仲裁 Agent",
  audit_plan_gate: "材料对齐校验门",
  ground_requirement_evidence: "检索补件依据 RAG",
  prepare_problem_human: "生成补件任务",
  prepare_human: "持久化人工任务",
  await_human: "等待人工命令",
  reconcile_state: "补件事实对账",
  selective_replan: "选择性重规划",
  final_validator: "最终齐套校验",
  CASE_ASSOCIATION: "进件事实关联原任务",
  MATERIAL_MATCHER: "材料匹配原任务",
  MATERIAL_AUDIT: "材料语义仲裁原任务",
  ASSOCIATION_EVIDENCE: "关联证据构建阶段",
  TASK_MATCHER: "材料匹配阶段",
};

export const eventLabels: Record<string, string> = {
  CHECKPOINT_LOOKUP_STARTED: "正在查找暂停 Checkpoint",
  CHECKPOINT_FOUND: "已找到暂停 Checkpoint",
  INTERRUPTED_STATE_LOADED: "已加载暂停状态摘要",
  RESUME_COMMAND_ACCEPTED: "恢复命令校验通过",
  CHECKPOINT_RESUMED: "已恢复同一审核线程",
  STATE_RECONCILIATION_STARTED: "开始补件事实对账",
  STATE_RECONCILIATION_COMPLETED: "补件事实对账完成",
  IMPACT_ANALYSIS_COMPLETED: "任务依赖影响分析完成",
  SELECTIVE_REPLAN_COMPLETED: "选择性重规划完成",
  READY_TASKS_DISPATCHED: "已派发可执行任务",
  PLAN_VERSION_COMMITTED: "新计划版本已提交",
  ASSOCIATION_DECISION_PROPOSED: "关联 Agent 已提交结构化提议",
  ASSOCIATION_GATE_EVALUATED: "关联事实校验完成",
  AUDIT_DECISION_PROPOSED: "仲裁 Agent 已提交结构化提议",
  AUDIT_PLAN_GATE_EVALUATED: "材料对齐校验完成",
  EXCEPTION_HANDOFF_PREPARED: "异常 Handoff 已建立",
  EXCEPTION_RESULT_GATE_EVALUATED: "恢复结果校验与回程完成",
  COMPLETION_EVALUATED: "异常完成条件已校验",
  COMPLETENESS_CHECKED: "材料齐套校验完成",
  RUN_PAUSED: "流程已暂停并持久化",
  RUN_COMPLETED: "审核流程已完成",
};

export const gateOutcomeLabels: Record<string, string> = {
  CONFIRMED: "确认事实并写入",
  APPLIED_AND_REMATCH: "写入后重新匹配",
  RECOVERY_REQUIRED: "需要补充机器证据",
  HITL_REQUIRED: "需要人工确认",
  REJECTED_TO_HITL: "提议被拒绝，转人工",
  ASSOCIATION_RETRY: "返回关联证据阶段",
  MATCHER_RETRY: "返回材料匹配阶段",
  ASSOCIATION_HUMAN: "转关联人工确认",
  MATERIAL_HUMAN: "转材料人工确认",
};

export function labelOf(dictionary: Record<string, string>, value: unknown, fallback = "—"): string {
  if (typeof value !== "string" || !value) return fallback;
  return dictionary[value] ?? value;
}
