"""Tests for the HTTP API and the legacy progress export.

Run with:  python3 -m unittest discover tests

The server holds no user state: every learner's progress lives in their own
browser. These tests run the real server against a small corpus and check
that it answers from the corpus alone, refuses the old progress-writing
endpoints, and never creates a database of its own. The last test feeds the
exporter's output to the browser code, so the two halves of the migration
cannot drift apart.
"""

from __future__ import annotations

import http.client
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from lxx import db
from lxx.app import MAX_BODY_BYTES, Handler, Server, lemma_detail
from export_progress import export

CORPUS_SCHEMA = """
CREATE TABLE books (
    id INTEGER PRIMARY KEY, code TEXT, name TEXT, is_primary INTEGER,
    sort_order INTEGER, verse_count INTEGER, token_count INTEGER
);
CREATE TABLE verses (
    id INTEGER PRIMARY KEY, book_id INTEGER, chapter INTEGER, verse INTEGER,
    segment TEXT, ref TEXT, seq INTEGER, text TEXT, token_count INTEGER
);
CREATE TABLE lemmas (
    id INTEGER PRIMARY KEY, lemma TEXT, gloss TEXT, gloss_long TEXT,
    source TEXT, freq INTEGER, rank INTEGER
);
CREATE TABLE tokens (
    id INTEGER PRIMARY KEY, verse_id INTEGER, idx INTEGER, surface TEXT,
    lemma_id INTEGER, pos TEXT, parse TEXT, preverb TEXT
);
INSERT INTO books VALUES (1, 'Gen', 'Genesis', 1, 0, 3, 10);
INSERT INTO verses VALUES
    (1, 1, 1, 1, '', 'Gen 1:1', 1, 'ἐν ἀρχῇ ἐποίησεν', 3),
    (2, 1, 1, 2, '', 'Gen 1:2', 2, 'ἡ δὲ γῆ', 3),
    (3, 1, 1, 3, '', 'Gen 1:3', 3, 'καὶ εἶπεν ὁ θεός', 4);
INSERT INTO lemmas VALUES
    (1, 'ἐν', 'in', NULL, 'dodson', 50, 2),
    (2, 'ἀρχή', 'beginning', NULL, 'dodson', 10, 5),
    (3, 'ποιέω', 'make', NULL, 'dodson', 20, 3),
    (4, 'ὁ', 'the', NULL, 'dodson', 100, 1),
    (5, 'Ιωναθαν', NULL, NULL, NULL, 5, 4),
    (6, 'θεός', 'God', NULL, 'dodson', 30, NULL);
INSERT INTO tokens VALUES
    (1, 1, 0, 'ἐν', 1, 'P', '', NULL),
    (2, 1, 1, 'ἀρχῇ', 2, 'N1', 'DSF', NULL),
    (3, 1, 2, 'ἐποίησεν', 3, 'V1', 'AAI3S', NULL),
    (4, 2, 0, 'ἡ', 4, 'RA', 'NSF', NULL),
    (5, 2, 1, 'δὲ', NULL, 'X', '', NULL),
    (6, 2, 2, 'γῆ', NULL, 'N1', 'NSF', NULL),
    (7, 3, 0, 'καὶ', NULL, 'C', '', NULL),
    (8, 3, 1, 'εἶπεν', NULL, 'V1', 'AAI3S', NULL),
    (9, 3, 2, 'ὁ', 4, 'RA', 'NSM', NULL),
    (10, 3, 3, 'θεός', 6, 'N2', 'NSM', NULL);
"""
TOTAL_FREQ = 215

