/**
 * Read-model types for the web app. Field names mirror the platform's Pydantic contracts
 * (search.PropertyCard, scoring.ScoreResult, engine.EngineOutputBlock, markets.MarketReportOut,
 * vision.PropertyConditionOut, reports.ReportOut) so swapping the mock provider for the
 * generated OpenAPI client is a type-compatible change, not a rewrite.
 */

import type {
  Strategy,
  Recommendation,
  PropertyType,
  ListingStatus,
  ConfidenceLevel,
} from "./domain";

/* ---------------- Search / cards ---------------- */

export interface PropertyCard {
  property_id: string;
  listing_id: string | null;
  line1: string;
  city: string;
  state: string;
  zip: string;
  lat: number;
  lon: number;
  property_type: PropertyType;
  beds: number;
  baths: number;
  sqft: number;
  lot_sqft: number | null;
  year_built: number;
  list_price: number;
  status: ListingStatus;
  dom: number;
  list_date: string;
  price_per_sqft: number;
  photo_count: number;
  score: number;
  grade: string;
  risk_score: number;
  confidence_score: number;
  recommendation: Recommendation;
  scored_strategy: Strategy;
  /** Dashboard movement badges (FR: "new / price cut / score ↑"). */
  movement: Array<"new" | "price_cut" | "score_up" | "back_on_market">;
  market_id: string;
  /* Headline economics for cards — from the winning strategy's stored analysis. */
  arv: number | null;
  arv_lo: number | null;
  arv_hi: number | null;
  rehab_estimate: number | null;
  rent_monthly: number | null;
  flip_margin_pct: number | null;
  coc_pct: number | null;
  cap_rate_pct: number | null;
  net_profit: number | null;
}

/* ---------------- Scoring ---------------- */

export interface FactorLedgerRow {
  factor_key: string;
  label: string;
  group: "F" | "D" | "C" | "L" | "M";
  raw_display: string;
  normalized: number;
  weight: number;
  contribution: number;
  rationale: string | null;
  capped_by: string | null;
}

export interface StrategyScore {
  strategy: Strategy;
  score: number;
  raw_score: number;
  capped_by: string | null;
  factors: FactorLedgerRow[];
}

export interface CategoryScores {
  profitability: number;
  risk: number;
  location: number;
  condition: number;
  appreciation: number;
  rental_strength: number;
  liquidity: number;
  renovation_complexity: number;
  financing_difficulty: number;
  market_conditions: number;
}

export interface ScoreResult {
  property_id: string;
  scoring_version: string;
  overall_score: number;
  grade: string;
  winning_strategy: Strategy;
  risk_score: number;
  confidence_score: number;
  recommendation: Recommendation;
  category_scores: CategoryScores;
  strategy_scores: StrategyScore[];
  explanation: { positives: string[]; negatives: string[]; caps: string[] };
  as_of: string;
}

/* ---------------- Engine / financials ---------------- */

export interface CalcLine {
  key: string;
  label: string;
  amount: number;
  note?: string;
}

export interface StrategyAnalysis {
  strategy: Strategy;
  /* Acquisition */
  purchase_price: number;
  closing_costs: number;
  rehab_budget: number;
  all_in: number;
  cash_invested: number;
  /* Value */
  arv: number;
  arv_lo: number;
  arv_hi: number;
  arv_confidence: number;
  /* Income (rental strategies) */
  rent_monthly: number | null;
  rent_lo: number | null;
  rent_hi: number | null;
  rent_confidence: number | null;
  expenses_monthly: CalcLine[];
  noi_annual: number | null;
  cash_flow_monthly: number | null;
  /* Returns */
  coc_pct: number | null;
  cap_rate_pct: number | null;
  dscr: number | null;
  flip_margin_pct: number | null;
  net_profit: number | null;
  roi_annualized_pct: number | null;
  capital_left_in: number | null;
  breakeven_occupancy_pct: number | null;
  hold_months: number;
  /* Waterfall for the analyzer */
  waterfall: CalcLine[];
}

/* ---------------- Condition / vision ---------------- */

export interface RehabLineItem {
  area: string;
  scope: string;
  cost_lo: number;
  cost_hi: number;
  confidence: ConfidenceLevel;
  source_note: string;
}

export interface RedFlag {
  label: string;
  severity: "low" | "medium" | "high";
  note: string;
}

export interface PropertyCondition {
  property_id: string;
  overall_condition: number; // 1–10
  condition_label: string;
  analyzed_photos: number;
  total_photos: number;
  areas: Array<{ area: string; score: number; note: string }>;
  red_flags: RedFlag[];
  rehab: RehabLineItem[];
  rehab_total_lo: number;
  rehab_total_hi: number;
  renovation_difficulty: number; // 1–5
  confidence: number;
  model_version: string;
  as_of: string;
}

