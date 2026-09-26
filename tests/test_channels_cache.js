'use strict';
/**
 * Regression tests for Channel Manager cache/dedupe/race/invalidation.
 * Runs with Node.js stdlib only (assert + vm). No DOM — stubs only what the code touches.
 * Tests use real code extracted from app.js via fs.readFileSync, NOT duplicated logic.
 */
const assert = require('assert');
const path = require('path');
const fs = require('fs');

// ── Helpers ──

let _fetchMock;
let _domStore;
let _filterCalls;

function resetEnv() {
  _fetchMock = null;
  _domStore = {};
  _filterCalls = 0;
}

function makeSandboxGlobals() {
  // Minimal DOM stubs
  const classList = () => ({ add(){}, remove(){}, toggle(){}, hidden: false });
  const me_dummy = { textContent: '', value: '', innerHTML: '' };

  const doc = {
    getElementById(id) {
      if (!_domStore[id]) {
        _domStore[id] = { value: '', textContent: '', innerHTML: '', classList: classList(), style: {} };
      }
      return _domStore[id];
    },
    querySelectorAll() { return []; },
    createElement() { return { textContent: '', innerHTML: '', className: '' }; }
  };

  return { document: doc, me_dummy, console, setTimeout, clearTimeout, Date, parseInt, String, Array, Set, Map, Math, Error, Promise, fetch: (...a) => _fetchMock(...a) };
}

function buildApp(globals) {
  // Read real app.js and extract only the channel-manager block + helpers
  const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'app.js'), 'utf8').replace(/\r\n/g, '\n');

  // We need: App object init, esc helper, and the CHANNEL MANAGER block
  // Build a minimal runnable script
  const script = `
    var me_dummy = me_dummy || {};
    function esc(s){if(!s)return'';return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
    function debounce(fn, d) { return fn; }

    var App = {
      _accounts: [{id:1, user_info:{first_name:'Test'}}, {id:2, user_info:{first_name:'Test2'}}],
      _chChannels: [],
      _chFiltered: [],
      _chSelected: new Set(),
      _chAccountId: null,
      _dialogsCache: new Map(),
      _dialogsRequests: new Map(),
      toast(){},
      _filterChannels() { _filterCalls++; App._chFiltered = App._chChannels; },
      _updateActionBar() {},
      _showChBanner() {},
      _renderChannelTable() {},
    };

    // Export _filterCalls
    var _filterCalls = 0;
  `;

  // Extract from CHANNEL MANAGER section: from App._DIALOGS_TTL to App._invalidateDialogsCache
  const startMarker = '// ponytail: cache TTL 60s';
  const endMarker = '// ══════════════════════════════════════════════════════════\n//  API KEY MANAGEMENT';
  const startIdx = src.indexOf(startMarker);
  const endIdx = src.indexOf(endMarker);
  if (startIdx === -1 || endIdx === -1) throw new Error('Cannot find CHANNEL MANAGER block in app.js');

  let channelCode = src.slice(startIdx, endIdx).replace(/\r\n/g, '\n');

  // Also extract _populateChAccountSelect
  const popStart = src.indexOf('App._populateChAccountSelect = async function()');
  const popEnd = src.indexOf('\n\n\n\nApp._DIALOGS_TTL', popStart); // before the cache section
  // Actually we need to find end of _populateChAccountSelect
  const popEndAlt = src.indexOf(startMarker, popStart);
  let popCode = '';
  if (popStart !== -1 && popEndAlt !== -1) {
    popCode = src.slice(popStart, popEndAlt).replace(/\r\n/g, '\n');
  }

  // Also extract refreshChannels and _invalidateDialogsCache — they are inside channelCode already

  const vm = require('vm');
  const ctx = vm.createContext({ ...globals, _filterCalls: 0 });
  vm.runInContext(script, ctx);
  if (popCode) vm.runInContext(popCode, ctx);
  vm.runInContext(channelCode, ctx);
  return ctx;
}

// ── Mock fetch factory ──

function mockFetch(status, body, delayMs = 0) {
  return async (url) => {
    if (delayMs > 0) await new Promise(r => setTimeout(r, delayMs));
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    };
  };
}

