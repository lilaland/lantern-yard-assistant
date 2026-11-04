"""Onboarding question plan: at most six questions, each derived from a finding class present in the
current records, each with options whose ledger effect is fixed in code, a default, and an
"unknown, keep checking" answer.

The model may reword a question (see prompts/onboard.md); it never chooses the options, the
defaults, or what gets recorded. Answers are recorded under ``human:onboarding``. An "unknown"
answer records the default policy but leaves facts at evidence level, so the brief keeps
rechecking them instead of treating them as settled.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from . import analysis as an
from . import policies
from .detectors import Finding
from .ledger import Ledger, Outcome
from .records import Records

SOURCE = "human:onboarding"
UNKNOWN = "unknown"
ANSWERS_KEY = "onboarding.answers"

Belief = tuple  # (kind, subject, statement, value, source)


@dataclass(frozen=True)
class Option:
    key: str
    label: str
    beliefs: tuple = ()      # Belief tuples recorded when chosen
    consequence: str = ""    # one line shown after choosing


@dataclass
class Question:
    id: str
    title: str
    why: str
    evidence: list[str]
    options: list[Option]
    default: str
    custom: dict | None = None   # {"prompt": str, "cast": callable, "build": callable(value) -> tuple[Belief,...]}

    def option(self, key: str) -> Option | None:
        return next((o for o in self.options if o.key == key), None)


def _defaults(*subjects: str) -> dict[str, tuple]:
    return {s: (k, s, st, v) for k, s, st, v in policies.DEFAULTS if s in subjects}


def _b(kind: str, subject: str, statement: str, value: dict, source: str = SOURCE) -> Belief:
    return (kind, subject, statement, value, source)


def _default_belief(subject: str, source: str = SOURCE) -> Belief:
    k, s, st, v = _defaults(subject)[subject]
    return (k, s, st, v, source)


# ----- question builders ---------------------------------------------------------------

def q_inbound(records: Records, findings: list[Finding]) -> Question | None:
    f = next((x for x in findings if x.cls == "status_inconsistent_with_evidence"), None)
    pending = an.not_on_hand_invoices(records)
    if f is None and not pending:
        return None
    inv = f.id.split(":")[1] if f else pending[0].invoice_id
    h = records.header(inv)
    n = f.numbers if f else {}
    why = (f"{inv} ({h.vendor_name}, ${h.invoice_total}) is marked '{h.status}' with delivery date {h.delivery_date}; "
           f"today is {records.now.date()}.")
    if n:
        stock = ", ".join(f"{q} of {ing}" for ing, q in (n.get("stock_on_invoice") or {}).items())
        why += (f" If its {stock} counts as on hand, the close is {abs(n['gap_if_received']):g} "
                f"{n['unit']} below expected; if not, {abs(n['gap_if_not_received']):g} {n['unit']}. The counted stock "
                f"covers about {n['runway_days']} days.")
    evidence_note = next((b for b in policies.evidence_assertions(records)
                          if b[0] == f"invoice.{inv}.physically_received"), None)
    unknown_beliefs = (_default_belief("inbound.on_hand_rule"),)
    if evidence_note:
        subject, statement, value, src, recheck = evidence_note
        unknown_beliefs += (("assertion", subject, statement, value, src),)
    return Question(
        "inbound", f"{inv} is marked received but its delivery date is after today", why,
        [str(c) for c in (f.evidence[:4] if f else [h.cite])],
        [Option("not_seen", "Nobody has seen the cases. Count inbound stock only once someone confirms it arrived.",
                (_default_belief("inbound.on_hand_rule"),
                 _b("assertion", f"invoice.{inv}.physically_received", f"{inv} has not been physically received.",
                    {"received": False})),
                f"{inv} stays out of on-hand stock until you say otherwise; the brief keeps flagging it."),
         Option("arrived", "The cases did arrive; the office delivery date is what is wrong.",
                (_default_belief("inbound.on_hand_rule"),
                 _b("assertion", f"invoice.{inv}.physically_received", f"{inv} was physically received.", {"received": True})),
                f"{inv} counts as on hand; the brief drops the receipt alarm to low and asks for the date to be fixed."),
         Option("office", "Trust the office receipt status as it stands.",
                (_b("policy", "inbound.on_hand_rule", "The office receipt status decides what is on hand.",
                    {"rule": "office_status"}),),
                "Marked-received goods count as on hand even with a future delivery date; variances will show the difference."),
         Option(UNKNOWN, "Unknown, keep checking.", unknown_beliefs,
                "Default rule recorded (physical confirmation); the receipt fact stays at evidence level and is rechecked every run.")],
        "not_seen")


def q_route(records: Records, findings: list[Finding]) -> Question | None:
    f = next((x for x in findings if x.cls == "reference_table_stale"), None)
    if f is None:
        return None
    vendor_id = f.id.split(":")[1]
    vendor = next(v for v in records.vendors if v.vendor_id == vendor_id)
    ev = next((b for b in policies.evidence_assertions(records) if b[0] == f"vendor.{vendor_id}.delivery_days"), None)
    days = ev[2]["days"] if ev else list(f.numbers.get("observed_days_since_review", []))
    cutoff = ev[2].get("cutoff") if ev else None
    human_days = _b("assertion", f"vendor.{vendor_id}.delivery_days",
                    f"{vendor.vendor_name} delivers {', '.join(days)}" + (f"; orders by {cutoff}" if cutoff else "") + ".",
                    {"days": days, "cutoff": cutoff})
    unknown = (_default_belief("sources.precedence"),)
    if ev:
        unknown += (("assertion", ev[0], ev[1], ev[2], ev[3]),)  # evidence-sourced: stays rechecked
    return Question(
        "route", f"{vendor.vendor_name} delivery days: the vendor sheet disagrees with newer records", f.summary,
        [str(c) for c in f.evidence[:5]],
        [Option("newer", f"Plan on {', '.join(days)} as the notes and invoices show; newer notes and invoices beat reference tables.",
                (_default_belief("sources.precedence"), human_days),
                f"Runway and order sizing use {'/'.join(days)}; the vendor sheet is still reported as stale."),
         Option("sheet", f"Keep the vendor sheet ({', '.join(vendor.delivery_days)}) until the vendor confirms in writing.",
                (_b("policy", "sources.precedence", "Reference tables win until the vendor confirms a change in writing.",
                    {"rule": "reference_tables"}),),
                f"Planning uses {', '.join(vendor.delivery_days)}; the notes are still cited as a possible change."),
         Option(UNKNOWN, "Unknown, keep checking.", unknown,
                "Default precedence recorded; the route stays an evidence-level fact tied to the note, not your word.")],
        "newer")


def q_sku(records: Records, findings: list[Finding]) -> Question | None:
    f = next((x for x in findings if x.cls == "sku_substitution"), None)
    if f is None:
        return None
    ing_id = f.id.split(":")[1]
    old, new = f.numbers["old_sku"], f.numbers["new_sku"]
    m = next(m for m in records.mappings if m.sku == new)
    unit = records.catalog_item(m.vendor_id, new).pack_unit
    name = m.ingredient_name
    count_unit = _b("policy", f"ingredient.{ing_id}.count_unit", f"Count and order {name} in {unit}, never in cases.", {"unit": unit})
    return Question(
        "sku", f"{name} now ships as {new} ({f.numbers['new_pack']:g} {unit}) instead of {old} ({f.numbers['old_pack']:g} {unit})",
        f.summary, [str(c) for c in f.evidence[:5]],
        [Option("new_sku", f"Order {new} from now on and count {name.split(',')[0].lower()} in {unit}.",
                (_b("preference", f"order.sku.{ing_id}", f"Order {name} as {new}.", {"vendor_id": m.vendor_id, "sku": new}), count_unit),
                f"Order proposals use {new}; quantities are always shown in {unit}."),
         Option("old_sku", f"Keep ordering {old} if the vendor still has it; count in {unit}.",
                (_b("preference", f"order.sku.{ing_id}", f"Order {name} as {old} while available.", {"vendor_id": m.vendor_id, "sku": old}), count_unit),
                f"Order proposals use {old}; the catalog still lists it."),
         Option(UNKNOWN, "Unknown, keep checking.",
                (_b("preference", f"order.sku.{ing_id}", f"Order {name} as {new} (default until decided).", {"vendor_id": m.vendor_id, "sku": new}), count_unit),
                f"Default {new} recorded for now; change it any time with `ask`.")],
        "new_sku")


def q_missing_recipe(records: Records, findings: list[Finding]) -> Question | None:
    fs = [x for x in findings if x.cls == "item_without_recipe"]
    if not fs:
        return None
    f = fs[0]
    item = f.id.split(":")[1]
    name = next((m.name for m in records.menu if m.pos_item_id == item), item)
    return Question(
        "missing_recipe", f"{name} ({item}) has no recipe card", f.summary, [str(c) for c in f.evidence[:4]],
        [Option("flag", "Treat its ingredient use as unknown and keep flagging it until the card is written.",
                (_default_belief("recipes.missing_item_rule"),),
                "Reconciliations say 'unknown draw' rather than guessing; the finding stays in the brief."),
         Option("ignore", "Stop listing it in the brief; I know about it.",
                (_b("policy", "recipes.missing_item_rule", "Items without a recipe are not listed in the brief; their use stays unknown.",
                    {"rule": "ignore"}),),
                "The finding is suppressed; variances still carry the 'unknown draw' caveat."),
         Option(UNKNOWN, "Unknown, keep checking.", (_default_belief("recipes.missing_item_rule"),),
                "Default recorded; the item keeps being flagged.")],
        "flag")


def q_price(records: Records, findings: list[Finding]) -> Question | None:
    outliers = [x for x in findings if x.cls == "unit_price_outlier"]
    mism = [x for x in findings if x.cls == "header_total_mismatch"]
    if not outliers and not mism:
        return None
    f = outliers[0] if outliers else mism[0]
    why = f.summary
    if mism:
        why += (f" Separately, {mism[0].id.split(':')[1]} has a header total that differs from its lines; arithmetic "
                f"mismatches are always reported and have no threshold.")

    def build(ratio: float) -> tuple:
        return (_b("preference", "invoices.price_anomaly_ratio",
                   f"Flag an invoice line at {ratio:g}x or more of the usual unit cost for that SKU.", {"ratio": float(ratio)}),)

    return Question(
        "price", "Invoice price anomalies: how big a jump should the brief flag?", why, [str(c) for c in f.evidence[:3]],
        [Option("2x", "Flag at 2x the usual unit cost or more.", build(2.0), "INV-010 at 10x stays flagged."),
         Option("5x", "Flag at 5x or more.", build(5.0), "INV-010 at 10x stays flagged; smaller jumps are not listed."),
         Option("10x", "Flag at 10x or more.", build(10.0), "INV-010 at exactly 10x stays flagged."),
         Option(UNKNOWN, "Unknown, keep checking.", build(2.0), "Default 2x recorded.")],
        "2x", custom={"prompt": "Or type a number, e.g. 3 for 3x", "cast": float, "build": build})


ORDERING_FIELDS = (
    ("buffer_days", "order.buffer_days", "days", int, "Buffer days on top of the coverage window"),
    ("cover_through", "order.cover_through", "weekday", str, "Weekday a midweek delivery must cover through"),
    ("uplift", "order.brunch_uplift", "uplift", float, "Weekend breakfast uplift as a fraction (0.25 = 25%)"),
    ("max_unflagged_usd", "order.max_unflagged_usd", "usd", float, "Order value above which the proposal is flagged, USD"),
)


def ordering_beliefs(values: dict[str, Any]) -> tuple:
    out = []
    for key, subject, vkey, cast, _ in ORDERING_FIELDS:
        k, s, st, default = _defaults(subject)[subject]
        v = cast(values.get(key, default[vkey]))
        if key == "cover_through" and v not in an.WEEKDAYS:
            raise ValueError(f"cover_through must be a weekday name, got {v!r}")
        if key == "buffer_days" and not 0 <= v <= 7 or key == "uplift" and not 0 <= v <= 2 or key == "max_unflagged_usd" and v <= 0:
            raise ValueError(f"{key}={v!r} is out of range")
        out.append((k, s, st if v == default[vkey] else f"{st} (set during onboarding: {v})", {vkey: v}, SOURCE))
    return tuple(out)


def q_ordering(records: Records, findings: list[Finding]) -> Question:
    wk = next((x for x in findings if x.cls == "weekend_readiness"), None)
    rw = [x for x in findings if x.cls == "runway_risk"]
    why = "Order proposals need four numbers from you."
    if rw:
        why += " Tight stock right now: " + "; ".join(f"{x.title}" for x in rw[:3]) + "."
    if wk:
        why += f" {wk.title}: {wk.summary.split('. ', 1)[0]}."
    defaults = {k: _defaults(s)[s][3][vk] for k, s, vk, _, _ in ORDERING_FIELDS}
    return Question(
        "ordering", "Ordering preferences", why, [str(c) for c in (wk.evidence[:2] if wk else [])] + [str(c) for x in rw[:2] for c in x.evidence[:1]],
        [Option("defaults", f"Use the defaults: buffer {defaults['buffer_days']} day, cover through {defaults['cover_through']}, "
                            f"brunch uplift {defaults['uplift']:.0%}, flag orders over ${defaults['max_unflagged_usd']:,.0f}.",
                ordering_beliefs({}), "Order proposals apply these and name them."),
         Option("custom", "Set each one.", (), "You will be asked for the four values."),
         Option(UNKNOWN, "Unknown, keep checking.", ordering_beliefs({}), "Defaults recorded; change any of them with `ask`.")],
        "defaults")


BUILDERS = (q_inbound, q_route, q_sku, q_missing_recipe, q_price, q_ordering)
MAX_QUESTIONS = 6


# ----- plan and apply -----------------------------------------------------------------------

def answers(ledger: Ledger) -> dict[str, Any]:
    raw = ledger.meta(ANSWERS_KEY)
    return json.loads(raw) if raw else {}


def question_plan(records: Records, findings: list[Finding], ledger: Ledger, again: bool = False) -> list[Question]:
    """Questions for classes present now, skipping ones already answered unless ``again``."""
    done = {} if again else answers(ledger)
    plan = []
    for build in BUILDERS:
        q = build(records, findings)
        if q is not None and q.id not in done:
            plan.append(q)
    return plan[:MAX_QUESTIONS]


def apply_answer(ledger: Ledger, q: Question, answer: Any, revision: int | None = None) -> tuple[Option, list[Outcome]]:
    """Record the beliefs for an answer. ``answer`` is an option key, a number (custom), or a dict (ordering)."""
    if q.id == "ordering" and isinstance(answer, dict):
        opt = Option("custom", "Set each one.", ordering_beliefs(answer), "Order proposals apply these and name them.")
    elif q.custom and not isinstance(answer, str):
        value = q.custom["cast"](answer)
        opt = Option("custom", f"{value:g}x", q.custom["build"](value), f"Lines at {value:g}x or more are flagged.")
    else:
        opt = q.option(str(answer))
        if opt is None:
            raise ValueError(f"{q.id}: unknown answer {answer!r}; options: {[o.key for o in q.options]}")
        if opt.key == "custom":
            raise ValueError(f"{q.id}: 'custom' needs values")
    outcomes = [ledger.record(kind, subject, statement, value, source, revision=revision)
                for kind, subject, statement, value, source in opt.beliefs]
    done = answers(ledger)
    done[q.id] = opt.key if not isinstance(answer, dict) else answer
    ledger.set_meta(ANSWERS_KEY, json.dumps(done, sort_keys=True))
    return opt, outcomes


def parse_answers(text: str) -> dict[str, Any]:
    """``--answers`` accepts JSON or ``id=key,id=key``; ordering may be a JSON object."""
    text = text.strip()
    if text.startswith("{"):
        return json.loads(text)
    out: dict[str, Any] = {}
    for part in filter(None, (p.strip() for p in text.split(","))):
        k, _, v = part.partition("=")
        out[k.strip()] = v.strip()
    return out
