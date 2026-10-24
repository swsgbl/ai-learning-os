"use client";

// M19-004 我的教材工作台（审计 02 §7）：导入列表 + 教材→学习链操作。
// 目录模式（M18-002）搜到的书可导入；导入后 解析 → 概念 → 一键冲刺。
import { useCallback, useEffect, useState } from "react";
import { BookOpen, ChevronRight, Loader2 } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

interface ImportJob {
  id: string;
  resource_id: string;
  status: string;
  title: string;
  parse_status: string;
  concept_counts: string;
  size_bytes: number;
  error: string;
}

const PARSE_LABELS: Record<string, string> = {
  parsed: "已解析",
  needs_ocr: "扫描书（需 OCR）",
  pending: "待解析",
};

export function Bookshelf({ onChanged }: { onChanged?: () => void }) {
  const [jobs, setJobs] = useState<ImportJob[] | null>(null);
  const [busyJob, setBusyJob] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [importingId, setImportingId] = useState<string | null>(null);

  const load = useCallback(() => {
    api.learning
      .listImports()
      .then(setJobs)
      .catch(() => setJobs([]));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const run = async (jobId: string, label: string, fn: () => Promise<unknown>) => {
    setBusyJob(jobId);
    setNotice(null);
    try {
      await fn();
      setNotice(`${label}完成`);
      load();
      onChanged?.();
    } catch (cause) {
      setNotice(cause instanceof ApiError ? cause.message : `${label}失败`);
    } finally {
      setBusyJob(null);
    }
  };

  const importResource = async (resourceId: string) => {
    setImportingId(resourceId);
    setNotice(null);
    try {
      await api.learning.createImport(resourceId);
      setNotice("已导入到我的教材（大书下载组装可能需要 1-2 分钟）");
      load();
    } catch (cause) {
      setNotice(cause instanceof ApiError ? cause.message : "导入失败");
    } finally {
      setImportingId(null);
    }
  };

  const busy = busyJob !== null;
  return (
    <Card className="p-4">
      <p className="text-sm font-medium">我的教材</p>
      <p className="mt-1 text-xs text-muted">
        私有导入（你已声明使用权的书）：解析 → 确认概念 → 一键开学习冲刺。目录模式搜到的书先导入才会出现在这里。
      </p>
      {notice && <p className="mt-2 text-xs text-muted">{notice}</p>}
      {jobs === null ? (
        <p className="mt-3 text-xs text-muted">加载中…</p>
      ) : jobs.length === 0 ? (
        <p className="mt-3 py-4 text-center text-xs text-muted">
          还没有导入的教材——在上方「找教材」搜到书后点「导入」开始
        </p>
      ) : (
        <ul className="mt-3 space-y-2">
          {jobs.map((job) => {
            const parsed = job.parse_status === "parsed";
            const needsOcr = job.parse_status === "needs_ocr";
            const hasConcepts = (job.concept_counts || "").includes("confirmed");
            return (
              <li key={job.id} className="rounded border p-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{job.title || job.resource_id}</p>
                    <p className="mt-0.5 text-xs text-muted">
                      {job.status === "ready" ? `${(job.size_bytes / 1048576).toFixed(1)} MB` : job.status}
                      {job.parse_status && ` · ${PARSE_LABELS[job.parse_status] ?? job.parse_status}`}
                      {job.concept_counts && ` · 概念（${job.concept_counts}）`}
                    </p>
                  </div>
                  <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
                    {job.status === "ready" && !job.parse_status && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy}
                        onClick={() => run(job.id, "解析", () => api.learning.parseImport(job.id))}
                      >
                        解析
                      </Button>
                    )}
                    {needsOcr && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy}
                        onClick={() =>
                          run(job.id, "OCR 分批解析", () => api.learning.parseImport(job.id, { ocrMaxPages: 15 }))
                        }
                      >
                        OCR 解析
                      </Button>
                    )}
                    {parsed && !hasConcepts && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy}
                        onClick={() => run(job.id, "概念提取", () => api.learning.extractConcepts(job.id))}
                      >
                        提取概念
                      </Button>
                    )}
                    {parsed && (
                      <Button
                        size="sm"
                        disabled={busy}
                        onClick={() =>
                          run(job.id, "一键开冲刺", () =>
                            api.learning.createSprintFromTextbook(job.id).then(() => undefined),
                          )
                        }
                      >
                        <BookOpen className="size-4" aria-hidden="true" />
                        一键开冲刺
                      </Button>
                    )}
                    {busyJob === job.id && <Loader2 className="size-4 animate-spin" aria-hidden="true" />}
                  </div>
                </div>
                {job.status !== "ready" && job.error && (
                  <p className="mt-1 text-xs text-red-600">{job.error.slice(0, 120)}</p>
                )}
              </li>
            );
          })}
        </ul>
      )}
      <ChevronRight className="hidden" aria-hidden="true" />
      <span className="hidden">{importingId}</span>
    </Card>
  );
}
