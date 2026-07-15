"use client";

/** S21 — Portfolio: owned properties, underwrite vs. actuals, equity tracking,
 * refi/sell flags. The feedback loop that keeps the engine honest. */

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Briefcase,
  Landmark,
  Wallet,
  PiggyBank,
  Percent,
  RefreshCcw,
  ArrowDownRight,
  ArrowUpRight,
  CircleAlert,
} from "lucide-react";
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RTooltip,
} from "recharts";
import { fetchPortfolio } from "@/lib/api";
import type { PortfolioHolding } from "@/lib/types";
import { money, moneyCompact, pct, dateShort, dateTick } from "@/lib/format";
import { STRATEGY_SHORT } from "@/lib/domain";
import { PropertyArt } from "@/components/deal/property-art";
import { Stat, SectionHeader, EmptyState } from "@/components/deal/stat";
import { Sparkline } from "@/components/deal/sparkline";
import { ChartTooltipFrame } from "@/components/charts/chart-kit";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { Tip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

function DeltaVsUnderwrite({ actual, underwritten, suffix = "" }: { actual: number | null; underwritten: number | null; suffix?: string }) {
  if (actual == null || underwritten == null) return <span className="text-ink-3">—</span>;
  const diff = actual - underwritten;
  const good = diff >= 0;
  const Icon = good ? ArrowUpRight : ArrowDownRight;
  return (
    <Tip label={`Underwrote ${moneyCompact(underwritten)}${suffix} — actual ${good ? "beats" : "trails"} by ${moneyCompact(Math.abs(diff))}${suffix}`}>
      <span className={cn("figure inline-flex cursor-help items-center gap-0.5 text-[11px] font-medium", good ? "text-pos" : "text-neg")}>
        <Icon className="size-3" aria-hidden />
        {good ? "+" : "−"}
        {moneyCompact(Math.abs(diff))}
        {suffix} vs UW
      </span>
    </Tip>
  );
}

export default function PortfolioPage() {
  const { data: holdings, isLoading } = useQuery({ queryKey: ["portfolio"], queryFn: fetchPortfolio });

  const totals = React.useMemo(() => {
    if (!holdings) return null;
    const value = holdings.reduce((s, h) => s + h.current_value, 0);
    const debt = holdings.reduce((s, h) => s + h.loan_balance, 0);
    const cf = holdings.reduce((s, h) => s + (h.cash_flow_actual ?? 0), 0);
    const basis = holdings.reduce((s, h) => s + h.purchase_price + h.rehab_actual, 0);
    return { value, debt, equity: value - debt, cf, basis, appreciation: ((value - basis) / basis) * 100 };
  }, [holdings]);

  /* Combined quarterly equity curve — sum of per-holding (value − straight-line balance est.). */
  const equitySeries = React.useMemo(() => {
    if (!holdings?.length) return [];
    const longest = Math.max(...holdings.map((h) => h.value_series.length));
    const ref = holdings.find((h) => h.value_series.length === longest)!;
    return ref.value_series.map((pt, i) => {
      let total = 0;
      for (const h of holdings) {
        const offset = i - (longest - h.value_series.length);
        if (offset >= 0) {
          // Current-balance approximation — good enough for a trend curve.
          total += h.value_series[offset].value - h.loan_balance;
        }
      }
      return { date: pt.date, value: Math.round(total) };
    });
  }, [holdings]);

  return (
    <div className="mx-auto max-w-[1280px] animate-fade-up px-4 py-5 md:px-6">
      <SectionHeader eyebrow={`${holdings?.length ?? 0} doors`} title="Portfolio" className="mb-4">
        <span className="figure hidden text-[11px] text-ink-3 sm:block">valuations refresh weekly</span>
      </SectionHeader>

      {/* Summary strip */}
      <div className="mb-5 grid grid-cols-2 gap-2.5 md:grid-cols-5">
        {totals ? (
          [
            { icon: Briefcase, label: "Market value", value: moneyCompact(totals.value), hint: "Sum of current model valuations" },
            { icon: PiggyBank, label: "Equity", value: moneyCompact(totals.equity), hint: "Value minus loan balances" },
            { icon: Landmark, label: "Debt", value: moneyCompact(totals.debt), hint: "Outstanding principal" },
            { icon: Wallet, label: "Cash flow", value: `${moneyCompact(totals.cf)}/mo`, hint: "Actual, after debt service" },
            { icon: Percent, label: "Apprec.", value: pct(totals.appreciation), hint: "Value growth over all-in basis" },
          ].map((t) => (
            <Tip key={t.label} label={t.hint}>
              <div className="flex items-center gap-3 rounded-lg border border-stroke bg-card px-3.5 py-3 transition-colors hover:border-stroke-strong">
                <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-accent-soft">
                  <t.icon className="size-4 text-accent-ink" />
                </div>
                <div className="min-w-0">
                  <div className="eyebrow truncate">{t.label}</div>
                  <div className="figure text-[15px] font-semibold text-ink">{t.value}</div>
                </div>
              </div>
            </Tip>
          ))
        ) : (
          Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-[62px]" />)
        )}
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        {/* Holdings */}
        <div className="min-w-0 space-y-3">
          {isLoading ? (
            Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-36" />)
          ) : !holdings?.length ? (
            <EmptyState
              icon={Briefcase}
              title="No properties in your portfolio yet"
              body="When you close a deal, add it here — DealLens tracks underwrite vs. actuals, equity, and flags refi or sell windows."
            />
          ) : (
            holdings.map((h) => (
              <article key={h.property_id} className="grid grid-cols-[96px_minmax(0,1fr)] gap-4 rounded-lg border border-stroke bg-card p-3.5 transition-colors hover:border-stroke-strong sm:grid-cols-[128px_minmax(0,1fr)]">
                <PropertyArt seed={h.property_id} className="h-full min-h-28 rounded-md" />
                <div className="min-w-0">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                      <h3 className="text-[14px] font-semibold text-ink">{h.nickname}</h3>
                      <div className="figure text-[11px] text-ink-3">
                        {STRATEGY_SHORT[h.strategy]} · acquired {dateShort(h.acquired_at)} · {pct(h.rate_pct, 1)} note
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="figure text-[15px] font-semibold text-ink">{moneyCompact(h.current_value)}</div>
                      <div className="figure text-[10.5px] text-ink-3">
                        basis {moneyCompact(h.purchase_price + h.rehab_actual)}
                      </div>
                    </div>
                  </div>

                  <div className="mt-2.5 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
                    <Stat
                      size="sm"
                      label="Equity"
                      value={<span className="text-pos">{moneyCompact(h.equity)}</span>}
                      hint={`Loan balance ${moneyCompact(h.loan_balance)}`}
                    />
                    <Stat
                      size="sm"
                      label="Rent"
                      value={h.rent_actual ? `${moneyCompact(h.rent_actual)}/mo` : "—"}
                      sub={<DeltaVsUnderwrite actual={h.rent_actual} underwritten={h.rent_underwritten} suffix="/mo" />}
                    />
                    <Stat
                      size="sm"
                      label="Cash flow"
                      value={
                        <span className={h.cash_flow_actual && h.cash_flow_actual > 0 ? "text-pos" : "text-neg"}>
                          {h.cash_flow_actual != null ? `${moneyCompact(h.cash_flow_actual)}/mo` : "—"}
                        </span>
                      }
                      sub={<DeltaVsUnderwrite actual={h.cash_flow_actual} underwritten={h.cash_flow_underwritten} suffix="/mo" />}
                    />
                    <Stat size="sm" label="CoC actual" value={pct(h.coc_actual_pct)} hint="Actual annual cash flow over cash invested at close" />
                  </div>

                  <div className="mt-2.5 flex flex-wrap items-center gap-2">
                    <Sparkline
                      data={h.value_series.map((p) => p.value)}
                      width={110}
                      height={24}
                      stroke="var(--viz-6)"
                    />
                    {h.flags.map((f) => (
                      <Tip key={f.kind} label={f.label}>
                        <span
                          className={cn(
                            "flex cursor-help items-center gap-1 rounded-sm px-1.5 py-px font-mono text-[10px] font-semibold uppercase tracking-wide",
                            f.kind === "refi" && "bg-accent-soft text-accent-ink",
                            f.kind === "sell" && "bg-grade-c-soft text-grade-c",
                            f.kind === "rent_below_market" && "bg-grade-d-soft text-grade-d",
                          )}
                        >
                          {f.kind === "refi" ? <RefreshCcw className="size-2.5" /> : <CircleAlert className="size-2.5" />}
                          {f.kind === "refi" ? "Refi window" : f.kind === "sell" ? "Review exit" : "Rent gap"}
                        </span>
                      </Tip>
                    ))}
                  </div>
                </div>
              </article>
            ))
          )}
        </div>

        {/* Equity curve */}
        <aside className="space-y-4 xl:sticky xl:top-[72px] xl:self-start">
          <Card>
            <CardHeader>
              <CardTitle>Portfolio equity</CardTitle>
              {totals && <span className="figure text-xs font-semibold text-pos">{moneyCompact(totals.equity)}</span>}
            </CardHeader>
            <CardContent className="pl-0 pr-2">
              {equitySeries.length ? (
                <div className="h-48">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={equitySeries} margin={{ top: 6, right: 4, bottom: 0, left: 0 }}>
                      <defs>
                        <linearGradient id="eq" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="var(--viz-6)" stopOpacity={0.28} />
                          <stop offset="100%" stopColor="var(--viz-6)" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid vertical={false} />
                      <XAxis dataKey="date" tickFormatter={dateTick} tickLine={false} axisLine={false} minTickGap={56} dy={4} />
                      <YAxis tickFormatter={(v) => moneyCompact(v)} tickLine={false} axisLine={false} width={56} />
                      <RTooltip
                        cursor={{ stroke: "var(--stroke-strong)", strokeDasharray: "3 3" }}
                        content={({ active, payload, label }) =>
                          active && payload?.length ? (
                            <ChartTooltipFrame
                              label={dateTick(String(label))}
                              rows={[{ name: "Equity", value: moneyCompact(Number(payload[0].value)), color: "var(--viz-6)" }]}
                            />
                          ) : null
                        }
                      />
                      <Area type="monotone" dataKey="value" stroke="var(--viz-6)" strokeWidth={1.75} fill="url(#eq)" animationDuration={600} />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <Skeleton className="ml-4 h-48" />
              )}
            </CardContent>
          </Card>

          <Card>
            <CardContent>
              <div className="eyebrow mb-2">Feedback loop</div>
              <p className="text-[12.5px] leading-relaxed text-ink-2">
                Your actuals quietly recalibrate local models: rent estimates in your markets now lean on{" "}
                <span className="figure font-medium text-ink">{holdings?.length ?? 0} labeled outcomes</span> from
                this portfolio. Underwrite-vs-actual gaps feed the accuracy dashboard.
              </p>
            </CardContent>
          </Card>
        </aside>
      </div>
    </div>
  );
}
