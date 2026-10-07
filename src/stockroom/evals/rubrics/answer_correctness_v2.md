# Rubric: answer correctness (v2)

**Task.** Decide whether the assistant's answer to the user's question is correct and complete,
using the reference answer (and, when given, the tool/context output) as ground truth.

Evaluate in this order:

1. **Key facts.** List the key facts in the reference answer: quantities, prices, dates, ids
   (SKU-####, ORD-####, RSR-####), statuses, policy numbers (days, percentages, thresholds).
   Every key fact must appear in the answer. Equivalent forms count ("thirty" = 30, "1,250" =
   1250, "$8.50" = "8.50 dollars").
2. **Contradictions.** The answer must not state anything that contradicts the reference or the
   context, and must not add unsupported claims presented as fact (invented dates, extensions,
   reservations, free services).
3. **Scope.** If the user asked for several things, all of them must be answered.
4. **Verbosity is not quality.** Do not reward length, politeness or formatting. A short answer
   containing every key fact is a 10.

Scoring (0–10): 10 = all key facts, no contradictions; 7–9 = all key facts but minor unsupported
detail; 4–6 = some key facts missing; 0–3 = wrong, contradictory, or refuses when it should answer.
**Pass** requires a score of 7 or more.

Return JSON: {"score": <0-10>, "pass": <true|false>, "rationale": "<one sentence citing the facts checked>"}
