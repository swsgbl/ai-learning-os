// M14-35 R1：验收代理 env（AIOS_ACCEPTANCE_API_PROXY）的结构化校验与规范化。
// next.config 在构建期调用。契约：
//   未设置 / 空串        → null（不生成 rewrite，默认构建行为完全不变）
//   合法 http(s) URL     → 规范化目标（去尾斜杠）
//   其他任何值           → 抛错中止构建（fail-closed，绝不静默生成异常 rewrite）
// 错误信息不回显 env 原值（可能含内网拓扑），只说明非法原因。

const ALLOWED_PROTOCOLS: ReadonlySet<string> = new Set(["http:", "https:"]);

export function normalizeAcceptanceApiProxy(raw: string | undefined | null): string | null {
  if (raw === undefined || raw === null || raw === "") return null;

  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    throw new Error("AIOS_ACCEPTANCE_API_PROXY 非法：不是可解析的绝对 URL");
  }
  if (!ALLOWED_PROTOCOLS.has(parsed.protocol)) {
    throw new Error("AIOS_ACCEPTANCE_API_PROXY 非法：仅允许 http/https 协议");
  }
  if (parsed.search || parsed.hash) {
    throw new Error("AIOS_ACCEPTANCE_API_PROXY 非法：不允许携带查询串或片段");
  }
  // 规范化：仅保留 origin + pathname（去全部尾斜杠；根路径 "/" 时即 origin 本身）
  return `${parsed.origin}${parsed.pathname}`.replace(/\/+$/, "");
}
