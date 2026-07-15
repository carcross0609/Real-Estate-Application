"use client";

/** Comps preview + price/listing history timeline (S12). Comps show similarity rationale
 * and pin/exclude affordances; history is the immutable event log. */

import * as React from "react";
import type { Comp, ListingEvent } from "@/lib/types";
import { money, moneyCompact, dateShort, pctSigned } from "@/lib/format";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tip } from "@/components/ui/tooltip";
import { Badge } from "@/components/ui/badge";
import {
  Tag,
  CalendarPlus,
  TrendingDown,
  RefreshCcw,
  Sparkles,
  CircleDot,
} from "lucide-react";
import { cn } from "@/lib/utils";

export function CompsTable({ comps, subjectSqft }: { comps: Comp[]; subjectSqft: number }) {
  const included = comps.filter((c) => c.status === "included");
  return (
    <div>
      <div className="overflow-x-auto rounded-md border border-stroke">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead>Comp</TableHead>
              <TableHead className="text-right">Sold</TableHead>
              <TableHead className="text-right">Price</TableHead>
              <TableHead className="text-right">$/sqft</TableHead>
              <TableHead className="text-right">Adjusted</TableHead>
              <TableHead className="text-right">Sim</TableHead>
              <TableHead className="text-right">Dist</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {comps.map((c) => (
              <TableRow key={c.comp_id} className={cn(c.status === "excluded" && "opacity-45")}>
                <TableCell className="max-w-52">
                  <Tip
                    label={
                      <span>
                        {c.rationale}
                        {c.adjustments.map((a) => (
                          <span key={a.label} className="block">
                            {a.label}: {a.amount >= 0 ? "+" : "−"}
                            {moneyCompact(Math.abs(a.amount))}
                          </span>
                        ))}
                      </span>
                    }
                  >
                    <span className="block cursor-help">
                      <span className="block truncate text-[13px] font-medium text-ink">{c.line1}</span>
                      <span className="figure text-[11px] text-ink-3">
                        {c.beds}bd {c.baths}ba · {c.sqft.toLocaleString()} sqft
                        {c.status === "excluded" && " · excluded"}
                      </span>
                    </span>
                  </Tip>
                </TableCell>
                <TableCell className="figure text-right text-ink-3">{dateShort(c.sold_date)}</TableCell>
                <TableCell className="figure text-right text-ink-2">{moneyCompact(c.sold_price)}</TableCell>
                <TableCell className="figure text-right text-ink-2">${Math.round(c.price_per_sqft)}</TableCell>
                <TableCell className="figure text-right font-medium text-ink">{moneyCompact(c.adjusted_price)}</TableCell>
                <TableCell className="text-right">
                  <span
                    className="figure rounded-sm px-1.5 py-px text-[11px] font-semibold"
                    style={{
                      background: c.similarity >= 80 ? "var(--grade-a-soft)" : c.similarity >= 65 ? "var(--grade-c-soft)" : "var(--raise)",
                      color: c.similarity >= 80 ? "var(--grade-a)" : c.similarity >= 65 ? "var(--grade-c)" : "var(--ink-3)",
                    }}
                  >
                    {c.similarity}
                  </span>
                </TableCell>
                <TableCell className="figure text-right text-ink-3">{c.distance_mi.toFixed(1)} mi</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <p className="mt-2 text-[11px] text-ink-faint">
        {included.length} of {comps.length} comps drive the estimate
        {included.length < 4 && " — thin comp set lowers confidence"}. Adjusted for sqft ({subjectSqft.toLocaleString()} subject), condition, lot and garage.
      </p>
    </div>
  );
}

const EVENT_ICON: Record<ListingEvent["type"], React.ComponentType<{ className?: string }>> = {
  listed: CalendarPlus,
  price_change: TrendingDown,
  status_change: Tag,
  back_on_market: RefreshCcw,
  score_change: Sparkles,
};

export function HistoryTimeline({ events }: { events: ListingEvent[] }) {
  return (
    <ol className="relative space-y-0" aria-label="Listing history">
      {events.map((e, i) => {
        const Icon = EVENT_ICON[e.type] ?? CircleDot;
        const isCut = e.type === "price_change" && (e.delta ?? 0) < 0;
        return (
          <li key={i} className="relative flex gap-3 pb-4 last:pb-0">
            {i < events.length - 1 && (
              <span className="absolute left-[13px] top-7 h-[calc(100%-20px)] w-px bg-stroke" aria-hidden />
            )}
            <span
              className={cn(
                "z-10 flex size-7 shrink-0 items-center justify-center rounded-full border",
                isCut ? "border-grade-a/30 bg-grade-a-soft" : e.type === "score_change" ? "border-accent/30 bg-accent-soft" : "border-stroke bg-raise",
              )}
            >
              <Icon className={cn("size-3.5", isCut ? "text-grade-a" : e.type === "score_change" ? "text-accent-ink" : "text-ink-3")} />
            </span>
            <div className="min-w-0 flex-1 pt-0.5">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="text-[13px] font-medium text-ink">{e.label}</span>
                {e.price != null && <span className="figure text-[13px] text-ink-2">{money(e.price)}</span>}
                {e.delta != null && e.delta !== 0 && (
                  <Badge variant={e.delta < 0 ? "pos" : "neg"}>
                    {e.delta < 0 ? "−" : "+"}
                    {moneyCompact(Math.abs(e.delta))}
                    {e.price ? ` (${pctSigned((e.delta / (e.price - e.delta)) * 100)})` : ""}
                  </Badge>
                )}
              </div>
              {e.detail && <div className="mt-0.5 text-xs text-ink-3">{e.detail}</div>}
              <div className="figure mt-0.5 text-[10.5px] text-ink-faint">{dateShort(e.date)}</div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
