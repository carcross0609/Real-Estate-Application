"use client";

/** Sortable deal table — the dashboard's table view and the explorer's list pane.
 * Dense Bloomberg-discipline: tabular mono numerals, aligned decimals, muted labels. */

import * as React from "react";
import { useRouter } from "next/navigation";
import { ArrowUpDown, ArrowUp, ArrowDown, Bookmark } from "lucide-react";
import type { PropertyCard } from "@/lib/types";
import { getPropertySync } from "@/lib/api";
import { useAppStore } from "@/stores/app-store";
import { moneyCompact, pct, num } from "@/lib/format";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ScoreChip } from "./grade-ring";
import { ScoreExplainer } from "./score-explainer";
import { MovementBadge } from "./chips";
import { cn } from "@/lib/utils";

type SortKey =
  | "rank"
  | "price"
  | "arv"
  | "rehab"
  | "upside"
  | "rent"
  | "dom"
  | "sqft"
  | "score";

const COLUMNS: Array<{ key: SortKey | "address" | "watch" | "movement"; label: string; sortable?: boolean; align?: "right" }> = [
  { key: "rank", label: "#", sortable: true },
  { key: "address", label: "Property" },
  { key: "score", label: "Score", sortable: true },
  { key: "price", label: "Price", sortable: true, align: "right" },
  { key: "arv", label: "ARV", sortable: true, align: "right" },
  { key: "rehab", label: "Rehab", sortable: true, align: "right" },
  { key: "upside", label: "Profit / CoC", sortable: true, align: "right" },
  { key: "rent", label: "Rent", sortable: true, align: "right" },
  { key: "sqft", label: "Sqft", sortable: true, align: "right" },
  { key: "dom", label: "DOM", sortable: true, align: "right" },
  { key: "movement", label: "" },
  { key: "watch", label: "" },
];

function sortValue(c: PropertyCard, key: SortKey): number {
  switch (key) {
    case "price": return c.list_price;
    case "arv": return c.arv ?? 0;
    case "rehab": return c.rehab_estimate ?? 0;
    case "upside": return c.scored_strategy === "flip" ? (c.net_profit ?? 0) : (c.coc_pct ?? 0) * 10_000;
    case "rent": return c.rent_monthly ?? 0;
    case "dom": return c.dom;
    case "sqft": return c.sqft;
    case "score":
    case "rank":
    default:
      return c.score;
  }
}

