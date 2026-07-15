"use client";

/** S13 — Deal Analyzer: interactive underwriting. Assumptions rail (grouped, resettable,
 * precedence-labeled) → live outputs on every keystroke. Scenario A/B/C compare. */

import * as React from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  RotateCcw,
  Plus,
  FlaskConical,
  Trash2,
} from "lucide-react";
import {
  ResponsiveContainer,
  ComposedChart,
  Line,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RTooltip,
  Legend,
} from "recharts";
import { fetchProperty } from "@/lib/api";
import {
  computeFlip,
  computeRental,
  type FlipAssumptions,
  type RentalAssumptions,
} from "@/lib/finance";
import { moneyCompact, money, pct, ratio } from "@/lib/format";
import { STRATEGY_LABEL, type Strategy } from "@/lib/domain";
import type { PropertyBundle } from "@/lib/types";
import { Stat } from "@/components/deal/stat";
import { GradeRing } from "@/components/deal/grade-ring";
import { UnitInput } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { ChartTooltipFrame } from "@/components/charts/chart-kit";
import { Tip } from "@/components/ui/tooltip";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

type AnyAssumptions = FlipAssumptions & RentalAssumptions;

interface Scenario {
  name: string;
  strategy: Strategy;
  assumptions: AnyAssumptions;
}

/* ---- Assumption field: slider + numeric input + reset affordance ---- */

function Field({
  label,
  value,
  system,
  onChange,
  min,
  max,
  step = 1,
  prefix,
  suffix,
  hint,
}: {
  label: string;
  value: number;
  system: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
  step?: number;
  prefix?: string;
  suffix?: string;
  hint?: string;
}) {
  const dirty = Math.abs(value - system) > 1e-9;
  return (
    <div className="group/field">
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-xs text-ink-2">
          {label}
          {dirty && (
            <Tip label={`User-adjusted (system: ${prefix ?? ""}${system.toLocaleString()}${suffix ?? ""}). Click to reset.`}>
              <button
                onClick={() => onChange(system)}
                className="flex size-3.5 cursor-pointer items-center justify-center rounded-full bg-accent-soft text-accent-ink transition-transform hover:scale-110"
                aria-label={`Reset ${label} to system value`}
              >
                <RotateCcw className="size-2" />
              </button>
            </Tip>
          )}
        </span>
        <UnitInput
          prefix={prefix}
          suffix={suffix}
          className="h-6.5 w-28"
          inputMode="decimal"
          value={Number.isInteger(value) ? value : value.toFixed(2).replace(/\.?0+$/, "")}
          onChange={(e) => {
            const v = Number(e.target.value);
            if (!Number.isNaN(v)) onChange(v);
          }}
          aria-label={label}
          title={hint}
        />
      </div>
      <Slider
        value={[value]}
        min={min}
        max={max}
        step={step}
        onValueChange={([v]) => onChange(v)}
        aria-label={`${label} slider`}
      />
    </div>
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border-b border-stroke px-4 py-3.5 last:border-0">
      <div className="eyebrow mb-3">{title}</div>
      <div className="space-y-3.5">{children}</div>
    </div>
  );
}

/* ---- Derive system defaults from the stored analysis ---- */

function systemAssumptions(bundle: PropertyBundle, strategy: Strategy): AnyAssumptions {
  const a = bundle.analyses.find((x) => x.strategy === strategy) ?? bundle.analyses[0];
  return {
    purchase_price: a.purchase_price,
    arv: a.arv,
    rehab_budget: a.rehab_budget,
    closing_pct: 2.5,
    selling_pct: 6.5,
    hold_months: a.hold_months <= 12 ? a.hold_months : 6,
    holding_monthly: Math.round(a.purchase_price * 0.007 + 800),
    down_pct: strategy === "flip" ? 15 : 25,
    rent_monthly: a.rent_monthly ?? Math.round(bundle.card.rent_monthly ?? 0),
    vacancy_pct: 6,
    mgmt_pct: 9,
    maintenance_pct: 8,
    capex_pct: 7,
    taxes_annual: bundle.taxes_annual,
    insurance_annual: Math.round((bundle.card.state === "FL" ? 3400 : 1500)),
    hoa_monthly: bundle.hoa_monthly ?? 0,
    rate_pct: 6.9,
    term_years: 30,
    refi_ltv_pct: 75,
    appreciation_pct: 3.5,
    rent_growth_pct: 3,
  };
}

