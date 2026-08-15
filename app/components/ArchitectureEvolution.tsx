"use client";

import { useState } from "react";
import styles from "./ArchitectureEvolution.module.css";

type ArchitectureVersion = 1 | 2 | 3;

export type ArchitectureEvolutionProps = {
  onStartDemo?: () => void | Promise<void>;
  busy?: boolean;
};

type VersionStory = {
  version: ArchitectureVersion;
  shortTitle: string;
  title: string;
  subtitle: string;
  problem: string;
  decision: string;
  boundary: string;
  result: string;
  events: readonly { type: string; detail: string; tone: "workflow" | "agent" | "exception" | "success" }[];
};

const STORIES: Record<ArchitectureVersion, VersionStory> = {
  1: {
    version: 1,
    shortTitle: "Workflow",
    title: "先用 Workflow 承接确定性",
    subtitle: "材料接入、字段归一化、规则校验与状态推进保持可预测。",
    problem: "借款人与抵押人不是同一人。字段规则只能发现“不相等”，无法判断是配偶、共有人还是第三方抵押。",
    decision: "先把可编码的材料检查、字段比对和任务依赖放进确定性 StateGraph。",
    boundary: "节点只接受 Typed CaseState；conditional edge 只处理稳定、可复现的业务条件。",
    result: "标准件路径顺利完成，但 relationship 与后续审核任务仍为 UNRESOLVED。",
    events: [
      { type: "CASE_INGESTED", detail: "6 类材料已登记", tone: "workflow" },
      { type: "STATE_BUILT", detail: "CaseState v1", tone: "workflow" },
      { type: "RULES_PASSED", detail: "字段与完整性通过", tone: "success" },
      { type: "RELATION_UNRESOLVED", detail: "后续任务无法确定", tone: "exception" },
    ],
  },
  2: {
    version: 2,
    shortTitle: "+ Audit Agent",
    title: "把语义判断交给 Audit Agent",
    subtitle: "Agent 识别跨材料关系并提出任务意图，Workflow 仍掌握写入权。",
    problem: "抵押关系需要联合婚姻证明、身份材料和产权信息理解；任务集合也会随关系类型变化。",
    decision: "在 needs_semantic_review 条件边后加入 Audit Agent，返回 relation、evidence_ids 与 task_intents。",
    boundary: "Agent 不直接修改 CaseState；Plan Gate 校验允许任务与依赖后，才把任务写入 DAG。",
    result: "relation_hypothesis = SPOUSE；提出配偶身份与抵押同意任务意图，但主图在关系材料确认前不写入计划。",
    events: [
      { type: "SEMANTIC_ROUTE", detail: "needs_semantic_review", tone: "workflow" },
      { type: "AUDIT_AGENT_RETURNED", detail: "task_intents: T06 / T07", tone: "agent" },
      { type: "RELATION_EVIDENCE_GAP", detail: "hypothesis ≠ verified fact", tone: "exception" },
      { type: "PLAN_PATCH_GATED", detail: "等待关系材料", tone: "workflow" },
    ],
  },
  3: {
    version: 3,
    shortTitle: "+ Exception Agent",
    title: "用受控子图隔离异常恢复",
    subtitle: "OCR 修复不进入业务推理上下文，只在局部、限步的异常子图中运行。",
    problem: "户口簿 OCR 与身份证姓名冲突。让 Audit Agent 同时负责数据修复，会扩大上下文、工具权限与重试风险。",
    decision: "将 OCR_CONFLICT 封装成 typed handoff，交给独立 Exception Recovery Subgraph。",
    boundary: "Scoped State · Tool Allowlist · max_steps=3 · Loop Guard；只返回 RESOLVED / NEED_HUMAN。",
    result: "可恢复异常携证据回到主图；关键外部事实仍不足时写入持久化 Checkpoint 并安全暂停。",
    events: [
      { type: "HANDOFF_CREATED", detail: "allowlist · 3 steps", tone: "workflow" },
      { type: "AGENT_TOOL_FINISHED", detail: "OCR retry · confidence low", tone: "exception" },
      { type: "AGENT_TOOL_FINISHED", detail: "VLM + trusted source matched", tone: "agent" },
      { type: "AGENT_RETURNED", detail: "RESOLVED → state patch", tone: "success" },
    ],
  },
};

const VERSION_ORDER: ArchitectureVersion[] = [1, 2, 3];

