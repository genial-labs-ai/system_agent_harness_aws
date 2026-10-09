"""Exercise bookkeeping for the lab notebooks: stable ids, pending vs passed, a completion summary.

Every graded exercise has a stable id of the form ``day<N>.ex<M>``. Its check cell calls
:func:`exercise_pending` while the participant's scaffold is untouched and
:func:`exercise_passed` once their code passes the check's assertions.

*Scaffold mode* (the default) only prints, so a student notebook runs top to bottom before
anything is solved. *Strict mode* (``STOCKROOM_STRICT_EXERCISES=1``; ``make notebooks`` sets it
for the solution notebooks, and participants can set it to self-assess) turns a pending exercise
into an error, so a broken solution can no longer pass by printing "not solved yet".
:func:`exercise_summary`, the last code cell of every notebook, prints the status of each exercise
and, in strict mode, fails if any of them did not report passing.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence

STRICT_ENV = "STOCKROOM_STRICT_EXERCISES"
EXERCISE_ID = re.compile(r"^day(?P<day>\d+)\.ex(?P<number>\d+)$")

PASSED = "passed"
PENDING = "not solved yet"
NOT_RUN = "not run"

_status: dict[str, str] = {}
_checked: dict[str, object] = {}


class ExerciseNotSolved(AssertionError):
    """Raised in strict mode when an exercise is pending or never reported passing."""


def strict_mode(env: Mapping[str, str] | None = None) -> bool:
    """True when ``STOCKROOM_STRICT_EXERCISES`` is set to a truthy value."""
    value = (os.environ if env is None else env).get(STRICT_ENV, "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _label(exercise_id: str) -> str:
    match = EXERCISE_ID.match(exercise_id)
    if match is None:
        raise ValueError(f"exercise id {exercise_id!r} must look like 'day4.ex2'")
    return f"Exercise {match['number']}"


def exercise_pending(exercise_id: str, detail: str = "", *, strict: bool | None = None) -> None:
    """Record that ``exercise_id`` is not solved yet; raise instead of printing in strict mode."""
    message = f"{_label(exercise_id)}: {PENDING}" + (f" ({detail})" if detail else "")
    _status[exercise_id] = PENDING
    _checked.pop(exercise_id, None)
    if strict_mode() if strict is None else strict:
        raise ExerciseNotSolved(f"{message} [{exercise_id}, strict mode]")
    print(message)


def exercise_passed(exercise_id: str, detail: str = "", *, checked: object = None) -> None:
    """Record that ``exercise_id`` passed its check and print the evidence.

    ``checked`` is the object the check validated (a case, a function, a class). The hand-off cells
    save that object, not whatever the participant's code returns later, so an edit made after the
    check passed is never saved as checked work.
    """
    _status[exercise_id] = PASSED
    _checked[exercise_id] = checked
    print(f"{_label(exercise_id)} passed" + (f": {detail}" if detail else ""))


def exercise_status(exercise_id: str) -> str:
    """``passed``, ``not solved yet`` or ``not run`` for one exercise in this kernel.

    The notebooks' hand-off cells use it to save a day's work only once its check has passed.
    """
    _label(exercise_id)  # validates the id
    return _status.get(exercise_id, NOT_RUN)


def exercise_checked(exercise_id: str) -> object:
    """The object ``exercise_id``'s check validated when it last passed (``None`` if it has not)."""
    _label(exercise_id)  # validates the id
    return _checked.get(exercise_id)


def exercise_summary(exercise_ids: Sequence[str], *, strict: bool | None = None) -> dict[str, str]:
    """Print one status line per exercise; in strict mode raise unless every one passed."""
    statuses = {eid: _status.get(eid, NOT_RUN) for eid in exercise_ids}
    for eid in exercise_ids:
        _label(eid)  # validates the id
    width = max((len(eid) for eid in exercise_ids), default=0)
    for eid, status in statuses.items():
        print(f"{eid.ljust(width)}  {status}")
    done = sum(1 for s in statuses.values() if s == PASSED)
    print(f"{done}/{len(statuses)} exercises passed")
    unfinished = [eid for eid, s in statuses.items() if s != PASSED]
    if unfinished and (strict_mode() if strict is None else strict):
        raise ExerciseNotSolved(f"not passed in strict mode: {', '.join(unfinished)}")
    return statuses


def reset_exercises() -> None:
    """Forget every recorded status (tests and re-runs of a notebook from the top)."""
    _status.clear()
    _checked.clear()
