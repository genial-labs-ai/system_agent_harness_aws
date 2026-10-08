# %% [markdown]
# # Day 1 — Deterministic and RAG evaluations for the Stockroom agent
#
# **Learning objectives.** By the end of this notebook you can:
#
# 1. Explain why "it looked fine when I tried it" is not evidence, and replace it with measured runs.
# 2. Name the failure classes of a tool-using agent (wrong tool, wrong arguments, schema drift,
#    loops/retries, context growth, injection, refusal, hallucination) and recognise each one in
#    `evaluate_case` output.
# 3. Read and extend the golden set (`data/golden/`) and its dataset card.
# 4. Write assertion-based tests with plain `assert`s and with DeepEval (`GEval`,
#    `ToolCorrectnessMetric`) wired to a Bedrock-or-fake judge, never to a third-party API.
# 5. Evaluate the retrieval step with RAGAS (`Faithfulness`, `ContextPrecision`, `ContextRecall`)
#    over the local policy corpus, and know where a Bedrock Knowledge Base plugs in.
#
# **How this notebook is graded.** Four exercises are marked `Exercise N`. Each has a *check* cell
# with explicit success criteria; it prints `not solved yet` until your code passes. The notebook
# runs top to bottom even before you solve anything, so you can always re-run everything. The
# *Exercise checklist* cell near the end lists each exercise's status; run the notebook with
# `STOCKROOM_STRICT_EXERCISES=1` to turn any unsolved exercise into an error (self-assessment).
#
# Everything runs offline in **mock mode** (a deterministic fake model and judge). With AWS
# credentials, `AGENT_MODEL_ID` and `JUDGE_MODEL_ID` set, the same cells run against Amazon Bedrock.

# %% [markdown]
# ## Setup
#
# Works locally (`make setup`), in Google Colab and in SageMaker Studio. The install is a no-op
# when the `stockroom` package is already importable; otherwise the repository is cloned and
# installed in editable mode with the pinned evaluation libraries from `pyproject.toml`.

# %%
import importlib.util
import os
import subprocess
from pathlib import Path

# Published repository (docs/DECISIONS.md entry 2). Only used when the package is not already
# installed, e.g. in a fresh Colab or SageMaker Studio kernel.
REPO_URL = "https://github.com/genial-labs-ai/system_agent_harness_aws"
REPO_DIR = Path("stockroom-workshop")

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")  # keep DeepEval fully offline

if importlib.util.find_spec("stockroom") is None:
    if not REPO_DIR.exists():
        subprocess.run(["git", "clone", "--depth", "1", REPO_URL, str(REPO_DIR)], check=True)
    # Pins mirror pyproject.toml; everything else (boto3, pydantic, OpenTelemetry, ...) is
    # resolved from the package metadata by the editable install.
    %pip install -q "deepeval==4.2.8" "ragas==0.4.3" "mcp==2.3.0" "jsonschema==4.26.0" "pyyaml==6.0.3" "langchain-community>=0.3.27,<0.4" "pandas>=2.2" "matplotlib>=3.9" -e ./stockroom-workshop
print("stockroom importable:", importlib.util.find_spec("stockroom") is not None)

# %% [markdown]
# ### Which mode am I in?
#
# `detect_mode()` honours `STOCKROOM_MODE` when it is set; otherwise it picks **live** only when AWS
# credentials *and* both model IDs are present, and falls back to **mock** (and says so).

# %%
from stockroom.config import detect_mode

config = detect_mode()

# %%
import pandas as pd

from stockroom.agent.harness import Harness
from stockroom.config import StockroomConfig
from stockroom.evals.golden import GoldenCase, golden_by_id, load_golden
from stockroom.evals.judge import make_judge
from stockroom.evals.metrics import OutputEvaluator, evaluate_case
from stockroom.exercises import exercise_passed, exercise_pending, exercise_summary

pd.set_option("display.max_colwidth", 80)
pd.set_option("display.width", 160)

cases = load_golden()
by_id = golden_by_id()
judge = make_judge(config)
harness = Harness(config)
print(f"{len(cases)} golden cases; judge = {judge.name} rubric {judge.rubric_version}")

