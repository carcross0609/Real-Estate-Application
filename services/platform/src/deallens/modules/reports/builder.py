"""Report narrative builder — pure, grounded, no I/O (§13.3, FR-060).

Turns a `ReportContextPack` into the report's sections. This is a **grounded** generator: every
number in the prose is read straight from the pack (which is the engines' own typed output), so a
figure can never be hallucinated and the report reconciles with the analyzer by construction. The
deterministic template generator here is the shipped default; a future LLM generator plugs in
behind the same `(pack) -> list[ReportSection]` shape (like the vision provider) to add polish,
still constrained to the pack's numbers and post-validated (§13.3).

Sections cover exactly what a comprehensive investment report needs (the prompt's list): the
opportunity, recommended strategies, supporting calculations, comparables, renovation estimate,
financial projections, risks, and exit scenarios. A section whose inputs are absent is skipped —
the report never shows an empty or fabricated section.
"""

from __future__ import annotations

from decimal import Decimal

from deallens.core.enums import Strategy
from deallens.modules.reports.schemas import ReportContextPack, ReportSection

_STRATEGY_LABELS = {
    "flip": "Fix & Flip", "ltr": "Long-Term Rental", "brrrr": "BRRRR",
    "wholesale": "Wholesale", "str": "Short-Term Rental", "house_hack": "House Hack",
}


def _money(v: Decimal | str | None) -> str:
    if v is None:
        return "—"
    d = Decimal(str(v))
    return f"${d:,.0f}"


def _pct(v: Decimal | None, places: int = 1) -> str:
    if v is None:
        return "—"
    return f"{Decimal(v) * 100:.{places}f}%"


def _num(v: Decimal | None, places: int = 2) -> str:
    if v is None:
        return "—"
    return f"{Decimal(v):.{places}f}"


def _label(strategy: str) -> str:
    return _STRATEGY_LABELS.get(strategy, strategy.replace("_", " ").title())


def _band(v: Decimal, high: str, mid: str, low: str, *, hi: int = 60, lo: int = 40) -> str:
    """A qualitative descriptor for a 0–100 figure — keeps the prose readable and the f-strings
    short."""
    return high if v >= hi else mid if v >= lo else low


# --- Sections ---------------------------------------------------------------------------


def _opportunity(pack: ReportContextPack) -> ReportSection | None:
    s = pack.score
    if s is None:
        return None
    win = _label(s.winning_strategy.value) if s.winning_strategy else "n/a"
    lines = [
        f"**Overall score: {_num(s.overall_score, 1)}/100 (grade {s.grade}).** "
        f"Recommendation: **{s.recommendation.value.replace('_', ' ').title()}**. "
        f"Best-fit strategy: **{win}**.",
        "",
        f"- Risk score: {_num(s.risk_score, 0)}/100 "
        f"({_band(s.risk_score, 'elevated', 'moderate', 'low')})",
        f"- Confidence: {_num(s.confidence_score, 0)}/100 "
        f"({_band(s.confidence_score, 'high', 'moderate', 'limited — verify inputs', hi=70)})",
    ]
    if s.explanation.positives:
        lines += ["", "**What's working:**"] + [f"- {p}" for p in s.explanation.positives]
    if s.explanation.negatives:
        lines += ["", "**What to watch:**"] + [f"- {n}" for n in s.explanation.negatives]
    if s.explanation.caps:
        lines += ["", "**Hard caps applied:** " + ", ".join(s.explanation.caps)
                  + " — these limit the score pending verification (see Risks)."]
    return ReportSection(id="opportunity", title="The Opportunity", body_markdown="\n".join(lines))


def _recommended_strategies(pack: ReportContextPack) -> ReportSection | None:
    s = pack.score
    if s is None or not s.strategy_scores:
        return None
    from deallens.modules.scoring.weights import grade_for
    ranked = sorted(s.strategy_scores, key=lambda x: x.score, reverse=True)
    rows = ["| Strategy | Score | Grade |", "|---|---|---|"]
    for ss in ranked:
        rows.append(
            f"| {_label(ss.strategy.value)} | {_num(ss.score, 1)} | {grade_for(ss.score)} |"
        )
    body = [
        "Strategies are scored independently; the overall score is the best fit, never an average "
        "— a property excellent for one strategy is a strong deal.",
        "", *rows,
    ]
    return ReportSection(id="strategies", title="Recommended Investment Strategies",
                         body_markdown="\n".join(body))


