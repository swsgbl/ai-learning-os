"use client";

import { PaperPicker } from "@/components/papers/paper-picker";
import { LiveKitConnectCard } from "@/components/voice/livekit-connect-card";

export function VoiceIndexView() {
  return (
    <div className="space-y-6">
      <div data-animate="block">
        <p className="text-xs text-muted">模式一</p>
        <h1 className="mt-1 font-display text-3xl">语音陪练</h1>
        <p className="mt-2 max-w-xl text-sm text-muted">练习仍使用浏览器本地语音；LiveKit 连接检测已接入，完整 ASR/TTS 会话尚未接入。</p>
      </div>
      <div data-animate="block">
        <LiveKitConnectCard />
      </div>
      <div data-animate="block">
        <PaperPicker />
      </div>
    </div>
  );
}
