"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import type { ExamReport, Submission } from "@/lib/types";
import { ReviewView } from "@/components/review-view";
import { ErrorState, LoadingState } from "@/components/states";

export default function ReviewPage() {
  const params = useParams<{ examId: string }>();
  const [submission, setSubmission] = useState<Submission | null>(null);
  const [report, setReport] = useState<ExamReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!params.examId) return;
    const examId = params.examId;
    // submission 是主体；report 是 M2-11 增强（旧数据可能 404），失败静默降级
    api.submission(examId).then(setSubmission).catch((cause: Error) => setError(cause.message));
    api
      .report(examId)
      .then(setReport)
      .catch(() => setReport(null));
  }, [params.examId]);

  if (error) return <ErrorState title="无法生成审阅报告" detail={error} />;
  if (!submission) return <LoadingState title="正在生成审阅报告" detail="正在从服务端取回你的作答与判分。" />;
  return <ReviewView submission={submission} report={report} />;
}
