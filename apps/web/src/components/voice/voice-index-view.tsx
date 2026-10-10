"use client";

import { PaperPicker } from "@/components/papers/paper-picker";
import { LiveKitConnectCard } from "@/components/voice/livekit-connect-card";

export function VoiceIndexView() {
  return (
    <div className="space-y-6">
      <div data-animate="block">
        <p className="text-xs text-muted">模式一</p>
        <h1 className="mt-1 font-display text-3xl">语音陪练</h1>
        <p className="mt-2 max-w-xl text-sm text-muted">语音作答走 LiveKit 管理的麦克风输入，识别与朗读由服务端 ASR/TTS 权威处理；流式语音（边说边识别、VAD、打断/暂停）尚未接入。</p>
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
