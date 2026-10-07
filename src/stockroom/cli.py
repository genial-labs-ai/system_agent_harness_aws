"""Tiny CLI: ``stockroom config`` and ``stockroom run "question"``."""

from __future__ import annotations

import argparse
import json

from stockroom.agent.harness import Harness
from stockroom.config import StockroomConfig, TraceExporter
from stockroom.evals.otel_tracer import RunTracer, configure_tracing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stockroom", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("config", help="print the effective configuration")
    run = sub.add_parser("run", help="run one query through the harness")
    run.add_argument("query")
    run.add_argument("--case-id", default=None, help="golden case id (enables scripted mock turns)")
    run.add_argument("--json", action="store_true", help="print the full RunResult as JSON")
    run.add_argument("--trace", action="store_true", help="print spans to the console")
    args = parser.parse_args(argv)

    config = StockroomConfig.from_env()
    if args.command == "config":
        print(config.describe())
        return 0
    handle = configure_tracing(TraceExporter.CONSOLE if args.trace else config.trace_exporter)
    harness = Harness(config, tracer=RunTracer(handle))
    result = harness.run(args.query, case_id=args.case_id)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
    else:
        print(result.final_answer)
        print(
            f"-- {result.termination_reason.value} in {result.steps} steps; "
            f"tools: {result.tool_names}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
