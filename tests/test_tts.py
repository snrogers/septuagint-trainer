"""Tests for the speech-path text rules that need no voice model installed.

Run with:  python3 -m unittest discover tests

The defect these guard against is silent and embarrassing: the article ὁ,
the most-clicked word in the reader, was synthesised as "ómikron" -- espeak
reads a bare one-character word as the letter's name. The fix rewrites lone
vowels to homophonic spellings; these tests pin each rewrite.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lxx import tts
from lxx.tts import _VOWEL_SUBSTITUTIONS, _speakable, audio_version, clip_path


class TestSpeakable(unittest.TestCase):
    def test_article_ho_says_o_not_omicron(self):
        """Regression: the masculine article must not be spelled out."""
        self.assertEqual(_speakable("ο"), "ο\u00b7")

    def test_each_lone_vowel_gets_a_sound_alike_spelling(self):
        expected = {
            "α": "αα",
            "ε": "εε",
            "η": "η\u00b7",
            "ι": "ει",
            "ο": "ο\u00b7",
            "υ": "ει",
            "ω": "ο\u00b7",
        }
        for vowel, spoken in expected.items():
            with self.subTest(vowel=vowel):
                self.assertEqual(_speakable(vowel), spoken)

    def test_every_substitution_is_longer_than_one_character(self):
        """A one-character rewrite would hit the same espeak bug again."""
        for vowel, spoken in _VOWEL_SUBSTITUTIONS.items():
            with self.subTest(vowel=vowel):
                self.assertGreater(len(spoken), 1)

    def test_multi_character_text_passes_through(self):
        self.assertEqual(_speakable("αρχή"), "αρχή")
        self.assertEqual(_speakable("εν αρχή"), "εν αρχή")

    def test_lone_consonant_keeps_its_letter_name(self):
        """Consonants have no word reading; the letter name is honest."""
        self.assertEqual(_speakable("β"), "β")

    def test_non_greek_single_character_passes_through(self):
        self.assertEqual(_speakable("V"), "V")
        self.assertEqual(_speakable("a"), "a")


class TestClipPath(unittest.TestCase):
    def test_speakable_text_drives_the_cache_key(self):
        bare, padded = clip_path("ὁ")[0], clip_path("ο\u00b7")[0]
        self.assertEqual(bare, padded)

    def test_bare_vowel_key_differs_from_unpatched_key(self):
        """Old broken clips under the raw key must not be served."""
        import hashlib

        key = hashlib.sha1("ο".encode("utf-8")).hexdigest()
        self.assertNotEqual(clip_path("ὁ")[0].name, f"{key}.wav")

    def test_spoken_return_value_is_the_rewritten_text(self):
        self.assertEqual(clip_path("ἤ")[1], "η\u00b7")

    def test_verse_text_is_not_rewritten(self):
        _, spoken = clip_path("ἐν ἀρχῇ ἐποίησεν ὁ θεός")
        self.assertEqual(spoken, "εν αρχή εποίησεν ο θεός")


class TestAudioVersion(unittest.TestCase):
    def test_version_reflects_tts_rules_not_only_monotonic(self):
        """The browser cache busts only if tts.py changes move the hash.

        audio_version() hashes its own source file, so this can only assert
        the seed covers both files indirectly: the hash must differ from one
        computed over monotonic.py alone.
        """
        import hashlib

        root = Path(__file__).resolve().parent.parent / "lxx"
        monotonic_only = hashlib.sha1(
            (root / "monotonic.py").read_bytes()
        ).hexdigest()[:12]
        self.assertNotEqual(audio_version(), monotonic_only)


class TestCacheBound(unittest.TestCase):
    """Every clip anyone asks for is kept, so without a cap a public instance
    fills the volume the corpus lives on."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cache = Path(tmp.name)
        for name, value in (
            ("CACHE_DIR", self.cache),
            ("CACHE_MAX_BYTES", 1000),
            ("_cache_bytes", None),
        ):
            patcher = patch.object(tts, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def clip(self, key, size, age):
        path = self.cache / key[:2] / f"{key}.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
        then = time.time() - age
        os.utime(path, (then, then))
        return path

    def test_past_the_cap_the_least_recently_played_clip_goes(self):
        oldest = self.clip("aa1", 400, age=300)
        older = self.clip("bb1", 400, age=200)
        newest = self.clip("cc1", 400, age=0)
        tts._store(newest)
        # 1200 bytes against a 1000 cap: evicting down to 900 takes only the
        # oldest clip.
        self.assertFalse(oldest.exists())
        self.assertTrue(older.exists())
        self.assertTrue(newest.exists())

    def test_under_the_cap_nothing_is_evicted(self):
        first = self.clip("aa1", 300, age=100)
        second = self.clip("bb1", 300, age=0)
        tts._store(second)
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())

    def test_clips_stored_one_by_one_are_counted_until_the_cap(self):
        first = self.clip("aa1", 400, age=300)
        tts._store(first)
        second = self.clip("bb1", 400, age=200)
        tts._store(second)
        self.assertTrue(first.exists())
        third = self.clip("cc1", 400, age=0)
        tts._store(third)
        self.assertFalse(first.exists())
        self.assertTrue(second.exists())

    def test_a_replayed_clip_counts_as_recently_played(self):
        replayed = self.clip("aa1", 400, age=300)
        idle = self.clip("bb1", 400, age=200)
        with patch.object(tts, "available", return_value=True), \
             patch.object(tts, "clip_path", return_value=(replayed, "λόγος")):
            self.assertEqual(tts.synthesize("λόγος"), replayed)
        tts._store(self.clip("cc1", 400, age=0))
        self.assertTrue(replayed.exists())
        self.assertFalse(idle.exists())


if __name__ == "__main__":
    unittest.main()
