"use client";

/** Per-strategy financial summary (S12). Numbers come from the deterministic engine —
 * AI never does arithmetic. Waterfall lines expose the derivation of every headline. */

import * as React from "react";
import Link from "next/link";
import type { PropertyBundle, StrategyAnalysis } from "@/lib/types";
import { STRATEGY_LABEL } from "@/lib/domain";
import { money, moneyCompact, moneyRange, pct, ratio } from "@/lib/format";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Stat } from "@/components/deal/stat";
import { buttonVariants } from "@/components/ui/button";
import { Calculator } from "lucide-react";
import { cn } from "@/lib/utils";

function Waterfall({ analysis }: { analysis: StrategyAnalysis }) {
  const max = Math.max(...analysis.waterfall.map((l) => Math.abs(l.amount)), 1);
  return (
    <div className="space-y-1" role="list" aria-label="Cash flow waterfall">
      {analysis.waterfall.map((line, i) => {
        const isResult = i === analysis.waterfall.length - 1;
        return (
          <div
            key={line.key}
            role="listitem"
            className={cn(
              "grid grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_92px] items-center gap-3 rounded px-2 py-1",
              isResult && "mt-1.5 border-t border-stroke pt-2.5",
            )}
          >
            <span className={cn("truncate text-xs", isResult ? "font-semibold text-ink" : "text-ink-2")}>
              {line.label}
            </span>
            <div className="h-[5px] overflow-hidden rounded-full bg-raise">
              <div
                className="h-full rounded-full transition-[width] duration-700 ease-[var(--ease-swift)]"
                style={{
                  width: `${(Math.abs(line.amount) / max) * 100}%`,
                  background: line.amount >= 0 ? "var(--pos)" : "var(--neg)",
                  opacity: isResult ? 1 : 0.55,
                }}
              />
            </div>
            <span
              className={cn(
                "figure text-right text-xs font-medium",
                isResult ? (line.amount >= 0 ? "text-pos" : "text-neg") : "text-ink-2",
              )}
            >
              {line.amount < 0 ? "−" : ""}
              {moneyCompact(Math.abs(line.amount))}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function StrategyPane({ a, propertyId }: { a: StrategyAnalysis; propertyId: string }) {
  const isFlip = a.strategy === "flip";
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div className="grid grid-cols-2 content-start gap-x-4 gap-y-4 sm:grid-cols-3">
        <Stat label="Purchase" value={money(a.purchase_price)} hint="Current list price as the acquisition basis" />
        <Stat
          label="Rehab"
          value={moneyCompact(a.rehab_budget)}
          sub="±18% band"
          hint="Vision-modeled midpoint; inspection-gated"
        />
        <Stat label="All-in" value={moneyCompact(a.all_in)} hint="Purchase + closing + rehab + holding" />
        <Stat
          label="ARV"
          value={moneyCompact(a.arv)}
          sub={moneyRange(a.arv_lo, a.arv_hi)}
          hint={`After-repair value, ${Math.round(a.arv_confidence)}% confidence`}
        />
        {isFlip ? (
          <>
            <Stat
              label="Net profit"
              value={<span className={a.net_profit && a.net_profit > 0 ? "text-pos" : "text-neg"}>{moneyCompact(a.net_profit)}</span>}
              hint="ARV − all-in − selling costs"
            />
            <Stat label="Margin" value={pct(a.flip_margin_pct)} hint="Net profit over ARV" />
            <Stat label="Ann. ROI" value={pct(a.roi_annualized_pct, 0)} hint={`Annualized over the ${a.hold_months}-month hold`} />
            <Stat label="Hold" value={`${a.hold_months} mo`} hint="Modeled acquisition-to-close timeline" />
            <Stat label="Cash needed" value={moneyCompact(a.cash_invested)} hint="15% down hard-money structure + rehab + closing" />
          </>
        ) : (
          <>
            <Stat
              label="Rent"
              value={`${moneyCompact(a.rent_monthly)}/mo`}
              sub={moneyRange(a.rent_lo, a.rent_hi)}
              hint={`Rental estimate, ${Math.round(a.rent_confidence ?? 0)}% confidence`}
            />
            <Stat
              label="Cash flow"
              value={
                <span className={a.cash_flow_monthly && a.cash_flow_monthly > 0 ? "text-pos" : "text-neg"}>
                  {moneyCompact(a.cash_flow_monthly)}/mo
                </span>
              }
              hint="After operating expenses and debt service"
            />
            <Stat label="CoC" value={pct(a.coc_pct)} hint="Year-1 cash flow over cash invested" />
            <Stat label="Cap rate" value={pct(a.cap_rate_pct)} hint="NOI over all-in basis" />
            <Stat label="DSCR" value={ratio(a.dscr)} hint="NOI over annual debt service — lenders want ≥1.2x" />
            {a.strategy === "brrrr" ? (
              <Stat
                label="Left in deal"
                value={moneyCompact(a.capital_left_in)}
                hint="Capital remaining after 75%-LTV refi at ARV"
              />
            ) : (
              <Stat label="Breakeven occ." value={pct(a.breakeven_occupancy_pct, 0)} hint="Occupancy at which cash flow crosses zero" />
            )}
          </>
        )}
      </div>

      <div className="rounded-md border border-hairline bg-panel/60 p-3">
        <div className="mb-2 flex items-center justify-between">
          <span className="eyebrow">{isFlip ? "Deal waterfall" : "Monthly waterfall"}</span>
          <Link
            href={`/analyzer/${propertyId}?strategy=${a.strategy}`}
            className={cn(buttonVariants({ variant: "outline", size: "xs" }))}
          >
            <Calculator className="size-3" /> Adjust assumptions
          </Link>
        </div>
        <Waterfall analysis={a} />
        {!isFlip && a.expenses_monthly.length > 0 && (
          <details className="group mt-3">
            <summary className="cursor-pointer list-none text-xs font-medium text-accent-ink hover:underline">
              Expense detail ({a.expenses_monthly.length} lines)
            </summary>
            <div className="mt-2 space-y-1">
              {a.expenses_monthly.map((e) => (
                <div key={e.key} className="flex items-baseline justify-between text-xs">
                  <span className="text-ink-3">{e.label}</span>
                  <span className="figure text-ink-2">−{moneyCompact(e.amount)}</span>
                </div>
              ))}
            </div>
          </details>
        )}
      </div>
    </div>
  );
}

export function FinancialTabs({ bundle }: { bundle: PropertyBundle }) {
  return (
    <Tabs defaultValue={bundle.score.winning_strategy}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <TabsList>
          {bundle.analyses.map((a) => (
            <TabsTrigger key={a.strategy} value={a.strategy}>
              {STRATEGY_LABEL[a.strategy]}
            </TabsTrigger>
          ))}
        </TabsList>
        <span className="figure text-[10.5px] text-ink-faint">
          deterministic engine v2026.07 · estimates, not offers
        </span>
      </div>
      {bundle.analyses.map((a) => (
        <TabsContent key={a.strategy} value={a.strategy}>
          <StrategyPane a={a} propertyId={bundle.card.property_id} />
        </TabsContent>
      ))}
    </Tabs>
  );
}
