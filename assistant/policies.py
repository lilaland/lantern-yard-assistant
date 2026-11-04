"""Policies and preferences: how ledger beliefs steer detectors, ordering and rechecks.

This module is the only bridge between the ledger and the deterministic layer.
Detectors never read the ledger; they take ``Params``. The model never writes
policies; a human does, through onboarding or ``ask``. Defaults live here and
are exactly what ``onboard --defaults`` records, under a human source, because
the manager is accepting them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import analysis as an
from .detectors import Finding, Params
from .ledger import Belief, Ledger, Outcome
from .records import Records

DEFAULT_SOURCE = "human:onboarding-defaults"

# (kind, subject, statement, value)
DEFAULTS: tuple[tuple[str, str, str, dict], ...] = (
    ("policy", "inbound.on_hand_rule",
     "Inbound stock counts as on hand only after someone confirms it physically arrived; an office "
     "'received' status with a future delivery date is not enough.",
     {"rule": "physical_confirmation"}),
    ("policy", "sources.precedence",
     "When a manager note or a delivered invoice disagrees with a reference table (vendor sheet, catalog), "
     "plan on the newer note or invoice and flag the table as stale.",
     {"rule": "newer_notes_and_invoices_override_reference_tables"}),
    ("policy", "ingredient.ING-CHICKEN.count_unit",
     "Count and order chicken in pounds, never in cases, because the case size changed.",
     {"unit": "lb"}),
    ("policy", "recipes.missing_item_rule",
     "A menu item with no recipe has unknown ingredient use; flag it, do not approximate.",
     {"rule": "unknown_and_flag"}),
    ("preference", "invoices.price_anomaly_ratio",
     "Flag an invoice line when its unit cost is at least this multiple of the usual cost for that SKU.",
     {"ratio": 2.0}),
    ("preference", "order.buffer_days",
     "Size orders to cover the gap to the next delivery plus this many days of buffer.",
     {"days": 1}),
    ("preference", "order.cover_through",
     "A midweek delivery has to get the kitchen through the weekend.",
     {"weekday": "Sunday"}),
    ("preference", "order.brunch_uplift",
     "Plan weekend breakfast ingredients at last weekend's pace plus this uplift, since the owner expects a "
     "busier brunch and there are no booking numbers.",
     {"uplift": 0.25}),
    ("preference", "order.max_unflagged_usd",
     "Say so explicitly when a proposed order exceeds this value.",
     {"usd": 1000}),
    ("preference", "order.sku.ING-CHICKEN",
     "Order chicken as the 20 lb case the vendor now ships, not the old 40 lb SKU.",
     {"vendor_id": "V1", "sku": "AW-CHK20"}),
)

WEEKDAY_RE = re.compile("|".join(an.WEEKDAYS), re.IGNORECASE)


# ----- onboarding defaults ------------------------------------------------------

def write_defaults(ledger: Ledger, records: Records | None = None, source: str = DEFAULT_SOURCE) -> list[Outcome]:
    """Record the default policies and preferences, plus evidence-sourced assertions if records are given."""
    out = [ledger.record(kind, subject, statement, value, source) for kind, subject, statement, value in DEFAULTS]
    if records is not None:
        for subject, statement, value, src, recheck in evidence_assertions(records):
            out.append(ledger.record("assertion", subject, statement, value, src, recheck, records.revision))
    return out


def evidence_assertions(records: Records) -> list[tuple[str, str, dict, str, str]]:
    """Facts read straight from records that planning relies on, each tied to its source row.

    Kept deliberately small: invoice fields the API can later correct (rechecked on revision change),
    a route change a note states in plain weekday names, and a note that says inbound goods were not seen.
    """
    out: list[tuple[str, str, dict, str, str]] = []
    today = records.now.date()
    for h in records.invoice_headers:
        if h.status == "received" and h.delivery_date > today or h.status != "received":
            src = f"invoice:{h.invoice_id}@rev{h.cite.revision}"
            out.append((f"invoice.{h.invoice_id}.status", f"{h.invoice_id} status is '{h.status}' per the office.",
                        {"status": h.status}, src, "on_revision_change"))
            out.append((f"invoice.{h.invoice_id}.delivery_date", f"{h.invoice_id} delivery date is {h.delivery_date}.",
                        {"date": str(h.delivery_date)}, src, "on_revision_change"))
            for n in records.notes:
                low = n.text.lower()
                if "received" in low and any(k in low for k in ("not seen", "haven't seen", "have not seen")):
                    out.append((f"invoice.{h.invoice_id}.physically_received",
                                f"Note {n.ref}: the office marked the next order received but the cases were not seen.",
                                {"received": False}, f"note:{n.ref}", "never"))
                    break
    for v in records.vendors:
        short = v.vendor_name.split(",")[0].split()[0].lower()
        for n in records.notes:
            low = n.text.lower()
            if short not in low or "instead of" not in low:
                continue
            before = n.text[: low.index("instead of")]
            days = [d.capitalize() for d in WEEKDAY_RE.findall(before)]
            days = [d for d in an.WEEKDAYS if d in days]
            if not days:
                continue
            cutoff = re.search(r"orders? by ([^.]+)\.", n.text, re.IGNORECASE)
            out.append((f"vendor.{v.vendor_id}.delivery_days",
                        f"Note {n.ref}: {v.vendor_name} delivers {', '.join(days)}"
                        f"{'; orders by ' + cutoff.group(1) if cutoff else ''}.",
                        {"days": days, "cutoff": cutoff.group(1) if cutoff else None}, f"note:{n.ref}", "never"))
            break
    return out


# ----- ledger -> detector params ----------------------------------------------------

def detector_params(ledger: Ledger) -> Params:
    applied: dict[str, str] = {}
    kw: dict = {}
    b = ledger.one("policy", "inbound.on_hand_rule")
    if b:
        kw["on_hand_rule"] = b.value["rule"]
        applied["on_hand_rule"] = f"{b.subject}={b.value['rule']} ({b.source})"
    b = ledger.one("preference", "invoices.price_anomaly_ratio")
    if b:
        kw["price_outlier_ratio"] = float(b.value["ratio"])
        applied["price_outlier_ratio"] = f"{b.subject}={b.value['ratio']} ({b.source})"
    b = ledger.one("preference", "order.brunch_uplift")
    if b:
        kw["brunch_uplift"] = float(b.value["uplift"])
        applied["brunch_uplift"] = f"{b.subject}={b.value['uplift']} ({b.source})"
    b = ledger.one("policy", "recipes.missing_item_rule")
    if b:
        kw["missing_recipe_rule"] = b.value["rule"]
        applied["missing_recipe_rule"] = f"{b.subject}={b.value['rule']} ({b.source})"
    received, not_received = set(), set()
    for a in ledger.active("assertion"):
        m = re.fullmatch(r"invoice\.([A-Z0-9-]+)\.physically_received", a.subject)
        if m:
            (received if a.value.get("received") else not_received).add(m.group(1))
            applied[f"received:{m.group(1)}"] = f"{a.subject}={a.value.get('received')} ({a.source})"
        m = re.fullmatch(r"vendor\.([A-Z0-9-]+)\.delivery_days", a.subject)
        if m and ledger.value("policy", "sources.precedence", {}).get("rule") != "reference_tables":
            kw.setdefault("delivery_days", {})[m.group(1)] = tuple(a.value["days"])
            applied[f"delivery_days:{m.group(1)}"] = f"{a.subject}={'/'.join(a.value['days'])} ({a.source})"
    return Params(confirmed_received=frozenset(received), confirmed_not_received=frozenset(not_received),
                  applied=applied, **kw)


def treat_inbound(invoice_id: str, status: str, delivery_date, records: Records, ledger: Ledger) -> bool:
    """Does this invoice's stock count as on hand under the current policy and confirmations?"""
    from .detectors import on_hand_exclusions
    return invoice_id not in on_hand_exclusions(records, detector_params(ledger))


