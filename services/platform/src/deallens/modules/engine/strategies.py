"""Per-strategy underwriting — pure composition of the `finance` primitives into a full
`EngineOutputBlock` per (property × strategy) (03 §26.3–§26.8). No I/O; the service supplies a
validated `FinancialInputs` and gets back the stored output shape.

Each strategy is a recipe over the same primitives (§26.8: "each is an engine module against
the same interfaces"), differing in which financing scenario is primary, whether there's a
rental pro-forma, and which return metrics define success:

- **Flip** — hard-money acquisition, resale at ARV, profit = ARV − all-in − selling − loan.
- **LTR** — conventional acquisition, operating pro-forma, cap/CoC/DSCR/cash-flow.
- **BRRRR** — flip-through-rehab, then a DSCR-constrained cash-out refi; the metric is the
  capital left in the deal.
- **Wholesale** — MAO (max allowable offer) and the assignment spread (§26.8).

**Winner's-curse-honest bands (§26.6, §25.8).** Profit numbers propagate the ARV and rehab
intervals: the P10 profit pairs the *conservative* ARV (P10) with the *expensive* rehab (high),
the P90 profit pairs the optimistic ARV with the cheap rehab. So a wide, uncertain estimate
produces a wide profit band — it cannot masquerade as a confident point, and ranking downstream
reads the conservative end.
"""

from __future__ import annotations

from decimal import Decimal

from deallens.core.enums import Strategy
from deallens.modules.engine import finance
from deallens.modules.engine.schemas import (
    AcquisitionCosts,
    AssumptionSet,
    CalcLine,
    EngineOutputBlock,
    FinancialInputs,
    FinancingScenario,
    FiveYearProjection,
    Interval,
    OperatingProForma,
    RehabEstimate,
    ReturnMetrics,
)

ENGINE_VERSION = "v1"
_ZERO = Decimal("0")
_ONE = Decimal("1")
_TWELVE = Decimal("12")


class UnsupportedStrategyError(ValueError):
    """Raised for a strategy whose underwriting model is not yet built (§26.8 [F])."""


# --- Shared building blocks -------------------------------------------------------------


def _taxes(inputs: FinancialInputs) -> Decimal:
    return inputs.annual_taxes or _ZERO


def _insurance(inputs: FinancialInputs) -> Decimal:
    return inputs.annual_insurance or _ZERO


def _rehab_total(rehab: RehabEstimate, assumptions: AssumptionSet, which: str) -> Decimal:
    """Rehab + contingency at the low/mid/high point of the band (03 §26.2)."""
    base = {"low": rehab.low, "mid": rehab.mid, "high": rehab.high}[which]
    return finance.rehab_with_contingency(
        base,
        contingency_pct=assumptions.rehab_contingency_pct,
        contingency_pct_flagged=assumptions.rehab_contingency_pct_flagged,
        red_flags_present=rehab.red_flags_present,
    ).value


def _financing_scenarios(
    inputs: FinancialInputs, assumptions: AssumptionSet, rehab_mid_total: Decimal
) -> list[FinancingScenario]:
    """The standard ≥3-scenario set (§26.3): conventional, DSCR, hard-money, cash. Payment and
    cash-to-close computed for each so the analyzer compares them head to head."""
    price = inputs.price
    scenarios: list[FinancingScenario] = []

    # (a) conventional 20% down 30-yr amortizing
    conv_loan = price * (_ONE - assumptions.conventional_down_pct)
    conv_pay = finance.mortgage_payment(
        conv_loan, assumptions.conventional_rate, assumptions.conventional_term_years
    )
    scenarios.append(FinancingScenario(
        label="conventional", down_pct=assumptions.conventional_down_pct,
        rate=assumptions.conventional_rate, term_years=assumptions.conventional_term_years,
        monthly_payment=conv_pay, loan_amount=conv_loan.quantize(Decimal("0.01")),
        down_payment=(price * assumptions.conventional_down_pct).quantize(Decimal("0.01")),
        loan_costs=finance.loan_costs(conv_loan, points=_ZERO).value,
    ))

    # (b) investor DSCR loan 25% down 30-yr
    dscr_loan = price * (_ONE - assumptions.dscr_down_pct)
    dscr_pay = finance.mortgage_payment(
        dscr_loan, assumptions.dscr_rate, assumptions.dscr_term_years
    )
    scenarios.append(FinancingScenario(
        label="dscr", down_pct=assumptions.dscr_down_pct, rate=assumptions.dscr_rate,
        term_years=assumptions.dscr_term_years, monthly_payment=dscr_pay,
        loan_amount=dscr_loan.quantize(Decimal("0.01")),
        down_payment=(price * assumptions.dscr_down_pct).quantize(Decimal("0.01")),
        loan_costs=finance.loan_costs(dscr_loan, points=_ZERO).value,
    ))

    # (c) hard money 10% down, 2 pts, interest-only, 12-mo (flip / BRRRR acquisition)
    hm_loan = price * (_ONE - assumptions.hard_money_down_pct)
    hm_pay = finance.interest_only_payment(hm_loan, assumptions.hard_money_rate)
    scenarios.append(FinancingScenario(
        label="hard_money", down_pct=assumptions.hard_money_down_pct,
        rate=assumptions.hard_money_rate, points=assumptions.hard_money_points,
        interest_only=True, monthly_payment=hm_pay,
        loan_amount=hm_loan.quantize(Decimal("0.01")),
        down_payment=(price * assumptions.hard_money_down_pct).quantize(Decimal("0.01")),
        loan_costs=finance.loan_costs(hm_loan, points=assumptions.hard_money_points).value,
    ))

    # (d) cash
    scenarios.append(FinancingScenario(
        label="cash", down_pct=_ONE, rate=_ZERO, monthly_payment=_ZERO,
        loan_amount=_ZERO, down_payment=price.quantize(Decimal("0.01")), loan_costs=_ZERO,
    ))
    return scenarios


