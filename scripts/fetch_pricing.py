#!/usr/bin/env python
"""Regenerate ``pricing.yaml`` from the public AWS Price List bulk API (no credentials needed).

Only models present in the Price List feed get numeric prices. Models billed through AWS
Marketplace (third-party providers such as Anthropic for Claude 4.x) are listed with ``null``
prices and a note pointing to the pricing page. Run::

    uv run python scripts/fetch_pricing.py --region us-east-1

This script is the *only* network call in the repo outside live mode and is never run in CI.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.request
from pathlib import Path

import yaml

PRICE_LIST_URL = "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonBedrock/current/{region}/index.json"

# Price-list "model" attribute -> Bedrock model ID (bare, no geo prefix).
# Only entries whose IDs were verified against the Bedrock "models at a glance" pages are mapped.
MODEL_NAME_TO_ID: dict[str, str] = {
    "Nova Micro": "amazon.nova-micro-v1:0",
    "Nova Lite": "amazon.nova-lite-v1:0",
    "Nova Pro": "amazon.nova-pro-v1:0",
    "Nova Premier": "amazon.nova-premier-v1:0",
    "Nova 2.0 Lite": "amazon.nova-2-lite-v1:0",
}

# Models the workshop references that are NOT in the Price List feed (Marketplace-billed).
PLACEHOLDER_MODELS: dict[str, str] = {
    "anthropic.claude-haiku-4-5-20251001-v1:0": (
        "Third-party model billed through AWS Marketplace; not published in the Price List API. "
        "Fill in from https://aws.amazon.com/bedrock/pricing/ or set "
        "AGENT_PRICE_INPUT_PER_1K / AGENT_PRICE_OUTPUT_PER_1K."
    ),
    "anthropic.claude-sonnet-4-5-20250929-v1:0": (
        "Third-party model billed through AWS Marketplace; not published in the Price List API. "
        "Fill in from https://aws.amazon.com/bedrock/pricing/ or set "
        "AGENT_PRICE_INPUT_PER_1K / AGENT_PRICE_OUTPUT_PER_1K."
    ),
}


def fetch(region: str) -> dict:
    url = PRICE_LIST_URL.format(region=region)
    with urllib.request.urlopen(url, timeout=120) as resp:
        return json.load(resp)


def extract(feed: dict) -> dict[str, dict[str, float]]:
    """Return ``{model_name: {"input": usd_per_1k, "output": usd_per_1k}}`` for on-demand prices."""
    prices: dict[str, dict[str, float]] = {}
    on_demand = feed["terms"]["OnDemand"]
    for sku, product in feed["products"].items():
        attrs = product["attributes"]
        model = attrs.get("model")
        if not model or sku not in on_demand:
            continue
        if attrs.get("feature") != "On-demand Inference":
            continue
        inference_type = attrs.get("inferenceType")
        if inference_type not in {"Input tokens", "Output tokens"}:
            continue
        for term in on_demand[sku].values():
            for dim in term["priceDimensions"].values():
                if dim["unit"] != "1K tokens":
                    continue
                usd = float(dim["pricePerUnit"]["USD"])
                key = "input" if inference_type == "Input tokens" else "output"
                prices.setdefault(model, {})[key] = usd
    return prices


def build_yaml(feed: dict, region: str) -> dict:
    found = extract(feed)
    today = dt.date.today().isoformat()
    models: dict[str, dict[str, object]] = {}
    for name, model_id in MODEL_NAME_TO_ID.items():
        entry = found.get(name)
        if entry and "input" in entry and "output" in entry:
            models[model_id] = {
                "input": entry["input"],
                "output": entry["output"],
                "verified_on": today,
                "source": "aws-price-list-api",
                "price_list_name": name,
            }
        else:
            models[model_id] = {
                "input": None,
                "output": None,
                "verified_on": None,
                "source": None,
                "note": f"'{name}' not found in the Price List feed for {region}.",
            }
    for model_id, note in PLACEHOLDER_MODELS.items():
        models[model_id] = {
            "input": None,
            "output": None,
            "verified_on": None,
            "source": None,
            "note": note,
        }
    return {
        "version": 1,
        "currency": "USD",
        "unit": "per_1k_tokens",
        "region": region,
        "source": PRICE_LIST_URL.format(region=region),
        "publication_date": feed.get("publicationDate"),
        "generated_on": today,
        "models": models,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="us-east-1")
    default_out = Path(__file__).resolve().parents[1] / "pricing.yaml"
    parser.add_argument("--out", type=Path, default=default_out)
    parser.add_argument(
        "--from-file", type=Path, help="Use a previously downloaded feed instead of the network"
    )
    args = parser.parse_args(argv)

    feed = json.loads(args.from_file.read_text()) if args.from_file else fetch(args.region)
    doc = build_yaml(feed, args.region)
    header = (
        "# Generated by scripts/fetch_pricing.py from the public AWS Price List bulk API.\n"
        "# Null prices mean 'unknown': the harness prints 'cost unknown' rather than guessing.\n"
    )
    body = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
    args.out.write_text(header + body, encoding="utf-8")
    known = [m for m, e in doc["models"].items() if e.get("input") is not None]
    unknown = [m for m in doc["models"] if m not in known]
    print(f"wrote {args.out} ({len(known)} priced, {len(unknown)} unknown)")
    for m in unknown:
        print(f"  unknown: {m}")
    if not re.search(r"nova-pro", " ".join(known)):
        print(
            "warning: Nova Pro price not found; check MODEL_NAME_TO_ID against the feed",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
