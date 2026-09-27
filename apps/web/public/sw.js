/* 砚席 AI Learning OS — PWA service worker（M14-164）
 *
 * 边界契约（sw-contract.test.ts 钉住，修改前先读测试）：
 *   - 只处理同源 GET；一切动态数据流量在请求侧显式 bypass——SW 绝不
 *     代理、绝不缓存 API/认证/考试/语音/上传请求；
 *   - 响应侧二次守卫：非 2xx、非同源 basic、带 Set-Cookie、API JSON
 *     一律不写入 cache；
 *   - 导航 network-first（在线考试等服务端权威页面永不读旧缓存），
 *     离线回退缓存壳，最终兜底内嵌极小离线页；
 *   - cache 名称版本化，activate 清理旧版本；
 *   - 路径全部由 self.registration.scope 推导：同一份 sw.js 在根路径
 *     构建与 basePath 公共构建下行为一致（不硬编码任何前缀）。
 */
"use strict";

var VERSION = "v1";
var STATIC_CACHE = "aios-pwa-static-" + VERSION;
var SHELL_CACHE = "aios-pwa-shell-" + VERSION;
var CACHE_WHITELIST = [STATIC_CACHE, SHELL_CACHE];

// 动态数据路径标记（非导航请求命中即放行）：
// "/api/" 子串同时覆盖根构建 /api/ 与 basePath 构建 /aios/api/，也覆盖
// 验收同源 rewrite 的 /api/* 转发；auth / exam / voice / upload 是纵深
// 防御——业务流量理论上全在 /api/v1/ 下，但守卫不依赖该假设。
// 注意：仅对非导航请求生效；/exam、/voice 等页面导航本身走 network-first。
var DYNAMIC_PATH_MARKERS = ["/api/", "/auth", "/exam", "/voice", "/upload"];

// scope 推导：SW 的 scope 由注册时的 scope 选项决定（root = "/"，
// basePath 构建 = 前缀路径），与构建产物解耦。
var SCOPE_URL = new URL(self.registration.scope);
var SCOPE_PATH = SCOPE_URL.pathname; // "/" 或带前缀路径，恒以尾斜杠结尾
var SHELL_URL = SCOPE_URL.origin + SCOPE_PATH; // scope 根导航 = 安装壳

// 内嵌最终兜底壳：shell cache 也未命中时避免白屏（诚实提示，不伪装数据）
var offlineFallbackHtml =
  '<!doctype html><html lang="zh-CN"><meta charset="utf-8">' +
  '<meta name="viewport" content="width=device-width, initial-scale=1">' +
  "<title>砚席 — 离线</title>" +
  '<style>body{font-family:system-ui,"PingFang SC","Noto Sans SC",sans-serif;' +
  "background:#f6f5f1;color:#191f1e;display:flex;min-height:100vh;" +
  "align-items:center;justify-content:center;margin:0}" +
    "main{max-width:28rem;padding:2rem;text-align:center}" +
    "h1{font-size:1.25rem;margin:0 0 .75rem}p{font-size:.875rem;color:#5b6663;line-height:1.7}" +
  "</style>" +
  "<main><h1>当前离线</h1><p>砚席需要联网提供服务（考试、语音与学习数据" +
  "均以服务端为准）。请恢复网络后重试；已安装的应用将在重新联网后自动恢复。" +
  "</p></main></html>";

// —— 响应侧守卫：可缓存性判定 ————————————————
function isCacheableResponse(response) {
  if (!response || !response.ok) return false; // 仅 2xx
  // basic = 同源响应；default 兜底（部分实现不给 basic 标记）
  if (response.type !== "basic" && response.type !== "default") return false;
  if (response.headers.has("set-cookie")) return false; // 会话痕迹不入缓存
  var contentType = (response.headers.get("content-type") || "").toLowerCase();
  if (contentType.includes("application/json")) return false; // API JSON 纵深防御
  return true;
}

// —— 请求侧守卫：动态流量识别（仅非导航请求）————————
function isDynamicRequest(url, request) {
  if (request.headers.get("Authorization")) return true; // 凭据流量
  var pathname = url.pathname;
  if (pathname.includes("/api/")) return true; // 覆盖 /api/ 与 /aios/api/
  for (var i = 0; i < DYNAMIC_PATH_MARKERS.length; i++) {
    if (pathname.includes(DYNAMIC_PATH_MARKERS[i])) return true;
  }
  return false;
}

// —— install：预缓存极小安装壳（失败不阻塞）——————————
self.addEventListener("install", function (event) {
  event.waitUntil(
    (async function () {
      try {
        var response = await fetch(SHELL_URL, { credentials: "omit" });
        if (isCacheableResponse(response)) {
          var cache = await caches.open(SHELL_CACHE);
          await cache.put(SHELL_URL, response);
        }
      } catch (err) {
        // 预缓存失败不阻塞 install：离线壳由后续成功导航回填，
        // 且仍有内嵌兜底页。
      }
    })(),
  );
});

// —— activate：清理旧版本 cache —————————————————
self.addEventListener("activate", function (event) {
  event.waitUntil(
    (async function () {
      var names = await caches.keys();
      await Promise.all(
        names.map(function (name) {
          if (CACHE_WHITELIST.indexOf(name) === -1) {
            return caches.delete(name);
          }
          return undefined;
        }),
      );
    })(),
  );
});

// —— fetch ————————————————————————————————————
self.addEventListener("fetch", function (event) {
  var request = event.request;
  if (request.method !== "GET") return; // 非 GET 一律放行
  if (request.mode === "navigate") {
    event.respondWith(handleNavigate(request));
    return;
  }
  var url = new URL(request.url);
  if (url.origin !== self.location.origin) return; // 仅同源（跨源 API/CDN 放行）
  if (isDynamicRequest(url, request)) return; // API/动态流量放行
  event.respondWith(handleStatic(request));
});

// 导航 network-first：在线永远拿最新页面（服务端权威），离线回退缓存壳。
async function handleNavigate(request) {
  try {
    var response = await fetch(request);
    if (isCacheableResponse(response)) {
      var cache = await caches.open(SHELL_CACHE);
      cache.put(request, response.clone()); // 回填离线壳（异步，不阻塞响应）
    }
    return response;
  } catch (err) {
    var cached =
      (await caches.match(request)) || (await caches.match(SHELL_URL));
    if (cached) return cached;
    return new Response(offlineFallbackHtml, {
      status: 200,
      headers: { "content-type": "text/html; charset=utf-8" },
    });
  }
}

// 静态资源 cache-first：/_next/static/ 带内容哈希不可变，icons 同理；
// 其余同源静态（manifest、图标、字体等）命中即用、后台回填。
async function handleStatic(request) {
  var cached = await caches.match(request);
  if (cached) return cached;
  var response = await fetch(request);
  if (isCacheableResponse(response) && isStaticAsset(request.url)) {
    var cache = await caches.open(STATIC_CACHE);
    cache.put(request, response.clone());
  }
  return response;
}

function isStaticAsset(requestUrl) {
  var pathname = new URL(requestUrl).pathname;
  return (
    pathname.includes("/_next/static/") ||
    pathname.includes("/icons/") ||
    pathname.endsWith(".png") ||
    pathname.endsWith(".webmanifest") ||
    pathname.endsWith(".woff2") ||
    pathname.endsWith(".css") ||
    pathname.endsWith(".js")
  );
}
