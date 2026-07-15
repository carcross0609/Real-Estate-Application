"use client";

/** DealCard — the ranked Top-25 card (S10). Photo art, GradeRing (click → decompose),
 * headline economics for the winning strategy, movement badges, watch toggle. */

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Bookmark, BedDouble, Bath, Ruler, Camera } from "lucide-react";
import type { PropertyCard } from "@/lib/types";
import { getPropertySync } from "@/lib/api";
import { useAppStore } from "@/stores/app-store";
import { moneyCompact, moneyRange, pct, num } from "@/lib/format";
import { STRATEGY_SHORT } from "@/lib/domain";
import { PropertyArt } from "./property-art";
import { GradeRing } from "./grade-ring";
import { ScoreExplainer } from "./score-explainer";
import { MovementBadge, RecommendationPill, ConfidenceChip } from "./chips";
import { Stat } from "./stat";
import { Tip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

export function DealCard({ card, rank }: { card: PropertyCard; rank?: number }) {
  const router = useRouter();
  const watched = useAppStore((s) => s.watched.has(card.property_id));
  const toggleWatch = useAppStore((s) => s.toggleWatch);
  const bundle = getPropertySync(card.property_id);
  const href = `/property/${card.property_id}`;
  const isRental = card.scored_strategy !== "flip";

  return (
    <article
      className={cn(
        "lift group relative flex cursor-pointer flex-col overflow-hidden rounded-lg border border-stroke bg-card",
        "hover:border-stroke-strong hover:shadow-[0_12px_32px_-14px_rgba(2,6,16,0.55)]",
      )}
      onClick={() => router.push(href)}
      onKeyDown={(e) => {
        if (e.key === "Enter") router.push(href);
      }}
      tabIndex={0}
      role="link"
      aria-label={`${card.line1}, ${card.city} — score ${Math.round(card.score)} grade ${card.grade}`}
    >
      {/* Art / photo region */}
      <div className="relative">
        <PropertyArt seed={card.property_id} className="aspect-[16/8.5] w-full" />
        {rank != null && (
          <div className="figure absolute left-2.5 top-2.5 rounded-md border border-white/10 bg-black/55 px-1.5 py-0.5 text-[11px] font-semibold text-white/90 backdrop-blur-sm">
            #{String(rank).padStart(2, "0")}
          </div>
        )}
        <div className="absolute right-2.5 top-2.5 flex items-center gap-1.5">
          {card.movement.map((m) => (
            <MovementBadge key={m} kind={m} />
          ))}
        </div>
        <div className="figure absolute bottom-2 left-2.5 flex items-center gap-1 rounded-md bg-black/45 px-1.5 py-0.5 text-[10.5px] text-white/85 backdrop-blur-sm">
          <Camera className="size-3" aria-hidden /> {card.photo_count}
        </div>
        <button
          onClick={(e) => {
            e.stopPropagation();
            toggleWatch(card.property_id);
            toast(watched ? "Removed from watchlist" : "Watching property", {
              description: card.line1,
            });
          }}
          aria-label={watched ? "Remove from watchlist" : "Add to watchlist"}
          aria-pressed={watched}
          className={cn(
            "absolute bottom-2 right-2.5 flex size-7 items-center justify-center rounded-md backdrop-blur-sm",
            "transition-all duration-150 hover:scale-110 active:scale-95",
            watched ? "bg-accent text-white" : "bg-black/45 text-white/80 hover:text-white",
          )}
        >
          <Bookmark className={cn("size-3.5", watched && "fill-current")} />
        </button>
      </div>

      {/* Body */}
      <div className="flex flex-1 flex-col gap-3 p-3.5">
        <div className="flex items-start gap-3">
          {bundle ? (
            <ScoreExplainer score={bundle.score} detailHref={href}>
              <GradeRing score={card.score} grade={card.grade} size="md" />
            </ScoreExplainer>
          ) : (
            <GradeRing score={card.score} grade={card.grade} size="md" />
          )}
          <div className="min-w-0 flex-1">
            <h3 className="truncate text-[13.5px] font-semibold text-ink group-hover:text-accent-ink">
              {card.line1}
            </h3>
            <div className="truncate text-xs text-ink-3">
              {card.city}, {card.state} {card.zip}
            </div>
            <div className="mt-1 flex items-center gap-2.5 text-[11px] text-ink-3">
              <span className="flex items-center gap-1"><BedDouble className="size-3" aria-hidden />{card.beds}</span>
              <span className="flex items-center gap-1"><Bath className="size-3" aria-hidden />{card.baths}</span>
              <span className="figure flex items-center gap-1"><Ruler className="size-3" aria-hidden />{num(card.sqft)}</span>
              <span className="figure">{card.year_built}</span>
            </div>
          </div>
          <div className="text-right">
            <div className="figure text-[15px] font-semibold text-ink">{moneyCompact(card.list_price)}</div>
            <div className="figure text-[11px] text-ink-3">${Math.round(card.price_per_sqft)}/sqft · {card.dom}d</div>
          </div>
        </div>

        {/* Economics row — winning strategy */}
        <div className="grid grid-cols-4 gap-2 rounded-md border border-hairline bg-panel/70 px-3 py-2.5">
          {isRental ? (
            <>
              <Stat size="sm" label="CoC" value={<span className="text-pos">{pct(card.coc_pct)}</span>} hint="Year-1 cash flow over cash invested" />
              <Stat size="sm" label="Rent" value={moneyCompact(card.rent_monthly)} hint={`Estimated band ${moneyRange((card.rent_monthly ?? 0) * 0.93, (card.rent_monthly ?? 0) * 1.07)}/mo`} />
              <Stat size="sm" label="Cap" value={pct(card.cap_rate_pct)} hint="NOI over all-in basis" />
              <Stat size="sm" label="Rehab" value={moneyCompact(card.rehab_estimate)} hint="Vision-modeled midpoint, inspection-gated" />
            </>
          ) : (
            <>
              <Stat size="sm" label="Profit" value={<span className="text-pos">{moneyCompact(card.net_profit)}</span>} hint="Net of purchase, rehab, holding & selling costs" />
              <Stat size="sm" label="Margin" value={pct(card.flip_margin_pct)} hint="Net profit over ARV" />
              <Stat size="sm" label="ARV" value={moneyCompact(card.arv)} hint={`Interval ${moneyRange(card.arv_lo, card.arv_hi)}`} />
              <Stat size="sm" label="Rehab" value={moneyCompact(card.rehab_estimate)} hint="Vision-modeled midpoint, inspection-gated" />
            </>
          )}
        </div>

        <div className="mt-auto flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5">
            <RecommendationPill value={card.recommendation} />
            <Tip label={`Underwritten best as ${STRATEGY_SHORT[card.scored_strategy]}`}>
              <span className="figure rounded-sm border border-stroke px-1.5 py-px text-[10px] font-medium uppercase tracking-wide text-ink-3">
                {STRATEGY_SHORT[card.scored_strategy]}
              </span>
            </Tip>
          </div>
          <ConfidenceChip value={card.confidence_score} />
        </div>
      </div>
    </article>
  );
}

/** Skeleton mirror of DealCard for loading states. */
export function DealCardSkeleton() {
  return (
    <div className="overflow-hidden rounded-lg border border-stroke bg-card">
      <div className="skeleton aspect-[16/8.5] w-full rounded-none" />
      <div className="space-y-3 p-3.5">
        <div className="flex items-center gap-3">
          <div className="skeleton size-11 rounded-full" />
          <div className="flex-1 space-y-1.5">
            <div className="skeleton h-3.5 w-3/4" />
            <div className="skeleton h-3 w-1/2" />
          </div>
        </div>
        <div className="skeleton h-14 w-full" />
        <div className="flex justify-between">
          <div className="skeleton h-4 w-20" />
          <div className="skeleton h-4 w-14" />
        </div>
      </div>
    </div>
  );
}

/** Link-wrapped compact row used in rails (alerts summary, similar deals). */
export function DealRow({ card }: { card: PropertyCard }) {
  return (
    <Link
      href={`/property/${card.property_id}`}
      className="flex items-center gap-2.5 rounded-md px-2 py-1.5 transition-colors hover:bg-raise"
    >
      <GradeRing score={card.score} grade={card.grade} size="xs" animate={false} />
      <div className="min-w-0 flex-1">
        <div className="truncate text-xs font-medium text-ink">{card.line1}</div>
        <div className="figure text-[10.5px] text-ink-3">
          {moneyCompact(card.list_price)} · {card.dom}d
        </div>
      </div>
      <span className="figure text-[11px] font-medium text-pos">
        {card.scored_strategy === "flip" ? moneyCompact(card.net_profit) : pct(card.coc_pct)}
      </span>
    </Link>
  );
}
