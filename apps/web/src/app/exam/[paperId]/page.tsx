import { ExamStudio } from "@/components/exam-studio";

export default async function ExamPage({ params }: { params: Promise<{ paperId: string }> }) {
  const { paperId } = await params;
  return <ExamStudio paperId={paperId} />;
}