export default function AnalyzerPage() {
  return (
    <React.Suspense fallback={<div className="p-6"><Skeleton className="h-96" /></div>}>
      <AnalyzerInner />
    </React.Suspense>
  );
}

function AnalyzerInner() {
  const params = useParams<{ id: string }>();
  const search = useSearchParams();
  const router = useRouter();
  const { data: bundle, isLoading } = useQuery({
    queryKey: ["property", params.id],
    queryFn: () => fetchProperty(params.id),
  });

  const initialStrategy = (search.get("strategy") as Strategy) || "flip";
  const [strategy, setStrategy] = React.useState<Strategy>(
    ["flip", "ltr", "brrrr"].includes(initialStrategy) ? initialStrategy : "flip",
  );
  const [assumptions, setAssumptions] = React.useState<AnyAssumptions | null>(null);
  const [scenarios, setScenarios] = React.useState<Scenario[]>([]);

  const system = React.useMemo(
    () => (bundle ? systemAssumptions(bundle, strategy) : null),
    [bundle, strategy],
  );

  React.useEffect(() => {
    if (system) setAssumptions(system);
  }, [system]);

  if (isLoading || !bundle || !assumptions || !system) {
    return (
      <div className="mx-auto max-w-[1440px] space-y-4 px-4 py-5 md:px-6">
        <Skeleton className="h-8 w-80" />
        <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
          <Skeleton className="h-[560px]" />
          <Skeleton className="h-[560px]" />
        </div>
      </div>
    );
  }

  const card = bundle.card;
  const isFlip = strategy === "flip";
  const set = (patch: Partial<AnyAssumptions>) => setAssumptions((a) => ({ ...a!, ...patch }));
  const dirtyCount = Object.keys(assumptions).filter(
    (k) => Math.abs((assumptions as never as Record<string, number>)[k] - (system as never as Record<string, number>)[k]) > 1e-9,
  ).length;

  const flipOut = isFlip ? computeFlip(assumptions) : null;
  const rentalOut = !isFlip ? computeRental(assumptions, strategy === "brrrr") : null;

  const saveScenario = () => {
    const name = String.fromCharCode(65 + scenarios.length);
    setScenarios((s) => [...s, { name, strategy, assumptions: { ...assumptions } }]);
    toast(`Scenario ${name} saved`, { description: `${STRATEGY_LABEL[strategy]} · ${dirtyCount} adjusted assumption${dirtyCount === 1 ? "" : "s"}` });
  };

  const scenarioRows: Array<{ label: string; get: (s: Scenario) => string }> = [
    { label: "Strategy", get: (s) => STRATEGY_LABEL[s.strategy] },
    { label: "Purchase", get: (s) => moneyCompact(s.assumptions.purchase_price) },
    { label: "Rehab", get: (s) => moneyCompact(s.assumptions.rehab_budget) },
    {
      label: "Profit / CF·mo",
      get: (s) =>
        s.strategy === "flip"
          ? moneyCompact(computeFlip(s.assumptions).net_profit)
          : moneyCompact(computeRental(s.assumptions, s.strategy === "brrrr").cash_flow_monthly),
    },
    {
      label: "Margin / CoC",
      get: (s) =>
        s.strategy === "flip"
          ? pct(computeFlip(s.assumptions).margin_pct)
          : pct(computeRental(s.assumptions, s.strategy === "brrrr").coc_pct),
    },
    {
      label: "Ann. ROI",
      get: (s) =>
        s.strategy === "flip"
          ? pct(computeFlip(s.assumptions).roi_annualized_pct, 0)
          : pct(computeRental(s.assumptions, s.strategy === "brrrr").coc_pct + 10.9, 0),
    },
  ];

  return (
    <div className="mx-auto max-w-[1440px] animate-fade-up px-4 py-5 md:px-6">
      {/* Header */}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <GradeRing score={card.score} grade={card.grade} size="sm" animate={false} />
          <div className="min-w-0">
            <div className="eyebrow">Deal analyzer</div>
            <h1 className="flex flex-wrap items-baseline gap-x-2 text-lg font-semibold tracking-tight text-ink">
              <Link href={`/property/${card.property_id}`} className="hover:text-accent-ink hover:underline">
                {card.line1}
              </Link>
              <span className="figure text-[13px] font-normal text-ink-3">
                {card.city} · {money(card.list_price)} ask
              </span>
            </h1>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Tabs value={strategy} onValueChange={(v) => setStrategy(v as Strategy)}>
            <TabsList aria-label="Strategy">
              <TabsTrigger value="flip">Flip</TabsTrigger>
              <TabsTrigger value="ltr">Rental</TabsTrigger>
              <TabsTrigger value="brrrr">BRRRR</TabsTrigger>
            </TabsList>
          </Tabs>
          <Button variant="primary" onClick={saveScenario} disabled={scenarios.length >= 4}>
            <Plus className="size-4" /> Save scenario
          </Button>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[300px_minmax(0,1fr)] xl:grid-cols-[320px_minmax(0,1fr)]">
        {/* Assumptions rail */}
        <Card className="h-fit lg:sticky lg:top-[72px]">
          <div className="flex items-center justify-between border-b border-stroke px-4 py-2.5">
            <span className="text-[13px] font-semibold text-ink">Assumptions</span>
            <div className="flex items-center gap-2">
              {dirtyCount > 0 && (
                <span className="figure rounded-sm bg-accent-soft px-1.5 py-px text-[10px] font-medium text-accent-ink">
                  {dirtyCount} adjusted
                </span>
              )}
              <Button size="xs" variant="ghost" onClick={() => setAssumptions(system)} disabled={dirtyCount === 0}>
                <RotateCcw className="size-3" /> Reset all
              </Button>
            </div>
          </div>

          <Group title="Acquisition">
            <Field label="Purchase price" value={assumptions.purchase_price} system={system.purchase_price} onChange={(v) => set({ purchase_price: v })} min={Math.round(system.purchase_price * 0.7)} max={Math.round(system.purchase_price * 1.15)} step={1000} prefix="$" />
            <Field label="Closing costs" value={assumptions.closing_pct} system={system.closing_pct} onChange={(v) => set({ closing_pct: v })} min={0} max={6} step={0.1} suffix="%" />
            <Field label="Rehab budget" value={assumptions.rehab_budget} system={system.rehab_budget} onChange={(v) => set({ rehab_budget: v })} min={0} max={Math.max(150_000, system.rehab_budget * 2)} step={500} prefix="$" hint="System value is the vision-model midpoint" />
          </Group>

          <Group title="Valuation">
            <Field label="ARV" value={assumptions.arv} system={system.arv} onChange={(v) => set({ arv: v })} min={Math.round(system.arv * 0.85)} max={Math.round(system.arv * 1.15)} step={1000} prefix="$" hint={`Comp-driven band ${moneyCompact(bundle.analyses[0].arv_lo)}–${moneyCompact(bundle.analyses[0].arv_hi)}`} />
          </Group>

          {isFlip ? (
            <>
              <Group title="Exit">
                <Field label="Selling costs" value={assumptions.selling_pct} system={system.selling_pct} onChange={(v) => set({ selling_pct: v })} min={4} max={10} step={0.1} suffix="%" />
                <Field label="Hold time" value={assumptions.hold_months} system={system.hold_months} onChange={(v) => set({ hold_months: v })} min={2} max={18} suffix="mo" />
                <Field label="Holding cost" value={assumptions.holding_monthly} system={system.holding_monthly} onChange={(v) => set({ holding_monthly: v })} min={0} max={8000} step={50} prefix="$" suffix="/mo" />
              </Group>
              <Group title="Financing">
                <Field label="Down payment" value={assumptions.down_pct} system={system.down_pct} onChange={(v) => set({ down_pct: v })} min={0} max={100} suffix="%" />
              </Group>
            </>
          ) : (
            <>
              <Group title="Income">
                <Field label="Monthly rent" value={assumptions.rent_monthly} system={system.rent_monthly} onChange={(v) => set({ rent_monthly: v })} min={Math.round(system.rent_monthly * 0.7)} max={Math.round(system.rent_monthly * 1.3)} step={25} prefix="$" />
                <Field label="Vacancy" value={assumptions.vacancy_pct} system={system.vacancy_pct} onChange={(v) => set({ vacancy_pct: v })} min={0} max={15} step={0.5} suffix="%" />
              </Group>
              <Group title="Expenses">
                <Field label="Management" value={assumptions.mgmt_pct} system={system.mgmt_pct} onChange={(v) => set({ mgmt_pct: v })} min={0} max={15} step={0.5} suffix="%" />
                <Field label="Maintenance" value={assumptions.maintenance_pct} system={system.maintenance_pct} onChange={(v) => set({ maintenance_pct: v })} min={0} max={15} step={0.5} suffix="%" />
                <Field label="CapEx reserve" value={assumptions.capex_pct} system={system.capex_pct} onChange={(v) => set({ capex_pct: v })} min={0} max={15} step={0.5} suffix="%" />
                <Field label="Taxes" value={assumptions.taxes_annual} system={system.taxes_annual} onChange={(v) => set({ taxes_annual: v })} min={0} max={20000} step={50} prefix="$" suffix="/yr" />
                <Field label="Insurance" value={assumptions.insurance_annual} system={system.insurance_annual} onChange={(v) => set({ insurance_annual: v })} min={0} max={9000} step={50} prefix="$" suffix="/yr" />
              </Group>
              <Group title="Financing">
                <Field label="Down payment" value={assumptions.down_pct} system={system.down_pct} onChange={(v) => set({ down_pct: v })} min={5} max={60} suffix="%" />
                <Field label="Rate" value={assumptions.rate_pct} system={system.rate_pct} onChange={(v) => set({ rate_pct: v })} min={4} max={11} step={0.05} suffix="%" hint="Today's quote — adjust to your lender" />
                {strategy === "brrrr" && (
                  <Field label="Refi LTV" value={assumptions.refi_ltv_pct} system={system.refi_ltv_pct} onChange={(v) => set({ refi_ltv_pct: v })} min={60} max={80} suffix="%" />
                )}
              </Group>
              <Group title="Growth">
                <Field label="Appreciation" value={assumptions.appreciation_pct} system={system.appreciation_pct} onChange={(v) => set({ appreciation_pct: v })} min={-2} max={8} step={0.1} suffix="%/yr" />
                <Field label="Rent growth" value={assumptions.rent_growth_pct} system={system.rent_growth_pct} onChange={(v) => set({ rent_growth_pct: v })} min={0} max={8} step={0.1} suffix="%/yr" />
              </Group>
            </>
          )}
        </Card>

        {/* Live outputs */}
        <div className="min-w-0 space-y-4">
          {/* Headline metrics */}
          <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
            {isFlip && flipOut ? (
              <>
                <Card className="p-3.5">
                  <Stat label="Net profit" size="lg" value={<span className={flipOut.net_profit > 0 ? "text-pos" : "text-neg"}>{moneyCompact(flipOut.net_profit)}</span>} hint="ARV − all-in − selling costs" />
                </Card>
                <Card className="p-3.5">
                  <Stat label="Margin" size="lg" value={pct(flipOut.margin_pct)} hint="Net profit over ARV — 12% is the usual floor" />
                </Card>
                <Card className="p-3.5">
                  <Stat label="Cash needed" size="lg" value={moneyCompact(flipOut.cash_needed)} hint="Down + closing + rehab" />
                </Card>
                <Card className="p-3.5">
                  <Stat label="Annualized ROI" size="lg" value={pct(flipOut.roi_annualized_pct, 0)} hint={`ROI ${pct(flipOut.roi_pct, 0)} over ${assumptions.hold_months} months`} />
                </Card>
              </>
            ) : rentalOut ? (
              <>
                <Card className="p-3.5">
                  <Stat label="Cash flow" size="lg" value={<span className={rentalOut.cash_flow_monthly > 0 ? "text-pos" : "text-neg"}>{moneyCompact(rentalOut.cash_flow_monthly)}/mo</span>} hint="After opex and debt service" />
                </Card>
                <Card className="p-3.5">
                  <Stat
                    label={strategy === "brrrr" ? "CoC (left-in)" : "Cash-on-cash"}
                    size="lg"
                    value={pct(strategy === "brrrr" && rentalOut.capital_left_in > 0 ? ((rentalOut.cash_flow_monthly * 12) / rentalOut.capital_left_in) * 100 : rentalOut.coc_pct)}
                    hint={strategy === "brrrr" ? "Annual cash flow over capital left after refi" : "Annual cash flow over cash invested"}
                  />
                </Card>
                <Card className="p-3.5">
                  <Stat label="DSCR" size="lg" value={<span className={rentalOut.dscr >= 1.2 ? "text-pos" : rentalOut.dscr >= 1 ? undefined : "text-neg"}>{ratio(rentalOut.dscr)}</span>} hint="NOI / debt service — lenders want ≥ 1.20x" />
                </Card>
                <Card className="p-3.5">
                  <Stat
                    label={strategy === "brrrr" ? "Left in deal" : "Cap rate"}
                    size="lg"
                    value={strategy === "brrrr" ? moneyCompact(rentalOut.capital_left_in) : pct(rentalOut.cap_rate_pct)}
                    hint={strategy === "brrrr" ? `Refi returns ${moneyCompact(assumptions.arv * (assumptions.refi_ltv_pct / 100))} at ${assumptions.refi_ltv_pct}% LTV` : "NOI over all-in basis"}
                  />
                </Card>
              </>
            ) : null}
          </div>

          {/* Waterfall + detail */}
          <div className="grid gap-4 xl:grid-cols-2">
            <Card>
              <CardContent>
                <div className="eyebrow mb-3">{isFlip ? "Deal waterfall" : "Monthly waterfall"}</div>
                <div className="space-y-1.5">
                  {(isFlip ? flipOut!.waterfall : rentalOut!.waterfall).map((line, i, arr) => {
                    const max = Math.max(...arr.map((l) => Math.abs(l.amount)), 1);
                    const isResult = i === arr.length - 1;
                    return (
                      <div key={line.key} className={cn("grid grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)_96px] items-center gap-3 px-1 py-0.5", isResult && "mt-1 border-t border-stroke pt-2")}>
                        <span className={cn("truncate text-xs", isResult ? "font-semibold text-ink" : "text-ink-2")}>{line.label}</span>
                        <div className="h-[5px] overflow-hidden rounded-full bg-raise">
                          <div
                            className="h-full rounded-full transition-all duration-300 ease-[var(--ease-swift)]"
                            style={{ width: `${(Math.abs(line.amount) / max) * 100}%`, background: line.amount >= 0 ? "var(--pos)" : "var(--neg)", opacity: isResult ? 1 : 0.55 }}
                          />
                        </div>
                        <span className={cn("figure text-right text-xs font-medium", isResult ? (line.amount >= 0 ? "text-pos" : "text-neg") : "text-ink-2")}>
                          {line.amount < 0 ? "−" : ""}
                          {moneyCompact(Math.abs(line.amount))}
                          {!isFlip ? "/mo" : ""}
                        </span>
                      </div>
                    );
                  })}
                </div>
                {!isFlip && rentalOut && (
                  <details className="mt-3">
                    <summary className="cursor-pointer list-none text-xs font-medium text-accent-ink hover:underline">
                      Expense detail ({rentalOut.expense_lines.length} lines)
                    </summary>
                    <div className="mt-2 space-y-1">
                      {rentalOut.expense_lines.map((e) => (
                        <div key={e.key} className="flex items-baseline justify-between px-1 text-xs">
                          <span className="text-ink-3">{e.label}</span>
                          <span className="figure text-ink-2">−{moneyCompact(e.amount)}/mo</span>
                        </div>
                      ))}
                    </div>
                  </details>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardContent>
                <div className="eyebrow mb-3">{isFlip ? "Returns" : "5-year projection"}</div>
                {isFlip && flipOut ? (
                  <div className="space-y-2.5">
                    {[
                      { label: "All-in basis", value: money(Math.round(flipOut.all_in)), note: `${pct((flipOut.all_in / assumptions.arv) * 100, 0)} of ARV` },
                      { label: "Break-even resale", value: money(Math.round(flipOut.all_in / (1 - assumptions.selling_pct / 100))), note: "covers selling costs" },
                      { label: "Profit per month held", value: moneyCompact(flipOut.net_profit / Math.max(1, assumptions.hold_months)), note: `${assumptions.hold_months}-month hold` },
                      { label: "ROI on cash", value: pct(flipOut.roi_pct, 1), note: `${moneyCompact(flipOut.cash_needed)} deployed` },
                    ].map((r) => (
                      <div key={r.label} className="flex items-baseline justify-between gap-3 border-b border-hairline pb-2 last:border-0">
                        <span className="text-xs text-ink-2">{r.label}</span>
                        <span className="text-right">
                          <span className="figure block text-[13px] font-semibold text-ink">{r.value}</span>
                          <span className="figure block text-[10.5px] text-ink-3">{r.note}</span>
                        </span>
                      </div>
                    ))}
                  </div>
                ) : rentalOut ? (
                  <div className="h-52">
                    <ResponsiveContainer width="100%" height="100%">
                      <ComposedChart data={rentalOut.projection} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                        <CartesianGrid vertical={false} />
                        <XAxis dataKey="year" tickFormatter={(y) => `Yr ${y}`} tickLine={false} axisLine={false} dy={4} />
                        <YAxis tickFormatter={(v) => moneyCompact(v)} tickLine={false} axisLine={false} width={54} />
                        <RTooltip
                          content={({ active, payload, label }) =>
                            active && payload?.length ? (
                              <ChartTooltipFrame
                                label={`Year ${label}`}
                                rows={payload.map((p) => ({
                                  name: p.name === "equity" ? "Equity" : "Cumulative CF",
                                  value: moneyCompact(Number(p.value)),
                                  color: String(p.color),
                                }))}
                              />
                            ) : null
                          }
                        />
                        <Area type="monotone" dataKey="equity" name="equity" stroke="var(--viz-1)" fill="var(--accent-soft)" strokeWidth={1.75} />
                        <Line type="monotone" dataKey="cumulative_cf" name="cf" stroke="var(--viz-6)" strokeWidth={1.75} dot={false} />
                      </ComposedChart>
                    </ResponsiveContainer>
                  </div>
                ) : null}
              </CardContent>
            </Card>
          </div>

          {/* Scenario compare */}
          <Card>
            <CardContent>
              <div className="mb-3 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <FlaskConical className="size-4 text-accent-ink" aria-hidden />
                  <span className="text-[13px] font-semibold text-ink">Scenario compare</span>
                </div>
                {scenarios.length > 0 && (
                  <Button size="xs" variant="ghost" onClick={() => setScenarios([])}>
                    <Trash2 className="size-3" /> Clear
                  </Button>
                )}
              </div>
              {scenarios.length === 0 ? (
                <p className="py-4 text-center text-[13px] text-ink-3">
                  Adjust assumptions, then <span className="font-medium text-ink-2">Save scenario</span> to
                  compare structures side by side — up to four columns.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-[13px]">
                    <thead>
                      <tr>
                        <th className="eyebrow pb-2 pr-4 text-left font-medium">Metric</th>
                        {scenarios.map((s) => (
                          <th key={s.name} className="figure pb-2 pr-4 text-right text-xs font-semibold text-accent-ink">
                            Scenario {s.name}
                          </th>
                        ))}
                        <th className="figure pb-2 text-right text-xs font-semibold text-ink-3">Current</th>
                      </tr>
                    </thead>
                    <tbody>
                      {scenarioRows.map((row) => (
                        <tr key={row.label} className="border-t border-hairline">
                          <td className="py-1.5 pr-4 text-ink-2">{row.label}</td>
                          {scenarios.map((s) => (
                            <td key={s.name} className="figure py-1.5 pr-4 text-right font-medium text-ink">
                              {row.get(s)}
                            </td>
                          ))}
                          <td className="figure py-1.5 text-right font-medium text-ink-3">
                            {row.get({ name: "cur", strategy, assumptions })}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>

          <p className="text-[10.5px] leading-relaxed text-ink-faint">
            Live recompute uses the same formulas as the platform engine; saved scenarios attach to this
            property. Estimates only — verify rates, taxes and rehab scope independently.
          </p>
        </div>
      </div>
    </div>
  );
}
