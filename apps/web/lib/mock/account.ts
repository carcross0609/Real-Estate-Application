/** Account-scope demo data: watchlist, alerts, portfolio, team, current user. */

import { ALL_CARDS, BUNDLES, mulberry32 } from "./engine";
import type {
  AlertItem,
  CurrentUser,
  MetricPoint,
  PortfolioHolding,
  TeamMember,
  ApiKeyRow,
  WatchlistItem,
} from "../types";

const ANCHOR = Date.UTC(2026, 6, 15, 12, 0, 0);
const daysAgoIso = (d: number) => new Date(ANCHOR - d * 86_400_000).toISOString();
const round = (v: number, s = 1) => Math.round(v / s) * s;

/* ---- Watchlist: strong-scoring properties across markets ---- */

const watchPicks = [...ALL_CARDS]
  .sort((a, b) => b.score - a.score)
  .filter((c, i) => c.score < 88 && i % 2 === 0)
  .slice(0, 8);

export const WATCHLIST: WatchlistItem[] = watchPicks.map((c, i) => ({
  property_id: c.property_id,
  added_at: daysAgoIso(3 + i * 4),
  note:
    i === 0
      ? "Waiting on one more cut — offer at $12k under current ask."
      : i === 2
        ? "GC walkthrough scheduled Friday. Verify roof age."
        : i === 4
          ? "Lender pre-approval expires Aug 30."
          : null,
  events: BUNDLES.get(c.property_id)?.events.slice(0, 3) ?? [],
}));

/* ---- Alerts inbox ---- */

const ranked = [...ALL_CARDS].sort((a, b) => b.score - a.score);
/** Always at least 20 entries — indexes below stay valid regardless of score distribution. */
const alertPicks = ranked.slice(0, 20);

export const ALERTS: AlertItem[] = [
  {
    id: "al-01",
    created_at: daysAgoIso(0.04),
    read: false,
    kind: "new_match",
    title: `New match · ${alertPicks[0]?.line1}`,
    body: `Scores ${Math.round(alertPicks[0]?.score ?? 0)} (${alertPicks[0]?.grade}) for ${alertPicks[0]?.scored_strategy.toUpperCase()} — matched "Eastside BRRRR" 14 min after listing.`,
    property_id: alertPicks[0]?.property_id ?? null,
    buy_box: "Eastside BRRRR",
    market_id: alertPicks[0]?.market_id ?? "mkt-columbus",
  },
  {
    id: "al-02",
    created_at: daysAgoIso(0.2),
    read: false,
    kind: "price_cut",
    title: `Price cut · ${alertPicks[3]?.line1}`,
    body: `Cut $${round(((alertPicks[3]?.list_price ?? 0) * 0.035) / 1000)}k — flip spread now clears floor. Score moved ${Math.round((alertPicks[3]?.score ?? 0) - 6)} → ${Math.round(alertPicks[3]?.score ?? 0)}.`,
    property_id: alertPicks[3]?.property_id ?? null,
    buy_box: "Sub-300 flips",
    market_id: alertPicks[3]?.market_id ?? "mkt-columbus",
  },
  {
    id: "al-03",
    created_at: daysAgoIso(0.6),
    read: false,
    kind: "score_change",
    title: `Score upgraded · ${alertPicks[5]?.line1}`,
    body: "Comps refresh raised ARV 2.4%; grade moved B+ → A-. You watch this property.",
    property_id: alertPicks[5]?.property_id ?? null,
    buy_box: null,
    market_id: alertPicks[5]?.market_id ?? "mkt-tampa",
  },
  {
    id: "al-04",
    created_at: daysAgoIso(1.1),
    read: true,
    kind: "new_match",
    title: `New match · ${alertPicks[7]?.line1}`,
    body: `Scores ${Math.round(alertPicks[7]?.score ?? 0)} (${alertPicks[7]?.grade}) — matched "Tampa cash-flow" with rent band $${round((alertPicks[7]?.rent_monthly ?? 0) * 0.93, 5)}–$${round((alertPicks[7]?.rent_monthly ?? 0) * 1.07, 5)}.`,
    property_id: alertPicks[7]?.property_id ?? null,
    buy_box: "Tampa cash-flow",
    market_id: alertPicks[7]?.market_id ?? "mkt-tampa",
  },
  {
    id: "al-05",
    created_at: daysAgoIso(1.4),
    read: true,
    kind: "digest",
    title: "Daily digest · 6 new matches",
    body: "4 more matches across your buy boxes folded into this digest (rate cap). Top unseen: two A- flips in Hilltop.",
    property_id: null,
    buy_box: null,
    market_id: "mkt-columbus",
  },
  {
    id: "al-06",
    created_at: daysAgoIso(2.2),
    read: true,
    kind: "status_change",
    title: `Went pending · ${alertPicks[9]?.line1}`,
    body: "Watched property accepted an offer after 41 days. Comparable inventory: 3 active matches remain.",
    property_id: alertPicks[9]?.property_id ?? null,
    buy_box: null,
    market_id: alertPicks[9]?.market_id ?? "mkt-phoenix",
  },
  {
    id: "al-07",
    created_at: daysAgoIso(3.1),
    read: true,
    kind: "system",
    title: "Phoenix feed recovered",
    body: "Listings delayed 2h 14m on Jul 12 have backfilled in order. No alerts fired on stale data.",
    property_id: null,
    buy_box: null,
    market_id: "mkt-phoenix",
  },
  {
    id: "al-08",
    created_at: daysAgoIso(3.8),
    read: true,
    kind: "price_cut",
    title: `Price cut · ${alertPicks[11]?.line1}`,
    body: `Second cut in 3 weeks (−$${round(((alertPicks[11]?.list_price ?? 0) * 0.07) / 1000)}k total). Seller motivation signals elevated.`,
    property_id: alertPicks[11]?.property_id ?? null,
    buy_box: "Sub-300 flips",
    market_id: alertPicks[11]?.market_id ?? "mkt-columbus",
  },
];