function mockFetchSequence(responses) {
  let i = 0;
  return async (url) => {
    const r = responses[Math.min(i, responses.length - 1)];
    i++;
    if (r.delay) await new Promise(resolve => setTimeout(resolve, r.delay));
    return {
      ok: r.status >= 200 && r.status < 300,
      status: r.status,
      json: async () => r.body,
    };
  };
}

// ── Test runner ──

const tests = [];
function test(name, fn) { tests.push({ name, fn }); }

// ══════════════════════════════════════════════════════════
// TESTS
// ══════════════════════════════════════════════════════════

test('_fetchDialogs: HTTP 200 + valid schema returns chats', async () => {
  resetEnv();
  const chats = [{ chat_id: 1, chat_title: 'A' }];
  _fetchMock = mockFetch(200, { chats });
  const ctx = buildApp(makeSandboxGlobals());
  const result = await ctx.App._fetchDialogs(1);
  assert.deepStrictEqual(result, chats);
});

test('_fetchDialogs: HTTP 500 throws with detail from error body', async () => {
  resetEnv();
  _fetchMock = mockFetch(500, { detail: 'Server on fire' });
  const ctx = buildApp(makeSandboxGlobals());
  await assert.rejects(() => ctx.App._fetchDialogs(1), /Server on fire/);
});

test('_fetchDialogs: HTTP 200 but invalid schema throws', async () => {
  resetEnv();
  _fetchMock = mockFetch(200, { oops: true });
  const ctx = buildApp(makeSandboxGlobals());
  await assert.rejects(() => ctx.App._fetchDialogs(1), /Schema/);
});

test('_fetchDialogs: HTTP 200 but chats is not array throws', async () => {
  resetEnv();
  _fetchMock = mockFetch(200, { chats: 'not-array' });
  const ctx = buildApp(makeSandboxGlobals());
  await assert.rejects(() => ctx.App._fetchDialogs(1), /Schema/);
});

test('_fetchDialogs: dedupe in-flight — same account returns same promise', async () => {
  resetEnv();
  let callCount = 0;
  _fetchMock = async () => {
    callCount++;
    await new Promise(r => setTimeout(r, 50));
    return { ok: true, status: 200, json: async () => ({ chats: [{ chat_id: 1 }] }) };
  };
  const ctx = buildApp(makeSandboxGlobals());
  const p1 = ctx.App._fetchDialogs(1);
  const p2 = ctx.App._fetchDialogs(1);
  // vm boundary wraps promises, so identity check is unreliable; check callCount instead
  await Promise.all([p1, p2]);
  assert.strictEqual(callCount, 1, 'fetch should be called only once (dedupe)');
});

test('_fetchDialogs: different accounts fetch independently', async () => {
  resetEnv();
  let callCount = 0;
  _fetchMock = async (url) => {
    callCount++;
    const id = url.includes('account_id=1') ? 1 : 2;
    return { ok: true, status: 200, json: async () => ({ chats: [{ chat_id: id }] }) };
  };
  const ctx = buildApp(makeSandboxGlobals());
  const [r1, r2] = await Promise.all([ctx.App._fetchDialogs(1), ctx.App._fetchDialogs(2)]);
  assert.strictEqual(callCount, 2);
  assert.strictEqual(r1[0].chat_id, 1);
  assert.strictEqual(r2[0].chat_id, 2);
});

test('_fetchDialogs: after completion, new call creates new request', async () => {
  resetEnv();
  let callCount = 0;
  _fetchMock = async () => {
    callCount++;
    return { ok: true, status: 200, json: async () => ({ chats: [] }) };
  };
  const ctx = buildApp(makeSandboxGlobals());
  await ctx.App._fetchDialogs(1);
  await ctx.App._fetchDialogs(1);
  assert.strictEqual(callCount, 2, 'After first completes, second should create new request');
});