def _min_confidence(inputs: FinancialInputs) -> Decimal | None:
    return inputs.estimator_confidence


def _warn_thin(inputs: FinancialInputs, warnings: list[str]) -> None:
    if inputs.arv is None:
        warnings.append("no ARV estimate — resale/flip figures unavailable")
    if inputs.annual_taxes is None:
        warnings.append("no assessor tax record — using $0 taxes (understates opex)")
    if inputs.annual_insurance is None:
        warnings.append("no insurance quote — using $0 insurance (understates opex)")


# --- Flip (§26.6) -----------------------------------------------------------------------


def analyze_flip(inputs: FinancialInputs, assumptions: AssumptionSet) -> EngineOutputBlock:
    """Underwrite a fix-and-flip (§26.6). Hard-money acquisition + resale at ARV; profit and
    margin as a P10/P50/P90 band propagated from the ARV and rehab intervals."""
    if inputs.arv is None:
        raise UnsupportedStrategyError("flip requires an ARV estimate")
    warnings: list[str] = []
    _warn_thin(inputs, warnings)
    price = inputs.price
    months = assumptions.holding_months
    taxes = inputs.annual_taxes or _ZERO
    insurance = inputs.annual_insurance or _ZERO

    rehab_total_mid = _rehab_total(inputs.rehab, assumptions, "mid")
    closing_buy = finance.closing_costs_buy(price, closing_pct=assumptions.closing_pct_buy).value

    # Hard-money carry: interest-only on the 90%-LTV acquisition loan over the hold.
    hm_loan = price * (_ONE - assumptions.hard_money_down_pct)
    hm_interest = finance.interest_only_payment(hm_loan, assumptions.hard_money_rate) * Decimal(
        months
    )
    hm_loan_costs = finance.loan_costs(hm_loan, points=assumptions.hard_money_points).value

    def profit_at(arv_pt: Decimal, rehab_which: str) -> Decimal:
        rehab_tot = _rehab_total(inputs.rehab, assumptions, rehab_which)
        holding = finance.holding_costs(
            annual_taxes=taxes, annual_insurance=insurance,
            monthly_utilities=inputs.utilities_monthly, monthly_hoa=inputs.hoa_monthly,
            months=months, loan_interest=hm_interest,
        ).value
        all_in_cost = finance.all_in(price, closing_buy, rehab_tot, holding).value
        selling = finance.selling_costs(
            arv_pt, agent_commission_pct=assumptions.agent_commission_pct,
            closing_pct_sell=assumptions.closing_pct_sell,
            concessions_pct=assumptions.concessions_pct,
        ).value
        return finance.flip_net_profit(arv_pt, all_in_cost, selling, hm_loan_costs).value

    arv = inputs.arv
    p50_arv = arv.p50 or arv.point
    # Conservative band: worst profit = low ARV × expensive rehab; best = high ARV × cheap rehab.
    profit_p50 = profit_at(p50_arv, "mid")
    profit_p10 = profit_at(arv.p10 or p50_arv, "high")
    profit_p90 = profit_at(arv.p90 or p50_arv, "low")

    # Full ledger built at the P50/mid point (the headline case).
    holding = finance.holding_costs(
        annual_taxes=taxes, annual_insurance=insurance,
        monthly_utilities=inputs.utilities_monthly, monthly_hoa=inputs.hoa_monthly,
        months=months, loan_interest=hm_interest,
    )
    acq = _acquisition_block(price, closing_buy, inputs.rehab, assumptions, holding, "mid")
    selling = finance.selling_costs(
        p50_arv, agent_commission_pct=assumptions.agent_commission_pct,
        closing_pct_sell=assumptions.closing_pct_sell, concessions_pct=assumptions.concessions_pct,
    )
    cash_in = finance.cash_invested(
        down_payment=price * assumptions.hard_money_down_pct, closing_buy=closing_buy,
        rehab_total=rehab_total_mid, holding_to_stabilization=holding.value,
    )
    margin = finance.flip_margin(profit_p50, cash_in.value)
    roi = finance.annualized_roi(margin, months)

    metrics = ReturnMetrics(
        net_profit=profit_p50, flip_margin=margin, roi_annualized=roi,
        cash_invested=cash_in.value,
        lines=[
            *cash_in.lines,
            CalcLine(label="net_profit_p50", amount=profit_p50, note="ARV(P50) − all-in − sell"),
            CalcLine(label="flip_margin", amount=margin or _ZERO,
                     note="net_profit / cash_invested"),
        ],
    )
    return EngineOutputBlock(
        engine_version=ENGINE_VERSION, strategy=Strategy.FLIP.value,
        arv=arv, as_is=inputs.as_is, all_in=acq.all_in, acquisition=acq,
        financing=_financing_scenarios(inputs, assumptions, rehab_total_mid),
        return_metrics=_with_profit_band(metrics, profit_p10, profit_p50, profit_p90, selling),
        rule_70_check=finance.rule_70_check(price, rehab_total_mid, p50_arv),
        confidence=_min_confidence(inputs), warnings=warnings,
    )


