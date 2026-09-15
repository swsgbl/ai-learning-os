import type { NextConfig } from "next";

import { normalizeAcceptanceApiProxy } from "./src/lib/acceptance-proxy";

// M14-35 验收拓扑：生产 API 的 CORS 是精确 allowlist（仅 3000/3011 源），
// 本地验收在任意空闲端口起 web 时，用 env 门控的同源 rewrite 把 /api/*
// 转发到上游 API —— 浏览器全程同源，无需放行新 CORS 源。
// AIOS_ACCEPTANCE_API_PROXY 经结构化校验（仅 http/https、去尾斜杠、
// 查询串/片段拒绝）：非法值在构建期抛错 fail-closed；
// 未设置时 normalize 返回 null，构建产物与默认构建完全一致（无 rewrite）。
const acceptanceApiProxy = normalizeAcceptanceApiProxy(process.env.AIOS_ACCEPTANCE_API_PROXY);

const nextConfig: NextConfig = {
  output: "standalone",
  typedRoutes: false,
  ...(acceptanceApiProxy
    ? {
        async rewrites() {
          return [{ source: "/api/:path*", destination: `${acceptanceApiProxy}/api/:path*` }];
        },
      }
    : {}),
};

export default nextConfig;
