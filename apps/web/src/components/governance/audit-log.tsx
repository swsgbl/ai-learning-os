"use client";

// M10-02 审计日志视图（admin-only 端点）：actor / action / target / time / summary。
// 只读 + 可刷新；before/after 折叠展示原始变更（均为角色与草稿状态，不含密钥类字段）。
import { useCallback, useEffect, useState } from "react";
import { ChevronDown, RefreshCw } from "lucide-react";
import { ApiError, api } from "@/lib/api";
import type { AuditEntry } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { formatTime } from "./draft-queue";

// action -> 中文摘要；未知 action 如实显示原始值（不虚构语义）
const ACTION_LABELS: Record<string, string> = {
  "source.create": "登记来源",
  "source.verify": "验证来源",
  "source.license_change": "变更来源授权",
  "dag.publish": "发布概念图",
  "course_import.approve": "通过课程导入",
  "course_import.reject": "驳回课程导入",
  "course_generation.approve": "通过课程生成",
  "course_generation.reject": "驳回课程生成",
  "paper_extractor.approve": "通过试卷抽取",
  "paper_extractor.reject": "驳回试卷抽取",
  "variant_generation.approve": "通过变式生成",
  "variant_generation.reject": "驳回变式生成",
  "role.promote": "提升管理员",
  "role.demote": "降级管理员",
};

function actionLabel(action: string): string {
  return ACTION_LABELS[action] ?? action;
}

function JsonBlock({ title, payload }: { title: string; payload: Record<string, unknown> | null }) {
  if (!payload) return null;
  return (
    <div className="min-w-0">
      <p className="text-xs font-medium text-muted">{title}</p>
      <pre className="mt-1 overflow-x-auto rounded-md bg-bg p-2 font-mono text-xs text-ink/80">
        {JSON.stringify(payload, null, 2)}
      </pre>
    </div>
  );
}

function AuditRow({ entry }: { entry: AuditEntry }) {
  const [open, setOpen] = useState(false);
  return (
    <Card className="overflow-hidden">
      <button
        type="button"
        className="flex w-full items-start justify-between gap-3 p-4 text-left"
        onClick={() => setOpen((value) => !value)}
      >
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="accent">{actionLabel(entry.action)}</Badge>
            <span className="text-xs text-muted">
              {entry.actor_username ?? entry.actor_id ?? "本地模式"}
            </span>
          </div>
          <p className="mt-2 truncate text-xs text-muted">
            目标 {entry.target_type} / {entry.target_id}
          </p>
          <p className="mt-1 text-xs text-subtle tabular-nums">{formatTime(entry.created_at)}</p>
        </div>
        <ChevronDown
          className={cn("mt-1 size-4 shrink-0 text-muted transition-transform", open && "rotate-180")}
        />
      </button>
      {open && (
        <div className="space-y-2 border-t border-border px-4 pt-3 pb-4">
          <p className="text-xs text-muted">
            request id <span className="font-mono">{entry.request_id}</span>
          </p>
          <JsonBlock title="before" payload={entry.before} />
          <JsonBlock title="after" payload={entry.after} />
        </div>
      )}
    </Card>
  );
}

export function AuditLog() {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);

  const reload = useCallback(async () => {
    setError(null);
    setForbidden(false);
    try {
      setEntries(await api.audit(100));
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 403) {
        setForbidden(true);
      } else {
        setError(cause instanceof Error ? cause.message : "审计日志加载失败");
      }
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  if (forbidden) {
    return <Card className="p-5 text-sm text-muted">需要管理员权限才能查看审计日志。</Card>;
  }
  if (error) {
    return (
      <Card className="flex items-center justify-between gap-3 p-5 text-sm text-bad">
        <span>审计日志加载失败：{error}</span>
        <Button variant="outline" size="sm" onClick={() => void reload()}>
          重试
        </Button>
      </Card>
    );
  }
  if (!entries) {
    return <Card className="p-5 text-sm text-muted">正在读取审计日志……</Card>;
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted tabular-nums">最近 {entries.length} 条</p>
        <Button variant="ghost" size="icon" title="刷新审计" onClick={() => void reload()}>
          <RefreshCw />
        </Button>
      </div>
      {entries.length === 0 ? (
        <Card className="p-5 text-sm text-muted">暂无审计记录。</Card>
      ) : (
        <div className="space-y-3">
          {entries.map((entry) => (
            <AuditRow key={entry.id} entry={entry} />
          ))}
        </div>
      )}
    </div>
  );
}
