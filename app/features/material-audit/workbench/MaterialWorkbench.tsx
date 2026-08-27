"use client";
/* eslint-disable @next/next/no-img-element -- 200+ runtime evidence thumbnails bypass the site image pipeline */

import { useMemo, useState } from "react";
import {
  CheckCircle2, ChevronRight, CircleAlert, FileImage, FileQuestion,
  FolderArchive, Search, Sparkles, UserRound,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { materialAssetUrl } from "../api/client";
import type { AuditEvent, CaseState, PageAsset, RagTrace, RequiredMaterialTask } from "../api/contracts";
import { ExecutionInspector } from "../runtime-inspector/ExecutionInspector";
import { eventBody } from "../runtime-inspector/projection";
import { materialLabels, roleLabels, statusLabels } from "../presentation/labels";

const pageStatusLabels: Record<string, string> = {
  VERIFIED: "已核验",
  PROCESSING: "处理中",
  LOW_CONFIDENCE: "低置信度",
  OWNER_AMBIGUOUS: "归属待确认",
  RECOVERY_EXHAUSTED: "恢复未完成",
};

function TaskStatus({ status }: { status: string }) {
  const variant = status === "MATCHED" ? "secondary" : status === "MISSING" || status === "UNREADABLE" ? "destructive" : "outline";
  return <Badge variant={variant}>{statusLabels[status] ?? status}</Badge>;
}

function ChecklistItem({ task, selected, personName, onSelect }: {
  task: RequiredMaterialTask;
  selected: boolean;
  personName: string;
  onSelect: () => void;
}) {
  return (
    <button className={`checklist-item ${selected ? "is-selected" : ""}`} onClick={onSelect}>
      <span className="checklist-icon">{task.status === "MATCHED" ? <CheckCircle2 /> : task.status === "MISSING" ? <FileQuestion /> : <CircleAlert />}</span>
      <span><strong>{materialLabels[task.material_type] ?? task.material_type}</strong><small>{personName} · {task.requirement_id}</small></span>
      <TaskStatus status={task.status} />
      <ChevronRight />
    </button>
  );
}

interface MaterialScope { personId: string | null; role: string | null }

function CaseTree({ state, associationConfirmed, selectedTaskId, onSelectTask, domain, onDomainChange, scope, onScopeChange, domains, tasks }: {
  state: CaseState;
  associationConfirmed: boolean;
  selectedTaskId: string | null;
  onSelectTask: (task: RequiredMaterialTask) => void;
  domain: string;
  onDomainChange: (value: string) => void;
  scope: MaterialScope;
  onScopeChange: (scope: MaterialScope) => void;
  domains: Array<{ name: string; count: number }>;
  tasks: RequiredMaterialTask[];
}) {
  const completed = tasks.filter((task) => task.status === "MATCHED").length;
  return (
    <aside className="case-tree">
      <div className="panel-heading"><span><UserRound /><strong>人员与动态清单</strong></span><Badge variant="outline">{state.audit_plan.length} 项</Badge></div>
      <div className="case-summary">
        <div><strong>{completed}/{tasks.length}</strong><span>{scope.personId ? "当前人员必需材料" : "全案必需材料"}</span></div>
        <Progress value={tasks.length ? (completed / tasks.length) * 100 : 0} />
      </div>
      <ScrollArea className="case-tree-scroll">
        <section className="person-list">
          {!associationConfirmed && <div className="association-pending-card">
            <UserRound /><span><strong>等待进件事实关联</strong><small>当前只有已分类影像；人员、角色和材料归属将在页级 Evidence 经 Agent + Gate 后显示。</small></span>
          </div>}
          {associationConfirmed && state.persons.map((person) => (
            <div className={`person-card ${scope.personId === person.person_id ? "is-selected" : ""}`} key={person.person_id}>
              <button className="person-focus" onClick={() => onScopeChange(scope.personId === person.person_id && !scope.role ? { personId: null, role: null } : { personId: person.person_id, role: null })}>
                <span className="person-avatar">{person.name.slice(0, 1)}</span>
                <span><strong>{person.name}</strong><small>{person.person_id} · 点击聚焦</small></span>
              </button>
              <div className="person-role-chips">{person.roles.map((role) => <button className={scope.personId === person.person_id && scope.role === role ? "is-active" : ""} key={role} onClick={() => onScopeChange({ personId: person.person_id, role })}>{roleLabels[role] ?? role}</button>)}</div>
            </div>
          ))}
        </section>
        <Separator />
        <section className="material-domains">
          <h3>影像卷宗</h3>
          {domains.map((item) => (
            <button className={domain === item.name ? "is-selected" : ""} key={item.name} onClick={() => onDomainChange(item.name)}>
              <FolderArchive /><span>{item.name}</span><strong>{item.count}</strong>
            </button>
          ))}
        </section>
        <Separator />
        <section className="checklist-list">
          <h3>{scope.role ? `${roleLabels[scope.role] ?? scope.role}应交材料` : "应交材料清单"}</h3>
          {tasks.map((task) => (
            <ChecklistItem
              key={task.task_id}
              task={task}
              selected={selectedTaskId === task.task_id}
              personName={state.persons.find((person) => person.person_id === task.person_id)?.name ?? task.person_id}
              onSelect={() => onSelectTask(task)}
            />
          ))}
        </section>
      </ScrollArea>
    </aside>
  );
}

function DocumentWorkspace({ state, associationConfirmed, domain, selectedPage, onSelectPage, selectedTask, pages, scopeLabel, ragTrace, onOpenRag }: {
  state: CaseState;
  associationConfirmed: boolean;
  domain: string;
  selectedPage: PageAsset | null;
  onSelectPage: (page: PageAsset) => void;
  selectedTask: RequiredMaterialTask | null;
  pages: PageAsset[];
  scopeLabel: string;
  ragTrace: RagTrace | null;
  onOpenRag: (requirementId: string) => void;
}) {
  const [visibleCount, setVisibleCount] = useState(24);
  const domainPages = useMemo(() => pages.filter((page) => page.domain === domain), [domain, pages]);
  const page = selectedPage && selectedPage.domain === domain ? selectedPage : domainPages[0] ?? null;
  return (
    <section className="document-workspace">
      <div className="panel-heading">
        <span><FileImage /><strong>{scopeLabel} / {domain || "进件影像"}</strong></span>
        <div><Badge variant="outline">{domainPages.length} 页</Badge><Badge variant="secondary">{pages.length}/{state.pages.length} 页</Badge></div>
      </div>
      <div className="document-main">
        <Card className="document-preview-card">
          <CardHeader>
            <CardTitle>{page?.page_id ?? "选择影像页"}</CardTitle>
            <Badge variant={page?.status === "VERIFIED" ? "secondary" : "outline"}>{page?.status ? pageStatusLabels[page.status] ?? page.status : "—"}</Badge>
          </CardHeader>
          <CardContent>
            {page?.preview_url ? <img src={materialAssetUrl(page.preview_url)} alt={`${page.page_id} 影像预览`} /> : <div className="empty-state"><FileImage />暂无预览</div>}
          </CardContent>
          {page && <div className="page-evidence"><span>{materialLabels[page.material_type ?? ""] ?? "待分类"}</span><code>{associationConfirmed ? page.owner_person_id ?? "OWNER ?" : "待关联"}</code><strong>{Math.round((page.confidence ?? 0) * 100)}%</strong></div>}
        </Card>
        <div className="contact-sheet-wrap">
          <div className="contact-sheet-toolbar"><span><Search />影像联系表</span><small>中等图按需加载</small></div>
          <ScrollArea className="contact-sheet-scroll">
            <div className="contact-sheet">
              {domainPages.slice(0, visibleCount).map((item) => (
                <Tooltip key={item.page_id}>
                  <TooltipTrigger asChild>
                    <button className={`${page?.page_id === item.page_id ? "is-selected" : ""} ${item.status !== "VERIFIED" ? "has-issue" : ""}`} onClick={() => onSelectPage(item)}>
                      {item.thumbnail_url ? <img src={materialAssetUrl(item.thumbnail_url)} alt="" loading="lazy" /> : <FileImage />}
                      <span>{item.page_id.replace("PAGE-", "")}</span>
                    </button>
                  </TooltipTrigger>
                  <TooltipContent>{item.page_id} · {materialLabels[item.material_type ?? ""] ?? item.material_type}</TooltipContent>
                </Tooltip>
              ))}
            </div>
            {visibleCount < domainPages.length && <Button variant="outline" size="sm" className="load-more" onClick={() => setVisibleCount((value) => value + 24)}>显示更多 · {visibleCount}/{domainPages.length}</Button>}
          </ScrollArea>
        </div>
      </div>
      <div className="match-strip">
        <span><Sparkles />当前应交项</span>
        {selectedTask ? <><strong>{selectedTask.requirement_id}</strong><small>{materialLabels[selectedTask.material_type] ?? selectedTask.material_type} · {selectedTask.matched_page_ids.length} 页证据</small><TaskStatus status={selectedTask.status} />{ragTrace?.final_requirements.includes(selectedTask.requirement_id) && <Button size="sm" variant="outline" onClick={() => onOpenRag(selectedTask.requirement_id)}><Search />查看补件依据</Button>}</> : <small>点击左侧清单查看匹配</small>}
      </div>
    </section>
  );
}

export function MaterialWorkbench({ state, events, ragTrace, onOpenRag }: {
  state: CaseState;
  events: AuditEvent[];
  ragTrace: RagTrace | null;
  onOpenRag: (requirementId: string) => void;
}) {
  const firstDomain = state.business_fields.material_manifest?.domains[0]?.name ?? "";
  const [domain, setDomain] = useState(firstDomain);
  const [scope, setScope] = useState<MaterialScope>({ personId: null, role: null });
  const [selectedPage, setSelectedPage] = useState<PageAsset | null>(state.pages.find((page) => page.domain === firstDomain) ?? null);
  const [selectedTask, setSelectedTask] = useState<RequiredMaterialTask | null>(state.audit_plan[0] ?? null);
  const associationConfirmed = state.persons.some((person) => person.confirmed) || events.some((event) => {
    if (event.event_type !== "ASSOCIATION_GATE_EVALUATED") return false;
    return eventBody(event).outcome === "CONFIRMED";
  });
  const scopedTasks = useMemo(() => state.audit_plan.filter((task) =>
    (!scope.personId || task.person_id === scope.personId) && (!scope.role || task.person_role === scope.role)
  ), [scope, state.audit_plan]);
  const scopedTypes = useMemo(() => new Set(scopedTasks.map((task) => task.material_type)), [scopedTasks]);
  const scopedPages = useMemo(() => !scope.personId ? state.pages : state.pages.filter((page) =>
    (page.owner_person_id === scope.personId || page.status === "OWNER_AMBIGUOUS")
    && (!scope.role || (page.material_type ? scopedTypes.has(page.material_type) : false))
  ), [scope, scopedTypes, state.pages]);
  const scopedDomains = useMemo(() => Array.from(new Set(scopedPages.map((page) => page.domain))).map((name) => ({
    name,
    count: scopedPages.filter((page) => page.domain === name).length,
  })), [scopedPages]);
  const scopePerson = state.persons.find((person) => person.person_id === scope.personId);
  const scopeLabel = scopePerson ? `${scopePerson.name}${scope.role ? ` · ${roleLabels[scope.role] ?? scope.role}` : ""}` : "全案";
  const activeDomain = scopedDomains.some((item) => item.name === domain) ? domain : scopedDomains[0]?.name ?? "";
  const activeTask = scopedTasks.find((task) => task.task_id === selectedTask?.task_id) ?? scopedTasks[0] ?? null;
  const activePage = scopedPages.find((page) => page.page_id === selectedPage?.page_id && page.domain === activeDomain)
    ?? scopedPages.find((page) => page.domain === activeDomain) ?? null;

  function selectTask(task: RequiredMaterialTask) {
    setSelectedTask(task);
  }
  return (
    <div className="workbench-shell">
      <ResizablePanelGroup orientation="horizontal">
        <ResizablePanel defaultSize="22%" minSize="19%" maxSize="28%">
          <CaseTree state={state} associationConfirmed={associationConfirmed} selectedTaskId={activeTask?.task_id ?? null} onSelectTask={selectTask} domain={activeDomain} onDomainChange={(value) => { setDomain(value); setSelectedPage(scopedPages.find((page) => page.domain === value) ?? null); }} scope={scope} onScopeChange={setScope} domains={scopedDomains} tasks={scopedTasks} />
        </ResizablePanel>
        <ResizableHandle withHandle />
        <ResizablePanel defaultSize="47%" minSize="38%">
          <DocumentWorkspace state={state} associationConfirmed={associationConfirmed} domain={activeDomain} selectedPage={activePage} onSelectPage={setSelectedPage} selectedTask={activeTask} pages={scopedPages} scopeLabel={scopeLabel} ragTrace={ragTrace} onOpenRag={onOpenRag} />
        </ResizablePanel>
        <ResizableHandle withHandle />
        <ResizablePanel defaultSize="31%" minSize="25%" maxSize="38%">
          <ExecutionInspector state={state} events={events} selectedTask={activeTask} onSelectTask={setSelectedTask} />
        </ResizablePanel>
      </ResizablePanelGroup>
      {ragTrace && <span className="sr-only">{ragTrace.final_requirements.length} 个应交项已绑定依据</span>}
    </div>
  );
}
