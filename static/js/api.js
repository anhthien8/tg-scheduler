/**
 * API Client - multi-account aware.
 */
const API = {
  base: '',

  async request(method, path, body = null) {
    const opts = {
      method,
      headers: {}
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
  getStats() { return this.get('/api/logs/stats'); }
};
