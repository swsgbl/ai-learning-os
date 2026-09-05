import { ProgressWorkbench } from "@/components/progress/progress-workbench";

// M11-01：页面保持路由职责，聚合与动效在 client leaf（ProgressWorkbench）。
export default function ProgressPage() {
  return <ProgressWorkbench />;
}
