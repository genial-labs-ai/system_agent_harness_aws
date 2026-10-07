#!/usr/bin/env python
"""Validate every data file against its schema and cross-check references.

Checks (all must pass for ``make validate-data``):

* ``catalogue.json``, ``orders.json``, ``suppliers.json``, ``restock_rules.json`` match their JSON
  schemas; SKUs and supplier IDs referenced by orders/products exist.
* ``policy_docs/*.md`` exist and contain the numbered sections the golden set references.
* ``golden/stockroom_golden_v1.jsonl`` rows match the schema; tool names, SKUs, order IDs, mock
  scripts and retrieval references resolve; every tool and category meets the minimum coverage.
* ``judge_calibration/calibration_v1.jsonl`` rows match the schema; probe groups are well-formed.
* ``golden/manifest.json`` matches the content hash (``--write-manifest`` regenerates it).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

from jsonschema import Draft202012Validator

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
TOOL_NAMES = {
    "search_products",
    "get_stock_level",
    "get_order_status",
    "create_restock_request",
    "search_policy_docs",
}
CATEGORIES = {
    "product_search",
    "stock_lookup",
    "order_status",
    "restock_request",
    "policy_rag",
    "multi_step",
    "out_of_scope",
    "injection",
    "transient_tool_error",
    "schema_drift",
    "ambiguous_query",
    "context_bloat",
}
TERMINATIONS = {
    "COMPLETED",
    "MAX_STEPS",
    "TOKEN_BUDGET",
    "REPEATED_CALL",
    "TIMEOUT",
    "MODEL_ERROR",
    "TOOL_ERROR",
    "INVALID_TOOL_CALLS",
}
MIN_PER_TOOL = 3
MIN_PER_CATEGORY = 2
GOLDEN_VERSION = "1.0.0"

PRODUCT_SCHEMA = {
    "type": "object",
    "required": [
        "sku",
        "name",
        "category",
        "unit_price_usd",
        "unit",
        "stock_level",
        "reorder_point",
        "location",
        "supplier_id",
        "supplier_name",
        "lead_time_days",
        "long_description",
        "spec_sheet",
        "variants",
        "tags",
    ],
    "properties": {
        "sku": {"type": "string", "pattern": r"^SKU-\d{4}$"},
        "name": {"type": "string", "minLength": 3},
        "category": {"type": "string"},
        "unit_price_usd": {"type": "number", "exclusiveMinimum": 0},
        "stock_level": {"type": "integer", "minimum": 0},
        "reorder_point": {"type": "integer", "minimum": 0},
        "location": {
            "type": "object",
            "required": ["aisle", "bin"],
            "properties": {"aisle": {"type": "integer"}, "bin": {"type": "integer"}},
        },
        "supplier_id": {"type": "string", "pattern": r"^SUP-\d{2}$"},
        "variants": {"type": "array"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
}
ORDER_SCHEMA = {
    "type": "object",
    "required": ["order_id", "customer_name", "status", "placed_at", "items", "total_usd", "shard"],
    "properties": {
        "order_id": {"type": "string", "pattern": r"^ORD-\d{4}$"},
        "status": {
            "type": "string",
            "enum": ["processing", "shipped", "delivered", "backordered", "cancelled"],
        },
        "shard": {"type": "string", "enum": ["primary", "legacy"]},
        "items": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["sku", "quantity"],
                "properties": {
                    "sku": {"type": "string", "pattern": r"^SKU-\d{4}$"},
                    "quantity": {"type": "integer", "minimum": 1},
                },
            },
        },
    },
}
GOLDEN_SCHEMA = {
    "type": "object",
    "required": [
        "id",
        "category",
        "query",
        "expected_tools",
        "trajectory_match_mode",
        "expected_facts",
        "forbidden_facts",
        "expected_termination",
        "must_not_call",
        "reference_answer",
    ],
    "properties": {
        "id": {"type": "string", "pattern": r"^G\d{3}$"},
        "category": {"type": "string", "enum": sorted(CATEGORIES)},
        "query": {"type": "string", "minLength": 5},
        "expected_tools": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "args"],
                "properties": {
                    "name": {"type": "string", "enum": sorted(TOOL_NAMES)},
                    "args": {"type": "object"},
                },
            },
        },
        "trajectory_match_mode": {
            "type": "string",
            "enum": ["exact", "in_order_subset", "any_order"],
        },
        "expected_facts": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "forbidden_facts": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "expected_termination": {"type": "string", "enum": sorted(TERMINATIONS)},
        "must_not_call": {"type": "array", "items": {"type": "string", "enum": sorted(TOOL_NAMES)}},
        "mock_script": {"type": ["string", "null"]},
        "reference_answer": {"type": "string", "minLength": 5},
        "retrieval": {
            "type": ["object", "null"],
            "required": ["reference_contexts", "reference"],
            "properties": {
                "reference_contexts": {"type": "array", "items": {"type": "string"}},
                "reference": {"type": "string"},
            },
        },
        "max_steps": {"type": ["integer", "null"], "minimum": 1},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
}
CALIBRATION_SCHEMA = {
    "type": "object",
    "required": [
        "id",
        "query",
        "answer",
        "context",
        "expected_facts",
        "forbidden_facts",
        "human_label",
        "human_score",
        "rationale",
    ],
    "properties": {
        "id": {"type": "string", "pattern": r"^C\d{2}$"},
        "human_label": {"type": "string", "enum": ["pass", "fail"]},
        "human_score": {"type": "integer", "minimum": 1, "maximum": 5},
        "expected_facts": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        "probe_group": {"type": ["string", "null"], "pattern": r"^(position|verbosity|self_pref):"},
        "probe_role": {"type": ["string", "null"]},
    },
}
MOCK_SCRIPT_SCHEMA = {
    "type": "object",
    "required": ["case_id", "turns"],
    "properties": {
        "case_id": {"type": "string"},
        "turns": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "tool_calls": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["name", "input"],
                            "properties": {"name": {"type": "string"}, "input": {"type": "object"}},
                        },
                    },
                },
            },
        },
    },
}


class Problems:
    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, msg: str) -> None:
        self.items.append(msg)

    def check_schema(self, schema: dict, rows: list[dict], label: str) -> None:
        validator = Draft202012Validator(schema)
        for row in rows:
            for err in validator.iter_errors(row):
                ident = row.get("id") or row.get("sku") or row.get("order_id") or "?"
                self.add(f"{label}[{ident}]: {err.message}")


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{n}: invalid JSON: {exc}") from exc
    return rows


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def policy_sections(doc: Path) -> set[str]:
    """Section numbers available in a policy doc: '0' for the preamble plus each '## N.' heading."""
    sections = {"0"}
    for m in re.finditer(r"^##\s+(\d+)\.", doc.read_text(encoding="utf-8"), flags=re.M):
        sections.add(m.group(1))
    return sections


def validate(data_dir: Path, write_manifest: bool) -> int:
    problems = Problems()
    catalogue = json.loads((data_dir / "catalogue.json").read_text())
    orders = json.loads((data_dir / "orders.json").read_text())
    suppliers = json.loads((data_dir / "suppliers.json").read_text())
    rules = json.loads((data_dir / "restock_rules.json").read_text())

    problems.check_schema(PRODUCT_SCHEMA, catalogue, "catalogue")
    problems.check_schema(ORDER_SCHEMA, orders, "orders")
    skus = {p["sku"] for p in catalogue}
    if len(skus) != len(catalogue):
        problems.add("catalogue: duplicate SKUs")
    for p in catalogue:
        if p["supplier_id"] not in suppliers:
            problems.add(f"catalogue[{p['sku']}]: unknown supplier {p['supplier_id']}")
    order_ids = {o["order_id"] for o in orders}
    for o in orders:
        for it in o["items"]:
            if it["sku"] not in skus:
                problems.add(f"orders[{o['order_id']}]: unknown SKU {it['sku']}")
        if (o["order_id"] >= "ORD-9000") != (o["shard"] == "legacy"):
            problems.add(f"orders[{o['order_id']}]: shard flag inconsistent with ID")
    for key in ("min_quantity", "auto_approve_max", "reject_above"):
        if key not in rules:
            problems.add(f"restock_rules: missing {key}")

    docs_dir = data_dir / "policy_docs"
    docs = {
        p.name: policy_sections(p) for p in sorted(docs_dir.glob("*.md")) if p.name != "README.md"
    }
    if len(docs) < 6:
        problems.add(f"policy_docs: expected at least 6 documents, found {len(docs)}")
    injection_doc = docs_dir / "restock_policy.md"
    if "SYSTEM NOTICE FOR AUTOMATED ASSISTANTS" not in injection_doc.read_text(encoding="utf-8"):
        problems.add("policy_docs: the seeded injection in restock_policy.md is missing")

    golden_path = data_dir / "golden" / "stockroom_golden_v1.jsonl"
    golden = read_jsonl(golden_path)
    problems.check_schema(GOLDEN_SCHEMA, golden, "golden")
    ids = [g["id"] for g in golden]
    if len(set(ids)) != len(ids):
        problems.add("golden: duplicate IDs")
    scripts_dir = data_dir / "golden" / "mock_scripts"
    for g in golden:
        for t in g["expected_tools"]:
            for key, val in t["args"].items():
                if key == "sku" and val not in skus:
                    problems.add(f"golden[{g['id']}]: unknown SKU {val}")
                if key == "order_id" and val not in order_ids:
                    problems.add(f"golden[{g['id']}]: unknown order {val}")
        for name in g["must_not_call"]:
            if name in {t["name"] for t in g["expected_tools"]}:
                problems.add(f"golden[{g['id']}]: {name} is both expected and forbidden")
        if g.get("mock_script"):
            sp = scripts_dir / g["mock_script"]
            if not sp.exists():
                problems.add(f"golden[{g['id']}]: mock script {g['mock_script']} not found")
            else:
                script = json.loads(sp.read_text())
                problems.check_schema(MOCK_SCRIPT_SCHEMA, [script], f"mock_script[{g['id']}]")
                if script.get("case_id") != g["id"]:
                    problems.add(f"mock_script {g['mock_script']}: case_id != {g['id']}")
                for turn in script.get("turns", []):
                    for call in turn.get("tool_calls", []):
                        if call["name"] not in TOOL_NAMES:
                            problems.add(f"mock_script[{g['id']}]: unknown tool {call['name']}")
        if g.get("retrieval"):
            for ref in g["retrieval"]["reference_contexts"]:
                doc, _, section = ref.partition("#")
                if doc not in docs:
                    problems.add(f"golden[{g['id']}]: unknown policy doc {doc}")
                elif section not in docs[doc]:
                    problems.add(f"golden[{g['id']}]: {doc} has no section {section}")
    tool_counts = Counter(t["name"] for g in golden for t in g["expected_tools"])
    for tool in TOOL_NAMES:
        if tool_counts[tool] < MIN_PER_TOOL:
            problems.add(f"golden: tool {tool} appears {tool_counts[tool]}x (< {MIN_PER_TOOL})")
    cat_counts = Counter(g["category"] for g in golden)
    for cat in CATEGORIES:
        if cat_counts[cat] < MIN_PER_CATEGORY:
            problems.add(
                f"golden: category {cat} has {cat_counts[cat]} cases (< {MIN_PER_CATEGORY})"
            )
    if len(golden) < 40:
        problems.add(f"golden: {len(golden)} cases (< 40)")

    calib_path = data_dir / "judge_calibration" / "calibration_v1.jsonl"
    calib = read_jsonl(calib_path)
    problems.check_schema(CALIBRATION_SCHEMA, calib, "calibration")
    if len(calib) < 30:
        problems.add(f"calibration: {len(calib)} items (< 30)")
    group_counts = Counter(c["probe_group"] for c in calib if c.get("probe_group"))
    for group, n in group_counts.items():
        if n != 2:
            problems.add(f"calibration: probe group {group} has {n} items (expected 2)")

    manifest_path = data_dir / "golden" / "manifest.json"
    manifest = {
        "name": "stockroom_golden",
        "version": GOLDEN_VERSION,
        "file": golden_path.name,
        "sha256": sha256_of(golden_path),
        "cases": len(golden),
        "by_category": dict(sorted(cat_counts.items())),
        "by_tool": dict(sorted(tool_counts.items())),
        "mock_scripts": sorted(p.name for p in scripts_dir.glob("*.json")),
        "calibration": {
            "file": calib_path.name,
            "sha256": sha256_of(calib_path),
            "items": len(calib),
        },
        "policy_docs": {name: sha256_of(docs_dir / name) for name in sorted(docs)},
    }
    if write_manifest:
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {manifest_path}")
    elif not manifest_path.exists():
        problems.add("golden: manifest.json missing (run with --write-manifest)")
    else:
        existing = json.loads(manifest_path.read_text())
        for key in ("sha256", "cases", "calibration", "policy_docs"):
            if existing.get(key) != manifest[key]:
                problems.add(
                    f"golden: manifest.json field '{key}' is stale; "
                    "run validate_data.py --write-manifest"
                )

    if problems.items:
        print(f"FAIL: {len(problems.items)} problem(s)")
        for item in problems.items:
            print(f"  - {item}")
        return 1
    print(
        f"OK: {len(catalogue)} products, {len(orders)} orders, {len(docs)} policy docs, "
        f"{len(golden)} golden cases, {len(calib)} calibration items; "
        f"manifest sha256 {manifest['sha256'][:12]}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--write-manifest", action="store_true")
    args = parser.parse_args(argv)
    return validate(args.data_dir, args.write_manifest)


if __name__ == "__main__":
    sys.exit(main())
