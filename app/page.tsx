import type { Metadata } from "next";
import AuditWorkbench from "./AuditWorkbench";

export const metadata: Metadata = {
  title: "ARGUS · 进件材料齐套审核 Agent",
  description: "用一条宅抵贷进件展示 LangGraph、补件依据 RAG、受控 Agent 与 HITL。",
};

export default function Home() {
  return <AuditWorkbench />;
}
