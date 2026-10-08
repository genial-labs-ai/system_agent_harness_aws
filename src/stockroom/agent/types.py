"""Shared data types for the agent runtime and the evaluation suite.

Everything that crosses a boundary (harness → evals, harness → JSON results file, tool server →
harness) is a pydantic model so it can be serialised deterministically.
"""

from __future__ import annotations

import datetime as dt
import json
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class State(StrEnum):
    """Harness states. The transition log records every change."""

    PLAN = "PLAN"
    CALL_MODEL = "CALL_MODEL"
    EXECUTE_TOOLS = "EXECUTE_TOOLS"
    OBSERVE = "OBSERVE"
    DONE = "DONE"
    FAILED = "FAILED"


class TerminationReason(StrEnum):
    """Why a run ended. Evals assert on these values."""

    COMPLETED = "COMPLETED"
    MAX_STEPS = "MAX_STEPS"
    TOKEN_BUDGET = "TOKEN_BUDGET"
    REPEATED_CALL = "REPEATED_CALL"
    TIMEOUT = "TIMEOUT"
    MODEL_ERROR = "MODEL_ERROR"
    TOOL_ERROR = "TOOL_ERROR"
    INVALID_TOOL_CALLS = "INVALID_TOOL_CALLS"


class ToolSpec(BaseModel):
    """A tool as advertised to the model (Converse ``toolSpec`` shape)."""

    name: str
    description: str
    input_schema: dict[str, Any]

    def to_converse(self) -> dict[str, Any]:
        return {
            "toolSpec": {
                "name": self.name,
                "description": self.description,
                "inputSchema": {"json": self.input_schema},
            }
        }


class ToolResult(BaseModel):
    """What a tool executor returns to the harness."""

    name: str
    content: Any = None
    is_error: bool = False
    error_code: str | None = None
    payload_chars: int = 0
    latency_ms: float = 0.0

    @classmethod
    def ok(cls, name: str, content: Any, latency_ms: float = 0.0) -> ToolResult:
        text = json.dumps(content, ensure_ascii=False, default=str)
        return cls(name=name, content=content, payload_chars=len(text), latency_ms=latency_ms)

    @classmethod
    def error(
        cls, name: str, message: str, code: str = "tool_error", latency_ms: float = 0.0
    ) -> ToolResult:
        content = {"error": code, "message": message}
        return cls(
            name=name,
            content=content,
            is_error=True,
            error_code=code,
            payload_chars=len(json.dumps(content)),
            latency_ms=latency_ms,
        )


class ToolCallRequest(BaseModel):
    """A tool call as emitted by the model (Converse ``toolUse`` shape)."""

    tool_use_id: str
    name: str
    arguments: Any = Field(default_factory=dict)


class ModelResponse(BaseModel):
    """Normalised Converse response produced by both the live and the fake client."""

    text: str | None = None
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    stop_reason: str = "end_turn"
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    model_id: str = "fake"
    raw: dict[str, Any] | None = None


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def add(self, response: ModelResponse) -> None:
        self.input_tokens += response.input_tokens
        self.output_tokens += response.output_tokens
        self.model_calls += 1


# --- trajectory steps -------------------------------------------------------------------------


class ModelTurn(BaseModel):
    kind: Literal["model_turn"] = "model_turn"
    step: int
    text: str | None = None
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    stop_reason: str = "end_turn"
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    context_tokens_estimate: int = 0


class ToolCallRecord(BaseModel):
    kind: Literal["tool_call"] = "tool_call"
    step: int
    tool_use_id: str
    name: str
    arguments: Any = None
    result_content: Any = None
    is_error: bool = False
    error_code: str | None = None
    validation_error: str | None = None
    payload_chars: int = 0
    latency_ms: float = 0.0
    sanitized: bool = False
    quarantined_lines: int = 0
    blocked_by: str | None = None

    @property
    def executed(self) -> bool:
        """True when the call passed validation, was not blocked by a run guard and ran."""
        return self.validation_error is None and self.blocked_by is None

    def signature(self) -> str:
        return f"{self.name}:{json.dumps(self.arguments, sort_keys=True, default=str)}"


class CompactionEvent(BaseModel):
    kind: Literal["compaction"] = "compaction"
    step: int
    tokens_before: int
    tokens_after: int
    results_summarised: int


class GuardEvent(BaseModel):
    kind: Literal["guard"] = "guard"
    step: int
    guard: str
    reason: TerminationReason
    detail: str


TrajectoryStep = Annotated[
    ModelTurn | ToolCallRecord | CompactionEvent | GuardEvent, Field(discriminator="kind")
]


class Transition(BaseModel):
    step: int
    from_state: State
    to_state: State
    reason: str


class CostEstimateRecord(BaseModel):
    model_id: str
    usd: float | None = None
    note: str = ""


class RunResult(BaseModel):
    """Everything an eval needs to know about one agent run."""

    run_id: str
    case_id: str | None = None
    query: str
    final_answer: str
    trajectory: list[TrajectoryStep] = Field(default_factory=list)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    cost_estimate: CostEstimateRecord
    termination_reason: TerminationReason
    transition_log: list[Transition] = Field(default_factory=list)
    steps: int = 0
    trace_id: str | None = None
    started_at: dt.datetime
    duration_ms: float = 0.0
    mode: str = "mock"
    weaknesses: list[str] = Field(default_factory=list)

    # -- convenience accessors used by metrics --------------------------------------------

    @property
    def tool_records(self) -> list[ToolCallRecord]:
        return [s for s in self.trajectory if isinstance(s, ToolCallRecord)]

    @property
    def executed_tool_calls(self) -> list[ToolCallRecord]:
        return [r for r in self.tool_records if r.executed]

    @property
    def invalid_tool_calls(self) -> list[ToolCallRecord]:
        """Calls that never reached the tool: schema-invalid ones and any a run guard blocked."""
        return [r for r in self.tool_records if not r.executed]

    @property
    def blocked_tool_calls(self) -> list[ToolCallRecord]:
        """Calls a run guard refused (see ``Harness(run_guards=...)``)."""
        return [r for r in self.tool_records if r.blocked_by is not None]

    @property
    def tool_names(self) -> list[str]:
        return [r.name for r in self.executed_tool_calls]

    @property
    def model_turns(self) -> list[ModelTurn]:
        return [s for s in self.trajectory if isinstance(s, ModelTurn)]

    @property
    def compaction_events(self) -> list[CompactionEvent]:
        return [s for s in self.trajectory if isinstance(s, CompactionEvent)]

    @property
    def guard_events(self) -> list[GuardEvent]:
        return [s for s in self.trajectory if isinstance(s, GuardEvent)]

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def print_cost_summary(self) -> None:
        """Human-readable end-of-run summary (live mode prints this by default)."""
        print(
            f"[stockroom] run {self.run_id}: {self.steps} steps, "
            f"{self.usage.model_calls} model calls, {self.usage.input_tokens} in / "
            f"{self.usage.output_tokens} out tokens, terminated {self.termination_reason.value}"
        )
        if self.cost_estimate.usd is None:
            print(f"[stockroom] estimated cost: unknown ({self.cost_estimate.note})")
        else:
            print(
                f"[stockroom] estimated cost: ${self.cost_estimate.usd:.6f} "
                f"({self.cost_estimate.note})"
            )
