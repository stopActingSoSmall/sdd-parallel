"""Standalone CLI wrapper for invoking sdd-parallel from agent skills."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow direct execution via `uv run python -X utf8 scripts/parallel_flow.py`
_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from sdd_parallel.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
