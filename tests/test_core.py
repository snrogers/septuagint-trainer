"""Tests for the pure logic: Beta Code conversion, parse codes, transliteration.

Run with:  python3 -m unittest discover tests

These cover the parts where a silent regression would be hard to notice by
eye -- a mangled breathing mark looks fine on screen but misleads a reader.
Scheduling now runs in the browser; its tests are tests/progress.test.js.
"""

from __future__ import annotations

import http.server
import sys
import threading
import time
import unicodedata
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from lxx.betacode import beta_to_plain, strip_accents, to_unicode
from lxx.morph import describe, pos_name
from lxx.monotonic import to_monotonic
from lxx.translit import transliterate
from fetch_data import fetch_zip_member, read_all


class TestBetacode(unittest.TestCase):
    def test_gen_1_1(self):
        """The opening verse, word for word."""
        beta = ["*)EN", "A)RXH=|", "E)POI/HSEN", "O(", "QEO\\S", "TO\\N",
                "OU)RANO\\N", "KAI\\", "TH\\N", "GH=N"]
        want = "Ἐν ἀρχῇ ἐποίησεν ὁ θεὸς τὸν οὐρανὸν καὶ τὴν γῆν"
        self.assertEqual(" ".join(to_unicode(b) for b in beta), want)

    def test_diacritics(self):
        cases = {
            "A)RXH=|": "ἀρχῇ",        # smooth + circumflex + iota subscript
            "U(/DATOS": "ὕδατος",      # rough + acute
            "H)=N": "ἦν",              # smooth + circumflex
            "QEO\\S": "θεὸς",          # grave
            "R(H=MA": "ῥῆμα",          # rough breathing on rho
            "PNEU=MA": "πνεῦμα",
        }
        for beta, want in cases.items():
            with self.subTest(beta=beta):
                self.assertEqual(to_unicode(beta), want)

    def test_capitals(self):
        """'*' marks a capital; its diacritics may precede the letter."""
        self.assertEqual(to_unicode("*)ABRAA/M"), "Ἀβραάμ")
        self.assertEqual(to_unicode("*)IHSOU=S"), "Ἰησοῦς")
        self.assertEqual(to_unicode("*MWUSH=S"), "Μωυσῆς")

    def test_final_sigma(self):
        self.assertEqual(to_unicode("QEO/S"), "θεός")
        self.assertEqual(to_unicode("KO/SMOS"), "κόσμος")
        # Medial sigma stays medial.
        self.assertIn("σ", to_unicode("KO/SMOS")[:-1])

    def test_editorial_markers_stripped(self):
        self.assertEqual(to_unicode("[QEO/S]"), "θεός")

    def test_strip_accents(self):
        self.assertEqual(strip_accents("ἀρχῇ"), "αρχη")
        # Final sigma folds to medial on purpose: it is a join key, and the
        # lexica disagree with CATSS about word-final spelling.
        self.assertEqual(strip_accents("Ἰησοῦς"), "ιησουσ")
        self.assertEqual(strip_accents("θεός"), strip_accents("θεόσ"))

    def test_beta_to_plain_joins_lexicon_forms(self):
        """CATSS uppercase and Dodson lowercase must reduce to the same key."""
        self.assertEqual(beta_to_plain("QEO/S"), beta_to_plain("qeo/s"))

    def test_empty(self):
        self.assertEqual(to_unicode(""), "")


