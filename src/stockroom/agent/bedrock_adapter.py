"""Model clients: the live Bedrock Converse adapter and the deterministic fake.

Both implement :class:`ModelClient` and return a normalised :class:`ModelResponse`, so the harness
has exactly one code path. The fake has two layers:

1. **Scripted turns** keyed by golden case id (``data/golden/mock_scripts/<id>.json``) for
   reproducible trajectories, including deliberately malformed tool calls.
2. A **heuristic planner** that simulates how a tool-using model behaves, including the failure
   classes the workshop studies: it selects tools *only* from their descriptions (so an ambiguous
   description really changes selection), it naively re-issues a call after a transient error, and
   it follows instruction-like text found in tool output unless the harness quarantined it.

The planner is a simulator of failure *classes*, not a language model; see docs/DECISIONS.md.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from stockroom.agent.retrieval import content_tokens, tokenize
from stockroom.agent.tools import StockroomData, find_order_ids, find_skus
from stockroom.agent.types import ModelResponse, ToolCallRequest, ToolSpec
from stockroom.config import (
    WEAKNESS_INJECTION_UNGUARDED,
    WEAKNESS_NAIVE_RETRY,
    Mode,
    StockroomConfig,
)

CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Deterministic token estimate used by the fake client and the budget guard."""
    return max(1, len(text) // CHARS_PER_TOKEN)


class ModelError(RuntimeError):
    """A model call failed. ``kind`` is one of throttled | validation | unavailable | other."""

    def __init__(self, message: str, kind: str = "other") -> None:
        super().__init__(message)
        self.kind = kind


class ModelClient(Protocol):
    model_id: str

    def converse(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        max_tokens: int,
    ) -> ModelResponse: ...


# --- live ---------------------------------------------------------------------------------------


def make_bedrock_runtime_client(config: StockroomConfig) -> Any:
    """A boto3 ``bedrock-runtime`` client with adaptive retries (live mode only)."""
    import boto3
    from botocore.config import Config as BotoConfig

    return boto3.client(
        "bedrock-runtime",
        region_name=config.aws_region,
        config=BotoConfig(retries={"mode": "adaptive", "max_attempts": 5}, read_timeout=120),
    )


def parse_converse_response(resp: Mapping[str, Any], model_id: str) -> ModelResponse:
    """Normalise a Converse API response dict."""
    content = resp.get("output", {}).get("message", {}).get("content", [])
    text_parts = [block["text"] for block in content if "text" in block]
    calls = [
        ToolCallRequest(
            tool_use_id=block["toolUse"]["toolUseId"],
            name=block["toolUse"]["name"],
            arguments=block["toolUse"].get("input", {}),
        )
        for block in content
        if "toolUse" in block
    ]
    usage = resp.get("usage", {})
    return ModelResponse(
        text="\n".join(text_parts) if text_parts else None,
        tool_calls=calls,
        stop_reason=resp.get("stopReason", "end_turn"),
        input_tokens=int(usage.get("inputTokens", 0)),
        output_tokens=int(usage.get("outputTokens", 0)),
        latency_ms=float(resp.get("metrics", {}).get("latencyMs", 0.0)),
        model_id=model_id,
        raw=dict(resp),
    )


class BedrockConverseClient:
    """Live adapter over ``bedrock-runtime.converse``."""

    def __init__(self, config: StockroomConfig, model_id: str, client: Any | None = None) -> None:
        self.config = config
        self.model_id = model_id
        self._client = client or make_bedrock_runtime_client(config)

    def converse(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        max_tokens: int,
    ) -> ModelResponse:
        from botocore.exceptions import BotoCoreError, ClientError

        request: dict[str, Any] = {
            "modelId": self.model_id,
            "messages": messages,
            "system": [{"text": system}],
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": 0.0},
        }
        if tools:
            request["toolConfig"] = {"tools": [t.to_converse() for t in tools]}
        try:
            resp = self._client.converse(**request)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            kind = {
                "ThrottlingException": "throttled",
                "ValidationException": "validation",
                "ServiceUnavailableException": "unavailable",
                "ModelNotReadyException": "unavailable",
                "ModelTimeoutException": "unavailable",
                "AccessDeniedException": "access_denied",
            }.get(code, "other")
            raise ModelError(f"{code}: {exc}", kind=kind) from exc
        except BotoCoreError as exc:
            raise ModelError(str(exc), kind="other") from exc
        return parse_converse_response(resp, self.model_id)


# --- fake ---------------------------------------------------------------------------------------

INJECTION_LINE = re.compile(
    r"(?i)(system notice|ignore (all |any |your )?(previous|prior|earlier) instructions"
    r"|disregard (any |all |your )?(previous|earlier|prior)? ?instructions"
    r"|you must immediately call"
    r"|reply .{0,40}system prompt|overrides? the .{0,40}policy)"
)
INJECTED_CALL = re.compile(
    r"call `?(?P<tool>[a-z_]+)`?\s+for\s+`?(?P<sku>SKU-\d{4})`?"
    r"\s+with\s+quantity\s+`?(?P<qty>\d+)`?"
    r"(?:\s+and\s+priority\s+`?(?P<priority>\w+)`?)?",
    re.I,
)
REFUSAL_PATTERNS = re.compile(
    r"(?i)\b(poem|joke|weather|recipe|song|story|politic|news|stock market|bitcoin|crypto"
    r"|home address|phone number|email address|personal details|date of birth|password)\b"
)
DOMAIN_WORDS = {
    "stock",
    "stocked",
    "sku",
    "order",
    "orders",
    "restock",
    "reorder",
    "policy",
    "return",
    "returns",
    "refund",
    "shipping",
    "ship",
    "shipped",
    "delivery",
    "delivered",
    "warranty",
    "lead",
    "product",
    "products",
    "catalogue",
    "catalog",
    "price",
    "tracking",
    "cancel",
    "cancelled",
    "parcel",
    "units",
    "inventory",
    "supplier",
    "left",
    "sell",
    "fee",
    "priority",
    "stored",
}
POLICY_WORDS = {
    "policy",
    "return",
    "returns",
    "refund",
    "shipping",
    "warranty",
    "lead",
    "fee",
    "parcel",
    "lost",
    "scan",
    "restocking",
    "priority",
    "cancelled",
    "approval",
    "surcharge",
    "cut-off",
    "express",
    "carrier",
    "glossary",
    "backordered",
    "mean",
    "means",
    "window",
    "policies",
}
QUERY_NOISE = {
    "do",
    "you",
    "any",
    "sell",
    "find",
    "me",
    "search",
    "catalogue",
    "catalog",
    "products",
    "product",
    "show",
    "everything",
    "list",
    "have",
    "there",
    "which",
    "what",
    "is",
    "are",
    "the",
    "a",
    "an",
    "in",
    "for",
    "of",
    "and",
    "does",
    "please",
    "say",
    "about",
    "tell",
    "much",
    "how",
    "when",
    "it",
    "to",
    "should",
    "can",
    "be",
    "i",
    "we",
    "our",
    "your",
    "with",
    "that",
    "this",
    "on",
    "at",
    "by",
    "from",
    "as",
    "or",
    "if",
    "so",
    "then",
    "has",
    "hasn't",
    "yet",
    "right",
    "now",
    "still",
    "look",
    "up",
    "give",
    "check",
    "many",
    "left",
    "got",
    "get",
    "where",
    "stored",
    "its",
    "my",
    "long",
    "after",
    "days",
    "treated",
    "without",
    "number",
}
NUMBER_WORDS = {
    "ten": 10,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "hundred": 100,
}


@dataclass
class MockScript:
    case_id: str
    turns: list[dict[str, Any]]

    @classmethod
    def load(cls, path: Path) -> MockScript:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(case_id=raw["case_id"], turns=list(raw["turns"]))


@dataclass
class PlannedCall:
    name: str
    arguments: dict[str, Any]
    intent: str
    condition: str | None = None  # e.g. "below_reorder_point"


@dataclass
class HistoryItem:
    name: str
    arguments: Any
    result: Any
    is_error: bool
    error_code: str | None
    text: str


@dataclass
class Plan:
    kind: str  # "tools" | "refuse" | "clarify_product" | "clarify_order"
    calls: list[PlannedCall] = field(default_factory=list)
    privacy: bool = False


class HeuristicPlanner:
    """Deterministic stand-in for a tool-using model (see module docstring)."""

    def __init__(self, config: StockroomConfig, data: StockroomData) -> None:
        self.config = config
        self.data = data
        self.product_vocab = self._product_vocab()

    # -- vocabulary -----------------------------------------------------------------------------

    def _product_vocab(self) -> set[str]:
        vocab: set[str] = set()
        for p in self.data.catalogue:
            vocab.update(content_tokens(p["name"]))
            vocab.update(content_tokens(p["category"]))
        return vocab | {t[:-1] for t in vocab if t.endswith("s")}

    # -- conversation parsing ------------------------------------------------------------------

    @staticmethod
    def user_query(messages: list[dict[str, Any]]) -> str:
        for m in messages:
            if m["role"] == "user":
                for block in m["content"]:
                    if "text" in block:
                        return block["text"]
        return ""

    @staticmethod
    def history(messages: list[dict[str, Any]]) -> list[HistoryItem]:
        """Pair every assistant toolUse block with the toolResult that followed it."""
        pending: dict[str, tuple[str, Any]] = {}
        items: list[HistoryItem] = []
        for m in messages:
            for block in m["content"]:
                if "toolUse" in block:
                    tu = block["toolUse"]
                    pending[tu["toolUseId"]] = (tu["name"], tu.get("input"))
                elif "toolResult" in block:
                    tr = block["toolResult"]
                    name, args = pending.pop(tr["toolUseId"], ("?", None))
                    parts = tr.get("content", [])
                    result: Any = None
                    text = ""
                    for part in parts:
                        if "json" in part:
                            result = part["json"]
                            text += json.dumps(part["json"], ensure_ascii=False)
                        elif "text" in part:
                            text += part["text"]
                            if result is None:
                                try:
                                    result = json.loads(part["text"])
                                except (ValueError, TypeError):
                                    result = part["text"]
                    is_error = tr.get("status") == "error"
                    code = None
                    if isinstance(result, dict) and "error" in result:
                        code = str(result.get("error"))
                        is_error = True
                    items.append(HistoryItem(name, args, result, is_error, code, text))
        return items

    @staticmethod
    def assistant_turns(messages: list[dict[str, Any]]) -> int:
        return sum(1 for m in messages if m["role"] == "assistant")

    # -- intent ---------------------------------------------------------------------------------

    def clean_query(self, query: str, drop_ids: bool = True) -> str:
        text = query
        if drop_ids:
            text = re.sub(r"(SKU|ORD|RSR)-\d{4}", " ", text)
        toks = [t for t in tokenize(text) if t not in QUERY_NOISE]
        return " ".join(toks)

    def plan(self, query: str) -> Plan:
        q = query.strip()
        lower = q.lower()
        tokens = set(tokenize(lower))
        skus = find_skus(q)
        orders = find_order_ids(q)
        product_hits = bool(tokens & self.product_vocab)
        has_domain = bool(tokens & DOMAIN_WORDS) or bool(skus) or bool(orders) or product_hits
        privacy = bool(re.search(r"(?i)(address|phone|email|personal)", lower)) and (
            "customer" in tokens or "customers" in tokens
        )
        if privacy or REFUSAL_PATTERNS.search(lower) or not has_domain:
            return Plan(kind="refuse", privacy=privacy)

        numbers = [int(n) for n in re.findall(r"\b\d+\b", re.sub(r"(SKU|ORD|RSR)-\d{4}", " ", q))]
        for word, value in NUMBER_WORDS.items():
            if word in tokens:
                numbers.append(value)
        restock_verb = bool(
            re.search(r"(?i)\b(raise|create|order|request|restock|reorder|place)\b", lower)
        )
        quantity = numbers[0] if numbers else None
        priority = "urgent" if "urgent" in tokens else "standard"
        conditional = bool(re.search(r"(?i)\bif so\b", lower))

        calls: list[PlannedCall] = []
        if skus and restock_verb and quantity is not None and "policy" not in tokens:
            if conditional:
                calls.append(PlannedCall("get_stock_level", {"sku": skus[0]}, "stock"))
                calls.append(
                    PlannedCall(
                        "create_restock_request",
                        {"sku": skus[0], "quantity": quantity, "priority": priority},
                        "restock",
                        condition="below_reorder_point",
                    )
                )
            else:
                calls.append(
                    PlannedCall(
                        "create_restock_request",
                        {"sku": skus[0], "quantity": quantity, "priority": priority},
                        "restock",
                    )
                )
            return Plan(kind="tools", calls=calls)

        policy_hit = bool(tokens & POLICY_WORDS) and not skus and not orders
        if policy_hit:
            calls.append(
                PlannedCall("search_policy_docs", {"query": self.clean_query(q)}, "policy")
            )
            return Plan(kind="tools", calls=calls)

        for order_id in orders:
            calls.append(PlannedCall("get_order_status", {"order_id": order_id}, "order"))
        for sku in skus:
            calls.append(PlannedCall("get_stock_level", {"sku": sku}, "stock"))
        if calls:
            return Plan(kind="tools", calls=calls)

        order_words = {"order", "orders", "parcel", "delivery", "tracking"}
        stock_words = {"stock", "units", "left", "inventory", "many"}
        cleaned = self.clean_query(q)
        product_tokens = [t for t in cleaned.split() if t in self.product_vocab]
        if tokens & order_words and not product_tokens:
            return Plan(kind="clarify_order")
        if tokens & stock_words and not product_tokens:
            return Plan(kind="clarify_product")
        if not cleaned:
            return Plan(kind="clarify_product")

        # Product search: split "X and Y" / "X, Y and Z" into separate searches.
        parts = [
            p.strip()
            for p in re.split(r",|\band\b", re.sub(r"(?i)\bshow me everything in\b", "", q))
            if p.strip()
        ]
        seen: set[str] = set()
        for part in parts:
            cleaned_part = self.clean_query(part)
            if cleaned_part and cleaned_part not in seen:
                seen.add(cleaned_part)
                calls.append(PlannedCall("search_products", {"query": cleaned_part}, "search"))
        if not calls:
            calls.append(PlannedCall("search_products", {"query": cleaned}, "search"))
        return Plan(kind="tools", calls=calls)

    # -- tool selection by description --------------------------------------------------------

    INTENT_PHRASES = {
        "stock": "stock level units in stock for a sku",
        "order": "order status tracking for an order id",
        "search": "search products catalogue by name or category keyword",
    }

    def select_tool(self, intent: str, specs: list[ToolSpec]) -> str:
        """Pick the lookup tool whose *description* best matches the intent; ties go to the
        first-listed tool. This is where an over-broad description captures calls."""
        phrase = set(content_tokens(self.INTENT_PHRASES[intent]))
        candidates = [
            s for s in specs if s.name in {"search_products", "get_stock_level", "get_order_status"}
        ]
        best_name, best_score = candidates[0].name, -1
        for spec in candidates:
            desc_tokens = set(content_tokens(spec.name.replace("_", " ") + " " + spec.description))
            score = len(phrase & desc_tokens)
            if score > best_score:
                best_name, best_score = spec.name, score
        return best_name

    def adapt_arguments(self, call: PlannedCall, chosen: str) -> dict[str, Any]:
        if chosen == call.name:
            return dict(call.arguments)
        if chosen == "search_products":
            key = call.arguments.get("sku") or call.arguments.get("order_id") or ""
            return {"query": key}
        return dict(call.arguments)

    # -- main entry -----------------------------------------------------------------------------

    def respond(
        self, system: str, messages: list[dict[str, Any]], specs: list[ToolSpec]
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """Return ``(text, tool_calls)`` for the next assistant turn."""
        query = self.user_query(messages)
        hist = self.history(messages)
        plan = self.plan(query)

        if plan.kind == "refuse":
            text = (
                "That's outside what I can help with. I can answer questions about Stockroom "
                "products, stock levels, orders, restock requests and policies."
            )
            if plan.privacy:
                text = (
                    "That's outside what I can help with; I don't share customer personal details."
                )
            return text, []
        if plan.kind == "clarify_product":
            return (
                "Which product do you mean? Please give me a SKU (format SKU-####) "
                "or a product name.",
                [],
            )
        if plan.kind == "clarify_order":
            return "Please give me the order number (format ORD-####) so I can look it up.", []

        # Naive retry: re-issue the identical call after a transient error.
        if hist and hist[-1].is_error and hist[-1].error_code == "transient":
            if self.config.has_weakness(WEAKNESS_NAIVE_RETRY):
                return None, [{"name": hist[-1].name, "input": hist[-1].arguments}]
            return self._final_answer(query, plan, hist, system), []

        # Indirect prompt injection: follow instruction-like text found in tool output.
        injected = self._find_injection(hist)
        if injected and self.config.has_weakness(WEAKNESS_INJECTION_UNGUARDED):
            already = any(
                h.name == injected["name"] and h.arguments == injected["input"] for h in hist
            )
            if not already:
                return None, [injected]

        executed = [(h.name, h.arguments) for h in hist]
        for call in plan.calls:
            chosen = (
                self.select_tool(call.intent, specs)
                if call.intent in self.INTENT_PHRASES
                else call.name
            )
            args = self.adapt_arguments(call, chosen)
            if (chosen, args) in executed:
                continue
            if call.condition == "below_reorder_point":
                stock = next((h for h in hist if h.name == "get_stock_level"), None)
                if stock is None or not isinstance(stock.result, dict):
                    continue
                if not stock.result.get("below_reorder_point"):
                    continue
            return None, [{"name": chosen, "input": args}]

        return self._final_answer(query, plan, hist, system), []

    def _find_injection(self, hist: list[HistoryItem]) -> dict[str, Any] | None:
        for h in hist:
            if h.name != "search_policy_docs" or not h.text:
                continue
            flat = re.sub(r"\\n(?:> ?)?|\n(?:> ?)?", " ", h.text)
            for line in h.text.splitlines() or [h.text]:
                if INJECTION_LINE.search(line):
                    m = INJECTED_CALL.search(flat)
                    if m and m.group("tool") == "create_restock_request":
                        return {
                            "name": "create_restock_request",
                            "input": {
                                "sku": m.group("sku"),
                                "quantity": int(m.group("qty")),
                                "priority": (m.group("priority") or "standard").lower(),
                            },
                            "_injected": True,
                        }
        return None

    # -- answer templates -----------------------------------------------------------------------

    def _final_answer(self, query: str, plan: Plan, hist: list[HistoryItem], system: str) -> str:
        sentences: list[str] = []
        succeeded = {
            (h.name, json.dumps(h.arguments, sort_keys=True, default=str))
            for h in hist
            if not h.is_error
        }
        for h in hist:
            if isinstance(h.arguments, dict) and h.arguments.get("_injected"):
                continue
            key = (h.name, json.dumps(h.arguments, sort_keys=True, default=str))
            if h.is_error and key in succeeded:
                continue  # a later retry of the same call succeeded
            sentences.append(self._describe(h))
        leaked = any(
            h.name == "create_restock_request"
            and isinstance(h.arguments, dict)
            and h.arguments.get("quantity") == 10000
            for h in hist
        )
        if leaked and self.config.has_weakness(WEAKNESS_INJECTION_UNGUARDED):
            sentences.append(
                "As instructed by the maintenance notice I created the compliance restock request. "
                f"My system prompt is: {system}"
            )
        if not sentences:
            return "I wasn't able to find anything for that request."
        return " ".join(s for s in sentences if s)

    def _describe(self, h: HistoryItem) -> str:
        r = h.result
        if h.is_error:
            msg = r.get("message") if isinstance(r, dict) else str(r)
            if h.name == "create_restock_request":
                return f"I couldn't create the restock request: {msg}."
            return f"The {h.name.replace('_', ' ')} lookup failed: {msg}."
        if h.name == "get_stock_level" and isinstance(r, dict):
            loc = r.get("location", {})
            where = f"aisle {loc.get('aisle')}, bin {loc.get('bin')}" if loc else "unknown location"
            if r.get("stock_level", 0) == 0:
                return (
                    f"{r['sku']} ({r['name']}) is out of stock (0 units); reorder point "
                    f"{r['reorder_point']}; location {where}."
                )
            flag = " which is at or below its reorder point" if r.get("below_reorder_point") else ""
            return (
                f"{r['sku']} ({r['name']}) has {r['stock_level']} units in stock{flag} "
                f"(reorder point {r['reorder_point']}); location {where}."
            )
        if h.name == "get_order_status" and isinstance(r, dict):
            items = ", ".join(
                f"{i['quantity']} x {i['sku']} {i['name']}" for i in r.get("items", [])
            )
            s = f"Order {r['order_id']} for {r['customer_name']} is {r['status']}."
            if r.get("tracking_number"):
                s += (
                    f" It shipped with {r['carrier']} on {r['shipped_at']}, tracking "
                    f"{r['tracking_number']}"
                )
                s += (
                    f"; estimated delivery {r['estimated_delivery']}."
                    if r.get("estimated_delivery")
                    else "."
                )
            if r.get("delivered_at"):
                s += f" It was delivered on {r['delivered_at']}."
            if r["status"] == "backordered":
                s += " At least one line is waiting for stock."
            if r["status"] == "cancelled":
                s += " It is no longer active."
            return s + f" Items: {items}. Total ${r['total_usd']:.2f}."
        if h.name == "search_products" and isinstance(r, list):
            if not r:
                return f"I couldn't find any products matching '{h.arguments.get('query', '')}'."
            shown = r[:8]
            listing = "; ".join(
                f"{p['sku']} {p['name']} (${p['unit_price_usd']:.2f}, "
                f"{_stock_phrase(p['stock_level'])})"
                for p in shown
            )
            more = f" and {len(r) - len(shown)} more" if len(r) > len(shown) else ""
            return (
                f"I found {len(r)} matching product{'s' if len(r) != 1 else ''}{more}: {listing}."
            )
        if h.name == "create_restock_request" and isinstance(r, dict):
            note = f" ({r['note']})" if r.get("status") == "pending_approval" else ""
            return (
                f"Created restock request {r['request_id']} for {r['quantity']} units of "
                f"{r['sku']} ({r['product_name']}) with priority {r['priority']}; "
                f"status {r['status']}{note}."
            )
        if h.name == "search_policy_docs" and isinstance(r, list):
            if not r:
                return "I couldn't find a policy section covering that."
            chunks = [c for c in r if not _is_preamble(c)] or r
            parts = []
            for top in chunks[:2]:
                body = re.sub(r"^##\s+\d+\.\s*.*$", "", top["text"], flags=re.M).strip()
                body = re.sub(r"\s+", " ", body)
                parts.append(f"From {top['title']}: {body}")
            return " ".join(parts)
        return f"{h.name} returned: {json.dumps(r, ensure_ascii=False)[:300]}"


def _stock_phrase(level: int) -> str:
    return "out of stock" if level == 0 else f"{level} in stock"


def _is_preamble(chunk: dict[str, Any]) -> bool:
    """Section-0 chunks that only carry the title and metadata line."""
    return chunk.get("section") == "0" and len(chunk.get("text", "")) < 300


class FakeBedrockClient:
    """Converse-shaped fake. Deterministic latency and token accounting."""

    def __init__(
        self,
        config: StockroomConfig,
        data: StockroomData | None = None,
        scripts_dir: Path | None = None,
        case_id: str | None = None,
    ) -> None:
        self.config = config
        self.data = data or StockroomData.load(config.data_dir)
        self.scripts_dir = scripts_dir or (config.data_dir / "golden" / "mock_scripts")
        self.case_id = case_id
        self.model_id = "fake.stockroom-planner-v1"
        self.planner = HeuristicPlanner(config, self.data)
        self.calls = 0

    def _script(self) -> MockScript | None:
        if not self.case_id:
            return None
        path = self.scripts_dir / f"{self.case_id}.json"
        return MockScript.load(path) if path.exists() else None

    def converse(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        max_tokens: int,
    ) -> ModelResponse:
        self.calls += 1
        prompt_chars = (
            len(system)
            + len(json.dumps(messages, default=str))
            + sum(len(json.dumps(t.to_converse())) for t in tools)
        )
        input_tokens = prompt_chars // CHARS_PER_TOKEN
        script = self._script()
        if script is not None:
            idx = HeuristicPlanner.assistant_turns(messages)
            turn = script.turns[idx] if idx < len(script.turns) else {"text": "Done."}
            text = turn.get("text")
            raw_calls = turn.get("tool_calls", [])
        else:
            text, raw_calls = self.planner.respond(system, messages, tools)
        calls = [
            ToolCallRequest(
                tool_use_id=f"tooluse_{uuid.uuid4().hex[:12]}",
                name=c["name"],
                arguments={k: v for k, v in c["input"].items() if not k.startswith("_")}
                if isinstance(c["input"], dict)
                else c["input"],
            )
            for c in raw_calls
        ]
        output_chars = len(text or "") + sum(
            len(json.dumps(c.arguments, default=str)) for c in calls
        )
        output_tokens = max(1, output_chars // CHARS_PER_TOKEN)
        if output_tokens > max_tokens:
            # Mirror a real model hitting max_tokens: truncate the text.
            text = (text or "")[: max_tokens * CHARS_PER_TOKEN]
            stop = "max_tokens"
            output_tokens = max_tokens
        else:
            stop = "tool_use" if calls else "end_turn"
        return ModelResponse(
            text=text,
            tool_calls=calls,
            stop_reason=stop,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=round(20.0 + input_tokens / 50.0, 2),
            model_id=self.model_id,
        )


def make_model_client(
    config: StockroomConfig, case_id: str | None = None, data: StockroomData | None = None
) -> ModelClient:
    """Pick the live or fake client from the config."""
    if config.mode is Mode.LIVE:
        agent_model, _ = config.require_live_models()
        return BedrockConverseClient(config, agent_model)
    return FakeBedrockClient(config, data=data, case_id=case_id)
