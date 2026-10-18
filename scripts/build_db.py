#!/usr/bin/env python3
"""Build data/lxx.db (the corpus) from the downloaded raw sources.

This database is disposable: it is derived entirely from data/raw/ and can be
rebuilt at any time. User progress lives in each learner's browser, never
here, so rebuilding destroys nothing anyone has earned.

Pipeline
--------
1. Parse the fixed-width CATSS .mlxx files into verses and tokens.
2. Convert every Beta Code surface form and lemma to Unicode.
3. Attach English glosses: Dodson first (concise, made for learners), Perseus
   LSJ as fallback for the many LXX lemmas absent from New Testament lexica.
4. Count lemma frequencies and assign ranks, which drive the
   frequency-first teaching order.
5. Attach Brenton's English to each verse that has a matching line.
"""

from __future__ import annotations

import csv
import html
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lxx.betacode import beta_to_plain, strip_accents, to_unicode  # noqa: E402
from lxx.brenton import RAW_VPL, attach_brenton  # noqa: E402
from lxx.translit import transliterate  # noqa: E402

RAW = ROOT / "data" / "raw"
MORPH_DIR = RAW / "lxxmorph"
LEX_DIR = RAW / "lexica"
DB_PATH = ROOT / "data" / "lxx.db"

# Fixed-width column layout of the .mlxx word lines, confirmed against all
# 624k lines in the corpus.
COL_SURFACE = slice(0, 25)
COL_POS = slice(25, 29)
COL_PARSE = slice(29, 36)
COL_LEMMA = slice(36, 53)
COL_PREVERB = slice(53, None)

# Verse headers come in several shapes across the corpus:
#     Gen 1:1        chapter and verse
#     Sir Prolog:1   non-numeric chapter
#     EpJer 12       single-chapter book, verse only
#     Sus 7/8        two verses merged in the edition
#     TobS 9:3-4     a verse range
#     Od             a bare superscription with no number of its own
# The book code itself may contain digits and slashes ("1Sam/K", "1/3Kgs"),
# so the reference part is split off from the right.
HEADER_RE = re.compile(r"^(?P<book>\S+)(?:\s+(?P<ref>\S.*?))?\s*$")
REF_RE = re.compile(
    r"^(?:(?P<chapter>Prolog|\d+):)?(?P<verse>\d+)"
    r"(?:[-/]\d+)?(?P<segment>[a-z]?)$"
)

