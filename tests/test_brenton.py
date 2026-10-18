"""Tests for Brenton's English verse text.

The Greek interlinear is the reading text. Brenton is the whole-verse
English that sits under it. These tests lock the parse, the book and
verse matching, and the rule that a missing line must stay missing.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lxx.app import reference_passage, verse_range
from lxx.brenton import (
    BRENTON_BOOK,
    attach_brenton,
    clean_verse_text,
    ensure_brenton_column,
    load_brenton_index,
    lookup_brenton,
    parse_vpl,
    parse_vpl_line,
    remap_reference,
)


SAMPLE_VPL = """\
GEN 1:1 In the beginning God made the heaven and the earth.
GEN 1:2 But the earth was unsightly and unfurnished.
GEN 31:50a And Laban said to Jacob, Behold, this heap.
ESG 1:1 [In the second year of the reign of Artaxerxes the great king.
ESG 1:1b a Jew dwelling in the city Susa, a great man.
SIR 1:1 [The Prologue to the Wisdom of] Jesus [the son of] Sirach Whereas many and great things have been delivered unto us by the law.
SIR 1:1a but also they that desire to learn be able to profit them which are without.
SIR 1:1g being prepared before in manners to live after the law.
SIR 1:1h All wisdom [cometh] from the Lord, and is with him for ever.
SIR 1:2 Who can number the sand of the sea.
EPJ 1:1 A copy of an epistle, which Jeremy sent unto them.
EPJ 1:2 For the sins that ye have committed before God.
EPJ 1:73 Better therefore is the just man that hath no idols.
PRO 24:22f [My] son, reverence my words, and receive them, and repent.
PRO 24:22g For I am the most simple of all men.
PRO 24:35 The horse-leech had three dearly-beloved daughters.
PRO 24:53 Milk out milk, and there shall be butter.
PRO 24:54 My words have been spoken by God—the oracular answer of a king.
PRO 24:62 Open thy mouth and judge justly.
PRO 25:1 These are the miscellaneous instructions of Solomon.
PRO 30:1 
EZR 1:1 Now in the first year of Cyrus king of the Persians.
EZR 11:1 The words of Neemias the son of Chelcia.
PRM 1:1 O Lord Almighty, the God of our fathers Abraham and Isaac and Jacob.
BEL 1:15 And it came to pass on the morrow.
BEL 1:16 The priests of Bel went in by a private door.
BEL 1:17 And they ate the food.
LAM 1:1 [][]Aleph. How does the city that was filled with people sit solitary!
"""


def sample_index():
    return load_brenton_index(SAMPLE_VPL.splitlines())


class TestParseVpl(unittest.TestCase):
    def test_given_a_normal_line_when_parsed_then_book_chapter_verse_and_text_are_kept(self):
        self.assertEqual(
            parse_vpl_line(
                "GEN 1:1 In the beginning God made the heaven and the earth."
            ),
            ("GEN", 1, 1, "", "In the beginning God made the heaven and the earth."),
        )

    def test_given_a_lettered_verse_when_parsed_then_the_segment_is_kept(self):
        self.assertEqual(
            parse_vpl_line("SIR 1:1h All wisdom [cometh] from the Lord."),
            ("SIR", 1, 1, "h", "All wisdom [cometh] from the Lord."),
        )

    def test_given_blank_or_broken_lines_when_parsed_then_they_are_dropped(self):
        self.assertIsNone(parse_vpl_line(""))
        self.assertIsNone(parse_vpl_line("   "))
        self.assertIsNone(parse_vpl_line("not a verse"))
        self.assertIsNone(parse_vpl_line("GEN missing-colon text"))

    def test_given_empty_bracket_pairs_when_cleaned_then_they_are_removed(self):
        self.assertEqual(
            clean_verse_text("[][]Aleph. How does the city sit solitary!  "),
            "Aleph. How does the city sit solitary!",
        )

    def test_given_a_blank_translation_when_cleaned_then_the_result_is_empty(self):
        self.assertEqual(clean_verse_text("   "), "")

    def test_given_a_vpl_block_when_loaded_then_lettered_and_plain_keys_are_both_stored(self):
        index = sample_index()
        self.assertEqual(
            index[("GEN", 1, 1, "")],
            "In the beginning God made the heaven and the earth.",
        )
        self.assertEqual(
            index[("SIR", 1, 1, "h")],
            "All wisdom [cometh] from the Lord, and is with him for ever.",
        )
        self.assertNotIn(("PRO", 30, 1, ""), index)

    def test_given_a_file_when_parse_vpl_runs_then_it_yields_the_same_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "eng-Brenton_vpl.txt"
            path.write_text(SAMPLE_VPL, encoding="utf-8")
            rows = list(parse_vpl(path))
        self.assertEqual(rows[0][:4], ("GEN", 1, 1, ""))
        self.assertTrue(all(row[4] for row in rows))


class TestRemapAndLookup(unittest.TestCase):
    def test_given_a_plain_genesis_verse_when_looked_up_then_brenton_is_returned(self):
        self.assertEqual(
            lookup_brenton(sample_index(), "Gen", 1, 1, "", "Gen 1:1"),
            "In the beginning God made the heaven and the earth.",
        )

    def test_given_esther_addition_1a_when_looked_up_then_brenton_1_1_is_used(self):
        self.assertEqual(
            lookup_brenton(sample_index(), "Esth", 1, 1, "a", "Esth 1:1a"),
            "[In the second year of the reign of Artaxerxes the great king.",
        )

    def test_given_sirach_1_1_when_looked_up_then_the_h_segment_is_used(self):
        self.assertEqual(
            lookup_brenton(sample_index(), "Sir", 1, 1, "", "Sir 1:1"),
            "All wisdom [cometh] from the Lord, and is with him for ever.",
        )

    def test_given_sirach_1_2_when_looked_up_then_the_plain_verse_is_used(self):
        self.assertEqual(
            lookup_brenton(sample_index(), "Sir", 1, 2, "", "Sir 1:2"),
            "Who can number the sand of the sea.",
        )

    def test_given_the_sirach_prologue_when_looked_up_then_only_the_first_row_gets_the_joined_english(self):
        first = lookup_brenton(sample_index(), "Sir", 0, 1, "", "Sir Prolog:1")
        later = lookup_brenton(sample_index(), "Sir", 0, 2, "", "Sir Prolog:2")
        self.assertIn("Whereas many and great things", first)
        self.assertIn("being prepared before", first)
        self.assertNotIn("All wisdom", first)
        self.assertIsNone(later)

    def test_given_epistle_of_jeremiah_when_looked_up_then_catss_verse_zero_is_brenton_1(self):
        self.assertEqual(
            lookup_brenton(sample_index(), "EpJer", 1, 0, "", "EpJer"),
            "A copy of an epistle, which Jeremy sent unto them.",
        )
        self.assertEqual(
            lookup_brenton(sample_index(), "EpJer", 1, 1, "", "EpJer 1"),
            "For the sins that ye have committed before God.",
        )
        self.assertEqual(
            lookup_brenton(sample_index(), "EpJer", 1, 72, "", "EpJer 72"),
            "Better therefore is the just man that hath no idols.",
        )

    def test_given_proverbs_30_when_looked_up_then_brenton_24_lettered_and_shifted_verses_are_used(self):
        self.assertEqual(
            lookup_brenton(sample_index(), "Prov", 30, 1, "", "Prov 30:1"),
            "[My] son, reverence my words, and receive them, and repent.",
        )
        self.assertEqual(
            lookup_brenton(sample_index(), "Prov", 30, 2, "", "Prov 30:2"),
            "For I am the most simple of all men.",
        )
        self.assertEqual(
            lookup_brenton(sample_index(), "Prov", 30, 15, "", "Prov 30:15"),
            "The horse-leech had three dearly-beloved daughters.",
        )
        self.assertEqual(
            lookup_brenton(sample_index(), "Prov", 30, 33, "", "Prov 30:33"),
            "Milk out milk, and there shall be butter.",
        )

    def test_given_proverbs_31_opening_when_looked_up_then_brenton_24_is_used(self):
        self.assertEqual(
            lookup_brenton(sample_index(), "Prov", 31, 1, "", "Prov 31:1"),
            "My words have been spoken by God—the oracular answer of a king.",
        )
        self.assertEqual(
            lookup_brenton(sample_index(), "Prov", 31, 9, "", "Prov 31:9"),
            "Open thy mouth and judge justly.",
        )

    def test_given_proverbs_32_when_looked_up_then_brenton_chapter_25_is_used(self):
        self.assertEqual(
            lookup_brenton(sample_index(), "Prov", 32, 1, "", "Prov 32:1"),
            "These are the miscellaneous instructions of Solomon.",
        )

    def test_given_2_esdras_when_looked_up_then_ezra_lxx_numbering_is_used(self):
        self.assertEqual(
            lookup_brenton(sample_index(), "2Esdr", 1, 1, "", "2Esdr 1:1"),
            "Now in the first year of Cyrus king of the Persians.",
        )
        self.assertEqual(
            lookup_brenton(sample_index(), "2Esdr", 11, 1, "", "2Esdr 11:1"),
            "The words of Neemias the son of Chelcia.",
        )

    def test_given_ode_12_when_looked_up_then_the_prayer_of_manasses_is_used(self):
        self.assertEqual(
            lookup_brenton(sample_index(), "Od", 12, 1, "", "Od 12:1"),
            "O Lord Almighty, the God of our fathers Abraham and Isaac and Jacob.",
        )

    def test_given_a_merged_bel_verse_when_looked_up_then_the_range_is_joined(self):
        self.assertEqual(
            lookup_brenton(sample_index(), "Bel", 1, 15, "", "Bel 15-17"),
            "And it came to pass on the morrow. "
            "The priests of Bel went in by a private door. "
            "And they ate the food.",
        )

    def test_given_a_book_brenton_does_not_translate_when_looked_up_then_none_is_returned(self):
        self.assertIsNone(
            lookup_brenton(sample_index(), "PsSol", 1, 1, "", "PsSol 1:1")
        )
        self.assertIsNone(
            lookup_brenton(sample_index(), "Od", 1, 1, "", "Od 1:1")
        )

    def test_given_a_missing_verse_when_looked_up_then_none_is_returned(self):
        self.assertIsNone(
            lookup_brenton(sample_index(), "Gen", 1, 99, "", "Gen 1:99")
        )

    def test_given_every_stored_book_when_the_map_is_read_then_psalms_of_solomon_are_absent(self):
        self.assertIn("Gen", BRENTON_BOOK)
        self.assertIn("2Esdr", BRENTON_BOOK)
        self.assertNotIn("PsSol", BRENTON_BOOK)
        self.assertNotIn("Od", BRENTON_BOOK)

    def test_given_proverbs_remap_when_applied_then_the_lxx_shift_is_stable(self):
        self.assertEqual(remap_reference("Prov", 30, 1, ""), ("PRO", 24, 22, "f"))
        self.assertEqual(remap_reference("Prov", 30, 14, ""), ("PRO", 24, 22, "t"))
        self.assertEqual(remap_reference("Prov", 36, 4, ""), ("PRO", 29, 4, ""))
        self.assertEqual(remap_reference("Sir", 1, 1, ""), ("SIR", 1, 1, "h"))
        self.assertEqual(remap_reference("Esth", 1, 1, "a"), ("ESG", 1, 1, ""))
        self.assertEqual(remap_reference("EpJer", 1, 0, ""), ("EPJ", 1, 1, ""))
        self.assertEqual(remap_reference("Od", 12, 3, ""), ("PRM", 1, 3, ""))


class TestAttachBrenton(unittest.TestCase):
    def test_given_an_existing_corpus_when_brenton_is_attached_then_matched_verses_are_filled(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript(
            """
            CREATE TABLE books (
              id INTEGER PRIMARY KEY, code TEXT, name TEXT
            );
            CREATE TABLE verses (
              id INTEGER PRIMARY KEY,
              book_id INTEGER,
              chapter INTEGER,
              verse INTEGER,
              segment TEXT DEFAULT '',
              ref TEXT,
              text TEXT
            );
            """
        )
        con.execute("INSERT INTO books (id, code, name) VALUES (1, 'Gen', 'Genesis')")
        con.execute("INSERT INTO books (id, code, name) VALUES (2, 'PsSol', 'Psalms of Solomon')")
        con.executemany(
            "INSERT INTO verses (id, book_id, chapter, verse, segment, ref, text)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (1, 1, 1, 1, "", "Gen 1:1", "Ἐν ἀρχῇ"),
                (2, 1, 1, 2, "", "Gen 1:2", "ἡ δὲ γῆ"),
                (3, 2, 1, 1, "", "PsSol 1:1", "ἐβόησα"),
            ],
        )
        ensure_brenton_column(con)
        stats = attach_brenton(con, SAMPLE_VPL.splitlines())

        self.assertEqual(
            con.execute("SELECT brenton FROM verses WHERE id = 1").fetchone()[0],
            "In the beginning God made the heaven and the earth.",
        )
        self.assertEqual(
            con.execute("SELECT brenton FROM verses WHERE id = 2").fetchone()[0],
            "But the earth was unsightly and unfurnished.",
        )
        self.assertIsNone(
            con.execute("SELECT brenton FROM verses WHERE id = 3").fetchone()[0]
        )
        self.assertEqual(stats["matched"], 2)
        self.assertEqual(stats["missing"], 1)

    def test_given_a_schema_without_the_column_when_ensured_then_the_column_is_added_once(self):
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE verses (id INTEGER PRIMARY KEY, text TEXT)")
        ensure_brenton_column(con)
        ensure_brenton_column(con)
        cols = [row[1] for row in con.execute("PRAGMA table_info(verses)")]
        self.assertEqual(cols.count("brenton"), 1)


class TestVersePayload(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.executescript(
            """
            CREATE TABLE books (
              id INTEGER PRIMARY KEY,
              code TEXT NOT NULL UNIQUE,
              name TEXT NOT NULL,
              is_primary INTEGER NOT NULL,
              sort_order INTEGER NOT NULL,
              verse_count INTEGER NOT NULL DEFAULT 0,
              token_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE verses (
              id INTEGER PRIMARY KEY,
              book_id INTEGER NOT NULL,
              chapter INTEGER NOT NULL,
              verse INTEGER NOT NULL,
              segment TEXT NOT NULL DEFAULT '',
              ref TEXT NOT NULL,
              seq INTEGER,
              text TEXT NOT NULL,
              token_count INTEGER NOT NULL,
              brenton TEXT
            );
            CREATE TABLE lemmas (
              id INTEGER PRIMARY KEY,
              lemma TEXT NOT NULL,
              gloss TEXT,
              freq INTEGER NOT NULL,
              rank INTEGER
            );
            CREATE TABLE tokens (
              id INTEGER PRIMARY KEY,
              verse_id INTEGER NOT NULL,
              idx INTEGER NOT NULL,
              surface TEXT NOT NULL,
              lemma_id INTEGER,
              pos TEXT,
              parse TEXT,
              preverb TEXT
            );
            ATTACH DATABASE ':memory:' AS p;
            CREATE TABLE p.cards (
              lemma_id INTEGER PRIMARY KEY,
              state TEXT,
              due TEXT
            );
            """
        )
        self.con.execute(
            "INSERT INTO books (id, code, name, is_primary, sort_order, verse_count)"
            " VALUES (1, 'Gen', 'Genesis', 1, 0, 1)"
        )
        self.con.execute(
            "INSERT INTO verses"
            " (id, book_id, chapter, verse, segment, ref, seq, text, token_count, brenton)"
            " VALUES (1, 1, 1, 1, '', 'Gen 1:1', 1, 'Ἐν ἀρχῇ', 1,"
            " 'In the beginning God made the heaven and the earth.')"
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

    def test_given_a_stored_translation_when_a_reading_range_is_built_then_brenton_is_on_the_verse(self):
        verses = verse_range(self.con, 1, 1)
        self.assertEqual(
            verses[0]["brenton"],
            "In the beginning God made the heaven and the earth.",
        )

    def test_given_a_stored_translation_when_a_bible_passage_is_built_then_brenton_is_on_the_verse(self):
        page = reference_passage(self.con, "Gen", chapter=1, verse=1, count=1)
        self.assertEqual(
            page["verses"][0]["brenton"],
            "In the beginning God made the heaven and the earth.",
        )


class TestReadingPageShowsBrenton(unittest.TestCase):
    def test_given_the_reader_ui_when_a_verse_is_drawn_then_brenton_is_rendered_under_the_greek(self):
        script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
        self.assertIn("v.brenton", script)
        self.assertIn("verse-brenton", script)
        self.assertIn(".verse-brenton", css)


if __name__ == "__main__":
    unittest.main(verbosity=2)
