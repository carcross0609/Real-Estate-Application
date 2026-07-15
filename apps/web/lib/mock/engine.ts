/**
 * Deterministic demo dataset. Seeded PRNG + fixed anchor date so server and client
 * render identical output (no hydration drift). Economics are internally coherent:
 * scores derive from the same margins/yields the cards display, and every factor
 * ledger reconciles to its headline score — "the number and the why" stays honest
 * even in demo data.
 *
 * Swap-out path: lib/api.ts is the only consumer; point it at the generated OpenAPI
 * client and this module disappears from the bundle.
 */

import { gradeFor, type Strategy, type Recommendation, type PropertyType } from "../domain";
import type {
  PropertyCard,
  PropertyBundle,
  ScoreResult,
  StrategyScore,
  FactorLedgerRow,
  StrategyAnalysis,
  PropertyCondition,
  RehabLineItem,
  RedFlag,
  Comp,
  ListingEvent,
  MarketReport,
  MarketSummary,
  MetricPoint,
  SubmarketRow,
  InvestmentReport,
  CalcLine,
} from "../types";

/* ------------------------------- RNG ------------------------------- */

export function mulberry32(seed: number) {
  return function () {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const ANCHOR = Date.UTC(2026, 6, 15, 12, 0, 0); // 2026-07-15 — demo "now"

function daysAgoIso(days: number): string {
  return new Date(ANCHOR - days * 86_400_000).toISOString();
}

function monthIso(monthsAgo: number): string {
  const d = new Date(ANCHOR);
  d.setUTCMonth(d.getUTCMonth() - monthsAgo, 1);
  return d.toISOString().slice(0, 10);
}

const round = (v: number, step = 1) => Math.round(v / step) * step;
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/* ------------------------------- Markets ------------------------------- */

interface MarketSpec {
  id: string;
  name: string;
  state: string;
  center: { lat: number; lon: number };
  medianPrice: number;
  rentYieldMo: number; // monthly rent as share of value
  appreciation3y: number; // annualized %
  domMedian: number;
  inventoryMonths: number;
  temperature: number;
  submarkets: Array<{ name: string; city: string; zip: string; factor: number }>;
}

export const MARKET_SPECS: MarketSpec[] = [
  {
    id: "mkt-columbus",
    name: "Columbus",
    state: "OH",
    center: { lat: 39.983, lon: -82.994 },
    medianPrice: 289_000,
    rentYieldMo: 0.0072,
    appreciation3y: 5.1,
    domMedian: 21,
    inventoryMonths: 1.9,
    temperature: 78,
    submarkets: [
      { name: "Hilltop", city: "Columbus", zip: "43204", factor: 0.62 },
      { name: "Linden", city: "Columbus", zip: "43211", factor: 0.55 },
      { name: "Westerville", city: "Westerville", zip: "43081", factor: 1.18 },
      { name: "Whitehall", city: "Whitehall", zip: "43213", factor: 0.72 },
      { name: "Reynoldsburg", city: "Reynoldsburg", zip: "43068", factor: 0.88 },
      { name: "Hilliard", city: "Hilliard", zip: "43026", factor: 1.22 },
      { name: "Clintonville", city: "Columbus", zip: "43214", factor: 1.12 },
      { name: "Grove City", city: "Grove City", zip: "43123", factor: 0.95 },
    ],
  },
  {
    id: "mkt-tampa",
    name: "Tampa Bay",
    state: "FL",
    center: { lat: 27.951, lon: -82.457 },
    medianPrice: 412_000,
    rentYieldMo: 0.0061,
    appreciation3y: 4.2,
    domMedian: 34,
    inventoryMonths: 3.4,
    temperature: 55,
    submarkets: [
      { name: "Seminole Heights", city: "Tampa", zip: "33604", factor: 0.92 },
      { name: "Brandon", city: "Brandon", zip: "33511", factor: 0.9 },
      { name: "Riverview", city: "Riverview", zip: "33569", factor: 0.97 },
      { name: "St. Petersburg", city: "St. Petersburg", zip: "33712", factor: 1.05 },
      { name: "Clearwater", city: "Clearwater", zip: "33756", factor: 0.98 },
      { name: "Town 'n' Country", city: "Tampa", zip: "33615", factor: 0.88 },
      { name: "Wesley Chapel", city: "Wesley Chapel", zip: "33544", factor: 1.15 },
      { name: "Ruskin", city: "Ruskin", zip: "33570", factor: 0.78 },
    ],
  },
  {
    id: "mkt-phoenix",
    name: "Phoenix",
    state: "AZ",
    center: { lat: 33.448, lon: -112.074 },
    medianPrice: 455_000,
    rentYieldMo: 0.0055,
    appreciation3y: 3.6,
    domMedian: 41,
    inventoryMonths: 3.9,
    temperature: 47,
    submarkets: [
      { name: "Maryvale", city: "Phoenix", zip: "85031", factor: 0.7 },
      { name: "South Mountain", city: "Phoenix", zip: "85042", factor: 0.82 },
      { name: "Mesa West", city: "Mesa", zip: "85201", factor: 0.86 },
      { name: "Glendale", city: "Glendale", zip: "85301", factor: 0.75 },
      { name: "Tempe", city: "Tempe", zip: "85281", factor: 1.08 },
      { name: "Sunnyslope", city: "Phoenix", zip: "85020", factor: 0.95 },
      { name: "Chandler", city: "Chandler", zip: "85225", factor: 1.12 },
      { name: "Laveen", city: "Laveen", zip: "85339", factor: 0.9 },
    ],
  },
];

const STREETS = [
  "Maple", "Oakwood", "Cedarhurst", "Willowbrook", "Sycamore", "Juniper", "Ashford",
  "Bellview", "Crestline", "Dunmore", "Eastgate", "Fairhaven", "Glenrose", "Harmon",
  "Ivywood", "Kingsley", "Larkspur", "Meridian", "Northfield", "Pemberton", "Quailrun",
  "Rosemont", "Stonebridge", "Thornbury", "Vandalia", "Wexford", "Yardley", "Boxelder",
  "Chestnut Hill", "Delmar", "Elmhurst", "Foxglove", "Granville", "Hawthorne", "Ironwood", "Bramblewood",
];
const SUFFIX = ["St", "Ave", "Dr", "Ln", "Ct", "Rd", "Pl", "Way"];

/* ------------------------------- Factor library ------------------------------- */

const FACTOR_DEFS: Record<
  string,
  { label: string; group: FactorLedgerRow["group"]; weight: number }
> = {
  flip_margin: { label: "Flip margin vs. target", group: "F", weight: 0.2 },
  coc: { label: "Cash-on-cash return", group: "F", weight: 0.16 },
  dscr: { label: "Debt service coverage", group: "F", weight: 0.08 },
  cap_vs_market: { label: "Cap rate vs. market", group: "F", weight: 0.08 },
  price_to_arv: { label: "Price-to-ARV ratio", group: "F", weight: 0.12 },
  dom_vs_market: { label: "Days on market vs. median", group: "D", weight: 0.06 },
  price_cuts: { label: "Price cut pattern", group: "D", weight: 0.05 },
  seller_signals: { label: "Seller motivation signals", group: "D", weight: 0.03 },
  condition_arbitrage: { label: "Condition arbitrage", group: "C", weight: 0.08 },
  reno_difficulty: { label: "Renovation difficulty", group: "C", weight: 0.05 },
  red_flags: { label: "Red-flag severity", group: "C", weight: 0.04 },
  school: { label: "School percentile", group: "L", weight: 0.05 },
  crime: { label: "Crime index", group: "L", weight: 0.04 },
  walkability: { label: "Walkability", group: "L", weight: 0.02 },
  flood: { label: "Flood exposure", group: "L", weight: 0.03 },
  appreciation: { label: "3-yr appreciation forecast", group: "M", weight: 0.06 },
  rent_growth: { label: "Rent growth trend", group: "M", weight: 0.04 },
  liquidity: { label: "Market liquidity", group: "M", weight: 0.05 },
  inventory: { label: "Inventory pressure", group: "M", weight: 0.04 },
};

/* ------------------------------- Property generation ------------------------------- */

export interface GeneratedProperty {
  card: PropertyCard;
  bundle: PropertyBundle;
}

function buildFactors(
  rnd: () => number,
  targetScore: number,
  keys: string[],
  displays: Record<string, string>,
  rationales: Record<string, string>,
): FactorLedgerRow[] {
  const rows: FactorLedgerRow[] = keys.map((key) => {
    const def = FACTOR_DEFS[key];
    const norm = clamp(targetScore + (rnd() - 0.5) * 44, 4, 99);
    return {
      factor_key: key,
      label: def.label,
      group: def.group,
      raw_display: displays[key] ?? "—",
      normalized: round(norm, 1),
      weight: def.weight,
      contribution: 0,
      rationale: rationales[key] ?? null,
      capped_by: null,
    };
  });
  const wSum = rows.reduce((s, r) => s + r.weight, 0);
  rows.forEach((r) => {
    r.contribution = Math.round(((r.normalized - 50) * (r.weight / wSum) * 2) * 10) / 10;
  });
  // Reconcile: nudge the largest factor so Σcontribution === targetScore − 50 exactly.
  const target = targetScore - 50;
  const sum = rows.reduce((s, r) => s + r.contribution, 0);
  rows[0].contribution = Math.round((rows[0].contribution + (target - sum)) * 10) / 10;
  return rows.sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution));
}

