// M14-159 R1：公共 Web base path env（NEXT_PUBLIC_BASE_PATH）的结构化校验。
// next.config 与 src/lib/api.ts 在构建期/模块加载期共同调用。契约：
//   未设置 / 空串  → ""（根路径构建，默认构建行为完全不变）
//   恰为 "/aios"    → "/aios"（唯一允许的非空值）
//   其他任何值      → 抛错（构建期 fail-closed：尾斜杠、查询串、片段、
//                     畸形 URL 或任何其他路径一律拒绝）
// 错误信息不回显 env 原值，只说明合法形态。

export const BASE_PATH_ENV = "NEXT_PUBLIC_BASE_PATH";

export function normalizeBasePath(raw: string | undefined | null): string {
  if (raw === undefined || raw === null || raw === "") return "";
  if (raw !== "/aios") {
    throw new Error(
      'NEXT_PUBLIC_BASE_PATH 非法：唯一允许的非空值是 "/aios"（无尾斜杠、无查询串、无片段）',
    );
  }
  return "/aios";
}