# %% [markdown]
# ## 1. Vibe-driven vs measured development
#
# The usual loop is: change a prompt, try three questions by hand, nod, ship. Let's do exactly that
# first — run a few golden queries and *read* the answers.

# %%
vibe_ids = ["G007", "G014", "G026"]
for case_id in vibe_ids:
    case = by_id[case_id]
    run = harness.run(case.query, case_id=case.id)
    print(f"{case.id} | {case.query}\n  -> {run.final_answer}\n")

# %% [markdown]
# Those all read well. Now the measured version: the same runs scored by `evaluate_case`, which
# combines deterministic checks (tool selection, argument correctness, termination reason, facts in
# the answer) with the judge. Nothing here depends on how an answer *reads*.

# %%
SCORE_COLUMNS = [
    "case_id",
    "category",
    "tool_selection",
    "argument_correctness",
    "termination_match",
    "answer_correctness_deterministic",
    "judge_passed",
    "model_calls",
    "input_tokens",
]


def score_cases(cfg: StockroomConfig, case_ids: list[str]) -> pd.DataFrame:
    """Run and score a list of golden cases under one configuration."""
    h = Harness(cfg)
    rows = [
        evaluate_case(by_id[cid], h.run(by_id[cid].query, case_id=cid), judge).model_dump()
        for cid in case_ids
    ]
    return pd.DataFrame(rows)[SCORE_COLUMNS]


score_cases(config, vibe_ids)

# %% [markdown]
# ## 2. A failure taxonomy you can point at
#
# The harness ships with four *seeded weaknesses* behind feature flags
# (`StockroomConfig.mock(weaknesses=...)`, or `STOCKROOM_WEAKNESSES` in the environment). Each one
# reproduces a failure class that matters in production:
#
# | flag | what it breaks | failure class | metric that catches it |
# |---|---|---|---|
# | `ambiguous_tool_desc` | `search_products` claims to cover stock and orders too | wrong tool | `tool_selection` |
# | `oversized_payload` | `search_products` returns every field of the whole catalogue | context growth | `termination_reason`, `context_growth_ratio` |
# | `naive_retry` | the model re-issues an identical call after a transient error | loops / retries | `loop_findings`, `termination_reason` |
# | `injection_unguarded` | tool output is not quarantined, so a planted instruction is obeyed | indirect prompt injection | `must_not_call_ok`, `forbidden_found` |
#
# The remaining classes in the golden set (schema drift, refusal, hallucination, ambiguous query)
# are exercised by scripted cases rather than flags. Let's toggle each flag on one representative
# case and compare with the fixed default.

# %%
TAXONOMY_DEMO = {
    "ambiguous_tool_desc": "G014",  # order status
    "oversized_payload": "G049",  # two catalogue searches
    "naive_retry": "G043",  # legacy order shard
    "injection_unguarded": "G041",  # policy question over the poisoned document
}
DEMO_COLUMNS = [
    "flag",
    "case_id",
    "tool_names",
    "tool_selection",
    "termination_reason",
    "loop_findings",
    "context_growth_ratio",
    "must_not_call_ok",
    "forbidden_found",
    "judge_passed",
]

rows = []
for flag, case_id in TAXONOMY_DEMO.items():
    case = by_id[case_id]
    for label, cfg in (("fixed", config), (flag, config.replace(weaknesses=flag))):
        run = Harness(cfg).run(case.query, case_id=case.id)
        scores = evaluate_case(case, run, judge).model_dump()
        scores["flag"] = label
        rows.append(scores)
taxonomy_df = pd.DataFrame(rows)[DEMO_COLUMNS]
taxonomy_df

# %% [markdown]
# Read the table row by row: every flagged run differs from its fixed twin in the column named in the
# taxonomy table above. Notice that `judge_passed` alone would have missed two of the four
# (`ambiguous_tool_desc` still produces a plausible answer; `naive_retry` fails only on termination).
# That is the whole argument for evaluating *trajectories*, not just final answers.

