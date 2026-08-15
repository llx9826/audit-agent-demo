import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000"),
  title: "ARGUS · 宅抵贷智能审核 Agent",
  description: "LangGraph 架构演进、受控 Agent 协作、选择性重规划与制度证据闭环。",
  icons: { icon: "/favicon.svg" },
  openGraph: {
    title: "ARGUS · 宅抵贷智能审核 Agent",
    description: "Workflow 管状态，Agent 管不确定性，Evidence 与 Validator 收口。",
    images: [{ url: "/og-v2.png", width: 1731, height: 909, alt: "ARGUS 宅抵贷智能审核三幕演示" }],
  },
  twitter: { card: "summary_large_image", images: ["/og-v2.png"] },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
