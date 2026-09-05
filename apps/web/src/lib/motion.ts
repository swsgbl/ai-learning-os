// M11-01 动效 token：全站共享的时长 / 缓动 / 位移语义，避免每个组件复制魔法数。
// 语义：fast = 即时反馈（hover/按压），base = 状态切换（面板/选项），
// slow = 编排入场（页面/大区块）。reduced-motion 下组件统一把时长归零。
export const MOTION = {
  duration: {
    fast: 0.15,
    base: 0.3,
    slow: 0.5,
  },
  ease: {
    out: "power2.out",
    outStrong: "power3.out",
    inOut: "power2.inOut",
  },
  distance: {
    /** 入场上移距离：足够感知但不干扰阅读 */
    rise: 14,
    /** 大区块（hero）入场距离 */
    riseLg: 22,
  },
} as const;

/** 列表编排入场的共享默认值（stagger 按元素数量自动收敛总量） */
export function staggerFor(count: number): number {
  if (count <= 1) return 0;
  return Math.min(0.08, 0.4 / count);
}
