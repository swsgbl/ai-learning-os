"use client";

// M10-03 Web 认证客户端：登录态由 API 的 HttpOnly cookie 承载。
// 页面 JavaScript 不读取 access token；legacy aios_token 只做迁移清理。

const TOKEN_KEY = "aios_token";
const NAME_KEY = "aios_username";

export type AuthState =
  | { mode: "disabled" } // API 未配置 AUTH_SECRET：本地模式，如实透出
  | { mode: "anonymous" } // 需要登录但本地无凭据
  | { mode: "authenticated"; username: string; role?: "learner" | "admin" };

export function getUsername(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(NAME_KEY);
}

export function saveSession(username: string) {
  // 兼容 M9-M10 旧版本：升级后页面不再持有 JWT。
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.setItem(NAME_KEY, username);
}

export function clearSession() {
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(NAME_KEY);
}

export interface MeInfo {
  id: string;
  username: string;
  role?: "learner" | "admin"; // M10-02: 旧 API 无此字段时视为 learner
}

/** 认证探测：后端开关（auth_enabled）优先，其次本地 token 有效性。 */
export async function probeAuth(apiBase: string): Promise<AuthState> {
  const status = await fetch(`${apiBase}/api/v1/auth/status`, {
    credentials: "include",
    cache: "no-store",
  });
  if (status.ok) {
    const body = (await status.json()) as { auth_enabled: boolean };
    if (!body.auth_enabled) return { mode: "disabled" };
  }
  const me = await fetch(`${apiBase}/api/v1/auth/me`, {
    credentials: "include",
    cache: "no-store",
  });
  if (!me.ok) {
    clearSession(); // token 过期/无效：静默降级为未登录
    return { mode: "anonymous" };
  }
  const user = (await me.json()) as MeInfo;
  return { mode: "authenticated", username: user.username, role: user.role };
}

export async function logout(apiBase: string): Promise<void> {
  await fetch(`${apiBase}/api/v1/auth/logout`, {
    method: "POST",
    credentials: "include",
    cache: "no-store",
  }).catch(() => undefined);
  clearSession();
}
