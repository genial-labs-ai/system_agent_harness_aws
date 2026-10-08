# Repository Improvement Suggestions

Reviewed on 2026-10-08 against the local checkout. This is a source-based review of the lectures,
notebook sources and generated notebooks, slides, website configuration, evaluation gate, and
instructor documentation. It does not establish live AWS correctness or rendered-site quality.

The strongest improvement would be to make Days 3–4 deliver the depth their titles promise, then
apply one editorial standard across the whole workshop. The later material is not shorter:
Day 3 and Day 4 have 596 and 594 lecture lines, and 634 and 774 notebook-source lines, respectively.
However, each has only three exercise cells. My assessment is that the perceived decline comes
from breadth, demonstration-heavy labs, and unresolved inconsistencies more than missing content.

Keep the repository's useful foundations: one running example, deterministic offline execution,
generated student/solution notebooks, reproducible failure flags, and explicit AWS opt-in.

## Priority Overview

| Priority | Improvement | Completion Evidence |
|---|---|---|
| P0 | Separate dataset validity from agent performance | A valid case the agent fails remains eligible for the golden set |
| P0 | Correct and align the gate/threshold teaching | Lecture, notebook, diagram, and implementation describe the same decision |
| P0 | Reject incomplete gate evidence and incompatible baselines | Missing coverage and mismatched provenance produce explicit failures or comparison warnings |
| P1 | Standardize title case and technical names | Source headings, navigation, page titles, and slides follow a documented style |
| P1 | Deepen the Day 3 construction lab and Day 4 capstone | Participants produce and defend working changes, rather than mainly run demonstrations |
| P1 | Add editorial and rendered-content checks | PRs catch broken labels, links, diagrams, and incomplete solution exercises |
| P2 | Improve setup, runtime, and instructor hand-off | Installation requirements agree; labs save reusable artifacts; later days have teaching decks |

## 1. Make Casing an Explicit Editorial Contract

**Evidence:** The main title in [README.md](README.md) uses Title Case, while lecture and notebook
titles generally use sentence case. [_quarto.yml](_quarto.yml) mixes labels such as
“LLM evaluation foundations” and “Bedrock Evaluations and CI gating.” The README notebook table
uses “OTEL traces,” while navigation uses “OTel traces” and the notebook heading spells out
“OpenTelemetry traces.” Lecture section numbering also differs: Days 1–2 number learning
objectives and agendas; Days 3–4 start numbering after them.

**Suggestion:** Add a short `docs/STYLE_GUIDE.md` with these rules:

- Use Title Case for the workshop title, page titles, H1–H3 headings, slide titles, and named
  lecture/lab navigation entries. Keep body prose, descriptions, and ordinary table labels in
  sentence case. Define how to capitalize small words and hyphenated terms.
- Maintain a canonical vocabulary: `Stockroom`, `AWS`, `Amazon Bedrock`, `Bedrock Evaluations`,
  `AgentCore`, `OpenTelemetry`, `OTel`, `MCP`, `RAG`, `CI/CD`, `GitHub`, `DeepEval`, `RAGAS`,
  `Promptfoo`, and `Arize Phoenix`. Use lowercase “harness” for the generic concept and preserve
  the casing of named products.
- Preserve code identifiers, environment variables, paths, and API fields exactly. `OTel` in
  prose does not imply renaming `OTEL_*` variables or existing notebook filenames.
- Choose one numbering scheme for every lecture. Use “red teaming” as a noun and “red-team”
  before a noun, such as “red-team case.”

For example, make the Day 2 lab's visible title “Judge Calibration and OpenTelemetry Traces”
and the Day 4 lab's “Bedrock Evaluations, Red Teaming, and CI Gating.”

**Acceptance:** Review the same title in the README, landing page, menus, sidebar, notebook,
browser title, and slides. All should use canonical wording or an explicitly defined short label.
Do not apply a blanket `.title()` transformation: it would damage acronyms and product names.

## 2. Fix Source Casing Separately From Display Casing

