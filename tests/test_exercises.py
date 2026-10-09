"""Exercise bookkeeping (stockroom.exercises) and the notebook builder's exercise-id rules."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path

import jupytext
import pytest

from stockroom.config import REPO_ROOT
from stockroom.exercises import (
    ExerciseNotSolved,
    exercise_checked,
    exercise_passed,
    exercise_pending,
    exercise_status,
    exercise_summary,
    reset_exercises,
    still_checked,
    strict_mode,
)


@pytest.fixture(autouse=True)
def _clean_status() -> Iterator[None]:
    reset_exercises()
    yield
    reset_exercises()


@pytest.fixture(scope="module")
def builder():
    spec = importlib.util.spec_from_file_location(
        "build_notebooks", REPO_ROOT / "scripts" / "build_notebooks.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve string annotations through sys.modules
    spec.loader.exec_module(module)
    return module


def test_strict_mode_reads_the_environment() -> None:
    assert strict_mode({"STOCKROOM_STRICT_EXERCISES": "1"})
    assert strict_mode({"STOCKROOM_STRICT_EXERCISES": "true"})
    assert not strict_mode({"STOCKROOM_STRICT_EXERCISES": "0"})
    assert not strict_mode({})


def test_scaffold_mode_prints_and_strict_mode_raises(capsys: pytest.CaptureFixture[str]) -> None:
    exercise_pending("day4.ex2", "no proposal yet", strict=False)
    assert capsys.readouterr().out.strip() == "Exercise 2: not solved yet (no proposal yet)"
    with pytest.raises(ExerciseNotSolved, match=r"day4\.ex2"):
        exercise_pending("day4.ex2", strict=True)


def test_summary_counts_and_fails_strictly_on_unfinished(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exercise_passed("day1.ex1", "ok")
    exercise_pending("day1.ex2", strict=False)
    statuses = exercise_summary(["day1.ex1", "day1.ex2", "day1.ex3"], strict=False)
    assert statuses == {"day1.ex1": "passed", "day1.ex2": "not solved yet", "day1.ex3": "not run"}
    assert "1/3 exercises passed" in capsys.readouterr().out
    with pytest.raises(ExerciseNotSolved, match=r"day1\.ex2, day1\.ex3"):
        exercise_summary(["day1.ex1", "day1.ex2", "day1.ex3"], strict=True)
    exercise_passed("day1.ex2")
    exercise_passed("day1.ex3")
    assert set(exercise_summary(["day1.ex1", "day1.ex2", "day1.ex3"], strict=True).values()) == {
        "passed"
    }


def test_exercise_ids_are_validated() -> None:
    with pytest.raises(ValueError, match=r"day4\.ex2"):
        exercise_passed("exercise-2")
    with pytest.raises(ValueError, match=r"day4\.ex2"):
        exercise_status("exercise-2")


def test_exercise_status_reports_one_exercise() -> None:
    assert exercise_status("day3.ex1") == "not run"
    exercise_pending("day3.ex1", strict=False)
    assert exercise_status("day3.ex1") == "not solved yet"
    exercise_passed("day3.ex1")
    assert exercise_status("day3.ex1") == "passed"


def test_the_checked_object_is_what_last_passed() -> None:
    # The hand-off cells save this object, so an edit after the check is never saved as checked.
    assert exercise_checked("day1.ex1") is None
    case = {"id": "G060"}
    exercise_passed("day1.ex1", checked=case)
    assert exercise_checked("day1.ex1") is case
    exercise_pending("day1.ex1", strict=False)  # a later failing check forgets it
    assert exercise_checked("day1.ex1") is None


def test_still_checked_needs_the_object_that_passed() -> None:
    # A re-check that fails on an assert records nothing, so "passed" survives it; the save cells
    # compare the object in the notebook now with the one that passed.
    def guard_v1() -> None:
        return None

    def guard_v2() -> None:
        return None

    assert not still_checked("day3.ex2", guard_v1)  # never passed
    exercise_passed("day3.ex2", checked=guard_v1)
    assert still_checked("day3.ex2", guard_v1)
    assert not still_checked("day3.ex2", guard_v2)  # edited and redefined after the check
    exercise_passed("day1.ex1", checked={"id": "G060"})
    assert still_checked("day1.ex1", {"id": "G060"})  # an equal value counts
    assert not still_checked("day1.ex1", {"id": "G061"})


def _source(check_body: str, summary: str = '["day9.ex1"]') -> str:
    return f"""# %%
from stockroom.exercises import exercise_passed, exercise_pending, exercise_summary

# %% tags=["exercise"]
ANSWER = None

# %% tags=["solution"]
ANSWER = 42

# %% tags=["check"]
{check_body}

# %%
exercise_summary({summary})
"""


GOOD_CHECK = """if ANSWER is None:
    exercise_pending("day9.ex1")
else:
    assert ANSWER == 42
    exercise_passed("day9.ex1", "42")"""


def _validate(builder, text: str, name: str = "day9_example.py") -> int:
    nb = jupytext.reads(text, fmt="py:percent")
    return builder.validate_source(nb, Path(name))


def test_builder_accepts_a_well_formed_source(builder) -> None:
    assert _validate(builder, _source(GOOD_CHECK)) == 1


@pytest.mark.parametrize(
    ("check", "summary", "message"),
    [
        (GOOD_CHECK.replace('exercise_pending("day9.ex1")', 'print("not solved")'), None, "same"),
        (GOOD_CHECK.replace("day9.ex1", "day9.ex2"), None, "expected"),
        (GOOD_CHECK, '["day9.ex1", "day9.ex2"]', "exercise_summary"),
    ],
    ids=["no-pending-call", "wrong-number", "summary-mismatch"],
)
def test_builder_rejects_broken_exercise_ids(
    builder, check: str, summary: str | None, message: str
) -> None:
    text = _source(check, summary) if summary else _source(check)
    with pytest.raises(builder.BuildError, match=message):
        _validate(builder, text)


def test_every_repository_notebook_follows_the_rules(builder) -> None:
    sources = sorted((REPO_ROOT / "notebooks" / "src").glob("day*_*.py"))
    assert len(sources) == 4
    for source in sources:
        nb = jupytext.reads(source.read_text(encoding="utf-8"), fmt="py:percent")
        assert builder.validate_source(nb, source) >= 3
