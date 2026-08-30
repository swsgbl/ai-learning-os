import { cn } from "@/lib/utils";

export function Progress({ value, className, ...props }: React.ComponentProps<"div"> & { value: number }) {
  return (
    <div className={cn("h-2 w-full overflow-hidden rounded-full bg-surface-2", className)} {...props}>
      <div className="h-full rounded-full bg-accent transition-width duration-500" style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </div>
  );
}
