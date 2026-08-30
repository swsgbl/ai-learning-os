import { cn } from "@/lib/utils";

export function Badge({
  className,
  tone = "mute",
  ...props
}: React.ComponentProps<"span"> & { tone?: "mute" | "good" | "bad" | "accent" }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium",
        tone === "mute" && "bg-surface-2 text-muted",
        tone === "good" && "bg-good-soft text-good",
        tone === "bad" && "bg-bad-soft text-bad",
        tone === "accent" && "bg-accent text-accent-fg",
        className,
      )}
      {...props}
    />
  );
}
