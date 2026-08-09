"""
tests/test_evaluation.py
=========================
Unit tests for the retrieval-metric math in src/evaluation.py. These are
pure functions (no Spark/MLflow/network calls) so they're the cheapest,
highest-value tests in the suite -- a bug here silently corrupts every
evaluation report downstream.
"""

from src.evaluation import retrieval_precision_at_k, retrieval_recall_at_k


class TestPrecisionAtK:
    def test_perfect_precision(self):
        assert retrieval_precision_at_k(["a", "b"], ["a", "b"]) == 1.0

    def test_partial_precision(self):
        assert retrieval_precision_at_k(["a", "b", "c", "d"], ["a"]) == 0.25

    def test_zero_precision_no_overlap(self):
        assert retrieval_precision_at_k(["x", "y"], ["a", "b"]) == 0.0

    def test_empty_retrieved_returns_zero_not_error(self):
        assert retrieval_precision_at_k([], ["a"]) == 0.0


class TestRecallAtK:
    def test_perfect_recall(self):
        assert retrieval_recall_at_k(["a", "b", "c"], ["a", "b"]) == 1.0

    def test_partial_recall(self):
        assert retrieval_recall_at_k(["a"], ["a", "b"]) == 0.5

    def test_zero_recall_no_overlap(self):
        assert retrieval_recall_at_k(["x"], ["a", "b"]) == 0.0

    def test_no_expected_docs_is_trivially_full_recall(self):
        # Out-of-scope / refusal-case questions expect zero source docs --
        # recall should be defined as 1.0 (nothing was missed) rather than
        # dividing by zero or defaulting to 0.0, which would incorrectly
        # penalize correct refusal behavior.
        assert retrieval_recall_at_k([], []) == 1.0
        assert retrieval_recall_at_k(["a"], []) == 1.0
