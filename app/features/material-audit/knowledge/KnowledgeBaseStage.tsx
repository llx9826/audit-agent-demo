"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { BookOpenCheck, Check, CircleSlash2, Database, ExternalLink, Filter, Layers3, Route, Search, Shuffle, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { materialAuditApi } from "../api/client";
import type { KnowledgeBuildReport, KnowledgeEvent, KnowledgeResult, RagCandidate } from "../api/contracts";

const intentLabels: Record<string, string> = {
  MATERIAL_REQUIREMENT: "材料要求",
  SOURCE_TRACE: "来源追溯",
  ANSWER_REQUIREMENT: "回答材料要求",
  TRACE_SOURCE: "返回来源证据",
  LOOKUP: "清单检索",
  APPLICABILITY: "适用性确认",
  WAIVER_OR_SUBSTITUTE: "免交或替代",
  REGION_COMPARISON: "地区/分行对比",
  SUPPLEMENT: "补件说明",
};

const pipelineLabels: Record<string, string> = {
  CACHE_LOOKUP: "精确作用域缓存查找",
  CACHE_HIT: "缓存命中（复用已校验结果）",
  CACHE_WRITE_VERIFIED: "缓存写入反读验证",
  CACHE_WRITE_FAILED: "缓存写入失败（本次答案仍有效）",
  INTENT_ROUTE: "LLM 意图识别",
  QUERY_REWRITE: "LLM 查询改写",
  METADATA_FILTER: "Metadata 路径内过滤",
  INDEPENDENT_SCOPE_RETRIEVAL: "地区独立检索",
  DENSE_BM25_RETRIEVAL: "BGE-M3 + Milvus BM25",
  RRF: "RRF 融合",
  CROSS_ENCODER_RERANK: "Cross-Encoder 重排",
  PARENT_CONTEXT_EXPANSION: "Parent Context 展开",
  REQUIREMENT_GROUNDING: "原子要求绑定",
  GROUNDED_ANSWER_LLM: "LLM Grounding",
  CITATION_VALIDATION: "引用校验",
};

function RankedLane({ title, candidates, rankKey, scoreKey }: {
  title: string;
  candidates: RagCandidate[];
  rankKey: "dense_rank" | "bm25_rank";
  scoreKey: "dense_score" | "bm25_score";
}) {
  const ranked = [...candidates].filter((item) => item.eligible && item[rankKey] !== null)
    .sort((a, b) => Number(a[rankKey]) - Number(b[rankKey])).slice(0, 5);
  return (
    <section className="retrieval-lane">
      <header><span>{title}</span><small>Top {ranked.length}</small></header>
      {!ranked.length && <div className="retrieval-empty">未命中（不参与 RRF）</div>}
      {ranked.map((item) => (
        <div className="retrieval-hit" key={`${title}-${item.requirement_id}`}>
          <code>#{item[rankKey]}</code>
          <span><strong>{item.title}</strong><i style={{ width: `${Math.max(18, 100 - (Number(item[rankKey]) - 1) * 16)}%` }} /></span>
          <b>{item[scoreKey].toFixed(3)}</b>
        </div>
      ))}
    </section>
  );
}

