/* Septuagint reading trainer — front end.
   Plain ES modules-free JS: no build step, no dependencies. The server only
   answers questions about the corpus; the learner's progress lives in this
   browser, kept by progress.js. */

'use strict';

const $  = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  bible: {
    books: [],
    initialized: false,
    loading: false,
    next: null,
    optionRequest: 0,
    pageRequest: 0,
    previous: null,
    showGlosses: true,
    verses: [],
  },
  view: 'read',
  corpus: { total_verses: 0, total_freq: 0 },
  cardRequest: 0,
  verses: [],
  newWords: [],
  card: null,
  revealed: false,
  showGlosses: true,
  tts: { available: false, version: '0' },
};

/* ── audio ───────────────────────────────────────────────
   One <audio> element reused for every clip: playing a new word should cut
   off the previous one rather than talking over it. */
const player = new Audio();
let playingBtn = null;

function audioUrl(params) {
  // `v` identifies the voice and preprocessing. Clips are cached hard by the
  // browser, so without it a change to how they are produced stays inaudible
  // behind a year-long cache entry.
  const q = new URLSearchParams({ ...params, v: state.tts.version || '0' });
  return `/api/audio?${q}`;
}

async function play(params, btn) {
  if (!state.tts.available) return;
  if (playingBtn) playingBtn.classList.remove('is-playing');
  player.pause();

  if (btn) {
    btn.classList.add('is-loading');
    playingBtn = btn;
  }
  try {
    player.src = audioUrl(params);
    await player.play();
    if (btn) { btn.classList.remove('is-loading'); btn.classList.add('is-playing'); }
  } catch (err) {
    if (btn) btn.classList.remove('is-loading', 'is-playing');
    // A play() rejection is usually the browser blocking autoplay before any
    // user gesture, which is harmless and self-correcting.
    if (err && err.name !== 'AbortError' && err.name !== 'NotAllowedError') {
      toast('Could not play audio');
    }
  }
}

player.addEventListener('ended', () => {
  if (playingBtn) playingBtn.classList.remove('is-playing');
  playingBtn = null;
});

/* Speak an inflected form exactly as it appears in the text, lighting the
   word up while it sounds. */
async function speakWord(surface, el) {
  if (el) el.classList.add('is-speaking');
  const clear = () => el && el.classList.remove('is-speaking');
  player.addEventListener('ended', clear, { once: true });
  await play({ text: surface });
  if (player.paused) clear();
}

function playButton(params, title) {
  const b = document.createElement('button');
  b.className = 'btn-play';
  b.type = 'button';
  b.textContent = '▶';
  b.title = title || 'Hear it';
  b.onclick = (e) => { e.stopPropagation(); play(params, b); };
  return b;
}

/* ── api ─────────────────────────────────────────────── */
/* A hairline bar at the top of the window shows that a request is in flight.
   Requests overlap, so count them rather than toggling on each one. */
let inFlight = 0;
function busy(delta) {
  inFlight = Math.max(0, inFlight + delta);
  const bar = document.getElementById('loadbar');
  if (bar) bar.classList.toggle('is-on', inFlight > 0);
}

async function api(path, opts) {
  busy(1);
  try {
    const res = await fetch(path, opts);
    const data = await res.json().catch(() => ({ error: 'bad response' }));
    if (data && data.error) toast(data.error);
    return data;
  } finally {
    busy(-1);
  }
}
const get  = (p) => api(p);
const post = (p, body) => api(p, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body || {}),
});

/* ── progress ────────────────────────────────────────── */
let progress = null;
let progressKept = true;

async function openProgress() {
  let store;
  try {
    store = await LxxProgress.IndexedDbStore.open();
  } catch (err) {
    // Some private-browsing modes refuse IndexedDB. The app still works for
    // the session, and says so where Export is offered.
    store = new LxxProgress.MemoryStore();
    progressKept = false;
    toast('This browser will not keep your progress');
  }
  progress = new LxxProgress.Progress(store);
  await progress.load();
}

/* Await a progress change. Resolves to { value } once it is stored, or to
   null after reporting the failure: a lost write is never silent. */
async function save(change) {
  try {
    const value = await change;
    keepStorage();
    return { value };
  } catch (err) {
    toast(`Could not save your progress: ${err.message || err}`);
    return null;
  }
}

/* Ask the browser not to evict this site's storage under disk pressure.
   Once a session is enough; a browser that grants it remembers. */
