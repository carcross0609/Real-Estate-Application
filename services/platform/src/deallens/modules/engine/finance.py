"""Financial calculation primitives — pure, Decimal, property-testable (03 §26.2–§26.6).

Every function here is a deterministic money calculation that returns its result **with the
ledger that produced it** (`Calc.value` + `Calc.lines`), so "show the math" (§26.9, NFR-08) is
produced by the same code path as the number — it cannot drift from the truth. No I/O, no
database, no float: money is `Decimal` end-to-end (§20), so a stored pro-forma never carries
rounding drift.

The split mirrors the comps engine: these primitives are pure and exhaustively unit-tested;
`strategies.py` composes them into per-strategy output blocks (flip / LTR / BRRRR / …), and
`service.py` assembles the property-specific `FinancialInputs` from the ORM and persists the
result. Nothing in this file knows what a database is.

Design commitments:
- **Standard definitions, labeled disputes.** NOI excludes the capex reserve and debt (the
  textbook definition); we *also* surface NOI-after-reserves rather than silently picking the
  side of the argument investors have about it (§26.4).
- **Bands, not false points (§26.6).** Profit numbers propagate the ARV and rehab intervals,
  so the flip P10/P50/P90 is the arithmetic on (conservative ARV, expensive rehab) …
  (optimistic ARV, cheap rehab) — an honest band, never a single confident-looking figure.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from deallens.modules.engine.schemas import CalcLine

_ZERO = Decimal("0")
_ONE = Decimal("1")
_TWELVE = Decimal("12")
_CENTS = Decimal("0.01")
_RATE_Q = Decimal("0.00001")


def _round(v: Decimal) -> Decimal:
    return v.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _q(v: Decimal, exp: Decimal = _RATE_Q) -> Decimal:
    return v.quantize(exp, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class Calc:
    """A computed money value plus the derivation ledger behind it (§26.9). `lines` are the
    signed contributions that sum toward (or explain) `value`; carrying them alongside the number
    is what makes every figure auditable without a second, drift-prone "explanation" code path.
    """

    value: Decimal
    lines: list[CalcLine] = field(default_factory=list)


def _line(label: str, amount: Decimal, note: str | None = None) -> CalcLine:
    return CalcLine(label=label, amount=_round(amount), note=note)


# --- Acquisition, rehab, holding (§26.2) ------------------------------------------------


def closing_costs_buy(
    price: Decimal, *, closing_pct: Decimal, fixed_fees: Decimal = _ZERO
) -> Calc:
    """Buy-side closing costs: `price × closing_pct + fixed_fees` (§26.2, default 2.0%,
    market-table overridable)."""
    pct_part = price * closing_pct
    total = pct_part + fixed_fees
    lines = [_line("closing_pct", pct_part, f"{price} × {closing_pct:.4f}")]
    if fixed_fees:
        lines.append(_line("fixed_fees", fixed_fees))
    return Calc(_round(total), lines)


def rehab_with_contingency(
    rehab_base: Decimal, *, contingency_pct: Decimal, contingency_pct_flagged: Decimal,
    red_flags_present: bool,
) -> Calc:
    """Rehab total = base + contingency (§26.2). Contingency is 15% of rehab normally, 20% when
    structural red flags are present (the extra reserve for the unknowns a flagged property
    hides). A $0 base yields a $0 total — no contingency on nothing."""
    pct = contingency_pct_flagged if red_flags_present else contingency_pct
    contingency = rehab_base * pct
    total = rehab_base + contingency
    note = f"{pct:.2f} of rehab" + (" (red flags → higher reserve)" if red_flags_present else "")
    return Calc(
        _round(total),
        [
            _line("rehab_base", rehab_base),
            _line("contingency", contingency, note),
        ],
    )


def holding_costs(
    *, annual_taxes: Decimal, annual_insurance: Decimal, monthly_utilities: Decimal,
    monthly_hoa: Decimal, months: int, loan_interest: Decimal = _ZERO,
) -> Calc:
    """Carry cost over the hold (§26.2/§26.6): `(taxes + insurance)/12 × months + (utilities +
    HOA) × months + loan interest`. Loan interest is passed in from the financing scenario (the
    hard-money interest-only carry for a flip), keeping this function free of loan math."""
    monthly_fixed = (annual_taxes + annual_insurance) / _TWELVE + monthly_utilities + monthly_hoa
    carry = monthly_fixed * Decimal(months)
    total = carry + loan_interest
    lines = [
        _line("taxes_insurance", (annual_taxes + annual_insurance) / _TWELVE * Decimal(months),
              f"({annual_taxes} + {annual_insurance})/12 × {months} mo"),
        _line("utilities_hoa", (monthly_utilities + monthly_hoa) * Decimal(months),
              f"({monthly_utilities} + {monthly_hoa})/mo × {months} mo"),
    ]
    if loan_interest:
        lines.append(_line("loan_interest", loan_interest, "financing-scenario carry"))
    return Calc(_round(total), lines)


def all_in(
    price: Decimal, closing_buy: Decimal, rehab_total: Decimal, holding: Decimal
) -> Calc:
    """Total cash into the deal before exit (§26.2): `price + closing_buy + rehab + holding`."""
    total = price + closing_buy + rehab_total + holding
    return Calc(
        _round(total),
        [
            _line("price", price),
            _line("closing_costs_buy", closing_buy),
            _line("rehab_total", rehab_total),
            _line("holding_costs", holding),
        ],
    )


# --- Financing (§26.3) ------------------------------------------------------------------


def mortgage_payment(principal: Decimal, annual_rate: Decimal, term_years: int) -> Decimal:
    """The fully-amortizing monthly payment (§26.3): `M = P·r(1+r)^n / ((1+r)^n − 1)`,
    `r = annual/12`, `n = term_years·12`. A zero rate degrades to straight-line `P/n` (the limit
    as r→0), and a zero principal/term is $0 — no division blow-up on a cash deal."""
    n = term_years * 12
    if principal <= _ZERO or n <= 0:
        return _ZERO
    r = annual_rate / _TWELVE
    if r == _ZERO:
        return _round(principal / Decimal(n))
    growth = (_ONE + r) ** n
    payment = principal * r * growth / (growth - _ONE)
    return _round(payment)


def interest_only_payment(principal: Decimal, annual_rate: Decimal) -> Decimal:
    """Monthly interest-only payment: `P · r` (§26.3, hard-money scenario)."""
    if principal <= _ZERO:
        return _ZERO
    return _round(principal * annual_rate / _TWELVE)


@dataclass(frozen=True, slots=True)
class AmortizationPoint:
    """One month of the schedule (§26.3): payment split into interest/principal + running
    balance. The service turns these into the analyzer chart and the payoff-at-exit figure."""

    month: int
    interest: Decimal
    principal: Decimal
    balance: Decimal


def amortization_schedule(
    principal: Decimal, annual_rate: Decimal, term_years: int, *, months: int | None = None
) -> list[AmortizationPoint]:
    """The month-by-month amortization (§26.3), truncated to `months` (e.g. the hold length) so
    the caller can read the payoff balance at exit. Interest accrues on the running balance;
    the final row floors the balance at 0 against rounding dust."""
    n = term_years * 12
    horizon = min(months, n) if months is not None else n
    if principal <= _ZERO or n <= 0 or horizon <= 0:
        return []
    r = annual_rate / _TWELVE
    payment = mortgage_payment(principal, annual_rate, term_years)
    schedule: list[AmortizationPoint] = []
    balance = principal
    for m in range(1, horizon + 1):
        interest = _round(balance * r)
        principal_paid = _round(payment - interest)
        balance = _round(balance - principal_paid)
        if balance < _ZERO:
            balance = _ZERO
        schedule.append(AmortizationPoint(m, interest, principal_paid, balance))
    return schedule


def principal_paid_over(
    principal: Decimal, annual_rate: Decimal, term_years: int, months: int
) -> Decimal:
    """Cumulative principal paid down over `months` — the amortization component of the 5-year
    equity build (§26.5). Sum of the schedule's principal column."""
    return _round(
        sum(
            (p.principal for p in amortization_schedule(
                principal, annual_rate, term_years, months=months)),
            _ZERO,
        )
    )


