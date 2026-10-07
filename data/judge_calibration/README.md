# Judge calibration set v1

`calibration_v1.jsonl` holds 32 human-labelled (query, answer, context) triples for the Day 2 lab.
Each item records `expected_facts`, `forbidden_facts`, a binary `human_label` (pass/fail), a 1–5
`human_score` and a rationale. Items 25–32 belong to bias probes:

- `position:*` — the same two answers presented in both orders (pairwise judging).
- `verbosity:*` — identical facts, short versus padded wording.
- `self_pref:*` — identical answers attributed to the judge's own model family versus another.

Labels were written by the workshop authors from the context shown; they deliberately include
paraphrases (C21–C22), hallucinated extras (C09–C12), partial answers (C13–C16) and two "misleading
but string-correct" items (C23–C24) that rule-based judges cannot catch, so that agreement with the
human labels is informative rather than trivially 100%.
