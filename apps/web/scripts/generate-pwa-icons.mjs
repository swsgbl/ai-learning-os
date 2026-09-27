// M14-164：PWA 图标生成脚本（可重复执行，零外部依赖）。
// 用途：生成 apps/web/public/icons/ 下的 4 枚 manifest 图标与
// public/apple-touch-icon.png。视觉与现有砚席 theme 一致：
//   porcelain #f6f5f1 底 + accent #0d5b55 圆角方砚体 + porcelain 墨堂
//   + ink #191f1e 墨点（一方砚的几何抽象）。
// 边界：
//   - any 用途：内容占画布 ~67%，常规显示；
//   - maskable 用途：全幅铺底色，内容收缩进中心 80% 安全区
//     （对角最远点 < 0.4×size），任何系统裁剪形状均不裁到内容；
//   - apple-touch-icon：180×180 不透明满幅（iOS 自动加圆角）。
// 运行：node scripts/generate-pwa-icons.mjs（在 apps/web 目录下）。
// 纯 Node 实现：逐像素 SDF 光栅化 + 手写 PNG 编码（zlib 内置），
// 不新增任何 npm 依赖，不从网络下载任何资源。

import { deflateSync } from "node:zlib";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const ICONS_DIR = join(ROOT, "public", "icons");

// 砚席 palette（与 src/app/globals.css 设计 token 一致）
const PORCELAIN = [0xf6, 0xf5, 0xf1];
const ACCENT = [0x0d, 0x5b, 0x55];
const INK = [0x19, 0x1f, 0x1e];

/** 有符号距离：点到圆角方边界（负=内部）。坐标均为 0..1 相对单位。 */
function roundedRectSDF(x, y, cx, cy, half, radius) {
  const dx = Math.abs(x - cx) - (half - radius);
  const dy = Math.abs(y - cy) - (half - radius);
  const ax = Math.max(dx, 0);
  const ay = Math.max(dy, 0);
  const outer = Math.hypot(ax, ay);
  const inner = Math.min(Math.max(dx, dy), 0);
  return outer + inner - radius;
}

function circleSDF(x, y, cx, cy, r) {
  return Math.hypot(x - cx, y - cy) - r;
}

/** 1px 抗锯齿覆盖度：SDF 值以相对单位给出，按像素步长折算。 */
function coverage(sdf, pixelStep) {
  const d = sdf / pixelStep; // 转换为像素单位
  return Math.min(1, Math.max(0, 0.5 - d));
}

function mix(base, top, alpha) {
  return [
    Math.round(base[0] + (top[0] - base[0]) * alpha),
    Math.round(base[1] + (top[1] - base[1]) * alpha),
    Math.round(base[2] + (top[2] - base[2]) * alpha),
  ];
}

/**
 * 绘制一枚砚席图标。
 * variant "any"：内容占画布 67%（常规显示）；
 * variant "maskable"：内容收缩至 80% 安全区内、底色铺满。
 */
function renderInkstone(size, variant) {
  const scale = variant === "maskable" ? 0.265 : 0.335;
  const pixelStep = 1 / size;
  const rows = [];
  for (let py = 0; py < size; py++) {
    const row = Buffer.alloc(size * 4);
    const y = (py + 0.5) / size;
    for (let px = 0; px < size; px++) {
      const x = (px + 0.5) / size;
      // 分层合成（后画的在上）：porcelain 底 → 砚体 → 墨堂 → 墨点
      let color = PORCELAIN;
      const stone = coverage(
        roundedRectSDF(x, y, 0.5, 0.5, scale, scale * 0.42),
        pixelStep,
      );
      if (stone > 0) color = mix(color, ACCENT, stone);
      const pool = coverage(circleSDF(x, y, 0.5, 0.52, scale * 0.47), pixelStep);
      if (pool > 0) color = mix(color, PORCELAIN, pool);
      const inkDot = coverage(circleSDF(x, y, 0.5, 0.52, scale * 0.22), pixelStep);
      if (inkDot > 0) color = mix(color, INK, inkDot);
      const o = px * 4;
      row[o] = color[0];
      row[o + 1] = color[1];
      row[o + 2] = color[2];
      row[o + 3] = 0xff; // 不透明：maskable / apple-touch 均要求满幅
    }
    rows.push(row);
  }
  return rows;
}

// —— 手写 PNG 编码（IHDR/IDAT/IEND + CRC32，color type 6 RGBA）———

const CRC_TABLE = (() => {
  const table = new Int32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) {
      c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    }
    table[n] = c;
  }
  return table;
})();

function crc32(buffer) {
  let c = 0xffffffff;
  for (const byte of buffer) {
    c = CRC_TABLE[(c ^ byte) & 0xff] ^ (c >>> 8);
  }
  return (c ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const out = Buffer.alloc(data.length + 12);
  out.writeUInt32BE(data.length, 0);
  out.write(type, 4, "ascii");
  data.copy(out, 8);
  out.writeUInt32BE(crc32(out.subarray(4, 8 + data.length)), 8 + data.length);
  return out;
}

function encodePNG(rows, width, height) {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // RGBA
  const raw = Buffer.concat(
    rows.flatMap((row) => [Buffer.from([0]), row]), // filter type 0
  );
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(raw, { level: 9 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

const TARGETS = [
  { file: "icon-192.png", size: 192, variant: "any" },
  { file: "icon-512.png", size: 512, variant: "any" },
  { file: "maskable-192.png", size: 192, variant: "maskable" },
  { file: "maskable-512.png", size: 512, variant: "maskable" },
];

mkdirSync(ICONS_DIR, { recursive: true });

for (const target of TARGETS) {
  const png = encodePNG(
    renderInkstone(target.size, target.variant),
    target.size,
    target.size,
  );
  writeFileSync(join(ICONS_DIR, target.file), png);
  console.log(`✓ icons/${target.file} (${target.size}×${target.size}, ${png.length} bytes)`);
}

// apple-touch-icon：180×180，any 构图（iOS 自动圆角，底必须不透明满幅）
const apple = encodePNG(renderInkstone(180, "any"), 180, 180);
writeFileSync(join(ROOT, "public", "apple-touch-icon.png"), apple);
console.log(`✓ apple-touch-icon.png (180×180, ${apple.length} bytes)`);