class TestMorph(unittest.TestCase):
    def test_verb_finite(self):
        self.assertEqual(describe("VAI", "AAI3S"),
                         "aorist active indicative 3rd singular")
        self.assertEqual(describe("V1", "PMI1P"),
                         "present middle indicative 1st plural")

    def test_verb_participle(self):
        self.assertEqual(describe("VA", "AAPNSM"),
                         "aorist active participle nominative singular masculine")

    def test_verb_infinitive(self):
        self.assertEqual(describe("VA", "AAN"), "aorist active infinitive")

    def test_nominal(self):
        self.assertEqual(describe("N1", "DSF"), "dative singular feminine")
        self.assertEqual(describe("N2", "GPM"), "genitive plural masculine")

    def test_indeclinable_falls_back_to_pos(self):
        self.assertEqual(describe("P", ""), "preposition")
        self.assertEqual(describe("C", ""), "conjunction")

    def test_pos_names(self):
        self.assertEqual(pos_name("RA"), "article")
        self.assertEqual(pos_name("N3M"), "noun")
        self.assertEqual(pos_name(None), "")

    def test_unknown_code_is_not_lost(self):
        """An unrecognised parse still surfaces rather than vanishing."""
        self.assertIn("ZZ", describe("N1", "ZZ"))


class TestTransliteration(unittest.TestCase):
    def test_names_are_recognisable(self):
        cases = {
            "Ιωναθαν": "Ionathan",
            "Μωαβ": "Moab",
            "Ιωαβ": "Ioab",
            "Δαυιδ": "Dauid",
        }
        for greek, want in cases.items():
            with self.subTest(greek=greek):
                self.assertEqual(transliterate(greek), want)

    def test_rough_breathing_becomes_h(self):
        self.assertTrue(transliterate("Ἑβραῖος").lower().startswith("h"))

    def test_digraphs(self):
        self.assertEqual(transliterate("ουρανος"), "ouranos")


class TestMonotonic(unittest.TestCase):
    """Polytonic -> monotonic, the one preprocessing step the voice needs.

    The voice phonemizes Greek itself and does it well; it just cannot read
    polytonic orthography. An earlier version generated IPA by hand to offer a
    choice of historical pronunciations, and it sounded wrong -- the voice only
    ever trained on modern Greek phonemes and its own stress conventions.
    """

    def test_breathings_and_iota_subscript_are_dropped(self):
        self.assertEqual(to_monotonic("ῥῆμα"), "ρήμα")
        self.assertEqual(to_monotonic("ὁ"), "ο")
        self.assertNotIn("ι", to_monotonic("ᾧ"))

    def test_accents_become_the_single_tonos(self):
        # Grave and circumflex both collapse onto the modern acute.
        self.assertEqual(to_monotonic("θεὸς"), "θεός")
        self.assertEqual(to_monotonic("ἀρχῇ"), "αρχή")

    def test_monosyllables_lose_their_accent(self):
        """Otherwise the voice stresses every article in the verse."""
        self.assertEqual(to_monotonic("τὸν"), "τον")
        self.assertEqual(to_monotonic("καὶ"), "και")
        self.assertEqual(to_monotonic("γῆν"), "γην")
        # Polysyllables keep theirs.
        self.assertEqual(to_monotonic("θεὸς"), "θεός")

    def test_diaeresis_survives(self):
        """It still distinguishes vowels that would otherwise form a digraph."""
        self.assertIn("\u0308", unicodedata.normalize("NFD", to_monotonic("ἀΐδιος")))

    def test_capitals_and_final_sigma(self):
        self.assertEqual(to_monotonic("Ἰησοῦς"), "Ιησούς")
        self.assertEqual(to_monotonic("ᾧ"), "ω")   # monosyllable, unaccented
        self.assertTrue(to_monotonic("θεὸς").endswith("ς"))

    def test_whole_verse(self):
        self.assertEqual(
            to_monotonic("ἐν ἀρχῇ ἐποίησεν ὁ θεὸς τὸν οὐρανὸν καὶ τὴν γῆν"),
            "εν αρχή εποίησεν ο θεός τον ουρανόν και την γην",
        )

    def test_output_carries_no_polytonic_marks(self):
        """Anything left over is something the voice would stumble on."""
        polytonic = {"\u0313", "\u0314", "\u0300", "\u0342", "\u0345"}
        samples = ["ἐν ἀρχῇ", "ῥῆμα", "Ἰησοῦς", "ᾧ", "υἱός", "Ἀβραάμ", "πνεῦμα"]
        for text in samples:
            with self.subTest(text=text):
                marks = set(unicodedata.normalize("NFD", to_monotonic(text)))
                self.assertEqual(marks & polytonic, set())

    def test_idempotent(self):
        once = to_monotonic("ἐν ἀρχῇ ἐποίησεν")
        self.assertEqual(to_monotonic(once), once)

    def test_empty(self):
        self.assertEqual(to_monotonic(""), "")


