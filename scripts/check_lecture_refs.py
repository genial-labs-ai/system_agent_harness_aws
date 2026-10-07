#!/usr/bin/env python
"""Check that lectures, slides and the instructor guide only reference things that exist.

Scans ``lectures/*.md``, ``slides/*.md`` and ``docs/INSTRUCTOR_GUIDE.md`` and verifies:

* **Paths.** Every token that looks like ``src/...py``, ``scripts/...py``, ``tests/...py``,
  ``notebooks/...ipynb`` or ``data/...`` exists on disk. Notebook paths are only *warnings* while
  ``notebooks/src/`` is empty or missing (the notebooks are generated in a later phase).
* **Symbols.** Every backticked identifier that looks like a Python symbol — ``name()``,
  ``ClassName``, ``ClassName.method()``, ``ClassName.attr``, ``snake_case_name`` (4+ chars, with an
  underscore), ``ALL_CAPS_NAME`` — is defined somewhere under ``src/stockroom``, ``scripts/`` or
  ``tests/``. Definitions are collected with :mod:`ast`: functions, async functions, classes,
  methods, class-body and module-level assignments, dotted module paths, and string literals that
  are valid identifiers (metric names, env-var names, weakness flags, tool names).

Shell commands and non-Python terms (``make ci``, ``uv sync``, ``npx``, AWS metric IDs such as
``Builtin.Correctness``) are skipped via :data:`IGNORE` / :data:`IGNORE_PATTERNS`. Symbol checks
skip fenced code blocks (Mermaid, bash, JSON); path checks apply everywhere.

Exit status 1 when any reference is unresolved; each one is printed as ``file:line``.
"""

from __future__ import annotations

import argparse
import ast
import builtins
import re
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC_GLOBS: tuple[str, ...] = ("lectures/*.md", "slides/*.md", "docs/INSTRUCTOR_GUIDE.md")
CODE_DIRS: tuple[str, ...] = ("src/stockroom", "scripts", "tests")
# Configuration files whose keys and string values (metric names, suite/category labels, gate
# names) lectures may cite verbatim.
CONFIG_FILES: tuple[str, ...] = ("eval_thresholds.yaml", "promptfooconfig.yaml")
# Where a bare file name such as ``check_thresholds.py`` or ``pyproject.toml`` may live.
FILE_DIRS: tuple[str, ...] = (".", "scripts", "tests", "docs", "docs/aws", "data", "data/golden")

