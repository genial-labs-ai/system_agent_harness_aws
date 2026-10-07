#!/usr/bin/env python
"""Deterministically generate ``data/catalogue.json`` and ``data/orders.json``.

The product names, categories and prices are authored by hand below; derived fields (stock levels,
bin locations, long descriptions, spec sheets, variants, order histories) are computed from a fixed
seed so the files are reproducible. Re-run after editing the tables::

    uv run python scripts/generate_seed_data.py
"""

from __future__ import annotations

import datetime as dt
import json
import random
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
SEED = 20261006

# (name, category, unit price USD, unit, supplier_id)
PRODUCTS: list[tuple[str, str, float, str, str]] = [
    # Office supplies
    ("A4 Copy Paper 80gsm (500 sheets)", "Office Supplies", 6.90, "ream", "SUP-01"),
    ("Ballpoint Pens Black (box of 50)", "Office Supplies", 11.50, "box", "SUP-01"),
    ("Sticky Notes 76x76mm Yellow (12 pads)", "Office Supplies", 8.20, "pack", "SUP-01"),
    ("Lever Arch File A4 Blue", "Office Supplies", 3.40, "each", "SUP-01"),
    ("Whiteboard Markers Assorted (pack of 8)", "Office Supplies", 9.75, "pack", "SUP-01"),
    ("Stapler Heavy Duty 100-sheet", "Office Supplies", 28.00, "each", "SUP-02"),
    ("Laser Toner Cartridge TN-2420", "Office Supplies", 64.00, "each", "SUP-02"),
    # Workshop tools
    ("Cordless Drill Driver 18V", "Workshop Tools", 129.00, "each", "SUP-03"),
    ("Hex Bolts M8 x 40mm Zinc (box of 100)", "Workshop Tools", 14.20, "box", "SUP-03"),
    ("Socket Set 1/2 inch Drive 24-piece", "Workshop Tools", 58.50, "set", "SUP-03"),
    ("Digital Calliper 150mm", "Workshop Tools", 32.00, "each", "SUP-03"),
    ("Adjustable Spanner 250mm", "Workshop Tools", 17.80, "each", "SUP-03"),
    ("Cable Ties 300mm Black (bag of 100)", "Workshop Tools", 5.60, "bag", "SUP-04"),
    ("Torque Wrench 1/2 inch 40-200Nm", "Workshop Tools", 74.00, "each", "SUP-03"),
    # Safety equipment
    ("Nitrile Gloves Large (box of 100)", "Safety Equipment", 12.90, "box", "SUP-05"),
    ("Safety Glasses Clear Anti-Fog", "Safety Equipment", 4.50, "each", "SUP-05"),
    ("Hi-Vis Vest Yellow Class 2", "Safety Equipment", 6.80, "each", "SUP-05"),
    ("Hard Hat White Vented", "Safety Equipment", 15.30, "each", "SUP-05"),
    ("Ear Defenders 31dB", "Safety Equipment", 19.90, "each", "SUP-05"),
    ("First Aid Kit Workplace 50-person", "Safety Equipment", 44.00, "each", "SUP-06"),
    ("Steel Toe Boots Size 10", "Safety Equipment", 68.00, "pair", "SUP-06"),
    # Packaging
    ("Cardboard Boxes 400x300x300mm (pack of 20)", "Packaging", 22.40, "pack", "SUP-07"),
    ("Packing Tape Clear 48mm (6 rolls)", "Packaging", 9.30, "pack", "SUP-07"),
    ("Bubble Wrap Roll 500mm x 50m", "Packaging", 18.70, "roll", "SUP-07"),
    ("Pallet Stretch Film 500mm (6 rolls)", "Packaging", 41.00, "pack", "SUP-07"),
    ("Shipping Labels 4x6 inch (roll of 500)", "Packaging", 13.60, "roll", "SUP-07"),
    ("Void Fill Paper 380mm x 450m", "Packaging", 36.00, "roll", "SUP-08"),
    # Electronics accessories
    ("USB-C Charging Cable 2m", "Electronics Accessories", 7.90, "each", "SUP-09"),
    ("Wireless Mouse Ergonomic", "Electronics Accessories", 24.50, "each", "SUP-09"),
    ("HDMI Cable 3m 4K", "Electronics Accessories", 10.40, "each", "SUP-09"),
    ("Power Strip 6-way Surge Protected", "Electronics Accessories", 21.00, "each", "SUP-09"),
    ("Barcode Scanner USB Handheld", "Electronics Accessories", 89.00, "each", "SUP-10"),
    ("Label Printer Thermal 4 inch", "Electronics Accessories", 159.00, "each", "SUP-10"),
    ("AA Batteries Alkaline (pack of 24)", "Electronics Accessories", 11.20, "pack", "SUP-09"),
    # Cleaning and janitorial
    ("Paper Towels 2-ply (case of 12 rolls)", "Cleaning and Janitorial", 27.50, "case", "SUP-11"),
    ("Floor Cleaner Concentrate 5L", "Cleaning and Janitorial", 16.40, "each", "SUP-11"),
    ("Refuse Sacks Heavy Duty (roll of 100)", "Cleaning and Janitorial", 14.90, "roll", "SUP-11"),
    ("Microfibre Cloths (pack of 10)", "Cleaning and Janitorial", 7.30, "pack", "SUP-11"),
    ("Hand Sanitiser Gel 500ml", "Cleaning and Janitorial", 4.20, "each", "SUP-11"),
    ("Mop Bucket with Wringer 15L", "Cleaning and Janitorial", 33.00, "each", "SUP-12"),
    ("Dispenser Soap Wall-mounted 1L", "Cleaning and Janitorial", 18.50, "each", "SUP-12"),
]

