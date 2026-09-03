"use client";

// M10-02 治理草稿队列：四类草稿端点同构（list/get/approve/reject），
// 本组件用每类的「摘要 / 元数据 / 详情」投影函数泛化，队列交互只写一遍。
// 动作语义：pending_review -> approved | rejected 终态不可逆，重复审核 409 有明确反馈。
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Check, ChevronDown, RefreshCw, X } from "lucide-react";
import { ApiError } from "@/lib/api";
import type { DraftStatus } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

export type DraftMeta = { label: string; value: string };

export type DraftKindConfig<T> = {
  key: string;
  label: string;
  load: () => Promise<T[]>;
  refreshOne: (id: string) => Promise<T>;
  approve: (id: string, note?: string) => Promise<T>;
  reject: (id: string, note?: string) => Promise<T>;
  idOf: (draft: T) => string;
  statusOf: (draft: T) => DraftStatus;
  titleOf: (draft: T) => string;
  metaOf: (draft: T) => DraftMeta[];
  detailOf: (draft: T) => ReactNode;
};

const STATUS_LABEL: Record<DraftStatus, string> = {
  pending_review: "待审",
  approved: "已通过",
  rejected: "已驳回",
};

const STATUS_TONE: Record<DraftStatus, "mute" | "good" | "bad" | "accent"> = {
  pending_review: "accent",
  approved: "good",
  rejected: "bad",
};

const FILTERS: Array<{ key: DraftStatus | "all"; label: string }> = [
  { key: "pending_review", label: "待审" },
  { key: "all", label: "全部" },
  { key: "approved", label: "已通过" },
  { key: "rejected", label: "已驳回" },
];

function formatTime(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return iso;
  }
}

