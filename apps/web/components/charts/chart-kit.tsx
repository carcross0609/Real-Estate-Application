"use client";

/** Shared Recharts building blocks — one viz spec across the product (PRD §21):
 * hairline grids, mono tick labels, tabular tooltips, colorblind-aware ramp. */

import * as React from "react";
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from "recharts";
import type { MetricPoint } from "@/lib/types";
import { dateTick } from "@/lib/format";

export function ChartTooltipFrame({
  label,
  rows,
}: {
  label: React.ReactNode;
  rows: Array<{ name: string; value: React.ReactNode; color?: string }>;
}) {
  return (
    <div className="rounded-md border border-stroke-strong bg-overlay px-2.5 py-2 shadow-[var(--shadow-pop)]">
      <div className="eyebrow mb-1">{label}</div>
      <div className="space-y-0.5">
        {rows.map((r) => (
          <div key={r.name} className="flex items-center gap-3 text-xs">
            <span className="flex items-center gap-1.5 text-ink-3">
              {r.color ? <span className="size-1.5 rounded-full" style={{ background: r.color }} /> : null}
              {r.name}
            </span>
            <span className="figure ml-auto font-medium text-ink">{r.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Compact single-series area trend — market pulse, portfolio value, price history. */
export function TrendArea({
  data,
  height = 180,
  color = "var(--viz-1)",
  format,
  ticks = 4,
}: {
  data: MetricPoint[];
  height?: number;
  color?: string;
  format: (v: number) => string;
  ticks?: number;
}) {
  const id = React.useId();
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 6, right: 4, bottom: 0, left: 0 }}>
        <defs>
          <linearGradient id={`g-${id}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.28} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid vertical={false} />
        <XAxis
          dataKey="date"
          tickFormatter={(d: string) => dateTick(d)}
          tickLine={false}
          axisLine={false}
          minTickGap={48}
          dy={4}
        />
        <YAxis
          tickFormatter={format}
          tickLine={false}
          axisLine={false}
          width={52}
          tickCount={ticks}
          domain={["auto", "auto"]}
        />
        <Tooltip
          cursor={{ stroke: "var(--stroke-strong)", strokeDasharray: "3 3" }}
          content={({ active, payload, label }) =>
            active && payload?.length ? (
              <ChartTooltipFrame
                label={dateTick(String(label))}
                rows={[{ name: "Value", value: format(Number(payload[0].value)), color }]}
              />
            ) : null
          }
        />
        <Area
          type="monotone"
          dataKey="value"
          stroke={color}
          strokeWidth={1.75}
          fill={`url(#g-${id})`}
          animationDuration={600}
          animationEasing="ease-out"
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
