# Databricks notebook source
# MAGIC %md
# MAGIC # 01 — Data Ingestion & Chunking
# MAGIC
# MAGIC **Goal:** turn Databricks documentation pages into a chunked Delta table
# MAGIC ready to be embedded and indexed (lab task: prerequisite for
# MAGIC "Create an AI Search Index").
# MAGIC
# MAGIC **Why this is its own notebook (not folded into index creation):**
# MAGIC ingestion is I/O-bound and rarely changes once stable, while indexing
# MAGIC parameters (chunk size, embedding model) get iterated on frequently
# MAGIC during tuning. Separating them means you can re-chunk without
# MAGIC re-downloading, and re-index without re-chunking.

# COMMAND ----------

import sys
from pathlib import Path

project_root = Path.cwd().parent
if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

from src.utils import load_config, get_logger  # noqa: E402
from src import ingestion  # noqa: E402

log = get_logger("01_ingestion")
cfg = load_config()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 1 — Land raw Databricks documentation into a UC Volume
# MAGIC
# MAGIC For this lab, download a representative subset of Databricks docs pages
# MAGIC (HTML) into the UC Volume. In a real production pipeline you would run
# MAGIC this as a scheduled job hitting a documentation export/sitemap, but for
# MAGIC the lab we keep it simple and reproducible: a fixed URL list.
# MAGIC
# MAGIC **Common mistake:** scraping documentation sites aggressively (no
# MAGIC rate-limiting) can get your IP blocked and violates most sites' terms of
# MAGIC use. Always rate-limit, set a descriptive User-Agent, and prefer an
# MAGIC official export/API/sitemap over ad-hoc crawling when one exists.

# COMMAND ----------

DOC_URLS = [
    "https://docs.databricks.com/en/delta/index.html",
    "https://docs.databricks.com/en/delta/delta-change-data-feed.html",
    "https://docs.databricks.com/en/delta-live-tables/index.html",
    "https://docs.databricks.com/en/data-governance/unity-catalog/index.html",
    "https://docs.databricks.com/en/connect/unity-catalog/cloud-storage/external-locations.html",
    "https://docs.databricks.com/en/generative-ai/vector-search.html",
    "https://docs.databricks.com/en/machine-learning/model-serving/index.html",
    "https://docs.databricks.com/en/mlflow/index.html",
    "https://docs.databricks.com/en/delta/history.html",
    "https://docs.databricks.com/en/jobs/index.html",
    # Add more pages here as needed. For a real lab submission, aim for
    # 100-500 pages to give the retriever a meaningfully sized corpus.
]

# COMMAND ----------

import time
import urllib.error
import urllib.request

dbutils.fs.mkdirs(cfg.volume_path)  # noqa: F821 (dbutils is injected by Databricks)

for url in DOC_URLS:
    slug = url.split("docs.databricks.com/")[-1].replace("/", "__").removesuffix(".html") + ".html"
    dest = f"{cfg.volume_path}/{slug}"
    req = urllib.request.Request(url, headers={"User-Agent": "databricks-rag-lab/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            html_bytes = resp.read()
    except urllib.error.HTTPError as e:
        # Databricks docs restructure pages over time; don't let one stale
        # URL abort the whole ingestion run.
        log.warning(f"Skipping {url}: {e}")
        continue
    dbutils.fs.put(dest, html_bytes.decode("utf-8", errors="ignore"), overwrite=True)  # noqa: F821
    log.info(f"Downloaded {url} -> {dest}")
    time.sleep(1)  # polite rate limit

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2 — Parse, clean, and load into the Bronze Delta table
# MAGIC
# MAGIC `ingestion.load_documents_from_volume` strips HTML boilerplate (nav,
# MAGIC scripts, footers) and keeps only the main article content. See
# MAGIC `src/ingestion.py::clean_html_to_text` for why this matters: unfiltered
# MAGIC HTML pollutes chunks with repeated navigation text that shows up as
# MAGIC false-positive matches in retrieval.

# COMMAND ----------

bronze_df = ingestion.load_documents_from_volume(spark, cfg)  # noqa: F821
display(bronze_df.limit(5))  # noqa: F821

# COMMAND ----------

ingestion.write_bronze_table(bronze_df, cfg)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 3 — Chunk documents into the Silver table
# MAGIC
# MAGIC **Chunking strategy chosen:** `RecursiveCharacterTextSplitter` with
# MAGIC `chunk_size=800`, `chunk_overlap=150` (see `config/config.yaml`).
# MAGIC
# MAGIC **Why 800/150 as a starting point:**
# MAGIC - 800 characters ≈ 150-200 English tokens — small enough that a chunk
# MAGIC   stays topically focused, large enough to contain a full explanation
# MAGIC   or code snippet.
# MAGIC - 150 character overlap (~19%) prevents an answer that straddles a
# MAGIC   paragraph break from being split so that neither chunk alone is
# MAGIC   sufficient context.
# MAGIC
# MAGIC These are STARTING values, not universal truths — see README section 12
# MAGIC "Chunking strategies" for how to tune them per corpus (docs with lots of
# MAGIC code samples often want larger chunks or markdown-header-aware splitting
# MAGIC so a code block is never cut in half).

# COMMAND ----------

chunked_df = ingestion.chunk_documents(spark, cfg)
display(chunked_df.limit(10))  # noqa: F821

# COMMAND ----------

ingestion.write_silver_table(chunked_df, cfg)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 4 — Sanity checks
# MAGIC Before moving to indexing, verify:
# MAGIC   1. Change Data Feed is actually enabled (Vector Search will fail loudly
# MAGIC      later if not, but it's cheaper to catch now).
# MAGIC   2. No duplicate `chunk_id`s (would silently overwrite rows in the index).
# MAGIC   3. Chunk length distribution looks sane (catches a broken splitter).

# COMMAND ----------

props = spark.sql(f"SHOW TBLPROPERTIES {cfg.chunked_docs_table}").toPandas()
cdf_enabled = props.loc[props["key"] == "delta.enableChangeDataFeed", "value"].iloc[0]
assert cdf_enabled == "true", "Change Data Feed is NOT enabled — Vector Search sync will fail."
log.info("Change Data Feed: enabled ✅")

dup_count = (
    spark.table(cfg.chunked_docs_table)
    .groupBy("chunk_id")
    .count()
    .filter("count > 1")
    .count()
)
assert dup_count == 0, f"Found {dup_count} duplicate chunk_ids — check make_chunk_id() collisions."
log.info("chunk_id uniqueness: OK ✅")

import pyspark.sql.functions as F  # noqa: E402

length_stats = spark.table(cfg.chunked_docs_table).select(
    F.length("chunk_text").alias("len")
).summary("min", "25%", "50%", "75%", "max")
display(length_stats)  # noqa: F821
