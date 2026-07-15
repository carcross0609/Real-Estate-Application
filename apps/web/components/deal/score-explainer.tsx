"use client";

/**
 * ScoreExplainer — "the number and the why, always together" (§21 #1). Wraps any score
 * display; clicking opens the factor decomposition for the winning strategy: top
 * contributions as a signed bar ledger, plus caps when a hard gate bound the score.
 */

import * as React from "react";
import Link from "next/link";
import type { ScoreResult } from "@/lib/types";
import { STRATEGY_LABEL } from "@/lib/domain";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { GradeRing } from "./grade-ring";
import { ConfidenceChip } from "./chips";
import { cn } from "@/lib/utils";

export function ScoreExplainer({
  score,
  children,
  detailHref,
  side = "bottom",
}: {
  score: ScoreResult;
  children: React.ReactNode;
  detailHref?: string;
  side?: "top" | "bottom" | "left" | "right";
}) {
  const winning =
    score.strategy_scores.find((s) => s.strategy === score.winning_strategy) ??
    score.strategy_scores[0];
  const top = winning?.factors.slice(0, 6) ?? [];
  const maxAbs = Math.max(...top.map((f) => Math.abs(f.contribution)), 1);

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          className="cursor-pointer rounded-md text-left transition-transform duration-150 hover:scale-[1.03] focus-visible:outline-2"
          aria-label={`Explain score ${Math.round(score.overall_score)}`}
        >
          {children}
        </button>
      </PopoverTrigger>
      <PopoverContent side={side} className="w-80 p-0" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-3 border-b border-stroke px-3.5 py-3">
          <GradeRing score={score.overall_score} grade={score.grade} size="sm" animate={false} />
          <div className="min-w-0 flex-1">
            <div className="text-[13px] font-semibold text-ink">
              {Math.round(score.overall_score)}/100 as {STRATEGY_LABEL[score.winning_strategy]}
            </div>
            <div className="figure text-[11px] text-ink-3">
              risk {Math.round(score.risk_score)} · v{score.scoring_version}
            </div>
          </div>
          <ConfidenceChip value={score.confidence_score} />
        </div>

        <div className="px-3.5 py-3">
          <div className="eyebrow mb-2">Top factors · {STRATEGY_LABEL[score.winning_strategy]}</div>
          <div className="space-y-1.5">
            {top.map((f) => (
              <div key={f.factor_key} className="grid grid-cols-[1fr_auto] items-center gap-x-2 gap-y-0.5">
                <div className="flex min-w-0 items-baseline gap-1.5">
                  <span className="truncate text-xs text-ink-2">{f.label}</span>
                  <span className="figure shrink-0 text-[10.5px] text-ink-3">{f.raw_display}</span>
                </div>
                <span
                  className={cn("figure text-[11px] font-semibold", f.contribution >= 0 ? "text-pos" : "text-neg")}
                >
                  {f.contribution >= 0 ? "+" : ""}
                  {f.contribution.toFixed(1)}
                </span>
                <div className="col-span-2 flex h-1 items-center">
                  <div className="relative h-[3px] w-full overflow-hidden rounded-full bg-raise">
                    <div
                      className="absolute left-1/2 h-full rounded-full"
                      style={{
                        width: `${(Math.abs(f.contribution) / maxAbs) * 50}%`,
                        background: f.contribution >= 0 ? "var(--pos)" : "var(--neg)",
                        transform: f.contribution >= 0 ? "none" : "translateX(-100%)",
                      }}
                    />
                  </div>
                </div>
              </div>
            ))}
          </div>
          {score.explanation.caps.length > 0 && (
            <div className="mt-2.5 rounded-md border border-grade-c/25 bg-grade-c-soft px-2.5 py-1.5 text-[11.5px] leading-snug text-grade-c">
              {score.explanation.caps[0]}
            </div>
          )}
        </div>

        {detailHref && (
          <div className="border-t border-stroke px-3.5 py-2">
            <Link href={detailHref} className="text-xs font-medium text-accent-ink hover:underline">
              Full breakdown →
            </Link>
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}
