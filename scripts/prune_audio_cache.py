"""Delete cached clips that no current text can ever request again.

The cache is keyed by the exact spoken text, so changing the voice or any
text rule (polytonic conversion, single-vowel rewrites) strands the clips
made under the old rules forever. Nothing reads them; they only grow the
data/audio tree. Run this after such a change:

    python3 scripts/prune_audio_cache.py            # report only
    python3 scripts/prune_audio_cache.py --delete   # actually remove
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lxx.tts import CACHE_DIR, clip_path


def live_keys() -> set[Path]:
    """Every clip path the current corpus can produce."""
    con = sqlite3.connect(ROOT / "data" / "lxx.db")
    keys: set[Path] = set()
    for (lemma,) in con.execute("SELECT lemma FROM lemmas"):
        keys.add(clip_path(lemma)[0])
    for (text,) in con.execute("SELECT text FROM verses"):
        keys.add(clip_path(text)[0])
    return keys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delete", action="store_true",
                        help="remove stranded clips instead of listing them")
    args = parser.parse_args()

    if not CACHE_DIR.exists():
        print(f"{CACHE_DIR} does not exist; nothing to prune")
        return 0

    keys = live_keys()
    stranded = bytes = removed = 0
    for wav in sorted(CACHE_DIR.rglob("*.wav")):
        if wav in keys:
            continue
        stranded += 1
        bytes += wav.stat().st_size
        if args.delete:
            wav.unlink()
            removed += 1
        else:
            print(wav)

    action = "removed" if args.delete else "stranded"
    print(f"{action}: {stranded} clips, {bytes / 1024:.1f} KiB")
    if args.delete:
        print(f"deleted: {removed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
