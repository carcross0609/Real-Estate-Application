/**
 * Pure client-side underwriting math for the Deal Analyzer (S13). Mirrors the platform
 * engine's formulas (03 §26) so live keystroke recompute matches the stored analysis;
 * the server engine remains the system of record for persisted numbers.
 */

export interface FlipAssumptions {
  purchase_price: number;
  arv: number;
  rehab_budget: number;
  closing_pct: number; // of purchase
  selling_pct: number; // of ARV
  hold_months: number;
  holding_monthly: number; // taxes+insurance+utilities+debt carry
  down_pct: number; // hard money down
}

export interface FlipOutputs {
  all_in: number;
  net_profit: number;
  margin_pct: number;
  cash_needed: number;
  roi_pct: number;
  roi_annualized_pct: number;
  waterfall: Array<{ key: string; label: string; amount: number }>;
}

export function computeFlip(a: FlipAssumptions): FlipOutputs {
  const closing = a.purchase_price * (a.closing_pct / 100);
  const holding = a.holding_monthly * a.hold_months;
  const selling = a.arv * (a.selling_pct / 100);
  const allIn = a.purchase_price + closing + a.rehab_budget + holding;
  const profit = a.arv - allIn - selling;
  const cashNeeded = a.purchase_price * (a.down_pct / 100) + closing + a.rehab_budget;
  const roi = cashNeeded > 0 ? (profit / cashNeeded) * 100 : 0;
  return {
    all_in: allIn,
    net_profit: profit,
    margin_pct: a.arv > 0 ? (profit / a.arv) * 100 : 0,
    cash_needed: cashNeeded,
    roi_pct: roi,
    roi_annualized_pct: roi * (12 / Math.max(1, a.hold_months)),
    waterfall: [
      { key: "arv", label: "Resale at ARV", amount: a.arv },
      { key: "purchase", label: "Purchase price", amount: -a.purchase_price },
      { key: "rehab", label: "Rehab budget", amount: -a.rehab_budget },
      { key: "closing", label: "Closing costs", amount: -closing },
      { key: "holding", label: `Holding (${a.hold_months} mo)`, amount: -holding },
      { key: "selling", label: "Selling costs", amount: -selling },
      { key: "profit", label: "Net profit", amount: profit },
    ],
  };
}

export interface RentalAssumptions {
  purchase_price: number;
  arv: number;
  rehab_budget: number;
  closing_pct: number;
  rent_monthly: number;
  vacancy_pct: number;
  mgmt_pct: number;
  maintenance_pct: number;
  capex_pct: number;
  taxes_annual: number;
  insurance_annual: number;
  hoa_monthly: number;
  down_pct: number;
  rate_pct: number;
  term_years: number;
  /** BRRRR only */
  refi_ltv_pct: number;
  appreciation_pct: number; // annual
  rent_growth_pct: number; // annual
}

export interface RentalOutputs {
  loan: number;
  p_and_i: number;
  opex_monthly: number;
  noi_annual: number;
  cash_flow_monthly: number;
  cash_invested: number;
  coc_pct: number;
  cap_rate_pct: number;
  dscr: number;
  breakeven_occupancy_pct: number;
  capital_left_in: number; // BRRRR
  coc_after_refi_pct: number | null;
  waterfall: Array<{ key: string; label: string; amount: number }>;
  expense_lines: Array<{ key: string; label: string; amount: number }>;
  projection: Array<{ year: number; equity: number; cumulative_cf: number; value: number }>;
}

export function amortizedPayment(principal: number, ratePct: number, years: number): number {
  const r = ratePct / 100 / 12;
  const n = years * 12;
  if (r === 0) return principal / n;
  return (principal * r) / (1 - Math.pow(1 + r, -n));
}

