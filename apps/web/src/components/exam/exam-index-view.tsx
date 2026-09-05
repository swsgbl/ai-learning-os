"use client";

import { PaperPicker } from "@/components/papers/paper-picker";

export function ExamIndexView() {
  return (
    <div className="space-y-6">
      <div data-animate="block">
        <p className="text-xs text-muted">模式二</p>
        <h1 className="mt-1 font-display text-3xl">考场审阅</h1>
        <p className="mt-2 max-w-xl text-sm text-muted">倒计时由服务器写入并校准，交卷后进入逐题审阅。</p>
      </div>
      <div data-animate="block">
        <PaperPicker />
      </div>
    </div>
  );
}
