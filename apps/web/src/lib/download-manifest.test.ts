// M14-174 TDD：同源 download manifest 的解析与 Android 渠道可信判定。
// 契约（对应 docs/MOBILE_DISTRIBUTION.md §3/§4）：
//   - manifest 是同源静态文件（basePath 前缀），schema 必须精确是
//     "aios-download-manifest/1"，结构异常整体拒绝（降级 pending）；
//   - Android 可下载的充分条件：恰好一个 signed===true 且 URL/size/hash
//     结构可信的条目（URL 为相对 /android/ 路径、无 query/fragment/遍历/
//     协议，sha256 为 64 位十六进制，size_bytes 为正整数）；
//   - 零个或多个可信条目都停留在 pending（歧义 fail-closed，不显示链接）；
//   - fetch 失败 / 非 200 / 坏 JSON 全部静默降级 pending，绝不显示错误链接。
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  DOWNLOAD_MANIFEST_FILENAME,
  DOWNLOAD_MANIFEST_SCHEMA,
  downloadManifestUrl,
  fetchAndroidChannelState,
  parseDownloadManifest,
  resolveAndroidDownload,
} from "./download-manifest";

const GOOD_SHA = "a".repeat(64);
const APK_URL = "/android/ai-learning-os-0.14.0-release-signed.apk";

function entry(overrides: Record<string, unknown> = {}) {
  return {
    name: "ai-learning-os-0.14.0-release-signed.apk",
    url: APK_URL,
    sha256: GOOD_SHA,
    size_bytes: 12345,
    signed: true,
    ...overrides,
  };
}

function manifest(overrides: Record<string, unknown> = {}) {
  return {
    schema: DOWNLOAD_MANIFEST_SCHEMA,
    version: "0.14.0",
    channels: {
      android: {
        package_name: "com.example.app",
        versionCode: 15,
        versionName: "0.14.0",
        files: [entry()],
      },
    },
    ...overrides,
  };
}

describe("downloadManifestUrl", () => {
  it("根 basePath 下是 /download-manifest.json", () => {
    expect(downloadManifestUrl("")).toBe(`/${DOWNLOAD_MANIFEST_FILENAME}`);
  });

  it("/aios basePath 下带前缀", () => {
    expect(downloadManifestUrl("/aios")).toBe(
      `/aios/${DOWNLOAD_MANIFEST_FILENAME}`,
    );
  });

  it("非法 basePath 构建期抛错（fail-closed）", () => {
    expect(() => downloadManifestUrl("/foo")).toThrow();
    expect(() => downloadManifestUrl("aios")).toThrow();
  });
});