test('loadChannels: first load fetches + caches', async () => {
  resetEnv();
  const chats = [{ chat_id: 10, chat_title: 'Ch1' }];
  _fetchMock = mockFetch(200, { chats });
  const ctx = buildApp(makeSandboxGlobals());
  _domStore['ch-account-select'] = { value: '1', classList: { add(){}, remove(){} }, textContent: '' };
  await ctx.App.loadChannels();
  assert.deepStrictEqual(ctx.App._chChannels, chats);
  assert.ok(ctx.App._dialogsCache.has(1), 'Cache should be populated');
  assert.deepStrictEqual(ctx.App._dialogsCache.get(1).data, chats);
});

test('loadChannels: second call within TTL uses cache, no fetch', async () => {
  resetEnv();
  let callCount = 0;
  _fetchMock = async () => {
    callCount++;
    return { ok: true, status: 200, json: async () => ({ chats: [{ chat_id: 10 }] }) };
  };
  const ctx = buildApp(makeSandboxGlobals());
  _domStore['ch-account-select'] = { value: '1', classList: { add(){}, remove(){} }, textContent: '' };
  await ctx.App.loadChannels();
  assert.strictEqual(callCount, 1);
  // Second call — should use cache
  await ctx.App.loadChannels();
  assert.strictEqual(callCount, 1, 'No second fetch within TTL');
});

test('loadChannels: force=true bypasses fresh cache', async () => {
  resetEnv();
  let callCount = 0;
  _fetchMock = async () => {
    callCount++;
    return { ok: true, status: 200, json: async () => ({ chats: [{ chat_id: callCount }] }) };
  };
  const ctx = buildApp(makeSandboxGlobals());
  _domStore['ch-account-select'] = { value: '1', classList: { add(){}, remove(){} }, textContent: '' };
  await ctx.App.loadChannels();
  assert.strictEqual(callCount, 1);
  await ctx.App.loadChannels(true); // force
  assert.strictEqual(callCount, 2, 'force=true should bypass cache');
});

test('loadChannels: stale cache shows data immediately + triggers background refresh', async () => {
  resetEnv();
  let callCount = 0;
  const chatsOld = [{ chat_id: 1, chat_title: 'Old' }];
  const chatsNew = [{ chat_id: 1, chat_title: 'New' }, { chat_id: 2, chat_title: 'Added' }];
  _fetchMock = async () => {
    callCount++;
    await new Promise(r => setTimeout(r, 30));
    return { ok: true, status: 200, json: async () => ({ chats: chatsNew }) };
  };
  const ctx = buildApp(makeSandboxGlobals());
  _domStore['ch-account-select'] = { value: '1', classList: { add(){}, remove(){} }, textContent: '' };
  // Seed stale cache
  ctx.App._dialogsCache.set(1, { data: chatsOld, ts: Date.now() - 120000 }); // 120s ago = stale
  ctx.App._chAccountId = 1;
  await ctx.App.loadChannels();
  // Should immediately show old data
  assert.deepStrictEqual(ctx.App._chChannels, chatsOld);
  // Wait for background refresh
  await new Promise(r => setTimeout(r, 80));
  assert.strictEqual(callCount, 1, 'background fetch triggered');
  assert.deepStrictEqual(ctx.App._chChannels, chatsNew, 'channels updated after background refresh');
});

test('loadChannels: account switch during fetch → response discarded', async () => {
  resetEnv();
  _fetchMock = async () => {
    await new Promise(r => setTimeout(r, 50));
    return { ok: true, status: 200, json: async () => ({ chats: [{ chat_id: 99 }] }) };
  };
  const ctx = buildApp(makeSandboxGlobals());
  _domStore['ch-account-select'] = { value: '1', classList: { add(){}, remove(){} }, textContent: '' };
  const p = ctx.App.loadChannels();
  // Simulate user switching to account 2 before fetch completes
  ctx.App._chAccountId = 2;
  await p;
  // Response for account 1 should be discarded — _chChannels stays empty
  assert.strictEqual(ctx.App._chChannels.length, 0, 'Old account response should be discarded');
  assert.ok(!ctx.App._dialogsCache.has(1), 'Should not cache discarded response');
});

