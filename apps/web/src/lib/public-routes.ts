// M14-179：公开路由（无需登录态）的单一事实来源。
// 背景（M14-178 真浏览器验收）：公开 /download 页上 AppShell 的匿名
// auth 探测只会收到必然的 401（auth/me），构成浏览器网络噪音。
// 契约（见 public-routes.test.ts）：
//   - pathname 是 Next App Router 规范路径（usePathname 不含 basePath，
//     root 与 /aios 构建同值，源码零 /aios 硬编码）；
//   - 恰为 "/download" → 免探测；其余一切路由（含前缀近似、子路径、
//     尾斜杠形态）→ 照常探测，AppShell 认证行为不回归。
export function isPublicRoute(pathname: string): boolean {
  return pathname === "/download";
}