def loan_costs(principal: Decimal, *, points: Decimal, origination: Decimal = _ZERO,
               fixed: Decimal = _ZERO) -> Calc:
    """Loan costs (§26.3): `points·principal + origination + fixed`. Points are a fraction
    (0.02 = 2 pts)."""
    pts = principal * points
    total = pts + origination + fixed
    lines = [_line("points", pts, f"{points:.4f} × {principal}")]
    if origination:
        lines.append(_line("origination", origination))
    if fixed:
        lines.append(_line("fixed", fixed))
    return Calc(_round(total), lines)


# --- Operating pro-forma (§26.4) --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProForma:
    """The computed rental operating statement (§26.4) — every line kept, plus both NOI
    definitions, and its ledger. `debt_service_annual` is carried for the cash-flow/DSCR math
    downstream but is *not* in NOI (standard definition)."""

    gross_potential_rent: Decimal
    vacancy: Decimal
    other_income: Decimal
    effective_gross_income: Decimal
    taxes: Decimal
    insurance: Decimal
    management: Decimal
    maintenance: Decimal
    capex_reserve: Decimal
    hoa: Decimal
    utilities: Decimal
    other_opex: Decimal
    operating_expenses: Decimal
    noi: Decimal
    noi_after_reserves: Decimal
    lines: list[CalcLine]


