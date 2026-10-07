#!/usr/bin/env python
"""Print resolved versions of the libraries the workshop depends on (for the README pins table)."""

from __future__ import annotations

import importlib.metadata as md
import subprocess
import sys

PACKAGES = [
    "boto3",
    "botocore",
    "deepeval",
    "ragas",
    "mcp",
    "opentelemetry-sdk",
    "opentelemetry-exporter-otlp-proto-http",
    "opentelemetry-semantic-conventions",
    "openinference-semantic-conventions",
    "arize-phoenix",
    "arize-phoenix-otel",
    "aws-opentelemetry-distro",
    "jsonschema",
    "pydantic",
    "pyyaml",
    "pytest",
    "ruff",
    "jupytext",
    "nbmake",
    "ipykernel",
]


def version_of(name: str) -> str:
    try:
        return md.version(name)
    except md.PackageNotFoundError:
        return "not installed"


def main() -> int:
    print("| package | version |")
    print("|---|---|")
    print(f"| python | {sys.version.split()[0]} |")
    for name in PACKAGES:
        print(f"| {name} | {version_of(name)} |")
    for npm_pkg in ("promptfoo", "@marp-team/marp-cli"):
        try:
            out = subprocess.run(
                ["npm", "view", npm_pkg, "version"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            ver = out.stdout.strip() or "unknown"
        except (OSError, subprocess.SubprocessError):
            ver = "npm unavailable"
        print(f"| {npm_pkg} (npm, latest) | {ver} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
