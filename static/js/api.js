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
  _accountsPromise: null,
  clearAccountsCache() {
    this._accountsPromise = null;
    sessionStorage.removeItem('tgs_accounts_cache');
  },
  authStatus() { return this.get('/api/auth/status'); },
  getAccounts() {
    if (this._accountsPromise) {
      return this._accountsPromise;
    }
    const cached = sessionStorage.getItem('tgs_accounts_cache');
    if (cached) {
      try {
        this._accountsPromise = Promise.resolve(JSON.parse(cached));
        return this._accountsPromise;
      } catch (e) {
        sessionStorage.removeItem('tgs_accounts_cache');
      }
    }
    const p = this.get('/api/auth/accounts')
      .then(data => {
        if (this._accountsPromise === p) {
          sessionStorage.setItem('tgs_accounts_cache', JSON.stringify(data));
        }
        return data;
      })
      .catch(err => {
        if (this._accountsPromise === p) {
          this._accountsPromise = null;
        }
        throw err;
      });
    this._accountsPromise = p;
    return p;
  },
  async addAccount(data) {
    this.clearAccountsCache();
    return this.post('/api/auth/accounts', data);
  },
  async deleteAccount(id) {
    this.clearAccountsCache();
    return this.del(`/api/auth/accounts/${id}`);
  },
  async togglePremium(id, isPremium) {
    this.clearAccountsCache();
    return this.post(`/api/auth/accounts/${id}/toggle-premium?is_premium=${isPremium}`);
  },
  async toggleAccountActive(id, isActive) {
    this.clearAccountsCache();
    return parseApiResponse(await fetch(`/api/auth/accounts/${id}/toggle-active?is_active=${isActive}`, { method: 'POST' }));
  },
  async setAccountAiAgent(id, aiAgentId) {
    this.clearAccountsCache();
    return parseApiResponse(await fetch(`/api/auth/accounts/${id}/set-ai-agent`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ai_agent_id: aiAgentId ? parseInt(aiAgentId) : null })
    }));
  },
  getDmStats(id) { return this.get(`/api/auth/accounts/${id}/dm-stats`); },
  sendCode(phone, accountId) { return this.post('/api/auth/send-code', { phone, account_id: accountId }); },
  async verify(phone, code, hash, accountId, password) {
    this.clearAccountsCache();
    return this.post('/api/auth/verify', { phone, code, phone_code_hash: hash, account_id: accountId, password });
  },
  async logoutAccount(id) {
    this.clearAccountsCache();
    return this.post(`/api/auth/logout/${id}`);
  },

  // Chats
  getChats(accountId) { return this.get(`/api/chats?account_id=${accountId}`); },

  // Schedules
  getSchedules(params = {}) {
    const q = new URLSearchParams(params).toString();
    return this.get('/api/schedules' + (q ? '?' + q : ''));
  },
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
  autoJoinGroups(account_ids, group_ids) { return this.post('/api/watchers/auto-join', { account_ids, group_ids }); },
  joinChannel(account_id, channel_link) { return this.post('/api/members/join-channel', { account_id, channel_link: String(channel_link) }); },

  // Settings
  getSetting(key) { return this.get(`/api/settings/${key}`); },
  setSetting(key, value) { return this.post(`/api/settings/${key}`, { value }); },

  // AI Remix test (calls backend directly)
  async testRemixDirect(provider, keys, text) {
    // Call backend to do the remix so we use real server-side logic
    return this.post('/api/settings/test-remix', { provider, keys, text });
  },
  // DM Campaigns
  cloneCampaign(id, data) { return MembersAPI.cloneCampaign(id, data); }
};


// ── Generic REST helpers ─────────────────────────────────────────────────────
async function parseApiResponse(r) {
  const text = await r.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch (e) {
    if (!r.ok) {
      throw new Error(`Lỗi máy chủ (${r.status} ${r.statusText})`);
    }
    throw new Error(`Phản hồi máy chủ không hợp lệ`);
  }

  if (!r.ok) {
    const errMsg = data.detail || data.error || data.message || `Lỗi máy chủ (${r.status})`;
    throw new Error(errMsg);
  }

  return data;
}

