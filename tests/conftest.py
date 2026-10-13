"""Pytest bootstrap: make the repo root importable.

Lets ``python -m pytest tests/`` work from the repo root so tests can import
``mock_upstream.app`` and the ``runner`` package without packaging/install.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
