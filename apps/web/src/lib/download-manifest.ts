// M14-174 同源 download manifest：/download 页 Android 渠道的运行时事实来源。
// 诚实边界（docs/MOBILE_DISTRIBUTION.md §3）：
//   - 无 manifest / 获取失败 / 校验失败时 Android 恒 pending、无下载链接
//     （静态默认见 src/lib/download.ts，本模块只做运行时覆盖）；
//   - 只有 manifest（同源静态文件，按 NEXT_PUBLIC_BASE_PATH 规范前缀）的
//     android 频道里**恰好一个** signed===true 且 URL/size/hash 结构可信的
//     条目才算 available；零个或多个可信条目都停在 pending（歧义
//     fail-closed），绝不显示错误链接。
// URL 可信判据与 tools/android_release/verify_artifact.py 的 ENTRY_URL_RE
// 同语义：相对 /android/ 路径、ASCII 白名单、无 query/fragment/协议/
// 反斜杠/百分号编码/遍历段。manifest 内容不写死进任何组件。

import { normalizeBasePath } from "./base-path";

export const DOWNLOAD_MANIFEST_SCHEMA = "aios-download-manifest/1";
export const DOWNLOAD_MANIFEST_FILENAME = "download-manifest.json";

const BASE_PATH = normalizeBasePath(process.env.NEXT_PUBLIC_BASE_PATH);

/** 同源 manifest URL（public/ 静态文件不自动带 basePath，需显式拼接）。 */
export function downloadManifestUrl(basePath: string): string {
  const normalized = normalizeBasePath(basePath);
  return `${normalized}/${DOWNLOAD_MANIFEST_FILENAME}`;
}

/** 可信的结构化条目（size_bytes 重命名为 sizeBytes）。 */
export interface TrustedAndroidEntry {
  name: string;
  url: string;
  sha256: string;
  sizeBytes: number;
  versionName: string | null;
  versionCode: number | null;
}

export interface DownloadManifestModel {
  androidEntries: TrustedAndroidEntry[];
}

/** Android 渠道展示状态：available 才有 href，其余一律 pending。 */
export type AndroidChannelState =
  | {
      status: "available";
      href: string;
      sha256: string;
      sizeBytes: number;
      versionName: string | null;
      versionCode: number | null;
    }
  | { status: "pending" };

const ENTRY_URL_RE = /^\/android\/[A-Za-z0-9._~+-]+(?:\/[A-Za-z0-9._~+-]+)*$/;
const SHA256_RE = /^[0-9a-f]{64}$/;

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function trustedEntry(
  raw: unknown,
  channel: Record<string, unknown>,
): TrustedAndroidEntry | null {
  if (!isPlainObject(raw)) return null;
  const url = raw.url;
  if (typeof url !== "string" || ENTRY_URL_RE.test(url) === false) return null;
  if (url.split("/").some((segment) => segment === "." || segment === "..")) {
    return null;
  }
  if (raw.signed !== true) return null;
  const sha256 =
    typeof raw.sha256 === "string" ? raw.sha256.trim().toLowerCase() : "";
  if (SHA256_RE.test(sha256) === false) return null;
  const sizeBytes = raw.size_bytes;
  if (
    typeof sizeBytes !== "number" ||
    !Number.isInteger(sizeBytes) ||
    sizeBytes <= 0
  ) {
    return null;
  }
  const versionName =
    typeof channel.versionName === "string" && channel.versionName !== ""
      ? channel.versionName
      : null;
  const rawCode = channel.versionCode;
  const versionCode =
    typeof rawCode === "number" && Number.isInteger(rawCode) && rawCode >= 0
      ? rawCode
      : null;
  return {
    name: typeof raw.name === "string" ? raw.name : "",
    url,
    sha256,
    sizeBytes,
    versionName,
    versionCode,
  };
}

/** 解析 manifest；整体结构不可信 → null（调用方降级 pending）。 */
export function parseDownloadManifest(
  raw: unknown,
): DownloadManifestModel | null {
  if (!isPlainObject(raw)) return null;
  if (raw.schema !== DOWNLOAD_MANIFEST_SCHEMA) return null;
  const channels = raw.channels;
  if (!isPlainObject(channels)) return null;
  const android = channels.android;
  if (!isPlainObject(android)) return null;
  const files = android.files;
  if (!Array.isArray(files)) return null;
  const androidEntries = files
    .map((item) => trustedEntry(item, android))
    .filter((item): item is TrustedAndroidEntry => item !== null);
  return { androidEntries };
}

/** 唯一可信条目 → available；零个或多个 → pending（fail-closed）。 */
export function resolveAndroidDownload(
  model: DownloadManifestModel,
): AndroidChannelState {
  if (model.androidEntries.length !== 1) return { status: "pending" };
  const entry = model.androidEntries[0];
  return {
    status: "available",
    href: entry.url,
    sha256: entry.sha256,
    sizeBytes: entry.sizeBytes,
    versionName: entry.versionName,
    versionCode: entry.versionCode,
  };
}

/**
 * 运行时获取并判定 Android 渠道状态；任何失败都静默降级 pending。
 * 绝不抛错、绝不渲染错误链接——这是 /download 的诚实默认。
 */
export async function fetchAndroidChannelState(): Promise<AndroidChannelState> {
  try {
    const response = await fetch(downloadManifestUrl(BASE_PATH), {
      cache: "no-store",
    });
    if (!response.ok) return { status: "pending" };
    const body: unknown = await response.json();
    const model = parseDownloadManifest(body);
    if (model === null) return { status: "pending" };
    return resolveAndroidDownload(model);
  } catch {
    return { status: "pending" };
  }
}