def _with_profit_band(
    metrics: ReturnMetrics, p10: Decimal, p50: Decimal, p90: Decimal, selling: finance.Calc
) -> ReturnMetrics:
    """Attach the P10/P50/P90 profit band + the selling-cost ledger to a flip's metrics (§26.6
    — the UI shows the band, not a false point)."""
    data = metrics.model_dump()
    data["net_profit_p10"] = p10
    data["net_profit_p90"] = p90
    data["lines"] = [*metrics.lines, *selling.lines]
    return ReturnMetrics(**data)


def _acquisition_block(
    price: Decimal, closing_buy: Decimal, rehab: RehabEstimate, assumptions: AssumptionSet,
    holding: finance.Calc, which: str,
) -> AcquisitionCosts:
    """Assemble the §26.2 acquisition stack + its rolled-up ledger at a rehab band point."""
    base = {"low": rehab.low, "mid": rehab.mid, "high": rehab.high}[which]
    rehab_calc = finance.rehab_with_contingency(
        base, contingency_pct=assumptions.rehab_contingency_pct,
        contingency_pct_flagged=assumptions.rehab_contingency_pct_flagged,
        red_flags_present=rehab.red_flags_present,
    )
    contingency = rehab_calc.value - base
    all_in_calc = finance.all_in(price, closing_buy, rehab_calc.value, holding.value)
    return AcquisitionCosts(
        price=price, closing_costs_buy=closing_buy, rehab_base=base,
        rehab_contingency=contingency, rehab_total=rehab_calc.value, holding_costs=holding.value,
        all_in=all_in_calc.value,
        lines=[*all_in_calc.lines, *holding.lines],
    )


# --- LTR (§26.4–§26.5) ------------------------------------------------------------------


def _proforma_out(pf: finance.ProForma) -> OperatingProForma:
    return OperatingProForma(
        gross_potential_rent=pf.gross_potential_rent, vacancy=pf.vacancy,
        other_income=pf.other_income, effective_gross_income=pf.effective_gross_income,
        taxes=pf.taxes, insurance=pf.insurance, management=pf.management,
        maintenance=pf.maintenance, capex_reserve=pf.capex_reserve, hoa=pf.hoa,
        utilities=pf.utilities, other_opex=pf.other_opex, operating_expenses=pf.operating_expenses,
        noi=pf.noi, noi_after_reserves=pf.noi_after_reserves, lines=pf.lines,
    )


