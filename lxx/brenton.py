"""Brenton's English Septuagint, keyed to CATSS verses.

The Greek text stays the reading text. This module only attaches the
1851 English verse so the reader can check a whole-verse rendering
under the interlinear.

Brenton and CATSS do not share one book list or one verse scheme.
Most books match on chapter, verse, and lettered segment. A few
need a fixed remap: Sirach's prologue, Esther addition 1a, the
Epistle of Jeremiah's off-by-one, Proverbs' shifted chapters, Ode 12
as the Prayer of Manasses, and CATSS range headers such as Bel 15-17.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_VPL = ROOT / "data" / "raw" / "brenton" / "eng-Brenton_vpl.txt"

# CATSS book code -> eBible / USFM book code. Psalms of Solomon and
# the Odes (apart from Ode 12) have no Brenton text.
BRENTON_BOOK = {
    "1/3Kgs": "1KI",
    "1Chr":   "1CH",
    "1Esdr":  "1ES",
    "1Mac":   "1MA",
    "1Sam/K": "1SA",
    "2/4Kgs": "2KI",
    "2Chr":   "2CH",
    "2Esdr":  "EZR",
    "2Mac":   "2MA",
    "2Sam/K": "2SA",
    "3Mac":   "3MA",
    "4Mac":   "4MA",
    "Amos":   "AMO",
    "Bar":    "BAR",
    "Bel":    "BEL",
    "BelTh":  "BEL",
    "Cant":   "SOL",
    "Dan":    "DNG",
    "DanTh":  "DNG",
    "Deut":   "DEU",
    "EpJer":  "EPJ",
    "Esth":   "ESG",
    "Exod":   "EXO",
    "Ezek":   "EZE",
    "Gen":    "GEN",
    "Hab":    "HAB",
    "Hag":    "HAG",
    "Hos":    "HOS",
    "Isa":    "ISA",
    "Jer":    "JER",
    "Jdt":    "JDT",
    "Job":    "JOB",
    "Joel":   "JOE",
    "Jonah":  "JON",
    "JoshA":  "JOS",
    "JoshB":  "JOS",
    "JudgA":  "JDG",
    "JudgB":  "JDG",
    "Lam":    "LAM",
    "Lev":    "LEV",
    "Mal":    "MAL",
    "Mic":    "MIC",
    "Nah":    "NAH",
    "Num":    "NUM",
    "Obad":   "OBA",
    "Prov":   "PRO",
    "Ps":     "PSA",
    "Qoh":    "ECC",
    "Ruth":   "RUT",
    "Sir":    "SIR",
    "Sus":    "SUS",
    "SusTh":  "SUS",
    "TobBA":  "TOB",
    "TobS":   "TOB",
    "Wis":    "WIS",
    "Zech":   "ZEC",
    "Zeph":   "ZEP",
}

# CATSS Prov 30:1-14 sit in Brenton as Prov 24:22f-t. The edition
# skips 22j, so the letters are not a plain alphabet slice.
_PROV_30_SEGS = (
    "f", "g", "h", "i", "k", "l", "m",
    "n", "o", "p", "q", "r", "s", "t",
)

_VPL_RE = re.compile(
    r"^(?P<book>\S+)\s+(?P<chapter>\d+):(?P<verse>\d+)(?P<segment>[a-z]*)\s*(?P<text>.*)$"
)
_RANGE_RE = re.compile(r"^(\d+)[-/](\d+)$")
_SPACE_RE = re.compile(r"\s+")


def clean_verse_text(text: str) -> str:
    """Strip empty editorial slots and collapse spare whitespace."""
    return _SPACE_RE.sub(" ", (text or "").replace("[]", "")).strip()


def parse_vpl_line(line: str) -> tuple[str, int, int, str, str] | None:
    """Parse one BibleWorks VPL line into book, chapter, verse, segment, text."""
    line = (line or "").strip()
    if not line:
        return None
    match = _VPL_RE.match(line)
    if match is None:
        return None
    text = clean_verse_text(match.group("text"))
    if not text:
        return None
    return (
        match.group("book"),
        int(match.group("chapter")),
        int(match.group("verse")),
        match.group("segment") or "",
        text,
    )


def parse_vpl(path: Path):
    """Yield parsed rows from an eBible Brenton VPL file."""
    text = Path(path).read_text(encoding="utf-8-sig")
    for line in text.splitlines():
        row = parse_vpl_line(line)
        if row is not None:
            yield row


def load_brenton_index(source) -> dict[tuple[str, int, int, str], str]:
    """Build (book, chapter, verse, segment) -> English text.

    `source` is a file path or an iterable of VPL lines.
    """
    if isinstance(source, (str, Path)):
        rows = parse_vpl(source)
    else:
        rows = (parsed for line in source if (parsed := parse_vpl_line(line)))
    index: dict[tuple[str, int, int, str], str] = {}
    for book, chapter, verse, segment, text in rows:
        index[(book, chapter, verse, segment)] = text
    return index


def remap_reference(
    book_code: str, chapter: int, verse: int, segment: str
) -> tuple[str, int, int, str] | None:
    """Map one CATSS reference onto Brenton's book and verse numbers."""
    if book_code == "Od":
        if chapter != 12:
            return None
        return ("PRM", 1, verse, segment or "")
    if book_code == "EpJer":
        return ("EPJ", 1, verse + 1, segment or "")
    if book_code == "Esth" and chapter == 1 and verse == 1 and segment == "a":
        return ("ESG", 1, 1, "")
    if book_code == "Sir" and chapter == 1 and verse == 1 and not segment:
        return ("SIR", 1, 1, "h")
    if book_code == "Prov":
        return _remap_prov(chapter, verse, segment or "")
    dest = BRENTON_BOOK.get(book_code)
    if dest is None:
        return None
    return (dest, chapter, verse, segment or "")