def operating_proforma(
    *,
    monthly_rent: Decimal,
    annual_taxes: Decimal,
    annual_insurance: Decimal,
    monthly_hoa: Decimal = _ZERO,
    monthly_utilities: Decimal = _ZERO,
    other_monthly_income: Decimal = _ZERO,
    vacancy_pct: Decimal,
    management_pct: Decimal,
    maintenance_pct: Decimal,
    capex_reserve_pct: Decimal,
) -> ProForma:
    """Build the §26.4 operating statement from a monthly market rent + the assumption rates.

    Percentage bases follow §26.4 exactly: vacancy/maintenance/capex on **GPR**, management on
    **EGI** (you don't pay a manager on vacant months). NOI excludes the capex reserve and debt;
    NOI-after-reserves subtracts the reserve — both surfaced and labeled (§26.4).
    """
    gpr = _round(monthly_rent * _TWELVE)
    other_income = _round(other_monthly_income * _TWELVE)
    vacancy = _round(gpr * vacancy_pct)
    egi = _round(gpr - vacancy + other_income)

    taxes = _round(annual_taxes)
    insurance = _round(annual_insurance)
    management = _round(egi * management_pct)
    maintenance = _round(gpr * maintenance_pct)
    capex_reserve = _round(gpr * capex_reserve_pct)
    hoa = _round(monthly_hoa * _TWELVE)
    utilities = _round(monthly_utilities * _TWELVE)

    opex = taxes + insurance + management + maintenance + hoa + utilities  # excl. reserve & debt
    noi = _round(egi - opex)
    noi_after_reserves = _round(noi - capex_reserve)

    lines = [
        _line("gross_potential_rent", gpr, f"{monthly_rent}/mo × 12"),
        _line("vacancy", -vacancy, f"{vacancy_pct:.3f} of GPR"),
        _line("other_income", other_income),
        _line("effective_gross_income", egi, "GPR − vacancy + other"),
        _line("taxes", -taxes, "actual assessor (reassessed-at-purchase where applicable)"),
        _line("insurance", -insurance),
        _line("management", -management, f"{management_pct:.3f} of EGI"),
        _line("maintenance", -maintenance, f"{maintenance_pct:.3f} of GPR"),
        _line("hoa", -hoa),
        _line("utilities", -utilities),
        _line("noi", noi, "EGI − opex (excl. reserve & debt)"),
        _line("capex_reserve", -capex_reserve, f"{capex_reserve_pct:.3f} of GPR"),
        _line("noi_after_reserves", noi_after_reserves, "NOI − capex reserve"),
    ]
    return ProForma(
        gross_potential_rent=gpr, vacancy=vacancy, other_income=other_income,
        effective_gross_income=egi, taxes=taxes, insurance=insurance, management=management,
        maintenance=maintenance, capex_reserve=capex_reserve, hoa=hoa, utilities=utilities,
        other_opex=_ZERO, operating_expenses=_round(opex), noi=noi,
        noi_after_reserves=noi_after_reserves, lines=lines,
    )


