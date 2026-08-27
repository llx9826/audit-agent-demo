"use client";

import { Check, Filter, Search, Shuffle, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import type { RagTrace } from "../api/contracts";
import { labelOf, materialLabels, roleLabels } from "../presentation/labels";

const labels: Record<string, string> = {
  QUERY_REWRITE: "查询改写",
  DENSE_BM25_RETRIEVAL: "Dense + BM25 召回",
  METADATA_FILTER: "Metadata 过滤",
  RRF: "RRF 融合",
  CROSS_ENCODER_RERANK: "Cross-Encoder 重排",
  PARENT_CONTEXT_EXPANSION: "Parent Context 展开",
  REQUIREMENT_GROUNDING: "原子要求绑定",
  WAITING_FOR_COMPLETENESS_PROBLEM: "等待齐套校验发现问题",
};

const stageIcons = [Search, Sparkles, Filter, Shuffle, Sparkles, Check];

export function RagTraceSheet({ open, onOpenChange, trace, requirementId }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  trace: RagTrace | null;
  requirementId?: string | null;
}) {
  const selected = trace?.candidates.find((item) => item.requirement_id === requirementId)
    ?? trace?.selected[0];
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="rag-sheet data-[side=right]:sm:max-w-2xl">
        <SheetHeader>
          <SheetTitle>补件依据 RAG</SheetTitle>
          <SheetDescription>齐套校验发现问题后，为当前任务绑定可引用的原子要求。</SheetDescription>
        </SheetHeader>
        {!trace ? <div className="empty-state">开始审核后加载 RAG Trace。</div> : (
          <ScrollArea className="rag-sheet-scroll">
            <section className="rag-query-card">
              <span>触发条件</span><p>{trace.trigger === "COMPLETENESS_PROBLEM" ? "齐套校验发现缺件、不可读或归属不确定" : trace.reason}</p>
              <span>原始查询</span><p>{trace.original_query ?? "尚未触发"}</p>
              <span>已确认任务查询</span><p>{trace.rewritten_query ?? "—"}</p>
            </section>
            <ol className="rag-stage-list">
              {trace.pipeline.map((stage, index) => {
                const Icon = stageIcons[index] ?? Check;
                return <li key={stage.stage}><Icon /><span><strong>{labels[stage.stage] ?? stage.stage}</strong><small>{JSON.stringify(Object.fromEntries(Object.entries(stage).filter(([key]) => key !== "stage")))}</small></span></li>;
              })}
            </ol>
            {selected && (
              <section className="requirement-proof">
                <div><Badge>最终选中</Badge><code>{selected.requirement_id}</code></div>
                <h3>{selected.title}</h3>
                <p>{selected.atomic_requirement}</p>
                <dl>
                  <div><dt>人员角色</dt><dd>{labelOf(roleLabels, selected.person_role)}</dd></div>
                  <div><dt>材料类型</dt><dd>{labelOf(materialLabels, selected.material_type)}</dd></div>
                  <div><dt>版本 / 生效</dt><dd>V{selected.checklist_version} · {selected.effective_from}</dd></div>
                  <div><dt>来源</dt><dd>{selected.source_document} · {selected.source_section}</dd></div>
                  <div><dt>Evidence</dt><dd>{selected.evidence_id}</dd></div>
                </dl>
              </section>
            )}
            <section className="candidate-table">
              <div className="candidate-table-head"><span>候选</span><span>Dense</span><span>BM25</span><span>RRF</span><span>Rerank</span></div>
              {trace.candidates.slice(0, 8).map((candidate) => (
                <div className={candidate.selected ? "is-selected" : candidate.eligible ? "" : "is-filtered"} key={candidate.requirement_id}>
                  <span><strong>{candidate.requirement_id}</strong><small>{candidate.filter_reasons.join(" / ") || "通过适用性过滤"}</small></span>
                  <code>{candidate.dense_score.toFixed(3)}</code><code>{candidate.bm25_score.toFixed(3)}</code><code>{candidate.rrf_score.toFixed(4)}</code><code>{candidate.rerank_score?.toFixed(3) ?? "—"}</code>
                </div>
              ))}
            </section>
          </ScrollArea>
        )}
      </SheetContent>
    </Sheet>
  );
}