# Hand-set stock levels for products the golden set asks about (SKU index -> units).
# Everything else gets a seeded random level. Zero/low levels drive the "out of stock" cases.
FIXED_STOCK: dict[int, int] = {
    0: 320,  # copy paper
    1: 48,  # ballpoint pens
    6: 0,  # toner cartridge: out of stock
    7: 5,  # cordless drill: low
    8: 1250,  # hex bolts
    14: 42,  # nitrile gloves large
    17: 0,  # hard hat: out of stock
    19: 12,  # first aid kit
    21: 86,  # cardboard boxes
    27: 210,  # usb-c cable
    31: 3,  # barcode scanner: low
    32: 0,  # label printer: out of stock
    34: 64,  # paper towels
}

SUPPLIERS: dict[str, dict[str, object]] = {
    "SUP-01": {"name": "Northfield Stationery", "lead_time_days": 3},
    "SUP-02": {"name": "PrintParts Direct", "lead_time_days": 7},
    "SUP-03": {"name": "Ironbridge Tools", "lead_time_days": 5},
    "SUP-04": {"name": "FastenRight", "lead_time_days": 4},
    "SUP-05": {"name": "SafeHands PPE", "lead_time_days": 2},
    "SUP-06": {"name": "WorkGuard Supply", "lead_time_days": 10},
    "SUP-07": {"name": "BoxLine Packaging", "lead_time_days": 3},
    "SUP-08": {"name": "EcoFill Materials", "lead_time_days": 6},
    "SUP-09": {"name": "Voltage Accessories", "lead_time_days": 4},
    "SUP-10": {"name": "ScanTech Devices", "lead_time_days": 14},
    "SUP-11": {"name": "CleanRoom Supplies", "lead_time_days": 2},
    "SUP-12": {"name": "Janitorial Wholesale", "lead_time_days": 5},
}

CUSTOMERS = [
    "Avery Lindqvist",
    "Priya Raman",
    "Tomás Ferreira",
    "Mei-Ling Chao",
    "Kwame Mensah",
    "Sofia Petrov",
    "Daniel O'Connor",
    "Hana Yamamoto",
    "Lucas Moreau",
    "Amara Okafor",
    "Noor Haddad",
    "Ben Carter",
]

CARRIERS = ["Parcelforce", "DPD", "UPS", "DHL"]