# --- Return metrics (§26.5) -------------------------------------------------------------


def _safe_div(num: Decimal, den: Decimal) -> Decimal | None:
    return None if den == _ZERO else num / den


def cap_rate(noi: Decimal, basis: Decimal) -> Decimal | None:
    """`NOI / basis` (§26.5). `basis` is price for the acquisition cap rate, ARV for the
    ARV-basis variant (both labeled by the caller). None on a zero basis."""
    r = _safe_div(noi, basis)
    return None if r is None else _q(r)


def monthly_cash_flow(egi: Decimal, opex: Decimal, capex_reserve: Decimal,
                      annual_debt_service: Decimal) -> Decimal:
    """After-reserve monthly cash flow (§26.5, conservative): `(EGI − opex − reserve − debt)/12`.
    We report the conservative (after-reserve) figure so a "positive cash flow" isn't quietly
    borrowing from the roof fund."""
    annual = egi - opex - capex_reserve - annual_debt_service
    return _round(annual / _TWELVE)


def cash_on_cash(annual_cash_flow: Decimal, cash_invested: Decimal) -> Decimal | None:
    """`annual_cash_flow / cash_invested` (§26.5). None when nothing is invested (a fully
    financed deal's CoC is undefined, not infinite)."""
    r = _safe_div(annual_cash_flow, cash_invested)
    return None if r is None else _q(r)


def dscr(noi: Decimal, annual_debt_service: Decimal) -> Decimal | None:
    """`NOI / annual_debt_service` (§26.5). None on a cash deal (no debt → DSCR undefined, and
    the strategy gate that reads it should treat "no debt" as "not debt-constrained")."""
    r = _safe_div(noi, annual_debt_service)
    return None if r is None else _q(r)


def grm(price: Decimal, gpr: Decimal) -> Decimal | None:
    """Gross rent multiplier `price / GPR` (§26.5)."""
    r = _safe_div(price, gpr)
    return None if r is None else _q(r)


def breakeven_occupancy(
    opex: Decimal, annual_debt_service: Decimal, gpr: Decimal
) -> Decimal | None:
    """`(opex + debt_service) / GPR` (§26.5) — the occupancy at which the property covers its
    costs. Above 1.0 means it never breaks even at market rent (a red flag the UI surfaces)."""
    r = _safe_div(opex + annual_debt_service, gpr)
    return None if r is None else _q(r)


def cash_invested(
    *, down_payment: Decimal, closing_buy: Decimal, rehab_total: Decimal,
    holding_to_stabilization: Decimal, financed_rehab: Decimal = _ZERO,
) -> Calc:
    """Actual cash in the deal (§26.5): `down + closing + rehab + holding − financed_rehab`. The
    denominator of CoC — getting this right (subtracting rehab financed by the loan) is the
    difference between an honest and a flattering CoC."""
    total = down_payment + closing_buy + rehab_total + holding_to_stabilization - financed_rehab
    lines = [
        _line("down_payment", down_payment),
        _line("closing_costs_buy", closing_buy),
        _line("rehab_total", rehab_total),
        _line("holding_to_stabilization", holding_to_stabilization),
    ]
    if financed_rehab:
        lines.append(_line("financed_rehab", -financed_rehab, "rehab covered by the loan"))
    return Calc(_round(total if total > _ZERO else _ZERO), lines)


# --- Flip (§26.6) -----------------------------------------------------------------------