class TestDownloadBudget(unittest.TestCase):
    """The corpus fetch must end even when the far end never does.

    `urlopen(timeout=N)` is a SOCKET timeout: it bounds the wait for the next
    byte, not the transfer. A server trickling one byte at a time satisfies it
    forever, and this download runs inside the container build, where nothing
    else sets a limit. These tests stall a real socket rather than checking
    that a parameter was passed.
    """

    PAYLOAD = b"a complete body" * 200

    def _serve(self):
        """A server that answers /ok honestly and /slow forever."""
        payload = self.PAYLOAD

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/ok":
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                # Announce far more than will ever be sent, so the body cannot
                # complete on its own: only a deadline can end this request.
                self.send_response(200)
                self.send_header("Content-Length", "999999999")
                self.end_headers()
                try:
                    while True:
                        self.wfile.write(b"x")
                        self.wfile.flush()
                        time.sleep(0.01)
                except Exception:
                    pass

            def log_message(self, *a):
                return

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    def test_a_healthy_download_is_returned_whole(self):
        base = self._serve()
        with urllib.request.urlopen(base + "/ok", timeout=5) as resp:
            self.assertEqual(read_all(resp, budget=5), self.PAYLOAD)

    def test_a_trickling_server_hits_the_total_budget(self):
        base = self._serve()
        outcome = {}

        def attempt():
            # The socket timeout is 30s and a byte arrives every 10ms, so it
            # can never fire; only the total budget can end this.
            try:
                with urllib.request.urlopen(base + "/slow", timeout=30) as resp:
                    read_all(resp, budget=0.5)
                outcome["result"] = "returned without raising"
            except BaseException as exc:  # noqa: BLE001 - reported, not handled
                outcome["result"] = exc

        # Run it in a worker with a bounded join. The bug this guards against
        # -- read() instead of read1(), which blocks until it has a full
        # buffer -- makes the call hang for hours rather than raise, and a
        # hanging test stops an unattended suite instead of turning it red.
        start = time.monotonic()
        worker = threading.Thread(target=attempt, daemon=True)
        worker.start()
        worker.join(timeout=10)
        elapsed = time.monotonic() - start

        self.assertFalse(worker.is_alive(),
                         "read_all never returned: the total budget is not honoured")
        result = outcome.get("result")
        # Identity, so a different failure cannot pass as this one.
        self.assertIsInstance(result, TimeoutError, f"got {result!r}")
        self.assertIn("total download budget", str(result))
        # Both bounds: firing instantly would mean the budget is not honoured
        # either, and "it ended quickly" is satisfied by ending for any reason.
        self.assertGreaterEqual(elapsed, 0.5)
        self.assertLess(elapsed, 10)


class TestFetchZipMember(unittest.TestCase):
    def test_given_a_zip_when_one_member_is_requested_then_only_that_file_is_kept(self):
        import io
        import tempfile
        import zipfile

        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as zf:
            zf.writestr("keep.txt", "brenton verse text")
            zf.writestr("skip.txt", "ignore me")
        blob = payload.getvalue()

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(blob)))
                self.end_headers()
                self.wfile.write(blob)

            def log_message(self, *a):
                return

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        url = f"http://127.0.0.1:{srv.server_address[1]}/pack.zip"

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "eng-Brenton_vpl.txt"
            self.assertTrue(fetch_zip_member(url, dest, "keep.txt"))
            self.assertEqual(dest.read_text(encoding="utf-8"), "brenton verse text")
            self.assertTrue(fetch_zip_member(url, dest, "missing.txt"))
            self.assertEqual(dest.read_text(encoding="utf-8"), "brenton verse text")


if __name__ == "__main__":
    unittest.main(verbosity=2)
