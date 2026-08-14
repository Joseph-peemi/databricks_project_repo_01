# Databricks notebook source
# MAGIC %md
# MAGIC # 05 — Evaluate the Model, and Interpret the Results
# MAGIC
# MAGIC **Lab task 4: "Evaluate the RAG model."**
# MAGIC **Lab task 5: "Analyze and interpret the evaluation results."**
# MAGIC
# MAGIC We measure two independent things:
# MAGIC   1. **Retrieval quality** (precision@k, recall@k) — is the retriever
# MAGIC      finding the right chunks?
# MAGIC   2. **Generation quality** (faithfulness/groundedness, context
# MAGIC      relevance, answer correctness, safety) via Databricks Mosaic AI
# MAGIC      Agent Evaluation's built-in LLM judges — given good/bad chunks,
# MAGIC      is the LLM producing a good/bad answer?
# MAGIC
# MAGIC Plus **latency** (p50/p90), because a technically-correct answer that
# MAGIC takes 12 seconds fails the product requirement just as surely as a wrong one.

# COMMAND ----------

# MAGIC %pip install -r ../requirements.txt
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import sys
from pathlib import Path

project_root = Path.cwd().parent
if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

import mlflow

from src.utils import load_config, get_logger, ensure_mlflow_experiment  # noqa: E402
from src.retriever import get_retriever  # noqa: E402
from src import evaluation  # noqa: E402

log = get_logger("05_evaluate")
cfg = load_config()
mlflow.set_registry_uri("databricks-uc")
ensure_mlflow_experiment(cfg)

# Pull the version notebook 04 just registered in this same job run (falls
# back to "1" when run standalone/interactively, outside the job's task DAG).
MODEL_VERSION = dbutils.jobs.taskValues.get(  # noqa: F821
    taskKey="04_register_model",
    key="registered_model_version",
    default="1",
    debugValue="1",
)
model_uri = f"models:/{cfg.registered_model_name}/{MODEL_VERSION}"

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 1 — Build the golden evaluation dataset
# MAGIC
# MAGIC `src/evaluation.py::SEED_EVAL_EXAMPLES` is a small hand-curated set
# MAGIC covering: a factual lookup, a comparison question, a "how do I" task,
# MAGIC and — importantly — one deliberately **out-of-scope** question that
# MAGIC should be REFUSED, not hallucinated. Testing refusal behavior is easy to
# MAGIC forget and is exactly the kind of gap that embarrasses a team in
# MAGIC production.
# MAGIC
# MAGIC **Best practice:** grow this to 50-200 examples before treating results
# MAGIC as statistically meaningful; 5 examples is enough to smoke-test the
# MAGIC pipeline wiring, not to sign off on quality.

# COMMAND ----------

eval_pdf = evaluation.build_eval_dataset(spark, cfg)  # noqa: F821
display(eval_pdf)  # noqa: F821

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2 — Retrieval metrics (precision@k / recall@k)
# MAGIC
# MAGIC Run ONLY the retriever (bypassing the LLM) against every eval question,
# MAGIC and compare retrieved URLs against each example's `expected_retrieved_context`.
# MAGIC This isolates retrieval quality from generation quality — critical for
# MAGIC diagnosing WHERE a bad answer came from (see Step 5 below).

# COMMAND ----------

retriever = get_retriever(cfg)
retrieval_results = evaluation.evaluate_retrieval(retriever, eval_pdf)
display(retrieval_results)  # noqa: F821

