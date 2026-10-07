from __future__ import annotations

from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from stockroom.agent.retrieval import default_index, split_sections
from stockroom.agent.tools import (
    SEARCH_PRODUCTS_DESC_AMBIGUOUS,
    SEARCH_PRODUCTS_DESC_FIXED,
    TOOL_NAMES,
    StockroomTools,
    build_tool_specs,
)
from stockroom.config import StockroomConfig


@pytest.fixture
def tools(mock_config: StockroomConfig) -> StockroomTools:
    return StockroomTools(mock_config)


def test_specs_are_valid_json_schemas(mock_config: StockroomConfig) -> None:
    specs = build_tool_specs(mock_config)
    assert [s.name for s in specs] == list(TOOL_NAMES)
    for spec in specs:
        Draft202012Validator.check_schema(spec.input_schema)
        converse = spec.to_converse()["toolSpec"]
        assert converse["name"] == spec.name and "json" in converse["inputSchema"]


def test_ambiguous_description_flag_changes_only_search_products() -> None:
    fixed = {s.name: s.description for s in build_tool_specs(StockroomConfig.mock())}
    ambiguous = {
        s.name: s.description
        for s in build_tool_specs(StockroomConfig.mock(weaknesses="ambiguous_tool_desc"))
    }
    assert fixed["search_products"] == SEARCH_PRODUCTS_DESC_FIXED
    assert ambiguous["search_products"] == SEARCH_PRODUCTS_DESC_AMBIGUOUS
    for name in TOOL_NAMES:
        if name != "search_products":
            assert fixed[name] == ambiguous[name]


def test_search_products_compact_and_ranked(tools: StockroomTools) -> None:
    result = tools.call("search_products", {"query": "ear defenders"})
    assert not result.is_error
    assert result.content[0]["sku"] == "SKU-1019"
    assert set(result.content[0]) == {
        "sku",
        "name",
        "category",
        "unit_price_usd",
        "unit",
        "stock_level",
        "in_stock",
    }
    assert len(tools.call("search_products", {"query": "packaging"}).content) == 5
    assert len(tools.call("search_products", {"query": "packaging", "max_results": 2}).content) == 2
    assert tools.call("search_products", {"query": "zzzz"}).content == []


def test_oversized_payload_flag_returns_everything() -> None:
    tools = StockroomTools(StockroomConfig.mock(weaknesses="oversized_payload"))
    small = tools.call("search_products", {"query": "ear defenders"})
    assert len(small.content) == 1 and "long_description" in small.content[0]
    big = tools.call("search_products", {"query": "packaging"})
    assert len(big.content) == 41  # "full category export"
    assert big.payload_chars > 40_000


def test_get_stock_level(tools: StockroomTools) -> None:
    r = tools.call("get_stock_level", {"sku": "SKU-1015"}).content
    assert r["stock_level"] == 42 and r["location"] == {"aisle": 3, "bin": 2}
    assert tools.call("get_stock_level", {"sku": "SKU-1032"}).content["below_reorder_point"]
    err = tools.call("get_stock_level", {"sku": "SKU-9999"})
    assert err.is_error and err.error_code == "invalid_request"


def test_get_order_status_primary_and_legacy(tools: StockroomTools) -> None:
    r = tools.call("get_order_status", {"order_id": "ORD-1001"}).content
    assert r["status"] == "shipped" and r["tracking_number"] == "DP48213377"
    legacy = tools.call("get_order_status", {"order_id": "ORD-9001"})
    assert not legacy.is_error, "bounded internal retry hides the first transient failure"
    assert legacy.content["tracking_number"] == "PA77120931"
    assert tools.legacy.attempts["ORD-9001"] == 2


def test_naive_retry_flag_disables_internal_retry() -> None:
    tools = StockroomTools(StockroomConfig.mock(weaknesses="naive_retry"))
    for _ in range(3):
        r = tools.call("get_order_status", {"order_id": "ORD-9001"})
        assert r.is_error and r.error_code == "transient"
    assert not tools.call("get_order_status", {"order_id": "ORD-1001"}).is_error


def test_restock_rules(tools: StockroomTools) -> None:
    ok = tools.call("create_restock_request", {"sku": "SKU-1007", "quantity": 50}).content
    assert (
        ok["request_id"] == "RSR-0001" and ok["status"] == "open" and ok["priority"] == "standard"
    )
    dup = tools.call("create_restock_request", {"sku": "SKU-1007", "quantity": 60})
    assert dup.is_error and "already an open" in dup.content["message"]
    pending = tools.call("create_restock_request", {"sku": "SKU-1009", "quantity": 800}).content
    assert pending["status"] == "pending_approval"
    too_small = tools.call("create_restock_request", {"sku": "SKU-1020", "quantity": 5})
    assert too_small.is_error and "minimum" in too_small.content["message"]
    too_big = tools.call("create_restock_request", {"sku": "SKU-1001", "quantity": 6000})
    assert too_big.is_error and "5,000" in too_big.content["message"]
    tools.reset()
    assert (
        tools.call("create_restock_request", {"sku": "SKU-1007", "quantity": 50}).content[
            "request_id"
        ]
        == "RSR-0001"
    )


def test_policy_search_and_chunking(tools: StockroomTools) -> None:
    hits = tools.call("search_policy_docs", {"query": "return window unused items"}).content
    assert hits[0]["chunk_id"] == "returns_policy.md#1" and "30 days" in hits[0]["text"]
    chunks = split_sections("x.md", "# Title\nmeta\n\n## 1. One\nalpha\n\n## 2. Two\nbeta")
    assert [c.chunk_id for c in chunks] == ["x.md#0", "x.md#1", "x.md#2"]
    assert chunks[1].text.startswith("## 1. One")


def test_injected_chunk_is_retrievable(mock_config: StockroomConfig) -> None:
    index = default_index(Path(mock_config.data_dir) / "policy_docs")
    top = [c.chunk_id for c, _ in index.search("restock priority stock zero", k=3)]
    assert "restock_policy.md#4" in top


def test_unknown_tool_and_bad_arguments(tools: StockroomTools) -> None:
    assert tools.call("teleport", {}).error_code == "unknown_tool"
    assert tools.call("get_stock_level", {"nope": 1}).error_code == "bad_arguments"
