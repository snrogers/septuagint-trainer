# Septuagint reading trainer

A web app for learning to read the Septuagint in Koine Greek, a few words at a
time. It remembers where you are in the text, shows you the next passage with
parsing and glosses on every word, and turns the vocabulary you have not met
yet into spaced-repetition flashcards.

Your progress lives in your browser, not on the server, so one running copy
can serve anyone: each visitor gets their own reading position and deck.

The premise: you cannot read Greek *yet*. So the app front-loads the words that
do the most work. Roughly 300 lemmas cover about two thirds of all running text
in the Septuagint — learn those and the page stops being opaque.

## Running it

```bash
./run.sh
```

First run downloads the source texts (~290 MB) and builds the database; that
takes a few minutes. After that it starts in about a second and opens
<http://127.0.0.1:8710/>.

```bash
./run.sh --no-browser      # don't open a browser
PORT=9000 ./run.sh         # different port
```

Requires Python 3.10+ and nothing else. No pip install, no Node, no network
access after the initial download.

To add spoken pronunciation — optional, ~120 MB for a neural voice:

```bash
./setup-audio.sh
```

## How you use it

**Read** — shows the next *n* verses from wherever you left off. Every word is
underlined by how well you know it (red = unstudied, amber = learning, green =
known), with a small gloss underneath the ones you have not learned yet. Known
words lose their gloss, because those you should be reading, not translating.
Brenton's 1851 English sits under the whole verse, so you can check the sense
after you have read the Greek. Click any word for its full entry, parsing, and
first occurrences in the corpus.

*Make flashcards* queues every new word in the passage. *Mark read & continue*
advances your position.

**Bible** — lets you choose any stored book, chapter, and verse without moving
your training position. It uses the same interlinear word states, glosses, word
details, and audio as **Read**. This view also includes the alternate textual
traditions that do not sit in the main reading sequence.

**Drill** — the flashcards. Greek on the front, meaning on the back, four
grades (`1`–`4`, space to reveal). New words are introduced in frequency order,
capped at 12/day by default so the review load stays sane. If you have nothing
queued, start a core deck of the most frequent words here.

**Vocabulary** — the frequency-ranked core list, so you can bulk-add ahead of
your reading.