let persistAsked = false;
function keepStorage() {
  if (persistAsked || !navigator.storage || !navigator.storage.persist) return;
  persistAsked = true;
  navigator.storage.persist().catch(() => {});
}

async function loadCorpus() {
  const totals = await get('/api/status');
  if (totals && !totals.error) state.corpus = totals;
}

/* ── chrome ──────────────────────────────────────────── */
function toast(msg) {
  let el = $('.toast');
  if (!el) {
    el = document.createElement('div');
    el.className = 'toast';
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), 2600);
}

function setView(name) {
  state.view = name;
  $$('.tab').forEach((t) => {
    const on = t.dataset.view === name;
    t.classList.toggle('is-active', on);
    t.setAttribute('aria-selected', String(on));
  });
  $$('.view').forEach((v) => v.classList.toggle('is-active', v.id === `view-${name}`));
  if (name === 'bible')    initBible();
  if (name === 'drill')    loadCard();
  if (name === 'vocab')    loadCore();
  if (name === 'progress') loadProgress();
}

function refreshStatus() {
  const s = progress.status(state.corpus, new Date());
  // The Read view always shows the passage at the reading position.
  const first = state.verses[0];
  $('#st-ref').textContent   = first && first.seq === s.position ? first.ref : '—';
  $('#st-read').textContent  = s.verses_read.toLocaleString();
  $('#st-known').textContent = s.known_words.toLocaleString();
  $('#st-cov').textContent   = `${s.text_coverage}%`;
  const pending = s.queue.due + s.queue.new_available;
  $('#st-due').textContent = pending;
  const badge = $('#badge-drill');
  badge.hidden = pending === 0;
  badge.textContent = pending;
}

/* ── reading ─────────────────────────────────────────── */
const STATE_CLASS = {
  review: 'w-review',
  learning: 'w-learning',
  new: 'w-learning',
};

async function loadVerses() {
  const n = $('#read-n').value;
  const data = await get(`/api/read?n=${n}&from=${progress.position}`);
  state.verses = data.verses || [];
  renderVerses();
  renderNewWords();
}

function wordState(t) {
  const card = t.lemma_id && progress.card(t.lemma_id);
  return card ? card.state : null;
}

function renderVerseList(host, verses, showGlosses, emptyHeading, emptyCopy) {
  host.innerHTML = '';
  if (!verses.length) {
    host.innerHTML = `<div class="empty"><h2>${esc(emptyHeading)}</h2>` +
                     `<p>${esc(emptyCopy)}</p></div>`;
    return;
  }
  for (const v of verses) {
    const el = document.createElement('article');
    el.className = 'verse';

    const head = document.createElement('div');
    head.className = 'verse-head';
    // Book name and chapter:verse are set apart, the way a printed edition
    // marks its margin: the name small above, the number below it.
    const ref = document.createElement('div');
    ref.className = 'verse-ref';
    const book = document.createElement('span');
    book.className = 'ref-book';
    book.textContent = v.book;
    const num = document.createElement('span');
    num.className = 'ref-num';
    num.textContent = v.ref.split(' ').slice(1).join(' ');
    ref.append(book, num);
    head.appendChild(ref);
    if (state.tts.available) {
      head.appendChild(playButton({ verse_id: v.id }, 'Hear this verse'));
    }
    el.appendChild(head);

    const text = document.createElement('div');
    text.className = 'verse-text';
    v.tokens.forEach((t) => {
      const wrap = document.createElement('span');
      wrap.className = 'w-wrap';

      const w = document.createElement('span');
      w.className = 'w ' + (t.lemma_id
        ? (STATE_CLASS[wordState(t)] || 'w-unknown')
        : 'w-none');
      w.textContent = t.surface;
      w.title = [t.lemma, t.parse, t.gloss].filter(Boolean).join(' · ');
      if (t.lemma_id) {
        w.onclick = (e) => {
          // Alt-click hears the word as it stands in the text; a plain click
          // opens the full entry, which has its own play button.
          if (e.altKey && state.tts.available) {
            speakWord(t.surface, w);
          } else {
            showLemma(t.lemma_id);
          }
        };
      }
      wrap.appendChild(w);

      if (showGlosses) {
        const g = document.createElement('span');
        g.className = 'w-gloss';
        // Only prompt with a gloss for words not yet known — known words
        // should be read, not translated. Show the whole short gloss and let
        // CSS ellipsize it: truncating at the first comma turns ἀρχή's
        // "ruler, beginning" into a plainly wrong "ruler".
        g.textContent = (wordState(t) === 'review') ? '' : (t.gloss || '');
        wrap.appendChild(g);
      }
      text.appendChild(wrap);
      text.appendChild(document.createTextNode(' '));
    });
    el.appendChild(text);
    if (v.brenton) {
      const en = document.createElement('p');
      en.className = 'verse-brenton';
      en.textContent = v.brenton;
      el.appendChild(en);
    }
    host.appendChild(el);
  }
}

