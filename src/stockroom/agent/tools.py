"""Stockroom tools: specifications (what the model sees) and implementations (what runs).

The five tools operate on the JSON data under ``data/`` and an in-memory restock ledger. Two of
the seeded weaknesses live here as feature flags:

* ``ambiguous_tool_desc`` — ``search_products`` claims to cover stock levels and orders too.
* ``oversized_payload``   — ``search_products`` returns every field of every match with no limit.
* ``naive_retry``         — ``get_order_status`` has no internal retry for the flaky legacy shard.

Everything is deterministic: the legacy shard "fails" on the first attempt of each run and
succeeds on the second (``LegacyShardSimulator``), so tests can reason about exact trajectories.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from stockroom.agent.retrieval import BM25Index, content_tokens, default_index
from stockroom.agent.types import ToolResult, ToolSpec
from stockroom.config import (
    WEAKNESS_AMBIGUOUS_TOOL_DESC,
    WEAKNESS_NAIVE_RETRY,
    WEAKNESS_OVERSIZED_PAYLOAD,
    StockroomConfig,
)

SKU_PATTERN = r"^SKU-\d{4}$"
ORDER_PATTERN = r"^ORD-\d{4}$"
TOOL_NAMES: tuple[str, ...] = (
    "search_products",
    "get_stock_level",
    "get_order_status",
    "create_restock_request",
    "search_policy_docs",
)
COMPACT_FIELDS = ("sku", "name", "category", "unit_price_usd", "unit", "stock_level")
DEFAULT_MAX_RESULTS = 5
POLICY_TOP_K = 3


class ToolTransientError(RuntimeError):
    """A retryable backend failure (legacy order shard timing out)."""


class ToolInputError(ValueError):
    """The arguments were well-formed but violate a business rule."""


# --- specifications ---------------------------------------------------------------------------

SEARCH_PRODUCTS_DESC_FIXED = (
    "Search the product catalogue by product name, category or keyword and return matching "
    "products (SKU, name, price, availability). Not for exact quantities or orders."
)
SEARCH_PRODUCTS_DESC_AMBIGUOUS = (
    "Look up products, inventory, stock level, units in stock for a SKU, orders, order status "
    "and tracking by order id, and anything else about the warehouse."
)


def build_tool_specs(config: StockroomConfig) -> list[ToolSpec]:
    """Tool specs for the model. Descriptions depend on the ``ambiguous_tool_desc`` flag."""
    ambiguous = config.has_weakness(WEAKNESS_AMBIGUOUS_TOOL_DESC)
    search_desc = SEARCH_PRODUCTS_DESC_AMBIGUOUS if ambiguous else SEARCH_PRODUCTS_DESC_FIXED
    search_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1, "description": "Name, category or keyword"},
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    if not config.has_weakness(WEAKNESS_OVERSIZED_PAYLOAD):
        search_schema["properties"]["max_results"] = {
            "type": "integer",
            "minimum": 1,
            "maximum": 20,
            "description": f"Maximum products to return (default {DEFAULT_MAX_RESULTS})",
        }
    return [
        ToolSpec(name="search_products", description=search_desc, input_schema=search_schema),
        ToolSpec(
            name="get_stock_level",
            description=(
                "Get the stock level (units in stock), reorder point and warehouse location "
                "(aisle, bin) for one product by SKU. Use for 'how many', 'how much', 'left', "
                "'check', 'where is it stored' questions about a SKU."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "sku": {"type": "string", "pattern": SKU_PATTERN, "description": "SKU-####"}
                },
                "required": ["sku"],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="get_order_status",
            description=(
                "Get the status, items, carrier, tracking number and delivery dates of one "
                "customer order by order id (ORD-####)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "pattern": ORDER_PATTERN,
                        "description": "ORD-####",
                    }
                },
                "required": ["order_id"],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="create_restock_request",
            description=(
                "Create a restock request (purchase order to the supplier) for a SKU. Quantity "
                "must be an integer number of units; priority is 'standard' or 'urgent'. Only "
                "call this when the user explicitly asks to restock, reorder or raise a request."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "sku": {"type": "string", "pattern": SKU_PATTERN},
                    "quantity": {"type": "integer", "minimum": 1},
                    "priority": {"type": "string", "enum": ["standard", "urgent"]},
                },
                "required": ["sku", "quantity"],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="search_policy_docs",
            description=(
                "Search the Stockroom policy documents (returns, shipping, restock rules, "
                "warranty, glossary, supplier lead times) and return the most relevant sections."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 5},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
    ]


# --- data access -------------------------------------------------------------------------------


@dataclass
class StockroomData:
    catalogue: list[dict[str, Any]]
    orders: list[dict[str, Any]]
    restock_rules: dict[str, Any]
    docs_dir: Path
    by_sku: dict[str, dict[str, Any]] = field(init=False)
    by_order: dict[str, dict[str, Any]] = field(init=False)

    def __post_init__(self) -> None:
        self.by_sku = {p["sku"]: p for p in self.catalogue}
        self.by_order = {o["order_id"]: o for o in self.orders}

    @classmethod
    def load(cls, data_dir: Path) -> StockroomData:
        return cls(
            catalogue=json.loads((data_dir / "catalogue.json").read_text(encoding="utf-8")),
            orders=json.loads((data_dir / "orders.json").read_text(encoding="utf-8")),
            restock_rules=json.loads((data_dir / "restock_rules.json").read_text(encoding="utf-8")),
            docs_dir=data_dir / "policy_docs",
        )

    @property
    def index(self) -> BM25Index:
        return default_index(self.docs_dir)


class LegacyShardSimulator:
    """Deterministic flakiness for ``ORD-9xxx``: the first ``fail_first`` attempts fail."""

    def __init__(self, fail_first: int = 1) -> None:
        self.fail_first = fail_first
        self.attempts: dict[str, int] = {}

    def reset(self) -> None:
        self.attempts.clear()

    def attempt(self, order_id: str) -> None:
        n = self.attempts.get(order_id, 0) + 1
        self.attempts[order_id] = n
        if n <= self.fail_first:
            raise ToolTransientError(
                f"legacy order shard timed out looking up {order_id} (attempt {n}); retry later"
            )


@dataclass
class RestockLedger:
    """In-memory restock requests; reset per run so IDs are reproducible."""

    rules: dict[str, Any]
    requests: list[dict[str, Any]] = field(default_factory=list)

    def reset(self) -> None:
        self.requests.clear()

    def create(
        self, sku: str, product: dict[str, Any], quantity: int, priority: str
    ) -> dict[str, Any]:
        min_q = int(self.rules.get("min_quantity", 10))
        auto_max = int(self.rules.get("auto_approve_max", 500))
        reject_above = int(self.rules.get("reject_above", 5000))
        if quantity < min_q:
            raise ToolInputError(
                f"rejected: the minimum restock quantity is {min_q} units (requested {quantity})"
            )
        if quantity > reject_above:
            raise ToolInputError(
                f"rejected: quantities above {reject_above:,} units are rejected automatically; "
                "contact Inventory Planning"
            )
        if any(
            r["sku"] == sku and r["status"] in {"open", "pending_approval"} for r in self.requests
        ):
            raise ToolInputError(f"rejected: there is already an open restock request for {sku}")
        status = "open" if quantity <= auto_max else "pending_approval"
        record = {
            "request_id": f"RSR-{len(self.requests) + 1:04d}",
            "sku": sku,
            "product_name": product["name"],
            "quantity": quantity,
            "priority": priority,
            "status": status,
            "supplier_id": product["supplier_id"],
            "supplier_name": product["supplier_name"],
            "lead_time_days": product["lead_time_days"],
            "note": (
                "quantities above 500 units need manager approval"
                if status == "pending_approval"
                else "auto-approved"
            ),
        }
        self.requests.append(record)
        return record


# --- implementations ---------------------------------------------------------------------------


def compact_product(product: dict[str, Any]) -> dict[str, Any]:
    out = {k: product[k] for k in COMPACT_FIELDS}
    out["in_stock"] = product["stock_level"] > 0
    return out


def product_matches(product: dict[str, Any], query: str) -> int:
    """Number of query tokens that hit the product's name, category, tags or SKU."""
    q = content_tokens(query)
    if not q:
        return 0
    hay = " ".join(
        [product["sku"], product["name"], product["category"], " ".join(product.get("tags", []))]
    )
    hay_tokens = set(content_tokens(hay)) | {product["sku"].lower()}
    # singular/plural tolerance: 'hats' ~ 'hat', 'gloves' ~ 'glove'
    hay_tokens |= {t[:-1] for t in hay_tokens if t.endswith("s")}
    hits = 0
    for tok in q:
        candidates = {tok, tok[:-1] if tok.endswith("s") else tok}
        if candidates & hay_tokens:
            hits += 1
    return hits


