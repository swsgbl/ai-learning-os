import { LibraryView } from "@/components/library/library-view";

// M11-01：页面保持路由职责，搜索/过滤/布局过渡在 client leaf（LibraryView）。
export default function LibraryPage() {
  return <LibraryView />;
}