function generateProperty(market: MarketSpec, idx: number): GeneratedProperty {
  const rnd = mulberry32(market.id.length * 7919 + idx * 104729 + 13);
  const sub = market.submarkets[Math.floor(rnd() * market.submarkets.length)];
  const id = `${market.id.replace("mkt-", "")}-${String(idx + 1).padStart(3, "0")}`;

  const propertyType: PropertyType =
    rnd() < 0.72 ? "sfr" : rnd() < 0.5 ? "townhome" : rnd() < 0.6 ? "condo" : "mf_2_4";
  const beds = propertyType === "mf_2_4" ? 4 + Math.floor(rnd() * 3) : 2 + Math.floor(rnd() * 4);
  const baths = Math.max(1, Math.round((beds * (0.55 + rnd() * 0.3)) * 2) / 2);
  const sqftV = round(
    (propertyType === "condo" ? 850 : 1050) + beds * 320 + rnd() * 700,
    10,
  );
  const yearBuilt = round(1948 + rnd() * 72);
  const lotSqft = propertyType === "condo" ? null : round(4800 + rnd() * 8200, 100);
  const stories = sqftV > 1900 && rnd() > 0.4 ? 2 : 1;
  const garage = rnd() > 0.35 ? (rnd() > 0.6 ? 2 : 1) : 0;

  // Condition drives everything: 1 (gut) – 10 (turnkey)
  const condition = clamp(round(2.4 + rnd() * 6.8, 0.1), 1.5, 9.4);
  const conditionDiscount = 1 - (10 - condition) * 0.033; // rougher → cheaper

  const baseValue = market.medianPrice * sub.factor * (0.72 + (sqftV / 1900) * 0.42) * (0.94 + rnd() * 0.12);
  const arv = round(baseValue * (1.02 + rnd() * 0.05), 1000);
  // Motivation factor is where deals come from: some sellers price 20%+ under fair value.
  const motivation = 0.76 + rnd() * 0.27;
  const listPrice = round(baseValue * conditionDiscount * motivation, 500);

  const rehabPsf = (10 - condition) * (4.4 + rnd() * 2.2);
  const rehabMid = round(sqftV * rehabPsf * 0.55 + (10 - condition) * 3600, 500);

  const dom = clamp(round(market.domMedian * (0.3 + rnd() * 2.4)), 1, 160);
  const priceCuts = dom > market.domMedian * 1.4 ? (rnd() > 0.5 ? 2 : 1) : rnd() > 0.75 ? 1 : 0;

  /* ---- Flip economics ---- */
  const closing = round(listPrice * 0.025 + 1800, 100);
  const holdMonths = 5 + Math.round(rnd() * 3);
  const holdingCosts = round((listPrice * 0.007 + 800) * holdMonths, 100);
  const sellingCosts = round(arv * 0.065, 100);
  const allInFlip = listPrice + closing + rehabMid + holdingCosts;
  const netProfit = round(arv - allInFlip - sellingCosts, 100);
  const flipMarginPct = (netProfit / arv) * 100;

  /* ---- Rental economics ---- */
  const rent = round(baseValue * market.rentYieldMo * (0.92 + rnd() * 0.18), 5);
  const taxes = round(listPrice * (market.state === "OH" ? 0.019 : market.state === "FL" ? 0.011 : 0.0075), 10);
  const insurance = round((market.state === "FL" ? 3400 : 1500) * (0.8 + rnd() * 0.5), 10);
  const hoa = propertyType === "condo" ? round(180 + rnd() * 240, 5) : propertyType === "townhome" && rnd() > 0.5 ? round(60 + rnd() * 120, 5) : null;
  const mgmtMo = round(rent * 0.09, 1);
  const maintMo = round(rent * 0.08, 1);
  const capexMo = round(rent * 0.07, 1);
  const vacancyMo = round(rent * 0.06, 1);
  const taxMo = round(taxes / 12, 1);
  const insMo = round(insurance / 12, 1);
  const opexMo = mgmtMo + maintMo + capexMo + vacancyMo + taxMo + insMo + (hoa ?? 0);
  const noi = round((rent - opexMo + vacancyMo * 0) * 12 - 0, 10); // NOI excludes debt
  const rate = 6.9;
  const downPct = 0.25;
  const loan = (listPrice + rehabMid * 0.5) * (1 - downPct);
  const mRate = rate / 100 / 12;
  const pAndI = round((loan * mRate) / (1 - Math.pow(1 + mRate, -360)), 1);
  const cashInvested = round(listPrice * downPct + closing + rehabMid * 0.5, 100);
  const cashFlowMo = round(rent - opexMo - pAndI, 1);
  const cocPct = (cashFlowMo * 12 / cashInvested) * 100;
  const capRatePct = (noi / (listPrice + rehabMid)) * 100;
  const dscr = noi / 12 / pAndI;

  /* ---- BRRRR ---- */
  const refiLoan = arv * 0.75;
  const capitalLeftIn = round(Math.max(0, listPrice + closing + rehabMid - refiLoan), 100);

  /* ---- Scores (derived from the economics) ---- */
  const flipScore = clamp(36 + flipMarginPct * 3.3 + (condition < 6 ? 4 : 0) + (rnd() - 0.5) * 6, 4, 98);
  const ltrScore = clamp(34 + cocPct * 5.6 + (dscr > 1.2 ? 6 : 0) + (rnd() - 0.5) * 6, 4, 97);
  const brrrrScore = clamp(
    30 + flipMarginPct * 1.5 + cocPct * 2.6 + (capitalLeftIn < cashInvested * 0.4 ? 18 : capitalLeftIn < cashInvested * 0.8 ? 8 : 0) + (rnd() - 0.5) * 6,
    4,
    97,
  );

  const school = clamp(round(sub.factor * 52 + rnd() * 28), 4, 98);
  const crime = clamp(round(78 - sub.factor * 34 + rnd() * 22), 5, 95);
  const walk = clamp(round(30 + rnd() * 55), 5, 96);
  const floodZone = market.state === "FL" ? (rnd() > 0.62 ? "AE" : "X") : rnd() > 0.9 ? "AE" : "X";

  const compCount = 3 + Math.floor(rnd() * 6);
  const photoCount = 14 + Math.floor(rnd() * 26);
  const confidence = clamp(
    round(44 + compCount * 5.5 + (photoCount > 22 ? 8 : 3) + (dom > 60 ? -4 : 0) + rnd() * 8),
    35,
    96,
  );
  const risk = clamp(
    round(
      62 -
        condition * 3.4 +
        (floodZone === "AE" ? 12 : 0) +
        (yearBuilt < 1960 ? 8 : 0) +
        (dscr < 1 ? 9 : 0) +
        rnd() * 10,
    ),
    8,
    88,
  );

  const strategies: Array<[Strategy, number]> = [
    ["flip", flipScore],
    ["ltr", ltrScore],
    ["brrrr", brrrrScore],
  ];
  strategies.sort((a, b) => b[1] - a[1]);
  const [winStrategy, winScoreRaw] = strategies[0];
  const overall = round(winScoreRaw, 0.1);
  const grade = gradeFor(overall);

  const recommendation: Recommendation =
    overall >= 83 && risk < 55 ? "strong_buy" : overall >= 72 ? "buy" : overall >= 52 ? "hold_watch" : "pass";

  /* ---- Displays for ledger ---- */
  const displays: Record<string, string> = {
    flip_margin: `${flipMarginPct.toFixed(1)}%`,
    coc: `${cocPct.toFixed(1)}%`,
    dscr: `${dscr.toFixed(2)}x`,
    cap_vs_market: `${capRatePct.toFixed(1)}% vs 5.8%`,
    price_to_arv: `${((listPrice / arv) * 100).toFixed(0)}%`,
    dom_vs_market: `${dom}d vs ${market.domMedian}d`,
    price_cuts: priceCuts ? `${priceCuts} cut${priceCuts > 1 ? "s" : ""}` : "none",
    seller_signals: priceCuts > 1 ? "motivated" : "neutral",
    condition_arbitrage: `${condition.toFixed(1)}/10 condition`,
    reno_difficulty: condition < 4 ? "heavy" : condition < 7 ? "moderate" : "light",
    red_flags: condition < 4 ? "2 flagged" : condition < 6.5 ? "1 flagged" : "none",
    school: `${school}th pctile`,
    crime: `${crime}/100`,
    walkability: `${walk}/100`,
    flood: floodZone === "AE" ? "Zone AE" : "Zone X",
    appreciation: `+${market.appreciation3y.toFixed(1)}%/yr`,
    rent_growth: `+${(2.2 + sub.factor).toFixed(1)}%/yr`,
    liquidity: `${round(market.temperature * 0.9 + 5)}/100`,
    inventory: `${market.inventoryMonths.toFixed(1)} mo`,
  };
  const rationales: Record<string, string> = {
    flip_margin: `Net margin after ${holdMonths}-month hold, 7% selling costs`,
    coc: `Year-1 cash flow over ${Math.round(cashInvested / 1000)}k invested`,
    condition_arbitrage: "Gap between condition-implied discount and rehab cost",
    dom_vs_market: "Longer exposure than market median improves negotiability",
    flood: floodZone === "AE" ? "FEMA AE — insurance quotable, priced into expenses" : "Outside the special flood hazard area",
  };

  const mkStrategyScore = (s: Strategy, sc: number): StrategyScore => {
    const keys =
      s === "flip"
        ? ["flip_margin", "price_to_arv", "condition_arbitrage", "dom_vs_market", "price_cuts", "reno_difficulty", "red_flags", "school", "crime", "flood", "liquidity", "appreciation"]
        : s === "ltr"
          ? ["coc", "dscr", "cap_vs_market", "price_to_arv", "dom_vs_market", "condition_arbitrage", "school", "crime", "walkability", "flood", "rent_growth", "inventory"]
          : ["flip_margin", "coc", "price_to_arv", "condition_arbitrage", "dscr", "dom_vs_market", "reno_difficulty", "school", "crime", "flood", "rent_growth", "liquidity"];
    return {
      strategy: s,
      score: round(sc, 0.1),
      raw_score: round(clamp(sc + rnd() * 3, 0, 100), 0.1),
      capped_by: dscr < 0.95 && s !== "flip" ? "DSCR below 1.0 gate" : null,
      factors: buildFactors(rnd, sc, keys, displays, rationales),
    };
  };

  const scoreResult: ScoreResult = {
    property_id: id,
    scoring_version: "2026.07.1",
    overall_score: overall,
    grade,
    winning_strategy: winStrategy,
    risk_score: risk,
    confidence_score: confidence,
    recommendation,
    category_scores: {
      profitability: round(clamp((winStrategy === "flip" ? flipScore : ltrScore) + (rnd() - 0.5) * 8, 5, 98)),
      risk,
      location: round(clamp(school * 0.5 + (100 - crime) * 0.35 + walk * 0.15, 5, 98)),
      condition: round(condition * 10),
      appreciation: round(clamp(market.appreciation3y * 13 + sub.factor * 12, 10, 95)),
      rental_strength: round(clamp(30 + cocPct * 5.2, 8, 96)),
      liquidity: round(clamp(market.temperature * 0.9 + sub.factor * 8, 10, 96)),
      renovation_complexity: round(clamp((10 - condition) * 11, 4, 96)),
      financing_difficulty: round(clamp(30 + (dscr < 1 ? 28 : 0) + (condition < 4 ? 22 : 0) + rnd() * 12, 8, 92)),
      market_conditions: round(clamp(market.temperature + (rnd() - 0.5) * 12, 10, 95)),
    },
    strategy_scores: strategies.map(([s, sc]) => mkStrategyScore(s, sc)),
    explanation: {
      positives: [
        flipMarginPct > 12
          ? `Flip margin of ${flipMarginPct.toFixed(1)}% clears the 12% floor with room`
          : cocPct > 6
            ? `${cocPct.toFixed(1)}% cash-on-cash beats the market-class median`
            : `Priced ${Math.round((1 - listPrice / arv) * 100)}% below ARV`,
        dom > market.domMedian * 1.5
          ? `${dom} days on market — seller exposed ${Math.round(dom / market.domMedian)}× the median`
          : `${sub.name} liquidity supports a ${holdMonths}-month exit`,
        condition < 6 ? "Condition gap is cosmetic-heavy; ARV upside exceeds rehab cost" : "Turnkey-adjacent condition keeps execution risk low",
      ],
      negatives: [
        floodZone === "AE" ? "FEMA Zone AE — flood premium reduces cash flow" : yearBuilt < 1962 ? `${yearBuilt} build — plumbing/electrical era risk priced into rehab` : "Comp set is thin beyond 0.6 mi",
        priceCuts === 0 && dom < market.domMedian ? "Fresh listing — limited negotiability yet" : "Rehab estimate carries ±18% band pending inspection",
      ],
      caps: dscr < 0.95 ? ["Rental strategies capped: DSCR below 1.0 at current rates"] : [],
    },
    as_of: daysAgoIso(0.2),
  };

  /* ---- Analyses ---- */
  const expensesMonthly: CalcLine[] = [
    { key: "taxes", label: "Property taxes", amount: taxMo },
    { key: "insurance", label: "Insurance", amount: insMo },
    ...(hoa ? [{ key: "hoa", label: "HOA dues", amount: hoa }] : []),
    { key: "mgmt", label: "Management (9%)", amount: mgmtMo },
    { key: "maintenance", label: "Maintenance (8%)", amount: maintMo },
    { key: "capex", label: "CapEx reserve (7%)", amount: capexMo },
    { key: "vacancy", label: "Vacancy (6%)", amount: vacancyMo },
  ];

  const flipAnalysis: StrategyAnalysis = {
    strategy: "flip",
    purchase_price: listPrice,
    closing_costs: closing,
    rehab_budget: rehabMid,
    all_in: allInFlip,
    cash_invested: round(listPrice * 0.15 + closing + rehabMid, 100),
    arv,
    arv_lo: round(arv * 0.955, 1000),
    arv_hi: round(arv * 1.045, 1000),
    arv_confidence: confidence,
    rent_monthly: null,
    rent_lo: null,
    rent_hi: null,
    rent_confidence: null,
    expenses_monthly: [],
    noi_annual: null,
    cash_flow_monthly: null,
    coc_pct: null,
    cap_rate_pct: null,
    dscr: null,
    flip_margin_pct: round(flipMarginPct, 0.1),
    net_profit: netProfit,
    roi_annualized_pct: round(((netProfit / (listPrice * 0.15 + closing + rehabMid)) * (12 / holdMonths)) * 100, 0.1),
    capital_left_in: null,
    breakeven_occupancy_pct: null,
    hold_months: holdMonths,
    waterfall: [
      { key: "arv", label: "Resale at ARV", amount: arv },
      { key: "purchase", label: "Purchase price", amount: -listPrice },
      { key: "rehab", label: "Rehab budget", amount: -rehabMid },
      { key: "closing", label: "Closing costs", amount: -closing },
      { key: "holding", label: `Holding (${holdMonths} mo)`, amount: -holdingCosts },
      { key: "selling", label: "Selling costs (7%)", amount: -sellingCosts },
      { key: "profit", label: "Net profit", amount: netProfit },
    ],
  };

  const ltrAnalysis: StrategyAnalysis = {
    strategy: "ltr",
    purchase_price: listPrice,
    closing_costs: closing,
    rehab_budget: round(rehabMid * 0.5, 500),
    all_in: listPrice + closing + rehabMid * 0.5,
    cash_invested: cashInvested,
    arv,
    arv_lo: round(arv * 0.955, 1000),
    arv_hi: round(arv * 1.045, 1000),
    arv_confidence: confidence,
    rent_monthly: rent,
    rent_lo: round(rent * 0.93, 5),
    rent_hi: round(rent * 1.07, 5),
    rent_confidence: clamp(confidence - 6, 30, 92),
    expenses_monthly: expensesMonthly,
    noi_annual: noi,
    cash_flow_monthly: cashFlowMo,
    coc_pct: round(cocPct, 0.1),
    cap_rate_pct: round(capRatePct, 0.1),
    dscr: round(dscr, 0.01),
    flip_margin_pct: null,
    net_profit: null,
    roi_annualized_pct: round(cocPct + market.appreciation3y * 3.1, 0.1),
    capital_left_in: null,
    breakeven_occupancy_pct: round(((opexMo + pAndI) / rent) * 100, 0.1),
    hold_months: 60,
    waterfall: [
      { key: "rent", label: "Gross rent", amount: rent },
      { key: "opex", label: "Operating expenses", amount: -round(opexMo, 1) },
      { key: "debt", label: "Principal & interest", amount: -pAndI },
      { key: "cashflow", label: "Monthly cash flow", amount: cashFlowMo },
    ],
  };

  const brrrrAnalysis: StrategyAnalysis = {
    ...ltrAnalysis,
    strategy: "brrrr",
    rehab_budget: rehabMid,
    all_in: listPrice + closing + rehabMid,
    capital_left_in: capitalLeftIn,
    coc_pct: capitalLeftIn > 0 ? round(((cashFlowMo * 12) / capitalLeftIn) * 100, 0.1) : null,
    waterfall: [
      { key: "allin", label: "All-in basis", amount: -(listPrice + closing + rehabMid) },
      { key: "refi", label: "Refi at 75% ARV", amount: round(refiLoan, 100) },
      { key: "left", label: "Capital left in", amount: -capitalLeftIn },
      { key: "cashflow", label: "Monthly cash flow", amount: cashFlowMo },
    ],
  };

  /* ---- Condition ---- */
  const mkArea = (area: string, bias: number, note: string) => ({
    area,
    score: clamp(round(condition + bias + (rnd() - 0.5) * 2.2, 0.1), 1, 10),
    note,
  });
  const areas = [
    mkArea("Kitchen", -0.8, condition < 5.5 ? "Dated cabinets, laminate counters visible in 4 photos" : "Updated within ~8 years; serviceable finishes"),
    mkArea("Bathrooms", -0.5, condition < 5.5 ? "Original tile and vanities; reglaze or replace" : "Mixed updates across baths"),
    mkArea("Roof", 0.4, yearBuilt < 1985 ? "Architectural shingle, mid-life wear at ridgeline" : "No visible curling or patching"),
    mkArea("HVAC", 0.1, "Condenser visible; age indeterminate from photos"),
    mkArea("Flooring", -0.3, condition < 5 ? "Carpet over probable hardwood in main rooms" : "LVP in main living areas"),
    mkArea("Exterior", 0.3, "Siding and trim consistent with listed year"),
    mkArea("Foundation", 0.8, "No step cracks or moisture staining visible"),
  ];
  const redFlags: RedFlag[] = [];
  if (condition < 4.2) redFlags.push({ label: "Possible water staining — basement", severity: "high", note: "Photo 11 shows discoloration at the north wall; verify with inspection" });
  if (condition < 6 && yearBuilt < 1975) redFlags.push({ label: "Era plumbing (galvanized likely)", severity: "medium", note: "Build year + visible fixtures suggest original supply lines" });
  if (floodZone === "AE") redFlags.push({ label: "FEMA flood zone AE", severity: "medium", note: "Insurance quotable; premium included in expense model" });
  if (redFlags.length === 0 && rnd() > 0.7) redFlags.push({ label: "Mature tree over roofline", severity: "low", note: "Trim recommended; minor gutter debris visible" });

  const rehabItems: RehabLineItem[] = [];
  const pushItem = (area: string, scope: string, lo: number, hi: number, conf: RehabLineItem["confidence"], src: string) =>
    rehabItems.push({ area, scope, cost_lo: round(lo, 100), cost_hi: round(hi, 100), confidence: conf, source_note: src });
  if (condition < 6.5) pushItem("Kitchen", condition < 4.5 ? "Full remodel — cabinets, counters, appliances" : "Refresh — paint cabinets, new counters & hardware", condition < 4.5 ? 16000 : 7500, condition < 4.5 ? 26000 : 12500, "medium", "4 photos · regional unit costs v2026.2");
  if (condition < 6.5) pushItem("Bathrooms", `${Math.min(Math.round(baths), 3)} bath ${condition < 4.5 ? "gut renovation" : "update"}`, (condition < 4.5 ? 8000 : 3800) * Math.min(baths, 3) * 0.7, (condition < 4.5 ? 13000 : 6200) * Math.min(baths, 3) * 0.7, "medium", "3 photos · fixture-count heuristic");
  if (condition < 5.5) pushItem("Flooring", `Refinish/replace ~${round(sqftV * 0.7, 50)} sqft`, sqftV * 2.6, sqftV * 4.6, "high", "Coverage across 6 photos");
  pushItem("Paint", "Interior repaint, full", sqftV * 1.4, sqftV * 2.1, "high", "Standard turnover scope");
  if (condition < 5 && yearBuilt < 1985) pushItem("Roof", "Replace architectural shingle", 8500, 14500, "low", "Aerial + 1 photo; verify age via permit records");
  if (condition < 4.5) pushItem("HVAC", "Replace condenser + air handler", 6800, 10500, "low", "Not visible in photos — age assumed from era");
  pushItem("Exterior & landscape", "Curb appeal package", 2400, 5200, "medium", "Front elevation photos");
  const rehabTotalLo = round(rehabItems.reduce((s, i) => s + i.cost_lo, 0), 500);
  const rehabTotalHi = round(rehabItems.reduce((s, i) => s + i.cost_hi, 0), 500);

  const conditionOut: PropertyCondition = {
    property_id: id,
    overall_condition: round(condition, 0.1),
    condition_label: condition >= 8 ? "Turnkey" : condition >= 6.5 ? "Rent-ready" : condition >= 4.5 ? "Cosmetic rehab" : "Heavy rehab",
    analyzed_photos: photoCount - Math.floor(rnd() * 3),
    total_photos: photoCount,
    areas,
    red_flags: redFlags,
    rehab: rehabItems,
    rehab_total_lo: rehabTotalLo,
    rehab_total_hi: rehabTotalHi,
    renovation_difficulty: condition < 4 ? 4 : condition < 6 ? 3 : 2,
    confidence: clamp(confidence - 8, 30, 90),
    model_version: "vision-2026.06",
    as_of: daysAgoIso(1 + rnd() * 3),
  };

  /* ---- Comps ---- */
  const mkComps = (kind: Comp["kind"]): Comp[] => {
    const n = kind === "sale" ? compCount : Math.max(3, compCount - 2);
    return Array.from({ length: n }, (_, i) => {
      const sim = clamp(round(92 - i * 6 - rnd() * 5), 48, 96);
      const base = kind === "sale" ? arv : rent;
      const soldPrice = round(base * (0.9 + rnd() * 0.2), kind === "sale" ? 1000 : 5);
      const adjs = [
        { label: "Sqft delta", amount: round((rnd() - 0.5) * (kind === "sale" ? 14000 : 90), kind === "sale" ? 500 : 5) },
        { label: "Condition", amount: round((rnd() - 0.4) * (kind === "sale" ? 11000 : 70), kind === "sale" ? 500 : 5) },
        { label: "Lot / garage", amount: round((rnd() - 0.5) * (kind === "sale" ? 7000 : 40), kind === "sale" ? 500 : 5) },
      ];
      const compSqft = round(sqftV * (0.85 + rnd() * 0.3), 10);
      return {
        comp_id: `${id}-${kind}-c${i + 1}`,
        line1: `${round(1000 + rnd() * 8500)} ${STREETS[Math.floor(rnd() * STREETS.length)]} ${SUFFIX[Math.floor(rnd() * SUFFIX.length)]}`,
        distance_mi: round(0.1 + rnd() * 0.9, 0.01),
        similarity: sim,
        sold_date: daysAgoIso(round(20 + rnd() * 150)),
        sold_price: soldPrice,
        beds: clamp(beds + Math.round((rnd() - 0.5) * 2), 1, 7),
        baths: Math.max(1, Math.round((baths + (rnd() - 0.5)) * 2) / 2),
        sqft: compSqft,
        year_built: clamp(yearBuilt + Math.round((rnd() - 0.5) * 18), 1900, 2024),
        price_per_sqft: round(soldPrice / compSqft, 1),
        adjusted_price: round(soldPrice + adjs.reduce((s, a) => s + a.amount, 0), kind === "sale" ? 500 : 5),
        adjustments: adjs,
        rationale: `Same ${sub.name} pocket, ${i === 0 ? "closest structural match" : `${round(0.1 + rnd() * 0.9, 0.1)} mi, similar era`}`,
        kind,
        status: i === n - 1 && rnd() > 0.6 ? "excluded" : "included",
      };
    });
  };

  /* ---- Events ---- */
  const events: ListingEvent[] = [
    { date: daysAgoIso(dom), type: "listed", label: "Listed", detail: null, price: round(listPrice * (1 + priceCuts * 0.035), 500), delta: null },
  ];
  for (let c = 0; c < priceCuts; c++) {
    const cutPrice = round(listPrice * (1 + (priceCuts - c - 1) * 0.035), 500);
    const prev: number = events[events.length - 1].price ?? cutPrice;
    events.push({
      date: daysAgoIso(round(dom * (1 - (c + 1) / (priceCuts + 1)))),
      type: "price_change",
      label: "Price cut",
      detail: null,
      price: cutPrice,
      delta: cutPrice - prev,
    });
  }
  if (rnd() > 0.82) {
    events.push({ date: daysAgoIso(round(dom * 0.3)), type: "score_change", label: "Score upgraded", detail: `Comps refresh moved ARV +${round(1 + rnd() * 3)}%`, price: null, delta: null });
  }

  const movement: PropertyCard["movement"] = [];
  if (dom <= 4) movement.push("new");
  if (priceCuts > 0 && dom - round(dom * (1 - priceCuts / (priceCuts + 1))) < 30) movement.push("price_cut");
  if (events.some((e) => e.type === "score_change")) movement.push("score_up");

  const streetNo = round(400 + rnd() * 9100);
  const street = STREETS[Math.floor(rnd() * STREETS.length)];
  const suffix = SUFFIX[Math.floor(rnd() * SUFFIX.length)];

  const card: PropertyCard = {
    property_id: id,
    listing_id: `lst-${id}`,
    line1: `${streetNo} ${street} ${suffix}`,
    city: sub.city,
    state: market.state,
    zip: sub.zip,
    lat: market.center.lat + (rnd() - 0.5) * 0.42,
    lon: market.center.lon + (rnd() - 0.5) * 0.5,
    property_type: propertyType,
    beds,
    baths,
    sqft: sqftV,
    lot_sqft: lotSqft,
    year_built: yearBuilt,
    list_price: listPrice,
    status: rnd() > 0.94 ? "pending" : "active",
    dom,
    list_date: daysAgoIso(dom).slice(0, 10),
    price_per_sqft: round(listPrice / sqftV, 1),
    photo_count: photoCount,
    score: overall,
    grade,
    risk_score: risk,
    confidence_score: confidence,
    recommendation,
    scored_strategy: winStrategy,
    movement,
    market_id: market.id,
    arv,
    arv_lo: round(arv * 0.955, 1000),
    arv_hi: round(arv * 1.045, 1000),
    rehab_estimate: rehabMid,
    rent_monthly: rent,
    flip_margin_pct: round(flipMarginPct, 0.1),
    coc_pct: round(cocPct, 0.1),
    cap_rate_pct: round(capRatePct, 0.1),
    net_profit: netProfit,
  };

  const bundle: PropertyBundle = {
    card,
    description:
      `${beds}-bed ${propertyType === "sfr" ? "single-family" : propertyType === "condo" ? "condo" : propertyType === "townhome" ? "townhome" : "small multifamily"} in ${sub.name} on a ${lotSqft ? `${(lotSqft / 43560).toFixed(2)}-acre` : "shared"} lot. ` +
      (condition < 5
        ? "Listing remarks and photo analysis indicate deferred maintenance throughout — priced accordingly, with the discount exceeding modeled rehab cost."
        : condition < 7
          ? "Mostly maintained with selective updates; value-add potential concentrated in kitchen and baths."
          : "Move-in ready condition with recent updates; execution risk is low for a rental hold."),
    lot_sqft: lotSqft ?? 0,
    stories,
    garage_spaces: garage,
    hoa_monthly: hoa,
    taxes_annual: taxes,
    flood_zone: floodZone,
    school_percentile: school,
    walkability: walk,
    crime_index: crime,
    events: events.reverse(),
    score: scoreResult,
    analyses: [flipAnalysis, ltrAnalysis, brrrrAnalysis],
    condition: conditionOut,
    comps_sale: mkComps("sale"),
    comps_rental: mkComps("rental"),
    data_as_of: daysAgoIso(0.13),
  };

  return { card, bundle };
}

