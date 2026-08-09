"""
src/rag_chain.py
=================
Defines the RAG pipeline as a single composable LangChain Expression
Language (LCEL) `Runnable`:

    retriever  ->  prompt assembly  ->  LLM  ->  string output parser

This file is dual-purpose, which is the standard Databricks "Models from
Code" pattern:

  1. Importable: `from src.rag_chain import build_chain` lets notebooks and
     tests build the chain in-process for experimentation.

  2. Directly loggable: when MLflow logs THIS FILE ITSELF as the model
     (`mlflow.langchain.log_model(lc_model="src/rag_chain.py", ...)`), the
     module-level code below runs, builds `chain`, and registers it via
     `mlflow.models.set_model(chain)`. This is strictly better than
     pickling a LangChain object because:
        - no pickle version-skew between logging and serving environments
        - the exact source code is the artifact -> fully auditable diffs
        - works with objects (like live endpoint clients) that don't pickle

Why LCEL instead of a hand-rolled Python function?
LCEL Runnables get streaming, batching, async, and automatic MLflow
tracing (`mlflow.langchain.autolog()`) for free, and compose with `|` the
same way Unix pipes compose commands -- each stage stays independently
testable (see tests/test_rag_chain.py).
"""

from __future__ import annotations

import mlflow
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda, RunnablePassthrough

from databricks_langchain import ChatDatabricks
from src.retriever import format_retrieved_context, get_retriever
from src.utils import get_logger, load_config

log = get_logger(__name__)


def build_prompt(system_prompt: str) -> ChatPromptTemplate:
    """Assemble the prompt template.

    Structure matters:
      - System message carries the grounding rule ("answer ONLY from
        context") -- this is the single biggest lever against hallucination.
      - The context block is clearly delimited and numbered so the model
        can point back to "[2]" when citing.
      - The question is placed LAST. LLMs attend most reliably to
        instructions closest to the generation point; putting the question
        right before generation reduces "lost in the middle" drift when
        context is long.
    """
    return ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            (
                "human",
                "Context from Databricks documentation:\n{context}\n\n"
                "Question: {question}\n\n"
                "Answer clearly and concisely. Cite sources like [1], [2] "
                "matching the numbered context blocks above.",
            ),
        ]
    )


def build_chain(cfg=None):
    """Construct the full retriever -> prompt -> LLM -> parser chain.

    Input shape:  {"question": "<user question>"}
    Output shape: "<answer string>"

    Using a dict input (rather than a bare string) keeps the chain
    extensible -- e.g. adding conversation history or a `filters` field
    later doesn't break the calling convention.
    """
    cfg = cfg or load_config()

    retriever = get_retriever(cfg)
    prompt = build_prompt(cfg.system_prompt)
    llm = ChatDatabricks(
        endpoint=cfg.llm_endpoint,
        temperature=cfg.raw["llm"]["temperature"],
        max_tokens=cfg.raw["llm"]["max_tokens"],
    )

    def _retrieve_and_format(inputs: dict) -> str:
        docs = retriever.invoke(inputs["question"])
        return format_retrieved_context(docs)

    rag_chain = (
        {
            "context": RunnableLambda(_retrieve_and_format),
            "question": RunnablePassthrough() | RunnableLambda(lambda x: x["question"]),
        }
        | prompt
        | llm
        | StrOutputParser()
    )

    log.info(
        f"RAG chain built: retriever(k={cfg.num_results}) -> prompt -> "
        f"llm({cfg.llm_endpoint}) -> StrOutputParser"
    )
    return rag_chain


# ---------------------------------------------------------------------------
# "Models from Code" entrypoint.
#
# MLflow's code-based logging (`mlflow.langchain.log_model(lc_model=<path>)`)
# loads this file with `runpy`, executing it as `__main__`. Guarding on that
# means:
#   - `import src.rag_chain` from a notebook or test does NOT execute this
#     block (safe: no live endpoint calls just from importing the module).
#   - When MLflow logs/serves THIS FILE AS THE MODEL, the block runs,
#     builds the chain, and registers it with `mlflow.models.set_model`.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    chain = build_chain()
    mlflow.models.set_model(chain)
