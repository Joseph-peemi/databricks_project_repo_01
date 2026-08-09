"""
src/retriever.py
=================
Wraps the Databricks Vector Search index behind LangChain's standard
Retriever interface so it can be composed into an LCEL chain with `|`.

Why wrap instead of calling the Vector Search SDK directly inside the chain?
LangChain's `Runnable` interface gives us, for free:
  - `.invoke()` / `.batch()` / `.stream()` symmetry with the rest of the chain
  - automatic tracing when wrapped in `mlflow.langchain.log_model`
  - the ability to swap retrievers (e.g. add a re-ranker) without touching
    rag_chain.py's composition logic
"""

from __future__ import annotations

from databricks_langchain import DatabricksVectorSearch
from langchain_core.retrievers import BaseRetriever

from src.utils import Config, get_logger

log = get_logger(__name__)


def get_retriever(cfg: Config) -> BaseRetriever:
    """Build a retriever bound to the Delta-Sync vector index.

    columns=["url", "title", "chunk_text"] controls which metadata fields
    come back with each hit -- we need `url`/`title` for citations and
    `chunk_text` as the actual retrieved content. Requesting only what you
    need keeps payloads small and query latency low.

    search_type="similarity" does pure dense ANN search. See
    docs/architecture.md / README "Hybrid Search" section for how to switch
    to hybrid (keyword + vector) search when queries contain exact
    identifiers (e.g. API/config names) that embeddings alone under-match.
    """
    vector_store = DatabricksVectorSearch(
        endpoint=cfg.vs_endpoint_name,
        index_name=cfg.vs_index_name,
        columns=["url", "title", "chunk_text"],
        text_column="chunk_text",
    )

    retriever = vector_store.as_retriever(
        search_kwargs={"k": cfg.num_results}
    )
    log.info(
        f"Retriever ready -> index={cfg.vs_index_name}, "
        f"endpoint={cfg.vs_endpoint_name}, k={cfg.num_results}"
    )
    return retriever


def format_retrieved_context(documents) -> str:
    """Turn a list of LangChain Documents into the numbered, citable context
    block the prompt template expects.

    Numbering + explicit source URLs is what lets the LLM (and a human
    reviewer) trace an answer back to a specific chunk -- essential for both
    the "cite your sources" instruction in the system prompt and for
    debugging faithfulness failures during evaluation (see
    src/evaluation.py and README section 8 "Interpret Results").
    """
    blocks = []
    for i, doc in enumerate(documents, start=1):
        source = doc.metadata.get("url", "unknown source")
        blocks.append(f"[{i}] Source: {source}\n{doc.page_content}")
    return "\n\n".join(blocks)