test('invalidateDialogsCache: deletes cache and increments epoch', () => {
  resetEnv();
  _fetchMock = mockFetch(200, { chats: [] });
  const ctx = buildApp(makeSandboxGlobals());
  ctx.App._dialogsCache.set(1, { data: [{ chat_id: 1 }], ts: Date.now() });
  const epochBefore = ctx.App._getDialogsEpoch(1);
  ctx.App._invalidateDialogsCache(1);
  assert.ok(!ctx.App._dialogsCache.has(1), 'Cache deleted');
  assert.strictEqual(ctx.App._getDialogsEpoch(1), epochBefore + 1, 'Epoch incremented');
});

test('epoch guard: leave during in-flight background refresh prevents resurrection', async () => {
  resetEnv();
  const chatsBeforeLeave = [{ chat_id: 1 }, { chat_id: 2 }];
  const chatsFromServer = [{ chat_id: 1 }, { chat_id: 2 }]; // server hasn't processed leave yet
  let resolveDelayed;
  _fetchMock = async () => {
    // Delay response to simulate slow fetch
    await new Promise(r => { resolveDelayed = r; });
    return { ok: true, status: 200, json: async () => ({ chats: chatsFromServer }) };
  };
  const ctx = buildApp(makeSandboxGlobals());
  _domStore['ch-account-select'] = { value: '1', classList: { add(){}, remove(){} }, textContent: '' };
  // Seed stale cache to trigger background refresh
  ctx.App._dialogsCache.set(1, { data: chatsBeforeLeave, ts: Date.now() - 120000 });
  ctx.App._chAccountId = 1;

  // loadChannels shows stale, triggers background
  await ctx.App.loadChannels();

  // Simulate leave: remove chat_id=2 from channels, invalidate cache
  ctx.App._chChannels = ctx.App._chChannels.filter(c => c.chat_id !== 2);
  ctx.App._invalidateDialogsCache(1);

  // Now the delayed background response arrives (still has chat_id=2)
  resolveDelayed();
  await new Promise(r => setTimeout(r, 10));

  // chat_id=2 should NOT be resurrected
  const hasResurrected = ctx.App._chChannels.some(c => c.chat_id === 2);
  assert.ok(!hasResurrected, 'Deleted chat should NOT be resurrected by stale background response');
});

test('refreshChannels: clears cache + calls loadChannels(true)', async () => {
  resetEnv();
  let callCount = 0;
  _fetchMock = async () => {
    callCount++;
    return { ok: true, status: 200, json: async () => ({ chats: [{ chat_id: callCount }] }) };
  };
  const ctx = buildApp(makeSandboxGlobals());
  _domStore['ch-account-select'] = { value: '1', classList: { add(){}, remove(){} }, textContent: '' };
  // Pre-populate cache as fresh
  await ctx.App.loadChannels();
  assert.strictEqual(callCount, 1);
  // refreshChannels should bypass fresh cache
  await ctx.App.refreshChannels();
  assert.strictEqual(callCount, 2, 'refreshChannels should force-reload');
  assert.ok(!ctx.App._dialogsCache.has(1) || ctx.App._dialogsCache.get(1).data[0].chat_id === 2,
    'Cache should be updated with new data');
});

test('_populateChAccountSelect: preserves previously selected account', async () => {
  resetEnv();
  const chats = [{ chat_id: 10, chat_title: 'Ch' }];
  _fetchMock = mockFetch(200, { chats });
  const ctx = buildApp(makeSandboxGlobals());
  // Simulate: account 2 was selected, then re-populate is called
  _domStore['ch-account-select'] = {
    value: '2',
    innerHTML: '',
    classList: { add(){}, remove(){} },
    textContent: ''
  };
  ctx.App._chAccountId = 2;
  await ctx.App._populateChAccountSelect();
  // After rebuild, the select should still have value=2 (account 2 exists in _accounts)
  assert.strictEqual(_domStore['ch-account-select'].value, '2', 'Should preserve selected account');
});

test('loadChannels: HTTP error → shows error, does not cache', async () => {
  resetEnv();
  _fetchMock = mockFetch(403, { detail: 'Forbidden' });
  const ctx = buildApp(makeSandboxGlobals());
  _domStore['ch-account-select'] = { value: '1', classList: { add(){}, remove(){} }, textContent: '' };
  await ctx.App.loadChannels();
  assert.ok(!ctx.App._dialogsCache.has(1), 'Should not cache errored response');
  assert.ok(_domStore['ch-loading'].textContent.includes('Forbidden'), 'Error message shown');
});