function renderVerses() {
  renderVerseList(
    $('#verses'),
    state.verses,
    state.showGlosses,
    'End of the corpus',
    'You have reached the last verse. Use ← Back to review.',
  );
}

/* The passage's lemmas not yet in the deck, most frequent first. */
function renderNewWords() {
  const fresh = new Map();
  state.verses.forEach((v) => v.tokens.forEach((t) => {
    if (t.lemma_id && !progress.card(t.lemma_id) && !fresh.has(t.lemma_id)) {
      fresh.set(t.lemma_id, {
        lemma_id: t.lemma_id, lemma: t.lemma, gloss: t.gloss, freq: t.freq, rank: t.rank,
      });
    }
  }));
  state.newWords = Array.from(fresh.values()).sort((a, b) => b.freq - a.freq);
  const host = $('#newwords');
  host.innerHTML = '';

  if (!state.newWords.length) {
    host.innerHTML = '<span class="muted">Every word here is already in your deck.</span>';
    $('#btn-queue').disabled = true;
    return;
  }
  $('#btn-queue').disabled = false;
  $('#btn-queue').textContent = `Make ${state.newWords.length} flashcards`;

  state.newWords.forEach((w) => {
    const chip = document.createElement('span');
    chip.className = 'chip';
    chip.onclick = () => showLemma(w.lemma_id);
    chip.innerHTML =
      `<span class="chip-greek">${esc(w.lemma)}</span>` +
      `<span class="chip-gloss">${esc((w.gloss || '—').split(/[,;]/)[0])}</span>` +
      `<span class="chip-freq">${w.freq}×</span>`;
    host.appendChild(chip);
  });
}

/* ── Bible browsing ──────────────────────────────────── */
function chapterLabel(chapter) {
  return Number(chapter) === 0 ? 'Prologue' : String(chapter);
}

function verseOptionLabel(verse) {
  const ref = String(verse.ref || '');
  const colon = ref.lastIndexOf(':');
  if (colon >= 0) return ref.slice(colon + 1);
  return ref.split(/\s+/).pop() || String(verse.verse);
}

async function initBible() {
  const reading = state.bible;
  if (reading.initialized || reading.loading) return;
  reading.loading = true;
  try {
    const books = await get('/api/books');
    if (!Array.isArray(books) || !books.length) return;

    reading.books = books;
    const select = $('#bible-book');
    select.innerHTML = '';
    books.forEach((book) => {
      const option = document.createElement('option');
      option.value = book.code;
      option.textContent = book.name;
      select.appendChild(option);
    });
    select.disabled = false;
    reading.initialized = await loadBibleOptions(null, true);
  } finally {
    reading.loading = false;
  }
}

async function loadBibleOptions(chapter, openPassage) {
  const reading = state.bible;
  const bookCode = $('#bible-book').value;
  if (!bookCode) return false;
  if (openPassage) reading.pageRequest += 1;

  const request = ++reading.optionRequest;
  const params = new URLSearchParams({ book: bookCode });
  if (chapter != null) params.set('chapter', String(chapter));
  const data = await get(`/api/reference-options?${params}`);
  if (request !== reading.optionRequest || $('#bible-book').value !== bookCode) {
    return false;
  }
  if (!data || data.error || !Array.isArray(data.verses)) return false;

  const chapterSelect = $('#bible-chapter');
  chapterSelect.innerHTML = '';
  data.chapters.forEach((item) => {
    const option = document.createElement('option');
    option.value = item.chapter;
    option.textContent = chapterLabel(item.chapter);
    chapterSelect.appendChild(option);
  });
  chapterSelect.value = String(data.chapter);
  chapterSelect.disabled = false;

  const verseSelect = $('#bible-verse');
  verseSelect.innerHTML = '';
  data.verses.forEach((verse) => {
    const option = document.createElement('option');
    option.value = verse.id;
    option.textContent = verseOptionLabel(verse);
    verseSelect.appendChild(option);
  });
  verseSelect.disabled = !data.verses.length;
  $('#bible-go').disabled = !data.verses.length;

  if (openPassage && data.verses.length) {
    return loadBiblePassage(data.verses[0].id);
  }
  return data.verses.length > 0;
}