# ----- ordering preferences -----------------------------------------------------------

@dataclass(frozen=True)
class OrderPrefs:
    buffer_days: int = 1
    cover_through: str = "Sunday"
    uplift: float = 0.0
    max_unflagged_usd: float = 1000.0
    sku_overrides: dict = field(default_factory=dict)   # ingredient_id -> {"vendor_id", "sku"}
    count_units: dict = field(default_factory=dict)     # ingredient_id -> unit
    applied: dict = field(default_factory=dict)         # pref name -> belief label


def order_prefs(ledger: Ledger) -> OrderPrefs:
    applied: dict[str, str] = {}
    kw: dict = {}

    def take(subject: str, key: str, attr: str, cast=lambda x: x):
        b = ledger.one("preference", subject)
        if b:
            kw[attr] = cast(b.value[key])
            applied[attr] = f"{b.subject}={b.value[key]} ({b.source})"

    take("order.buffer_days", "days", "buffer_days", int)
    take("order.cover_through", "weekday", "cover_through")
    take("order.brunch_uplift", "uplift", "uplift", float)
    take("order.max_unflagged_usd", "usd", "max_unflagged_usd", float)
    skus, units = {}, {}
    for b in ledger.active("preference"):
        m = re.fullmatch(r"order\.sku\.(.+)", b.subject)
        if m:
            skus[m.group(1)] = dict(b.value)
            applied[f"sku:{m.group(1)}"] = f"{b.subject}={b.value['sku']} ({b.source})"
    for b in ledger.active("policy"):
        m = re.fullmatch(r"ingredient\.(.+)\.count_unit", b.subject)
        if m:
            units[m.group(1)] = b.value["unit"]
            applied[f"unit:{m.group(1)}"] = f"{b.subject}={b.value['unit']} ({b.source})"
    return OrderPrefs(sku_overrides=skus, count_units=units, applied=applied, **kw)


