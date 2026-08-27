var me_dummy = me_dummy || {}; var me_dummy_style = me_dummy_style || {};
/**
 * static/js/ai_followup.js
 * Lead & AI Follow-Up — 2 tab: Leads (default) + Cấu hình AI.
 * Data fetch 1 lần (limit 200), filter + phân trang client-side.
 */

const AIFollowUp = {
  settings: null,
  _chats: [],
  _statusFilter: '',
  _page: 1,
  _modalChat: null,
  PAGE_SIZE: 20,

  STATUS_META: {
    needs_human:  { label: '⚠️ Cần người thật',  badge: 'badge badge-red' },
    active:       { label: '🤖 AI đang chat',    badge: 'badge badge-blue' },
    onboarded:    { label: '✅ Onboarded',        badge: 'badge badge-green' },
    paused_admin: { label: '⏸ Chat tay',          badge: 'badge badge-gray' },
    bot_ignored:  { label: '🚫 Ignored bot',      badge: 'badge badge-gray' }
  },

  async init() {
    await this.loadSettings();
    this.switchTab('leads');
    await this.loadChats();
  },

  // ── Tabs ──────────────────────────────────────────────────────────
  switchTab(tab) {
    const leads = tab === 'leads';
    document.getElementById('aifu-tab-leads')?.classList.toggle('hidden', !leads);
    document.getElementById('aifu-tab-settings')?.classList.toggle('hidden', leads);
    const lb = document.getElementById('aifu-tab-leads-btn');
    const sb = document.getElementById('aifu-tab-settings-btn');
    if (lb) lb.className = leads ? 'btn btn-primary btn-sm' : 'btn btn-ghost btn-sm';
    if (sb) sb.className = leads ? 'btn btn-ghost btn-sm' : 'btn btn-primary btn-sm';
  },

  // ── Settings ──────────────────────────────────────────────────────
  async loadSettings() {
    try {
      const res = await fetch('/api/ai-followup/settings');
      if (!res.ok) throw new Error('Không thể tải cài đặt AI Follow-Up');
      this.settings = await res.json();
      this.renderSettings();
    } catch (e) {
      App.toast(e.message, 'error');
    }
  },

  renderSettings() {
    if (!this.settings) return;
    const enabledEl = document.getElementById('aifu-enabled');
    if (enabledEl) enabledEl.checked = !!this.settings.enabled;
    const promptEl = document.getElementById('aifu-sys-prompt');
    if (promptEl) promptEl.value = this.settings.system_prompt || '';
    const kbEl = document.getElementById('aifu-kb');
    if (kbEl) kbEl.value = this.settings.knowledge_base || '';
    const maxRepliesEl = document.getElementById('aifu-max-replies');
    if (maxRepliesEl) maxRepliesEl.value = this.settings.max_replies_per_user || 5;
    const handoverEl = document.getElementById('aifu-handover-kw');
    if (handoverEl) handoverEl.value = (this.settings.handover_keywords || []).join(', ');
  },

  _collectSettingsForm() {
    return {
      enabled: document.getElementById('aifu-enabled')?.checked || false,
      system_prompt: (document.getElementById('aifu-sys-prompt')?.value || '').trim(),
      knowledge_base: (document.getElementById('aifu-kb')?.value || '').trim(),
      max_replies_per_user: parseInt(document.getElementById('aifu-max-replies')?.value || '5', 10),
      handover_keywords: (document.getElementById('aifu-handover-kw')?.value || '').split(',').map(s => s.trim()).filter(Boolean)
    };
  },

  async _postSettings(payload) {
    const res = await fetch('/api/ai-followup/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (!res.ok) throw new Error('Lưu cài đặt thất bại');
    return res.json();
  },

  async saveSettings() {
    try {
      const payload = this._collectSettingsForm();
      const data = await this._postSettings(payload);
      App.toast(data.message || 'Đã lưu cài đặt AI Sales Agent!', 'success');
      this.settings = payload;
    } catch (e) {
      App.toast(e.message, 'error');
    }
  },

  // Quick toggle từ toolbar — lưu ngay, không cần bấm Lưu
  async quickToggle() {
    const enabled = document.getElementById('aifu-enabled')?.checked || false;
    const s = this.settings || {};
    try {
      await this._postSettings({
        enabled,
        system_prompt: s.system_prompt || '',
        knowledge_base: s.knowledge_base || '',
        max_replies_per_user: s.max_replies_per_user || 5,
        handover_keywords: s.handover_keywords || []
      });
      this.settings = { ...s, enabled };
      App.toast(enabled ? 'Đã BẬT AI Follow-Up' : 'Đã TẮT AI Follow-Up', enabled ? 'success' : 'info');
    } catch (e) {
      App.toast(e.message, 'error');
      this.renderSettings();
    }
  },

  // ── Leads: load + stats + chips + pagination ─────────────────────
  async loadChats() {
    try {
      const res = await fetch('/api/ai-followup/chats?limit=200');
      if (!res.ok) throw new Error('Lỗi tải danh sách lead');
      const data = await res.json();
      this._chats = data.chats || [];
      this._page = 1;
      this.renderStats();
      this.renderChips();
      this.renderPage();
    } catch (e) {
      App.toast(e.message, 'error');
      const tb = document.getElementById('aifu-chats-table-body');
      if (tb) tb.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:20px;color:var(--red)">${esc(e.message)}</td></tr>`;
    }
  },

  renderStats() {
    const count = s => this._chats.filter(c => c.status === s).length;
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    set('aifu-stat-human', count('needs_human'));
    set('aifu-stat-active', count('active'));
    set('aifu-stat-onboarded', count('onboarded'));
    set('aifu-stat-total', this._chats.length);
  },

  renderChips() {
    const el = document.getElementById('aifu-status-chips');
    if (!el) return;
    const chips = [
      ['', 'Tất cả'],
      ['needs_human', '⚠️ Cần người thật'],
      ['active', '🤖 AI đang chat'],
      ['paused_admin', '⏸ Chat tay'],
      ['onboarded', '✅ Onboarded'],
      ['bot_ignored', '🚫 Ignored']
    ];
    el.innerHTML = chips.map(([v, l]) =>
      `<button class="btn btn-sm ${this._statusFilter === v ? 'btn-primary' : 'btn-ghost'}" onclick="AIFollowUp.filterByStatus('${v}')">${l}</button>`
    ).join('');
  },

  filterByStatus(s) {
    this._statusFilter = s;
    this._page = 1;
    this.renderChips();
    this.renderPage();
  },

  _filtered() {
    const s = this._statusFilter;
    return s ? this._chats.filter(c => c.status === s) : this._chats;
  },

  renderPage() {
    const filtered = this._filtered();
    const pages = Math.max(1, Math.ceil(filtered.length / this.PAGE_SIZE));
    if (this._page > pages) this._page = pages;
    this.renderChats(filtered.slice((this._page - 1) * this.PAGE_SIZE, this._page * this.PAGE_SIZE), filtered.length === 0);
    const cnt = document.getElementById('aifu-count');
    if (cnt) cnt.textContent = this._statusFilter
      ? `Hiển thị: ${filtered.length}/${this._chats.length}`
      : (this._chats.length ? `Tổng: ${this._chats.length} lead` : '');
    const pager = document.getElementById('aifu-pager');
    if (pager) pager.innerHTML = pages > 1
      ? `<button class="btn btn-ghost btn-sm" onclick="AIFollowUp.goPage(${this._page - 1})" ${this._page <= 1 ? 'disabled' : ''}>‹ Trước</button><span>Trang ${this._page}/${pages}</span><button class="btn btn-ghost btn-sm" onclick="AIFollowUp.goPage(${this._page + 1})" ${this._page >= pages ? 'disabled' : ''}>Sau ›</button>`
      : '';
  },

  goPage(n) {
    this._page = n;
    this.renderPage();
  },

  renderChats(rows, isEmpty) {
    const tb = document.getElementById('aifu-chats-table-body');
    if (!tb) return;
    if (isEmpty) {
      tb.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:32px"><div style="font-size:2rem;margin-bottom:8px">📭</div><div style="color:var(--text);font-weight:600;margin-bottom:4px">Chưa có lead nào</div><div style="color:var(--text2);font-size:.85rem">Khi có người trả lời DM, AI Follow-Up sẽ tự chat và lead xuất hiện tại đây.</div></td></tr>`;
      return;
    }
    tb.innerHTML = rows.map(c => {
      const meta = this.STATUS_META[c.status] || { label: c.status, badge: 'badge badge-gray' };
      const uName = c.name || (c.username ? `@${c.username}` : `User #${c.user_id}`);
      const tier = c.lead_tier || 'Tier C';
      const score = c.intent_score || 0;
      const tierBadge = tier === 'Tier A'
        ? '<span class="badge" style="background:var(--orange-dim);color:var(--orange);font-weight:700">⭐ A</span>'
        : (tier === 'Tier B' ? '<span class="badge badge-blue">🔷 B</span>' : '<span class="badge badge-gray">⚪ C</span>');
      const updated = c.updated_at ? new Date(c.updated_at).toLocaleString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh' }) : '—';
      const toggleBtn = c.status === 'active'
        ? `<button class="btn btn-ghost btn-sm" onclick="AIFollowUp.updateStatus(${c.account_id},${c.user_id},'paused_admin')" title="Tắt AI, tự chat tay" aria-label="Tắt AI cho ${esc(uName)}">⏸</button>`
        : `<button class="btn btn-ghost btn-sm" onclick="AIFollowUp.updateStatus(${c.account_id},${c.user_id},'active')" title="Bật AI chat tiếp" aria-label="Bật AI cho ${esc(uName)}">▶️</button>`;
      return `<tr>
        <td><div style="font-weight:600">${esc(uName)}</div><div style="font-size:11px;color:var(--text2);font-family:monospace">ID ${c.user_id} • Acc #${c.account_id}</div></td>
        <td><div style="display:flex;align-items:center;gap:6px">${tierBadge}<span style="font-size:11px;color:var(--text2)">${score}%</span></div><div style="width:70px;height:4px;background:rgba(255,255,255,.08);border-radius:2px;overflow:hidden;margin-top:4px"><div style="width:${Math.min(100, Math.max(0, score))}%;height:100%;background:${score >= 80 ? 'var(--orange)' : score >= 50 ? 'var(--accent)' : 'var(--text3)'}"></div></div></td>
        <td>${c.reply_count || 0}</td>
        <td><span class="${meta.badge}">${meta.label}</span></td>
        <td style="font-size:12px;color:var(--text2);white-space:nowrap">${updated}</td>
        <td><div style="display:flex;gap:6px"><button class="btn btn-primary btn-sm" onclick="AIFollowUp.openChat(${c.account_id},${c.user_id})">💬 Xem chat</button>${toggleBtn}</div></td>
      </tr>`;
    }).join('');
  },

  // ── Status update ─────────────────────────────────────────────────
  async updateStatus(accountId, userId, status) {
    try {
      const res = await fetch(`/api/ai-followup/chats/${accountId}/${userId}/status`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status })
      });
      if (!res.ok) throw new Error('Cập nhật trạng thái thất bại');
      const data = await res.json();
      App.toast(data.message || 'Đã cập nhật trạng thái', 'success');
      this.closeHistoryModal();
      this.loadChats();
    } catch (e) {
      App.toast(e.message, 'error');
    }
  },

  // ── Chat modal ────────────────────────────────────────────────────
  openChat(accountId, userId) {
    const c = this._chats.find(x => x.account_id === accountId && x.user_id === userId);
    if (!c) return;
    this._modalChat = c;
    const modal = document.getElementById('aifu-history-modal');
    if (!modal) return;

    const uName = c.name || (c.username ? `@${c.username}` : `User #${c.user_id}`);
    const title = document.getElementById('aifu-modal-title');
    if (title) title.textContent = `💬 Chat với ${uName} (Acc #${accountId})`;

    const box = document.getElementById('aifu-modal-chat-box');
    const tier = c.lead_tier || 'Tier C';
    const score = c.intent_score || 0;
    const summaryBanner = `
      <div style="background:var(--bg3);border:1px solid var(--border-hover);border-radius:var(--radius);padding:12px 14px;margin-bottom:14px">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;gap:8px;flex-wrap:wrap">
          <div style="font-size:12px;font-weight:700;color:var(--accent-text)">📋 TÓM TẮT BỐI CẢNH</div>
          <div style="font-size:11px;font-weight:600;color:${score >= 80 ? 'var(--orange)' : 'var(--accent-text)'}">${esc(tier)} • Intent: ${score}%</div>
        </div>
        <div style="font-size:13px;color:var(--text);line-height:1.5">${esc(c.summary || 'Chưa có tóm tắt nhu cầu khách hàng.')}</div>
      </div>`;

    const history = c.history || [];
    if (!history.length) {
      box.innerHTML = summaryBanner + '<div style="text-align:center;color:var(--text2);padding:20px">Chưa có tin nhắn nào</div>';
    } else {
      box.innerHTML = summaryBanner + history.map(msg => {
        const isUser = msg.role === 'user';
        const timeText = msg.timestamp ? new Date(msg.timestamp).toLocaleTimeString('vi-VN') : '';
        return `
          <div style="display:flex;flex-direction:column;align-items:${isUser ? 'flex-start' : 'flex-end'};margin-bottom:12px">
            <div style="font-size:11px;color:var(--text2);margin-bottom:3px">${isUser ? '👤 ' + esc(uName) : '🤖 AI Sales Agent'} • ${timeText}</div>
            <div style="background:${isUser ? 'var(--accent-dim)' : 'var(--purple-dim)'};border:1px solid var(--border-hover);padding:10px 14px;border-radius:12px;max-width:85%;white-space:pre-wrap;font-size:13px;line-height:1.5">${esc(msg.content || '')}</div>
          </div>`;
      }).join('');
      box.scrollTop = box.scrollHeight;
    }

    this._renderModalActions();
    modal.classList.add('open');
  },

  _renderModalActions() {
    const c = this._modalChat;
    const el = document.getElementById('aifu-modal-actions');
    if (!el || !c) return;
    const toggle = c.status === 'active'
      ? `<button class="btn btn-ghost" onclick="AIFollowUp.updateStatus(${c.account_id},${c.user_id},'paused_admin')">⏸ Chat tay</button>`
      : `<button class="btn btn-ghost" onclick="AIFollowUp.updateStatus(${c.account_id},${c.user_id},'active')">▶️ Bật AI</button>`;
    el.innerHTML = `${toggle}<button class="btn btn-green" onclick="AIFollowUp.updateStatus(${c.account_id},${c.user_id},'onboarded')">✅ Đã onboard</button><button class="btn btn-primary" onclick="AIFollowUp.closeHistoryModal()">Đóng</button>`;
  },

  closeHistoryModal() {
    document.getElementById('aifu-history-modal')?.classList.remove('open');
    this._modalChat = null;
  }
};
