# Rubric: faithfulness to tool output (v1)

**Task.** Decide whether every factual claim in the assistant's answer is supported by the
context (the tool results and retrieved policy sections shown).

1. Split the answer into atomic claims.
2. For each claim, decide if it can be directly inferred from the context.
3. Score = (supported claims / total claims) × 10. Claims that only restate the question or
   politely decline are ignored.

**Pass** requires a score of 8 or more and no claim that contradicts the context.

Return JSON: {"score": <0-10>, "pass": <true|false>, "rationale": "<which claims were unsupported>"}
