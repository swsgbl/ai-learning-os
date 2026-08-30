import { cn } from "@/lib/utils";

export function Card({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("rounded-xl bg-surface text-ink shadow-border", className)} {...props} />;
}
