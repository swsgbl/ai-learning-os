"use client";

import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap font-medium transition-[opacity,transform,background-color,box-shadow] duration-[var(--motion-quick)] ease-[var(--ease-out)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-45 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0 active:scale-[0.98]",
  {
    variants: {
      variant: {
        default: "bg-accent text-accent-fg shadow-border hover:opacity-90",
        invert: "bg-ink text-paper hover:opacity-90",
        outline: "bg-transparent text-ink shadow-border hover:bg-surface-2",
        ghost: "bg-transparent text-ink hover:bg-surface-2",
        danger: "bg-bad text-paper hover:opacity-90",
      },
      size: {
        default: "h-11 rounded-lg px-4 text-sm",
        // M11-01 移动端触控目标 >=44px；桌面（md+）回到紧凑密度
        sm: "h-11 rounded-lg px-4 text-sm md:h-9 md:rounded-md md:px-3",
        lg: "h-12 rounded-xl px-5 text-base",
        icon: "size-11 rounded-lg",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export function Button({
  className,
  variant,
  size,
  asChild = false,
  ...props
}: React.ComponentProps<"button"> & VariantProps<typeof buttonVariants> & { asChild?: boolean }) {
  const Component = asChild ? Slot : "button";
  return <Component className={cn(buttonVariants({ variant, size }), className)} {...props} />;
}
