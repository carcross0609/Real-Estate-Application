"use client";

/** S12 — Property Detail: the single-property command center. Progressive depth:
 * hero facts → score decomposition → financials → condition → comps → history. */

import * as React from "react";
import { useParams, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  Bookmark,
  Calculator,
  FileText,
  Share2,
  BedDouble,
  Bath,
  Ruler,
  CalendarDays,
  Car,
  Layers,
  Camera,
  MapPin,
  ShieldQuestion,
  Waves,
  GraduationCap,
  Footprints,
  Landmark,
} from "lucide-react";
import { fetchProperty } from "@/lib/api";
import { useAppStore } from "@/stores/app-store";
import { money, moneyCompact, num, ago, sqft as fmtSqft } from "@/lib/format";
import { PROPERTY_TYPE_LABEL, STATUS_LABEL } from "@/lib/domain";
import { PropertyArt } from "@/components/deal/property-art";
import { MovementBadge } from "@/components/deal/chips";
import { Stat, SectionHeader, EmptyState } from "@/components/deal/stat";
import { ScorePanel } from "@/components/property/score-panel";
import { FinancialTabs } from "@/components/property/financial-tabs";
import { ConditionPanel } from "@/components/property/condition-panel";
import { CompsTable, HistoryTimeline } from "@/components/property/comps-and-history";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tip } from "@/components/ui/tooltip";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

function CompsSection({ bundle }: { bundle: NonNullable<Awaited<ReturnType<typeof fetchProperty>>> }) {
  const [kind, setKind] = React.useState<"sale" | "rental">("sale");
  return (
    <section>
      <SectionHeader eyebrow="Valuation basis" title={kind === "sale" ? "Comparable sales" : "Rental comps"} className="mb-3">
        <Tabs value={kind} onValueChange={(v) => setKind(v as "sale" | "rental")}>
          <TabsList className="h-7">
            <TabsTrigger value="sale" className="text-[11px]">Sales</TabsTrigger>
            <TabsTrigger value="rental" className="text-[11px]">Rentals</TabsTrigger>
          </TabsList>
        </Tabs>
      </SectionHeader>
      <CompsTable
        comps={kind === "sale" ? bundle.comps_sale : bundle.comps_rental}
        subjectSqft={bundle.card.sqft}
      />
    </section>
  );
}

function DetailSkeleton() {
  return (
    <div className="mx-auto max-w-[1440px] space-y-4 px-4 py-5 md:px-6">
      <Skeleton className="h-8 w-72" />
      <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
        <Skeleton className="aspect-[16/9]" />
        <Skeleton className="h-full min-h-72" />
      </div>
      <Skeleton className="h-64" />
    </div>
  );
}

