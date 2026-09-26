// Deterministic unit tests for static/js/polling.js — Node stdlib only (node:test + vm fake clock).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Fake-clock harness: loads polling.js into a vm context with fake
// setInterval/clearInterval/setTimeout and a fake `document`.
function harness() {
  let now = 0;
  let nextId = 0;
  const timers = new Map(); // id -> {fn, at, every}
  const errors = [];
  const document = { hidden: false };
  const fakeSetTimeout = (fn, ms) => { const id = ++nextId; timers.set(id, { fn, at: now + (ms || 0), every: null }); return id; };
  const fakeSetInterval = (fn, ms) => { const id = ++nextId; timers.set(id, { fn, at: now + (ms || 0), every: ms || 0 }); return id; };
  const context = vm.createContext({
    document,
    console: { error: (...args) => errors.push(args) },
    setTimeout: fakeSetTimeout,
    clearTimeout: (id) => timers.delete(id),
    setInterval: fakeSetInterval,
    clearInterval: (id) => timers.delete(id),
  });
  const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'polling.js'), 'utf8');
  vm.runInContext(src, context, { filename: 'polling.js' });

  // Drain the microtask queue so promise chains inside Polling settle.
  const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };

  return {
    document,
    timers,
    errors,
    polling: vm.runInContext('Polling', context),
    flush,
    async advance(ms) {
      const end = now + ms;
      for (;;) {
        let dueId = null, due = null;
        for (const [id, t] of timers) {
          if (t.at <= end && (due === null || t.at < due.at)) { dueId = id; due = t; }
        }
        if (!due) break;
        now = due.at;
        if (due.every) due.at = now + due.every; else timers.delete(dueId);
        due.fn();
        await flush();
      }
      now = end;
      await flush();
    },
  };
}

test('start does not invoke immediately; jobs fire on their own intervals', async () => {
  const h = harness();
  let a = 0, b = 0;
  assert.equal(h.polling.start('a', () => { a++; }, 5000), 'a'); // returns key
  h.polling.start('b', () => { b++; }, 10000);
  await h.flush();
  assert.equal(a, 0, 'no immediate invocation');
  assert.equal(b, 0);
  await h.advance(5000);
  assert.equal(a, 1); assert.equal(b, 0);
  await h.advance(5000);
  assert.equal(a, 2); assert.equal(b, 1);
});

test('one shared timer while jobs exist; removed when the last job stops', async () => {
  const h = harness();
  assert.equal(h.timers.size, 0, 'no timer with zero jobs');
  h.polling.start('a', () => {}, 5000);
  h.polling.start('b', () => {}, 10000);
  assert.equal(h.timers.size, 1, 'exactly one shared timer for two jobs');
  h.polling.stop('a');
  assert.equal(h.timers.size, 1, 'timer survives while one job remains');
  h.polling.stop('b');
  assert.equal(h.timers.size, 0, 'timer cleared when last job stops');
  h.polling.stop('b'); // idempotent
  assert.equal(h.timers.size, 0);
});

test('polling pauses while document.hidden, resumes without catch-up burst', async () => {
  const h = harness();
  let n = 0;
  h.polling.start('a', () => { n++; }, 5000);
  await h.advance(5000);
  assert.equal(n, 1);
  h.document.hidden = true;
  await h.advance(60000);
  assert.equal(n, 1, 'no polls while hidden');
  h.document.hidden = false;
  await h.advance(5000);
  assert.equal(n, 2, 'exactly one poll after resume, no burst');
});

test('a slow in-flight run is never overlapped by the next tick of the same job', async () => {
  const h = harness();
  let started = 0, resolveRun;
  h.polling.start('slow', () => { started++; return new Promise((res) => { resolveRun = res; }); }, 5000);
  await h.advance(5000);
  assert.equal(started, 1);
  await h.advance(15000); // 3 more due times pass while first run is in flight
  assert.equal(started, 1, 'no overlapping run started');
  resolveRun();
  await h.flush();
  await h.advance(5000);
  assert.equal(started, 2, 'next run starts after previous completes');
});

test('independent jobs do not block each other while one is in flight', async () => {
  const h = harness();
  let fast = 0;
  h.polling.start('slow', () => new Promise(() => {}), 5000); // never resolves
  h.polling.start('fast', () => { fast++; }, 5000);
  await h.advance(15000);
  assert.equal(fast, 3, 'fast job keeps running while slow job hangs');
});

test('stop during in-flight run, then restart: old run cannot kill the new registration', async () => {
  const h = harness();
  let oldRuns = 0, newRuns = 0, resolveOld;
  h.polling.start('job', () => { oldRuns++; return new Promise((res) => { resolveOld = res; }); }, 5000);
  await h.advance(5000);
  assert.equal(oldRuns, 1);
  h.polling.stop('job');
  h.polling.start('job', () => { newRuns++; }, 5000); // restart while old run is in flight
  resolveOld(); // old request finally completes
  await h.flush();
  assert.equal(h.polling.isActive('job'), true, 'new registration still active');
  await h.advance(5000);
  assert.equal(newRuns, 1, 'new job runs on schedule');
  await h.advance(5000);
  assert.equal(newRuns, 2, 'old completion did not mark the new job as running');
  assert.equal(oldRuns, 1, 'old callback never re-invoked');
});

test('callback errors are caught + logged and the job keeps polling', async () => {
  const seen = [];
  const onUnhandled = (err) => seen.push(err);
  process.on('unhandledRejection', onUnhandled);
  try {
    const h = harness();
    let n = 0;
    h.polling.start('sync-throw', () => { n++; throw new Error('boom-sync'); }, 5000);
    h.polling.start('async-reject', () => { n++; return Promise.reject(new Error('boom-async')); }, 5000);
    await h.advance(10000);
    assert.equal(n, 4, 'both jobs kept polling after errors');
    assert.ok(h.errors.length >= 4, 'errors were logged via console.error');
    await new Promise((res) => setImmediate(res));
    assert.deepEqual(seen, [], 'no unhandled rejections escaped');
  } finally {
    process.removeListener('unhandledRejection', onUnhandled);
  }
});

test('Polling.run invokes a registered job immediately and respects the overlap guard', async () => {
  const h = harness();
  let n = 0, resolveRun;
  h.polling.start('a', () => { n++; return new Promise((res) => { resolveRun = res; }); }, 5000);
  h.polling.run('a');
  await h.flush();
  assert.equal(n, 1, 'run() fires immediately');
  h.polling.run('a'); // still in flight — must not overlap
  await h.flush();
  assert.equal(n, 1, 'run() respects the anti-overlap guard');
  resolveRun();
  await h.flush();
  h.polling.run('missing'); // unknown key is a no-op
  await h.flush();
  assert.equal(n, 1);
});

module.exports = { harness };
