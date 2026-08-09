"""
tests/conftest.py
==================
Why these tests run WITHOUT a Databricks cluster:
Anything that needs `spark`, a live Vector Search index, or a Foundation
Model API endpoint is an integration concern, not a unit-test concern.
These tests exercise the pure-Python logic (chunking rules, id generation,
prompt formatting, metric math) that's cheap and fast to verify on every
commit -- in CI, before you ever spend cluster time or endpoint calls.

Best practice: keep this split explicit. Unit tests here run in <1s locally
or in GitHub Actions; integration tests (hitting real Databricks resources)
belong in a separate suite run against a dev workspace, not in the default
`pytest` invocation.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def sample_html() -> str:
    return """
    <html>
      <head><script>trackPageView();</script></head>
      <body>
        <nav>Home | Docs | Pricing</nav>
        <header>Databricks Documentation</header>
        <main>
          <h1>Change Data Feed</h1>
          <p>Change Data Feed (CDF) allows Delta tables to track row-level
          changes between versions of a table.</p>
          <p>Enable it with ALTER TABLE ... SET TBLPROPERTIES
          (delta.enableChangeDataFeed = true).</p>
        </main>
        <footer>Was this page helpful?</footer>
      </body>
    </html>
    """