test('loadChannels: network error → shows error, does not cache', async () => {
  resetEnv();
  _fetchMock = async () => { throw new Error('Network failure'); };
  const ctx = buildApp(makeSandboxGlobals());
  _domStore['ch-account-select'] = { value: '1', classList: { add(){}, remove(){} }, textContent: '' };
  await ctx.App.loadChannels();
  assert.ok(!ctx.App._dialogsCache.has(1), 'Should not cache on network error');
  assert.ok(_domStore['ch-loading'].textContent.includes('Network failure'), 'Error message shown');
});

test('epoch guard: force load after mutation discards pre-mutation response', async () => {
  resetEnv();
  let resolveFirst;
  let callNum = 0;
  _fetchMock = async () => {
    callNum++;
    if (callNum === 1) {
      // First fetch is slow
      await new Promise(r => { resolveFirst = r; });
      return { ok: true, status: 200, json: async () => ({ chats: [{ chat_id: 1 }, { chat_id: 2 }] }) };
    }
    // Second fetch (force after invalidate)
    return { ok: true, status: 200, json: async () => ({ chats: [{ chat_id: 1 }] }) };
  };
  const ctx = buildApp(makeSandboxGlobals());
  _domStore['ch-account-select'] = { value: '1', classList: { add(){}, remove(){} }, textContent: '' };

  // Start first load
  const p1 = ctx.App.loadChannels();
  // Invalidate (simulating a leave success)
  ctx.App._invalidateDialogsCache(1);
  // Force reload (after leave success, code does filterChannels locally)
  const p2 = ctx.App.loadChannels(true);
  // Let the first fetch complete (it has stale data with chat_id=2)
  resolveFirst();
  await p1;
  await p2;

  // Epoch check: first response should be discarded, second should stick
  const hasBoth = ctx.App._chChannels.some(c => c.chat_id === 2);
  assert.ok(!hasBoth, 'Pre-mutation response should be discarded by epoch guard');
});

test('regression: navigate(channels) loads exactly once (no double load)', async () => {
  resetEnv();
  let fetchCount = 0;
  _fetchMock = async () => {
    fetchCount++;
    return { ok: true, status: 200, json: async () => ({ chats: [{ chat_id: 1 }] }) };
  };
  const globals = makeSandboxGlobals();
  const ctx = buildApp(globals);
  _domStore['ch-account-select'] = { value: '1', innerHTML: '', classList: { add(){}, remove(){} }, textContent: '' };

  // Count loadChannels invocations, forwarding to the real implementation
  let loadCalls = 0;
  const realLoad = ctx.App.loadChannels;
  ctx.App.loadChannels = function(...args) { loadCalls++; return realLoad.apply(this, args); };

  // navigate('channels') → _populateChAccountSelect → loadChannels (exactly one chain)
  await ctx.App._populateChAccountSelect();
  await new Promise(r => setTimeout(r, 10));

  assert.strictEqual(loadCalls, 1, 'loadChannels must run exactly once per navigation');
  assert.strictEqual(fetchCount, 1, '/api/chats must be hit exactly once');
});

// ══════════════════════════════════════════════════════════
// Run all tests
// ══════════════════════════════════════════════════════════

(async () => {
  let passed = 0, failed = 0;
  for (const t of tests) {
    try {
      await t.fn();
      passed++;
      console.log(`  ✓ ${t.name}`);
    } catch (e) {
      failed++;
      console.error(`  ✗ ${t.name}`);
      console.error(`    ${e.message}`);
      if (e.stack) {
        const lines = e.stack.split('\n').filter(l => l.includes('test_channels_cache'));
        if (lines.length) console.error(`    ${lines[0].trim()}`);
      }
    }
  }
  console.log(`\n${passed}/${tests.length} passed, ${failed} failed`);
  process.exit(failed > 0 ? 1 : 0);
})();
