r"""Beta Code -> polytonic Unicode Greek.

CCAT/CATSS and the Perseus lexica both store Greek as Beta Code, an ASCII
transliteration. This module converts it to proper Unicode so the reader can
display real Greek, and produces the stripped-down "plain" forms used to join
records across sources that disagree about accentuation.

Beta Code conventions handled here:

    letters     A B G D E Z H Q I K L M N C O P R S T U F X Y W
    *           the following letter is a capital  (*)ABRAA/M)
    )  (        smooth / rough breathing
    /  \  =     acute / grave / circumflex
    |           iota subscript
    +           diaeresis

Diacritics follow the letter they belong to. Conversion goes through combining
marks plus NFC rather than a lookup table of precomposed characters, so every
legal combination composes correctly instead of needing ~270 table entries.
"""

from __future__ import annotations

import re
import unicodedata

# Beta Code letter -> lowercase Greek. Uppercase is derived via str.upper(),
# which is correct for every letter here.
_LETTERS = {
    "A": "α",  # alpha
    "B": "β",  # beta
    "G": "γ",  # gamma
    "D": "δ",  # delta
    "E": "ε",  # epsilon
    "Z": "ζ",  # zeta
    "H": "η",  # eta
    "Q": "θ",  # theta
    "I": "ι",  # iota
    "K": "κ",  # kappa
    "L": "λ",  # lambda
    "M": "μ",  # mu
    "N": "ν",  # nu
    "C": "ξ",  # xi
    "O": "ο",  # omicron
    "P": "π",  # pi
    "R": "ρ",  # rho
    "S": "σ",  # sigma (final form applied later)
    "T": "τ",  # tau
    "U": "υ",  # upsilon
    "F": "φ",  # phi
    "X": "χ",  # chi
    "Y": "ψ",  # psi
    "W": "ω",  # omega
}

_SMOOTH = "̓"
_ROUGH = "̔"
_ACUTE = "́"
_GRAVE = "̀"
_CIRCUMFLEX = "͂"
_DIAERESIS = "̈"
_IOTA_SUB = "ͅ"

_BREATHINGS = {")": _SMOOTH, "(": _ROUGH}
_ACCENTS = {"/": _ACUTE, "\\": _GRAVE, "=": _CIRCUMFLEX}

_SIGMA = "σ"
_FINAL_SIGMA = "ς"

# Editorial apparatus markers used in the CATSS text. They carry textual-
# criticism information that is not useful for a reading trainer, so surface
# forms are cleaned of them.
_EDITORIAL = re.compile(r"[\[\]{}<>%@^#~]")

# Anything that is neither a Beta Code letter nor a recognised diacritic.
_KNOWN = set(_LETTERS) | set(_BREATHINGS) | set(_ACCENTS) | {"|", "+", "*"}


def to_unicode(beta: str, *, final_sigma: bool = True) -> str:
    """Convert a Beta Code string to polytonic Unicode Greek.

    Combining marks are emitted in the order Unicode expects for composition:
    breathing or diaeresis, then accent, then iota subscript. Breathings and
    accents share a canonical combining class, so NFC will not reorder them for
    us -- emitting them correctly here is what makes composition work.
    """
    if not beta:
        return ""

    beta = _EDITORIAL.sub("", beta.strip())
    out: list[str] = []
    i = 0
    n = len(beta)

    while i < n:
        ch = beta[i]

        # '*' marks the next letter as a capital. In CATSS the diacritics of a
        # capital may be written before the letter (*)ABRAA/M) or after it, so
        # collect any that appear in between.
        if ch == "*":
            i += 1
            pre = _read_marks(beta, i)
            i += len(pre)
            if i >= n:
                break
            letter = _LETTERS.get(beta[i].upper())
            if letter is None:
                i += 1
                continue
            i += 1
            post = _read_marks(beta, i)
            i += len(post)
            out.append(_compose(letter.upper(), pre + post))
            continue

        upper = ch.upper()
        if upper in _LETTERS:
            letter = _LETTERS[upper]
            i += 1
            marks = _read_marks(beta, i)
            i += len(marks)
            out.append(_compose(letter, marks))
            continue

        # Unknown/punctuation passes through untouched.
        if ch not in _KNOWN:
            out.append(ch)
        i += 1

    text = "".join(out)
    if final_sigma:
        text = _apply_final_sigma(text)
    return unicodedata.normalize("NFC", text)


def _read_marks(beta: str, i: int) -> list[str]:
    """Collect the run of diacritic characters starting at index i."""
    marks = []
    while i < len(beta) and beta[i] in ")(/\\=|+":
        marks.append(beta[i])
        i += 1
    return marks


def _compose(letter: str, marks: list[str]) -> str:
    """Attach diacritics to a base letter in canonical composition order."""
    breathing = ""
    accent = ""
    iota = ""
    diaeresis = ""
    for m in marks:
        if m in _BREATHINGS:
            breathing = _BREATHINGS[m]
        elif m in _ACCENTS:
            accent = _ACCENTS[m]
        elif m == "|":
            iota = _IOTA_SUB
        elif m == "+":
            diaeresis = _DIAERESIS
    # Diaeresis and breathing are mutually exclusive in practice; both are
    # placed ahead of the accent, and iota subscript always comes last.
    return unicodedata.normalize(
        "NFC", letter + diaeresis + breathing + accent + iota
    )


def _apply_final_sigma(text: str) -> str:
    """Rewrite word-final sigma as ς.

    Beta Code writes both forms as S. A sigma is final when the next character
    is not a Greek letter, so trailing punctuation is handled correctly.
    """
    chars = list(text)
    for idx, ch in enumerate(chars):
        if ch != _SIGMA:
            continue
        nxt = idx + 1
        # Skip past combining marks belonging to this position.
        while nxt < len(chars) and unicodedata.combining(chars[nxt]):
            nxt += 1
        if nxt >= len(chars) or not _is_greek_letter(chars[nxt]):
            chars[idx] = _FINAL_SIGMA
    return "".join(chars)


def _is_greek_letter(ch: str) -> bool:
    decomposed = unicodedata.normalize("NFD", ch)
    return bool(decomposed) and "Ͱ" <= decomposed[0] <= "Ͽ"


def strip_accents(text: str) -> str:
    """Return lowercase Greek with all diacritics removed.

    Used as the join key between corpora that accent differently, and for
    accent-insensitive search. Final sigma is normalised to medial sigma so
    that word-position differences do not split a lemma in two.
    """
    decomposed = unicodedata.normalize("NFD", text.lower())
    bare = "".join(c for c in decomposed if not unicodedata.combining(c))
    return unicodedata.normalize("NFC", bare).replace(_FINAL_SIGMA, _SIGMA)


def beta_to_plain(beta: str) -> str:
    """Beta Code straight to a stripped lowercase Unicode join key."""
    return strip_accents(to_unicode(beta, final_sigma=False))
