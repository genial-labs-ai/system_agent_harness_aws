#!/usr/bin/env python
"""Check the rendered website in ``_site/`` before it is deployed (no browser needed).

``make site`` already fails when a page cannot render. This catches pages that render but are
wrong:

* ``missing-page`` - every lecture, lab notebook, solution notebook, slide deck and site page that
  ``docs/workshop.yml`` names was rendered into ``_site``;
* ``page-title`` - each lecture, notebook and site page has its canonical title as its H1, and the
  browser tab (``<title>``) reads "Day N · title – workshop" (site pages: "title – workshop",
  the landing page: the workshop's short name);
* ``broken-link`` - every local ``href`` and ``src`` resolves to a file in ``_site``, and every
  ``#fragment`` that points at a rendered page names an element id on that page.

Layout (overflow at phone width, diagrams drawn, light and dark themes) needs a browser; that is
``scripts/site_layout.mjs``. ``make site-check`` runs both after ``make site``.
Usage: ``uv run python scripts/check_site.py [--root PATH] [--site PATH]``; exits 1 on any problem.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path("docs/workshop.yml")
LINK_ATTRS = {"a": "href", "link": "href", "img": "src", "script": "src", "source": "src"}
SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class Problem:
    rule: str
    file: str
    message: str

    def __str__(self) -> str:
        return f"{self.file}: [{self.rule}] {self.message}"


@dataclass
class Page:
    """What the checks need from one rendered HTML file."""

    title: str = ""
    h1: list[str] = field(default_factory=list)
    ids: set[str] = field(default_factory=set)
    links: list[str] = field(default_factory=list)


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page = Page()
        self._in_title = False
        self._h1_depth = 0
        self._h1_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.page.ids.add(str(values["id"]))
        if tag == "a" and values.get("name"):
            self.page.ids.add(str(values["name"]))
        attr = LINK_ATTRS.get(tag)
        if attr and values.get(attr):
            self.page.links.append(str(values[attr]))
        if tag == "title":
            self._in_title = True
        if tag == "h1":
            self._h1_depth += 1
            self._h1_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag == "h1" and self._h1_depth:
            self._h1_depth -= 1
            self.page.h1.append(_squash("".join(self._h1_text)))

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.page.title += data
        if self._h1_depth:
            self._h1_text.append(data)


def _squash(text: str) -> str:
    return SPACES.sub(" ", text).strip()


def parse_page(path: Path) -> Page:
    parser = _PageParser()
    parser.feed(path.read_text(encoding="utf-8", errors="replace"))
    parser.page.title = _squash(parser.page.title)
    return parser.page


def rendered_path(source: str) -> str:
    """Where Quarto writes a source file inside ``_site`` (Markdown, Quarto and notebooks)."""
    return str(PurePosixPath(source).with_suffix(".html"))


@dataclass(frozen=True)
class Expected:
    """A page the manifest says must exist, with the title it must show (if any)."""

    path: str
    h1: str | None = None
    tab: str | None = None


def expected_pages(manifest: dict[str, Any]) -> list[Expected]:
    site = manifest["workshop"]["short"]
    pages: list[Expected] = []
    for day in manifest["days"]:
        n, lecture, lab = day["day"], day["lecture"], day["lab"]
        tab = f"Day {n} · {lecture['title']} – {site}"
        pages.append(Expected(rendered_path(lecture["file"]), lecture["title"], tab))
        tab = f"Day {n} · {lab['title']} – {site}"
        solutions = lab["notebook"].replace("notebooks/", "notebooks/solutions/", 1)
        for notebook in (lab["notebook"], solutions):
            pages.append(Expected(rendered_path(notebook), lab["title"], tab))
    for deck in manifest.get("decks", []):
        pages.append(Expected(deck.get("rendered") or rendered_path(deck["file"])))
    for page in manifest.get("pages", []):
        tab = f"{page['title']} – {site}"
        pages.append(Expected(rendered_path(page["file"]), page["title"], tab))
    pages.append(Expected("index.html", tab=site))
    return pages


class SiteChecker:
    def __init__(self, root: Path, site: Path) -> None:
        self.root = root
        self.site = site
        self.manifest = yaml.safe_load((root / MANIFEST).read_text(encoding="utf-8"))
        self.problems: list[Problem] = []
        self._pages: dict[Path, Page] = {}

    def page(self, path: Path) -> Page:
        if path not in self._pages:
            self._pages[path] = parse_page(path)
        return self._pages[path]

    def report(self, rule: str, file: str, message: str) -> None:
        self.problems.append(Problem(rule, file, message))

    def run(self) -> list[Problem]:
        if not self.site.is_dir():
            self.report("missing-page", str(self.site), "no rendered site; run `make site` first")
            return self.problems
        self.check_expected_pages()
        for path in sorted(self.site.rglob("*.html")):
            if "site_libs" not in path.relative_to(self.site).parts:
                self.check_links(path)
        return self.problems

    def check_expected_pages(self) -> None:
        for want in expected_pages(self.manifest):
            path = self.site / want.path
            if not path.is_file():
                self.report("missing-page", want.path, "listed in docs/workshop.yml, not rendered")
                continue
            page = self.page(path)
            if want.h1 is not None and want.h1 not in page.h1:
                found = page.h1[0] if page.h1 else "no H1"
                self.report("page-title", want.path, f"H1 is {found!r}, expected {want.h1!r}")
            if want.tab is not None and page.title != want.tab:
                self.report(
                    "page-title", want.path, f"<title> is {page.title!r}, expected {want.tab!r}"
                )

    def check_links(self, path: Path) -> None:
        rel = path.relative_to(self.site).as_posix()
        for link in dict.fromkeys(self.page(path).links):
            problem = self.link_problem(path, link)
            if problem:
                self.report("broken-link", rel, problem)

    def link_problem(self, page: Path, link: str) -> str | None:
        parts = urlsplit(link)
        if parts.scheme or link.startswith("//") or not (parts.path or parts.fragment):
            return None
        target = page if not parts.path else (page.parent / unquote(parts.path))
        if parts.path.endswith("/") or target.is_dir():
            target = target / "index.html"
        target = target.resolve()
        if not target.is_relative_to(self.site.resolve()):
            return f"{link} points outside the site"
        if not target.is_file():
            return f"{link} does not exist"
        fragment = unquote(parts.fragment)
        if fragment and target.suffix == ".html" and fragment not in self.page(target).ids:
            return f"{link}: no element with id {fragment!r}"
        return None


def check(root: Path, site: Path | None = None) -> list[Problem]:
    return SiteChecker(root, site or root / "_site").run()


def layout_pages(manifest: dict[str, Any]) -> list[dict[str, str]]:
    """The pages ``scripts/site_layout.mjs`` opens: the landing page, the site pages and every
    day's lecture, lab and solutions."""
    paths = ["index.html"]
    paths += [rendered_path(page["file"]) for page in manifest.get("pages", [])]
    for day in manifest["days"]:
        notebook = day["lab"]["notebook"]
        solutions = notebook.replace("notebooks/", "notebooks/solutions/", 1)
        paths += [rendered_path(p) for p in (day["lecture"]["file"], notebook, solutions)]
    return [{"path": path} for path in paths]


def summarise(problems: Iterable[Problem]) -> str:
    problems = list(problems)
    files = {p.file for p in problems}
    return f"check_site: {len(problems)} problem(s) in {len(files)} file(s)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="repository root")
    parser.add_argument("--site", type=Path, default=None, help="rendered site (default _site)")
    parser.add_argument(
        "--layout-pages",
        action="store_true",
        help="print the pages site_layout.mjs should open, as JSON, and exit",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.layout_pages:
        manifest = yaml.safe_load((root / MANIFEST).read_text(encoding="utf-8"))
        print(json.dumps(layout_pages(manifest)))
        return 0
    problems = check(root, args.site.resolve() if args.site else None)
    for problem in problems:
        print(problem)
    print(summarise(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
