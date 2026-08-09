# Databricks notebook source
# MAGIC %md
# MAGIC # 00 — Environment Setup
# MAGIC
# MAGIC **Why this notebook exists:** every downstream step (indexing, chaining,
# MAGIC evaluation, deployment) assumes libraries are installed, Unity Catalog
# MAGIC objects exist, and the repo's `src/` package is importable. Doing that
# MAGIC setup once, here, keeps every other notebook focused on its actual task.
# MAGIC
# MAGIC **Cluster requirement:** Databricks Runtime **15.4 LTS ML** or newer
# MAGIC (16.x ML also works). You need the **ML runtime**, not the standard
# MAGIC runtime — it pre-installs MLflow, scikit-learn, and other GenAI
# MAGIC dependencies at versions tested against each other. Using the standard
# MAGIC runtime is a common mistake that leads to subtle MLflow/numpy conflicts.
# MAGIC
# MAGIC **Common mistake:** installing packages with `%pip install` on a cluster
# MAGIC that's shared with other users/jobs. Prefer a **single-user, personal
# MAGIC compute** cluster for development, and pin exact versions in
# MAGIC `requirements.txt` before promoting to a job cluster.

# COMMAND ----------

# MAGIC %pip install -r ../requirements.txt
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Add the project root to `sys.path`
# MAGIC So that `from src.utils import load_config` works the same way inside a
# MAGIC Databricks notebook as it does in `pytest` locally.

# COMMAND ----------

import sys
from pathlib import Path

project_root = Path.cwd().parent  # notebooks/ -> repo root
if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

from src.utils import load_config, ensure_uc_objects, get_logger  # noqa: E402

log = get_logger("00_environment_setup")
cfg = load_config()

log.info(f"Catalog.Schema = {cfg.catalog}.{cfg.schema}")
log.info(f"Vector Search endpoint = {cfg.vs_endpoint_name}")
log.info(f"LLM endpoint = {cfg.llm_endpoint}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Create Unity Catalog objects
# MAGIC
# MAGIC We need:
# MAGIC   - a **catalog** (top-level container, like a database server)
# MAGIC   - a **schema** (like a database) to hold our tables and the registered model
# MAGIC   - a **volume** (governed file storage) to land raw documentation files
# MAGIC
# MAGIC **Why Unity Catalog and not the legacy Hive metastore / DBFS root?**
# MAGIC UC gives us fine-grained access control, lineage (you can trace a served
# MAGIC answer back to the exact chunk, table, and job that produced it), and is
# MAGIC required by Databricks Vector Search and the UC Model Registry used later.

# COMMAND ----------

ensure_uc_objects(spark, cfg)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Verify Foundation Model API access
# MAGIC
# MAGIC Confirm the embedding and LLM endpoints configured in `config.yaml` are
# MAGIC reachable BEFORE building the rest of the pipeline. Catching an auth or
# MAGIC entitlement problem here costs 10 seconds; catching it three notebooks
# MAGIC later after chunking 5,000 documents costs an afternoon.

# COMMAND ----------

from mlflow.deployments import get_deploy_client

client = get_deploy_client("databricks")

test_embedding = client.predict(
    endpoint=cfg.embedding_model_endpoint,
    inputs={"input": ["What is Delta Lake?"]},
)
assert len(test_embedding["data"][0]["embedding"]) == cfg.raw["vector_search"]["embedding_dimension"], (
    "Embedding dimension mismatch vs config.yaml — update "
    "vector_search.embedding_dimension to match the model actually returned."
)
log.info("Embedding endpoint OK ✅")

test_llm = client.predict(
    endpoint=cfg.llm_endpoint,
    inputs={"messages": [{"role": "user", "content": "Say 'ready' and nothing else."}], "max_tokens": 10},
)
log.info(f"LLM endpoint OK ✅ -> {test_llm['choices'][0]['message']['content']}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Checklist before moving to `01_data_ingestion_and_chunking`
# MAGIC - [ ] Cluster is DBR 15.4 LTS ML+ (check top-right of the notebook)
# MAGIC - [ ] `%pip install` cell completed without dependency resolver errors
# MAGIC - [ ] `ensure_uc_objects` ran without permission errors (you need
# MAGIC       `CREATE CATALOG`/`CREATE SCHEMA` privileges, or ask a workspace admin
# MAGIC       to pre-create `main.rag_lab` and grant you `USE`/`CREATE` on it)
# MAGIC - [ ] Both endpoint smoke tests printed "OK ✅"
