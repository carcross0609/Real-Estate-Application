/**
 * Domain vocabulary shared by every surface — mirrors services/platform enums and the
 * scoring module's grade bands (scoring/weights.py GRADE_THRESHOLDS) exactly, so a grade
 * rendered here always matches what the API returns.
 */

export type Strategy =
  | "flip"
  | "ltr"
  | "brrrr"
  | "str"
  | "house_hack"
  | "wholesale"
  | "multifamily"
  | "land"
  | "commercial"
  | "value_add"
  | "overall";

export type Recommendation = "strong_buy" | "buy" | "hold_watch" | "pass";

export type PropertyType =
  | "sfr"
  | "condo"
  | "townhome"
  | "mf_2_4"
  | "mf_5plus"
  | "land"
  | "commercial"
  | "mixed";

export type ListingStatus =
  | "active"
  | "pending"
  | "contingent"
  | "sold"
  | "withdrawn"
  | "expired"
  | "coming_soon";

export const STRATEGY_LABEL: Record<Strategy, string> = {
  flip: "Fix & Flip",
  ltr: "Long-Term Rental",
  brrrr: "BRRRR",
  str: "Short-Term Rental",
  house_hack: "House Hack",
  wholesale: "Wholesale",
  multifamily: "Multifamily",
  land: "Land",
  commercial: "Commercial",
  value_add: "Value-Add",
  overall: "Best Strategy",
};

export const STRATEGY_SHORT: Record<Strategy, string> = {
  flip: "Flip",
  ltr: "LTR",
  brrrr: "BRRRR",
  str: "STR",
  house_hack: "Hack",
  wholesale: "Whsl",
  multifamily: "MF",
  land: "Land",
  commercial: "Com",
  value_add: "V-Add",
  overall: "Best",
};

export const RECOMMENDATION_LABEL: Record<Recommendation, string> = {
  strong_buy: "Strong Buy",
  buy: "Buy",
  hold_watch: "Hold / Watch",
  pass: "Pass",
};

export const PROPERTY_TYPE_LABEL: Record<PropertyType, string> = {
  sfr: "Single Family",
  condo: "Condo",
  townhome: "Townhome",
  mf_2_4: "Multifamily 2–4",
  mf_5plus: "Multifamily 5+",
  land: "Land",
  commercial: "Commercial",
  mixed: "Mixed Use",
};

export const STATUS_LABEL: Record<ListingStatus, string> = {
  active: "Active",
  pending: "Pending",
  contingent: "Contingent",
  sold: "Sold",
  withdrawn: "Withdrawn",
  expired: "Expired",
  coming_soon: "Coming Soon",
};

/* --- Grades — bands mirror scoring/weights.py GRADE_THRESHOLDS ------------------- */

const GRADE_THRESHOLDS: [number, string][] = [
  [95, "A+"],
  [90, "A"],
  [85, "A-"],
  [75, "B+"],
  [65, "B"],
  [55, "B-"],
  [45, "C+"],
  [35, "C"],
  [25, "C-"],
  [10, "D"],
];

export function gradeFor(score: number): string {
  for (const [threshold, grade] of GRADE_THRESHOLDS) {
    if (score >= threshold) return grade;
  }
  return "F";
}

export type GradeFamily = "a" | "b" | "c" | "d" | "f";

/** "A-" → "a"; the color family. Letter + color always travel together. */
export function gradeFamily(grade: string | null | undefined): GradeFamily {
  const letter = (grade ?? "f").charAt(0).toLowerCase();
  return (["a", "b", "c", "d", "f"].includes(letter) ? letter : "f") as GradeFamily;
}

export const GRADE_VAR: Record<GradeFamily, string> = {
  a: "var(--grade-a)",
  b: "var(--grade-b)",
  c: "var(--grade-c)",
  d: "var(--grade-d)",
  f: "var(--grade-f)",
};

export const GRADE_SOFT_VAR: Record<GradeFamily, string> = {
  a: "var(--grade-a-soft)",
  b: "var(--grade-b-soft)",
  c: "var(--grade-c-soft)",
  d: "var(--grade-d-soft)",
  f: "var(--grade-f-soft)",
};

export function scoreColor(score: number): string {
  return GRADE_VAR[gradeFamily(gradeFor(score))];
}

/* --- Confidence (§21 #5 — visible, neutral; never the quality ramp) --------------- */

export type ConfidenceLevel = "high" | "medium" | "low";

export function confidenceLevel(v: number | null | undefined): ConfidenceLevel {
  if (v == null) return "low";
  if (v >= 75) return "high";
  if (v >= 50) return "medium";
  return "low";
}

export const CONFIDENCE_LABEL: Record<ConfidenceLevel, string> = {
  high: "High confidence",
  medium: "Med confidence",
  low: "Low confidence",
};
