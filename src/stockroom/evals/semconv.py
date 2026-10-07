"""OpenTelemetry GenAI semantic-convention attribute names used by the tracer.

Source: https://github.com/open-telemetry/semantic-conventions-genai (docs/gen-ai/gen-ai-spans.md,
gen-ai-agent-spans.md), status *Development* as of 2026-10. The names are spelled out here rather
than imported from ``opentelemetry.semconv._incubating`` so the workshop does not break when the
incubating module moves; ``tests/test_tracer.py`` cross-checks them against the installed package
when it is available.
"""

from __future__ import annotations

# Span name formats
SPAN_CHAT = "chat {model}"
SPAN_EXECUTE_TOOL = "execute_tool {tool}"
SPAN_INVOKE_AGENT = "invoke_agent {agent}"

# Operation names
OP_CHAT = "chat"
OP_EXECUTE_TOOL = "execute_tool"
OP_INVOKE_AGENT = "invoke_agent"

# Attribute keys (gen_ai.*)
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_PROVIDER_NAME = "gen_ai.provider.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"
GEN_AI_CONVERSATION_ID = "gen_ai.conversation.id"
GEN_AI_AGENT_NAME = "gen_ai.agent.name"
GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"
GEN_AI_TOOL_DESCRIPTION = "gen_ai.tool.description"
GEN_AI_TOOL_TYPE = "gen_ai.tool.type"
GEN_AI_TOOL_CALL_ARGUMENTS = "gen_ai.tool.call.arguments"
GEN_AI_TOOL_CALL_RESULT = "gen_ai.tool.call.result"
ERROR_TYPE = "error.type"

# Provider value for Amazon Bedrock
PROVIDER_AWS_BEDROCK = "aws.bedrock"
PROVIDER_FAKE = "stockroom.fake"

# OpenInference (Arize Phoenix) span kind, emitted alongside gen_ai.* for viewers that predate the
# automatic gen_ai -> OpenInference conversion (Phoenix >= 15.10 converts automatically).
OPENINFERENCE_SPAN_KIND = "openinference.span.kind"
OI_KIND_LLM = "LLM"
OI_KIND_TOOL = "TOOL"
OI_KIND_AGENT = "AGENT"
OI_KIND_CHAIN = "CHAIN"

# Workshop-specific attributes (namespaced to avoid collisions)
STOCKROOM_STEP = "stockroom.step"
STOCKROOM_TRAJECTORY_DEPTH = "stockroom.trajectory.depth"
STOCKROOM_TERMINATION_REASON = "stockroom.termination_reason"
STOCKROOM_RUN_ID = "stockroom.run_id"
STOCKROOM_CASE_ID = "stockroom.case_id"
STOCKROOM_CONTEXT_TOKENS = "stockroom.context_tokens_estimate"
STOCKROOM_TOOL_IS_ERROR = "stockroom.tool.is_error"
STOCKROOM_TOOL_PAYLOAD_CHARS = "stockroom.tool.payload_chars"
STOCKROOM_TOOL_VALIDATION_ERROR = "stockroom.tool.validation_error"
STOCKROOM_COMPACTION_TOKENS_BEFORE = "stockroom.compaction.tokens_before"
STOCKROOM_COMPACTION_TOKENS_AFTER = "stockroom.compaction.tokens_after"
STOCKROOM_WEAKNESSES = "stockroom.weaknesses"

MAX_ATTRIBUTE_CHARS = 512


def truncate(value: object, limit: int = MAX_ATTRIBUTE_CHARS) -> str:
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= limit else text[: limit - 15] + f"...[+{len(text) - limit + 15}]"
