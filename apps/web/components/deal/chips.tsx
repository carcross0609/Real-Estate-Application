"use client";

import * as React from "react";
import { ShieldCheck, ShieldAlert, ShieldQuestion, TrendingUp, TrendingDown } from "lucide-react";
import {
  confidenceLevel,
  CONFIDENCE_LABEL,
  RECOMMENDATION_LABEL,
  type Recommendation,
} from "@/lib/domain";
import { Tip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/**
 * ConfidenceChip — §21 #5 "confidence made visible". Deliberately neutral colors:
 * the quality ramp is reserved for deal grade & risk.
 */
export function ConfidenceChip({
  value,
  reason,
  className,
}: {
  value: number | null | undefined;
  reason?: string;
  className?: string;
}) {
  const level = confidenceLevel(value);
  const Icon =
    level === "high" ? ShieldCheck : level === "medium" ? ShieldAlert : ShieldQuestion;
  const chip = (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-sm border border-stroke bg-panel px-1.5 py-px",
        "font-mono text-[10.5px] font-medium tracking-tight",
        level === "high" ? "text-ink-2" : level === "medium" ? "text-ink-3" : "text-ink-3 opacity-80",
        className,
      )}
    >
      <Icon className="size-3" aria-hidden />
      {value != null ? `${Math.round(value)}%` : "—"} conf
    </span>
  );
  return (
    <Tip label={reason ?? `${CONFIDENCE_LABEL[level]}${value != null ? ` (${Math.round(value)}/100)` : ""}`}>
      {chip}
    </Tip>
  );
}

/** Recommendation pill — strong_buy/buy/hold_watch/pass, on the quality ramp. */
export function RecommendationPill({
  value,
  className,
}: {
  value: Recommendation;
  className?: string;
}) {
  const style: Record<Recommendation, { bg: string; fg: string }> = {
    strong_buy: { bg: "var(--grade-a-soft)", fg: "var(--grade-a)" },
    buy: { bg: "var(--grade-b-soft)", fg: "var(--grade-b)" },
    hold_watch: { bg: "var(--grade-c-soft)", fg: "var(--grade-c)" },
    pass: { bg: "var(--raise)", fg: "var(--ink-3)" },
  };
  const s = style[value];
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 font-mono text-[10.5px] font-semibold uppercase tracking-[0.07em]",
        className,
      )}
      style={{ background: s.bg, color: s.fg }}
    >
      {RECOMMENDATION_LABEL[value]}
    </span>
  );
}

/** Signed delta with direction arrow — used for score/price movement. */
export function Delta({
  value,
  format = (v) => `${v > 0 ? "+" : ""}${v.toFixed(1)}%`,
  className,
  invert = false,
}: {
  value: number;
  format?: (v: number) => string;
  className?: string;
  /** When true, negative movement is good (e.g. price cuts, DOM). */
  invert?: boolean;
}) {
  const good = invert ? value < 0 : value > 0;
  const Icon = value >= 0 ? TrendingUp : TrendingDown;
  return (
    <span
      className={cn("figure inline-flex items-center gap-0.5 text-xs font-medium", className)}
      style={{ color: value === 0 ? "var(--ink-3)" : good ? "var(--pos)" : "var(--neg)" }}
    >
      <Icon className="size-3" aria-hidden />
      {format(value)}
    </span>
  );
}

/** Movement badges — "NEW", "PRICE CUT", "SCORE ↑" (dashboard cards, watchlist). */
export function MovementBadge({
  kind,
  className,
}: {
  kind: "new" | "price_cut" | "score_up" | "back_on_market" | "pending";
  className?: string;
}) {
  const config = {
    new: { label: "New", bg: "var(--accent-soft)", fg: "var(--accent-ink)" },
    price_cut: { label: "Price cut", bg: "var(--grade-a-soft)", fg: "var(--grade-a)" },
    score_up: { label: "Score ↑", bg: "var(--grade-a-soft)", fg: "var(--grade-a)" },
    back_on_market: { label: "Back on market", bg: "var(--grade-c-soft)", fg: "var(--grade-c)" },
    pending: { label: "Pending", bg: "var(--raise)", fg: "var(--ink-3)" },
  }[kind];
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-sm px-1.5 py-px font-mono text-[10px] font-semibold uppercase tracking-[0.06em]",
        className,
      )}
      style={{ background: config.bg, color: config.fg }}
    >
      {config.label}
    </span>
  );
}