export function KnowledgeBaseStage() {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<KnowledgeResult | null>(null);
  const [build, setBuild] = useState<KnowledgeBuildReport | null>(null);
  const [runEvents, setRunEvents] = useState<KnowledgeEvent[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const querySequence = useRef(0);

  async function runQuery(nextQuestion = question) {
    const requestId = ++querySequence.current;
    setBusy(true);
    setError("");
    setResult(null);
    setRunEvents([]);
    try {
      const run = await materialAuditApi.startKnowledgeRun(nextQuestion);
      const completed = await materialAuditApi.consumeKnowledgeStream(run, (event) => {
        if (requestId === querySequence.current) {
          setRunEvents((current) => current.some((item) => item.seq === event.seq && item.run_id === event.run_id)
            ? current
            : [...current, event]);
        }
      });
      if (requestId === querySequence.current && completed.result) setResult(completed.result);
    } catch (reason) {
      if (requestId === querySequence.current) {
        setError(reason instanceof Error ? reason.message : "知识检索失败");
      }
    } finally {
      if (requestId === querySequence.current) setBusy(false);
    }
  }

  useEffect(() => {
    let active = true;
    let initialRequestId: number | null = null;
    materialAuditApi.getKnowledgeBuild()
      .then(async (buildReport) => {
        if (!active) return;
        setBuild(buildReport);
        const initialQuestion = buildReport.suggested_questions?.[0];
        if (initialQuestion) {
          const requestId = ++querySequence.current;
          initialRequestId = requestId;
          setQuestion(initialQuestion);
          setBusy(true);
          setRunEvents([]);
          const run = await materialAuditApi.startKnowledgeRun(initialQuestion);
          const completed = await materialAuditApi.consumeKnowledgeStream(run, (event) => {
            if (active && requestId === querySequence.current) {
              setRunEvents((current) => current.some((item) => item.seq === event.seq && item.run_id === event.run_id)
                ? current
                : [...current, event]);
            }
          });
          if (active && requestId === querySequence.current && completed.result) setResult(completed.result);
          if (active && requestId === querySequence.current) setBusy(false);
        }
      })
      .catch((reason: unknown) => {
        if (active && (initialRequestId === null || initialRequestId === querySequence.current)) {
          setError(reason instanceof Error ? reason.message : "知识库初始化失败");
          setBusy(false);
        }
      });
    return () => { active = false; };
  }, []);

  const eligible = result?.trace.candidates.filter((item) => item.eligible) ?? [];
  const fused = useMemo(() => [...(result?.trace.selected ?? [])]
    .sort((a, b) => Number(a.rerank_rank) - Number(b.rerank_rank)), [result]);
  const stageEvents = runEvents.filter((event) => event.event_type === "KNOWLEDGE_STAGE_COMPLETED");
  const liveIntent = [...stageEvents].reverse().find((event) => event.payload.stage === "INTENT_ROUTE")?.payload;
  const liveFilter = [...stageEvents].reverse().find((event) => event.payload.stage === "METADATA_FILTER")?.payload;
  const liveFilters = liveFilter?.filters && typeof liveFilter.filters === "object" && !Array.isArray(liveFilter.filters)
    ? liveFilter.filters as Record<string, string | string[]>
    : {};

  return (
    <section className="knowledge-stage">
      <div className="knowledge-build-ribbon">
        <span><Database />知识构建</span>
        <strong>{build?.record_count ?? "—"} 条原子要求</strong>
        <code>{build?.online_index?.backend ?? "等待建库"}</code>
        <span>{build?.online_index?.dense_model ?? "BGE-M3"} · {build?.online_index?.sparse_model ?? "Milvus BM25"}</span>
        <span><Layers3 />Document → Parent Section → Atomic Requirement</span>
        <span className="build-regions">{build?.regions.join(" · ") ?? "南京 · 北京 · 广州"}</span>
      </div>

      <div className="knowledge-query-bar">
        <div><Badge variant="outline">材料知识库</Badge><strong>自然语言先识别意图，再进入受约束检索</strong></div>
        <form onSubmit={(event) => { event.preventDefault(); void runQuery(); }}>
          <Input value={question} onChange={(event) => setQuestion(event.target.value)} aria-label="知识库问题" />
          <Button type="submit" disabled={busy || !question.trim()}><Search data-icon="inline-start" />{busy ? "检索中…" : "检索证据"}</Button>
        </form>
        <div className="question-presets">
          {(build?.suggested_questions ?? []).map((item) => (
            <button
              type="button"
              key={item}
              disabled={busy}
              onClick={() => { setQuestion(item); void runQuery(item); }}
            >
              {item}
            </button>
          ))}
        </div>
      </div>

      {error && <div className="knowledge-error">{error}</div>}
      <div className="knowledge-grid">
        <Card className="knowledge-scope-card">
          <CardHeader><Route /><div><CardTitle>意图与适用范围</CardTitle><CardDescription>Query Understanding</CardDescription></div></CardHeader>
          <CardContent>
            <section className="intent-block">
              <span>识别意图 <Badge variant="secondary">{result?.intent.route ?? String(liveIntent?.route ?? "—")} · {Math.round((result?.intent.confidence ?? Number(liveIntent?.confidence ?? 0)) * 100)}%</Badge></span>
              <div>
                {result?.intent.primary_intent && <Badge>{intentLabels[result.intent.primary_intent] ?? result.intent.primary_intent}</Badge>}
                {result?.intent.answer_modes.map((mode) => <Badge variant="secondary" key={mode}>{intentLabels[mode] ?? mode}</Badge>)}
                {result?.intent.query_modes.map((mode) => <Badge variant="outline" key={mode}>{intentLabels[mode] ?? mode}</Badge>)}
              </div>
              <code>{result?.intent.router ?? "STRUCTURED_INTENT_ROUTER_V1"}</code>
            </section>
            <section className="filter-block">
              <span><Filter />Metadata Pre-filter</span>
              <dl>{Object.entries(result?.applied_filters ?? liveFilters).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{Array.isArray(value) ? value.join(" · ") : String(value)}</dd></div>)}</dl>
            </section>
            <section className="pipeline-block">
              <span>真实检索链路</span>
              <ol>{(stageEvents.length ? stageEvents.map((event) => event.payload) : result?.trace.pipeline ?? []).map((item, index) => <li key={`${String(item.stage)}-${index}`}><Check /><span>{pipelineLabels[String(item.stage)] ?? String(item.stage)}{item.degraded ? <small>网络降级 · 保留原 Query</small> : item.scope ? <small>{String(item.scope)}</small> : null}</span></li>)}</ol>
            </section>
          </CardContent>
        </Card>

        <Card className="knowledge-retrieval-card">
          <CardHeader><Sparkles /><div><CardTitle>双路召回与排序</CardTitle><CardDescription>{result?.trace.retrieval.channel_backend} · {result?.trace.retrieval.reranker}</CardDescription></div></CardHeader>
          <CardContent>
            <div className="dual-retrieval">
              <RankedLane title="Dense 向量召回" candidates={eligible} rankKey="dense_rank" scoreKey="dense_score" />
              <RankedLane title="BM25 原始相关性 / Rank" candidates={eligible} rankKey="bm25_rank" scoreKey="bm25_score" />
            </div>
            <div className="fusion-divider"><Shuffle /><span>RRF 融合 → Cross-Encoder 重排</span></div>
            <div className="rerank-list">
              {fused.map((item) => <div key={item.requirement_id}><Badge>{item.rerank_rank}</Badge><span><strong>{item.title}</strong><small>{item.requirement_id}</small></span><code>{item.rrf_score.toFixed(4)} / {item.rerank_score?.toFixed(3)}</code></div>)}
            </div>
          </CardContent>
        </Card>

        <Card className="knowledge-answer-card">
          <CardHeader><BookOpenCheck /><div><CardTitle>引用回答</CardTitle><CardDescription>Parent Context + Atomic Requirement</CardDescription></div></CardHeader>
          <CardContent>
            <p className={`grounded-answer ${result?.status === "REFUSE" || result?.status === "CLARIFY" ? "is-refused" : ""}`}>
              {(result?.status === "REFUSE" || result?.status === "CLARIFY") && <CircleSlash2 />}
              {result?.answer ?? "正在检索可引用依据…"}
            </p>
            <ScrollArea className="citation-scroll">
              <div className="citation-list">
                {result?.citations.map((item) => (
                  <article key={item.child_chunk_id}>
                    <div><Badge variant="secondary">{item.region}</Badge><code>{item.child_chunk_id}</code></div>
                    <strong>{item.atomic_requirement}</strong>
                    <p><span>Parent</span>{item.parent_title} · {item.parent_text}</p>
                    <footer><span>{item.source_document} · {item.source_section}</span>{item.source_url && <a href={item.source_url} target="_blank" rel="noreferrer" aria-label={`打开${item.title}来源`}><ExternalLink /></a>}</footer>
                  </article>
                ))}
              </div>
            </ScrollArea>
          </CardContent>
        </Card>
      </div>
    </section>
  );
}
