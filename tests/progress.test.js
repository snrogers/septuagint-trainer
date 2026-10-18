/* Tests for web/progress.js: scheduling, the deck, and exported files.

   Run with:  node --test tests/

   A scheduling rule that quietly resets intervals looks fine on screen but
   corrupts months of study, and the progress now exists nowhere but the
   learner's browser -- so these pin the rules and the round trip through an
   exported file. */

'use strict';

const assert = require('node:assert/strict');
const { describe, test } = require('node:test');

const {
  AGAIN, HARD, GOOD, EASY, MemoryStore, Progress, parseSnapshot, schedule,
} = require('../web/progress.js');

const NOW = new Date('2026-07-26T12:00:00Z');
const card = (fields) => ({
  lemma_id: 1, state: 'new', step: 0, ease: 2.5, interval_days: 0, reps: 0, lapses: 0, ...fields,
});
const minutesAfter = (date, m) => new Date(date.getTime() + m * 60000);

async function deck(lemmas) {
  const progress = new Progress(new MemoryStore());
  await progress.addCards(lemmas, 'core', NOW);
  return progress;
}

describe('scheduling', () => {
  test('a new card graduates after two goods', () => {
    let c = schedule(card(), GOOD, NOW);
    assert.equal(c.state, 'learning');
    c = schedule(c, GOOD, NOW);
    assert.equal(c.state, 'review');
    assert.equal(c.interval_days, 1);
    assert.equal(c.due, '2026-07-27T12:00:00Z');
  });

  test('easy skips learning', () => {
    const c = schedule(card(), EASY, NOW);
    assert.equal(c.state, 'review');
    assert.equal(c.interval_days, 4);
  });

  test('again in learning repeats the first step a minute later', () => {
    const c = schedule(card({ state: 'learning', step: 1 }), AGAIN, NOW);
    assert.equal(c.step, 0);
    assert.equal(c.due, '2026-07-26T12:01:00Z');
  });

  test('hard in learning repeats the current step', () => {
    const c = schedule(card({ state: 'learning', step: 1 }), HARD, NOW);
    assert.equal(c.state, 'learning');
    assert.equal(c.step, 1);
    assert.equal(c.due, '2026-07-26T12:10:00Z');
  });

  test('intervals grow by ease', () => {
    const c = schedule(card({ state: 'review', interval_days: 10 }), GOOD, NOW);
    assert.equal(c.interval_days, 25);
  });

  test('again lapses a review card and penalises its ease', () => {
    const c = schedule(card({ state: 'review', interval_days: 30 }), AGAIN, NOW);
    assert.equal(c.state, 'learning');
    assert.equal(c.lapses, 1);
    assert.ok(Math.abs(c.ease - 2.3) < 1e-9);
    assert.equal(c.interval_days, 15);
    assert.equal(c.due, '2026-07-26T12:10:00Z');
  });

  test('a card that lapsed from 30 days returns to 15, not to 1', () => {
    let c = schedule(card({ state: 'review', interval_days: 30 }), AGAIN, NOW);
    c = schedule(c, GOOD, NOW);
    c = schedule(c, GOOD, NOW);
    assert.equal(c.state, 'review');
    assert.equal(c.interval_days, 15);
  });

  test('ease never drops below 1.3', () => {
    let c = card({ state: 'review', interval_days: 5, ease: 1.35 });
    for (let i = 0; i < 5; i += 1) c = schedule(c, AGAIN, NOW);
    assert.ok(c.ease >= 1.3);
  });

  test('intervals are capped at five years', () => {
    const c = schedule(card({ state: 'review', interval_days: 2000, ease: 3 }), EASY, NOW);
    assert.ok(c.interval_days <= 365 * 5);
  });

  test('every successful grade lengthens the interval', () => {
    let c = card({ state: 'review', interval_days: 1 });
    let previous = c.interval_days;
    for (const g of [HARD, GOOD, HARD, EASY, GOOD]) {
      c = schedule(c, g, NOW);
      assert.ok(c.interval_days > previous);
      previous = c.interval_days;
    }
  });

  test('an out-of-range grade is clamped, not trusted', () => {
    assert.equal(schedule(card(), 99, NOW).state, 'review');
    assert.equal(schedule(card(), -5, NOW).state, 'learning');
  });

  test('the card passed in is left untouched', () => {
    const before = card({ state: 'review', interval_days: 10 });
    schedule(before, AGAIN, NOW);
    assert.deepEqual(before, card({ state: 'review', interval_days: 10 }));
  });
});

