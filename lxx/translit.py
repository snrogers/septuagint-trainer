"""Transliterate Greek to Latin letters.

Used for proper nouns. Roughly 3,800 lemmas in the Septuagint are Hebrew names
carried over into Greek (Μωυσῆς, Ιωναθαν, Αβεσσαλωμ); classical lexica do not
list them, so no gloss is available. A transliteration plus a "proper name"
tag is genuinely useful here, because the English name is usually recognisable
once the Greek is sounded out: Ιωναθαν -> Ionathan -> Jonathan.

The scheme favours readability over strict scholarly convention: no macrons,
digraphs kept familiar (ph, th, ch, ps), and diphthongs spelled as in common
English renderings of biblical names.
"""

from __future__ import annotations

import unicodedata

_SINGLE = {
    "α": "a", "β": "b", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "e",
    "θ": "th", "ι": "i", "κ": "k", "λ": "l", "μ": "m", "ν": "n", "ξ": "x",
    "ο": "o", "π": "p", "ρ": "r", "σ": "s", "ς": "s", "τ": "t", "υ": "y",
    "φ": "ph", "χ": "ch", "ψ": "ps", "ω": "o",
}

# Diphthongs, checked before single letters.
_DIGRAPHS = {
    "ου": "ou", "αυ": "au", "ευ": "eu", "ηυ": "eu",
    "αι": "ai", "ει": "ei", "οι": "oi", "υι": "ui",
    # Gamma before a velar is a nasal.
    "γγ": "ng", "γκ": "nk", "γχ": "nch", "γξ": "nx",
}

_ROUGH = "̔"  # combining rough breathing -> leading h


def transliterate(word: str) -> str:
    """Render a Greek word in Latin letters."""
    if not word:
        return ""

    decomposed = unicodedata.normalize("NFD", word)
    # A rough breathing anywhere in the first syllable aspirates the word.
    leading_h = ""
    head = decomposed[:4]
    if _ROUGH in head:
        leading_h = "h"

    bare = "".join(c for c in decomposed if not unicodedata.combining(c))
    lowered = bare.lower()

    out: list[str] = []
    i = 0
    while i < len(lowered):
        pair = lowered[i : i + 2]
        if pair in _DIGRAPHS:
            out.append(_DIGRAPHS[pair])
            i += 2
            continue
        ch = lowered[i]
        out.append(_SINGLE.get(ch, ch))
        i += 1

    result = leading_h + "".join(out)
    # Initial rho is aspirated in Greek (ῥ -> rh).
    if lowered.startswith("ρ") and not leading_h:
        result = "rh" + result[1:]
    return result.capitalize() if word[:1].isupper() else result
