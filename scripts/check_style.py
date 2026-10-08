#!/usr/bin/env python
"""Check workshop titles, terminology, Node versions and local links against ``docs/workshop.yml``.

The rules are those of ``docs/STYLE_GUIDE.md``:

* ``canonical-title`` - every copy of a day, lecture, lab, deck or site-page title (lecture and
  notebook-source H1s, ``_quarto.yml`` menus and sidebar, README tables, ``index.qmd`` cards and
  path, the instructor guide, both slide decks, ``CITATION.cff``) matches the manifest;
* ``page-title`` - each site page listed under ``pages`` has the manifest title as its H1;
* ``title-case`` - manifest titles, menu labels and slide titles follow the Title Case rules;
* ``banned-spelling`` - prose uses the canonical spelling of product names and terms (code
  fences, code spans, URLs and link targets are skipped);
* ``node-version`` - ``package.json``, the workflows, the devcontainer and the prose agree on Node;
* ``broken-link`` - relative Markdown links and ``_quarto.yml`` hrefs resolve, and ``#anchors``
  into Markdown files name a heading (GitHub's slug rules);
* ``exemption`` - an exemption in the manifest that no longer matches anything.

Sentence case for section headings is a review judgement, not a check: it depends on which words
are names. Usage: ``uv run python scripts/check_style.py [--root PATH]``; exits 1 on any problem.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path("docs/workshop.yml")

# Articles, coordinating conjunctions and prepositions of four letters or fewer (plus "than").
SMALL_WORDS = frozenset(
    {
        *("a", "an", "the"),
        *("and", "but", "or", "nor", "for", "so", "yet"),
        *("as", "at", "by", "in", "of", "off", "on", "onto", "per", "to", "up", "via", "vs"),
        *("with", "from", "into", "over", "than"),
    }
)
HYPHEN_PREFIXES = frozenset(
    {"anti", "co", "de", "multi", "non", "post", "pre", "pro", "re", "semi", "sub", "un"}
)
LOWERCASE_NAMES = frozenset({"uv", "npm", "pytest", "make"})
PHRASE_BREAKS = ("—", "–", "·")

FENCE = re.compile(r"^\s*(```|~~~)")
CODE_SPAN = re.compile(r"`[^`]*`")
LINK_TARGET = re.compile(r"\]\(\s*([^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")
URL = re.compile(r"<?https?://[^\s)>]+>?")
EXTERNAL = ("http://", "https://", "mailto:", "tel:", "data:")
NODE_MENTION = re.compile(r"\bNode(?:\.js)?\s*(?:≥|>=|version)?\s*(\d+)")


@dataclass(frozen=True)
class Problem:
    rule: str
    file: str
    line: int
    message: str
    text: str = ""

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: [{self.rule}] {self.message}"


# --------------------------------------------------------------------------- text helpers


def title_case_problems(title: str) -> list[str]:
    """Words of ``title`` that break the Title Case rules (empty list = fine).

    Capitalise every word except articles, coordinating conjunctions and prepositions of four
    letters or fewer; always capitalise the first and last word and the first word after a colon,
    dash or middle dot; in a hyphenated compound, capitalise each part except small words and the
    part after a prefix (``Non-deterministic``). Code spans, quotations, numbers, paths and
    identifiers keep their own case.
    """
    text = CODE_SPAN.sub(" ", title)
    text = re.sub(r'"[^"]*"|“[^”]*”', " ", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    words = text.split()
    bad: list[str] = []
    phrase_start = True
    for index, raw in enumerate(words):
        token = raw.strip("*_()[]{}.,;:!?'\"“”‘’")
        if raw in PHRASE_BREAKS or not token:
            phrase_start = phrase_start or raw in PHRASE_BREAKS
            continue
        last = index == len(words) - 1
        skip = (
            any(ch.isdigit() for ch in token)
            or any(ch in token for ch in "/_.=+@<>")
            or token in LOWERCASE_NAMES
        )
        if not skip:
            parts = token.split("-")
            for pos, part in enumerate(parts):
                if not part or not part[0].isalpha() or not part[0].islower():
                    continue
                if any(ch.isupper() for ch in part[1:]):  # iPhone, eBPF: a name keeps its case
                    continue
                small = part.lower() in SMALL_WORDS
                if pos == 0:
                    wrong = phrase_start or (last and len(parts) == 1) or not small
                else:
                    wrong = not small and parts[pos - 1].lower() not in HYPHEN_PREFIXES
                if wrong:
                    bad.append(token)
                    break
        phrase_start = raw.endswith(":") or raw.endswith(PHRASE_BREAKS)
    return bad


def github_slug(heading: str) -> str:
    """The anchor GitHub generates for a Markdown heading."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    text = text.replace("`", "").strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def heading_slugs(lines: list[str]) -> set[str]:
    slugs: set[str] = set()
    counts: dict[str, int] = {}
    in_fence = False
    for line in lines:
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        match = re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if in_fence or match is None:
            continue
        slug = github_slug(match[1])
        n = counts.get(slug, 0)
        counts[slug] = n + 1
        slugs.add(slug if n == 0 else f"{slug}-{n}")
    return slugs


def front_matter(lines: list[str]) -> dict[str, Any]:
    if not lines or lines[0].strip() != "---":
        return {}
    for end in range(1, len(lines)):
        if lines[end].strip() == "---":
            try:
                data = yaml.safe_load("\n".join(lines[1:end]))
            except yaml.YAMLError:  # Marp accepts unquoted values with colons; YAML does not
                data = {}
                for line in lines[1:end]:
                    match = re.match(r"^([\w-]+):\s*(.*?)\s*$", line)
                    if match:
                        data[match[1]] = match[2].strip("\"'")
            return data if isinstance(data, dict) else {}
    return {}


def prose_lines(path: Path, lines: list[str]) -> Iterator[tuple[int, str]]:
    """(line number, text) for the prose of a Markdown file or a notebook source's Markdown cells,
    with code fences dropped and code spans blanked."""
    notebook = path.suffix == ".py"
    in_markdown = not notebook
    in_fence = False
    for number, line in enumerate(lines, 1):
        if notebook:
            if line.startswith("# %%"):
                in_markdown = "[markdown]" in line
                in_fence = False
                continue
            if not in_markdown:
                continue
            line = re.sub(r"^# ?", "", line)
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            yield number, CODE_SPAN.sub("``", line)


def first_h1(lines: list[str], notebook: bool = False) -> tuple[int, str] | None:
    prefix = "# # " if notebook else "# "
    in_fence = False
    for number, line in enumerate(lines, 1):
        if not notebook and FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence and line.startswith(prefix):
            return number, line[len(prefix) :].strip()
    return None


def find_line(lines: list[str], needle: str) -> int:
    for number, line in enumerate(lines, 1):
        if needle in line:
            return number
    return 1


# --------------------------------------------------------------------------- the checker


class StyleChecker:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.manifest: dict[str, Any] = yaml.safe_load(
            (root / MANIFEST).read_text(encoding="utf-8")
        )
        self.problems: list[Problem] = []
        self._lines: dict[str, list[str]] = {}

    # helpers -----------------------------------------------------------------------------------

    def lines(self, rel: str) -> list[str]:
        if rel not in self._lines:
            path = self.root / rel
            text = path.read_text(encoding="utf-8") if path.is_file() else ""
            self._lines[rel] = text.splitlines()
        return self._lines[rel]

    def report(self, rule: str, rel: str, line: int, message: str, text: str = "") -> None:
        self.problems.append(Problem(rule, rel, line, message, text))

    def expect(self, rel: str, line: int, found: str, expected: str, what: str) -> None:
        if found != expected:
            self.report(
                "canonical-title", rel, line, f"{what} is {found!r}, expected {expected!r}", found
            )

    def check_title_case(self, rel: str, line: int, title: str, what: str) -> None:
        bad = title_case_problems(title)
        if bad:
            self.report(
                "title-case", rel, line, f"{what} {title!r}: capitalise {', '.join(bad)}", title
            )

    @property
    def days(self) -> list[dict[str, Any]]:
        return self.manifest["days"]

    def prose_files(self) -> list[str]:
        files: list[str] = []
        for pattern in self.manifest.get("prose", []):
            for path in sorted(self.root.glob(pattern)):
                rel = path.relative_to(self.root).as_posix()
                if rel not in files:
                    files.append(rel)
        return files

    def labels_by_href(self) -> dict[str, str]:
        """Expected menu label for every file a menu can link to."""
        labels: dict[str, str] = {}
        for d in self.days:
            n, lecture, lab = d["day"], d["lecture"], d["lab"]
            labels[lecture["file"]] = f"Day {n} · {lecture['short']}"
            labels[lab["notebook"]] = f"Day {n} · {lab['short']}"
            solutions = lab["notebook"].replace("notebooks/", "notebooks/solutions/", 1)
            labels[solutions] = f"Day {n} · Solutions"
        for deck in self.manifest.get("decks", []):
            label = (f"Day {deck['day']} · " if deck.get("day") else "") + deck["short"]
            labels[deck["file"]] = label
            if deck.get("rendered"):
                labels[deck["rendered"]] = label
        for page in self.manifest.get("pages", []):
            labels[page["file"]] = page["short"]
        return labels

    def aliases(self) -> dict[str, set[str]]:
        return {p["file"]: set(p.get("aliases", [])) for p in self.manifest.get("pages", [])}

    # rules -------------------------------------------------------------------------------------

    def check_manifest(self) -> None:
        rel = MANIFEST.as_posix()
        lines = self.lines(rel)
        w = self.manifest["workshop"]
        titles = [w["title"], w["short"], w["subtitle"]]
        for d in self.days:
            titles += [d["theme"], d["lecture"]["title"], d["lecture"]["short"]]
            titles += [d["lab"]["title"], d["lab"]["short"]]
        for item in [*self.manifest.get("decks", []), *self.manifest.get("pages", [])]:
            titles += [item["title"], item["short"]]
        for title in titles:
            self.check_title_case(rel, find_line(lines, title), title, "manifest title")

    def check_lectures_and_notebooks(self) -> None:
        for d in self.days:
            n = d["day"]
            for rel, entry, notebook in (
                (d["lecture"]["file"], d["lecture"], False),
                (d["lab"]["source"], d["lab"], True),
            ):
                h1 = first_h1(self.lines(rel), notebook=notebook)
                if h1 is None:
                    self.report("canonical-title", rel, 1, "no H1 heading found")
                    continue
                self.expect(rel, h1[0], h1[1], f"Day {n} — {entry['title']}", "H1")

    def check_pages(self) -> None:
        for page in self.manifest.get("pages", []):
            rel = page["file"]
            h1 = first_h1(self.lines(rel))
            if h1 is None:
                self.report("page-title", rel, 1, "no H1 heading found")
            elif h1[1] != page["title"]:
                self.report(
                    "page-title",
                    rel,
                    h1[0],
                    f"H1 is {h1[1]!r}, expected {page['title']!r}",
                    h1[1],
                )

    def check_quarto(self) -> None:
        rel = "_quarto.yml"
        lines = self.lines(rel)
        config = yaml.safe_load("\n".join(lines)) or {}
        site = config.get("website", {})
        w = self.manifest["workshop"]
        self.expect(rel, find_line(lines, "  title:"), site.get("title", ""), w["short"], "title")
        labels = self.labels_by_href()
        aliases = self.aliases()

        def items(node: Any, footer: bool = False) -> Iterator[tuple[dict[str, Any], bool]]:
            if isinstance(node, dict):
                if "text" in node or "section" in node:
                    yield node, footer
                for value in node.values():
                    yield from items(value, footer)
            elif isinstance(node, list):
                for value in node:
                    yield from items(value, footer)

        nav = [site.get("navbar", {}), site.get("sidebar", [])]
        footer = site.get("page-footer", {})
        for node, is_footer in [*items(nav), *items(footer.get("center", []), footer=True)]:
            label = str(node.get("text") or node.get("section") or "")
            if not label or label == "---":
                continue
            line = find_line(lines, label)
            self.check_title_case(rel, line, label, "menu label")
            href = str(node.get("href", ""))
            expected = labels.get(href)
            if expected is None or label in aliases.get(href, set()):
                continue
            if is_footer:
                if expected.split()[: len(label.split())] != label.split():
                    self.report(
                        "canonical-title",
                        rel,
                        line,
                        f"footer label {label!r} should be the start of {expected!r}",
                        label,
                    )
            else:
                self.expect(rel, line, label, expected, f"menu label for {node['href']}")
        for sidebar in site.get("sidebar", []) or []:
            title = sidebar.get("title")
            if title:
                self.check_title_case(rel, find_line(lines, f"title: {title}"), title, "sidebar")
        self.check_hrefs(rel, config)

    def check_readme(self) -> None:
        rel = "README.md"
        lines = self.lines(rel)
        h1 = first_h1(lines)
        self.expect(
            rel, h1[0] if h1 else 1, h1[1] if h1 else "", self.manifest["workshop"]["title"], "H1"
        )
        by_day = {d["day"]: d for d in self.days}
        for number, line in enumerate(lines, 1):
            match = re.match(r"^\|\s*(\d+)\s*·\s*([^|]+?)\s*\|", line)
            if match and int(match[1]) in by_day:
                expected = by_day[int(match[1])]["lab"]["short"]
                self.expect(rel, number, match[2], expected, f"Day {match[1]} notebook label")

    def check_index(self) -> None:
        rel = "index.qmd"
        lines = self.lines(rel)
        w = self.manifest["workshop"]
        meta = front_matter(lines)
        self.expect(rel, find_line(lines, "title:"), str(meta.get("title")), w["short"], "title")
        self.expect(
            rel, find_line(lines, "subtitle:"), str(meta.get("subtitle")), w["subtitle"], "subtitle"
        )
        for number, line in enumerate(lines, 1):
            for match in re.finditer(r"\[([^\]]+)\]\{\.hero-subtitle\}", line):
                self.expect(rel, number, match[1], w["subtitle"], "hero subtitle")
        for d in self.days:
            n, lecture, lab = d["day"], d["lecture"], d["lab"]
            expected = {
                ("day-card-title", lecture["file"]): lecture["short"],
                ("path-title", lecture["file"]): lecture["title"],
                ("path-title", lab["notebook"]): lab["title"],
                ("path-day-label", lecture["file"]): f"Day {n} · {d['theme']}",
            }
            for number, line in enumerate(lines, 1):
                pattern = r"\[([^\]]+)\]\(([^)]+)\)\{\.(day-card-title|path-title|path-day-label)\}"
                for match in re.finditer(pattern, line):
                    want = expected.get((match[3], match[2]))
                    if want is not None:
                        self.expect(rel, number, match[1], want, f"{match[3]} for {match[2]}")

    def check_instructor_guide(self) -> None:
        rel = "docs/INSTRUCTOR_GUIDE.md"
        by_day = {d["day"]: d for d in self.days}
        for number, line in enumerate(self.lines(rel), 1):
            match = re.match(r"^### Day (\d+) — (.+?)(?: \(.*\))?$", line)
            if match and int(match[1]) in by_day:
                lab = by_day[int(match[1])]["lab"]["short"]
                self.expect(rel, number, match[2], lab, f"Day {match[1]} facilitation heading")
            match = re.match(r"^\| (\d+) — ([^|]+?) \|", line)
            if match and int(match[1]) in by_day:
                lecture = by_day[int(match[1])]["lecture"]["short"]
                self.expect(rel, number, match[2], lecture, f"Day {match[1]} timetable row")

    def check_decks(self) -> None:
        w = self.manifest["workshop"]
        by_day = {d["day"]: d for d in self.days}
        for deck in self.manifest.get("decks", []):
            rel = deck["file"]
            lines = self.lines(rel)
            meta = front_matter(lines)
            marp = bool(meta.get("marp"))
            if marp:
                self.expect(
                    rel, find_line(lines, "title:"), str(meta.get("title")), deck["title"], "title"
                )
                if w["title"] not in str(meta.get("description", "")):
                    self.report(
                        "canonical-title",
                        rel,
                        find_line(lines, "description:"),
                        f"description should name the workshop as {w['title']!r}",
                    )
            else:
                self.expect(
                    rel, find_line(lines, "title:"), str(meta.get("title")), w["short"], "title"
                )
                subtitle = f"{w['subtitle']} · {deck['title']}"
                self.expect(
                    rel,
                    find_line(lines, "subtitle:"),
                    str(meta.get("subtitle")),
                    subtitle,
                    "subtitle",
                )
            slide_heading = re.compile(r"^#{1,2} (.+)$" if marp else r"^## (.+)$")
            in_fence = False
            first_title = True
            for number, line in enumerate(lines, 1):
                if FENCE.match(line):
                    in_fence = not in_fence
                    continue
                match = None if in_fence else slide_heading.match(line)
                if match is None:
                    continue
                if marp and first_title:
                    self.expect(rel, number, match[1], deck["title"], "title slide")
                first_title = False
                self.check_title_case(rel, number, match[1], "slide title")
            for number, line in enumerate(lines, 1):
                match = re.match(
                    r"^\| \*\*(?:Day )?(\d+)\*\* \| ([^|]+?) \|(?: ([^|]+?) \|)?", line
                )
                if not match or int(match[1]) not in by_day:
                    continue
                d = by_day[int(match[1])]
                if marp:
                    if not match[2].startswith(f"{d['theme']}:"):
                        self.report(
                            "canonical-title",
                            rel,
                            number,
                            f"Day {match[1]} theme should start with {d['theme'] + ':'!r}",
                            match[2],
                        )
                else:
                    if not match[2].startswith(f"{d['lecture']['short']}:"):
                        self.report(
                            "canonical-title",
                            rel,
                            number,
                            f"Day {match[1]} lecture should start with "
                            f"{d['lecture']['short'] + ':'!r}",
                            match[2],
                        )
                    if match[3] is not None:
                        self.expect(rel, number, match[3], d["lab"]["short"], f"Day {match[1]} lab")

    def check_citation(self) -> None:
        rel = "CITATION.cff"
        data = yaml.safe_load("\n".join(self.lines(rel))) or {}
        found = str(data.get("title", ""))
        self.expect(
            rel,
            find_line(self.lines(rel), "title:"),
            found,
            self.manifest["workshop"]["title"],
            "title",
        )

    def check_node(self) -> None:
        node = self.manifest["node"]
        ci, minimum = int(node["ci"]), int(node["minimum"])
        package = json.loads((self.root / "package.json").read_text(encoding="utf-8"))
        engines = package.get("engines", {}).get("node", "")
        if engines.replace(" ", "") != f">={minimum}":
            self.report(
                "node-version",
                "package.json",
                find_line(self.lines("package.json"), '"node"'),
                f"engines.node is {engines!r}, expected '>={minimum}'",
            )
        pinned = [
            p.relative_to(self.root).as_posix()
            for p in sorted(self.root.glob(".github/workflows/*.yml"))
        ]
        pinned.append(".devcontainer/devcontainer.json")
        for rel in pinned:
            for number, line in enumerate(self.lines(rel), 1):
                match = re.search(
                    r'node-version:\s*"?(\d+)|features/node:\d+"\s*:\s*\{\s*"version"\s*:\s*"(\d+)',
                    line,
                )
                if match and int(match[1] or match[2]) != ci:
                    self.report(
                        "node-version", rel, number, f"Node {match[1] or match[2]}, expected {ci}"
                    )
        statement = str(node.get("statement", ""))
        if str(ci) not in statement or str(minimum) not in statement:
            self.report(
                "node-version",
                MANIFEST.as_posix(),
                find_line(self.lines(MANIFEST.as_posix()), "statement:"),
                f"node.statement should name {ci} and {minimum}",
            )
        for rel in node.get("stated_in", []):
            if statement not in "\n".join(self.lines(rel)):
                self.report("node-version", rel, 1, f"setup instructions should say {statement!r}")
        for rel in self.prose_files():
            for number, text in prose_lines(Path(rel), self.lines(rel)):
                for match in NODE_MENTION.finditer(text):
                    if int(match[1]) not in (ci, minimum):
                        self.report(
                            "node-version",
                            rel,
                            number,
                            f"prose names Node {match[1]}; docs/workshop.yml allows {ci} (CI) "
                            f"and {minimum}+ (minimum)",
                        )

    def check_banned(self) -> None:
        rules = [(re.compile(b["pattern"]), b["use"]) for b in self.manifest.get("banned", [])]
        for rel in self.prose_files():
            for number, text in prose_lines(Path(rel), self.lines(rel)):
                text = URL.sub(" ", LINK_TARGET.sub("]", text))
                for pattern, use in rules:
                    for match in pattern.finditer(text):
                        self.report(
                            "banned-spelling", rel, number, f"{match[0]!r}: use {use}", match[0]
                        )

    def check_links(self) -> None:
        for rel in self.prose_files():
            base = (self.root / rel).parent
            if rel.startswith("notebooks/src/"):
                base = self.root / "notebooks"  # the generated notebooks live one level up
            for number, text in prose_lines(Path(rel), self.lines(rel)):
                for match in LINK_TARGET.finditer(text):
                    self.check_target(rel, number, base, match[1])

    def check_hrefs(self, rel: str, config: Any) -> None:
        lines = self.lines(rel)

        def hrefs(node: Any) -> Iterator[str]:
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "href" and isinstance(value, str):
                        yield value
                    else:
                        yield from hrefs(value)
            elif isinstance(node, list):
                for value in node:
                    yield from hrefs(value)

        for href in hrefs(config):
            self.check_target(rel, find_line(lines, f"href: {href}"), self.root, href)

    def check_target(self, rel: str, number: int, base: Path, target: str) -> None:
        if target.startswith(EXTERNAL) or "{{" in target:
            return
        path_part, _, anchor = target.partition("#")
        path_part = path_part.split("?", 1)[0]
        if path_part:
            path = (base / path_part).resolve()
            exists = path.exists()
            if not exists and path.suffix == ".html":
                exists = any(path.with_suffix(s).exists() for s in (".md", ".qmd"))
            if not exists:
                self.report(
                    "broken-link", rel, number, f"link target {target!r} does not exist", target
                )
                return
        else:
            path = (self.root / rel).resolve()
        # Quarto pages (.qmd, notebooks) get pandoc ids, not GitHub slugs: only .md anchors count.
        if not anchor or path.suffix != ".md":
            return
        if anchor not in heading_slugs(path.read_text(encoding="utf-8").splitlines()):
            self.report(
                "broken-link",
                rel,
                number,
                f"no heading for anchor '#{anchor}' in {path.name}",
                target,
            )

    # driver ------------------------------------------------------------------------------------

    def apply_exemptions(self) -> list[Problem]:
        exemptions = self.manifest.get("exemptions", []) or []
        used = [False] * len(exemptions)
        kept: list[Problem] = []
        for problem in self.problems:
            hit = next(
                (
                    i
                    for i, e in enumerate(exemptions)
                    if e["file"] == problem.file
                    and e["rule"] == problem.rule
                    and e["text"] == problem.text
                ),
                None,
            )
            if hit is None:
                kept.append(problem)
            else:
                used[hit] = True
        lines = self.lines(MANIFEST.as_posix())
        for exemption, was_used in zip(exemptions, used, strict=True):
            if not was_used:
                kept.append(
                    Problem(
                        "exemption",
                        MANIFEST.as_posix(),
                        find_line(lines, f"file: {exemption['file']}"),
                        f"exemption for {exemption['text']!r} in {exemption['file']} matches "
                        "nothing; delete it",
                    )
                )
        return kept

    def run(self) -> list[Problem]:
        for check in (
            self.check_manifest,
            self.check_lectures_and_notebooks,
            self.check_pages,
            self.check_quarto,
            self.check_readme,
            self.check_index,
            self.check_instructor_guide,
            self.check_decks,
            self.check_citation,
            self.check_node,
            self.check_banned,
            self.check_links,
        ):
            check()
        return self.apply_exemptions()


def check(root: Path = REPO_ROOT) -> list[Problem]:
    return StyleChecker(root).run()


def summarise(problems: Iterable[Problem]) -> str:
    problems = list(problems)
    files = {p.file for p in problems}
    return f"check_style: {len(problems)} problem(s) in {len(files)} file(s)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="repository root")
    args = parser.parse_args(argv)
    problems = check(args.root.resolve())
    for problem in problems:
        print(problem)
    print(summarise(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