async function loadBiblePassage(cursor, direction = 'next') {
  const reading = state.bible;
  const bookCode = $('#bible-book').value;
  const selected = cursor == null ? Number($('#bible-verse').value) : Number(cursor);
  if (!bookCode || !Number.isInteger(selected) || selected < 1) return false;

  const request = ++reading.pageRequest;
  const params = new URLSearchParams({
    book: bookCode,
    cursor: String(selected),
    direction,
    n: $('#bible-n').value,
  });
  const data = await get(`/api/passage?${params}`);
  if (request !== reading.pageRequest || $('#bible-book').value !== bookCode) {
    return false;
  }
  if (!data || data.error || !Array.isArray(data.verses)) return false;

  reading.next = data.next;
  reading.previous = data.previous;
  reading.verses = data.verses;
  renderBibleVerses();
  updateBibleNavigation();

  const first = reading.verses[0];
  if (first) {
    const hasFirst = Array.from($('#bible-verse').options)
      .some((option) => Number(option.value) === first.id);
    if (Number($('#bible-chapter').value) !== first.chapter || !hasFirst) {
      await loadBibleOptions(first.chapter, false);
    }
    if (request !== reading.pageRequest || $('#bible-book').value !== bookCode) {
      return false;
    }
    $('#bible-verse').value = String(first.id);
  }
  return true;
}

function renderBibleVerses() {
  renderVerseList(
    $('#bible-verses'),
    state.bible.verses,
    state.bible.showGlosses,
    'No passage found',
    'Choose another book, chapter, or verse.',
  );
}

function updateBibleNavigation() {
  const reading = state.bible;
  const first = reading.verses[0];
  const last = reading.verses[reading.verses.length - 1];
  const label = !first ? 'Choose a passage'
    : first.ref === last.ref ? first.ref : `${first.ref} – ${last.ref}`;
  $$('.bible-range').forEach((output) => { output.textContent = label; });
  $$('[data-bible-page]').forEach((button) => {
    button.disabled = reading[button.dataset.biblePage] == null;
  });
}

/* ── drilling ────────────────────────────────────────── */
async function loadCard() {
  const now = new Date();
  const request = ++state.cardRequest;
  const card = progress.nextCard(now);
  const queue = progress.queueCounts(now);
  if (!card) {
    showCard(null, queue);
    return;
  }
  // The deck holds the schedule; the corpus holds the word itself.
  const entry = await get(`/api/lemma?id=${card.lemma_id}`);
  if (request !== state.cardRequest || !entry || entry.error) return;
  showCard({ ...entry, ...card }, queue);
}

function showCard(card, queue) {
  state.card = card;
  state.revealed = false;
  $('#drill-card').hidden = !card;
  $('#drill-empty').hidden = !!card;
  if (!card) return;

  $('#card-front').textContent = card.lemma;
  $('#card-gloss').textContent = card.gloss || '(no gloss available)';
  $('#card-gloss-long').textContent = longGlossAdds(card) ? card.gloss_long : '';
  $('#card-state').textContent =
    card.state === 'new' ? 'new word'
      : card.state === 'learning' ? 'learning' : `review · ${fmtDays(card.interval_days)}`;
  $('#card-freq').textContent =
    card.rank ? `#${card.rank} most frequent · ${card.freq}× in the LXX` : '';

  const ex = $('#card-example');
  if (card.example) {
    ex.innerHTML =
      `<div class="example-ref">${esc(card.example.ref)}</div>` +
      `<div class="example-text">${highlight(card.example.text, card.example.forms)}</div>`;
    ex.hidden = false;
  } else {
    ex.hidden = true;
  }

  $('#card-say').hidden = !state.tts.available;

  $('#card-back').hidden = true;
  $('#grade-buttons').hidden = true;
  $('#btn-reveal').hidden = false;

  // Hearing the word while its meaning is still hidden is the point: you are
  // testing recall from the sound, not just the spelling.
  if (state.tts.available && autoplayOn()) {
    play({ lemma_id: card.lemma_id }, $('#btn-card-say'));
  }

  if (queue) {
    $('#queue-line').textContent =
      `${queue.due} due · ${queue.new_available} new available today ` +
      `(${queue.introduced_today}/${queue.new_limit} introduced)`;
  }
}

