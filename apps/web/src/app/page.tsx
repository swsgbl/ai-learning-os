import { HomeDashboard } from "@/components/home/home-dashboard";

// M11-01：页面保持路由职责，聚合与编排动效在 client leaf（HomeDashboard）。
export default function HomePage() {
  return <HomeDashboard />;
}