# The server-side progress database as it existed before progress moved into
# the browser.
LEGACY_PROGRESS = """
CREATE TABLE progress (id INTEGER PRIMARY KEY, position INTEGER, updated_at TEXT);
CREATE TABLE cards (
    lemma_id INTEGER PRIMARY KEY, state TEXT, step INTEGER, ease REAL,
    interval_days REAL, due TEXT, reps INTEGER, lapses INTEGER, origin TEXT,
    added_at TEXT, last_review TEXT
);
CREATE TABLE reviews (
    id INTEGER PRIMARY KEY, lemma_id INTEGER, reviewed_at TEXT, grade INTEGER,
    interval_days REAL, ease REAL
);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
INSERT INTO progress VALUES (1, 3, '2026-08-15T11:00:00+00:00');
INSERT INTO settings VALUES ('daily_new_limit', '5'), ('verses_per_session', '5');
INSERT INTO cards VALUES
    (4, 'review', 0, 2.65, 4.0, '2099-01-01T00:00:00+00:00', 2, 0, 'core',
     '2026-08-07T09:00:00+00:00', '2026-08-15T10:00:00+00:00'),
    (1, 'learning', 0, 2.5, 0.0, '2026-08-15T11:01:00+00:00', 1, 0, 'core',
     '2026-08-07T09:00:00+00:00', '2026-08-15T11:00:00+00:00'),
    (3, 'new', 0, 2.5, 0.0, '2026-08-07T09:00:00+00:00', 0, 0, 'reading',
     '2026-08-07T09:00:00+00:00', NULL);
INSERT INTO reviews VALUES
    (1, 4, '2026-08-07T10:00:00+00:00', 2, 1.0, 2.5),
    (2, 4, '2026-08-15T10:00:00+00:00', 3, 4.0, 2.65),
    (3, 1, '2026-08-15T11:00:00+00:00', 0, 0.0, 2.5);
"""


def build(path: Path, script: str) -> None:
    con = sqlite3.connect(path)
    con.executescript(script)
    con.commit()
    con.close()


class ApiTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        self.corpus = self.data / "lxx.db"
        build(self.corpus, CORPUS_SCHEMA)

        patcher = patch.object(db, "CORPUS_DB", self.corpus)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.server = Server(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def connect(self):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=10)
        self.addCleanup(conn.close)
        return conn

    def request(self, method, path, body=None):
        conn = self.connect()
        if body is None:
            conn.request(method, path)
        else:
            conn.request(method, path, body=json.dumps(body).encode(),
                         headers={"Content-Type": "application/json"})
        res = conn.getresponse()
        return res.status, json.loads(res.read() or b"null")

    def test_status_reports_corpus_totals_only(self):
        self.assertEqual(
            self.request("GET", "/api/status"),
            (200, {"total_verses": 3, "total_freq": TOTAL_FREQ}),
        )

    def test_core_skips_the_callers_deck_and_unglossed_or_unranked_lemmas(self):
        status, rows = self.request("POST", "/api/core", {"n": 3, "exclude": [4]})
        self.assertEqual(status, 200)
        self.assertEqual([r["lemma_id"] for r in rows], [1, 3, 2])
        self.assertEqual(rows[0], {"lemma_id": 1, "lemma": "ἐν", "gloss": "in",
                                   "freq": 50, "rank": 2})

    def test_core_rejects_an_exclusion_list_that_is_not_ids(self):
        self.assertEqual(
            self.request("POST", "/api/core", {"exclude": ["not-an-id"]}),
            (400, {"error": "invalid core request"}),
        )
        self.assertEqual(
            self.request("POST", "/api/core", [1, 2]),
            (400, {"error": "invalid core request"}),
        )

    def test_an_oversized_body_is_refused_before_it_is_read(self):
        conn = self.connect()
        conn.putrequest("POST", "/api/core")
        conn.putheader("Content-Length", str(MAX_BODY_BYTES + 1))
        # No body follows: a server that tried to read it would hang here.
        conn.endheaders()
        res = conn.getresponse()
        self.assertEqual((res.status, json.loads(res.read())),
                         (413, {"error": "request body too large"}))

    def test_the_old_progress_endpoints_are_gone(self):
        for path in ("/api/advance", "/api/grade", "/api/queue", "/api/settings",
                     "/api/goto", "/api/reset"):
            with self.subTest(path=path):
                self.assertEqual(self.request("POST", path, {"n": 5}),
                                 (404, {"error": "unknown endpoint"}))
        for path in ("/api/drill", "/api/preview", "/api/stats"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 404)

    def test_read_returns_the_requested_range_without_any_learner_state(self):
        status, data = self.request("GET", "/api/read?from=2&n=2")
        self.assertEqual(status, 200)
        self.assertEqual([v["ref"] for v in data["verses"]], ["Gen 1:2", "Gen 1:3"])
        token = data["verses"][0]["tokens"][0]
        self.assertEqual((token["lemma_id"], token["freq"], token["rank"]), (4, 100, 1))
        self.assertNotIn("state", token)

    def test_lemma_entry_carries_its_first_example_with_the_forms_used_there(self):
        status, entry = self.request("GET", "/api/lemma?id=4")
        self.assertEqual(status, 200)
        self.assertEqual(entry["example"], {"ref": "Gen 1:2", "text": "ἡ δὲ γῆ", "forms": ["ἡ"]})
        self.assertEqual([o["ref"] for o in entry["occurrences"]], ["Gen 1:2", "Gen 1:3"])
        self.assertNotIn("state", entry)

    def test_malformed_query_parameters_are_client_errors(self):
        for path, error in (
            ("/api/read?n=abc", "invalid range"),
            ("/api/read?from=x", "invalid range"),
            ("/api/lemma", "invalid lemma id"),
            ("/api/lemma?id=abc", "invalid lemma id"),
        ):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path), (400, {"error": error}))

    def test_serving_requests_creates_no_database_beside_the_corpus(self):
        self.request("GET", "/api/status")
        self.request("GET", "/api/read?from=1&n=3")
        self.request("POST", "/api/core", {"n": 10})
        self.assertEqual(sorted(p.name for p in self.data.iterdir()), ["lxx.db"])