# %% [markdown]
# ## 3. Golden-set tour
#
# `data/golden/stockroom_golden_v1.jsonl` holds 50 hand-labelled cases. Expected tools, arguments
# and facts were derived from the data files, not from running the agent (no label leakage). The
# dataset card documents sourcing, schema, coverage and known limitations.

# %%
golden_df = pd.DataFrame([c.model_dump() for c in cases])
print(golden_df["category"].value_counts().to_string())
print()
print("trajectory match modes:", golden_df["trajectory_match_mode"].value_counts().to_dict())
print("cases with a retrieval reference:", int(golden_df["retrieval"].notna().sum()))
print("cases with a scripted mock model:", int(golden_df["mock_script"].notna().sum()))

# %%
example = by_id["G033"]
print(example.model_dump_json(indent=2))

# %%
card = (config.data_dir / "golden" / "DATASET_CARD.md").read_text(encoding="utf-8")
print(card[: card.index("## Schema")])

# %% [markdown]
# ### Exercise 1 — write a new golden case and validate it
#
# Add a case for an order that the set does not cover yet: `ORD-1006`. Derive the expected facts
# from `data/orders.json` (look it up below), **not** from what the agent happens to answer.
#
# Success criteria (the check cell):
# * `new_golden_case()` returns a `GoldenCase` whose `id` and `query` are not already in the set;
# * `category` is one of the dataset-card categories;
# * run through the harness it scores `tool_selection == 1.0` and every expected fact is present.

# %%
import json

orders = {o["order_id"]: o for o in json.loads((config.data_dir / "orders.json").read_text())}
print({k: v for k, v in orders["ORD-1006"].items() if k != "items"})

# %% tags=["exercise"]
def new_golden_case() -> GoldenCase | None:
    """Return a GoldenCase for a query about ORD-1006 (None = not solved yet)."""
    # TODO: build and return GoldenCase(id=..., category=..., query=..., expected_tools=[...],
    #       expected_facts=[...], reference_answer=...). Use ExpectedTool for expected_tools.
    return None


# %% tags=["solution"]
from stockroom.evals.golden import ExpectedTool


def new_golden_case() -> GoldenCase | None:
    """Return a GoldenCase for a query about ORD-1006 (None = not solved yet)."""
    return GoldenCase(
        id="G051",
        category="order_status",
        query="Is order ORD-1006 still going ahead?",
        expected_tools=[ExpectedTool(name="get_order_status", args={"order_id": "ORD-1006"})],
        trajectory_match_mode="exact",
        expected_facts=["cancelled"],
        forbidden_facts=["shipped", "delivered"],
        reference_answer="Order ORD-1006 for Sofia Petrov is cancelled and no longer active.",
        tags=["day1-exercise"],
    )


# %% tags=["check"]
from stockroom.evals.metrics import tool_selection_score

KNOWN_CATEGORIES = set(golden_df["category"])
candidate = new_golden_case()
if candidate is None:
    exercise_pending("day1.ex1")
else:
    assert isinstance(candidate, GoldenCase), "return a GoldenCase instance"
    assert candidate.id not in by_id, f"{candidate.id} already exists; pick a new id"
    assert candidate.query not in set(golden_df["query"]), "that query is already in the set"
    assert candidate.category in KNOWN_CATEGORIES, f"unknown category {candidate.category}"
    assert candidate.expected_facts, "add at least one expected fact"
    run = harness.run(candidate.query, case_id=candidate.id)
    check = OutputEvaluator.evaluate(candidate, run)
    assert tool_selection_score(candidate, run) == 1.0, f"tools called: {run.tool_names}"
    assert check.passed, f"facts missing {check.facts_missing}, forbidden {check.forbidden_found}"
    exercise_passed("day1.ex1", f"{candidate.id} -> {run.final_answer}")

# %% [markdown]
# ## 4. Assertion-based tests
#
# The cheapest evaluation is a plain `assert`. `tests/test_trajectory_regression.py` parametrises
# the golden set into pytest; here is the same idea in a function you can run on any case.

# %%
from stockroom.agent.types import RunResult
from stockroom.evals.metrics import argument_correctness_score


