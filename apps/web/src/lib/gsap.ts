"use client";

// M11-01 GSAP 客户端注册入口：所有 client leaf 组件统一从这里 import gsap，
// 保证插件只注册一次、且只在浏览器执行（本文件仅被 "use client" 组件引用，
// SSR 不会走到 registerPlugin 之外的任何 DOM 操作）。
import type { RefObject } from "react";
import { useSyncExternalStore } from "react";
import { gsap } from "gsap";
import { useGSAP } from "@gsap/react";
import { Flip } from "gsap/Flip";

// 只注册实际使用的插件：useGSAP（React 集成）与 Flip（学习库过滤重排）。
// 不引入 ScrollTrigger——本工作台无滚动叙事需求，避免装饰性滚动动画与多余包体。
if (typeof window !== "undefined") {
  gsap.registerPlugin(useGSAP, Flip);
}

export { gsap, useGSAP, Flip };

/**
 * 统一动效挂载点：useGSAP（scope + 自动 cleanup）内包一层 gsap.matchMedia()，
 * 组件只写「reduced 与否」两条分支，媒体条件翻转时 matchMedia 自动 revert。
 * 依赖数组变化时重跑（动态列表 / 异步数据）。
 */
export function useMotion(
  effect: (reduced: boolean) => void,
  options?: { scope?: RefObject<HTMLElement | null>; dependencies?: unknown[] },
) {
  useGSAP(
    () => {
      const mm = gsap.matchMedia();
      mm.add("(prefers-reduced-motion: reduce)", () => effect(true));
      mm.add("(prefers-reduced-motion: no-preference)", () => effect(false));
      return () => mm.revert();
    },
    { scope: options?.scope, dependencies: options?.dependencies },
  );
}

// 同步读取 prefers-reduced-motion 的 React 钩子（用于渲染分支，如波形降级为静态点）。
// M14-189: 由「effect 内同步 setState」改为 useSyncExternalStore——外部媒体
// 查询就是典型外部 store：订阅 change 事件、快照读 matches，消
// set-state-in-effect 级联渲染；行为等价（SSR/水合期间恒 false，水合后
// 同步真实值并跟随系统设置变化；GSAP 路径仍由 useMotion 的 matchMedia 兜底）。
const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

function subscribeReducedMotion(onChange: () => void) {
  const query = window.matchMedia(REDUCED_MOTION_QUERY);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

function getReducedMotionSnapshot(): boolean {
  return window.matchMedia(REDUCED_MOTION_QUERY).matches;
}

function getReducedMotionServerSnapshot(): boolean {
  return false;
}

export function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(
    subscribeReducedMotion,
    getReducedMotionSnapshot,
    getReducedMotionServerSnapshot,
  );
}
