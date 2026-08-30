"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { PaperSummary } from "@/lib/types";
import { PaperCard } from "@/components/paper-card";
import { Card } from "@/components/ui/card";

export default function ExamIndexPage() {
  const [papers, setPapers] = useState<PaperSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.papers().then(setPapers).catch((cause: Error) => setError(cause.message));
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <p className="text-xs text-muted">模式二</p>
        <h1 className="mt-1 font-display text-3xl">考场审阅</h1>
        <p className="mt-2 max-w-xl text-sm text-muted">倒计时由服务器写入并校准，交卷后进入逐题审阅。</p>
      </div>
      {error && <Card className="p-5 text-sm text-bad">{error}</Card>}
      <div className="grid gap-3 sm:grid-cols-2">
        {papers.map((paper) => (
          <PaperCard key={paper.id} paper={paper} compact />
        ))}
      </div>
    </div>
  );
}
