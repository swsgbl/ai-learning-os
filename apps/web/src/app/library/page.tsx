"use client";

import { useEffect, useMemo, useState } from "react";
import { Search } from "lucide-react";
import { api } from "@/lib/api";
import type { PaperSummary } from "@/lib/types";
import { PaperCard } from "@/components/paper-card";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

export default function LibraryPage() {
  const [papers, setPapers] = useState<PaperSummary[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.papers().then(setPapers).catch((cause: Error) => setError(cause.message));
  }, []);

  const filtered = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return papers;
    return papers.filter((paper) =>
      [paper.title, paper.subtitle, paper.source, paper.university ?? "", paper.subject, ...paper.tags].some((value) =>
        value.toLowerCase().includes(keyword),
      ),
    );
  }, [papers, query]);

  return (
    <div className="space-y-8">
      <div>
        <p className="text-xs text-muted">模式三</p>
        <h1 className="mt-1 font-display text-3xl">学习库</h1>
        <p className="mt-2 max-w-xl text-sm text-muted">当前是服务端来源目录；真实多源检索、License Gate 和 Evidence 将在 M1 接入。</p>
      </div>

      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-subtle" />
        <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索课程、学科、学校或标签" className="pl-9" />
      </div>
      {error && <Card className="p-5 text-sm text-bad">{error}</Card>}

      <section>
        <h2 className="font-display text-xl">可练试卷</h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          {filtered.map((paper) => (
            <PaperCard key={paper.id} paper={paper} compact />
          ))}
        </div>
        {filtered.length === 0 && <Card className="mt-3 p-5 text-sm text-muted">没有匹配的服务端题库。</Card>}
      </section>
    </div>
  );
}
