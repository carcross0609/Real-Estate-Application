"""Report rendering — pure, no I/O (FR-060, §15). Turns the grounded sections into the two
exportable formats: Markdown (portable, diffable) and a professionally-formatted, **self-contained**
HTML document (inline CSS, no external assets — safe to email, print, or hand to a headless
Chromium for PDF).

Security (§15): report bodies can embed user-authored text (notes, listing remarks), so the HTML
renderer **escapes all content first**, then applies the small, fixed markdown subset the builder
emits (headings, bold/italic, bullet lists, tables). No raw HTML from the content ever reaches the
page — an XSS/SSRF-via-PDF vector is closed by construction, not by trusting the input.
"""

from __future__ import annotations

import html
import re

from deallens.modules.reports.schemas import ReportContextPack, ReportSection

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")


def _inline(text: str) -> str:
    """Escape, then apply the inline markdown subset (bold, italic). Escaping first means the
    content can never inject markup; the `**`/`*` markers are added by our own builder, not the
    data."""
    out = html.escape(text)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    out = _ITALIC.sub(r"<em>\1</em>", out)
    return out


def _table_html(rows: list[str]) -> str:
    """A markdown pipe-table block → an HTML table. The second row (`|---|`) is the separator."""
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    header, body = cells[0], cells[2:] if len(cells) > 2 else []
    head = "".join(f"<th>{_inline(c)}</th>" for c in header)
    trs = "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{trs}</tbody></table>"


def _md_to_html(md: str) -> str:
    """Convert the builder's markdown subset to HTML: headings (`**...**` lines act as leads),
    bullet lists, pipe tables, and paragraphs. Deterministic and dependency-free."""
    lines = md.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.lstrip().startswith("|"):  # gather a table block
            block = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                block.append(lines[i])
                i += 1
            out.append(_table_html(block))
            continue
        if line.lstrip().startswith("- "):  # gather a bullet list
            items = []
            while i < len(lines) and lines[i].lstrip().startswith("- "):
                items.append(f"<li>{_inline(lines[i].lstrip()[2:])}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue
        out.append(f"<p>{_inline(line)}</p>")
        i += 1
    return "\n".join(out)


REPORT_CSS = """
:root { --ink:#1a2332; --muted:#5b6b82; --line:#e2e8f0; --accent:#1f6feb; --bg:#ffffff; }
* { box-sizing: border-box; }
body { font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  color: var(--ink); background: var(--bg); margin: 0; line-height: 1.55; }
.report { max-width: 820px; margin: 0 auto; padding: 48px 40px 72px; }
.report-header { border-bottom: 3px solid var(--accent); padding-bottom: 18px; margin-bottom: 8px; }
.report-header h1 { font-size: 26px; margin: 0 0 4px; letter-spacing: -0.01em; }
.report-header .sub { color: var(--muted); font-size: 14px; }
.report-header .facts { margin-top: 12px; font-size: 13px; color: var(--muted); }
h2 { font-size: 18px; margin: 34px 0 10px; padding-top: 10px; border-top: 1px solid var(--line); }
p { margin: 8px 0; } ul { margin: 8px 0; padding-left: 22px; } li { margin: 3px 0; }
strong { color: var(--ink); } em { color: var(--muted); }
table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 13px; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--line); }
th { color: var(--muted); font-weight: 600; text-transform: uppercase; font-size: 11px;
  letter-spacing: 0.04em; }
.footer { margin-top: 40px; padding-top: 14px; border-top: 1px solid var(--line);
  color: var(--muted); font-size: 11px; }
@media print { .report { padding: 0; } h2 { break-after: avoid; } table { break-inside: avoid; } }
"""


def _header(pack: ReportContextPack) -> str:
    f = pack.facts
    addr = ", ".join(p for p in (f.address_line1, f.city, f.state, f.zip) if p) or "Property"
    facts = " · ".join(p for p in (
        f.property_type, f"{f.beds} bd" if f.beds else None,
        f"{f.baths} ba" if f.baths else None, f"{f.sqft:,} sqft" if f.sqft else None,
        f"built {f.year_built}" if f.year_built else None,
        f"listed {f.list_price}" if f.list_price else None,
    ) if p)
    stamp = pack.generated_at.strftime("%B %d, %Y") if pack.generated_at else ""
    return (
        f'<div class="report-header"><h1>Investment Analysis</h1>'
        f'<div class="sub">{html.escape(addr)}</div>'
        f'<div class="facts">{html.escape(facts)}{" · " + stamp if stamp else ""}</div></div>'
    )


def to_html(pack: ReportContextPack, sections: list[ReportSection]) -> str:
    """A complete, self-contained HTML document — professionally formatted, PDF-ready (FR-060)."""
    body = [_header(pack)]
    for s in sections:
        body.append(f"<h2>{html.escape(s.title)}</h2>")
        body.append(_md_to_html(s.body_markdown))
    body.append(
        '<div class="footer">Generated by DealLens. Estimates carry uncertainty and are not '
        "investment advice; verify all figures and inspect the property before committing "
        "capital.</div>"
    )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<meta name=\"robots\" content=\"noindex\">"
        f"<title>Investment Analysis</title><style>{REPORT_CSS}</style></head>"
        f'<body><div class="report">{"".join(body)}</div></body></html>'
    )


def to_markdown(pack: ReportContextPack, sections: list[ReportSection]) -> str:
    """The report as portable Markdown (FR-060 export)."""
    f = pack.facts
    addr = ", ".join(p for p in (f.address_line1, f.city, f.state, f.zip) if p) or "Property"
    out = [f"# Investment Analysis — {addr}", ""]
    if pack.generated_at:
        out.append(f"*Generated {pack.generated_at.strftime('%B %d, %Y')}*")
        out.append("")
    for s in sections:
        out += [f"## {s.title}", "", s.body_markdown, ""]
    out.append("---")
    out.append("*Generated by DealLens. Not investment advice; verify all figures.*")
    return "\n".join(out)


__all__ = ["to_html", "to_markdown"]
