"""Order sizing: from counts, theoretical use, the delivery calendar and the manager's preferences
to integer cases of catalog SKUs, with a rationale the manager can check line by line.

Pure: no I/O, no model. The numbers here are the only numbers an order can contain.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from . import analysis as an
from . import policies
from .detectors import Params, on_hand_exclusions
from .ledger import Ledger
from .records import Cite, Records


@dataclass
class LineProposal:
    ingredient: str
    ingredient_id: str
    sku: str
    description: str
    pack_quantity: float
    pack_unit: str
    order_unit: str
    on_hand: float
    inbound_counted: float
    burn_per_day: float
    days: int
    base_need: float
    uplift_add: float
    need: float
    short: float
    cases: int
    est_case_usd: Decimal | None
    est_line_usd: Decimal | None
    cites: list[Cite]
    notes: list[str] = field(default_factory=list)

    @property
    def api_line(self) -> dict:
        return {"sku": self.sku, "quantity": int(self.cases), "unit": self.order_unit}


@dataclass
class Proposal:
    vendor_id: str
    vendor_name: str
    today: date
    delivery_date: date | None
    following_delivery: date | None
    order_by: datetime | None
    window_end: date
    window_days: int
    buffer_days: int
    planning_days: list[str]
    lines: list[LineProposal]
    skipped: list[tuple[str, str]]  # (ingredient, reason)
    not_counted: list[str]
    warnings: list[str]
    applied: list[str]
    est_total_usd: Decimal | None
    max_unflagged_usd: float
    revision: int

    @property
    def api_lines(self) -> list[dict]:
        return [l.api_line for l in self.lines]

    @property
    def order(self) -> dict:
        return {"site_id": "SITE-01", "vendor_id": self.vendor_id, "lines": self.api_lines}

    @property
    def over_max(self) -> bool:
        return self.est_total_usd is not None and float(self.est_total_usd) > self.max_unflagged_usd


# ----- calendar -----------------------------------------------------------------------

def next_weekday_on_or_after(d: date, weekday_name: str) -> date:
    target = an.WEEKDAYS.index(weekday_name)
    return d + timedelta(days=(target - d.weekday()) % 7)


def delivery_schedule(now: datetime, planning_days: list[str], cutoff_rule: str | None) -> tuple[date | None, date | None, datetime | None]:
    """(first feasible delivery, the one after it, the order-by moment for the first).

    The only cutoff rule the records state is "noon the day before" (note N09); anything else, or none,
    is treated the same way and said so in the rationale.
    """
    if not planning_days:
        return None, None, None
    today = now.date()
    candidates = sorted(next_weekday_on_or_after(today + timedelta(days=i), d)
                        for d in planning_days for i in (0, 7))
    feasible = []
    for d in sorted(set(candidates)):
        order_by = datetime.combine(d - timedelta(days=1), time(12, 0), tzinfo=now.tzinfo)
        if now < order_by:
            feasible.append((d, order_by))
    if not feasible:
        return None, None, None
    first, order_by = feasible[0]
    following = next((d for d, _ in feasible[1:] if d > first), None)
    return first, following, order_by


# ----- sizing -------------------------------------------------------------------------

def choose_sku(records: Records, vendor_id: str, ingredient_id: str, prefs: policies.OrderPrefs) -> tuple[str | None, str | None]:
    """(sku, reason). Preference first, then the SKU most recently bought from this vendor, then the only one."""
    options = [m.sku for m in records.mappings if m.vendor_id == vendor_id and m.ingredient_id == ingredient_id]
    options = [s for s in options if records.catalog_item(vendor_id, s)]
    if not options:
        return None, None
    pref = prefs.sku_overrides.get(ingredient_id)
    if pref and pref.get("vendor_id") == vendor_id and pref.get("sku") in options:
        return pref["sku"], f"preference {prefs.applied.get(f'sku:{ingredient_id}', 'order.sku')}"
    headers = {h.invoice_id: h for h in records.invoice_headers}
    bought = sorted(((headers[l.invoice_id].invoice_date, l.sku) for l in records.invoice_lines
                     if l.sku in options and l.invoice_id in headers), reverse=True)
    if bought:
        return bought[0][1], f"most recently purchased ({bought[0][0]})"
    return options[0], "only catalog SKU"


def plan_order(records: Records, ledger: Ledger, vendor_id: str = "V1") -> Proposal:
    prefs = policies.order_prefs(ledger)
    params = policies.detector_params(ledger)
    vendor = next((v for v in records.vendors if v.vendor_id == vendor_id), None)
    if vendor is None:
        raise ValueError(f"unknown vendor {vendor_id}; vendors: {[v.vendor_id for v in records.vendors]}")
    now, today = records.now, records.now.date()
    planning = list(params.delivery_days.get(vendor_id, vendor.delivery_days))
    days_belief = ledger.one("assertion", f"vendor.{vendor_id}.delivery_days")
    cutoff_rule = (days_belief.value or {}).get("cutoff") if days_belief else None
    first, following, order_by = delivery_schedule(now, planning, cutoff_rule)

    applied = list(prefs.applied.values())
    for k in ("on_hand_rule", f"delivery_days:{vendor_id}"):
        if k in params.applied:
            applied.append(params.applied[k])
    warnings: list[str] = []

    # Coverage window: today through the day before the delivery after next, at least through cover_through.
    if following:
        end = following - timedelta(days=1)
    elif first:
        end = first + timedelta(days=6)
    else:
        end = today + timedelta(days=6)
        warnings.append(f"No feasible {vendor.vendor_name} delivery day is known; sized for 7 days from today.")
    if first and prefs.cover_through in an.WEEKDAYS:
        end = max(end, next_weekday_on_or_after(first, prefs.cover_through))
    window_days = (end - today).days + 1
    days = window_days + prefs.buffer_days
    weekend_days_in_window = sum(1 for i in range(window_days) if (today + timedelta(days=i)).weekday() >= 5)

    burn, window = an.daily_burn(records, 7)
    daily_items = an.daily_service_ingredients(records)
    counts = an.opening_and_closing(records)
    excluded = set(on_hand_exclusions(records, params))
    inbound_counted: dict[str, float] = {}
    not_counted: list[str] = []
    for h in records.invoice_headers:
        if h.vendor_account != vendor.account_number or h.delivery_date < today and h.status == "received":
            continue
        for l in records.lines_for(h.invoice_id):
            ing = records.ingredient_for_sku(l.sku)
            if not ing:
                continue
            if h.invoice_id in excluded:
                why = params.applied.get(f"received:{h.invoice_id}") or params.applied.get("on_hand_rule", "on-hand rule")
                not_counted.append(f"{h.invoice_id} ({l.stock_quantity:g} {l.pack_unit} {ing}, status {h.status}, "
                                   f"delivery {h.delivery_date}) is not counted as supply: {why}.")
            else:
                inbound_counted[ing] = inbound_counted.get(ing, 0.0) + l.stock_quantity

    # Uplift applies to weekend-service items only (the brunch preference), on the weekend days in the window.
    weekend_add: dict[str, float] = {}
    if prefs.uplift and weekend_days_in_window:
        for m in records.menu:
            if m.service != "weekends":
                continue
            f = an.weekend_forecast(records, m.pos_item_id, 0.0)
            if not f.prior_days:
                continue
            for ing, need in f.needs.items():
                per_weekend_day = need / len(f.days)
                weekend_add[ing] = weekend_add.get(ing, 0.0) + prefs.uplift * per_weekend_day * weekend_days_in_window

    lines: list[LineProposal] = []
    skipped: list[tuple[str, str]] = []
    supplied = {m.ingredient_id: m.ingredient_name for m in records.mappings if m.vendor_id == vendor_id}
    for ing_id, ing in sorted(supplied.items(), key=lambda kv: kv[1]):
        if ing not in counts:
            skipped.append((ing, "no inventory count"))
            continue
        close = counts[ing][1]
        b = burn.get(ing, 0.0)
        base = b * days
        add = weekend_add.get(ing, 0.0)
        need = base + add
        supply = close.quantity + inbound_counted.get(ing, 0.0)
        short = need - supply
        if short <= 0:
            skipped.append((ing, f"{supply:g} {close.unit} on hand covers {need:.1f} needed through {end}"))
            continue
        sku, why = choose_sku(records, vendor_id, ing_id, prefs)
        if sku is None:
            skipped.append((ing, "no catalog SKU for this vendor"))
            continue
        cat = records.catalog_item(vendor_id, sku)
        cases = max(1, min(100, math.ceil(short / cat.pack_quantity)))
        pts = [p for p in an.price_history(records, ing) if p.sku == sku]
        est_case = (pts[-1].unit_cost * Decimal(str(cat.pack_quantity))).quantize(Decimal("0.01")) if pts else None
        notes = [f"SKU {sku}: {why}"]
        if ing in inbound_counted:
            notes.append(f"includes {inbound_counted[ing]:g} {close.unit} inbound counted as supply")
        if add:
            notes.append(f"uplift {prefs.uplift:.0%} on {weekend_days_in_window} weekend day(s): +{add:.1f} {close.unit}")
        if cases * cat.pack_quantity > short * 1.5 and cases > 1:
            notes.append("rounded up a full case beyond the shortfall")
        cites = [close.cite.with_fact(f"{close.quantity:g} {close.unit} on hand"), cat.cite.with_fact(f"{cat.pack_quantity:g} {cat.pack_unit}/{cat.order_unit}")]
        cites += [pts[-1].cite] if pts else []
        lines.append(LineProposal(ing, ing_id, sku, cat.description, cat.pack_quantity, cat.pack_unit, cat.order_unit,
                                  close.quantity, inbound_counted.get(ing, 0.0), b, days, base, add, need, short, cases,
                                  est_case, (est_case * cases) if est_case is not None else None, cites, notes))
        runway = an.runway_days(supply, b)
        if ing in daily_items and first and runway is not None and runway < (first - today).days + 0.5:
            warnings.append(f"{ing}: {supply:g} {close.unit} covers about {runway:.1f} days but the first feasible "
                            f"{vendor.vendor_name} delivery is {an.weekday_name(first)} {first}, "
                            f"{(first - today).days} day(s) away. Ask about an earlier drop or a pickup.")
    if planning and not first:
        warnings.append(f"{vendor.vendor_name} lists {', '.join(planning)} but no delivery is reachable before "
                        f"its cutoff; check the route.")
    est_total = sum((l.est_line_usd for l in lines if l.est_line_usd is not None), Decimal("0")) if lines else None
    if lines and any(l.est_line_usd is None for l in lines):
        est_total = None
    return Proposal(vendor_id, vendor.vendor_name, today, first, following, order_by, end, window_days,
                    prefs.buffer_days, planning, lines, skipped, not_counted, warnings, applied, est_total,
                    prefs.max_unflagged_usd, records.revision)


# ----- rendering ----------------------------------------------------------------------

def rationale(p: Proposal) -> str:
    out = [f"Order proposal — {p.vendor_name} ({p.vendor_id}) — computed at dataset revision {p.revision}"]
    if p.delivery_date:
        out.append(f"Delivery: {an.weekday_name(p.delivery_date)} {p.delivery_date}; order by "
                   f"{p.order_by:%A %H:%M} ({p.planning_days and '/'.join(p.planning_days)} route; cutoff noon the day before). "
                   f"Covers today through {an.weekday_name(p.window_end)} {p.window_end} = {p.window_days} days, "
                   f"plus {p.buffer_days} buffer day(s).")
    else:
        out.append(f"No feasible delivery day known; covers {p.window_days} days plus {p.buffer_days} buffer.")
    head = f"{'ingredient':32s} {'on hand':>12s} {'use/day':>8s} {'days':>5s} {'uplift':>7s} {'need':>7s} {'short':>7s}  {'SKU':10s} {'pack':>9s} {'cases':>5s} {'est $':>8s}"
    out += ["", head, "-" * len(head)]
    for l in p.lines:
        out.append(f"{l.ingredient:32s} {l.on_hand:>8g} {l.pack_unit:3s} {l.burn_per_day:8.2f} {l.days:5d} {l.uplift_add:7.1f} "
                   f"{l.need:7.1f} {l.short:7.1f}  {l.sku:10s} {l.pack_quantity:>5g} {l.pack_unit:3s} {l.cases:5d} "
                   f"{(('%.2f' % l.est_line_usd) if l.est_line_usd is not None else 'n/a'):>8s}")
    for l in p.lines:
        for n in l.notes:
            out.append(f"  {l.ingredient}: {n}")
    if p.skipped:
        out.append("")
        out += [f"Not ordered: {ing} — {why}" for ing, why in p.skipped]
    if p.not_counted:
        out.append("")
        out += [f"Not counted: {n}" for n in p.not_counted]
    out.append("")
    if p.est_total_usd is not None:
        flag = (f" This exceeds your ${p.max_unflagged_usd:,.0f} unflagged limit." if p.over_max
                else f" Under your ${p.max_unflagged_usd:,.0f} unflagged limit.")
        out.append(f"Estimated total ${p.est_total_usd:,.2f} at the last invoiced unit cost per SKU (an estimate; "
                   f"orders do not carry prices).{flag}")
    for w in p.warnings:
        out.append(f"Warning: {w}")
    out.append("Applied: " + (", ".join(p.applied) or "detector defaults, nothing from the ledger"))
    out.append("")
    out.append("Exact order the draft will contain:")
    out.append(json.dumps(p.order, indent=1))
    return "\n".join(out)