mean_precision = retrieval_results["precision_at_k"].mean()
mean_recall = retrieval_results["recall_at_k"].mean()
log.info(f"Mean Precision@k = {mean_precision:.3f} | Mean Recall@k = {mean_recall:.3f}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 3 — Generation-quality metrics via Mosaic AI Agent Evaluation
# MAGIC
# MAGIC `mlflow.evaluate(model_type="databricks-agent")` runs the REGISTERED
# MAGIC model (not the in-memory object) through built-in LLM judges:
# MAGIC
# MAGIC | Metric | What it measures | Why it matters |
# MAGIC |---|---|---|
# MAGIC | **chunk_relevance** (context relevance) | Are the retrieved chunks actually relevant to the question? | Low score -> retrieval problem (bad chunking, wrong embedding model, k too small) |
# MAGIC | **groundedness** (faithfulness) | Is every claim in the answer supported by the retrieved context? | Low score -> hallucination — the LLM is adding facts not present in context |
# MAGIC | **correctness** (answer correctness) | Does the answer match the expected/reference answer? | Low score -> either generation OR retrieval failure — cross-reference with chunk_relevance to tell which |
# MAGIC | **safety** | Does the answer avoid harmful/inappropriate content? | Guards against toxic or unsafe completions leaking through |
# MAGIC
# MAGIC These are **LLM-as-judge** metrics — a separate strong LLM scores each
# MAGIC answer against a rubric. This scales far better than manual grading, but
# MAGIC is not infallible: spot-check a sample of judge scores against your own
# MAGIC reading before fully trusting the aggregate (see Step 5).

# COMMAND ----------

eval_results = evaluation.run_mlflow_evaluation(model_uri, eval_pdf, cfg)
display(eval_results.tables["eval_results"])  # noqa: F821

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 4 — Latency
# MAGIC
# MAGIC Measured end-to-end (retrieval + generation) since that's what the user
# MAGIC actually experiences. We report p50 AND p90 — p90 catches tail-latency
# MAGIC problems (e.g. cold-start on a scale-to-zero endpoint) that a mean alone
# MAGIC would hide.

# COMMAND ----------

from src.rag_chain import build_chain

chain = build_chain(cfg)
latency_stats = evaluation.measure_latency(chain, eval_pdf["request"].tolist())
log.info(latency_stats)

with mlflow.start_run(run_name="latency_benchmark"):
    mlflow.log_metrics(latency_stats)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 5 — Interpret the results (lab task 5)
# MAGIC
# MAGIC Use this decision table to turn metric numbers into concrete next steps.
# MAGIC Cross-referencing retrieval metrics against generation metrics is what
# MAGIC lets you tell "bad retrieval" apart from "bad generation" for the SAME
# MAGIC low correctness score:
# MAGIC
# MAGIC | Symptom | Likely root cause | Fix |
# MAGIC |---|---|---|
# MAGIC | High recall, high precision, low groundedness | LLM is hallucinating despite good context | Tighten system prompt grounding rule; lower temperature; add explicit "answer only from context" reinforcement in the human message |
# MAGIC | Low recall (right doc never retrieved) | Corpus gap, embedding/query mismatch, or chunk too small/large to represent the concept | Verify the source page was ingested at all; try a larger `k`; reconsider chunk_size; consider hybrid (keyword+vector) search for exact-term queries |
# MAGIC | Low precision, high recall | Right doc retrieved, but buried among noisy irrelevant chunks | Lower `k` won't help if it's ranked low — check embedding model fit; add metadata filtering (e.g. product area) |
# MAGIC | Low chunk_relevance AND low correctness together | Retrieval-stage failure — generation never had a chance | Fix retrieval FIRST; don't tune prompts to compensate for missing context |
# MAGIC | High chunk_relevance, low correctness | Generation-stage failure despite good context | Prompt engineering issue, or question requires reasoning across multiple chunks the LLM isn't combining well |
# MAGIC | Correct on grounded questions, but hallucinates instead of refusing on the out-of-scope question | Grounding instruction is too weak | Make the refusal instruction more explicit and add a matching few-shot example in the prompt |
# MAGIC | p90 latency far above p50 | Cold starts (scale-to-zero) or occasional slow retrieval | Consider disabling scale-to-zero for latency-sensitive prod traffic, or pre-warm the endpoint |
# MAGIC
# MAGIC **Poor chunking symptoms specifically:** answers that are technically
# MAGIC grounded but incomplete (cut off mid-explanation), or that cite a chunk
# MAGIC whose text ends mid-sentence — both point at `chunk_size`/`chunk_overlap`
# MAGIC tuning, or switching to a markdown-header-aware splitter for pages with
# MAGIC deep section structure.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 6 — Gate promotion on thresholds
# MAGIC
# MAGIC Don't eyeball metrics and promote by feel — encode the bar in
# MAGIC `config.yaml` (`evaluation.min_faithfulness`, etc.) and check it
# MAGIC programmatically. This is what makes evaluation a real CI gate instead
# MAGIC of a report nobody acts on.

# COMMAND ----------

eval_cfg = cfg.raw["evaluation"]
metrics = eval_results.metrics

passed = (
    metrics.get("groundedness/v1/mean", 0) >= eval_cfg["min_faithfulness"]
    and metrics.get("correctness/v1/mean", 0) >= eval_cfg["min_answer_correctness"]
    and latency_stats["p90_seconds"] <= eval_cfg["max_p90_latency_seconds"]
)

if passed:
    log.info("✅ All quality gates PASSED — safe to promote to 'champion' alias in notebook 06.")
else:
    log.warning("❌ Quality gates FAILED — do NOT promote. Review the interpretation table above.")
