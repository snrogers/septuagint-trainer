"""Render CATSS part-of-speech and parse codes as readable English.

The corpus stores compact codes: a token tagged ``VAI AAI3S`` is an aorist
active indicative 3rd singular. Learners need the expansion, so this turns the
codes into prose at display time rather than baking strings into the database.

Code layout (CATSS/CCAT "Morph-Coding"):
    nominals    case + number + gender (+ degree)      e.g. NSM, GPF, ASNC
    finite verb tense + voice + mood + person + number e.g. AAI3S, PMI1P
    participle  tense + voice + P + case/number/gender e.g. AAPNSM
    infinitive  tense + voice + N                      e.g. AAN
"""

from __future__ import annotations

POS_NAMES = {
    "RA": "article",
    "RD": "demonstrative pronoun",
    "RI": "interrogative/indefinite pronoun",
    "RP": "personal pronoun",
    "RR": "relative pronoun",
    "N": "noun",
    "A": "adjective",
    "V": "verb",
    "P": "preposition",
    "C": "conjunction",
    "X": "particle",
    "I": "interjection",
    "D": "adverb",
    "M": "indeclinable numeral",
}

CASE = {"N": "nominative", "G": "genitive", "D": "dative",
        "A": "accusative", "V": "vocative"}
NUMBER = {"S": "singular", "P": "plural", "D": "dual"}
GENDER = {"M": "masculine", "F": "feminine", "N": "neuter"}
TENSE = {"P": "present", "I": "imperfect", "F": "future", "A": "aorist",
         "X": "perfect", "Y": "pluperfect"}
VOICE = {"A": "active", "M": "middle", "P": "passive"}
MOOD = {"I": "indicative", "D": "imperative", "S": "subjunctive",
        "O": "optative", "N": "infinitive", "P": "participle"}
DEGREE = {"C": "comparative", "S": "superlative"}
PERSON = {"1": "1st", "2": "2nd", "3": "3rd"}


def pos_name(pos: str | None) -> str:
    """Human-readable part of speech for a CATSS POS code."""
    if not pos:
        return ""
    pos = pos.strip()
    # Two-letter pronoun/article codes are looked up whole; everything else is
    # a letter plus a declension/conjugation class digit (N1, A1B, V1, ...).
    return POS_NAMES.get(pos, POS_NAMES.get(pos[:2], POS_NAMES.get(pos[:1], "")))


def describe(pos: str | None, parse: str | None) -> str:
    """Expand a parse code into English, e.g. 'aorist active indicative 3rd singular'."""
    pos = (pos or "").strip()
    code = (parse or "").strip()
    base = pos_name(pos)

    if not code:
        return base

    if pos.startswith("V"):
        parts = _describe_verb(code)
        return " ".join(parts) if parts else f"{base} {code}".strip()

    parts = _describe_nominal(code)
    if not parts:
        return f"{base} {code}".strip()
    # Distinguish an article/pronoun from a plain noun in the readout.
    return " ".join(parts)


def _describe_verb(code: str) -> list[str]:
    if len(code) < 3:
        return []
    tense, voice, mood = code[0], code[1], code[2]
    if tense not in TENSE or voice not in VOICE or mood not in MOOD:
        return []
    parts = [TENSE[tense], VOICE[voice], MOOD[mood]]
    rest = code[3:]

    # Finite forms carry person + number.
    if len(rest) >= 2 and rest[0] in PERSON and rest[1] in NUMBER:
        parts += [PERSON[rest[0]], NUMBER[rest[1]]]
        return parts

    # Participles decline: case + number + gender.
    if rest:
        parts += _describe_nominal(rest)
    return parts


def _describe_nominal(code: str) -> list[str]:
    parts: list[str] = []
    if len(code) >= 1 and code[0] in CASE:
        parts.append(CASE[code[0]])
    if len(code) >= 2 and code[1] in NUMBER:
        parts.append(NUMBER[code[1]])
    if len(code) >= 3 and code[2] in GENDER:
        parts.append(GENDER[code[2]])
    if len(code) >= 4 and code[3] in DEGREE:
        parts.append(DEGREE[code[3]])
    return parts
