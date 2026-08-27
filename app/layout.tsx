import type { Metadata } from "next";
import "./globals.css";
import { Geist } from "next/font/google";
import { cn } from "@/lib/utils";

const geist = Geist({subsets:['latin'],variable:'--font-sans'});

export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000"),
  title: "ARGUS · 进件材料齐套审核 Agent",
  description: "LangGraph Workflow、受控 Agent、补件依据 RAG、HITL 与选择性重规划。",
  icons: { icon: "/favicon.svg" },
  openGraph: {
    title: "ARGUS · 进件材料齐套审核 Agent",
    description: "Workflow 管状态，Agent 管材料歧义，应交项与 Evidence 收口。",
    images: [{ url: "/og-v2.png", width: 1731, height: 909, alt: "ARGUS 宅抵贷智能审核三幕演示" }],
  },
  twitter: { card: "summary_large_image", images: ["/og-v2.png"] },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN" className={cn("font-sans", geist.variable)}>
      <body>{children}</body>
    </html>
  );
}