**Hearing it** — optional; run `./setup-audio.sh` once to enable. Then every
verse gets a play button, flashcards get a *Hear it* button (`s`), and
alt-clicking any word in the text speaks it as it stands there. Each entry
See [Pronunciation](#pronunciation) for what the voice does and does not do.

**Progress** — words known, verses read, and the share of all running text your
vocabulary now covers. That last number is the one that matters: it climbs fast
early on. *Export* and *Import* live here too; see [Your progress](#your-progress).

## Your progress

Everything you earn — reading position, flashcards, review history — is kept
in this browser's storage (IndexedDB) for this site, and never sent to the
server. That has three consequences:

- Clearing the site's data in your browser erases it.
- Another browser, another device, or the same app under another address
  starts fresh.
- *Export* on the Progress tab downloads it as one JSON file, and *Import*
  replaces this browser's progress with such a file. That is the backup, and
  the way to carry progress elsewhere.

Older versions kept progress on the server in `data/progress.db`. To bring it
across, convert it and import the result:

```bash
python3 scripts/export_progress.py data/progress.db > progress.json
```

## Pronunciation

Modern Greek, spoken by the voice itself. The only preprocessing is polytonic
→ monotonic, because the voice cannot read polytonic orthography — handed
ἀρχῇ it emits a stray cedilla, handed αρχή it is fine:

```
ἐν ἀρχῇ ἐποίησεν ὁ θεὸς τὸν οὐρανὸν καὶ τὴν γῆν
εν αρχή  εποίησεν ο θεός τον ουρανόν και την γην
```

Breathings and iota subscript are dropped, grave and circumflex become the
single modern tonos, and monosyllables lose their accent as modern Greek
writes them. That last rule matters for rhythm: carry the grave on τὸν
through as an accent and the voice stresses every article in the verse.

An earlier version of this feature generated IPA by hand to offer a choice of
Erasmian, reconstructed Koine, and modern. It sounded wrong, and was removed.
The voice was trained on modern Greek phonemes and on its own conventions for
stress and palatalisation; hand-written phonology drifted outside both, and
nothing about that fails loudly — out-of-vocabulary phonemes synthesise
happily as noise. Letting the voice do the job it was trained for is simpler
and correct. If you read with Erasmian pronunciation, the audio will not match
your reading, and that is a real limitation rather than an oversight.

Clips are cached on disk and the URL carries a version hash, so changing the
voice or the preprocessing invalidates the browser cache automatically. The
disk cache is capped at 1 GiB; past that, the least recently played clips are
deleted and synthesised again if anyone asks.

## How it works

```
data/raw/          downloaded sources, treated as read-only
data/lxx.db        the corpus: verses, tokens, lemmas, glosses (rebuildable)
data/voices/       the TTS model (optional)
data/audio/        synthesised clip cache (disposable, capped at 1 GiB)
lxx/               betacode.py   Beta Code -> Unicode Greek
                   morph.py      CATSS parse codes -> English
                   translit.py   Greek -> Latin, for proper names
                   monotonic.py  polytonic -> monotonic, for the voice
                   db.py         read-only corpus connection
                   tts.py        clip cache + worker management
                   tts_worker.py synthesis, runs inside .venv-tts
                   app.py        HTTP server and read-only JSON API
                   brenton.py    Brenton English verse lookup
scripts/           fetch_data.py, build_db.py, export_progress.py
web/               the UI (no build step, no dependencies)
                   progress.js   your progress: storage, SM-2 scheduling,
                                 export and import
```

The app itself uses only the standard library. Voice synthesis is the one
exception, so it lives in its own virtualenv (`.venv-tts`) behind its own
installer and talks to the app over a pipe. If it is not installed, the reader
works exactly as before and the play buttons simply do not appear.

The server holds no user state. It answers questions about the corpus, which
it opens read-only, and leaves everything you earn to `web/progress.js` in the
browser. Rebuilding `lxx.db` therefore never touches your progress.

Tests:

```bash
python3 -m unittest discover tests
node --test tests/*.test.js     # web/progress.js
```

To rebuild the corpus:

```bash
python3 scripts/build_db.py
```

### Corpus

29,161 verses in the reading sequence, 623,685 parsed words, 13,003 distinct
lemmas. Every word carries its dictionary form and a full morphological parse.

Glosses come from Dodson first (concise and written for learners), falling back
to Liddell–Scott–Jones for the many Septuagint words that never appear in the
New Testament. Where the two traditions cite a verb differently — CATSS lists
δεικνύω, the lexica list δείκνυμι — a small set of variant rules bridges the
gap. The ~3,800 Hebrew proper names that no Greek lexicon carries are labelled
and transliterated (Ιωναθαν → "proper name (Ionathan)").

Coverage: 99.4% of running text has a gloss; 7 of the 1,000 most frequent
lemmas do not.

Books that exist in two textual traditions (Joshua A/B, Judges A/B, Tobit BA/S,
and the Old Greek vs Theodotion forms of Daniel, Susanna, and Bel) are all
stored, but only one of each pair sits in the linear reading sequence so that
"advance 5 verses" is unambiguous. The others are reachable by reference.

## Sources and terms

- **Text and morphology**: CATSS/CCAT morphologically analysed Septuagint
  (Rahlfs base text), University of Pennsylvania.
  <http://ccat.sas.upenn.edu/gopher/text/religion/biblical/lxxmorph/>
  Distributed for **non-commercial study, teaching, and research**. CCAT asks
  that recipients register a signed user declaration; the terms and the
  declaration form are downloaded to `data/raw/lxxmorph/` alongside the text.
  Underlying credits: TLG (Thesaurus Linguae Graecae), UBS, and the CATSS
  project under R. Kraft and E. Tov.
- **Dodson Greek Lexicon** — public domain.
  <https://github.com/biblicalhumanities/Dodson-Greek-Lexicon>
- **Liddell–Scott–Jones**, Perseus Digital Library — CC BY-SA.
  <https://github.com/PerseusDL/lexica>
- **Brenton English Septuagint** (1851) — public domain. Whole-verse English
  under the interlinear. eBible verse-per-line text.
  <https://ebible.org/find/details.php?id=eng-Brenton&all=1>
- **Voice** (optional): Piper `el_GR-joy-medium`, MIT-licensed.
  <https://github.com/OHF-Voice/piper1-gpl> ·
  <https://huggingface.co/rhasspy/piper-voices>

This is a personal study tool. If you redistribute it or the data, mind the
CCAT terms above.
