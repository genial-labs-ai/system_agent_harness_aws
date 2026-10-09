"""The hand-off between lab days: what Days 1–3 save and the Day 4 capstone reads back.

At the end of the Day 1, 2 and 3 labs one notebook cell saves that day's artefact as a small JSON
file in the hand-off directory, ``reports/participant/`` (or ``STOCKROOM_HANDOFF_DIR``):

* ``day1.json`` (:class:`Day1Handoff`): the golden case from Day 1, Exercise 1;
* ``day2.json`` (:class:`Day2Handoff`): the judge calibration measured on Day 2 (rubric v2 and the
  participant's rubric from Exercise 1) and the calibration item rubric v2 gets wrong (Exercise 2);
* ``day3.json`` (:class:`Day3Handoff`): the golden cases the Day 3 trajectory assertion
  (Exercise 1) fails and the run guard (Exercise 2) blocks, on each of the five :data:`BUILDS`.

A notebook saves a day's file only once the exercises it comes from have passed. The Day 4
capstone reads each file with :func:`load_handoff`. A day with no file falls back to the reference
artefact in ``data/handoff/`` (what the solution notebooks save), and the returned :class:`Loaded`
says which of the two was used, so the capstone review can record it. A file that exists but does
not parse raises :class:`HandoffError` rather than falling back: otherwise a participant's work
would drop out of the review without anyone noticing.

The hand-off directory is gitignored with the rest of ``reports/``. Each participant's artefacts
are their own, and a fresh clone works without them.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from stockroom.config import DATA_DIR, REPO_ROOT, WEAKNESS_FLAGS, Mode
from stockroom.evals.calibration import CalibrationReport
from stockroom.evals.golden import CalibrationItem, GoldenCase

HANDOFF_ENV = "STOCKROOM_HANDOFF_DIR"
DEFAULT_HANDOFF_DIR: Path = REPO_ROOT / "reports" / "participant"
REFERENCE_DIR: Path = DATA_DIR / "handoff"
# The builds the Day 4 gate compares: the fixed agent and each seeded weakness on its own.
BUILDS: tuple[str, ...] = ("fixed", *sorted(WEAKNESS_FLAGS))

Source = Literal["participant", "reference"]


class HandoffError(ValueError):
    """A hand-off file exists but is not a valid artefact for its day."""


class HandoffArtefact(BaseModel):
    """Fields every saved artefact carries; subclasses set ``DAY`` and ``LABEL``."""

    model_config = ConfigDict(extra="forbid")

    DAY: ClassVar[int]
    LABEL: ClassVar[str]

    mode: Mode
    saved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def filename(cls) -> str:
        return f"day{cls.DAY}.json"


class Day1Handoff(HandoffArtefact):
    """Day 1, Exercise 1: a new golden case whose labels come from the data files."""

    DAY: ClassVar[int] = 1
    LABEL: ClassVar[str] = "golden case (Exercise 1)"

    golden_case: GoldenCase


class CalibrationSummary(BaseModel):
    """One rubric's agreement with the human labels on the Day 2 calibration set."""

    model_config = ConfigDict(extra="forbid")

    rubric_version: str
    agreement: float = Field(ge=0.0, le=1.0)
    kappa: float = Field(ge=-1.0, le=1.0)
    disagreements: list[str] = Field(default_factory=list)

    @classmethod
    def from_report(cls, report: CalibrationReport) -> CalibrationSummary:
        return cls(
            rubric_version=report.rubric_version,
            agreement=report.agreement,
            kappa=report.kappa,
            disagreements=[item_id for item_id, *_ in report.disagreements],
        )


class Day2Handoff(HandoffArtefact):
    """Day 2, Exercises 1–2: judge calibration per rubric and an item rubric v2 gets wrong."""

    DAY: ClassVar[int] = 2
    LABEL: ClassVar[str] = "judge calibration and item (Exercises 1–2)"

    calibrations: list[CalibrationSummary] = Field(min_length=1)
    calibration_item: CalibrationItem


