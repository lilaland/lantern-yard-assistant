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


# ----- ledger views ------------------------------------------------------------------

def _belief_line(b, width: int = 36) -> str:
    val = ", ".join(f"{k}={fmt(v)}" for k, v in b.value.items()) if isinstance(b.value, dict) else fmt(b.value)
    return f"  {b.kind:11s} {b.subject:{width}s} {val:34s} {b.source}  recorded {b.recorded_at[:16]}"


def numbers_diff(old: dict, new: dict) -> str:
    """Which keys of two observation values differ, as 'k: a -> b'."""
    o, n = old.get("numbers", {}), new.get("numbers", {})
    parts = [f"{k}: {fmt(o.get(k, '-'))} -> {fmt(n.get(k, '-'))}" for k in sorted(set(o) | set(n)) if o.get(k) != n.get(k)]
    if old.get("severity") != new.get("severity"):
        parts.insert(0, f"severity {old.get('severity')} -> {new.get('severity')}")
    if old.get("title") != new.get("title"):
        parts.insert(0, f"{old.get('title')} -> {new.get('title')}")
    return "; ".join(parts) or "no visible change"


def retained_block(report, ledger, onboarded_at: str | None) -> str:
    """The brief's closing block: what was retained unchanged, what was rechecked, what needs the manager."""
    lines = []
    if onboarded_at:
        lines.append(f"Retained (human choices, never rechecked against data): {report.retained.get('policy', 0)} policies, "
                     f"{report.retained.get('preference', 0)} preferences; onboarded {onboarded_at[:16]}. "
                     f"`memory` lists them, `why <subject>` explains one.")
    else:
        lines.append("Not onboarded: detector defaults in force. Run `python -m assistant onboard --defaults` "
                     "(or `onboard` to answer the questions) so your choices are retained.")
    rev = f"revision {report.revision}"
    if report.revision_changed:
        rev = f"revision {report.previous_revision} -> {report.revision} (the data changed since the last run)"
    obs = (f"{len(report.observations_new)} new, {report.observations_unchanged} unchanged, "
           f"{len(report.observations_changed)} changed, {len(report.observations_cleared)} cleared")
    if report.first_run:
        obs = f"{len(report.observations_new)} recorded (first run)"
    lines.append(f"Rechecked from the API this run at {rev}: every resource; observations {obs}.")
    for old, reason in report.observations_cleared:
        lines.append(f"  - cleared: {old.subject} (seen at revision {old.revision}); {reason}")
    for old, new in report.observations_changed:
        lines.append(f"  - changed: {new.subject}: {numbers_diff(old.value, new.value)}")
    for old, new in report.assertions_superseded:
        lines.append(f"  - superseded: {old.subject} {fmt(old.value)} ({old.source}) -> {fmt(new.value)} ({new.source})")
    conflicts = ledger.conflicts()
    if conflicts:
        lines.append("Needs you (a newer record disagrees with something a person stated; nothing was overwritten):")
        for old, new in conflicts:
            lines.append(f"  - {old.subject}: {old.source} said {fmt(old.value)} on {old.recorded_at[:10]}; "
                         f"{new.source if new else 'newer evidence'} says {fmt(new.value) if new else '?'}. "
                         f"Planning uses the newer record until you confirm with `ask`.")
    return "\n".join(lines)


def memory_text(ledger, onboarded_at: str | None, cost_usd: float | None, include_closed: bool = False) -> str:
    counts = ledger.counts()
    out = ["Ledger: " + (f"onboarded {onboarded_at[:16]}" if onboarded_at else "not onboarded"),
           "  kind         active  superseded  conflicted"]
    for k, c in counts.items():
        out.append(f"  {k:11s} {c['active']:6d}  {c['superseded']:10d}  {c['conflicted']:10d}")
    out.append("")
    out.append("Retained (human; a person changes these, data never does):")
    human = [b for b in ledger.all(include_closed) if b.kind in ("policy", "preference")]
    out += [_belief_line(b) for b in human] or ["  (none)"]
    out.append("")
    out.append("Assertions (facts; rechecked when the dataset revision changes):")
    facts = [b for b in ledger.all(include_closed) if b.kind == "assertion"]
    out += [_belief_line(b) + ("" if b.status == "active" else f"  [{b.status}]") for b in facts] or ["  (none)"]
    conflicts = ledger.conflicts()
    if conflicts:
        out.append("")
        out.append("Conflicted (needs a person):")
        for old, new in conflicts:
            out.append(f"  {old.subject}: {old.source} said {fmt(old.value)}; "
                       f"{new.source if new else '?'} says {fmt(new.value) if new else '?'}")
    obs = ledger.active("observation")
    live = [b for b in obs if not b.value.get("cleared")]
    cleared = [b for b in obs if b.value.get("cleared")]
    out.append("")
    out.append(f"Observations (detector output; rechecked every run): {len(live)} current, {len(cleared)} cleared, "
               f"last revision {ledger.meta('last_revision') or 'n/a'}, last run {ledger.meta('last_run_at') or 'never'}")
    drafts = ledger.drafts()
    if drafts:
        out.append("")
        out.append("Drafts:")
        out += [f"  {d.draft_id}  {d.status:18s} {d.vendor_id}  order_id={d.order_id or '-'}  {d.updated_at[:16]}"
                for d in drafts]
    out.append("")
    out.append(f"Model cost so far: {('$%.4f' % cost_usd) if cost_usd is not None else 'no usage log yet'}")
    return "\n".join(out)


def why_text(subject: str, chain: list) -> str:
    if not chain:
        return f"Nothing recorded about {subject!r}."
    out = [f"{subject}: {len(chain)} record(s), oldest first"]
    for b in chain:
        link = f" supersedes {b.supersedes}" if b.supersedes else ""
        link += f" conflicts with {b.conflicts_with}" if b.conflicts_with else ""
        out.append(f"  [{b.status:10s}] {b.recorded_at[:16]}  {b.kind} from {b.source}{link}")
        out.append(f"               {b.statement}")
        out.append(f"               value {fmt(b.value)}; recheck {b.recheck}"
                   + (f"; revision {b.revision}" if b.revision is not None else ""))
    return "\n".join(out)
