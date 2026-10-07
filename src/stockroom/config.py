"""Runtime configuration for the Stockroom agent and its evaluation suite.

Every setting comes from environment variables (or an explicit mapping passed to
:meth:`StockroomConfig.from_env`). Nothing model-specific is hardcoded: in live mode the
caller *must* provide ``AGENT_MODEL_ID`` and ``JUDGE_MODEL_ID``.

Seeded weaknesses are feature flags (see :data:`WEAKNESS_FLAGS`). The shipped default is
"all fixed" so that the CI gate is green; the Day 3 lab turns them on one at a time.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parents[2]
DATA_DIR: Path = REPO_ROOT / "data"
PRICING_FILE: Path = REPO_ROOT / "pricing.yaml"


class Mode(StrEnum):
    """Where model calls go."""

    MOCK = "mock"
    LIVE = "live"


class ToolTransport(StrEnum):
    """How the harness reaches its tools."""

    LOCAL = "local"
    MCP_STDIO = "mcp-stdio"
    MCP_HTTP = "mcp-http"


class TraceExporter(StrEnum):
    """Where OpenTelemetry spans are exported."""

    MEMORY = "memory"
    CONSOLE = "console"
    PHOENIX = "phoenix"
    CLOUDWATCH = "cloudwatch"
    NONE = "none"


WEAKNESS_AMBIGUOUS_TOOL_DESC = "ambiguous_tool_desc"
WEAKNESS_OVERSIZED_PAYLOAD = "oversized_payload"
WEAKNESS_NAIVE_RETRY = "naive_retry"
WEAKNESS_INJECTION_UNGUARDED = "injection_unguarded"

WEAKNESS_FLAGS: frozenset[str] = frozenset(
    {
        WEAKNESS_AMBIGUOUS_TOOL_DESC,
        WEAKNESS_OVERSIZED_PAYLOAD,
        WEAKNESS_NAIVE_RETRY,
        WEAKNESS_INJECTION_UNGUARDED,
    }
)


class ConfigError(ValueError):
    """Raised when the environment is inconsistent (e.g. live mode without model IDs)."""


def _get_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer, got {raw!r}") from exc


def _get_float(env: Mapping[str, str], key: str, default: float | None) -> float | None:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number, got {raw!r}") from exc


def _get_str(env: Mapping[str, str], key: str, default: str | None) -> str | None:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()


def parse_weaknesses(raw: str | Iterable[str] | None) -> frozenset[str]:
    """Parse ``STOCKROOM_WEAKNESSES`` (comma separated) into a validated frozenset."""
    if raw is None:
        return frozenset()
    items = raw.split(",") if isinstance(raw, str) else list(raw)
    flags = {item.strip() for item in items if item.strip()}
    unknown = flags - WEAKNESS_FLAGS
    if unknown:
        raise ConfigError(
            f"Unknown weakness flag(s) {sorted(unknown)}; valid flags: {sorted(WEAKNESS_FLAGS)}"
        )
    return frozenset(flags)


@dataclass(frozen=True, slots=True)
class StockroomConfig:
    """Immutable settings object. Build it with :meth:`from_env` or :meth:`mock`."""

    mode: Mode = Mode.MOCK
    agent_model_id: str | None = None
    judge_model_id: str | None = None
    aws_region: str = "us-east-1"
    max_tokens: int = 1024
    max_steps: int = 8
    token_budget: int = 20_000
    wall_clock_timeout_s: float = 60.0
    repeat_call_window: int = 3
    compaction_token_threshold: int = 6_000
    compaction_keep_turns: int = 4
    weaknesses: frozenset[str] = frozenset()
    tool_transport: ToolTransport = ToolTransport.LOCAL
    mcp_url: str = "http://127.0.0.1:8765/mcp"
    trace_exporter: TraceExporter = TraceExporter.MEMORY
    s3_bucket: str | None = None
    s3_prefix: str = "stockroom-workshop"
    knowledge_base_id: str | None = None
    data_dir: Path = DATA_DIR
    pricing_file: Path = PRICING_FILE
    agent_price_input_per_1k: float | None = None
    agent_price_output_per_1k: float | None = None
    judge_price_input_per_1k: float | None = None
    judge_price_output_per_1k: float | None = None
    confirm_aws_spend: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> StockroomConfig:
        """Read every setting from ``env`` (defaults to ``os.environ``)."""
        env = os.environ if env is None else env
        mode_raw = (_get_str(env, "STOCKROOM_MODE", Mode.MOCK.value) or "mock").lower()
        try:
            mode = Mode(mode_raw)
        except ValueError as exc:
            raise ConfigError(f"STOCKROOM_MODE must be 'mock' or 'live', got {mode_raw!r}") from exc
        transport_raw = _get_str(env, "STOCKROOM_TOOL_TRANSPORT", None) or ToolTransport.LOCAL.value
        try:
            transport = ToolTransport(transport_raw)
        except ValueError as exc:
            raise ConfigError(
                f"STOCKROOM_TOOL_TRANSPORT must be one of {[t.value for t in ToolTransport]}"
            ) from exc
        exporter_raw = _get_str(env, "STOCKROOM_TRACE_EXPORTER", None) or TraceExporter.MEMORY.value
        try:
            exporter = TraceExporter(exporter_raw)
        except ValueError as exc:
            raise ConfigError(
                f"STOCKROOM_TRACE_EXPORTER must be one of {[t.value for t in TraceExporter]}"
            ) from exc

        data_dir_raw = _get_str(env, "STOCKROOM_DATA_DIR", None)
        cfg = cls(
            mode=mode,
            agent_model_id=_get_str(env, "AGENT_MODEL_ID", None),
            judge_model_id=_get_str(env, "JUDGE_MODEL_ID", None),
            aws_region=_get_str(env, "AWS_REGION", None)
            or _get_str(env, "AWS_DEFAULT_REGION", "us-east-1")
            or "us-east-1",
            max_tokens=_get_int(env, "MAX_TOKENS", 1024),
            max_steps=_get_int(env, "MAX_STEPS", 8),
            token_budget=_get_int(env, "TOKEN_BUDGET", 20_000),
            wall_clock_timeout_s=_get_float(env, "WALL_CLOCK_TIMEOUT_S", 60.0) or 60.0,
            repeat_call_window=_get_int(env, "REPEAT_CALL_WINDOW", 3),
            compaction_token_threshold=_get_int(env, "COMPACTION_TOKEN_THRESHOLD", 6_000),
            compaction_keep_turns=_get_int(env, "COMPACTION_KEEP_TURNS", 4),
            weaknesses=parse_weaknesses(_get_str(env, "STOCKROOM_WEAKNESSES", None)),
            tool_transport=transport,
            mcp_url=_get_str(env, "STOCKROOM_MCP_URL", "http://127.0.0.1:8765/mcp")
            or "http://127.0.0.1:8765/mcp",
            trace_exporter=exporter,
            s3_bucket=_get_str(env, "S3_BUCKET", None),
            s3_prefix=_get_str(env, "S3_PREFIX", "stockroom-workshop") or "stockroom-workshop",
            knowledge_base_id=_get_str(env, "KNOWLEDGE_BASE_ID", None),
            data_dir=Path(data_dir_raw) if data_dir_raw else DATA_DIR,
            agent_price_input_per_1k=_get_float(env, "AGENT_PRICE_INPUT_PER_1K", None),
            agent_price_output_per_1k=_get_float(env, "AGENT_PRICE_OUTPUT_PER_1K", None),
            judge_price_input_per_1k=_get_float(env, "JUDGE_PRICE_INPUT_PER_1K", None),
            judge_price_output_per_1k=_get_float(env, "JUDGE_PRICE_OUTPUT_PER_1K", None),
            confirm_aws_spend=_get_bool(env, "STOCKROOM_CONFIRM_AWS_SPEND", False),
        )
        cfg.validate()
        return cfg

    @classmethod
    def mock(cls, **overrides: object) -> StockroomConfig:
        """A mock-mode config with optional field overrides (handy in tests and notebooks)."""
        return cls(mode=Mode.MOCK).replace(**overrides)

    def replace(self, **changes: object) -> StockroomConfig:
        """Return a copy with ``changes`` applied (``weaknesses`` accepts str or iterable)."""
        if "weaknesses" in changes:
            changes["weaknesses"] = parse_weaknesses(changes["weaknesses"])  # type: ignore[arg-type]
        new = dataclasses.replace(self, **changes)  # type: ignore[arg-type]
        new.validate()
        return new

    def validate(self) -> None:
        """Raise :class:`ConfigError` for impossible combinations."""
        if self.max_steps < 1:
            raise ConfigError("MAX_STEPS must be >= 1")
        if self.max_tokens < 1:
            raise ConfigError("MAX_TOKENS must be >= 1")
        if self.token_budget < self.max_tokens:
            raise ConfigError("TOKEN_BUDGET must be >= MAX_TOKENS")
        if self.repeat_call_window < 2:
            raise ConfigError("REPEAT_CALL_WINDOW must be >= 2")
        if self.compaction_keep_turns < 1:
            raise ConfigError("COMPACTION_KEEP_TURNS must be >= 1")
        if self.wall_clock_timeout_s <= 0:
            raise ConfigError("WALL_CLOCK_TIMEOUT_S must be > 0")

    @property
    def is_live(self) -> bool:
        return self.mode is Mode.LIVE

    def has_weakness(self, flag: str) -> bool:
        if flag not in WEAKNESS_FLAGS:
            raise ConfigError(f"Unknown weakness flag {flag!r}")
        return flag in self.weaknesses

    def require_live_models(self) -> tuple[str, str]:
        """Return ``(agent_model_id, judge_model_id)`` or raise a clear error in live mode."""
        missing = [
            name
            for name, value in (
                ("AGENT_MODEL_ID", self.agent_model_id),
                ("JUDGE_MODEL_ID", self.judge_model_id),
            )
            if not value
        ]
        if missing:
            raise ConfigError(
                "Live mode needs "
                + " and ".join(missing)
                + ". List candidates with `aws bedrock list-inference-profiles "
                "--type-equals SYSTEM_DEFINED` (see README > Live mode). Example: "
                "AGENT_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0 "
                "JUDGE_MODEL_ID=us.amazon.nova-pro-v1:0"
            )
        return self.agent_model_id or "", self.judge_model_id or ""

    def describe(self) -> str:
        """One-line, secret-free summary used by notebooks and the CLI."""
        flags = ",".join(sorted(self.weaknesses)) or "none"
        models = (
            f"agent={self.agent_model_id or '-'} judge={self.judge_model_id or '-'}"
            if self.is_live
            else "agent=FakeBedrockClient judge=FakeJudge"
        )
        return (
            f"mode={self.mode.value} {models} region={self.aws_region} "
            f"tools={self.tool_transport.value} tracing={self.trace_exporter.value} "
            f"weaknesses={flags} max_steps={self.max_steps} token_budget={self.token_budget}"
        )


def aws_credentials_available() -> bool:
    """True when boto3 can resolve credentials from the usual chain (env, profile, role)."""
    try:
        import boto3  # local import keeps config importable without boto3 at module load
    except ImportError:
        return False
    try:
        session = boto3.Session()
        return session.get_credentials() is not None
    except Exception:  # any resolution failure means "no credentials"
        return False


def detect_mode(env: Mapping[str, str] | None = None, *, announce: bool = True) -> StockroomConfig:
    """Config for notebooks: honour ``STOCKROOM_MODE`` if set, otherwise probe for credentials.

    Live mode is only selected when credentials *and* both model IDs are present; otherwise the
    notebook falls back to mock mode and says so, as the brief requires.
    """
    env = os.environ if env is None else env
    explicit = _get_str(env, "STOCKROOM_MODE", None)
    if explicit is None:
        merged = dict(env)
        have_models = bool(_get_str(env, "AGENT_MODEL_ID", None)) and bool(
            _get_str(env, "JUDGE_MODEL_ID", None)
        )
        if aws_credentials_available() and have_models:
            merged["STOCKROOM_MODE"] = Mode.LIVE.value
            reason = "AWS credentials and model IDs found"
        else:
            merged["STOCKROOM_MODE"] = Mode.MOCK.value
            reason = (
                "no AWS credentials"
                if not aws_credentials_available()
                else "AGENT_MODEL_ID / JUDGE_MODEL_ID not set"
            )
        cfg = StockroomConfig.from_env(merged)
    else:
        cfg = StockroomConfig.from_env(env)
        reason = f"STOCKROOM_MODE={explicit}"
    if cfg.is_live:
        cfg.require_live_models()
    if announce:
        print(f"[stockroom] running in {cfg.mode.value.upper()} mode ({reason})")
        print(f"[stockroom] {cfg.describe()}")
    return cfg
