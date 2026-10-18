"""Tests for direct-reference Bible reading."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lxx.app import Handler, reference_options, reference_passage


class TestReadingQueries(unittest.TestCase):
  def setUp(self):
    self.con = sqlite3.connect(":memory:")
    self.con.row_factory = sqlite3.Row
    self.con.executescript(
      """
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
        book_id     INTEGER NOT NULL,
        chapter     INTEGER NOT NULL,
        verse       INTEGER NOT NULL,
        segment     TEXT NOT NULL DEFAULT '',
        ref         TEXT NOT NULL,
        seq         INTEGER,
        text        TEXT NOT NULL,
        token_count INTEGER NOT NULL,
        brenton     TEXT
      );
      CREATE TABLE lemmas (
        id         INTEGER PRIMARY KEY,
        lemma      TEXT NOT NULL,
        gloss      TEXT,
        freq       INTEGER NOT NULL,
        rank       INTEGER
      );
      CREATE TABLE tokens (
        id       INTEGER PRIMARY KEY,
        verse_id INTEGER NOT NULL,
        idx      INTEGER NOT NULL,
        surface  TEXT NOT NULL,
        lemma_id INTEGER,
        pos      TEXT,
        parse    TEXT,
        preverb  TEXT
      );
      """
    )
    self.con.executemany(
      "INSERT INTO books (id, code, name, is_primary, sort_order, verse_count)"
      " VALUES (?, ?, ?, ?, ?, ?)",
      [
        (1, "Gen", "Genesis", 1, 0, 4),
        (2, "JudgA", "Judges (A text)", 0, 1, 1),
        (3, "Prov", "Proverbs", 1, 2, 5),
      ],
    )
    self.con.executemany(
      "INSERT INTO verses"
      " (id, book_id, chapter, verse, segment, ref, seq, text, token_count, brenton)"
      " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
      [
        (1, 1, 1, 1, "", "Gen 1:1", 1, "Ἐν ἀρχῇ", 2,
         "In the beginning God made the heaven and the earth."),
        (2, 1, 1, 2, "", "Gen 1:2", 2, "ἡ δὲ γῆ", 3, None),
        (3, 1, 2, 1, "", "Gen 2:1", 3, "καὶ συνετελέσθησαν", 2, None),
        (4, 1, 2, 2, "", "Gen 2:2", 4, "καὶ συνετέλεσεν", 2, None),
        (5, 2, 1, 1, "", "JudgA 1:1", None, "λ" * 601, 2, None),
        (6, 3, 30, 1, "", "Prov 30:1", 5, "λόγοι", 1, None),
        (7, 3, 24, 23, "", "Prov 24:23", 6, "ταῦτα", 1, None),
        (8, 3, 24, 24, "", "Prov 24:24", 7, "ὅς", 1, None),
        (9, 3, 31, 1, "", "Prov 31:1", 8, "οἱ λόγοι", 2, None),
        (10, 3, 24, 0, "", "Prov", 9, "παροιμίαι", 1, None),
      ],
    )
    self.con.execute(
      "INSERT INTO lemmas (id, lemma, gloss, freq, rank)"
      " VALUES (1, 'ἐν', 'in', 100, 1)"
    )
    self.con.execute(
      "INSERT INTO tokens"
      " (id, verse_id, idx, surface, lemma_id, pos, parse, preverb)"
      " VALUES (1, 1, 0, 'Ἐν', 1, 'P', '', NULL)"
    )

  def tearDown(self):
    self.con.close()

  def test_reference_options_list_real_chapters_and_verses(self):
    options = reference_options(self.con, "Gen", 2)

    self.assertEqual(options["book"]["name"], "Genesis")
    self.assertEqual(
      options["chapters"],
      [
        {"chapter": 1, "verse_count": 2},
        {"chapter": 2, "verse_count": 2},
      ],
    )
    self.assertEqual(options["chapter"], 2)
    self.assertEqual(
      [(verse["id"], verse["ref"]) for verse in options["verses"]],
      [(3, "Gen 2:1"), (4, "Gen 2:2")],
    )

  def test_reference_options_choose_first_chapter_by_default(self):
    options = reference_options(self.con, "Gen")

    self.assertEqual(options["chapter"], 1)
    self.assertEqual(options["verses"][0]["ref"], "Gen 1:1")

  def test_reference_options_keep_stored_order_for_late_title_rows(self):
    options = reference_options(self.con, "Prov", 24)

    self.assertEqual(
      [(verse["id"], verse["ref"]) for verse in options["verses"]],
      [(7, "Prov 24:23"), (8, "Prov 24:24"), (10, "Prov")],
    )

  def test_passage_starts_at_an_exact_reference_and_pages_in_book_order(self):
    page = reference_passage(self.con, "Gen", chapter=1, verse=1, count=2)

    self.assertEqual([verse["ref"] for verse in page["verses"]],
                     ["Gen 1:1", "Gen 1:2"])
    self.assertIsNone(page["previous"])
    self.assertEqual(page["next"], 3)
    token = page["verses"][0]["tokens"][0]
    self.assertEqual(token["lemma"], "ἐν")
    self.assertEqual(token["parse"], "preposition")
    self.assertEqual(
      page["verses"][0]["brenton"],
      "In the beginning God made the heaven and the earth.",
    )
    self.assertIsNone(page["verses"][1]["brenton"])

    next_page = reference_passage(self.con, "Gen", count=2, cursor=page["next"])
    self.assertEqual([verse["ref"] for verse in next_page["verses"]],
                     ["Gen 2:1", "Gen 2:2"])
    self.assertEqual(next_page["previous"], 3)
    self.assertIsNone(next_page["next"])

    previous_page = reference_passage(
      self.con,
      "Gen",
      count=2,
      cursor=next_page["previous"],
      direction="previous",
    )
    self.assertEqual([verse["ref"] for verse in previous_page["verses"]],
                     ["Gen 1:1", "Gen 1:2"])
    self.assertIsNone(previous_page["previous"])
    self.assertEqual(previous_page["next"], 3)

  def test_previous_page_before_an_arbitrary_jump_does_not_repeat_verses(self):
    jumped = reference_passage(self.con, "Gen", chapter=1, verse=2, count=3)

    self.assertEqual([verse["ref"] for verse in jumped["verses"]],
                     ["Gen 1:2", "Gen 2:1", "Gen 2:2"])
    previous = reference_passage(
      self.con,
      "Gen",
      count=3,
      cursor=jumped["previous"],
      direction="previous",
    )
    self.assertEqual([verse["ref"] for verse in previous["verses"]], ["Gen 1:1"])
    self.assertIsNone(previous["previous"])
    self.assertEqual(previous["next"], 2)

  def test_passage_can_read_an_alternate_text_without_a_sequence_number(self):
    page = reference_passage(self.con, "JudgA", chapter=1, verse=1, count=5)

    self.assertEqual([verse["ref"] for verse in page["verses"]], ["JudgA 1:1"])
    self.assertIsNone(page["verses"][0]["seq"])

  def test_passage_keeps_source_order_when_reference_numbers_move_backward(self):
    page = reference_passage(self.con, "Prov", chapter=30, verse=1, count=3)

    self.assertEqual(
      [verse["ref"] for verse in page["verses"]],
      ["Prov 30:1", "Prov 24:23", "Prov 24:24"],
    )
    self.assertEqual(page["next"], 9)

  def test_audio_accepts_a_long_stored_verse_but_rejects_long_free_text(self):
    class Response:
      def __init__(self):
        self.body = b""
        self.code = None
        self.error = None

      def _json(self, data, code=200):
        self.error = data
        self.code = code

      def _write_body(self, body):
        self.body = body

      def end_headers(self):
        return None

      def send_header(self, _name, _value):
        return None

      def send_response(self, code):
        self.code = code

    with tempfile.TemporaryDirectory() as tmp:
      audio = Path(tmp) / "clip.wav"
      audio.write_bytes(b"stored verse audio")
      stored = self.con.execute(
        "SELECT text FROM verses WHERE id = 5"
      ).fetchone()["text"]

      response = Response()
      with patch("lxx.app.tts.synthesize", return_value=audio) as synthesize:
        Handler._audio(response, self.con, {"verse_id": ["5"]})
      synthesize.assert_called_once_with(stored)
      self.assertEqual(response.code, 200)
      self.assertEqual(response.body, b"stored verse audio")

      rejected = Response()
      with patch("lxx.app.tts.synthesize") as synthesize:
        Handler._audio(rejected, self.con, {"text": ["λ" * 601]})
      synthesize.assert_not_called()
      self.assertEqual(rejected.code, 400)
      self.assertEqual(rejected.error, {"error": "text too long to pronounce"})

      invalid = Response()
      Handler._audio(invalid, self.con, {"verse_id": ["not-an-id"]})
      self.assertEqual(invalid.code, 400)
      self.assertEqual(invalid.error, {"error": "invalid audio reference"})

  def test_unknown_book_reference_or_cursor_returns_none(self):
    self.assertIsNone(reference_options(self.con, "Missing"))
    self.assertIsNone(
      reference_passage(self.con, "Gen", chapter=99, verse=1, count=5)
    )
    self.assertIsNone(reference_passage(self.con, "Gen", count=5, cursor=999))
    self.assertIsNone(
      reference_passage(self.con, "JudgA", count=5, cursor=1)
    )


if __name__ == "__main__":
  unittest.main(verbosity=2)