/* ------------------------------- Market series & reports ------------------------------- */

function series(rndSeed: number, months: number, start: number, drift: number, vol: number, seasonal = 0): MetricPoint[] {
  const rnd = mulberry32(rndSeed);
  const pts: MetricPoint[] = [];
  let v = start;
  for (let m = months - 1; m >= 0; m--) {
    const season = seasonal * Math.sin(((months - m) / 12) * Math.PI * 2);
    pts.push({ date: monthIso(m), value: round(v * (1 + season), 0.01) });
    v = v * (1 + drift + (rnd() - 0.5) * vol);
  }
  return pts;
}

function buildMarketReport(spec: MarketSpec, cards: PropertyCard[]): MarketReport {
  const seed = spec.id.length * 31 + spec.medianPrice;
  const medianPrice = series(seed + 1, 24, spec.medianPrice * 0.9, 0.0045, 0.012, 0.012);
  const dom = series(seed + 2, 24, spec.domMedian * 1.25, -0.006, 0.05, 0.16);
  const inv = series(seed + 3, 24, spec.inventoryMonths * 1.2, -0.005, 0.04, 0.1);
  const rent = series(seed + 4, 24, spec.medianPrice * spec.rentYieldMo * 0.94, 0.003, 0.008, 0.006);
  const cuts = series(seed + 5, 24, 18, 0.002, 0.06, 0.2);
  const stl = series(seed + 6, 24, 98.4, 0.0003, 0.004, 0.004);

  const last = (s: MetricPoint[]) => s[s.length - 1].value;
  const prev = (s: MetricPoint[]) => s[s.length - 2].value;
  const momPct = (s: MetricPoint[]) => ((last(s) - prev(s)) / prev(s)) * 100;

  const summary: MarketSummary = {
    market_id: spec.id,
    name: spec.name,
    state: spec.state,
    center: spec.center,
    active_listings: cards.length * 34 + round(spec.medianPrice / 1000),
    median_price: round(last(medianPrice), 100),
    median_price_mom_pct: round(momPct(medianPrice), 0.1),
    median_dom: round(last(dom)),
    dom_mom: round(last(dom) - prev(dom)),
    inventory_months: round(last(inv), 0.1),
    inventory_mom_pct: round(momPct(inv), 0.1),
    median_rent: round(last(rent), 5),
    rent_yoy_pct: round(((last(rent) - rent[rent.length - 13].value) / rent[rent.length - 13].value) * 100, 0.1),
    temperature: spec.temperature,
    temperature_label: spec.temperature >= 70 ? "Seller's market" : spec.temperature >= 45 ? "Balanced" : "Buyer's market",
    as_of: daysAgoIso(0.019),
  };

  const rnd = mulberry32(seed + 9);
  const submarkets: SubmarketRow[] = spec.submarkets.map((s) => {
    const subCards = cards.filter((c) => c.zip === s.zip);
    return {
      name: s.name,
      zip: s.zip,
      median_price: round(spec.medianPrice * s.factor, 500),
      yoy_pct: round(2 + s.factor * 2.4 + (rnd() - 0.5) * 3, 0.1),
      median_dom: round(spec.domMedian * (1.35 - s.factor * 0.3)),
      rent_yield_pct: round(spec.rentYieldMo * 12 * 100 * (1.25 - s.factor * 0.28), 0.1),
      avg_score: round(subCards.length ? subCards.reduce((x, c) => x + c.score, 0) / subCards.length : 55 + rnd() * 20),
      active: subCards.length * 27 + round(rnd() * 60),
    };
  });

  const bands = ["<$200k", "$200–300k", "$300–400k", "$400–550k", "$550k+"];
  const price_bands = bands.map((band, i) => ({
    band,
    active: round(40 + rnd() * 320 * (1 - Math.abs(i - 2) * 0.24)),
    median_dom: round(spec.domMedian * (0.7 + i * 0.18)),
    avg_score: round(clamp(74 - i * 6 + rnd() * 8, 30, 90)),
  }));

  return {
    summary,
    series: {
      median_price: medianPrice,
      median_dom: dom,
      inventory_months: inv,
      median_rent: rent,
      price_cuts_pct: cuts,
      sale_to_list_pct: stl,
    },
    submarkets,
    narrative: [
      `${spec.name} is ${summary.temperature_label.toLowerCase()} territory: ${summary.inventory_months} months of inventory against a ${summary.median_dom}-day median DOM. ${summary.median_price_mom_pct >= 0 ? "Prices ticked up" : "Prices eased"} ${Math.abs(summary.median_price_mom_pct).toFixed(1)}% month-over-month.`,
      `Rents are compounding at ${summary.rent_yoy_pct.toFixed(1)}% YoY, ${summary.rent_yoy_pct > 3 ? "outpacing" : "tracking"} price growth — ${summary.rent_yoy_pct > 3 ? "yield-positive for buy-and-hold entries" : "neutral for cash-flow entries"}.`,
      `Spread opportunity is concentrated in ${submarkets.slice().sort((a, b) => b.avg_score - a.avg_score)[0].name} and ${submarkets.slice().sort((a, b) => b.avg_score - a.avg_score)[1].name}, where distress listings price below renovated comps by double digits.`,
    ],
    price_bands,
  };
}