def check_golden_case(case: GoldenCase, run: RunResult) -> None:
    """pytest-style hard assertions over one run (raise AssertionError on the first failure)."""
    assert run.termination_reason.value == case.expected_termination, (
        f"terminated {run.termination_reason.value}, expected {case.expected_termination}"
    )
    assert tool_selection_score(case, run) == 1.0, f"tools {run.tool_names}"
    assert argument_correctness_score(case, run) == 1.0, "wrong arguments"
    output = OutputEvaluator.evaluate(case, run)
    assert not output.must_not_call_violations, f"forbidden tools {output.must_not_call_violations}"
    assert not output.facts_missing, f"missing facts {output.facts_missing}"
    assert not output.forbidden_found, f"forbidden content {output.forbidden_found}"


def run_assertions(cfg: StockroomConfig, case_ids: list[str]) -> pd.DataFrame:
    h = Harness(cfg)
    rows = []
    for cid in case_ids:
        case = by_id[cid]
        run = h.run(case.query, case_id=cid)
        try:
            check_golden_case(case, run)
            rows.append({"case_id": cid, "result": "pass", "detail": ""})
        except AssertionError as exc:
            rows.append({"case_id": cid, "result": "FAIL", "detail": str(exc)})
    return pd.DataFrame(rows)


sample_ids = ["G007", "G014", "G020", "G033", "G037", "G041", "G043", "G045"]
print("fixed configuration")
display(run_assertions(config, sample_ids))
print("with ambiguous_tool_desc")
display(run_assertions(config.replace(weaknesses="ambiguous_tool_desc"), sample_ids))

# %% [markdown]
# ### The same checks with DeepEval
#
# DeepEval gives you the same two ideas as reusable metrics: `ToolCorrectnessMetric` compares the
# tools called with the expected tools, and `GEval` grades the answer against a rubric with an LLM
# judge. `StockroomDeepEvalLLM` is a `DeepEvalBaseLLM` that routes every judge prompt to our judge
# (`BedrockJudge` in live mode, `FakeJudge` in mock mode) — no third-party API key is involved.

# %%
from deepeval.metrics import GEval, ToolCorrectnessMetric
from deepeval.test_case import LLMTestCase, SingleTurnParams, ToolCall

from stockroom.evals.judge import StockroomDeepEvalLLM, load_rubric
from stockroom.evals.metrics import context_from_run

deepeval_llm = StockroomDeepEvalLLM(config, judge)


def deepeval_test_case(case: GoldenCase, run: RunResult) -> LLMTestCase:
    return LLMTestCase(
        input=case.query,
        actual_output=run.final_answer,
        expected_output=case.reference_answer,
        context=[context_from_run(run)] if run.executed_tool_calls else None,
        tools_called=[
            ToolCall(name=r.name, input_parameters=r.arguments) for r in run.executed_tool_calls
        ],
        expected_tools=[ToolCall(name=t.name, input_parameters=t.args) for t in case.expected_tools],
        name=case.id,
    )


def answer_correctness_metric() -> GEval:
    return GEval(
        name="Answer correctness (rubric v2)",
        criteria=load_rubric("answer_correctness", "v2"),
        evaluation_params=[
            SingleTurnParams.INPUT,
            SingleTurnParams.ACTUAL_OUTPUT,
            SingleTurnParams.EXPECTED_OUTPUT,
        ],
        model=deepeval_llm,
        threshold=0.7,
        async_mode=False,
    )


case = by_id["G033"]
run = harness.run(case.query, case_id=case.id)
tc = deepeval_test_case(case, run)

tool_metric = ToolCorrectnessMetric(model=deepeval_llm, should_consider_ordering=True)
tool_metric.measure(tc)
geval = answer_correctness_metric()
geval.measure(tc)
print(f"ToolCorrectness: score={tool_metric.score} success={tool_metric.is_successful()}")
print(f"GEval:           score={geval.score} success={geval.is_successful()} ({geval.reason})")

# %% [markdown]
# And a deliberately wrong answer, to see the judge metric fail (the reference says 42 units):

