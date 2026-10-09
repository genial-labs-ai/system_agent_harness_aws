"""OpenTelemetry tracing for agent runs.

One root ``invoke_agent`` span per run, a ``chat {model}`` span per model call and an
``execute_tool {tool}`` span per tool call, following the GenAI semantic conventions (see
``semconv.py``). Exporters: in-memory (tests/evals), console, Phoenix (OTLP/HTTP) and CloudWatch
(via the AWS Distro for OpenTelemetry — see README > Live mode > CloudWatch).

:func:`detect_loops` works on a finished trace and is reused by ``ToolCallEvaluator``.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Span, Status, StatusCode

from stockroom.agent.types import ModelResponse, ToolCallRecord, ToolCallRequest, ToolSpec
from stockroom.config import TraceExporter
from stockroom.evals import semconv as sc

PHOENIX_DEFAULT_ENDPOINT = "http://localhost:6006/v1/traces"


@dataclass
class TracingHandle:
    provider: TracerProvider | trace.TracerProvider
    tracer: trace.Tracer
    exporter_kind: str
    memory_exporter: InMemorySpanExporter | None = None

    def finished_spans(self) -> list[ReadableSpan]:
        if self.memory_exporter is None:
            return []
        return list(self.memory_exporter.get_finished_spans())

    def clear(self) -> None:
        if self.memory_exporter is not None:
            self.memory_exporter.clear()

    def flush(self) -> None:
        provider = self.provider
        if hasattr(provider, "force_flush"):
            provider.force_flush()


def configure_tracing(
    exporter: TraceExporter | str = TraceExporter.MEMORY,
    service_name: str = "stockroom-agent",
    endpoint: str | None = None,
) -> TracingHandle:
    """Create an isolated tracer provider for the requested exporter.

    * ``memory``     – spans retained in :class:`InMemorySpanExporter` (tests, notebooks, evals).
    * ``console``    – spans printed as JSON.
    * ``phoenix``    – OTLP/HTTP to a local Arize Phoenix (``PHOENIX_COLLECTOR_ENDPOINT`` or
      ``http://localhost:6006/v1/traces``).
    * ``cloudwatch`` – uses the globally configured provider that ADOT installs when the process
      is started with ``opentelemetry-instrument`` (documented env vars in the README); if ADOT is
      not active this degrades to a plain OTLP/HTTP exporter at ``OTEL_EXPORTER_OTLP_ENDPOINT``.
    * ``none``       – spans are created and dropped.
    """
    kind = TraceExporter(exporter) if isinstance(exporter, str) else exporter
    resource = Resource.create({"service.name": service_name})
    memory: InMemorySpanExporter | None = None
    if kind is TraceExporter.CLOUDWATCH and os.environ.get("OTEL_PYTHON_DISTRO") == "aws_distro":
        provider: TracerProvider | trace.TracerProvider = trace.get_tracer_provider()
    else:
        provider = TracerProvider(resource=resource)
        if kind is TraceExporter.MEMORY:
            memory = InMemorySpanExporter()
            provider.add_span_processor(SimpleSpanProcessor(memory))
        elif kind is TraceExporter.CONSOLE:
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        elif kind in (TraceExporter.PHOENIX, TraceExporter.CLOUDWATCH):
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            if kind is TraceExporter.PHOENIX:
                url = (
                    endpoint
                    or os.environ.get("PHOENIX_COLLECTOR_ENDPOINT")
                    or PHOENIX_DEFAULT_ENDPOINT
                )
                if not url.endswith("/v1/traces"):
                    url = url.rstrip("/") + "/v1/traces"
            else:
                url = endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or ""
                if url and not url.endswith("/v1/traces"):
                    url = url.rstrip("/") + "/v1/traces"
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=url or None)))
    tracer = provider.get_tracer("stockroom", "0.1.0")
    return TracingHandle(
        provider=provider, tracer=tracer, exporter_kind=kind.value, memory_exporter=memory
    )


class RunTracer:
    """Span helpers used by the harness. Safe to construct with ``handle=None`` (no-op provider)."""

    def __init__(
        self,
        handle: TracingHandle | None = None,
        agent_name: str = "stockroom",
        provider_name: str = sc.PROVIDER_FAKE,
    ) -> None:
        self.handle = handle or configure_tracing(TraceExporter.NONE)
        self.agent_name = agent_name
        self.provider_name = provider_name

    @property
    def tracer(self) -> trace.Tracer:
        return self.handle.tracer

    @contextmanager
    def run_span(
        self, run_id: str, case_id: str | None, query: str, weaknesses: Sequence[str]
    ) -> Iterator[Span]:
        with self.tracer.start_as_current_span(
            sc.SPAN_INVOKE_AGENT.format(agent=self.agent_name),
            attributes={
                sc.GEN_AI_OPERATION_NAME: sc.OP_INVOKE_AGENT,
                sc.GEN_AI_PROVIDER_NAME: self.provider_name,
                sc.GEN_AI_AGENT_NAME: self.agent_name,
                sc.GEN_AI_CONVERSATION_ID: run_id,
                sc.OPENINFERENCE_SPAN_KIND: sc.OI_KIND_AGENT,
                sc.STOCKROOM_RUN_ID: run_id,
                sc.STOCKROOM_CASE_ID: case_id or "",
                sc.STOCKROOM_WEAKNESSES: ",".join(sorted(weaknesses)),
                "input.value": sc.truncate(query),
            },
        ) as span:
            yield span

    @contextmanager
    def model_span(
        self, step: int, model_id: str, max_tokens: int, context_tokens: int, depth: int
    ) -> Iterator[Span]:
        with self.tracer.start_as_current_span(
            sc.SPAN_CHAT.format(model=model_id),
            attributes={
                sc.GEN_AI_OPERATION_NAME: sc.OP_CHAT,
                sc.GEN_AI_PROVIDER_NAME: self.provider_name,
                sc.GEN_AI_REQUEST_MODEL: model_id,
                sc.GEN_AI_REQUEST_MAX_TOKENS: max_tokens,
                sc.OPENINFERENCE_SPAN_KIND: sc.OI_KIND_LLM,
                sc.STOCKROOM_STEP: step,
                sc.STOCKROOM_TRAJECTORY_DEPTH: depth,
                sc.STOCKROOM_CONTEXT_TOKENS: context_tokens,
            },
        ) as span:
            yield span

    @staticmethod
    def record_model_response(span: Span, response: ModelResponse) -> None:
        span.set_attribute(sc.GEN_AI_RESPONSE_MODEL, response.model_id)
        span.set_attribute(sc.GEN_AI_USAGE_INPUT_TOKENS, response.input_tokens)
        span.set_attribute(sc.GEN_AI_USAGE_OUTPUT_TOKENS, response.output_tokens)
        span.set_attribute(sc.GEN_AI_RESPONSE_FINISH_REASONS, [response.stop_reason])
        span.set_attribute("output.value", sc.truncate(response.text or ""))
        if response.tool_calls:
            span.set_attribute(
                "stockroom.tool_calls_requested", [c.name for c in response.tool_calls]
            )

    @contextmanager
    def tool_span(
        self, step: int, call: ToolCallRequest, spec: ToolSpec | None, depth: int
    ) -> Iterator[Span]:
        attributes: dict[str, Any] = {
            sc.GEN_AI_OPERATION_NAME: sc.OP_EXECUTE_TOOL,
            sc.GEN_AI_PROVIDER_NAME: self.provider_name,
            sc.GEN_AI_TOOL_NAME: call.name,
            sc.GEN_AI_TOOL_CALL_ID: call.tool_use_id,
            sc.GEN_AI_TOOL_TYPE: "function",
            sc.GEN_AI_TOOL_CALL_ARGUMENTS: sc.truncate(json.dumps(call.arguments, default=str)),
            sc.OPENINFERENCE_SPAN_KIND: sc.OI_KIND_TOOL,
            sc.STOCKROOM_STEP: step,
            sc.STOCKROOM_TRAJECTORY_DEPTH: depth,
        }
        if spec is not None:
            attributes[sc.GEN_AI_TOOL_DESCRIPTION] = sc.truncate(spec.description, 256)
        with self.tracer.start_as_current_span(
            sc.SPAN_EXECUTE_TOOL.format(tool=call.name), attributes=attributes
        ) as span:
            yield span

    @staticmethod
    def record_tool_result(span: Span, record: ToolCallRecord) -> None:
        span.set_attribute(
            sc.GEN_AI_TOOL_CALL_RESULT, sc.truncate(json.dumps(record.result_content, default=str))
        )
        span.set_attribute(sc.STOCKROOM_TOOL_IS_ERROR, record.is_error)
        span.set_attribute(sc.STOCKROOM_TOOL_PAYLOAD_CHARS, record.payload_chars)
        if record.validation_error:
            span.set_attribute(
                sc.STOCKROOM_TOOL_VALIDATION_ERROR, sc.truncate(record.validation_error, 256)
            )
            span.set_attribute(sc.ERROR_TYPE, "invalid_arguments")
            span.set_status(Status(StatusCode.ERROR, "invalid arguments"))
        elif record.is_error:
            span.set_attribute(sc.ERROR_TYPE, record.error_code or "tool_error")
            span.set_status(Status(StatusCode.ERROR, record.error_code or "tool_error"))

    def compaction_span(self, step: int, before: int, after: int, summarised: int) -> None:
        with self.tracer.start_as_current_span(
            "compaction",
            attributes={
                sc.OPENINFERENCE_SPAN_KIND: sc.OI_KIND_CHAIN,
                sc.STOCKROOM_STEP: step,
                sc.STOCKROOM_COMPACTION_TOKENS_BEFORE: before,
                sc.STOCKROOM_COMPACTION_TOKENS_AFTER: after,
                "stockroom.compaction.results_summarised": summarised,
            },
        ) as span:
            span.add_event("compaction", {"tokens_saved": max(0, before - after)})

    @staticmethod
    def set_termination(span: Span, reason: str, steps: int, final_answer: str) -> None:
        span.set_attribute(sc.STOCKROOM_TERMINATION_REASON, reason)
        span.set_attribute("stockroom.steps", steps)
        span.set_attribute("output.value", sc.truncate(final_answer))
        if reason != "COMPLETED":
            span.set_status(Status(StatusCode.ERROR, reason))


# --- analysis of finished traces ---------------------------------------------------------------


@dataclass
class TraceToolCall:
    name: str
    arguments: str
    step: int
    is_error: bool
    latency_ms: float
    span_id: str
    error_type: str | None = None  # the span's error.type: invalid_arguments, blocked_by_guard, ...

    @property
    def signature(self) -> str:
        return f"{self.name}:{self.arguments}"


@dataclass
class TraceModelCall:
    step: int
    input_tokens: int
    output_tokens: int
    context_tokens: int
    latency_ms: float


@dataclass
class TraceSummary:
    run_id: str | None
    case_id: str | None
    termination_reason: str | None
    tool_calls: list[TraceToolCall] = field(default_factory=list)
    model_calls: list[TraceModelCall] = field(default_factory=list)
    compactions: int = 0
    span_count: int = 0

    @classmethod
    def from_spans(cls, spans: Sequence[ReadableSpan], run_id: str | None = None) -> TraceSummary:
        tool_calls: list[TraceToolCall] = []
        model_calls: list[TraceModelCall] = []
        compactions = 0
        found_run: str | None = None
        case_id: str | None = None
        termination: str | None = None
        selected = [s for s in spans if run_id is None or _root_run_id(s, spans) == run_id]
        for s in selected:
            attrs = dict(s.attributes or {})
            op = attrs.get(sc.GEN_AI_OPERATION_NAME)
            if op == sc.OP_INVOKE_AGENT:
                found_run = str(attrs.get(sc.STOCKROOM_RUN_ID))
                case_id = str(attrs.get(sc.STOCKROOM_CASE_ID) or "") or None
                termination = attrs.get(sc.STOCKROOM_TERMINATION_REASON)
            elif op == sc.OP_EXECUTE_TOOL:
                tool_calls.append(
                    TraceToolCall(
                        name=str(attrs.get(sc.GEN_AI_TOOL_NAME)),
                        arguments=str(attrs.get(sc.GEN_AI_TOOL_CALL_ARGUMENTS, "")),
                        step=int(attrs.get(sc.STOCKROOM_STEP, 0)),
                        is_error=bool(attrs.get(sc.STOCKROOM_TOOL_IS_ERROR, False)),
                        latency_ms=_duration_ms(s),
                        span_id=format(s.context.span_id, "016x") if s.context else "",
                        error_type=attrs.get(sc.ERROR_TYPE),
                    )
                )
            elif op == sc.OP_CHAT:
                model_calls.append(
                    TraceModelCall(
                        step=int(attrs.get(sc.STOCKROOM_STEP, 0)),
                        input_tokens=int(attrs.get(sc.GEN_AI_USAGE_INPUT_TOKENS, 0)),
                        output_tokens=int(attrs.get(sc.GEN_AI_USAGE_OUTPUT_TOKENS, 0)),
                        context_tokens=int(attrs.get(sc.STOCKROOM_CONTEXT_TOKENS, 0)),
                        latency_ms=_duration_ms(s),
                    )
                )
            elif s.name == "compaction":
                compactions += 1
        tool_calls.sort(key=lambda c: (c.step, c.span_id))
        model_calls.sort(key=lambda c: c.step)
        return cls(
            run_id=found_run,
            case_id=case_id,
            termination_reason=termination,
            tool_calls=tool_calls,
            model_calls=model_calls,
            compactions=compactions,
            span_count=len(selected),
        )


def _duration_ms(span: ReadableSpan) -> float:
    if span.start_time is None or span.end_time is None:
        return 0.0
    return (span.end_time - span.start_time) / 1_000_000.0


def _root_run_id(span: ReadableSpan, spans: Sequence[ReadableSpan]) -> str | None:
    by_id = {s.context.span_id: s for s in spans if s.context}
    cur: ReadableSpan | None = span
    while cur is not None:
        attrs = dict(cur.attributes or {})
        if attrs.get(sc.GEN_AI_OPERATION_NAME) == sc.OP_INVOKE_AGENT:
            return str(attrs.get(sc.STOCKROOM_RUN_ID))
        parent = cur.parent
        cur = by_id.get(parent.span_id) if parent else None
    return None


@dataclass
class LoopFinding:
    kind: str  # repeated_identical_call | loop | redundant_query
    signature: str
    count: int
    steps: list[int]


def detect_loops(summary: TraceSummary, window: int = 3) -> list[LoopFinding]:
    """Flag repeated identical calls (consecutive), loops (same call >= ``window`` times anywhere)
    and redundant queries (a later call whose arguments are a subset of an earlier one's)."""
    findings: list[LoopFinding] = []
    calls = summary.tool_calls
    # consecutive repeats
    run_start = 0
    for i in range(1, len(calls) + 1):
        if i == len(calls) or calls[i].signature != calls[run_start].signature:
            length = i - run_start
            if length >= 2:
                findings.append(
                    LoopFinding(
                        "repeated_identical_call",
                        calls[run_start].signature,
                        length,
                        [c.step for c in calls[run_start:i]],
                    )
                )
            run_start = i
    # loops anywhere
    counts = Counter(c.signature for c in calls)
    for sig, n in counts.items():
        if n >= window:
            findings.append(
                LoopFinding("loop", sig, n, [c.step for c in calls if c.signature == sig])
            )
    # redundant queries
    seen: list[TraceToolCall] = []
    for c in calls:
        for earlier in seen:
            if (
                earlier.name == c.name
                and earlier.signature != c.signature
                and _args_subset(c.arguments, earlier.arguments)
            ):
                findings.append(
                    LoopFinding("redundant_query", c.signature, 2, [earlier.step, c.step])
                )
                break
        seen.append(c)
    return findings


def _args_subset(later: str, earlier: str) -> bool:
    try:
        a, b = json.loads(later), json.loads(earlier)
    except ValueError:
        return False
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    return all(k in b and b[k] == v for k, v in a.items()) and len(a) < len(b)


def span_tree(spans: Sequence[ReadableSpan]) -> str:
    """Render spans as an indented tree (for notebooks)."""
    by_parent: dict[int | None, list[ReadableSpan]] = {}
    for s in spans:
        parent = s.parent.span_id if s.parent else None
        by_parent.setdefault(parent, []).append(s)
    lines: list[str] = []

    def walk(parent: int | None, depth: int) -> None:
        for s in sorted(by_parent.get(parent, []), key=lambda x: x.start_time or 0):
            attrs = dict(s.attributes or {})
            extra = ""
            if attrs.get(sc.GEN_AI_OPERATION_NAME) == sc.OP_CHAT:
                extra = (
                    f" in={attrs.get(sc.GEN_AI_USAGE_INPUT_TOKENS)}"
                    f" out={attrs.get(sc.GEN_AI_USAGE_OUTPUT_TOKENS)}"
                )
            elif attrs.get(sc.GEN_AI_OPERATION_NAME) == sc.OP_EXECUTE_TOOL:
                extra = (
                    f" args={attrs.get(sc.GEN_AI_TOOL_CALL_ARGUMENTS)}"
                    f" error={attrs.get(sc.STOCKROOM_TOOL_IS_ERROR)}"
                )
            lines.append(f"{'  ' * depth}{s.name} [{_duration_ms(s):.1f} ms]{extra}")
            walk(s.context.span_id if s.context else None, depth + 1)

    walk(None, 0)
    return "\n".join(lines)
