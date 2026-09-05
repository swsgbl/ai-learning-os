"use client";

import { PaperPicker } from "@/components/papers/paper-picker";

export function VoiceIndexView() {
  return (
    <div className="space-y-6">
      <div data-animate="block">
        <p className="text-xs text-muted">模式一</p>
        <h1 className="mt-1 font-display text-3xl">语音陪练</h1>
        <p className="mt-2 max-w-xl text-sm text-muted">本地浏览器语音先行，后续切换 LiveKit 与 FunASR/CosyVoice adapter。</p>
      </div>
      <div data-animate="block">
        <PaperPicker />
      </div>
    </div>
  );
}
