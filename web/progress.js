/* Septuagint reading trainer — the learner's progress, kept in this browser.

   Everything a learner earns lives here and never on the server: the reading
   position, one flashcard per queued lemma, and a log of every review. The
   server only answers questions about the corpus, so one instance serves any
   number of people without their progress ever meeting.

   Storage is IndexedDB. A deck growing toward all 13,003 lemmas, plus years of
   review history, would outgrow localStorage's few megabytes. Progress keeps
   everything in memory and writes each change through, so reads stay
   synchronous.

   Scheduling is SM-2 with learning steps. A card is one lemma and moves
   through three states:

       new       queued but never studied
       learning  answered at least once but not yet graduated; repeats within
                 the session on short steps until answered well twice
       review    graduated; intervals grow by the card's ease factor

   Grades follow the familiar four-button scheme:

       0 again   forgot it        -> back to learning, ease penalised
       1 hard    recalled slowly  -> interval grows slightly, ease down a little
       2 good    recalled         -> interval * ease
       3 easy    instant          -> interval * ease * bonus, ease up

   Choosing SM-2 over a newer algorithm (FSRS) is deliberate: it needs no
   pre-trained parameters, behaves sensibly from the very first review, and is
   easy to reason about.

   "Today" is the UTC calendar day throughout: the daily new-card limit and the
   per-day review counts both roll over at UTC midnight.

   Loaded by the page as a classic script (window.LxxProgress) and by the
   tests as a CommonJS module. */

'use strict';

