"""Plain-text rendering of findings, tables and status blocks. No model."""
from __future__ import annotations

from decimal import Decimal

from . import analysis as an
from .detectors import SEVERITY_LABEL, Finding
from .records import Records


def fmt(v: object) -> str:
    if isinstance(v, Decimal):
        return f"${v:,.2f}" if v == v.quantize(Decimal("0.01")) else str(v)
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".") if v != int(v) else f"{int(v)}"
    if isinstance(v, dict):
        return ", ".join(f"{k}={fmt(x)}" for k, x in v.items())
    if isinstance(v, list):
        return ", ".join(fmt(x) for x in v)
    return str(v)


def finding_block(f: Finding, numbers: bool = True) -> str:
    out = [f"[{SEVERITY_LABEL[f.severity]}] {f.title}", "", f.summary, ""]
    out.append("Evidence:")
    out += [f"  - {c}" for c in f.evidence]
    if f.uncertainty:
        out.append("")
        out.append("Uncertain:")
        out += [f"  - {u}" for u in f.uncertainty]
    out.append("")
    out.append(f"Next step: {f.next_step}")
    if numbers and f.numbers:
        out.append("")
        out.append("Numbers: " + "; ".join(f"{k}={fmt(v)}" for k, v in f.numbers.items()))
    if f.applied_policies:
        out.append("Applied: " + ", ".join(f.applied_policies))
    return "\n".join(out)


def findings_index(findings: list[Finding], skip: str | None = None) -> str:
    rows = [f for f in findings if f.id != skip]
    if not rows:
        return "  (none)"
    return "\n".join(f"  - [{SEVERITY_LABEL[f.severity]}] {f.title}  ({f.id})" for f in rows)


def runway_table(records: Records, burn: dict[str, float], window: tuple) -> str:
    counts = an.opening_and_closing(records)
    daily = an.daily_service_ingredients(records)
    rows = []
    for ing, (_, close) in counts.items():
        b = burn.get(ing, 0.0)
        days = an.runway_days(close.quantity, b)
        rows.append((days if days is not None else 1e9, ing, close.quantity, close.unit, b, days, ing in daily))
    rows.sort()
    head = f"{'ingredient':32s} {'on hand':>10s} {'use/day':>9s} {'days':>6s}"
    lines = [head, "-" * len(head)]
    for _, ing, qty, unit, b, days, is_daily in rows:
        tag = "" if is_daily else "  (weekend item; days are a 7-day average)"
        lines.append(f"{ing:32s} {fmt(qty):>6s} {unit:3s} {fmt(b):>9s} {('%.1f' % days) if days is not None else 'n/a':>6s}{tag}")
    lines.append(f"(use/day = theoretical recipe use {window[0]}..{window[1]} / 7; counts from the "
                 f"{list(counts.values())[0][1].counted_at.date()} close)")
    return "\n".join(lines)


def brief_text(records: Records, findings: list[Finding], burn: dict[str, float], window: tuple,
               retained_block: str | None = None) -> str:
    head = (f"Lantern Yard Kitchen ({records.site}) — morning brief\n"
            f"now {records.now:%Y-%m-%d %H:%M} · dataset revision {records.revision} · "
            f"{len(findings)} findings")
    parts = [head, "=" * 72]
    if findings:
        parts.append(finding_block(findings[0]))
        parts.append("")
        parts.append(f"Other findings ({len(findings) - 1}):")
        parts.append(findings_index(findings, skip=findings[0].id))
    else:
        parts.append("No findings.")
    parts.append("")
    parts.append("Stock runway (daily-service ingredients and others):")
    parts.append(runway_table(records, burn, window))
    parts.append("")
    parts.append(retained_block or f"Rechecked from the API this run: every resource at dataset revision "
                                   f"{records.revision}. Retained beliefs: ledger not wired yet.")
    return "\n".join(parts)
