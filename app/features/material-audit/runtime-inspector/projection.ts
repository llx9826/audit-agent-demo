import type { AuditEvent } from "../api/contracts";

export interface WorkflowProjection {
  latestNode: string | null;
  latestTaskId: string | null;
  latestEvent: AuditEvent | null;
}

export interface AuditDecisionProjection {
  candidates: Array<Record<string, unknown>>;
  issue: Record<string, unknown> | null;
  decision: Record<string, unknown> | null;
  gate: Record<string, unknown> | null;
}

export interface AssociationProjection {
  selectedPageCount: number;
  extractedPageCount: number;
  mentionCount: number;
  roleSignalCount: number;
  candidateCount: number;
  candidateTypes: string[];
  decision: Record<string, unknown> | null;
  gate: Record<string, unknown> | null;
  modelRoute: ModelRouteProjection | null;
}

export interface ModelRouteProjection {
  route: string;
  selectedEndpoint: string | null;
  attempts: Array<{
    endpoint: string;
    status: string;
    errorCode: string | null;
    latencyMs: number | null;
  }>;
}

export type SpineStatus = "waiting" | "active" | "done" | "failed";

export interface CheckpointSpineStep {
  id: string;
  label: string;
  status: SpineStatus;
  event: AuditEvent | null;
  detail: string;
}

export interface ReplanProjection {
  changedFacts: string[];
  impactedTaskIds: string[];
  reusedTaskIds: string[];
  invalidatedTaskIds: string[];
  decisions: ReplanDecisionProjection[];
  checkpointId: string | null;
  pausedStatus: string | null;
  beforePlanVersion: number | null;
  planVersion: number | null;
}

export interface ReplanDecisionProjection {
  taskId: string;
  operation: "KEEP" | "RERUN";
  before: string | null;
  after: string | null;
  matchedChangedFacts: string[];
  matchedFactDependencies: string[];
  beforeResultVersion: number | null;
  afterResultVersion: number | null;
}

export interface ReturnJourneyProjection {
  origin: string | null;
  returnTarget: string | null;
  sourceTaskId: string | null;
  exceptionType: string | null;
  pageIds: string[];
  route: string | null;
  accepted: boolean | null;
}

export interface TaskOrchestrationProjection {
  dispatchId: string | null;
  readyTaskIds: string[];
  completedTaskIds: string[];
  committedTaskIds: string[];
  rejectedTaskIds: string[];
  workerOutcomes: Record<string, { status: string; resultVersion: number | null }>;
}

export interface ExceptionStepProjection {
  step: number;
  decision?: Record<string, unknown>;
  gate?: Record<string, unknown>;
  tool?: string;
  observation?: Record<string, unknown>;
}

export interface CandidateRoundProjection {
  step: number;
  enabled: string[];
  blocked: Record<string, string>;
  remainingBudget: number | null;
}

