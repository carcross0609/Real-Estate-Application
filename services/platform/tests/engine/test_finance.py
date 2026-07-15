"""Financial-primitive unit tests (no DB) — the §26.2–§26.6 formulas, exact in Decimal. These
pin the arithmetic *and* the explainability contract: every primitive returns its ledger, and
the ledger reconstructs the number. A drift here is a wrong dollar figure in front of an
investor, so the tests are concrete (known inputs → known outputs), not property-based vibes.
"""

from decimal import Decimal

from deallens.modules.engine import finance

_D = Decimal


# --- Acquisition / rehab / holding ------------------------------------------------------


def test_closing_costs_buy_pct_plus_fixed() -> None:
    c = finance.closing_costs_buy(_D("300000"), closing_pct=_D("0.02"), fixed_fees=_D("500"))
    assert c.value == _D("6500.00")
    assert sum(line.amount for line in c.lines) == _D("6500.00")


def test_rehab_contingency_normal_vs_flagged() -> None:
    normal = finance.rehab_with_contingency(
        _D("40000"), contingency_pct=_D("0.15"), contingency_pct_flagged=_D("0.20"),
        red_flags_present=False,
    )
    flagged = finance.rehab_with_contingency(
        _D("40000"), contingency_pct=_D("0.15"), contingency_pct_flagged=_D("0.20"),
        red_flags_present=True,
    )
    assert normal.value == _D("46000.00")  # 40k + 15%
    assert flagged.value == _D("48000.00")  # 40k + 20%


def test_holding_costs_sum() -> None:
    h = finance.holding_costs(
        annual_taxes=_D("6000"), annual_insurance=_D("1200"), monthly_utilities=_D("150"),
        monthly_hoa=_D("0"), months=6, loan_interest=_D("5000"),
    )
    # (6000+1200)/12*6 = 3600 ; utilities 150*6 = 900 ; + loan 5000 = 9500
    assert h.value == _D("9500.00")


def test_all_in_rolls_up() -> None:
    a = finance.all_in(_D("300000"), _D("6000"), _D("46000"), _D("9500"))
    assert a.value == _D("361500.00")


# --- Financing --------------------------------------------------------------------------


def test_mortgage_payment_known_value() -> None:
    # $240,000 @ 7% / 30yr ≈ $1,596.73/mo (standard amortization).
    pay = finance.mortgage_payment(_D("240000"), _D("0.07"), 30)
    assert abs(pay - _D("1596.73")) <= _D("0.05")


def test_zero_rate_is_straight_line() -> None:
    pay = finance.mortgage_payment(_D("120000"), _D("0"), 10)
    assert pay == _D("1000.00")  # 120000 / 120 months


def test_cash_deal_zero_payment() -> None:
    assert finance.mortgage_payment(_D("0"), _D("0.07"), 30) == _D("0")


def test_interest_only_payment() -> None:
    # 270k @ 11.5% IO → 270000*0.115/12 = 2587.50
    assert finance.interest_only_payment(_D("270000"), _D("0.115")) == _D("2587.50")


def test_amortization_balance_decreases_and_truncates() -> None:
    sched = finance.amortization_schedule(_D("240000"), _D("0.07"), 30, months=12)
    assert len(sched) == 12
    assert sched[0].balance < _D("240000")
    assert sched[-1].balance < sched[0].balance
    # Interest + principal ≈ the level payment each month.
    assert abs((sched[0].interest + sched[0].principal) - _D("1596.73")) <= _D("0.05")


def test_principal_paid_over_positive() -> None:
    paid = finance.principal_paid_over(_D("240000"), _D("0.07"), 30, 60)
    assert paid > _D("0") and paid < _D("240000")


def test_loan_costs_points() -> None:
    c = finance.loan_costs(_D("270000"), points=_D("0.02"), origination=_D("1000"))
    assert c.value == _D("6400.00")  # 5400 pts + 1000


# --- Pro-forma --------------------------------------------------------------------------


def test_proforma_noi_definitions() -> None:
    pf = finance.operating_proforma(
        monthly_rent=_D("2000"), annual_taxes=_D("4000"), annual_insurance=_D("1200"),
        vacancy_pct=_D("0.08"), management_pct=_D("0.09"), maintenance_pct=_D("0.08"),
        capex_reserve_pct=_D("0.07"),
    )
    assert pf.gross_potential_rent == _D("24000.00")
    assert pf.vacancy == _D("1920.00")  # 8% of GPR
    assert pf.effective_gross_income == _D("22080.00")  # GPR − vacancy
    # NOI excludes capex reserve; after-reserves subtracts it.
    assert pf.noi > pf.noi_after_reserves
    assert pf.noi_after_reserves == (pf.noi - pf.capex_reserve).quantize(_D("0.01"))


def test_proforma_management_on_egi_not_gpr() -> None:
    pf = finance.operating_proforma(
        monthly_rent=_D("2000"), annual_taxes=_D("0"), annual_insurance=_D("0"),
        vacancy_pct=_D("0.08"), management_pct=_D("0.09"), maintenance_pct=_D("0"),
        capex_reserve_pct=_D("0"),
    )
    assert pf.management == (pf.effective_gross_income * _D("0.09")).quantize(_D("0.01"))


