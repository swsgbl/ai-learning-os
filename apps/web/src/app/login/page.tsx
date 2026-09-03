"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { LogIn, UserPlus, EyeOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { API_BASE } from "@/lib/api";
import { saveSession } from "@/lib/auth";

type Tab = "login" | "register";

const USERNAME_HINT = "3-32 位字母/数字/下划线";
const PASSWORD_HINT = "至少 8 位";

export default function LoginPage() {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [disabledMode, setDisabledMode] = useState(false);

  useEffect(() => {
    // 后端未配置 AUTH_SECRET 时如实告知（不虚装登录可用）
    fetch(`${API_BASE}/api/v1/auth/status`, { cache: "no-store", credentials: "include" })
      .then((r) => r.json())
      .then((b: { auth_enabled: boolean }) => setDisabledMode(!b.auth_enabled))
      .catch(() => setError("无法连接学习服务"));
  }, []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const path = tab === "login" ? "/api/v1/auth/login" : "/api/v1/auth/register";
      const res = await fetch(`${API_BASE}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ username, password }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
        throw new Error(typeof body.detail === "string" ? body.detail : "请求失败");
      }
      if (tab === "register") {
        // 注册成功自动登录
        const login = await fetch(`${API_BASE}/api/v1/auth/login`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ username, password }),
        });
        if (!login.ok) throw new Error("注册成功，请登录");
        saveSession(username);
      } else {
        saveSession(username);
      }
      router.push("/");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "请求失败");
    } finally {
      setBusy(false);
    }
  }

  if (disabledMode) {
    return (
      <div className="mx-auto max-w-md pt-10">
        <Card className="p-6">
          <h1 className="flex items-center gap-2 font-display text-xl font-medium">
            <EyeOff className="size-5" /> 本地模式
          </h1>
          <p className="mt-2 text-sm text-muted">
            当前部署未启用账号登录（API 未配置 AUTH_SECRET），学习数据保存在本机、无需登录。
          </p>
          <Button variant="outline" className="mt-5 w-full" onClick={() => router.push("/")}>
            返回首页
          </Button>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-md pt-10">
      <Card className="p-6">
          <h1 className="font-display text-xl font-medium">
            {tab === "login" ? "登录" : "注册"}
          </h1>
          <p className="mt-2 text-sm text-muted">
            登录后你的资源、考试与语音记录将按账号隔离保存。
          </p>
          <div className="mt-5">
          <div className="mb-5 grid grid-cols-2 gap-1 rounded-lg bg-surface-2 p-1">
            {(["login", "register"] as const).map((key) => (
              <button
                key={key}
                type="button"
                onClick={() => {
                  setTab(key);
                  setError(null);
                }}
                className={`rounded-md px-3 py-2 text-sm transition-colors ${
                  tab === key ? "bg-surface text-ink shadow-border" : "text-muted hover:text-ink"
                }`}
              >
                {key === "login" ? "登录" : "注册"}
              </button>
            ))}
          </div>
          <form onSubmit={submit} className="space-y-4">
            <div className="space-y-1.5">
              <label htmlFor="username" className="text-xs text-muted">
                用户名（{USERNAME_HINT}）
              </label>
              <Input
                id="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                required
                minLength={3}
                maxLength={32}
                pattern="[a-zA-Z0-9_]+"
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="password" className="text-xs text-muted">
                密码（{PASSWORD_HINT}）
              </label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={tab === "login" ? "current-password" : "new-password"}
                required
                minLength={8}
                maxLength={128}
              />
            </div>
            {error && <p className="text-sm text-bad">{error}</p>}
            <Button type="submit" className="w-full" disabled={busy || !username || !password}>
              {tab === "login" ? <LogIn className="size-4" /> : <UserPlus className="size-4" />}
              {busy ? "请稍候…" : tab === "login" ? "登录" : "注册并登录"}
            </Button>
          </form>
          </div>
      </Card>
    </div>
  );
}