# ----- recheck on every run -----------------------------------------------------------

@dataclass
class RecheckReport:
    revision: int
    previous_revision: int | None
    first_run: bool
    observations_new: list[Belief] = field(default_factory=list)
    observations_changed: list[tuple[Belief, Belief]] = field(default_factory=list)
    observations_cleared: list[tuple[Belief, str]] = field(default_factory=list)
    observations_unchanged: int = 0
    assertions_superseded: list[tuple[Belief, Belief]] = field(default_factory=list)
    assertions_conflicted: list[tuple[Belief, Belief]] = field(default_factory=list)
    retained: dict = field(default_factory=dict)  # kind -> count of active human beliefs
    previous_headline: dict | None = None         # {"id", "title", "severity"} from the last run
    headline: dict | None = None
    new_notes: list = field(default_factory=list)  # UntrustedText not seen on the last run
    previous_run_wall: str | None = None          # wall-clock time of the last run

    @property
    def revision_changed(self) -> bool:
        return self.previous_revision is not None and self.previous_revision != self.revision

    @property
    def headline_changed(self) -> bool:
        return bool(self.previous_headline and self.headline and self.previous_headline["id"] != self.headline["id"])

    @property
    def anything_changed(self) -> bool:
        return bool(self.revision_changed or self.headline_changed or self.observations_new or self.observations_changed
                    or self.observations_cleared or self.assertions_superseded or self.assertions_conflicted
                    or self.new_notes)


def observation_value(f: Finding) -> dict:
    return {"severity": f.severity, "title": f.title, "numbers": f.numbers}


