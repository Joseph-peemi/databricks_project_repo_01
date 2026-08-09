"""
tests/test_ingestion.py
========================
Unit tests for the pure-Python parts of src/ingestion.py: HTML cleaning and
deterministic chunk-id generation. Spark-dependent functions
(load_documents_from_volume, chunk_documents, write_*_table) are exercised
in the Databricks notebook itself / an integration suite, not here.
"""

from src.ingestion import clean_html_to_text, make_chunk_id


class TestCleanHtmlToText:
    def test_strips_nav_header_footer_and_scripts(self, sample_html):
        text = clean_html_to_text(sample_html)
        assert "trackPageView" not in text
        assert "Home | Docs | Pricing" not in text
        assert "Was this page helpful?" not in text

    def test_keeps_main_content(self, sample_html):
        text = clean_html_to_text(sample_html)
        assert "Change Data Feed" in text
        assert "delta.enableChangeDataFeed" in text

    def test_collapses_excess_blank_lines(self, sample_html):
        text = clean_html_to_text(sample_html)
        assert "\n\n\n" not in text


class TestMakeChunkId:
    def test_deterministic_for_same_input(self):
        id1 = make_chunk_id("https://docs.databricks.com/delta/index", 0)
        id2 = make_chunk_id("https://docs.databricks.com/delta/index", 0)
        assert id1 == id2

    def test_differs_by_chunk_index(self):
        id1 = make_chunk_id("https://docs.databricks.com/delta/index", 0)
        id2 = make_chunk_id("https://docs.databricks.com/delta/index", 1)
        assert id1 != id2

    def test_differs_by_url(self):
        id1 = make_chunk_id("https://docs.databricks.com/delta/index", 0)
        id2 = make_chunk_id("https://docs.databricks.com/jobs/index", 0)
        assert id1 != id2

    def test_stable_length(self):
        # Vector Search primary keys should be short, fixed-width strings.
        assert len(make_chunk_id("https://x.com/a", 5)) == 16
