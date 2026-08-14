# Databricks notebook source
# MAGIC %md
# MAGIC # 06 — Deploy the Model
# MAGIC
# MAGIC **Lab task 6: "Deploy the model."**
# MAGIC
# MAGIC **Databricks feature:** Mosaic AI Model Serving (via the Agent Framework's
# MAGIC `agents.deploy`), which provisions:
# MAGIC   - an autoscaling REST endpoint serving the registered model version
# MAGIC   - an **inference table** logging every request/response for monitoring
# MAGIC   - a **Review App** (used in notebook 07) wired to the same endpoint
# MAGIC
# MAGIC **Precondition:** only deploy a version that passed the quality gates in
# MAGIC notebook 05. We set the `champion` alias here as the explicit,
# MAGIC auditable "this is the version approved for production" marker.

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
from mlflow import MlflowClient

from src.utils import load_config, get_logger  # noqa: E402
from src import deployment  # noqa: E402

log = get_logger("06_deploy")
cfg = load_config()
mlflow.set_registry_uri("databricks-uc")
client = MlflowClient()

# Pull the version notebook 04 just registered in this same job run (falls
# back to "1" when run standalone/interactively, outside the job's task DAG).
# Must match the version that PASSED evaluation in notebook 05.
MODEL_VERSION = dbutils.jobs.taskValues.get(  # noqa: F821
    taskKey="04_register_model",
    key="registered_model_version",
    default="1",
    debugValue="1",
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 1 — Promote the evaluated version with an alias
# MAGIC
# MAGIC Aliases decouple "which version is deployed" from "which version number
# MAGIC that happens to be" — downstream code always references `@champion`,
# MAGIC so promoting a new version later is a one-line alias move, not a
# MAGIC redeploy-everything event.

# COMMAND ----------

client.set_registered_model_alias(
    name=cfg.registered_model_name,
    alias=cfg.model_alias,
    version=MODEL_VERSION,
)
log.info(f"Alias '{cfg.model_alias}' -> version {MODEL_VERSION}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2 — Deploy via the Agent Framework
# MAGIC
# MAGIC `workload_size` controls the underlying compute (Small/Medium/Large —
# MAGIC start Small, scale up based on observed p90 latency/throughput).
# MAGIC `scale_to_zero=True` saves cost on low/spiky traffic (a lab or internal
# MAGIC tool) at the cost of cold-start latency on the first request after
# MAGIC idling — turn it OFF for latency-sensitive production traffic.
# MAGIC
# MAGIC **Common mistake:** deploying by re-pointing at `models:/name/1` instead
# MAGIC of the alias — six months later nobody remembers why prod is pinned to
# MAGIC version 1 instead of the (better) version 4 that passed eval since.
# MAGIC Always deploy the ALIAS's current version, not a hard-coded number.

# COMMAND ----------

champion_version = client.get_model_version_by_alias(
    cfg.registered_model_name, cfg.model_alias
).version

deployment_info = deployment.deploy_with_agents_framework(cfg, model_version=champion_version)
log.info(f"Serving endpoint: {deployment_info.endpoint_name}")
log.info(f"Review App URL:   {deployment_info.review_app_url}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 3 — Wait for the endpoint to become READY, then smoke-test it
# MAGIC
# MAGIC Always test the ACTUAL deployed REST endpoint before calling deployment
# MAGIC done — logging/registering success does not guarantee the served
# MAGIC container starts cleanly (missing env vars, dependency resolution
# MAGIC differences between notebook and serving container images are the most
# MAGIC common causes of a "registered fine, fails to serve" gap).

# COMMAND ----------

from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
w.serving_endpoints.wait_get_serving_endpoint_not_updating(cfg.serving_endpoint_name)
log.info(f"Endpoint '{cfg.serving_endpoint_name}' is READY ✅")

# COMMAND ----------

answer = deployment.query_endpoint(cfg, "How do I enable Change Data Feed on a Delta table?")
print(answer)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 4 — Confirm inference-table logging is capturing traffic
# MAGIC
# MAGIC The inference table is what monitoring/drift-detection (README section
# MAGIC 12 "Monitoring") reads from later — verify it's actually populated now,
# MAGIC not during an incident.

# COMMAND ----------

import time

# agents.deploy() provisions its own AI Gateway inference table and picks the
# catalog/schema/table-name-prefix itself -- it does NOT read
# cfg.raw["serving"]["inference_table_*"] (those config.yaml keys apply only
# to the deploy_with_sdk() legacy path, which this project doesn't use).
# Read the real location back from the live endpoint config instead of
# reconstructing a name that would silently point at the wrong catalog.
endpoint_config = w.serving_endpoints.get(cfg.serving_endpoint_name)
itc = endpoint_config.ai_gateway.inference_table_config
inference_table = f"{itc.catalog_name}.{itc.schema_name}.{itc.table_name_prefix}_payload"

# The table is created with a minimal schema (just databricks_request_id)
# and evolves to include request/response/timestamp columns only once the
# first row actually lands -- which can take longer than a single fixed
# sleep, and the exact column name to sort by isn't known until it does.
# Poll instead of assuming both the timing and the schema.
inference_df = None
for attempt in range(6):
    time.sleep(20)
    df = spark.table(inference_table)  # noqa: F821
    if df.count() > 0:
        inference_df = df
        break
    log.info(f"No rows in {inference_table} yet (attempt {attempt + 1}/6) -- waiting...")

if inference_df is not None:
    order_col = "timestamp_ms" if "timestamp_ms" in inference_df.columns else None
    result = inference_df.orderBy(order_col, ascending=False) if order_col else inference_df
    display(result.limit(5))  # noqa: F821
else:
    log.warning(
        f"No rows landed in {inference_table} yet after ~2 minutes -- "
        "inference-table logging may just need more time; re-query this "
        "table later to verify it's capturing traffic."
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ## Checklist before moving to `07_review_app_testing`
# MAGIC - [ ] `champion` alias points at the version that PASSED evaluation
# MAGIC - [ ] Serving endpoint status is READY (not `UPDATING`/`FAILED`)
# MAGIC - [ ] A direct REST call via `deployment.query_endpoint` returned a
# MAGIC       correct, grounded answer
# MAGIC - [ ] The inference table has rows -- logging is confirmed working
# MAGIC - [ ] You have the `review_app_url` printed above for notebook 07
