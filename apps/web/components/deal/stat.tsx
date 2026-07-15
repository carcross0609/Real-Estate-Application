import * as React from "react";
import { cn } from "@/lib/utils";
import { Tip } from "@/components/ui/tooltip";
import { Info } from "lucide-react";

/**
 * Stat — the atomic "muted label / strong value" pair (§21 #3). `hint` renders the
 * "why" affordance; every displayed number should be able to explain itself.
 */
export function Stat({
  label,
  value,
  sub,
  hint,
  align = "left",
  size = "md",
  className,
}: {
  label: React.ReactNode;
  value: React.ReactNode;
  sub?: React.ReactNode;
  hint?: React.ReactNode;
  align?: "left" | "right";
  size?: "sm" | "md" | "lg";
  className?: string;
}) {
  return (
    <div className={cn("min-w-0", align === "right" && "text-right", className)}>
      <div
        className={cn(
          "eyebrow flex items-center gap-1 whitespace-nowrap",
          align === "right" && "justify-end",
        )}
      >
        {label}
        {hint ? (
          <Tip label={hint}>
            <Info className="size-3 cursor-help text-ink-faint transition-colors hover:text-ink-3" aria-label="Explanation" />
          </Tip>
        ) : null}
      </div>
      <div
        className={cn(
          "figure mt-0.5 font-semibold text-ink",
          size === "sm" && "text-[13px]",
          size === "md" && "text-[15px]",
          size === "lg" && "text-xl",
        )}
      >
        {value}
      </div>
      {sub ? <div className="mt-0.5 text-[11px] leading-tight text-ink-3">{sub}</div> : null}
    </div>
  );
}

/** Section header with mono eyebrow + optional actions on the right. */
export function SectionHeader({
  eyebrow,
  title,
  children,
  className,
}: {
  eyebrow?: string;
  title: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-wrap items-end justify-between gap-2", className)}>
      <div>
        {eyebrow ? <div className="eyebrow mb-0.5">{eyebrow}</div> : null}
        <h2 className="text-[15px] font-semibold tracking-tight text-ink">{title}</h2>
      </div>
      {children ? <div className="flex items-center gap-2">{children}</div> : null}
    </div>
  );
}

/** Designed empty state (§21 #9) — explicit, honest, actionable. */
export function EmptyState({
  icon: Icon,
  title,
  body,
  action,
  className,
}: {
  icon?: React.ComponentType<{ className?: string }>;
  title: string;
  body?: string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-stroke bg-panel/50 px-6 py-12 text-center",
        className,
      )}
    >
      {Icon ? (
        <div className="mb-1 flex size-10 items-center justify-center rounded-lg border border-stroke bg-card">
          <Icon className="size-5 text-ink-3" />
        </div>
      ) : null}
      <div className="text-sm font-medium text-ink">{title}</div>
      {body ? <p className="max-w-sm text-[13px] leading-relaxed text-ink-3">{body}</p> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}