/* ------------------------------- Assembly (module-level, computed once) ------------------------------- */

const PROPS_PER_MARKET = 36;

const generated = MARKET_SPECS.map((spec) => {
  const props = Array.from({ length: PROPS_PER_MARKET }, (_, i) => generateProperty(spec, i));
  return { spec, props };
});

export const ALL_CARDS: PropertyCard[] = generated.flatMap((g) => g.props.map((p) => p.card));
export const BUNDLES: Map<string, PropertyBundle> = new Map(
  generated.flatMap((g) => g.props.map((p) => [p.card.property_id, p.bundle] as const)),
);
export const MARKET_REPORTS: Map<string, MarketReport> = new Map(
  generated.map((g) => [g.spec.id, buildMarketReport(g.spec, g.props.map((p) => p.card))]),
);
export const MARKETS: MarketSummary[] = MARKET_SPECS.map((s) => MARKET_REPORTS.get(s.id)!.summary);

/* ------------------------------- Investment report generation ------------------------------- */

export function buildInvestmentReport(bundle: PropertyBundle): InvestmentReport {
  const { card, score, condition } = bundle;
  const win = bundle.analyses.find((a) => a.strategy === score.winning_strategy) ?? bundle.analyses[0];
  const flip = bundle.analyses.find((a) => a.strategy === "flip")!;
  const ltr = bundle.analyses.find((a) => a.strategy === "ltr")!;
  const marketName = MARKETS.find((m) => m.market_id === card.market_id)?.name ?? "the market";
  const fmtK = (v: number) => `$${Math.round(v / 1000)}k`;

  const verdict =
    score.recommendation === "strong_buy"
      ? `A ${score.grade} ${STRAT_NAME[score.winning_strategy]} candidate — ${fmtK(card.list_price)} entry against a ${fmtK(card.arv ?? 0)} ARV with ${condition.condition_label.toLowerCase()} scope. The margin survives conservative assumptions.`
      : score.recommendation === "buy"
        ? `A credible ${STRAT_NAME[score.winning_strategy]} at asking, and a strong one ${fmtK(card.list_price * 0.05)} below. Execution risk is ${score.risk_score < 40 ? "modest" : "meaningful"} but priced.`
        : score.recommendation === "hold_watch"
          ? `The spread doesn't clear our floor at today's price. One more cut — or a rehab bid under ${fmtK(condition.rehab_total_lo)} — changes the answer. Worth watching.`
          : `The numbers don't work: ${score.explanation.negatives[0] ?? "margin below floor"}. Pass unless the basis changes materially.`;

  return {
    report_id: `rpt-${card.property_id}`,
    property_id: card.property_id,
    generated_at: daysAgoIso(0.05),
    model_version: "report-2026.07 · claude-sonnet-5",
    verdict,
    verdict_recommendation: score.recommendation,
    fact_check_passed: true,
    sections: [
      {
        key: "summary",
        title: "Executive summary",
        confidence: score.confidence_score,
        paragraphs: [
          `${card.line1} is a ${card.beds}-bed, ${card.baths}-bath ${card.sqft.toLocaleString()} sqft ${card.year_built} build in ${card.city}, listed at ${fmtK(card.list_price)} after ${card.dom} days on market. The engine underwrites it best as a ${STRAT_NAME[score.winning_strategy]} at ${Math.round(score.overall_score)}/100 (${score.grade}).`,
        ],
        bullets: score.explanation.positives.slice(0, 3),
      },
      {
        key: "deal",
        title: "The deal structure",
        confidence: clamp(score.confidence_score + 4, 0, 97),
        paragraphs: [
          `Acquisition at ${fmtK(win.purchase_price)} plus ${fmtK(win.closing_costs)} closing and a ${fmtK(win.rehab_budget)} rehab budget puts all-in basis at ${fmtK(win.all_in)} — ${Math.round((win.all_in / win.arv) * 100)}% of the ${fmtK(win.arv)} ARV midpoint (band ${fmtK(win.arv_lo)}–${fmtK(win.arv_hi)}).`,
          score.winning_strategy === "flip"
            ? `Modeled exit nets ${fmtK(flip.net_profit ?? 0)} over a ${flip.hold_months}-month hold — a ${flip.flip_margin_pct?.toFixed(1)}% margin on ARV, annualizing to ${flip.roi_annualized_pct?.toFixed(0)}% on cash deployed.`
            : `At ${fmtK(ltr.rent_monthly ?? 0)}/mo rent (band ${fmtK(ltr.rent_lo ?? 0)}–${fmtK(ltr.rent_hi ?? 0)}), the property carries a ${ltr.dscr?.toFixed(2)}x DSCR and returns ${ltr.coc_pct?.toFixed(1)}% cash-on-cash after ${fmtK(ltr.cash_invested)} invested.`,
        ],
      },
      {
        key: "condition",
        title: "Condition & rehab plan",
        confidence: condition.confidence,
        paragraphs: [
          `Vision analysis of ${condition.analyzed_photos} of ${condition.total_photos} listing photos rates overall condition ${condition.overall_condition}/10 (${condition.condition_label.toLowerCase()}). The rehab estimate bands ${fmtK(condition.rehab_total_lo)}–${fmtK(condition.rehab_total_hi)} across ${condition.rehab.length} line items.`,
          condition.red_flags.length
            ? `Flagged for verification: ${condition.red_flags.map((f) => f.label.toLowerCase()).join("; ")}. These are photo-inferred and inspection-gated — treat the high band as the planning number.`
            : `No structural red flags surfaced in the photo set. Scope is concentrated in finishes, which compresses both timeline and variance.`,
        ],
      },
      {
        key: "market",
        title: "Market context",
        confidence: 88,
        paragraphs: MARKET_REPORTS.get(card.market_id)?.narrative.slice(0, 2) ?? [
          `${marketName} fundamentals support the exit assumptions used above.`,
        ],
      },
      {
        key: "risks",
        title: "Risks & mitigations",
        confidence: clamp(100 - score.risk_score, 20, 95),
        paragraphs: [
          `Composite risk scores ${Math.round(score.risk_score)}/100. ${score.explanation.negatives[0] ?? ""}`,
        ],
        bullets: [
          ...score.explanation.negatives,
          ...score.explanation.caps,
          `Comp set: ${bundle.comps_sale.filter((c) => c.status === "included").length} sales within 1 mi; ARV confidence ${Math.round(win.arv_confidence)}%`,
        ],
      },
    ],
  };
}

const STRAT_NAME: Record<Strategy, string> = {
  flip: "fix-and-flip",
  ltr: "long-term rental",
  brrrr: "BRRRR",
  str: "short-term rental",
  house_hack: "house hack",
  wholesale: "wholesale",
  multifamily: "multifamily",
  land: "land",
  commercial: "commercial",
  value_add: "value-add",
  overall: "best-strategy",
};