def long_description(name: str, category: str, rng: random.Random) -> str:
    """A deliberately verbose description (used by the oversized-payload weakness)."""
    fillers = [
        f"{name} is stocked for {category.lower()} customers who need dependable supplies on a predictable schedule.",
        "Each unit is inspected on receipt, labelled with a scannable barcode and stored in a climate-controlled bay.",
        "Bulk pricing applies automatically at checkout once the order quantity crosses the pack-size threshold.",
        "Compatible with the standard Stockroom replenishment workflow, including scheduled restock requests.",
        "Returns are accepted under the standard returns policy provided the packaging is intact.",
        "Supplier batch numbers are retained for twelve months to support warranty claims and recalls.",
        "This line is reviewed quarterly against usage data so that reorder points stay realistic.",
        "Ships from the main distribution centre; split shipments are consolidated where possible.",
    ]
    picks = rng.sample(fillers, 5)
    return " ".join(picks)


def spec_sheet(category: str, rng: random.Random) -> dict[str, str]:
    base = {
        "country_of_origin": rng.choice(["DE", "PL", "CN", "GB", "IT", "US"]),
        "pack_weight_kg": f"{rng.uniform(0.2, 12.0):.2f}",
        "pack_dimensions_mm": f"{rng.randint(100, 600)}x{rng.randint(100, 400)}x{rng.randint(50, 400)}",
        "barcode_format": "GS1-128",
        "shelf_life_months": str(rng.choice([12, 24, 36, 60])),
        "hazardous": "no",
    }
    if category == "Cleaning and Janitorial":
        base["hazardous"] = rng.choice(["no", "irritant"])
    if category == "Safety Equipment":
        base["certification"] = rng.choice(["EN 166", "EN 388", "EN ISO 20345", "EN 352-1"])
    if category == "Electronics Accessories":
        base["certification"] = "CE, RoHS"
    return base


def variants(sku: str, name: str, rng: random.Random) -> list[dict[str, object]]:
    attrs = ["Standard", "Bulk 5-pack", "Bulk 10-pack"]
    out = []
    for idx, attr in enumerate(attrs[: rng.randint(1, 3)], start=1):
        out.append(
            {"variant_sku": f"{sku}-V{idx}", "option": attr, "stock_level": rng.randint(0, 60)}
        )
    return out