(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.LxxProgress = api;
}(typeof self !== 'undefined' ? self : this, () => {
  const AGAIN = 0, HARD = 1, GOOD = 2, EASY = 3;

  // Minutes between repetitions while a card is still being learned.
  // Answering "good" advances one step; finishing the last step graduates it.
  const LEARNING_STEPS_MIN = [1, 10];

  const GRADUATING_INTERVAL_DAYS = 1;
  const EASY_INTERVAL_DAYS = 4;
  const RELEARN_STEP_MIN = 10;

  const MIN_EASE = 1.3;
  const EASY_BONUS = 1.3;
  const HARD_FACTOR = 1.2;
  const LAPSE_INTERVAL_FACTOR = 0.5;
  const MAX_INTERVAL_DAYS = 365 * 5;

  const MINUTE_MS = 60 * 1000;
  const DAY_MS = 24 * 60 * MINUTE_MS;

  const DEFAULT_SETTINGS = { daily_new_limit: 12 };
  const STATES = ['new', 'learning', 'review'];

  // Marks an exported file. Bump the version when the shape changes, and keep
  // reading the old one.
  const FORMAT = 'septuagint-progress';
  const FORMAT_VERSION = 1;

  /* Timestamps are ISO-8601 UTC to the second, so their first ten characters
     are the UTC calendar day. */
  const stamp = (date) => date.toISOString().slice(0, 19) + 'Z';
  const utcDay = (date) => date.toISOString().slice(0, 10);
  const later = (now, ms) => stamp(new Date(now.getTime() + ms));
  const isDue = (card, nowMs) => card.state !== 'new' && Date.parse(card.due) <= nowMs;

  const clampGrade = (grade) => Math.max(AGAIN, Math.min(EASY, Math.trunc(Number(grade)) || 0));

  // New cards are introduced most frequent first; unranked lemmas go last.
  const rankKey = (card) => (card.rank == null ? Infinity : card.rank);
  const byRank = (a, b) => (rankKey(a) - rankKey(b)) || (a.lemma_id - b.lemma_id);

  /* ── scheduling ─────────────────────────────────────── */

  /* Apply a grade and return the updated card, its next `due` included.
     Pure: the card passed in is left untouched. */
  function schedule(card, grade, now) {
    const next = { ...card, reps: card.reps + 1 };
    return card.state === 'review'
      ? reviewStep(next, clampGrade(grade), now)
      : learningStep(next, clampGrade(grade), now);
  }

  /* Interval a relearning card returns to once it graduates again. A card
     that lapsed from 30 days keeps a halved memory of that interval; a
     genuinely new card has nothing to retain and starts from scratch. */
  const retained = (card) => (card.lapses ? card.interval_days : 0);

  const graduate = (card, days, now) =>
    ({ ...card, state: 'review', step: 0, interval_days: days, due: later(now, days * DAY_MS) });

  const learnAgainIn = (card, step, minutes, now) =>
    ({ ...card, state: 'learning', step, due: later(now, minutes * MINUTE_MS) });

  function learningStep(card, grade, now) {
    if (grade === AGAIN) return learnAgainIn(card, 0, LEARNING_STEPS_MIN[0], now);

    // Easy skips the remaining steps entirely.
    if (grade === EASY) return graduate(card, Math.max(EASY_INTERVAL_DAYS, retained(card)), now);

    if (grade === HARD) {
      // Repeat the current step rather than advancing.
      const minutes = LEARNING_STEPS_MIN[Math.min(card.step, LEARNING_STEPS_MIN.length - 1)];
      return learnAgainIn(card, card.step, minutes, now);
    }

    // Good: advance one step, graduating off the end.
    const step = card.step + 1;
    if (step >= LEARNING_STEPS_MIN.length) {
      return graduate(card, Math.max(GRADUATING_INTERVAL_DAYS, retained(card)), now);
    }
    return learnAgainIn(card, step, LEARNING_STEPS_MIN[step], now);
  }

  function reviewStep(card, grade, now) {
    if (grade === AGAIN) {
      return {
        ...card,
        lapses: card.lapses + 1,
        ease: Math.max(MIN_EASE, card.ease - 0.2),
        state: 'learning',
        step: 0,
        // Keep a memory of the old interval so a lapsed card does not restart
        // from nothing once it graduates again.
        interval_days: Math.max(GRADUATING_INTERVAL_DAYS, card.interval_days * LAPSE_INTERVAL_FACTOR),
        due: later(now, RELEARN_STEP_MIN * MINUTE_MS),
      };
    }

    const prev = Math.max(card.interval_days, GRADUATING_INTERVAL_DAYS);
    let ease = card.ease;
    let interval;
    if (grade === HARD) {
      ease = Math.max(MIN_EASE, ease - 0.15);
      interval = prev * HARD_FACTOR;
    } else if (grade === GOOD) {
      interval = prev * ease;
    } else {
      ease += 0.15;
      interval = prev * ease * EASY_BONUS;
    }
    return graduate({ ...card, ease }, Math.min(MAX_INTERVAL_DAYS, Math.max(interval, prev + 1)), now);
  }

  /* ── exported files ─────────────────────────────────── */

  const fail = (field, value) => {
    throw new Error(`Invalid ${field}: ${JSON.stringify(value)}`);
  };
  const orNull = (value, check) => (value == null ? null : check(value));

  function integer(value, field, min) {
    if (!Number.isInteger(value) || value < min) fail(field, value);
    return value;
  }

  function finite(value, field) {
    if (typeof value !== 'number' || !Number.isFinite(value)) fail(field, value);
    return value;
  }

  // Normalised to the stored form, so the UTC day is always its prefix.
  function timestamp(value, field) {
    const ms = typeof value === 'string' ? Date.parse(value) : NaN;
    if (Number.isNaN(ms)) fail(field, value);
    return stamp(new Date(ms));
  }

  function records(value, field) {
    if (!Array.isArray(value)) fail(field, value);
    value.forEach((item) => { if (!item || typeof item !== 'object') fail(field, item); });
    return value;
  }

  function parseCard(c) {
    if (!STATES.includes(c.state)) fail('card state', c.state);
    return {
      lemma_id: integer(c.lemma_id, 'card lemma_id', 1),
      state: c.state,
      step: integer(c.step, 'card step', 0),
      ease: finite(c.ease, 'card ease'),
      interval_days: finite(c.interval_days, 'card interval_days'),
      due: orNull(c.due, (v) => timestamp(v, 'card due')),
      reps: integer(c.reps, 'card reps', 0),
      lapses: integer(c.lapses, 'card lapses', 0),
      origin: orNull(c.origin, String),
      added_at: orNull(c.added_at, (v) => timestamp(v, 'card added_at')),
      last_review: orNull(c.last_review, (v) => timestamp(v, 'card last_review')),
      freq: orNull(c.freq, (v) => integer(v, 'card freq', 0)),
      rank: orNull(c.rank, (v) => integer(v, 'card rank', 1)),
    };
  }

  function parseReview(r) {
    return {
      lemma_id: integer(r.lemma_id, 'review lemma_id', 1),
      reviewed_at: timestamp(r.reviewed_at, 'review reviewed_at'),
      grade: integer(r.grade, 'review grade', 0),
      interval_days: orNull(r.interval_days, (v) => finite(v, 'review interval_days')),
      ease: orNull(r.ease, (v) => finite(v, 'review ease')),
    };
  }

  /* Validate an exported progress file and return it normalised. Throws an
     Error naming the first problem, before anything is replaced. */
  function parseSnapshot(raw) {
    if (!raw || raw.format !== FORMAT) throw new Error('Not a Septuagint progress file');
    if (raw.version !== FORMAT_VERSION) {
      throw new Error(`Unsupported progress file version: ${JSON.stringify(raw.version)}`);
    }
    const limit = raw.settings && raw.settings.daily_new_limit;
    return {
      position: integer(raw.position, 'position', 1),
      settings: { daily_new_limit: integer(limit == null ? 12 : limit, 'daily_new_limit', 0) },
      cards: records(raw.cards, 'cards').map(parseCard),
      reviews: records(raw.reviews, 'reviews').map(parseReview),
    };
  }

  /* ── progress ───────────────────────────────────────── */

  class Progress {
    constructor(store) {
      this.store = store;
      this.reset({});
    }

    reset(data) {
      this.position = data.position || 1;
      this.settings = { ...DEFAULT_SETTINGS, ...data.settings };
      this.cards = new Map((data.cards || []).map((c) => [c.lemma_id, c]));
      this.reviews = data.reviews || [];
      // UTC day each lemma was first reviewed: the daily limit counts those.
      this.firstReviewed = new Map();
      this.reviews.forEach((r) => this.noteFirstReview(r));
    }

    noteFirstReview(review) {
      const day = review.reviewed_at.slice(0, 10);
      const seen = this.firstReviewed.get(review.lemma_id);
      if (!seen || day < seen) this.firstReviewed.set(review.lemma_id, day);
    }

    async load() {
      this.reset(await this.store.load());
    }

    card(lemmaId) {
      return this.cards.get(lemmaId);
    }

    deckIds() {
      return Array.from(this.cards.keys());
    }

    /* Move the reading position, clamped to the corpus: one past the last
       verse means the whole text has been read. */
    async setPosition(seq, totalVerses) {
      const position = Math.max(1, Math.min(Math.trunc(seq) || 1, totalVerses + 1));
      await this.store.putMeta('position', position);
      this.position = position;
    }

    /* Queue lemmas as new cards and return how many were added; lemmas
       already in the deck are left alone. Each lemma carries its corpus
       `freq` and `rank`: rank orders new cards, freq weighs text coverage. */
    async addCards(lemmas, origin, now) {
      const at = stamp(now);
      const added = new Map();
      for (const l of lemmas) {
        if (this.cards.has(l.lemma_id) || added.has(l.lemma_id)) continue;
        added.set(l.lemma_id, {
          lemma_id: l.lemma_id, state: 'new', step: 0, ease: 2.5, interval_days: 0,
          due: at, reps: 0, lapses: 0, origin, added_at: at, last_review: null,
          freq: l.freq == null ? null : l.freq,
          rank: l.rank == null ? null : l.rank,
        });
      }
      if (!added.size) return 0;
      await this.store.putCards(Array.from(added.values()));
      added.forEach((card, id) => this.cards.set(id, card));
      return added.size;
    }

    /* Grade a card, persist its new schedule, and log the review. */
    async review(lemmaId, grade, now) {
      const card = this.cards.get(lemmaId);
      if (!card) throw new Error('That word is not in your deck');
      const at = stamp(now);
      const next = { ...schedule(card, grade, now), last_review: at };
      const entry = {
        lemma_id: lemmaId, reviewed_at: at, grade: clampGrade(grade),
        interval_days: next.interval_days, ease: next.ease,
      };
      await this.store.recordReview(next, entry);
      this.cards.set(lemmaId, next);
      this.reviews.push(entry);
      this.noteFirstReview(entry);
      return next;
    }

    introducedToday(now) {
      const today = utcDay(now);
      let n = 0;
      this.firstReviewed.forEach((day) => { if (day >= today) n += 1; });
      return n;
    }

    queueCounts(now) {
      const nowMs = now.getTime();
      let due = 0;
      let fresh = 0;
      this.cards.forEach((c) => {
        if (c.state === 'new') fresh += 1;
        else if (isDue(c, nowMs)) due += 1;
      });
      const limit = this.settings.daily_new_limit;
      const introduced = this.introducedToday(now);
      return {
        due,
        new: fresh,
        new_available: Math.min(fresh, Math.max(0, limit - introduced)),
        new_limit: limit,
        introduced_today: introduced,
      };
    }

    /* The next card to study: the most overdue first, then new cards in
       frequency order, which is what makes early reading possible at all. */
    nextCard(now) {
      const nowMs = now.getTime();
      let next = null;
      this.cards.forEach((c) => {
        if (isDue(c, nowMs) && (!next || Date.parse(c.due) < Date.parse(next.due))) next = c;
      });
      if (next || this.queueCounts(now).new_available <= 0) return next;
      this.cards.forEach((c) => {
        if (c.state === 'new' && (!next || byRank(c, next) < 0)) next = c;
      });
      return next;
    }

    /* Everything the status bar and Progress view show. `corpus` is the
       server's /api/status: the verse count and total word frequency. */
    status(corpus, now) {
      let known = 0;
      let knownFreq = 0;
      this.cards.forEach((c) => {
        if (c.state !== 'review') return;
        known += 1;
        knownFreq += c.freq || 0;
      });
      const today = utcDay(now);
      // Share of running text made of the words graduated to review.
      const coverage = corpus.total_freq ? knownFreq / corpus.total_freq : 0;
      return {
        position: this.position,
        total_verses: corpus.total_verses,
        verses_read: this.position - 1,
        known_words: known,
        studying: this.cards.size - known,
        reviews_today: this.reviews.filter((r) => r.reviewed_at.slice(0, 10) >= today).length,
        text_coverage: Math.round(coverage * 10000) / 100,
        queue: this.queueCounts(now),
      };
    }

    /* Reviews per UTC day, newest first, for at most 60 days. */
    stats() {
      const days = new Map();
      this.reviews.forEach((r) => {
        const day = r.reviewed_at.slice(0, 10);
        const row = days.get(day) || { day, reviews: 0, again: 0 };
        row.reviews += 1;
        if (r.grade === AGAIN) row.again += 1;
        days.set(day, row);
      });
      return Array.from(days.values())
        .sort((a, b) => (a.day < b.day ? 1 : -1))
        .slice(0, 60);
    }

    snapshot(now) {
      return {
        format: FORMAT,
        version: FORMAT_VERSION,
        exported_at: stamp(now),
        position: this.position,
        settings: { ...this.settings },
        cards: Array.from(this.cards.values()),
        reviews: this.reviews.slice(),
      };
    }

    /* Replace all progress with an exported file's. */
    async replace(raw) {
      const data = parseSnapshot(raw);
      await this.store.replaceAll(data);
      this.reset(data);
    }
  }

  /* ── storage ────────────────────────────────────────── */

  const DB_NAME = 'septuagint-progress';
  const DB_VERSION = 1;
  const STORES = ['meta', 'cards', 'reviews'];

  const settle = (request) => new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });

  class IndexedDbStore {
    static async open(factory = globalThis.indexedDB) {
      if (!factory) throw new Error('IndexedDB is unavailable');
      const request = factory.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        const db = request.result;
        db.createObjectStore('meta');
        db.createObjectStore('cards', { keyPath: 'lemma_id' });
        db.createObjectStore('reviews', { autoIncrement: true });
      };
      return new IndexedDbStore(await settle(request));
    }

    constructor(db) {
      this.db = db;
    }

    async load() {
      const tx = this.db.transaction(STORES);
      const [position, settings, cards, reviews] = await Promise.all([
        settle(tx.objectStore('meta').get('position')),
        settle(tx.objectStore('meta').get('settings')),
        settle(tx.objectStore('cards').getAll()),
        settle(tx.objectStore('reviews').getAll()),
      ]);
      return { position, settings, cards, reviews };
    }

    /* One read-write transaction: every write in `fill` lands, or none do. */
    write(stores, fill) {
      return new Promise((resolve, reject) => {
        const tx = this.db.transaction(stores, 'readwrite');
        tx.oncomplete = () => resolve();
        tx.onerror = tx.onabort = () => reject(tx.error || new Error('progress write aborted'));
        fill((name) => tx.objectStore(name));
      });
    }

    putMeta(key, value) {
      return this.write(['meta'], (store) => store('meta').put(value, key));
    }

    putCards(cards) {
      return this.write(['cards'], (store) => cards.forEach((c) => store('cards').put(c)));
    }

    recordReview(card, review) {
      return this.write(['cards', 'reviews'], (store) => {
        store('cards').put(card);
        store('reviews').add(review);
      });
    }

    replaceAll(data) {
      return this.write(STORES, (store) => {
        STORES.forEach((name) => store(name).clear());
        store('meta').put(data.position, 'position');
        store('meta').put(data.settings, 'settings');
        data.cards.forEach((c) => store('cards').put(c));
        data.reviews.forEach((r) => store('reviews').add(r));
      });
    }
  }

  const copy = (value) => (value === undefined ? value : JSON.parse(JSON.stringify(value)));

  /* Progress for the life of the page only: the stand-in when IndexedDB is
     unavailable, and the store the tests use. */
  class MemoryStore {
    constructor() {
      this.data = { position: undefined, settings: undefined, cards: new Map(), reviews: [] };
    }

    async load() {
      const { position, settings, cards, reviews } = this.data;
      return copy({ position, settings, cards: Array.from(cards.values()), reviews });
    }

    async putMeta(key, value) {
      this.data[key] = copy(value);
    }

    async putCards(cards) {
      cards.forEach((c) => this.data.cards.set(c.lemma_id, copy(c)));
    }

    async recordReview(card, review) {
      await this.putCards([card]);
      this.data.reviews.push(copy(review));
    }

    async replaceAll(data) {
      this.data = { position: undefined, settings: undefined, cards: new Map(), reviews: [] };
      await this.putMeta('position', data.position);
      await this.putMeta('settings', data.settings);
      await this.putCards(data.cards);
      this.data.reviews = copy(data.reviews);
    }
  }

  return {
    AGAIN, HARD, GOOD, EASY,
    IndexedDbStore,
    MemoryStore,
    Progress,
    parseSnapshot,
    schedule,
  };
}));
