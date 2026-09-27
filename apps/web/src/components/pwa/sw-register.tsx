"use client";

// M14-164：Service Worker 注册（PWA 渐进增强）。注册路径与作用域来自
// basePath 契约常量——root 构建注册 /sw.js（行为不回归），basePath 公共
// 构建注册带前缀路径。注册失败静默：离线能力不可用不影响在线功能。
import { useEffect } from "react";
import { SW_REGISTER_SCOPE, SW_REGISTER_SRC } from "@/lib/pwa";

export function ServiceWorkerRegister() {
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register(SW_REGISTER_SRC, { scope: SW_REGISTER_SCOPE }).catch(
      () => {
        // 静默：SW 注册失败（如非 HTTPS 环境）时 PWA 安装入口仍可用
      },
    );
  }, []);
  return null;
}