# Backticked terms that are not Python symbols (shell commands, GitHub/AWS identifiers).
IGNORE: frozenset[str] = frozenset(
    {
        "make",
        "make ci",
        "make test",
        "make eval",
        "make setup",
        "make baseline",
        "make promptfoo",
        "make notebooks",
        "make build-notebooks",
        "make mcp-server",
        "make thresholds",
        "make validate-data",
        "uv",
        "uv sync",
        "npx",
        "ruff",
        "pytest",
        "nbmake",
        "jupytext",
        "marp",
        "promptfoo",
        "boto3",
        "mcp",
        "deepeval",
        "ragas",
        "anyio",
        "asyncio",
        "pydantic",
        "jsonschema",
        "PROMPTFOO_PYTHON",
        "AWS_OIDC_ROLE_ARN",
        "AWS_DEFAULT_REGION",
        "GITHUB_STEP_SUMMARY",
        "UV_FROZEN",
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "KNOWLEDGE_BASE_ID",
        "S3_PREFIX",
        "EvaluatorId",
        "SessionSpans",
        "Evaluation",  # bedrock_agentcore_starter_toolkit client shown in the AWS docs
        "CreateEvaluationJob",
        "GetEvaluationJob",
        "ListEvaluationJobs",
        "PassRole",
        "pull_request",
        "workflow_dispatch",
        "cancel-in-progress",
        "LLMTestCase",
        "ToolCall",
        "GEval",
        "ToolCorrectnessMetric",
        "SingleTurnParams",
        "InvokeHarness",
        "Evaluate",
        "Transaction Search",
        "CloudWatch",
        "OpenTelemetry",
        "OpenInference",
        "Strands",
        "LangGraph",
        "Dogwood",
        "Cedar",
        "JSONL",
        "JSON",
        "YAML",
        "OIDC",
        "IAM",
        "ARN",
        "MCP",
        "RAG",
        "SDK",
        "CLI",
        "TUI",
        "ADOT",
        "OTEL",
        "BM25",
        "SKU",
        "ORD",
        "RSR",
    }
)
IGNORE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^Builtin\.[A-Za-z]+$"),  # Bedrock / AgentCore built-in metric and evaluator IDs
    re.compile(r"^(bedrock|s3|sts|iam|logs|cloudwatch):"),  # IAM actions
    re.compile(r"^arn:"),
    re.compile(r"^(aws|agentcore|gh|git|uv|npx|make|python|pytest|ruff|docker)\b"),  # shell
    re.compile(r"^[A-Z]{2,5}$"),  # bare acronyms (CI, PR, S3, KB, ...)
    re.compile(r"^(us|eu|apac|global)\.[a-z0-9.\-]+:\d+$"),  # inference profile IDs
    re.compile(r"^(anthropic|amazon|meta|mistral|openai)\.[a-z0-9.\-]+(:\d+)?$"),  # model IDs
    re.compile(r"^(G|RT|C)\d{2,3}$"),  # golden / red-team / calibration case ids
    re.compile(r"^(SKU|ORD|RSR)-\d{4}$"),
    re.compile(r"^v\d+$"),
    re.compile(r"^(evaluationInput|evaluationTarget|evaluationResults|sessionSpans|traceIds)$"),
    re.compile(r"^(spanIds|evaluatorId|spanContext|modelResponses|referenceResponse)$"),
    re.compile(r"^(modelIdentifier|invoke_agent_runtime|get_inference_profile)$"),
    re.compile(r"^(role-to-assume|aws-region|role-session-name|id-token)$"),
    re.compile(
        r"^(OTEL|AWS|AGENT_OBSERVABILITY|PHOENIX|DEEPEVAL|PROMPTFOO|GITHUB)_[A-Z0-9_]+$"
    ),  # env vars
    re.compile(
        r"^(ragas|deepeval|mcp|opentelemetry|boto3|botocore|phoenix|openinference|jsonschema"
        r"|pydantic)\."
    ),  # third-party module paths
)

PATH_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("src", re.compile(r"(?<![\w/.\-])src/[\w/]+\.py")),
    ("scripts", re.compile(r"(?<![\w/.\-])scripts/[\w]+\.py")),
    ("tests", re.compile(r"(?<![\w/.\-])tests/[\w]+\.py")),
    ("notebooks", re.compile(r"(?<![\w/.\-])notebooks/(?:src/)?[\w]+\.(?:ipynb|py)")),
    ("data", re.compile(r"(?<![\w/.\-])data/[\w/.\-]+")),
)
BACKTICK = re.compile(r"`([^`\n]+)`")
SYMBOL = re.compile(r"^(?P<name>[A-Za-z_][\w.]*)(?P<call>\(\))?$")
CAMEL = re.compile(r"^[A-Z][A-Za-z0-9]*$")
SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")
UPPER = re.compile(r"^[A-Z][A-Z0-9_]*$")
IDENT = re.compile(r"^[A-Za-z_]\w*$")
FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass
class Definitions:
    """Everything a lecture may legitimately name, harvested from the code base."""

    functions: set[str] = field(default_factory=set)
    classes: set[str] = field(default_factory=set)
    members: dict[str, set[str]] = field(default_factory=dict)
    assignments: set[str] = field(default_factory=set)
    modules: set[str] = field(default_factory=set)
    strings: set[str] = field(default_factory=set)
    imports: set[str] = field(default_factory=set)
    # Attribute accesses, parameter names and keyword arguments the code base uses: third-party
    # API surface the lectures may cite (``structured_content``, ``tools_called``, ...).
    attributes: set[str] = field(default_factory=set)
    files: set[str] = field(default_factory=set)

    @property
    def names(self) -> set[str]:
        all_members = set().union(*self.members.values()) if self.members else set()
        return self.functions | self.classes | self.assignments | all_members | self.imports

    def has_name(self, name: str) -> bool:
        return name in self.names or name in self.strings or name in self.attributes

    def has_callable(self, name: str) -> bool:
        return name in self.functions or name in self.classes or name in self.attributes

    def has_member(self, owner: str, member: str) -> bool:
        return member in self.members.get(owner, set())