# --- Return metrics ---------------------------------------------------------------------


def test_cap_rate_and_none_on_zero() -> None:
    assert finance.cap_rate(_D("18000"), _D("300000")) == _D("0.06000")
    assert finance.cap_rate(_D("18000"), _D("0")) is None


def test_dscr_and_none_on_cash() -> None:
    assert finance.dscr(_D("24000"), _D("20000")) == _D("1.20000")
    assert finance.dscr(_D("24000"), _D("0")) is None  # cash deal → undefined


def test_cash_on_cash_none_when_nothing_invested() -> None:
    assert finance.cash_on_cash(_D("6000"), _D("60000")) == _D("0.10000")
    assert finance.cash_on_cash(_D("6000"), _D("0")) is None


def test_breakeven_occupancy() -> None:
    be = finance.breakeven_occupancy(_D("10000"), _D("14000"), _D("30000"))
    assert be == _D("0.80000")  # (10k+14k)/30k


def test_cash_invested_subtracts_financed_rehab() -> None:
    c = finance.cash_invested(
        down_payment=_D("30000"), closing_buy=_D("6000"), rehab_total=_D("40000"),
        holding_to_stabilization=_D("2000"), financed_rehab=_D("40000"),
    )
    assert c.value == _D("38000.00")  # rehab financed by loan removed from cash


# --- Flip -------------------------------------------------------------------------------


def test_selling_costs_on_arv() -> None:
    s = finance.selling_costs(
        _D("400000"), agent_commission_pct=_D("0.055"), closing_pct_sell=_D("0.01"),
        concessions_pct=_D("0.01"),
    )
    assert s.value == _D("30000.00")  # 7.5% of 400k


def test_flip_net_profit_can_be_negative() -> None:
    p = finance.flip_net_profit(_D("400000"), _D("380000"), _D("30000"), _D("6000"))
    assert p.value == _D("-16000.00")  # a losing flip is shown, not floored


def test_flip_margin_and_annualized_roi() -> None:
    margin = finance.flip_margin(_D("30000"), _D("100000"))
    assert margin == _D("0.30000")
    assert finance.annualized_roi(margin, 6) == _D("0.60000")  # ×12/6


def test_rule_70() -> None:
    assert finance.rule_70_check(_D("200000"), _D("40000"), _D("400000")) is True  # 240k ≤ 280k
    assert finance.rule_70_check(_D("260000"), _D("40000"), _D("400000")) is False  # 300k > 280k


# --- BRRRR refi -------------------------------------------------------------------------


def test_brrrr_refi_ltv_bound() -> None:
    # High NOI → LTV binds; loan = 75% of ARV, cash-out = loan − payoff − costs.
    refi = finance.brrrr_refinance(
        arv=_D("400000"), refi_ltv=_D("0.75"), monthly_noi=_D("3000"), refi_rate=_D("0.0775"),
        refi_term_years=30, min_dscr=_D("1.20"), payoff=_D("270000"), refi_costs=_D("5000"),
        cash_invested_amt=_D("90000"),
    )
    assert refi.new_loan == _D("300000.00")
    assert refi.binding_constraint == "ltv"
    assert refi.cash_out == _D("25000.00")  # 300k − 270k − 5k
    assert refi.capital_left_in == _D("65000.00")  # 90k − 25k


def test_brrrr_refi_dscr_bound_shrinks_loan() -> None:
    # Thin NOI → DSCR binds and sizes the loan below the LTV max.
    refi = finance.brrrr_refinance(
        arv=_D("400000"), refi_ltv=_D("0.75"), monthly_noi=_D("1500"), refi_rate=_D("0.0775"),
        refi_term_years=30, min_dscr=_D("1.20"), payoff=_D("270000"), refi_costs=_D("5000"),
        cash_invested_amt=_D("90000"),
    )
    assert refi.binding_constraint == "dscr"
    assert refi.new_loan < _D("300000")
    assert refi.dscr_at_refi is not None and refi.dscr_at_refi >= _D("1.19")


# --- 5-year projection ------------------------------------------------------------------


def test_future_value_compounds() -> None:
    fv = finance.future_value(_D("300000"), _D("0.03"), 5)
    assert abs(fv - _D("347782.23")) <= _D("1")


def test_simple_irr_positive_stream() -> None:
    # −100k then five years of 10k + 120k terminal → a positive IRR.
    irr = finance.simple_irr([_D("-100000"), _D("10000"), _D("10000"), _D("10000"),
                              _D("10000"), _D("130000")])
    assert irr is not None and irr > _D("0.10")


def test_simple_irr_none_on_all_negative() -> None:
    assert finance.simple_irr([_D("-100"), _D("-50")]) is None


def test_equity_multiple() -> None:
    assert finance.equity_multiple(_D("150000"), _D("100000")) == _D("1.50000")