def _supporting_calculations(pack: ReportContextPack) -> ReportSection | None:
    strat = _primary_strategy(pack)
    if strat is None:
        return None
    block = pack.analyses[strat]
    rm = block.return_metrics
    lines = [f"Underwriting for the recommended **{_label(strat)}** strategy "
             f"(engine {block.engine_version}). All figures are the P50 case; ranges reflect the "
             "ARV and rehab bands.", ""]
    if block.acquisition is not None:
        a = block.acquisition
        lines += [
            "**Acquisition (all-in):**",
            f"- Purchase price: {_money(a.price)}",
            f"- Closing costs: {_money(a.closing_costs_buy)}",
            f"- Rehab (incl. contingency): {_money(a.rehab_total)}",
            f"- Holding costs: {_money(a.holding_costs)}",
            f"- **All-in: {_money(a.all_in)}**",
            "",
        ]
    lines += ["**Return metrics:**"]
    metric_rows = [
        ("Cap rate", _pct(rm.cap_rate)), ("Cash-on-cash", _pct(rm.coc)),
        ("DSCR", _num(rm.dscr)), ("Monthly cash flow", _money(rm.cash_flow_monthly)),
        ("Net profit (flip)", _money(rm.net_profit)), ("Flip margin", _pct(rm.flip_margin)),
        ("Annualized ROI", _pct(rm.roi_annualized)),
        ("Capital left in (BRRRR)", _money(rm.capital_left_in)),
        ("Break-even occupancy", _pct(rm.breakeven_occupancy)),
    ]
    lines += [f"- {name}: {val}" for name, val in metric_rows if val != "—"]
    if block.rule_70_check is not None:
        lines += ["", f"70% rule: **{'passes' if block.rule_70_check else 'does not pass'}** "
                  "(sanity check, not gospel)."]
    return ReportSection(id="calculations", title="Supporting Calculations",
                         body_markdown="\n".join(lines))


def _comparables(pack: ReportContextPack) -> ReportSection | None:
    if pack.valuation is None or pack.valuation.arv is None:
        return None
    arv = pack.valuation.arv
    comps = [c for c in arv.comps if c.included][:8]
    lines = [
        f"**After-Repair Value: {_money(arv.point)}** "
        f"(range {_money(arv.low)}–{_money(arv.high)}, {arv.comp_count} comps, "
        f"method: {arv.method or 'comp-based'}, confidence {_pct(arv.confidence, 0)}).",
    ]
    if pack.valuation.as_is is not None:
        lines.append(f"As-is value: **{_money(pack.valuation.as_is.point)}**.")
    if pack.valuation.rent_ltr is not None:
        lines.append(f"Market rent: **{_money(pack.valuation.rent_ltr.point)}/mo**.")
    if comps:
        lines += ["", "| Comp | Sale/Ask | Adjusted | Distance | Similarity |",
                  "|---|---|---|---|---|"]
        for c in comps:
            dist = f"{Decimal(c.distance_m) / 1609:.2f} mi" if c.distance_m is not None else "—"
            lines.append(
                f"| {str(c.property_id)[:8]} | {_money(c.observed_value)} | "
                f"{_money(c.adjusted_value)} | {dist} | {_pct(c.similarity, 0)} |"
            )
    return ReportSection(id="comparables", title="Comparable Properties",
                         body_markdown="\n".join(lines))


def _renovation(pack: ReportContextPack) -> ReportSection | None:
    c = pack.condition
    if c is None or c.rehab is None:
        return None
    r = c.rehab
    lines = [
        f"Estimated rehab: **{_money(r.total_mid)}** "
        f"(range {_money(r.total_low)}–{_money(r.total_high)}, "
        f"{_pct(r.contingency_pct, 0)} contingency"
        + (", elevated for red flags" if r.red_flags_present else "") + ").",
        "",
        f"- Cosmetic: {_money(r.cosmetic_low)}–{_money(r.cosmetic_high)}",
        f"- Major/structural: {_money(r.major_low)}–{_money(r.major_high)}",
    ]
    if c.renovation_difficulty is not None:
        lines.append(f"- Renovation difficulty: {c.renovation_difficulty}/5")
    if r.line_items:
        lines += ["", "**Line items:**"]
        for li in r.line_items[:12]:
            flag = " *(inspection-contingent)*" if li.inspection_contingent else ""
            lines.append(f"- {li.system.replace('_', ' ').title()} "
                         f"({li.category}): {_money(li.low)}–{_money(li.high)}{flag}")
    if c.red_flags:
        lines += ["", "**Red flags:** " + ", ".join(
            f"{f.type.value.replace('_', ' ')} ({f.severity.value})" for f in c.red_flags)]
    return ReportSection(id="renovation", title="Renovation Estimate",
                         body_markdown="\n".join(lines))


