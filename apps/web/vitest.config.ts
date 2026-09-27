// M14-164：契约测试需要解析 tsconfig 的 "@/*" 别名（app/manifest.ts 等
// 源文件按项目约定使用 "@/lib/..." 导入）。仅用既有 vitest 依赖，node
// 环境与既有 *.test.ts 的运行方式保持一致。
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
});