export function computeRental(a: RentalAssumptions, brrrr = false): RentalOutputs {
  const closing = a.purchase_price * (a.closing_pct / 100);
  const financedBasis = brrrr ? a.purchase_price : a.purchase_price + a.rehab_budget * 0.5;
  const loan = financedBasis * (1 - a.down_pct / 100);
  const pAndI = amortizedPayment(loan, a.rate_pct, a.term_years);

  const vacancy = a.rent_monthly * (a.vacancy_pct / 100);
  const mgmt = a.rent_monthly * (a.mgmt_pct / 100);
  const maint = a.rent_monthly * (a.maintenance_pct / 100);
  const capex = a.rent_monthly * (a.capex_pct / 100);
  const taxMo = a.taxes_annual / 12;
  const insMo = a.insurance_annual / 12;
  const opex = vacancy + mgmt + maint + capex + taxMo + insMo + a.hoa_monthly;

  const noi = (a.rent_monthly - opex) * 12;
  const cashFlow = a.rent_monthly - opex - pAndI;

  const cashInvestedBase = a.purchase_price * (a.down_pct / 100) + closing + a.rehab_budget * (brrrr ? 1 : 0.5);
  const refiLoan = a.arv * (a.refi_ltv_pct / 100);
  const capitalLeftIn = Math.max(0, a.purchase_price + closing + a.rehab_budget - refiLoan);
  const cashInvested = cashInvestedBase;

  const projection: RentalOutputs["projection"] = [];
  let value = a.arv;
  let rent = a.rent_monthly;
  let balance = brrrr ? refiLoan : loan;
  let cumCf = 0;
  const monthlyRate = a.rate_pct / 100 / 12;
  for (let y = 1; y <= 5; y++) {
    for (let m = 0; m < 12; m++) {
      const interest = balance * monthlyRate;
      balance = Math.max(0, balance - (pAndI - interest));
    }
    value *= 1 + a.appreciation_pct / 100;
    rent *= 1 + a.rent_growth_pct / 100;
    cumCf += (rent - opex * (rent / a.rent_monthly) - pAndI) * 12;
    projection.push({
      year: y,
      equity: Math.round(value - balance),
      cumulative_cf: Math.round(cumCf),
      value: Math.round(value),
    });
  }

  return {
    loan,
    p_and_i: pAndI,
    opex_monthly: opex,
    noi_annual: noi,
    cash_flow_monthly: cashFlow,
    cash_invested: cashInvested,
    coc_pct: cashInvested > 0 ? ((cashFlow * 12) / cashInvested) * 100 : 0,
    cap_rate_pct: (noi / (a.purchase_price + a.rehab_budget)) * 100,
    dscr: pAndI > 0 ? noi / 12 / pAndI : 0,
    breakeven_occupancy_pct: a.rent_monthly > 0 ? ((opex + pAndI) / a.rent_monthly) * 100 : 0,
    capital_left_in: capitalLeftIn,
    coc_after_refi_pct: brrrr && capitalLeftIn > 0 ? ((cashFlow * 12) / capitalLeftIn) * 100 : brrrr ? null : null,
    waterfall: [
      { key: "rent", label: "Gross rent", amount: a.rent_monthly },
      { key: "opex", label: "Operating expenses", amount: -opex },
      { key: "debt", label: "Principal & interest", amount: -pAndI },
      { key: "cf", label: "Monthly cash flow", amount: cashFlow },
    ],
    expense_lines: [
      { key: "vacancy", label: `Vacancy (${a.vacancy_pct}%)`, amount: vacancy },
      { key: "mgmt", label: `Management (${a.mgmt_pct}%)`, amount: mgmt },
      { key: "maint", label: `Maintenance (${a.maintenance_pct}%)`, amount: maint },
      { key: "capex", label: `CapEx (${a.capex_pct}%)`, amount: capex },
      { key: "taxes", label: "Taxes", amount: taxMo },
      { key: "insurance", label: "Insurance", amount: insMo },
      ...(a.hoa_monthly > 0 ? [{ key: "hoa", label: "HOA", amount: a.hoa_monthly }] : []),
    ],
    projection,
  };
}
