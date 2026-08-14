# Runbook — Incidents Encountered Getting the Pipeline Green

This is a chronological record of every real failure hit while getting the
`dev` environment's Databricks Job (`00_environment_setup` through
`06_deploy_model`) to run end to end, with root cause and fix for each. It
exists because most of these are non-obvious platform/library quirks that
cost real time to diagnose — the goal is that nobody (including future us)
has to re-discover them.

For general usage docs, see [`README.md`](README.md). For infrastructure
setup and CI/CD, see [`terraform/README.md`](terraform/README.md).

Each entry follows the same shape: **Symptom → Root cause → Fix**, plus the
commit that applied it where relevant.

---

## Infrastructure / cluster

### 1. Job cluster hung forever on any real Spark stage
**Symptom:** notebook 01's Delta table write (`write_bronze_table`) — and
earlier, a `spark.read.format("binaryFile")` call — would hang indefinitely.
Live inspection via `spark.sparkContext.statusTracker()` showed a stage with
`numTasks > 0` but `numActiveTasks=0` forever, and `clusters/get` showed
`executors: []`.

**Root cause:** the job cluster is configured `num_workers=0` as a
cost-saving "single-node" setup, but `num_workers=0` alone does **not** make
Databricks run the driver as its own executor — it just means zero
executors exist. Any real Spark stage has nowhere to schedule tasks.

**Fix:** add `spark_conf` (`spark.databricks.cluster.profile=singleNode`,
`spark.master=local[*]`) and `custom_tags` (`ResourceClass=SingleNode`) to
the job cluster, conditional on `num_workers == 0`.
— `terraform/modules/rag_lab/main.tf` (commit `428f6c8`)

---

### 2. `northeurope` region had no viable VM SKU for the subscription
**Symptom:** every workspace/cluster provisioning attempt failed on quota or
SKU availability.

**Root cause:** exhaustive cross-referencing of `az vm list-skus` /
`az vm list-usage` against Databricks' `/api/2.0/clusters/list-node-types`
showed no usable SKU combination in `northeurope` for this subscription.

**Fix:** migrate to `eastus` / `Standard_E4ads_v7`.

---

### 3. Orphaned VM silently consumed the entire Azure vCPU quota
**Symptom:** a fresh job run failed immediately with
`AZURE_QUOTA_EXCEEDED_EXCEPTION` (`Current Limit: 4, Current Usage: 4`), even
though the Databricks Clusters API showed nothing running.

**Root cause:** an orphaned `Standard_E4ads_v7` VM in the workspace's managed
resource group, invisible to the Databricks Clusters API but still consuming
Azure subscription quota. Separately, running multiple diagnostic clusters
concurrently (via the Command Execution API) competed with the real job's
cluster for the same 4-vCPU cap, producing the exact same "hang" signature
as incident #1 — worth ruling out before concluding it's a code bug.

**Fix:** identify and delete the orphaned VM (`az vm list -d`); avoid
running concurrent diagnostic clusters against a tightly-quota'd
subscription while debugging.

---

## `config.yaml` values not resolving per-environment

Several of these share one root pattern: `config.yaml` stores one literal
value (`main`, or an unsuffixed name), but Terraform provisions
environment-suffixed resources (`dev`, `<name>_dev`, etc.). Without an
explicit `RAG_<SECTION>__<KEY>` environment variable override, the code
falls back to the literal and silently points at the wrong (or
nonexistent) resource.

### 4. `cfg.catalog` resolved to `"main"` instead of `"dev"`
**Symptom:** `CREATE SCHEMA`/volume calls failed with
`"Metastore storage root URL does not exist"`.

**Root cause:** `config.yaml`'s `unity_catalog.catalog: main` was never
overridden — no `RAG_UNITY_CATALOG__CATALOG` env var was wired into the job
cluster.

