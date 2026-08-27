import type { AuditEvent, AuditRun, CaseState, HumanResumeCommand, KnowledgeBuildReport, KnowledgeEvent, KnowledgeResult, KnowledgeRun, RagTrace } from "./contracts";

const API_BASE = (process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000").replace(/\/$/, "");
const CASE_BOOTSTRAP_PATH = process.env.NEXT_PUBLIC_CASE_BOOTSTRAP_PATH || "/api/demo/cases/material_completeness";

function describeApiDetail(detail: unknown): string | null {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail.flatMap((item) => {
      if (!isRecord(item)) return [];
      const location = Array.isArray(item.loc)
        ? item.loc.filter((part) => part !== "body").map(String).join(".")
        : "";
      const message = typeof item.msg === "string" ? item.msg : "请求参数不符合接口契约";
      return [`${location ? `${location}：` : ""}${message}`];
    });
    return messages.length ? messages.join("；") : null;
  }
  if (isRecord(detail)) {
    for (const key of ["message", "error", "reason"]) {
      if (typeof detail[key] === "string" && detail[key].trim()) return detail[key];
    }
  }
  return null;
}

function apiUrl(path: string): string {
  return /^https?:\/\//.test(path) ? path : `${API_BASE}${path.startsWith("/") ? path : `/${path}`}`;
}

export const materialAssetUrl = apiUrl;

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "content-type": "application/json" } : {}),
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as unknown;
    const detail = isRecord(payload) ? describeApiDetail(payload.detail ?? payload.message) : null;
    throw new Error(detail ?? `请求失败（HTTP ${response.status}）`);
  }
  return response.json() as Promise<T>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function parseFrame(frame: string): AuditEvent | null {
  const data = frame.split(/\r?\n/)
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n");
  if (!data) return null;
  const parsed: unknown = JSON.parse(data);
  if (!isRecord(parsed) || typeof parsed.seq !== "number" || typeof parsed.event_type !== "string") {
    throw new Error("SSE 事件不符合 AuditEvent 契约");
  }
  return parsed as unknown as AuditEvent;
}

async function consumeStream(run: AuditRun, onEvent: (event: AuditEvent) => void): Promise<void> {
  let cursor = run.after_seq;
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const response = await fetch(apiUrl(run.stream_url), {
      headers: { Accept: "text/event-stream", ...(cursor ? { "Last-Event-ID": String(cursor) } : {}) },
    });
    if (!response.ok || !response.body) throw new Error(`${response.status} ${response.statusText}`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const frames = buffer.split(/\r?\n\r?\n/);
      buffer = frames.pop() ?? "";
      for (const frame of frames) {
        const event = parseFrame(frame);
        if (event && event.seq > cursor) {
          cursor = event.seq;
          onEvent(event);
        }
      }
      if (done) break;
    }
    const status = await requestJson<AuditRun>(`/api/runs/${run.run_id}`);
    if (status.status === "FAILED") throw new Error(status.error ?? "运行失败");
    if (["PAUSED", "COMPLETED"].includes(status.status)) return;
    await new Promise((resolve) => setTimeout(resolve, 250 * (attempt + 1)));
  }
  throw new Error("SSE 重连超过上限");
}

function parseKnowledgeFrame(frame: string): KnowledgeEvent | null {
  const data = frame.split(/\r?\n/)
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n");
  if (!data) return null;
  const parsed: unknown = JSON.parse(data);
  if (!isRecord(parsed) || typeof parsed.seq !== "number" || typeof parsed.event_type !== "string" || !isRecord(parsed.payload)) {
    throw new Error("SSE 事件不符合 KnowledgeEvent 契约");
  }
  return parsed as unknown as KnowledgeEvent;
}

async function consumeKnowledgeStream(
  run: KnowledgeRun,
  onEvent: (event: KnowledgeEvent) => void,
): Promise<KnowledgeRun> {
  let cursor = 0;
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const response = await fetch(apiUrl(run.stream_url), {
      headers: { Accept: "text/event-stream", ...(cursor ? { "Last-Event-ID": String(cursor) } : {}) },
    });
    if (!response.ok || !response.body) throw new Error(`${response.status} ${response.statusText}`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const frames = buffer.split(/\r?\n\r?\n/);
      buffer = frames.pop() ?? "";
      for (const frame of frames) {
        const event = parseKnowledgeFrame(frame);
        if (event && event.seq > cursor) {
          cursor = event.seq;
          onEvent(event);
        }
      }
      if (done) break;
    }
    const status = await requestJson<KnowledgeRun>(`/api/knowledge/runs/${run.run_id}`);
    if (status.status === "FAILED") throw new Error(status.error ?? "知识检索运行失败");
    if (status.status === "COMPLETED") return status;
    await new Promise((resolve) => setTimeout(resolve, 250 * (attempt + 1)));
  }
  throw new Error("知识库 SSE 重连超过上限");
}

export const materialAuditApi = {
  createCase: () => requestJson<CaseState>(CASE_BOOTSTRAP_PATH, { method: "POST" }),
  getCase: (caseId: string) => requestJson<CaseState>(`/api/cases/${caseId}/state`),
  getRagTrace: (caseId: string) => requestJson<RagTrace>(`/api/cases/${caseId}/rag-trace`),
  startRun: (caseId: string) => requestJson<AuditRun>(`/api/cases/${caseId}/runs`, { method: "POST" }),
  resumeRun: (caseId: string, command: HumanResumeCommand) => requestJson<AuditRun>(`/api/cases/${caseId}/resume-runs`, {
    method: "POST",
    body: JSON.stringify(command),
  }),
  consumeStream,
  startKnowledgeRun: (question: string) => requestJson<KnowledgeRun>("/api/knowledge/runs", {
    method: "POST",
    body: JSON.stringify({ question }),
  }),
  consumeKnowledgeStream,
  queryKnowledge: (question: string) => requestJson<KnowledgeResult>("/api/knowledge/queries", {
    method: "POST",
    body: JSON.stringify({ question }),
  }),
  getKnowledgeBuild: () => requestJson<KnowledgeBuildReport>("/api/knowledge/build"),
};
