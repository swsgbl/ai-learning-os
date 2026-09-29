// M14-164 TDD：public/sw.js 与 SW 注册组件的源码级契约。
// sw.js 是不经构建的静态脚本，无法用单元测试直接执行——以文本契约钉住
// 安全边界（这些断言失败 = 边界被意外删除，必须在 review 前拦下）：
//   1. 只处理同源 GET；一切动态数据（/api/、认证、考试、语音、上传）
//      在请求侧显式 bypass，永不让 SW 代理或缓存 API 流量；
//   2. 响应侧二次守卫：Set-Cookie / API JSON 永不写入 cache；
//   3. cache 名称版本化，activate 清理旧版本；
//   4. 导航 network-first，离线回退缓存壳；
//   5. 路径基于 self.registration.scope 推导（同一份 sw.js 在根路径与
//      /aios 两种构建下都正确）；
//   6. 注册组件用 SW_REGISTER_SRC（basePath 契约），root 行为不回归；
//   7. （M14-188）catch 一律 optional binding，无未使用 err 绑定。
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const SW_SOURCE = readFileSync(join(__dirname, "../../public/sw.js"), "utf8");
const REGISTER_SOURCE = readFileSync(
  join(__dirname, "../components/pwa/sw-register.tsx"),
  "utf8",
);

describe("sw.js 请求侧安全边界", () => {
  it("非 GET 一律直接放行（fetch handler 首判请求方法）", () => {
    expect(SW_SOURCE).toContain('request.method !== "GET"');
  });

  it("仅处理同源请求（origin 比较，跨源 API/字体 CDN 不经 SW 逻辑）", () => {
    expect(SW_SOURCE).toContain("self.location.origin");
    expect(SW_SOURCE).toContain("request.url");
    expect(SW_SOURCE).toContain("origin");
  });

  it("API 路径显式 bypass：/api/ 子串同时覆盖 /api/ 与 /aios/api/", () => {
    expect(SW_SOURCE).toContain('"/api/"');
    // pathname.includes("/api/") 形态的判定必须存在
    expect(SW_SOURCE).toMatch(/pathname\.includes\(["']\/api\/["']\)/);
  });

  it("认证 / 考试 / 语音 / 上传动态标记纵深防御（非导航请求 bypass）", () => {
    for (const marker of ["/auth", "/exam", "/voice", "/upload"]) {
      expect(SW_SOURCE).toContain(`"${marker}"`);
    }
    // 动态 bypass 只作用于非导航请求（/exam、/voice 页面导航本身走 network-first）
    expect(SW_SOURCE).toContain('request.mode === "navigate"');
  });

  it("带 Authorization 头的请求 bypass（凭据流量永不进 SW 缓存逻辑）", () => {
    expect(SW_SOURCE).toContain("Authorization");
  });
});

describe("sw.js 缓存与响应守卫", () => {
  it("cache 名称版本化（aios-pwa- 前缀 + 版本常量）", () => {
    expect(SW_SOURCE).toContain("aios-pwa-");
    expect(SW_SOURCE).toMatch(/VERSION\s*=\s*"v\d+"/);
  });

  it("activate 清理旧版本 cache（白名单外全删）", () => {
    expect(SW_SOURCE).toContain("caches.keys()");
    expect(SW_SOURCE).toContain("caches.delete(");
  });

  it("响应侧守卫：Set-Cookie 响应永不入 cache", () => {
    expect(SW_SOURCE).toMatch(/has\(["']set-cookie["']\)/i);
  });

  it("响应侧守卫：API JSON 永不入 cache（content-type 判定）", () => {
    expect(SW_SOURCE).toContain("application/json");
  });

  it("仅缓存 2xx 基本类型响应（非 ok 不缓存）", () => {
    expect(SW_SOURCE).toMatch(/response\.ok/);
    expect(SW_SOURCE).toContain("response.type");
  });
});

describe("sw.js 离线策略", () => {
  it("导航 network-first：先网络，失败回退缓存壳", () => {
    expect(SW_SOURCE).toContain('request.mode === "navigate"');
    expect(SW_SOURCE).toContain("caches.match(");
    // 离线最终兜底：内嵌极小离线壳（避免 shell cache 也 miss 时白屏）
    expect(SW_SOURCE).toContain("offline");
  });

  it("路径基于 self.registration.scope 推导（root 与 /aios 共用一份 sw.js）", () => {
    expect(SW_SOURCE).toContain("self.registration.scope");
    // 不得硬编码 /aios（否则 root 构建行为回归）
    expect(SW_SOURCE).not.toMatch(/["']\/aios/);
  });
});

// M14-188：warning 卫生契约 —— sw.js 的 catch 一律 optional binding
// （不带未使用的 err 绑定，CI no-unused-vars 清零）；同时钉住两个
// catch 守卫本体与离线回退链不被顺手删掉（行为零变更）。
describe("sw.js warning 卫生（M14-188）", () => {
  it("不再出现带绑定的 catch（no-unused-vars 清零，也不留 eslint-disable）", () => {
    expect(SW_SOURCE).not.toMatch(/catch\s*\(/);
    expect(SW_SOURCE).not.toContain("eslint-disable");
  });

  it("两处 catch 守卫仍以 optional binding 形态存在（行为不变）", () => {
    const optionalCatches = SW_SOURCE.match(/catch\s*\{/g) ?? [];
    expect(optionalCatches.length).toBe(2);
  });

  it("导航离线回退链完整保留（请求缓存 → 壳缓存 → 内嵌兜底页）", () => {
    expect(SW_SOURCE).toMatch(
      /caches\.match\(request\)[\s\S]*?\|\|[\s\S]*?caches\.match\(SHELL_URL\)/,
    );
    expect(SW_SOURCE).toMatch(/new Response\(offlineFallbackHtml/);
  });
});

describe("SW 注册组件契约", () => {
  it("注册路径来自 basePath 契约常量 SW_REGISTER_SRC（root 构建不回归）", () => {
    expect(REGISTER_SOURCE).toContain("SW_REGISTER_SRC");
    expect(REGISTER_SOURCE).toContain("serviceWorker.register");
  });

  it("能力检测 + 静默失败（PWA 是增强，不阻塞应用渲染）", () => {
    expect(REGISTER_SOURCE).toContain('"serviceWorker" in navigator');
    expect(REGISTER_SOURCE).toMatch(/\.catch\(/);
  });

  it("注册作用域显式使用 SW_REGISTER_SCOPE", () => {
    expect(REGISTER_SOURCE).toContain("SW_REGISTER_SCOPE");
  });
});