def recheck(ledger: Ledger, records: Records, findings: list[Finding]) -> RecheckReport:
    """Re-record detector observations, recheck evidence assertions, and report what moved."""
    import json as _json
    from datetime import datetime as _dt
    rev = records.revision
    prev = ledger.meta("last_revision")
    report = RecheckReport(rev, int(prev) if prev else None, prev is None)
    report.previous_run_wall = ledger.meta("last_run_wall")
    prev_head = ledger.meta("last_headline")
    report.previous_headline = _json.loads(prev_head) if prev_head else None
    if findings:
        report.headline = {"id": findings[0].id, "title": findings[0].title, "severity": findings[0].severity}
    prev_notes = ledger.meta("last_note_refs")
    if prev_notes is not None:
        seen = set(_json.loads(prev_notes))
        report.new_notes = [n for n in records.notes if n.ref not in seen]

    current = {f.id for f in findings}
    for f in findings:
        o = ledger.record("observation", f.id, f.title, observation_value(f), f"detector:{f.cls}", revision=rev)
        if o.action == "added":
            report.observations_new.append(o.belief)
        elif o.action == "unchanged":
            report.observations_unchanged += 1
        else:
            report.observations_changed.append((o.previous, o.belief))

    for b in ledger.active("observation"):
        if b.subject in current or b.value.get("cleared"):
            continue
        reason = _cleared_reason(b.subject, records)
        o = ledger.record("observation", b.subject, f"{b.statement} — no longer detected at revision {rev}: {reason}",
                          {"cleared": True, "reason": reason, "last_seen_revision": b.revision},
                          "detector:cleared", revision=rev)
        report.observations_cleared.append((b, reason))

    for a in ledger.active("assertion") + [old for old, _ in ledger.conflicts() if old.kind == "assertion"]:
        m = re.fullmatch(r"invoice\.([A-Z0-9-]+)\.(status|delivery_date)", a.subject)
        if not m:
            continue
        h = records.header(m.group(1))
        if h is None:
            continue
        now_value = {"status": h.status} if m.group(2) == "status" else {"date": str(h.delivery_date)}
        if now_value == a.value:
            continue
        src = f"invoice:{h.invoice_id}@rev{h.cite.revision}"
        if a.source == src:
            continue
        statement = (f"{h.invoice_id} status is '{h.status}' per the office." if m.group(2) == "status"
                     else f"{h.invoice_id} delivery date is {h.delivery_date}.")
        o = ledger.record("assertion", a.subject, statement, now_value, src, "on_revision_change", rev)
        if o.action == "superseded":
            report.assertions_superseded.append((o.previous, o.belief))
        elif o.action == "conflicted":
            report.assertions_conflicted.append((o.previous, o.belief))

    counts = ledger.counts()
    report.retained = {k: counts[k]["active"] for k in ("policy", "preference")}
    ledger.set_meta("last_revision", str(rev))
    ledger.set_meta("last_run_at", records.now.isoformat())
    ledger.set_meta("last_run_wall", _dt.now().astimezone().isoformat(timespec="seconds"))
    ledger.set_meta("last_headline", _json.dumps(report.headline))
    ledger.set_meta("last_note_refs", _json.dumps([n.ref for n in records.notes]))
    history = _json.loads(ledger.meta("last_recheck") or "[]")
    history = (history + [report_dict(report)])[-2:]
    ledger.set_meta("last_recheck", _json.dumps(history, default=str))
    return report


def _bd(b: Belief) -> dict:
    return {"id": b.id, "kind": b.kind, "subject": b.subject, "statement": b.statement, "value": b.value,
            "source": b.source, "recorded_at": b.recorded_at, "status": b.status, "revision": b.revision}


def report_dict(r: RecheckReport) -> dict:
    """JSON view of a recheck, stored under meta.last_recheck (last two) for the findings diff."""
    return {"revision": r.revision, "previous_revision": r.previous_revision, "first_run": r.first_run,
            "run_wall": _now_wall(), "previous_run_wall": r.previous_run_wall,
            "headline": r.headline, "previous_headline": r.previous_headline,
            "new": [_bd(b) for b in r.observations_new],
            "changed": [{"old": _bd(o), "new": _bd(n)} for o, n in r.observations_changed],
            "cleared": [{"belief": _bd(b), "reason": why} for b, why in r.observations_cleared],
            "unchanged": r.observations_unchanged,
            "assertions_superseded": [{"old": _bd(o), "new": _bd(n)} for o, n in r.assertions_superseded],
            "assertions_conflicted": [{"old": _bd(o), "new": _bd(n)} for o, n in r.assertions_conflicted],
            "retained": r.retained, "new_notes": [n.ref for n in r.new_notes]}


def _now_wall() -> str:
    from datetime import datetime as _dt
    return _dt.now().astimezone().isoformat(timespec="seconds")


def _cleared_reason(subject: str, records: Records) -> str:
    m = re.fullmatch(r"phantom_receipt:([A-Z0-9-]+)", subject)
    if m:
        h = records.header(m.group(1))
        if h:
            return f"{h.invoice_id} is now '{h.status}' with delivery date {h.delivery_date}"
    return "the condition no longer holds in the current records"
