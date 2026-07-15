"use client";

/** S14 — AI Investment Report: web-native, every number traceable to its source panel.
 * AI writes the narrative; the deterministic engine owns every dollar figure. */

import * as React from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  Download,
  Share2,
  RefreshCcw,
  BadgeCheck,
  FileText,
  Sparkles,
} from "lucide-react";
import { fetchProperty, fetchReport } from "@/lib/api";
import { money, moneyCompact, moneyRange, pct, ago } from "@/lib/format";
import { STRATEGY_LABEL } from "@/lib/domain";
import { GradeRing } from "@/components/deal/grade-ring";
import { RecommendationPill, ConfidenceChip } from "@/components/deal/chips";
import { Stat } from "@/components/deal/stat";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import { toast } from "sonner";

export default function ReportPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { data: bundle } = useQuery({
    queryKey: ["property", params.id],
    queryFn: () => fetchProperty(params.id),
  });
  const { data: report, isLoading } = useQuery({
    queryKey: ["report", params.id],
    queryFn: () => fetchReport(params.id),
  });

  if (isLoading || !report || !bundle) {
    return (
      <div className="mx-auto max-w-3xl space-y-4 px-4 py-8">
        <div className="flex items-center gap-2 text-[13px] text-ink-3">
          <Sparkles className="size-4 animate-pulse-dot text-accent-ink" />
          Generating report — narrative drafts while the engine locks the numbers…
        </div>
        <Skeleton className="h-40" />
        <Skeleton className="h-24" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  const { card, score } = bundle;
  const win = bundle.analyses.find((a) => a.strategy === score.winning_strategy) ?? bundle.analyses[0];

  return (
    <div className="mx-auto max-w-[1100px] animate-fade-up px-4 py-6 md:px-6">
      {/* Toolbar */}
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <button
          onClick={() => router.push(`/property/${card.property_id}`)}
          className="flex cursor-pointer items-center gap-1 text-xs font-medium text-ink-3 transition-colors hover:text-ink-2"
        >
          <ArrowLeft className="size-3.5" /> Property detail
        </button>
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="sm" onClick={() => toast("Export queued", { description: "PDF renders server-side — ready in ~15s, link lands in your alerts." })}>
            <Download className="size-3.5" /> PDF
          </Button>
          <Button variant="secondary" size="sm" onClick={() => toast("Share link copied", { description: "View-only, revocable. Recipients don't need an account." })}>
            <Share2 className="size-3.5" /> Share
          </Button>
          <Button variant="ghost" size="sm" onClick={() => toast("Regeneration queued", { description: "A fresh narrative over current engine outputs." })}>
            <RefreshCcw className="size-3.5" /> Regenerate
          </Button>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_264px]">
        {/* Document */}
        <article className="min-w-0">
          {/* Masthead */}
          <header className="mb-6 rounded-xl border border-stroke bg-card p-5 md:p-6">
            <div className="mb-4 flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <FileText className="size-4 text-accent-ink" aria-hidden />
                <span className="eyebrow">Investment report</span>
              </div>
              <div className="flex items-center gap-2">
                {report.fact_check_passed && (
                  <span className="flex items-center gap-1 rounded-sm border border-stroke bg-panel px-1.5 py-px font-mono text-[10.5px] text-ink-2">
                    <BadgeCheck className="size-3 text-pos" aria-hidden /> fact-check passed
                  </span>
                )}
                <span className="figure text-[10.5px] text-ink-faint">{report.model_version}</span>
              </div>
            </div>

            <div className="flex flex-wrap items-start gap-5">
              <GradeRing score={score.overall_score} grade={score.grade} size="lg" />
              <div className="min-w-0 flex-1">
                <h1 className="text-[22px] font-semibold leading-snug tracking-tight text-ink">
                  {card.line1}, {card.city} {card.state}
                </h1>
                <div className="figure mt-0.5 text-[13px] text-ink-3">
                  {card.beds}bd {card.baths}ba · {card.sqft.toLocaleString()} sqft · listed {money(card.list_price)} ·{" "}
                  {card.dom} DOM
                </div>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <RecommendationPill value={report.verdict_recommendation} />
                  <span className="text-[13px] text-ink-2">
                    as {STRATEGY_LABEL[score.winning_strategy]}
                  </span>
                  <ConfidenceChip value={score.confidence_score} />
                </div>
              </div>
            </div>

            <p className="mt-4 border-l-2 border-accent pl-3.5 text-[15px] font-medium leading-relaxed text-ink">
              {report.verdict}
            </p>
            <div className="figure mt-3 text-[10.5px] text-ink-faint">
              Generated {ago(report.generated_at)} · engine numbers as of {ago(bundle.data_as_of)} · report ID{" "}
              {report.report_id}
            </div>
          </header>

          {/* Sections */}
          <div className="space-y-6">
            {report.sections.map((section, idx) => (
              <section key={section.key} id={section.key} className="scroll-mt-20">
                <div className="mb-2.5 flex flex-wrap items-center justify-between gap-2">
                  <h2 className="flex items-baseline gap-2.5 text-[16px] font-semibold tracking-tight text-ink">
                    <span className="figure text-[11px] text-ink-faint">
                      {String(idx + 1).padStart(2, "0")}
                    </span>
                    {section.title}
                  </h2>
                  <ConfidenceChip
                    value={section.confidence}
                    reason="Section-level confidence from source-data coverage and estimator agreement"
                  />
                </div>
                <div className="space-y-3 text-[14px] leading-[1.75] text-ink-2">
                  {section.paragraphs.map((p, i) => (
                    <p key={i}>{p}</p>
                  ))}
                </div>
                {section.bullets && section.bullets.length > 0 && (
                  <ul className="mt-3 space-y-1.5">
                    {section.bullets.map((b, i) => (
                      <li key={i} className="flex gap-2.5 text-[13.5px] leading-relaxed text-ink-2">
                        <span className="mt-[9px] size-1 shrink-0 rounded-full bg-accent" aria-hidden />
                        {b}
                      </li>
                    ))}
                  </ul>
                )}
                {idx < report.sections.length - 1 && <Separator className="mt-6" />}
              </section>
            ))}
          </div>

          <footer className="mt-8 rounded-lg border border-stroke bg-panel/60 p-4 text-[11px] leading-relaxed text-ink-3">
            This report was generated by DealLens from licensed listing data, public records, and AI-assisted
            photo analysis. All dollar figures come from the deterministic calculation engine — the language
            model writes narrative only and cannot alter numbers. Estimates carry stated confidence and are not
            an appraisal, offer, or investment advice.
          </footer>
        </article>

        {/* Rail: numbers at a glance + TOC */}
        <aside className="space-y-4 lg:sticky lg:top-[72px] lg:self-start">
          <Card>
            <CardContent className="space-y-3.5">
              <div className="eyebrow">Numbers at a glance</div>
              <Stat label="List price" value={money(card.list_price)} />
              <Stat label="ARV" value={moneyCompact(win.arv)} sub={moneyRange(win.arv_lo, win.arv_hi)} />
              <Stat label="Rehab" value={moneyRange(bundle.condition.rehab_total_lo, bundle.condition.rehab_total_hi)} />
              {score.winning_strategy === "flip" ? (
                <>
                  <Stat label="Net profit" value={<span className="text-pos">{moneyCompact(win.net_profit)}</span>} />
                  <Stat label="Margin" value={pct(win.flip_margin_pct)} />
                </>
              ) : (
                <>
                  <Stat label="Rent" value={`${moneyCompact(win.rent_monthly)}/mo`} />
                  <Stat label="Cash-on-cash" value={pct(win.coc_pct)} />
                </>
              )}
              <Separator />
              <Link
                href={`/analyzer/${card.property_id}`}
                className="block text-xs font-medium text-accent-ink hover:underline"
              >
                Stress-test these numbers in the Analyzer →
              </Link>
            </CardContent>
          </Card>

          <Card>
            <CardContent>
              <div className="eyebrow mb-2">Contents</div>
              <nav className="space-y-1" aria-label="Report sections">
                {report.sections.map((s, i) => (
                  <a
                    key={s.key}
                    href={`#${s.key}`}
                    className="flex items-baseline gap-2 rounded px-1.5 py-1 text-[12.5px] text-ink-2 transition-colors hover:bg-raise hover:text-ink"
                  >
                    <span className="figure text-[10px] text-ink-faint">{String(i + 1).padStart(2, "0")}</span>
                    {s.title}
                  </a>
                ))}
              </nav>
            </CardContent>
          </Card>
        </aside>
      </div>
    </div>
  );
}
