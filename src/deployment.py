"""
src/deployment.py
==================
Deploys a Unity Catalog-registered model version to a Databricks Model
Serving REST endpoint, and provisions the Review App for stakeholder
testing.

Two deployment paths are shown:

  A) `deploy_with_agents_framework()` -- the RECOMMENDED path for RAG/agent
     models. `databricks.agents.deploy()` provisions the serving endpoint
     AND the Review App AND the permissions/feedback plumbing between them
     in one call. This is what task 6 ("Deploy the model") + task 7
     ("Test using the Review App") map to in the Databricks Agent Framework.

  B) `deploy_with_sdk()` -- the lower-level path using the Databricks SDK's
     `WorkspaceClient`, shown for transparency / for models that are NOT
     agents (e.g. if you ever need to hand-roll endpoint config: workload
     size, scale-to-zero, inference tables).

Best practice: deploy by (registered_model_name, version) or an ALIAS
(e.g. "champion"), never by re-logging the model at deploy time. The
registry is the single source of truth for "what is running in
production" -- re-logging at deploy time breaks that guarantee.
"""

from __future__ import annotations

from src.utils import Config, call_with_retry, get_logger

log = get_logger(__name__)


def deploy_with_agents_framework(cfg: Config, model_version: str):
    """Preferred deployment path for this lab.

    `agents.deploy` does four things atomically:
      1. Creates/updates a Model Serving endpoint serving the given UC model
         version.
      2. Enables an inference table for request/response logging.
      3. Provisions a Review App backed by that endpoint.
      4. Grants CAN_QUERY to the reviewers you specify, without needing to
         hand-manage Model Serving ACLs.
    """
    from databricks import agents

    deployment = agents.deploy(
        model_name=cfg.registered_model_name,
        model_version=model_version,
        scale_to_zero=cfg.raw["serving"]["scale_to_zero_enabled"],
        workload_size=cfg.raw["serving"]["workload_size"],
        environment_vars={},  # inject secrets/env here, never hard-code them
    )
    log.info(f"Agent deployment created: endpoint={deployment.endpoint_name}")
    log.info(f"Review App URL: {deployment.review_app_url}")
    return deployment


def deploy_with_sdk(cfg: Config, model_version: str):
    """Lower-level equivalent using the Databricks SDK directly. Useful when
    you need endpoint config the high-level `agents.deploy` doesn't expose,
    or you're deploying a non-agent pyfunc model.
    """
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.serving import (
        EndpointCoreConfigInput,
        ServedEntityInput,
        AutoCaptureConfigInput,
    )

    w = WorkspaceClient()

    served_entity = ServedEntityInput(
        entity_name=cfg.registered_model_name,
        entity_version=model_version,
        workload_size=cfg.raw["serving"]["workload_size"],
        scale_to_zero_enabled=cfg.raw["serving"]["scale_to_zero_enabled"],
    )

    auto_capture = AutoCaptureConfigInput(
        catalog_name=cfg.raw["serving"]["inference_table_catalog"],
        schema_name=cfg.raw["serving"]["inference_table_schema"],
        table_name_prefix=cfg.raw["serving"]["inference_table_prefix"],
        enabled=True,
    )

    endpoint_name = cfg.serving_endpoint_name
    existing = [e.name for e in w.serving_endpoints.list()]

    if endpoint_name in existing:
        log.info(f"Endpoint {endpoint_name} exists -> updating served entity")
        w.serving_endpoints.update_config(
            name=endpoint_name,
            served_entities=[served_entity],
        )
    else:
        log.info(f"Creating new serving endpoint: {endpoint_name}")
        w.serving_endpoints.create(
            name=endpoint_name,
            config=EndpointCoreConfigInput(
                served_entities=[served_entity],
                auto_capture_config=auto_capture,
            ),
        )

    w.serving_endpoints.wait_get_serving_endpoint_not_updating(endpoint_name)
    log.info(f"Endpoint {endpoint_name} is READY")
    return endpoint_name


def query_endpoint(cfg: Config, question: str) -> str:
    """Call the deployed endpoint exactly the way a downstream application
    (or the Review App) would -- over the standard Model Serving REST API,
    authenticated with the caller's Databricks credentials (never a
    hard-coded token).
    """
    from mlflow.deployments import get_deploy_client

    client = get_deploy_client("databricks")
    response = call_with_retry(
        client.predict,
        endpoint=cfg.serving_endpoint_name,
        inputs={"inputs": [{"question": question}]},
    )
    return response["predictions"][0]
