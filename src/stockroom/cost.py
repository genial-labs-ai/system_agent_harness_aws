"""Token-cost estimation from ``pricing.yaml``.

Prices are *never* invented: if a model is missing from the table (and no env override is set)
the estimate is reported as unknown. Amazon Nova prices come from the public AWS Price List
bulk API; third-party models billed through AWS Marketplace (e.g. Anthropic Claude 4.x) are not
in that API, so their entries are ``null`` until a human fills them in from
https://aws.amazon.com/bedrock/pricing/ or sets ``AGENT_PRICE_INPUT_PER_1K`` and friends.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from stockroom.config import PRICING_FILE

_GEO_PREFIX = re.compile(r"^(us|eu|apac|global|jp|au|in|ca|us-gov)\.")


def canonical_model_id(model_id: str) -> str:
    """Strip a cross-region inference-profile prefix (``us.``, ``global.`` …) from a model ID."""
    return _GEO_PREFIX.sub("", model_id.strip())


@dataclass(frozen=True, slots=True)
class ModelPrice:
    model_id: str
    input_per_1k: float | None
    output_per_1k: float | None
    verified_on: str | None
    source: str | None
    note: str | None = None

    @property
    def known(self) -> bool:
        return self.input_per_1k is not None and self.output_per_1k is not None


@dataclass(frozen=True, slots=True)
class CostEstimate:
    model_id: str
    input_tokens: int
    output_tokens: int
    usd: float | None
    price: ModelPrice | None

    @property
    def known(self) -> bool:
        return self.usd is not None

    def format(self) -> str:
        if self.usd is None:
            why = self.price.note if self.price and self.price.note else "model not in pricing.yaml"
            return (
                f"{self.model_id}: {self.input_tokens} in / {self.output_tokens} out tokens, "
                f"cost unknown ({why})"
            )
        return (
            f"{self.model_id}: {self.input_tokens} in / {self.output_tokens} out tokens "
            f"≈ ${self.usd:.6f} (prices verified {self.price.verified_on if self.price else 'n/a'})"
        )


class PriceTable:
    """In-memory view of ``pricing.yaml`` with optional per-run overrides."""

    def __init__(self, models: dict[str, ModelPrice], metadata: dict[str, object]) -> None:
        self._models = models
        self.metadata = metadata

    @classmethod
    def load(cls, path: Path | None = None) -> PriceTable:
        path = path or PRICING_FILE
        if not path.exists():
            return cls({}, {"source": None})
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        models: dict[str, ModelPrice] = {}
        for model_id, entry in (raw.get("models") or {}).items():
            entry = entry or {}
            models[model_id] = ModelPrice(
                model_id=model_id,
                input_per_1k=entry.get("input"),
                output_per_1k=entry.get("output"),
                verified_on=str(entry["verified_on"]) if entry.get("verified_on") else None,
                source=entry.get("source"),
                note=entry.get("note"),
            )
        metadata = {k: v for k, v in raw.items() if k != "models"}
        return cls(models, metadata)

    def lookup(self, model_id: str) -> ModelPrice | None:
        return self._models.get(model_id) or self._models.get(canonical_model_id(model_id))

    def estimate(
        self,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        *,
        input_override: float | None = None,
        output_override: float | None = None,
    ) -> CostEstimate:
        price = self.lookup(model_id)
        if input_override is not None and output_override is not None:
            price = ModelPrice(
                model_id=model_id,
                input_per_1k=input_override,
                output_per_1k=output_override,
                verified_on="env override",
                source="env",
            )
        if price is None or not price.known:
            return CostEstimate(model_id, input_tokens, output_tokens, None, price)
        usd = (input_tokens / 1000.0) * float(price.input_per_1k or 0.0) + (
            output_tokens / 1000.0
        ) * float(price.output_per_1k or 0.0)
        return CostEstimate(model_id, input_tokens, output_tokens, round(usd, 8), price)


@lru_cache(maxsize=4)
def default_price_table(path: Path | None = None) -> PriceTable:
    """Cached loader used by the harness."""
    return PriceTable.load(path)