**Fix:** add `spark_env_vars.RAG_UNITY_CATALOG__CATALOG = var.catalog_name`
to the job cluster. — `terraform/modules/rag_lab/main.tf` (commit `5bd99e5`)

### 5. Same pattern for Vector Search endpoint, registered model, serving endpoint
**Symptom:** notebook 02 tried to *create* a new Vector Search endpoint
(`rag_lab_vs_endpoint`, no suffix) instead of reusing the Terraform-managed
one (`rag_lab_vs_endpoint_dev`), and hit
`QUOTA_EXCEEDED: Maximum number of AI Search endpoints per workspace
exceeded quota of 1`.

**Root cause:** same missing-override pattern for
`vector_search.endpoint_name`, `mlflow.registered_model_name`, and
`serving.endpoint_name`.

**Fix:** add `RAG_VECTOR_SEARCH__ENDPOINT_NAME`,
`RAG_MLFLOW__REGISTERED_MODEL_NAME`, `RAG_SERVING__ENDPOINT_NAME` to the
same `spark_env_vars` block, each composed from the module's already
env-suffixed locals. — `terraform/modules/rag_lab/main.tf` (commit `428f6c8`)

### 6. `load_config()` crashed on non-conforming `RAG_`-prefixed env vars
**Symptom:** `ValueError: not enough values to unpack (expected 2, got 1)`
inside `load_config()`, only inside the **Model Serving** container (never
in a job cluster).

**Root cause:** `load_config()`'s override loop assumed every environment
variable starting with `RAG_` follows the exact `RAG_<SECTION>__<KEY>`
shape and unconditionally did `section, key = path.split("__")`. The Agent
Framework serving container injects its own platform env var(s) starting
with `RAG_` that don't match that shape.

**Fix:** skip any `RAG_`-prefixed var that doesn't split into exactly two
parts, instead of assuming every one is ours.
— `src/utils.py` (commit `b6e344c`)

### 7. Model Serving container fell back to `"main"` catalog despite `cfg` being correct
**Symptom:** `Unity Catalog entity main.rag_lab.databricks_docs_index does
not exist`, thrown from inside the serving container at model-load time —
even though the job cluster that logged the model had `cfg.catalog == "dev"`.

**Root cause:** the Model Serving container is a **separate compute plane**
from the job cluster. It does not inherit `spark_env_vars` set on the job
cluster — `agents.deploy()` was being called with `environment_vars={}`.

**Fix:** pass the already-resolved `RAG_*` values through
`agents.deploy(environment_vars={...})` explicitly.
— `src/deployment.py` (commit `9b35320`)

---

## `CREATE CATALOG IF NOT EXISTS` eager validation

### 8. `CREATE CATALOG IF NOT EXISTS` failed even though the catalog already existed
**Symptom:** `INVALID_STATE: Metastore storage root URL does not exist`,
persisting even after incident #4 was fixed and verified.

**Root cause:** `CREATE CATALOG IF NOT EXISTS` validates the **metastore's
default storage root** eagerly, regardless of whether the catalog already
exists — and this metastore has no default storage root configured (by
design; `cfg.catalog` has its own explicit Terraform-managed storage root).
The `IF NOT EXISTS` guard doesn't prevent this validation from running.

**Fix:** check `SHOW CATALOGS` first and skip the `CREATE CATALOG` entirely
if it already exists, instead of relying on `IF NOT EXISTS`.
— `src/utils.py::ensure_uc_objects` (commit `8b394ab`)

---

## Ingestion (notebook 01)

### 9. Stale Databricks documentation URLs
**Symptom:** two of the ten seed URLs 404'd.

**Root cause:** `docs.databricks.com/en/unity-catalog/*` paths were
restructured; the correct paths are under
`/en/data-governance/unity-catalog/*` and
`/en/connect/unity-catalog/cloud-storage/*`. (Note: an external diagnosis
tool wrongly blamed `delta-live-tables/index.html` for this — verified live
via direct `curl`/`urllib` testing from the actual cluster's network path
that URL works fine.)

