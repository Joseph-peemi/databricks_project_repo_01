# Databricks notebook source
# MAGIC %md
# MAGIC # 04 — Register the RAG Model
# MAGIC
# MAGIC **Lab task 3: "Register the RAG model."**
# MAGIC
# MAGIC **Why register at all, instead of just deploying the notebook's `chain`
# MAGIC object?**
# MAGIC   - **Versioning**: every registration creates an immutable version —
# MAGIC     you can always roll back.
# MAGIC   - **Governance**: Unity Catalog model registry applies the same
# MAGIC     access-control model as tables (GRANT/REVOKE), and lineage links
# MAGIC     the model back to the experiment run, the source table, and (via
# MAGIC     the Vector Search index) the underlying documents.
# MAGIC   - **Reproducibility**: the registered artifact is exactly what gets
# MAGIC     served — no "works on my notebook" drift between dev and prod.
# MAGIC   - **Aliases**: instead of hard-coding "version 7" in deployment code,
# MAGIC     you point at an alias like `champion`, and repoint the alias after
# MAGIC     each evaluation cycle. Deployment code never changes.

# COMMAND ----------

import sys
from pathlib import Path

project_root = Path.cwd().parent
if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

import mlflow
from mlflow.models import infer_signature

from src.utils import load_config, get_logger  # noqa: E402

log = get_logger("04_register_model")
cfg = load_config()

mlflow.set_registry_uri("databricks-uc")  # register into Unity Catalog, not the workspace registry
mlflow.set_experiment(cfg.experiment_path)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 1 — Log the model using "Models from Code"
# MAGIC
# MAGIC `lc_model="../src/rag_chain.py"` tells MLflow to log the SOURCE FILE as
# MAGIC the model, not a pickled Python object (see the big comment at the top of
# MAGIC `src/rag_chain.py` for why). When this model is later loaded (for
# MAGIC evaluation or serving), MLflow re-executes that file, which rebuilds the
# MAGIC chain against the retriever/LLM endpoints configured in `config.yaml`.
# MAGIC
# MAGIC `input_example` + `infer_signature`: ALWAYS log a signature. Without one,
# MAGIC Model Serving cannot validate incoming request shapes, and errors surface
# MAGIC as opaque 500s at inference time instead of clear schema-validation
# MAGIC errors at logging time.
# MAGIC
# MAGIC `code_paths=["../src"]`: bundles the whole `src/` package (not just
# MAGIC `rag_chain.py`) into the model artifact, since `rag_chain.py` imports
# MAGIC `retriever.py` and `utils.py`. Forgetting this is the #1 cause of
# MAGIC `ModuleNotFoundError` at serving time — the file logs fine locally
# MAGIC (because `src` is on your notebook's `sys.path`) but fails in the
# MAGIC isolated serving container that only has what was explicitly packaged.

# COMMAND ----------

input_example = {"question": "How do I enable Change Data Feed on a Delta table?"}
output_example = (
    "Set the table property delta.enableChangeDataFeed = true using ALTER TABLE "
    "... SET TBLPROPERTIES. [1]"
)
signature = infer_signature(input_example, output_example)

with mlflow.start_run(run_name="rag_chain_v1") as run:
    logged_model = mlflow.langchain.log_model(
        lc_model=str(project_root / "src" / "rag_chain.py"),
        artifact_path="rag_chain",
        code_paths=[str(project_root / "src")],
        input_example=input_example,
        signature=signature,
        pip_requirements=str(project_root / "requirements.txt"),
        metadata={
            "vector_search_index": cfg.vs_index_name,
            "llm_endpoint": cfg.llm_endpoint,
        },
    )
    mlflow.log_params(
        {
            "chunk_size": cfg.raw["chunking"]["chunk_size"],
            "chunk_overlap": cfg.raw["chunking"]["chunk_overlap"],
            "num_results": cfg.num_results,
            "llm_temperature": cfg.raw["llm"]["temperature"],
            "embedding_model": cfg.embedding_model_endpoint,
        }
    )

log.info(f"Model logged at: {logged_model.model_uri}")
log.info(f"Run ID: {run.info.run_id}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2 — Sanity-check the logged artifact BEFORE registering
# MAGIC
# MAGIC Load it back with `mlflow.langchain.load_model` (or `pyfunc.load_model`,
# MAGIC which is what Model Serving actually uses) and run one prediction. This
# MAGIC catches packaging bugs (missing `code_paths`, wrong signature) in
# MAGIC seconds instead of after a failed registration or deployment.

# COMMAND ----------

sanity_model = mlflow.pyfunc.load_model(logged_model.model_uri)
sanity_answer = sanity_model.predict(input_example)
log.info(f"Sanity check answer: {sanity_answer}")
assert isinstance(sanity_answer, str) and len(sanity_answer) > 0

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 3 — Register into the Unity Catalog Model Registry
# MAGIC
# MAGIC The 3-level name (`catalog.schema.model_name`) is why we set
# MAGIC `registry_uri="databricks-uc"` above — it puts the model under the SAME
# MAGIC governance boundary as the tables and index it depends on, instead of a
# MAGIC separate flat workspace-registry namespace.

# COMMAND ----------

registered = mlflow.register_model(
    model_uri=logged_model.model_uri,
    name=cfg.registered_model_name,
)
log.info(f"Registered {cfg.registered_model_name} as version {registered.version}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 4 — Version it: tag + document this version
# MAGIC
# MAGIC Tags make `mlflow.MlflowClient().search_model_versions(...)` filterable
# MAGIC later ("show me every version trained on the August docs snapshot").

# COMMAND ----------

from mlflow import MlflowClient

client = MlflowClient()
client.update_model_version(
    name=cfg.registered_model_name,
    version=registered.version,
    description=(
        "RAG chain over Databricks documentation. "
        f"Embedding: {cfg.embedding_model_endpoint}. LLM: {cfg.llm_endpoint}. "
        f"chunk_size={cfg.raw['chunking']['chunk_size']}."
    ),
)
client.set_model_version_tag(
    name=cfg.registered_model_name,
    version=registered.version,
    key="source_run_id",
    value=run.info.run_id,
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 5 — Load it back by version (proves the registry round-trips correctly)

# COMMAND ----------

reloaded = mlflow.pyfunc.load_model(
    f"models:/{cfg.registered_model_name}/{registered.version}"
)
log.info(reloaded.predict(input_example))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Checklist before moving to `05_evaluate_model`
# MAGIC - [ ] `mlflow.pyfunc.load_model(logged_model.model_uri)` succeeded with
# MAGIC       NO import errors (if it fails, check `code_paths`)
# MAGIC - [ ] Registration succeeded and printed a version number
# MAGIC - [ ] Loading `models:/<name>/<version>` (the registry URI, not the run
# MAGIC       URI) also succeeds — this is the path Model Serving will use
# MAGIC - [ ] Note the `registered.version` number, you'll need it for
# MAGIC       evaluation and deployment
