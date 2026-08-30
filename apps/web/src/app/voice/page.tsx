"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { PaperSummary } from "@/lib/types";
import { PaperCard } from "@/components/paper-card";
import { Card } from "@/components/ui/card";

export default function VoiceIndexPage() {
  const [papers, setPapers] = useState<PaperSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.papers().then(setPapers).catch((cause: Error) => setError(cause.message));
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <p className="text-xs text-muted">模式一</p>
        <h1 className="mt-1 font-display text-3xl">语音陪练</h1>
        <p className="mt-2 max-w-xl text-sm text-muted">本地浏览器语音先行，后续切换 LiveKit 与 FunASR/CosyVoice adapter。</p>
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
