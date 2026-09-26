// Integration tests: verify members.js uses Polling.start/stop correctly.
// Node stdlib only (node:test + vm). No real network / Telegram.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function loadMembers() {
  let nextId = 0;
  const timers = new Map(), errors = [];
  const apiCalls = [];
  const elements = {};
  const document = {
    hidden: false,
    getElementById(id) {
      if (!elements[id]) elements[id] = {
        textContent: '', innerHTML: '', value: '', classList: { add() {}, remove() {} },
        disabled: false, style: {}, options: [], selectedIndex: 0,
      };
      return elements[id];
    },
  };
  const me_dummy = { textContent: '', innerHTML: '', classList: { add() {}, remove() {} } };
  const context = vm.createContext({
    document,
    console: { error: (...args) => errors.push(args), log() {} },
    setInterval: (fn, ms) => { const id = ++nextId; timers.set(id, { fn, ms }); return id; },
    clearInterval: (id) => timers.delete(id),
    setTimeout: (fn, ms) => { const id = ++nextId; timers.set(id, { fn, ms, once: true }); return id; },
    clearTimeout: (id) => timers.delete(id),
    Date: { now: () => 0 },
    me_dummy,
    me_dummy_style: {},
    esc: String,
    App: { toast() {} },
    API: { getAccounts: async () => ({ accounts: [] }) },
    AnalyticsAPI: { exportMembers: () => '' },
    MembersAPI: {
      getScrapeJobs: async () => { apiCalls.push('getScrapeJobs'); return { jobs: [] }; },
      getCampaigns: async (since) => { apiCalls.push('getCampaigns'); return { campaigns: [] }; },
      getBatchProgress: async (id) => { apiCalls.push('getBatchProgress'); return { status: 'done', total_members: 0, done: 0, running: 0, errors: 0, channels: [] }; },
    },
    InviteAPI: {
      getCampaigns: async () => { apiCalls.push('getInviteCampaigns'); return { campaigns: [] }; },
    },
    Promise,
  });
  const load = f => vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'js', f), 'utf8'), context, { filename: f });
  load('polling.js');
  load('members.js');
  const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
  return {
    polling: vm.runInContext('Polling', context),
    members: vm.runInContext('Members', context),
    timers, apiCalls, errors, document, elements, flush,
  };
}

test('members.init() stops any previously active campaign/invite polls', async () => {
  const h = loadMembers();
  // Simulate a running campaign so _pollCampaign starts
  h.members._campaigns = [{ id: 1, status: 'running', updated_at: '' }];
  h.members._pollCampaign();
  assert.ok(h.polling.isActive('members:campaigns'), 'campaign poll started');

  h.members._inviteCampaigns = [{ id: 2, status: 'running', updated_at: '' }];
  h.members._pollInviteCampaign();
  assert.ok(h.polling.isActive('members:inviteCampaigns'), 'invite poll started');

  await h.members.init();
  await h.flush();
  assert.ok(!h.polling.isActive('members:campaigns'), 'campaign poll stopped by init');
  assert.ok(!h.polling.isActive('members:inviteCampaigns'), 'invite poll stopped by init');
});

test('_pollCampaign uses Polling.start with 10s interval', async () => {
  const h = loadMembers();
  h.members._campaigns = [{ id: 1, status: 'running' }];
  h.members._pollCampaign();
  assert.ok(h.polling.isActive('members:campaigns'));
  // Calling again is idempotent
  h.members._pollCampaign();
  assert.ok(h.polling.isActive('members:campaigns'));
});

test('_pollInviteCampaign uses Polling.start with 10s interval', async () => {
  const h = loadMembers();
  h.members._inviteCampaigns = [{ id: 1, status: 'running' }];
  h.members._pollInviteCampaign();
  assert.ok(h.polling.isActive('members:inviteCampaigns'));
});

test('_pollBatchProgress uses Polling.start and never registers a zombie job when initial poll is already done', async () => {
  const h = loadMembers();
  await h.members._pollBatchProgress('test-batch-123');
  await h.flush();
  assert.ok(h.apiCalls.includes('getBatchProgress'), 'initial poll fired');
  // Mock always returns status=done on the initial poll — Polling.start must
  // never be called in that case, else a completed job's poll loop would
  // zombie-register a periodic timer that runs forever.
  assert.ok(!h.polling.isActive('members:batchProgress'), 'no zombie batch poll after immediate completion');
});

test('no setInterval/clearInterval usage remaining in members.js source', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'members.js'), 'utf8');
  assert.ok(!src.includes('setInterval'), 'members.js must not use setInterval');
  assert.ok(!src.includes('clearInterval'), 'members.js must not use clearInterval');
});

test('deep crawl still uses setTimeout for backoff (not Polling)', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'members.js'), 'utf8');
  assert.ok(src.includes('_deepCrawlPollInterval'), 'deep crawl backoff preserved');
  assert.ok(src.includes('setTimeout'), 'deep crawl uses setTimeout');
});