# %%
wrong = LLMTestCase(
    input=by_id["G007"].query,
    actual_output="SKU-1015 has 420 units in stock.",
    expected_output=by_id["G007"].reference_answer,
)
geval_wrong = answer_correctness_metric()
geval_wrong.measure(wrong)
print(f"GEval on a wrong answer: score={geval_wrong.score} success={geval_wrong.is_successful()}")
assert not geval_wrong.is_successful()

# %% [markdown]
# ### Exercise 2 — add an `expected_facts` assertion
#
# Case `G013` asks *where* `SKU-1022` is stored and only checks the aisle. Extend it so the bin
# number is asserted as well (look the product up in `data/catalogue.json`).
#
# Success criteria: `extra_facts_for_g013()` returns a non-empty list of facts that are **not**
# already in the case, and the extended case still passes `OutputEvaluator` on a real run; the check
# also confirms that a wrong bin number would fail.

# %%
catalogue = {p["sku"]: p for p in json.loads((config.data_dir / "catalogue.json").read_text())}
print(by_id["G013"].query, "->", by_id["G013"].expected_facts)
print("SKU-1022 location:", catalogue["SKU-1022"]["location"])

# %% tags=["exercise"]
def extra_facts_for_g013() -> list[str]:
    """Additional expected_facts for G013 (empty list = not solved yet)."""
    # TODO: return the fact(s) that pin the bin number, e.g. ["bin <n>"].
    return []


# %% tags=["solution"]
def extra_facts_for_g013() -> list[str]:
    """Additional expected_facts for G013 (empty list = not solved yet)."""
    return ["bin 2"]


# %% tags=["check"]
extra = extra_facts_for_g013()
if not extra:
    exercise_pending("day1.ex2")
else:
    base = by_id["G013"]
    assert not set(extra) & set(base.expected_facts), "those facts are already asserted"
    extended = base.model_copy(update={"expected_facts": [*base.expected_facts, *extra]})
    run = harness.run(extended.query, case_id=extended.id)
    result = OutputEvaluator.evaluate(extended, run)
    assert result.passed, f"facts missing from the answer: {result.facts_missing}"
    wrong_bin = base.model_copy(update={"expected_facts": [*base.expected_facts, "bin 99"]})
    assert not OutputEvaluator.evaluate(wrong_bin, run).passed, "a wrong bin should fail"
    exercise_passed("day1.ex2", f"facts {extended.expected_facts} all present in -> {run.final_answer}")

# %% [markdown]
# ## 5. RAG evaluation of the policy retriever
#
# `search_policy_docs` is a small BM25 retriever over `data/policy_docs/` (six markdown files split
# at `## N.` headings; chunk ids look like `returns_policy.md#1`). Ten golden cases carry a
# `retrieval` block with the reference chunk and a reference sentence, so we can evaluate the
# retrieval step on its own:
#
# * **hit@3** (deterministic): is the reference chunk among the top-3 results?
# * **ContextPrecision / ContextRecall** (RAGAS, judge-based): are the retrieved chunks relevant to
#   the reference, and does the reference's content appear in them?
# * **Faithfulness** (RAGAS): is the agent's final answer supported by the retrieved chunks?
#
# `StockroomRagasLLM` adapts our judge to RAGAS' `InstructorBaseRagasLLM` interface, so again no
# third-party API key is needed. RAGAS metrics are async, hence the top-level `await`.

# %%
from ragas.metrics.collections import ContextPrecision, ContextRecall, Faithfulness

from stockroom.agent.tools import StockroomData
from stockroom.evals.judge import StockroomRagasLLM

data = StockroomData.load(config.data_dir)
index = data.index
ragas_llm = StockroomRagasLLM(config, judge)
rag_cases = [c for c in cases if c.retrieval is not None]
print(f"{len(index.chunks)} chunks indexed; {len(rag_cases)} golden cases with retrieval references")


def retrieve(query: str, k: int = 3) -> list[tuple[str, str]]:
    """(chunk_id, text) pairs from the local BM25 index."""
    return [(chunk.chunk_id, chunk.text) for chunk, _score in index.search(query, k=k)]


