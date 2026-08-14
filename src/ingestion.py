"""
src/ingestion.py
=================
Stage 1 of the RAG pipeline: turn raw Databricks documentation pages into a
clean, chunked Delta table that is ready to be embedded and indexed.

Pipeline shape (Medallion architecture):
    Source files (HTML/Markdown) --[load_documents]--> Bronze Delta table
                                  --[chunk_documents]--> Silver Delta table

Why chunk at all?
Embedding models and LLM context windows are finite, and retrieval quality
degrades badly if you embed whole 5,000-word pages: the vector becomes a
diffuse "average" of many topics, so a specific question ("how do I enable
Change Data Feed?") no longer scores highest against the one paragraph that
actually answers it. Chunking trades some cross-chunk context for much
higher retrieval precision.

Common mistakes this module tries to prevent:
  1. Chunking on a fixed character count with NO overlap -> answers that
     straddle a chunk boundary become unretrievable. We default to 150 chars
     of overlap.
  2. Losing provenance (url/title) during chunking -> the LLM can no longer
     cite sources and you can't debug bad retrievals. Every chunk keeps its
     parent metadata.
  3. Indexing near-empty chunks (nav bars, "Was this page helpful?" footers)
     -> pollutes the index with noise. We filter chunks below `min_chunk_size`.
  4. Forgetting to enable Change Data Feed on the Silver table -> Vector
     Search delta-sync indexes require it to do incremental sync.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.utils import Config, get_logger

log = get_logger(__name__)


@dataclass
class RawDocument:
    """One row of the Bronze table: a full source page."""

    url: str
    title: str
    content: str  # cleaned, plain-text / markdown content of the page


def clean_html_to_text(html: str) -> str:
    """Strip navigation, scripts, and markup noise from a raw HTML page,
    leaving readable markdown-ish text.

    Best practice: never feed raw HTML to a chunker/embedder. Boilerplate
    (nav menus, cookie banners) burns embedding "attention" on non-content
    and shows up as near-duplicate chunks across many pages.
    """
    from bs4 import BeautifulSoup
    from markdownify import markdownify

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        tag.decompose()

    main = soup.find("main") or soup.find("article") or soup.body or soup
    markdown = markdownify(str(main), heading_style="ATX")

    lines = [ln.rstrip() for ln in markdown.splitlines()]
    collapsed: list[str] = []
    blank_run = 0
    for ln in lines:
        if ln.strip() == "":
            blank_run += 1
            if blank_run > 1:
                continue
        else:
            blank_run = 0
        collapsed.append(ln)
    return "\n".join(collapsed).strip()


def load_documents_from_volume(spark, cfg: Config):
    """Read every .html/.md file landed in the UC Volume and return a Spark
    DataFrame matching the Bronze schema (url, title, content, ingested_at).

    Reads via plain filesystem I/O (UC Volumes are also exposed as a normal
    POSIX-style path) rather than `spark.read.format("binaryFile")`: the
    latter was observed to hang indefinitely against this volume -- a single
    Spark job/stage stuck for 1h+ reading ~10 small files, reproducible even
    with `.limit(1)`, while plain `os.listdir`/`open()` on the same path
    completed in under a second. For a driver-side, lab-scale corpus like
    this (tens to low hundreds of pages), skipping the distributed reader
    entirely is simpler and doesn't depend on it working.
    """
    import os

    import pyspark.sql.functions as F
    from pyspark.sql import Row
    from pyspark.sql.types import StringType, StructField, StructType

    schema = StructType(
        [
            StructField("url", StringType(), False),
            StructField("title", StringType(), False),
            StructField("content", StringType(), False),
        ]
    )

    def _row_to_doc(filename: str, content_bytes: bytes) -> tuple[str, str, str]:
        raw = content_bytes.decode("utf-8", errors="ignore")
        if filename.endswith((".html", ".htm")):
            text = clean_html_to_text(raw)
        else:
            text = raw  # already markdown/plain text
        title = text.splitlines()[0].lstrip("# ").strip() if text else filename
        # Reconstruct a docs.databricks.com-style URL from the filename so
        # citations in the final answer are clickable.
        slug = filename.rsplit(".", 1)[0]
        url = f"https://docs.databricks.com/{slug.replace('__', '/')}"
        return url, title, text

    filenames = [
        f for f in os.listdir(cfg.volume_path) if f.endswith((".html", ".htm", ".md"))
    ]
    parsed_rows = []
    for filename in filenames:
        with open(f"{cfg.volume_path}/{filename}", "rb") as f:
            content_bytes = f.read()
        u, t, c = _row_to_doc(filename, content_bytes)
        if c and len(c.strip()) > 0:
            parsed_rows.append(Row(url=u, title=t, content=c))

    bronze_df = spark.createDataFrame(parsed_rows, schema=schema).withColumn(
        "ingested_at", F.current_timestamp()
    )
    log.info(f"Loaded {bronze_df.count()} raw documents from {cfg.volume_path}")
    return bronze_df


def write_bronze_table(bronze_df, cfg: Config) -> None:
    """Persist the Bronze DataFrame as a managed Delta table (idempotent
    overwrite -- ingestion is a batch job, not an append-only stream)."""
    bronze_df.write.format("delta").mode("overwrite").option(
        "overwriteSchema", "true"
    ).saveAsTable(cfg.raw_docs_table)
    log.info(f"Wrote Bronze table: {cfg.raw_docs_table}")


def make_chunk_id(url: str, chunk_index: int) -> str:
    """Deterministic primary key: same page + same position -> same id.
    Deterministic IDs let Delta-Sync Vector Search perform proper
    upserts/deletes on re-ingestion instead of duplicating every chunk."""
    digest = hashlib.sha256(f"{url}::{chunk_index}".encode()).hexdigest()[:16]
    return f"{digest}"


def chunk_documents(spark, cfg: Config):
    """Split every Bronze row into overlapping text chunks and return a
    Spark DataFrame matching the Silver (chunked) schema:
        chunk_id, url, title, chunk_index, chunk_text

    Uses LangChain's RecursiveCharacterTextSplitter, which tries to split on
    paragraph breaks first, then sentences, then words -- only falling back
    to a hard character cut as a last resort. This keeps chunks
    semantically coherent instead of severing mid-sentence.
    """
    import pandas as pd
    from pyspark.sql.types import IntegerType, StringType, StructField, StructType

    chunk_cfg = cfg.raw["chunking"]
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_cfg["chunk_size"],
        chunk_overlap=chunk_cfg["chunk_overlap"],
        separators=["\n## ", "\n### ", "\n\n", "\n", ". ", " ", ""],
    )
    min_chunk_size = chunk_cfg["min_chunk_size"]

    bronze_pdf: pd.DataFrame = spark.table(cfg.raw_docs_table).toPandas()

    records = []
    for _, row in bronze_pdf.iterrows():
        pieces = splitter.split_text(row["content"])
        for idx, piece in enumerate(pieces):
            piece = piece.strip()
            if len(piece) < min_chunk_size:
                continue  # drop nav-fragment / near-empty chunks
            records.append(
                {
                    "chunk_id": make_chunk_id(row["url"], idx),
                    "url": row["url"],
                    "title": row["title"],
                    "chunk_index": idx,
                    "chunk_text": piece,
                }
            )

    schema = StructType(
        [
            StructField("chunk_id", StringType(), False),
            StructField("url", StringType(), False),
            StructField("title", StringType(), False),
            StructField("chunk_index", IntegerType(), False),
            StructField("chunk_text", StringType(), False),
        ]
    )
    chunked_df = spark.createDataFrame(pd.DataFrame(records), schema=schema)
    log.info(
        f"Chunked {bronze_pdf.shape[0]} documents into {chunked_df.count()} chunks "
        f"(chunk_size={chunk_cfg['chunk_size']}, overlap={chunk_cfg['chunk_overlap']})"
    )
    return chunked_df


def write_silver_table(chunked_df, cfg: Config) -> None:
    """Persist the chunked DataFrame and turn on Change Data Feed.

    Change Data Feed (CDF) is NOT optional: Databricks Vector Search's
    Delta-Sync index reads the table's change log to incrementally add,
    update, or delete vectors without a full re-embed on every run. Without
    CDF, index creation will fail with a clear error -- so we enable it here
    at write time rather than discovering the failure two steps later.
    """
    (
        chunked_df.write.format("delta")
        .mode("overwrite")
        .option("overwriteSchema", "true")
        .option("delta.enableChangeDataFeed", "true")
        .saveAsTable(cfg.chunked_docs_table)
    )
    log.info(
        f"Wrote Silver table: {cfg.chunked_docs_table} "
        "(Change Data Feed enabled for Vector Search sync)"
    )


def run_ingestion_pipeline(spark, cfg: Config) -> None:
    """End-to-end entrypoint used by notebooks/01_data_ingestion_and_chunking.py."""
    bronze_df = load_documents_from_volume(spark, cfg)
    write_bronze_table(bronze_df, cfg)

    chunked_df = chunk_documents(spark, cfg)
    write_silver_table(chunked_df, cfg)
