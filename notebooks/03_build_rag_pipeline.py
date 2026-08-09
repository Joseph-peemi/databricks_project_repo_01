# Databricks notebook source
# MAGIC %md
# MAGIC # 03 — Build the RAG Pipeline
# MAGIC
# MAGIC **Lab task 2: "Build the RAG model."**
# MAGIC
# MAGIC This notebook builds and interactively tests the chain defined in
# MAGIC `src/rag_chain.py`:
# MAGIC
# MAGIC ```
# MAGIC question -> retriever -> prompt assembly -> LLM -> answer
# MAGIC ```
# MAGIC
# MAGIC We build it here (outside of MLflow logging) first so you can iterate
# MAGIC quickly — tweak the prompt, try a different `k`, and immediately see the
# MAGIC output — before committing to a logged/registered version in notebook 04.

# COMMAND ----------

import sys
from pathlib import Path

project_root = Path.cwd().parent
if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

import mlflow

from src.utils import load_config, get_logger  # noqa: E402
from src.rag_chain import build_chain, build_prompt  # noqa: E402
from src.retriever import get_retriever, format_retrieved_context  # noqa: E402

log = get_logger("03_rag_pipeline")
cfg = load_config()

mlflow.set_experiment(cfg.experiment_path)
mlflow.langchain.autolog()  # traces every retriever/prompt/LLM call in the MLflow UI

# COMMAND ----------

# MAGIC %md
# MAGIC ## Component 1 — Retriever
# MAGIC `src/retriever.py::get_retriever` wraps the Vector Search index from
# MAGIC notebook 02 as a LangChain `BaseRetriever`. Test it in isolation first —
# MAGIC if retrieval is bad, no amount of prompt engineering will fix the final
# MAGIC answer.

# COMMAND ----------

retriever = get_retriever(cfg)
docs = retriever.invoke("How do I enable Change Data Feed?")
for d in docs:
    print(d.metadata["url"], "-", d.page_content[:100].replace("\n", " "))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Component 2 — Prompt template
# MAGIC Explained line by line in `src/rag_chain.py::build_prompt`:
# MAGIC   - system message = grounding rule (answer only from context, else refuse)
# MAGIC   - human message = numbered context blocks + question, with an explicit
# MAGIC     citation instruction
# MAGIC
# MAGIC Print the compiled prompt for a real question so you can SEE exactly
# MAGIC what the LLM receives — this is the single most useful debugging step
# MAGIC for both hallucination and "why didn't it cite sources" problems.

# COMMAND ----------

prompt = build_prompt(cfg.system_prompt)
context = format_retrieved_context(docs)
compiled = prompt.invoke({"context": context, "question": "How do I enable Change Data Feed?"})
print(compiled.to_string())

# COMMAND ----------

# MAGIC %md
# MAGIC ## Component 3 — LLM
# MAGIC `ChatDatabricks` (from `databricks-langchain`) calls a Foundation Model
# MAGIC API serving endpoint the same way `ChatOpenAI` calls OpenAI — same
# MAGIC LangChain interface, but requests never leave the Databricks control
# MAGIC plane, and billing/governance flow through your Databricks account.
# MAGIC
# MAGIC `temperature=0.1` in `config.yaml`: RAG answers should be grounded and
# MAGIC repeatable, not creative — keep temperature low. Reserve higher
# MAGIC temperatures for brainstorming/creative-writing use cases, not
# MAGIC documentation Q&A.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Component 4 — Full chain (retriever | prompt | LLM | parser)

# COMMAND ----------

chain = build_chain(cfg)

test_questions = [
    "How do I enable Change Data Feed on a Delta table?",
    "What is the difference between a Job and a Delta Live Tables pipeline?",
    "What is the capital of France?",  # out-of-scope: should refuse, not hallucinate
]

for q in test_questions:
    answer = chain.invoke({"question": q})
    print(f"Q: {q}\nA: {answer}\n{'-' * 80}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Inspect traces in the MLflow UI
# MAGIC `mlflow.langchain.autolog()` recorded every retriever call, the exact
# MAGIC prompt sent, and the raw LLM response for each `chain.invoke()` above.
# MAGIC Open **Experiments -> (this run) -> Traces** to step through the chain
# MAGIC span-by-span. This is invaluable once you get to debugging bad answers
# MAGIC in notebook 05/08 — you can see EXACTLY which chunks were retrieved
# MAGIC without re-running anything.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Checklist before moving to `04_register_model`
# MAGIC - [ ] Retriever returns on-topic chunks for several test questions
# MAGIC - [ ] Compiled prompt looks correct (context numbered, question last)
# MAGIC - [ ] Chain answers grounded questions correctly and CITES sources
# MAGIC - [ ] Chain REFUSES the out-of-scope question instead of hallucinating
# MAGIC       (if it hallucinates here, tighten the system prompt before
# MAGIC       registering — don't rely on evaluation to catch it later)