function WorkflowNode({ className, eyebrow, title, detail }: { className: string; eyebrow: string; title: string; detail: string }) {
  return (
    <div className={`${styles.node} ${styles.workflowNode} ${className}`}>
      <span>{eyebrow}</span>
      <strong>{title}</strong>
      <small>{detail}</small>
    </div>
  );
}

function ArchitectureGraph({ version, replayKey }: { version: ArchitectureVersion; replayKey: number }) {
  const v2Animated = version === 2;

  return (
    <div className={styles.canvasScroller}>
      <p className={styles.mobileCanvasHint}>左右滑动查看完整拓扑</p>
      <div
        className={styles.graphCanvas}
        role="img"
        aria-label={`CASE-ZD-042 架构第 ${version} 版：${STORIES[version].title}`}
      >
        <div className={styles.canvasHeading}>
          <span>LANGGRAPH MAIN GRAPH</span>
          <small>CASE-ZD-042 · ARCHITECTURE V{version}</small>
        </div>

        <WorkflowNode className={styles.ingestNode} eyebrow="01 / INGEST" title="材料接入" detail="6 类进件材料" />
        <WorkflowNode className={styles.normalizeNode} eyebrow="02 / STATE" title="事实归一化" detail="Typed CaseState" />
        <WorkflowNode className={styles.rulesNode} eyebrow="03 / RULE" title="确定性审核" detail="字段 · 完整性 · 依赖" />
        <div className={`${styles.node} ${styles.finalNode}`}>
          <span>FINAL GATE</span>
          <strong>Validator</strong>
          <small>Schema · Evidence</small>
        </div>

        {version === 1 ? (
          <div className={styles.layer} key={`v1-${replayKey}`}>
            <i className={`${styles.edge} ${styles.edgeHorizontal} ${styles.edgeIngestNormalize} ${styles.drawEdge}`} />
            <i className={`${styles.edge} ${styles.edgeHorizontal} ${styles.edgeNormalizeRules} ${styles.drawEdge} ${styles.delayOne}`} />
            <i className={`${styles.edge} ${styles.edgeHorizontal} ${styles.edgeRulesFinal} ${styles.drawEdge} ${styles.delayTwo}`} />
            <span className={`${styles.routeLabel} ${styles.unresolvedLabel}`}>
              relationship ? <b>UNRESOLVED</b>
            </span>
          </div>
        ) : (
          <div className={styles.layer} aria-hidden="true">
            <i className={`${styles.edge} ${styles.edgeHorizontal} ${styles.edgeIngestNormalize}`} />
            <i className={`${styles.edge} ${styles.edgeHorizontal} ${styles.edgeNormalizeRules}`} />
          </div>
        )}

        {version >= 2 && (
          <div
            className={`${styles.layer} ${v2Animated ? styles.animatedLayer : styles.settledLayer}`}
            key={v2Animated ? `v2-${replayKey}` : "v2-settled"}
          >
            <div className={`${styles.routeGate} ${styles.v2RevealOne}`}>
              <span>CONDITIONAL EDGE</span>
              <strong>semantic?</strong>
            </div>
            <i className={`${styles.edge} ${styles.edgeHorizontal} ${styles.edgeRulesRoute} ${styles.v2RevealOne}`} />
            <i className={`${styles.edge} ${styles.edgeHorizontal} ${styles.edgeRouteFinal} ${styles.v2RevealTwo}`} />
            <i className={`${styles.edge} ${styles.edgeVertical} ${styles.edgeRouteAudit} ${styles.v2RevealTwo}`} />
            <span className={`${styles.edgeLabel} ${styles.semanticLabel} ${styles.v2RevealTwo}`}>needs_semantic_review</span>

            <div className={`${styles.node} ${styles.auditNode} ${styles.v2RevealThree}`}>
              <span>SEMANTIC NODE</span>
              <strong>Audit Agent</strong>
              <small>relation_review · policy_applicability</small>
              <div className={styles.contractLine}>
                <code>EvidenceRefs</code><b>→</b><code>task_intents</code>
              </div>
            </div>
            <i className={`${styles.edge} ${styles.edgeHorizontal} ${styles.edgeAuditPlan} ${styles.v2RevealFour}`} />
            <div className={`${styles.node} ${styles.planNode} ${styles.v2RevealFour}`}>
              <span>WRITE CONTROL</span>
              <strong>Plan Gate</strong>
              <small>允许任务 + 依赖校验</small>
            </div>
            <i className={`${styles.edge} ${styles.edgeVertical} ${styles.edgePlanFinal} ${styles.edgeUp} ${styles.v2RevealFour}`} />

            <div className={`${styles.planPatch} ${styles.v2RevealFive}`}>
              <span>PLAN PATCH GATED</span>
              <b>T06 配偶身份 · PROPOSED</b>
              <b>T07 抵押同意 · PROPOSED</b>
            </div>
            <div className={`${styles.evidenceRail} ${styles.v2RevealFive}`}>
              <span>EVIDENCE GUARDRAIL</span>
              <code>relation_hypothesis=SPOUSE</code>
              <code>E-HOUSEHOLD-04</code>
              <code>rule=NFRA-2024</code>
            </div>
          </div>
        )}

        {version === 3 && (
          <div className={`${styles.layer} ${styles.animatedLayer}`} key={`v3-${replayKey}`}>
            <i className={`${styles.edge} ${styles.edgeHorizontal} ${styles.edgeAuditException} ${styles.edgeReverse} ${styles.v3RevealOne}`} />
            <span className={`${styles.edgeLabel} ${styles.exceptionLabel} ${styles.v3RevealOne}`}>OCR_CONFLICT · typed handoff</span>

            <section className={`${styles.exceptionSubgraph} ${styles.v3RevealTwo}`} aria-label="Exception Recovery Subgraph">
              <header>
                <div>
                  <span>LANGGRAPH SUBGRAPH</span>
                  <strong>Exception Recovery Agent</strong>
                </div>
                <code>step 2 / 3</code>
              </header>
              <div className={styles.exceptionSteps}>
                <div><i>01</i><b>Classify</b><small>scoped_fields</small></div>
                <em>→</em>
                <div><i>02</i><b>Tool</b><small>VLM Extract</small></div>
                <em>→</em>
                <div><i>03</i><b>Evaluate</b><small>state delta</small></div>
              </div>
              <div className={styles.guardrailTags}>
                <span>Tool Allowlist</span><span>max_steps = 3</span><span>Loop Guard</span>
              </div>
              <footer>
                <b>RESOLVED → return patch</b>
                <span>NEED_HUMAN → checkpoint</span>
              </footer>
            </section>

            <i className={`${styles.edge} ${styles.edgeHorizontal} ${styles.edgeExceptionReturn} ${styles.v3RevealThree}`} />
            <i className={`${styles.edge} ${styles.edgeVertical} ${styles.edgeReturnUp} ${styles.edgeUp} ${styles.v3RevealThree}`} />
            <span className={`${styles.edgeLabel} ${styles.resolvedLabel} ${styles.v3RevealThree}`}>RESOLVED · state patch validated</span>
            <div className={`${styles.checkpointChip} ${styles.v3RevealFour}`}>
              <i />
              <span>CHECKPOINT</span>
              <b>durable pause / resume</b>
            </div>
          </div>
        )}

        <div className={styles.stateRail} key={`state-${version}-${replayKey}`}>
          <span>STATE</span>
          {version === 1 && <><code>relationship: UNKNOWN</code><code>next_tasks: unresolved</code></>}
          {version === 2 && <><code>relationship: SPOUSE?</code><code>task_intents: [T06, T07]</code></>}
          {version === 3 && <><code>exception: RESOLVED</code><code>attempts: 2 / 3</code></>}
          <small>append-only events · checkpointed</small>
        </div>

        <i className={`${styles.flowToken} ${styles[`tokenV${version}`]}`} key={`token-${version}-${replayKey}`} aria-hidden="true" />
      </div>
    </div>
  );
}

