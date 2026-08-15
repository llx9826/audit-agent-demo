import type { Metadata } from "next";
import AuditWorkbench from "./AuditWorkbench";

export const metadata: Metadata = {
  title: "ARGUS · 宅抵贷智能审核 Agent",
  description: "用一条宅抵贷 Case 展示 LangGraph 架构演进、受控 Agent 协作与 RAG 证据闭环。",
};

export default function Home() {
  return <AuditWorkbench />;
}
