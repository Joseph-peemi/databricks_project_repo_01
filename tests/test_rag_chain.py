"""
tests/test_rag_chain.py
========================
Tests the prompt-assembly stage of src/rag_chain.py in isolation, without
building the full chain (which would require a live retriever + LLM
endpoint). This is exactly the kind of stage-by-stage testability LCEL
composition is meant to give you -- see the module docstring in
src/rag_chain.py.
"""

from src.rag_chain import build_prompt

SYSTEM_PROMPT = (
    "You are a helpful assistant that answers questions about Databricks "
    "using ONLY the provided context."
)


def test_system_message_present():
    prompt = build_prompt(SYSTEM_PROMPT)
    compiled = prompt.invoke({"context": "some context", "question": "some question"})
    system_messages = [m for m in compiled.to_messages() if m.type == "system"]
    assert len(system_messages) == 1
    assert "ONLY the provided context" in system_messages[0].content


def test_context_and_question_are_both_included():
    prompt = build_prompt(SYSTEM_PROMPT)
    compiled = prompt.invoke(
        {"context": "[1] Source: https://x.com\nCDF info", "question": "How do I enable CDF?"}
    )
    human_messages = [m for m in compiled.to_messages() if m.type == "human"]
    assert len(human_messages) == 1
    assert "CDF info" in human_messages[0].content
    assert "How do I enable CDF?" in human_messages[0].content


def test_question_appears_after_context_in_the_human_message():
    # Regression guard for the "put the question last" design decision
    # documented in src/rag_chain.py::build_prompt -- reduces "lost in the
    # middle" drift on long context blocks.
    prompt = build_prompt(SYSTEM_PROMPT)
    compiled = prompt.invoke({"context": "CONTEXT_MARKER", "question": "QUESTION_MARKER"})
    content = compiled.to_messages()[-1].content
    assert content.index("CONTEXT_MARKER") < content.index("QUESTION_MARKER")
