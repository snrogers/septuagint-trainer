"""Database access.

The server reads exactly one database, data/lxx.db: the corpus. It is derived
from data/raw/, rebuildable at any time by scripts/build_db.py, and opened
read-only at runtime.

Everything a learner earns -- reading position, flashcard scheduling, review
history -- lives in that learner's own browser (web/progress.js). The server
holds no user state, so any number of people can use one instance without
seeing or disturbing each other's progress.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DB = ROOT / "data" / "lxx.db"


def connect() -> sqlite3.Connection:
    """Open the corpus read-only."""
    if not CORPUS_DB.exists():
        raise FileNotFoundError(
            f"Corpus database missing at {CORPUS_DB}.\n"
            "Run: python3 scripts/fetch_data.py && python3 scripts/build_db.py"
        )
    con = sqlite3.connect(f"{CORPUS_DB.as_uri()}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    return con