export interface ExceptionTraceProjection {
  exceptionType: string | null;
  candidateTools: string[];
  blockedTools: Record<string, string>;
  candidateRounds: CandidateRoundProjection[];
  stepBudget: number | null;
  remainingBudget: number | null;
  steps: ExceptionStepProjection[];
  completion: Record<string, unknown> | null;
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

/** Custom LangGraph events keep their typed body under payload.payload. */
export function eventBody(event: AuditEvent): Record<string, unknown> {
  return record(event.payload.payload) ?? record(event.payload.observation) ?? {};
}

export function projectRuntime(events: AuditEvent[]): {
  workflow: WorkflowProjection;
  association: AssociationProjection;
  audit: AuditDecisionProjection;
  exception: ExceptionTraceProjection;
  checkpointSpine: CheckpointSpineStep[];
  replan: ReplanProjection;
  returnJourney: ReturnJourneyProjection;
  taskOrchestration: TaskOrchestrationProjection;
} {
  const association: AssociationProjection = {
    selectedPageCount: 0, extractedPageCount: 0, mentionCount: 0, roleSignalCount: 0,
    candidateCount: 0, candidateTypes: [], decision: null, gate: null, modelRoute: null,
  };
  const audit: AuditDecisionProjection = { candidates: [], issue: null, decision: null, gate: null };
  const exception: ExceptionTraceProjection = {
    exceptionType: null, candidateTools: [], blockedTools: {}, candidateRounds: [],
    stepBudget: null, remainingBudget: null, steps: [], completion: null,
  };
  const byStep = new Map<number, ExceptionStepProjection>();
  const eventByType = new Map<string, AuditEvent>();
  const replan: ReplanProjection = {
    changedFacts: [], impactedTaskIds: [], reusedTaskIds: [], invalidatedTaskIds: [], decisions: [],
    checkpointId: null, pausedStatus: null, beforePlanVersion: null, planVersion: null,
  };
  const returnJourney: ReturnJourneyProjection = {
    origin: null, returnTarget: null, sourceTaskId: null, exceptionType: null,
    pageIds: [], route: null, accepted: null,
  };
  const taskOrchestration: TaskOrchestrationProjection = {
    dispatchId: null, readyTaskIds: [], completedTaskIds: [], committedTaskIds: [], rejectedTaskIds: [],
    workerOutcomes: {},
  };

  for (const event of events) {
    const body = eventBody(event);
    const modelTrace = record(event.payload.model_trace);
    if (modelTrace?.route === "association") {
      const attempts = Array.isArray(modelTrace.attempts) ? modelTrace.attempts : [];
      association.modelRoute = {
        route: "association",
        selectedEndpoint: typeof modelTrace.selected_endpoint === "string" ? modelTrace.selected_endpoint : null,
        attempts: attempts.flatMap((item) => {
          const attempt = record(item);
          return attempt ? [{
            endpoint: String(attempt.endpoint ?? "unknown"),
            status: String(attempt.status ?? "UNKNOWN"),
            errorCode: typeof attempt.error_code === "string" ? attempt.error_code : null,
            latencyMs: typeof attempt.latency_ms === "number" ? attempt.latency_ms : null,
          }] : [];
        }),
      };
    }
    eventByType.set(event.event_type, event);
    if (event.event_type === "CHECKPOINT_FOUND" || event.event_type === "INTERRUPTED_STATE_LOADED") {
      replan.checkpointId = typeof body.checkpoint_id === "string" ? body.checkpoint_id : replan.checkpointId;
      replan.pausedStatus = typeof body.status === "string" ? body.status : replan.pausedStatus;
      replan.beforePlanVersion = typeof body.plan_version === "number" ? body.plan_version : replan.beforePlanVersion;
    }
    if (event.event_type === "EXCEPTION_HANDOFF_PREPARED") {
      returnJourney.origin = typeof body.origin === "string" ? body.origin : null;
      returnJourney.returnTarget = typeof body.return_target === "string" ? body.return_target : null;
      returnJourney.sourceTaskId = typeof body.source_task_id === "string" ? body.source_task_id : null;
      returnJourney.exceptionType = typeof body.exception_type === "string" ? body.exception_type : null;
      returnJourney.pageIds = Array.isArray(body.page_ids) ? body.page_ids.map(String) : [];
      returnJourney.route = null;
      returnJourney.accepted = null;
    } else if (event.event_type === "EXCEPTION_RESULT_GATE_EVALUATED") {
      returnJourney.origin = typeof body.origin === "string" ? body.origin : returnJourney.origin;
      returnJourney.returnTarget = typeof body.return_target === "string" ? body.return_target : returnJourney.returnTarget;
      returnJourney.sourceTaskId = event.payload.task_id ?? returnJourney.sourceTaskId;
      returnJourney.route = typeof body.route === "string" ? body.route : null;
      returnJourney.accepted = typeof body.accepted === "boolean" ? body.accepted : null;
    }
    if (event.event_type === "READY_TASKS_DISPATCHED") {
      taskOrchestration.dispatchId = typeof body.dispatch_id === "string" ? body.dispatch_id : null;
      taskOrchestration.readyTaskIds = Array.isArray(body.ready_task_ids) ? body.ready_task_ids.map(String) : [];
      taskOrchestration.completedTaskIds = [];
      taskOrchestration.committedTaskIds = [];
      taskOrchestration.rejectedTaskIds = [];
      taskOrchestration.workerOutcomes = {};
    } else if (event.event_type === "TASK_WORKER_COMPLETED" && typeof event.payload.task_id === "string") {
      taskOrchestration.completedTaskIds.push(event.payload.task_id);
      taskOrchestration.workerOutcomes[event.payload.task_id] = {
        status: String(body.status ?? "UNKNOWN"),
        resultVersion: typeof body.result_version === "number" ? body.result_version : null,
      };
    } else if (event.event_type === "TASK_FAN_IN_COMMITTED") {
      taskOrchestration.committedTaskIds = Array.isArray(body.committed_task_ids) ? body.committed_task_ids.map(String) : [];
      taskOrchestration.rejectedTaskIds = Array.isArray(body.rejected_task_ids) ? body.rejected_task_ids.map(String) : [];
    }
    if (event.event_type === "ASSOCIATION_PAGES_SELECTED") {
      association.selectedPageCount = typeof body.selected_count === "number" ? body.selected_count : 0;
      association.extractedPageCount = 0;
    } else if (event.event_type === "ASSOCIATION_PAGE_EVIDENCE_EXTRACTED") {
      association.extractedPageCount += 1;
    } else if (event.event_type === "ASSOCIATION_EVIDENCE_EXTRACTED") {
      association.mentionCount = typeof body.mention_count === "number" ? body.mention_count : 0;
      association.roleSignalCount = typeof body.role_signal_count === "number" ? body.role_signal_count : 0;
    } else if (event.event_type === "ASSOCIATION_CANDIDATES_BUILT") {
      association.candidateCount = typeof body.candidate_count === "number" ? body.candidate_count : 0;
      association.candidateTypes = Array.isArray(body.candidate_types) ? body.candidate_types.map(String) : [];
    } else if (event.event_type === "ASSOCIATION_DECISION_PROPOSED") {
      association.decision = body;
    } else if (event.event_type === "ASSOCIATION_GATE_EVALUATED") {
      association.gate = body;
    } else if (event.event_type === "AUDIT_CANDIDATES_BUILT") {
      audit.issue = record(body.issue);
      audit.candidates = Array.isArray(body.candidates) ? body.candidates.filter(record) as Array<Record<string, unknown>> : [];
    } else if (event.event_type === "AUDIT_DECISION_PROPOSED") {
      audit.decision = body;
    } else if (event.event_type === "AUDIT_PLAN_GATE_EVALUATED") {
      audit.gate = body;
    } else if (event.event_type === "EXCEPTION_CANDIDATES_BUILT") {
      exception.exceptionType = typeof body.exception_type === "string" ? body.exception_type : null;
      exception.candidateTools = Array.isArray(body.candidate_tools) ? body.candidate_tools.map(String) : [];
      exception.blockedTools = Object.fromEntries(
        Object.entries(record(body.blocked_tools) ?? {}).map(([tool, reason]) => [tool, String(reason)]),
      );
      exception.stepBudget = typeof body.step_budget === "number" ? body.step_budget : null;
      exception.remainingBudget = typeof body.remaining_budget === "number" ? body.remaining_budget : null;
      exception.candidateRounds.push({
        step: typeof body.step === "number" ? body.step : exception.candidateRounds.length + 1,
        enabled: [...exception.candidateTools],
        blocked: { ...exception.blockedTools },
        remainingBudget: exception.remainingBudget,
      });
    } else if (event.event_type.startsWith("EXCEPTION_") || event.event_type === "TOOL_STARTED" || event.event_type === "COMPLETION_EVALUATED") {
      const step = typeof body.step === "number" ? body.step : 0;
      if (step > 0) {
        const projected = byStep.get(step) ?? { step };
        if (event.event_type === "EXCEPTION_DECISION_MADE") projected.decision = body;
        if (event.event_type === "EXCEPTION_TOOL_GATE_EVALUATED") projected.gate = body;
        if (event.event_type === "TOOL_STARTED") projected.tool = typeof body.tool === "string" ? body.tool : undefined;
        if (event.event_type === "EXCEPTION_TOOL_OBSERVED") projected.observation = body;
        byStep.set(step, projected);
      }
      if (event.event_type === "COMPLETION_EVALUATED") exception.completion = body;
    }
    if (event.event_type === "FACTS_CHANGED") {
      // 一个线程可能跨天多次补件。每次 FACTS_CHANGED 开启新的 Replan 窗口，
      // 右栏只解释当前这轮依赖影响，不把历史决策混到一起。
      replan.impactedTaskIds = [];
      replan.reusedTaskIds = [];
      replan.invalidatedTaskIds = [];
      replan.decisions = [];
      replan.planVersion = null;
      replan.changedFacts = Array.isArray(body.changed_facts) ? body.changed_facts.map(String) : [];
    } else if (event.event_type === "IMPACT_ANALYSIS_COMPLETED") {
      replan.impactedTaskIds = Array.isArray(body.impacted_task_ids) ? body.impacted_task_ids.map(String) : [];
    } else if (event.event_type === "TASK_RESULT_REUSED" && typeof body.task_id === "string") {
      replan.reusedTaskIds.push(body.task_id);
      replan.decisions.push(replanDecision(body, "KEEP"));
    } else if (event.event_type === "TASK_RESULT_INVALIDATED" && typeof body.task_id === "string") {
      replan.invalidatedTaskIds.push(body.task_id);
      replan.decisions.push(replanDecision(body, "RERUN"));
    } else if (event.event_type === "PLAN_VERSION_COMMITTED") {
      replan.planVersion = typeof body.plan_version === "number" ? body.plan_version : null;
    }
  }
  exception.steps = [...byStep.values()].sort((left, right) => left.step - right.step);
  const latestEvent = events.at(-1) ?? null;
  const spineDefinitions = [
    { id: "lookup", label: "查找 Checkpoint", types: ["CHECKPOINT_LOOKUP_STARTED", "CHECKPOINT_FOUND"] },
    { id: "load", label: "加载暂停状态", types: ["INTERRUPTED_STATE_LOADED"] },
    { id: "resume", label: "恢复同一 Thread", types: ["RESUME_COMMAND_ACCEPTED", "CHECKPOINT_RESUMED"] },
    { id: "reconcile", label: "状态对账", types: ["STATE_RECONCILIATION_STARTED", "STATE_RECONCILIATION_COMPLETED"] },
    { id: "impact", label: "依赖影响分析", types: ["IMPACT_ANALYSIS_COMPLETED"] },
    { id: "replan", label: "选择性 Replan", types: ["SELECTIVE_REPLAN_COMPLETED"] },
    { id: "dispatch", label: "重跑 Dirty Task", types: ["READY_TASKS_DISPATCHED", "PLAN_VERSION_COMMITTED"] },
  ];
  const reached = spineDefinitions.map((definition) => {
    const matching = definition.types.map((type) => eventByType.get(type)).filter(Boolean) as AuditEvent[];
    return matching.at(-1) ?? null;
  });
  const lastReached = reached.reduce((last, event, index) => event ? index : last, -1);
  const checkpointSpine = spineDefinitions.map((definition, index): CheckpointSpineStep => {
    const event = reached[index];
    const body = event ? eventBody(event) : {};
    const observation = record(body);
    const checkpointId = typeof observation?.checkpoint_id === "string" ? observation.checkpoint_id : null;
    const taskIds = Array.isArray(observation?.task_ids) ? observation.task_ids.length : null;
    const impactedTaskIds = Array.isArray(observation?.impacted_task_ids) ? observation.impacted_task_ids.length : null;
    const dirtyTaskIds = Array.isArray(observation?.dirty_task_ids) ? observation.dirty_task_ids.length : null;
    const reusedCount = typeof observation?.reused_count === "number" ? observation.reused_count : null;
    const changedFactCount = typeof observation?.changed_fact_count === "number" ? observation.changed_fact_count : null;
    const committedPlanVersion = typeof observation?.plan_version === "number" ? observation.plan_version : null;
    const detail = checkpointId
      ?? (impactedTaskIds !== null ? `${impactedTaskIds} 个 Task 受影响` : null)
      ?? (dirtyTaskIds !== null ? `${dirtyTaskIds} 个重跑 / ${reusedCount ?? 0} 个复用` : null)
      ?? (changedFactCount !== null ? `${changedFactCount} 项事实变化` : null)
      ?? (taskIds !== null ? `${taskIds} 个 Task` : null)
      ?? (committedPlanVersion !== null ? `计划 P${committedPlanVersion} 已提交` : null)
      ?? event?.payload.task_id
      ?? "事件已完成";
    return {
      id: definition.id,
      label: definition.label,
      status: event ? (index === lastReached && index < spineDefinitions.length - 1 ? "active" : "done") : "waiting",
      event,
      detail,
    };
  });
  return {
    workflow: {
      latestNode: latestEvent?.payload.node ?? null,
      latestTaskId: latestEvent?.payload.task_id ?? null,
      latestEvent,
    },
    association,
    audit,
    exception,
    checkpointSpine,
    replan,
    returnJourney,
    taskOrchestration,
  };
}

function replanDecision(
  body: Record<string, unknown>,
  operation: "KEEP" | "RERUN",
): ReplanDecisionProjection {
  return {
    taskId: String(body.task_id),
    operation,
    before: typeof body.before === "string" ? body.before : null,
    after: typeof body.after === "string" ? body.after : null,
    matchedChangedFacts: Array.isArray(body.matched_changed_facts)
      ? body.matched_changed_facts.map(String)
      : [],
    matchedFactDependencies: Array.isArray(body.matched_fact_dependencies)
      ? body.matched_fact_dependencies.map(String)
      : [],
    beforeResultVersion: typeof body.before_result_version === "number" ? body.before_result_version : null,
    afterResultVersion: typeof body.after_result_version === "number" ? body.after_result_version : null,
  };
}