def _proforma_for(inputs: FinancialInputs, assumptions: AssumptionSet) -> finance.ProForma:
    rent = inputs.market_rent_monthly or _ZERO
    return finance.operating_proforma(
        monthly_rent=rent, annual_taxes=_taxes(inputs), annual_insurance=_insurance(inputs),
        monthly_hoa=inputs.hoa_monthly, monthly_utilities=inputs.utilities_monthly,
        other_monthly_income=inputs.other_monthly_income,
        vacancy_pct=assumptions.vacancy_pct, management_pct=assumptions.management_pct,
        maintenance_pct=assumptions.maintenance_pct,
        capex_reserve_pct=assumptions.capex_reserve_pct,
    )


def analyze_ltr(inputs: FinancialInputs, assumptions: AssumptionSet) -> EngineOutputBlock:
    """Underwrite a long-term rental (§26.4–§26.5): conventional financing, operating pro-forma,
    and the full return-metric set plus the 5-year equity/IRR view."""
    if inputs.market_rent_monthly is None:
        raise UnsupportedStrategyError("LTR requires a market-rent estimate")
    warnings: list[str] = []
    _warn_thin(inputs, warnings)
    price = inputs.price
    pf = _proforma_for(inputs, assumptions)

    rehab_total_mid = _rehab_total(inputs.rehab, assumptions, "mid")
    closing_buy = finance.closing_costs_buy(price, closing_pct=assumptions.closing_pct_buy).value
    down = price * assumptions.conventional_down_pct
    loan = price - down
    payment = finance.mortgage_payment(
        loan, assumptions.conventional_rate, assumptions.conventional_term_years
    )
    annual_ds = payment * _TWELVE

    stab_holding = finance.holding_costs(
        annual_taxes=_taxes(inputs), annual_insurance=_insurance(inputs),
        monthly_utilities=inputs.utilities_monthly, monthly_hoa=inputs.hoa_monthly,
        months=assumptions.stabilization_months,
    )
    acq = _acquisition_block(price, closing_buy, inputs.rehab, assumptions, stab_holding, "mid")
    cash_in = finance.cash_invested(
        down_payment=down, closing_buy=closing_buy, rehab_total=rehab_total_mid,
        holding_to_stabilization=stab_holding.value,
    )

    cash_flow = finance.monthly_cash_flow(
        pf.effective_gross_income, pf.operating_expenses, pf.capex_reserve, annual_ds
    )
    metrics = ReturnMetrics(
        cap_rate=finance.cap_rate(pf.noi, price),
        cap_rate_arv=finance.cap_rate(pf.noi, (inputs.arv.point if inputs.arv else _ZERO)),
        cash_flow_monthly=cash_flow,
        coc=finance.cash_on_cash(cash_flow * _TWELVE, cash_in.value),
        dscr=finance.dscr(pf.noi, annual_ds),
        grm=finance.grm(price, pf.gross_potential_rent),
        breakeven_occupancy=finance.breakeven_occupancy(
            pf.operating_expenses, annual_ds, pf.gross_potential_rent
        ),
        cash_invested=cash_in.value,
        lines=cash_in.lines,
    )
    five_year = _five_year(inputs, assumptions, cash_flow, loan, payment, cash_in.value)
    return EngineOutputBlock(
        engine_version=ENGINE_VERSION, strategy=Strategy.LTR.value,
        arv=inputs.arv, as_is=inputs.as_is,
        rent_ltr=Interval(point=inputs.market_rent_monthly), all_in=acq.all_in, acquisition=acq,
        financing=_financing_scenarios(inputs, assumptions, rehab_total_mid),
        proforma=_proforma_out(pf), return_metrics=metrics, five_year=five_year,
        confidence=_min_confidence(inputs), warnings=warnings,
    )


