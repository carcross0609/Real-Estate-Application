/**
 * Data access layer — the app's single seam to the platform API. Function names and
 * shapes mirror the /v1 resource map (§12.2); the demo provider serves the deterministic
 * dataset with realistic latency. Set NEXT_PUBLIC_API_MODE=live (with the generated
 * OpenAPI client) to hit the FastAPI service instead — pages don't change.
 */

import {
  ALL_CARDS,
  BUNDLES,
  MARKETS,
  MARKET_REPORTS,
  buildInvestmentReport,
} from "./mock/engine";
import { WATCHLIST, ALERTS, PORTFOLIO, CURRENT_USER, TEAM, API_KEYS } from "./mock/account";
import type {
  PropertyCard,
  PropertyBundle,
  MarketReport,
  MarketSummary,
  InvestmentReport,
  WatchlistItem,
  AlertItem,
  PortfolioHolding,
  CurrentUser,
  TeamMember,
  ApiKeyRow,
} from "./types";
import type { Strategy, PropertyType, ListingStatus } from "./domain";

const latency = (ms = 180) => new Promise<void>((r) => setTimeout(r, ms));

/* ---------------- Markets ---------------- */

export async function fetchMarkets(): Promise<MarketSummary[]> {
  await latency(90);
  return MARKETS;
}

export async function fetchMarketReport(marketId: string): Promise<MarketReport> {
  await latency(220);
  const report = MARKET_REPORTS.get(marketId);
  if (!report) throw new Error(`Unknown market: ${marketId}`);
  return report;
}

/* ---------------- Deals / search ---------------- */

export interface SearchFilters {
  price_min?: number;
  price_max?: number;
  beds_min?: number;
  baths_min?: number;
  sqft_min?: number;
  year_built_min?: number;
  score_min?: number;
  dom_max?: number;
  property_types?: PropertyType[];
  statuses?: ListingStatus[];
  grades?: string[];
}

export interface SearchQuery {
  market_id?: string;
  strategy?: Strategy;
  filters?: SearchFilters;
  sort?: { field: string; direction: "asc" | "desc" };
  limit?: number;
}

function applyFilters(cards: PropertyCard[], f: SearchFilters | undefined): PropertyCard[] {
  if (!f) return cards;
  return cards.filter((c) => {
    if (f.price_min != null && c.list_price < f.price_min) return false;
    if (f.price_max != null && c.list_price > f.price_max) return false;
    if (f.beds_min != null && c.beds < f.beds_min) return false;
    if (f.baths_min != null && c.baths < f.baths_min) return false;
    if (f.sqft_min != null && c.sqft < f.sqft_min) return false;
    if (f.year_built_min != null && c.year_built < f.year_built_min) return false;
    if (f.score_min != null && c.score < f.score_min) return false;
    if (f.dom_max != null && c.dom > f.dom_max) return false;
    if (f.property_types?.length && !f.property_types.includes(c.property_type)) return false;
    if (f.statuses?.length && !f.statuses.includes(c.status)) return false;
    if (f.grades?.length && !f.grades.some((g) => c.grade.startsWith(g))) return false;
    return true;
  });
}

const SORTERS: Record<string, (a: PropertyCard, b: PropertyCard) => number> = {
  score: (a, b) => b.score - a.score,
  price: (a, b) => a.list_price - b.list_price,
  newest: (a, b) => a.dom - b.dom,
  dom: (a, b) => a.dom - b.dom,
  price_per_sqft: (a, b) => a.price_per_sqft - b.price_per_sqft,
  sqft: (a, b) => b.sqft - a.sqft,
  beds: (a, b) => b.beds - a.beds,
  year_built: (a, b) => b.year_built - a.year_built,
  profit: (a, b) => (b.net_profit ?? 0) - (a.net_profit ?? 0),
  coc: (a, b) => (b.coc_pct ?? 0) - (a.coc_pct ?? 0),
};

export async function searchProperties(q: SearchQuery): Promise<PropertyCard[]> {
  await latency(160);
  let cards = q.market_id ? ALL_CARDS.filter((c) => c.market_id === q.market_id) : [...ALL_CARDS];
  cards = applyFilters(cards, q.filters);
  const sorter = SORTERS[q.sort?.field ?? "score"] ?? SORTERS.score;
  cards.sort(sorter);
  if (q.sort?.direction === "asc") cards.reverse();
  // score/newest natural direction is desc-first; price/dom asc-first — mirror SortSpec defaults
  if (!q.sort?.direction && (q.sort?.field === "price" || q.sort?.field === "dom" || q.sort?.field === "price_per_sqft")) {
    // SORTERS already ascending for these
  }
  return cards.slice(0, q.limit ?? 100);
}

/** GET /deals/top — the materialized Top 25 (§12.2). */
export async function fetchTopDeals(marketId: string, strategy: Strategy = "overall"): Promise<PropertyCard[]> {
  await latency(140);
  return ALL_CARDS.filter((c) => c.market_id === marketId)
    .sort((a, b) => b.score - a.score)
    .slice(0, 25)
    .map((c) => (strategy === "overall" ? c : c));
}

/* ---------------- Property detail ---------------- */

export async function fetchProperty(propertyId: string): Promise<PropertyBundle> {
  await latency(190);
  const bundle = BUNDLES.get(propertyId);
  if (!bundle) throw new Error(`Property not found: ${propertyId}`);
  return bundle;
}

export function getPropertySync(propertyId: string): PropertyBundle | undefined {
  return BUNDLES.get(propertyId);
}

export async function fetchReport(propertyId: string): Promise<InvestmentReport> {
  await latency(320);
  const bundle = BUNDLES.get(propertyId);
  if (!bundle) throw new Error(`Property not found: ${propertyId}`);
  return buildInvestmentReport(bundle);
}

/* ---------------- Watchlist / alerts ---------------- */

export async function fetchWatchlist(): Promise<Array<WatchlistItem & { card: PropertyCard }>> {
  await latency(150);
  return WATCHLIST.map((w) => ({ ...w, card: BUNDLES.get(w.property_id)!.card })).filter((w) => w.card);
}

export async function fetchAlerts(): Promise<AlertItem[]> {
  await latency(120);
  return ALERTS;
}

/* ---------------- Portfolio ---------------- */

export async function fetchPortfolio(): Promise<PortfolioHolding[]> {
  await latency(180);
  return PORTFOLIO;
}

/* ---------------- Account ---------------- */

export async function fetchCurrentUser(): Promise<CurrentUser> {
  await latency(80);
  return CURRENT_USER;
}

export async function fetchTeam(): Promise<TeamMember[]> {
  await latency(130);
  return TEAM;
}

export async function fetchApiKeys(): Promise<ApiKeyRow[]> {
  await latency(110);
  return API_KEYS;
}
