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

# MAGIC %pip install -r ../requirements.txt
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import sys
from pathlib import Path

project_root = Path.cwd().parent
if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

import mlflow
from mlflow.models import ModelSignature

from src.utils import load_config, get_logger, ensure_mlflow_experiment  # noqa: E402

log = get_logger("04_register_model")
cfg = load_config()

mlflow.set_registry_uri("databricks-uc")  # register into Unity Catalog, not the workspace registry
ensure_mlflow_experiment(cfg)

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

# Signature built from mlflow's own reference dataclasses (SplitChatMessagesRequest
# -> StringResponse) rather than inferred from an example: notebook 06 deploys via
# the Agent Framework (databricks.agents.deploy), which validates the registered
# model's schema against exactly these two shapes and refuses to deploy anything
# else. infer_signature on an example with an empty history=[] list can't reliably
# infer that field's element type, so we use the exact reference shapes instead.
from mlflow.models.rag_signatures import SplitChatMessagesRequest, StringResponse
from mlflow.types.schema import convert_dataclass_to_schema

input_example = {"query": "How do I enable Change Data Feed on a Delta table?", "history": []}
signature = ModelSignature(
    inputs=convert_dataclass_to_schema(SplitChatMessagesRequest()),
    outputs=convert_dataclass_to_schema(StringResponse()),
)

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
            # Required for agents.deploy() (notebook 06) to recognize this as
            # an Agent Framework model and use AI Gateway inference tables --
            # without it, databricks.agents.utils.mlflow_utils._check_model_is_agent()
            # returns False and the SDK falls back to the legacy
            # auto_capture_config path, which the backend now rejects outright
            # ("Legacy inference tables have been deprecated").
            "task": "agent/v1/chat",
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
# The schema-enforced pyfunc layer treats a single input as a one-row batch,
# so this comes back as a one-element list of {"content": ...} dicts
# (StringResponse shape) rather than a bare string -- unwrap both layers.
if isinstance(sanity_answer, list):
    sanity_answer = sanity_answer[0]
sanity_answer = sanity_answer["content"]
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

# Hand the version this run just registered to notebooks 05/06 via Databricks
# Jobs task values -- every prior run happened to be the FIRST registration
# (version 1), which masked that those notebooks otherwise hard-code
# MODEL_VERSION = "1" and would silently evaluate/deploy a stale earlier
# version once more than one version exists.
dbutils.jobs.taskValues.set(key="registered_model_version", value=registered.version)  # noqa: F821

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
reloaded_answer = reloaded.predict(input_example)
if isinstance(reloaded_answer, list):
    reloaded_answer = reloaded_answer[0]
log.info(reloaded_answer["content"])

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