class Day3Handoff(HandoffArtefact):
    """Day 3, Exercises 1–2: what the trajectory assertion and the run guard catch per build."""

    DAY: ClassVar[int] = 3
    LABEL: ClassVar[str] = "assertion and run guard verdicts (Exercises 1–2)"

    assertion: str
    guard: str
    assertion_fails: dict[str, list[str]]  # build -> golden case ids whose run fails the assertion
    guard_blocks: dict[str, list[str]]  # build -> golden case ids where the guard blocked a call

    @field_validator("assertion_fails", "guard_blocks")
    @classmethod
    def _every_build(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        if set(value) != set(BUILDS):
            raise ValueError(f"needs exactly the builds {list(BUILDS)}, got {sorted(value)}")
        return value


@dataclass(frozen=True)
class Loaded[T: HandoffArtefact]:
    """An artefact and where it came from: the participant's file or the reference fallback."""

    artefact: T
    source: Source
    path: Path  # the file that was read
    participant_path: Path  # where the participant's file is (or would be)

    def describe(self) -> str:
        """One line for the notebook: which day, whose artefact, which file."""
        head = f"Day {self.artefact.DAY} {self.artefact.LABEL}"
        if self.source == "participant":
            return (
                f"{head}: participant, {display_path(self.path)} "
                f"(saved {self.artefact.saved_at:%Y-%m-%d %H:%M} UTC, {self.artefact.mode} mode)"
            )
        return (
            f"{head}: REFERENCE fallback, {display_path(self.path)} "
            f"(nothing saved at {display_path(self.participant_path)})"
        )

    def provenance(self) -> dict[str, str]:
        """A row for the provenance table in the capstone review."""
        return {
            "day": str(self.artefact.DAY),
            "artefact": self.artefact.LABEL,
            "source": self.source,
            "file": display_path(self.path),
            "saved (UTC)": f"{self.artefact.saved_at:%Y-%m-%d %H:%M}",
            "mode": str(self.artefact.mode),
        }


def handoff_dir(env: Mapping[str, str] | None = None) -> Path:
    """``STOCKROOM_HANDOFF_DIR`` if set (relative to the repository root), else the default."""
    value = (os.environ if env is None else env).get(HANDOFF_ENV, "").strip()
    if not value:
        return DEFAULT_HANDOFF_DIR
    path = Path(value).expanduser()
    return path if path.is_absolute() else REPO_ROOT / path


def display_path(path: Path) -> str:
    """``path`` relative to the repository root when it lies inside it."""
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def save_handoff(artefact: HandoffArtefact, directory: Path | None = None) -> Path:
    """Write ``artefact`` to ``day<N>.json`` in the hand-off directory and return the path."""
    target = (directory or handoff_dir()) / artefact.filename()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(artefact.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return target


def read_handoff[T: HandoffArtefact](kind: type[T], path: Path) -> T:
    """Parse one hand-off file; any problem becomes a :class:`HandoffError` naming the file."""
    fix = (
        f"Re-run the save cell at the end of the Day {kind.DAY} notebook, or delete the file "
        "to use the reference artefact instead."
    )
    try:
        data = path.read_bytes()
    except OSError as exc:  # a directory of that name, permissions, an unmounted Drive folder
        raise HandoffError(f"{display_path(path)} could not be read ({exc}).\n{fix}") from exc
    try:
        return kind.model_validate_json(data)
    except ValidationError as exc:  # also covers a file that is not JSON at all
        raise HandoffError(
            f"{display_path(path)} is not a valid Day {kind.DAY} hand-off file: {exc}\n{fix}"
        ) from exc


def load_handoff[T: HandoffArtefact](
    kind: type[T], directory: Path | None = None, reference_dir: Path | None = None
) -> Loaded[T]:
    """The participant's artefact for ``kind``'s day if one was saved, else the reference one."""
    own = (directory or handoff_dir()) / kind.filename()
    if own.exists():
        return Loaded(read_handoff(kind, own), "participant", own, own)
    reference = (reference_dir or REFERENCE_DIR) / kind.filename()
    if not reference.exists():
        raise HandoffError(
            f"no Day {kind.DAY} artefact: nothing saved at {display_path(own)} and the reference "
            f"file {display_path(reference)} is missing from this checkout"
        )
    return Loaded(read_handoff(kind, reference), "reference", reference, own)


def markdown_table(rows: Sequence[Mapping[str, object]]) -> str:
    """A GitHub-flavoured Markdown table; the first row's keys are the header."""
    if not rows:
        return ""
    header = list(rows[0])

    def cell(value: object) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(cell(row.get(h, "")) for h in header) + " |" for row in rows]
    return "\n".join(lines)


def provenance_table(items: Sequence[Loaded[HandoffArtefact]]) -> str:
    """The Markdown table of which inputs were the participant's and which were reference."""
    return markdown_table([item.provenance() for item in items])
