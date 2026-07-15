"use client";

/** S10 — Dashboard / Top Deals. The home surface: Top 25 for market × strategy,
 * market pulse strip, movement badges, alert summary. Ranked by profit, never price. */

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  LayoutGrid,
  Rows3,
  Bell,
  ArrowRight,
  Thermometer,
  Home,
  Timer,
  Package,
  Wallet,
} from "lucide-react";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Cell,
  Tooltip as RTooltip,
} from "recharts";
import { fetchTopDeals, fetchMarketReport, fetchAlerts, getPropertySync } from "@/lib/api";
import { useAppStore } from "@/stores/app-store";
import { moneyCompact, pct, pctSigned, ago, num } from "@/lib/format";
import { STRATEGY_LABEL, gradeFamily, GRADE_VAR } from "@/lib/domain";
import { DealCard, DealCardSkeleton, DealRow } from "@/components/deal/deal-card";
import { DealsTable } from "@/components/deal/deals-table";
import { Sparkline } from "@/components/deal/sparkline";
import { Delta } from "@/components/deal/chips";
import { ChartTooltipFrame } from "@/components/charts/chart-kit";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

function PulseTile({
  icon: Icon,
  label,
  value,
  delta,
  invert,
  spark,
  hint,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: React.ReactNode;
  delta?: number;
  invert?: boolean;
  spark?: number[];
  hint: string;
}) {
  return (
    <Tip label={hint}>
      <div className="flex min-w-0 items-center gap-3 rounded-lg border border-stroke bg-card px-3.5 py-3 transition-colors hover:border-stroke-strong">
        <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-accent-soft">
          <Icon className="size-4 text-accent-ink" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="eyebrow truncate">{label}</div>
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="figure whitespace-nowrap text-[15px] font-semibold text-ink">{value}</span>
            {delta != null && <Delta value={delta} invert={invert} />}
          </div>
        </div>
        {spark && <Sparkline data={spark} width={64} height={26} className="hidden min-[1500px]:block" />}
      </div>
    </Tip>
  );
}