function reveal() {
  if (!state.card || state.revealed) return;
  state.revealed = true;
  $('#card-back').hidden = false;
  $('#grade-buttons').hidden = false;
  $('#btn-reveal').hidden = true;
}

async function grade(g) {
  if (!state.card || !state.revealed) return;
  // One grade per card, however fast the keys arrive.
  state.revealed = false;
  if (!(await save(progress.review(state.card.lemma_id, g, new Date())))) {
    state.revealed = true;
    return;
  }
  refreshStatus();
  loadCard();
}

/* ── vocabulary ──────────────────────────────────────── */
function coreWords(n) {
  return post('/api/core', { n, exclude: progress.deckIds() });
}

async function loadCore() {
  const rows = await coreWords(100);
  if (!Array.isArray(rows)) return;
  const host = $('#corelist');
  if (!rows.length) {
    host.innerHTML = '<div class="empty"><p>Every glossed word in the corpus is queued.</p></div>';
    return;
  }
  host.innerHTML =
    '<table><thead><tr><th>Rank</th><th>Word</th><th>Meaning</th>' +
    '<th class="num">Occurrences</th></tr></thead><tbody>' +
    rows.map((r) =>
      `<tr><td class="num">${r.rank}</td>` +
      `<td class="greek">${esc(r.lemma)}</td>` +
      `<td>${esc(r.gloss || '')}</td>` +
      `<td class="num">${r.freq.toLocaleString()}</td></tr>`).join('') +
    '</tbody></table>';
}

/* ── progress ────────────────────────────────────────── */
function loadProgress() {
  const s = progress.status(state.corpus, new Date());
  const hist = progress.stats();
  const pctRead = s.total_verses ? (100 * s.verses_read / s.total_verses) : 0;
  const days = hist.slice().reverse();
  const max = Math.max(1, ...days.map((d) => d.reviews));

  $('#progress-body').innerHTML = `
    <div class="cards-grid">
      <div class="metric">
        <div class="metric-value">${s.known_words.toLocaleString()}</div>
        <div class="metric-label">words known</div>
      </div>
      <div class="metric">
        <div class="metric-value">${s.text_coverage}%</div>
        <div class="metric-label">of all running text you can read</div>
        <div class="bar"><span style="width:${Math.min(100, s.text_coverage)}%"></span></div>
      </div>
      <div class="metric">
        <div class="metric-value">${s.verses_read.toLocaleString()}</div>
        <div class="metric-label">verses read of ${s.total_verses.toLocaleString()}</div>
        <div class="bar"><span style="width:${pctRead.toFixed(2)}%"></span></div>
      </div>
      <div class="metric">
        <div class="metric-value">${s.studying.toLocaleString()}</div>
        <div class="metric-label">words in progress</div>
      </div>
    </div>
    <div class="panel">
      <h2>Reviews per day</h2>
      ${days.length
        ? `<div class="spark">${days.map((d) =>
            `<i style="height:${Math.round(100 * d.reviews / max)}%" title="${d.day}: ${d.reviews} reviews"></i>`
          ).join('')}</div>
           <p class="muted" style="font-size:.78rem;margin-top:.5rem">
             ${days[0].day} → ${days[days.length - 1].day}</p>`
        : '<p class="muted">No reviews recorded yet.</p>'}
    </div>`;
}

/* Progress lives only in this browser, so Export is the backup and the way
   to carry it to another browser or device. */
function exportProgress() {
  const now = new Date();
  const file = new Blob([JSON.stringify(progress.snapshot(now), null, 1)],
                        { type: 'application/json' });
  const link = document.createElement('a');
  link.href = URL.createObjectURL(file);
  link.download = `septuagint-progress-${now.toISOString().slice(0, 10)}.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 10000);
}

async function importProgress(input) {
  const file = input.files && input.files[0];
  input.value = '';  // so picking the same file again still fires `change`
  if (!file) return;
  let raw;
  try {
    raw = JSON.parse(await file.text());
  } catch (err) {
    toast('That file is not a progress export');
    return;
  }
  const incoming = raw && Array.isArray(raw.cards) ? raw.cards.length : 0;
  if (!confirm(`Replace the progress in this browser (${progress.cards.size} cards) ` +
               `with the file's (${incoming} cards)?`)) return;
  if (!(await save(progress.replace(raw)))) return;
  toast('Progress imported');
  await loadVerses();
  refreshStatus();
  loadProgress();
}

