import Link from "next/link";
import { Headphones, Timer } from "lucide-react";
import type { PaperSummary } from "@/lib/types";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card } from "./ui/card";

const DIFFICULTY = { intro: "入门", core: "核心", advanced: "进阶" } as const;

// M11-01 试卷卡：hover/focus 用 transform 提升反馈（可合成，无布局抖动），
// 双入口（语音/考场）保持 ≥44px 触控目标。
export function PaperCard({ paper, compact = false }: { paper: PaperSummary; compact?: boolean }) {
  return (
    <Card className="flex h-full flex-col gap-4 p-5 transition-[transform,box-shadow] duration-150 ease-[var(--ease-out)] hover:-translate-y-0.5 hover:shadow-border-hover">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs text-muted">
            {paper.university ?? paper.source}
            {paper.year ? ` · ${paper.year}` : ""}
          </p>
          <h3 className="mt-1 font-display text-lg font-medium leading-snug">{paper.title}</h3>
          {!compact && <p className="mt-1 text-sm text-muted">{paper.subtitle}</p>}
        </div>
        <Badge tone="mute">{DIFFICULTY[paper.difficulty]}</Badge>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {paper.tags.slice(0, 4).map((tag) => (
          <Badge key={tag} className="font-normal">
            {tag}
          </Badge>
        ))}
        <Badge className="font-normal">{paper.duration_minutes} 分钟</Badge>
      </div>
      <div className="mt-auto flex flex-wrap gap-2">
        <Button asChild className="min-w-[calc(50%-0.25rem)] flex-1">
          <Link href={`/voice/${paper.id}`}>
            <Headphones aria-hidden="true" />
            语音陪练
          </Link>
        </Button>
        <Button asChild variant="outline" className="min-w-[calc(50%-0.25rem)] flex-1">
          <Link href={`/exam/${paper.id}`}>
            <Timer aria-hidden="true" />
            考场
          </Link>
        </Button>
      </div>
    </Card>
  );
}
