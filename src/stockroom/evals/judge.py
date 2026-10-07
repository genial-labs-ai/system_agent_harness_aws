"""LLM-as-a-judge: a Bedrock-backed judge, a deterministic fake, and bridges to DeepEval/RAGAS.

* Rubrics are versioned markdown files in ``rubrics/`` (``answer_correctness_v1.md`` …).
* :class:`FakeJudge` is used in mock mode. It is deliberately simple and *imperfect*: rubric v1
  passes any answer that contains at least half of the key facts (so it is verbosity-biased and
  blind to contradictions); v2 requires every key fact and no contradiction. Calibrating the fake
  judge against the human labels in ``data/judge_calibration`` therefore shows real movement.
* :class:`BedrockJudge` sends the rubric and the inputs to ``JUDGE_MODEL_ID`` via Converse and
  parses a JSON verdict. By default it is a different model family from the agent (README).
* :class:`StockroomDeepEvalLLM` and :class:`StockroomRagasLLM` adapt either judge to the custom
  model interfaces of DeepEval (``DeepEvalBaseLLM``) and RAGAS (``InstructorBaseRagasLLM``), so
  no OpenAI key is ever needed.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, Field

from stockroom.config import Mode, StockroomConfig

RUBRICS_DIR = Path(__file__).resolve().parent / "rubrics"
NUMBER_WORDS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "twelve": "12",
    "fifteen": "15",
    "twenty": "20",
    "thirty": "30",
    "forty": "40",
    "fifty": "50",
    "sixty": "60",
    "ninety": "90",
    "hundred": "100",
}
_FACT_PATTERNS = re.compile(
    r"(?i)(SKU-\d{4}|ORD-\d{4}|RSR-\d{4}|\$\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?%"
    r"|\d{4}-\d{2}-\d{2}|\b\d[\d,]*(?:\.\d+)?\b|\b(?:shipped|delivered|backordered|cancelled"
    r"|processing|pending_approval|rejected|out of stock|urgent|standard|manager approval)\b)"
)


@lru_cache(maxsize=16)
def load_rubric(name: str, version: str) -> str:
    path = RUBRICS_DIR / f"{name}_{version}.md"
    if not path.exists():
        raise FileNotFoundError(f"rubric {name}_{version}.md not found in {RUBRICS_DIR}")
    return path.read_text(encoding="utf-8")


def normalize(text: str) -> str:
    """Lower-case, number words → digits, drop thousands separators, collapse whitespace."""
    out = text.lower()
    out = re.sub(r"(\d),(\d{3})", r"\1\2", out)
    out = re.sub(r"\b(" + "|".join(NUMBER_WORDS) + r")\b", lambda m: NUMBER_WORDS[m.group(1)], out)
    out = re.sub(r"\s+", " ", out)
    return out.strip()


def extract_key_facts(text: str) -> list[str]:
    """Numbers, ids, money, percentages, dates and status words from a reference answer."""
    facts: list[str] = []
    for m in _FACT_PATTERNS.finditer(text):
        fact = m.group(0).strip()
        if len(fact) == 1 and fact.isdigit():
            continue  # single digits are too weak as facts
        if fact not in facts:
            facts.append(fact)
    return facts


def fact_present(fact: str, answer: str) -> bool:
    """Substring match after normalisation; numeric facts must not be embedded in longer numbers
    (so ``42`` does not match ``420`` and ``125`` does not match ``1250``)."""
    needle = normalize(fact)
    hay = normalize(answer)
    if not needle:
        return False
    if needle[0].isdigit() or needle[-1].isdigit():
        pattern = r"(?<![\d.])" + re.escape(needle) + r"(?![\d])"
        return re.search(pattern, hay) is not None
    return needle in hay


class JudgeInput(BaseModel):
    query: str
    answer: str
    reference: str | None = None
    context: str | None = None
    expected_facts: list[str] = Field(default_factory=list)
    forbidden_facts: list[str] = Field(default_factory=list)
    rubric: str = "answer_correctness"


class JudgeVerdict(BaseModel):
    score: float  # 0.0 – 1.0
    passed: bool
    rationale: str
    rubric: str
    rubric_version: str
    judge_name: str
    raw: str | None = None


class Judge(Protocol):
    name: str
    rubric_version: str

    def grade(self, item: JudgeInput) -> JudgeVerdict: ...


class FakeJudge:
    """Deterministic judge for mock mode (see module docstring for the v1/v2 difference)."""

    name = "fake-judge"

    def __init__(self, rubric_version: str = "v2") -> None:
        if rubric_version not in {"v1", "v2"}:
            raise ValueError("FakeJudge supports rubric versions v1 and v2")
        self.rubric_version = rubric_version

    def grade(self, item: JudgeInput) -> JudgeVerdict:
        facts = list(item.expected_facts) or extract_key_facts(item.reference or "")
        if item.rubric == "faithfulness":
            return self._grade_faithfulness(item)
        found = [f for f in facts if fact_present(f, item.answer)]
        missing = [f for f in facts if f not in found]
        forbidden_hit = [f for f in item.forbidden_facts if fact_present(f, item.answer)]
        fraction = (len(found) / len(facts)) if facts else 1.0
        if self.rubric_version == "v1":
            passed = fraction >= 0.5
            score = fraction
            rationale = f"v1: {len(found)}/{len(facts)} key facts present"
        else:
            passed = not missing and not forbidden_hit
            score = max(0.0, fraction - (0.5 if forbidden_hit else 0.0))
            rationale = f"v2: {len(found)}/{len(facts)} key facts present"
            if missing:
                rationale += f"; missing {missing}"
            if forbidden_hit:
                rationale += f"; contradiction/forbidden content {forbidden_hit}"
        return JudgeVerdict(
            score=round(score, 4),
            passed=passed,
            rationale=rationale,
            rubric=item.rubric,
            rubric_version=self.rubric_version,
            judge_name=self.name,
        )

    def _grade_faithfulness(self, item: JudgeInput) -> JudgeVerdict:
        context = normalize(item.context or "")
        claims = split_claims(item.answer)
        if not claims:
            return JudgeVerdict(
                score=1.0,
                passed=True,
                rationale="no factual claims",
                rubric=item.rubric,
                rubric_version=self.rubric_version,
                judge_name=self.name,
            )
        supported = 0
        unsupported: list[str] = []
        for claim in claims:
            facts = extract_key_facts(claim)
            ok = all(normalize(f) in context for f in facts) if facts else True
            if ok:
                supported += 1
            else:
                unsupported.append(claim[:60])
        score = supported / len(claims)
        return JudgeVerdict(
            score=round(score, 4),
            passed=score >= 0.8,
            rationale=f"{supported}/{len(claims)} claims supported"
            + (f"; unsupported: {unsupported}" if unsupported else ""),
            rubric=item.rubric,
            rubric_version=self.rubric_version,
            judge_name=self.name,
        )


def split_claims(text: str) -> list[str]:
    parts = re.split(r"(?<=[.;!?])\s+|\n+", text.strip())
    return [p.strip() for p in parts if len(p.strip()) > 3]


JUDGE_PROMPT = """{rubric}

