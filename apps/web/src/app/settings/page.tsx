"use client";

// M15-018/019 Web 个人设置：我的 AI 密钥（多厂商 LLM 密钥库，与 App 同口径）。
// 语音为本机部署无需密钥；判分走当前激活厂商；不配置用服务器默认模型。
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { LlmProfile } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

export default function SettingsPage() {
  const [profiles, setProfiles] = useState<LlmProfile[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [label, setLabel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [model, setModel] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setProfiles(await api.llmProfiles());
      setError(null);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "加载失败");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const add = async () => {
    if (apiKey.length < 8 || !label.trim()) {
      setNotice("请填写厂商名称与至少 8 位密钥");
      return;
    }
    setBusy(true);
    try {
      const hasActive = (profiles ?? []).some((p) => p.active);
      await api.createLlmProfile(
        label.trim(), apiKey, endpoint.trim() || undefined, model.trim() || undefined, !hasActive,
      );
      setNotice("已添加厂商配置");
      setLabel(""); setApiKey(""); setEndpoint(""); setModel(""); setModels([]);
      await load();
    } catch (cause) {
      setNotice(cause instanceof ApiError ? cause.message : "添加失败");
    } finally {
      setBusy(false);
    }
  };

  const probe = async () => {
    setBusy(true);
    setModels([]);
    try {
      const list = await api.probeLlmModels(apiKey || undefined, endpoint || undefined);
      setModels(list);
    } catch (cause) {
      setNotice(cause instanceof ApiError ? cause.message : "获取模型列表失败");
    } finally {
      setBusy(false);
    }
  };

  const activate = async (id: string) => {
    setBusy(true);
    try {
      await api.activateLlmProfile(id);
      setNotice("已切换，判分即时走该厂商");
      await load();
    } catch (cause) {
      setNotice(cause instanceof ApiError ? cause.message : "切换失败");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: string) => {
    setBusy(true);
    try {
      await api.deleteLlmProfile(id);
      setNotice("已删除");
      await load();
    } catch (cause) {
      setNotice(cause instanceof ApiError ? cause.message : "删除失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6 px-4 py-6">
      <div>
        <p className="text-xs text-muted">设置</p>
        <h1 className="mt-1 font-display text-3xl">我的 AI 密钥</h1>
        <p className="mt-2 max-w-xl text-sm text-muted">
          可保存多家厂商密钥（如 DeepSeek / OpenAI / Agnes）自由切换，答题判分即时走当前选中厂商；不配置则用服务器默认模型。语音为本机部署，无需密钥。
        </p>
      </div>

      {error && (
        <Card className="p-4 text-sm text-danger">
          {error}
          <Button variant="outline" size="sm" className="ml-3" onClick={() => void load()}>重试</Button>
        </Card>
      )}

      {profiles && profiles.length > 0 && (
        <Card className="divide-y divide-border">
          {profiles.map((p) => (
            <div key={p.id} className="flex flex-wrap items-center justify-between gap-3 p-4">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{p.label}</span>
                  {p.active && <Badge tone="accent">使用中</Badge>}
                </div>
                <p className="mt-1 truncate text-xs text-muted">
                  {[p.model, `····${p.last4}`].filter(Boolean).join(" · ") || `····${p.last4}`}
                </p>
              </div>
              <div className="flex gap-2">
                {!p.active && (
                  <Button size="sm" disabled={busy} onClick={() => void activate(p.id)}>切换</Button>
                )}
                <Button size="sm" variant="outline" disabled={busy} onClick={() => void remove(p.id)}>删除</Button>
              </div>
            </div>
          ))}
        </Card>
      )}

      <Card className="space-y-3 p-4">
        <h2 className="font-display text-xl">添加厂商</h2>
        <input
          className="w-full rounded-md border border-border bg-bg px-3 py-2 text-sm"
          placeholder="厂商名称（如 DeepSeek）"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
        />
        <input
          className="w-full rounded-md border border-border bg-bg px-3 py-2 font-mono text-sm"
          type="password"
          placeholder="API 密钥（必填，不回显）"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          autoComplete="off"
        />
        <input
          className="w-full rounded-md border border-border bg-bg px-3 py-2 font-mono text-sm"
          placeholder="接口地址（选填，如 https://api.deepseek.com/v1）"
          value={endpoint}
          onChange={(e) => setEndpoint(e.target.value)}
        />
        <div className="flex gap-2">
          <input
            className="min-w-0 flex-1 rounded-md border border-border bg-bg px-3 py-2 font-mono text-sm"
            placeholder="模型名（选填；可点右侧获取）"
            value={model}
            onChange={(e) => setModel(e.target.value)}
          />
          <Button variant="outline" disabled={busy} onClick={() => void probe()}>获取列表</Button>
        </div>
        {models.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {models.slice(0, 12).map((m) => (
              <Button key={m} size="sm" variant="outline" onClick={() => setModel(m)}>{m}</Button>
            ))}
            {models.length > 12 && <span className="text-xs text-muted">共 {models.length} 个</span>}
          </div>
        )}
        <Button disabled={busy} onClick={() => void add()}>添加厂商配置</Button>
        {notice && <p className="text-xs text-muted">{notice}</p>}
      </Card>
    </div>
  );
}