export default function ArchitectureEvolution({ onStartDemo, busy = false }: ArchitectureEvolutionProps) {
  const [version, setVersion] = useState<ArchitectureVersion>(1);
  const [replayKey, setReplayKey] = useState(0);
  const story = STORIES[version];

  const goToVersion = (nextVersion: ArchitectureVersion) => {
    setVersion(nextVersion);
    setReplayKey((current) => current + 1);
  };

  const goBack = () => {
    if (version > 1) goToVersion((version - 1) as ArchitectureVersion);
  };

  const upgrade = () => {
    if (version < 3) goToVersion((version + 1) as ArchitectureVersion);
  };

  return (
    <article className={styles.evolution}>
      <header className={styles.hero}>
        <div>
          <p className={styles.eyebrow}>LANGGRAPH ARCHITECTURE EVOLUTION · CASE-ZD-042</p>
          <h1>一笔宅抵贷，<br />三次架构演进。</h1>
        </div>
        <div className={styles.heroCopy}>
          <p>每次升级，只增加上一版无法安全处理的能力。点击下一版，看见 Agent 为什么出现、以及它不能越过的边界。</p>
          <strong>Workflow 管状态 · Audit Agent 管语义 · Exception Agent 管局部恢复</strong>
        </div>
      </header>

      <section className={styles.caseStrip} aria-label="当前案例">
        <div className={styles.caseIdentity}>
          <span>ACTIVE CASE</span>
          <strong>CASE-ZD-042</strong>
        </div>
        <dl>
          <div><dt>产品</dt><dd>宅抵贷</dd></div>
          <div><dt>借款人</dt><dd>张三</dd></div>
          <div><dt>抵押人</dt><dd>李四</dd></div>
          <div><dt>关键事实</dt><dd className={styles.caseDifference}>借款人 ≠ 抵押人</dd></div>
          <div><dt>已收材料</dt><dd>6 / 7</dd></div>
        </dl>
      </section>

      <nav className={styles.versionNav} aria-label="架构版本">
        <ol>
          {VERSION_ORDER.map((itemVersion) => {
            const item = STORIES[itemVersion];
            const isCurrent = itemVersion === version;
            const isComplete = itemVersion < version;
            return (
              <li key={itemVersion} className={isCurrent ? styles.currentVersion : isComplete ? styles.completeVersion : ""}>
                <button
                  type="button"
                  disabled={itemVersion > version}
                  aria-current={isCurrent ? "step" : undefined}
                  onClick={() => isCurrent ? setReplayKey((current) => current + 1) : goToVersion(itemVersion)}
                >
                  <i>{isComplete ? "✓" : `0${itemVersion}`}</i>
                  <span>V{itemVersion}</span>
                  <strong>{item.shortTitle}</strong>
                </button>
              </li>
            );
          })}
        </ol>
        <p aria-live="polite">V{version} · {story.title}</p>
      </nav>

      <section className={styles.workspace}>
        <div className={styles.graphPanel}>
          <div className={styles.panelHeading}>
            <div>
              <span>ARCHITECTURE CANVAS</span>
              <h2>{story.title}</h2>
            </div>
            <p>{story.subtitle}</p>
          </div>
          <ArchitectureGraph version={version} replayKey={replayKey} />
        </div>

        <aside className={styles.storyPanel} aria-label={`V${version} 架构说明`}>
          <header>
            <span>WHY THIS VERSION</span>
            <b>0{version}</b>
          </header>
          <div className={styles.storyItem}>
            <span>01 / 触发问题</span>
            <p>{story.problem}</p>
          </div>
          <div className={styles.storyItem}>
            <span>02 / 架构决策</span>
            <p>{story.decision}</p>
          </div>
          <div className={styles.storyItem}>
            <span>03 / 职责边界</span>
            <p>{story.boundary}</p>
          </div>
          <div className={`${styles.storyItem} ${styles.storyResult}`}>
            <span>04 / 本版结果</span>
            <p>{story.result}</p>
          </div>
        </aside>
      </section>

      <section className={styles.eventSection} aria-label="本版关键事件" key={`events-${version}-${replayKey}`}>
        <div className={styles.eventHeading}>
          <span>KEY GRAPH EVENTS</span>
          <small>只保留能解释路由决策的事件</small>
        </div>
        <ol className={styles.eventList}>
          {story.events.map((event, index) => (
            <li className={`${styles.event} ${styles[`event_${event.tone}`]}`} key={event.type}>
              <i>{String(index + 1).padStart(2, "0")}</i>
              <span><strong>{event.type}</strong><small>{event.detail}</small></span>
            </li>
          ))}
        </ol>
      </section>

      <footer className={styles.controls}>
        <button type="button" className={styles.secondaryButton} onClick={goBack} disabled={version === 1}>
          <span aria-hidden="true">←</span> 上一版
        </button>
        <button type="button" className={styles.replayButton} onClick={() => setReplayKey((current) => current + 1)}>
          <span aria-hidden="true">↻</span> 重播当前版本
        </button>
        {version < 3 ? (
          <button type="button" className={styles.primaryButton} onClick={upgrade}>
            升级到 V{version + 1} · {STORIES[(version + 1) as ArchitectureVersion].shortTitle}
            <span aria-hidden="true">→</span>
          </button>
        ) : onStartDemo ? (
          <button type="button" className={styles.primaryButton} onClick={() => void onStartDemo()} disabled={busy}>
            {busy ? "正在初始化审核…" : "带着这笔案例进入执行演示"}
            <span aria-hidden="true">→</span>
          </button>
        ) : (
          <span className={styles.completeBadge}>架构演进完成 · READY</span>
        )}
      </footer>
    </article>
  );
}