describe("parseDownloadManifest", () => {
  it("接受 schema 精确匹配且结构完整的 manifest", () => {
    const model = parseDownloadManifest(manifest());
    expect(model).not.toBeNull();
    expect(model!.androidEntries).toHaveLength(1);
    expect(model!.androidEntries[0].url).toBe(APK_URL);
    expect(model!.androidEntries[0].sizeBytes).toBe(12345);
  });

  it("schema 不符 / 非对象 / channels 缺失 / android 缺失 全部拒绝", () => {
    expect(parseDownloadManifest(manifest({ schema: "other/2" }))).toBeNull();
    expect(parseDownloadManifest("nope")).toBeNull();
    expect(parseDownloadManifest([manifest()])).toBeNull();
    expect(parseDownloadManifest(manifest({ channels: {} }))).toBeNull();
    expect(
      parseDownloadManifest({ schema: DOWNLOAD_MANIFEST_SCHEMA }),
    ).toBeNull();
  });

  it("结构不可信的条目被丢弃而不污染整份 manifest", () => {
    const model = parseDownloadManifest(
      manifest({
        channels: {
          android: {
            versionCode: 15,
            versionName: "0.14.0",
            files: [entry({ url: "https://evil.example/x.apk" }), entry()],
          },
        },
      }),
    );
    expect(model).not.toBeNull();
    expect(model!.androidEntries).toHaveLength(1);
  });

  it.each([
    ["绝对 URL", entry({ url: "https://evil.example/x.apk" })],
    ["协议相对 URL", entry({ url: "//evil.example/x.apk" })],
    ["query", entry({ url: `${APK_URL}?x=1` })],
    ["fragment", entry({ url: `${APK_URL}#f` })],
    ["遍历", entry({ url: "/android/../secret.apk" })],
    ["非 android 前缀", entry({ url: "/harmony/x.apk" })],
    ["反斜杠", entry({ url: "/android\\x.apk" })],
    ["百分号编码", entry({ url: "/android/%2e%2e.apk" })],
    ["sha256 非 64 位", entry({ sha256: "abc" })],
    ["sha256 非十六进制", entry({ sha256: "z".repeat(64) })],
    ["size 非正整数", entry({ size_bytes: 0 })],
    ["size 负数", entry({ size_bytes: -1 })],
    ["size 字符串", entry({ size_bytes: "12345" })],
  ])("不可信条目：%s", (_label, bad) => {
    const model = parseDownloadManifest(
      manifest({
        channels: {
          android: {
            versionCode: 15,
            versionName: "0.14.0",
            files: [bad],
          },
        },
      }),
    );
    expect(model).not.toBeNull();
    expect(model!.androidEntries).toHaveLength(0);
  });

  it("大写 sha256 与首尾空白按十六进制规范接受", () => {
    const model = parseDownloadManifest(
      manifest({
        channels: {
          android: {
            versionCode: 15,
            versionName: "0.14.0",
            files: [entry({ sha256: ` ${"A".repeat(64)} ` })],
          },
        },
      }),
    );
    expect(model!.androidEntries).toHaveLength(1);
    expect(model!.androidEntries[0].sha256).toBe(GOOD_SHA);
  });

  it("signed!==true（含 truthy 字符串）的条目不算可信", () => {
    const model = parseDownloadManifest(
      manifest({
        channels: {
          android: {
            versionCode: 15,
            versionName: "0.14.0",
            files: [entry({ signed: false }), entry({ signed: "true" })],
          },
        },
      }),
    );
    expect(model!.androidEntries).toHaveLength(0);
  });
});

describe("resolveAndroidDownload", () => {
  it("唯一可信条目 → available，href 为条目 url", () => {
    const model = parseDownloadManifest(manifest())!;
    const state = resolveAndroidDownload(model);
    expect(state.status).toBe("available");
    if (state.status === "available") {
      expect(state.href).toBe(APK_URL);
      expect(state.versionName).toBe("0.14.0");
      expect(state.versionCode).toBe(15);
    }
  });

  it("零个可信条目 → pending", () => {
    const model = parseDownloadManifest(
      manifest({
        channels: {
          android: {
            versionCode: 15,
            versionName: "0.14.0",
            files: [entry({ signed: false })],
          },
        },
      }),
    )!;
    expect(resolveAndroidDownload(model).status).toBe("pending");
  });

  it("多个可信条目 → pending（歧义 fail-closed，不给链接）", () => {
    const model = parseDownloadManifest(
      manifest({
        channels: {
          android: {
            versionCode: 15,
            versionName: "0.14.0",
            files: [entry(), entry({ url: "/android/second.apk", name: "second.apk" })],
          },
        },
      }),
    )!;
    expect(resolveAndroidDownload(model).status).toBe("pending");
  });
});

describe("fetchAndroidChannelState", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("200 + 可信 manifest → available", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify(manifest()), { status: 200 }),
      ),
    );
    const state = await fetchAndroidChannelState();
    expect(state.status).toBe("available");
  });

  it("404 / 500 → pending（不抛错）", async () => {
    for (const status of [404, 500]) {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => new Response("nope", { status })),
      );
      const state = await fetchAndroidChannelState();
      expect(state.status).toBe("pending");
    }
  });

  it("坏 JSON → pending", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("not json {", { status: 200 })),
    );
    expect((await fetchAndroidChannelState()).status).toBe("pending");
  });

  it("schema 不符的 200 响应 → pending", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify(manifest({ schema: "evil/1" })), {
          status: 200,
        }),
      ),
    );
    expect((await fetchAndroidChannelState()).status).toBe("pending");
  });

  it("网络拒绝 → pending（静默降级）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("network down");
      }),
    );
    expect((await fetchAndroidChannelState()).status).toBe("pending");
  });
});