PARADIGM_EXTRA = """
INSERT INTO verses VALUES
    (4, 1, 1, 4, '', 'Gen 1:4', 4, 'ποιεῖ ποιήσας ἐποίησαν ποιῆσαι', 4),
    (5, 1, 1, 5, '', 'Gen 1:5', 5, 'θεοῦ θεῷ θεόν θεοί θεῶν θεοῖς θεούς', 7);
INSERT INTO tokens VALUES
    (11, 4, 0, 'ποιεῖ', 3, 'V1', 'PAI3S', NULL),
    (12, 4, 1, 'ποιήσας', 3, 'V1', 'AAPNSM', NULL),
    (13, 4, 2, 'ἐποίησαν', 3, 'V1', 'AAI3P', NULL),
    (14, 4, 3, 'ποιῆσαι', 3, 'V1', 'AAN', NULL),
    (15, 5, 0, 'θεοῦ', 6, 'N2', 'GSM', NULL),
    (16, 5, 1, 'θεῷ', 6, 'N2', 'DSM', NULL),
    (17, 5, 2, 'θεόν', 6, 'N2', 'ASM', NULL),
    (18, 5, 3, 'θεοί', 6, 'N2', 'NPM', NULL),
    (19, 5, 4, 'θεῶν', 6, 'N2', 'GPM', NULL),
    (20, 5, 5, 'θεοῖς', 6, 'N2', 'DPM', NULL),
    (21, 5, 6, 'θεούς', 6, 'N2', 'APM', NULL);
"""


