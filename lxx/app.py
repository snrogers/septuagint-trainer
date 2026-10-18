"""HTTP server: read-only corpus API plus the static reading/drilling UI.

Deliberately built on the standard library alone. The whole point of this tool
is that it keeps working years from now with no dependency drift -- a corpus
and one `python3 -m lxx.app` to run it.

The server holds no user state. Reading position, flashcards and review
history live in each learner's browser (web/progress.js); every endpoint here
answers from the corpus alone, so one instance serves any number of people.
"""

from __future__ import annotations

import json
import mimetypes
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import db, tts
from .morph import describe, pos_name

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"

# The only request body is /api/core's list of lemmas the learner already has:
# one id per card, so even a deck holding every lemma stays under 100 KiB.
MAX_BODY_BYTES = 256 * 1024


# --- query helpers ---------------------------------------------------------

def _has_column(con: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in con.execute(f"PRAGMA table_info({table})"))


def _verse_select(con: sqlite3.Connection) -> str:
    extra = ", v.brenton" if _has_column(con, "verses", "brenton") else ""
    return (
        "SELECT v.id, v.seq, v.ref, v.text" + extra +
        ", v.chapter, v.verse, b.name AS book, b.code AS book_code"
    )


def _verses_with_tokens(con: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[dict]:
    """Add parsed token data to verse rows from either reading mode."""
    if not rows:
        return []

    ids = [r["id"] for r in rows]
    placeholders = ",".join("?" * len(ids))
    tokens = con.execute(
        f"""
        SELECT t.verse_id, t.idx, t.surface, t.pos, t.parse, t.preverb,
               l.id AS lemma_id, l.lemma, l.gloss, l.freq, l.rank
        FROM tokens t
        LEFT JOIN lemmas l ON l.id = t.lemma_id
        WHERE t.verse_id IN ({placeholders})
        ORDER BY t.verse_id, t.idx
        """,
        ids,
    ).fetchall()

    by_verse: dict[int, list[dict]] = {i: [] for i in ids}
    for t in tokens:
        by_verse[t["verse_id"]].append(
            {
                "surface": t["surface"],
                "lemma_id": t["lemma_id"],
                "lemma": t["lemma"],
                "gloss": t["gloss"],
                "parse": describe(t["pos"], t["parse"]),
                "pos": pos_name(t["pos"]),
                "preverb": t["preverb"],
                "freq": t["freq"],
                "rank": t["rank"],
            }
        )

    return [
        {
            "id": r["id"],
            "seq": r["seq"],
            "ref": r["ref"],
            "book": r["book"],
            "book_code": r["book_code"],
            "chapter": r["chapter"],
            "verse": r["verse"],
            "text": r["text"],
            "brenton": r["brenton"] if "brenton" in r.keys() else None,
            "tokens": by_verse[r["id"]],
        }
        for r in rows
    ]


def verse_range(con: sqlite3.Connection, start: int, count: int) -> list[dict]:
    """Verses [start, start+count) in reading order, each with parsed tokens."""
    rows = con.execute(
        _verse_select(con) + """
        FROM verses v JOIN books b ON b.id = v.book_id
        WHERE v.seq >= ? AND v.seq < ?
        ORDER BY v.seq
        """,
        (start, start + count),
    ).fetchall()
    return _verses_with_tokens(con, rows)


def reference_options(
    con: sqlite3.Connection, book_code: str, chapter: int | None = None
) -> dict | None:
    """Return the real chapters and verse rows available for one stored book."""
    book = con.execute(
        """
        SELECT id, code, name, is_primary
        FROM books WHERE code = ? AND verse_count > 0
        """,
        (book_code,),
    ).fetchone()
    if book is None:
        return None

    chapter_rows = con.execute(
        """
        SELECT chapter, COUNT(*) AS verse_count
        FROM verses WHERE book_id = ?
        GROUP BY chapter ORDER BY chapter
        """,
        (book["id"],),
    ).fetchall()
    chapters = [dict(row) for row in chapter_rows]
    if not chapters:
        return None

    available = {row["chapter"] for row in chapter_rows}
    selected = chapters[0]["chapter"] if chapter is None else int(chapter)
    if selected not in available:
        return None

    verses = con.execute(
        """
        SELECT id, chapter, verse, segment, ref
        FROM verses
        WHERE book_id = ? AND chapter = ?
        ORDER BY id
        """,
        (book["id"], selected),
    ).fetchall()
    return {
        "book": dict(book),
        "chapters": chapters,
        "chapter": selected,
        "verses": [dict(row) for row in verses],
    }


def reference_passage(
    con: sqlite3.Connection,
    book_code: str,
    count: int,
    *,
    chapter: int | None = None,
    verse: int | None = None,
    cursor: int | None = None,
    direction: str = "next",
) -> dict | None:
    """Read one page in stored source order."""
    book = con.execute(
        """
        SELECT id, code, name, is_primary
        FROM books WHERE code = ? AND verse_count > 0
        """,
        (book_code,),
    ).fetchone()
    if book is None:
        return None

    if cursor is not None:
        anchor = con.execute(
            """
            SELECT id, chapter, verse, segment
            FROM verses WHERE id = ? AND book_id = ?
            """,
            (int(cursor), book["id"]),
        ).fetchone()
    elif chapter is not None and verse is not None:
        anchor = con.execute(
            """
            SELECT id, chapter, verse, segment
            FROM verses
            WHERE book_id = ? AND chapter = ? AND verse = ?
            ORDER BY segment, id LIMIT 1
            """,
            (book["id"], int(chapter), int(verse)),
        ).fetchone()
    else:
        return None
    if anchor is None:
        return None

    count = max(1, min(int(count), 200))
    if direction == "previous":
        descending = con.execute(
            _verse_select(con) + """
            FROM verses v JOIN books b ON b.id = v.book_id
            WHERE v.book_id = ? AND v.id < ?
            ORDER BY v.id DESC LIMIT ?
            """,
            (book["id"], anchor["id"], count),
        ).fetchall()
        rows = list(reversed(descending))
    elif direction == "next":
        rows = con.execute(
            _verse_select(con) + """
            FROM verses v JOIN books b ON b.id = v.book_id
            WHERE v.book_id = ? AND v.id >= ?
            ORDER BY v.id LIMIT ?
            """,
            (book["id"], anchor["id"], count),
        ).fetchall()
    else:
        return None
    if not rows:
        return None

    first = rows[0]
    last = rows[-1]
    prior = con.execute(
        """
        SELECT id FROM verses
        WHERE book_id = ? AND id < ?
        ORDER BY id DESC LIMIT 1
        """,
        (book["id"], first["id"]),
    ).fetchone()
    following = con.execute(
        """
        SELECT id FROM verses
        WHERE book_id = ? AND id > ?
        ORDER BY id LIMIT 1
        """,
        (book["id"], last["id"]),
    ).fetchone()

    return {
        "book": dict(book),
        "verses": _verses_with_tokens(con, rows),
        "previous": first["id"] if prior else None,
        "next": following["id"] if following else None,
    }


def corpus_status(con: sqlite3.Connection) -> dict:
    """Corpus totals the front end needs for its progress figures.

    `total_freq` is the denominator of text coverage: the share of all running
    text made of the words a learner has graduated to review.
    """
    total_verses = con.execute("SELECT MAX(seq) AS m FROM verses").fetchone()["m"]
    total_freq = con.execute("SELECT SUM(freq) AS f FROM lemmas").fetchone()["f"]
    return {"total_verses": total_verses or 0, "total_freq": total_freq or 0}


def core_words(con: sqlite3.Connection, count: int, exclude: list[int]) -> list[dict]:
    """Highest-frequency glossed lemmas, skipping the ones already in a deck.

    This is the bootstrap deck. The caller passes its own deck as `exclude`,
    because the server does not know what anyone has studied.
    """
    rows = con.execute(
        """
        SELECT id AS lemma_id, lemma, gloss, freq, rank
        FROM lemmas
        WHERE rank IS NOT NULL AND gloss IS NOT NULL
          AND id NOT IN (SELECT value FROM json_each(?))
        ORDER BY rank LIMIT ?
        """,
        (json.dumps(exclude), count),
    ).fetchall()
    return [dict(r) for r in rows]


def lemma_example(con: sqlite3.Connection, lemma_id: int) -> dict | None:
    """The lemma's first verse in reading order, plus the forms it takes there.

    The forms let the front end highlight the word being learned in context.
    """
    row = con.execute(
        """
        SELECT v.ref, v.text, v.id AS verse_id FROM tokens t
        JOIN verses v ON v.id = t.verse_id
        WHERE t.lemma_id = ? AND v.seq IS NOT NULL
        ORDER BY v.seq LIMIT 1
        """,
        (lemma_id,),
    ).fetchone()
    if row is None:
        return None
    forms = [
        r["surface"]
        for r in con.execute(
            "SELECT DISTINCT surface FROM tokens WHERE verse_id = ? AND lemma_id = ?",
            (row["verse_id"], lemma_id),
        ).fetchall()
    ]
    return {"ref": row["ref"], "text": row["text"], "forms": forms}


def lemma_detail(con: sqlite3.Connection, lemma_id: int) -> dict:
    """Everything the corpus knows about one lemma, for its entry and its card."""
    row = con.execute("SELECT * FROM lemmas WHERE id = ?", (lemma_id,)).fetchone()
    if row is None:
        return {"error": "no such lemma"}
    data = dict(row)
    data["example"] = lemma_example(con, lemma_id)
    data["occurrences"] = [
        dict(r)
        for r in con.execute(
            """
            -- One row per verse: a lemma can occur several times in the
            -- same verse, and repeating the verse text adds nothing.
            SELECT v.ref, v.text, v.seq, MIN(t.surface) AS surface,
                   MIN(t.pos) AS pos, MIN(t.parse) AS parse,
                   COUNT(*) AS hits
            FROM tokens t JOIN verses v ON v.id = t.verse_id
            WHERE t.lemma_id = ? AND v.seq IS NOT NULL
            GROUP BY v.id
            ORDER BY v.seq LIMIT 12
            """,
            (lemma_id,),
        ).fetchall()
    ]
    for occ in data["occurrences"]:
        occ["parse_text"] = describe(occ["pos"], occ["parse"])
    return data


# --- HTTP ------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "lxx/1.0"

    # Set for the duration of a HEAD request; see do_HEAD.
    _head_only = False

    def log_message(self, fmt, *args):  # quieter console
        return

    # -- plumbing
    def _write_body(self, body: bytes) -> None:
        """Write a response body, unless this is a HEAD request.

        The headers, Content-Length included, must describe the body a GET
        would have returned, so callers build the body either way and only
        the write is skipped.
        """
        if not self._head_only:
            self.wfile.write(body)

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self._write_body(body)

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> dict | None:
        """The JSON request body, or None once an error has been answered."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            self._json({"error": "invalid Content-Length"}, 400)
            return None
        if length > MAX_BODY_BYTES:
            # The body is never read, so the connection cannot carry another
            # request.
            self.close_connection = True
            self._json({"error": "request body too large"}, 413)
            return None
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return {}

    def _api(self, route, arg) -> None:
        try:
            con = self.server.connect()
            try:
                route(self, con, arg)
            finally:
                con.close()
        except Exception as exc:  # surface errors as JSON, never a stack dump
            self._json({"error": str(exc)}, 500)

    # -- routing
    def do_GET(self) -> None:
        url = urlparse(self.path)
        if not url.path.startswith("/api/"):
            self._static(url.path)
        elif url.path in GET_ROUTES:
            self._api(GET_ROUTES[url.path], parse_qs(url.query))
        else:
            self._json({"error": "unknown endpoint"}, 404)

    def do_HEAD(self) -> None:
        """Answer HEAD with the status and headers a GET would produce.

        Without this, BaseHTTPRequestHandler answers 501: only do_GET and
        do_POST existed, so any monitor that probes with HEAD saw the app as
        broken. Routing through do_GET keeps the two answers in step by
        construction -- a separate implementation would drift.
        """
        self._head_only = True
        try:
            self.do_GET()
        finally:
            self._head_only = False

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path not in POST_ROUTES:
            self._json({"error": "unknown endpoint"}, 404)
            return
        body = self._body()
        if body is not None:
            self._api(POST_ROUTES[path], body)

    # -- endpoints
    def _get_status(self, con: sqlite3.Connection, q: dict) -> None:
        self._json(corpus_status(con))

    def _get_read(self, con: sqlite3.Connection, q: dict) -> None:
        try:
            n = max(1, min(int(q.get("n", ["5"])[0]), 200))
            start = int(q.get("from", ["1"])[0])
        except (OverflowError, TypeError, ValueError):
            self._json({"error": "invalid range"}, 400)
            return
        self._json({"start": start, "verses": verse_range(con, start, n)})

    def _get_reference_options(self, con: sqlite3.Connection, q: dict) -> None:
        try:
            chapter = int(q["chapter"][0]) if "chapter" in q else None
            data = reference_options(con, q.get("book", [""])[0], chapter)
        except (OverflowError, TypeError, ValueError):
            self._json({"error": "invalid chapter"}, 400)
            return
        if data is None:
            self._json({"error": "reference not found"}, 404)
        else:
            self._json(data)

    def _get_passage(self, con: sqlite3.Connection, q: dict) -> None:
        try:
            n = max(1, min(int(q.get("n", ["10"])[0]), 200))
            chapter = int(q["chapter"][0]) if "chapter" in q else None
            verse = int(q["verse"][0]) if "verse" in q else None
            cursor = int(q["cursor"][0]) if "cursor" in q else None
            direction = q.get("direction", ["next"])[0]
            if direction not in ("next", "previous"):
                raise ValueError("invalid direction")
            data = reference_passage(
                con,
                q.get("book", [""])[0],
                n,
                chapter=chapter,
                verse=verse,
                cursor=cursor,
                direction=direction,
            )
        except (OverflowError, TypeError, ValueError):
            self._json({"error": "invalid reference"}, 400)
            return
        if data is None:
            self._json({"error": "reference not found"}, 404)
        else:
            self._json(data)

    def _get_lemma(self, con: sqlite3.Connection, q: dict) -> None:
        try:
            lemma_id = int(q["id"][0])
        except (KeyError, OverflowError, TypeError, ValueError):
            self._json({"error": "invalid lemma id"}, 400)
            return
        self._json(lemma_detail(con, lemma_id))

    def _get_books(self, con: sqlite3.Connection, q: dict) -> None:
        rows = con.execute(
            "SELECT code, name, verse_count, token_count, is_primary,"
            " (SELECT MIN(seq) FROM verses WHERE book_id = books.id) AS start_seq"
            " FROM books WHERE verse_count > 0 ORDER BY sort_order"
        ).fetchall()
        self._json([dict(r) for r in rows])

    def _get_tts(self, con: sqlite3.Connection, q: dict) -> None:
        info = tts.status()
        info["cache"] = tts.cache_stats()
        info["version"] = tts.audio_version()
        self._json(info)

    def _post_core(self, con: sqlite3.Connection, body: dict) -> None:
        try:
            n = max(1, min(int(body.get("n", 50)), 2000))
            exclude = [int(i) for i in body.get("exclude", [])]
        except (AttributeError, OverflowError, TypeError, ValueError):
            self._json({"error": "invalid core request"}, 400)
            return
        self._json(core_words(con, n, exclude))

    def _audio(self, con: sqlite3.Connection, q: dict) -> None:
        """Serve a WAV clip for a word, a lemma, or a whole verse."""
        corpus_text = False
        try:
            if "lemma_id" in q:
                row = con.execute(
                    "SELECT lemma FROM lemmas WHERE id = ?",
                    (int(q["lemma_id"][0]),),
                ).fetchone()
                text = row["lemma"] if row else ""
                corpus_text = True
            elif "verse_id" in q:
                row = con.execute(
                    "SELECT text FROM verses WHERE id = ?",
                    (int(q["verse_id"][0]),),
                ).fetchone()
                text = row["text"] if row else ""
                corpus_text = True
            elif "seq" in q:
                row = con.execute(
                    "SELECT text FROM verses WHERE seq = ?", (int(q["seq"][0]),)
                ).fetchone()
                text = row["text"] if row else ""
                corpus_text = True
            else:
                text = q.get("text", [""])[0]
        except (OverflowError, TypeError, ValueError):
            self._json({"error": "invalid audio reference"}, 400)
            return

        text = text.strip()
        if not text:
            self._json({"error": "nothing to pronounce"}, 400)
            return
        # Bound arbitrary text. A corpus ID can only select one stored verse.
        if not corpus_text and len(text) > 600:
            self._json({"error": "text too long to pronounce"}, 400)
            return

        try:
            path = tts.synthesize(text)
        except RuntimeError as exc:
            self._json({"error": str(exc)}, 503)
            return

        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(data)))
        # Clips are immutable for a given text and voice, so let the browser
        # keep them; the URL carries a version that changes when they are not.
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.end_headers()
        self._write_body(data)

    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB_ROOT / rel).resolve()
        # Never serve outside the web root.
        if not str(target).startswith(str(WEB_ROOT.resolve())) or not target.is_file():
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        ctype, _ = mimetypes.guess_type(str(target))
        if ctype and ctype.startswith("text/"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype or "application/octet-stream")


GET_ROUTES = {
    "/api/status": Handler._get_status,
    "/api/read": Handler._get_read,
    "/api/reference-options": Handler._get_reference_options,
    "/api/passage": Handler._get_passage,
    "/api/lemma": Handler._get_lemma,
    "/api/books": Handler._get_books,
    "/api/tts": Handler._get_tts,
    "/api/audio": Handler._audio,
}

# Read-only like everything else; a POST only because the exclusion list can
# run to thousands of ids.
POST_ROUTES = {
    "/api/core": Handler._post_core,
}


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def connect(self) -> sqlite3.Connection:
        # One connection per request: SQLite connections are not thread-safe,
        # and the handler is threaded.
        return db.connect()


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Septuagint reading trainer")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8710)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    con = db.connect()
    st = corpus_status(con)
    con.close()

    url = f"http://{args.host}:{args.port}/"
    print("Septuagint reading trainer")
    print(f"  corpus    {st['total_verses']:,} verses")
    print("  progress  kept in your browser; Progress -> Export saves a copy")
    print(f"\n  {url}\n")

    if not args.no_browser:
        import threading
        import webbrowser

        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    server = Server((args.host, args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
