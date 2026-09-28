/**
 * Offline unit test for the split-runtime layer in static/js/api.js.
 *
 * Run:  node test_api_runtime.mjs
 *
 * No browser, no server, no network: fetch/localStorage/sessionStorage are
 * stubbed and api.js is evaluated in a vm sandbox. Asserts the *contract* the
 * dashboard depends on:
 *   - runtime detection caches and degrades safely
 *   - combined mode keeps using the legacy direct routes unchanged
 *   - web mode routes ONLY the migrated slice through /api/ipc
 *   - enqueue is reported as 'queued', never as a fake success
 *   - polling maps succeeded/failed/unknown honestly and never auto-resends
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const SOURCE = fs.readFileSync(new URL('./static/js/api.js', import.meta.url), 'utf8');

function makeSandbox({ routes }) {
  const calls = [];
  const store = new Map();
  const webStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
  };

  async function fetchStub(path, opts = {}) {
    const method = (opts.method || 'GET').toUpperCase();
    calls.push({ method, path, body: opts.body ? JSON.parse(opts.body) : null, headers: opts.headers || {} });
    const key = `${method} ${path.split('?')[0]}`;
    const handler = routes[key] ?? routes[`${method} *`];
    if (!handler) {
      return { ok: false, status: 404, statusText: 'Not Found', json: async () => ({ detail: 'no stub: ' + key }), text: async () => '{"detail":"no stub"}' };
    }
    const res = typeof handler === 'function' ? await handler({ method, path, opts }) : handler;
    const payload = res.body ?? {};
    return {
      ok: res.status === undefined ? true : res.status < 400,
      status: res.status ?? 200,
      statusText: res.statusText ?? 'OK',
      json: async () => payload,
      text: async () => JSON.stringify(payload),
    };
  }

  const sandbox = {
    fetch: fetchStub,
    localStorage: webStorage,
    sessionStorage: {
      getItem: () => null,       // never serve accounts from cache in tests
      setItem: () => {},
      removeItem: () => {},
    },
    FormData: class FormData {},
    setTimeout,
    clearTimeout,
    console,
    calls,
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  // `const API = {...}` is a lexical binding, not a global property: re-export it
  // from inside the same script so the harness can reach it.
  vm.runInContext(
    SOURCE + '\n;globalThis.API = API; globalThis.MembersAPI = MembersAPI;',
    sandbox,
    { filename: 'api.js' },
  );
  return sandbox;
}

const RUNTIME_WEB = { body: { runtime_mode: 'web', worker: { state: 'ready' } } };
const RUNTIME_COMBINED = { body: { runtime_mode: 'combined', worker: { state: 'unknown' } } };

let passed = 0;
async function test(name, fn) {
  try {
    await fn();
    passed += 1;
    console.log(`ok   - ${name}`);
  } catch (err) {
    console.error(`FAIL - ${name}\n      ${err.message}`);
    process.exitCode = 1;
  }
}

// ── runtime detection ────────────────────────────────────────────────────────

await test('detects web runtime and caches the result (single probe)', async () => {
  const s = makeSandbox({ routes: { 'GET /api/health/telegram-worker': RUNTIME_WEB } });
  assert.equal(await s.API.getRuntimeMode(), 'web');
  assert.equal(await s.API.getRuntimeMode(), 'web');
  const probes = s.calls.filter((c) => c.path.startsWith('/api/health/telegram-worker'));
  assert.equal(probes.length, 1, 'runtime probe must be cached, got ' + probes.length);
});

await test('unreachable health endpoint falls back to combined, never throws', async () => {
  const s = makeSandbox({ routes: {} });  // 404 for everything
  assert.equal(await s.API.getRuntimeMode(), 'combined');
});

// ── chats ────────────────────────────────────────────────────────────────────

await test('combined mode: getChats keeps hitting the legacy direct route', async () => {
  const s = makeSandbox({
    routes: {
      'GET /api/health/telegram-worker': RUNTIME_COMBINED,
      'GET /api/chats': { body: { chats: [{ id: 5 }] } },
    },
  });
  const out = await s.API.getChats(1);
  assert.deepEqual(out.chats, [{ id: 5 }]);
  assert.ok(s.calls.some((c) => c.path.startsWith('/api/chats?account_id=1')));
  assert.ok(!s.calls.some((c) => c.path.includes('/api/ipc/')), 'combined must not use IPC');
});

await test('web mode: getChats enqueues and reports queued, not success', async () => {
  const s = makeSandbox({
    routes: {
      'GET /api/health/telegram-worker': RUNTIME_WEB,
      'POST /api/ipc/commands/chats.refresh': { status: 202, body: { command_id: 'c1', status: 'queued' } },
    },
  });
  const out = await s.API.getChats(1);
  assert.equal(out.status, 'queued');
  assert.equal(out.command_id, 'c1');
  assert.ok(!('chats' in out), 'must not fabricate a chat list it never received');
});

// ── campaigns ────────────────────────────────────────────────────────────────

await test('web mode: campaign start/stop enqueue with a positive int id', async () => {
  const s = makeSandbox({
    routes: {
      'GET /api/health/telegram-worker': RUNTIME_WEB,
      'POST /api/ipc/commands/campaign.start': { status: 202, body: { command_id: 'a', status: 'queued' } },
      'POST /api/ipc/commands/campaign.stop': { status: 202, body: { command_id: 'b', status: 'queued' } },
    },
  });
  assert.equal((await s.API.startCampaignRuntime(7)).status, 'queued');
  assert.equal((await s.API.stopCampaignRuntime(7)).status, 'queued');
  const started = s.calls.find((c) => c.path.endsWith('campaign.start'));
  assert.deepEqual(started.body, { campaign_id: 7 });
});

await test('campaign id is validated client-side before any request', async () => {
  const s = makeSandbox({ routes: { 'GET /api/health/telegram-worker': RUNTIME_WEB } });
  for (const bad of [0, -1, 1.5, 'x', null, undefined]) {
    await assert.rejects(() => s.API.startCampaignRuntime(bad), /không hợp lệ/);
  }
});

await test('combined mode: campaign start/stop use the existing members routes', async () => {
  const s = makeSandbox({
    routes: {
      'GET /api/health/telegram-worker': RUNTIME_COMBINED,
      'POST /api/members/campaigns/7/start': { body: { message: 'started' } },
      'POST /api/members/campaigns/7/stop': { body: { message: 'stopped' } },
    },
  });
  assert.equal((await s.API.startCampaignRuntime(7)).message, 'started');
  assert.equal((await s.API.stopCampaignRuntime(7)).message, 'stopped');
  assert.ok(!s.calls.some((c) => c.path.includes('/api/ipc/')));
});

// ── watchers ─────────────────────────────────────────────────────────────────

await test('web mode: watcher list reads the DB-only IPC snapshot', async () => {
  const s = makeSandbox({
    routes: {
      'GET /api/health/telegram-worker': RUNTIME_WEB,
      'GET /api/ipc/watchers': { body: { watchers: [{ id: 3, name: 'w' }] } },
    },
  });
  const rows = await s.API.listWatchersRuntime();
  assert.deepEqual(rows, [{ id: 3, name: 'w' }]);
});

await test('web mode: watcher CREATE is refused loudly (not migrated)', async () => {
  const s = makeSandbox({ routes: { 'GET /api/health/telegram-worker': RUNTIME_WEB } });
  await assert.rejects(() => s.API.createWatcherRuntime({ name: 'x' }), /chưa được migrate/);
  assert.ok(!s.calls.some((c) => c.method === 'POST'), 'must not silently POST anywhere');
});

// ── polling honesty ──────────────────────────────────────────────────────────

await test('pollCommand returns terminal succeeded result', async () => {
  let n = 0;
  const s = makeSandbox({
    routes: {
      'GET /api/health/telegram-worker': RUNTIME_WEB,
      'GET /api/ipc/commands/c1': () => ({
        body: n++ === 0 ? { id: 'c1', status: 'running' } : { id: 'c1', status: 'succeeded', result: { chats: [] } },
      }),
    },
  });
  const out = await s.API.pollCommand('c1', { intervalMs: 1, timeoutMs: 500 });
  assert.equal(out.status, 'succeeded');
  assert.deepEqual(out.result, { chats: [] });
});

await test('pollCommand surfaces failed with the real error text', async () => {
  const s = makeSandbox({
    routes: {
      'GET /api/health/telegram-worker': RUNTIME_WEB,
      'GET /api/ipc/commands/c2': { body: { id: 'c2', status: 'failed', error_text: 'engine refused' } },
    },
  });
  const out = await s.API.pollCommand('c2', { intervalMs: 1, timeoutMs: 500 });
  assert.equal(out.status, 'failed');
  assert.match(out.error_text, /engine refused/);
});

await test('pollCommand treats unknown as terminal and never resends', async () => {
  const s = makeSandbox({
    routes: {
      'GET /api/health/telegram-worker': RUNTIME_WEB,
      'GET /api/ipc/commands/c3': { body: { id: 'c3', status: 'unknown', error_text: 'worker lease expired' } },
    },
  });
  const out = await s.API.pollCommand('c3', { intervalMs: 1, timeoutMs: 500 });
  assert.equal(out.status, 'unknown');
  assert.ok(!s.calls.some((c) => c.method === 'POST'), 'unknown must never trigger a resend');
});

await test('pollCommand times out without claiming an outcome', async () => {
  const s = makeSandbox({
    routes: {
      'GET /api/health/telegram-worker': RUNTIME_WEB,
      'GET /api/ipc/commands/c4': { body: { id: 'c4', status: 'running' } },
    },
  });
  const out = await s.API.pollCommand('c4', { intervalMs: 1, timeoutMs: 30 });
  assert.equal(out.status, 'running');
  assert.equal(out.timed_out, true);
});

console.log(`\n${passed} passed`);
