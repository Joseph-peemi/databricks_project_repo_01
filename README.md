# Databricks RAG Lab — Retrieval-Augmented Generation over Databricks Documentation

A complete, production-quality Retrieval-Augmented Generation (RAG) pipeline built
entirely on Databricks: Mosaic AI Vector Search, Foundation Model APIs, MLflow /
Unity Catalog Model Registry, Model Serving, Agent Evaluation, and the Review App.

This README is the master tutorial. It explains **why** each step exists, **what**
Databricks feature powers it, **best practices**, **common mistakes**, and **how it
fits into the overall architecture**. Full, runnable code lives in `notebooks/` and
`src/` — this document walks you through it rather than duplicating it.

> **No prior Databricks RAG experience required.** Follow the numbered sections in
> order; each one builds on the last, exactly like the notebooks do.

---

## Table of Contents

1. [Architecture](#1-architecture)
2. [Project Structure](#2-project-structure)
3. [Environment Setup](#3-environment-setup)
4. [Task 1 — Create the AI Search Index](#4-task-1--create-the-ai-search-index)
5. [Task 2 — Build the RAG Pipeline](#5-task-2--build-the-rag-pipeline)
6. [Task 3 — Register the Model](#6-task-3--register-the-model)
7. [Task 4 — Evaluate the Model](#7-task-4--evaluate-the-model)
8. [Task 5 — Interpret the Evaluation Results](#8-task-5--interpret-the-evaluation-results)
9. [Task 6 — Deploy the Model](#9-task-6--deploy-the-model)
10. [Task 7 — Test with the Review App](#10-task-7--test-with-the-review-app)
11. [End-to-End Workflow](#11-end-to-end-workflow)
12. [Best Practices](#12-best-practices)
13. [Troubleshooting Reference](#13-troubleshooting-reference)
14. [Infrastructure as Code (Terraform)](#14-infrastructure-as-code-terraform)

---

## 1. Architecture

Full Mermaid + ASCII diagrams and a component-by-component explanation live in
**[`docs/architecture.md`](docs/architecture.md)**. The short version:

```
User Question → Embedding → Vector Search → Top-k Chunks →
Prompt Assembly → LLM → Generated Answer → Evaluation → Deployment → Review App
```

Every component in that chain maps to one Databricks feature:

| Stage | Databricks Feature |
|---|---|
| Knowledge base storage | Unity Catalog Volumes + Delta Lake |
| Embedding | Foundation Model APIs (`databricks-bge-large-en`) |
| Vector index | Mosaic AI Vector Search (Delta-Sync index) |
| LLM | Foundation Model APIs (`databricks-meta-llama-3-3-70b-instruct`) |
| Chain orchestration | LangChain (LCEL) via `databricks-langchain` |
| Experiment tracking + versioning | MLflow + Unity Catalog Model Registry |
| Evaluation | Mosaic AI Agent Evaluation (`mlflow.evaluate`) |
| Serving | Mosaic AI Model Serving |
| Human testing | Agent Framework Review App |

---

## 2. Project Structure

```
rag-databricks-lab/
├── README.md                          # <- you are here: the master tutorial
├── requirements.txt                   # pinned Python dependencies
├── config/
│   └── config.yaml                    # single source of truth for every name/id used across the pipeline
├── data/                              # local scratch space (nothing committed here; docs land in a UC Volume, not this folder)
├── docs/
│   └── architecture.md                # Mermaid + ASCII diagrams, component explanations
├── notebooks/                         # the lab, one notebook per task, meant to be run in order
│   ├── 00_environment_setup.py        # install deps, create UC objects, smoke-test endpoints
│   ├── 01_data_ingestion_and_chunking.py   # scrape docs -> Bronze table -> chunk -> Silver table
│   ├── 02_create_vector_search_index.py    # Task 1: create + populate + verify the AI Search index
│   ├── 03_build_rag_pipeline.py            # Task 2: build + interactively test the RAG chain
│   ├── 04_register_model.py                # Task 3: log + register + version the model in MLflow/UC
│   ├── 05_evaluate_model.py                # Task 4 & 5: run evaluation, interpret results, gate promotion
│   ├── 06_deploy_model.py                  # Task 6: deploy to a Model Serving endpoint
│   └── 07_review_app_testing.py            # Task 7: test via the Review App, troubleshooting
├── src/                                # reusable library code imported by the notebooks (and unit-tested)
│   ├── __init__.py
│   ├── utils.py                       # config loading, UC bootstrap, retry wrapper, logging
│   ├── ingestion.py                   # HTML cleaning, chunking, Bronze/Silver Delta writes
│   ├── embeddings.py                  # direct embedding-endpoint client (debugging / self-managed embeddings)
│   ├── retriever.py                   # DatabricksVectorSearch retriever wrapper + context formatting
│   ├── rag_chain.py                   # the LCEL chain itself; also the MLflow "Models from Code" entrypoint
│   ├── evaluation.py                  # golden dataset, retrieval metrics, mlflow.evaluate wrapper, latency
│   └── deployment.py                  # agents.deploy() / Databricks SDK endpoint creation + invocation
└── tests/                             # fast, dependency-light unit tests (no live Databricks resources needed)
    ├── conftest.py
    ├── test_ingestion.py
    ├── test_retriever.py
    ├── test_rag_chain.py
    └── test_evaluation.py
```

**Design principle:** notebooks are *thin orchestration* — they call into `src/`,
narrate the "why" in markdown cells, and display results. All actual logic lives in
`src/`, which means it's unit-testable (`tests/`) independent of a running cluster,
and reusable if you later wrap this in a Databricks Asset Bundle / CI job instead of
running notebooks by hand.

---

## 3. Environment Setup

**Cluster:** Databricks Runtime **15.4 LTS ML** or newer (16.x ML also fine). The ML
runtime matters — it ships MLflow, numpy, and scikit-learn at versions tested
against each other; the standard runtime does not, and you'll fight dependency
conflicts for no reason.

**Install dependencies** (run in `notebooks/00_environment_setup.py`, cell 1):

```python
%pip install -r ../requirements.txt
dbutils.library.restartPython()
```

`requirements.txt` pins:

```text
mlflow[databricks]==2.19.0
databricks-vectorsearch==0.42
databricks-langchain==0.4.1
langchain==0.3.14
langchain-community==0.3.14
langchain-core==0.3.29
databricks-sdk==0.40.0
databricks-agents==0.16.0
mlflow[genai]==2.19.0
beautifulsoup4==4.12.3
markdownify==0.13.1
lxml==5.3.0
pytest==8.3.4
pyyaml==6.0.2
tenacity==9.0.0
```

**Best practice:** develop on a single-user personal compute cluster, not a shared
one — `%pip install` mutates the Python environment for everyone attached to a
shared cluster, which is a classic way to silently break a colleague's notebook.

**Common mistake:** forgetting `dbutils.library.restartPython()` after `%pip
install`. Without it, the notebook process keeps using the environment snapshot
from before the install, and you get baffling `ImportError`s for packages that
"are definitely installed."

**Unity Catalog objects:** `src/utils.py::ensure_uc_objects` creates the catalog,
schema, and volume declared in `config/config.yaml` (`main.rag_lab`, volume
`raw_docs`), using `CREATE ... IF NOT EXISTS` so the notebook is safely re-runnable.
If your workspace restricts catalog creation, ask an admin to pre-create
`main.rag_lab` and grant you `USE CATALOG`, `USE SCHEMA`, `CREATE TABLE`, and
`CREATE VOLUME`.

**Endpoint verification:** the setup notebook's last cell calls the embedding and
LLM Foundation Model API endpoints with a trivial request and asserts the
embedding dimension matches `config.yaml`. Do this *before* touching Vector Search
— an entitlement or auth problem is a 5-second fix here versus a confusing failure
three notebooks later.

---

## 4. Task 1 — Create the AI Search Index

**Notebook:** [`notebooks/02_create_vector_search_index.py`](notebooks/02_create_vector_search_index.py)
(preceded by ingestion in [`notebooks/01_data_ingestion_and_chunking.py`](notebooks/01_data_ingestion_and_chunking.py))

### Why it's needed
An LLM's parametric knowledge is frozen at training time and doesn't know your
specific/internal documentation, or anything published after its cutoff. RAG
solves this by retrieving relevant text at query time and feeding it into the
prompt — but retrieval requires an index that can find *semantically* similar
text, not just keyword matches. That's what a vector search index provides.

### What Databricks feature is used
**Mosaic AI Vector Search**, specifically a **Delta-Sync index** with
**Databricks-managed embeddings**:

```python
vsc.create_delta_sync_index(
    endpoint_name=cfg.vs_endpoint_name,
    source_table_name=cfg.chunked_docs_table,      # main.rag_lab.databricks_docs_chunked
    index_name=cfg.vs_index_name,                   # main.rag_lab.databricks_docs_index
    pipeline_type="TRIGGERED",
    primary_key="chunk_id",
    embedding_source_column="chunk_text",
    embedding_model_endpoint_name="databricks-bge-large-en",
)
```

Because it's Delta-Sync, the index tracks the source Delta table's **Change Data
Feed** and re-embeds only rows that changed — you never write custom
incremental-embedding code.

### Pipeline, step by step
1. **Prepare documents** (`01_data_ingestion_and_chunking.py`): download Databricks
   docs pages, land them in a UC Volume (`/Volumes/main/rag_lab/raw_docs`).
2. **Clean**: `src/ingestion.py::clean_html_to_text` strips `<nav>`, `<script>`,
   `<header>`, `<footer>` before converting to markdown — unfiltered HTML pollutes
   chunks with repeated boilerplate that shows up as false-positive matches.
3. **Chunk**: `src/ingestion.py::chunk_documents` uses LangChain's
   `RecursiveCharacterTextSplitter` (`chunk_size=800`, `chunk_overlap=150`),
   preferring paragraph/section breaks over hard character cuts.
4. **Write Silver table with Change Data Feed enabled**:
   `option("delta.enableChangeDataFeed", "true")` — required by Vector Search,
   and easy to forget if you don't already know to look for it.
5. **Create the endpoint** (compute layer) and **the index** (the actual ANN
   structure), then **wait for `ONLINE` status** before querying.
6. **Verify**: run a real `similarity_search` call and eyeball the results.

### Best practices
- Use **deterministic chunk IDs** (`src/ingestion.py::make_chunk_id`, a hash of
  `url + chunk_index`) so re-ingesting the same page **upserts** instead of
  duplicating rows in the index.
- Request only the metadata **columns you need** (`url`, `title`, `chunk_text`) to
  keep query payloads small.
- Start with `pipeline_type="TRIGGERED"` (sync on demand, predictable cost); switch
  to `"CONTINUOUS"` only if near-real-time freshness is a real product requirement
  — it keeps a cluster running.

### Common mistakes
- **Querying before the index is `ONLINE`** — always call `wait_until_ready()`.
- **Not enabling Change Data Feed** on the source table — index creation fails with
  a clear but easy-to-miss error.
- **Chunking whole pages with no chunking at all** — a single 5,000-word page
  embeds to a diffuse "average" vector that never scores highest for any specific
  question.
- **Zero-length or duplicate chunks** — filtered via `min_chunk_size` and a
  uniqueness assertion in the ingestion notebook; skipping this check lets noise
  silently degrade retrieval quality.

### How it fits into the architecture
This is the **knowledge base layer** — everything downstream (the retriever, the
chain, evaluation, serving) depends on this index being populated and accurate. Get
this wrong and no amount of prompt engineering downstream will fix bad answers.

---

## 5. Task 2 — Build the RAG Pipeline

**Notebook:** [`notebooks/03_build_rag_pipeline.py`](notebooks/03_build_rag_pipeline.py)
**Library code:** [`src/rag_chain.py`](src/rag_chain.py), [`src/retriever.py`](src/retriever.py)

### Why it's needed
The index alone doesn't answer questions — it returns chunks. "Building the RAG
model" means wiring retrieval, prompt construction, and generation into one
callable pipeline: `question in, grounded answer out`.

### What Databricks feature is used
- **`databricks-langchain`**'s `DatabricksVectorSearch` — a LangChain `VectorStore`
  that wraps the index in a standard `.as_retriever()` interface.
- **`databricks-langchain`**'s `ChatDatabricks` — a LangChain chat model that calls
  a Foundation Model API serving endpoint (`databricks-meta-llama-3-3-70b-instruct`)
  exactly the way `ChatOpenAI` calls OpenAI, but requests never leave the
  Databricks control plane.
- **LangChain Expression Language (LCEL)** to compose the stages with `|`.

### The four components, explained

**1. Embedding model** — not called directly in this file; it's invoked
automatically twice: once by Vector Search to embed the corpus at index time (Task
1), and once per query by `DatabricksVectorSearch` under the hood when the
retriever runs. Using the *same* embedding model for both is non-negotiable —
mixing embedding models breaks cosine-similarity comparability between query and
corpus vectors.

**2. Retriever** (`src/retriever.py::get_retriever`):
```python
vector_store = DatabricksVectorSearch(
    endpoint=cfg.vs_endpoint_name,
    index_name=cfg.vs_index_name,
    columns=["url", "title", "chunk_text"],
    text_column="chunk_text",
)
retriever = vector_store.as_retriever(search_kwargs={"k": cfg.num_results})
```
`k=5` (from `config.yaml`) is the number of chunks retrieved per query — enough
context to usually contain the answer, few enough to stay within the LLM's context
budget and keep the prompt focused (see Best Practices §12 for tuning guidance).

**3. Prompt template** (`src/rag_chain.py::build_prompt`):
```python
ChatPromptTemplate.from_messages([
    ("system", cfg.system_prompt),   # grounding rule: "answer ONLY from context"
    ("human", "Context:\n{context}\n\nQuestion: {question}\n\nCite sources like [1], [2]."),
])
```
Design decisions that matter:
- The **system message carries the grounding rule** — this is the single biggest
  lever against hallucination.
- **Context is numbered** (`[1]`, `[2]`, ...) via
  `src/retriever.py::format_retrieved_context`, which is what lets the model (and a
  human reviewer) cite a specific source.
- **The question comes last**, immediately before generation — LLMs attend most
  reliably to instructions near the generation point, so this reduces "lost in the
  middle" drift when the context block is long.

**4. LLM**: `ChatDatabricks(endpoint="databricks-meta-llama-3-3-70b-instruct",
temperature=0.1, max_tokens=1024)`. Low temperature (`0.1`) because RAG answers
should be grounded and repeatable, not creative.

**5. The chain** (`src/rag_chain.py::build_chain`):
```python
rag_chain = (
    {
        "context": RunnableLambda(_retrieve_and_format),   # question -> retriever -> formatted chunks
        "question": RunnablePassthrough() | RunnableLambda(lambda x: x["question"]),
    }
    | prompt
    | llm
    | StrOutputParser()
)
```
Input: `{"question": "..."}`. Output: a plain answer string. The dict-input shape
(rather than a bare string) keeps the interface extensible — e.g. adding
conversation history later doesn't change the calling convention for existing
callers.

### Best practices
- Turn on `mlflow.langchain.autolog()` while iterating — every retriever call,
  compiled prompt, and raw LLM response gets traced in the MLflow UI, which is
  dramatically faster for debugging than adding print statements.
- Test the retriever **in isolation** before testing the full chain — if retrieval
  is bad, no prompt tweak fixes the final answer.
- Test an **out-of-scope question** ("What is the capital of France?") explicitly —
  a system that answers everything confidently, including things it shouldn't know,
  is worse than one that sometimes says "I don't know."

### Common mistakes
- Putting the question *before* the context in the human message — works fine on
  short contexts, degrades silently as `k` or chunk size grows.
- Skipping an explicit refusal instruction — without it, the LLM will happily
  hallucinate a plausible-sounding but ungrounded answer.
- Using a high temperature "to make answers more natural" — this directly
  increases hallucination rate in a grounded-QA setting.

### How it fits into the architecture
This IS "the model." Everything from here on — registration, evaluation,
deployment — operates on this chain as a single unit.

---

## 6. Task 3 — Register the Model

**Notebook:** [`notebooks/04_register_model.py`](notebooks/04_register_model.py)

### Why it's needed
An object living in a notebook's memory isn't reproducible, versioned, or
governable. Registration turns "the chain I just built" into a durable,
access-controlled, lineage-tracked artifact that can be loaded identically by
anyone/anything (evaluation, serving) months later.

### What Databricks feature is used
**MLflow** (tracking + logging) targeting the **Unity Catalog Model Registry**
(`mlflow.set_registry_uri("databricks-uc")`), using the **"Models from Code"**
pattern.

### Step by step

**1. Log the model** — as *source code*, not a pickled object:
```python
mlflow.langchain.log_model(
    lc_model="src/rag_chain.py",          # the file itself is the model definition
    artifact_path="rag_chain",
    code_paths=["src"],                    # bundles retriever.py / utils.py it imports
    input_example={"question": "How do I enable Change Data Feed?"},
    signature=signature,
    pip_requirements="requirements.txt",
)
```
**Why source code, not pickle?** No pickle version-skew between the environment
that logged the model and the (different) container that later serves it; the
exact source is the artifact, so `git diff` on `rag_chain.py` *is* your model diff;
and it works with objects (live endpoint clients) that don't pickle cleanly.

`code_paths=["src"]` is the detail most people miss: `rag_chain.py` imports
`retriever.py` and `utils.py`. Without bundling the whole package, the model logs
fine locally (because `src` happens to be on the notebook's `sys.path`) but throws
`ModuleNotFoundError` inside the isolated serving container later — a classic
"works on my notebook" bug.

**2. Sanity-check before registering**: reload with `mlflow.pyfunc.load_model` and
run one prediction. This catches packaging bugs in seconds instead of after a
failed registration or deployment three steps later.

**3. Register**:
```python
mlflow.register_model(model_uri=logged_model.model_uri, name="main.rag_lab.databricks_docs_rag_model")
```
The 3-level UC name is why we set the registry URI to `databricks-uc` — it puts the
model under the *same* governance boundary (GRANT/REVOKE, lineage) as the tables
and index it depends on.

**4. Version it**: every `register_model` call creates an immutable version number.
Tag it (`set_model_version_tag(..., key="source_run_id", ...)`) so you can trace a
served model back to the exact experiment run, parameters, and metrics that
produced it.

**5. Load it back**: `mlflow.pyfunc.load_model("models:/main.rag_lab.databricks_docs_rag_model/1")`
— proving the registry round-trips correctly is what Model Serving will do later,
so verify it now.

### Why each step matters
- **Logging** = reproducibility (exact artifact, pinned dependencies).
- **Signature** = Model Serving can validate request shapes and reject malformed
  input with a clear error instead of an opaque 500.
- **Registering** = governance + discoverability (anyone with UC access can find
  and inspect this model).
- **Versioning + tagging** = auditability (what changed between v3 and v4, and
  why).
- **Aliases** (used in the deployment step) = decouple "what's in production" from
  a specific version number, so promoting a new version is a one-line change, not a
  redeploy-everything event.

### Common mistakes
- Forgetting `code_paths` → `ModuleNotFoundError` only surfaces at serving time.
- Skipping `input_example`/`signature` → schema errors surface as confusing runtime
  500s instead of being caught at logging time.
- Registering to the legacy workspace registry instead of Unity Catalog (forgetting
  `mlflow.set_registry_uri("databricks-uc")`) → loses UC governance/lineage.

---

## 7. Task 4 — Evaluate the Model

**Notebook:** [`notebooks/05_evaluate_model.py`](notebooks/05_evaluate_model.py)
**Library code:** [`src/evaluation.py`](src/evaluation.py)

### Why it's needed
"It gave a reasonable-looking answer to the one question I tried" is not
evaluation. RAG systems fail in two independent places — retrieval and generation
— and you need metrics that can tell you *which one* failed, on a repeatable
dataset, before you ship.

### What Databricks feature is used
**Mosaic AI Agent Evaluation**, via `mlflow.evaluate(model_type="databricks-agent")`,
which runs the *registered* model through built-in LLM-judge metrics, combined with
hand-written retrieval metrics for full-stack coverage.

### The evaluation dataset
`src/evaluation.py::SEED_EVAL_EXAMPLES` — a small golden set of
`(question, expected_answer, expected_source_urls)` triples, deliberately
including **one out-of-scope question** ("What is the capital of France?") that
should be *refused*, not answered. Grow this to 50–200 examples covering realistic
phrasing before treating results as statistically meaningful; 5 examples only
proves the pipeline is wired correctly.

### Metrics measured

| Metric | Definition | Computed by |
|---|---|---|
| **Retrieval Precision@k** | `\|retrieved ∩ expected\| / \|retrieved\|` — what fraction of retrieved chunks are actually relevant | `src/evaluation.py::retrieval_precision_at_k` |
| **Retrieval Recall@k** | `\|retrieved ∩ expected\| / \|expected\|` — what fraction of the truly relevant docs were found | `src/evaluation.py::retrieval_recall_at_k` |
| **Context Relevance** (`chunk_relevance`) | LLM-judged: are the retrieved chunks relevant to the question? | `mlflow.evaluate(model_type="databricks-agent")` |
| **Faithfulness / Groundedness** | LLM-judged: is every claim in the answer supported by the retrieved context (i.e., not hallucinated)? | same |
| **Answer Correctness** | LLM-judged: does the answer match the expected/reference answer? | same |
| **Safety** | LLM-judged: does the answer avoid harmful/inappropriate content? | same |
| **Latency (p50 / p90 / mean)** | End-to-end wall-clock time per question | `src/evaluation.py::measure_latency` |

### Why measure both retrieval and generation metrics
If you only measure "is the final answer correct," a low score is ambiguous: did
the retriever fetch the wrong chunks, or did the LLM hallucinate despite good
chunks? Splitting the metrics is what makes results *actionable* rather than just a
single scary/reassuring number (see Section 8).

### Code sketch
```python
eval_pdf = evaluation.build_eval_dataset(spark, cfg)
retrieval_results = evaluation.evaluate_retrieval(get_retriever(cfg), eval_pdf)
eval_results = evaluation.run_mlflow_evaluation(model_uri, eval_pdf, cfg)
latency_stats = evaluation.measure_latency(build_chain(cfg), eval_pdf["request"].tolist())
```

### Best practices
- Evaluate the **registered model URI**, not the in-memory Python object — this
  exercises the exact artifact that will be deployed, catching packaging bugs an
  in-memory smoke test would miss.
- Include **adversarial / out-of-scope examples**, not just "easy" questions —
  refusal behavior is a correctness property too.
- Treat LLM-judge scores as **strong signal, not ground truth** — spot-check a
  sample of judged answers yourself before fully trusting the aggregate.

### Common mistakes
- Evaluating on the same handful of questions used during prompt-engineering
  iteration (overfitting the eval set to your own testing).
- Reporting only the mean latency and missing p90 — tail latency (cold starts,
  slow retrievals) is what actually drives user complaints.
- Treating a single low metric in isolation instead of cross-referencing retrieval
  vs. generation metrics (see Section 8).

---

## 8. Task 5 — Interpret the Evaluation Results

This is where metrics become decisions. Use this table (also embedded as markdown
in `notebooks/05_evaluate_model.py`) to map symptom → root cause → fix:

| Symptom | Likely Root Cause | Fix |
|---|---|---|
| **Good retrieval** (high precision, high recall) but **low groundedness** | LLM is hallucinating despite having the right context | Tighten the system prompt's grounding rule; lower temperature; add a reinforcing instruction in the human message |
| **Bad retrieval**: low recall (right doc never retrieved) | Corpus gap, embedding/query mismatch, or a chunk size that doesn't represent the concept well | Verify the source page was actually ingested; try a larger `k`; reconsider `chunk_size`; consider hybrid search for exact-term queries |
| **Bad retrieval**: low precision, recall OK | Right doc retrieved but buried among irrelevant chunks | Check embedding model fit for your domain; add metadata filtering (e.g., product area, doc version) |
| **Hallucinations**: low chunk_relevance AND low correctness together | Retrieval-stage failure — generation never had a chance | Fix retrieval first; don't tune prompts to compensate for missing context |
| **Missing context**: high chunk_relevance, low correctness | Generation-stage failure despite good context | Prompt engineering issue, or the question needs reasoning across multiple chunks the LLM isn't combining |
| **Low relevance** across most questions | Embedding model mismatch for the domain, or overly aggressive chunking destroying semantic coherence | Try a different embedding model; increase chunk size; review chunk boundaries manually |
| **Poor chunking**: answers technically grounded but incomplete / cut off mid-explanation | `chunk_size` too small, or splitter cutting through a logical section (e.g., a code block) | Increase `chunk_size`/`chunk_overlap`; switch to a markdown-header-aware splitter for sections with deep structure |
| Confidently answers the out-of-scope question instead of refusing | Grounding instruction too weak | Make the refusal instruction explicit; add a matching few-shot example |
| p90 latency far above p50 | Cold starts (scale-to-zero) or occasional slow retrieval | Disable scale-to-zero for latency-sensitive traffic, or pre-warm the endpoint |

**Rule of thumb:** always diagnose retrieval before generation. A generation-stage
fix (prompt engineering) cannot compensate for a retrieval-stage failure (the right
information was never in the prompt to begin with) — you'll just be teaching the
model to hedge or hallucinate more gracefully instead of fixing the actual gap.

**Gate promotion on thresholds, not vibes** (`config.yaml::evaluation`):
```python
passed = (
    metrics["groundedness/v1/mean"] >= cfg.raw["evaluation"]["min_faithfulness"]
    and metrics["correctness/v1/mean"] >= cfg.raw["evaluation"]["min_answer_correctness"]
    and latency_stats["p90_seconds"] <= cfg.raw["evaluation"]["max_p90_latency_seconds"]
)
```
This turns evaluation into a real CI-style gate instead of a report nobody acts on.

---

## 9. Task 6 — Deploy the Model

**Notebook:** [`notebooks/06_deploy_model.py`](notebooks/06_deploy_model.py)
**Library code:** [`src/deployment.py`](src/deployment.py)

### Why it's needed
A registered model isn't callable by an application until it's served behind an
API. Deployment also adds request/response logging (for monitoring) and, via the
Agent Framework, a human-testable UI (Task 7).

### What Databricks feature is used
**Mosaic AI Model Serving**, provisioned the recommended way for agents via
`databricks.agents.deploy()`.

### Endpoint creation
```python
client.set_registered_model_alias(name=cfg.registered_model_name, alias="champion", version=MODEL_VERSION)

deployment_info = agents.deploy(
    model_name=cfg.registered_model_name,
    model_version=champion_version,
    scale_to_zero=True,
    workload_size="Small",
)
```
`agents.deploy()` does four things atomically: creates/updates the serving
endpoint, enables an **inference table**, provisions the **Review App**, and wires
permissions between them.

### Configuration choices explained
- **Alias-based deployment** (`champion`) instead of a hard-coded version number:
  deployment code never changes when you promote a new version — you just move the
  alias. Six months later, nobody has to remember *why* production is pinned to
  version 1.
- **`workload_size="Small"`**: start small, scale up based on observed p90
  latency/throughput from real traffic — don't over-provision preemptively.
- **`scale_to_zero=True`**: saves cost for spiky/low-volume traffic (a lab, an
  internal tool) at the cost of cold-start latency on the first request after
  idling. Turn it **off** for latency-sensitive production traffic.

### Authentication
Both `agents.deploy()` and `deployment.query_endpoint()` authenticate through the
Databricks control plane using the caller's credentials (workspace auth or a
service principal token in a job context) — never a hard-coded API key. Secrets
(if any are needed) are injected via `environment_vars`, not literals in code.

### Calling the endpoint
```python
from mlflow.deployments import get_deploy_client
client = get_deploy_client("databricks")
response = client.predict(
    endpoint="databricks_docs_rag_endpoint",
    inputs={"inputs": [{"question": "How do I enable Change Data Feed?"}]},
)
```
Wrapped with exponential-backoff retry (`src/utils.py::call_with_retry`) — serving
endpoints can return transient 429/503 under burst load; a single failed request
shouldn't fail the whole calling application.

### Best practices
- Deploy **the alias's current version**, never a hard-coded version number.
- Always **smoke-test the actual REST endpoint** after deployment — logging and
  registering successfully does not guarantee the served container starts cleanly
  (environment/dependency drift between notebook and serving container is the most
  common gap).
- Confirm the **inference table is populated** immediately after deploying — that's
  what monitoring/drift-detection reads from later; better to catch a logging gap
  now than during an incident.

### Common mistakes
- Skipping the post-deploy smoke test and discovering packaging bugs only when a
  real user hits the endpoint.
- Leaving `scale_to_zero` on for a demo/review session and being confused by a slow
  first response (it's a cold start, not a bug).
- Granting overly broad permissions instead of the minimum (`CAN_QUERY`) needed for
  reviewers.

---

## 10. Task 7 — Test with the Review App

**Notebook:** [`notebooks/07_review_app_testing.py`](notebooks/07_review_app_testing.py)

### Why it's needed
Offline evaluation (Task 4/5) runs against a fixed golden set you wrote yourself.
Real users ask questions differently. The Review App closes that loop by letting
non-technical stakeholders exercise the *actual deployed endpoint* and leave
structured feedback, without needing code or API access.

### What Databricks feature is used
The **Mosaic AI Agent Framework Review App** — auto-provisioned by `agents.deploy()`
in Task 6, pointed at the same serving endpoint.

### Connect the deployed endpoint
```python
deployment_info = agents.get_deployments(model_name=cfg.registered_model_name)[0]
review_app_url = deployment_info.review_app_url

agents.set_permissions(
    model_name=cfg.registered_model_name,
    users=["stakeholder@company.com"],
    permission_level=agents.PermissionLevel.CAN_QUERY,
)
```
Granting `CAN_QUERY` specifically (not broader workspace access) is the principle
of least privilege applied to human testers — they can use the app, not see the
underlying notebooks or endpoint config.

### Test prompts (run manually in the browser UI)

| Prompt | What it checks | Expected outcome |
|---|---|---|
| "How do I enable Change Data Feed on a Delta table?" | Correctness + citation | Correct syntax, cites the right doc URL |
| "What's the difference between a Job and a DLT pipeline?" | Comparison handling | Both concepts covered |
| "What is the capital of France?" | Refusal behavior | Declines, does NOT hallucinate |
| Ambiguous/vague question | Judgment under ambiguity | Asks for clarification or covers the most likely interpretation |

### Interpreting responses
For each test, click 👍/👎 and leave a **specific** free-text comment — "wrong" is
not actionable, "cited the right doc but got the SQL syntax wrong" is. This
feedback is what eventually feeds back into `src/evaluation.py::SEED_EVAL_EXAMPLES`,
growing the golden set with real failure modes.

### Troubleshooting common failures

| Symptom | Cause | Fix |
|---|---|---|
| "Endpoint not ready" in the Review App | Serving endpoint still `UPDATING` | Wait for the endpoint to fully finish updating before sharing the URL |
| Reviewer sees "permission denied" | Not granted `CAN_QUERY`, or email mismatch | Re-run `agents.set_permissions`; confirm SSO email matches exactly |
| Every answer is the refusal message | Retriever failing silently in the served environment | Check `code_paths` was set at logging time; inspect endpoint logs in the Serving UI |
| First message slow, rest fast | Scale-to-zero cold start | Expected; disable scale-to-zero for demo sessions if it matters |
| Feedback button doesn't save | Reviewer not authenticated / cookies blocked | Have them log into the workspace directly first, then open the Review App link in the same session |
| Cited sources are broken/wrong URLs | URL reconstruction bug during ingestion | Spot-check `src/ingestion.py::load_documents_from_volume`'s slug→URL logic |

---

## 11. End-to-End Workflow

```
User Question
      │
      ▼
Embedding                (Foundation Model API: databricks-bge-large-en)
      │                  query text -> dense vector, SAME model used to embed the corpus
      ▼
Vector Search             (Mosaic AI Vector Search: Delta-Sync index)
      │                  ANN similarity search over main.rag_lab.databricks_docs_index
      ▼
Top-k Documents            (k=5 by default)
      │                  chunk_text + url + title metadata for each hit
      ▼
Prompt Assembly            (LangChain ChatPromptTemplate)
      │                  system grounding rule + numbered context blocks + question
      ▼
LLM                        (Foundation Model API: databricks-meta-llama-3-3-70b-instruct)
      │                  temperature=0.1, generates a grounded answer with citations
      ▼
Generated Answer
      │
      ▼
Evaluation                 (mlflow.evaluate(model_type="databricks-agent") + retrieval metrics)
      │                  offline gate: faithfulness, correctness, precision/recall, latency thresholds
      ▼
Deployment                 (Mosaic AI Model Serving via agents.deploy())
      │                  autoscaling REST endpoint + inference table logging
      ▼
Review App                 (Agent Framework Review App)
      │                  human feedback (👍/👎 + comments) -> feeds back into the golden eval set
      ▼
   (loop back to Evaluation on the next iteration)
```

**Step-by-step explanation:**

1. **User Question** — natural language, no special formatting required.
2. **Embedding** — the question is encoded into the same vector space as the
   corpus; using a *different* embedding model here than at index time would make
   cosine similarity meaningless.
3. **Vector Search** — approximate nearest-neighbor search finds chunks whose
   embeddings are closest to the query embedding.
4. **Top-k Documents** — the actual retrieved text plus metadata (source URL) that
   will ground the answer and enable citation.
5. **Prompt Assembly** — deterministic templating combines instructions + context +
   question into exactly what the LLM sees; nothing here is left to chance or
   free-form string concatenation.
6. **LLM** — generates the answer conditioned on the assembled prompt.
7. **Generated Answer** — what the user (or the calling application) receives.
8. **Evaluation** — happens *offline*, against the registered model, before every
   promotion — not something that happens once and is forgotten.
9. **Deployment** — the exact evaluated artifact (by alias) is what gets served —
   no re-logging, no drift between "what was tested" and "what's running."
10. **Review App** — closes the loop with human-in-the-loop feedback that improves
    the evaluation set for the next iteration.

---

## 12. Best Practices

### Chunking strategies
- Start with `RecursiveCharacterTextSplitter` (paragraph → sentence → word →
  character fallback) — it preserves semantic coherence better than a hard
  fixed-length cut.
- For documentation with heavy code samples, consider a **markdown-header-aware**
  splitter so a code block is never bisected.
- Always keep **10–20% overlap** between chunks so answers that straddle a
  paragraph boundary remain retrievable from at least one chunk.
- Filter near-empty chunks (nav fragments, "Was this page helpful?" footers) —
  noise in the index directly degrades precision.

### Embedding model selection
- Use a model trained/benchmarked on retrieval tasks (e.g., BGE family), not a
  general-purpose sentence encoder.
- Match embedding dimensionality expectations in your index schema explicitly —
  a silent mismatch causes cryptic index-creation failures.
- Never mix embedding models between corpus and query embedding.

### Prompt engineering
- Put the grounding/refusal rule in the **system** message, not buried in the human
  message — it's more reliably followed there.
- Number and clearly delimit context blocks; ask explicitly for citations.
- Keep the question closest to the generation point in the prompt.
- Keep temperature low (`0.0`–`0.2`) for factual/documentation Q&A.

### Retrieval optimization
- Tune `k` empirically against your eval set's recall — too low misses relevant
  chunks, too high dilutes the prompt and increases latency/cost.
- Re-rank retrieved chunks with a cross-encoder if precision remains low after
  chunking/embedding tuning.

### Metadata filtering
- Store structured metadata (product area, doc version, page type) alongside each
  chunk, and expose it as filterable columns in Vector Search
  (`filters={"doc_version": "latest"}`) — this lets you scope retrieval instead of
  relying on embeddings alone to disambiguate near-duplicate content (e.g., old vs.
  current API docs).

### Hybrid search
- Pure dense vector search under-matches exact identifiers (config keys, API
  names, error codes). Mosaic AI Vector Search supports **hybrid** (keyword +
  vector) search — enable it when your corpus has many exact-term lookups.

### Index optimization
- Use `TRIGGERED` sync for batch-updated corpora (cost-efficient); reserve
  `CONTINUOUS` for corpora that change constantly and where near-real-time
  freshness is a genuine product requirement.
- Re-sync (`index.sync()`) explicitly after bulk re-ingestion rather than relying
  on implicit triggers.

### Security
- Everything stays inside Unity Catalog governance — grant access to the source
  tables, the index, and the registered model independently, following least
  privilege.
- Grant Review App reviewers `CAN_QUERY` only, never broader workspace permissions.
- Never hard-code tokens/secrets — inject via `environment_vars` at deploy time or
  Databricks secret scopes.
- Sanitize scraped documentation content before indexing if the source could ever
  include user-generated content (prompt-injection risk via retrieved context).

### Cost optimization
- `scale_to_zero=True` for spiky/low-traffic endpoints (labs, internal tools);
  disable it for latency-sensitive production traffic where cold starts are
  unacceptable.
- Use `TRIGGERED` Vector Search sync instead of `CONTINUOUS` unless you need
  near-real-time updates.
- Right-size `workload_size` from observed metrics, not guesses — start Small.

### Performance optimization
- Track **p90**, not just mean, latency — tail latency drives complaints.
- Keep `k` and `max_tokens` as small as your quality bar allows — both directly
  affect latency and cost.
- Batch embedding calls during ingestion (see `src/embeddings.py::embed_texts`)
  instead of one-request-per-chunk.

### Monitoring
- Inference tables (enabled automatically by `agents.deploy()`) log every
  request/response — build a scheduled job or DLT pipeline over them to track
  answer-length drift, latency trends, and refusal rate over time.
- Periodically re-run the evaluation notebook against production traffic samples
  pulled from the inference table, not just the static golden set.

### Scaling
- As the corpus grows past a few thousand pages, re-benchmark chunk size and `k` —
  what worked for 500 pages may retrieve too much noise at 50,000.
- Consider partitioning the index by product area / doc version if the corpus
  becomes heterogeneous, combined with metadata filtering at query time.

---

## 13. Troubleshooting Reference

> **Looking for a specific error message?** This table covers general
> categories. **[`RUNBOOK.md`](RUNBOOK.md)** has the full incident log of every
> real failure hit getting this pipeline green in `dev` — exact error text,
> root cause, and the fix, for 31 distinct issues spanning infra, dependency
> pinning, MLflow internals, and Model Serving deployment. Check there first
> if the error below doesn't match exactly.

| Problem | Where to look | Likely fix |
|---|---|---|
| `ModuleNotFoundError` when serving | Model logging step | Add the missing package to `code_paths` in `mlflow.langchain.log_model` |
| Vector Search index creation fails | Silver Delta table | Confirm `delta.enableChangeDataFeed = true` is set |
| Index stuck in `PROVISIONING` | Vector Search endpoint | Call `wait_until_ready(timeout=1800)`; check endpoint health in Catalog Explorer |
| Retrieval returns irrelevant chunks | Ingestion / chunking | Re-check `chunk_size`/`chunk_overlap`; verify HTML cleaning didn't strip real content |
| Model hallucinates | Prompt / evaluation | Tighten system prompt grounding rule; check `groundedness` metric, not just `correctness` |
| Deployment succeeds but every request 500s | Serving container | Check `code_paths` completeness; review endpoint build logs in the Serving UI |
| Review App reviewer can't access | Permissions | Re-run `agents.set_permissions` with the correct, exact email |
| Evaluation gate keeps failing | Interpretation table | Work through Section 8's symptom → root cause → fix table before re-tuning blindly |

---

## 14. Infrastructure as Code (Terraform)

The infra shell around this pipeline — Unity Catalog objects, the Vector Search
endpoint, the registered-model container, the orchestration Job, and (once a model
exists) the Model Serving endpoint — is managed by Terraform in **[`terraform/`](terraform/README.md)**,
built to organization production standard:

- A single reviewed module (`terraform/modules/rag_lab`) instantiated per
  environment (`dev` / `staging` / `prod`), not copy-pasted config
- Remote state on Azure Blob Storage with versioning, bootstrapped once via
  `terraform/bootstrap`
- `prevent_destroy` on every data-bearing resource (catalog, schema, volume,
  registered model) in every environment, including dev
- Variable validation blocks that fail `terraform plan` with a clear message
  instead of a confusing Databricks API error
- No stored secrets — Azure AD OIDC federation for CI, `az login`/env vars locally
- CI/CD (`.github/workflows/terraform.yml`): `fmt`/`validate`/`tflint`/`tfsec` gate
  on every PR with a posted plan, then linear promotion dev → staging (reviewer
  approval) → prod (reviewer approval) on merge
- Optional network hardening (VNet injection, No Public IP, Log Analytics
  diagnostics) — on by default in prod

What Terraform does **not** manage — populating the Delta tables, logging/
registering an actual MLflow model version, running evaluation, and provisioning
the Review App — stays in the notebooks above; see `terraform/README.md` for the
full explanation of that boundary and the "two-phase apply" pattern used for the
Vector Search index and Serving endpoint.

---

## References
- `docs/architecture.md` — full architecture diagrams and component explanations
- `config/config.yaml` — all configurable names/IDs used throughout the pipeline
- `terraform/README.md` — infrastructure-as-code setup, CI/CD, and environment promotion
- **[`RUNBOOK.md`](RUNBOOK.md)** — real incident log: every failure hit getting the
  pipeline green, with root cause and fix for each
- Databricks documentation: Mosaic AI Vector Search, Foundation Model APIs, MLflow
  on Databricks, Unity Catalog Model Registry, Mosaic AI Agent Framework & Review App
