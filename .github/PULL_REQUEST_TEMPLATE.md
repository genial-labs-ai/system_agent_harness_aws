## What changed

<!-- One paragraph. Link the issue if there is one. -->

## Why

<!-- The failure mode, lab, or lecture point this serves. -->

## Checklist

- [ ] `make ci` passes locally with no AWS credentials and `STOCKROOM_WEAKNESSES` unset
- [ ] No new model IDs, regions or credentials in code (config comes from the environment)
- [ ] No `pass`, `...` or `TODO` outside tagged `exercise` cells in `notebooks/src/*.py`
- [ ] Teaching materials (`lectures/`, `slides/`, `docs/INSTRUCTOR_GUIDE.md`) still pass `uv run python scripts/check_lecture_refs.py`
- [ ] Any figure quoted in teaching materials was reproduced from this branch in mock mode
- [ ] `docs/DECISIONS.md` has an entry for each deviation from the brief or each new version pin

## Metrics

<!--
If this PR touches a metric, rubric, tool description, golden case or threshold:
paste the "before vs. after" rows from the sticky CI comment (or `reports/summary.md`),
say whether you ran `make baseline`, and explain the diff.
Otherwise write "No metric change expected".
-->