---
User question:
{query}

Assistant answer:
{answer}

Reference answer:
{reference}

Context (tool results / retrieved policy text):
{context}
---
Respond with only the JSON object."""


class BedrockJudge:
    """Live judge via the Converse API; expects a JSON object in the model's reply."""

    name = "bedrock-judge"

    def __init__(
        self,
        config: StockroomConfig,
        model_id: str | None = None,
        rubric_version: str = "v2",
        client: Any | None = None,
    ) -> None:
        from stockroom.agent.bedrock_adapter import make_bedrock_runtime_client

        self.config = config
        _, judge_model = config.require_live_models()
        self.model_id = model_id or judge_model
        self.rubric_version = rubric_version
        self._client = client or make_bedrock_runtime_client(config)

    def complete(self, prompt: str, max_tokens: int = 512) -> str:
        resp = self._client.converse(
            modelId=self.model_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": max_tokens, "temperature": 0.0},
        )
        blocks = resp.get("output", {}).get("message", {}).get("content", [])
        return "".join(b.get("text", "") for b in blocks)

    def grade(self, item: JudgeInput) -> JudgeVerdict:
        rubric = load_rubric(item.rubric, self.rubric_version)
        prompt = JUDGE_PROMPT.format(
            rubric=rubric,
            query=item.query,
            answer=item.answer,
            reference=item.reference or "(none)",
            context=item.context or "(none)",
        )
        raw = self.complete(prompt)
        data = parse_json_object(raw)
        score10 = float(data.get("score", 0))
        score = max(0.0, min(1.0, score10 / 10.0 if score10 > 1 else score10))
        passed = bool(data.get("pass", score >= 0.7))
        return JudgeVerdict(
            score=round(score, 4),
            passed=passed,
            rationale=str(data.get("rationale", ""))[:500],
            rubric=item.rubric,
            rubric_version=self.rubric_version,
            judge_name=f"{self.name}:{self.model_id}",
            raw=raw[:2000],
        )