**Fix:** correct the two URLs; wrap the download loop in try/except so one
stale URL doesn't abort the whole ingestion run.
— `notebooks/01_data_ingestion_and_chunking.py` (commit `e8b86d2`)

### 10. `spark.read.format("binaryFile")` hung indefinitely
**Symptom:** reading ~10 small files via the `binaryFile` datasource hung
for 1h23m+, reproducible even with `.limit(1)`.

**Root cause:** never fully isolated from incident #1/#3's quota/executor
issues (the symptom is identical: a stage stuck with 0 active tasks) — but
regardless of root cause, this is unnecessary complexity for a driver-side,
lab-scale corpus.

**Fix:** read via plain `os.listdir()`/`open()` (UC Volumes are also
exposed as a normal POSIX path) instead of the distributed reader.
— `src/ingestion.py::load_documents_from_volume` (commit `a047931`)

### 11. `ModuleNotFoundError` in every notebook after 00
**Symptom:** `No module named 'markdownify'` (and similar) in notebooks
01–07.

**Root cause:** `%pip install` is notebook-attachment-scoped and does not
propagate across separate job tasks, even when they share one job cluster.

**Fix:** add the `%pip install -r ../requirements.txt` +
`dbutils.library.restartPython()` cell to every notebook, not just 00.
— commit `5bd99e5`

---

## Dependency pinning

### 12. `unstructured==0.16.11` no longer exists on PyPI
**Symptom:** `pip install -r requirements.txt` failed for every notebook
(including `00_environment_setup`) with
`No matching distribution found for unstructured==0.16.11`.

**Root cause:** versions `0.16.11`–`0.16.18` were pulled from PyPI at some
point after this pin was set (available versions jump from `0.11.8` to
`0.16.19`) — external, not caused by any change here.

**Fix:** bump to the nearest available release, `0.16.19`.
— `requirements.txt` (commit `df509bc`)

### 13. `databricks-vectorsearch==0.50` missing the `reranker` module
**Symptom:** `ModuleNotFoundError: No module named
'databricks.vector_search.reranker'`, raised from `databricks_ai_bridge`
(pulled in transitively by `databricks-langchain`), which imports it
unconditionally.

**Root cause:** the `reranker` submodule was added in `databricks-vectorsearch`
0.57; 0.50 predates it.

**Fix:** bump to `0.66` — verified to be the newest release that still has
`reranker` while keeping `mlflow-skinny<4,>=2.11.3` (0.67+ requires
`mlflow-skinny>=3.10.1`, which conflicts with the pinned `mlflow==2.20.1`).
Confirmed via binary-searching PyPI release metadata directly (`pip
download --no-deps` + inspecting `METADATA`/wheel contents for each
candidate version).
— `requirements.txt` (commits `540eba9`, `df509bc`)

---

## MLflow "Models from Code" internals

### 14. `mlflow.models.set_model()` never ran
**Symptom:** `MlflowException: If the model is logged as code, ensure the
model is set using mlflow.models.set_model() within the code file`.

**Root cause:** `src/rag_chain.py` guarded the `set_model()` call with
`if __name__ == "__main__":`, based on the (outdated) assumption that MLflow
loads the file via `runpy`. As of `mlflow==2.20.1`,
`mlflow.langchain.log_model(lc_model=<path>)` actually loads it via
`importlib.util.spec_from_file_location(f"code_model_{uuid4().hex}", path)`
— so `__name__` is a random `code_model_<uuid>` string, never `"__main__"`.
Confirmed by reading MLflow's own source (`mlflow/models/utils.py::_load_model_code_path`).

**Fix:** guard on `__name__ == "__main__" or
__name__.startswith("code_model_")` instead, preserving the original intent
(plain `import src.rag_chain` stays side-effect-free).
— `src/rag_chain.py` (commit `dd4d2f1`)

