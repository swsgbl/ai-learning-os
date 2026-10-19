"use client";

// M15-008 AI 能力密钥治理（admin-only）：三槽位状态 / 在线填写 / 删除回退 env。
// 安全边界与后端一致：密钥永不回显（只有尾 4 位），写入/删除走审计。
// 生效语义如实透出：asr_cloud/tts_cloud 写入即热生效；llm 需重启 api 容器。
import { useCallback, useEffect, useRef, useState } from "react";
import { KeyRound, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { formatTime } from "./draft-queue";

type ProviderSlot = {
  slot: string;
  env_var: string;
  configured: boolean;
  source: string | null;
  last4: string | null;
  updated_at: string | null;
  effect: string;
};

const SLOT_LABELS: Record<string, string> = {
  llm: "LLM（判定/生成）",
  asr_cloud: "云语音识别 ASR",
  tts_cloud: "云语音合成 TTS",
};

function effectLabel(effect: string): string {
  return effect === "hot" ? "写入即生效" : "需重启 api 容器生效";
}

function SlotCard({ slot }: { slot: ProviderSlot }) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const save = useCallback(async () => {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await api.aiProviderPut(slot.slot, value);
      setValue("");
      setSaved(true);
      window.dispatchEvent(new CustomEvent("ai-providers:refresh"));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }, [slot.slot, value]);

  const remove = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      await api.aiProviderDelete(slot.slot);
      window.dispatchEvent(new CustomEvent("ai-providers:refresh"));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "删除失败");
    } finally {
      setBusy(false);
    }
  }, [slot.slot]);

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <KeyRound className="size-4 text-muted" aria-hidden="true" />
          <h3 className="font-display text-base">{SLOT_LABELS[slot.slot] ?? slot.slot}</h3>
        </div>
        {slot.configured ? (
          <Badge tone={slot.source === "db" ? "accent" : "mute"}>
            {slot.source === "db" ? `在线密钥 ····${slot.last4}` : "部署 env 注入"}
          </Badge>
        ) : (
          <Badge tone="bad">未配置</Badge>
        )}
      </div>
      <p className="mt-2 text-xs text-muted">
        env 变量 <code className="font-mono">{slot.env_var}</code>
        {slot.updated_at ? ` · 更新于 ${formatTime(slot.updated_at)}` : ""} · {effectLabel(slot.effect)}
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <input
          type="password"
          className="min-w-0 flex-1 rounded-md border border-border bg-bg px-3 py-2 font-mono text-sm"
          placeholder={slot.configured ? "输入新密钥覆盖（不填则不变）" : "粘贴 API 密钥（只存加密密文）"}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          autoComplete="off"
        />
        <Button size="sm" disabled={busy || value.length < 8} onClick={save}>
          {busy ? "保存中…" : "保存"}
        </Button>
        {slot.source === "db" && (
          <Button size="sm" variant="outline" disabled={busy} onClick={remove}>
            删除（回退 env）
          </Button>
        )}
      </div>
      {saved && <p className="mt-2 text-xs text-accent">已保存（密文落库，审计已记录）。</p>}
      {error && <p className="mt-2 text-xs text-danger">{error}</p>}
    </Card>
  );
}

export function AiProviders() {
  const [slots, setSlots] = useState<ProviderSlot[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setSlots((await api.aiProviders()).slots);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "加载失败");
    }
  }, []);

  useEffect(() => {
    void load();
    const onRefresh = () => void load();
    window.addEventListener("ai-providers:refresh", onRefresh);
    return () => window.removeEventListener("ai-providers:refresh", onRefresh);
  }, [load]);

  if (error) {
    return <ErrorState title="AI 能力状态加载失败" detail={error} onRetry={() => void load()} />;
  }
  if (!slots) {
    return <LoadingState />;
  }

  return (
    <div className="space-y-4" ref={rootRef}>
      <Card className="p-4 text-xs leading-relaxed text-muted">
        AI 密钥是服务端部署配置：在线填写后以 AUTH_SECRET 派生密钥 AES-GCM 加密落库（密钥全文永不回显，
        页面只显示尾 4 位）。删除槽位即回退到部署 env 注入的值。云 ASR/TTS 写入即生效；LLM 判定器在
        应用启动时装配，写入后需重启 api 容器。
      </Card>
      {slots.map((slot) => (
        <SlotCard key={slot.slot} slot={slot} />
      ))}
      <div className="flex justify-end">
        <Button variant="outline" size="sm" onClick={() => void load()}>
          <RefreshCw className="size-4" aria-hidden="true" /> 刷新状态
        </Button>
      </div>
    </div>
  );
}
