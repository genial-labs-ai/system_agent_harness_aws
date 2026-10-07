"""Typed loaders for the golden dataset and the judge-calibration set."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from stockroom.config import DATA_DIR

TrajectoryMode = Literal["exact", "in_order_subset", "any_order"]


class ExpectedTool(BaseModel):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class RetrievalReference(BaseModel):
    reference_contexts: list[str]
    reference: str


class GoldenCase(BaseModel):
    id: str
    category: str
    query: str
    expected_tools: list[ExpectedTool] = Field(default_factory=list)
    trajectory_match_mode: TrajectoryMode = "exact"
    expected_facts: list[str] = Field(default_factory=list)
    forbidden_facts: list[str] = Field(default_factory=list)
    expected_termination: str = "COMPLETED"
    must_not_call: list[str] = Field(default_factory=list)
    mock_script: str | None = None
    reference_answer: str
    retrieval: RetrievalReference | None = None
    max_steps: int | None = None
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None

    @property
    def expected_tool_names(self) -> list[str]:
        return [t.name for t in self.expected_tools]


class CalibrationItem(BaseModel):
    id: str
    query: str
    answer: str
    context: str
    expected_facts: list[str]
    forbidden_facts: list[str] = Field(default_factory=list)
    human_label: Literal["pass", "fail"]
    human_score: int
    rationale: str
    probe_group: str | None = None
    probe_role: str | None = None

    @property
    def human_pass(self) -> bool:
        return self.human_label == "pass"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


GOLDEN_PATH = DATA_DIR / "golden" / "stockroom_golden_v1.jsonl"
CALIBRATION_PATH = DATA_DIR / "judge_calibration" / "calibration_v1.jsonl"
MOCK_SCRIPTS_DIR = DATA_DIR / "golden" / "mock_scripts"


def load_golden(path: Path | None = None) -> list[GoldenCase]:
    return [GoldenCase.model_validate(r) for r in _read_jsonl(path or GOLDEN_PATH)]


def load_calibration(path: Path | None = None) -> list[CalibrationItem]:
    return [CalibrationItem.model_validate(r) for r in _read_jsonl(path or CALIBRATION_PATH)]


def golden_by_id(path: Path | None = None) -> dict[str, GoldenCase]:
    return {c.id: c for c in load_golden(path)}