### 15. `mlflow.set_experiment()` failed with an opaque error
**Symptom:** `RestException: BAD_REQUEST: For input string: "None"`.

**Root cause:** several frames deep, this is
`mlflow.create_experiment()` failing because the experiment path's parent
workspace folder (`/Shared/rag_lab`) doesn't exist — confirmed directly by
calling `POST /api/2.0/mlflow/experiments/create` and getting
`NOT_FOUND: Parent directory does not exist: /Shared/rag_lab`. Unlike
saving a notebook into a new path, the MLflow experiments API does not
auto-create parent directories, and the client-side error message doesn't
say so.

**Fix:** `src/utils.py::ensure_mlflow_experiment` — `mkdirs` the parent
folder via `WorkspaceClient().workspace.mkdirs(...)` before calling
`mlflow.set_experiment()`. — commit `d5f0459`

### 16. `pyfunc.predict()` returned a list, not a bare string
**Symptom:** `assert isinstance(sanity_answer, str)` failed in notebook 04's
sanity check.

**Root cause:** the signature was inferred from a single dict/str example,
so `pyfunc` enforces it as a one-row batch internally — the LangChain
flavor's `predict()` then returns a one-element list.

**Fix:** unwrap `if isinstance(result, list): result = result[0]` before
using the prediction. — `notebooks/04_register_model.py` (commit `de85856`)

### 17. `index.wait_until_ready(timeout=1800)` — `TypeError`
**Symptom:** `'<' not supported between instances of 'datetime.timedelta'
and 'int'`.

**Root cause:** this `databricks-vectorsearch` version compares elapsed
time (a `timedelta`) against `timeout` directly; passing a raw int (seconds)
breaks the comparison.

**Fix:** pass `datetime.timedelta(seconds=1800)`.
— `notebooks/02_create_vector_search_index.py`

### 18. `DatabricksVectorSearch(text_column=...)` — `ValueError`
**Symptom:** `The index '...' has the source column configured as
'chunk_text'. Do not pass the text_column parameter.`

**Root cause:** this `databricks-langchain` version auto-detects
`text_column` from the index's own `embedding_source_column` config and
raises if it's also passed explicitly — even when the value matches.

**Fix:** drop the `text_column` kwarg entirely.
— `src/retriever.py` (commit `a128fd7`)

---

## Agent Framework schema requirements (notebooks 03/04)

### 19. `agents.deploy()` refused the model's schema
**Symptom:** `ValueError: The model's schema is not compatible with Agent
Framework. The input schema must be either ChatCompletionRequest or
SplitChatMessagesRequest. Input schema: ['question': string (required)]`

**Root cause:** the chain's public contract was a bespoke
`{"question": str} -> str` shape. The Agent Framework validates the
registered model's schema against exactly `ChatCompletionRequest` /
`SplitChatMessagesRequest` on input and a matching shape on output, and
refuses to deploy anything else.

**Fix:** reshape `build_chain()` to `{"query": str, "history": [...]}
-> {"content": str}`, matching `mlflow.models.rag_signatures.
SplitChatMessagesRequest` / `StringResponse` exactly. Build the logged
signature directly from those reference dataclasses (via
`convert_dataclass_to_schema`) rather than `infer_signature`, since an
empty `history=[]` example can't reliably infer its element type.
Propagated through `notebooks/03`'s manual `chain.invoke()` calls,
`src/evaluation.py::measure_latency`, and
`src/deployment.py::query_endpoint`.
— commit `54f7f14`

### 20. Notebooks 05/06 silently evaluated/deployed a stale model version
**Symptom:** no error the first time (every prior successful run happened
to be registering version 1 for the first time) — but a real bug: both
notebooks hard-coded `MODEL_VERSION = "1"`.

**Root cause:** once more than one version exists, the next run's notebook
04 registers version 2+, but 05/06 would keep referencing version 1
without any error — silently testing/deploying the wrong artifact.