def _five_year(
    inputs: FinancialInputs, assumptions: AssumptionSet, monthly_cash_flow: Decimal,
    loan: Decimal, payment: Decimal, cash_invested_amt: Decimal,
) -> FiveYearProjection:
    """The §26.5 5-year equity/IRR view: appreciation + amortization + cash flow − exit costs,
    turned into an equity multiple and a simple annualized IRR."""
    years = assumptions.hold_years
    appr = inputs.appreciation_pct if inputs.appreciation_pct is not None else (
        assumptions.appreciation_pct
    )
    basis = (inputs.arv.point if inputs.arv else None) or inputs.price
    exit_value = finance.future_value(basis, appr, years)
    principal_paid = finance.principal_paid_over(
        loan, assumptions.conventional_rate, assumptions.conventional_term_years, years * 12
    )
    remaining_loan = loan - principal_paid
    exit_costs = exit_value * (assumptions.agent_commission_pct + assumptions.closing_pct_sell)
    equity_at_exit = exit_value - remaining_loan - exit_costs
    annual_cf = monthly_cash_flow * _TWELVE
    total_cf = annual_cf * Decimal(years)

    # IRR cash-flow vector: −cash in at t0, annual cash flows t1..n, plus net equity at exit in n.
    flows = [-cash_invested_amt]
    for y in range(1, years + 1):
        flows.append(annual_cf + (equity_at_exit if y == years else _ZERO))
    total_returned = total_cf + equity_at_exit
    return FiveYearProjection(
        hold_years=years, appreciation_pct=appr, exit_value=exit_value,
        equity_at_exit=equity_at_exit.quantize(Decimal("0.01")),
        total_cash_flow=total_cf.quantize(Decimal("0.01")),
        equity_multiple=finance.equity_multiple(total_returned, cash_invested_amt),
        irr=finance.simple_irr(flows),
        lines=[
            CalcLine(label="exit_value", amount=exit_value,
                     note=f"{basis} × (1+{appr:.3f})^{years}"),
            CalcLine(label="principal_paid", amount=principal_paid, note="amortization over hold"),
            CalcLine(label="exit_costs", amount=-exit_costs.quantize(Decimal("0.01")),
                     note="commission + closing on sale"),
            CalcLine(label="equity_at_exit", amount=equity_at_exit.quantize(Decimal("0.01"))),
        ],
    )


# --- BRRRR (§26.7) ----------------------------------------------------------------------


def analyze_brrrr(inputs: FinancialInputs, assumptions: AssumptionSet) -> EngineOutputBlock:
    """Underwrite BRRRR (§26.7): flip-through-rehab on hard money, then a DSCR-constrained
    cash-out refi. The headline metric is `capital_left_in`; "infinite return" is flagged when it
    reaches zero (all capital recovered)."""
    if inputs.arv is None or inputs.market_rent_monthly is None:
        raise UnsupportedStrategyError("BRRRR requires both an ARV and a market-rent estimate")
    warnings: list[str] = []
    _warn_thin(inputs, warnings)
    price = inputs.price
    months = assumptions.holding_months
    arv_pt = inputs.arv.point

    rehab_total_mid = _rehab_total(inputs.rehab, assumptions, "mid")
    closing_buy = finance.closing_costs_buy(price, closing_pct=assumptions.closing_pct_buy).value
    hm_loan = price * (_ONE - assumptions.hard_money_down_pct)
    hm_interest = finance.interest_only_payment(hm_loan, assumptions.hard_money_rate) * Decimal(
        months
    )
    holding = finance.holding_costs(
        annual_taxes=_taxes(inputs), annual_insurance=_insurance(inputs),
        monthly_utilities=inputs.utilities_monthly, monthly_hoa=inputs.hoa_monthly,
        months=months, loan_interest=hm_interest,
    )
    acq = _acquisition_block(price, closing_buy, inputs.rehab, assumptions, holding, "mid")
    cash_in = finance.cash_invested(
        down_payment=price * assumptions.hard_money_down_pct, closing_buy=closing_buy,
        rehab_total=rehab_total_mid, holding_to_stabilization=holding.value,
    )

    pf = _proforma_for(inputs, assumptions)
    refi = finance.brrrr_refinance(
        arv=arv_pt, refi_ltv=assumptions.refi_ltv, monthly_noi=pf.noi_after_reserves / _TWELVE,
        refi_rate=assumptions.dscr_rate, refi_term_years=assumptions.dscr_term_years,
        min_dscr=assumptions.dscr_min, payoff=hm_loan,
        refi_costs=finance.loan_costs(arv_pt * assumptions.refi_ltv, points=_ZERO).value,
        cash_invested_amt=cash_in.value,
    )
    refi_payment = finance.mortgage_payment(
        refi.new_loan, assumptions.dscr_rate, assumptions.dscr_term_years
    )
    annual_ds = refi_payment * _TWELVE
    post_cash_flow = finance.monthly_cash_flow(
        pf.effective_gross_income, pf.operating_expenses, pf.capex_reserve, annual_ds
    )
    if refi.capital_left_in <= _ZERO:
        warnings.append("infinite return — all invested capital recovered at refi")

    metrics = ReturnMetrics(
        cap_rate=finance.cap_rate(pf.noi, price),
        cash_flow_monthly=post_cash_flow,
        coc=finance.cash_on_cash(
            post_cash_flow * _TWELVE, refi.capital_left_in if refi.capital_left_in > _ZERO else _ONE
        ),
        dscr=refi.dscr_at_refi, cash_invested=cash_in.value, capital_left_in=refi.capital_left_in,
        lines=[*cash_in.lines, *refi.lines],
    )
    return EngineOutputBlock(
        engine_version=ENGINE_VERSION, strategy=Strategy.BRRRR.value,
        arv=inputs.arv, as_is=inputs.as_is,
        rent_ltr=Interval(point=inputs.market_rent_monthly), all_in=acq.all_in, acquisition=acq,
        financing=_financing_scenarios(inputs, assumptions, rehab_total_mid),
        proforma=_proforma_out(pf), return_metrics=metrics,
        confidence=_min_confidence(inputs), warnings=warnings,
    )


