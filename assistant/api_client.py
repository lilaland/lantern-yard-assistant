"""HTTP client for the supplied local operational API (see API.md).

Only the scoped agent token is used. Every read and every draft is pinned to
SITE-01 at this boundary; there is no parameter that selects another site.
The client has no method for approval or scenario control. Those endpoints
are operator-only and the assistant must never hold that credential.
"""
from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import SITE_ID, Config

RESOURCES = (
    "sites", "invoice_headers", "invoice_lines", "pos_daily", "inventory_counts",
    "vendors", "vendor_catalog", "menu", "recipes", "ingredient_mappings",
    "notes", "supplier_documents",
)
DATE_FILTERED = frozenset({"pos_daily", "invoice_headers", "inventory_counts", "notes"})
MAX_LIMIT = 100


class ApiError(Exception):
    """An HTTP error with the JSON error code the fixture returned."""

    def __init__(self, status: int, code: str, method: str, path: str):
        super().__init__(f"{method} {path} -> {status} {code}")
        self.status, self.code, self.method, self.path = status, code, method, path


class BadRequest(ApiError):
    pass


class Unauthorized(ApiError):
    pass


class Forbidden(ApiError):
    pass


class NotFound(ApiError):
    pass


class Conflict(ApiError):
    pass


class ServerUnavailable(Exception):
    """No connection could be made. Nothing was processed."""


class ConnectionLost(Exception):
    """The connection dropped after the request was sent.

    The server may or may not have processed it. Callers must verify by a
    read, never by resending with a new identity.
    """

    def __init__(self, method: str, path: str, cause: BaseException):
        super().__init__(f"{method} {path}: connection lost ({type(cause).__name__}); result unknown")
        self.method, self.path, self.cause = method, path, cause


class RevisionChanged(Exception):
    """dataset_revision changed while paginating; the caller should restart."""


_STATUS_TO_ERROR = {400: BadRequest, 401: Unauthorized, 403: Forbidden, 404: NotFound, 409: Conflict}


@dataclass(frozen=True)
class Page:
    records: list[dict[str, str]]
    total: int
    next_offset: int | None
    source: str
    dataset_revision: int


@dataclass(frozen=True)
class Collection:
    records: list[dict[str, str]]
    total: int
    source: str
    dataset_revision: int


class ApiClient:
    def __init__(self, cfg: Config, timeout: float = 10.0):
        self.base = cfg.api_base
        self._token = cfg.agent_token
        self.timeout = timeout

    # ----- transport -----------------------------------------------------

    def _request(self, method: str, path: str, query: dict[str, Any] | None = None,
                 body: dict[str, Any] | None = None, auth: bool = True) -> Any:
        url = self.base + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        if auth:
            headers["Authorization"] = f"Bearer {self._token}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode() or "null")
        except urllib.error.HTTPError as err:
            try:
                code = json.loads(err.read().decode()).get("error", "unknown")
            except (ValueError, AttributeError):
                code = "unknown"
            raise _STATUS_TO_ERROR.get(err.code, ApiError)(err.code, code, method, path) from None
        except urllib.error.URLError as err:
            if isinstance(err.reason, ConnectionRefusedError):
                raise ServerUnavailable(f"cannot connect to {self.base}") from err
            raise ConnectionLost(method, path, err) from err
        except (http.client.HTTPException, ConnectionError, TimeoutError) as err:
            # RemoteDisconnected, IncompleteRead, reset, broken pipe, timeout:
            # the request left this process; the outcome is unknown.
            raise ConnectionLost(method, path, err) from err

    # ----- reads ---------------------------------------------------------

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health", auth=False)

    def get_page(self, resource: str, limit: int = MAX_LIMIT, offset: int = 0,
                 **filters: Any) -> Page:
        if resource not in RESOURCES:
            raise ValueError(f"unknown resource {resource!r}")
        site = filters.pop("site_id", SITE_ID)
        if site != SITE_ID:
            raise ValueError(f"this assistant is scoped to {SITE_ID}; refused site_id={site!r}")
        if not 1 <= limit <= MAX_LIMIT:
            raise ValueError(f"limit must be 1..{MAX_LIMIT}")
        if resource not in DATE_FILTERED and ({"date_from", "date_to"} & set(filters)):
            raise ValueError(f"{resource} does not accept date filters")
        query = {"site_id": SITE_ID, "limit": limit, "offset": offset, **filters}
        raw = self._request("GET", f"/api/{resource}", query)
        return Page(
            records=raw["records"], total=raw["total"], next_offset=raw["next_offset"],
            source=raw["source"], dataset_revision=int(raw["dataset_revision"]),
        )

    def get_all(self, resource: str, page_size: int = MAX_LIMIT, **filters: Any) -> Collection:
        """Fetch every page. Raises RevisionChanged if the dataset moved mid-way."""
        records: list[dict[str, str]] = []
        offset = 0
        first: Page | None = None
        while True:
            page = self.get_page(resource, limit=page_size, offset=offset, **filters)
            if first is None:
                first = page
            elif page.dataset_revision != first.dataset_revision:
                raise RevisionChanged(f"{resource}: revision {first.dataset_revision} -> {page.dataset_revision}")
            records.extend(page.records)
            if page.next_offset is None:
                break
            offset = page.next_offset
        assert first is not None
        return Collection(records, first.total, first.source, first.dataset_revision)

    # ----- draft / submit / verify (approval is not here on purpose) ------

    def create_draft(self, vendor_id: str, lines: list[dict[str, Any]]) -> dict[str, Any]:
        order = {"site_id": SITE_ID, "vendor_id": vendor_id, "lines": lines}
        return self._request("POST", "/api/drafts", body=order)

    def get_draft(self, draft_id: str) -> dict[str, Any]:
        return self._request("GET", "/api/drafts/" + urllib.parse.quote(draft_id, safe=""))

    def submit_order(self, draft_id: str) -> dict[str, Any]:
        """Submit an approved draft. May raise ConnectionLost: then verify with find_orders."""
        return self._request("POST", "/api/orders", body={"draft_id": draft_id})

    def find_orders(self, client_reference: str | None = None) -> list[dict[str, Any]]:
        query: dict[str, Any] = {"site_id": SITE_ID}
        if client_reference:
            query["client_reference"] = client_reference
        return self._request("GET", "/api/orders", query)["orders"]
