"use client";

/** §28 / S33 — Market Analysis: market health, trend series, submarket league table,
 * price-band structure. Where "which pocket, at what price" gets answered. */

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Thermometer, ArrowRight } from "lucide-react";
import { fetchMarketReport, fetchMarkets } from "@/lib/api";
import { useAppStore } from "@/stores/app-store";
import { moneyCompact, pct, pctSigned, num, ago } from "@/lib/format";
import { scoreColor } from "@/lib/domain";
import { TrendArea } from "@/components/charts/chart-kit";
import { Stat, SectionHeader } from "@/components/deal/stat";
import { Delta } from "@/components/deal/chips";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

function TemperatureGauge({ value, label }: { value: number; label: string }) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-stroke bg-card px-4 py-3">
      <Thermometer className="size-4 shrink-0 text-accent-ink" aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="mb-1.5 flex items-baseline justify-between gap-3">
          <span className="eyebrow">Market temperature</span>
          <span className="figure text-sm font-semibold text-ink">
            {value} <span className="text-xs font-medium text-ink-3">· {label}</span>
          </span>
        </div>
        <div className="relative h-1.5 overflow-hidden rounded-full bg-raise">
          <div
            className="absolute inset-y-0 left-0 rounded-full"
            style={{
              width: `${value}%`,
              background: "linear-gradient(90deg, var(--viz-2), var(--accent), var(--grade-d))",
            }}
          />
          <div
            className="absolute top-1/2 size-2.5 -translate-y-1/2 rounded-full border-2 border-canvas bg-ink"
            style={{ left: `calc(${value}% - 5px)` }}
            aria-hidden
          />
        </div>
        <div className="mt-1 flex justify-between font-mono text-[9px] uppercase tracking-wide text-ink-faint">
          <span>Buyer&apos;s</span>
          <span>Balanced</span>
          <span>Seller&apos;s</span>
        </div>
      </div>
    </div>
  );
}

