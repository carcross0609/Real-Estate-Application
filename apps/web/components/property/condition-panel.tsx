"use client";

/** AI condition analysis + rehab breakdown (S12). Photo-inferred, confidence-labeled,
 * inspection-gated — degraded states are explicit, never blank (§21 #9). */

import * as React from "react";
import type { PropertyCondition } from "@/lib/types";
import { moneyRange, moneyCompact, ago } from "@/lib/format";
import { ConfidenceChip } from "@/components/deal/chips";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tip } from "@/components/ui/tooltip";
import { ScanEye, AlertTriangle, Camera } from "lucide-react";
import { cn } from "@/lib/utils";

function conditionColor(v: number) {
  return v >= 7 ? "var(--grade-a)" : v >= 5 ? "var(--grade-c)" : "var(--grade-f)";
}

export function ConditionPanel({ condition }: { condition: PropertyCondition }) {
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
      {/* Condition read */}
      <div className="space-y-4">
        <div className="flex items-center gap-4 rounded-md border border-hairline bg-panel/60 p-3.5">
          <div className="relative flex size-16 shrink-0 items-center justify-center">
            <svg viewBox="0 0 64 64" className="absolute inset-0 -rotate-90">
              <circle cx="32" cy="32" r="28" fill="none" stroke="var(--raise)" strokeWidth="5" />
              <circle
                cx="32"
                cy="32"
                r="28"
                fill="none"
                stroke={conditionColor(condition.overall_condition)}
                strokeWidth="5"
                strokeLinecap="round"
                strokeDasharray={2 * Math.PI * 28}
                strokeDashoffset={2 * Math.PI * 28 * (1 - condition.overall_condition / 10)}
                className="transition-[stroke-dashoffset] duration-700 ease-[var(--ease-swift)]"
              />
            </svg>
            <span className="figure text-lg font-semibold text-ink">
              {condition.overall_condition.toFixed(1)}
            </span>
          </div>
          <div className="min-w-0">
            <div className="text-sm font-semibold text-ink">{condition.condition_label}</div>
            <div className="mt-0.5 flex items-center gap-1.5 text-xs text-ink-3">
              <Camera className="size-3.5" aria-hidden />
              {condition.analyzed_photos}/{condition.total_photos} photos analyzed
            </div>
            <div className="mt-1.5 flex items-center gap-2">
              <ConfidenceChip
                value={condition.confidence}
                reason="Vision condition read — grounded in listing photos only; unphotographed areas assumed era-typical"
              />
            </div>
          </div>
        </div>

        <div className="space-y-2.5">
          {condition.areas.map((area) => (
            <Tip key={area.area} label={area.note}>
              <div className="group cursor-help">
                <div className="mb-1 flex items-baseline justify-between">
                  <span className="text-xs text-ink-2">{area.area}</span>
                  <span className="figure text-xs font-semibold text-ink">{area.score.toFixed(1)}</span>
                </div>
                <div className="h-1 overflow-hidden rounded-full bg-raise">
                  <div
                    className="h-full rounded-full transition-[width] duration-700 ease-[var(--ease-swift)]"
                    style={{ width: `${area.score * 10}%`, background: conditionColor(area.score), opacity: 0.85 }}
                  />
                </div>
              </div>
            </Tip>
          ))}
        </div>

        {condition.red_flags.length > 0 && (
          <div className="space-y-2">
            {condition.red_flags.map((flag) => (
              <div
                key={flag.label}
                className={cn(
                  "flex gap-2.5 rounded-md border px-3 py-2.5",
                  flag.severity === "high"
                    ? "border-grade-f/30 bg-grade-f-soft"
                    : flag.severity === "medium"
                      ? "border-grade-c/25 bg-grade-c-soft"
                      : "border-stroke bg-panel/60",
                )}
              >
                <AlertTriangle
                  className={cn(
                    "mt-0.5 size-3.5 shrink-0",
                    flag.severity === "high" ? "text-grade-f" : flag.severity === "medium" ? "text-grade-c" : "text-ink-3",
                  )}
                  aria-hidden
                />
                <div className="min-w-0 text-xs leading-relaxed">
                  <span className="font-semibold text-ink">{flag.label}.</span>{" "}
                  <span className="text-ink-2">{flag.note}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Rehab estimate */}
      <div>
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <ScanEye className="size-4 text-accent-ink" aria-hidden />
            <span className="text-[13px] font-semibold text-ink">Rehab estimate</span>
          </div>
          <div className="figure text-sm font-semibold text-ink">
            {moneyRange(condition.rehab_total_lo, condition.rehab_total_hi)}
          </div>
        </div>
        <div className="overflow-x-auto rounded-md border border-stroke">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>Area</TableHead>
                <TableHead>Scope</TableHead>
                <TableHead className="text-right">Low</TableHead>
                <TableHead className="text-right">High</TableHead>
                <TableHead>Conf</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {condition.rehab.map((item) => (
                <TableRow key={item.area}>
                  <TableCell className="font-medium text-ink">{item.area}</TableCell>
                  <TableCell className="max-w-52">
                    <Tip label={item.source_note}>
                      <span className="block cursor-help truncate text-ink-2">{item.scope}</span>
                    </Tip>
                  </TableCell>
                  <TableCell className="figure text-right text-ink-2">{moneyCompact(item.cost_lo)}</TableCell>
                  <TableCell className="figure text-right text-ink-2">{moneyCompact(item.cost_hi)}</TableCell>
                  <TableCell>
                    <span
                      className={cn(
                        "figure rounded-sm px-1.5 py-px text-[10px] font-medium uppercase",
                        item.confidence === "high"
                          ? "bg-raise text-ink-2"
                          : item.confidence === "medium"
                            ? "bg-raise text-ink-3"
                            : "bg-grade-c-soft text-grade-c",
                      )}
                    >
                      {item.confidence}
                    </span>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        <p className="mt-2 text-[11px] leading-relaxed text-ink-faint">
          Modeled from {condition.analyzed_photos} photos against regional unit costs ({condition.model_version},{" "}
          {ago(condition.as_of)}). Unphotographed systems assumed era-typical — verify with inspection before
          committing capital.
        </p>
      </div>
    </div>
  );
}