function showKeepNote() {
  $('#keep-note').textContent = progressKept
    ? 'Nothing is stored on the server. Clearing this site\'s data erases it, and ' +
      'another browser or device starts fresh. Export saves a copy you can import anywhere.'
    : 'This browser is not keeping your progress: it is lost when the page closes. ' +
      'Export before you leave, and import it next time.';
}

/* Another tab may have changed progress while this one was hidden. Reload,
   so this tab never writes a stale card back over it. */
async function reloadProgress() {
  if (document.visibilityState !== 'visible' || !progress) return;
  const position = progress.position;
  await progress.load();
  if (progress.position !== position) await loadVerses();
  else { renderVerses(); renderNewWords(); }
  if (state.bible.verses.length) renderBibleVerses();
  refreshStatus();
  if (state.view === 'progress') loadProgress();
}

/* ── word detail ─────────────────────────────────────── */
/* ── paradigm tables (word detail modal) ─────────────── */
function paradigmCell(c) {
  if (!c) return '<td class="pempty">—</td>';
  return `<td class="pgreek">${c.forms.map(esc).join(', ')}${c.count > 1
    ? ` <span class="pcount">×${c.count}</span>` : ''}</td>`;
}

const PARADIGM_NUMBERS = { S: 'Singular', P: 'Plural', D: 'Dual' };

function declensionTable(t) {
  const title = [t.degree_label, t.gender_label].filter(Boolean).join(' ');
  const head = t.numbers.map((n) => `<th>${PARADIGM_NUMBERS[n]}</th>`).join('');
  const rows = t.rows.map((r) =>
    `<tr><th>${esc(r.label)}</th>${t.numbers.map((n) => paradigmCell(r.cells[n])).join('')}</tr>`).join('');
  return `${title ? `<div class="ptitle">${esc(title)}</div>` : ''}
    <table class="paradigm"><thead><tr><th></th>${head}</tr></thead><tbody>${rows}</tbody></table>`;
}

function conjugationTable(s) {
  const head = s.numbers.map((n) => `<th>${PARADIGM_NUMBERS[n]}</th>`).join('');
  const rows = s.rows.map((r) =>
    `<tr><th>${esc(r.label)}</th>${s.numbers.map((n) => paradigmCell(r.cells[n])).join('')}</tr>`).join('');
  return `<div class="ptitle">${esc(s.label)}</div>
    <table class="paradigm"><thead><tr><th></th>${head}</tr></thead><tbody>${rows}</tbody></table>`;
}

/* Declension/conjugation tables from the corpus's attested forms only:
   an empty cell means the corpus never uses the word in that form. */
function paradigmHTML(p) {
  if (!p || !p.sections || !p.sections.length) return '';
  let html = '';
  let verbHeaded = false;
  for (const s of p.sections) {
    if (s.kind === 'declension') {
      html += '<h3 style="margin:1.4rem 0 .4rem;font-size:.95rem">Declension</h3>'
        + '<p class="muted" style="font-size:.78rem;margin:0 0 .4rem">Forms attested in this corpus; a dash means the corpus never uses the word that way.</p>'
        + s.tables.map(declensionTable).join('');
      continue;
    }
    if (!verbHeaded) {
      html += '<h3 style="margin:1.4rem 0 .4rem;font-size:.95rem">Conjugation</h3>'
        + '<p class="muted" style="font-size:.78rem;margin:0 0 .4rem">Forms attested in this corpus; a dash means the corpus never uses the word that way.</p>';
      verbHeaded = true;
    }
    if (s.kind === 'conjugation') html += conjugationTable(s);
    else if (s.kind === 'participles') {
      html += `<div class="ptitle">${esc(s.label)}</div>` + s.tables.map(declensionTable).join('');
    } else if (s.kind === 'infinitives') {
      html += s.items.map((i) =>
        `<div class="pinf">${esc(i.label)} infinitive: <span class="pgreek">${i.forms.map(esc).join(', ')}</span>${i.count > 1 ? ` <span class="pcount">×${i.count}</span>` : ''}</div>`).join('');
    }
  }
  return html;
}