export default function DashboardPage() {
  const marketId = useAppStore((s) => s.marketId);
  const strategy = useAppStore((s) => s.strategy);
  const [view, setView] = React.useState<"cards" | "table">("cards");

  const { data: deals, isLoading: dealsLoading } = useQuery({
    queryKey: ["top-deals", marketId, strategy],
    queryFn: () => fetchTopDeals(marketId, strategy),
  });
  const { data: report } = useQuery({
    queryKey: ["market-report", marketId],
    queryFn: () => fetchMarketReport(marketId),
  });
  const { data: alerts } = useQuery({ queryKey: ["alerts"], queryFn: fetchAlerts });

  const summary = report?.summary;
  const sparkOf = (pts?: { value: number }[]) => pts?.slice(-12).map((p) => p.value);

  const gradeDist = React.useMemo(() => {
    if (!deals) return [];
    const buckets = ["A", "B", "C", "D", "F"].map((g) => ({
      grade: g,
      count: deals.filter((d) => d.grade.startsWith(g)).length,
    }));
    return buckets.filter((b) => b.count > 0);
  }, [deals]);

  const unreadAlerts = alerts?.filter((a) => !a.read) ?? [];

  return (
    <div className="mx-auto max-w-[1560px] animate-fade-up px-4 py-5 md:px-6">
      {/* Header */}
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="eyebrow mb-1">
            {summary ? `${summary.name}, ${summary.state}` : "…"} · ranked by {STRATEGY_LABEL[strategy]}
          </div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">Top 25 opportunities</h1>
        </div>
        <div className="flex items-center gap-2">
          {summary && (
            <span className="figure hidden text-[11px] text-ink-3 sm:block">
              scores as of {ago(summary.as_of)}
            </span>
          )}
          <Tabs value={view} onValueChange={(v) => setView(v as "cards" | "table")}>
            <TabsList aria-label="View mode">
              <TabsTrigger value="cards" aria-label="Card view">
                <LayoutGrid className="size-3.5" /> Cards
              </TabsTrigger>
              <TabsTrigger value="table" aria-label="Table view">
                <Rows3 className="size-3.5" /> Table
              </TabsTrigger>
            </TabsList>
          </Tabs>
        </div>
      </div>

      {/* Market pulse strip */}
      <div className="mb-5 grid grid-cols-2 gap-2.5 md:grid-cols-3 xl:grid-cols-5">
        {summary ? (
          <>
            <PulseTile
              icon={Home}
              label="Med price"
              value={moneyCompact(summary.median_price)}
              delta={summary.median_price_mom_pct}
              spark={sparkOf(report?.series.median_price)}
              hint="Market median list price, month-over-month"
            />
            <PulseTile
              icon={Timer}
              label="Med DOM"
              value={`${summary.median_dom}d`}
              delta={summary.dom_mom}
              invert
              spark={sparkOf(report?.series.median_dom)}
              hint="Days on market — falling DOM means a faster market"
            />
            <PulseTile
              icon={Package}
              label="Inventory"
              value={`${summary.inventory_months.toFixed(1)} mo`}
              delta={summary.inventory_mom_pct}
              invert
              spark={sparkOf(report?.series.inventory_months)}
              hint="Months of supply at the current absorption rate"
            />
            <PulseTile
              icon={Wallet}
              label="Med rent"
              value={`${moneyCompact(summary.median_rent)}/mo`}
              delta={summary.rent_yoy_pct}
              spark={sparkOf(report?.series.median_rent)}
              hint="Median asking rent, year-over-year"
            />
            <PulseTile
              icon={Thermometer}
              label="Temp"
              value={
                <span className="flex items-baseline gap-1.5">
                  {summary.temperature}
                  <span className="truncate text-xs font-medium text-ink-3">
                    {summary.temperature_label.replace(" market", "")}
                  </span>
                </span>
              }
              hint="Composite of DOM, inventory, sale-to-list and price-cut share (0 cold – 100 hot)"
            />
          </>
        ) : (
          Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-[62px]" />)
        )}
      </div>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[1fr_300px]">
        {/* Deals */}
        <div className="min-w-0">
          {view === "cards" ? (
            <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2 2xl:grid-cols-3">
              {dealsLoading || !deals
                ? Array.from({ length: 9 }).map((_, i) => <DealCardSkeleton key={i} />)
                : deals.map((card, i) => <DealCard key={card.property_id} card={card} rank={i + 1} />)}
            </div>
          ) : dealsLoading || !deals ? (
            <Skeleton className="h-[480px] w-full" />
          ) : (
            <DealsTable cards={deals} />
          )}
        </div>

        {/* Rail */}
        <aside className="space-y-4 xl:sticky xl:top-[72px] xl:self-start">
          {/* Alert summary */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-1.5">
                <Bell className="size-3.5 text-accent-ink" /> Alerts
                {unreadAlerts.length > 0 && (
                  <span className="figure rounded-full bg-accent px-1.5 text-[10px] font-semibold text-white">
                    {unreadAlerts.length}
                  </span>
                )}
              </CardTitle>
              <Link href="/alerts" className="flex items-center gap-0.5 text-xs font-medium text-accent-ink hover:underline">
                Inbox <ArrowRight className="size-3" />
              </Link>
            </CardHeader>
            <CardContent className="space-y-0.5 p-2">
              {(alerts ?? []).slice(0, 3).map((a) => {
                const card = a.property_id ? getPropertySync(a.property_id)?.card : undefined;
                return card ? (
                  <DealRow key={a.id} card={card} />
                ) : (
                  <div key={a.id} className="px-2 py-1.5">
                    <div className="truncate text-xs font-medium text-ink">{a.title}</div>
                    <div className="figure text-[10.5px] text-ink-3">{ago(a.created_at)}</div>
                  </div>
                );
              })}
            </CardContent>
          </Card>

          {/* Price trend */}
          <Card>
            <CardHeader>
              <CardTitle>Median price · 12 mo</CardTitle>
              <span className="figure text-[11px] text-ink-3">{summary?.name}</span>
            </CardHeader>
            <CardContent className="pb-2 pl-0 pr-2 pt-2">
              {report ? (
                <div className="h-[150px]">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart
                      data={(() => {
                        const pts = report.series.median_price.slice(-12);
                        const floor = Math.min(...pts.map((p) => p.value)) * 0.985;
                        return pts.map((p) => ({ date: p.date, raw: p.value, value: p.value - floor }));
                      })()}
                      margin={{ top: 4, right: 4, bottom: 0, left: 8 }}
                    >
                      <XAxis dataKey="date" hide />
                      <YAxis hide />
                      <RTooltip
                        cursor={{ fill: "var(--raise)" }}
                        content={({ active, payload }) =>
                          active && payload?.length ? (
                            <ChartTooltipFrame
                              label={String(payload[0].payload.date).slice(0, 7)}
                              rows={[{ name: "Median", value: moneyCompact(Number(payload[0].payload.raw)), color: "var(--viz-1)" }]}
                            />
                          ) : null
                        }
                      />
                      <Bar dataKey="value" radius={[3, 3, 0, 0]} animationDuration={500}>
                        {report.series.median_price.slice(-12).map((_, i) => (
                          <Cell key={i} fill={i === 11 ? "var(--viz-1)" : "var(--accent-soft)"} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <Skeleton className="h-[150px]" />
              )}
              {summary && (
                <div className="flex items-center justify-between px-4 pt-1">
                  <span className="figure text-xs text-ink-3">{pctSigned(summary.median_price_mom_pct)} MoM</span>
                  <Link href="/markets" className="text-xs font-medium text-accent-ink hover:underline">
                    Market analysis →
                  </Link>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Grade distribution of the Top 25 */}
          <Card>
            <CardHeader>
              <CardTitle>Top-25 grade mix</CardTitle>
            </CardHeader>
            <CardContent className="pt-3">
              {gradeDist.length ? (
                <div className="h-[120px]">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={gradeDist} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
                      <XAxis dataKey="grade" tickLine={false} axisLine={false} dy={4} />
                      <YAxis hide />
                      <RTooltip
                        cursor={{ fill: "var(--raise)" }}
                        content={({ active, payload }) =>
                          active && payload?.length ? (
                            <ChartTooltipFrame
                              label={`Grade ${payload[0].payload.grade}`}
                              rows={[{ name: "Deals", value: String(payload[0].value) }]}
                            />
                          ) : null
                        }
                      />
                      <Bar dataKey="count" radius={[3, 3, 0, 0]} animationDuration={500}>
                        {gradeDist.map((b) => (
                          <Cell key={b.grade} fill={GRADE_VAR[gradeFamily(b.grade)]} opacity={0.85} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <Skeleton className="h-[120px]" />
              )}
              {summary && (
                <p className="mt-2 text-[11.5px] leading-relaxed text-ink-3">
                  {num(summary.active_listings)} active listings analyzed · top{" "}
                  {deals ? Math.round((25 / summary.active_listings) * 1000) / 10 : "—"}% shown
                </p>
              )}
            </CardContent>
          </Card>

          <p className="px-1 text-[10.5px] leading-relaxed text-ink-faint">
            Scores and dollar figures are estimates with stated confidence, not offers or
            appraisals. Verify with inspection and local counsel before transacting.
          </p>
        </aside>
      </div>
    </div>
  );
}
