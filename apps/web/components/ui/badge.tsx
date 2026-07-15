import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-sm border px-1.5 py-px font-mono text-[11px] font-medium tracking-tight whitespace-nowrap [&_svg]:size-3",
  {
    variants: {
      variant: {
        neutral: "border-stroke bg-raise text-ink-2",
        outline: "border-stroke bg-transparent text-ink-3",
        accent: "border-accent/25 bg-accent-soft text-accent-ink",
        pos: "border-transparent bg-grade-a-soft text-pos",
        neg: "border-transparent bg-grade-f-soft text-neg",
        warn: "border-transparent bg-grade-c-soft text-grade-c",
        danger: "border-danger/25 bg-danger-soft text-danger",
      },
    },
    defaultVariants: { variant: "neutral" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