def _remap_prov(chapter: int, verse: int, segment: str) -> tuple[str, int, int, str]:
    if chapter == 30:
        if 1 <= verse <= 14:
            return ("PRO", 24, 22, _PROV_30_SEGS[verse - 1])
        if 15 <= verse <= 33:
            return ("PRO", 24, verse + 20, segment)
    if chapter == 31 and 1 <= verse <= 9:
        return ("PRO", 24, verse + 53, segment)
    if 32 <= chapter <= 36:
        return ("PRO", chapter - 7, verse, segment)
    return ("PRO", chapter, verse, segment)


def _ref_span(ref: str) -> tuple[int, int] | None:
    """Read a CATSS range such as 'Bel 15-17' or 'Jer 7:27/28'."""
    if not ref:
        return None
    token = ref.split()[-1]
    if ":" in token:
        token = token.rsplit(":", 1)[1]
    match = _RANGE_RE.fullmatch(token)
    if match is None:
        return None
    start, end = int(match.group(1)), int(match.group(2))
    if end < start:
        return None
    return start, end


def _join_range(
    index: dict[tuple[str, int, int, str], str],
    book: str,
    chapter: int,
    start: int,
    end: int,
) -> str | None:
    parts = [
        index[key]
        for n in range(start, end + 1)
        if (key := (book, chapter, n, "")) in index
    ]
    return " ".join(parts) if parts else None


def _sirach_prologue(index: dict[tuple[str, int, int, str], str]) -> str | None:
    parts = []
    head = index.get(("SIR", 1, 1, ""))
    if head:
        parts.append(head)
    for letter in "abcdefg":
        piece = index.get(("SIR", 1, 1, letter))
        if piece:
            parts.append(piece)
    return " ".join(parts) if parts else None


def lookup_brenton(
    index: dict[tuple[str, int, int, str], str],
    book_code: str,
    chapter: int,
    verse: int,
    segment: str,
    ref: str,
) -> str | None:
    """Return Brenton's English for one CATSS verse, or None."""
    if book_code == "Sir" and chapter == 0:
        if verse != 1:
            return None
        return _sirach_prologue(index)

    dest = remap_reference(book_code, chapter, verse, segment)
    if dest is None:
        return None
    book, dest_chapter, dest_verse, dest_segment = dest

    span = _ref_span(ref)
    if span is not None:
        return _join_range(index, book, dest_chapter, span[0], span[1])
    return index.get((book, dest_chapter, dest_verse, dest_segment))


def ensure_brenton_column(con: sqlite3.Connection) -> None:
    """Add verses.brenton when an older corpus schema is missing it."""
    cols = {row[1] for row in con.execute("PRAGMA table_info(verses)")}
    if "brenton" not in cols:
        con.execute("ALTER TABLE verses ADD COLUMN brenton TEXT")
        con.commit()


def attach_brenton(con: sqlite3.Connection, source) -> dict[str, int]:
    """Write Brenton English onto every verse that has a match."""
    ensure_brenton_column(con)
    index = load_brenton_index(source)
    rows = con.execute(
        """
        SELECT v.id, b.code, v.chapter, v.verse, v.segment, v.ref
        FROM verses v JOIN books b ON b.id = v.book_id
        """
    ).fetchall()
    updates = []
    matched = 0
    missing = 0
    for vid, code, chapter, verse, segment, ref in rows:
        text = lookup_brenton(index, code, chapter, verse, segment or "", ref)
        updates.append((text, vid))
        if text:
            matched += 1
        else:
            missing += 1
    con.executemany("UPDATE verses SET brenton = ? WHERE id = ?", updates)
    con.commit()
    return {"matched": matched, "missing": missing, "verses": len(rows)}


def attach_to_db(
    db_path: Path | None = None, source: Path | None = None
) -> dict[str, int]:
    """Attach Brenton to an on-disk corpus. Used after a fetch, without a rebuild."""
    db_path = Path(db_path or (ROOT / "data" / "lxx.db"))
    source = Path(source or RAW_VPL)
    if not source.exists():
        raise FileNotFoundError(f"Brenton VPL missing at {source}")
    con = sqlite3.connect(db_path)
    try:
        return attach_brenton(con, source)
    finally:
        con.close()


if __name__ == "__main__":
    stats = attach_to_db()
    print(
        f"Brenton attached: {stats['matched']:,} matched, "
        f"{stats['missing']:,} without a line."
    )
