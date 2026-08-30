import { Card } from "@/components/ui/card";

export default function ProgressPage() {
  return (
    <div className="space-y-6">
      <div>
        <p className="text-xs text-muted">学习记录</p>
        <h1 className="mt-1 font-display text-3xl">掌握</h1>
        <p className="mt-2 max-w-xl text-sm text-muted">掌握度将在 M3 从服务端学习事件重放计算。</p>
      </div>
      <Card className="p-6">
        <p className="text-sm text-muted">服务端 Student Model 尚未接入。当前先保证考试事件、评分与复盘链路可信。</p>
      </Card>
    </div>
  );
}
