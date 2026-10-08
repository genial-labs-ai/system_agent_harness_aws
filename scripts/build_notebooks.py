#!/usr/bin/env python
"""Build the student and solution notebooks from the jupytext percent sources.

``notebooks/src/day<N>_*.py`` are the source of truth. Each is read with jupytext and split into
two ``.ipynb`` files:

* ``notebooks/<Name>.ipynb`` (student): every untagged cell, the ``exercise`` cells (participant
  skeletons) and the ``check`` cells. ``solution`` cells are dropped.
* ``notebooks/solutions/<Name>.ipynb``: the same, but each ``exercise`` cell is replaced by the
  ``solution`` cell that follows it. ``check`` cells are kept so the solution is verified.

Cell tags come from the percent header, e.g. ``# %% tags=["exercise"]``. The builder also enforces
the repository rules: the participant marker (``TO`` + ``DO``, split here so repository-wide greps
for it only match exercise cells) may only appear in ``exercise`` cells, every ``exercise`` cell
needs a ``solution`` cell, and generated notebooks are written without outputs with the default
``python3`` kernelspec so ``nbmake`` can execute them in any environment.

Exercise ids (``stockroom.exercises``): every ``check`` cell reports through
``exercise_pending("dayN.exM")`` and ``exercise_passed("dayN.exM", ...)`` with one id per exercise,
numbered 1..M in order, and the notebook's ``exercise_summary([...])`` cell lists exactly those ids.
That is what lets ``make notebooks`` run the solutions in strict mode and fail on any exercise that
does not report passing.

Usage: ``uv run python scripts/build_notebooks.py [--src notebooks/src] [--out notebooks]``.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import jupytext
import nbformat

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "notebooks" / "src"
OUT_DIR = REPO_ROOT / "notebooks"
SOLUTIONS_SUBDIR = "solutions"
EXERCISE_MARKER = "TO" + "DO"

TAG_EXERCISE = "exercise"
TAG_SOLUTION = "solution"
TAG_CHECK = "check"
KNOWN_TAGS = {TAG_EXERCISE, TAG_SOLUTION, TAG_CHECK}

PASSED_CALL = re.compile(r'exercise_passed\(\s*"(day\d+\.ex\d+)"')
PENDING_CALL = re.compile(r'exercise_pending\(\s*"(day\d+\.ex\d+)"')
SUMMARY_CALL = re.compile(r"exercise_summary\(\s*\[([^\]]*)\]")

NOTEBOOK_NAMES: dict[str, str] = {
    "day1_deterministic_and_rag_evals": "Day1_Deterministic_and_RAG_Evals",
    "day2_judge_calibration_and_otel_traces": "Day2_Judge_Calibration_and_OTEL_Traces",
    "day3_building_custom_agent_harness": "Day3_Building_Custom_Agent_Harness",
    "day4_bedrock_evaluations_and_ci_gating": "Day4_Bedrock_Evaluations_and_CI_Gating",
}

KERNELSPEC = {"name": "python3", "display_name": "Python 3", "language": "python"}
LANGUAGE_INFO = {"name": "python"}


class BuildError(ValueError):
    """A source notebook violates the tagging rules."""


@dataclass
class BuildReport:
    source: Path
    student: Path
    solution: Path
    cells_total: int
    exercises: int


def cell_tags(cell: nbformat.NotebookNode) -> set[str]:
    return set(cell.get("metadata", {}).get("tags", []))


def validate_source(nb: nbformat.NotebookNode, source: Path) -> int:
    """Check the tagging rules and return the number of exercises."""
    exercises = 0
    pending_solution = False
    for idx, cell in enumerate(nb.cells):
        tags = cell_tags(cell)
        unknown = tags - KNOWN_TAGS
        if unknown:
            raise BuildError(f"{source.name} cell {idx}: unknown tag(s) {sorted(unknown)}")
        if len(tags & KNOWN_TAGS) > 1:
            raise BuildError(f"{source.name} cell {idx}: a cell may carry only one of {KNOWN_TAGS}")
        if EXERCISE_MARKER in cell.source and TAG_EXERCISE not in tags:
            raise BuildError(
                f"{source.name} cell {idx}: '{EXERCISE_MARKER}' is only allowed in exercise cells"
            )
        if TAG_EXERCISE in tags:
            if cell.cell_type != "code":
                raise BuildError(f"{source.name} cell {idx}: exercise cells must be code cells")
            if pending_solution:
                raise BuildError(
                    f"{source.name} cell {idx}: exercise started before the previous one had a "
                    "solution cell"
                )
            exercises += 1
            pending_solution = True
        elif TAG_SOLUTION in tags:
            if not pending_solution:
                raise BuildError(
                    f"{source.name} cell {idx}: solution cell without a preceding exercise cell"
                )
            if cell.cell_type != "code":
                raise BuildError(f"{source.name} cell {idx}: solution cells must be code cells")
            pending_solution = False
    if pending_solution:
        raise BuildError(f"{source.name}: the last exercise has no solution cell")
    validate_exercise_ids(nb, source, exercises)
    return exercises


def validate_exercise_ids(nb: nbformat.NotebookNode, source: Path, exercises: int) -> None:
    """Check cells report under stable ids ``dayN.ex1..exM``; the summary cell lists them all."""
    day = re.match(r"day(\d+)_", source.name)
    if day is None:
        raise BuildError(f"{source.name}: source names must start with 'day<N>_'")
    expected = [f"day{day[1]}.ex{i}" for i in range(1, exercises + 1)]
    reported: list[str] = []
    summaries: list[list[str]] = []
    for idx, cell in enumerate(nb.cells):
        if cell.cell_type != "code":
            continue
        if TAG_CHECK in cell_tags(cell):
            passed = PASSED_CALL.findall(cell.source)
            pending = PENDING_CALL.findall(cell.source)
            if len(set(passed)) != 1 or set(pending) != set(passed):
                raise BuildError(
                    f"{source.name} cell {idx}: a check cell must call exercise_pending() and "
                    "exercise_passed() with the same single exercise id"
                )
            reported.append(passed[0])
        summaries += [re.findall(r'"([^"]+)"', m) for m in SUMMARY_CALL.findall(cell.source)]
    if reported != expected:
        raise BuildError(
            f"{source.name}: check cells report {reported}, expected {expected} "
            "(one check cell per exercise, in order)"
        )
    if summaries != [expected]:
        raise BuildError(
            f"{source.name}: expected one exercise_summary({expected}) cell, found {summaries}"
        )


def strip_cell(cell: nbformat.NotebookNode) -> nbformat.NotebookNode:
    """A copy of ``cell`` with no outputs or execution counts (diff-friendly, nbmake-ready)."""
    clean = nbformat.from_dict(cell)
    if clean.cell_type == "code":
        clean["outputs"] = []
        clean["execution_count"] = None
    tags = sorted(cell_tags(cell))
    clean["metadata"] = {"tags": tags} if tags else {}  # drops jupytext layout keys
    return clean


def stable_id(cell: nbformat.NotebookNode, seen: set[str]) -> str:
    """A cell id derived from the cell's content, so rebuilding unchanged sources is a no-op."""
    digest = hashlib.sha1(cell.source.encode("utf-8")).hexdigest()[:8]
    candidate = f"{cell.cell_type[:2]}-{digest}"
    suffix = 1
    while candidate in seen:
        suffix += 1
        candidate = f"{cell.cell_type[:2]}-{digest}-{suffix}"
    seen.add(candidate)
    return candidate


