"""The Day 1–3 → Day 4 hand-off (stockroom.handoff): save, load, fall back, fail clearly."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from stockroom.config import REPO_ROOT, Mode
from stockroom.evals.golden import ExpectedTool, GoldenCase, golden_by_id, load_calibration
from stockroom.handoff import (
    BUILDS,
    DEFAULT_HANDOFF_DIR,
    HANDOFF_ENV,
    REFERENCE_DIR,
    CalibrationSummary,
    Day1Handoff,
    Day2Handoff,
    Day3Handoff,
    HandoffArtefact,
    HandoffError,
    handoff_dir,
    load_handoff,
    markdown_table,
    provenance_table,
    save_handoff,
)

KINDS: tuple[type[HandoffArtefact], ...] = (Day1Handoff, Day2Handoff, Day3Handoff)


def _day1() -> Day1Handoff:
    case = GoldenCase(
        id="G900",
        category="order_status",
        query="Where is order ORD-1003?",
        expected_tools=[ExpectedTool(name="get_order_status", args={"order_id": "ORD-1003"})],
        expected_facts=["shipped"],
        reference_answer="Order ORD-1003 has shipped.",
    )
    return Day1Handoff(mode=Mode.MOCK, golden_case=case)


def _day3(**overrides: dict[str, list[str]]) -> Day3Handoff:
    verdicts = {build: (["G041"] if build == "injection_unguarded" else []) for build in BUILDS}
    fields = {"assertion_fails": verdicts, "guard_blocks": verdicts, **overrides}
    return Day3Handoff(mode=Mode.MOCK, assertion="assert_x", guard="guard_x", **fields)


@pytest.fixture
def own_dir(tmp_path: Path) -> Path:
    return tmp_path / "participant"


def test_round_trip_keeps_every_field(own_dir: Path) -> None:
    day1 = _day1()
    path = save_handoff(day1, own_dir)
    assert path == own_dir / "day1.json"
    loaded = load_handoff(Day1Handoff, own_dir)
    assert loaded.source == "participant"
    assert loaded.path == path == loaded.participant_path
    assert loaded.artefact == day1
    assert loaded.artefact.golden_case.expected_tools[0].args == {"order_id": "ORD-1003"}

    day3 = _day3()
    save_handoff(day3, own_dir)
    assert load_handoff(Day3Handoff, own_dir).artefact == day3


def test_calibration_summary_comes_from_a_report(own_dir: Path) -> None:
    from stockroom.evals.calibration import calibrate
    from stockroom.evals.judge import FakeJudge

    items = load_calibration()
    summary = CalibrationSummary.from_report(calibrate(FakeJudge("v2"), items))
    assert summary.rubric_version == "v2"
    assert summary.disagreements and all(d.startswith("C") for d in summary.disagreements)
    day2 = Day2Handoff(mode=Mode.MOCK, calibrations=[summary], calibration_item=items[0])
    save_handoff(day2, own_dir)
    assert load_handoff(Day2Handoff, own_dir).artefact == day2


@pytest.mark.parametrize("kind", KINDS, ids=lambda k: k.__name__)
def test_a_missing_file_falls_back_to_the_reference(
    own_dir: Path, kind: type[HandoffArtefact]
) -> None:
    loaded = load_handoff(kind, own_dir)
    assert loaded.source == "reference"
    assert loaded.path == REFERENCE_DIR / kind.filename()
    assert loaded.participant_path == own_dir / kind.filename()
    assert "REFERENCE fallback" in loaded.describe()
    assert "nothing saved at" in loaded.describe()


def test_one_saved_day_does_not_hide_the_others(own_dir: Path) -> None:
    save_handoff(_day1(), own_dir)
    sources = {kind.DAY: load_handoff(kind, own_dir).source for kind in KINDS}
    assert sources == {1: "participant", 2: "reference", 3: "reference"}


def test_the_shipped_reference_artefacts_fit_the_data() -> None:
    golden = golden_by_id()
    day1 = load_handoff(Day1Handoff, REPO_ROOT / "no-such-dir").artefact
    assert day1.golden_case.id not in golden
    day2 = load_handoff(Day2Handoff, REPO_ROOT / "no-such-dir").artefact
    assert day2.calibration_item.id not in {item.id for item in load_calibration()}
    assert {c.rubric_version for c in day2.calibrations} >= {"v2"}
    day3 = load_handoff(Day3Handoff, REPO_ROOT / "no-such-dir").artefact
    verdicts = [*day3.assertion_fails.values(), *day3.guard_blocks.values()]
    named = {cid for ids in verdicts for cid in ids}
    assert named <= set(golden)
    assert day3.assertion_fails["fixed"] == [] and day3.guard_blocks["fixed"] == []


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("{not json", "Invalid JSON"),
        ("", "Invalid JSON"),
        (json.dumps({"mode": "mock"}), "golden_case"),
        (json.dumps({"mode": "staging", "golden_case": {}}), "mode"),
    ],
    ids=["broken-json", "empty", "missing-field", "bad-values"],
)
def test_a_malformed_file_is_a_clear_error_not_a_fallback(
    own_dir: Path, content: str, message: str
) -> None:
    own_dir.mkdir(parents=True)
    (own_dir / "day1.json").write_text(content, encoding="utf-8")
    with pytest.raises(HandoffError, match="not a valid Day 1 hand-off file") as excinfo:
        load_handoff(Day1Handoff, own_dir)
    text = str(excinfo.value)
    assert "day1.json" in text and message in text
    assert "Re-run the save cell at the end of the Day 1 notebook" in text


def test_an_unreadable_file_is_a_clear_error(own_dir: Path) -> None:
    (own_dir / "day1.json").mkdir(parents=True)  # a directory where the file should be
    with pytest.raises(HandoffError, match="could not be read") as excinfo:
        load_handoff(Day1Handoff, own_dir)
    assert "Re-run the save cell at the end of the Day 1 notebook" in str(excinfo.value)


def test_day3_needs_every_build(own_dir: Path) -> None:
    with pytest.raises(ValueError, match="needs exactly the builds"):
        _day3(assertion_fails={"fixed": []})
    payload = json.loads(_day3().model_dump_json())
    payload["guard_blocks"]["staging"] = []
    own_dir.mkdir(parents=True)
    (own_dir / "day3.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(HandoffError, match="needs exactly the builds"):
        load_handoff(Day3Handoff, own_dir)


def test_unknown_fields_are_rejected(own_dir: Path) -> None:
    payload = json.loads(_day1().model_dump_json()) | {"golden_cases": []}
    own_dir.mkdir(parents=True)
    (own_dir / "day1.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(HandoffError, match="golden_cases"):
        load_handoff(Day1Handoff, own_dir)


def test_no_participant_file_and_no_reference_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(HandoffError, match="no Day 2 artefact"):
        load_handoff(Day2Handoff, tmp_path / "participant", reference_dir=tmp_path / "reference")


def test_the_reference_directory_is_readable_from_a_copy(tmp_path: Path, own_dir: Path) -> None:
    reference = tmp_path / "reference"
    shutil.copytree(REFERENCE_DIR, reference)
    loaded = load_handoff(Day3Handoff, own_dir, reference_dir=reference)
    assert loaded.source == "reference" and loaded.path.parent == reference


def test_handoff_dir_reads_the_environment(tmp_path: Path) -> None:
    assert handoff_dir({}) == DEFAULT_HANDOFF_DIR
    assert handoff_dir({HANDOFF_ENV: "  "}) == DEFAULT_HANDOFF_DIR
    assert handoff_dir({HANDOFF_ENV: str(tmp_path)}) == tmp_path
    assert handoff_dir({HANDOFF_ENV: "reports/elsewhere"}) == REPO_ROOT / "reports" / "elsewhere"


def test_save_uses_the_environment_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(HANDOFF_ENV, str(tmp_path / "from-env"))
    assert save_handoff(_day1()) == tmp_path / "from-env" / "day1.json"
    assert load_handoff(Day1Handoff).source == "participant"


def test_provenance_table_names_the_source_of_each_input(own_dir: Path) -> None:
    save_handoff(_day1(), own_dir)
    table = provenance_table([load_handoff(kind, own_dir) for kind in KINDS])
    lines = table.splitlines()
    assert lines[0] == "| day | artefact | source | file | saved (UTC) | mode |"
    assert len(lines) == 2 + len(KINDS)
    assert "| participant |" in lines[2] and "| reference |" in lines[3]
    assert "data/handoff/day2.json" in lines[3]


def test_markdown_table_escapes_cells() -> None:
    assert markdown_table([]) == ""
    table = markdown_table([{"a": "x|y", "b": "two\nlines"}])
    assert table.splitlines() == ["| a | b |", "|---|---|", "| x\\|y | two lines |"]