/* ---------------- History & comps ---------------- */

export interface ListingEvent {
  date: string;
  type: "listed" | "price_change" | "status_change" | "back_on_market" | "score_change";
  label: string;
  detail: string | null;
  price: number | null;
  delta: number | null;
}

export interface Comp {
  comp_id: string;
  line1: string;
  distance_mi: number;
  similarity: number;
  sold_date: string;
  sold_price: number;
  beds: number;
  baths: number;
  sqft: number;
  year_built: number;
  price_per_sqft: number;
  adjusted_price: number;
  adjustments: Array<{ label: string; amount: number }>;
  rationale: string;
  kind: "sale" | "rental";
  status: "included" | "excluded";
}

/* ---------------- Property bundle (S12 read model) ---------------- */

export interface PropertyBundle {
  card: PropertyCard;
  description: string;
  lot_sqft: number;
  stories: number;
  garage_spaces: number;
  hoa_monthly: number | null;
  taxes_annual: number;
  flood_zone: string;
  school_percentile: number;
  walkability: number;
  crime_index: number;
  events: ListingEvent[];
  score: ScoreResult;
  analyses: StrategyAnalysis[];
  condition: PropertyCondition;
  comps_sale: Comp[];
  comps_rental: Comp[];
  data_as_of: string;
}

/* ---------------- Markets ---------------- */

export interface MetricPoint {
  date: string;
  value: number;
}

export interface MarketSummary {
  market_id: string;
  name: string;
  state: string;
  center: { lat: number; lon: number };
  active_listings: number;
  median_price: number;
  median_price_mom_pct: number;
  median_dom: number;
  dom_mom: number;
  inventory_months: number;
  inventory_mom_pct: number;
  median_rent: number;
  rent_yoy_pct: number;
  temperature: number; // 0 cold – 100 hot
  temperature_label: string;
  as_of: string;
}

export interface SubmarketRow {
  name: string;
  zip: string;
  median_price: number;
  yoy_pct: number;
  median_dom: number;
  rent_yield_pct: number;
  avg_score: number;
  active: number;
}

export interface MarketReport {
  summary: MarketSummary;
  series: {
    median_price: MetricPoint[];
    median_dom: MetricPoint[];
    inventory_months: MetricPoint[];
    median_rent: MetricPoint[];
    price_cuts_pct: MetricPoint[];
    sale_to_list_pct: MetricPoint[];
  };
  submarkets: SubmarketRow[];
  narrative: string[];
  price_bands: Array<{ band: string; active: number; median_dom: number; avg_score: number }>;
}

/* ---------------- Reports (S14) ---------------- */

export interface ReportSection {
  key: string;
  title: string;
  paragraphs: string[];
  confidence: number;
  bullets?: string[];
}

export interface InvestmentReport {
  report_id: string;
  property_id: string;
  generated_at: string;
  model_version: string;
  verdict: string;
  verdict_recommendation: Recommendation;
  sections: ReportSection[];
  fact_check_passed: boolean;
}

/* ---------------- Watchlist / alerts ---------------- */

export interface WatchlistItem {
  property_id: string;
  added_at: string;
  note: string | null;
  events: ListingEvent[];
}

export interface AlertItem {
  id: string;
  created_at: string;
  read: boolean;
  kind: "new_match" | "price_cut" | "score_change" | "status_change" | "digest" | "system";
  title: string;
  body: string;
  property_id: string | null;
  buy_box: string | null;
  market_id: string;
}

/* ---------------- Portfolio (S21) ---------------- */

export interface PortfolioHolding {
  property_id: string;
  nickname: string;
  acquired_at: string;
  strategy: Strategy;
  purchase_price: number;
  rehab_actual: number;
  current_value: number;
  value_series: MetricPoint[];
  loan_balance: number;
  rate_pct: number;
  rent_actual: number | null;
  rent_underwritten: number | null;
  cash_flow_actual: number | null;
  cash_flow_underwritten: number | null;
  equity: number;
  coc_actual_pct: number | null;
  flags: Array<{ kind: "refi" | "sell" | "rent_below_market"; label: string }>;
}

/* ---------------- Account / team ---------------- */

export interface TeamMember {
  user_id: string;
  name: string;
  email: string;
  role: "owner" | "admin" | "analyst" | "viewer";
  status: "active" | "invited";
  last_active: string | null;
}

export interface ApiKeyRow {
  key_id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used: string | null;
}

export interface CurrentUser {
  user_id: string;
  name: string;
  email: string;
  org: string;
  plan: "basic" | "pro" | "team";
  seats_used: number;
  seats_total: number;
  markets_used: number;
  markets_total: number;
  reports_used: number;
  reports_total: number;
  renews_at: string;
}
