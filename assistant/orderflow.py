"""The order state machine. Code decides every transition; a person approves; the model never touches it.

    proposed -> drafted/awaiting_approval -> approved -> submitting -> accepted
                                   |                        |-> unknown (response lost) -> accepted | unknown
                                   |-> cancelled            |-> awaiting_approval (409 approval_required)
                                                            |-> failed (any other error)

Rules this module enforces:
- A draft is created only from a Proposal the operator saw in full.
- Submission goes only through POST /api/orders {draft_id}; the server enforces approval too.
- A lost response never leads to a new draft: verify by client_reference, retry the same draft once, else stay unknown.
- While any draft for a vendor is submitting or unknown, no new draft is proposed for that vendor.
"""
from __future__ import annotations

from dataclasses import dataclass

from .api_client import ApiClient, ApiError, Conflict, ConnectionLost, ServerUnavailable
from .ledger import Draft, Ledger
from .ordering import Proposal

APPROVE_COMMAND = "python3 simulator.py approve --draft-id {draft_id}"


@dataclass
class Outcome:
    state: str            # accepted | unknown | awaiting_approval | failed | cancelled
    message: str
    order_id: str | None = None
    verified: bool = False
    draft: Draft | None = None


class OrderFlow:
    def __init__(self, client: ApiClient, ledger: Ledger):
        self.client = client
        self.ledger = ledger

    # ----- queries -------------------------------------------------------------

    def blocked_vendors(self) -> dict[str, Draft]:
        return {d.vendor_id: d for d in self.ledger.drafts(("submitting", "unknown"))}

    def open_drafts(self) -> list[Draft]:
        return self.ledger.drafts(("drafted", "awaiting_approval", "approved"))

    def approval_status(self, draft_id: str) -> str:
        """The server's view: awaiting_approval or approved. Mirrors it into the ledger."""
        status = self.client.get_draft(draft_id)["status"]
        d = self.ledger.draft(draft_id)
        if d and d.status in ("drafted", "awaiting_approval", "approved") and d.status != status:
            self.ledger.update_draft(draft_id, status)
        return status

    # ----- transitions -----------------------------------------------------------

    def create_draft(self, proposal: Proposal) -> Draft:
        blocked = self.blocked_vendors()
        if proposal.vendor_id in blocked:
            b = blocked[proposal.vendor_id]
            raise RuntimeError(f"draft {b.draft_id} for {proposal.vendor_id} is {b.status}; resolve it before drafting again")
        resp = self.client.create_draft(proposal.vendor_id, proposal.api_lines)
        if resp.get("order") != proposal.order:
            # The server echoes the exact order; anything else means we would be approving something we did not propose.
            self.ledger.save_draft(resp["draft_id"], proposal.vendor_id, resp.get("order", {}), "failed")
            raise RuntimeError(f"server draft differs from the proposal: {resp.get('order')} vs {proposal.order}")
        return self.ledger.save_draft(resp["draft_id"], proposal.vendor_id, resp["order"], resp.get("status", "awaiting_approval"))

    def cancel(self, draft_id: str) -> Draft:
        d = self.ledger.draft(draft_id)
        if d is None:
            raise RuntimeError(f"no draft {draft_id} in the ledger")
        if d.status in ("accepted", "submitting", "unknown"):
            raise RuntimeError(f"draft {draft_id} is {d.status}; it cannot be cancelled from here")
        return self.ledger.update_draft(draft_id, "cancelled", note="cancelled by the operator before submission; never submitted")

    def submit(self, draft_id: str) -> Outcome:
        d = self.ledger.draft(draft_id)
        if d is None:
            return Outcome("failed", f"no draft {draft_id} in the ledger")
        if d.status == "accepted":
            return Outcome("accepted", f"{draft_id} was already accepted as {d.order_id}.", d.order_id, True, d)
        if d.status == "cancelled":
            return Outcome("cancelled", f"{draft_id} was cancelled; nothing submitted.", draft=d)
        self.ledger.update_draft(draft_id, "submitting", note="submitting to the supplier")
        try:
            resp = self.client.submit_order(draft_id)
        except Conflict as e:
            if e.code == "approval_required":
                d = self.ledger.update_draft(draft_id, "awaiting_approval", last_error=e.code)
                return Outcome("awaiting_approval", f"The supplier reports {draft_id} is not approved; nothing was "
                               f"submitted. Approve it with: {APPROVE_COMMAND.format(draft_id=draft_id)}", draft=d)
            d = self.ledger.update_draft(draft_id, "failed", last_error=e.code)
            return Outcome("failed", f"The supplier refused {draft_id}: {e.code}.", draft=d)
        except ApiError as e:
            d = self.ledger.update_draft(draft_id, "failed", last_error=e.code)
            return Outcome("failed", f"Submission of {draft_id} failed: {e.status} {e.code}. Nothing to retry blindly; "
                           f"check the draft.", draft=d)
        except ServerUnavailable as e:
            d = self.ledger.update_draft(draft_id, "approved", last_error=str(e))
            return Outcome("failed", f"The supplier API is unreachable ({e}); the request was never sent, so the draft "
                           f"stays approved. Run `order --resume` when it is back.", draft=d)
        except ConnectionLost as e:
            self.ledger.update_draft(draft_id, "unknown", last_error=str(e))
            return self._recover(draft_id, str(e))
        return self._accepted(draft_id, resp["order_id"], "The supplier accepted the order.")

    def _accepted(self, draft_id: str, order_id: str, lead: str) -> Outcome:
        try:
            found = [o for o in self.client.find_orders(draft_id) if o.get("order_id") == order_id]
        except (ConnectionLost, ServerUnavailable):
            found = []
        if found:
            msg = (f"{lead} Verified by lookup: order {order_id} for reference {draft_id}, status accepted. Accepted means "
                   f"the supplier recorded it, not that it has shipped.")
        else:
            msg = f"{lead} Order {order_id}; the verification lookup did not return it yet."
        d = self.ledger.update_draft(draft_id, "accepted", order_id=order_id, note=msg)
        return Outcome("accepted", msg, order_id, bool(found), d)

    def _recover(self, draft_id: str, cause: str) -> Outcome:
        """The response to a submission was lost. Look up by reference; retry the same draft once; else stay unknown."""
        try:
            orders = self.client.find_orders(draft_id)
        except (ConnectionLost, ServerUnavailable) as e:
            d = self.ledger.update_draft(draft_id, "unknown", last_error=f"{cause}; lookup failed: {e}")
            return Outcome("unknown", f"The connection dropped while submitting {draft_id} and the lookup also failed. "
                           f"Its result is unknown; it is recorded as such and rechecked on the next start. No new draft "
                           f"will be made for this vendor until then.", draft=d)
        if orders:
            return self._accepted(draft_id, orders[0]["order_id"], "The response to our submission was lost, but the "
                                  "supplier had recorded the order.")
        try:
            resp = self.client.submit_order(draft_id)  # idempotent for the same draft_id
        except ConnectionLost as e:
            try:
                orders = self.client.find_orders(draft_id)
            except (ConnectionLost, ServerUnavailable):
                orders = []
            if orders:
                return self._accepted(draft_id, orders[0]["order_id"], "The connection dropped twice, but the supplier "
                                      "had recorded the order.")
            d = self.ledger.update_draft(draft_id, "unknown", last_error=f"{cause}; retry: {e}")
            return Outcome("unknown", f"The connection dropped twice while submitting {draft_id} and no order with that "
                           f"reference is visible. Its result is unknown and recorded as such; it is rechecked on the "
                           f"next start.", draft=d)
        except Conflict as e:
            d = self.ledger.update_draft(draft_id, "awaiting_approval" if e.code == "approval_required" else "failed",
                                         last_error=e.code)
            return Outcome(d.status, f"Retry of {draft_id} was refused: {e.code}.", draft=d)
        except ApiError as e:
            d = self.ledger.update_draft(draft_id, "failed", last_error=e.code)
            return Outcome("failed", f"Retry of {draft_id} failed: {e.status} {e.code}.", draft=d)
        return self._accepted(draft_id, resp["order_id"], "The first response was lost; the retry of the same draft was accepted.")

    # ----- restart ---------------------------------------------------------------

    def reconcile(self, submit_approved: bool = False) -> list[Outcome]:
        """On start: settle unknown/submitting drafts by lookup; refresh approval of open drafts;
        optionally submit the ones a person has approved since."""
        out: list[Outcome] = []
        for d in self.ledger.drafts(("submitting", "unknown")):
            try:
                orders = self.client.find_orders(d.draft_id)
            except (ConnectionLost, ServerUnavailable) as e:
                out.append(Outcome("unknown", f"{d.draft_id} is still unknown; the lookup failed: {e}", draft=d))
                continue
            if orders:
                out.append(self._accepted(d.draft_id, orders[0]["order_id"], f"{d.draft_id} was recorded by the supplier "
                                          f"after all (the earlier response was lost)."))
            else:
                nd = self.ledger.update_draft(d.draft_id, "unknown", last_error="no order with this reference on recheck")
                out.append(Outcome("unknown", f"{d.draft_id}: no order with this reference exists at the supplier. It was "
                                   f"submitting when the connection dropped. Decide whether to resubmit it (same draft) "
                                   f"or cancel it; nothing is done automatically.", draft=nd))
        for d in self.open_drafts():
            try:
                status = self.approval_status(d.draft_id)
            except ApiError as e:
                out.append(Outcome("failed", f"{d.draft_id}: {e.status} {e.code} when checking approval.", draft=d))
                continue
            if status == "approved" and submit_approved:
                out.append(self.submit(d.draft_id))
            else:
                out.append(Outcome(status, f"{d.draft_id} is {status.replace('_', ' ')}" +
                                   ("" if status == "approved" else f"; approve with: {APPROVE_COMMAND.format(draft_id=d.draft_id)}"),
                                   draft=self.ledger.draft(d.draft_id)))
        return out