class ParadigmTest(unittest.TestCase):
    """lemma_detail's paradigm tables group attested forms by parse code."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "lxx.db"
        build(path, CORPUS_SCHEMA + PARADIGM_EXTRA)
        self.con = sqlite3.connect(path)
        self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)

    def paradigm(self, lemma_id):
        return lemma_detail(self.con, lemma_id)["paradigm"]

    def test_noun_declension_grid(self):
        sections = self.paradigm(6)["sections"]
        self.assertEqual([s["kind"] for s in sections], ["declension"])
        (table,) = sections[0]["tables"]
        self.assertEqual(table["gender_label"], "masculine")
        self.assertEqual([r["label"] for r in table["rows"]],
                         ["nominative", "genitive", "dative", "accusative"])
        cells = {r["label"]: r["cells"] for r in table["rows"]}
        self.assertEqual(cells["nominative"]["S"]["forms"], ["θεός"])
        self.assertEqual(cells["nominative"]["P"]["forms"], ["θεοί"])
        self.assertEqual(cells["genitive"]["S"]["forms"], ["θεοῦ"])
        self.assertEqual(cells["accusative"]["P"]["forms"], ["θεούς"])

    def test_verb_conjugation_participle_infinitive(self):
        sections = self.paradigm(3)["sections"]
        by_kind = {}
        for s in sections:
            by_kind.setdefault(s["kind"], []).append(s)
        present, aorist = by_kind["conjugation"]
        self.assertEqual(present["label"], "present active indicative")
        self.assertEqual(present["rows"][0]["cells"]["S"]["forms"], ["ποιεῖ"])
        self.assertEqual(aorist["label"], "aorist active indicative")
        (row,) = aorist["rows"]
        self.assertEqual(row["label"], "3rd")
        self.assertEqual(row["cells"]["S"]["forms"], ["ἐποίησεν"])
        self.assertEqual(row["cells"]["P"]["forms"], ["ἐποίησαν"])
        (participle,) = by_kind["participles"]
        self.assertEqual(participle["label"], "aorist active participle")
        (table,) = participle["tables"]
        self.assertEqual(table["rows"][0]["cells"]["S"]["forms"], ["ποιήσας"])
        (infinitives,) = by_kind["infinitives"]
        self.assertEqual(infinitives["items"],
                         [{"label": "aorist active", "forms": ["ποιῆσαι"],
                           "count": 1}])

    def test_article_declines_across_genders(self):
        sections = self.paradigm(4)["sections"]
        tables = sections[0]["tables"]
        self.assertEqual([t["gender_label"] for t in tables],
                         ["masculine", "feminine"])
        self.assertEqual(tables[0]["rows"][0]["cells"]["S"]["forms"], ["ὁ"])
        self.assertEqual(tables[1]["rows"][0]["cells"]["S"]["forms"], ["ἡ"])

    def test_indeclinable_and_single_form(self):
        self.assertIsNone(self.paradigm(1))
        sections = self.paradigm(2)["sections"]
        (table,) = sections[0]["tables"]
        self.assertEqual(table["gender_label"], "feminine")
        self.assertEqual([r["label"] for r in table["rows"]], ["dative"])
        self.assertEqual(table["rows"][0]["cells"]["S"]["forms"], ["ἀρχῇ"])


@unittest.skipUnless(shutil.which("node"), "node is needed to run web/progress.js")
class LegacyExportTest(unittest.TestCase):
    """The exporter's file must load into the browser's progress as it was."""

    IMPORT = """
    const { Progress, MemoryStore } = require('./web/progress.js');
    let raw = '';
    process.stdin.on('data', (chunk) => { raw += chunk; }).on('end', async () => {
      const progress = new Progress(new MemoryStore());
      await progress.replace(JSON.parse(raw));
      const now = new Date('2026-08-15T12:00:00Z');
      console.log(JSON.stringify({
        status: progress.status({ total_verses: 3, total_freq: %d }, now),
        next: progress.nextCard(now).lemma_id,
        stats: progress.stats(),
      }));
    });
    """ % TOTAL_FREQ

    def test_exported_progress_imports_with_the_same_position_deck_and_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus, legacy = Path(tmp) / "lxx.db", Path(tmp) / "progress.db"
            build(corpus, CORPUS_SCHEMA)
            build(legacy, LEGACY_PROGRESS)
            exported = export(legacy, corpus)

        self.assertEqual([c["rank"] for c in exported["cards"]], [2, 3, 1])
        result = subprocess.run(
            ["node", "-e", self.IMPORT], input=json.dumps(exported), cwd=ROOT,
            capture_output=True, text=True, timeout=30, check=True,
        )
        loaded = json.loads(result.stdout)

        self.assertEqual(loaded["status"], {
            "position": 3,
            "total_verses": 3,
            "verses_read": 2,
            "known_words": 1,
            "studying": 2,
            "reviews_today": 2,
            "text_coverage": 46.51,
            "queue": {"due": 1, "new": 1, "new_available": 1, "new_limit": 5,
                      "introduced_today": 1},
        })
        self.assertEqual(loaded["next"], 1)
        self.assertEqual(loaded["stats"], [
            {"day": "2026-08-15", "reviews": 2, "again": 1},
            {"day": "2026-08-07", "reviews": 1, "again": 0},
        ])


if __name__ == "__main__":
    unittest.main()
