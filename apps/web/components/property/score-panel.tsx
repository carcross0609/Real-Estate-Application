"use client";

/** Score decomposition panel (S12) — headline GradeRing, category bars, factor ledger.
 * Every number carries its why: factors show raw value, weight, signed contribution. */

import * as React from "react";
import type { ScoreResult } from "@/lib/types";
import { STRATEGY_LABEL, GRADE_VAR, gradeFamily, type Strategy } from "@/lib/domain";
import { GradeRing } from "@/components/deal/grade-ring";
import { RecommendationPill, ConfidenceChip } from "@/components/deal/chips";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tip } from "@/components/ui/tooltip";
import { ago } from "@/lib/format";
import { CheckCircle2, MinusCircle, OctagonAlert } from "lucide-react";
import { cn } from "@/lib/utils";

const CATEGORY_LABELS: Array<{ key: keyof ScoreResult["category_scores"]; label: string; inverted?: boolean }> = [
  { key: "profitability", label: "Profitability" },
  { key: "location", label: "Location" },
  { key: "condition", label: "Condition" },
  { key: "rental_strength", label: "Rental strength" },
  { key: "appreciation", label: "Appreciation" },
  { key: "liquidity", label: "Liquidity" },
  { key: "market_conditions", label: "Market" },
  { key: "risk", label: "Risk", inverted: true },
  { key: "renovation_complexity", label: "Reno complexity", inverted: true },
  { key: "financing_difficulty", label: "Financing difficulty", inverted: true },
];

function CategoryBar({ label, value, inverted }: { label: string; value: number; inverted?: boolean }) {
  // For inverted categories high = bad → color from the flipped value.
  const effective = inverted ? 100 - value : value;
  const color =
    effective >= 70 ? "var(--grade-a)" : effective >= 45 ? "var(--grade-c)" : "var(--grade-f)";
  return (
    <div className="group">
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <span className="text-xs text-ink-2">{label}</span>
        <span className="figure text-xs font-semibold text-ink">{Math.round(value)}</span>
      </div>
      <div className="h-1 overflow-hidden rounded-full bg-raise">
        <div
          className="h-full rounded-full transition-[width] duration-700 ease-[var(--ease-swift)]"
          style={{ width: `${value}%`, background: color, opacity: 0.9 }}
        />
      </div>
    </div>
  );
}