def build_catalogue() -> list[dict[str, object]]:
    rng = random.Random(SEED)
    rows = []
    for idx, (name, category, price, unit, supplier_id) in enumerate(PRODUCTS):
        sku = f"SKU-{1001 + idx}"
        stock = FIXED_STOCK.get(idx, rng.randint(15, 400))
        reorder_point = max(10, int(stock * 0.25)) if stock else 20
        rows.append(
            {
                "sku": sku,
                "name": name,
                "category": category,
                "unit_price_usd": price,
                "unit": unit,
                "stock_level": stock,
                "reorder_point": reorder_point,
                "location": {"aisle": (idx // 7) + 1, "bin": (idx % 7) * 3 + 2},
                "supplier_id": supplier_id,
                "supplier_name": SUPPLIERS[supplier_id]["name"],
                "lead_time_days": SUPPLIERS[supplier_id]["lead_time_days"],
                "long_description": long_description(name, category, rng),
                "spec_sheet": spec_sheet(category, rng),
                "variants": variants(sku, name, rng),
                "tags": [w.lower().strip("(),") for w in name.split()[:3]],
            }
        )
    return rows


def build_orders(catalogue: list[dict[str, object]]) -> list[dict[str, object]]:
    rng = random.Random(SEED + 1)
    statuses = ["processing", "shipped", "delivered", "delivered", "backordered", "cancelled"]
    base_date = dt.date(2026, 9, 1)
    orders = []
    ids = [f"ORD-{1001 + i}" for i in range(20)] + [f"ORD-{9001 + i}" for i in range(5)]
    for i, order_id in enumerate(ids):
        placed = base_date + dt.timedelta(days=rng.randint(0, 30))
        status = statuses[i % len(statuses)]
        n_items = rng.randint(1, 3)
        items = []
        for prod in rng.sample(catalogue, n_items):
            items.append(
                {
                    "sku": prod["sku"],
                    "name": prod["name"],
                    "quantity": rng.randint(1, 12),
                    "unit_price_usd": prod["unit_price_usd"],
                }
            )
        total = round(sum(it["quantity"] * it["unit_price_usd"] for it in items), 2)
        shipped = (
            placed + dt.timedelta(days=rng.randint(1, 3))
            if status in {"shipped", "delivered"}
            else None
        )
        delivered = (
            shipped + dt.timedelta(days=rng.randint(1, 4))
            if status == "delivered" and shipped
            else None
        )
        carrier = rng.choice(CARRIERS) if shipped else None
        orders.append(
            {
                "order_id": order_id,
                "customer_name": CUSTOMERS[i % len(CUSTOMERS)],
                "status": status,
                "placed_at": placed.isoformat(),
                "shipped_at": shipped.isoformat() if shipped else None,
                "delivered_at": delivered.isoformat() if delivered else None,
                "carrier": carrier,
                "tracking_number": f"{carrier[:2].upper()}{rng.randint(10_000_000, 99_999_999)}"
                if carrier
                else None,
                "estimated_delivery": (
                    (shipped + dt.timedelta(days=3)).isoformat()
                    if shipped and status == "shipped"
                    else None
                ),
                "items": items,
                "total_usd": total,
                "shard": "legacy" if order_id.startswith("ORD-9") else "primary",
            }
        )
    # Pin a few values the golden set relies on.
    by_id = {o["order_id"]: o for o in orders}
    by_id["ORD-1001"].update(
        {
            "status": "shipped",
            "carrier": "DPD",
            "tracking_number": "DP48213377",
            "shipped_at": "2026-09-12",
            "delivered_at": None,
            "estimated_delivery": "2026-09-15",
        }
    )
    by_id["ORD-1002"].update(
        {
            "status": "delivered",
            "carrier": "UPS",
            "tracking_number": "UP55019842",
            "shipped_at": "2026-09-05",
            "delivered_at": "2026-09-08",
            "estimated_delivery": None,
        }
    )
    by_id["ORD-1003"].update(
        {
            "status": "backordered",
            "carrier": None,
            "tracking_number": None,
            "shipped_at": None,
            "delivered_at": None,
            "estimated_delivery": None,
        }
    )
    by_id["ORD-1004"].update(
        {
            "status": "processing",
            "carrier": None,
            "tracking_number": None,
            "shipped_at": None,
            "delivered_at": None,
            "estimated_delivery": None,
        }
    )
    by_id["ORD-1005"].update(
        {
            "status": "cancelled",
            "carrier": None,
            "tracking_number": None,
            "shipped_at": None,
            "delivered_at": None,
            "estimated_delivery": None,
        }
    )
    by_id["ORD-9001"].update(
        {
            "status": "shipped",
            "carrier": "Parcelforce",
            "tracking_number": "PA77120931",
            "shipped_at": "2026-09-20",
            "delivered_at": None,
            "estimated_delivery": "2026-09-23",
        }
    )
    by_id["ORD-9002"].update(
        {
            "status": "delivered",
            "carrier": "DHL",
            "tracking_number": "DH30987112",
            "shipped_at": "2026-09-02",
            "delivered_at": "2026-09-04",
            "estimated_delivery": None,
        }
    )
    return orders


def main() -> int:
    catalogue = build_catalogue()
    orders = build_orders(catalogue)
    (DATA_DIR / "catalogue.json").write_text(
        json.dumps(catalogue, indent=2) + "\n", encoding="utf-8"
    )
    (DATA_DIR / "orders.json").write_text(json.dumps(orders, indent=2) + "\n", encoding="utf-8")
    (DATA_DIR / "suppliers.json").write_text(
        json.dumps(SUPPLIERS, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(catalogue)} products, {len(orders)} orders, {len(SUPPLIERS)} suppliers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
