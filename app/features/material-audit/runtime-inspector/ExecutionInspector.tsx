"use client";

import {
  Bot,
  Check,
  CheckCircle2,
  Circle,
  CornerUpLeft,
  GitBranch,
  ListChecks,
  RotateCcw,
  ScanSearch,
  ShieldCheck,
  UserRound,
  Workflow,
  Wrench,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { AuditEvent, CaseState, RequiredMaterialTask } from "../api/contracts";
import {
  actionLabels,
  executionGroupLabels,
  eventLabels,
  labelOf,
  nodeLabels,
  statusLabels,
} from "../presentation/labels";
import { presentTask } from "../presentation/task-presentation";
import {
  presentAction,
  presentExceptionType,
  presentFact,
  presentGateOutcome,
  presentMaterial,
  presentReason,
  presentTool,
} from "../presentation/trace-presentation";
import { projectRuntime, type ReplanDecisionProjection } from "./projection";

const gateCheckLabels: Record<string, string> = {
  assignment_is_current: "Assignment 版本为最新",
  candidate_membership: "关联候选未越界",
  role_people_are_confirmed: "角色人员已经确认",
  owner_people_are_confirmed: "材料所属人已经确认",
  owner_pages_are_scoped: "材料页范围合法",
  evidence_is_bounded: "Evidence 引用未越界",
  allowed_action: "结构化动作被允许",
  candidate_is_scoped: "材料候选属于当前 Task",
};

function value(input: unknown): string {
  if (typeof input === "string" && input) return input;
  if (typeof input === "number" || typeof input === "boolean") return String(input);
  return "—";
}

function taskById(state: CaseState, taskId: string | null): RequiredMaterialTask | null {
  if (!taskId) return null;
  return (state.audit_plan ?? []).find((task) => task.task_id === taskId) ?? null;
}

function GateChecks({ checks }: { checks: unknown }) {
  if (!checks || typeof checks !== "object" || Array.isArray(checks)) return null;
  const entries = Object.entries(checks as Record<string, unknown>);
  if (!entries.length) return null;
  return (
    <div className="gate-check-grid" aria-label="校验门检查项">
      {entries.map(([name, passed]) => (
        <span className={passed === true ? "is-pass" : "is-block"} key={name}>
          {passed === true ? <Check /> : <Circle />}
          {gateCheckLabels[name] ?? name}
        </span>
      ))}
    </div>
  );
}

function TaskLedger({ state, tasks, selectedTask, onSelectTask }: {
  state: CaseState;
  tasks: RequiredMaterialTask[];
  selectedTask: RequiredMaterialTask | null;
  onSelectTask?: (task: RequiredMaterialTask) => void;
}) {
  return (
    <section className="runtime-ledger">
      <header><ListChecks /><strong>审核任务账本</strong><small>{tasks.length} 个可追溯 Task</small></header>
      <div className="runtime-task-list">
        {tasks.map((task) => {
          const view = presentTask(state, task);
          return (
            <button
              type="button"
              className={selectedTask?.task_id === task.task_id ? "is-selected" : ""}
              key={task.task_id}
              onClick={() => onSelectTask?.(task)}
            >
              <span className={`task-ledger-dot is-${task.status.toLowerCase()}`} />
              <span>
                <strong className="runtime-task-title">{view.title}</strong>
                <small>{view.personLabel} · {view.requirementTitle}</small>
                <code>{view.technicalId}</code>
              </span>
              <Badge variant={task.status === "MATCHED" ? "secondary" : "outline"}>{view.statusLabel}</Badge>
            </button>
          );
        })}
      </div>
    </section>
  );
}

function SelectedTaskCard({ state, task }: { state: CaseState; task: RequiredMaterialTask | null }) {
  if (!task) return null;
  const view = presentTask(state, task);
  return (
    <Card className="runtime-task-detail">
      <CardHeader>
        <CardTitle>当前选中任务</CardTitle>
        <Badge variant="outline">{view.statusLabel}</Badge>
      </CardHeader>
      <CardContent>
        <strong className="runtime-task-title">{view.title}</strong>
        <span className="technical-id">{view.technicalId}</span>
        <dl>
          <div><dt>应交项</dt><dd>{view.requirementTitle}</dd></div>
          <div><dt>执行者</dt><dd>{view.executorLabel}</dd></div>
          <div><dt>并发组</dt><dd>{labelOf(executionGroupLabels, task.execution_group, "材料匹配并发组")}</dd></div>
          <div><dt>事实依赖</dt><dd>{task.fact_dependencies?.map(presentFact).join(" / ") || task.depends_on?.map(presentFact).join(" / ") || "无"}</dd></div>
          <div><dt>Task 依赖</dt><dd>{task.task_dependencies?.join(" / ") || "无，可由 Send 并行执行"}</dd></div>
          <div><dt>结果版本</dt><dd>R{task.result_version ?? 0} · C{task.result?.case_version ?? "—"} / P{task.result?.plan_version ?? "—"}</dd></div>
          <div><dt>Evidence</dt><dd>{task.evidence_refs?.length ? task.evidence_refs.join(" / ") : "等待绑定材料 Evidence"}</dd></div>
        </dl>
      </CardContent>
    </Card>
  );
}

function ReturnJourney({ state, journey }: {
  state: CaseState;
  journey: ReturnType<typeof projectRuntime>["returnJourney"];
}) {
  if (!journey.origin && !journey.returnTarget) return null;
  const sourceTask = taskById(state, journey.sourceTaskId);
  const view = sourceTask ? presentTask(state, sourceTask) : null;
  return (
    <section className="return-journey">
      <header><CornerUpLeft /><strong>异常取证精确回程</strong><small>Typed Handoff</small></header>
      <div className="return-route">
        <span><small>原任务</small><strong>{view?.title ?? nodeLabels[journey.origin ?? ""] ?? value(journey.origin)}</strong></span>
        <i><ScanSearch /><b>独立 Context Tool Loop</b></i>
        <span><small>Return Target</small><strong>{nodeLabels[journey.returnTarget ?? ""] ?? value(journey.returnTarget)}</strong></span>
      </div>
      <dl>
        <div><dt>异常类型</dt><dd>{presentExceptionType(journey.exceptionType)}</dd></div>
        <div><dt>页范围</dt><dd>{journey.pageIds.join(" / ") || "由原任务限定"}</dd></div>
        <div><dt>结果校验</dt><dd>{journey.accepted === null ? "等待恢复结果" : journey.accepted ? "通过，返回原任务" : "未通过，转人工介入"}</dd></div>
        <div><dt>Workflow 路由</dt><dd>{presentGateOutcome(journey.route)}</dd></div>
      </dl>
    </section>
  );
}

function ReplanDecision({ state, decision, workerOutcome }: {
  state: CaseState;
  decision: ReplanDecisionProjection;
  workerOutcome?: { status: string; resultVersion: number | null };
}) {
  const task = taskById(state, decision.taskId);
  const view = task ? presentTask(state, task) : null;
  const rerun = decision.operation === "RERUN";
  const currentResultVersion = task?.result_version ?? task?.result?.result_version ?? null;
  const rerunCompleted = rerun
    && currentResultVersion !== null
    && decision.afterResultVersion !== null
    && currentResultVersion >= decision.afterResultVersion
    && task?.status !== "DIRTY"
    && task?.status !== "INVALIDATED";
  const statusTrail = [decision.before, decision.after, rerunCompleted ? task?.status : null]
    .filter((status, index, all): status is string => Boolean(status) && all.indexOf(status) === index)
    .map((status) => labelOf(statusLabels, status))
    .join(" → ");
  return (
    <div className={`replan-task-diff ${rerun ? "is-rerun" : "is-keep"}`}>
      <span>{rerun ? <RotateCcw /> : <Check />}</span>
      <div>
        <strong>{view?.title ?? decision.taskId}</strong>
        <small>{decision.taskId}</small>
        <p>{rerun
          ? `命中变化事实：${decision.matchedChangedFacts.map(presentFact).join(" / ") || "依赖变化"}`
          : "依赖未受影响，复用原 Evidence 与结构化 Result"}</p>
        {rerun && workerOutcome?.status === "MATCHED" ? <em>重跑路径：Worker 确定性匹配完成，未进入仲裁 Agent</em> : null}
      </div>
      <Badge variant={rerun ? "outline" : "secondary"}>{rerun ? "失效并重跑" : "保留原结果"}</Badge>
      <code>{statusTrail || "—"}</code>
      <code>R{decision.beforeResultVersion ?? "—"} → {rerunCompleted ? `R${currentResultVersion} 已生成` : rerun ? `目标 R${decision.afterResultVersion ?? "—"}` : `R${decision.afterResultVersion ?? decision.beforeResultVersion ?? "—"}`}</code>
    </div>
  );
}

export function ExecutionInspector({ state, events, selectedTask, onSelectTask }: {
  state: CaseState;
  events: AuditEvent[];
  selectedTask: RequiredMaterialTask | null;
  onSelectTask?: (task: RequiredMaterialTask) => void;
}) {
  // 旧 Checkpoint 可能没有后来新增的关联字段；展示投影必须保持向后兼容。
  const tasks = state.audit_plan ?? [];
  const projection = projectRuntime(events);
  const projectedActiveNode = projection.workflow.latestNode ?? state.active_node;
  const currentTask = taskById(state, state.current_task_id ?? projection.workflow.latestTaskId);
  const currentTaskView = currentTask ? presentTask(state, currentTask) : null;
  const hasAudit = projection.audit.candidates.length > 0;
  const hasException = projection.exception.candidateRounds.length > 0 || projection.exception.steps.length > 0;
  const hasResume = events.some((event) => [
    "CHECKPOINT_LOOKUP_STARTED", "CHECKPOINT_FOUND", "INTERRUPTED_STATE_LOADED",
    "RESUME_COMMAND_ACCEPTED", "CHECKPOINT_RESUMED", "STATE_RECONCILIATION_STARTED",
  ].includes(event.event_type));
  const finishedTasks = tasks.filter((task) => task.status === "MATCHED").length;
  const progress = tasks.length ? finishedTasks / tasks.length * 100 : 0;
  const latestEventLabel = projection.workflow.latestEvent
    ? labelOf(eventLabels, projection.workflow.latestEvent.event_type)
    : "尚无运行事件";

  return (
    <aside className="execution-inspector">
      <div className="panel-heading runtime-heading">
        <span><GitBranch /><strong>LangGraph 运行检查器</strong></span>
        <Badge variant={state.status.includes("WAIT") ? "outline" : "secondary"}>{labelOf(statusLabels, state.status)}</Badge>
      </div>

      <div className="runtime-run-strip">
        <span><small>审核线程</small><strong>{state.thread_id}</strong></span>
        <span><small>进件 / 计划</small><strong>C{state.case_version} / P{state.plan_version}</strong></span>
        <span><small>已完成 Task</small><strong>{finishedTasks}/{tasks.length}</strong></span>
        <Progress value={progress} />
      </div>

      <ScrollArea className="execution-inspector-scroll">
        <Card className="runtime-focus-card">
          <CardHeader><CardTitle>当前控制点</CardTitle><Badge variant="outline">{currentTask ? "Task" : "Control"}</Badge></CardHeader>
          <CardContent>
            <strong>{currentTaskView?.title ?? nodeLabels[projectedActiveNode ?? ""] ?? projectedActiveNode ?? "等待启动"}</strong>
            <span>{latestEventLabel}</span>
            {currentTaskView ? <code>{currentTaskView.technicalId}</code> : null}
          </CardContent>
        </Card>

        {hasResume || state.plan_version > 1 ? (
          <section className="checkpoint-spine">
            <header><RotateCcw /><strong>Checkpoint 恢复脊柱</strong><small>恢复同一 thread_id，不重开进件</small></header>
            <ol>
              {projection.checkpointSpine.map((step) => (
                <li className={`is-${step.status}`} key={step.id}>
                  <span>{step.status === "done" ? <Check /> : <Circle />}</span>
                  <div><strong>{step.label}</strong><small>{step.detail}</small></div>
                </li>
              ))}
            </ol>
          </section>
        ) : null}

        <section className="inspector-section is-association">
          <header><Workflow /><strong>进件事实关联 Agent</strong><small>Evidence 候选 → Agent → Gate</small></header>
          {projection.association.selectedPageCount > 0 ? (
            <div className="association-worker-line"><span>页级证据并行提取</span><strong>{projection.association.extractedPageCount}/{projection.association.selectedPageCount}</strong><small>Send Worker</small></div>
          ) : null}
          <div className="association-summary">
            <span><small>身份 Mention</small><strong>{state.identity_mentions?.length ?? projection.association.mentionCount}</strong></span>
            <span><small>角色 Binding</small><strong>{state.role_bindings?.length ?? projection.association.roleSignalCount}</strong></span>
            <span><small>校验门</small><strong>{presentGateOutcome(projection.association.gate?.outcome ?? state.association_gate?.outcome)}</strong></span>
          </div>
          {projection.association.candidateCount > 0 && !projection.association.decision && !projection.association.modelRoute ? (
            <div className="model-route is-running"><small>模型路由</small><strong>正在生成结构化提议</strong><span>有界重试后切换备用 Endpoint</span></div>
          ) : null}
          {projection.association.modelRoute ? (
            <div className="model-route">
              <small>模型路由 · 进件事实关联</small>
              <div>{projection.association.modelRoute.attempts.map((attempt, index) => (
                <span className={`is-${attempt.status.toLowerCase()}`} key={`${attempt.endpoint}-${index}`}>
                  {attempt.endpoint} · {attempt.status}{attempt.errorCode ? ` · ${attempt.errorCode}` : ""}
                </span>
              ))}</div>
              <strong>{projection.association.modelRoute.selectedEndpoint ? `最终采用 ${projection.association.modelRoute.selectedEndpoint}` : "所有 Endpoint 均失败"}</strong>
            </div>
          ) : null}
          {projection.association.decision ? (
            <div className="decision-row"><Bot /><span><small>Agent 结构化提议</small><strong>{presentAction(projection.association.decision.action)}</strong></span></div>
          ) : null}
          <GateChecks checks={projection.association.gate?.checks ?? state.association_gate?.checks} />
          {projection.association.gate?.outcome === "CONFIRMED" ? (
            <div className="gate-commit-summary">
              <small>校验门提交的 State Projection</small>
              <strong>{value(projection.association.gate.confirmed_person_count)} 人员 · {value(projection.association.gate.confirmed_role_count)} 角色 · {value(projection.association.gate.confirmed_owner_count)} 页归属</strong>
              <span>下一步：进入应交清单规则引擎</span>
            </div>
          ) : null}
        </section>

        {projection.taskOrchestration.dispatchId ? (
          <section className="inspector-section is-orchestrator">
            <header><Workflow /><strong>审核任务编排器</strong><small>Dependency → Send → Fan-in</small></header>
            <div className="orchestrator-summary">
              <span><small>可执行 Task</small><strong>{projection.taskOrchestration.readyTaskIds.length}</strong></span>
              <span><small>Worker 完成</small><strong>{projection.taskOrchestration.completedTaskIds.length}</strong></span>
              <span><small>校验门提交</small><strong>{projection.taskOrchestration.committedTaskIds.length}</strong></span>
            </div>
            <code>{projection.taskOrchestration.dispatchId}</code>
            <small>{projection.taskOrchestration.rejectedTaskIds.length
              ? `拒绝陈旧结果：${projection.taskOrchestration.rejectedTaskIds.join(" / ")}`
              : "Worker 只返回结果；Fan-in Gate 校验版本后统一写入 State"}</small>
          </section>
        ) : null}

        <TaskLedger state={state} tasks={tasks} selectedTask={selectedTask} onSelectTask={onSelectTask} />
        <SelectedTaskCard state={state} task={selectedTask} />

        {hasAudit ? (
          <section className="inspector-section is-audit">
            <header><Bot /><strong>材料语义仲裁 Agent</strong><small>只处理封闭候选歧义</small></header>
            <div className="candidate-stack">
              {projection.audit.candidates.map((candidate) => (
                <div key={value(candidate.candidate_id)}>
                  <span>{value(candidate.proposed_person_id)}</span>
                  <strong>{presentMaterial(candidate.proposed_material_type)}</strong>
                  <small>{value(candidate.candidate_id)}</small>
                </div>
              ))}
            </div>
            <div className="decision-row"><Bot /><span><small>Agent 结构化提议</small><strong>{presentAction(projection.audit.decision?.action)}</strong></span></div>
            <div className="decision-row"><ShieldCheck /><span><small>材料对齐校验门</small><strong>{presentGateOutcome(projection.audit.gate?.outcome)}</strong></span></div>
            <GateChecks checks={projection.audit.gate?.checks} />
            <div className="gate-commit-summary">
              <small>校验门提交结果</small>
              <strong>{presentGateOutcome(projection.audit.gate?.outcome)}</strong>
              <span>{projection.audit.gate?.outcome === "APPLIED_AND_REMATCH" ? "下一步：写入页面事实并重新匹配" : "下一步：构造 Typed Handoff，业务 State 暂不写入"}</span>
            </div>
            {projection.audit.decision?.action === "REQUEST_RECOVERY" ? (
              <div className="decision-row"><GitBranch /><span><small>Typed Handoff</small><strong>转交异常取证恢复子 Agent</strong></span></div>
            ) : null}
          </section>
        ) : null}

        {hasException ? (
          <section className="inspector-section is-exception">
            <header><ScanSearch /><strong>异常取证恢复子 Agent</strong><small>剩余预算 {projection.exception.remainingBudget ?? "—"}/{projection.exception.stepBudget ?? "—"}</small></header>
            <div className="candidate-builder">
              <small>本轮动态暴露的候选 Tool</small>
              <div className="tool-candidates">{projection.exception.candidateTools.map((tool) => <Badge variant="outline" key={tool}>{presentTool(tool)}</Badge>)}</div>
              {Object.keys(projection.exception.blockedTools).length > 0 ? (
                <div className="tool-candidates is-blocked">{Object.entries(projection.exception.blockedTools).map(([tool, reason]) => <Badge variant="outline" title={presentReason(reason)} key={tool}>{presentTool(tool)} · {presentReason(reason)}</Badge>)}</div>
              ) : null}
            </div>
            {projection.exception.steps.map((step) => {
              const observation = step.observation?.result ?? step.observation?.error_code;
              return (
                <div className="exception-step" key={step.step}>
                  <span>{step.step}</span>
                  <div>
                    <small>决策 → Tool Gate → Observation</small>
                    <strong><Wrench />{presentTool(step.tool ?? (step.decision?.decision as Record<string, unknown> | undefined)?.action)}</strong>
                    <p>{value(observation)}</p>
                    <div className="exception-step-meta"><span>Gate {step.gate?.allowed === true ? "通过" : step.gate ? "阻止" : "等待"}</span><span>{step.observation?.state_changed === true ? "State 已变化" : "State 无变化"}</span></div>
                  </div>
                </div>
              );
            })}
            {projection.exception.completion ? (
              <div className="completion-row"><CheckCircle2 /><span><small>完成条件</small><strong>{presentReason(projection.exception.completion.status)} · {presentReason(projection.exception.completion.stop_reason)}</strong></span></div>
            ) : null}
          </section>
        ) : null}

        <ReturnJourney state={state} journey={projection.returnJourney} />

        {state.pending_human_request ? (
          <section className="inspector-section is-human">
            <header><UserRound /><strong>人工介入</strong><small>interrupt 已持久化</small></header>
            <div className="decision-row"><UserRound /><span><small>{state.pending_human_request.title ?? "等待人工确认"}</small><strong>{labelOf(actionLabels, state.pending_human_request.action)}</strong></span></div>
          </section>
        ) : null}

        {projection.replan.planVersion || projection.replan.decisions.length ? (
          <section className="runtime-replan-detail">
            <header>
              <RotateCcw />
              <strong>选择性 Replan · P{projection.replan.beforePlanVersion ?? Math.max(1, (projection.replan.planVersion ?? state.plan_version) - 1)} → P{projection.replan.planVersion ?? state.plan_version}</strong>
              <small>继承 Checkpoint，只重跑受影响 Task</small>
            </header>
            <div className="replan-summary">
              <span>复用 {projection.replan.reusedTaskIds.length}</span>
              <span>失效并重跑 {projection.replan.invalidatedTaskIds.length}</span>
              <span>变化事实 {projection.replan.changedFacts.length}</span>
            </div>
            <p>{projection.replan.changedFacts.map(presentFact).join(" / ") || "没有检测到事实变化"}</p>
            <div className="replan-task-list">
              {projection.replan.decisions.map((decision) => (
                <ReplanDecision
                  state={state}
                  decision={decision}
                  workerOutcome={projection.taskOrchestration.workerOutcomes[decision.taskId]}
                  key={`${decision.operation}-${decision.taskId}`}
                />
              ))}
            </div>
          </section>
        ) : null}

        {!events.length ? (
          <div className="empty-state"><Bot /><strong>尚未启动审核</strong><span>运行后将按事件合同还原关联、Task、Agent、Tool、Gate、Checkpoint 和 Replan。</span></div>
        ) : null}
      </ScrollArea>
    </aside>
  );
}