def _targets(node: ast.AST) -> Iterator[str]:
    if isinstance(node, ast.Name):
        yield node.id
    elif isinstance(node, ast.Tuple | ast.List):
        for elt in node.elts:
            yield from _targets(elt)


def _collect_class(node: ast.ClassDef, defs: Definitions) -> None:
    defs.classes.add(node.name)
    members = defs.members.setdefault(node.name, set())
    for item in node.body:
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
            members.add(item.name)
            defs.functions.add(item.name)
        elif isinstance(item, ast.Assign):
            for target in item.targets:
                members.update(_targets(target))
        elif isinstance(item, ast.AnnAssign):
            members.update(_targets(item.target))
        elif isinstance(item, ast.ClassDef):
            _collect_class(item, defs)


def collect_definitions(root: Path, code_dirs: Iterable[str]) -> Definitions:
    defs = Definitions()
    for rel in code_dirs:
        base = root / rel
        for path in sorted(base.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            dotted = path.relative_to(root).with_suffix("")
            parts = [p for p in dotted.parts if p != "src"]
            if parts[-1] == "__init__":
                parts = parts[:-1]
            for i in range(1, len(parts) + 1):
                defs.modules.add(".".join(parts[:i]))
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    defs.functions.add(node.name)
                elif isinstance(node, ast.ClassDef):
                    _collect_class(node, defs)
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        defs.assignments.update(_targets(target))
                elif isinstance(node, ast.AnnAssign):
                    defs.assignments.update(_targets(node.target))
                elif isinstance(node, ast.Import | ast.ImportFrom):
                    # Third-party names the code base imports (``MCPServer``, ``Client``, ...)
                    # may be cited by the lectures as well.
                    defs.imports.update(a.asname or a.name.split(".")[0] for a in node.names)
                elif isinstance(node, ast.Attribute):
                    defs.attributes.add(node.attr)
                elif isinstance(node, ast.arg | ast.keyword) and node.arg:
                    defs.attributes.add(node.arg)
                elif (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and IDENT.match(node.value)
                ):
                    defs.strings.add(node.value)
    defs.assignments.update(n for n in dir(builtins) if not n.startswith("_"))
    return defs


def _yaml_strings(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str):
                yield key
            yield from _yaml_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _yaml_strings(item)
    elif isinstance(value, str):
        yield value


def collect_config_vocabulary(root: Path, files: Iterable[str], defs: Definitions) -> None:
    """Keys and string values of the YAML configs (metric names, suites, categories)."""
    for rel in files:
        path = root / rel
        if not path.exists():
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        defs.strings.update(s for s in _yaml_strings(data) if IDENT.match(s))


def collect_file_names(root: Path, dirs: Iterable[str], defs: Definitions) -> None:
    """Bare file names (``check_thresholds.py``, ``pyproject.toml``) that may be cited."""
    for rel in dirs:
        base = root / rel
        if base.is_dir():
            defs.files.update(p.name for p in base.iterdir() if p.is_file())


@dataclass(frozen=True)
class Finding:
    file: Path
    line: int
    reference: str
    reason: str
    severity: str  # "error" | "warning"

    def render(self, root: Path) -> str:
        rel = self.file.relative_to(root)
        return f"{rel}:{self.line}: {self.severity}: `{self.reference}` {self.reason}"


def is_ignored(term: str) -> bool:
    return term in IGNORE or any(p.match(term) for p in IGNORE_PATTERNS)


def looks_like_symbol(name: str, called: bool) -> bool:
    """Decide whether a backticked token should be resolved as a Python symbol."""
    if called:
        return True
    if "." in name:
        return all(IDENT.match(part) for part in name.split("."))
    if CAMEL.match(name) and any(c.islower() for c in name):
        return True
    if UPPER.match(name) and ("_" in name or len(name) >= 4):
        return True
    return bool(SNAKE.match(name) and "_" in name and len(name) >= 4)


def resolve_symbol(name: str, called: bool, defs: Definitions) -> str | None:
    """Return ``None`` when ``name`` resolves, else a short reason."""
    if "." not in name:
        if called and defs.has_callable(name):
            return None  # function, method, or a constructor call such as ``Harness()``
        if not called and defs.has_name(name):
            return None
        kind = "function or method" if called else "symbol"
        return f"is not a known {kind}"
    if name in defs.modules or name in defs.files:
        return None
    owner, _, member = name.rpartition(".")
    head = owner.split(".")[0]
    if head in defs.classes:
        if defs.has_member(head, member) or (owner != head and defs.has_name(member)):
            return None
        return f"{member!r} is not a member of class {head}"
    if owner in defs.modules and (defs.has_name(member) or member in defs.classes):
        return None
    if member in defs.functions or defs.has_name(member):
        return None  # attribute access on a local variable, e.g. ``run.tool_names``
    return f"{member!r} is not a known attribute, method or function"


def iter_doc_files(root: Path, globs: Iterable[str]) -> list[Path]:
    files: list[Path] = []
    for pattern in globs:
        files.extend(sorted(root.glob(pattern)))
    return files


def check_file(path: Path, root: Path, defs: Definitions, notebooks_ready: bool) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[tuple[int, str]] = set()
    in_fence = False
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        for kind, pattern in PATH_PATTERNS:
            for match in pattern.finditer(line):
                ref = match.group(0).rstrip(".")
                if (lineno, ref) in seen:
                    continue
                seen.add((lineno, ref))
                if (root / ref).exists():
                    continue
                if kind == "notebooks" and not notebooks_ready:
                    reason, severity = "does not exist yet (notebooks/src is empty)", "warning"
                else:
                    reason, severity = "does not exist on disk", "error"
                findings.append(Finding(path, lineno, ref, reason, severity))
        if in_fence:
            continue
        for match in BACKTICK.finditer(line):
            term = match.group(1).strip()
            if is_ignored(term):
                continue
            sym = SYMBOL.match(term)
            if sym is None:
                continue
            name, called = sym.group("name"), bool(sym.group("call"))
            if "/" in name or not looks_like_symbol(name, called):
                continue
            if (lineno, term) in seen:
                continue
            seen.add((lineno, term))
            reason = resolve_symbol(name, called, defs)
            if reason is not None:
                findings.append(Finding(path, lineno, term, reason, "error"))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="repository root")
    parser.add_argument(
        "--strict-notebooks",
        action="store_true",
        help="treat missing notebooks as errors even when notebooks/src is empty",
    )
    parser.add_argument("--quiet", action="store_true", help="only print unresolved references")
    args = parser.parse_args(argv)
    root: Path = args.root.resolve()

    docs = iter_doc_files(root, DOC_GLOBS)
    if not docs:
        print(f"no documents found under {root} for {DOC_GLOBS}", file=sys.stderr)
        return 1
    defs = collect_definitions(root, CODE_DIRS)
    collect_config_vocabulary(root, CONFIG_FILES, defs)
    collect_file_names(root, FILE_DIRS, defs)
    nb_src = root / "notebooks" / "src"
    notebooks_ready = args.strict_notebooks or (nb_src.is_dir() and any(nb_src.glob("*.py")))

    findings: list[Finding] = []
    for doc in docs:
        findings.extend(check_file(doc, root, defs, notebooks_ready))
    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]
    for finding in findings:
        print(finding.render(root))
    if not args.quiet:
        print(
            f"checked {len(docs)} document(s) against {len(defs.names)} symbols, "
            f"{len(defs.modules)} modules and {len(defs.strings)} string constants: "
            f"{len(errors)} error(s), {len(warnings)} warning(s)"
        )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
