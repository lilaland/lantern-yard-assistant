"""Pure calculations over Records. No I/O, no model.

Every result carries the citations of the rows that produced it, so a
derived number can always be traced back to source file, row, and revision.
Conventions follow DATA_GUIDE.md: stock received = billed quantity x the
pack quantity captured on that line; recipe quantities are stock withdrawn
per yield portion; an absent recipe is unknown usage, not zero.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from statistics import median

from .records import Cite, Count, InvoiceHeader, InvoiceLine, RecipeLine, Records, Sale

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def weekday_name(d: date) -> str:
    return WEEKDAYS[d.weekday()]


# ----- theoretical usage -----------------------------------------------------

@dataclass
class Usage:
    totals: dict[str, float]
    by_item: dict[str, dict[str, float]]
    portions: dict[str, int]
    unknown_items: dict[str, int]
    units: dict[str, str]
    recipe_cites: list[Cite]
    sales_cite: Cite | None
    window: tuple[date, date] | None


def recipe_index(recipes: list[RecipeLine]) -> dict[str, list[RecipeLine]]:
    idx: dict[str, list[RecipeLine]] = defaultdict(list)
    for r in recipes:
        idx[r.pos_item_id].append(r)
    return idx


def effective_recipe(lines: list[RecipeLine], on: date) -> list[RecipeLine]:
    """Latest recipe line per ingredient whose effective_from is on or before the date."""
    best: dict[str, RecipeLine] = {}
    for line in lines:
        if line.effective_from <= on:
            current = best.get(line.ingredient_name)
            if current is None or line.effective_from > current.effective_from:
                best[line.ingredient_name] = line
    return list(best.values())


def theoretical_usage(sales: list[Sale], recipes: list[RecipeLine],
                      date_from: date | None = None, date_to: date | None = None) -> Usage:
    idx = recipe_index(recipes)
    totals: dict[str, float] = defaultdict(float)
    by_item: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    portions: dict[str, int] = defaultdict(int)
    unknown: dict[str, int] = defaultdict(int)
    units: dict[str, str] = {}
    used_recipes: dict[str, Cite] = {}
    rows = [s for s in sales if (date_from is None or s.business_date >= date_from)
            and (date_to is None or s.business_date <= date_to)]
    for s in rows:
        portions[s.pos_item_id] += s.quantity_sold
        lines = effective_recipe(idx.get(s.pos_item_id, []), s.business_date)
        if not lines:
            unknown[s.pos_item_id] += s.quantity_sold
            continue
        for line in lines:
            qty = s.quantity_sold * line.per_portion
            totals[line.ingredient_name] += qty
            by_item[s.pos_item_id][line.ingredient_name] += qty
            units[line.ingredient_name] = line.unit
            used_recipes[line.row_id] = line.cite
    sales_cite = None
    if rows:
        lo, hi = min(r.business_date for r in rows), max(r.business_date for r in rows)
        sales_cite = Cite(rows[0].cite.source, rows[0].cite.site, f"{len(rows)} rows {lo}..{hi}",
                          rows[0].cite.revision, "daily item sales")
    window = (lo, hi) if rows else None
    return Usage(dict(totals), {k: dict(v) for k, v in by_item.items()}, dict(portions), dict(unknown),
                 units, list(used_recipes.values()), sales_cite, window)


# ----- purchases -------------------------------------------------------------

@dataclass
class Purchases:
    totals: dict[str, float]
    spend: dict[str, Decimal]
    units: dict[str, str]
    cites: dict[str, list[Cite]]
    unmapped: list[InvoiceLine]
    excluded: tuple[str, ...]


def purchases(records: Records, exclude_invoices: tuple[str, ...] = (),
              date_from: date | None = None, date_to: date | None = None) -> Purchases:
    headers = {h.invoice_id: h for h in records.invoice_headers}
    totals: dict[str, float] = defaultdict(float)
    spend: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    units: dict[str, str] = {}
    cites: dict[str, list[Cite]] = defaultdict(list)
    unmapped: list[InvoiceLine] = []
    for line in records.invoice_lines:
        h = headers.get(line.invoice_id)
        if h is None or line.invoice_id in exclude_invoices:
            continue
        if date_from and h.invoice_date < date_from or date_to and h.invoice_date > date_to:
            continue
        ing = records.ingredient_for_sku(line.sku)
        if ing is None:
            unmapped.append(line)
            continue
        totals[ing] += line.stock_quantity
        spend[ing] += line.line_total
        units[ing] = line.pack_unit
        cites[ing].append(line.cite.with_fact(
            f"{line.quantity:g} {line.unit} x {line.pack_quantity:g} {line.pack_unit} = "
            f"{line.stock_quantity:g} {line.pack_unit}, ${line.line_total}, {h.invoice_date} {h.status}"))
    return Purchases(dict(totals), dict(spend), units, dict(cites), unmapped, tuple(exclude_invoices))


# ----- what counts as on hand --------------------------------------------------

def unreceived_invoices(records: Records) -> list[InvoiceHeader]:
    """Invoices the office itself does not mark received (e.g. scheduled)."""
    return [h for h in records.invoice_headers if h.status != "received"]


def not_on_hand_invoices(records: Records) -> list[InvoiceHeader]:
    """Invoices whose goods cannot be on hand yet: not received, or delivery dated after today."""
    today = records.now.date()
    return [h for h in records.invoice_headers if h.status != "received" or h.delivery_date > today]


# ----- reconciliation ----------------------------------------------------------

@dataclass
class Reconciliation:
    ingredient: str
    unit: str
    opening: float
    purchased: float
    theoretical: float
    expected_close: float
    counted: float
    gap: float
    excluded_invoices: tuple[str, ...]
    cites: list[Cite]

    @property
    def gap_pct(self) -> float | None:
        return (self.gap / self.expected_close) if self.expected_close else None


def opening_and_closing(records: Records) -> dict[str, tuple[Count, Count]]:
    by_ing: dict[str, list[Count]] = defaultdict(list)
    for c in records.counts:
        by_ing[c.ingredient_name].append(c)
    out: dict[str, tuple[Count, Count]] = {}
    for ing, cs in by_ing.items():
        cs.sort(key=lambda c: c.counted_at)
        out[ing] = (cs[0], cs[-1])
    return out


def reconcile_all(records: Records, exclude_invoices: tuple[str, ...] = ()) -> dict[str, Reconciliation]:
    counts = opening_and_closing(records)
    bought = purchases(records, exclude_invoices)
    usage = theoretical_usage(records.sales, records.recipes)
    out: dict[str, Reconciliation] = {}
    for ing, (opening, closing) in counts.items():
        p = bought.totals.get(ing, 0.0)
        u = usage.totals.get(ing, 0.0)
        expected = opening.quantity + p - u
        cites = [opening.cite.with_fact(f"opening {opening.quantity:g} {opening.unit}"),
                 closing.cite.with_fact(f"closing {closing.quantity:g} {closing.unit}")]
        cites += bought.cites.get(ing, [])
        cites += [r.cite.with_fact(f"{r.per_portion:g} {r.unit} per portion of {r.pos_item_id}")
                  for r in records.recipes if r.ingredient_name == ing]
        if usage.sales_cite:
            cites.append(usage.sales_cite)
        out[ing] = Reconciliation(ing, closing.unit, opening.quantity, p, u, expected, closing.quantity,
                                  closing.quantity - expected, tuple(exclude_invoices), cites)
    return out


# ----- invoice checks ------------------------------------------------------------

@dataclass
class InvoiceIssue:
    kind: str
    invoice_id: str
    detail: str
    numbers: dict[str, object]
    cites: list[Cite]


def unit_cost(line: InvoiceLine) -> Decimal:
    """Cost per pack unit (per lb or per each) as billed on this line."""
    return line.line_total / Decimal(str(line.stock_quantity))


def invoice_checks(records: Records, outlier_ratio: float = 2.0) -> list[InvoiceIssue]:
    issues: list[InvoiceIssue] = []
    today = records.now.date()
    for h in records.invoice_headers:
        lines = records.lines_for(h.invoice_id)
        line_sum = sum((l.line_total for l in lines), Decimal("0"))
        computed = line_sum + h.tax + h.delivery_fee
        if computed != h.invoice_total:
            issues.append(InvoiceIssue(
                "header_total_mismatch", h.invoice_id,
                f"{h.invoice_id} header total ${h.invoice_total} but lines + tax + fee = ${computed} "
                f"(difference ${h.invoice_total - computed})",
                {"header_total": h.invoice_total, "computed": computed, "difference": h.invoice_total - computed},
                [h.cite.with_fact(f"total ${h.invoice_total}, tax ${h.tax}, fee ${h.delivery_fee}")]
                + [l.cite.with_fact(f"${l.line_total}") for l in lines]))
        if h.status == "received" and h.delivery_date > today:
            issues.append(InvoiceIssue(
                "status_inconsistent_with_date", h.invoice_id,
                f"{h.invoice_id} is marked received but its delivery date {h.delivery_date} is after "
                f"today ({today})",
                {"status": h.status, "delivery_date": h.delivery_date, "today": today,
                 "invoice_total": h.invoice_total},
                [h.cite.with_fact(f"status {h.status}, delivery {h.delivery_date}, invoice {h.invoice_date}")]
                + [l.cite.with_fact(f"{l.stock_quantity:g} {l.pack_unit} {l.item_description}") for l in lines]))

    by_sku: dict[str, list[InvoiceLine]] = defaultdict(list)
    for l in records.invoice_lines:
        by_sku[l.sku].append(l)
    for sku, lines in by_sku.items():
        if len(lines) < 3:
            continue
        for l in lines:
            others = [unit_cost(o) for o in lines if o is not l]
            med = median(others)
            if med and max(unit_cost(l) / med, med / unit_cost(l)) >= Decimal(str(outlier_ratio)):
                overcharge = (unit_cost(l) - med) * Decimal(str(l.stock_quantity))
                issues.append(InvoiceIssue(
                    "unit_price_outlier", l.invoice_id,
                    f"{l.invoice_id} line {l.row_id}: {sku} billed at ${l.unit_price} per {l.unit} "
                    f"(${unit_cost(l):.2f}/{l.pack_unit}); other purchases median ${med:.2f}/{l.pack_unit}",
                    {"unit_cost": unit_cost(l), "median_unit_cost": med, "ratio": float(unit_cost(l) / med),
                     "amount_vs_median": overcharge, "line_total": l.line_total},
                    [l.cite.with_fact(f"{l.quantity:g} {l.unit} @ ${l.unit_price} = ${l.line_total}")]
                    + [o.cite.with_fact(f"${unit_cost(o):.2f}/{o.pack_unit}") for o in lines if o is not l]))

    by_account: dict[str, dict[str, list[InvoiceHeader]]] = defaultdict(lambda: defaultdict(list))
    for h in records.invoice_headers:
        by_account[h.vendor_account][h.vendor_name].append(h)
    for account, names in by_account.items():
        if len(names) > 1:
            first = next(iter(names.values()))[0]
            issues.append(InvoiceIssue(
                "vendor_name_variance", first.invoice_id,
                f"account {account} appears under {len(names)} display names: " + ", ".join(sorted(names)),
                {"account": account, "names": sorted(names)},
                [hs[0].cite.with_fact(f"vendor_name {name!r}") for name, hs in names.items()]))
    return issues


# ----- prices --------------------------------------------------------------------

@dataclass(frozen=True)
class PricePoint:
    date: date
    invoice_id: str
    sku: str
    unit_cost: Decimal
    pack_quantity: float
    pack_unit: str
    cite: Cite


def price_history(records: Records, ingredient: str) -> list[PricePoint]:
    headers = {h.invoice_id: h for h in records.invoice_headers}
    points = []
    for l in records.invoice_lines:
        if records.ingredient_for_sku(l.sku) == ingredient and l.invoice_id in headers:
            h = headers[l.invoice_id]
            points.append(PricePoint(h.invoice_date, l.invoice_id, l.sku, unit_cost(l), l.pack_quantity,
                                     l.pack_unit, l.cite.with_fact(
                                         f"{h.invoice_date} {l.sku} ${unit_cost(l):.3f}/{l.pack_unit}, "
                                         f"pack {l.pack_quantity:g} {l.pack_unit}")))
    return sorted(points, key=lambda p: (p.date, p.invoice_id))


def price_changes(points: list[PricePoint], min_change: float = 0.01) -> list[tuple[PricePoint, PricePoint]]:
    changes = []
    for a, b in zip(points, points[1:]):
        if a.unit_cost and abs(float(b.unit_cost / a.unit_cost) - 1.0) >= min_change:
            changes.append((a, b))
    return changes


def latest_unit_cost(records: Records, ingredient: str) -> Decimal | None:
    pts = price_history(records, ingredient)
    return pts[-1].unit_cost if pts else None


# ----- runway and forecast ---------------------------------------------------------

def last_sales_date(records: Records) -> date:
    return max(s.business_date for s in records.sales)


def daily_burn(records: Records, days: int = 7) -> tuple[dict[str, float], tuple[date, date]]:
    end = last_sales_date(records)
    start = end - timedelta(days=days - 1)
    usage = theoretical_usage(records.sales, records.recipes, start, end)
    return {k: v / days for k, v in usage.totals.items()}, (start, end)


def runway_days(on_hand: float, burn_per_day: float) -> float | None:
    return (on_hand / burn_per_day) if burn_per_day > 0 else None


def daily_service_ingredients(records: Records) -> set[str]:
    daily_items = {m.pos_item_id for m in records.menu if m.service == "daily"}
    return {r.ingredient_name for r in records.recipes if r.pos_item_id in daily_items}


@dataclass
class WeekendForecast:
    item: str
    days: list[date]
    prior_days: list[Sale]
    portions_per_day: float
    uplift: float
    portions_total: float
    needs: dict[str, float]
    units: dict[str, str]
    cites: list[Cite]


def next_weekend(after: date) -> list[date]:
    sat = after + timedelta(days=(5 - after.weekday()) % 7 or 7)
    return [sat, sat + timedelta(days=1)]


def weekend_forecast(records: Records, item_id: str, uplift: float = 0.0) -> WeekendForecast:
    prior = sorted((s for s in records.sales if s.pos_item_id == item_id and s.business_date.weekday() >= 5),
                   key=lambda s: s.business_date)
    per_day = (sum(s.quantity_sold for s in prior) / len(prior)) if prior else 0.0
    days = next_weekend(records.now.date())
    total = per_day * len(days) * (1 + uplift)
    recipe = effective_recipe(recipe_index(records.recipes).get(item_id, []), days[0])
    needs = {r.ingredient_name: total * r.per_portion for r in recipe}
    units = {r.ingredient_name: r.unit for r in recipe}
    cites = [s.cite.with_fact(f"{s.business_date} {s.quantity_sold} sold") for s in prior]
    cites += [r.cite.with_fact(f"{r.per_portion:g} {r.unit} per portion") for r in recipe]
    return WeekendForecast(item_id, days, prior, per_day, uplift, total, needs, units, cites)


def deliveries_by_weekday(records: Records, account: str, since: date | None = None
                          ) -> dict[str, list[InvoiceHeader]]:
    out: dict[str, list[InvoiceHeader]] = defaultdict(list)
    for h in records.invoice_headers:
        if h.vendor_account == account and h.status == "received" and h.delivery_date <= records.now.date():
            if since is None or h.delivery_date > since:
                out[weekday_name(h.delivery_date)].append(h)
    return dict(out)
