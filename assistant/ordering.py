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


def plan_order(records: Records, ledger: Ledger, vendor_id: str = "V1",
               prefs: policies.OrderPrefs | None = None, params: Params | None = None) -> Proposal:
    """``prefs`` and ``params`` override what the ledger says, for what-if views; nothing is written."""
    prefs = prefs if prefs is not None else policies.order_prefs(ledger)
    params = params if params is not None else policies.detector_params(ledger)
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
                if today <= h.delivery_date <= end:
                    warnings.append(f"{h.invoice_id} is {h.status} to land {an.weekday_name(h.delivery_date)} "
                                    f"{h.delivery_date}, inside this window, but is not counted. If it does arrive you "
                                    f"will carry about {l.stock_quantity:g} {l.pack_unit} of {ing} beyond this order; "
                                    f"if that is likely, say so in `ask` and the proposal shrinks.")
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


# ----- manager edits and the planner ------------------------------------------------------------

def validate_lines(records: Records, vendor_id: str, lines: list[dict]) -> list[dict]:
    """Lines a person (or the model on their behalf) edited: catalog SKUs of this vendor, integer 1..100,
    the vendor's order unit, no duplicates. Returns normalised lines or raises ValueError with the reason."""
    out: list[dict] = []
    seen: set[str] = set()
    for l in lines or []:
        sku = str(l.get("sku", "")).strip()
        cat = records.catalog_item(vendor_id, sku)
        if cat is None:
            raise ValueError(f"{sku!r} is not a catalog SKU for {vendor_id}")
        if sku in seen:
            raise ValueError(f"{sku} appears twice")
        seen.add(sku)
        try:
            qty = int(l.get("quantity"))
        except (TypeError, ValueError):
            raise ValueError(f"{sku}: quantity must be a whole number")
        if not 1 <= qty <= 100:
            raise ValueError(f"{sku}: quantity {qty} is outside 1..100 (drop the line to order none)")
        out.append({"sku": sku, "quantity": qty, "unit": cat.order_unit, "description": cat.description,
                    "pack": f"{cat.pack_quantity:g} {cat.pack_unit}", "ingredient": records.ingredient_for_sku(sku)})
    if not out:
        raise ValueError("an order needs at least one line")
    if len(out) > 20:
        raise ValueError("at most 20 lines")
    return out


def lines_diff(before: list[dict], after: list[dict]) -> list[str]:
    b = {l["sku"]: l["quantity"] for l in before}
    a = {l["sku"]: l["quantity"] for l in after}
    out = []
    for sku in sorted(set(b) | set(a)):
        if sku in b and sku not in a:
            out.append(f"removed {sku} (was {b[sku]})")
        elif sku in a and sku not in b:
            out.append(f"added {sku} x{a[sku]}")
        elif b[sku] != a[sku]:
            out.append(f"{sku}: {b[sku]} -> {a[sku]}")
    return out or ["no change"]


def accepted_cover(drafts: list, vendor_id: str) -> dict[str, int]:
    """Cases per SKU that accepted orders for this vendor already cover."""
    covered: dict[str, int] = {}
    for d in drafts:
        if d.vendor_id == vendor_id and d.status == "accepted":
            for l in d.order.get("lines", []):
                covered[l["sku"]] = covered.get(l["sku"], 0) + int(l.get("quantity", 0))
    return covered


def net_lines(p: Proposal, covered: dict[str, int]) -> list[dict]:
    """The proposal's lines with what is already ordered taken out; lines fully covered are dropped."""
    out = []
    for l in p.lines:
        left = l.cases - covered.get(l.sku, 0)
        if left > 0:
            out.append(l.api_line | {"quantity": left, "ingredient": l.ingredient, "cases": left, "proposed": l.cases,
                                     "already_ordered": covered.get(l.sku, 0), "on_hand": l.on_hand, "count_unit": l.pack_unit,
                                     "days_left": round(l.on_hand / l.burn_per_day, 1) if l.burn_per_day > 0 else None})
    return out


def _q(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".")


def orders_block(records: Records, ledger: Ledger) -> dict:
    """The brief's "Orders to cover": every item still to order, grouped by vendor with the next feasible delivery, the
    cutoff and the suggested cases, from the planner (net of accepted orders). One draft is suggested per vendor,
    because an order goes to one vendor: needs across two vendors take two orders, or something runs out."""
    plan = planner(records, ledger)
    vendors = []
    for v in plan:
        if not v["lines"] or v["urgency"] in ("ordered", "nothing needed"):
            continue
        runs_out = {r["ingredient"] for r in v["runs_out_before_delivery"]}
        vendors.append({"vendor_id": v["vendor_id"], "vendor_name": v["vendor_name"], "short": v["vendor_name"].split(",")[0].split()[0],
                        "urgency": v["urgency"], "delivery_date": str(v["delivery_date"]) if v["delivery_date"] else None,
                        "order_by": v["order_by"].isoformat() if v["order_by"] else None, "hours_to_cutoff": v["hours_to_cutoff"],
                        "est_total_usd": str(v["est_total_usd"]) if v["est_total_usd"] is not None else None, "over_max": v["over_max"],
                        "lines": [l | {"runs_out": l["ingredient"] in runs_out} for l in v["lines"]],
                        "open_draft": v["open_draft"], "warnings": v["warnings"]})
    covered = [{"vendor_name": v["vendor_name"], "accepted": v["accepted"]} for v in plan if v["accepted"]]
    n = len(vendors)
    if n == 0:
        note = "every need is covered by an accepted order." if covered else "stock covers the window; nothing to order."
    elif n == 1:
        note = f"1 draft suggested ({vendors[0]['short']})."
    else:
        note = (f"{n} drafts suggested, one per vendor ({', '.join(v['short'] for v in vendors)}): an order goes to one vendor, "
                f"so needs across {n} vendors take {n} orders or something runs out.")
    return {"vendors": vendors, "covered": covered, "note": note,
            "suggested": [{"vendor_id": v["vendor_id"], "vendor_name": v["vendor_name"], "lines": len(v["lines"]),
                           "est_total_usd": v["est_total_usd"], "order_by": v["order_by"], "delivery_date": v["delivery_date"]} for v in vendors]}


