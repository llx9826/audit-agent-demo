"use client";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { materialAuditApi } from "../api/client";
import type { AuditEvent, AuditRun, CaseState, HumanResumeCommand, RagTrace } from "../api/contracts";

interface EventState {
  ordered: AuditEvent[];
  keys: Set<string>;
}

function eventReducer(state: EventState, incoming: AuditEvent[]): EventState {
  const keys = new Set(state.keys);
  const fresh = incoming.filter((event) => {
    const key = `${event.run_id ?? "none"}:${event.seq}`;
    if (keys.has(key)) return false;
    keys.add(key);
    return true;
  });
  return fresh.length ? { keys, ordered: [...state.ordered, ...fresh].sort((a, b) => a.seq - b.seq) } : state;
}

export function useAuditRun() {
  const [caseState, setCaseState] = useState<CaseState | null>(null);
  const [ragTrace, setRagTrace] = useState<RagTrace | null>(null);
  const [events, dispatchEvents] = useReducer(eventReducer, { ordered: [], keys: new Set<string>() });
  const [activeRun, setActiveRun] = useState<AuditRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const eventQueue = useRef<AuditEvent[]>([]);
  const frameRef = useRef<number | null>(null);

  const queueEvent = useCallback((event: AuditEvent) => {
    eventQueue.current.push(event);
    if (frameRef.current !== null) return;
    frameRef.current = requestAnimationFrame(() => {
      dispatchEvents(eventQueue.current.splice(0));
      frameRef.current = null;
    });
  }, []);

  useEffect(() => () => {
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
  }, []);

  const refresh = useCallback(async (caseId: string) => {
    const [state, trace] = await Promise.all([
      materialAuditApi.getCase(caseId),
      materialAuditApi.getRagTrace(caseId),
    ]);
    setCaseState(state);
    setRagTrace(trace);
    return state;
  }, []);

  const initialize = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      const created = await materialAuditApi.createCase();
      setCaseState(created);
      return created;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "进件初始化失败");
      throw reason;
    } finally {
      setBusy(false);
    }
  }, []);

  const execute = useCallback(async (caseId: string, command?: HumanResumeCommand) => {
    setBusy(true);
    setError("");
    try {
      const run = command
        ? await materialAuditApi.resumeRun(caseId, command)
        : await materialAuditApi.startRun(caseId);
      setActiveRun(run);
      await materialAuditApi.consumeStream(run, queueEvent);
      return await refresh(caseId);
    } catch (reason) {
      // 失败也要拉取后端最新 Checkpoint/Thread/活动节点，
      // 否则页面会把已终止的 Run 误显示成“仍在卡住”。
      await refresh(caseId).catch(() => undefined);
      setError(reason instanceof Error ? reason.message : "审核运行失败");
      throw reason;
    } finally {
      setBusy(false);
    }
  }, [queueEvent, refresh]);

  return {
    caseState,
    ragTrace,
    events: events.ordered,
    activeRun,
    busy,
    error,
    clearError: () => setError(""),
    initialize,
    start: () => caseState ? execute(caseState.case_id) : Promise.reject(new Error("请先初始化进件")),
    resume: (command: HumanResumeCommand) => caseState ? execute(caseState.case_id, command) : Promise.reject(new Error("请先初始化进件")),
  };
}
