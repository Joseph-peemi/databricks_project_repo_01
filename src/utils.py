"""
src/utils.py
=============
Shared helpers used by every notebook and module in this project:
  - config loading (single source of truth -> config/config.yaml)
  - Unity Catalog bootstrap (catalog/schema/volume creation)
  - a thin retry wrapper for calling Databricks-served endpoints
  - a logger factory

Why this file exists:
Without it, every notebook re-implements "read config.yaml", "create schema
if not exists", etc. Small duplication like that is how lab projects rot into
seven slightly-different copies of the same 10 lines. Centralizing it here
means a config change (e.g. switching catalogs) takes effect everywhere.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from tenacity import retry, stop_after_attempt, wait_exponential

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "config.yaml"


def get_logger(name: str) -> logging.Logger:
    """Standard logger: consistent format across notebooks and modules."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


log = get_logger(__name__)


@dataclass
class Config:
    """Typed accessor over config.yaml so callers get IDE autocomplete
    instead of chasing dict[str, Any] KeyErrors at 2am."""

    raw: dict[str, Any]

    # --- Unity Catalog -----------------------------------------------------
    @property
    def catalog(self) -> str:
        return self.raw["unity_catalog"]["catalog"]

    @property
    def schema(self) -> str:
        return self.raw["unity_catalog"]["schema"]

    @property
    def volume(self) -> str:
        return self.raw["unity_catalog"]["volume"]

    @property
    def volume_path(self) -> str:
        return f"/Volumes/{self.catalog}/{self.schema}/{self.volume}"

    # --- Tables --------------------------------------------------------------
    @property
    def raw_docs_table(self) -> str:
        return f"{self.catalog}.{self.schema}.{self.raw['tables']['raw_docs_table']}"

    @property
    def chunked_docs_table(self) -> str:
        return f"{self.catalog}.{self.schema}.{self.raw['tables']['chunked_docs_table']}"

    # --- Vector Search ---------------------------------------------------------
    @property
    def vs_endpoint_name(self) -> str:
        return self.raw["vector_search"]["endpoint_name"]

    @property
    def vs_index_name(self) -> str:
        return f"{self.catalog}.{self.schema}.{self.raw['vector_search']['index_name']}"

    @property
    def embedding_model_endpoint(self) -> str:
        return self.raw["vector_search"]["embedding_model_endpoint"]

    # --- LLM / chain -------------------------------------------------------------
    @property
    def llm_endpoint(self) -> str:
        return self.raw["llm"]["serving_endpoint"]

    @property
    def num_results(self) -> int:
        return self.raw["rag_chain"]["num_results"]

    @property
    def system_prompt(self) -> str:
        return self.raw["rag_chain"]["system_prompt"]

    # --- MLflow / Registry ----------------------------------------------------
    @property
    def experiment_path(self) -> str:
        return self.raw["mlflow"]["experiment_path"]

    @property
    def registered_model_name(self) -> str:
        return self.raw["mlflow"]["registered_model_name"]

    @property
    def model_alias(self) -> str:
        return self.raw["mlflow"]["model_alias"]

    # --- Serving -----------------------------------------------------------------
    @property
    def serving_endpoint_name(self) -> str:
        return self.raw["serving"]["endpoint_name"]


def load_config(config_path: str | Path = _CONFIG_PATH) -> Config:
    """Load config/config.yaml into a typed Config object.

    Environment variables of the form RAG_<SECTION>__<KEY> override values,
    e.g. RAG_UNITY_CATALOG__CATALOG=dev overrides unity_catalog.catalog.
    This is what lets the same code run in a CI job against a `dev` catalog
    without editing the YAML file.
    """
    with open(config_path, "r") as f:
        raw = yaml.safe_load(f)

    for env_key, env_val in os.environ.items():
        if not env_key.startswith("RAG_"):
            continue
        _, path = env_key.split("RAG_", 1)
        section, key = path.lower().split("__")
        if section in raw and key in raw[section]:
            log.info(f"Overriding {section}.{key} from environment variable {env_key}")
            raw[section][key] = env_val

    return Config(raw=raw)


def ensure_uc_objects(spark, cfg: Config) -> None:
    """Idempotently create the catalog/schema/volume used by this lab.

    Best practice: never assume a catalog/schema exists in a fresh workspace.
    `CREATE ... IF NOT EXISTS` makes the notebook safely re-runnable, which
    matters a lot when students/engineers re-run cells out of order.
    """
    spark.sql(f"CREATE CATALOG IF NOT EXISTS {cfg.catalog}")
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {cfg.catalog}.{cfg.schema}")
    spark.sql(
        f"CREATE VOLUME IF NOT EXISTS {cfg.catalog}.{cfg.schema}.{cfg.volume}"
    )
    log.info(f"UC objects ready: {cfg.catalog}.{cfg.schema} (volume: {cfg.volume})")


@retry(wait=wait_exponential(multiplier=1, min=1, max=10), stop=stop_after_attempt(4))
def call_with_retry(fn, *args, **kwargs):
    """Wrap any endpoint call (embedding, LLM, vector search) with exponential
    backoff. Foundation Model API endpoints and serverless Vector Search can
    return transient 429/503s under burst load -- retrying instead of
    failing the whole notebook is the difference between a flaky demo and a
    production-grade pipeline."""
    return fn(*args, **kwargs)
