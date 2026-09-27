import type { NextConfig } from "next";

import { normalizeAcceptanceApiProxy } from "./src/lib/acceptance-proxy";
import { normalizeBasePath } from "./src/lib/base-path";

// M14-35 验收拓扑：生产 API 的 CORS 是精确 allowlist（仅 3000/3011 源），
// 本地验收在任意空闲端口起 web 时，用 env 门控的同源 rewrite 把 /api/*
// 转发到上游 API —— 浏览器全程同源，无需放行新 CORS 源。
// AIOS_ACCEPTANCE_API_PROXY 经结构化校验（仅 http/https、去尾斜杠、
// 查询串/片段拒绝）：非法值在构建期抛错 fail-closed；
// 未设置时 normalize 返回 null，构建产物与默认构建完全一致（无 rewrite）。
const acceptanceApiProxy = normalizeAcceptanceApiProxy(process.env.AIOS_ACCEPTANCE_API_PROXY);

// M14-159 公共 Web base path：构建期 NEXT_PUBLIC_BASE_PATH 经同一结构化
// 校验（未设置/空 = 根路径构建完全不变；唯一允许的非空值恰为 "/aios"，
// 尾斜杠/查询串/片段/任何其他路径构建期抛错 fail-closed）。Next 在
// basePath 下自动为静态资源（/_next/...）与 rewrite source 加前缀，
// acceptance rewrite 的 source 维持无前缀写法不变。
const basePath = normalizeBasePath(process.env.NEXT_PUBLIC_BASE_PATH);

const nextConfig: NextConfig = {
  output: "standalone",
  typedRoutes: false,
  ...(basePath ? { basePath } : {}),
  ...(acceptanceApiProxy
    ? {
        async rewrites() {
          return [{ source: "/api/:path*", destination: `${acceptanceApiProxy}/api/:path*` }];
        },
      }
    : {}),
};

export default nextConfig;