async function showLemma(id) {
  const d = await get(`/api/lemma?id=${id}`);
  if (d.error) return;
  const card = progress.card(id);
  const sched = card
    ? `<dt>Status</dt><dd>${card.state}${card.due ? ` · due ${card.due.slice(0, 10)}` : ''}</dd>
       <dt>Reviews</dt><dd>${card.reps || 0} (${card.lapses || 0} lapses)</dd>`
    : '<dt>Status</dt><dd>not in your deck</dd>';

  $('#modal-body').innerHTML = `
    <div class="modal-greek">${esc(d.lemma)}</div>
    ${state.tts.available
      ? `<div class="modal-say">
           <button class="btn-play" id="modal-say" title="Hear it">▶</button>
           <span class="muted" style="font-size:.78rem">modern Greek pronunciation</span>
         </div>` : ''}
    <div class="modal-sub">${esc(d.gloss || 'no gloss available')}</div>
    ${longGlossAdds(d) ? `<p>${esc(d.gloss_long)}</p>` : ''}
    <dl class="kv">
      <dt>Frequency</dt><dd>${(d.freq || 0).toLocaleString()}× ${d.rank ? `(#${d.rank})` : ''}</dd>
      <dt>Gloss source</dt><dd>${esc(sourceLabel(d.source))}</dd>
      ${sched}
    </dl>
    ${paradigmHTML(d.paradigm)}
    ${!card ? '<button class="btn btn-primary" id="modal-add">Add to deck</button>' : ''}
    <h3 style="margin:1.4rem 0 .4rem;font-size:.95rem">First occurrences</h3>
    ${(d.occurrences || []).map((o) => `
      <div class="occ">
        <div class="occ-ref">${esc(o.ref)} · ${esc(o.surface)}${o.hits > 1 ? ` ×${o.hits}` : ''} — ${esc(o.parse_text || '')}</div>
        <div class="occ-text">${esc(o.text)}</div>
      </div>`).join('')}`;

  const say = $('#modal-say');
  if (say) say.onclick = () => play({ lemma_id: id }, say);

  const add = $('#modal-add');
  if (add) add.onclick = async () => {
    const lemma = { lemma_id: id, freq: d.freq, rank: d.rank };
    if (!(await save(progress.addCards([lemma], 'reading', new Date())))) return;
    toast('Added to deck');
    closeModal();
    refreshStatus();
    renderNewWords();
  };
  $('#overlay').hidden = false;
}

const closeModal = () => { $('#overlay').hidden = true; };

/* The long gloss is worth printing only if it says more than the short one.
   Many entries differ by a trailing full stop alone, which reads as the same
   line twice. */
function longGlossAdds(d) {
  const trim = (s) => String(s || '').trim().replace(/[.\s]+$/, '').toLowerCase();
  return !!d.gloss_long && trim(d.gloss_long) !== trim(d.gloss);
}

function sourceLabel(s) {
  return {
    dodson: 'Dodson lexicon',
    'dodson~': 'Dodson (variant form)',
    lsj: 'Liddell–Scott–Jones',
    'lsj~': 'LSJ (variant form)',
    translit: 'transliterated proper name',
  }[s] || 'none';
}

function fmtDays(d) {
  if (!d) return '';
  if (d < 1) return 'today';
  if (d < 30) return `${Math.round(d)}d`;
  if (d < 365) return `${(d / 30).toFixed(1)}mo`;
  return `${(d / 365).toFixed(1)}y`;
}

/* Bold the inflected forms of the card's word inside its example verse, so
   the learner can see the word they are studying in real context. Escaping
   happens per word, so the highlight markup is the only HTML introduced. */
function highlight(text, forms) {
  const wanted = new Set(forms || []);
  if (!wanted.size) return esc(text);
  return text
    .split(' ')
    .map((w) => (wanted.has(w) ? `<b>${esc(w)}</b>` : esc(w)))
    .join(' ');
}

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* ── pronunciation ───────────────────────────────────── */
/* The drill card speaks itself unless the learner turns that off. The toggle
   lives inside #card-say, so it disappears with the rest of the audio row
   when no voice is installed. */
function autoplayOn() {
  const box = $('#opt-autoplay');
  return !box || box.checked;
}

async function loadTts() {
  const info = await get('/api/tts');
  state.tts = {
    available: !!info.available,
    version: info.version || '0',
  };
}

/* ── wiring ──────────────────────────────────────────── */
async function init() {
  // Audio status, corpus totals and stored progress must all land before any
  // control is live: every view draws word states, and verses play buttons.
  await Promise.all([loadTts(), loadCorpus(), openProgress()]);

  $$('.tab').forEach((t) => (t.onclick = () => setView(t.dataset.view)));

  $('#read-n').onchange   = () => loadVerses();
  $('#btn-reload').onclick = async () => { await loadVerses(); refreshStatus(); };

  $('#opt-gloss').onchange = (e) => {
    state.showGlosses = e.target.checked;
    renderVerses();
  };

  $('#bible-jump').onsubmit = (e) => {
    e.preventDefault();
    loadBiblePassage();
  };
  $('#bible-book').onchange = () => loadBibleOptions(null, true);
  $('#bible-chapter').onchange = (e) =>
    loadBibleOptions(Number(e.target.value), true);
  $('#bible-n').onchange = () => {
    const first = state.bible.verses[0];
    loadBiblePassage(first ? first.id : null);
  };
  $('#bible-opt-gloss').onchange = (e) => {
    state.bible.showGlosses = e.target.checked;
    renderBibleVerses();
  };
  $$('[data-bible-page]').forEach((button) => {
    button.onclick = () => {
      const direction = button.dataset.biblePage;
      const cursor = state.bible[direction];
      if (cursor != null) loadBiblePassage(cursor, direction);
    };
  });
  updateBibleNavigation();

  // The header casts a shadow only once the text scrolls under it.
  const chrome = $('#chrome');
  const shade = () => chrome.classList.toggle('is-stuck', window.scrollY > 4);
  window.addEventListener('scroll', shade, { passive: true });
  shade();

  $('#btn-advance').onclick = async () => {
    const n = parseInt($('#read-n').value, 10);
    if (!(await save(progress.setPosition(progress.position + n, state.corpus.total_verses)))) return;
    await loadVerses();
    refreshStatus();
  };
  $('#btn-back').onclick = async () => {
    const n = parseInt($('#read-n').value, 10);
    if (!(await save(progress.setPosition(progress.position - n, state.corpus.total_verses)))) return;
    await loadVerses();
    refreshStatus();
  };

  $('#btn-queue').onclick = async () => {
    const saved = await save(progress.addCards(state.newWords, 'reading', new Date()));
    if (!saved) return;
    toast(`${saved.value} card${saved.value === 1 ? '' : 's'} added`);
    refreshStatus();
    renderNewWords();
  };

  $$('[data-core]').forEach((b) => (b.onclick = async () => {
    const rows = await coreWords(parseInt(b.dataset.core, 10));
    if (!Array.isArray(rows)) return;
    const saved = await save(progress.addCards(rows, 'core', new Date()));
    if (!saved) return;
    toast(`${saved.value} core word${saved.value === 1 ? '' : 's'} added`);
    refreshStatus();
    if (state.view === 'drill') loadCard();
    if (state.view === 'vocab') loadCore();
  }));

  $('#btn-export').onclick = exportProgress;
  $('#btn-import').onclick = () => $('#import-file').click();
  $('#import-file').onchange = (e) => importProgress(e.target);
  document.addEventListener('visibilitychange', reloadProgress);

  $('#btn-reveal').onclick = reveal;
  $('#btn-card-say').onclick = () =>
    state.card && play({ lemma_id: state.card.lemma_id }, $('#btn-card-say'));
  $$('[data-grade]').forEach((b) =>
    (b.onclick = () => grade(parseInt(b.dataset.grade, 10))));

  $('#modal-close').onclick = closeModal;
  $('#overlay').onclick = (e) => { if (e.target.id === 'overlay') closeModal(); };

  document.addEventListener('keydown', (e) => {
    if (!$('#overlay').hidden && e.key === 'Escape') return closeModal();
    if (state.view !== 'drill') return;
    if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); reveal(); }
    if (e.key === 's' || e.key === 'S') {
      if (state.card) play({ lemma_id: state.card.lemma_id }, $('#btn-card-say'));
    }
    if (['1', '2', '3', '4'].includes(e.key)) grade(parseInt(e.key, 10) - 1);
  });

  showKeepNote();
  await loadVerses();
  refreshStatus();
}

document.addEventListener('DOMContentLoaded', init);
