import type { Metadata } from "next";
import { DownloadPanel } from "@/components/download/download-panel";

export const metadata: Metadata = {
  title: "下载与安装 — AI Learning OS",
  description: "把砚席安装到手机：PWA 现在可用，原生应用就绪后同步提供。",
};

// M14-164：通用手机下载/安装入口（可用工具页，不做营销 hero）。
// 渠道状态与文案的单一事实来源是 src/lib/download.ts。
export default function DownloadPage() {
  return <DownloadPanel />;
}
