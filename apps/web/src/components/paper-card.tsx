import Link from "next/link";
import { Headphones, Timer } from "lucide-react";
import type { PaperSummary } from "@/lib/types";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card } from "./ui/card";

const DIFFICULTY = { intro: "入门", core: "核心", advanced: "进阶" } as const;

export function PaperCard({ paper, compact = false }: { paper: PaperSummary; compact?: boolean }) {
  return (
    <Card className="flex h-full flex-col gap-4 p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs text-muted">
            {paper.university ?? paper.source}
            {paper.year ? ` · ${paper.year}` : ""}
          </p>
          <h3 className="mt-1 font-display text-lg font-medium leading-snug">{paper.title}</h3>
          {!compact && <p className="mt-1 text-sm text-muted">{paper.subtitle}</p>}
        </div>
        <Badge>{DIFFICULTY[paper.difficulty]}</Badge>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {paper.tags.slice(0, 4).map((tag) => (
          <Badge key={tag} className="font-normal">
            {tag}
          </Badge>
        ))}
        <Badge className="font-normal">{paper.duration_minutes} 分钟</Badge>
      </div>
      <div className="mt-auto flex gap-2">
        <Button asChild className="flex-1">
          <Link href={`/voice/${paper.id}`}>
            <Headphones />
            语音陪练
          </Link>
        </Button>
        <Button asChild variant="outline" className="flex-1">
          <Link href={`/exam/${paper.id}`}>
            <Timer />
            考场
          </Link>
        </Button>
      </div>
    </Card>
  );
}