# Display names for the CATSS book codes, and whether a book belongs to the
# default linear reading sequence. The corpus ships several books in two
# textual traditions; both are stored and readable, but only one of each pair
# sits in the reading sequence so that "advance n verses" stays unambiguous.
#   is_primary=False -> JoshA, JudgA, TobS, BelTh, DanTh, SusTh
BOOKS: dict[str, tuple[str, bool]] = {
    "Gen": ("Genesis", True),
    "Exod": ("Exodus", True),
    "Lev": ("Leviticus", True),
    "Num": ("Numbers", True),
    "Deut": ("Deuteronomy", True),
    "JoshB": ("Joshua (B)", True),
    "JoshA": ("Joshua (A text)", False),
    "JudgB": ("Judges (B)", True),
    "JudgA": ("Judges (A text)", False),
    "Ruth": ("Ruth", True),
    "1Sam/K": ("1 Samuel (1 Kingdoms)", True),
    "2Sam/K": ("2 Samuel (2 Kingdoms)", True),
    "1/3Kgs": ("1 Kings (3 Kingdoms)", True),
    "2/4Kgs": ("2 Kings (4 Kingdoms)", True),
    "1Chr": ("1 Chronicles", True),
    "2Chr": ("2 Chronicles", True),
    "1Esdr": ("1 Esdras", True),
    "2Esdr": ("2 Esdras (Ezra-Nehemiah)", True),
    "Esth": ("Esther", True),
    "Jdt": ("Judith", True),
    "TobBA": ("Tobit (BA)", True),
    "TobS": ("Tobit (S text)", False),
    "1Mac": ("1 Maccabees", True),
    "2Mac": ("2 Maccabees", True),
    "3Mac": ("3 Maccabees", True),
    "4Mac": ("4 Maccabees", True),
    "Ps": ("Psalms", True),
    "Od": ("Odes", True),
    "Prov": ("Proverbs", True),
    "Qoh": ("Ecclesiastes (Qoheleth)", True),
    "Cant": ("Song of Songs", True),
    "Job": ("Job", True),
    "Wis": ("Wisdom of Solomon", True),
    "Sir": ("Sirach (Ecclesiasticus)", True),
    "PsSol": ("Psalms of Solomon", True),
    "Hos": ("Hosea", True),
    "Mic": ("Micah", True),
    "Amos": ("Amos", True),
    "Joel": ("Joel", True),
    "Jonah": ("Jonah", True),
    "Obad": ("Obadiah", True),
    "Nah": ("Nahum", True),
    "Hab": ("Habakkuk", True),
    "Zeph": ("Zephaniah", True),
    "Hag": ("Haggai", True),
    "Zech": ("Zechariah", True),
    "Mal": ("Malachi", True),
    "Isa": ("Isaiah", True),
    "Jer": ("Jeremiah", True),
    "Bar": ("Baruch", True),
    "EpJer": ("Epistle of Jeremiah", True),
    "Lam": ("Lamentations", True),
    "Ezek": ("Ezekiel", True),
    "Bel": ("Bel and the Dragon (OG)", True),
    "BelTh": ("Bel and the Dragon (Theodotion)", False),
    "Dan": ("Daniel (OG)", True),
    "DanTh": ("Daniel (Theodotion)", False),
    "Sus": ("Susanna (OG)", True),
    "SusTh": ("Susanna (Theodotion)", False),
}

SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE books (
    id          INTEGER PRIMARY KEY,
    code        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    is_primary  INTEGER NOT NULL,
    sort_order  INTEGER NOT NULL,
    verse_count INTEGER NOT NULL DEFAULT 0,
    token_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE verses (
    id          INTEGER PRIMARY KEY,
    book_id     INTEGER NOT NULL REFERENCES books(id),
    chapter     INTEGER NOT NULL,
    verse       INTEGER NOT NULL,
    segment     TEXT NOT NULL DEFAULT '',
    ref         TEXT NOT NULL,
    -- Position in the linear reading sequence. NULL for books outside it
    -- (alternate textual traditions), which are readable by reference only.
    seq         INTEGER,
    text        TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    brenton     TEXT
);
CREATE UNIQUE INDEX idx_verses_seq ON verses(seq) WHERE seq IS NOT NULL;
CREATE INDEX idx_verses_ref ON verses(book_id, chapter, verse);

CREATE TABLE lemmas (
    id         INTEGER PRIMARY KEY,
    lemma      TEXT NOT NULL UNIQUE,   -- Unicode, accented
    plain      TEXT NOT NULL,          -- lowercase, accents stripped
    gloss      TEXT,                   -- short definition for flashcards
    gloss_long TEXT,
    source     TEXT,                   -- 'dodson' | 'lsj' | NULL
    freq       INTEGER NOT NULL DEFAULT 0,
    rank       INTEGER,                -- 1 = most frequent in the corpus
    first_seq  INTEGER                 -- earliest reading position it occurs
);
CREATE INDEX idx_lemmas_plain ON lemmas(plain);
CREATE INDEX idx_lemmas_rank ON lemmas(rank);

CREATE TABLE tokens (
    id       INTEGER PRIMARY KEY,
    verse_id INTEGER NOT NULL REFERENCES verses(id),
    idx      INTEGER NOT NULL,
    surface  TEXT NOT NULL,
    plain    TEXT NOT NULL,
    lemma_id INTEGER REFERENCES lemmas(id),
    pos      TEXT,
    parse    TEXT,
    preverb  TEXT
);
CREATE INDEX idx_tokens_verse ON tokens(verse_id, idx);
CREATE INDEX idx_tokens_lemma ON tokens(lemma_id);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""

def parse_mlxx(path: Path):
    """Yield (book_code, chapter, verse, segment, label, [token, ...]) per verse."""
    tokens: list[dict] = []
    pending = None
    last_chapter: dict[str, int] = {}

    with path.open(encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if not line.strip():
                continue

            # A word line always carries a part-of-speech code at column 25.
            if line[COL_POS].strip():
                surface_beta = line[COL_SURFACE].strip()
                if not surface_beta:
                    continue
                tokens.append(
                    {
                        "surface_beta": surface_beta,
                        "pos": line[COL_POS].strip(),
                        "parse": line[COL_PARSE].strip(),
                        "lemma_beta": line[COL_LEMMA].strip(),
                        "preverb_beta": line[COL_PREVERB].strip(),
                    }
                )
                continue

            m = HEADER_RE.match(line.strip())
            if not m:
                continue
            if pending and tokens:
                yield (*pending, tokens)
            tokens = []

            book_code = m.group("book")
            ref = (m.group("ref") or "").strip()
            label = f"{book_code} {ref}".strip()
            rm = REF_RE.match(ref) if ref else None

            if rm:
                ch = rm.group("chapter")
                if ch is None:
                    # Single-chapter books write the verse alone ("EpJer 12").
                    chapter = last_chapter.get(book_code, 1)
                elif ch == "Prolog":
                    chapter = 0
                else:
                    chapter = int(ch)
                verse = int(rm.group("verse"))
                segment = rm.group("segment") or ""
            else:
                # A bare book name heads a superscription that the edition
                # gives no number of its own -- keep it as verse 0 so the text
                # is not silently dropped.
                chapter = last_chapter.get(book_code, 1)
                verse = 0
                segment = ""

            last_chapter[book_code] = chapter
            pending = (book_code, chapter, verse, segment, label)

    if pending and tokens:
        yield (*pending, tokens)


def load_dodson() -> dict[str, tuple[str, str]]:
    """plain lemma -> (short gloss, long gloss) from the Dodson lexicon."""
    out: dict[str, tuple[str, str]] = {}
    path = LEX_DIR / "dodson.csv"
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            word = (row.get("Greek Word") or "").strip()
            if not word:
                continue
            # Entries look like "a)eto/s, ou=, o(" -- the headword is the part
            # before the first comma; the rest is grammatical apparatus.
            head = word.split(",")[0].strip()
            key = beta_to_plain(head)
            if not key or key in out:
                continue
            short = (row.get("English Definition (brief)") or "").strip()
            long = (row.get("English Definition (longer)") or "").strip()
            if short or long:
                out[key] = (short or long, long)
    return out


_TR_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S)

# LSJ marks comparative-philology cognates with the same <tr> tag it uses for
# English glosses, so εἶπον picks up "avocam" and "vac-" alongside "said".
# Reconstructed roots and Latin/Sanskrit citations are recognisable by their
# hyphens and macrons; English glosses have neither.
_ETYMOLOGY_RE = re.compile(
    r"^-|-$|-.*-|[^\x00-\x7f]",   # leading/trailing/double hyphen, or non-ASCII
)


def _is_english_gloss(text: str) -> bool:
    return bool(text) and not _ETYMOLOGY_RE.search(text)
_TAG_RE = re.compile(r"<[^>]+>")
_ENTRY_RE = re.compile(r'<entryFree\b[^>]*\bkey="([^"]+)"[^>]*>(.*?)</entryFree>', re.S)


def load_lsj() -> dict[str, tuple[str, str]]:
    """plain lemma -> (short gloss, long gloss) from Perseus LSJ.

    LSJ entries are full scholarly articles. The <tr> elements mark the actual
    translation equivalents, so joining the first few gives a usable gloss
    without dragging in citations. Read across all 27 volumes.
    """
    out: dict[str, tuple[str, str]] = {}
    volumes = sorted(LEX_DIR.glob("lsj-*.xml"))
    if not volumes:
        print("  WARNING: no LSJ volumes found", file=sys.stderr)
    for vol in volumes:
        text = vol.read_text(encoding="utf-8", errors="replace")
        for key, body in _ENTRY_RE.findall(text):
            # Homographs are keyed a)/gw1, a)/gw2, ...
            headword = re.sub(r"\d+$", "", key).strip()
            plain = beta_to_plain(headword)
            if not plain or plain in out:
                continue
            trs = []
            for frag in _TR_RE.findall(body):
                g = html.unescape(_TAG_RE.sub("", frag)).strip(" ,;:.\n\t")
                g = re.sub(r"\s+", " ", g)
                if g and g not in trs and _is_english_gloss(g):
                    trs.append(g)
                if len(trs) >= 6:
                    break
            if not trs:
                continue
            short = "; ".join(trs[:3])
            if len(short) > 110:
                short = short[:107].rstrip(" ;,") + "..."
            out[plain] = (short, "; ".join(trs))
    return out


# CATSS and the classical lexica often choose different citation forms for the
# same verb or noun. These rewrites are tried when a lemma has no direct hit;
# each maps a CATSS-style ending to the ending a lexicon is likely to list.
#   δεικνύω / δείκνυμι, ἁλίσκω / ἁλίσκομαι, σωτήριον / σωτήριος
_VARIANT_RULES: list[tuple[str, str]] = [
    ("υω", "υμι"),      # δεικνυω  -> δεικνυμι
    ("υμι", "υω"),
    ("ομαι", "ω"),      # επισκεπτομαι-style deponents listed as active
    ("ω", "ομαι"),      # αλισκω   -> αλισκομαι
    ("ον", "ος"),       # σωτηριον -> σωτηριος (neuter substantive)
    ("ους", "ος"),      # χειμαρρους -> χειμαρρος
    ("ομαι", "αω"),
    ("αομαι", "αω"),
    ("εομαι", "εω"),
    ("οομαι", "οω"),
]


def variant_keys(plain: str) -> list[str]:
    """Alternative citation forms to try when a lemma has no direct lexicon hit.

    Candidates are pushed back through strip_accents so that a rule written
    with a final sigma still matches keys stored with a medial one.
    """
    out = []
    for src, dst in _VARIANT_RULES:
        src, dst = strip_accents(src), strip_accents(dst)
        if plain.endswith(src) and len(plain) > len(src):
            cand = strip_accents(plain[: -len(src)] + dst)
            if cand != plain and cand not in out:
                out.append(cand)
    return out


def resolve_gloss(lemma: str, plain: str, pos: str | None, dodson, lsj):
    """Find the best available gloss for a lemma.

    Order: exact match in Dodson (concise, learner-oriented), then LSJ, then
    the same two under variant citation forms, and finally -- for the ~3,800
    Hebrew proper names that no Greek lexicon carries -- a transliteration.
    Returns (short, long, source) or None.
    """
    hit = dodson.get(plain)
    if hit:
        return hit[0], hit[1], "dodson"
    hit = lsj.get(plain)
    if hit:
        return hit[0], hit[1], "lsj"

    for cand in variant_keys(plain):
        hit = dodson.get(cand)
        if hit:
            return hit[0], hit[1], "dodson~"
        hit = lsj.get(cand)
        if hit:
            return hit[0], hit[1], "lsj~"

    # Crasis and compound tokens are tagged with a '+' part-of-speech such as
    # C+RP (καί + ἐγώ); gloss them from their components where possible.
    if pos and "+" in pos:
        return None

    if lemma[:1].isupper():
        name = transliterate(lemma)
        return f"proper name ({name})", f"Proper name, transliterated {name}.", "translit"
    return None


def main() -> int:
    files = sorted(MORPH_DIR.glob("*.mlxx"), key=lambda p: int(p.name.split(".")[0]))
    if not files:
        print("No .mlxx files found -- run scripts/fetch_data.py first.", file=sys.stderr)
        return 1

    print("Loading lexica...")
    dodson = load_dodson()
    lsj = load_lsj()
    print(f"  Dodson: {len(dodson):,} entries")
    print(f"  LSJ:    {len(lsj):,} entries")

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    for suffix in ("-wal", "-shm"):
        stale = DB_PATH.with_name(DB_PATH.name + suffix)
        if stale.exists():
            stale.unlink()

    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)

    # --- books -------------------------------------------------------------
    book_ids: dict[str, int] = {}
    for order, (code, (name, primary)) in enumerate(BOOKS.items()):
        cur = con.execute(
            "INSERT INTO books (code, name, is_primary, sort_order) VALUES (?,?,?,?)",
            (code, name, int(primary), order),
        )
        book_ids[code] = cur.lastrowid

    # --- verses and tokens -------------------------------------------------
    print("\nParsing corpus...")
    lemma_ids: dict[str, int] = {}
    lemma_rows: list[tuple] = []
    verse_rows: list[tuple] = []
    token_rows: list[tuple] = []
    verse_id = 0
    token_id = 0
    seq = 0
    unknown_books: set[str] = set()

    for path in files:
        count = 0
        for code, chapter, verse, segment, label, toks in parse_mlxx(path):
            if code not in book_ids:
                unknown_books.add(code)
                continue
            book_id = book_ids[code]
            primary = BOOKS[code][1]
            verse_id += 1
            count += 1

            words = []
            trow = []
            for i, t in enumerate(toks):
                surface = to_unicode(t["surface_beta"])
                lemma = to_unicode(t["lemma_beta"])
                if not lemma:
                    lemma_id = None
                else:
                    lemma_id = lemma_ids.get(lemma)
                    if lemma_id is None:
                        lemma_id = len(lemma_ids) + 1
                        lemma_ids[lemma] = lemma_id
                        lemma_rows.append((lemma_id, lemma, strip_accents(lemma)))
                words.append(surface)
                token_id += 1
                trow.append(
                    (
                        token_id, verse_id, i, surface, strip_accents(surface),
                        lemma_id, t["pos"] or None, t["parse"] or None,
                        to_unicode(t["preverb_beta"]) or None,
                    )
                )
            token_rows.extend(trow)

            if primary:
                seq += 1
                vseq = seq
            else:
                vseq = None
            # Keep the edition's own reference string (which may read "Sus 7/8"
            # or "TobS 9:3-4") rather than reconstructing one from the numbers.
            ref = label
            verse_rows.append(
                (verse_id, book_id, chapter, verse, segment, ref, vseq,
                 " ".join(words), len(toks))
            )

        print(f"  {path.name:22s} {count:6,} verses")

        if len(token_rows) > 200_000:
            _flush(con, verse_rows, token_rows, lemma_rows)
            verse_rows, token_rows, lemma_rows = [], [], []

    _flush(con, verse_rows, token_rows, lemma_rows)

    if unknown_books:
        print(f"\nWARNING unmapped book codes: {sorted(unknown_books)}", file=sys.stderr)

    # --- glosses -----------------------------------------------------------
    print("\nAttaching glosses...")
    # One representative POS per lemma, used to spot crasis/compound tokens.
    lemma_pos = dict(
        con.execute(
            "SELECT lemma_id, MIN(pos) FROM tokens WHERE lemma_id IS NOT NULL"
            " GROUP BY lemma_id"
        ).fetchall()
    )
    updates = []
    for lemma, lid in lemma_ids.items():
        got = resolve_gloss(lemma, strip_accents(lemma), lemma_pos.get(lid), dodson, lsj)
        if got is None:
            continue
        short, long, source = got
        updates.append((short or None, long or None, source, lid))
    con.executemany(
        "UPDATE lemmas SET gloss=?, gloss_long=?, source=? WHERE id=?", updates
    )

    # --- frequencies and ranks --------------------------------------------
    print("Computing frequencies...")
    con.execute(
        """
        UPDATE lemmas SET
            freq = COALESCE((
                SELECT COUNT(*) FROM tokens t
                JOIN verses v ON v.id = t.verse_id
                WHERE t.lemma_id = lemmas.id AND v.seq IS NOT NULL), 0),
            first_seq = (
                SELECT MIN(v.seq) FROM tokens t
                JOIN verses v ON v.id = t.verse_id
                WHERE t.lemma_id = lemmas.id AND v.seq IS NOT NULL)
        """
    )
    con.execute(
        """
        WITH ranked AS (
            SELECT id, ROW_NUMBER() OVER (ORDER BY freq DESC, plain ASC) AS r
            FROM lemmas WHERE freq > 0
        )
        UPDATE lemmas SET rank = (SELECT r FROM ranked WHERE ranked.id = lemmas.id)
        """
    )

    con.execute(
        """
        UPDATE books SET
            verse_count = COALESCE((SELECT COUNT(*) FROM verses WHERE book_id=books.id),0),
            token_count = COALESCE((SELECT SUM(token_count) FROM verses WHERE book_id=books.id),0)
        """
    )

    stats = {
        "verses": con.execute("SELECT COUNT(*) FROM verses").fetchone()[0],
        "reading_verses": con.execute(
            "SELECT COUNT(*) FROM verses WHERE seq IS NOT NULL").fetchone()[0],
        "tokens": con.execute("SELECT COUNT(*) FROM tokens").fetchone()[0],
        "lemmas": con.execute("SELECT COUNT(*) FROM lemmas").fetchone()[0],
        "lemmas_in_reading": con.execute(
            "SELECT COUNT(*) FROM lemmas WHERE freq > 0").fetchone()[0],
        "glossed": con.execute(
            "SELECT COUNT(*) FROM lemmas WHERE gloss IS NOT NULL").fetchone()[0],
    }
    for k, v in stats.items():
        con.execute("INSERT INTO meta (key, value) VALUES (?,?)", (k, str(v)))
    if RAW_VPL.exists():
        print("\nAttaching Brenton...")
        brenton = attach_brenton(con, RAW_VPL)
        print(f"  matched {brenton['matched']:,} / {brenton['verses']:,}")
        con.execute(
            "INSERT INTO meta (key, value) VALUES ('brenton_matched', ?)",
            (str(brenton["matched"]),),
        )
        con.execute(
            "INSERT INTO meta (key, value) VALUES ('brenton_missing', ?)",
            (str(brenton["missing"]),),
        )
    else:
        print(
            "\nWARNING: Brenton VPL missing -- run scripts/fetch_data.py",
            file=sys.stderr,
        )

    con.execute(
        "INSERT INTO meta (key, value) VALUES"
        " ('source','CCAT/CATSS LXXM; Dodson; Perseus LSJ; Brenton 1851')"
    )

    con.commit()
    con.execute("VACUUM")
    con.close()

    print("\n" + "=" * 52)
    for k, v in stats.items():
        print(f"  {k:20s} {v:>10,}")
    cov = 100 * stats["glossed"] / stats["lemmas"] if stats["lemmas"] else 0
    print(f"  {'gloss coverage':20s} {cov:>9.1f}%")
    print("=" * 52)
    print(f"\nWrote {DB_PATH} ({DB_PATH.stat().st_size / 1e6:.1f} MB)")
    return 0


def _flush(con, verse_rows, token_rows, lemma_rows):
    if lemma_rows:
        con.executemany(
            "INSERT INTO lemmas (id, lemma, plain) VALUES (?,?,?)", lemma_rows
        )
    if verse_rows:
        con.executemany(
            "INSERT INTO verses (id, book_id, chapter, verse, segment, ref, seq,"
            " text, token_count) VALUES (?,?,?,?,?,?,?,?,?)",
            verse_rows,
        )
    if token_rows:
        con.executemany(
            "INSERT INTO tokens (id, verse_id, idx, surface, plain, lemma_id,"
            " pos, parse, preverb) VALUES (?,?,?,?,?,?,?,?,?)",
            token_rows,
        )
    con.commit()


if __name__ == "__main__":
    raise SystemExit(main())