def parse_json_object(text: str) -> dict[str, Any]:
    """Extract the first JSON object from a model reply (tolerates code fences and prose)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fence.group(1) if fence else None
    if candidate is None:
        start = text.find("{")
        end = text.rfind("}")
        candidate = text[start : end + 1] if start != -1 and end > start else "{}"
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def make_judge(config: StockroomConfig, rubric_version: str = "v2") -> Judge:
    if config.mode is Mode.LIVE:
        return BedrockJudge(config, rubric_version=rubric_version)
    return FakeJudge(rubric_version=rubric_version)


# --- DeepEval bridge --------------------------------------------------------------------------

T = TypeVar("T", bound=BaseModel)
_SECTION = re.compile(r"^(Input|Actual Output|Expected Output|Context|Retrieval Context):\n", re.M)


def parse_geval_test_case(prompt: str) -> dict[str, str]:
    """Pull the labelled sections DeepEval's GEval puts in its prompt (``Input:\\n…``)."""
    sections: dict[str, str] = {}
    matches = list(_SECTION.finditer(prompt))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(prompt)
        body = prompt[start:end]
        body = re.split(r"\n\s*\n(?:Parameters:|Additional Context:|---|\*\*)", body)[0]
        sections[m.group(1)] = body.strip()
    return sections


def schema_fill_from_verdict[S: BaseModel](
    schema: type[S], verdict: JudgeVerdict, strict: bool
) -> S:
    fields = set(schema.model_fields)
    if fields >= {"reason", "score"}:
        score = (1 if verdict.passed else 0) if strict else round(verdict.score * 10)
        return schema(reason=verdict.rationale, score=score)  # type: ignore[call-arg]
    if "steps" in fields:
        return schema(  # type: ignore[call-arg]
            steps=[
                "Identify the key facts (ids, quantities, dates, statuses) in the expected output.",
                "Check that each key fact appears in the actual output in an equivalent form.",
                "Check the actual output for claims that contradict the expected output.",
                "Score 10 when all key facts are present and nothing contradicts; lower otherwise.",
            ]
        )
    raise TypeError(f"FakeJudge cannot fill schema {schema.__name__}")


def _import_deepeval_base() -> type:
    from deepeval.models import DeepEvalBaseLLM

    return DeepEvalBaseLLM


class StockroomDeepEvalLLM(_import_deepeval_base()):  # type: ignore[misc]
    """DeepEval custom model that routes GEval prompts to the Stockroom judge.

    * Live: the full GEval prompt goes to Bedrock and the JSON reply is parsed into ``schema``.
    * Mock: the test-case fields are parsed out of the prompt and graded by :class:`FakeJudge`.
    """

    def __init__(self, config: StockroomConfig, judge: Judge | None = None) -> None:
        self.config = config
        self.judge = judge or make_judge(config)
        super().__init__(model=self.get_model_name())

    def load_model(self) -> Judge:
        return self.judge

    def get_model_name(self) -> str:
        return f"stockroom-{self.judge.name}-{self.judge.rubric_version}"

    def generate(self, prompt: str, schema: type[T] | None = None) -> str | T:
        if isinstance(self.judge, BedrockJudge):
            if schema is None:
                return self.judge.complete(prompt)
            instruction = (
                f"\n\nReturn only a JSON object matching this schema: "
                f"{json.dumps(schema.model_json_schema())}"
            )
            data = parse_json_object(self.judge.complete(prompt + instruction, max_tokens=1024))
            return schema.model_validate(data)
        sections = parse_geval_test_case(prompt)
        item = JudgeInput(
            query=sections.get("Input", ""),
            answer=sections.get("Actual Output", ""),
            reference=sections.get("Expected Output") or None,
            context=sections.get("Context") or sections.get("Retrieval Context") or None,
            rubric="faithfulness" if "faithful" in prompt.lower()[:400] else "answer_correctness",
        )
        verdict = self.judge.grade(item)
        if schema is None:
            return json.dumps({"reason": verdict.rationale, "score": round(verdict.score * 10)})
        strict = "STRICTLY EITHER 1" in prompt
        return schema_fill_from_verdict(schema, verdict, strict)

    async def a_generate(self, prompt: str, schema: type[T] | None = None) -> str | T:
        return self.generate(prompt, schema)