**Evidence:** [site/theme.scss](site/theme.scss) uppercases eyebrow labels;
[site/slides.scss](site/slides.scss) uppercases table headings. Meanwhile,
[site/filters/gfm.lua](site/filters/gfm.lua) splits day headings into page-title metadata and a
subtitle, preserving the source's inline text rather than correcting its case.

**Suggestion:** Keep canonical spelling in the sources and metadata. Retain decorative uppercase
only where intentionally chosen; avoid applying it to technical names if preserving visible
product casing matters. Check the rendered title, navigation, search entry, and browser tab
separately. Add a small workshop-content manifest for canonical day titles and short labels, then
generate or validate duplicated labels against it.

**Acceptance:** Light and dark desktop/mobile previews show the intended case, and generated
metadata agrees with the source. Renaming a title cannot leave an old label elsewhere.

## 3. Turn Day 3 Into a Construction Lab

**Evidence:** [The Day 3 notebook](notebooks/src/day3_building_custom_agent_harness.py) imports the
completed `Harness`, demonstrates its behavior, and “fixes” weaknesses in the core lab by removing
flags. Its exercises add a payload executor wrapper, rewrite a description, and add an assertion.
Those are useful tasks, but “Building a custom agent harness” implies more construction.
[DECISIONS entry 32](docs/DECISIONS.md) already records that `MaxToolPayloadGuard` is an executor
wrapper because the harness has no guard-registration hook.

**Suggestion:** Add a participant task that implements a bounded harness extension through a
small, documented seam. Require participants to identify a failing trajectory, write an assertion,
implement interception or a guard, and compare before/after traces and metrics. Alternatively,
rename the lab to “Inspecting and Extending a Custom Agent Harness” if construction is out of scope.
Explain the distinction between a run guard and a payload wrapper explicitly.

**Acceptance:** Each participant produces a working extension and evidence that it changes behavior.
Keep the seeded branches and flags: the extension must not erase the failure demonstration.

## 4. Give Day 4 One Coherent Capstone

**Evidence:** [The Day 4 notebook](notebooks/src/day4_bedrock_evaluations_and_ci_gating.py) moves
through synthetic generation, red teaming, variance, gate reports, Bedrock job construction,
AgentCore session evaluation, and OIDC. Its three exercises are a red-team dictionary, a threshold
formula, and a boolean acceptance rule. The AWS portions include substantial demonstration code.

**Suggestion:** Organize the required lab around one deliverable: an offline evaluation change
that rejects a seeded regression and passes after a justified fix. Reuse a participant's Day 1
case, Day 2 judge analysis, and Day 3 trajectory. Require a case/assertion, a gate decision,
before/after reports, and a short review explaining the baseline implications. Put Bedrock,
AgentCore, and cloud deployment details in clearly timed optional extensions.

**Acceptance:** A participant can explain why the capstone passes or fails using repository
artifacts. The required path fits the instructor timetable without an AWS account.

## 5. Separate Case Acceptance From Agent Success

**Evidence:** Day 4's `accept_candidate()` requires the current agent to pass the judge, select
the expected tools, and finish successfully before a candidate may join the golden set. This
can exclude exactly the valid difficult cases an evaluation suite needs to retain.

**Suggestion:** Validate labels independently against the data and policies; check schema,
identifiers, duplicates, forbidden facts, and coverage. Use agent performance to classify an
accepted case as passing, failing, or needing investigation. Keep judge disagreement for review
rather than treating every model failure as an invalid example. Include paraphrase duplicates,
unsupported labels, and valid agent-failing cases in the exercise.

**Acceptance:** A valid hard case is accepted even when Stockroom fails it; a mislabeled case is
rejected even if Stockroom happens to agree with the incorrect label. Labels remain independent
of the agent under test.

## 6. Align and Correct the Threshold Lesson

**Evidence:** [Day 4 lecture section 2.3](lectures/day4_aws_ci_cd_redteaming.md) recommends an
absolute floor below a confidence-interval bound; notebook Exercise 2 uses mean minus two
population standard deviations. These are different rules. The lecture's diagram also asks
`PR: metric − baseline > max_drop?`, while [the gate](scripts/check_thresholds.py) correctly
checks `baseline − value > max_drop`. The notebook check repeats the solution's formula.

