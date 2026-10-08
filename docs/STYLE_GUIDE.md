# Style Guide

How the workshop writes titles, product names and terms. It applies to the README, the landing page
and site menus, lectures, notebook Markdown cells, slide decks, the instructor guide and the other
site pages. Code, paths, environment variables and API fields are never restyled to fit it.

The canonical titles live in one file, [`docs/workshop.yml`](workshop.yml); every copy of a title
is checked against it by `scripts/check_style.py`, which `make ci` and the PR workflow run. To
rename a day, a lecture, a lab, a deck or a site page, change `docs/workshop.yml` first and then fix
every place the checker reports. Never apply a blanket `.title()` transformation: it breaks
acronyms and product names (`Llm-As-A-Judge`, `Aws Ci/Cd`).

## Two kinds of heading

**Title Case for the names of things.** The workshop title, day/lecture/lab/deck titles (the H1 of
a lecture or notebook), site page titles, navigation and menu labels, sidebar sections, and slide
titles. These are names: they appear in menus, browser tabs and search results, and they must read
the same everywhere.

**Sentence case for everything inside a page.** Section headings (H2 and below in lectures,
notebooks and docs), body prose, table headers and table labels, figure captions and list items.
Proper nouns and the canonical vocabulary below keep their capitals, so a sentence-case heading
can still say "Bedrock Evaluations and AgentCore Evaluations". Slide titles are the exception
inside a deck: each slide's heading is a title.

Which words in a sentence-case heading are names is a judgement, so it is reviewed rather than
checked. The Title Case rules are mechanical and are checked.

### Title Case rules

- Capitalise every word except articles (a, an, the), coordinating conjunctions (and, but, or,
  nor, for, so, yet) and prepositions of four letters or fewer (as, at, by, from, in, into, of,
  off, on, onto, over, per, to, up, via, vs, with), plus "than".
- Always capitalise the first and the last word, and the first word after a colon, an em dash or
  a middle dot: "LLM Evaluation Foundations: From Vibes to Measured Agents",
  "Day 1 · Why Your AI Agent Fails in Production".
- Longer prepositions are capitalised: "Four Seeded Weaknesses, Behind Feature Flags".
- Hyphenated compounds: capitalise each part except the small words above
  ("LLM-as-a-Judge", "Live-Mode", "Vibe-Driven") and except the second part after a prefix such as
  anti-, co-, multi-, non-, pre-, re- ("Non-deterministic").
- Code spans, quotations, numbers and identifiers keep their own case:
  "The Numbers the Repo Produces (Mock Mode, G043, `MAX_STEPS=8`)".
- Use the serial comma in titles: "Bedrock Evaluations, Red Teaming, and CI Gating". The workshop's
  own name keeps its ampersand: "Evaluating Autonomous Agents: Systems, Harnesses & AWS Production
  CI/CD".

## Canonical titles

The full list, with short labels, is `docs/workshop.yml`. At a glance:

| day | lecture | lab |
|---|---|---|
| 1 · Foundations | LLM Evaluation Foundations: From Vibes to Measured Agents | Deterministic and RAG Evaluations for the Stockroom Agent |
| 2 · Judges and Traces | LLM-as-a-Judge, Calibration, and OpenTelemetry Traces | Judge Calibration and OpenTelemetry Traces |
| 3 · The Harness | Building a Custom Agent Harness, Mocking Tools over MCP, and the Managed Alternative | Building a Custom Agent Harness |
| 4 · The Gate | Production CI/CD for Agents on AWS: Regression Gates, Red Teaming, and Bedrock Evaluations | Bedrock Evaluations, Red Teaming, and CI Gating |

- A lecture or notebook H1 is "Day N — title"; the site filter (`site/filters/gfm.lua`) turns
  "Day N" into the eyebrow above the title.
- Menus, day cards and README tables use the short label: "Day 2 · LLM-as-a-Judge, Calibration,
  and OTel", "Day 4 · Bedrock Evaluations and CI Gating", "Day 3 · Solutions".