**Fix:** notebook 04 publishes the version it just registered via
`dbutils.jobs.taskValues.set()`; 05/06 read it back via
`dbutils.jobs.taskValues.get(taskKey="04_register_model", ...)`, falling
back to `"1"` when run standalone outside the job's task DAG.
— commit `762ef2c`

---

## Model Serving deployment (notebook 06 / `agents.deploy()`)

Getting `06_deploy_model` green took the most iterations — each one
surfaced only after the previous was fixed, since the container has to
build and the model has to actually load before the next failure appears.
Each fix was confirmed by pulling the real error from the live endpoint
rather than guessing:
```
GET /api/2.0/serving-endpoints/{name}/events
GET /api/2.0/serving-endpoints/{name}/served-models/{served_name}/logs?config_version=N
```

### 21. `agents.deploy(workload_size=...)` — `AttributeError`
**Symptom:** `AttributeError: 'str' object has no attribute 'value'`.

**Root cause:** `agents.deploy()` internally does
`workload_size.value` — it expects a `ServedModelInputWorkloadSize` enum
member, not the plain `"Small"`/`"Medium"`/`"Large"` string `config.yaml`
stores.

**Fix:** `ServedModelInputWorkloadSize[cfg.raw["serving"]["workload_size"].upper()]`.
— `src/deployment.py` (commit `792f53b`)

### 22. `agents.deploy()` created an endpoint under the wrong name
**Symptom:** `ResourceDoesNotExist: Endpoint with name
'databricks_docs_rag_endpoint_dev' does not exist` when notebook 06's
Step 3 tried to wait on it.

**Root cause:** `agents.deploy()` was called without `endpoint_name=`, so
it auto-generated its own name from `model_name` instead of using
`cfg.serving_endpoint_name`.

**Fix:** pass `endpoint_name=cfg.serving_endpoint_name` explicitly.
— `src/deployment.py` (commit `408e6f6`)

### 23. Legacy inference tables rejected by the backend
**Symptom:** `InvalidParameterValue: Legacy inference tables have been
deprecated. The 'auto_capture_config' field can only be used with
'enabled=false'. Please use AI Gateway inference tables instead.`

**Root cause:** `agents.deploy()` decides whether to use the new AI Gateway
inference-table path based on
`databricks.agents.utils.mlflow_utils._check_model_is_agent()`, which
checks `model_info.metadata.get("task")` against the regex
`agent/v\d+/chat`. The model was logged via a generic
`mlflow.langchain.log_model()` call with no `"task"` key, so the SDK always
took the legacy path, which the backend now rejects outright.

**Fix:** add `"task": "agent/v1/chat"` to the `metadata` dict passed to
`log_model()`. — `notebooks/04_register_model.py` (commit `30ffcea`)

### 24. Model Serving container build rejected the packaged environment
**Symptom:** `Container image creation aborted - unsupported model:
Environment file conda.yaml does not specify mlflow as a dependency.`

**Root cause:** MLflow's own `conda.yaml` generator **merges** multiple
`mlflow[...]==X.Y.Z` lines (with different extras) into a single combined
entry. `requirements.txt` had `mlflow[databricks]==2.20.1` and
`mlflow[genai]==2.20.1` on separate lines; these got merged into
`mlflow[databricks,genai]==2.20.1`. Model Serving's container-build
validation specifically requires an **unbracketed** `mlflow` entry and
doesn't recognize the merged one. Adding a third, bare `mlflow==2.20.1`
line to the same file didn't help — it got silently absorbed into the same
merge. Confirmed by pulling the actual packaged `conda.yaml` off a failed
model version via `mlflow.artifacts.download_artifacts()` (run through a
throwaway diagnostic job, since the direct DBFS/artifact REST APIs are
blocked in this UC-enabled workspace).