**Suggestion:** Explain separately the minimum acceptable quality, run-to-run variation, and
allowed regression. Choose a consistent worked procedure, document its assumptions, and fix the
diagram's subtraction direction. Clearly label the invented nightly values as synthetic teaching
data throughout. Test decisions on fixed examples of a passing run, a real regression, and an
uncertain comparison rather than only recomputing the formula.

Also revisit [the confidence-interval helper](tests/test_trajectory_regression.py), which pools
all case/repeat scores into one normal approximation. Preserve repeat identity and explain what
uncertainty is being estimated; use an appropriate method for bounded rates and paired case
comparisons. Deterministic reruns demonstrate reproducibility, not production reliability.

**Acceptance:** The lecture, notebook, gate report, and diagram give the same decision for the same
examples and distinguish measured results from illustrative data.

## 7. Make the Gate Reject Incomplete Evidence

**Evidence:** In a read-only check of `scripts/check_thresholds.py`, an available Promptfoo result
with an empty results list produced no gate failures when headline metrics matched the baseline.
The CLI does reject a missing explicitly requested Promptfoo file, but an empty suite is a
different gap. Baseline comparisons also do not check dataset or mode compatibility: changing
those fields in the input did not produce a failure.

**Suggestion:** Validate result schemas, finite numeric values, expected case/repeat coverage,
and required red-team case IDs before evaluating thresholds. Distinguish report-only optional
inputs from mandatory PR-gate evidence. Record and compare mode, dataset hash/version, judge
rubric, metric version, and model identity where relevant; surface intentional changes explicitly.
Require a baseline for the PR gate while allowing an explicit report-only path without one.

**Acceptance:** Empty/truncated suites and missing required cases cannot yield a green PR gate.
An incompatible baseline produces an explicit decision instead of a misleading regression delta.

## 8. Make Exercise Completion Observable

**Evidence:** Student check cells intentionally print “not solved yet” without failing, as documented
in [DECISIONS entry 28](docs/DECISIONS.md). This makes scaffold execution useful, but a green
`make notebooks` does not establish that a participant completed the exercises. Day 4's
red-team exercise only requires an assertion to exist and pass on the fixed agent; it does not
require a trajectory assertion or prove that it catches unsafe behavior.

**Suggestion:** Keep scaffold execution permissive. Add a separate strict completion mode for
solutions and participant self-assessment, with stable exercise IDs and a completion summary.
For each new red-team assertion, show a safe positive case and an unsafe negative case. Require
a trajectory assertion where the threat involves a tool action. Use varied inputs so memorizing
one case or phrase is insufficient.

**Acceptance:** Every solution reports every required exercise as completed. An assertion that
always passes fails the exercise verification.

## 9. Review What the Security and Cost Demos Actually Guarantee

**Evidence:** [The harness](src/stockroom/agent/harness.py) sanitizes tool output using an
instruction-pattern matcher and compacts older results by retaining a JSON prefix. Its token
guard checks estimated next-input usage; [the budget test](tests/test_harness_guards.py)
explicitly permits overshoot by one call. Day 4's `download_session_spans()` polls until
`Complete` or `Failed`, without a deadline. Bedrock job submission checks the spend confirmation,
while the AgentCore session branch checks live mode and a session ID.

**Suggestion:** Present pattern matching and prefix summaries as workshop mechanisms with
specific limitations. Add adversarial paraphrases, useful policy text that must survive
sanitization, and a compaction case whose required fact falls outside the retained prefix.
Reserve output allowance when describing a strict token budget, distinguish estimates from
billing, and add a bounded polling policy. Make cost-incurring notebook extensions follow a
consistent explicit-consent convention.

**Acceptance:** Examples establish both success and limitations; timeout and budget behavior match
the written promises. Verify current AWS/package contracts before implementing live changes,
record pins/deviations in `docs/DECISIONS.md`, and keep all required checks offline.

## 10. Add Editorial and Rendered-Content Quality Gates