export default function MarketsPage() {
  const marketId = useAppStore((s) => s.marketId);
  const setMarket = useAppStore((s) => s.setMarket);
  const { data: markets } = useQuery({ queryKey: ["markets"], queryFn: fetchMarkets });
  const { data: report, isLoading } = useQuery({
    queryKey: ["market-report", marketId],
    queryFn: () => fetchMarketReport(marketId),
  });

  const s = report?.summary;

  return (
    <div className="mx-auto max-w-[1280px] animate-fade-up px-4 py-5 md:px-6">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="eyebrow mb-1">Market intelligence {s ? `· as of ${ago(s.as_of)}` : ""}</div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">
            {s ? `${s.name}, ${s.state}` : "…"}
          </h1>
        </div>
        {markets && (
          <Tabs value={marketId} onValueChange={setMarket}>
            <TabsList aria-label="Market">
              {markets.map((m) => (
                <TabsTrigger key={m.market_id} value={m.market_id}>
                  {m.name}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        )}
      </div>

      {isLoading || !report || !s ? (
        <div className="space-y-4">
          <Skeleton className="h-20" />
          <div className="grid gap-4 md:grid-cols-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-56" />
            ))}
          </div>
        </div>
      ) : (
        <>
          {/* Narrative + gauge */}
          <div className="mb-5 grid gap-3 lg:grid-cols-[minmax(0,1fr)_360px]">
            <Card>
              <CardContent className="space-y-2">
                {report.narrative.map((p, i) => (
                  <p key={i} className="text-[13.5px] leading-relaxed text-ink-2">
                    <span className="figure mr-2 text-[10px] text-ink-faint">{String(i + 1).padStart(2, "0")}</span>
                    {p}
                  </p>
                ))}
              </CardContent>
            </Card>
            <div className="space-y-3">
              <TemperatureGauge value={s.temperature} label={s.temperature_label} />
              <div className="grid grid-cols-3 gap-2">
                <div className="rounded-lg border border-stroke bg-card px-3 py-2.5">
                  <Stat size="sm" label="Active" value={num(s.active_listings)} />
                </div>
                <div className="rounded-lg border border-stroke bg-card px-3 py-2.5">
                  <Stat size="sm" label="Med rent" value={`${moneyCompact(s.median_rent)}`} sub={<Delta value={s.rent_yoy_pct} format={(v) => `${pctSigned(v)} YoY`} />} />
                </div>
                <div className="rounded-lg border border-stroke bg-card px-3 py-2.5">
                  <Stat size="sm" label="Inventory" value={`${s.inventory_months.toFixed(1)}mo`} sub={<Delta value={s.inventory_mom_pct} invert format={(v) => `${pctSigned(v)} MoM`} />} />
                </div>
              </div>
            </div>
          </div>

          {/* Trend charts */}
          <div className="mb-5 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {(
              [
                { title: "Median list price", series: report.series.median_price, color: "var(--viz-1)", fmt: (v: number) => moneyCompact(v), current: moneyCompact(s.median_price), delta: s.median_price_mom_pct, invert: false },
                { title: "Days on market", series: report.series.median_dom, color: "var(--viz-2)", fmt: (v: number) => `${Math.round(v)}d`, current: `${s.median_dom}d`, delta: s.dom_mom, invert: true },
                { title: "Months of inventory", series: report.series.inventory_months, color: "var(--viz-3)", fmt: (v: number) => v.toFixed(1), current: `${s.inventory_months.toFixed(1)} mo`, delta: s.inventory_mom_pct, invert: true },
                { title: "Median asking rent", series: report.series.median_rent, color: "var(--viz-6)", fmt: (v: number) => moneyCompact(v), current: `${moneyCompact(s.median_rent)}/mo`, delta: s.rent_yoy_pct, invert: false },
                { title: "Listings with price cuts", series: report.series.price_cuts_pct, color: "var(--viz-5)", fmt: (v: number) => `${Math.round(v)}%`, current: `${Math.round(report.series.price_cuts_pct.at(-1)!.value)}%`, delta: undefined, invert: false },
                { title: "Sale-to-list ratio", series: report.series.sale_to_list_pct, color: "var(--viz-4)", fmt: (v: number) => `${v.toFixed(1)}%`, current: `${report.series.sale_to_list_pct.at(-1)!.value.toFixed(1)}%`, delta: undefined, invert: false },
              ] as const
            ).map((c) => (
              <Card key={c.title}>
                <CardHeader>
                  <CardTitle>{c.title}</CardTitle>
                  <div className="flex items-center gap-2">
                    <span className="figure text-[13px] font-semibold text-ink">{c.current}</span>
                    {c.delta != null && <Delta value={c.delta} invert={c.invert} />}
                  </div>
                </CardHeader>
                <CardContent className="pl-0 pr-2 pt-1">
                  <TrendArea data={c.series} height={140} color={c.color} format={c.fmt} />
                </CardContent>
              </Card>
            ))}
          </div>

          {/* Submarkets + price bands */}
          <div className="grid gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
            <section>
              <SectionHeader eyebrow="Where the spread lives" title="Submarkets" className="mb-3" />
              <div className="overflow-x-auto rounded-lg border border-stroke bg-card">
                <Table>
                  <TableHeader>
                    <TableRow className="hover:bg-transparent">
                      <TableHead>Submarket</TableHead>
                      <TableHead className="text-right">Median</TableHead>
                      <TableHead className="text-right">YoY</TableHead>
                      <TableHead className="text-right">DOM</TableHead>
                      <TableHead className="text-right">Rent yield</TableHead>
                      <TableHead className="text-right">Avg score</TableHead>
                      <TableHead className="text-right">Active</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {[...report.submarkets]
                      .sort((a, b) => b.avg_score - a.avg_score)
                      .map((sub) => (
                        <TableRow key={sub.zip}>
                          <TableCell>
                            <span className="text-[13px] font-medium text-ink">{sub.name}</span>
                            <span className="figure ml-2 text-[11px] text-ink-3">{sub.zip}</span>
                          </TableCell>
                          <TableCell className="figure text-right text-ink-2">{moneyCompact(sub.median_price)}</TableCell>
                          <TableCell className="text-right">
                            <Delta value={sub.yoy_pct} />
                          </TableCell>
                          <TableCell className="figure text-right text-ink-2">{sub.median_dom}d</TableCell>
                          <TableCell className="figure text-right text-ink-2">{pct(sub.rent_yield_pct)}</TableCell>
                          <TableCell className="text-right">
                            <span
                              className="figure rounded-sm px-1.5 py-0.5 text-xs font-semibold"
                              style={{ color: scoreColor(sub.avg_score), background: "var(--raise)" }}
                            >
                              {Math.round(sub.avg_score)}
                            </span>
                          </TableCell>
                          <TableCell className="figure text-right text-ink-3">{sub.active}</TableCell>
                        </TableRow>
                      ))}
                  </TableBody>
                </Table>
              </div>
            </section>

            <section>
              <SectionHeader eyebrow="Structure by price" title="Price bands" className="mb-3" />
              <div className="space-y-2">
                {report.price_bands.map((band) => {
                  const maxActive = Math.max(...report.price_bands.map((b) => b.active));
                  return (
                    <Tip key={band.band} label={`${band.active} active listings · ${band.median_dom}d median DOM · avg score ${band.avg_score}`}>
                      <div className="cursor-help rounded-lg border border-stroke bg-card px-3.5 py-2.5 transition-colors hover:border-stroke-strong">
                        <div className="mb-1.5 flex items-baseline justify-between">
                          <span className="figure text-xs font-medium text-ink">{band.band}</span>
                          <span className="figure text-[11px] text-ink-3">
                            {band.active} active · {band.median_dom}d DOM
                          </span>
                        </div>
                        <div className="flex items-center gap-2">
                          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-raise">
                            <div
                              className="h-full rounded-full transition-[width] duration-700 ease-[var(--ease-swift)]"
                              style={{ width: `${(band.active / maxActive) * 100}%`, background: "var(--accent)", opacity: 0.75 }}
                            />
                          </div>
                          <span className="figure w-8 text-right text-[11px] font-semibold" style={{ color: scoreColor(band.avg_score) }}>
                            {band.avg_score}
                          </span>
                        </div>
                      </div>
                    </Tip>
                  );
                })}
              </div>
              <Link
                href="/explorer"
                className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-accent-ink hover:underline"
              >
                Open these pockets in the Explorer <ArrowRight className="size-3" />
              </Link>
            </section>
          </div>
        </>
      )}
    </div>
  );
}
