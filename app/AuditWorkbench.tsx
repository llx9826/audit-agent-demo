"use client";

import { useMemo, useState } from "react";
import ArchitectureEvolution from "./components/ArchitectureEvolution";
import {
  createRecordedEvents,
  createRecordedFinalEvents,
  createRecordedFinalState,
  createRecordedRagTrace,
  createRecordedWaitingState,
} from "./demoRecording";

type Act = 1 | 2 | 3;
type ExecutionMode = "LOCAL_GRAPH_RUNTIME" | "RECORDED_GRAPH_TRACE";
type FieldValue = string | number | boolean | Record<string, unknown> | unknown[] | null | undefined;

interface AuditResult {
  task_id: string;
  status: string;
  conclusion: string;
  confidence: number;
  evidence_refs: string[];
  rule_refs: string[];
  case_version: number;
  plan_version: number;
}

interface AuditTask {
  task_id: string;
  task_type: string;
  status: string;
  depends_on: string[];
  result?: AuditResult | null;
}

interface EvidenceItem {
  evidence_id: string;
  source_type: string;
  source_id: string;
  value: string;
  rule_id?: string | null;
  confidence?: number | null;
}

interface CaseState {
  case_id: string;
  case_version: number;
  plan_version: number;
  documents: Array<{
    document_id: string;
    type: string;
    status: string;
    fields: Record<string, FieldValue>;
  }>;
  entities: Record<string, Record<string, FieldValue>>;
  business_fields: Record<string, FieldValue>;
  audit_plan: AuditTask[];
  task_results: Record<string, AuditResult>;
  evidence_ledger: EvidenceItem[];
  changed_facts: string[];
  dirty_tasks: string[];
  invalidated_tasks: string[];
  pending_human_request: Record<string, unknown> | null;
  status: string;
}

interface AuditEvent {
  event_id: string;
  seq: number;
  event_type: string;
  actor: string;
  checkpoint_id?: string | null;
  payload: Record<string, unknown> & {
    node?: string;
    title?: string;
    task_id?: string;
    action?: string;
    tool?: string;
    observation?: unknown;
    state_diff?: Record<string, unknown>;
    reason_code?: string;
    predicate?: string;
    actual_value?: unknown;
    selected_edge?: string;
    rejected_edges?: string[];
    allowed_tools?: string[];
    scoped_context_refs?: string[];
    max_steps?: number;
    remaining_budget?: number;
    stop_condition?: string;
    status?: string;
    stop_reason?: string;
  };
}

interface RagCandidate {
  rule_id: string;
  title: string;
  version: number;
  score?: number;
  dense_score?: number;
  bm25_score?: number;
  rrf_score?: number;
  valid?: boolean;
  eligible?: boolean;
  reason?: string;
  filter_reason?: string;
  effective_date?: string;
  status?: string;
  issuer?: string;
  article?: string;
  source_url?: string;
  source_type?: string;
}

interface RagTrace {
  original_query: string;
  rewritten_query: string;
  candidates: RagCandidate[];
  final_rule: string;
  final_evidence_id?: string;
  clause?: string;
  retrieval?: { strategy?: string; score_source?: string };
}

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";
const HAS_CONFIGURED_API = Boolean(process.env.NEXT_PUBLIC_API_BASE);

const ACTS = [
  { id: 1, kicker: "WHY", title: "架构演进", subtitle: "为什么增加 Agent" },
  { id: 2, kicker: "HOW", title: "协作执行", subtitle: "图如何路由与交接" },
  { id: 3, kicker: "PROOF", title: "证据闭环", subtitle: "补件后如何收口" },
] as const;

