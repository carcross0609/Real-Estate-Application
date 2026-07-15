"use client";

/** S11 — Market Explorer. Split map + table, score-colored pins, full filter panel,
 * saved views; hover states stay synchronized bidirectionally (§21 #7). */

import * as React from "react";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  SlidersHorizontal,
  ChevronDown,
  X,
  Rows3,
  Map as MapIcon,
  Columns2,
  Bookmark,
} from "lucide-react";
import { searchProperties, fetchMarkets, type SearchFilters } from "@/lib/api";
import { useAppStore } from "@/stores/app-store";
import { PROPERTY_TYPE_LABEL, type PropertyType } from "@/lib/domain";
import { moneyCompact } from "@/lib/format";
import { DealsTable } from "@/components/deal/deals-table";
import { Button } from "@/components/ui/button";
import { UnitInput } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Checkbox } from "@/components/ui/checkbox";
import { Slider } from "@/components/ui/slider";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/deal/stat";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const DealMap = dynamic(() => import("@/components/map/deal-map").then((m) => m.DealMap), {
  ssr: false,
  loading: () => <div className="skeleton size-full rounded-lg" />,
});

type Pane = "split" | "table" | "map";

const SAVED_VIEWS: Array<{ name: string; filters: SearchFilters; sort?: string }> = [
  { name: "Sub-$300k flips", filters: { price_max: 300_000, score_min: 60 } },
  { name: "Cash-flow 3+bd", filters: { beds_min: 3, score_min: 55 }, sort: "coc" },
  { name: "A-grade only", filters: { grades: ["A"] } },
];

function FilterChip({
  label,
  active,
  children,
  onClear,
}: {
  label: string;
  active?: string | null;
  children: React.ReactNode;
  onClear?: () => void;
}) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          className={cn(
            "flex h-7.5 cursor-pointer items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium transition-colors",
            active
              ? "border-accent/40 bg-accent-soft text-accent-ink"
              : "border-stroke bg-card text-ink-2 hover:border-stroke-strong hover:text-ink",
          )}
        >
          {label}
          {active ? <span className="figure">{active}</span> : null}
          {active && onClear ? (
            <X
              className="size-3 hover:text-ink"
              onClick={(e) => {
                e.stopPropagation();
                onClear();
              }}
              aria-label={`Clear ${label} filter`}
            />
          ) : (
            <ChevronDown className="size-3 opacity-60" />
          )}
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-64">
        {children}
      </PopoverContent>
    </Popover>
  );
}

