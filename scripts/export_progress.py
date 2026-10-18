"""Turn a legacy data/progress.db into a file the browser can import.

Progress used to live on the server, in data/progress.db. It now lives in each
learner's browser. This reads the old database, plus the corpus for each
card's frequency and rank, and prints the JSON that Progress -> Import in the
app accepts:

    python3 scripts/export_progress.py data/progress.db > progress.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lxx.db import CORPUS_DB  # noqa: E402

# Must match FORMAT and FORMAT_VERSION in web/progress.js.
FORMAT = "septuagint-progress"
FORMAT_VERSION = 1
DEFAULT_DAILY_NEW_LIMIT = 12


def _open_ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def export(progress_db: Path, corpus_db: Path) -> dict:
    con = _open_ro(progress_db)
    con.execute("ATTACH DATABASE ? AS corpus", (f"{corpus_db.resolve().as_uri()}?mode=ro",))
    try:
        position = con.execute("SELECT position FROM progress WHERE id = 1").fetchone()
        limit = con.execute(
            "SELECT value FROM settings WHERE key = 'daily_new_limit'"
        ).fetchone()
        cards = con.execute(
            """
            SELECT c.lemma_id, c.state, c.step, c.ease, c.interval_days, c.due,
                   c.reps, c.lapses, c.origin, c.added_at, c.last_review,
                   l.freq, l.rank
            FROM cards c LEFT JOIN corpus.lemmas l ON l.id = c.lemma_id
            ORDER BY c.lemma_id
            """
        ).fetchall()
        reviews = con.execute(
            "SELECT lemma_id, reviewed_at, grade, interval_days, ease"
            " FROM reviews ORDER BY id"
        ).fetchall()
    finally:
        con.close()

    return {
        "format": FORMAT,
        "version": FORMAT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "position": position["position"] if position else 1,
        "settings": {
            "daily_new_limit": int(limit["value"]) if limit else DEFAULT_DAILY_NEW_LIMIT,
        },
        "cards": [dict(row) for row in cards],
        "reviews": [dict(row) for row in reviews],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("progress_db", type=Path, help="legacy data/progress.db")
    parser.add_argument("--corpus", type=Path, default=CORPUS_DB,
                        help=f"corpus database (default {CORPUS_DB})")
    args = parser.parse_args()
    for path in (args.progress_db, args.corpus):
        if not path.is_file():
            parser.error(f"{path} does not exist")
    json.dump(export(args.progress_db, args.corpus), sys.stdout, ensure_ascii=False, indent=1)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
