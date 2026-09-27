import type { Metadata, Viewport } from "next";
import { Figtree, Newsreader } from "next/font/google";
import { AppShell } from "@/components/app-shell";
import { ServiceWorkerRegister } from "@/components/pwa/sw-register";
import { APPLE_TOUCH_ICON } from "@/lib/pwa";
import "./globals.css";

const figtree = Figtree({
  subsets: ["latin"],
  variable: "--font-figtree",
});

const newsreader = Newsreader({
  subsets: ["latin"],
  variable: "--font-newsreader",
});

export const metadata: Metadata = {
  title: "AI Learning OS",
  description: "语音陪练、考场审阅与公开学习资源治理。",
  // M14-164：iOS 主屏图标（public/apple-touch-icon.png，180×180 不透明）。
  // Next 静态 metadata icons 不自动加 basePath——路径来自 pwa.ts basePath 契约。
  // manifest.webmanifest 由 app/manifest.ts metadata route 自动注入 <link>。
  icons: {
    apple: [{ url: APPLE_TOUCH_ICON, sizes: "180x180", type: "image/png" }],
  },
};

export const viewport: Viewport = {
  themeColor: "#f6f5f1",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" className={`${figtree.variable} ${newsreader.variable}`}>
      <body>
        <AppShell>{children}</AppShell>
        {/* M14-164：PWA service worker 注册（渐进增强，失败静默） */}
        <ServiceWorkerRegister />
      </body>
    </html>
  );
}
