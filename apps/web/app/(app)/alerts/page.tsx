"use client";

/** S19 — Alerts inbox: every notification with read state, kind filters, deep links. */

import * as React from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  Bell,
  BellOff,
  Sparkles,
  TrendingDown,
  Tag,
  Layers,
  Wrench,
  CheckCheck,
  type LucideIcon,
} from "lucide-react";
import { fetchAlerts, fetchMarkets, getPropertySync } from "@/lib/api";
import type { AlertItem } from "@/lib/types";
import { ago } from "@/lib/format";
import { SectionHeader, EmptyState } from "@/components/deal/stat";
import { ScoreChip } from "@/components/deal/grade-ring";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

const KIND_META: Record<AlertItem["kind"], { icon: LucideIcon; label: string }> = {
  new_match: { icon: Sparkles, label: "New match" },
  price_cut: { icon: TrendingDown, label: "Price cut" },
  score_change: { icon: Layers, label: "Score change" },
  status_change: { icon: Tag, label: "Status" },
  digest: { icon: Bell, label: "Digest" },
  system: { icon: Wrench, label: "System" },
};

export default function AlertsPage() {
  const router = useRouter();
  const { data: alerts, isLoading } = useQuery({ queryKey: ["alerts"], queryFn: fetchAlerts });
  const { data: markets } = useQuery({ queryKey: ["markets"], queryFn: fetchMarkets });
  const [filter, setFilter] = React.useState<"all" | "unread" | AlertItem["kind"]>("all");
  const [readIds, setReadIds] = React.useState<Set<string>>(new Set());

  const isRead = (a: AlertItem) => a.read || readIds.has(a.id);
  const list = (alerts ?? []).filter((a) =>
    filter === "all" ? true : filter === "unread" ? !isRead(a) : a.kind === filter,
  );
  const unreadCount = (alerts ?? []).filter((a) => !isRead(a)).length;

  return (
    <div className="mx-auto max-w-[860px] animate-fade-up px-4 py-5 md:px-6">
      <SectionHeader eyebrow={`${unreadCount} unread`} title="Alerts" className="mb-4">
        <Button
          size="sm"
          variant="ghost"
          disabled={unreadCount === 0}
          onClick={() => setReadIds(new Set((alerts ?? []).map((a) => a.id)))}
        >
          <CheckCheck className="size-3.5" /> Mark all read
        </Button>
      </SectionHeader>

      <Tabs value={filter} onValueChange={(v) => setFilter(v as typeof filter)} className="mb-4">
        <TabsList className="flex-wrap">
          <TabsTrigger value="all">All</TabsTrigger>
          <TabsTrigger value="unread">
            Unread
            {unreadCount > 0 && (
              <span className="figure rounded-full bg-accent px-1.5 text-[9.5px] font-bold text-white">{unreadCount}</span>
            )}
          </TabsTrigger>
          <TabsTrigger value="new_match">Matches</TabsTrigger>
          <TabsTrigger value="price_cut">Price cuts</TabsTrigger>
          <TabsTrigger value="score_change">Scores</TabsTrigger>
          <TabsTrigger value="system">System</TabsTrigger>
        </TabsList>
      </Tabs>

      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-20" />
          ))}
        </div>
      ) : list.length === 0 ? (
        <EmptyState
          icon={BellOff}
          title={filter === "unread" ? "All caught up" : "No alerts in this view"}
          body={
            filter === "unread"
              ? "New matches, price cuts, and score changes on watched properties will land here the moment they fire."
              : "Adjust the filter, or create a buy box to start matching new listings within minutes of ingest."
          }
        />
      ) : (
        <div className="space-y-2" role="list">
          {list.map((a) => {
            const meta = KIND_META[a.kind];
            const card = a.property_id ? getPropertySync(a.property_id)?.card : undefined;
            const market = markets?.find((m) => m.market_id === a.market_id);
            const read = isRead(a);
            return (
              <article
                key={a.id}
                role="listitem"
                tabIndex={0}
                onClick={() => {
                  setReadIds((s) => new Set(s).add(a.id));
                  if (a.property_id) router.push(`/property/${a.property_id}`);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && a.property_id) router.push(`/property/${a.property_id}`);
                }}
                className={cn(
                  "lift flex cursor-pointer gap-3 rounded-lg border p-3.5 transition-colors",
                  read
                    ? "border-stroke bg-card opacity-75 hover:opacity-100"
                    : "border-accent/25 bg-card shadow-[inset_2px_0_0_var(--accent)]",
                  "hover:border-stroke-strong",
                )}
              >
                <div
                  className={cn(
                    "flex size-8 shrink-0 items-center justify-center rounded-md",
                    read ? "bg-raise" : "bg-accent-soft",
                  )}
                >
                  <meta.icon className={cn("size-4", read ? "text-ink-3" : "text-accent-ink")} aria-hidden />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
                    <h3 className={cn("truncate text-[13.5px]", read ? "font-medium text-ink-2" : "font-semibold text-ink")}>
                      {a.title}
                    </h3>
                    <span className="figure shrink-0 text-[10.5px] text-ink-3">{ago(a.created_at)}</span>
                  </div>
                  <p className="mt-0.5 text-[12.5px] leading-relaxed text-ink-3">{a.body}</p>
                  <div className="mt-1.5 flex flex-wrap items-center gap-2">
                    {card && <ScoreChip score={card.score} grade={card.grade} />}
                    {a.buy_box && (
                      <span className="figure rounded-full border border-stroke px-2 py-px text-[10px] text-ink-3">
                        {a.buy_box}
                      </span>
                    )}
                    {market && (
                      <span className="figure text-[10px] uppercase tracking-wide text-ink-faint">
                        {market.name}
                      </span>
                    )}
                    {!read && <span className="size-1.5 rounded-full bg-accent" aria-label="Unread" />}
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}

      <p className="mt-5 text-[10.5px] leading-relaxed text-ink-faint">
        Alerts fire within 15 minutes of feed ingest and pause automatically when a feed degrades — no alerts
        on stale data. Delivery channels and rate caps are configured per buy box in Settings.
      </p>
    </div>
  );
}
