"use client";
/* eslint-disable @next/next/no-img-element -- evidence URLs are runtime FastAPI assets, not build-time images */

import { useState } from "react";
import { FileCheck2, FileWarning, Send, Upload, Workflow } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { materialAssetUrl } from "../api/client";
import type { CaseState, HumanResumeCommand } from "../api/contracts";
import { labelOf, materialLabels, reasonLabels, roleLabels } from "../presentation/labels";

const actionTitleLabels = {
  CONFIRM_ASSOCIATION: "人员、角色与材料归属待确认",
  RESOLVE_ASSOCIATION_EVIDENCE: "补充人员与角色证据",
  CONFIRM_OWNER: "材料归属待确认",
  REVIEW_IMAGE: "不可读影像待复核",
  REQUEST_SUPPLEMENT: "必需材料缺失",
  SUPPLEMENT_RECEIVED: "补件已到件",
} as const;

export function HumanTaskDialog({ state, open, busy, error, onOpenChange, onSubmit }: {
  state: CaseState;
  open: boolean;
  busy: boolean;
  error?: string;
  onOpenChange: (open: boolean) => void;
  onSubmit: (command: HumanResumeCommand) => Promise<unknown>;
}) {
  const [resolvedPersonId, setResolvedPersonId] = useState("");
  const [resolvedPersonName, setResolvedPersonName] = useState("");
  const [resolvedRoles, setResolvedRoles] = useState("");
  const request = state.pending_human_request;
  if (!request) return null;
  const targetPerson = state.persons.find((person) => person.person_id === request.person_id);
  const candidatePage = state.pages.find((page) =>
    request.page_id === page.page_id || request.candidate_page_ids?.includes(page.page_id)
  );
  const title = actionTitleLabels[request.action];
  const requiresCasePage = request.action === "CONFIRM_OWNER" || request.action === "REVIEW_IMAGE";
  const missingCasePage = requiresCasePage && !candidatePage;
  const associationCandidateIds = (request.candidate_options ?? [])
    .map((candidate) => candidate.candidate_id)
    .filter((candidateId): candidateId is string => typeof candidateId === "string");
  const selectedMaterialCandidate = (request.candidate_options ?? []).find((candidate) =>
    candidatePage
      ? candidate.page_ids?.includes(candidatePage.page_id)
        && candidate.proposed_person_id === request.person_id
        && candidate.proposed_material_type === request.material_type
      : false
  );

  async function submit(extra: Partial<HumanResumeCommand> = {}) {
    const base: HumanResumeCommand = {
      event_id: `HUMAN-${crypto.randomUUID()}`,
      action: request.action,
      task_id: request.task_id,
      ...extra,
    };
    if (request.action === "SUPPLEMENT_RECEIVED") {
      base.page = {
        page_id: `PAGE-UPLOAD-${Date.now()}`,
        bundle_id: "SUPPLEMENT",
        confidence: 0.99,
      };
    }
    await onSubmit(base);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="human-dialog sm:max-w-2xl" showCloseButton={false}>
        <DialogHeader>
          <div className="human-dialog-kicker"><Badge variant="outline">HITL · LangGraph interrupt</Badge><code>{request.task_id}</code></div>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{request.rationale_summary ?? request.reason ?? "Workflow 已保存 Checkpoint，等待结构化人工决策。"}</DialogDescription>
        </DialogHeader>
        <div className="human-task-grid">
          {request.action === "CONFIRM_ASSOCIATION" ? (
            <div className="human-page-empty"><Workflow /><strong>{associationCandidateIds.length} 个证据候选</strong><small>Person · Role · Material Owner</small></div>
          ) : candidatePage ? (
            <div className="human-page-preview">
              <img src={materialAssetUrl(candidatePage.preview_url ?? "")} alt={`${candidatePage.page_id} 材料影像`} />
              <span><strong>{candidatePage.page_id}</strong><small>{labelOf(materialLabels, candidatePage.material_type, "待分类")} · {Math.round((candidatePage.confidence ?? 0) * 100)}%</small></span>
            </div>
          ) : (
            <div className="human-page-empty"><FileWarning /><strong>当前进件未匹配到影像</strong><small>{labelOf(materialLabels, request.material_type, "待关联")}</small></div>
          )}
          <div className="human-task-context">
            <dl>
              <div><dt>当前卡点</dt><dd>{labelOf(reasonLabels, request.reason_code ?? request.action)}</dd></div>
              <div><dt>对应人员</dt><dd>{targetPerson?.name ?? request.person_id ?? "由候选确认"} · {targetPerson?.roles.map((role) => labelOf(roleLabels, role)).join(" / ")}</dd></div>
              <div><dt>需求来源</dt><dd>{request.requirement_id ?? "关联证据候选"}</dd></div>
              {request.requirement_grounding && <><div><dt>原文依据</dt><dd>{request.requirement_grounding.atomic_requirement}</dd></div><div><dt>来源章节</dt><dd>{request.requirement_grounding.source_document} · {request.requirement_grounding.source_section}</dd></div></>}
              <div><dt>案例版本</dt><dd>Case V{state.case_version} · Plan V{state.plan_version}</dd></div>
            </dl>
            {request.action === "RESOLVE_ASSOCIATION_EVIDENCE" && (
              <div className="association-evidence-form">
                <strong>录入已核验的页级事实</strong>
                <Input value={resolvedPersonId} onChange={(event) => setResolvedPersonId(event.target.value)} placeholder="人员标识，例如 P01" aria-label="人员标识" />
                <Input value={resolvedPersonName} onChange={(event) => setResolvedPersonName(event.target.value)} placeholder="脱敏姓名" aria-label="脱敏姓名" />
                <Input value={resolvedRoles} onChange={(event) => setResolvedRoles(event.target.value)} placeholder="角色，例如 BORROWER, MORTGAGOR" aria-label="人员角色" />
                <small>多个角色使用英文逗号分隔；提交后重新执行页级取证和关联事实校验门。</small>
              </div>
            )}
            <Alert>
              <FileCheck2 />
              <AlertTitle>状态已持久化</AlertTitle>
              <AlertDescription>提交后使用同一 thread_id 恢复，并执行 State Reconciliation 与 Selective Replan。</AlertDescription>
            </Alert>
            {error && <Alert variant="destructive"><FileWarning /><AlertTitle>人工任务未提交</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
            {missingCasePage && !error && <Alert variant="destructive"><FileWarning /><AlertTitle>缺少可操作影像</AlertTitle><AlertDescription>当前任务没有关联有效 page_id，已阻止发送无效命令。请刷新 Case 状态或重新运行匹配。</AlertDescription></Alert>}
          </div>
        </div>
        <DialogFooter>
          {request.action === "CONFIRM_ASSOCIATION" && (
            <Button onClick={() => submit({ selected_candidate_ids: associationCandidateIds })} disabled={busy || !associationCandidateIds.length}>
              <FileCheck2 /> 确认证据闭合
            </Button>
          )}
          {request.action === "RESOLVE_ASSOCIATION_EVIDENCE" && (
            <Button
              onClick={() => submit({
                page_id: candidatePage?.page_id ?? request.page_id,
                person_id: resolvedPersonId.trim(),
                person_name: resolvedPersonName.trim(),
                roles: resolvedRoles.split(",").map((item) => item.trim()).filter(Boolean),
              })}
              disabled={busy || !candidatePage || !resolvedPersonId.trim() || !resolvedPersonName.trim() || !resolvedRoles.trim()}
            >
              <FileCheck2 /> 保存事实并恢复流程
            </Button>
          )}
          {request.action === "CONFIRM_OWNER" && (
            <Button onClick={() => submit({
              page_id: candidatePage?.page_id,
              person_id: request.person_id,
              material_type: request.material_type,
              selected_candidate_id: selectedMaterialCandidate?.candidate_id,
              reason_code: "HUMAN_CONFIRMED_OWNER",
              operator_id: "demo-reviewer",
            })} disabled={busy || missingCasePage}>
              <FileCheck2 /> 确认归属于 {targetPerson?.name ?? request.person_id}
            </Button>
          )}
          {request.action === "REVIEW_IMAGE" && (
            <Button onClick={() => submit({
              page_id: candidatePage?.page_id,
              person_id: request.person_id,
              material_type: request.material_type,
              selected_candidate_id: selectedMaterialCandidate?.candidate_id,
              reason_code: "HUMAN_CONFIRMED_IMAGE",
              operator_id: "demo-reviewer",
            })} disabled={busy || missingCasePage}>
              <FileCheck2 /> 确认识别结果
            </Button>
          )}
          {request.action === "REQUEST_SUPPLEMENT" && (
            <Button onClick={() => submit()} disabled={busy}><Send /> 发起补件单</Button>
          )}
          {request.action === "SUPPLEMENT_RECEIVED" && (
            <Button onClick={() => submit()} disabled={busy}><Upload /> 登记补件到件</Button>
          )}
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>暂不处理</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
