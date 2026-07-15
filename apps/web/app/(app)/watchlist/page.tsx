"use client";

/** S17 — Watchlist: watched properties with the event feed and quick metrics. */

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Bookmark, BookmarkX, StickyNote, ArrowRight } from "lucide-react";
import { fetchWatchlist, getPropertySync } from "@/lib/api";
import { useAppStore } from "@/stores/app-store";
import { moneyCompact, pct, ago, dateShort } from "@/lib/format";
import { STRATEGY_SHORT } from "@/lib/domain";
import { PropertyArt } from "@/components/deal/property-art";
import { GradeRing } from "@/components/deal/grade-ring";
import { ScoreExplainer } from "@/components/deal/score-explainer";
import { MovementBadge, RecommendationPill } from "@/components/deal/chips";
import { Stat, SectionHeader, EmptyState } from "@/components/deal/stat";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";

export default function WatchlistPage() {
  const router = useRouter();
  const watchedSet = useAppStore((s) => s.watched);
  const toggleWatch = useAppStore((s) => s.toggleWatch);
  const { data: items, isLoading } = useQuery({ queryKey: ["watchlist"], queryFn: fetchWatchlist });

  /* The store is the source of truth for watch state; server rows add notes/added_at. */
  const visible = React.useMemo(() => {
    if (!items) return [];
    const byId = new Map(items.map((i) => [i.property_id, i]));
    return [...watchedSet]
      .map((id) => {
        const server = byId.get(id);
        if (server) return server;
        const bundle = getPropertySync(id);
        if (!bundle) return null;
        return {
          property_id: id,
          added_at: bundle.data_as_of,
          note: null as string | null,
          events: bundle.events.slice(0, 2),
          card: bundle.card,
        };
      })
      .filter((x): x is NonNullable<typeof x> => x != null)
      .sort((a, b) => b.card.score - a.card.score);
  }, [items, watchedSet]);

  return (
    <div className="mx-auto max-w-[1100px] animate-fade-up px-4 py-5 md:px-6">
      <SectionHeader
        eyebrow={`${visible.length} watched`}
        title="Watchlist"
        className="mb-4"
      >
        <span className="figure hidden text-[11px] text-ink-3 sm:block">
          price cuts, status and score changes alert instantly
        </span>
      </SectionHeader>

      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-32" />
          ))}
        </div>
      ) : visible.length === 0 ? (
        <EmptyState
          icon={Bookmark}
          title="Nothing on watch yet"
          body="Watch a property from any card or detail page and every price cut, status change, and score move lands here — and in your alerts."
          action={
            <Button variant="primary" onClick={() => router.push("/dashboard")}>
              Browse Top Deals
            </Button>
          }
        />
      ) : (
        <div className="space-y-3">
          {visible.map((item) => {
            const card = item.card;
            const bundle = getPropertySync(card.property_id);
            return (
              <article
                key={item.property_id}
                className="lift group grid cursor-pointer grid-cols-[96px_minmax(0,1fr)] gap-4 rounded-lg border border-stroke bg-card p-3 hover:border-stroke-strong sm:grid-cols-[140px_minmax(0,1fr)]"
                onClick={() => router.push(`/property/${card.property_id}`)}
              >
                <PropertyArt seed={card.property_id} className="h-full min-h-24 rounded-md" />
                <div className="min-w-0">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="truncate text-[14px] font-semibold text-ink group-hover:text-accent-ink">
                          {card.line1}
                        </h3>
                        {card.movement.map((m) => (
                          <MovementBadge key={m} kind={m} />
                        ))}
                      </div>
                      <div className="figure text-xs text-ink-3">
                        {card.city} · {moneyCompact(card.list_price)} · {card.dom}d · watched {ago(item.added_at)}
                      </div>
                    </div>
                    <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
                      {bundle ? (
                        <ScoreExplainer score={bundle.score} detailHref={`/property/${card.property_id}`}>
                          <GradeRing score={card.score} grade={card.grade} size="sm" animate={false} />
                        </ScoreExplainer>
                      ) : (
                        <GradeRing score={card.score} grade={card.grade} size="sm" animate={false} />
                      )}
                      <Button
                        size="icon-sm"
                        variant="ghost"
                        aria-label="Remove from watchlist"
                        onClick={() => {
                          toggleWatch(card.property_id);
                          toast("Removed from watchlist", { description: card.line1 });
                        }}
                      >
                        <BookmarkX />
                      </Button>
                    </div>
                  </div>

                  <div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-1.5">
                    <Stat size="sm" label={card.scored_strategy === "flip" ? "Profit" : "CoC"} value={
                      <span className="text-pos">
                        {card.scored_strategy === "flip" ? moneyCompact(card.net_profit) : pct(card.coc_pct)}
                      </span>
                    } />
                    <Stat size="sm" label="ARV" value={moneyCompact(card.arv)} />
                    <Stat size="sm" label="Rehab" value={moneyCompact(card.rehab_estimate)} />
                    <RecommendationPill value={card.recommendation} />
                    <Badge variant="outline">{STRATEGY_SHORT[card.scored_strategy]}</Badge>
                  </div>

                  {item.note && (
                    <div className="mt-2 flex items-start gap-1.5 rounded-md bg-accent-soft/60 px-2.5 py-1.5 text-xs text-ink-2">
                      <StickyNote className="mt-px size-3 shrink-0 text-accent-ink" aria-hidden />
                      {item.note}
                    </div>
                  )}

                  {item.events.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
                      {item.events.slice(0, 2).map((e, i) => (
                        <span key={i} className="figure text-[10.5px] text-ink-3">
                          {dateShort(e.date)} · {e.label}
                          {e.delta ? ` ${e.delta < 0 ? "−" : "+"}${moneyCompact(Math.abs(e.delta))}` : ""}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      )}

      <div className="mt-5 flex justify-end">
        <Link href="/alerts" className="flex items-center gap-1 text-xs font-medium text-accent-ink hover:underline">
          Alert history <ArrowRight className="size-3" />
        </Link>
      </div>
    </div>
  );
}
