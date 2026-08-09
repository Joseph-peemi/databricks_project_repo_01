"""
src/evaluation.py
==================
Everything needed to answer "is this RAG pipeline actually good?":

  1. build_eval_dataset()   -> a golden Q/A/expected-context set
  2. retrieval_precision_at_k / retrieval_recall_at_k -> classic IR metrics
  3. run_mlflow_evaluation() -> LLM-judged generation-quality metrics via
     mlflow.evaluate(..., model_type="databricks-agent")
  4. measure_latency()      -> p50/p90 end-to-end latency

Why we measure BOTH retrieval metrics and generation metrics:
A RAG system can fail in two independent places. If you only measure
"is the final answer correct", you cannot tell whether a wrong answer was
caused by (a) the retriever fetching the wrong chunks, or (b) the LLM
hallucinating despite good chunks. Splitting the metrics into
retrieval-stage vs. generation-stage is what makes the results actionable
(see README section 8 "Interpret Results" for the decision tree).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import mlflow
import pandas as pd

from src.utils import Config, get_logger

log = get_logger(__name__)


@dataclass
class EvalExample:
    """One row of the golden evaluation set.

    expected_source_urls: the set of doc URLs that SHOULD be retrieved for
    this question. This is what makes retrieval precision/recall computable
    without a human re-grading every run.
    """

    question: str
    expected_answer: str
    expected_source_urls: list[str] = field(default_factory=list)


# A small hand-curated seed set covering common Databricks documentation
# topics. In production, grow this to 50-200 examples (see README best
# practices: "representative, not exhaustive" and include adversarial /
# out-of-scope questions to test refusal behavior).
SEED_EVAL_EXAMPLES: list[EvalExample] = [
    EvalExample(
        question="How do I enable Change Data Feed on a Delta table?",
        expected_answer=(
            "Set the table property delta.enableChangeDataFeed = true, either "
            "at creation time with TBLPROPERTIES or afterward with ALTER TABLE "
            "... SET TBLPROPERTIES (delta.enableChangeDataFeed = true)."
        ),
        expected_source_urls=["https://docs.databricks.com/delta/delta-change-data-feed"],
    ),
    EvalExample(
        question="What is the difference between a Databricks Job and a Delta Live Table pipeline?",
        expected_answer=(
            "Jobs orchestrate arbitrary tasks (notebooks, JARs, SQL) on a "
            "schedule or trigger, while Delta Live Tables (DLT) is a "
            "declarative framework specifically for building and managing "
            "reliable ETL pipelines, with built-in data quality checks and "
            "automatic dependency management between tables."
        ),
        expected_source_urls=["https://docs.databricks.com/delta-live-tables/index"],
    ),
    EvalExample(
        question="How do I create a Unity Catalog external location?",
        expected_answer=(
            "Use CREATE EXTERNAL LOCATION with a storage credential and the "
            "cloud storage path, e.g. CREATE EXTERNAL LOCATION my_loc URL "
            "'s3://bucket/path' WITH (STORAGE CREDENTIAL my_cred)."
        ),
        expected_source_urls=["https://docs.databricks.com/unity-catalog/external-locations"],
    ),
    EvalExample(
        question="What programming language does Delta Lake support for time travel queries?",
        expected_answer=(
            "Delta Lake supports time travel via SQL (VERSION AS OF / "
            "TIMESTAMP AS OF), and equivalent DataFrameReader options in "
            "Python, Scala, and R."
        ),
        expected_source_urls=["https://docs.databricks.com/delta/history"],
    ),
    EvalExample(
        # Deliberately out-of-scope question: tests that the model refuses
        # rather than hallucinates, per the system prompt's grounding rule.
        question="What is the capital of France?",
        expected_answer=(
            "I don't have enough information in the Databricks documentation "
            "to answer that."
        ),
        expected_source_urls=[],
    ),
]


def build_eval_dataset(spark, cfg: Config, examples: list[EvalExample] | None = None) -> pd.DataFrame:
    """Materialize the golden set as both a UC Delta table (for lineage +
    reuse across runs) and a pandas DataFrame (what mlflow.evaluate expects).
    """
    examples = examples or SEED_EVAL_EXAMPLES
    rows = [
        {
            "request": ex.question,
            "expected_response": ex.expected_answer,
            "expected_retrieved_context": ex.expected_source_urls,
        }
        for ex in examples
    ]
    eval_pdf = pd.DataFrame(rows)

    eval_table = f"{cfg.catalog}.{cfg.schema}.{cfg.raw['evaluation']['eval_table']}"
    spark.createDataFrame(eval_pdf.astype(str)).write.format("delta").mode(
        "overwrite"
    ).option("overwriteSchema", "true").saveAsTable(eval_table)
    log.info(f"Wrote {len(eval_pdf)} eval examples to {eval_table}")
    return eval_pdf


def retrieval_precision_at_k(retrieved_urls: list[str], expected_urls: list[str]) -> float:
    """Fraction of retrieved chunks that are actually relevant.
    Precision@k = |retrieved ∩ expected| / |retrieved|

    Low precision -> the index is returning plausible-looking but wrong
    chunks (see README: "poor chunking" / "missing metadata filters").
    """
    if not retrieved_urls:
        return 0.0
    hits = len(set(retrieved_urls) & set(expected_urls))
    return hits / len(retrieved_urls)


def retrieval_recall_at_k(retrieved_urls: list[str], expected_urls: list[str]) -> float:
    """Fraction of the truly relevant documents that were found.
    Recall@k = |retrieved ∩ expected| / |expected|

    Low recall -> the right document exists in the corpus but the retriever
    isn't surfacing it (see README: "bad retrieval" -> check embedding
    model fit, chunk size, or k too small).
    """
    if not expected_urls:
        return 1.0  # nothing was expected to be found (e.g. refusal case)
    hits = len(set(retrieved_urls) & set(expected_urls))
    return hits / len(expected_urls)


def evaluate_retrieval(retriever, eval_pdf: pd.DataFrame) -> pd.DataFrame:
    """Run every eval question through the retriever ALONE (bypassing the
    LLM) and score precision/recall. Isolating the retriever is what lets us
    blame "retrieval" vs. "generation" independently."""
    records = []
    for _, row in eval_pdf.iterrows():
        docs = retriever.invoke(row["request"])
        retrieved_urls = [d.metadata.get("url") for d in docs]
        expected = row["expected_retrieved_context"]
        expected = expected if isinstance(expected, list) else eval(expected)
        records.append(
            {
                "request": row["request"],
                "retrieved_urls": retrieved_urls,
                "precision_at_k": retrieval_precision_at_k(retrieved_urls, expected),
                "recall_at_k": retrieval_recall_at_k(retrieved_urls, expected),
            }
        )
    result_df = pd.DataFrame(records)
    log.info(
        f"Retrieval eval: mean precision@k={result_df['precision_at_k'].mean():.3f}, "
        f"mean recall@k={result_df['recall_at_k'].mean():.3f}"
    )
    return result_df


def measure_latency(chain, questions: list[str]) -> dict[str, float]:
    """End-to-end wall-clock latency per question, reported as p50/p90/mean.

    Measured against the FULL chain (retrieval + generation) because that is
    what the user actually waits on. p90 (not just mean) matters because
    tail latency is what drives complaints and timeouts under real traffic.
    """
    latencies = []
    for q in questions:
        start = time.perf_counter()
        chain.invoke({"question": q})
        latencies.append(time.perf_counter() - start)

    series = pd.Series(latencies)
    stats = {
        "p50_seconds": float(series.quantile(0.50)),
        "p90_seconds": float(series.quantile(0.90)),
        "mean_seconds": float(series.mean()),
    }
    log.info(f"Latency: {stats}")
    return stats


def run_mlflow_evaluation(model_uri: str, eval_pdf: pd.DataFrame, cfg: Config):
    """Run Databricks Mosaic AI Agent Evaluation against the REGISTERED
    model (not the in-memory chain), scoring generation quality with
    LLM-judge metrics:

        - faithfulness / groundedness: is the answer supported by the
          retrieved context (i.e. not hallucinated)?
        - context relevance: are the retrieved chunks actually relevant to
          the question?
        - answer correctness: does the answer match the expected_response?

    `model_type="databricks-agent"` is what activates the built-in RAG
    judges (this requires the `databricks-agents` package and a Unity
    Catalog-registered / mlflow-tracked model). We evaluate the REGISTERED
    URI (not the Python object) so the eval exercises the exact artifact
    that will be deployed -- catching serialization or environment bugs
    that an in-memory smoke test would miss.
    """
    with mlflow.start_run(run_name="rag_agent_evaluation"):
        results = mlflow.evaluate(
            model=model_uri,
            data=eval_pdf,
            model_type="databricks-agent",
            evaluator_config={
                "databricks-agent": {
                    "metrics": [
                        "chunk_relevance",
                        "groundedness",
                        "correctness",
                        "safety",
                    ]
                }
            },
        )
    log.info(f"Agent evaluation metrics: {results.metrics}")
    return results