def select_cells(nb: nbformat.NotebookNode, variant: str) -> list[nbformat.NotebookNode]:
    drop = TAG_SOLUTION if variant == "student" else TAG_EXERCISE
    seen: set[str] = set()
    cells = []
    for cell in nb.cells:
        if drop in cell_tags(cell):
            continue
        clean = strip_cell(cell)
        clean["id"] = stable_id(clean, seen)
        cells.append(clean)
    return cells


def make_notebook(
    cells: list[nbformat.NotebookNode], title: str, variant: str
) -> nbformat.NotebookNode:
    nb = nbformat.v4.new_notebook()
    nb.cells = cells
    nb.metadata = {
        "kernelspec": dict(KERNELSPEC),
        "language_info": dict(LANGUAGE_INFO),
        "stockroom": {"notebook": title, "variant": variant, "generated_by": "build_notebooks.py"},
    }
    nbformat.validate(nb)
    return nb


def build_one(source: Path, out_dir: Path) -> BuildReport:
    stem = source.stem
    if stem not in NOTEBOOK_NAMES:
        raise BuildError(
            f"{source.name}: no notebook name registered for '{stem}' "
            f"(known: {sorted(NOTEBOOK_NAMES)})"
        )
    title = NOTEBOOK_NAMES[stem]
    nb = jupytext.reads(source.read_text(encoding="utf-8"), fmt="py:percent")
    exercises = validate_source(nb, source)

    student_path = out_dir / f"{title}.ipynb"
    solution_path = out_dir / SOLUTIONS_SUBDIR / f"{title}.ipynb"
    solution_path.parent.mkdir(parents=True, exist_ok=True)
    nbformat.write(make_notebook(select_cells(nb, "student"), title, "student"), student_path)
    nbformat.write(make_notebook(select_cells(nb, "solution"), title, "solution"), solution_path)
    return BuildReport(
        source=source,
        student=student_path,
        solution=solution_path,
        cells_total=len(nb.cells),
        exercises=exercises,
    )


def build_all(src_dir: Path, out_dir: Path) -> list[BuildReport]:
    sources = sorted(src_dir.glob("day*_*.py"))
    if not sources:
        raise BuildError(f"no day*_*.py sources found in {src_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    return [build_one(source, out_dir) for source in sources]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--src", type=Path, default=SRC_DIR, help="directory of percent sources")
    parser.add_argument("--out", type=Path, default=OUT_DIR, help="output notebooks directory")
    args = parser.parse_args(argv)
    try:
        reports = build_all(args.src, args.out)
    except BuildError as exc:
        print(f"build_notebooks: {exc}", file=sys.stderr)
        return 1
    for r in reports:
        print(
            f"{r.source.name}: {r.cells_total} cells, {r.exercises} exercises -> "
            f"{r.student.relative_to(REPO_ROOT)} + {r.solution.relative_to(REPO_ROOT)}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