def selling_costs(
    arv: Decimal, *, agent_commission_pct: Decimal, closing_pct_sell: Decimal,
    concessions_pct: Decimal,
) -> Calc:
    """Sell-side costs on the ARV (§26.6): commission (5.5%) + closing (1%) + concessions (1%)."""
    commission = arv * agent_commission_pct
    closing = arv * closing_pct_sell
    concessions = arv * concessions_pct
    total = commission + closing + concessions
    return Calc(
        _round(total),
        [
            _line("agent_commission", commission, f"{agent_commission_pct:.4f} of ARV"),
            _line("closing_sell", closing, f"{closing_pct_sell:.4f} of ARV"),
            _line("concessions", concessions, f"{concessions_pct:.4f} of ARV"),
        ],
    )


def flip_net_profit(
    arv: Decimal, all_in_cost: Decimal, selling: Decimal, loan_cost: Decimal
) -> Calc:
    """`ARV − all_in − selling − loan_costs` (§26.6). Can be negative — a bad flip is a loss, and
    we show it, never floor it to zero."""
    profit = arv - all_in_cost - selling - loan_cost
    return Calc(
        _round(profit),
        [
            _line("arv", arv),
            _line("all_in", -all_in_cost),
            _line("selling_costs", -selling),
            _line("loan_costs", -loan_cost),
        ],
    )


def flip_margin(net_profit: Decimal, cash_invested_amt: Decimal) -> Decimal | None:
    """`net_profit / cash_invested` (§26.6) — the flip's return on the cash actually at risk."""
    r = _safe_div(net_profit, cash_invested_amt)
    return None if r is None else _q(r)


def annualized_roi(margin: Decimal | None, months: int) -> Decimal | None:
    """`margin × 12/months` (§26.6) — annualize a project return so a 6-month flip and a 12-month
    hold compare on the same axis (the §25.4 cross-strategy calibration requirement)."""
    if margin is None or months <= 0:
        return None
    return _q(margin * _TWELVE / Decimal(months))


def rule_70_check(price: Decimal, rehab_total: Decimal, arv: Decimal) -> bool:
    """The 70% rule sanity flag (§26.6): `price + rehab ≤ 0.70 × ARV`. Shown as a heuristic flag,
    not gospel — a thin-margin deal can still pencil, and a passing one can still be bad."""
    return price + rehab_total <= Decimal("0.70") * arv


# --- BRRRR refinance (§26.7) ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RefiResult:
    """The BRRRR cash-out refinance outcome (§26.7): the new loan (LTV- and DSCR-constrained),
    the cash pulled out, and the capital left in the deal — the metric that defines a BRRRR."""

    new_loan: Decimal
    cash_out: Decimal
    capital_left_in: Decimal
    dscr_at_refi: Decimal | None
    binding_constraint: str  # "ltv" | "dscr" — which limit sized the loan (shown, not hidden)
    lines: list[CalcLine]


def brrrr_refinance(
    *, arv: Decimal, refi_ltv: Decimal, monthly_noi: Decimal, refi_rate: Decimal,
    refi_term_years: int, min_dscr: Decimal, payoff: Decimal, refi_costs: Decimal,
    cash_invested_amt: Decimal,
) -> RefiResult:
    """Size the refi loan as `min(LTV·ARV, loan where DSCR ≥ min_dscr)` and derive cash-out +
    capital-left-in (§26.7). The DSCR-max loan is the principal whose amortizing payment keeps
    annual NOI / annual debt service ≥ `min_dscr`; we solve it by scaling the LTV-max payment.
    The binding constraint is reported — a deal capped by DSCR (thin rent) reads very differently
    from one capped by LTV (thin equity), and hiding which one bit would mislead."""
    ltv_loan = _round(arv * refi_ltv)
    annual_noi = monthly_noi * _TWELVE

    # Largest loan whose amortizing annual debt service ≤ annual_noi / min_dscr.
    max_annual_ds = annual_noi / min_dscr if min_dscr > _ZERO else annual_noi
    ltv_payment = mortgage_payment(ltv_loan, refi_rate, refi_term_years)
    ltv_annual_ds = ltv_payment * _TWELVE
    if ltv_annual_ds <= max_annual_ds or ltv_annual_ds == _ZERO:
        new_loan = ltv_loan
        binding = "ltv"
    else:
        # Payment scales linearly with principal at a fixed rate/term → scale the LTV loan down.
        dscr_loan = _round(ltv_loan * (max_annual_ds / ltv_annual_ds))
        new_loan = dscr_loan
        binding = "dscr"

    new_payment = mortgage_payment(new_loan, refi_rate, refi_term_years)
    dscr_at_refi = dscr(annual_noi, new_payment * _TWELVE)
    cash_out = _round(new_loan - payoff - refi_costs)
    capital_left = _round(cash_invested_amt - cash_out)
    return RefiResult(
        new_loan=new_loan,
        cash_out=cash_out,
        capital_left_in=capital_left,
        dscr_at_refi=dscr_at_refi,
        binding_constraint=binding,
        lines=[
            _line("ltv_max_loan", ltv_loan, f"{refi_ltv:.2f} × ARV"),
            _line("new_loan", new_loan, f"sized by {binding}"),
            _line("payoff", -payoff, "existing loan retired"),
            _line("refi_costs", -refi_costs),
            _line("cash_out", cash_out, "new_loan − payoff − refi_costs"),
            _line("capital_left_in", capital_left, "cash_invested − cash_out"),
        ],
    )