async def rag_scores(case: GoldenCase, contexts: list[tuple[str, str]]) -> dict[str, object]:
    assert case.retrieval is not None
    ids = [cid for cid, _ in contexts]
    texts = [text for _, text in contexts]
    run = harness.run(case.query, case_id=case.id)
    precision = await ContextPrecision(llm=ragas_llm).ascore(
        user_input=case.query, reference=case.retrieval.reference, retrieved_contexts=texts
    )
    recall = await ContextRecall(llm=ragas_llm).ascore(
        user_input=case.query, reference=case.retrieval.reference, retrieved_contexts=texts
    )
    faith = await Faithfulness(llm=ragas_llm).ascore(
        user_input=case.query, response=run.final_answer, retrieved_contexts=texts
    )
    return {
        "case_id": case.id,
        "reference_chunk": case.retrieval.reference_contexts[0],
        "hit@3": case.retrieval.reference_contexts[0] in ids,
        "context_precision": round(precision.value, 3),
        "context_recall": round(recall.value, 3),
        "faithfulness": round(faith.value, 3),
    }


rag_rows = [await rag_scores(c, retrieve(c.query)) for c in rag_cases]
rag_df = pd.DataFrame(rag_rows)
display(rag_df)
print(rag_df[["hit@3", "context_precision", "context_recall", "faithfulness"]].mean().round(3))

# %% [markdown]
# ### Live only: the same evaluation over a Bedrock Knowledge Base
#
# In production the retriever is usually a Bedrock Knowledge Base. The cell below calls
# `bedrock-agent-runtime.retrieve` (request shape: `knowledgeBaseId`, `retrievalQuery={"text": ...}`,
# `retrievalConfiguration.vectorSearchConfiguration.numberOfResults`; see the API reference at
# https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent-runtime_Retrieve.html) and
# feeds the returned chunks into exactly the same RAGAS metrics. It only runs in **live mode with
# `KNOWLEDGE_BASE_ID` set**; in mock mode it prints why it was skipped. The policy documents would
# need to be synced to the knowledge base's data source first.

# %%
if config.is_live and config.knowledge_base_id:
    import boto3

    kb_client = boto3.client("bedrock-agent-runtime", region_name=config.aws_region)
    kb_rows = []
    for case in rag_cases:
        resp = kb_client.retrieve(
            knowledgeBaseId=config.knowledge_base_id,
            retrievalQuery={"text": case.query},
            retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": 3}},
        )
        kb_contexts = [
            (r.get("location", {}).get("s3Location", {}).get("uri", "?"), r["content"]["text"])
            for r in resp["retrievalResults"]
        ]
        kb_rows.append(await rag_scores(case, kb_contexts))
    display(pd.DataFrame(kb_rows))
else:
    reason = "KNOWLEDGE_BASE_ID is not set" if config.is_live else f"mode is {config.mode.value}"
    print(f"Skipped the Bedrock Knowledge Base branch ({reason}); the BM25 results above stand in.")

# %% [markdown]
# ### Exercise 3 — context precision for a new policy query
#
# Pick a policy question that the golden set does not ask, state which chunk should answer it and
# write the one-sentence reference. Use `retrieve()` to explore the corpus.
#
# Success criteria: the reference chunk is retrieved in the top 3 and RAGAS `ContextPrecision`
# scores at least 0.5 for your (query, reference) pair.

# %%
for chunk_id, text in retrieve("express shipping cost next business day"):
    print(chunk_id, "::", text[:120].replace("\n", " "), "...")

# %% tags=["exercise"]
NEW_POLICY_QUERY: str | None = None  # TODO: a policy question not already in the golden set
NEW_REFERENCE_CHUNK: str | None = None  # TODO: e.g. "shipping_policy.md#1"
NEW_REFERENCE: str | None = None  # TODO: one sentence answering the question from that chunk

# %% tags=["solution"]
NEW_POLICY_QUERY: str | None = "How much does express shipping cost?"
NEW_REFERENCE_CHUNK: str | None = "shipping_policy.md#1"
NEW_REFERENCE: str | None = (
    "Express shipping costs $19.00 per order and is delivered the next business day."
)