export function DraftQueue<T>({ config }: { config: DraftKindConfig<T> }) {
  const [drafts, setDrafts] = useState<T[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [filter, setFilter] = useState<DraftStatus | "all">("pending_review");
  const [openId, setOpenId] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [acting, setActing] = useState(false);
  const [actionFeedback, setActionFeedback] = useState<
    { kind: "ok" | "conflict" | "error"; text: string } | null
  >(null);

  const reload = useCallback(async () => {
    setError(null);
    setForbidden(false);
    try {
      setDrafts(await config.load());
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 403) {
        setForbidden(true); // 后端权威拒绝：只提示无权限，不泄露队列内容
      } else {
        setError(cause instanceof Error ? cause.message : "队列加载失败");
      }
    }
  }, [config]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const act = async (decision: "approve" | "reject") => {
    if (!openId) return;
    setActing(true);
    setActionFeedback(null);
    try {
      const updated =
        decision === "approve"
          ? await config.approve(openId, note)
          : await config.reject(openId, note);
      setActionFeedback({
        kind: "ok",
        text: decision === "approve" ? "已通过该草稿" : "已驳回该草稿",
      });
      setNote("");
      setDrafts(await config.load()); // 动作后刷新队列
      setDetailDraft(updated); // 详情同步为审核后的版本
      // 终态草稿已不属于「待审」过滤集——切到全部，让刚审的卡片与终态详情保持可见
      setFilter("all");
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 409) {
        setActionFeedback({
          kind: "conflict",
          text: "该草稿已被审核过（终态不可逆），已为您刷新到最新状态",
        });
        try {
          setDrafts(await config.load());
          setDetailDraft(await config.refreshOne(openId));
          setFilter("all");
        } catch {
          // 刷新失败保留原状态；手动刷新仍可用
        }
      } else {
        setActionFeedback({
          kind: "error",
          text: cause instanceof Error ? cause.message : "操作失败",
        });
      }
    } finally {
      setActing(false);
    }
  };

  // 展开详情时持有的最新版本（详情接口 / 动作结果写入，比列表行更实时）
  const [detailDraft, setDetailDraft] = useState<T | null>(null);

  if (forbidden) {
    return (
      <Card className="p-5 text-sm text-muted">需要管理员权限才能查看该队列。</Card>
    );
  }
  if (error) {
    return (
      <Card className="flex items-center justify-between gap-3 p-5 text-sm text-bad">
        <span>队列加载失败：{error}</span>
        <Button variant="outline" size="sm" onClick={() => void reload()}>
          重试
        </Button>
      </Card>
    );
  }
  if (!drafts) {
    return <Card className="p-5 text-sm text-muted">正在读取队列……</Card>;
  }

  const pending = drafts.filter((draft) => config.statusOf(draft) === "pending_review").length;
  const approved = drafts.filter((draft) => config.statusOf(draft) === "approved").length;
  const rejected = drafts.filter((draft) => config.statusOf(draft) === "rejected").length;
  const visible = filter === "all" ? drafts : drafts.filter((draft) => config.statusOf(draft) === filter);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2 text-sm text-muted">
          <span className="tabular-nums">
            共 {drafts.length} 条 · 待审 {pending} · 已通过 {approved} · 已驳回 {rejected}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex gap-1 overflow-x-auto">
            {FILTERS.map((item) => (
              <button
                key={item.key}
                type="button"
                onClick={() => setFilter(item.key)}
                className={cn(
                  "rounded-full px-3 py-1.5 text-xs whitespace-nowrap",
                  filter === item.key ? "bg-accent text-accent-fg" : "bg-surface-2 text-muted",
                )}
              >
                {item.label}
              </button>
            ))}
          </div>
          <Button variant="ghost" size="icon" title="刷新队列" onClick={() => void reload()}>
            <RefreshCw className={cn(drafts === null && "animate-spin")} />
          </Button>
        </div>
      </div>

      {visible.length === 0 ? (
        <Card className="p-5 text-sm text-muted">
          {filter === "pending_review" ? "该队列为空：没有等待审核的草稿。" : "该状态下暂无草稿。"}
        </Card>
      ) : (
        <div className="space-y-3">
          {visible.map((draft) => {
            const id = config.idOf(draft);
            const open = openId === id;
            const detail = open ? (detailDraft ?? draft) : null;
            const status = config.statusOf(detail ?? draft);
            return (
              <Card key={id} className="overflow-hidden">
                <button
                  type="button"
                  className="flex w-full items-start justify-between gap-3 p-4 text-left"
                  onClick={() => {
                    const next = open ? null : id;
                    setOpenId(next);
                    setActionFeedback(null);
                    setNote("");
                    setDetailDraft(null); // 展开时先显示列表版本；有新版本由动作写入
                    if (next !== null) {
                      void config
                        .refreshOne(next)
                        .then(setDetailDraft)
                        .catch(() => setDetailDraft(null)); // 详情拉取失败退回列表数据
                    }
                  }}
                >
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone={STATUS_TONE[status]}>{STATUS_LABEL[status]}</Badge>
                      <span className="text-sm font-medium">{config.titleOf(draft)}</span>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
                      {config.metaOf(draft).map((meta) => (
                        <span key={meta.label}>
                          {meta.label}：<span className="text-ink/80">{meta.value}</span>
                        </span>
                      ))}
                    </div>
                  </div>
                  <ChevronDown
                    className={cn(
                      "mt-1 size-4 shrink-0 text-muted transition-transform",
                      open && "rotate-180",
                    )}
                  />
                </button>

                {open && detail && (
                  <div className="border-t border-border px-4 pt-3 pb-4">
                    <div className="text-sm leading-relaxed">{config.detailOf(detail)}</div>

                    <div className="mt-4 rounded-lg bg-bg p-3">
                      {status === "pending_review" ? (
                        <>
                          <input
                            value={note}
                            onChange={(event) => setNote(event.target.value)}
                            maxLength={512}
                            placeholder="审核备注（可选，将写入审计）"
                            className="w-full rounded-lg bg-surface px-3 py-2 text-sm shadow-border outline-none placeholder:text-subtle focus:bg-surface-2"
                          />
                          <div className="mt-2 flex gap-2">
                            <Button
                              size="sm"
                              disabled={acting}
                              onClick={() => void act("approve")}
                            >
                              <Check /> 通过
                            </Button>
                            <Button
                              size="sm"
                              variant="danger"
                              disabled={acting}
                              onClick={() => void act("reject")}
                            >
                              <X /> 驳回
                            </Button>
                          </div>
                        </>
                      ) : (
                        <p className="text-xs text-muted">
                          该草稿已终态（{STATUS_LABEL[status]}），不可再次审核。
                        </p>
                      )}
                      {actionFeedback && (
                        <p
                          className={cn(
                            "mt-2 text-xs",
                            actionFeedback.kind === "ok" && "text-good",
                            actionFeedback.kind === "conflict" && "text-warn",
                            actionFeedback.kind === "error" && "text-bad",
                          )}
                        >
                          {actionFeedback.text}
                        </p>
                      )}
                    </div>
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}

// 四类草稿队列 key：页面组合处按此给出配置（见 governance/page.tsx）
export { formatTime };