const DECISIONS = [
  {
    id: "plan",
    index: "01",
    title: "确定性 Workflow 编译任务图",
    summary: "先完成可编码的身份、经营与抵押物检查，把不确定性留给后续路由。",
    action: "查看语义路由",
  },
  {
    id: "route",
    index: "02",
    title: "OCR 冲突触发条件边与 Typed Handoff",
    summary: "主图先识别异常类型，再用受限信封把局部字段交给 Exception Sub-Agent。",
    action: "进入异常子图",
  },
  {
    id: "exception",
    index: "03",
    title: "OCR 冲突进入受控 Exception Sub-Agent",
    summary: "Typed Handoff 限定上下文、工具和步数；子图必须以 RESOLVED 或 NEED_HUMAN 返回。",
    action: "查看人工暂停",
  },
  {
    id: "pause",
    index: "04",
    title: "回到 Audit Agent，关系证据仍不足",
    summary: "异常已解决，但外部关系不能由工具推断；Audit Agent 请求主图写入 Checkpoint。",
    action: "进入补件闭环",
  },
] as const;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json() as Promise<T>;
}

function asText(value: unknown, fallback = "—"): string {
  if (value === null || value === undefined || value === "") return fallback;
  if (Array.isArray(value)) return value.join(" · ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function findEvent(events: AuditEvent[], types: string[]): AuditEvent | undefined {
  return events.find((event) => types.includes(event.event_type));
}

function findLastEvent(events: AuditEvent[], types: string[]): AuditEvent | undefined {
  return [...events].reverse().find((event) => types.includes(event.event_type));
}

function ChapterRail({ act }: { act: Act }) {
  return (
    <ol className="chapter-rail" aria-label="三幕演示进度">
      {ACTS.map((item) => (
        <li className={item.id === act ? "is-active" : item.id < act ? "is-complete" : ""} key={item.id}>
          <i>{String(item.id).padStart(2, "0")}</i>
          <span><small>{item.kicker}</small><strong>{item.title}</strong><em>{item.subtitle}</em></span>
        </li>
      ))}
    </ol>
  );
}

function StatusPill({ value }: { value: string }) {
  const tone = value === "COMPLETED" || value === "SUCCESS" ? "success" : value.includes("WAIT") ? "warning" : "neutral";
  return <span className={`status-pill status-pill--${tone}`}>{value}</span>;
}

function RuntimeGraph({ step }: { step: number }) {
  const active = DECISIONS[step].id;
  return (
    <div className={`runtime-graph runtime-graph--${active}`} aria-label="当前 LangGraph 执行位置">
      <div className="graph-lane-label"><span>MAIN GRAPH</span><small>状态与控制权始终留在 Workflow</small></div>
      <div className="graph-flow graph-flow--main">
        <div className="runtime-graph-node is-done"><small>01</small><strong>Ingest</strong><span>材料接入</span></div>
        <i className="runtime-edge is-done" />
        <div className="runtime-graph-node is-done"><small>02</small><strong>Build State</strong><span>事实归一化</span></div>
        <i className="runtime-edge is-done" />
        <div className={`runtime-graph-node ${active === "plan" ? "is-active" : "is-done"}`}><small>03</small><strong>Plan Gate</strong><span>任务与依赖</span></div>
        <i className={`runtime-edge ${step >= 1 ? "is-done" : ""}`} />
        <div className={`runtime-graph-node runtime-graph-node--decision ${active === "route" ? "is-active" : step > 1 ? "is-done" : ""}`}><small>IF</small><strong>Semantic?</strong><span>条件路由</span></div>
        <i className={`runtime-edge ${step >= 1 ? "is-done" : ""}`} />
        <div className={`runtime-graph-node runtime-graph-node--agent ${active === "pause" ? "is-active" : ""}`}><small>AG</small><strong>Audit Agent</strong><span>关系证据审核</span></div>
      </div>

      <div className="agent-handoff-line"><span>TYPED HANDOFF</span><i /></div>
      <div className={`exception-subgraph ${active === "exception" ? "is-active" : step > 2 ? "is-done" : ""}`}>
        <div className="graph-lane-label"><span>BOUNDED SUBGRAPH</span><small>局部上下文 · 3 步预算 · 明确出口</small></div>
        <div className="subgraph-flow">
          <div><i>1</i><span>Classify</span></div><b>→</b>
          <div><i>2</i><span>Select Tool</span></div><b>→</b>
          <div><i>3</i><span>Evaluate</span></div><b>→</b>
          <div className="subgraph-exit"><i>↳</i><span>RESOLVED<br />NEED_HUMAN</span></div>
        </div>
      </div>

      <div className={`checkpoint-node ${active === "pause" ? "is-active" : ""}`}>
        <i>Ⅱ</i><span><small>DURABLE PAUSE</small><strong>Checkpoint · Case V1 / Plan V1</strong></span>
      </div>
    </div>
  );
}

function CaseFacts({ state }: { state: CaseState }) {
  const fields = state.business_fields;
  const relation = asText(fields.relation, "UNKNOWN");
  const rawAmount = fields.display_amount ?? fields.application_amount ?? fields.loan_amount;
  const amount = typeof rawAmount === "number" ? `${(rawAmount / 10000).toLocaleString("zh-CN")} 万元` : asText(rawAmount, "280 万元");
  const facts = [
    ["申请金额", amount],
    ["贷款期限", `${asText(fields.loan_term_months, "60")} 个月`],
    ["资金用途", asText(fields.loan_purpose, "企业流动资金")],
    ["企业成立", `${asText(fields.company_age_months, "10")} 个月`],
    ["房产持有", `${asText(fields.property_holding_months, "8")} 个月`],
  ];
  return (
    <aside className="case-context-panel">
      <div className="context-heading"><span>CASE CONTEXT</span><strong>{state.case_id}</strong></div>
      <div className="case-people">
        <div><small>借款人</small><strong>{asText(state.entities.borrower?.name, "张三")}</strong></div>
        <i>≠</i>
        <div><small>抵押人</small><strong>{asText(state.entities.mortgagor?.name, "李四")}</strong></div>
      </div>
      <div className="relation-signal"><span>主体关系</span><strong className={relation === "UNKNOWN" ? "is-warning" : "is-success"}>{relation}</strong></div>
      <dl className="case-fact-list">
        {facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
      </dl>
      <div className="evidence-conflict">
        <span>FIELD CONFLICT</span>
        <div><code>身份证</code><strong>张三</strong></div>
        <div><code>户口簿 OCR</code><strong>张叁 · 0.61</strong></div>
      </div>
    </aside>
  );
}

function DecisionInspector({ step, events }: { step: number; events: AuditEvent[] }) {
  const route = findEvent(events, ["ROUTE_EVALUATED"]);
  const handoff = findEvent(events, ["HANDOFF_CREATED", "EXCEPTION_RAISED"]);
  const returned = findLastEvent(events, ["AGENT_RETURNED", "EXCEPTION_RESOLVED"]);
  const relationReviewed = findEvent(events, ["RELATION_REVIEWED"]);
  const relationRoute = [...events].reverse().find((event) => event.event_type === "ROUTE_EVALUATED" && event.payload.node === "relation_route");
  const finishedTools = events.filter((event) => event.event_type === "AGENT_TOOL_FINISHED");
  const tools = finishedTools.length ? finishedTools : events.filter((event) => ["TOOL_CALLED", "TOOL_RESULT"].includes(event.event_type));
  const current = DECISIONS[step];
  const routeDetail = (route?.payload.route ?? {}) as Record<string, unknown>;
  const handoffDetail = (handoff?.payload.handoff ?? {}) as Record<string, unknown>;
  const agentResult = (returned?.payload.agent_result ?? {}) as Record<string, unknown>;
  const auditDecision = (relationReviewed?.payload.audit_decision ?? {}) as Record<string, unknown>;
  const relationRouteDetail = (relationRoute?.payload.route ?? {}) as Record<string, unknown>;

  return (
    <aside className="decision-inspector">
      <div className="decision-inspector__head"><span>DECISION {current.index}</span><h2>{current.title}</h2><p>{current.summary}</p></div>
      {step === 0 && (
        <div className="inspector-stack">
          <div className="inspector-block"><small>INPUT</small><strong>规范化 CaseState</strong><p>主体、材料、经营期限、抵押物持有期限与资金用途。</p></div>
          <div className="inspector-block"><small>PLAN OUTPUT</small><strong>依赖感知任务图</strong><p>确定性任务直接执行；关系语义和异常恢复不预先混入规则节点。</p></div>
          <div className="boundary-tags"><span>RULE FIRST</span><span>PLAN GATE</span><span>EXPLICIT STATE</span></div>
        </div>
      )}
      {step === 1 && (
        <div className="inspector-stack">
          <dl className="route-contract">
            <div><dt>Predicate</dt><dd>{asText(routeDetail.predicate, "事件未返回")}</dd></div>
            <div><dt>Actual</dt><dd>{asText(routeDetail.actual_value, "事件未返回")}</dd></div>
            <div><dt>Selected edge</dt><dd>{asText(routeDetail.selected_edge, "事件未返回")}</dd></div>
            <div><dt>Reason code</dt><dd>{asText(routeDetail.reason_code, "事件未返回")}</dd></div>
          </dl>
          <div className="agent-return-card"><small>HANDOFF CONTRACT</small><code>{`ExceptionEnvelope {
  exception_type: ${asText(handoffDetail.exception_type, "—")},
  scoped_context: ${asText(handoffDetail.context_refs, "—")},
  exit: RESOLVED | NEED_HUMAN
}`}</code><p>异常信封通过 Schema 后才允许进入子图。</p></div>
        </div>
      )}
      {step === 2 && (
        <div className="inspector-stack">
          <div className="handoff-contract"><span>TYPED HANDOFF</span><dl>
            <div><dt>Exception</dt><dd>{asText(handoffDetail.exception_type, "事件未返回")}</dd></div>
            <div><dt>Context</dt><dd>{asText(handoffDetail.context_refs, "事件未返回")}</dd></div>
            <div><dt>Allowed tools</dt><dd>{asText(handoffDetail.allowed_tools, "事件未返回")}</dd></div>
            <div><dt>Budget</dt><dd>{asText(handoffDetail.step_budget, "—")} steps</dd></div>
          </dl></div>
          <div className="tool-loop">
            <span>CONTROLLED TOOL LOOP</span>
            {(tools.length ? tools.slice(0, 3) : [null, null, null]).map((event, index) => (
              <div key={event?.event_id ?? index}><i>{index + 1}</i><p><strong>{asText(event?.payload.tool, "等待工具事件")}</strong><small>{asText(event?.payload.observation, "尚未执行")}</small></p><b>{event ? "DONE" : "WAIT"}</b></div>
            ))}
          </div>
          <div className="agent-stop"><span>RETURN</span><strong>{asText(agentResult.status, "事件未返回")}</strong><small>{asText(agentResult.stop_reason, "—")}</small></div>
        </div>
      )}
      {step === 3 && (
        <div className="inspector-stack">
          <div className="pause-reason"><i>Ⅱ</i><span><small>AUDIT AGENT RETURN</small><strong>{asText(relationReviewed?.payload.observation, "关键外部事实仍缺失")}</strong><p>关系证明不能由模型或恢复工具推断，主图保存状态并请求补件。</p></span></div>
          <div className="agent-return-card"><small>STRUCTURED AUDIT DECISION</small><code>{`AuditDecision {
  relation: ${asText(auditDecision.relation, "—")},
  hypothesis: ${asText(auditDecision.relation_hypothesis, "—")},
  task_intents: ${asText(auditDecision.task_intents, "—")},
  write_authority: ${asText(auditDecision.write_authority, "—")}
}`}</code><p>Audit Agent 只返回判断与任务意图，主图决定暂停和写入。</p></div>
          <dl className="route-contract">
            <div><dt>Reason code</dt><dd>{asText(relationRouteDetail.reason_code, "RELATION_EVIDENCE_GAP")}</dd></div>
            <div><dt>Selected edge</dt><dd>{asText(relationRouteDetail.selected_edge, "provisional_policy_review")}</dd></div>
            <div><dt>Status</dt><dd>WAITING_HUMAN</dd></div>
            <div><dt>Resume from</dt><dd>supplement_ingest</dd></div>
          </dl>
        </div>
      )}
    </aside>
  );
}

function MilestoneRail({ step }: { step: number }) {
  return (
    <ol className="milestone-rail">
      {DECISIONS.map((item, index) => (
        <li className={index === step ? "is-active" : index < step ? "is-done" : ""} key={item.id}>
          <i>{index < step ? "✓" : item.index}</i><span><strong>{item.title}</strong><small>{index === step ? "当前停顿" : index < step ? "已完成" : "待执行"}</small></span>
        </li>
      ))}
    </ol>
  );
}

function ReplanMap() {
  const items = [
    ["KEEP", "T01 · 借款人身份", "V1 结果继续复用"],
    ["KEEP", "T02 · 抵押人与产权人", "事实未发生变化"],
    ["RERUN", "T03 · 主体关系", "UNKNOWN → SPOUSE"],
    ["RESOLVED", "T04 · 关系证明", "新材料直接满足"],
    ["INVALIDATED", "T05 · 制度审核", "旧结论依赖 UNKNOWN"],
    ["ADD", "T06 · 配偶身份", "由 SPOUSE 关系触发"],
    ["ADD", "T07 · 同意抵押", "由处分权边界触发"],
    ["ADD", "T12 · 融资成本明示", "2026-08-01 新规触发"],
  ];
  return <div className="replan-map">{items.map(([status, task, reason]) => <div className={`replan-item replan-item--${status.toLowerCase()}`} key={`${status}-${task}`}><span>{status}</span><strong>{task}</strong><small>{reason}</small></div>)}</div>;
}

function RagProof({ trace }: { trace: RagTrace | null }) {
  if (!trace) return <div className="rag-empty">恢复执行后生成真实检索 Trace，不使用前端备用结果。</div>;
  const candidates = trace.candidates ?? [];
  return (
    <section className="rag-proof">
      <div className="proof-heading"><div><span>POLICY APPLICABILITY GATE</span><h3>召回只是候选，适用性决定是否落入计划</h3></div><div className="strategy-chip">{trace.retrieval?.strategy ?? "DENSE + BM25 + RRF"}</div></div>
      <div className="rag-query-flow"><div><small>CASE QUESTION</small><p>{trace.original_query}</p></div><i>→</i><div><small>QUERY REWRITE</small><p>{trace.rewritten_query}</p></div></div>
      <div className="policy-candidates">
        {candidates.slice(0, 4).map((candidate) => {
          const valid = candidate.valid ?? candidate.eligible ?? false;
          const reasonLabels: Record<string, string> = {
            VERSION_INACTIVE: "版本状态已停用",
            EXPIRED: "案例日期已超过有效期",
            NOT_YET_EFFECTIVE: "案例日期早于生效日",
            PRODUCT_MISMATCH: "产品范围不匹配",
            VERSION_SUPERSEDED: "已被新版本替代",
          };
          const reason = candidate.reason ?? candidate.filter_reason ?? (valid ? "产品、状态与生效日期均匹配" : "已被适用性门禁排除");
          return <div className={valid ? "is-selected" : "is-rejected"} key={candidate.rule_id}>
            <span>{valid ? "SELECT" : "FILTER"}</span>
            <p><strong>{candidate.rule_id}</strong><small>{candidate.title} · V{candidate.version} · {candidate.effective_date ?? "—"}</small>{candidate.source_url && <a href={candidate.source_url} target="_blank" rel="noreferrer">查看官方来源 ↗</a>}</p>
            <b>{candidate.rrf_score ? candidate.rrf_score.toFixed(4) : candidate.score?.toFixed(2) ?? "—"}</b>
            <em>{reason.split("；").map((item) => reasonLabels[item] ?? item).join("；")}</em>
          </div>;
        })}
      </div>
      <div className="grounding-chain"><div><small>CONCLUSION</small><strong>{trace.clause ?? "已根据案例日期确定适用规则"}</strong></div><i>→</i><div><small>EVIDENCE</small><strong>{trace.final_evidence_id ?? "E-RULE"}</strong></div><i>→</i><div><small>RULE</small><strong>{trace.final_rule}</strong></div></div>
    </section>
  );
}

export default function AuditWorkbench() {
  const [act, setAct] = useState<Act>(1);
  const [caseState, setCaseState] = useState<CaseState | null>(null);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [ragTrace, setRagTrace] = useState<RagTrace | null>(null);
  const [decisionStep, setDecisionStep] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [supplemented, setSupplemented] = useState(false);
  const [executionMode, setExecutionMode] = useState<ExecutionMode | null>(null);

  const completedTaskCount = useMemo(() => caseState?.audit_plan.filter((task) => ["SUCCESS", "RESOLVED", "KEEP"].includes(task.status)).length ?? 0, [caseState]);
  const controls = useMemo(() => {
    const value = caseState?.business_fields.controls;
    return Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object") : [];
  }, [caseState]);

  async function startExecution() {
    setBusy(true);
    setError("");
    const isLocalHost = ["localhost", "127.0.0.1"].includes(window.location.hostname);
    if (!HAS_CONFIGURED_API && !isLocalHost) {
      setCaseState(createRecordedWaitingState());
      setEvents(createRecordedEvents());
      setDecisionStep(0);
      setSupplemented(false);
      setRagTrace(null);
      setExecutionMode("RECORDED_GRAPH_TRACE");
      setAct(2);
      setBusy(false);
      window.scrollTo({ top: 0, behavior: "smooth" });
      return;
    }

    setExecutionMode("LOCAL_GRAPH_RUNTIME");
    try {
      const created = await request<CaseState>("/api/cases/demo/architecture_demo", { method: "POST" });
      const result = await request<CaseState>(`/api/cases/${created.case_id}/run`, { method: "POST" });
      const runtimeEvents = await request<AuditEvent[]>(`/api/cases/${created.case_id}/events`);
      setCaseState(result);
      setEvents(runtimeEvents);
      setDecisionStep(0);
      setSupplemented(false);
      setRagTrace(null);
      setAct(2);
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch {
      setError("审核执行服务未连接。请启动本地后端后重试。");
    } finally {
      setBusy(false);
    }
  }

  async function resumeWithSupplement() {
    if (!caseState) return;
    setBusy(true);
    setError("");
    if (executionMode === "RECORDED_GRAPH_TRACE") {
      setCaseState(createRecordedFinalState());
      setEvents(createRecordedFinalEvents());
      setRagTrace(createRecordedRagTrace());
      setSupplemented(true);
      setBusy(false);
      return;
    }

    try {
      const result = await request<CaseState>(`/api/cases/${caseState.case_id}/resume`, {
        method: "POST",
        body: JSON.stringify({
          event_id: `SUP-${Date.now()}`,
          marriage_certificate: { husband: "张三", wife: "李四", registered_at: "2022-06-18" },
          coowner_consent: { signer: "李四", signed_at: "2026-08-14" },
        }),
      });
      const [runtimeEvents, trace] = await Promise.all([
        request<AuditEvent[]>(`/api/cases/${caseState.case_id}/events`),
        request<RagTrace>(`/api/cases/${caseState.case_id}/rag-trace`),
      ]);
      setCaseState(result);
      setEvents(runtimeEvents);
      setRagTrace(trace);
      setSupplemented(true);
    } catch {
      setError("补件恢复失败，请检查后端状态后重试。");
    } finally {
      setBusy(false);
    }
  }

  function resetDemo() {
    setAct(1);
    setCaseState(null);
    setEvents([]);
    setRagTrace(null);
    setDecisionStep(0);
    setSupplemented(false);
    setExecutionMode(null);
    setError("");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function nextDecision() {
    if (decisionStep < DECISIONS.length - 1) setDecisionStep((current) => current + 1);
    else {
      setAct(3);
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  }

  return (
    <main className="product-shell">
      <header className="product-header">
        <button className="product-brand" onClick={resetDemo} aria-label="重新开始演示">
          <span className="brand-mark"><i /><i /><i /></span>
          <span><strong>ARGUS</strong><small>住宅抵押经营贷 · 智能审核</small></span>
        </button>
        <ChapterRail act={act} />
        <div className="header-case"><i /><span><small>{executionMode ? `SYNTHETIC · ${executionMode}` : "SYNTHETIC CASE"}</small><strong>{caseState?.case_id ?? "CASE-ZD-042"}</strong></span></div>
      </header>

      {error && <div className="error-banner" role="alert">{error}<button onClick={() => setError("")} aria-label="关闭提示">×</button></div>}

      {act === 1 && <ArchitectureEvolution onStartDemo={startExecution} busy={busy} />}

      {act === 2 && caseState && (
        <section className="act-page execution-act">
          <div className="act-intro">
            <div><span>ACT II · RUNTIME COLLABORATION</span><h1>不是节点亮灯，<br />而是控制权如何交接。</h1></div>
            <p>同一个宅抵贷 Case 停在四个关键瞬间。每一步都只回答三件事：为什么路由、交给谁、改变了什么状态。</p>
          </div>
          <div className={`runtime-disclosure ${executionMode === "RECORDED_GRAPH_TRACE" ? "is-recorded" : "is-local"}`}>
            <strong>{executionMode}</strong>
            <span>{executionMode === "RECORDED_GRAPH_TRACE" ? "托管页面正在回放与本地 LangGraph 后端同契约的固化事件；不代表当前正在调用后端。" : "当前事件来自本机 Python / LangGraph 运行时与 SQLite Event Store。"}</span>
          </div>

          <div className="runtime-layout">
            <CaseFacts state={caseState} />
            <RuntimeGraph step={decisionStep} />
            <DecisionInspector step={decisionStep} events={events} />
          </div>

          <MilestoneRail step={decisionStep} />

          <div className="act-controls">
            <button className="button-secondary" onClick={() => decisionStep > 0 ? setDecisionStep((current) => current - 1) : setAct(1)}>← 上一步</button>
            <div><small>{decisionStep + 1} / {DECISIONS.length}</small><span>由你控制讲解节奏</span></div>
            <button className="button-primary" onClick={nextDecision}>{DECISIONS[decisionStep].action}<i>→</i></button>
          </div>
        </section>
      )}

      {act === 3 && caseState && (
        <section className="act-page closure-act">
          <div className="act-intro">
            <div><span>ACT III · STATE &amp; EVIDENCE</span><h1>补件不是终点，<br />它会重写后续计划。</h1></div>
            <p>新事实进入后先对账，再沿任务依赖传播影响。只有受影响的结果会失效、重跑或新增，最终结论必须绑定材料和适用规则。</p>
          </div>
          <div className={`runtime-disclosure ${executionMode === "RECORDED_GRAPH_TRACE" ? "is-recorded" : "is-local"}`}>
            <strong>{executionMode}</strong>
            <span>{executionMode === "RECORDED_GRAPH_TRACE" ? "以下补件、重规划与 Grounding 为已记录 Graph Trace；字段、事件与本地真实执行使用同一契约。" : "以下状态变化由本机后端恢复同一 Case Checkpoint 后生成。"}</span>
          </div>

          <section className={`checkpoint-resume ${supplemented ? "is-resolved" : ""}`}>
            <div className="checkpoint-identity"><i>{supplemented ? "✓" : "Ⅱ"}</i><span><small>{supplemented ? "RESUMED" : "DURABLE CHECKPOINT"}</small><strong>{supplemented ? "主图已从 Supplement Ingest 恢复" : "Case V1 / Plan V1 已持久化暂停"}</strong></span></div>
            <div className="supplement-file"><span className="file-icon">PDF</span><p><strong>婚姻关系证明.pdf</strong><small>张三 · 李四 · 登记日期 2022-06-18</small></p><b>{supplemented ? "VERIFIED" : "READY"}</b></div>
            <button className="button-primary" onClick={resumeWithSupplement} disabled={busy || supplemented}>{busy ? "正在恢复主图…" : supplemented ? "补件已处理" : "补充材料并恢复"}<i>→</i></button>
          </section>

          {!supplemented ? (
            <div className="closure-waiting">
              <span>RESUME CONTRACT</span>
              <h2>新材料不会直接覆盖旧结论</h2>
              <div><code>Supplement Ingest</code><i>→</i><code>Reconcile</code><i>→</i><code>Impact Analysis</code><i>→</i><code>Selective Replan</code></div>
            </div>
          ) : (
            <>
              <section className="state-reconciliation">
                <div className="proof-heading"><div><span>STATE RECONCILIATION</span><h2>Case V1 → V2，Plan V1 → V2</h2></div><div className="reuse-summary"><strong>2</strong><span>个结果复用</span><i /><strong>3</strong><span>个任务局部重算</span></div></div>
                <div className="state-diff-row">
                  <div><small>DOCUMENT</small><del>关系证明缺失</del><i>→</i><ins>已验证</ins></div>
                  <div><small>RELATION</small><del>UNKNOWN</del><i>→</i><ins>SPOUSE</ins></div>
                  <div><small>CASE VERSION</small><del>V1</del><i>→</i><ins>V{caseState.case_version}</ins></div>
                  <div><small>PLAN VERSION</small><del>V1</del><i>→</i><ins>V{caseState.plan_version}</ins></div>
                </div>
                <ReplanMap />
              </section>

              <RagProof trace={ragTrace} />

              <section className="control-measures">
                <div className="proof-heading"><div><span>DECISION CONTROLS</span><h2>不是简单通过，而是带约束的执行决定</h2></div><div className="strategy-chip">{asText(caseState.business_fields.final_decision, "PASS_WITH_CONTROLS")}</div></div>
                <div className="control-measure-grid">
                  {controls.map((control, index) => <div key={asText(control.control_id, String(index))}><i>{String(index + 1).padStart(2, "0")}</i><span><small>{asText(control.code, "CONTROL")}</small><strong>{asText(control.title, "审核控制措施")}</strong></span><b>{asText(control.status, "REQUIRED")}</b></div>)}
                </div>
              </section>

              <section className="final-validation">
                <div><span>FINAL VALIDATOR</span><h2>结论必须同时通过任务、证据与规则契约</h2><p>{completedTaskCount} 个任务已形成结构化结果；最终状态由 Validator 决定，而不是由 Agent 直接输出。</p></div>
                <ul>
                  <li><i>✓</i><span><strong>Task Contract</strong><small>必需任务无 PENDING / DIRTY</small></span></li>
                  <li><i>✓</i><span><strong>Evidence Contract</strong><small>结论引用有效 Evidence ID</small></span></li>
                  <li><i>✓</i><span><strong>Policy Contract</strong><small>Rule ID 与案例日期匹配</small></span></li>
                  <li><i>✓</i><span><strong>Exception Contract</strong><small>不存在未闭合异常</small></span></li>
                </ul>
                <div className="final-outcome"><small>FINAL DECISION</small><strong>{asText(caseState.business_fields.final_decision, caseState.status)}</strong><StatusPill value={caseState.status} /></div>
              </section>
            </>
          )}

          <div className="act-controls">
            <button className="button-secondary" onClick={() => { setAct(2); setDecisionStep(3); }}>← 返回执行</button>
            <div><small>CASE {caseState.case_version} · PLAN {caseState.plan_version}</small><span>{events.length} 条底层事件已收敛为关键决策</span></div>
            <button className="button-primary" onClick={resetDemo}>重新演示<i>↻</i></button>
          </div>
        </section>
      )}
    </main>
  );
}
