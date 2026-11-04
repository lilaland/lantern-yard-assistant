"""Typed, cited views over the API records.

Everything downstream reasons over these objects, never over raw rows. Each
row carries a Cite: source file, site, row identity, source revision.

Source-revision rules from DATA_GUIDE.md are applied here: a higher revision
replaces an earlier version of the same source record; an older or repeated
revision never reverses a later one; equal revisions with different content
are a SourceConflict that is reported, never silently resolved.

Notes and supplier documents are UntrustedText: evidence, not instructions.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Callable, Iterable, TypeVar

from .api_client import ApiClient, RevisionChanged
from .config import SITE_ID, now_for_revision

IDENTITY = {
    "invoice_headers": "invoice_id", "invoice_lines": "row_id", "pos_daily": "row_id",
    "inventory_counts": "row_id", "vendors": "vendor_id", "vendor_catalog": "catalog_row_id",
    "menu": "pos_item_id", "recipes": "row_id", "ingredient_mappings": "mapping_id",
    "notes": "note_id", "supplier_documents": "document_id",
}


@dataclass(frozen=True)
class Cite:
    source: str
    site: str
    ref: str
    revision: int
    fact: str = ""

    def __str__(self) -> str:
        base = f"{self.source} {self.site} {self.ref} rev{self.revision}"
        return f"{base}: {self.fact}" if self.fact else base

    def with_fact(self, fact: str) -> "Cite":
        return Cite(self.source, self.site, self.ref, self.revision, fact)


@dataclass(frozen=True)
class SourceConflict:
    source: str
    ref: str
    revision: int
    fields: tuple[str, ...]


def dedupe_by_revision(rows: Iterable[dict[str, str]], source: str, key: str
                       ) -> tuple[list[dict[str, str]], list[SourceConflict]]:
    kept: dict[str, dict[str, str]] = {}
    conflicts: list[SourceConflict] = []
    for row in rows:
        ident, rev = row[key], int(row["source_revision"])
        current = kept.get(ident)
        if current is None or rev > int(current["source_revision"]):
            kept[ident] = row
        elif rev == int(current["source_revision"]) and row != current:
            fields = tuple(sorted(k for k in set(row) | set(current) if row.get(k) != current.get(k)))
            conflicts.append(SourceConflict(source, ident, rev, fields))
        # A lower revision after a higher one is an older copy: ignored.
    return list(kept.values()), conflicts


# ----- row types -----------------------------------------------------------

@dataclass(frozen=True)
class InvoiceHeader:
    cite: Cite
    invoice_id: str
    vendor_name: str
    vendor_account: str
    invoice_date: date
    delivery_date: date
    status: str
    tax: Decimal
    delivery_fee: Decimal
    invoice_total: Decimal
    currency: str
    invoice_number: str


@dataclass(frozen=True)
class InvoiceLine:
    cite: Cite
    row_id: str
    invoice_id: str
    sku: str
    item_description: str
    quantity: float
    unit: str
    unit_price: Decimal
    line_total: Decimal
    pack_quantity: float
    pack_unit: str

    @property
    def stock_quantity(self) -> float:
        """Stock received in pack units (lb or each), per DATA_GUIDE: billed qty x captured pack qty."""
        return self.quantity * self.pack_quantity


@dataclass(frozen=True)
class Sale:
    cite: Cite
    row_id: str
    business_date: date
    pos_item_id: str
    item_name: str
    quantity_sold: int
    gross_sales: Decimal
    discounts: Decimal
    net_sales: Decimal


@dataclass(frozen=True)
class Count:
    cite: Cite
    row_id: str
    counted_at: datetime
    ingredient_name: str
    quantity: float
    unit: str
    counted_by: str
    comment: str


@dataclass(frozen=True)
class Vendor:
    cite: Cite
    vendor_id: str
    vendor_name: str
    account_number: str
    delivery_days: tuple[str, ...]
    last_reviewed: date


@dataclass(frozen=True)
class CatalogItem:
    cite: Cite
    catalog_row_id: str
    vendor_id: str
    sku: str
    description: str
    pack_quantity: float
    pack_unit: str
    order_unit: str


@dataclass(frozen=True)
class MenuItem:
    cite: Cite
    pos_item_id: str
    name: str
    menu_price: Decimal
    available_from: date
    service: str


@dataclass(frozen=True)
class RecipeLine:
    cite: Cite
    row_id: str
    pos_item_id: str
    ingredient_name: str
    quantity: float
    unit: str
    yield_portions: float
    effective_from: date

    @property
    def per_portion(self) -> float:
        return self.quantity / self.yield_portions


@dataclass(frozen=True)
class Mapping:
    cite: Cite
    mapping_id: str
    vendor_id: str
    sku: str
    ingredient_id: str
    ingredient_name: str


@dataclass(frozen=True)
class UntrustedText:
    """Free text written by a person or a supplier. Quoted as evidence only."""
    cite: Cite
    kind: str  # "note" | "supplier_document"
    ref: str
    author: str
    recorded_at: str
    title: str
    text: str

    def wrapped(self) -> str:
        safe = self.text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return (f'<evidence kind="{self.kind}" source="{self.cite.source}" ref="{self.ref}" '
                f'author="{self.author}" at="{self.recorded_at}" revision="{self.cite.revision}">\n'
                f"{safe}\n</evidence>")


@dataclass
class Records:
    revision: int
    now: datetime
    site: str
    invoice_headers: list[InvoiceHeader]
    invoice_lines: list[InvoiceLine]
    sales: list[Sale]
    counts: list[Count]
    vendors: list[Vendor]
    catalog: list[CatalogItem]
    menu: list[MenuItem]
    recipes: list[RecipeLine]
    mappings: list[Mapping]
    notes: list[UntrustedText]
    supplier_documents: list[UntrustedText]
    conflicts: list[SourceConflict] = field(default_factory=list)

    def header(self, invoice_id: str) -> InvoiceHeader | None:
        return next((h for h in self.invoice_headers if h.invoice_id == invoice_id), None)

    def lines_for(self, invoice_id: str) -> list[InvoiceLine]:
        return [l for l in self.invoice_lines if l.invoice_id == invoice_id]

    def ingredient_for_sku(self, sku: str) -> str | None:
        return next((m.ingredient_name for m in self.mappings if m.sku == sku), None)

    def vendor_by_account(self, account: str) -> Vendor | None:
        return next((v for v in self.vendors if v.account_number == account), None)

    def catalog_item(self, vendor_id: str, sku: str) -> CatalogItem | None:
        return next((c for c in self.catalog if c.vendor_id == vendor_id and c.sku == sku), None)


# ----- parsing ---------------------------------------------------------------

def _dec(s: str) -> Decimal:
    return Decimal(s.strip())


def _num(s: str) -> float:
    return float(s.strip())


def _date(s: str) -> date:
    return date.fromisoformat(s.strip()[:10])


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.strip())


def _cite(source: str, row: dict[str, str], key: str) -> Cite:
    return Cite(source, row["site_id"], row[key], int(row["source_revision"]))


def _header(src: str, r: dict[str, str]) -> InvoiceHeader:
    return InvoiceHeader(_cite(src, r, "invoice_id"), r["invoice_id"], r["vendor_name"], r["vendor_account"],
                         _date(r["invoice_date"]), _date(r["delivery_date"]), r["status"], _dec(r["tax"]),
                         _dec(r["delivery_fee"]), _dec(r["invoice_total"]), r["currency"], r["invoice_number"])


def _line(src: str, r: dict[str, str]) -> InvoiceLine:
    return InvoiceLine(_cite(src, r, "row_id"), r["row_id"], r["invoice_id"], r["sku"], r["item_description"],
                       _num(r["quantity"]), r["unit"], _dec(r["unit_price"]), _dec(r["line_total"]),
                       _num(r["pack_quantity"]), r["pack_unit"])


def _sale(src: str, r: dict[str, str]) -> Sale:
    return Sale(_cite(src, r, "row_id"), r["row_id"], _date(r["business_date"]), r["pos_item_id"], r["item_name"],
                int(_num(r["quantity_sold"])), _dec(r["gross_sales"]), _dec(r["discounts"]), _dec(r["net_sales"]))


def _count(src: str, r: dict[str, str]) -> Count:
    return Count(_cite(src, r, "row_id"), r["row_id"], _dt(r["counted_at"]), r["ingredient_name"],
                 _num(r["quantity"]), r["unit"], r["counted_by"], r.get("comment", ""))


def _vendor(src: str, r: dict[str, str]) -> Vendor:
    days = tuple(d.strip() for d in r["delivery_days"].split(",") if d.strip())
    return Vendor(_cite(src, r, "vendor_id"), r["vendor_id"], r["vendor_name"], r["account_number"], days,
                  _date(r["last_reviewed"]))


def _catalog(src: str, r: dict[str, str]) -> CatalogItem:
    return CatalogItem(_cite(src, r, "catalog_row_id"), r["catalog_row_id"], r["vendor_id"], r["sku"],
                       r["description"], _num(r["pack_quantity"]), r["pack_unit"], r["order_unit"])


def _menu(src: str, r: dict[str, str]) -> MenuItem:
    return MenuItem(_cite(src, r, "pos_item_id"), r["pos_item_id"], r["name"], _dec(r["menu_price"]),
                    _date(r["available_from"]), r["service"])


def _recipe(src: str, r: dict[str, str]) -> RecipeLine:
    return RecipeLine(_cite(src, r, "row_id"), r["row_id"], r["pos_item_id"], r["ingredient_name"],
                      _num(r["quantity"]), r["unit"], _num(r["yield_portions"]), _date(r["effective_from"]))


def _mapping(src: str, r: dict[str, str]) -> Mapping:
    return Mapping(_cite(src, r, "mapping_id"), r["mapping_id"], r["vendor_id"], r["sku"], r["ingredient_id"],
                   r["ingredient_name"])


def _note(src: str, r: dict[str, str]) -> UntrustedText:
    return UntrustedText(_cite(src, r, "note_id"), "note", r["note_id"], r["author"], r["recorded_at"], "", r["text"])


def _supplier_doc(src: str, r: dict[str, str]) -> UntrustedText:
    return UntrustedText(_cite(src, r, "document_id"), "supplier_document", r["document_id"],
                         f"vendor {r['vendor_id']}", "", r["title"], r["text"])


T = TypeVar("T")

_LOADERS: dict[str, Callable[[str, dict[str, str]], object]] = {
    "invoice_headers": _header, "invoice_lines": _line, "pos_daily": _sale, "inventory_counts": _count,
    "vendors": _vendor, "vendor_catalog": _catalog, "menu": _menu, "recipes": _recipe,
    "ingredient_mappings": _mapping, "notes": _note, "supplier_documents": _supplier_doc,
}


def with_clock(records: Records, days_ahead: int) -> Records:
    """The same records with `now` moved forward by whole days: the demo's "new session, next day". The data is
    unchanged (the last count is still the last count); only what depends on the date moves: delivery dates,
    cutoffs, the weekend, what is "after today"."""
    if not days_ahead:
        return records
    return dataclasses.replace(records, now=records.now + timedelta(days=int(days_ahead)))


def load_all(client: ApiClient) -> Records:
    """Fetch every resource at one dataset revision and build typed, deduplicated records."""
    parsed: dict[str, list] = {}
    revisions: set[int] = set()
    conflicts: list[SourceConflict] = []
    for resource, loader in _LOADERS.items():
        coll = client.get_all(resource)
        revisions.add(coll.dataset_revision)
        rows, found = dedupe_by_revision(coll.records, coll.source, IDENTITY[resource])
        conflicts.extend(found)
        parsed[resource] = [loader(coll.source, r) for r in rows]
    if len(revisions) != 1:
        raise RevisionChanged(f"resources span revisions {sorted(revisions)}; reload")
    revision = revisions.pop()
    return Records(
        revision=revision,
        now=_dt(now_for_revision(revision)),
        site=SITE_ID,
        invoice_headers=parsed["invoice_headers"],
        invoice_lines=parsed["invoice_lines"],
        sales=parsed["pos_daily"],
        counts=parsed["inventory_counts"],
        vendors=parsed["vendors"],
        catalog=parsed["vendor_catalog"],
        menu=parsed["menu"],
        recipes=parsed["recipes"],
        mappings=parsed["ingredient_mappings"],
        notes=parsed["notes"],
        supplier_documents=parsed["supplier_documents"],
        conflicts=conflicts,
    )