async function apiGet(path) {
  const headers = API.getHeaders();
  const r = await fetch(path, { headers });
  return parseApiResponse(r);
}
async function apiPost(path, body) {
  const headers = { ...API.getHeaders(), 'Content-Type': 'application/json' };
  const r = await fetch(path, { method: 'POST', headers, body: JSON.stringify(body) });
  return parseApiResponse(r);
}
async function apiPut(path, body) {
  const headers = { ...API.getHeaders(), 'Content-Type': 'application/json' };
  const r = await fetch(path, { method: 'PUT', headers, body: JSON.stringify(body) });
  return parseApiResponse(r);
}
async function apiDelete(path) {
  const headers = API.getHeaders();
  const r = await fetch(path, { method: 'DELETE', headers });
  return parseApiResponse(r);
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


// ── AI Agents API ─────────────────────────────────────────────────────────────
const AIAgentsAPI = {
  getAll:     ()           => apiGet('/api/ai-agents'),
  get:        (id)         => apiGet(`/api/ai-agents/${id}`),
  create:     (data)       => apiPost('/api/ai-agents', data),
  update:     (id, data)   => apiPut(`/api/ai-agents/${id}`, data),
  remove:     (id)         => apiDelete(`/api/ai-agents/${id}`),
  test:       (id, text)   => apiPost(`/api/ai-agents/${id}/test`, { text }),
  duplicate:  (id)         => apiPost(`/api/ai-agents/${id}/duplicate`, {}),
};


// ── Members / Scraping / DM Campaign API ─────────────────────────────────────
const MembersAPI = {
  // Scraping
  startScrape:       (data)       => apiPost('/api/members/scrape', data),
  getScrapeJobs:     ()           => apiGet('/api/members/scrape-jobs'),
  getScrapeMembers:  (jobId, limit = 500, offset = 0) =>
    apiGet(`/api/members/scrape-jobs/${jobId}?limit=${limit}&offset=${offset}`),
  deleteScrapeJob:   (jobId)      => apiDelete(`/api/members/scrape-jobs/${jobId}`),

  // Batch Scraping
  batchScrape:        (data)    => apiPost('/api/members/batch-scrape', data),
  getBatchProgress:   (jobId)   => apiGet(`/api/members/batch-scrape/${jobId}/progress`),
  resolveChannels:    (data)    => apiPost('/api/members/batch-scrape/resolve', data),

  // DM Campaigns
  createCampaign:    (data)       => apiPost('/api/members/campaigns', data),
  getCampaigns:      (updatedSince = null) => {
    const q = updatedSince ? `?updated_since=${encodeURIComponent(updatedSince)}` : '';
    return apiGet('/api/members/campaigns' + q);
  },
  getCampaign:       (id)         => apiGet(`/api/members/campaigns/${id}`),
  startCampaign:     (id)         => apiPost(`/api/members/campaigns/${id}/start`, {}),
  stopCampaign:      (id)         => apiPost(`/api/members/campaigns/${id}/stop`, {}),
  cancelSchedule:    (id)         => apiPost(`/api/members/campaigns/${id}/cancel-schedule`, {}),
  deleteCampaign:    (id)         => apiDelete(`/api/members/campaigns/${id}`),
  cloneCampaign:     (id, data)   => apiPost(`/api/members/campaigns/${id}/clone`, data || {}),
  updateCampaignMessages: (id, data) => apiPut(`/api/members/campaigns/${id}/messages`, data),
  getCampaignLogs:   (id, limit = 200) =>
    apiGet(`/api/members/campaigns/${id}/logs?limit=${limit}`),
};

// ── Invite Campaigns API ─────────────────────────────────────────────────────
const InviteAPI = {
  getCampaigns:      (updatedSince = null) => {
    const q = updatedSince ? `?updated_since=${encodeURIComponent(updatedSince)}` : '';
    return apiGet('/api/invite-campaigns' + q);
  },
  getCampaign:       (id)         => apiGet(`/api/invite-campaigns/${id}`),
  createCampaign:    (data)       => apiPost('/api/invite-campaigns', data),
  updateCampaign:    (id, data)   => apiPut(`/api/invite-campaigns/${id}`, data),
  deleteCampaign:    (id)         => apiDelete(`/api/invite-campaigns/${id}`),
  startCampaign:     (id)         => apiPost(`/api/invite-campaigns/${id}/start`, {}),
  stopCampaign:      (id)         => apiPost(`/api/invite-campaigns/${id}/stop`, {}),
  getCampaignLogs:   (id, limit = 200) =>
    apiGet(`/api/invite-campaigns/${id}/logs?limit=${limit}`),
  resolveGroup:      (identifier) => apiPost('/api/invite-campaigns/resolve-group', { identifier }),
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