**Evidence:** `scripts/check_lecture_refs.py` currently passes with zero errors/warnings across
eight documents. It checks selected paths and symbols, not casing, schedules, pedagogical
alignment, or rendered layout, and skips symbol checking inside code fences. The PR workflow
executes notebooks and renders Marp, while [the Pages workflow](.github/workflows/pages.yml)
renders the complete Quarto site only on `main` or manual dispatch.

**Suggestion:** Add a lightweight prose/terminology check over source Markdown, Quarto files,
YAML labels, and notebook Markdown cells. Exclude code, URLs, identifiers, and generated outputs.
Validate heading structure, canonical labels, local links/anchors, and stale placeholders.
Add a PR site build and focused browser checks for each day in desktop/mobile and light/dark
views: titles, tables, diagrams, code overflow, and solution navigation. Keep prose judgments
reviewable; automatic title conversion should not rewrite technical names.

**Acceptance:** An intentionally wrong product spelling, mismatched day title, broken local link,
or failed site render is detected before merge. Manual review still checks explanation quality.

## 11. Give Every Day the Same Teaching Structure

**Evidence:** Full lecture notes exist for all four days, but the slide sources contain a kickoff
deck and a Day 1 motivational deck. Later days have no equivalent teaching decks. Days 1–2
still list a two-hour morning lab ending at 13:00, while the instructor guide and Days 3–4 use
90 minutes ending at 12:30; `docs/TASKS.md` already flags this mismatch.

**Suggestion:** Use the same daily structure: prerequisite recap, one motivating failure,
objectives, worked example, prediction checkpoint, independent task, expected evidence, review,
and bridge to the next day. Add concise Day 2–4 teaching decks with speaker notes and executable
repository references. Distinguish what is read beforehand from what is taught in 90 minutes.
Use one timetable source and validate all repeated schedules against it.

**Acceptance:** An instructor can teach each day from its own materials, and every objective maps
to a specific exercise or observable demonstration with a realistic time allocation.

## 12. Reduce Repetition and Improve the Hand-Off Between Days

**Evidence:** Titles, setup instructions, version pins, schedules, and day summaries are repeated
across the README, landing page, guide, configuration, slides, and notebook setup cells. The
landing page says Node 20 or newer, while the guide and CI use Node 22. Day 3's first fix-log row
also runs the entire suite once per metric inside a comprehension instead of reusing one result.

**Suggestion:** Centralize stable workshop metadata and validate copied dependency requirements
against the pinned configuration. Use installed Node binaries with offline-enforcing commands
after setup; `--prefer-offline` alone permits a network fallback. Save day-specific artifacts in
a documented directory so Day 4 actually consumes earlier participant work. Cache each notebook
suite result once for its tables and charts, and distinguish quick lab checks from the full gate.
Describe the manual live workflow consistently despite its retained “nightly” filename.

**Acceptance:** A participant follows one setup path, can resume a later day from saved artifacts,
and completes the required labs with network access disabled after dependencies are installed.

## Suggested Implementation Order

1. Correct the case-filtering lesson, threshold diagram/procedure, and gate evidence validation.
2. Establish the style guide and canonical labels; normalize titles, terminology, and schedules.
3. Rework Day 3 around a working extension and Day 4 around one assessed capstone.
4. Add strict solution-completion checks, editorial validation, and PR site previews.
5. Finish Day 2–4 decks, artifact hand-offs, and runtime/setup improvements.

For each teaching change, review the lecture, source notebook, generated student and solution
notebooks, website labels, and instructor notes together. Apply the repository's required
`make test` → `make baseline` process when metrics, rubrics, tool descriptions, or golden cases
change; explain the resulting baseline diff. Suggestions alone do not require a baseline update.

## Review Checks Performed

- Inspected the four lecture outlines, notebook exercise/check structure, later-day lab code,
  slide inventory, site styling/filtering, Makefile, workflows, and relevant gate/harness code.
- Counted generated student exercise cells: Day 1 has four; Days 2–4 have three each.
- Ran the existing lecture reference checker: eight documents, zero errors, zero warnings.
- Probed gate evaluation in memory with empty Promptfoo evidence and mismatched mode/dataset
  provenance; it returned no failures with baseline-equivalent headline metrics.
- No live AWS calls, resource creation, deployment, or baseline changes were performed.
