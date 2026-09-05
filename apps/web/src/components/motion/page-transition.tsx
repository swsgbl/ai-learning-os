"use client";

// M11-01 路由过渡：新路由内容做克制的 rise+fade 入场（enter 过渡）。
// Next App Router 的 leave 由框架直接卸载旧树，不强行拦截拖慢导航；
// reduced-motion 下时长归零，信息与功能完全等价。
import { useRef } from "react";
import { usePathname } from "next/navigation";
import { gsap, useMotion } from "@/lib/gsap";
import { MOTION, staggerFor } from "@/lib/motion";

export function PageTransition({ children }: { children: React.ReactNode }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const pathname = usePathname();

  useMotion(
    (reduced) => {
      const root = containerRef.current;
      if (!root) return;
      const blocks = root.querySelectorAll<HTMLElement>("[data-animate='block']");
      const targets: Array<HTMLElement | HTMLDivElement> =
        blocks.length > 0 ? Array.from(blocks) : [root];
      if (reduced) {
        gsap.set(targets, { clearProps: "all" });
        return;
      }
      gsap.fromTo(
        targets,
        { autoAlpha: 0, y: MOTION.distance.rise },
        {
          autoAlpha: 1,
          y: 0,
          duration: MOTION.duration.slow,
          ease: MOTION.ease.out,
          stagger: staggerFor(targets.length),
          clearProps: "opacity,visibility,transform",
          overwrite: "auto",
        },
      );
    },
    { scope: containerRef, dependencies: [pathname] },
  );

  return (
    <div ref={containerRef} key={pathname}>
      {children}
    </div>
  );
}