export function ScorePanel({ score }: { score: ScoreResult }) {
  const [strategyTab, setStrategyTab] = React.useState<Strategy>(score.winning_strategy);
  const current = score.strategy_scores.find((s) => s.strategy === strategyTab) ?? score.strategy_scores[0];

  return (
    <div className="rounded-lg border border-stroke bg-card">
      {/* Headline */}
      <div className="flex flex-wrap items-center gap-4 border-b border-stroke p-4">
        <GradeRing score={score.overall_score} grade={score.grade} size="xl" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="figure text-2xl font-semibold tracking-tight text-ink">
              {Math.round(score.overall_score)}
            </span>
            <span className="text-sm text-ink-3">/100</span>
            <RecommendationPill value={score.recommendation} />
          </div>
          <div className="mt-1 text-[13px] text-ink-2">
            Best as <span className="font-medium text-ink">{STRATEGY_LABEL[score.winning_strategy]}</span>
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <Tip label="Composite exposure across condition, market, financing and data-quality factors — lower is safer">
              <span className="figure cursor-help rounded-sm border border-stroke bg-panel px-1.5 py-px text-[10.5px] text-ink-3">
                risk {Math.round(score.risk_score)}/100
              </span>
            </Tip>
            <ConfidenceChip value={score.confidence_score} />
            <span className="figure text-[10.5px] text-ink-faint">
              v{score.scoring_version} · {ago(score.as_of)}
            </span>
          </div>
        </div>
      </div>

      {/* Why — positives / negatives / caps */}
      <div className="grid gap-1.5 border-b border-stroke p-4 text-[12.5px] leading-relaxed">
        {score.explanation.positives.map((p, i) => (
          <div key={`p${i}`} className="flex gap-2 text-ink-2">
            <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-pos" aria-hidden />
            {p}
          </div>
        ))}
        {score.explanation.negatives.map((n, i) => (
          <div key={`n${i}`} className="flex gap-2 text-ink-2">
            <MinusCircle className="mt-0.5 size-3.5 shrink-0 text-neg" aria-hidden />
            {n}
          </div>
        ))}
        {score.explanation.caps.map((c, i) => (
          <div key={`c${i}`} className="flex gap-2 font-medium text-grade-c">
            <OctagonAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
            {c}
          </div>
        ))}
      </div>

      {/* Category scores */}
      <div className="grid grid-cols-2 gap-x-5 gap-y-3 border-b border-stroke p-4">
        {CATEGORY_LABELS.map((c) => (
          <CategoryBar
            key={c.key}
            label={c.label}
            value={score.category_scores[c.key]}
            inverted={c.inverted}
          />
        ))}
      </div>

      {/* Factor ledger per strategy */}
      <div className="p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <span className="eyebrow">Factor ledger</span>
          <Tabs value={strategyTab} onValueChange={(v) => setStrategyTab(v as Strategy)}>
            <TabsList className="h-7">
              {score.strategy_scores.map((s) => (
                <TabsTrigger key={s.strategy} value={s.strategy} className="px-2 text-[11px]">
                  {STRATEGY_LABEL[s.strategy]}
                  <span
                    className="figure font-semibold"
                    style={{ color: GRADE_VAR[gradeFamily(s.score >= 85 ? "A" : s.score >= 55 ? "B" : s.score >= 35 ? "C" : "F")] }}
                  >
                    {Math.round(s.score)}
                  </span>
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        </div>

        {current && (
          <div className="space-y-1">
            {current.capped_by && (
              <div className="mb-2 rounded-md border border-grade-c/25 bg-grade-c-soft px-2.5 py-1.5 text-xs text-grade-c">
                Hard gate: {current.capped_by}
              </div>
            )}
            {current.factors.map((f) => {
              const maxAbs = Math.max(...current.factors.map((x) => Math.abs(x.contribution)), 1);
              return (
                <Tip
                  key={f.factor_key}
                  label={
                    f.rationale ??
                    `Weight ${(f.weight * 100).toFixed(0)}% · normalized ${Math.round(f.normalized)}/100`
                  }
                >
                  <div className="grid cursor-help grid-cols-[minmax(0,1fr)_88px_44px] items-center gap-2 rounded px-1.5 py-1 transition-colors hover:bg-raise/60">
                    <div className="flex min-w-0 items-baseline gap-1.5">
                      <span className="figure w-4 shrink-0 text-[10px] text-ink-faint">{f.group}</span>
                      <span className="truncate text-xs text-ink-2">{f.label}</span>
                      <span className="figure hidden shrink-0 text-[10.5px] text-ink-3 sm:inline">{f.raw_display}</span>
                    </div>
                    <div className="relative h-[3px] overflow-hidden rounded-full bg-raise">
                      <div
                        className="absolute left-1/2 h-full rounded-full"
                        style={{
                          width: `${(Math.abs(f.contribution) / maxAbs) * 50}%`,
                          background: f.contribution >= 0 ? "var(--pos)" : "var(--neg)",
                          transform: f.contribution >= 0 ? "none" : "translateX(-100%)",
                        }}
                      />
                    </div>
                    <span
                      className={cn(
                        "figure text-right text-[11px] font-semibold",
                        f.contribution >= 0 ? "text-pos" : "text-neg",
                      )}
                    >
                      {f.contribution >= 0 ? "+" : ""}
                      {f.contribution.toFixed(1)}
                    </span>
                  </div>
                </Tip>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