def orders_text(o: dict) -> str:
    """The same block as plain text for the CLI brief."""
    out = [f"Orders to cover: {o['note']}"]
    for v in o["vendors"]:
        when = (f"deliver {v['delivery_date']}, order by {v['order_by'][:16].replace('T', ' ')}" if v["delivery_date"] else "no delivery day known")
        h = v["hours_to_cutoff"]
        hrs = "" if h is None else (f" ({h:.0f} h left)" if h >= 0 else " (cutoff passed)")
        est = f", est. ${float(v['est_total_usd']):,.2f}" if v["est_total_usd"] else ""
        out.append(f"  {v['vendor_name']} — {when}{hrs}{est}")
        for l in v["lines"]:
            days = f"{l['days_left']:.1f} days" if l["days_left"] is not None else "no use rate"
            out.append(f"    {l['ingredient']:32s} {_q(l['on_hand']):>6s} {l['count_unit']:4s} {days:>10s}   {l['quantity']} × {l['sku']}"
                       + ("   runs out before delivery" if l["runs_out"] else ""))
        if v["open_draft"]:
            out.append(f"    open draft {v['open_draft']['draft_id']} is {v['open_draft']['status'].replace('_', ' ')}")
    for c in o["covered"]:
        for a in c["accepted"]:
            out.append(f"  already ordered from {c['vendor_name']}: {a['draft_id']} → {a['order_id']} "
                       f"({', '.join(str(l['quantity']) + ' × ' + l['sku'] for l in a['lines'])})")
    return "\n".join(out)


def orders_summary(o: dict) -> str:
    """One line per vendor for the model: what the table below its text will say, as data."""
    lines = [o["note"]]
    for v in o["vendors"]:
        lines.append(f"{v['vendor_name']}: {len(v['lines'])} line(s), order by {v['order_by'][:16].replace('T', ' ') if v['order_by'] else 'n/a'} "
                     f"for delivery {v['delivery_date'] or 'n/a'}" + (f", est. ${float(v['est_total_usd']):,.2f}" if v["est_total_usd"] else "")
                     + ("; runs out before delivery: " + ", ".join(l["ingredient"] for l in v["lines"] if l["runs_out"]) if any(l["runs_out"] for l in v["lines"]) else ""))
    return "\n".join(lines)


def planner(records: Records, ledger: Ledger) -> list[dict]:
    """What to order next and when, per vendor: the next feasible delivery and its order-by moment, the
    ingredients that run out before it, the proposed lines, the estimate, and any open or accepted draft."""
    now, today = records.now, records.now.date()
    burn, _ = an.daily_burn(records, 7)
    counts = an.opening_and_closing(records)
    daily = an.daily_service_ingredients(records)
    drafts = ledger.drafts()
    out = []
    for v in records.vendors:
        p = plan_order(records, ledger, v.vendor_id)
        supplied = {m.ingredient_name for m in records.mappings if m.vendor_id == v.vendor_id}
        runs_out = []
        for ing in sorted(supplied):
            if ing not in counts or ing not in daily:
                continue
            days = an.runway_days(counts[ing][1].quantity, burn.get(ing, 0.0))
            if days is not None and p.delivery_date and days < (p.delivery_date - today).days + 0.5:
                runs_out.append({"ingredient": ing, "days": round(days, 1)})
        hours_to_cutoff = (p.order_by - now).total_seconds() / 3600 if p.order_by else None
        vd = [d for d in drafts if d.vendor_id == v.vendor_id]
        open_draft = next((d for d in reversed(vd) if d.status in ("drafted", "awaiting_approval", "approved", "submitting", "unknown")), None)
        accepted = [d for d in vd if d.status == "accepted"]
        remaining = net_lines(p, accepted_cover(vd, v.vendor_id))  # what an accepted order already covers comes off
        urgency = ("nothing needed" if not p.lines else "ordered" if accepted and not remaining else
                   "overdue" if hours_to_cutoff is not None and hours_to_cutoff < 0 else
                   "today" if hours_to_cutoff is not None and hours_to_cutoff <= 24 else "this week")
        out.append({"vendor_id": v.vendor_id, "vendor_name": v.vendor_name, "planning_days": p.planning_days,
                    "delivery_date": p.delivery_date, "order_by": p.order_by, "hours_to_cutoff": round(hours_to_cutoff, 1) if hours_to_cutoff is not None else None,
                    "window_end": p.window_end, "lines": remaining, "proposed_lines": [l.api_line | {"ingredient": l.ingredient, "cases": l.cases} for l in p.lines],
                    "est_total_usd": p.est_total_usd, "over_max": p.over_max, "runs_out_before_delivery": runs_out,
                    "warnings": p.warnings, "skipped": p.skipped, "open_draft": open_draft.__dict__ if open_draft else None,
                    "accepted": [{"draft_id": d.draft_id, "order_id": d.order_id, "updated_at": d.updated_at, "lines": d.order.get("lines", [])} for d in accepted],
                    "urgency": urgency})
    order = {"overdue": 0, "today": 1, "this week": 2, "ordered": 3, "nothing needed": 4}
    out.sort(key=lambda x: (order.get(x["urgency"], 9), x["hours_to_cutoff"] if x["hours_to_cutoff"] is not None else 1e9))
    return out
