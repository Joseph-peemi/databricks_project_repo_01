# Databricks notebook source
# MAGIC %md
# MAGIC # 02 — Create the AI Search (Vector Search) Index
# MAGIC
# MAGIC **Lab task 1: "Create an AI search index."**
# MAGIC
# MAGIC **Databricks feature:** Mosaic AI Vector Search — a managed vector
# MAGIC database that (a) computes embeddings for you via Databricks-hosted
# MAGIC embedding models, and (b) keeps the index automatically in sync with a
# MAGIC source Delta table via **Delta-Sync indexes**.
# MAGIC
# MAGIC **Why Vector Search instead of a self-hosted vector DB (FAISS/Chroma/etc.)?**
# MAGIC   - No infrastructure to manage (serverless compute endpoint).
# MAGIC   - Governed by Unity Catalog: same access control as the source table.
# MAGIC   - Delta-Sync means new/changed docs flow into the index automatically
# MAGIC     instead of you writing a custom incremental-embedding job.
# MAGIC   - Built-in hybrid (keyword + vector) search support.

# COMMAND ----------

# MAGIC %pip install -r ../requirements.txt
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import sys
from pathlib import Path

project_root = Path.cwd().parent
if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

from src.utils import load_config, get_logger  # noqa: E402

log = get_logger("02_vector_index")
cfg = load_config()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 1 — Create (or reuse) a Vector Search endpoint
# MAGIC
# MAGIC The **endpoint** is the compute layer (like a serverless SQL warehouse,
# MAGIC but for vector search); an endpoint can host multiple **indexes**.
# MAGIC
# MAGIC **Best practice:** one endpoint per environment (dev/staging/prod), not
# MAGIC one per index — indexes are cheap to add to an existing endpoint, but
# MAGIC each endpoint has its own warm-up/cold-start cost.
# MAGIC
# MAGIC **Common mistake:** re-creating the endpoint on every notebook run. We
# MAGIC check `list_endpoints()` first and create only if missing, so this cell
# MAGIC is safe to re-run.

# COMMAND ----------

from databricks.vector_search.client import VectorSearchClient

vsc = VectorSearchClient(disable_notice=True)

existing_endpoints = [e["name"] for e in vsc.list_endpoints().get("endpoints", [])]

if cfg.vs_endpoint_name not in existing_endpoints:
    log.info(f"Creating Vector Search endpoint: {cfg.vs_endpoint_name}")
    vsc.create_endpoint(name=cfg.vs_endpoint_name, endpoint_type="STANDARD")
else:
    log.info(f"Reusing existing endpoint: {cfg.vs_endpoint_name}")

vsc.wait_for_endpoint(cfg.vs_endpoint_name)
log.info("Endpoint is ONLINE ✅")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2 — Create a Delta-Sync index with Databricks-managed embeddings
# MAGIC
# MAGIC Key parameters explained:
# MAGIC   - `source_table_name`: the Silver chunked table from notebook 01. MUST
# MAGIC     have Change Data Feed enabled (we verified this already).
# MAGIC   - `pipeline_type="TRIGGERED"`: sync runs when you explicitly call
# MAGIC     `.sync()` (good for batch/lab workloads — predictable cost). Use
# MAGIC     `"CONTINUOUS"` in production if the corpus changes constantly and
# MAGIC     near-real-time freshness matters (higher cost — a cluster stays up).
# MAGIC   - `primary_key="chunk_id"`: must be unique per row (we validated this
# MAGIC     in notebook 01).
# MAGIC   - `embedding_source_column="chunk_text"` + `embedding_model_endpoint_name`:
# MAGIC     this is what makes it "Databricks-managed embeddings" — you never
# MAGIC     write embedding code yourself; Databricks computes and refreshes
# MAGIC     vectors whenever the source column changes.

# COMMAND ----------

existing_indexes = [
    idx["name"] for idx in vsc.list_indexes(cfg.vs_endpoint_name).get("vector_indexes", [])
]

if cfg.vs_index_name not in existing_indexes:
    log.info(f"Creating Delta-Sync index: {cfg.vs_index_name}")
    vsc.create_delta_sync_index(
        endpoint_name=cfg.vs_endpoint_name,
        source_table_name=cfg.chunked_docs_table,
        index_name=cfg.vs_index_name,
        pipeline_type=cfg.raw["vector_search"]["pipeline_type"],
        primary_key=cfg.raw["vector_search"]["primary_key"],
        embedding_source_column=cfg.raw["vector_search"]["embedding_source_column"],
        embedding_model_endpoint_name=cfg.embedding_model_endpoint,
    )
else:
    log.info(f"Index {cfg.vs_index_name} already exists — will sync instead of re-create.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 3 — Populate / sync the index
# MAGIC
# MAGIC For a `TRIGGERED` pipeline, the initial `create_delta_sync_index` call
# MAGIC already performs a first full sync. Call `.sync()` again any time the
# MAGIC source table changes (e.g. after re-running notebook 01 with new docs).
# MAGIC
# MAGIC **Common mistake:** querying the index immediately after creation while
# MAGIC it's still in `PROVISIONING` status — always wait for `ONLINE` first.

# COMMAND ----------

import datetime  # noqa: E402

index = vsc.get_index(endpoint_name=cfg.vs_endpoint_name, index_name=cfg.vs_index_name)
# databricks-vectorsearch==0.50's wait_until_ready compares elapsed time
# (a timedelta) against `timeout` directly -- passing a raw int (seconds)
# raises "'<' not supported between instances of 'datetime.timedelta' and
# 'int'". Must pass a timedelta.
index.wait_until_ready(timeout=datetime.timedelta(seconds=1800))
log.info("Index status: ONLINE ✅")

# Re-sync explicitly (no-op / fast if already up to date). Uncomment when
# re-running after new documents have been ingested:
# index.sync()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 4 — Verify the index works: run a manual similarity search
# MAGIC
# MAGIC Never trust index creation success alone — always issue a real query and
# MAGIC eyeball the results before building the RAG chain on top of it. This is
# MAGIC the fastest way to catch a bad embedding model choice, a schema mismatch,
# MAGIC or an empty index.

# COMMAND ----------

test_query = "How do I enable Change Data Feed on a Delta table?"

results = index.similarity_search(
    query_text=test_query,
    columns=["chunk_id", "url", "title", "chunk_text"],
    num_results=5,
)

for i, row in enumerate(results["result"]["data_array"], start=1):
    chunk_id, url, title, chunk_text, score = row
    print(f"[{i}] score={score:.4f} | {title} | {url}")
    print(f"    {chunk_text[:160]}...\n")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Checklist before moving to `03_build_rag_pipeline`
# MAGIC - [ ] Endpoint status is ONLINE
# MAGIC - [ ] Index status is ONLINE (not PROVISIONING / FAILED)
# MAGIC - [ ] Manual `similarity_search` returned relevant, on-topic chunks for
# MAGIC       a known question — if results look random, re-check `chunk_size`
# MAGIC       and the embedding model choice before proceeding
# MAGIC - [ ] Row count in the index roughly matches `SELECT COUNT(*) FROM
# MAGIC       {chunked_docs_table}` (a large gap suggests failed embedding calls
# MAGIC       during sync — check the index's sync history in the Catalog Explorer UI)
