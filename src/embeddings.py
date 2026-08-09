"""
src/embeddings.py
==================
Helpers for talking to the Databricks Foundation Model API embedding
endpoint directly.

Two ways to get embeddings into the Vector Search index:

  A) DATABRICKS-MANAGED EMBEDDINGS (used by this project, see
     notebooks/02_create_vector_search_index.py): you give the Delta-Sync
     index an `embedding_source_column` and an `embedding_model_endpoint_name`,
     and Databricks computes + refreshes embeddings for you automatically
     whenever the Silver table changes. Zero embedding code required.

  B) SELF-MANAGED EMBEDDINGS: you compute the vectors yourself (e.g. because
     you need a custom/fine-tuned embedding model) and write them into a
     `embedding` column, then create the index in `embedding_vector_column`
     mode instead.

This module exists for (B), and more importantly for:
  - ad-hoc debugging ("what does the model actually embed my query as?")
  - the retrieval-precision/recall evaluation in src/evaluation.py, where we
    need raw query vectors to reason about nearest neighbors outside of the
    LangChain retriever abstraction.

Best practice: ALWAYS use the exact same embedding endpoint for corpus and
query embedding. Mixing embedding models (or even different versions of the
"same" model) silently degrades retrieval because the two vector spaces are
no longer comparable by cosine similarity.
"""

from __future__ import annotations

from mlflow.deployments import get_deploy_client

from src.utils import Config, call_with_retry, get_logger

log = get_logger(__name__)


def get_embedding_client():
    """mlflow.deployments client scoped to the Databricks Model Serving
    control plane -- this is the supported way to call Foundation Model API
    endpoints from a notebook without hand-rolling REST auth."""
    return get_deploy_client("databricks")


def embed_texts(texts: list[str], cfg: Config) -> list[list[float]]:
    """Embed a batch of strings using the configured embedding endpoint.

    Batches are capped conservatively because Foundation Model API endpoints
    enforce per-request payload limits; batching also keeps a single slow
    request from blocking the whole pipeline (fail fast, retry small).
    """
    client = get_embedding_client()
    batch_size = 150
    vectors: list[list[float]] = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        response = call_with_retry(
            client.predict,
            endpoint=cfg.embedding_model_endpoint,
            inputs={"input": batch},
        )
        vectors.extend([row["embedding"] for row in response["data"]])
        log.info(f"Embedded {i + len(batch)}/{len(texts)} texts")

    return vectors


def embed_query(query: str, cfg: Config) -> list[float]:
    """Convenience wrapper for a single query string (used in retrieval
    debugging / manual recall@k checks in evaluation.py)."""
    return embed_texts([query], cfg)[0]