**Fix:** log the served model against a separate, minimal
`requirements-serving.txt` (no extras anywhere, only what
`src/rag_chain.py`/`src/retriever.py`/`src/utils.py` actually import at
module level) instead of the full dev `requirements.txt`.
— `requirements-serving.txt`, `notebooks/04_register_model.py` (commits
`5dcac73`, `84999e9`)

### 25. Model load failed: missing `config.yaml` inside the container
**Symptom:** `FileNotFoundError: [Errno 2] No such file or directory:
'/model/code/config/config.yaml'`.

**Root cause:** `src/utils.py::load_config()` resolves `config.yaml`
relative to its own file location
(`Path(__file__).resolve().parent.parent / "config" / "config.yaml"`).
`code_paths=[".../src"]` only bundles `src/` into the served container —
`config/` was never bundled, so that relative path resolves to nothing
inside `/model/code/`.

**Fix:** `code_paths=[".../src", ".../config"]`.
— `notebooks/04_register_model.py` (commit `3841790`)

### 26. Model load failed: missing Databricks resource credentials
**Symptom:** `MlflowException: Reading Databricks credential
configuration in model serving failed. Most commonly, this happens because
the model currently being served was logged without Databricks resource
dependencies properly specified.` (`the MLflow tracking URI was set to
'None'`)

**Root cause:** the Agent Framework requires the served model to declare
which Databricks-hosted resources it calls at inference time (the vector
search index, the LLM serving endpoint), via `resources=[...]` on
`log_model()`, so it can auto-provision scoped credentials for the serving
container. Without it, the container has no way to authenticate to those
resources at all.

**Fix:** add
`resources=[DatabricksVectorSearchIndex(index_name=cfg.vs_index_name),
DatabricksServingEndpoint(endpoint_name=cfg.llm_endpoint)]` to
`log_model()`. Confirmed working: the endpoint events subsequently showed
`System service principal creation ... succeeded`.
— `notebooks/04_register_model.py` (commit `227456c`)

### 27. Model load failed: wrong catalog inside the serving container
**Symptom:** `Unity Catalog entity main.rag_lab.databricks_docs_index does
not exist.` — see incident #7 above; documented here for chronological
completeness since it was the last of the model-*load* failures.

### 28. Inference table name reconstructed from the wrong config
**Symptom:** would have queried a table that doesn't exist
(`main.rag_lab.rag_endpoint_logs_payload`).

**Root cause:** notebook 06 Step 4 reconstructed the inference table name
from `cfg.raw["serving"]["inference_table_*"]` — but `agents.deploy()`
provisions its own AI Gateway inference table and picks
catalog/schema/table-name-prefix **itself**; it never reads those
`config.yaml` keys (those apply only to the unused `deploy_with_sdk()`
legacy path). Confirmed via the live endpoint's
`ai_gateway.inference_table_config`: real location was
`dev.rag_lab.databricks_docs_rag_model_1_payload`, matching nothing in
`config.yaml`.

**Fix:** read the real location back from
`w.serving_endpoints.get(name).ai_gateway.inference_table_config` instead
of reconstructing a name from static config.
— `notebooks/06_deploy_model.py` (commit `3b452a3`)

### 29. Inference-table query assumed a column that didn't exist yet
**Symptom:** `[UNRESOLVED_COLUMN.WITH_SUGGESTION] A column, variable, or
function parameter with name 'timestamp_ms' cannot be resolved. Did you
mean one of the following? [databricks_request_id].`

**Root cause:** the AI Gateway inference table is created with only a
single `databricks_request_id` column; it evolves to include
`request`/`response`/`timestamp_ms`/etc. only once the **first row
actually lands**, which took longer than the original fixed 30-second
sleep.

**Fix:** poll for rows (up to ~2 minutes) instead of a single fixed sleep,
and only sort by `timestamp_ms` if that column actually exists in the
current schema yet.
— `notebooks/06_deploy_model.py` (commit `fc55587`)