- Decks: "Workshop Kickoff" (`slides/intro.qmd`) and "Why Your AI Agent Fails in Production (and
  How Evals Fix It)" (`slides/DAY1_MOTIVATIONAL_SLIDES.md`, short label "Why Your AI Agent Fails in
  Production").
- Notebook file names (`Day2_Judge_Calibration_and_OTEL_Traces.ipynb`) and lecture file names are
  identifiers: they are never renamed to match a title, because links, Colab badges and the site
  depend on them.

## Canonical vocabulary

| write | not | note |
|---|---|---|
| Stockroom | `stockroom`, `StockRoom` in prose | the package is `stockroom` in code |
| AWS | `Aws` | |
| Amazon Bedrock, then Bedrock | `Bedrock` on first mention in a page | |
| Bedrock Evaluations | `Bedrock evaluations` | the product; "a model evaluation job" is the generic act |
| AgentCore, Amazon Bedrock AgentCore | `Agentcore`, `Agent Core` | |
| AgentCore Evaluations | | |
| OpenTelemetry | `OTEL`, `Opentelemetry`, `Open Telemetry` | in prose |
| OTel | `OTEL`, `Otel` | short labels only (menus, cards, table cells) |
| `OTEL_*` | | environment variables only, always in a code span |
| MCP, RAG, CI/CD | `CICD`, `CI-CD` | |
| GitHub, GitHub Actions | `Github` | |
| DeepEval | `Deepeval`, `Deep Eval` | |
| RAGAS | `Ragas` | the package is `ragas` in code |
| Promptfoo | `PromptFoo` | the CLI is `promptfoo` in code |
| Arize Phoenix, then Phoenix | `Arize phoenix` | |
| LLM-as-a-judge | `LLM-as-judge`, `LLM as a judge` | "LLM-as-a-Judge" in a title |
| harness | `Harness` mid-sentence | lowercase for the generic concept; `Harness` the class and "AgentCore Harness" the product keep their capitals |
| red teaming | `red-teaming` | the activity (noun or gerund): "red teaming the agent" |
| red-team | | only before a noun: "a red-team case", "the red-team suite" |
| red team | | the team or the suite as a noun: "the Promptfoo red team" |

The checker reads the "not" column from the `banned` list in `docs/workshop.yml`; it skips code
fences, code spans, URLs and link targets, and reads only the Markdown cells of notebook sources.
The decisions log (`docs/DECISIONS.md`) and the task list (`docs/TASKS.md`) are records of what was
written at the time and are not rewritten.

## Lecture structure and numbering

Every lecture has the same skeleton: the H1, a one-paragraph framing, then two unnumbered sections,
"Learning objectives" and "Agenda (09:00–17:00)", then numbered sections starting at 1
("## 1. …", "### 1.1 …"). Cross-references say "section 3.2" and use those numbers. The agenda
follows the timetable in `docs/INSTRUCTOR_GUIDE.md`: lecture 09:15–10:45, lab part 1 11:00–12:30,
lunch 12:30–13:15, lab part 2 13:15–15:45, review 16:00–17:00.

## Setup requirements

State Node the same way everywhere: "Node 22 (the version CI uses; 20 or newer works)". The CI and
Pages workflows and the devcontainer pin Node 22, and `package.json` declares `>=20`; the checker
keeps those numbers and the sentence in the README, the landing page and the instructor guide in
step.

## Links

Relative links must resolve: to a file in the repository, to a Markdown heading (`#anchor`, using
GitHub's anchor rules), or, for a rendered deck (`.html`), to its source. The checker reports any
that do not, in the prose files and in `_quarto.yml`.

## Exemptions

When a finding sits in a file another branch is rewriting, add an entry under `exemptions` in
`docs/workshop.yml` naming the file, the rule, the exact text and the reason. An exemption that no
longer matches anything is itself an error, so the list only shrinks; delete the entry when you fix
the text.
