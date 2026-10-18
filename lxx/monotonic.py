"""Polytonic Greek -> monotonic, so the voice can read it.

This is the only preprocessing the speech path needs. The voice phonemizes
Greek itself and does it well; all it cannot cope with is polytonic
orthography, which is a nineteenth-century-and-earlier convention the modern
language dropped in 1982. Handed ἀρχῇ it emits a stray cedilla and mangles the
vowel, but handed αρχή it is perfectly happy.

The conversion is the same one modern Greek editions apply:

    breathings          dropped   (ῥῆμα  -> ρήμα)
    iota subscript      dropped   (ᾧ      -> ώ)
    grave, circumflex   become the single modern tonos
    diaeresis           kept, since it still distinguishes ἀΐδιος
    monosyllables       unaccented (τὸν -> τον, καὶ -> και)

That last rule is not cosmetic. Polytonic marks the article τὸν with a grave;
carry that through as a tonos and the voice stresses every article in the
verse -- "TON ouraNON KAI TIN GIN". Modern Greek writes monosyllabic clitics
bare, and doing the same restores a normal reading rhythm. A monosyllable is
still audibly stressed where it deserves to be, since it is the only syllable
there is.
"""

from __future__ import annotations

import unicodedata

_TONOS = "́"          # U+0301, the one accent monotonic Greek uses
_DIAERESIS = "̈"      # U+0308, still meaningful
_ACCENTS = {"́", "̀", "͂"}   # acute, grave, circumflex

_VOWELS = set("αεηιουω")
#: Vowel pairs that form one syllable nucleus.
_DIGRAPHS = {"αι", "ει", "οι", "υι", "ου", "αυ", "ευ", "ηυ"}


def _syllables(word: str) -> int:
    """Count vowel nuclei, treating the digraphs as one each."""
    bare = "".join(
        c for c in unicodedata.normalize("NFD", word.lower())
        if not unicodedata.combining(c)
    )
    count = 0
    i = 0
    while i < len(bare):
        if bare[i] in _VOWELS:
            if bare[i:i + 2] in _DIGRAPHS:
                i += 2
            else:
                i += 1
            count += 1
        else:
            i += 1
    return count


def to_monotonic(text: str) -> str:
    """Strip polytonic diacritics down to what modern Greek writes."""
    return " ".join(_word(w) for w in text.split())


def _word(word: str) -> str:
    out: list[str] = []
    for ch in unicodedata.normalize("NFD", word):
        if unicodedata.combining(ch):
            if ch in _ACCENTS:
                out.append(_TONOS)
            elif ch == _DIAERESIS:
                out.append(ch)
            # Breathings and iota subscript carry no modern pronunciation.
            continue
        out.append(ch)

    if _syllables(word) <= 1:
        out = [c for c in out if c != _TONOS]
    return unicodedata.normalize("NFC", "".join(out))
