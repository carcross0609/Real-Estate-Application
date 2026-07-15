/**
 * Formatting discipline (PRD §21 #5): never false precision. Dollar estimates render
 * compact ("$237k") or as intervals ("$230k–$245k"); exact figures only for user-entered
 * or contractual numbers. All output is designed for tabular mono rendering.
 */

const USD = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

/** "$1,234,567" — exact. For user-entered / contractual values. */
export function money(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  return USD.format(v);
}

/** "$1.24M" / "$487k" / "$950" — compact. For estimates and dense tables. */
export function moneyCompact(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  const sign = v < 0 ? "-" : "";
  const a = Math.abs(v);
  if (a >= 1_000_000) return `${sign}$${(a / 1_000_000).toFixed(2).replace(/\.?0+$/, "")}M`;
  if (a >= 10_000) return `${sign}$${Math.round(a / 1_000)}k`;
  if (a >= 1_000) return `${sign}$${(a / 1_000).toFixed(1).replace(/\.0$/, "")}k`;
  return `${sign}$${Math.round(a)}`;
}

/** "$230k – $245k" — interval band for estimates with uncertainty. */
export function moneyRange(lo: number | null | undefined, hi: number | null | undefined): string {
  if (lo == null || hi == null) return "—";
  return `${moneyCompact(lo)}–${moneyCompact(hi)}`;
}

/** "12.4%" */
export function pct(v: number | null | undefined, digits = 1): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${v.toFixed(digits)}%`;
}

/** "+12.4%" / "-3.1%" — signed delta. */
export function pctSigned(v: number | null | undefined, digits = 1): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

/** "1,234" */
export function num(v: number | null | undefined, digits = 0): string {
  if (v == null || Number.isNaN(v)) return "—";
  return v.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** "1.24x" — ratios (DSCR etc.) */
export function ratio(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${v.toFixed(digits)}x`;
}

/** Demo anchor "now" — fixed so SSR and client render identically. */
export const NOW = new Date("2026-07-15T12:00:00Z");

/** "3d ago" / "2h ago" / "just now" relative to the demo anchor. */
export function ago(iso: string | Date | null | undefined): string {
  if (!iso) return "—";
  const d = typeof iso === "string" ? new Date(iso) : iso;
  const mins = Math.max(0, Math.round((NOW.getTime() - d.getTime()) / 60_000));
  if (mins < 2) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  if (days < 45) return `${days}d ago`;
  const months = Math.round(days / 30);
  return `${months}mo ago`;
}

/** "Jul 15, 2026" */
export function dateShort(iso: string | Date | null | undefined): string {
  if (!iso) return "—";
  const d = typeof iso === "string" ? new Date(iso) : iso;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
}

/** "Jul '26" — chart axis ticks. */
export function dateTick(iso: string | Date): string {
  const d = typeof iso === "string" ? new Date(iso) : iso;
  return d.toLocaleDateString("en-US", { month: "short", timeZone: "UTC" }) + " ’" + String(d.getUTCFullYear()).slice(2);
}

/** 1,240 sqft */
export function sqft(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${num(v)} sqft`;
}
