/**
 * API Client - multi-account aware, with optional X-API-Key header support.
 */
const API = {
  base: '',

  /** Returns base headers, injecting X-API-Key from localStorage if configured. */
  getHeaders() {
    const headers = {};
    const apiKey = localStorage.getItem('tgs_api_key');
    if (apiKey) headers['X-API-Key'] = apiKey;
    return headers;
  },

  async request(method, path, body = null) {
    const opts = {
      method,
      headers: this.getHeaders()
    };
    if (body && !(body instanceof FormData)) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    } else if (body instanceof FormData) {
      opts.body = body;
    }
    let res;
    try {
      res = await fetch(this.base + path, opts);
    } catch (e) {
      throw new Error('Không thể kết nối server. Kiểm tra server đang chạy (python main.py)');
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || 'Request failed');
    }
    return res.json();
  },

  get(path) { return this.request('GET', path); },
  post(path, body) { return this.request('POST', path, body); },
  put(path, body) { return this.request('PUT', path, body); },
  patch(path, body) { return this.request('PATCH', path, body); },
  del(path) { return this.request('DELETE', path); },

  // Auth & Accounts
  authStatus() { return this.get('/api/auth/status'); },
  getAccounts() { return this.get('/api/auth/accounts'); },
  addAccount(data) { return this.post('/api/auth/accounts', data); },
  deleteAccount(id) { return this.del(`/api/auth/accounts/${id}`); },
  togglePremium(id, isPremium) { return this.post(`/api/auth/accounts/${id}/toggle-premium?is_premium=${isPremium}`); },
  getDmStats(id) { return this.get(`/api/auth/accounts/${id}/dm-stats`); },
  sendCode(phone, accountId) { return this.post('/api/auth/send-code', { phone, account_id: accountId }); },
  verify(phone, code, hash, accountId, password) {
    return this.post('/api/auth/verify', { phone, code, phone_code_hash: hash, account_id: accountId, password });
  },
  logoutAccount(id) { return this.post(`/api/auth/logout/${id}`); },

  // Chats
  getChats(accountId) { return this.get(`/api/chats?account_id=${accountId}`); },

  // Schedules
  getSchedules() { return this.get('/api/schedules'); },
  getSchedule(id) { return this.get(`/api/schedules/${id}`); },
  createSchedule(data) { return this.post('/api/schedules', data); },
  updateSchedule(id, data) { return this.put(`/api/schedules/${id}`, data); },
  deleteSchedule(id) { return this.del(`/api/schedules/${id}`); },
  toggleSchedule(id) { return this.patch(`/api/schedules/${id}/toggle`); },
  sendNow(id) { return this.post(`/api/schedules/${id}/send-now`); },
  previewSchedule(id) { return this.post(`/api/schedules/${id}/preview`); },
  resetCount(id) { return this.post(`/api/schedules/${id}/reset-count`); },
  getBlockedTargets(id) { return this.get(`/api/schedules/${id}/blocked-targets`); },
  unblockTarget(scheduleId, accountId, chatId) {
    return this.post(`/api/schedules/${scheduleId}/unblock-target?account_id=${accountId}&chat_id=${chatId}`);
  },

  // Upload
  upload(file) {
    const fd = new FormData();
    fd.append('file', file);
    return this.post('/api/upload', fd);
  },

  // Logs
  getLogs(params = {}) {
    const q = new URLSearchParams(params).toString();
    return this.get('/api/logs' + (q ? '?' + q : ''));
  },
  getStats() { return this.get('/api/logs/stats'); },

  // Keyword Watchers
  getWatchers() { return this.get('/api/watchers'); },
  getWatcher(id) { return this.get(`/api/watchers/${id}`); },
  createWatcher(data) { return this.post('/api/watchers', data); },
  updateWatcher(id, data) { return this.put(`/api/watchers/${id}`, data); },
  deleteWatcher(id) { return this.del(`/api/watchers/${id}`); },
  toggleWatcher(id) { return this.post(`/api/watchers/${id}/toggle`); },
  getWatcherLogs(params = {}) {
    const q = new URLSearchParams(params).toString();
    return this.get('/api/watchers/logs' + (q ? '?' + q : ''));
  },
  getWatcherStats() { return this.get('/api/watchers/stats'); },
  testWatcherDM(id, target) { return this.post(`/api/watchers/${id}/test-dm`, { target }); },
  checkMembership(account_ids, group_ids) { return this.post('/api/watchers/check-membership', { account_ids, group_ids }); },

  // Settings
  getSetting(key) { return this.get(`/api/settings/${key}`); },
  setSetting(key, value) { return this.post(`/api/settings/${key}`, { value }); },

  // AI Remix test (calls backend directly)
  async testRemixDirect(provider, keys, text) {
    // Call backend to do the remix so we use real server-side logic
    return this.post('/api/settings/test-remix', { provider, keys, text });
  }
};


// ── Generic REST helpers ─────────────────────────────────────────────────────
async function apiGet(path) {
  const headers = API.getHeaders();
  const r = await fetch(path, { headers });
  return r.json();
}
async function apiPost(path, body) {
  const headers = { ...API.getHeaders(), 'Content-Type': 'application/json' };
  const r = await fetch(path, { method: 'POST', headers, body: JSON.stringify(body) });
  return r.json();
}
async function apiPut(path, body) {
  const headers = { ...API.getHeaders(), 'Content-Type': 'application/json' };
  const r = await fetch(path, { method: 'PUT', headers, body: JSON.stringify(body) });
  return r.json();
}
async function apiDelete(path) {
  const headers = API.getHeaders();
  const r = await fetch(path, { method: 'DELETE', headers });
  return r.json();
}


