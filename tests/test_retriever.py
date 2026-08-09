"""
tests/test_retriever.py
========================
Unit tests for src/retriever.py::format_retrieved_context -- the function
that turns retrieved LangChain Documents into the numbered, citable text
block the prompt template consumes. Getting this wrong silently breaks
citation quality without raising any exception, so it's worth pinning down
with tests.
"""

from langchain_core.documents import Document

from src.retriever import format_retrieved_context


def test_numbers_chunks_starting_at_one():
    docs = [
        Document(page_content="First chunk", metadata={"url": "https://a.com"}),
        Document(page_content="Second chunk", metadata={"url": "https://b.com"}),
    ]
    formatted = format_retrieved_context(docs)
    assert formatted.startswith("[1] Source: https://a.com")
    assert "[2] Source: https://b.com" in formatted


def test_includes_full_chunk_text():
    docs = [Document(page_content="Set delta.enableChangeDataFeed = true", metadata={"url": "https://a.com"})]
    formatted = format_retrieved_context(docs)
    assert "Set delta.enableChangeDataFeed = true" in formatted


def test_handles_missing_url_metadata_gracefully():
    docs = [Document(page_content="No URL here", metadata={})]
    formatted = format_retrieved_context(docs)
    assert "unknown source" in formatted


def test_empty_document_list_returns_empty_string():
    assert format_retrieved_context([]) == ""