export default function ExplorerPage() {
  const router = useRouter();
  const marketId = useAppStore((s) => s.marketId);
  const strategy = useAppStore((s) => s.strategy);
  const [pane, setPane] = React.useState<Pane>("split");
  const [filters, setFilters] = React.useState<SearchFilters>({});
  const [sortField, setSortField] = React.useState("score");
  const [hoveredId, setHoveredId] = React.useState<string | null>(null);
  const [activeView, setActiveView] = React.useState<string | null>(null);

  const { data: markets } = useQuery({ queryKey: ["markets"], queryFn: fetchMarkets });
  const market = markets?.find((m) => m.market_id === marketId);

  const { data: cards, isLoading } = useQuery({
    queryKey: ["search", marketId, strategy, filters, sortField],
    queryFn: () =>
      searchProperties({
        market_id: marketId,
        strategy,
        filters,
        sort: { field: sortField, direction: "desc" },
        limit: 100,
      }),
  });

  const set = (patch: Partial<SearchFilters>) => {
    setActiveView(null);
    setFilters((f) => ({ ...f, ...patch }));
  };
  const activeCount = Object.values(filters).filter((v) => v != null && (!Array.isArray(v) || v.length > 0)).length;

  const applyView = (v: (typeof SAVED_VIEWS)[number]) => {
    setFilters(v.filters);
    if (v.sort) setSortField(v.sort);
    setActiveView(v.name);
  };

  const typeToggle = (t: PropertyType) => {
    const cur = filters.property_types ?? [];
    set({ property_types: cur.includes(t) ? cur.filter((x) => x !== t) : [...cur, t] });
  };

  return (
    <div className="flex h-[calc(100svh-56px)] animate-fade-in flex-col">
      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-2 border-b border-stroke bg-panel/60 px-4 py-2.5 md:px-6">
        <SlidersHorizontal className="hidden size-4 text-ink-3 sm:block" aria-hidden />

        <FilterChip
          label="Price"
          active={
            filters.price_min || filters.price_max
              ? `${filters.price_min ? moneyCompact(filters.price_min) : "0"}–${filters.price_max ? moneyCompact(filters.price_max) : "any"}`
              : null
          }
          onClear={() => set({ price_min: undefined, price_max: undefined })}
        >
          <div className="space-y-2.5">
            <div className="eyebrow">List price</div>
            <div className="flex items-center gap-2">
              <UnitInput
                prefix="$"
                placeholder="Min"
                inputMode="numeric"
                defaultValue={filters.price_min ?? ""}
                onBlur={(e) => set({ price_min: e.target.value ? Number(e.target.value) : undefined })}
              />
              <span className="text-ink-3">–</span>
              <UnitInput
                prefix="$"
                placeholder="Max"
                inputMode="numeric"
                defaultValue={filters.price_max ?? ""}
                onBlur={(e) => set({ price_max: e.target.value ? Number(e.target.value) : undefined })}
              />
            </div>
          </div>
        </FilterChip>

        <FilterChip
          label="Beds / Baths"
          active={filters.beds_min || filters.baths_min ? `${filters.beds_min ?? 0}+bd ${filters.baths_min ?? 0}+ba` : null}
          onClear={() => set({ beds_min: undefined, baths_min: undefined })}
        >
          <div className="space-y-3">
            <div>
              <div className="eyebrow mb-1.5">Beds (min)</div>
              <div className="flex gap-1">
                {[0, 2, 3, 4, 5].map((n) => (
                  <Button
                    key={n}
                    size="xs"
                    variant={filters.beds_min === (n || undefined) ? "primary" : "outline"}
                    onClick={() => set({ beds_min: n || undefined })}
                  >
                    {n === 0 ? "Any" : `${n}+`}
                  </Button>
                ))}
              </div>
            </div>
            <div>
              <div className="eyebrow mb-1.5">Baths (min)</div>
              <div className="flex gap-1">
                {[0, 1, 2, 3].map((n) => (
                  <Button
                    key={n}
                    size="xs"
                    variant={filters.baths_min === (n || undefined) ? "primary" : "outline"}
                    onClick={() => set({ baths_min: n || undefined })}
                  >
                    {n === 0 ? "Any" : `${n}+`}
                  </Button>
                ))}
              </div>
            </div>
          </div>
        </FilterChip>

        <FilterChip
          label="Type"
          active={filters.property_types?.length ? `${filters.property_types.length}` : null}
          onClear={() => set({ property_types: undefined })}
        >
          <div className="space-y-1.5">
            <div className="eyebrow mb-1">Property type</div>
            {(["sfr", "townhome", "condo", "mf_2_4"] as PropertyType[]).map((t) => (
              <label key={t} className="flex cursor-pointer items-center gap-2 rounded-md px-1 py-1 text-[13px] text-ink-2 hover:text-ink">
                <Checkbox
                  checked={filters.property_types?.includes(t) ?? false}
                  onCheckedChange={() => typeToggle(t)}
                />
                {PROPERTY_TYPE_LABEL[t]}
              </label>
            ))}
          </div>
        </FilterChip>

        <FilterChip
          label="Score"
          active={filters.score_min ? `${filters.score_min}+` : null}
          onClear={() => set({ score_min: undefined })}
        >
          <div className="space-y-3">
            <div className="flex items-baseline justify-between">
              <span className="eyebrow">Minimum score</span>
              <span className="figure text-sm font-semibold text-ink">{filters.score_min ?? 0}</span>
            </div>
            <Slider
              value={[filters.score_min ?? 0]}
              min={0}
              max={95}
              step={5}
              onValueChange={([v]) => set({ score_min: v || undefined })}
              aria-label="Minimum score"
            />
            <div className="flex gap-1">
              {["A", "B", "C"].map((g) => (
                <Button
                  key={g}
                  size="xs"
                  variant={filters.grades?.includes(g) ? "primary" : "outline"}
                  onClick={() => {
                    const cur = filters.grades ?? [];
                    set({ grades: cur.includes(g) ? cur.filter((x) => x !== g) : [...cur, g] });
                  }}
                >
                  {g} grades
                </Button>
              ))}
            </div>
          </div>
        </FilterChip>

        <FilterChip
          label="More"
          active={filters.dom_max || filters.year_built_min ? "·" : null}
          onClear={() => set({ dom_max: undefined, year_built_min: undefined, sqft_min: undefined })}
        >
          <div className="space-y-2.5">
            <div>
              <div className="eyebrow mb-1">Max days on market</div>
              <UnitInput
                suffix="days"
                placeholder="Any"
                inputMode="numeric"
                defaultValue={filters.dom_max ?? ""}
                onBlur={(e) => set({ dom_max: e.target.value ? Number(e.target.value) : undefined })}
              />
            </div>
            <div>
              <div className="eyebrow mb-1">Year built (min)</div>
              <UnitInput
                placeholder="Any"
                inputMode="numeric"
                defaultValue={filters.year_built_min ?? ""}
                onBlur={(e) => set({ year_built_min: e.target.value ? Number(e.target.value) : undefined })}
              />
            </div>
            <div>
              <div className="eyebrow mb-1">Min sqft</div>
              <UnitInput
                suffix="sqft"
                placeholder="Any"
                inputMode="numeric"
                defaultValue={filters.sqft_min ?? ""}
                onBlur={(e) => set({ sqft_min: e.target.value ? Number(e.target.value) : undefined })}
              />
            </div>
          </div>
        </FilterChip>

        {activeCount > 0 && (
          <Button size="sm" variant="ghost" onClick={() => { setFilters({}); setActiveView(null); }}>
            <X className="size-3.5" /> Reset ({activeCount})
          </Button>
        )}

        <div className="mx-1 hidden h-5 w-px bg-stroke lg:block" />

        {/* Saved views */}
        <div className="hidden items-center gap-1.5 lg:flex" role="group" aria-label="Saved views">
          <Bookmark className="size-3.5 text-ink-3" aria-hidden />
          {SAVED_VIEWS.map((v) => (
            <button
              key={v.name}
              onClick={() => applyView(v)}
              className={cn(
                "cursor-pointer rounded-full border px-2.5 py-1 font-mono text-[10.5px] font-medium tracking-tight transition-colors",
                activeView === v.name
                  ? "border-accent/40 bg-accent-soft text-accent-ink"
                  : "border-stroke text-ink-3 hover:border-stroke-strong hover:text-ink-2",
              )}
            >
              {v.name}
            </button>
          ))}
        </div>

        <div className="flex-1" />

        <span className="figure hidden text-xs text-ink-3 sm:block" role="status">
          {isLoading ? "searching…" : `${cards?.length ?? 0} match${(cards?.length ?? 0) === 1 ? "" : "es"}`}
        </span>

        <Select value={sortField} onValueChange={setSortField}>
          <SelectTrigger className="w-36" aria-label="Sort by">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="score">Sort: Score</SelectItem>
            <SelectItem value="profit">Sort: Profit</SelectItem>
            <SelectItem value="coc">Sort: CoC</SelectItem>
            <SelectItem value="price">Sort: Price</SelectItem>
            <SelectItem value="newest">Sort: Newest</SelectItem>
            <SelectItem value="dom">Sort: DOM</SelectItem>
          </SelectContent>
        </Select>

        {/* Pane toggle */}
        <div className="hidden items-center rounded-md border border-stroke bg-panel p-0.5 md:flex" role="group" aria-label="Layout">
          {(
            [
              { key: "table", icon: Rows3, label: "Table only" },
              { key: "split", icon: Columns2, label: "Split view" },
              { key: "map", icon: MapIcon, label: "Map only" },
            ] as const
          ).map((p) => (
            <button
              key={p.key}
              onClick={() => setPane(p.key)}
              aria-label={p.label}
              aria-pressed={pane === p.key}
              className={cn(
                "flex size-6.5 cursor-pointer items-center justify-center rounded-[5px] transition-colors",
                pane === p.key ? "bg-raise text-ink shadow-[inset_0_0_0_1px_var(--stroke-strong)]" : "text-ink-3 hover:text-ink-2",
              )}
            >
              <p.icon className="size-3.5" />
            </button>
          ))}
        </div>
      </div>

      {/* Panes */}
      <div className="relative flex min-h-0 flex-1">
        {pane !== "map" && (
          <div className={cn("min-w-0 overflow-y-auto p-3 md:p-4", pane === "split" ? "hidden flex-[11] md:block" : "flex-1")}>
            {isLoading ? (
              <div className="space-y-2">
                {Array.from({ length: 10 }).map((_, i) => (
                  <Skeleton key={i} className="h-12 w-full" />
                ))}
              </div>
            ) : cards && cards.length > 0 ? (
              <DealsTable cards={cards} hoveredId={hoveredId} onHover={setHoveredId} />
            ) : (
              <EmptyState
                icon={SlidersHorizontal}
                title="No properties match these filters"
                body={`${market?.name ?? "This market"} has active inventory, but nothing clears every constraint you set. Loosen one filter — score and price are usually the binding ones.`}
                action={
                  <Button variant="secondary" size="sm" onClick={() => setFilters({})}>
                    Reset filters
                  </Button>
                }
              />
            )}
          </div>
        )}
        {pane !== "table" && market && (
          <div className={cn("relative p-3 md:p-4", pane === "split" ? "flex-[9] pl-0 max-md:pl-3" : "flex-1")}>
            <DealMap
              cards={cards ?? []}
              center={market.center}
              hoveredId={hoveredId}
              onHover={setHoveredId}
              onSelect={(id) => router.push(`/property/${id}`)}
              className="relative size-full"
            />
          </div>
        )}
      </div>
    </div>
  );
}
