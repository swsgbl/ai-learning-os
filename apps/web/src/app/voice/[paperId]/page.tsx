import { VoiceStudio } from "@/components/voice-studio";

export default async function VoicePage({ params }: { params: Promise<{ paperId: string }> }) {
  const { paperId } = await params;
  return <VoiceStudio paperId={paperId} />;
}
