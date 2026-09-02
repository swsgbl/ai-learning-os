"use client";

// M9-03 Web 认证客户端：token 持久化 + 登录/注册/状态探测。
// token 存 localStorage（单机自托管版取舍：无 BFF 层，cookie 方案属后续演进）。

const TOKEN_KEY = "aios_token";
const NAME_KEY = "aios_username";

export type AuthState =
  | { mode: "disabled" } // API 未配置 AUTH_SECRET：本地模式，如实透出
  | { mode: "anonymous" } // 需要登录但本地无凭据
  | { mode: "authenticated"; token: string; username: string };

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function getUsername(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(NAME_KEY);
}

export function saveSession(token: string, username: string) {
  window.localStorage.setItem(TOKEN_KEY, token);
  window.localStorage.setItem(NAME_KEY, username);
}

export function clearSession() {
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(NAME_KEY);
}

export interface MeInfo {
  id: string;
  username: string;
}

/** 认证探测：后端开关（auth_enabled）优先，其次本地 token 有效性。 */
export async function probeAuth(apiBase: string): Promise<AuthState> {
  const status = await fetch(`${apiBase}/api/v1/auth/status`, { cache: "no-store" });
  if (status.ok) {
    const body = (await status.json()) as { auth_enabled: boolean };
    if (!body.auth_enabled) return { mode: "disabled" };
  }
  const token = getToken();
  if (!token) return { mode: "anonymous" };
  const me = await fetch(`${apiBase}/api/v1/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  if (!me.ok) {
    clearSession(); // token 过期/无效：静默降级为未登录
    return { mode: "anonymous" };
  }
  const user = (await me.json()) as MeInfo;
  return { mode: "authenticated", token, username: user.username };
}
