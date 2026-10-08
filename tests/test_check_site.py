"""scripts/check_site.py: rendered pages, their titles and their local links."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

from stockroom.config import REPO_ROOT

WORKSHOP = "Evaluating Agents"
MANIFEST = {
    "workshop": {"short": WORKSHOP},
    "days": [
        {
            "day": 1,
            "lecture": {"file": "lectures/day1.md", "title": "Foundations"},
            "lab": {"notebook": "notebooks/Day1.ipynb", "title": "First Lab"},
        }
    ],
    "decks": [{"file": "slides/deck.md", "rendered": "slides/deck.html", "title": "Deck"}],
    "pages": [{"file": "docs/GUIDE.md", "title": "Guide"}],
}


@pytest.fixture(scope="module")
def site_check():
    spec = importlib.util.spec_from_file_location(
        "check_site", REPO_ROOT / "scripts" / "check_site.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve string annotations through sys.modules
    spec.loader.exec_module(module)
    return module


def _html(title: str, h1: str = "", body: str = "") -> str:
    return (
        f"<html><head><title>{title}</title></head><body>"
        f'<h1 class="title">{h1}</h1>{body}<h2 id="setup">Setup</h2></body></html>'
    )


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "workshop.yml").write_text(yaml.safe_dump(MANIFEST))
    pages = {
        "index.html": _html(WORKSHOP, body='<a href="lectures/day1.html#setup">day 1</a>'),
        "lectures/day1.html": _html(
            f"Day 1 · Foundations – {WORKSHOP}",
            "Foundations",
            '<a href="../notebooks/Day1.html">lab</a><a href="https://example.org/x">ext</a>'
            '<a href="#setup">top</a><img src="../site_libs/logo.svg">',
        ),
        "notebooks/Day1.html": _html(f"Day 1 · First Lab – {WORKSHOP}", "First Lab"),
        "notebooks/solutions/Day1.html": _html(f"Day 1 · First Lab – {WORKSHOP}", "First Lab"),
        "slides/deck.html": "<html><head><title>Deck</title></head></html>",
        "docs/GUIDE.html": _html(f"Guide – {WORKSHOP}", "Guide"),
        "site_libs/logo.svg": "<svg/>",
    }
    for rel, text in pages.items():
        path = tmp_path / "_site" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path


def test_a_complete_site_passes(site_check, root: Path) -> None:
    assert site_check.check(root) == []


def test_missing_pages_and_wrong_titles_are_reported(site_check, root: Path) -> None:
    (root / "_site" / "slides" / "deck.html").unlink()
    (root / "_site" / "docs" / "GUIDE.html").write_text(_html(f"Guide – {WORKSHOP}", "Guides"))
    (root / "_site" / "notebooks" / "Day1.html").write_text(
        _html(f"First Lab – {WORKSHOP}", "First Lab")
    )
    problems = {(p.rule, p.file) for p in site_check.check(root)}
    assert problems == {
        ("missing-page", "slides/deck.html"),
        ("page-title", "docs/GUIDE.html"),
        ("page-title", "notebooks/Day1.html"),
    }


def test_broken_links_and_anchors_are_reported(site_check, root: Path) -> None:
    page = root / "_site" / "lectures" / "day1.html"
    page.write_text(
        _html(
            f"Day 1 · Foundations – {WORKSHOP}",
            "Foundations",
            '<a href="../notebooks/Day9.html">gone</a><a href="../index.html#nowhere">anchor</a>'
            '<a href="../../README.md">outside</a><a href="mailto:a@example.org">mail</a>',
        )
    )
    messages = sorted(p.message for p in site_check.check(root) if p.rule == "broken-link")
    assert messages == [
        "../../README.md points outside the site",
        "../index.html#nowhere: no element with id 'nowhere'",
        "../notebooks/Day9.html does not exist",
    ]


def test_no_site_is_one_clear_problem(site_check, tmp_path: Path, root: Path) -> None:
    (problem,) = site_check.check(root, tmp_path / "missing")
    assert "run `make site` first" in problem.message


def test_layout_pages_lists_every_page_a_reader_opens(
    site_check, root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert site_check.main(["--root", str(root), "--layout-pages"]) == 0
    paths = [p["path"] for p in json.loads(capsys.readouterr().out)]
    assert paths == [
        "index.html",
        "docs/GUIDE.html",
        "lectures/day1.html",
        "notebooks/Day1.html",
        "notebooks/solutions/Day1.html",
    ]


def test_cli_exit_codes(site_check, root: Path) -> None:
    assert site_check.main(["--root", str(root)]) == 0
    (root / "_site" / "index.html").unlink()
    assert site_check.main(["--root", str(root)]) == 1