// ── Reactions API ─────────────────────────────────────────────────────────────
const ReactionsAPI = {
  getTargets: () => apiGet('/api/reactions/targets'),
  addTarget: (data) => apiPost('/api/reactions/targets', data),
  updateTarget: (id, data) => apiPut(`/api/reactions/targets/${id}`, data),
  deleteTarget: (id) => apiDelete(`/api/reactions/targets/${id}`),
  joinTarget: (id) => apiPost(`/api/reactions/targets/${id}/join`, {}),
  getViews: (id, posts = 3) => apiGet(`/api/reactions/targets/${id}/views?posts=${posts}`),
  getLogs: (targetId = null, limit = 100) => {
    const qs = new URLSearchParams({ limit });
    if (targetId !== null) qs.set('target_id', targetId);
    return apiGet(`/api/reactions/logs?${qs}`);
  },
};


// ── Discord API ──────────────────────────────────────────────────────────────
const DiscordAPI = {
  // Bots
  getBots:          ()           => apiGet('/api/discord/bots'),
  addBot:           (data)       => apiPost('/api/discord/bots', data),
  updateBot:        (id, data)   => apiPut(`/api/discord/bots/${id}`, data),
  deleteBot:        (id)         => apiDelete(`/api/discord/bots/${id}`),
  connectBot:       (id)         => apiPost(`/api/discord/bots/${id}/connect`, {}),
  disconnectBot:    (id)         => apiPost(`/api/discord/bots/${id}/disconnect`, {}),
  getBotGuilds:     (id)         => apiGet(`/api/discord/bots/${id}/guilds`),
  // Watchers
  getWatchers:      ()           => apiGet('/api/discord/watchers'),
  createWatcher:    (data)       => apiPost('/api/discord/watchers', data),
  getWatcherLogs:   (params = {}) => {
    const q = new URLSearchParams(params).toString();
    return apiGet('/api/discord/watchers/logs' + (q ? '?' + q : ''));
  },
  // Reactions
  getReactions:     ()           => apiGet('/api/discord/reactions'),
  // Stats
  getStats:         ()           => apiGet('/api/discord/stats'),
};


// ── Members / Scraping / DM Campaign API ─────────────────────────────────────
const MembersAPI = {
  // Scraping
  startScrape:       (data)       => apiPost('/api/members/scrape', data),
  getScrapeJobs:     ()           => apiGet('/api/members/scrape-jobs'),
  getScrapeMembers:  (jobId, limit = 500, offset = 0) =>
    apiGet(`/api/members/scrape-jobs/${jobId}?limit=${limit}&offset=${offset}`),
  deleteScrapeJob:   (jobId)      => apiDelete(`/api/members/scrape-jobs/${jobId}`),

  // DM Campaigns
  createCampaign:    (data)       => apiPost('/api/members/campaigns', data),
  getCampaigns:      ()           => apiGet('/api/members/campaigns'),
  getCampaign:       (id)         => apiGet(`/api/members/campaigns/${id}`),
  startCampaign:     (id)         => apiPost(`/api/members/campaigns/${id}/start`, {}),
  stopCampaign:      (id)         => apiPost(`/api/members/campaigns/${id}/stop`, {}),
  deleteCampaign:    (id)         => apiDelete(`/api/members/campaigns/${id}`),
  getCampaignLogs:   (id, limit = 200) =>
    apiGet(`/api/members/campaigns/${id}/logs?limit=${limit}`),
};


// ── Analytics & Growth Features API ──────────────────────────────────────────
const AnalyticsAPI = {
  overview:            ()           => apiGet('/api/analytics/overview'),
  dailyStats:          (days = 30)  => apiGet(`/api/analytics/daily-stats?days=${days}`),
  accountHealth:       ()           => apiGet('/api/analytics/account-health'),
  campaignPerformance: ()           => apiGet('/api/analytics/campaign-performance'),
  exportMembers:       (jobId)      => `/api/export/members/${jobId}`,
  exportCampaignLogs:  (campId)     => `/api/export/campaign-logs/${campId}`,
  exportContacts:      ()           => '/api/export/contacts',
};

const TemplatesAPI = {
  getAll:   ()           => apiGet('/api/templates'),
  create:   (data)       => apiPost('/api/templates', data),
  update:   (id, data)   => apiPut(`/api/templates/${id}`, data),
  remove:   (id)         => apiDelete(`/api/templates/${id}`),
};

const AutoReplyAPI = {
  getRules:   ()           => apiGet('/api/auto-reply/rules'),
  createRule: (data)       => apiPost('/api/auto-reply/rules', data),
  updateRule: (id, data)   => apiPut(`/api/auto-reply/rules/${id}`, data),
  deleteRule: (id)         => apiDelete(`/api/auto-reply/rules/${id}`),
  toggleRule: (id)         => apiPost(`/api/auto-reply/rules/${id}/toggle`, {}),
  getLogs:    (ruleId, limit = 100) => apiGet(`/api/auto-reply/logs/${ruleId}?limit=${limit}`),
};