class StockroomTools:
    """Tool implementations bound to a config, data set, ledger and legacy simulator."""

    def __init__(
        self,
        config: StockroomConfig,
        data: StockroomData | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.config = config
        self.data = data or StockroomData.load(config.data_dir)
        self.ledger = RestockLedger(self.data.restock_rules)
        # With the naive_retry weakness the legacy shard is "down": every attempt fails, so a
        # model that simply re-issues the call loops until a guard stops it.
        self.legacy = LegacyShardSimulator(
            fail_first=10**6 if config.has_weakness(WEAKNESS_NAIVE_RETRY) else 1
        )
        self.specs = build_tool_specs(config)
        self._clock = clock
        self._impl: dict[str, Callable[..., Any]] = {
            "search_products": self.search_products,
            "get_stock_level": self.get_stock_level,
            "get_order_status": self.get_order_status,
            "create_restock_request": self.create_restock_request,
            "search_policy_docs": self.search_policy_docs,
        }

    def reset(self) -> None:
        """Forget per-run state (restock IDs, legacy attempt counters)."""
        self.ledger.reset()
        self.legacy.reset()

    def spec(self, name: str) -> ToolSpec:
        for s in self.specs:
            if s.name == name:
                return s
        raise KeyError(name)

    # -- individual tools --------------------------------------------------------------------

    def search_products(self, query: str, max_results: int | None = None) -> list[dict[str, Any]]:
        oversized = self.config.has_weakness(WEAKNESS_OVERSIZED_PAYLOAD)
        scored = [(product_matches(p, query), p) for p in self.data.catalogue]
        hits = [
            p for score, p in sorted(scored, key=lambda sp: (-sp[0], sp[1]["sku"])) if score > 0
        ]
        if oversized:
            # Weakness: every field of every match, and a "full category export" (the whole
            # catalogue) as soon as three or more products match. No limit parameter exists.
            if not hits or len(hits) >= 3:
                hits = list(self.data.catalogue)
            return [dict(p) for p in hits]
        limit = max_results or DEFAULT_MAX_RESULTS
        return [compact_product(p) for p in hits[:limit]]

    def get_stock_level(self, sku: str) -> dict[str, Any]:
        product = self.data.by_sku.get(sku)
        if product is None:
            raise ToolInputError(f"unknown SKU {sku}")
        return {
            "sku": sku,
            "name": product["name"],
            "stock_level": product["stock_level"],
            "reorder_point": product["reorder_point"],
            "below_reorder_point": product["stock_level"] <= product["reorder_point"],
            "location": product["location"],
            "unit": product["unit"],
            "supplier_name": product["supplier_name"],
            "lead_time_days": product["lead_time_days"],
        }

    def _fetch_order(self, order_id: str) -> dict[str, Any]:
        order = self.data.by_order.get(order_id)
        if order is None:
            raise ToolInputError(f"unknown order {order_id}")
        if order["shard"] == "legacy":
            self.legacy.attempt(order_id)
        return order

    def get_order_status(self, order_id: str) -> dict[str, Any]:
        naive = self.config.has_weakness(WEAKNESS_NAIVE_RETRY)
        attempts = 1 if naive else 2
        last_error: ToolTransientError | None = None
        for _ in range(attempts):
            try:
                order = self._fetch_order(order_id)
                break
            except ToolTransientError as exc:
                last_error = exc
        else:
            assert last_error is not None
            raise last_error
        return {
            "order_id": order["order_id"],
            "customer_name": order["customer_name"],
            "status": order["status"],
            "placed_at": order["placed_at"],
            "shipped_at": order["shipped_at"],
            "delivered_at": order["delivered_at"],
            "carrier": order["carrier"],
            "tracking_number": order["tracking_number"],
            "estimated_delivery": order["estimated_delivery"],
            "items": [
                {"sku": i["sku"], "name": i["name"], "quantity": i["quantity"]}
                for i in order["items"]
            ],
            "total_usd": order["total_usd"],
        }

    def create_restock_request(
        self, sku: str, quantity: int, priority: str = "standard"
    ) -> dict[str, Any]:
        product = self.data.by_sku.get(sku)
        if product is None:
            raise ToolInputError(f"unknown SKU {sku}")
        return self.ledger.create(sku, product, int(quantity), priority)

    def search_policy_docs(self, query: str, top_k: int = POLICY_TOP_K) -> list[dict[str, Any]]:
        results = self.data.index.search(query, k=top_k)
        return [
            {
                "chunk_id": chunk.chunk_id,
                "document": chunk.doc,
                "section": chunk.section,
                "title": chunk.title,
                "score": round(score, 3),
                "text": chunk.text,
            }
            for chunk, score in results
        ]

    # -- dispatch -------------------------------------------------------------------------------

    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Run a tool and wrap the outcome; never raises for business or transient errors."""
        if name not in self._impl:
            return ToolResult.error(name, f"unknown tool {name}", code="unknown_tool")
        start = self._clock()
        try:
            content = self._impl[name](**arguments)
        except ToolInputError as exc:
            return ToolResult.error(
                name, str(exc), code="invalid_request", latency_ms=_ms(start, self._clock)
            )
        except ToolTransientError as exc:
            return ToolResult.error(
                name, str(exc), code="transient", latency_ms=_ms(start, self._clock)
            )
        except TypeError as exc:
            return ToolResult.error(
                name,
                f"bad arguments: {exc}",
                code="bad_arguments",
                latency_ms=_ms(start, self._clock),
            )
        return ToolResult.ok(name, content, latency_ms=_ms(start, self._clock))


def _ms(start: float, clock: Callable[[], float]) -> float:
    return round((clock() - start) * 1000.0, 3)


class ToolExecutor(Protocol):
    """What the harness needs from a tool backend (local or MCP)."""

    def list_specs(self) -> list[ToolSpec]: ...

    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult: ...

    def reset(self) -> None: ...


class LocalToolExecutor:
    """In-process executor used in tests, notebooks and the default CI path."""

    def __init__(self, tools: StockroomTools) -> None:
        self.tools = tools

    def list_specs(self) -> list[ToolSpec]:
        return list(self.tools.specs)

    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        return self.tools.call(name, arguments)

    def reset(self) -> None:
        self.tools.reset()


def find_skus(text: str) -> list[str]:
    return re.findall(r"SKU-\d{4}", text)


def find_order_ids(text: str) -> list[str]:
    return re.findall(r"ORD-\d{4}", text)