# --- RAGAS bridge -----------------------------------------------------------------------------


def _import_ragas_base() -> type:
    from ragas.llms.base import InstructorBaseRagasLLM

    return InstructorBaseRagasLLM


def _last_json_object(text: str) -> dict[str, Any]:
    """The last balanced JSON object in a RAGAS prompt is the input block."""
    depth = 0
    start = -1
    last: dict[str, Any] = {}
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                try:
                    obj = json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    last = obj
    return last


class StockroomRagasLLM(_import_ragas_base()):  # type: ignore[misc]
    """RAGAS structured-output LLM over the Stockroom judge (no litellm/langchain needed)."""

    def __init__(self, config: StockroomConfig, judge: Judge | None = None) -> None:
        self.config = config
        self.judge = judge or make_judge(config)

    def generate(self, prompt: str, response_model: type[T]) -> T:
        if isinstance(self.judge, BedrockJudge):
            instruction = (
                "\n\nReturn only a JSON object matching this schema: "
                f"{json.dumps(response_model.model_json_schema())}"
            )
            data = parse_json_object(self.judge.complete(prompt + instruction, max_tokens=1024))
            return response_model.model_validate(data)
        return self._fake(prompt, response_model)

    async def agenerate(self, prompt: str, response_model: type[T]) -> T:
        return self.generate(prompt, response_model)

    def _fake(self, prompt: str, response_model: type[T]) -> T:
        payload = _last_json_object(prompt)
        fields = set(response_model.model_fields)
        if (
            fields == {"statements"}
            and response_model.model_fields["statements"].annotation is not None
        ):
            inner = getattr(
                response_model.model_fields["statements"].annotation, "__args__", (str,)
            )[0]
            if inner is str:
                return response_model(statements=split_claims(str(payload.get("answer", ""))))  # type: ignore[call-arg]
            context = normalize(str(payload.get("context", "")))
            rows = []
            for stmt in payload.get("statements", []):
                facts = extract_key_facts(stmt)
                ok = (
                    all(normalize(f) in context for f in facts)
                    if facts
                    else _overlap(stmt, context) >= 0.5
                )
                rows.append(
                    {
                        "statement": stmt,
                        "reason": "facts found in context" if ok else "not supported",
                        "verdict": int(ok),
                    }
                )
            return response_model(statements=rows)  # type: ignore[call-arg]
        if fields == {"reason", "verdict"}:
            context = str(payload.get("context", ""))
            target = str(payload.get("answer", "")) + " " + str(payload.get("question", ""))
            useful = _overlap(target, context) >= 0.2 or any(
                normalize(f) in normalize(context) for f in extract_key_facts(target)
            )
            return response_model(
                reason="shared key facts" if useful else "no overlap", verdict=int(useful)
            )  # type: ignore[call-arg]
        if fields == {"classifications"}:
            context = normalize(str(payload.get("context", "")))
            rows = []
            for stmt in split_claims(str(payload.get("answer", ""))):
                facts = extract_key_facts(stmt)
                ok = (
                    all(normalize(f) in context for f in facts)
                    if facts
                    else _overlap(stmt, context) >= 0.5
                )
                rows.append(
                    {
                        "statement": stmt,
                        "reason": "attributed" if ok else "not in context",
                        "attributed": int(ok),
                    }
                )
            return response_model(classifications=rows)  # type: ignore[call-arg]
        raise TypeError(f"fake RAGAS LLM cannot fill {response_model.__name__} ({sorted(fields)})")


def _overlap(a: str, b: str) -> float:
    from stockroom.agent.retrieval import content_tokens

    ta, tb = set(content_tokens(a)), set(content_tokens(b))
    return len(ta & tb) / len(ta) if ta else 0.0
