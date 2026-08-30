"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import type { Submission } from "@/lib/types";
import { ReviewView } from "@/components/review-view";
import { Card } from "@/components/ui/card";

export default function ReviewPage() {
  const params = useParams<{ examId: string }>();
  const [submission, setSubmission] = useState<Submission | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!params.examId) return;
    api.submission(params.examId).then(setSubmission).catch((cause: Error) => setError(cause.message));
  }, [params.examId]);

  if (error) return <Card className="p-6 text-sm text-bad">{error}</Card>;
  if (!submission) return <Card className="p-6 text-sm text-muted">正在生成审阅报告。</Card>;
  return <ReviewView submission={submission} />;
}
