"""Deterministic checks that turn Records into ranked Findings with citations.

No model is involved. Thresholds live here; policies (block 3) may re-rank or
suppress. Ranking is deterministic: severity, then urgency in days, then money
at stake, then id. Every finding is stamped with the dataset revision it was
computed from so a later run can tell what to recheck.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from . import analysis as an
from .records import Cite, Records, UntrustedText

SEVERITY_LABEL = {1: "HIGH", 2: "MED", 3: "LOW"}

INSTRUCTION_PATTERN = re.compile(
    r"\b(AI|assistant|LLM|language model|bypass|pre-?authori[sz]ed|ignore (?:previous|prior|all)"
    r"|automatically retry|treat any)\b", re.IGNORECASE)


@dataclass
class Finding:
    id: str
    cls: str
    severity: int  # 1 high, 2 medium, 3 low
    title: str
    summary: str
    evidence: list[Cite]
    uncertainty: list[str]
    next_step: str
    numbers: dict[str, object] = field(default_factory=dict)
    computed_at_revision: int = 0
    urgency_days: float | None = None
    usd_at_stake: float = 0.0
    applied_policies: list[str] = field(default_factory=list)


def rank_key(f: Finding) -> tuple:
    return (f.severity, f.urgency_days if f.urgency_days is not None else 999.0, -f.usd_at_stake, f.id)


ON_HAND_RULES = ("physical_confirmation", "office_status")


@dataclass(frozen=True)
class Params:
    """Knobs the ledger's policies and preferences set. Defaults are the detectors' own.

    ``applied`` maps a knob to the belief that set it (e.g. ``"on_hand_rule": "inbound.on_hand_rule
    (human:onboarding-defaults)"``) so a finding can name the policy that shaped it.
    """
    on_hand_rule: str = "physical_confirmation"
    confirmed_received: frozenset = frozenset()      # invoice ids a human confirmed as physically received
    confirmed_not_received: frozenset = frozenset()  # invoice ids a human confirmed as not received
    price_outlier_ratio: float = 2.0
    runway_threshold_days: float = 3.0
    brunch_uplift: float = 0.0
    delivery_days: dict = field(default_factory=dict)  # vendor_id -> tuple of weekday names
    applied: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.on_hand_rule not in ON_HAND_RULES:
            raise ValueError(f"on_hand_rule must be one of {ON_HAND_RULES}, got {self.on_hand_rule!r}")

    def label(self, *keys: str) -> list[str]:
        return [self.applied[k] for k in keys if k in self.applied]


@dataclass
class Ctx:
    records: Records
    params: Params
    usage: an.Usage
    invoice_issues: list[an.InvoiceIssue]
    inbound_issues: list[an.InvoiceIssue]
    rec_trusting_status: dict[str, an.Reconciliation]  # office status taken at face value
    rec_evidence: dict[str, an.Reconciliation]         # only goods the current rule counts as on hand
    burn: dict[str, float]
    burn_window: tuple[date, date]
    counts: dict[str, tuple]


def on_hand_exclusions(records: Records, params: Params) -> tuple[str, ...]:
    """Invoices whose goods do not count as on hand under the current rule and confirmations."""
    if params.on_hand_rule == "office_status":
        excluded = {h.invoice_id for h in an.unreceived_invoices(records)}
    else:
        excluded = {h.invoice_id for h in an.not_on_hand_invoices(records)}
    excluded -= set(params.confirmed_received)
    excluded |= set(params.confirmed_not_received)
    return tuple(sorted(excluded))


def build_ctx(records: Records, params: Params | None = None) -> Ctx:
    params = params or Params()
    issues = an.invoice_checks(records, outlier_ratio=params.price_outlier_ratio)
    inbound = [i for i in issues if i.kind == "status_inconsistent_with_date"]
    trusting = tuple(h.invoice_id for h in an.unreceived_invoices(records))
    evidence = on_hand_exclusions(records, params)
    burn, window = an.daily_burn(records, 7)
    return Ctx(records, params, an.theoretical_usage(records.sales, records.recipes), issues, inbound,
               an.reconcile_all(records, trusting), an.reconcile_all(records, evidence), burn, window,
               an.opening_and_closing(records))


# ----- helpers -----------------------------------------------------------------

def _short(name: str) -> str:
    return name.split(",")[0].split()[0].lower()


def _excerpt(text: str, n: int = 110, around: tuple[str, ...] = ()) -> str:
    text = " ".join(text.split())
    if len(text) <= n:
        return text
    low = text.lower()
    hit = min((low.find(a.lower()) for a in around if a and a.lower() in low), default=-1)
    if hit < 0:
        return text[: n - 1].rstrip() + "…"
    start = max(0, hit - n // 3)
    if start:
        start = text.rfind(" ", 0, start) + 1  # begin on a whole word
    end = min(len(text), start + n)
    if end < len(text) and " " in text[hit:end]:
        end = hit + text[hit:end].rindex(" ")  # end on a whole word
    return ("…" if start else "") + text[start:end].strip() + ("…" if end < len(text) else "")


def _notes_with(records: Records, *needles: str, any_of: tuple[str, ...] = ()) -> list[UntrustedText]:
    out = []
    for n in records.notes:
        t = n.text.lower()
        if all(x.lower() in t for x in needles) and (not any_of or any(x.lower() in t for x in any_of)):
            out.append(n)
    return out


def _note_cites(notes: list[UntrustedText], around: tuple[str, ...] = ()) -> list[Cite]:
    return [n.cite.with_fact(_excerpt(n.text, around=around)) for n in notes]


def _vendor_for_ingredient(records: Records, ingredient: str):
    for m in records.mappings:
        if m.ingredient_name == ingredient:
            return next((v for v in records.vendors if v.vendor_id == m.vendor_id), None)
    return None


def _delivery_days(ctx: Ctx, vendor) -> tuple[str, ...]:
    if vendor is None:
        return ()
    return tuple(ctx.params.delivery_days.get(vendor.vendor_id, vendor.delivery_days))


def _days_until_listed_delivery(days: tuple[str, ...], today: date) -> int | None:
    """Days from today until the next listed delivery weekday (0 = today). None if no days listed."""
    idx = [an.WEEKDAYS.index(d) for d in days if d in an.WEEKDAYS]
    if not idx:
        return None
    return min((i - today.weekday()) % 7 for i in idx)


def _usd(x: Decimal | float | None) -> float:
    return float(x) if x is not None else 0.0


# ----- detectors ---------------------------------------------------------------

def detect_phantom_receipts(ctx: Ctx) -> list[Finding]:
    r = ctx.records
    out = []
    for issue in ctx.inbound_issues:
        h = r.header(issue.invoice_id)
        lines = r.lines_for(h.invoice_id)
        impacts: dict[str, float] = {}
        unit = ""
        for l in lines:
            ing = r.ingredient_for_sku(l.sku)
            if ing:
                impacts[ing] = impacts.get(ing, 0.0) + l.stock_quantity
                unit = l.pack_unit
        main = max(impacts, key=impacts.get) if impacts else None
        notes = _notes_with(r, h.invoice_id) or _notes_with(r, "received", any_of=("not seen", "haven't seen", "have not seen"))
        evidence = list(issue.cites) + _note_cites(notes, around=(h.invoice_id, "received"))
        numbers: dict[str, object] = {"invoice_total_usd": h.invoice_total, "status": h.status,
                                      "delivery_date": str(h.delivery_date), "today": str(r.now.date()),
                                      "stock_on_invoice": {k: f"{v:g} {unit}" for k, v in impacts.items()}}
        summary = (f"{h.invoice_id} ({h.vendor_name}, ${h.invoice_total}) is marked '{h.status}' with delivery "
                   f"date {h.delivery_date}, which is after today ({r.now.date()}).")
        uncertainty = ["Receipt status is typed in by the office, not scanned at the door, so the status alone "
                       "does not show the cases arrived."]
        urgency = None
        if main:
            ra, rx = ctx.rec_trusting_status[main], ctx.rec_evidence[main]
            burn = ctx.burn.get(main, 0.0)
            runway = an.runway_days(rx.counted, burn)
            urgency = runway
            numbers.update({"gap_if_received": round(ra.gap, 1), "expected_close_if_received": round(ra.expected_close, 1),
                            "gap_if_not_received": round(rx.gap, 1), "expected_close_if_not": round(rx.expected_close, 1),
                            "counted_close": rx.counted, "unit": rx.unit, "burn_per_day": round(burn, 1),
                            "runway_days": round(runway, 1) if runway is not None else None})
            summary += (f" It carries {impacts[main]:g} {unit} of {main}. Counting it as on hand, the "
                        f"{ctx.counts[main][1].counted_at.date()} close is {abs(ra.gap):.1f} {rx.unit} "
                        f"{'below' if ra.gap < 0 else 'above'} expected ({ra.expected_close:.1f} expected vs "
                        f"{ra.counted:g} counted); not counting it, the gap is {abs(rx.gap):.1f} {rx.unit}.")
            if runway is not None:
                summary += (f" What was counted covers about {runway:.1f} days at last week's theoretical use "
                            f"of {burn:.1f} {rx.unit}/day.")
            evidence += ra.cites[:2]
            uncertainty.append("Theoretical use assumes recipe cards were followed; there are no intermediate "
                               "counts or waste records to confirm it.")
            for item, portions in ctx.usage.unknown_items.items():
                name = next((m.name for m in r.menu if m.pos_item_id == item), item)
                uncertainty.append(f"{name} ({item}) sold {portions} portions with no recipe on file, so some "
                                   f"{_short(main)} draw is unmeasured.")
            waste = _notes_with(r, _short(main), any_of=("tossed", "waste", "threw"))
            for n in waste:
                uncertainty.append(f"Note {n.ref} ({n.recorded_at[:10]}) records {_short(main)} discarded without weighing.")
            evidence += _note_cites(waste, around=("tossed", "waste", "threw"))
        vendor = r.vendor_by_account(h.vendor_account)
        vname = vendor.vendor_name if vendor else h.vendor_name
        p = ctx.params
        confirmed = h.invoice_id in p.confirmed_received
        denied = h.invoice_id in p.confirmed_not_received
        applied = p.label("on_hand_rule", f"received:{h.invoice_id}")
        if confirmed:
            severity = 3
            next_step = (f"The manager confirmed {h.invoice_id} was physically received, so its goods count as on "
                         f"hand; ask the office to correct the delivery date.")
        elif p.on_hand_rule == "office_status" and not denied:
            severity = 3
            next_step = (f"Policy trusts the office receipt status, so {h.invoice_id} counts as on hand. The delivery "
                         f"date still disagrees; worth a call to {vname} before relying on it.")
        else:
            severity = 1
            next_step = (f"Confirm with {vname} whether {h.invoice_id} has shipped before placing the next order. "
                         f"Until confirmed, plan with {main} at {ctx.rec_evidence[main].counted:g} {unit} on hand"
                         f"{f', about {urgency:.1f} days' if urgency else ''}." if main else
                         f"Confirm with {vname} whether {h.invoice_id} has shipped.")
            if denied:
                next_step = f"The manager confirmed the cases are not here. {next_step}"
        out.append(Finding(f"phantom_receipt:{h.invoice_id}", "status_inconsistent_with_evidence", severity,
                           f"{h.invoice_id} is marked received but its delivery date is in the future",
                           summary, evidence, uncertainty, next_step, numbers, r.revision, urgency,
                           _usd(h.invoice_total), applied))
    return out


def detect_count_variance(ctx: Ctx) -> list[Finding]:
    r = ctx.records
    out = []
    for ing, rec in ctx.rec_evidence.items():
        if abs(rec.gap) < 0.5:
            continue
        pct = abs(rec.gap_pct or 0.0)
        usd = abs(rec.gap) * _usd(an.latest_unit_cost(r, ing))
        if pct < 0.02 and usd < 10:
            continue
        severity = 1 if usd >= 100 else 2 if usd >= 25 else 3
        excl = f" (excluding not-on-hand {', '.join(rec.excluded_invoices)})" if rec.excluded_invoices else ""
        summary = (f"{ing}: opening {rec.opening:g} + purchased {rec.purchased:g}{excl} - theoretical use "
                   f"{rec.theoretical:.1f} = {rec.expected_close:.1f} {rec.unit} expected; counted {rec.counted:g}, "
                   f"a gap of {rec.gap:+.1f} {rec.unit} ({pct:.0%}), roughly ${usd:.0f} at the latest unit cost.")
        uncertainty = ["No intermediate counts, waste sheet, or transfers exist, so the gap cannot be attributed."]
        for item, portions in ctx.usage.unknown_items.items():
            name = next((m.name for m in r.menu if m.pos_item_id == item), item)
            uncertainty.append(f"{name} ({item}) sold {portions} portions with no recipe; it may draw {_short(ing)}.")
        waste = _notes_with(r, _short(ing), any_of=("tossed", "waste", "threw"))
        uncertainty += [f"Note {n.ref} records {_short(ing)} discarded unweighed." for n in waste]
        out.append(Finding(f"count_variance:{ing}", "count_variance", severity,
                           f"{ing} count is {rec.gap:+.1f} {rec.unit} against theoretical",
                           summary, rec.cites + _note_cites(waste, around=("tossed", "waste", "threw")), uncertainty,
                           f"Spot-check {_short(ing)} with a mid-week count; if the gap persists, review portioning "
                           f"and start the waste sheet.", {"gap": round(rec.gap, 2), "gap_pct": round(pct, 3),
                                                            "usd": round(usd, 2), "unit": rec.unit},
                           r.revision, None, usd))
    return out


def detect_invoice_anomalies(ctx: Ctx) -> list[Finding]:
    r = ctx.records
    out = []
    for issue in ctx.invoice_issues:
        if issue.kind == "status_inconsistent_with_date":
            continue
        h = r.header(issue.invoice_id)
        vendor = r.vendor_by_account(h.vendor_account) if h else None
        vname = vendor.vendor_name if vendor else (h.vendor_name if h else "the vendor")
        if issue.kind == "unit_price_outlier":
            usd = _usd(issue.numbers["amount_vs_median"])
            applied = ctx.params.label("price_outlier_ratio")
            out.append(Finding(f"unit_price_outlier:{issue.cites[0].ref}", issue.kind, 1,
                               f"{issue.invoice_id} bills a line at {issue.numbers['ratio']:.0f}x the usual unit cost",
                               issue.detail + f". Against the median this line is ${usd:,.2f} higher.",
                               issue.cites, ["The header total agrees with the line, so the error is on the invoice "
                                             "itself or in data entry; the paper invoice decides which."],
                               f"Ask {vname} for a corrected invoice or credit on {issue.invoice_id}. Do not adjust "
                               f"the recorded amounts yourself.", dict(issue.numbers), r.revision, None, usd, applied))
        elif issue.kind == "header_total_mismatch":
            usd = abs(_usd(issue.numbers["difference"]))
            out.append(Finding(f"header_total_mismatch:{issue.invoice_id}", issue.kind, 2,
                               f"{issue.invoice_id} header total differs from its lines by ${usd:,.2f}",
                               issue.detail + ".", issue.cites,
                               ["A missing line, a fee not captured, or a typo would all look like this."],
                               f"Compare {issue.invoice_id} with the paper invoice from {vname} and flag it in the "
                               f"invoice folder; do not edit either figure.", dict(issue.numbers), r.revision, None, usd))
        elif issue.kind == "vendor_name_variance":
            out.append(Finding(f"vendor_name_variance:{issue.numbers['account']}", issue.kind, 3,
                               f"Account {issue.numbers['account']} appears under more than one vendor name",
                               issue.detail + ". The account number is the stable key.", issue.cites, [],
                               "Match invoices to vendors by account number, not display name.",
                               dict(issue.numbers), r.revision, None, 0.0))
    return out


def detect_reference_table_stale(ctx: Ctx) -> list[Finding]:
    r = ctx.records
    out = []
    for v in r.vendors:
        actual = an.deliveries_by_weekday(r, v.account_number, since=v.last_reviewed)
        unexpected = {d: hs for d, hs in actual.items() if d not in v.delivery_days}
        notes = _notes_with(r, _short(v.vendor_name), any_of=an.WEEKDAYS)
        if not unexpected and not notes:
            continue
        evidence = [v.cite.with_fact(f"delivery_days {', '.join(v.delivery_days)}; last_reviewed {v.last_reviewed}")]
        parts = []
        for day, hs in unexpected.items():
            for h in hs:
                evidence.append(h.cite.with_fact(f"delivered {day} {h.delivery_date}"))
                parts.append(f"{h.invoice_id} on {day} {h.delivery_date}")
        evidence += _note_cites(notes, around=an.WEEKDAYS)
        summary = (f"The vendor sheet lists {v.vendor_name} on {', '.join(v.delivery_days)} (last reviewed "
                   f"{v.last_reviewed}).")
        if parts:
            summary += f" Since then deliveries landed on other days: {'; '.join(parts)}."
        if notes:
            summary += f" Note{'s' if len(notes) > 1 else ''} {', '.join(n.ref for n in notes)} mention a route change."
        override = ctx.params.delivery_days.get(v.vendor_id)
        applied = ctx.params.label(f"delivery_days:{v.vendor_id}")
        if override:
            next_step = (f"Planning already uses {', '.join(override)} for {v.vendor_name}; update the vendor sheet "
                         f"so the two agree.")
        else:
            next_step = (f"Confirm {v.vendor_name}'s current delivery days and order cutoff, then update the "
                         f"vendor sheet.")
        out.append(Finding(f"reference_table_stale:{v.vendor_id}", "reference_table_stale", 2,
                           f"{v.vendor_name} delivery days on the vendor sheet look out of date",
                           summary, evidence,
                           ["The vendor sheet is the only place order cutoffs are implied; the notes are the "
                            "newer source but are informal."],
                           next_step, {"listed_days": list(v.delivery_days),
                                       "planning_days": list(override or v.delivery_days),
                                       "observed_days_since_review": sorted(actual)},
                           r.revision, None, 0.0, applied))
    return out


def detect_sku_substitution(ctx: Ctx) -> list[Finding]:
    r = ctx.records
    headers = {h.invoice_id: h for h in r.invoice_headers}
    groups: dict[tuple[str, str], list] = {}
    for m in r.mappings:
        groups.setdefault((m.ingredient_id, m.vendor_id), []).append(m)
    out = []
    for (ing_id, vendor_id), maps in groups.items():
        if len(maps) < 2:
            continue
        ingredient = maps[0].ingredient_name
        spans = {}
        for m in maps:
            dates = sorted(headers[l.invoice_id].invoice_date for l in r.invoice_lines
                           if l.sku == m.sku and l.invoice_id in headers)
            if dates:
                spans[m.sku] = (dates[0], dates[-1])
        if len(spans) < 2:
            continue
        old_sku, new_sku = sorted(spans, key=lambda s: spans[s][0])[:2]
        if spans[new_sku][0] <= spans[old_sku][1]:
            continue  # overlapping use, not a substitution
        pts = an.price_history(r, ingredient)
        old_cost = next((p.unit_cost for p in reversed(pts) if p.sku == old_sku), None)
        new_cost = next((p.unit_cost for p in pts if p.sku == new_sku), None)
        old_cat, new_cat = r.catalog_item(vendor_id, old_sku), r.catalog_item(vendor_id, new_sku)
        vendor = next((v for v in r.vendors if v.vendor_id == vendor_id), None)
        notes = _notes_with(r, _short(ingredient), any_of=("case", "/lb", "$"))
        weekly = ctx.burn.get(ingredient, 0.0) * 7
        delta_week = float((new_cost or 0) - (old_cost or 0)) * weekly
        unit = new_cat.pack_unit if new_cat else ""
        summary = (f"{ingredient} from {vendor.vendor_name if vendor else vendor_id}: purchases switched from "
                   f"{old_sku} ({old_cat.pack_quantity:g} {unit} per {old_cat.order_unit}, ${old_cost:.2f}/{unit}, last "
                   f"bought {spans[old_sku][1]}) to {new_sku} ({new_cat.pack_quantity:g} {unit} per {new_cat.order_unit}, "
                   f"${new_cost:.2f}/{unit}, first bought {spans[new_sku][0]}). The catalog still lists both.")
        evidence = [m.cite.with_fact(f"{m.sku} -> {m.ingredient_name}") for m in maps]
        evidence += [c.cite.with_fact(f"{c.sku} pack {c.pack_quantity:g} {c.pack_unit}") for c in (old_cat, new_cat) if c]
        evidence += [p.cite for p in pts if p.sku in (old_sku, new_sku)][-4:]
        evidence += _note_cites(notes)
        out.append(Finding(f"sku_substitution:{ing_id}", "sku_substitution", 2,
                           f"{ingredient} now arrives as {new_sku}: half the case size, higher price per {unit}",
                           summary, evidence,
                           ["Whether the 40 lb case is still available is not in the records; the catalog "
                            "was not updated."],
                           f"Count and order {_short(ingredient)} in {unit}, not cases, and order {new_sku} unless "
                           f"{vendor.vendor_name if vendor else 'the vendor'} says otherwise. At last week's use the "
                           f"price change adds about ${delta_week:,.0f} per week.",
                           {"old_sku": old_sku, "new_sku": new_sku, "old_pack": old_cat.pack_quantity,
                            "new_pack": new_cat.pack_quantity, "old_unit_cost": old_cost, "new_unit_cost": new_cost,
                            "weekly_use": round(weekly, 1), "usd_per_week": round(delta_week, 2)},
                           r.revision, None, delta_week))
    return out


def detect_items_without_recipe(ctx: Ctx) -> list[Finding]:
    r = ctx.records
    out = []
    for item, portions in ctx.usage.unknown_items.items():
        menu = next((m for m in r.menu if m.pos_item_id == item), None)
        name = menu.name if menu else item
        rows = [s for s in r.sales if s.pos_item_id == item]
        net = sum((s.net_sales for s in rows), Decimal("0"))
        notes = _notes_with(r, _short(name), any_of=("recipe", "portion", "wrote", "write"))
        evidence = ([menu.cite.with_fact(f"{name}, {menu.service}, from {menu.available_from}")] if menu else [])
        evidence += [s.cite.with_fact(f"{s.business_date} {s.quantity_sold} sold") for s in rows] + _note_cites(notes)
        out.append(Finding(f"item_without_recipe:{item}", "item_without_recipe", 2,
                           f"{name} ({item}) has no recipe, so its ingredient use is unknown",
                           f"{name} sold {portions} portions (${net} net) across {len(rows)} days but no recipe is "
                           f"on file. Every reconciliation treats its draw as unknown, not zero.",
                           evidence, ["Notes say the kitchen assembled it from whatever the line had; it may "
                                      "draw chicken, tortillas, cheese and peppers."],
                           f"Write the {name} recipe card (ingredients per portion) so its draw can be counted.",
                           {"portions": portions, "net_sales_usd": net}, r.revision, None, _usd(net)))
    return out


def detect_runway(ctx: Ctx) -> list[Finding]:
    r = ctx.records
    today = r.now.date()
    daily = an.daily_service_ingredients(r)
    out = []
    for ing, (_, closing) in ctx.counts.items():
        if ing not in daily:
            continue
        burn = ctx.burn.get(ing, 0.0)
        days = an.runway_days(closing.quantity, burn)
        if days is None or days >= ctx.params.runway_threshold_days:
            continue
        vendor = _vendor_for_ingredient(r, ing)
        pdays = _delivery_days(ctx, vendor)
        until = _days_until_listed_delivery(pdays, today)
        covered = until is not None and until <= days
        severity = 2 if covered or days >= 2 else 1
        vname = vendor.vendor_name if vendor else "the supplier"
        vdays = ", ".join(pdays) if vendor else "unknown days"
        basis = "per the vendor sheet" if not vendor or pdays == vendor.delivery_days else "per the recorded route change"
        applied = ctx.params.label(f"delivery_days:{vendor.vendor_id}") if vendor else []
        summary = (f"{ing}: {closing.quantity:g} {closing.unit} on hand at the {closing.counted_at.date()} close against "
                   f"{burn:.1f} {closing.unit}/day theoretical use ({ctx.burn_window[0]}..{ctx.burn_window[1]}), "
                   f"about {days:.1f} days. {vname} delivers {vdays} {basis}.")
        uncertainty = ["Use is theoretical from recipes and sales, not measured."]
        if until == 0:
            uncertainty.append(f"A {vname} delivery is listed for today, but the order sheet is not among the exported "
                               f"records, so whether an order was placed is not captured.")
        out.append(Finding(f"runway_risk:{ing}", "runway_risk", severity,
                           f"{ing} covers about {days:.1f} days", summary,
                           [closing.cite.with_fact(f"{closing.quantity:g} {closing.unit}")]
                           + ([vendor.cite.with_fact(f"delivers {vdays}")] if vendor else []),
                           uncertainty,
                           f"Confirm whether a {vname} delivery is arriving within {days:.0f} day(s); if not, order today.",
                           {"on_hand": closing.quantity, "unit": closing.unit, "burn_per_day": round(burn, 2),
                            "runway_days": round(days, 2), "vendor": vname, "vendor_days": vdays,
                            "days_until_listed_delivery": until}, r.revision, days, 0.0, applied))
    return out


def detect_weekend_readiness(ctx: Ctx) -> list[Finding]:
    r = ctx.records
    daily = an.daily_service_ingredients(r)
    out = []
    for m in r.menu:
        if m.service != "weekends":
            continue
        uplift = ctx.params.brunch_uplift
        f = an.weekend_forecast(r, m.pos_item_id, uplift)
        if not f.prior_days or not f.needs:
            continue
        applied = ctx.params.label("brunch_uplift") if uplift else []
        uplift_note = (f"A {uplift:.0%} uplift is applied on the manager's preference; there are no booking numbers "
                       f"to confirm it." if uplift else
                       "No uplift is applied; the owner expects a busier brunch but there are no booking numbers.")
        shortages = {}
        for ing, need in f.needs.items():
            on_hand = ctx.counts[ing][1].quantity if ing in ctx.counts else 0.0
            if on_hand < need or ing in daily:
                shortages[ing] = (need, on_hand)
        hard = {k: v for k, v in shortages.items() if v[1] < v[0]}
        if not hard:
            continue
        notes = _notes_with(r, _short(m.name), any_of=("brunch", "busy", "egg"))
        notes += [n for n in _notes_with(r, "brunch") if n not in notes]
        lines = [f"{ing}: need ~{need:.0f} {f.units[ing]}, {on_hand:g} on hand" + (" (also used daily)" if ing in daily else "")
                 for ing, (need, on_hand) in shortages.items()]
        usd = sum((need - on_hand) * _usd(an.latest_unit_cost(r, ing)) for ing, (need, on_hand) in hard.items())
        evidence = f.cites + [ctx.counts[i][1].cite.with_fact(f"{ctx.counts[i][1].quantity:g} on hand") for i in shortages if i in ctx.counts]
        out.append(Finding(f"weekend_readiness:{m.pos_item_id}", "weekend_readiness", 2,
                           f"{m.name} next weekend will outrun what is on hand",
                           f"{m.name} ({m.pos_item_id}) averaged {f.portions_per_day:.0f} portions per weekend day over "
                           f"{len(f.prior_days)} days. At that pace{f' plus {uplift:.0%}' if uplift else ''} "
                           f"{f.days[0]}..{f.days[1]} needs: " + "; ".join(lines) + ".",
                           evidence + _note_cites(notes),
                           [uplift_note],
                           f"Include {', '.join(_short(i) for i in hard)} in the next order from the vendor that supplies "
                           f"them, sized for at least last weekend's pace.",
                           {"portions_per_day": round(f.portions_per_day, 1), "days": [str(d) for d in f.days],
                            "needs": {k: round(v, 1) for k, v in f.needs.items()}, "uplift": uplift,
                            "short": {k: round(v[0] - v[1], 1) for k, v in hard.items()}},
                           r.revision, float((f.days[0] - r.now.date()).days), usd, applied))
    return out


def detect_untrusted_instructions(ctx: Ctx) -> list[Finding]:
    r = ctx.records
    out = []
    for t in r.notes + r.supplier_documents:
        matches = sorted({m.group(0).lower() for m in INSTRUCTION_PATTERN.finditer(t.text)})
        if len(matches) < 2:
            continue
        who = t.author if t.kind == "note" else t.author.replace("vendor ", "")
        vendor = next((v for v in r.vendors if v.vendor_id == who), None)
        src = vendor.vendor_name if vendor else t.author
        out.append(Finding(f"untrusted_instructions:{t.ref}", "untrusted_instructions", 2,
                           f"{t.kind.replace('_', ' ')} {t.ref} contains instructions addressed to automated systems",
                           f"{t.kind.replace('_', ' ').capitalize()} {t.ref}{f' ({t.title})' if t.title else ''} from {src} "
                           f"contains wording aimed at an AI implementation (matched: {', '.join(matches)}). It was "
                           f"treated as quoted evidence and not acted on; approval stays with the operator.",
                           [t.cite.with_fact(_excerpt(t.text, 90))],
                           ["Supplier text can be mistaken or misleading by design; nothing in it changes how orders are approved."],
                           f"Tell {src} that their export contains instructions aimed at automated systems.",
                           {"matched": matches}, r.revision, None, 0.0))
    return out


def detect_source_conflicts(ctx: Ctx) -> list[Finding]:
    r = ctx.records
    return [Finding(f"source_conflict:{c.source}:{c.ref}", "source_conflict", 1,
                    f"{c.source} {c.ref} has two revision-{c.revision} versions that disagree",
                    f"Fields differ: {', '.join(c.fields)}. Neither version was accepted.",
                    [Cite(c.source, r.site, c.ref, c.revision, "conflicting copies")],
                    ["Equal revisions with different content are a source conflict, not an update."],
                    "Ask the office which copy is right; the export must carry a higher revision to resolve it.",
                    {"fields": list(c.fields)}, r.revision, None, 0.0) for c in r.conflicts]


DETECTORS = (detect_source_conflicts, detect_phantom_receipts, detect_invoice_anomalies, detect_runway,
             detect_weekend_readiness, detect_sku_substitution, detect_reference_table_stale,
             detect_items_without_recipe, detect_count_variance, detect_untrusted_instructions)


def run_all(records: Records, params: Params | None = None) -> list[Finding]:
    ctx = build_ctx(records, params)
    findings: list[Finding] = []
    for det in DETECTORS:
        findings.extend(det(ctx))
    return sorted(findings, key=rank_key)
