"use client";

import { useState } from "react";
import { ArrowRight, CheckCircle2, CircleAlert, Play, RotateCcw } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ArchitectureStage } from "./features/material-audit/architecture/ArchitectureStage";
import type { HumanResumeCommand } from "./features/material-audit/api/contracts";
import { HumanTaskDialog } from "./features/material-audit/human-task/HumanTaskDialog";
import { KnowledgeBaseStage } from "./features/material-audit/knowledge/KnowledgeBaseStage";
import { useAuditRun } from "./features/material-audit/model/use-audit-run";
import { RagTraceSheet } from "./features/material-audit/rag-trace/RagTraceSheet";
import { MaterialWorkbench } from "./features/material-audit/workbench/MaterialWorkbench";

const stages = [
  { index: 1, title: "架构演进", detail: "Workflow → Agent → Sub-Agent" },
  { index: 2, title: "材料审核", detail: "216 页影像与动态清单" },
  { index: 3, title: "人机闭环", detail: "HITL · 补件 · Selective Replan" },
  { index: 4, title: "材料知识库", detail: "Hybrid RAG · 可引用依据" },
] as const;

const runtimeStatusLabels: Record<string, string> = {
  READY: "待启动",
  RUNNING: "运行中",
  WAITING_HUMAN: "等待人工处理",
  WAITING_SUPPLEMENT: "等待补件",
  COMPLETED: "已完成",
  FAILED: "执行失败",
};

function AppHeader({ stage, stateStatus, caseId, onNavigate }: { stage: number; stateStatus?: string; caseId?: string; onNavigate: (stage: number) => void }) {
  return (
    <header className="app-header">
      <div className="app-brand"><span className="brand-symbol">A</span><span><strong>ARGUS</strong><small>复杂信贷进件材料齐套审核</small></span></div>
      <ol className="stage-rail">
        {stages.map((item) => <li className={`${item.index === stage ? "is-active" : item.index < stage && stage < 4 ? "is-complete" : ""} ${item.index === 4 ? "is-knowledge" : ""}`} key={item.index}><button onClick={() => onNavigate(item.index)}><i>{item.index < stage && stage < 4 ? "✓" : item.index}</i><span><strong>{item.title}</strong><small>{item.detail}</small></span></button></li>)}
      </ol>
      <div className="header-runtime"><Badge variant={stateStatus?.includes("WAIT") ? "outline" : "secondary"}>{stateStatus ? runtimeStatusLabels[stateStatus] ?? stateStatus : "架构讲解"}</Badge><code>{caseId ?? "LANGGRAPH"}</code></div>
    </header>
  );
}

export default function AuditWorkbench() {
  const runtime = useAuditRun();
  const [stage, setStage] = useState(1);
  const [architectureVersion, setArchitectureVersion] = useState(1);
  const [humanOpen, setHumanOpen] = useState(false);
  const [ragOpen, setRagOpen] = useState(false);
  const [ragRequirementId, setRagRequirementId] = useState<string | null>(null);

  async function enterWorkbench() {
    try {
      await runtime.initialize();
      setStage(2);
    } catch {
      // The hook exposes the actionable error in the persistent alert.
    }
  }

  async function startAudit() {
    try {
      const next = await runtime.start();
      if (next.pending_human_request) {
        setStage(3);
        setHumanOpen(true);
      }
    } catch {
      // The hook exposes the actionable error in the persistent alert.
    }
  }

  async function handleHumanCommand(command: HumanResumeCommand) {
    setHumanOpen(false);
    try {
      const next = await runtime.resume(command);
      setStage(3);
      if (next.pending_human_request) setHumanOpen(true);
    } catch {
      setHumanOpen(true);
    }
  }

  function openRag(requirementId: string) {
    setRagRequirementId(requirementId);
    setRagOpen(true);
  }

  function openHumanTask() {
    runtime.clearError();
    setHumanOpen(true);
  }

  async function navigate(nextStage: number) {
    if (nextStage === 1 || nextStage === 4) {
      setStage(nextStage);
      return;
    }
    if (!runtime.caseState) {
      await enterWorkbench();
      return;
    }
    setStage(nextStage);
  }

  return (
    <TooltipProvider>
      <main className="audit-app">
        <AppHeader stage={stage} stateStatus={runtime.caseState?.status} caseId={runtime.caseState?.case_id} onNavigate={(value) => void navigate(value)} />
        {runtime.error && (
          <Alert variant="destructive" className="global-alert">
            <CircleAlert /><AlertTitle>执行未完成</AlertTitle><AlertDescription>{runtime.error}</AlertDescription>
            <Button size="icon-sm" variant="ghost" onClick={runtime.clearError} aria-label="关闭错误">×</Button>
          </Alert>
        )}
        <div className="app-content">
          {stage === 1 && (
            <ArchitectureStage
              version={architectureVersion}
              onNext={() => setArchitectureVersion((value) => Math.min(3, value + 1))}
              onEnter={enterWorkbench}
              busy={runtime.busy}
            />
          )}
          {(stage === 2 || stage === 3) && runtime.caseState && (
            <MaterialWorkbench state={runtime.caseState} events={runtime.events} ragTrace={runtime.ragTrace} onOpenRag={openRag} />
          )}
          {(stage === 2 || stage === 3) && !runtime.caseState && <div className="loading-state">正在初始化进件…</div>}
          {stage === 4 && <KnowledgeBaseStage />}
        </div>
        <footer className="app-footer">
          <div className="footer-runtime">
            <span className={runtime.busy ? "runtime-dot is-running" : "runtime-dot"} />
            <code>{runtime.caseState?.thread_id ?? "thread_id 将在进件创建后生成"}</code>
            {runtime.activeRun && <Badge variant="outline">{runtime.activeRun.run_id}</Badge>}
          </div>
          <div className="footer-version">
            {runtime.caseState ? `Case V${runtime.caseState.case_version} · Plan V${runtime.caseState.plan_version} · ${runtime.events.length} 条实时事件` : `架构版本 V${architectureVersion}`}
          </div>
          <div className="footer-actions">
            {stage === 1 && architectureVersion > 1 && <Button variant="outline" onClick={() => setArchitectureVersion((value) => Math.max(1, value - 1))}>上一版</Button>}
            {stage === 2 && <Button onClick={startAudit} disabled={runtime.busy || runtime.caseState?.status !== "READY"}><Play />{runtime.busy ? "正在运行…" : "开始材料审核"}</Button>}
            {(stage === 2 || stage === 3) && runtime.caseState?.pending_human_request && <Button onClick={openHumanTask} disabled={runtime.busy}>处理当前人工任务 <ArrowRight /></Button>}
            {stage === 3 && runtime.caseState?.status === "COMPLETED" && <><Badge className="completion-badge"><CheckCircle2 />材料已齐套</Badge><Button variant="outline" onClick={() => window.location.reload()}><RotateCcw />重新演示</Button></>}
            {stage === 4 && <Badge variant="outline">知识库查询不修改 Case 状态</Badge>}
          </div>
        </footer>

        {runtime.caseState && (
          <HumanTaskDialog state={runtime.caseState} open={humanOpen} busy={runtime.busy} error={runtime.error} onOpenChange={setHumanOpen} onSubmit={handleHumanCommand} />
        )}
        <RagTraceSheet open={ragOpen} onOpenChange={setRagOpen} trace={runtime.ragTrace} requirementId={ragRequirementId} />
      </main>
    </TooltipProvider>
  );
}