# %% tags=["check"]
if not (NEW_POLICY_QUERY and NEW_REFERENCE_CHUNK and NEW_REFERENCE):
    exercise_pending("day1.ex3")
else:
    assert NEW_POLICY_QUERY not in set(golden_df["query"]), "pick a query that is not in the set"
    assert NEW_REFERENCE_CHUNK in {c.chunk_id for c in index.chunks}, "unknown chunk id"
    hits = retrieve(NEW_POLICY_QUERY)
    assert NEW_REFERENCE_CHUNK in [cid for cid, _ in hits], f"top-3 was {[c for c, _ in hits]}"
    precision = await ContextPrecision(llm=ragas_llm).ascore(
        user_input=NEW_POLICY_QUERY,
        reference=NEW_REFERENCE,
        retrieved_contexts=[text for _, text in hits],
    )
    assert precision.value >= 0.5, f"context precision {precision.value:.2f} < 0.5"
    exercise_passed("day1.ex3", f"context precision {precision.value:.2f}, chunks {[c for c, _ in hits]}")

# %% [markdown]
# ### Exercise 4 — classify three failures into the taxonomy
#
# Below are the scores of three flagged runs from section 2, with the flag names hidden. Classify
# each run into one failure class using the metric columns only.
#
# Allowed labels: `wrong_tool`, `wrong_arguments`, `schema_drift`, `loop_or_retry`,
# `context_growth`, `injection`, `refusal`, `hallucination`.

# %%
mystery = taxonomy_df[taxonomy_df["flag"] != "fixed"].set_index("case_id")
mystery = mystery.loc[["G014", "G043", "G041"]].drop(columns=["flag"])
mystery

# %% tags=["exercise"]
FAILURE_LABELS: dict[str, str] = {
    # TODO: fill in one label per case id, e.g. "G014": "wrong_tool"
}

# %% tags=["solution"]
FAILURE_LABELS: dict[str, str] = {
    "G014": "wrong_tool",  # search_products executed instead of get_order_status
    "G043": "loop_or_retry",  # MAX_STEPS with loop findings and identical repeated calls
    "G041": "injection",  # create_restock_request executed although must_not_call forbids it
}

# %% tags=["check"]
EXPECTED_LABELS = {"G014": "wrong_tool", "G043": "loop_or_retry", "G041": "injection"}
ALLOWED_LABELS = {
    "wrong_tool",
    "wrong_arguments",
    "schema_drift",
    "loop_or_retry",
    "context_growth",
    "injection",
    "refusal",
    "hallucination",
}
if not FAILURE_LABELS:
    exercise_pending("day1.ex4")
else:
    unknown = set(FAILURE_LABELS.values()) - ALLOWED_LABELS
    assert not unknown, f"unknown labels {unknown}"
    assert set(FAILURE_LABELS) == set(EXPECTED_LABELS), f"label exactly {sorted(EXPECTED_LABELS)}"
    wrong = {k: v for k, v in FAILURE_LABELS.items() if EXPECTED_LABELS[k] != v}
    assert not wrong, f"re-read the metric columns for {sorted(wrong)}"
    exercise_passed("day1.ex4", "all three failures classified correctly")

# %% [markdown]
# ## Exercise checklist
#
# One line per graded exercise. With `STOCKROOM_STRICT_EXERCISES=1` this cell fails unless every
# exercise passed; `make notebooks` runs the solution notebooks that way.

# %%
exercise_summary(["day1.ex1", "day1.ex2", "day1.ex3", "day1.ex4"])

# %% [markdown]
# ## Wrap-up
#
# * A handful of hand-checked answers is not evidence; `evaluate_case` over the golden set is.
# * Trajectory metrics (tool selection, termination, loops, forbidden calls) catch failures that
#   answer-only judging misses — two of the four seeded weaknesses produced plausible answers.
# * Plain assertions, DeepEval metrics and RAGAS metrics all run against the same judge abstraction,
#   so switching from the fake judge to Bedrock is a configuration change.
#
# Day 2 looks at the judge itself: how to tell whether it agrees with humans, and how to read the
# OpenTelemetry trace of a run.