def _financial_projections(pack: ReportContextPack) -> ReportSection | None:
    strat = _primary_strategy(pack)
    if strat is None:
        return None
    block = pack.analyses[strat]
    lines: list[str] = []
    if block.proforma is not None:
        pf = block.proforma
        lines += [
            "**Operating pro-forma (annual):**",
            f"- Gross potential rent: {_money(pf.gross_potential_rent)}",
            f"- Effective gross income: {_money(pf.effective_gross_income)}",
            f"- Operating expenses: {_money(pf.operating_expenses)}",
            f"- NOI: **{_money(pf.noi)}** (after reserves: {_money(pf.noi_after_reserves)})",
            "",
        ]
    if block.five_year is not None:
        fy = block.five_year
        lines += [
            f"**{fy.hold_years}-year projection** "
            f"(appreciation {_pct(fy.appreciation_pct)}):",
            f"- Projected exit value: {_money(fy.exit_value)}",
            f"- Equity at exit: {_money(fy.equity_at_exit)}",
            f"- Cumulative cash flow: {_money(fy.total_cash_flow)}",
            f"- Equity multiple: {_num(fy.equity_multiple)}×"
            + (f" · IRR: {_pct(fy.irr)}" if fy.irr is not None else ""),
        ]
    if not lines:
        return None
    return ReportSection(id="projections", title="Financial Projections",
                         body_markdown="\n".join(lines))


def _risks(pack: ReportContextPack) -> ReportSection | None:
    s = pack.score
    if s is None:
        return None
    lines = [f"**Risk score: {_num(s.risk_score, 0)}/100.** Key contributors:"]
    contributors = []
    cat = s.category_scores
    if cat.renovation_complexity is not None:
        contributors.append(f"renovation complexity {_num(cat.renovation_complexity, 0)}/100")
    if cat.financing_difficulty is not None:
        contributors.append(f"financing difficulty {_num(cat.financing_difficulty, 0)}/100")
    if pack.condition and pack.condition.red_flags:
        contributors.append(f"{len(pack.condition.red_flags)} structural red flag(s)")
    if s.explanation.caps:
        contributors.append("hard caps: " + ", ".join(s.explanation.caps))
    lines += [f"- {c}" for c in contributors] if contributors else ["- No elevated risk signals."]
    lines += ["", "*Estimates carry uncertainty; confidence of "
              f"{_num(s.confidence_score, 0)}/100 reflects data completeness. Verify flagged "
              "items with a professional inspection before committing.*"]
    return ReportSection(id="risks", title="Risks", body_markdown="\n".join(lines))


def _exit_scenarios(pack: ReportContextPack) -> ReportSection | None:
    lines: list[str] = []
    flip = pack.analyses.get("flip")
    if flip is not None and flip.arv is not None:
        rm = flip.return_metrics
        lines.append(f"**Resale (flip):** sell at ARV {_money(flip.arv.point)} for a projected "
                     f"net profit of {_money(rm.net_profit)} ({_pct(rm.flip_margin)} on cash).")
    brrrr = pack.analyses.get("brrrr")
    if brrrr is not None:
        rm = brrrr.return_metrics
        lines.append(f"**Refinance & hold (BRRRR):** cash-out refi leaves "
                     f"{_money(rm.capital_left_in)} in the deal, then rent at "
                     f"{_money(rm.cash_flow_monthly)}/mo cash flow.")
    ltr = pack.analyses.get("ltr")
    if ltr is not None and ltr.five_year is not None:
        fy = ltr.five_year
        lines.append(f"**Long-term hold:** {fy.hold_years}-year equity of "
                     f"{_money(fy.equity_at_exit)} via appreciation + amortization + cash flow.")
    if pack.valuation and pack.valuation.appreciation and pack.valuation.appreciation.annual_pct:
        ap = pack.valuation.appreciation
        lines.append(f"*Market appreciation assumed at {_pct(ap.annual_pct)}/yr "
                     f"({ap.geo_level or 'market'}-level, labeled honestly — not parcel-precise).*")
    if not lines:
        return None
    return ReportSection(id="exits", title="Exit Scenarios",
                         body_markdown="\n\n".join(lines))


# --- Assembly ---------------------------------------------------------------------------


def _primary_strategy(pack: ReportContextPack) -> str | None:
    """The strategy the calculations/projections focus on: the scored winner if it has an analysis,
    else any available analysis."""
    if pack.score and pack.score.winning_strategy:
        key = pack.score.winning_strategy.value
        if key in pack.analyses:
            return key
    for key in ("ltr", "flip", "brrrr", "wholesale"):
        if key in pack.analyses:
            return key
    return next(iter(pack.analyses), None)


_SECTION_BUILDERS = (
    _opportunity, _recommended_strategies, _supporting_calculations, _comparables,
    _renovation, _financial_projections, _risks, _exit_scenarios,
)


def compose_narrative(pack: ReportContextPack) -> list[ReportSection]:
    """Build the full report narrative from the grounded pack (§13.3). Sections with no inputs are
    skipped, so the report is comprehensive where data exists and honest where it doesn't."""
    sections = [b(pack) for b in _SECTION_BUILDERS]
    return [s for s in sections if s is not None]


def market_context_block(pack: ReportContextPack) -> Strategy | None:  # pragma: no cover
    """Reserved hook (kept for symmetry with the engine seams); the market summary is folded into
    the opportunity/exit sections today."""
    return None


__all__ = ["compose_narrative"]