export default function PropertyDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const watched = useAppStore((s) => s.watched.has(params.id));
  const toggleWatch = useAppStore((s) => s.toggleWatch);

  const { data: bundle, isLoading, isError } = useQuery({
    queryKey: ["property", params.id],
    queryFn: () => fetchProperty(params.id),
  });

  if (isLoading) return <DetailSkeleton />;
  if (isError || !bundle)
    return (
      <div className="mx-auto max-w-lg px-4 py-16">
        <EmptyState
          icon={ShieldQuestion}
          title="Property not found"
          body="It may have left the feed or the link is stale. Deals move fast — the Top 25 always has current inventory."
          action={
            <Button variant="primary" onClick={() => router.push("/dashboard")}>
              Back to Top Deals
            </Button>
          }
        />
      </div>
    );

  const { card } = bundle;

  return (
    <div className="mx-auto max-w-[1440px] animate-fade-up px-4 py-5 md:px-6">
      {/* Header */}
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <button
            onClick={() => router.back()}
            className="mb-1.5 flex cursor-pointer items-center gap-1 text-xs font-medium text-ink-3 transition-colors hover:text-ink-2"
          >
            <ArrowLeft className="size-3.5" /> Back
          </button>
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="text-xl font-semibold tracking-tight text-ink">{card.line1}</h1>
            <Badge variant={card.status === "active" ? "accent" : "warn"}>{STATUS_LABEL[card.status]}</Badge>
            {card.movement.map((m) => (
              <MovementBadge key={m} kind={m} />
            ))}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-ink-3">
            <span className="flex items-center gap-1">
              <MapPin className="size-3.5" aria-hidden />
              {card.city}, {card.state} {card.zip}
            </span>
            <span>{PROPERTY_TYPE_LABEL[card.property_type]}</span>
            <span className="figure">
              {card.dom} DOM · listed {card.list_date}
            </span>
            <span className="figure text-ink-faint">data as of {ago(bundle.data_as_of)}</span>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant={watched ? "primary" : "secondary"}
            onClick={() => {
              toggleWatch(card.property_id);
              toast(watched ? "Removed from watchlist" : "Watching property", { description: card.line1 });
            }}
            aria-pressed={watched}
          >
            <Bookmark className={cn("size-4", watched && "fill-current")} />
            {watched ? "Watching" : "Watch"}
          </Button>
          <Button variant="secondary" onClick={() => router.push(`/analyzer/${card.property_id}`)}>
            <Calculator className="size-4" /> Analyzer
          </Button>
          <Button variant="primary" onClick={() => router.push(`/property/${card.property_id}/report`)}>
            <FileText className="size-4" /> AI Report
          </Button>
          <Tip label="Create a revocable share link (view-only, no login required)">
            <Button
              variant="ghost"
              size="icon"
              aria-label="Share"
              onClick={() =>
                toast("Share link copied", { description: "View-only link — revoke any time from Settings." })
              }
            >
              <Share2 />
            </Button>
          </Tip>
        </div>
      </div>

      {/* Hero: gallery + score */}
      <div className="mb-5 grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="space-y-3">
          <div className="relative overflow-hidden rounded-lg border border-stroke">
            <PropertyArt seed={card.property_id} className="aspect-[16/9] w-full" />
            <div className="figure absolute bottom-2.5 left-2.5 flex items-center gap-1.5 rounded-md bg-black/50 px-2 py-1 text-[11px] text-white/90 backdrop-blur-sm">
              <Camera className="size-3.5" aria-hidden />
              {card.photo_count} photos · MLS display licensing pending for this market
            </div>
          </div>

          {/* Key facts strip */}
          <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
            {[
              { icon: BedDouble, label: "Beds", value: String(card.beds) },
              { icon: Bath, label: "Baths", value: String(card.baths) },
              { icon: Ruler, label: "Sqft", value: num(card.sqft) },
              { icon: CalendarDays, label: "Built", value: String(card.year_built) },
              { icon: Layers, label: "Stories", value: String(bundle.stories) },
              { icon: Car, label: "Garage", value: bundle.garage_spaces ? String(bundle.garage_spaces) : "—" },
            ].map((f) => (
              <div key={f.label} className="flex items-center gap-2 rounded-md border border-stroke bg-card px-2.5 py-2">
                <f.icon className="size-3.5 shrink-0 text-ink-3" aria-hidden />
                <div className="min-w-0">
                  <div className="eyebrow text-[9.5px]">{f.label}</div>
                  <div className="figure truncate text-[13px] font-semibold text-ink">{f.value}</div>
                </div>
              </div>
            ))}
          </div>

          {/* Price + description */}
          <Card>
            <CardContent className="space-y-3">
              <div className="flex flex-wrap items-end justify-between gap-3">
                <div>
                  <div className="eyebrow">List price</div>
                  <div className="figure text-2xl font-semibold tracking-tight text-ink">{money(card.list_price)}</div>
                  <div className="figure text-xs text-ink-3">${Math.round(card.price_per_sqft)}/sqft</div>
                </div>
                <div className="flex gap-5">
                  <Stat label="Taxes" value={`${moneyCompact(bundle.taxes_annual)}/yr`} align="right" />
                  <Stat label="HOA" value={bundle.hoa_monthly ? `${moneyCompact(bundle.hoa_monthly)}/mo` : "None"} align="right" />
                  <Stat label="Lot" value={bundle.lot_sqft ? fmtSqft(bundle.lot_sqft) : "—"} align="right" />
                </div>
              </div>
              <p className="text-[13px] leading-relaxed text-ink-2">{bundle.description}</p>
            </CardContent>
          </Card>

          {/* Location context */}
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Tip label="District percentile from state assessment data">
              <div className="flex items-center gap-2.5 rounded-md border border-stroke bg-card px-3 py-2.5">
                <GraduationCap className="size-4 shrink-0 text-accent-ink" aria-hidden />
                <div>
                  <div className="eyebrow text-[9.5px]">Schools</div>
                  <div className="figure text-[13px] font-semibold text-ink">{Math.round(bundle.school_percentile)}th pctile</div>
                </div>
              </div>
            </Tip>
            <Tip label="Composite incident index, lower is safer (county records)">
              <div className="flex items-center gap-2.5 rounded-md border border-stroke bg-card px-3 py-2.5">
                <Landmark className="size-4 shrink-0 text-accent-ink" aria-hidden />
                <div>
                  <div className="eyebrow text-[9.5px]">Crime idx</div>
                  <div className="figure text-[13px] font-semibold text-ink">{Math.round(bundle.crime_index)}/100</div>
                </div>
              </div>
            </Tip>
            <Tip label="Walk Score-style access measure">
              <div className="flex items-center gap-2.5 rounded-md border border-stroke bg-card px-3 py-2.5">
                <Footprints className="size-4 shrink-0 text-accent-ink" aria-hidden />
                <div>
                  <div className="eyebrow text-[9.5px]">Walkability</div>
                  <div className="figure text-[13px] font-semibold text-ink">{Math.round(bundle.walkability)}/100</div>
                </div>
              </div>
            </Tip>
            <Tip
              label={
                bundle.flood_zone === "AE"
                  ? "FEMA special flood hazard area — insurance required and priced into the expense model"
                  : "Outside the special flood hazard area"
              }
            >
              <div
                className={cn(
                  "flex items-center gap-2.5 rounded-md border px-3 py-2.5",
                  bundle.flood_zone === "AE" ? "border-grade-c/30 bg-grade-c-soft" : "border-stroke bg-card",
                )}
              >
                <Waves className={cn("size-4 shrink-0", bundle.flood_zone === "AE" ? "text-grade-c" : "text-accent-ink")} aria-hidden />
                <div>
                  <div className="eyebrow text-[9.5px]">Flood</div>
                  <div className="figure text-[13px] font-semibold text-ink">Zone {bundle.flood_zone}</div>
                </div>
              </div>
            </Tip>
          </div>
        </div>

        <ScorePanel score={bundle.score} />
      </div>

      {/* Financials */}
      <section className="mb-5">
        <SectionHeader eyebrow="Deterministic engine" title="Financial summary" className="mb-3" />
        <Card>
          <CardContent>
            <FinancialTabs bundle={bundle} />
          </CardContent>
        </Card>
      </section>

      {/* Condition */}
      <section className="mb-5">
        <SectionHeader eyebrow="AI vision analysis" title="Condition & rehab" className="mb-3" />
        <Card>
          <CardContent>
            <ConditionPanel condition={bundle.condition} />
          </CardContent>
        </Card>
      </section>

      {/* Comps + history */}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <CompsSection bundle={bundle} />
        <section>
          <SectionHeader eyebrow="Immutable event log" title="Price & listing history" className="mb-3" />
          <Card>
            <CardContent>
              <HistoryTimeline events={bundle.events} />
            </CardContent>
          </Card>
        </section>
      </div>

      <p className="mt-6 text-[10.5px] leading-relaxed text-ink-faint">
        All values are estimates with stated confidence intervals, produced by DealLens&apos;s deterministic
        calculation engine from licensed listing data and AI-assisted inputs. Not an appraisal, offer, or
        investment advice. Verify independently before transacting.
      </p>
    </div>
  );
}