### 30. `query_endpoint()`'s request/response shape was never actually tested
**Symptom:** none yet observed directly (caught proactively during a
final audit pass, before it could fail on the next run) — but
`mlflow.deployments`' `DatabricksDeploymentClient.predict(inputs=X)` sends
`X` **as-is** as the `/invocations` body; it does not wrap it. The
original code sent `{"inputs": [{"query": ..., "history": []}]}` —
double-wrapped and almost certainly wrong for a `SplitChatMessagesRequest`
-schema endpoint.

**Fix:** send the schema shape directly
(`{"query": question, "history": []}`), and defensively unwrap the
response (bare dict, or wrapped under `"predictions"` as either a dict or
a one-element list) instead of assuming one exact wire shape. Confirmed
correct against the live endpoint after deployment succeeded: response
came back as a bare `{"content": ..., "id": ..., "databricks_output":
{...}}` dict, no `"predictions"` wrapper.
— `src/deployment.py` (commit `3b452a3`)

### 31. Chain crashed on real-world traffic: Review App requests use a different wire format than direct API calls
**Symptom:** the Review App returned
`InternalError: {"error_code":"BAD_REQUEST","message":"Encountered an
unexpected error while converting model response to JSON. Error 'Invalid
Agent output. Outputs must be a JSON dictionary, but got <class
'NoneType'>.'"}` — even though the exact same question worked fine via a
direct REST call with `{"query": ..., "history": []}`.

**Root cause:** the `task: agent/v1/chat` metadata (incident #23) makes the
endpoint accept **either** our declared `SplitChatMessagesRequest` shape
**or** a standard ChatCompletion `{"messages": [...]}` request — which is
what the Review App (and most chat UIs) actually send. For the latter, the
serving framework converts the request into a bare **list of LangChain
`BaseMessage` objects** before invoking the chain, not our dict shape.
`inputs["query"]` then raised
`TypeError: list indices must be integers or slices, not str` deep inside
the chain, which got swallowed somewhere upstream instead of propagating
as a clear error — surfacing to the caller as an opaque `NoneType` output
error. Confirmed by reproducing directly: `{"query": "...", "history":
[]}` via REST worked; `{"messages": [{"role": "user", "content": "..."}]}`
(the Review App's actual format) reproduced the exact crash, and the
serving logs showed the real traceback including the offending request
payload (`[HumanMessage(content='WHAT IS DATABRICKS', ...)]`).

**Fix:** `_extract_query()` in `src/rag_chain.py` handles both shapes —
`inputs[-1].content` if `inputs` is a list (ChatCompletion path),
`inputs["query"]` otherwise. **Lesson:** a schema declared at logging time
is not the only shape a served Agent Framework endpoint will actually
receive at runtime — test via the actual UI/client a real user will use,
not just a direct API call matching the declared signature.
— `src/rag_chain.py` (commit `2d2488d`)

---

## General lessons

- **A "hang" and a "quota exhaustion" can look identical** (a Spark stage
  stuck at 0 active tasks) — rule out concurrent resource contention before
  concluding a specific data source or code path is broken.
- **The Model Serving compute plane does not inherit job-cluster
  configuration.** Any environment variable, `spark_conf`, or `spark_env_vars`
  set via Terraform on the job cluster must be re-passed explicitly through
  `agents.deploy(environment_vars=...)` if the served model needs it too.
- **When an error message references internal MLflow/SDK mechanics you're
  not certain about, read the actual installed package source
  (`pip download --no-deps` + inspect) rather than guessing from
  documentation that may describe a different version's behavior.** Several
  of the fixes above (incidents #14, #21, #24) were only found this way.
- **Pull the real artifact/config off a failed resource before re-guessing
  a fix.** `mlflow.artifacts.download_artifacts()`, the serving endpoint's
  `/events` and `/served-models/.../logs` APIs, and Unity Catalog's table
  metadata API were all used to get ground truth instead of iterating
  blindly.
