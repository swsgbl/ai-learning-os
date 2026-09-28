// M14-164 通用 PWA：manifest / SW / 图标的共享路径契约。
// 所有路径由 normalizeBasePath 推导：未设置/空 = 根路径构建行为完全不变；
// 唯一非空合法值 "/aios"。契约（见 pwa.test.ts）：
//   - scope 恒以尾斜杠结尾（W3C：scope 是 URL 前缀匹配）；
//   - start_url / id 恒落在 scope 内；
//   - 绝不出现双重前缀（校验器保证 BASE_PATH 本身合法）。
// public/ 静态文件 Next 不会自动加 basePath 前缀，SW 注册与
// apple-touch-icon 必须引用这里的显式前缀路径。
import { normalizeBasePath } from "./base-path";

const BASE_PATH = normalizeBasePath(process.env.NEXT_PUBLIC_BASE_PATH);

/** PWA 起始地址（添加到主屏幕后打开的入口）。 */
export const PWA_START_URL = `${BASE_PATH}/`;

/** PWA scope：SW 与 manifest 的作用域前缀（规范要求尾斜杠）。 */
export const PWA_SCOPE = `${BASE_PATH}/`;

/** PWA identity：已安装应用的稳定标识（解析基准是 scope，用 scope 本身）。 */
export const PWA_ID = `${BASE_PATH}/`;

/** Service Worker 脚本地址（public/sw.js，注册时显式带 basePath）。 */
export const SW_REGISTER_SRC = `${BASE_PATH}/sw.js`;

/** SW 注册作用域（与 PWA scope 一致）。 */
export const SW_REGISTER_SCOPE = `${BASE_PATH}/`;

/** apple-touch-icon 地址（public/apple-touch-icon.png，iOS 主屏图标 180）。 */
export const APPLE_TOUCH_ICON = `${BASE_PATH}/apple-touch-icon.png`;

/** 通用 favicon 地址（public/icons/icon-192.png，复用既有 any-192 资产）。
 *  M14-179：metadata 不提供 rel=icon 时浏览器会回退请求域根
 *  /favicon.ico（宿主未提供 → 404 噪音，见 M14-178 真浏览器验收）；
 *  显式 <link rel="icon"> 消除该回退，且路径同受 basePath 契约约束。 */
export const FAVICON_ICON = `${BASE_PATH}/icons/icon-192.png`;

export interface PwaManifestIcon {
  src: string;
  sizes: string;
  type: "image/png";
  purpose: "any" | "maskable";
}

/** manifest 图标条目：192/512 × any/maskable，资产在 public/icons/。 */
export const PWA_MANIFEST_ICONS: readonly PwaManifestIcon[] = [
  { src: `${BASE_PATH}/icons/icon-192.png`, sizes: "192x192", type: "image/png", purpose: "any" },
  { src: `${BASE_PATH}/icons/icon-512.png`, sizes: "512x512", type: "image/png", purpose: "any" },
  { src: `${BASE_PATH}/icons/maskable-192.png`, sizes: "192x192", type: "image/png", purpose: "maskable" },
  { src: `${BASE_PATH}/icons/maskable-512.png`, sizes: "512x512", type: "image/png", purpose: "maskable" },
];
