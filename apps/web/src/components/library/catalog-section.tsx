"use client";

// M18-002 找教材（审计升级包 UX「找课程/教材」入口）：ChinaTextbook 目录
// Mode A——只搜元数据与外链（许可状态如实展示；未知许可 = 不镜像原文，
// 打开来源跳转 GitHub raw）。
import { useCallback, useEffect, useState } from "react";
import { ExternalLink, Search } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { CatalogResource } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

const PERMISSION_LABELS: Record<string, string> = {
  metadata_only: "目录模式（未验证许可——仅外链）",
  user_private: "私有导入",
  approved_shared: "已授权共享",
  review_required: "许可审核中",
  blocked: "已阻断",
  unknown: "许可未知",
};

export function CatalogSection() {
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [results, setResults] = useState<CatalogResource[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const search = useCallback((q: string) => {
    if (!q.trim()) return;
    setLoading(true);
    setError(null);
    api
      .catalogSearch({ q: q.trim(), limit: 30 })
      .then((rows) => {
        setResults(rows);
        setSubmitted(q.trim());
      })
      .catch((cause) => setError(cause instanceof ApiError ? cause.message : "目录搜索失败"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    // 首次默认展示一部分大学数学（有内容感，不空白）
    search("数学");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <Card className="p-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm font-medium">找教材（ChinaTextbook 目录）</p>
          <p className="mt-1 text-xs text-muted">
            中小学到大学教材目录检索——许可未逐册核验，一律目录模式：展示元数据与来源外链，不镜像原文。
          </p>
        </div>
      </div>
      <form
        className="mt-3 flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          search(query);
        }}
      >
        <Input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索书名，如：高等数学、七年级、线性代数"
          aria-label="搜索教材目录"
          className="max-w-md"
        />
        <Button type="submit" size="sm" variant="outline" disabled={loading || !query.trim()}>
          <Search className="size-4" aria-hidden="true" />
          {loading ? "搜索中…" : "搜索"}
        </Button>
      </form>
      {error && <p className="mt-2 text-sm text-destructive">{error}</p>}
      {results !== null && (
        <>
          <p className="mt-3 text-xs text-muted">
            {submitted ? `「${submitted}」` : ""}命中 {results.length} 条
            {results.length >= 30 ? "（前 30）" : ""}
          </p>
          <ul className="mt-2 max-h-80 space-y-1.5 overflow-y-auto pr-1 text-sm">
            {results.map((resource) => (
              <li
                key={resource.id}
                className="flex items-center justify-between gap-3 rounded border px-3 py-2"
              >
                <div className="min-w-0">
                  <p className="truncate font-medium">{resource.title}</p>
                  <p className="mt-0.5 truncate text-xs text-muted">
                    {[resource.education_stage, resource.subject, resource.grade, resource.volume]
                      .filter(Boolean)
                      .join(" · ") || "未分类"}
                    {resource.parts_count > 0 && ` · ${resource.parts_count} 分片`}
                    {" · "}
                    {resource.provider}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Badge tone="mute">
                    {PERMISSION_LABELS[resource.permission_status] ?? resource.permission_status}
                  </Badge>
                  <a
                    href={resource.source_url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-xs text-muted hover:text-ink"
                  >
                    <ExternalLink className="size-3" aria-hidden="true" />
                    来源
                  </a>
                </div>
              </li>
            ))}
            {results.length === 0 && (
              <li className="py-6 text-center text-xs text-muted">
                无命中——换关键词，或等待管理员同步最新目录快照
              </li>
            )}
          </ul>
        </>
      )}
    </Card>
  );
}
