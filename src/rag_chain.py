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

    Input shape:  {"query": "<user question>", "history": [...]}
    Output shape: {"content": "<answer string>"}

    This matches MLflow's `SplitChatMessagesRequest` / `StringResponse`
    signatures (see notebook 04) instead of a bespoke {"question": ...} ->
    str shape, because notebook 06 deploys via the Agent Framework
    (`databricks.agents.deploy`), which validates the registered model's
    schema against exactly those two shapes and refuses to deploy anything
    else ("The model's schema is not compatible with Agent Framework").
    `history` is accepted for schema compatibility but not yet used to
    condition generation -- see README for multi-turn follow-up work.
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
        docs = retriever.invoke(inputs["query"])
        return format_retrieved_context(docs)

    rag_chain = (
        {
            "context": RunnableLambda(_retrieve_and_format),
            "question": RunnablePassthrough() | RunnableLambda(lambda x: x["query"]),
        }
        | prompt
        | llm
        | StrOutputParser()
        | RunnableLambda(lambda answer: {"content": answer})
    )

    log.info(
        f"RAG chain built: retriever(k={cfg.num_results}) -> prompt -> "
        f"llm({cfg.llm_endpoint}) -> StrOutputParser"
    )
    return rag_chain


# ---------------------------------------------------------------------------
# "Models from Code" entrypoint.
#
# mlflow.langchain.log_model(lc_model=<path>) does NOT use runpy / __main__
# (despite what older MLflow docs/examples suggest) -- as of mlflow==2.20.1
# it loads this file via importlib.util.spec_from_file_location with a
# random module name ("code_model_<uuid>"), so `__name__ == "__main__"`
# never matches and mlflow.models.set_model() silently never runs, which
# surfaces as "ensure the model is set using mlflow.models.set_model()"
# several frames away from this file. Guard on the "code_model_" prefix
# MLflow actually uses instead, so this still means:
#   - `import src.rag_chain` from a notebook or test does NOT execute this
#     block (__name__ == "src.rag_chain": safe, no live endpoint calls just
#     from importing the module).
#   - When MLflow logs/serves THIS FILE AS THE MODEL, the block runs,
#     builds the chain, and registers it with `mlflow.models.set_model`.
# ---------------------------------------------------------------------------
if __name__ == "__main__" or __name__.startswith("code_model_"):
    chain = build_chain()
    mlflow.models.set_model(chain)
