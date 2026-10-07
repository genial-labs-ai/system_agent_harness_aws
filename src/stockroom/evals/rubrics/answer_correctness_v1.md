# Rubric: answer correctness (v1)

**Task.** Decide whether the assistant's answer to the user's question is correct, using the
reference answer as ground truth.

**Pass** if the answer conveys the main point of the reference answer.
**Fail** if the answer is off-topic or contradicts the reference answer.

Score from 0 to 10: 10 = fully correct, 5 = partially correct, 0 = wrong.

Return JSON: {"score": <0-10>, "pass": <true|false>, "rationale": "<one sentence>"}
