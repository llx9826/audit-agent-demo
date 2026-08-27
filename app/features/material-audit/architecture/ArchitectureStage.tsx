"use client";

import {
  ArrowDown,
  ArrowRight,
  Bot,
  CheckCircle2,
  Database,
  FileStack,
  GitBranch,
  ListChecks,
  RefreshCcw,
  SearchCheck,
  ShieldCheck,
  UserRoundCheck,
  Workflow,
  Wrench,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

const versions = [
  {
    version: 1,
    name: "先建立确定性 Workflow",
    scene: "入口是已按六大类整理的 200+ 页影像，但页面里出现了谁、承担什么角色、材料属于谁，尚未形成可信事实。",
    solved: "规则引擎可以生成应交清单，任务编排器可以并行完成材料匹配、缺件判断和补件依据检索。",
    gap: "规则无法稳定处理人员归并、角色绑定和页面归属，也无法唯一判断模糊材料该进入哪个应交项。",
    change: "在事实入口和材料匹配后分别加入一个受控决策 Agent；模型只选封闭候选，校验门保留写入权。",
  },
  {
    version: 2,
    name: "加入两个受控决策 Agent",
    scene: "第一个 Agent 在生成清单前确认进件事实；第二个 Agent 只在材料匹配出现语义歧义后仲裁候选。",
    solved: "两个 Agent 分别补足事实入口和材料对齐的开放判断，并通过独立 Assignment、Prompt、Schema 与 Gate 控制边界。",
    gap: "当 OCR/VLM 低置信、跨页证据冲突、缺页重复或 Tool Failure 时，候选本身缺少 Observation，单次选择仍无法收敛。",
    change: "三个异常入口统一交给一个独立上下文的恢复子 Agent，再由恢复结果校验门送回原任务。",
  },
  {
    version: 3,
    name: "加入共享异常取证恢复子 Agent",
    scene: "关联校验、材料匹配和材料对齐都使用同一种 Typed Handoff，请求补充机器证据，不让决策 Agent 彼此自由对话。",
    solved: "子 Agent 每轮根据新 Observation 重建候选 Tool；Tool Gate、步数预算、Loop Guard 和完成条件共同控制循环。",
    gap: "恢复成功必须精确返回发起它的原 Task；预算耗尽、重复无变化或证据仍冲突时必须持久化并转人工。",
    change: "最终形成 1 个确定性主图、2 个受控决策 Agent、1 个共享异常取证恢复子 Agent。",
  },
] as const;

type NodeTone = "workflow" | "agent" | "gate" | "evidence" | "exception";

function FlowNode({
  icon: Icon,
  title,
  detail,
  codeLabel,
  tone = "workflow",
}: {
  icon: typeof FileStack;
  title: string;
  detail: string;
  codeLabel?: string;
  tone?: NodeTone;
}) {
  return (
    <div className={`architecture-node architecture-node--${tone}`}>
      <Icon aria-hidden="true" />
      <strong>{title}</strong>
      <span>{detail}</span>
      {codeLabel ? <code>{codeLabel}</code> : null}
    </div>
  );
}

function WorkflowBaseline() {
  return (
    <div className="baseline-stage">
      <div className="architecture-flow architecture-flow--baseline">
        <FlowNode icon={FileStack} title="六大类影像" detail="页面 · 卷宗 · 分类字段" codeLabel="PAGE / BUNDLE" />
        <ArrowRight className="flow-arrow" />
        <FlowNode icon={Database} title="应交清单规则引擎" detail="产品 · 渠道 · 角色 · 生效日期" codeLabel="RULE ENGINE" />
        <ArrowRight className="flow-arrow" />
        <FlowNode icon={ListChecks} title="审核任务计划" detail="人员 × 应交材料项 × 依赖" codeLabel="TASK PLAN" />
        <ArrowRight className="flow-arrow" />
        <FlowNode icon={Workflow} title="Send 并行匹配" detail="可执行任务 → Worker → 汇聚门" codeLabel="SEND / FAN-IN" />
        <ArrowRight className="flow-arrow" />
        <FlowNode icon={GitBranch} title="Task 结果路由" detail="缺件 · 语义歧义 · 机器异常" codeLabel="CONDITIONAL EDGE" tone="gate" />
      </div>
      <div className="baseline-blockers" aria-label="确定性规则的两个边界">
        <div><UserRoundCheck /><span><strong>事实入口被阻断</strong><small>人员、角色和页面归属尚未形成可信事实</small></span></div>
        <div><SearchCheck /><span><strong>材料对齐被阻断</strong><small>页面无法唯一对齐到人员、类型、跨页分组或应交项</small></span></div>
      </div>
    </div>
  );
}

function AgentContract({
  position,
  agentName,
  codeName,
  input,
  decision,
  gate,
  next,
}: {
  position: string;
  agentName: string;
  codeName: string;
  input: string;
  decision: string;
  gate: string;
  next: string;
}) {
  return (
    <div className="decision-agent-position">
      <span className="position-label">{position}</span>
      <div className="decision-agent-contract">
        <div className="decision-agent-chain">
          <FlowNode icon={ListChecks} title="Workflow 封闭候选" detail={input} codeLabel="MINIMUM CONTEXT" />
          <ArrowRight className="flow-arrow" />
          <FlowNode icon={Bot} title={agentName} detail={decision} codeLabel={codeName} tone="agent" />
          <ArrowRight className="flow-arrow" />
          <FlowNode icon={ShieldCheck} title={gate} detail="版本 · 范围 · 证据 · 写入" codeLabel="GATE" tone="gate" />
        </div>
        <div className="agent-contract-result">
          <span><strong>调用原因</strong>确定性规则无法唯一选择</span>
          <span><strong>最小上下文</strong>仅当前 Assignment、候选与 Evidence</span>
          <span><strong>Agent 输出</strong>应用候选 / 请求取证 / 请求人工</span>
          <span><strong>Gate 与下一步</strong>{next}</span>
        </div>
      </div>
    </div>
  );
}

function DecisionAgents() {
  return (
    <div className="decision-agent-map">
      <AgentContract
        position="清单生成前 · 事实入口"
        agentName="进件事实关联 Agent"
        codeName="CASE ASSOCIATION AGENT"
        input="身份 · 角色 · 页面归属候选"
        decision="人员归并 · 角色绑定 · 材料归属"
        gate="关联事实校验门"
        next="确认事实 → 应交清单规则引擎"
      />
      <div className="workflow-spine">
        <span>确认进件事实</span><ArrowRight /><span>生成应交清单</span><ArrowRight /><span>Send 并行匹配</span><ArrowRight /><span>发现语义歧义</span>
      </div>
      <AgentContract
        position="材料匹配后 · 语义歧义"
        agentName="材料语义仲裁 Agent"
        codeName="MATERIAL AUDIT AGENT"
        input="当前 Task · 问题页 · Plan 允许组合"
        decision="所属人 · 类型 · 跨页 · 清单项对齐"
        gate="材料对齐校验门"
        next="写入页面事实 → 返回 Matcher 重新匹配"
      />
    </div>
  );
}

function ExceptionRecoveryArchitecture() {
  return (
    <div className="shared-recovery-map">
      <div className="recovery-sources" aria-label="三个异常来源">
        <span><strong>关联事实校验门</strong><small>身份 / 角色证据不足</small><code>CASE_ASSOCIATION</code></span>
        <span><strong>材料匹配 Worker</strong><small>OCR · 缺页 · Tool Failure</small><code>MATERIAL_MATCHER</code></span>
        <span><strong>材料对齐校验门</strong><small>候选缺少独立 Observation</small><code>MATERIAL_AUDIT</code></span>
      </div>
      <div className="recovery-convergence" aria-hidden="true"><i /><i /><i /><b /></div>
      <div className="typed-handoff-strip">
        <GitBranch /><strong>Typed Handoff</strong><span>来源 · Task · 页范围 · 异常类型 · 版本 · Return Target</span>
      </div>
      <ArrowDown className="recovery-down" aria-hidden="true" />
      <div className="exception-loop-diagram">
        <div className="exception-agent-name">
          <Bot /><span><strong>异常取证恢复子 Agent</strong><small>补充 Observation，不直接修改业务结论</small></span><code>EXCEPTION RECOVERY SUB-AGENT</code>
        </div>
        <div className="exception-loop-row">
          <span className="loop-node"><ListChecks /><strong>重建候选 Tool</strong><small>每轮只暴露 2–4 个</small></span>
          <ArrowRight />
          <span className="loop-node loop-node--decision"><Bot /><strong>模型选择下一步</strong><small>调用 · 解决 · 升级人工</small></span>
          <ArrowRight />
          <span className="loop-node"><Wrench /><strong>工具调用校验门</strong><small>Schema · 白名单 · 重试</small></span>
          <ArrowRight />
          <span className="loop-node loop-node--evaluate"><RefreshCcw /><strong>Observation + 完成条件</strong><small>状态变化 · 预算 · Loop Guard</small></span>
        </div>
        <svg className="loop-backedge" viewBox="0 0 760 58" preserveAspectRatio="none" aria-hidden="true">
          <defs><marker id="loop-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0 L10 5 L0 10z" /></marker></defs>
          <path d="M730 4 C730 48 585 51 380 51 L110 51 C80 51 72 38 72 20" markerEnd="url(#loop-arrow)" />
        </svg>
        <span className="loop-caption">完成条件未满足且仍有预算：携带新 Observation 回到候选构建</span>
      </div>
      <div className="recovery-result-gate">
        <ShieldCheck /><strong>恢复结果校验门</strong>
        <span><CheckCircle2 /> 已解决 → 按 Return Target 返回原 Task</span>
        <span><UserRoundCheck /> 未收敛 → Checkpoint + HITL</span>
      </div>
      <div className="recovery-return-strip">
        <span>关联证据阶段</span><i />
        <span>材料匹配阶段</span><i />
        <span>材料人工确认</span>
        <strong><RefreshCcw /> 精确回程，不自由对话</strong>
      </div>
    </div>
  );
}

export function ArchitectureStage({
  version,
  onNext,
  onEnter,
  busy,
}: {
  version: number;
  onNext: () => void;
  onEnter: () => void;
  busy: boolean;
}) {
  const current = versions[version - 1];
  return (
    <section className="architecture-stage">
      <div className="architecture-copy">
        <Badge variant="outline">第一幕 · 架构演进</Badge>
        <h1>材料齐套审核的三个架构版本</h1>
        <p>只判断：对应人员应提供的材料是否到齐、可读且归属明确。</p>
        <Card className="architecture-reason">
          <CardHeader>
            <CardDescription>版本 V{current.version}</CardDescription>
            <CardTitle>{current.name}</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="evolution-story">
              <div><dt>业务现场</dt><dd>{current.scene}</dd></div>
              <div><dt>本版解决</dt><dd>{current.solved}</dd></div>
              <div className="is-gap"><dt>为什么升级</dt><dd>{current.gap}</dd></div>
              <div className="is-change"><dt>下一步设计</dt><dd>{current.change}</dd></div>
            </dl>
          </CardContent>
        </Card>
        <div className="architecture-actions">
          {version < 3 ? (
            <Button size="lg" onClick={onNext}>生成下一版 <ArrowRight data-icon="inline-end" /></Button>
          ) : (
            <Button size="lg" onClick={onEnter} disabled={busy}>{busy ? "正在载入进件…" : "进入材料审核工作台"} <ArrowRight data-icon="inline-end" /></Button>
          )}
          <span>{version} / 3 · 点击控制演示节奏</span>
        </div>
      </div>

      <div className={`architecture-canvas architecture-canvas--v${version}`}>
        <header className="architecture-canvas-header">
          <div><Badge>V{version}</Badge><strong>{current.name}</strong></div>
          <span>{version === 1 ? "确定性主图" : version === 2 ? "1 主图 · 2 决策 Agent" : "1 主图 · 2 决策 Agent · 1 恢复子 Agent"}</span>
        </header>
        <div className="architecture-lane">
          <div className="lane-title">
            <span>{version === 1 ? "LangGraph 确定性主图" : version === 2 ? "两个决策 Agent 的生命周期位置" : "共享异常取证恢复与精确回程"}</span>
            <small>状态、路由和最终写入权始终属于 Workflow</small>
          </div>
          {version === 1 ? <WorkflowBaseline /> : null}
          {version === 2 ? <DecisionAgents /> : null}
          {version === 3 ? <ExceptionRecoveryArchitecture /> : null}
        </div>
        <div className="architecture-footer-strip">
          <span><SearchCheck />确定性缺件 → 补件依据 RAG</span>
          <span><ShieldCheck />Agent 提议 → 校验门写入</span>
          <span><UserRoundCheck />不能安全收敛 → 持久化 HITL</span>
        </div>
      </div>
    </section>
  );
}
