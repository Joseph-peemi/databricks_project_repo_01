# Databricks notebook source
# MAGIC %md
# MAGIC # 07 — Test the Model Using the Databricks Review App
# MAGIC
# MAGIC **Lab task 7: "Test the model using the Review App."**
# MAGIC
# MAGIC **What the Review App is:** a pre-built, no-code chat UI that
# MAGIC `agents.deploy()` (notebook 06) automatically provisioned and wired to
# MAGIC your serving endpoint. It exists so that PEOPLE WHO CANNOT WRITE CODE
# MAGIC (product managers, subject-matter experts, QA) can exercise the model
# MAGIC and leave structured feedback (👍/👎 + free-text) without needing REST
# MAGIC API access or notebook permissions.
# MAGIC
# MAGIC **Why this step matters architecturally:** it closes the loop between
# MAGIC "offline evaluation" (notebook 05, against a fixed golden set) and
# MAGIC "real-world usage" (actual questions people ask, in whatever phrasing
# MAGIC they use). Feedback captured here becomes new eval examples, which is
# MAGIC how the golden set grows from 5 curated questions into hundreds of
# MAGIC realistic ones over time.

# COMMAND ----------

# MAGIC %pip install -r ../requirements.txt
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import sys
from pathlib import Path

project_root = Path.cwd().parent
if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

from databricks.sdk import WorkspaceClient
from databricks import agents

from src.utils import load_config, get_logger  # noqa: E402

log = get_logger("07_review_app")
cfg = load_config()
w = WorkspaceClient()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 1 — Retrieve the Review App URL and grant reviewer access
# MAGIC
# MAGIC `agents.set_permissions` grants `CAN_QUERY` to specific users/groups
# MAGIC without exposing broader workspace permissions — reviewers can use the
# MAGIC app but cannot see the underlying notebooks, tables, or endpoint config.
# MAGIC This is the principle of least privilege applied to human testers.

# COMMAND ----------

deployment_info = agents.get_deployments(model_name=cfg.registered_model_name)[0]
review_app_url = deployment_info.review_app_url
log.info(f"Review App: {review_app_url}")

REVIEWER_EMAILS = [
    # Add the emails of stakeholders/SMEs who should test this model, e.g.:
    # "peemijoe9522@gmail.com",
]

if REVIEWER_EMAILS:
    agents.set_permissions(
        model_name=cfg.registered_model_name,
        users=REVIEWER_EMAILS,
        permission_level=agents.PermissionLevel.CAN_QUERY,
    )
    log.info(f"Granted CAN_QUERY to: {REVIEWER_EMAILS}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2 — Manual test script (run through this IN the Review App UI)
# MAGIC
# MAGIC Open `review_app_url` in a browser and manually verify each row below.
# MAGIC Automating "does the UI look right" isn't the point here — the point is
# MAGIC exercising the deployed system exactly as a real user would, including
# MAGIC latency perception and answer formatting.
# MAGIC
# MAGIC | # | Prompt to test | What to check | Expected outcome |
# MAGIC |---|---|---|---|
# MAGIC | 1 | "How do I enable Change Data Feed on a Delta table?" | Correctness + citation present | Correct SQL syntax, cites the CDF doc URL |
# MAGIC | 2 | "What's the difference between a Job and a DLT pipeline?" | Handles a comparison question | Both concepts explained, not just one |
# MAGIC | 3 | "What is the capital of France?" | Refusal behavior (out-of-scope) | Politely declines, does NOT hallucinate an answer |
# MAGIC | 4 | A follow-up question with no context ("what about for Iceberg tables?") | Multi-turn robustness | Chain has no memory (single-turn design) — verify it degrades gracefully rather than erroring |
# MAGIC | 5 | A deliberately vague/ambiguous question | Judgment under ambiguity | Answer should ask for clarification or cover the most likely interpretation, not confidently guess |
# MAGIC
# MAGIC For each, click 👍/👎 in the Review App UI and leave a free-text comment
# MAGIC explaining WHY — "wrong" alone isn't actionable; "cited the right doc but
# MAGIC got the SQL syntax wrong" is.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 3 — Troubleshooting common Review App failures
# MAGIC
# MAGIC | Symptom | Cause | Fix |
# MAGIC |---|---|---|
# MAGIC | Review App shows "endpoint not ready" | Serving endpoint still `UPDATING` | Wait for `wait_get_serving_endpoint_not_updating` from notebook 06 to fully return before sharing the URL |
# MAGIC | Reviewer sees "permission denied" | Not granted `CAN_QUERY` | Re-run `agents.set_permissions` with their email; workspace SSO email must match exactly |
# MAGIC | Every answer is the refusal message | Retriever returning zero/irrelevant results in the SERVED environment even though it worked in notebook 03 | Check `code_paths` was set at logging time (notebook 04) -- a missing import silently falls back or errors inside the served container; check endpoint logs in the Serving UI |
# MAGIC | Answers are slow (>5s) on first message, fast after | Scale-to-zero cold start | Expected behavior; disable scale-to-zero for demo/review sessions if latency matters, per README "Cost optimization" tradeoffs |
# MAGIC | Feedback button doesn't seem to save anything | Reviewer not authenticated / third-party cookies blocked | Have reviewer log into the workspace directly first, then open the Review App link in the same browser session |
# MAGIC | Answers cite sources that don't exist / broken URLs | `format_retrieved_context` metadata mismatch, or corpus URLs were malformed during ingestion (notebook 01) | Spot check `src/ingestion.py::load_documents_from_volume`'s URL reconstruction against a few real chunks |
# MAGIC
# MAGIC ## Step 4 — Pull feedback back into the evaluation loop
# MAGIC
# MAGIC Review App feedback is persisted to a Delta table Databricks manages
# MAGIC automatically. Periodically export flagged (👎) examples into
# MAGIC `src/evaluation.py::SEED_EVAL_EXAMPLES` (or the `eval_table`) so real
# MAGIC failure modes get regression-tested on every future evaluation run,
# MAGIC closing the loop: **Review App -> golden set -> evaluation gate ->
# MAGIC redeploy.**

# COMMAND ----------

# MAGIC %md
# MAGIC ## Lab complete ✅
# MAGIC You have now built, registered, evaluated, deployed, and reviewer-tested
# MAGIC a full RAG pipeline over Databricks documentation. See `README.md`
# MAGIC section 12 ("Best Practices") before promoting this beyond a lab
# MAGIC environment.