export function DealsTable({
  cards,
  className,
  hoveredId,
  onHover,
}: {
  cards: PropertyCard[];
  className?: string;
  /** Explorer sync: id highlighted from the map pane. */
  hoveredId?: string | null;
  onHover?: (id: string | null) => void;
}) {
  const router = useRouter();
  const watchedSet = useAppStore((s) => s.watched);
  const toggleWatch = useAppStore((s) => s.toggleWatch);
  const [sort, setSort] = React.useState<{ key: SortKey; dir: "asc" | "desc" }>({ key: "rank", dir: "desc" });

  const ranked = React.useMemo(() => {
    const byScore = [...cards].sort((a, b) => b.score - a.score);
    const rankOf = new Map(byScore.map((c, i) => [c.property_id, i + 1]));
    const sorted = [...cards].sort((a, b) => {
      const d = sortValue(a, sort.key) - sortValue(b, sort.key);
      return sort.dir === "asc" ? d : -d;
    });
    return { sorted, rankOf };
  }, [cards, sort]);

  const onSort = (key: SortKey) =>
    setSort((s) => ({ key, dir: s.key === key ? (s.dir === "desc" ? "asc" : "desc") : "desc" }));

  return (
    <div className={cn("overflow-x-auto rounded-lg border border-stroke bg-card", className)}>
      <Table>
        <TableHeader>
          <TableRow className="border-b-0 hover:bg-transparent">
            {COLUMNS.map((col) => (
              <TableHead key={col.key} className={cn(col.align === "right" && "text-right")}>
                {col.sortable ? (
                  <button
                    onClick={() => onSort(col.key as SortKey)}
                    className={cn(
                      "group inline-flex cursor-pointer items-center gap-1 uppercase transition-colors hover:text-ink",
                      col.align === "right" && "flex-row-reverse",
                      sort.key === col.key && "text-ink",
                    )}
                    aria-label={`Sort by ${col.label}`}
                  >
                    {col.label}
                    {sort.key === col.key ? (
                      sort.dir === "desc" ? (
                        <ArrowDown className="size-3 text-accent-ink" />
                      ) : (
                        <ArrowUp className="size-3 text-accent-ink" />
                      )
                    ) : (
                      <ArrowUpDown className="size-3 opacity-0 transition-opacity group-hover:opacity-60" />
                    )}
                  </button>
                ) : (
                  col.label
                )}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {ranked.sorted.map((c) => {
            const bundle = getPropertySync(c.property_id);
            const watched = watchedSet.has(c.property_id);
            return (
              <TableRow
                key={c.property_id}
                data-interactive="true"
                data-state={hoveredId === c.property_id ? "selected" : undefined}
                onClick={() => router.push(`/property/${c.property_id}`)}
                onMouseEnter={onHover ? () => onHover(c.property_id) : undefined}
                onMouseLeave={onHover ? () => onHover(null) : undefined}
              >
                <TableCell className="figure text-xs text-ink-3">
                  {String(ranked.rankOf.get(c.property_id)).padStart(2, "0")}
                </TableCell>
                <TableCell className="max-w-56">
                  <div className="truncate text-[13px] font-medium text-ink">{c.line1}</div>
                  <div className="truncate text-[11px] text-ink-3">
                    {c.city} · {c.beds}bd {c.baths}ba
                  </div>
                </TableCell>
                <TableCell onClick={(e) => e.stopPropagation()}>
                  {bundle ? (
                    <ScoreExplainer score={bundle.score} detailHref={`/property/${c.property_id}`}>
                      <ScoreChip score={c.score} grade={c.grade} />
                    </ScoreExplainer>
                  ) : (
                    <ScoreChip score={c.score} grade={c.grade} />
                  )}
                </TableCell>
                <TableCell className="figure text-right font-medium text-ink">{moneyCompact(c.list_price)}</TableCell>
                <TableCell className="figure text-right text-ink-2">{moneyCompact(c.arv)}</TableCell>
                <TableCell className="figure text-right text-ink-2">{moneyCompact(c.rehab_estimate)}</TableCell>
                <TableCell className="figure text-right font-medium text-pos">
                  {c.scored_strategy === "flip" ? moneyCompact(c.net_profit) : pct(c.coc_pct)}
                </TableCell>
                <TableCell className="figure text-right text-ink-2">
                  {c.rent_monthly ? `${moneyCompact(c.rent_monthly)}/mo` : "—"}
                </TableCell>
                <TableCell className="figure text-right text-ink-2">{num(c.sqft)}</TableCell>
                <TableCell className="figure text-right text-ink-2">{c.dom}d</TableCell>
                <TableCell>
                  <div className="flex gap-1">
                    {c.movement.slice(0, 1).map((m) => (
                      <MovementBadge key={m} kind={m} />
                    ))}
                  </div>
                </TableCell>
                <TableCell onClick={(e) => e.stopPropagation()}>
                  <button
                    onClick={() => toggleWatch(c.property_id)}
                    aria-label={watched ? "Remove from watchlist" : "Add to watchlist"}
                    aria-pressed={watched}
                    className={cn(
                      "flex size-6 items-center justify-center rounded transition-all hover:scale-110",
                      watched ? "text-accent-ink" : "text-ink-faint hover:text-ink-2",
                    )}
                  >
                    <Bookmark className={cn("size-3.5", watched && "fill-current")} />
                  </button>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