# --- Wholesale (§26.8) ------------------------------------------------------------------


def analyze_wholesale(inputs: FinancialInputs, assumptions: AssumptionSet) -> EngineOutputBlock:
    """Underwrite a wholesale assignment (§26.8): `MAO = ARV×0.70 − rehab − assignment_fee`, and
    the spread between MAO and the contract price. A negative spread means there's no deal at this
    price — shown, not hidden."""
    if inputs.arv is None:
        raise UnsupportedStrategyError("wholesale requires an ARV estimate")
    warnings: list[str] = []
    _warn_thin(inputs, warnings)
    arv_pt = inputs.arv.point
    rehab_total_mid = _rehab_total(inputs.rehab, assumptions, "mid")
    assignment_fee = Decimal("10000")  # config default (§26.8); market/user-overridable
    mao = Decimal("0.70") * arv_pt - rehab_total_mid - assignment_fee
    spread = mao - inputs.price
    metrics = ReturnMetrics(
        net_profit=assignment_fee.quantize(Decimal("0.01")),
        lines=[
            CalcLine(label="mao", amount=mao.quantize(Decimal("0.01")),
                     note="0.70×ARV − rehab − assignment_fee"),
            CalcLine(label="spread_vs_price", amount=spread.quantize(Decimal("0.01")),
                     note="MAO − contract price (negative → no deal)"),
            CalcLine(label="assignment_fee", amount=assignment_fee),
        ],
    )
    if spread < _ZERO:
        warnings.append("negative spread — contract price exceeds MAO")
    return EngineOutputBlock(
        engine_version=ENGINE_VERSION, strategy=Strategy.WHOLESALE.value,
        arv=inputs.arv, as_is=inputs.as_is, return_metrics=metrics,
        rule_70_check=finance.rule_70_check(inputs.price, rehab_total_mid, arv_pt),
        confidence=_min_confidence(inputs), warnings=warnings,
    )


# --- Dispatch ---------------------------------------------------------------------------

_ANALYZERS = {
    Strategy.FLIP: analyze_flip,
    Strategy.LTR: analyze_ltr,
    Strategy.BRRRR: analyze_brrrr,
    Strategy.WHOLESALE: analyze_wholesale,
}

# Strategies whose underwriting needs inputs beyond `FinancialInputs` (unit mix, acreage, leases)
# — deferred [F] per §26.8, surfaced explicitly rather than silently returning a wrong number.
_DEFERRED = {
    Strategy.STR, Strategy.HOUSE_HACK, Strategy.MULTIFAMILY, Strategy.LAND,
    Strategy.COMMERCIAL, Strategy.VALUE_ADD,
}


def analyze(
    strategy: Strategy, inputs: FinancialInputs, assumptions: AssumptionSet
) -> EngineOutputBlock:
    """Run the deterministic underwriting for one strategy (§26). Raises
    `UnsupportedStrategyError` for `overall` (a scoring construct, not an engine run — §25.4) and
    for the [F] strategies whose models aren't built yet."""
    if strategy is Strategy.OVERALL:
        raise UnsupportedStrategyError("`overall` is a scoring construct, not an engine run")
    analyzer = _ANALYZERS.get(strategy)
    if analyzer is None:
        raise UnsupportedStrategyError(f"strategy '{strategy.value}' underwriting not yet built")
    return analyzer(inputs, assumptions)


__all__ = [
    "ENGINE_VERSION",
    "UnsupportedStrategyError",
    "analyze",
    "analyze_brrrr",
    "analyze_flip",
    "analyze_ltr",
    "analyze_wholesale",
]