# --- 5-year projection (§26.5) ----------------------------------------------------------


def future_value(present: Decimal, annual_rate: Decimal, years: int) -> Decimal:
    """Compound appreciation `present · (1+rate)^years` (§26.5). Rate is user-cappable upstream;
    this is deliberately simple compounding, no Monte-Carlo pretense."""
    return _round(present * (_ONE + annual_rate) ** years)


def equity_multiple(total_cash_returned: Decimal, cash_invested_amt: Decimal) -> Decimal | None:
    """`total cash returned / cash invested` over the hold (§26.5)."""
    r = _safe_div(total_cash_returned, cash_invested_amt)
    return None if r is None else _q(r)


def simple_irr(cash_flows: Sequence[Decimal], *, max_iter: int = 100) -> Decimal | None:
    """Annualized IRR of a cash-flow vector (index 0 = initial outflow, then per-year flows),
    found by bisection on NPV (§26.5). Bisection (not Newton) because it is dependency-free and
    can't diverge; returns None when no sign change brackets a root (e.g. an all-negative stream)
    — an honest "undefined," never a fabricated rate. Kept exact-ish in Decimal via a bounded
    rate search on [-0.99, 10.0]."""
    if not cash_flows or all(cf >= _ZERO for cf in cash_flows) or all(
        cf <= _ZERO for cf in cash_flows
    ):
        return None

    def npv(rate: Decimal) -> Decimal:
        return sum(
            (cf / (_ONE + rate) ** t for t, cf in enumerate(cash_flows)), _ZERO
        )

    lo, hi = Decimal("-0.99"), Decimal("10.0")
    npv_lo, npv_hi = npv(lo), npv(hi)
    if npv_lo * npv_hi > _ZERO:
        return None
    for _ in range(max_iter):
        mid = (lo + hi) / Decimal(2)
        npv_mid = npv(mid)
        if npv_mid == _ZERO:
            return _q(mid)
        if npv_lo * npv_mid < _ZERO:
            hi, npv_hi = mid, npv_mid
        else:
            lo, npv_lo = mid, npv_mid
    return _q((lo + hi) / Decimal(2))


__all__ = [
    "AmortizationPoint",
    "Calc",
    "ProForma",
    "RefiResult",
    "all_in",
    "amortization_schedule",
    "annualized_roi",
    "breakeven_occupancy",
    "brrrr_refinance",
    "cap_rate",
    "cash_invested",
    "cash_on_cash",
    "closing_costs_buy",
    "dscr",
    "equity_multiple",
    "flip_margin",
    "flip_net_profit",
    "future_value",
    "grm",
    "holding_costs",
    "interest_only_payment",
    "loan_costs",
    "monthly_cash_flow",
    "mortgage_payment",
    "operating_proforma",
    "principal_paid_over",
    "rehab_with_contingency",
    "rule_70_check",
    "selling_costs",
    "simple_irr",
]
