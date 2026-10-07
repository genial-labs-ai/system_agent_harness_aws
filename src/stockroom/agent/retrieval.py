"""A small, dependency-free BM25 retriever over the policy-document corpus.

Documents are split into sections at ``## N.`` headings; the preamble (title + metadata) is section
``0``. Chunk IDs look like ``returns_policy.md#1`` so golden cases can reference them.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_TOKEN = re.compile(r"[a-z0-9]+(?:[.,][0-9]+)?")
_HEADING = re.compile(r"^##\s+(\d+)\.\s*(.*)$", re.M)
_STOPWORD_TEXT = (
    "a an the and or of to in on for is are was were be been it its this that these those with "
    "by as at from do does did you your we our i me my can could would should will what which who "
    "when where why how much many there here any some all please give tell about into than then "
    "so if not no yes also just only more most other after before during out up down over under "
    "again further once has have had having am being without within"
)
STOPWORDS: frozenset[str] = frozenset(_STOPWORD_TEXT.split())


def tokenize(text: str) -> list[str]:
    """Lower-case alphanumeric tokens; keeps decimals like ``8.50`` and ``1,250`` as one token."""
    return [t.strip(".,") for t in _TOKEN.findall(text.lower()) if t.strip(".,")]


def content_tokens(text: str) -> list[str]:
    """Tokens minus stopwords, used for query matching."""
    return [t for t in tokenize(text) if t not in STOPWORDS]


@dataclass(frozen=True, slots=True)
class Chunk:
    doc: str
    section: str
    title: str
    text: str

    @property
    def chunk_id(self) -> str:
        return f"{self.doc}#{self.section}"


def split_sections(doc_name: str, text: str) -> list[Chunk]:
    """Split a markdown policy document into its numbered sections."""
    title_match = re.search(r"^#\s+(.*)$", text, re.M)
    doc_title = title_match.group(1).strip() if title_match else doc_name
    headings = list(_HEADING.finditer(text))
    chunks: list[Chunk] = []
    preamble_end = headings[0].start() if headings else len(text)
    preamble = text[:preamble_end].strip()
    if preamble:
        chunks.append(Chunk(doc=doc_name, section="0", title=doc_title, text=preamble))
    for idx, match in enumerate(headings):
        start = match.start()
        end = headings[idx + 1].start() if idx + 1 < len(headings) else len(text)
        body = text[start:end].strip()
        chunks.append(
            Chunk(
                doc=doc_name,
                section=match.group(1),
                title=f"{doc_title} — {match.group(2).strip()}",
                text=body,
            )
        )
    return chunks


def load_policy_chunks(docs_dir: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(docs_dir.glob("*.md")):
        if path.name == "README.md":
            continue
        chunks.extend(split_sections(path.name, path.read_text(encoding="utf-8")))
    return chunks


class BM25Index:
    """Okapi BM25 (k1=1.5, b=0.75) over :class:`Chunk` texts."""

    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75) -> None:
        self.chunks = chunks
        self.k1 = k1
        self.b = b
        self._doc_tokens = [tokenize(c.title + " " + c.text) for c in chunks]
        self._doc_len = [len(t) for t in self._doc_tokens]
        self._avg_len = (sum(self._doc_len) / len(self._doc_len)) if self._doc_len else 0.0
        self._tf = [Counter(t) for t in self._doc_tokens]
        df: Counter[str] = Counter()
        for tokens in self._doc_tokens:
            df.update(set(tokens))
        n = len(chunks)
        self._idf = {term: math.log(1 + (n - d + 0.5) / (d + 0.5)) for term, d in df.items()}

    def score(self, query_tokens: list[str], idx: int) -> float:
        score = 0.0
        tf = self._tf[idx]
        dl = self._doc_len[idx]
        for term in query_tokens:
            if term not in tf:
                continue
            f = tf[term]
            idf = self._idf.get(term, 0.0)
            denom = f + self.k1 * (1 - self.b + self.b * dl / (self._avg_len or 1.0))
            score += idf * (f * (self.k1 + 1)) / denom
        return score

    def search(self, query: str, k: int = 3) -> list[tuple[Chunk, float]]:
        q = content_tokens(query)
        scored = [(self.chunks[i], self.score(q, i)) for i in range(len(self.chunks))]
        scored = [(c, s) for c, s in scored if s > 0]
        scored.sort(key=lambda cs: (-cs[1], cs[0].chunk_id))
        return scored[:k]


@lru_cache(maxsize=4)
def default_index(docs_dir: Path) -> BM25Index:
    return BM25Index(load_policy_chunks(docs_dir))