describe('deck', () => {
  test('adding cards leaves lemmas already in the deck alone', async () => {
    const progress = await deck([{ lemma_id: 1, freq: 50, rank: 2 }]);
    await progress.review(1, GOOD, NOW);
    const added = await progress.addCards(
      [{ lemma_id: 1, freq: 50, rank: 2 }, { lemma_id: 2, freq: 9, rank: 7 }, { lemma_id: 2, freq: 9, rank: 7 }],
      'reading', NOW,
    );
    assert.equal(added, 1);
    assert.equal(progress.card(1).state, 'learning');
    assert.equal(progress.card(2).origin, 'reading');
  });

  test('new cards come most frequent first, unranked last', async () => {
    const progress = await deck([
      { lemma_id: 3, freq: 1, rank: null },
      { lemma_id: 2, freq: 9, rank: 7 },
      { lemma_id: 1, freq: 50, rank: 2 },
    ]);
    const order = [];
    for (let i = 0; i < 3; i += 1) {
      const next = progress.nextCard(NOW);
      order.push(next.lemma_id);
      await progress.review(next.lemma_id, EASY, NOW);
    }
    assert.deepEqual(order, [1, 2, 3]);
  });

  test('a due card comes before any new card, most overdue first', async () => {
    const progress = await deck([
      { lemma_id: 1, freq: 50, rank: 1 }, { lemma_id: 2, freq: 9, rank: 2 }, { lemma_id: 3, freq: 5, rank: 3 },
    ]);
    await progress.review(2, AGAIN, NOW);
    await progress.review(3, AGAIN, minutesAfter(NOW, -5));
    assert.equal(progress.nextCard(minutesAfter(NOW, 2)).lemma_id, 3);
    assert.equal(progress.queueCounts(minutesAfter(NOW, 2)).due, 2);
  });

  test('the daily limit stops new cards but never due ones', async () => {
    const progress = await deck([{ lemma_id: 1, freq: 5, rank: 1 }, { lemma_id: 2, freq: 5, rank: 2 }]);
    progress.settings.daily_new_limit = 1;
    await progress.review(1, AGAIN, NOW);
    const later = minutesAfter(NOW, 2);
    assert.deepEqual(progress.queueCounts(later), {
      due: 1, new: 1, new_available: 0, new_limit: 1, introduced_today: 1,
    });
    assert.equal(progress.nextCard(later).lemma_id, 1);
    await progress.review(1, EASY, later);
    assert.equal(progress.nextCard(later), null);
    // The limit is per UTC day.
    assert.equal(progress.nextCard(new Date('2026-07-27T00:00:01Z')).lemma_id, 2);
  });

  test('status counts known words, coverage and today\'s reviews', async () => {
    const progress = await deck([{ lemma_id: 1, freq: 50, rank: 1 }, { lemma_id: 2, freq: 25, rank: 2 }]);
    await progress.review(1, EASY, NOW);
    await progress.setPosition(11, 100);
    const s = progress.status({ total_verses: 100, total_freq: 300 }, NOW);
    assert.equal(s.verses_read, 10);
    assert.equal(s.known_words, 1);
    assert.equal(s.studying, 1);
    assert.equal(s.text_coverage, 16.67);
    assert.equal(s.reviews_today, 1);
  });

  test('the position stays within the corpus', async () => {
    const progress = await deck([]);
    await progress.setPosition(-4, 100);
    assert.equal(progress.position, 1);
    await progress.setPosition(500, 100);
    assert.equal(progress.position, 101);
  });

  test('grading a word outside the deck is refused', async () => {
    const progress = await deck([]);
    await assert.rejects(progress.review(42, GOOD, NOW), /not in your deck/);
    assert.equal(progress.reviews.length, 0);
  });

  test('progress survives a reload from its store', async () => {
    const store = new MemoryStore();
    const first = new Progress(store);
    await first.addCards([{ lemma_id: 1, freq: 50, rank: 1 }], 'core', NOW);
    await first.review(1, GOOD, NOW);
    await first.setPosition(7, 100);

    const second = new Progress(store);
    await second.load();
    assert.equal(second.position, 7);
    assert.deepEqual(second.card(1), first.card(1));
    assert.deepEqual(second.stats(), [{ day: '2026-07-26', reviews: 1, again: 0 }]);
  });

  test('a failed write leaves the in-memory progress unchanged', async () => {
    const progress = await deck([{ lemma_id: 1, freq: 50, rank: 1 }]);
    progress.store.recordReview = async () => { throw new Error('quota exceeded'); };
    await assert.rejects(progress.review(1, GOOD, NOW), /quota exceeded/);
    assert.equal(progress.card(1).state, 'new');
    assert.equal(progress.reviews.length, 0);
  });
});

describe('exported files', () => {
  test('an export imports into another browser as the same progress', async () => {
    const source = await deck([{ lemma_id: 1, freq: 50, rank: 1 }, { lemma_id: 2, freq: 9, rank: 2 }]);
    await source.review(1, GOOD, NOW);
    await source.setPosition(12, 100);
    const file = JSON.parse(JSON.stringify(source.snapshot(NOW)));

    const target = await deck([{ lemma_id: 9, freq: 1, rank: 9 }]);
    await target.replace(file);
    assert.equal(target.position, 12);
    assert.deepEqual(target.deckIds().sort(), [1, 2]);
    assert.deepEqual(target.card(1), source.card(1));
    assert.deepEqual(target.reviews, source.reviews);
  });

  test('timestamps from the legacy server are normalised to UTC', () => {
    const data = parseSnapshot({
      format: 'septuagint-progress', version: 1, position: 1, settings: { daily_new_limit: 12 },
      cards: [card({ state: 'learning', due: '2026-08-15T07:00:00-05:00', added_at: null, last_review: null })],
      reviews: [{ lemma_id: 1, reviewed_at: '2026-08-15T23:30:00-05:00', grade: 0, interval_days: 0, ease: 2.5 }],
    });
    assert.equal(data.cards[0].due, '2026-08-15T12:00:00Z');
    assert.equal(data.reviews[0].reviewed_at, '2026-08-16T04:30:00Z');
  });

  test('a file that is not a progress export is refused before anything changes', async () => {
    const progress = await deck([{ lemma_id: 1, freq: 50, rank: 1 }]);
    await assert.rejects(progress.replace({ cards: [] }), /Not a Septuagint progress file/);
    await assert.rejects(progress.replace({ format: 'septuagint-progress', version: 2 }), /version/);
    const bad = progress.snapshot(NOW);
    bad.cards[0].state = 'mastered';
    await assert.rejects(progress.replace(bad), /card state/);
    assert.deepEqual(progress.deckIds(), [1]);
  });
});
