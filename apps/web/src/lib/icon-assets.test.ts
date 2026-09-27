// M14-164 TDD：PWA 图标资产契约（真实 PNG，尺寸/magic/关键像素钉住）。
// 图标由 scripts/generate-pwa-icons.mjs 生成（可重复执行、零依赖），
// 本测试防止：资产被误删、被换成非 PNG、尺寸不符、或 maskable 内容
// 逸出安全区（以关键像素采样近似验证构图未漂移）。
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { inflateSync } from "node:zlib";
import { describe, expect, it } from "vitest";

const PUBLIC_DIR = join(__dirname, "../../public");

const PNG_MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

/** 解析 PNG：返回 { width, height, pixel(x,y) -> [r,g,b,a] }。 */
function readPng(file: string) {
  const buf = readFileSync(join(PUBLIC_DIR, file));
  expect(buf.subarray(0, 8).equals(PNG_MAGIC)).toBe(true); // 是真实 PNG
  const width = buf.readUInt32BE(16);
  const height = buf.readUInt32BE(20);
  expect(buf[24]).toBe(8); // bit depth
  const idats: Buffer[] = [];
  let off = 8;
  while (off < buf.length) {
    const len = buf.readUInt32BE(off);
    const type = buf.toString("ascii", off + 4, off + 8);
    if (type === "IDAT") idats.push(buf.subarray(off + 8, off + 8 + len));
    if (type === "IEND") break;
    off += 12 + len;
  }
  const raw = inflateSync(Buffer.concat(idats));
  const stride = width * 4 + 1;
  return {
    width,
    height,
    pixel(x: number, y: number): [number, number, number, number] {
      const o = y * stride + 1 + x * 4;
      return [raw[o], raw[o + 1], raw[o + 2], raw[o + 3]];
    },
  };
}

const PORCELAIN = "#f6f5f1";
const ACCENT = "#0d5b55";
const INK = "#191f1e";

function hex([r, g, b]: [number, number, number, number]) {
  return `#${[r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

const EXPECTED = [
  { file: "icons/icon-192.png", size: 192 },
  { file: "icons/icon-512.png", size: 512 },
  { file: "icons/maskable-192.png", size: 192 },
  { file: "icons/maskable-512.png", size: 512 },
  { file: "apple-touch-icon.png", size: 180 },
];

describe("PWA 图标资产契约", () => {
  it("生成脚本入库（资产可重复再生，非一次性临时产物）", () => {
    expect(existsSync(join(__dirname, "../../scripts/generate-pwa-icons.mjs"))).toBe(true);
  });

  for (const { file, size } of EXPECTED) {
    it(`${file}：真实 PNG，${size}×${size}，不透明满幅`, () => {
      const png = readPng(file);
      expect(png.width).toBe(size);
      expect(png.height).toBe(size);
      // 四角均为不透明底色（maskable / apple-touch 满幅要求）
      for (const [x, y] of [[1, 1], [size - 2, 1], [1, size - 2], [size - 2, size - 2]] as const) {
        const [r, g, b, a] = png.pixel(x, y);
        expect(a).toBe(255);
        expect(hex([r, g, b, a])).toBe(PORCELAIN);
      }
      // 中心墨点（ink）
      expect(hex(png.pixel(size >> 1, size >> 1))).toBe(INK);
      // 砚体上部（accent）
      expect(hex(png.pixel(size >> 1, Math.round(size * 0.3)))).toBe(ACCENT);
    });
  }

  it("maskable 512：内容收缩在中心 80% 安全区内（对角方向仍为底色）", () => {
    const png = readPng("icons/maskable-512.png");
    const size = 512;
    // 安全区半径 = 0.4×size = 204.8px；45° 方向略出安全区的位置（0.41×size）
    // 必须仍是 porcelain 底（内容未逸出）
    const d = Math.round(0.41 * size * Math.SQRT1_2);
    for (const [sx, sy] of [[1, 1], [-1, 1], [1, -1], [-1, -1]] as const) {
      const x = size / 2 + sx * d;
      const y = size / 2 + sy * d;
      expect(hex(png.pixel(Math.round(x), Math.round(y)))).toBe(PORCELAIN);
    }
  });
});
