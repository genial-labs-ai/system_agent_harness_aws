"""scripts/check_style.py: titles, terms, Node versions and links against docs/workshop.yml."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from stockroom.config import REPO_ROOT


@pytest.fixture(scope="module")
def style():
    spec = importlib.util.spec_from_file_location(
        "check_style", REPO_ROOT / "scripts" / "check_style.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve string annotations through sys.modules
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def repo_copy(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A copy of the repository's sources (no venv, node_modules, builds or git metadata)."""
    root = tmp_path_factory.mktemp("repo") / "repo"
    shutil.copytree(
        REPO_ROOT,
        root,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            "node_modules",
            "_site",
            ".quarto",
            ".claude",
            "reports",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
        ),
    )
    return root


def test_the_repository_is_clean(style) -> None:
    problems = style.check(REPO_ROOT)
    assert problems == [], "\n".join(str(p) for p in problems)


@pytest.mark.parametrize(
    "title",
    [
        "LLM Evaluation Foundations: From Vibes to Measured Agents",
        "LLM-as-a-Judge, Calibration, and OpenTelemetry Traces",
        "Building a Custom Agent Harness, Mocking Tools over MCP, and the Managed Alternative",
        "Day 1 · Why Your AI Agent Fails in Production (and How Evals Fix It)",
        "The Numbers the Repo Produces (Mock Mode, G043, `MAX_STEPS=8`)",
        'Myth 1: "If the final answer is right, the agent is right"',
        "Non-deterministic Runs and Live-Mode Workflows",
        "Instructor Guide — Evaluating Autonomous Agents",
    ],
)
def test_title_case_accepts(style, title: str) -> None:
    assert style.title_case_problems(title) == []


@pytest.mark.parametrize(
    ("title", "flagged"),
    [
        ("LLM evaluation foundations", ["evaluation", "foundations"]),
        ("LLM-as-a-judge", ["LLM-as-a-judge"]),
        ("Four Seeded Weaknesses, behind Feature Flags", ["behind"]),
        ("AWS Policies for the Nightly Live-mode Workflow", ["Live-mode"]),
        ("Let's go", ["go"]),
        ("Production CI/CD: the Gate", ["the"]),
    ],
)
def test_title_case_flags(style, title: str, flagged: list[str]) -> None:
    assert style.title_case_problems(title) == flagged


def test_github_slugs(style) -> None:
    assert style.github_slug("Live mode on Amazon Bedrock") == "live-mode-on-amazon-bedrock"
    assert style.github_slug("3. The failure taxonomy, mapped to the repo") == (
        "3-the-failure-taxonomy-mapped-to-the-repo"
    )
    assert style.github_slug("`make ci` — the gate") == "make-ci--the-gate"


@pytest.fixture
def edit(repo_copy: Path) -> Iterator:
    """Replace text in a file of the copy for one test, then restore it."""
    originals: dict[Path, str] = {}

    def apply(rel: str, old: str, new: str) -> None:
        path = repo_copy / rel
        current = path.read_text(encoding="utf-8")
        originals.setdefault(path, current)
        assert old in current, f"{old!r} not in {rel}"
        path.write_text(current.replace(old, new, 1), encoding="utf-8")

    yield apply
    for path, text in originals.items():
        path.write_text(text, encoding="utf-8")


def _rules(style, root: Path) -> set[tuple[str, str]]:
    return {(p.rule, p.file) for p in style.check(root)}


def test_the_copy_starts_clean(style, repo_copy: Path) -> None:
    assert style.check(repo_copy) == []


@pytest.mark.parametrize(
    ("rel", "old", "new", "rule"),
    [
        (
            "lectures/day1_llm_eval_foundations.md",
            "# Day 1 — LLM Evaluation Foundations: From Vibes to Measured Agents",
            "# Day 1 — LLM evaluation foundations: from vibes to measured agents",
            "canonical-title",
        ),
        (
            "_quarto.yml",
            '"Day 2 · Judge Calibration and OTel Traces"',
            '"Day 2 · Judge calibration and OTel traces"',
            "canonical-title",
        ),
        ("SECURITY.md", "# Security Policy", "# Security policy", "page-title"),
        (
            "slides/intro.qmd",
            "## The Thesis",
            "## The thesis",
            "title-case",
        ),
        ("README.md", "teach from it", "teach from it on Github", "banned-spelling"),
        (
            "lectures/day2_llm_as_a_judge_and_otel.md",
            "OpenTelemetry SDK",
            "OTEL SDK",
            "banned-spelling",
        ),
        ("README.md", "(LICENSE)", "(LICENCE)", "broken-link"),
        ("README.md", "(#modes)", "(#modes-of-operation)", "broken-link"),
        (
            "index.qmd",
            "Node 22 (the version CI uses; 20 or newer works)",
            "Node 18",
            "node-version",
        ),
        ("package.json", '"node": ">=20"', '"node": ">=18"', "node-version"),
    ],
    ids=[
        "lecture-h1",
        "menu-label",
        "page-h1",
        "slide-title",
        "product-spelling",
        "otel-in-prose",
        "missing-file",
        "missing-anchor",
        "node-in-prose",
        "node-engines",
    ],
)
def test_deliberate_mistakes_are_caught(
    style, repo_copy: Path, edit, rel: str, old: str, new: str, rule: str
) -> None:
    edit(rel, old, new)
    assert (rule, rel) in _rules(style, repo_copy)


def test_code_spans_and_fences_are_not_prose(style, repo_copy: Path, edit) -> None:
    edit("README.md", "instructors teach from it", "instructors teach from it on `Github`")
    edit("CONTRIBUTING.md", "# Contributing\n", "# Contributing\n\n```text\nGithub OTEL\n```\n")
    assert style.check(repo_copy) == []


def test_an_exemption_that_matches_nothing_is_an_error(style, repo_copy: Path, edit) -> None:
    edit(
        "docs/workshop.yml",
        "exemptions: []",
        "exemptions:\n"
        "  - file: lectures/day4_aws_ci_cd_redteaming.md\n"
        "    rule: banned-spelling\n"
        '    text: "Red-teaming"\n'
        '    reason: "fixed long ago"',
    )
    assert ("exemption", "docs/workshop.yml") in _rules(style, repo_copy)


def test_cli_exit_codes(style, repo_copy: Path, edit, capsys: pytest.CaptureFixture[str]) -> None:
    assert style.main(["--root", str(repo_copy)]) == 0
    edit("README.md", "instructors teach from it", "instructors teach from it on Github")
    assert style.main(["--root", str(repo_copy)]) == 1
    out = capsys.readouterr().out
    assert "[banned-spelling]" in out and "check_style: 1 problem(s) in 1 file(s)" in out