/* ---- Portfolio ---- */

function valueSeries(seed: number, start: number, quarters: number, drift: number): MetricPoint[] {
  const rnd = mulberry32(seed);
  const pts: MetricPoint[] = [];
  let v = start;
  for (let q = quarters - 1; q >= 0; q--) {
    const d = new Date(ANCHOR);
    d.setUTCMonth(d.getUTCMonth() - q * 3, 1);
    pts.push({ date: d.toISOString().slice(0, 10), value: round(v, 500) });
    v *= 1 + drift + (rnd() - 0.45) * 0.02;
  }
  return pts;
}

const PORTFOLIO_SPECS = [
  { nick: "The Duplex", strat: "brrrr" as const, price: 218_000, rehab: 46_000, apprec: 0.021, rent: 2350, rentUw: 2200, rate: 6.4, quarters: 11 },
  { nick: "Maple St Flip → Hold", strat: "ltr" as const, price: 176_500, rehab: 38_000, apprec: 0.018, rent: 1795, rentUw: 1850, rate: 7.1, quarters: 8 },
  { nick: "Riverview SFR", strat: "ltr" as const, price: 342_000, rehab: 12_500, apprec: 0.014, rent: 2540, rentUw: 2450, rate: 6.9, quarters: 6 },
  { nick: "Glendale 4-plex", strat: "multifamily" as const, price: 512_000, rehab: 61_000, apprec: 0.016, rent: 4980, rentUw: 5100, rate: 6.2, quarters: 13 },
  { nick: "Whitehall Starter", strat: "ltr" as const, price: 148_000, rehab: 21_500, apprec: 0.024, rent: 1425, rentUw: 1300, rate: 5.9, quarters: 15 },
];

export const PORTFOLIO: PortfolioHolding[] = PORTFOLIO_SPECS.map((s, i) => {
  const basis = s.price + s.rehab;
  const vs = valueSeries(900 + i, basis * 1.04, s.quarters, s.apprec);
  const current = vs[vs.length - 1].value;
  const loan = round(s.price * 0.75 * (1 - 0.012 * s.quarters), 100);
  const mRate = s.rate / 100 / 12;
  const pAndI = (loan * mRate) / (1 - Math.pow(1 + mRate, -360));
  const opex = s.rent * 0.38;
  const cf = round(s.rent - opex - pAndI);
  const cfUw = round(s.rentUw - s.rentUw * 0.38 - pAndI);
  const cashIn = round(s.price * 0.25 + s.rehab, 100);
  const flags: PortfolioHolding["flags"] = [];
  if (current > loan * 1.9) flags.push({ kind: "refi", label: `Refi window: ~$${Math.round((current * 0.75 - loan) / 1000)}k extractable at 75% LTV` });
  if (s.rent < s.rentUw) flags.push({ kind: "rent_below_market", label: `Rent trails underwrite by $${s.rentUw - s.rent}/mo` });
  if (s.apprec < 0.015 && cf < 150) flags.push({ kind: "sell", label: "Thin cash flow + slowing appreciation — evaluate exit" });
  return {
    property_id: `port-${i + 1}`,
    nickname: s.nick,
    acquired_at: new Date(ANCHOR - s.quarters * 91.3 * 86_400_000).toISOString().slice(0, 10),
    strategy: s.strat,
    purchase_price: s.price,
    rehab_actual: s.rehab,
    current_value: current,
    value_series: vs,
    loan_balance: loan,
    rate_pct: s.rate,
    rent_actual: s.rent,
    rent_underwritten: s.rentUw,
    cash_flow_actual: cf,
    cash_flow_underwritten: cfUw,
    equity: round(current - loan, 100),
    coc_actual_pct: Math.round(((cf * 12) / cashIn) * 1000) / 10,
    flags,
  };
});

/* ---- Team & user ---- */

export const CURRENT_USER: CurrentUser = {
  user_id: "usr-carson",
  name: "Carson Crossno",
  email: "carcross0609@gmail.com",
  org: "Crossno Capital",
  plan: "pro",
  seats_used: 4,
  seats_total: 5,
  markets_used: 3,
  markets_total: 5,
  reports_used: 38,
  reports_total: 100,
  renews_at: "2026-08-02",
};

export const TEAM: TeamMember[] = [
  { user_id: "usr-carson", name: "Carson Crossno", email: "carcross0609@gmail.com", role: "owner", status: "active", last_active: daysAgoIso(0.01) },
  { user_id: "usr-priya", name: "Priya Raman", email: "priya@crossnocapital.com", role: "admin", status: "active", last_active: daysAgoIso(0.3) },
  { user_id: "usr-marcus", name: "Marcus Webb", email: "marcus@crossnocapital.com", role: "analyst", status: "active", last_active: daysAgoIso(1.6) },
  { user_id: "usr-elena", name: "Elena Ortiz", email: "elena.ortiz@crossnocapital.com", role: "analyst", status: "active", last_active: daysAgoIso(4.2) },
  { user_id: "usr-invite", name: "Dan Kessler", email: "dan.kessler@gmail.com", role: "viewer", status: "invited", last_active: null },
];

export const API_KEYS: ApiKeyRow[] = [
  { key_id: "key-1", name: "Underwriting sheet sync", prefix: "dl_live_4f8a", created_at: daysAgoIso(64), last_used: daysAgoIso(0.4) },
  { key_id: "key-2", name: "Zapier — pipeline export", prefix: "dl_live_9c2e", created_at: daysAgoIso(122), last_used: daysAgoIso(11) },
];
