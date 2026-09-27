// M14-164 通用 PWA manifest（app router metadata route，构建期静态生成
// /manifest.webmanifest；basePath 构建下由 Next 自动暴露在 /aios/ 下并
// 注入带前缀的 <link rel="manifest">）。字段值全部来自 src/lib/pwa.ts 的
// basePath 契约——start_url / scope / id / icons 随 NEXT_PUBLIC_BASE_PATH
// 正确变化（空 = 根路径，"/aios" = 带且仅带一层前缀）。
import type { MetadataRoute } from "next";
import {
  PWA_ID,
  PWA_MANIFEST_ICONS,
  PWA_SCOPE,
  PWA_START_URL,
} from "@/lib/pwa";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "砚席 AI Learning OS",
    short_name: "砚席",
    description: "语音陪练、考场审阅与公开学习资源治理，添加到主屏幕随时进入。",
    id: PWA_ID,
    start_url: PWA_START_URL,
    scope: PWA_SCOPE,
    display: "standalone",
    background_color: "#f6f5f1",
    theme_color: "#f6f5f1",
    icons: [...PWA_MANIFEST_ICONS],
  };
}
