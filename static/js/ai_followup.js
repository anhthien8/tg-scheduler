var me_dummy = me_dummy || {}; var me_dummy_style = me_dummy_style || {};
/**
 * static/js/ai_followup.js
 * AI Agent — 2 tab: Leads (default) + Cấu hình AI.
 * Data fetch 1 lần (limit 200), filter + phân trang client-side.
 */

const AIFollowUp = {
  settings: null,
  _chats: [],
  _chatsLoadedAt: 0,   // thời điểm fetch chats gần nhất (để cache 30s)
  _statusFilter: '',
  _stepFilter: '',     // lọc theo kol_step ('' = tất cả, 'link_confirmed+' = từ bước 3 trở lên)
  _page: 1,
  _modalChat: null,
  _modalStep: null,    // state 5 bước của lead đang mở (từ /step)
  _selected: new Set(),
  _ladder: null,       // thang commission đang chỉnh trong form
  PAGE_SIZE: 20,

  STATUS_META: {
    needs_human:  { label: '⚠️ Cần người thật',  badge: 'badge badge-red' },
    active:       { label: '🤖 AI đang chat',    badge: 'badge badge-blue' },
    onboarded:    { label: '✅ Onboarded (cũ)',   badge: 'badge badge-green' },
    paused_admin: { label: '⏸ Chat tay',          badge: 'badge badge-gray' },
    bot_ignored:  { label: '🚫 Ignored bot',      badge: 'badge badge-gray' }
  },

  // 5 bước onboarding KOL (kol_step) + 2 trạng thái kết thúc. Thứ tự = thứ tự funnel.
  STEPS: ['prospect', 'deal_agreed', 'uid_received', 'link_confirmed', 'first_post', 'active'],
  STEP_META: {
    prospect:       { label: 'Chưa chốt',     emoji: '⚪', color: 'var(--text3)' },
    deal_agreed:    { label: 'Đồng ý deal',   emoji: '🤝', color: 'var(--accent)' },
    uid_received:   { label: 'Nhận UID',      emoji: '🆔', color: 'var(--accent)' },
    link_confirmed: { label: 'Đã gắn link',   emoji: '🔗', color: 'var(--green)' },
    first_post:     { label: 'Bài đầu tiên',  emoji: '📝', color: 'var(--green)' },
    active:         { label: 'Hoạt động',     emoji: '🟢', color: 'var(--green)' },
    declined:       { label: 'Từ chối',       emoji: '⛔', color: 'var(--red)' },
    dormant:        { label: 'Ngủ đông',      emoji: '💤', color: 'var(--text3)' }
  },
  MISSING_LABELS: {
    deal_agreement: 'KOL đồng ý thang', weex_uid: 'WEEX UID', vip_code: 'vipCode (ops)',
    affiliate_link: 'Link affiliate (ops)', link_confirmation: 'Xác nhận đã gắn link (ops)',
    selected_campaign: 'Chọn campaign', first_post_link: 'Link bài đầu tiên', volume_verified: 'Volume hợp lệ kỳ đầu (ops)'
  },

  stepOf(c) { return (c && c.kol_step) || 'prospect'; },
  stepRank(s) { const i = this.STEPS.indexOf(s || 'prospect'); return i; },
  stepLabel(s) { const m = this.STEP_META[s] || { label: s, emoji: '' }; return `${m.emoji} ${m.label}`.trim(); },
  stepBar(s) {
    const r = this.stepRank(s);
    if (r < 0) return this.stepLabel(s);
    return '●'.repeat(r) + '○'.repeat(5 - r) + ` ${r}/5`;
  },

  async init(force = false) {
    this.switchTab('leads');
    // Cache: chỉ fetch lại 86KB chats nếu quá 30s hoặc bị yêu cầu (nút 🔄 / force).
    const fresh = this._chatsLoadedAt && (Date.now() - this._chatsLoadedAt) < 30000 && this._chats.length;
    const tasks = [this.loadSettings(), this.loadKolChannelSettings(), this.loadReferrals(), this.loadLadder()];
    if (force || !fresh) {
      tasks.push(this.loadChats());
    } else {
      // Dùng lại dữ liệu sẵn có — không fetch lại, render tức thì.
      this.renderStats();
      this.renderChips();
      this.renderPage();
    }
    await Promise.all(tasks);
  },

  // ── Thang commission KOL (kol_offer_ladder) ─────────────────────────
  async loadLadder() {
    try {
      const res = await fetch('/api/ai-followup/offer-ladder');
      if (!res.ok) throw new Error('Không thể tải thang commission');
      const d = await res.json();
      this._ladder = d.ladder;
      this._ladderPreview = d.prompt_preview || '';
      this._ladderIsDefault = !!d.is_default;
      this.renderLadder();
    } catch (e) {
      const tb = document.getElementById('aifu-ladder-tiers');
      if (tb) tb.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--red);padding:12px">${esc(e.message)}</td></tr>`;
    }
  },

  renderLadder() {
    const L = this._ladder;
    if (!L) return;
    const meta = document.getElementById('aifu-ladder-meta');
    if (meta) meta.textContent = `v${L.version || 1} · hiệu lực ${L.effective_from || '—'}${this._ladderIsDefault ? ' · mặc định' : ''}`;
    const tb = document.getElementById('aifu-ladder-tiers');
    if (tb) {
      const num = v => (v === null || v === undefined) ? '' : String(Number(v));
      tb.innerHTML = (L.tiers || []).map((t, i) => `<tr>
        <td style="text-align:center;font-weight:700">${i + 1}</td>
        <td><input type="number" class="form-input" data-ladder-tier="${i}" data-field="min_volume_m" value="${num(t.min_volume_m)}" min="0" step="1" ${i === 0 ? 'readonly title="Bậc 1 luôn là khởi điểm (0)"' : ''}></td>
        <td><input type="number" class="form-input" data-ladder-tier="${i}" data-field="revshare_pct" value="${num(t.revshare_pct)}" min="1" max="100" step="1"></td>
        <td><input type="number" class="form-input" data-ladder-tier="${i}" data-field="bonus_per_1m_taker_usd" value="${num(t.bonus_per_1m_taker_usd)}" min="0" step="1"></td>
        <td><input type="text" class="form-input" data-ladder-tier="${i}" data-field="label" value="${esc(t.label || '')}" placeholder="VD: khởi điểm, cao hơn affiliate thường 5%"></td>
        <td style="text-align:center">${i > 0 ? `<button class="btn btn-ghost btn-sm" title="Xoá bậc" onclick="AIFollowUp.ladderRemoveTier(${i})">✕</button>` : ''}</td>
      </tr>`).join('');
    }
    const el = id => document.getElementById(id);
    if (el('ladder-headline')) el('ladder-headline').value = L.headline || '';
    if (el('ladder-effective-from')) el('ladder-effective-from').value = L.effective_from || '';
    const R = L.rules || {};
    ['upgrade', 'downgrade', 'threshold_basis', 'bonus_basis', 'commission_payout', 'bonus_payout', 'exclusions', 'sub_affiliate', 'bonus_dip_explanation'].forEach(k => {
      const f = el(`ladder-rule-${k}`); if (f) f.value = R[k] || '';
    });
    if (el('ladder-rule-why_us')) el('ladder-rule-why_us').value = (R.why_us || []).join('\n');
    const pre = el('aifu-ladder-preview');
    if (pre) pre.textContent = this._ladderPreview || '';
  },

  _collectLadder() {
    const L = JSON.parse(JSON.stringify(this._ladder || { tiers: [], rules: {} }));
    const tiers = [];
    document.querySelectorAll('[data-ladder-tier]').forEach(inp => {
      const i = Number(inp.dataset.ladderTier);
      tiers[i] = tiers[i] || { tier: i + 1, label: '' };
      const f = inp.dataset.field;
      tiers[i][f] = f === 'label' ? inp.value.trim() : Number(inp.value);
    });
    L.tiers = tiers.filter(Boolean).map((t, i) => ({ ...t, tier: i + 1 }));
    const el = id => document.getElementById(id);
    L.headline = (el('ladder-headline')?.value || '').trim();
    L.effective_from = (el('ladder-effective-from')?.value || '').trim();
    L.rules = L.rules || {};
    ['upgrade', 'downgrade', 'threshold_basis', 'bonus_basis', 'commission_payout', 'bonus_payout', 'exclusions', 'sub_affiliate', 'bonus_dip_explanation'].forEach(k => {
      L.rules[k] = (el(`ladder-rule-${k}`)?.value || '').trim();
    });
    L.rules.why_us = (el('ladder-rule-why_us')?.value || '').split('\n').map(s => s.trim()).filter(Boolean);
    return L;
  },

  ladderAddTier() {
    const L = this._collectLadder();
    const last = L.tiers[L.tiers.length - 1] || { min_volume_m: 0, revshare_pct: 50, bonus_per_1m_taker_usd: 0 };
    L.tiers.push({ tier: L.tiers.length + 1, min_volume_m: Number(last.min_volume_m) * 2 || 10, revshare_pct: Math.min(100, Number(last.revshare_pct) + 5), bonus_per_1m_taker_usd: Number(last.bonus_per_1m_taker_usd), label: '' });
    this._ladder = L; this.renderLadder();
  },

  ladderRemoveTier(i) {
    const L = this._collectLadder();
    if (i <= 0 || L.tiers.length <= 1) return;
    L.tiers.splice(i, 1);
    this._ladder = L; this.renderLadder();
  },

  ladderResetDefault() {
    if (!window.confirm('Nạp lại thang mặc định (55/60/65/70/80%) vào form? Chưa lưu cho tới khi bấm Lưu.')) return;
    this._ladder = {
      ...(this._ladder || {}), headline: 'up to 80% revshare',
      tiers: [
        { tier: 1, min_volume_m: 0, revshare_pct: 55, bonus_per_1m_taker_usd: 100, label: 'Starting tier (5% above a standard affiliate)' },
        { tier: 2, min_volume_m: 10, revshare_pct: 60, bonus_per_1m_taker_usd: 100, label: '' },
        { tier: 3, min_volume_m: 20, revshare_pct: 65, bonus_per_1m_taker_usd: 100, label: '' },
        { tier: 4, min_volume_m: 50, revshare_pct: 70, bonus_per_1m_taker_usd: 50, label: '' },
        { tier: 5, min_volume_m: 100, revshare_pct: 80, bonus_per_1m_taker_usd: 10, label: '' }
      ]
    };
    this.renderLadder();
  },

  async saveLadder() {
    const ladder = this._collectLadder();
    if (!window.confirm(`Lưu thang commission mới (v${(Number(this._ladder?.version) || 1) + 1})? AI sẽ dùng thang này ngay cho MỌI KOL.`)) return;
    try {
      const res = await fetch('/api/ai-followup/offer-ladder', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ladder }) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Lưu thang thất bại');
      this._ladder = data.ladder; this._ladderPreview = data.prompt_preview || ''; this._ladderIsDefault = false;
      this.renderLadder();
      App.toast(data.message || 'Đã lưu thang', 'success');
      const hist = document.getElementById('aifu-ladder-history');
      if (hist && !hist.classList.contains('hidden')) this.loadLadderHistory(false);
    } catch (e) { App.toast(e.message, 'error'); }
  },

  toggleLadderPreview() {
    const pre = document.getElementById('aifu-ladder-preview');
    if (!pre) return;
    pre.textContent = this._ladderPreview || '';
    pre.classList.toggle('hidden');
  },

  async loadLadderHistory(toggle) {
    const box = document.getElementById('aifu-ladder-history');
    if (!box) return;
    if (toggle && !box.classList.contains('hidden')) { box.classList.add('hidden'); return; }
    box.classList.remove('hidden');
    box.textContent = 'Đang tải...';
    try {
      const res = await fetch('/api/ai-followup/offer-ladder/history?limit=20');
      const d = await res.json();
      const rows = d.history || [];
      if (!rows.length) { box.textContent = 'Chưa có phiên bản nào được lưu (đang dùng thang mặc định).'; return; }
      box.innerHTML = '<div style="font-weight:600;margin-bottom:4px">Lịch sử phiên bản</div>' + rows.map(h => {
        let tiers = '';
        try { const j = h.ladder || JSON.parse(h.ladder_json || '{}'); tiers = (j.tiers || []).map(t => `${Number(t.revshare_pct)}%`).join('/'); } catch (_) {}
        return `<div>• v${h.version} · ${esc(h.created_at || '')} · ${esc(h.actor || '')} · ${esc(tiers)}</div>`;
      }).join('');
    } catch (e) { box.textContent = 'Lỗi tải lịch sử: ' + e.message; }
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
      if (!res.ok) throw new Error('Không thể tải cài đặt AI Agent');
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
    const cadenceEl = document.getElementById('aifu-drip-cadence');
    if (cadenceEl) cadenceEl.value = (this.settings.drip_cadence_hours || [24, 72, 168]).join(', ');
    const tierAEl = document.getElementById('aifu-drip-tier-a');
    if (tierAEl) tierAEl.value = (this.settings.drip_cadence_tier_a || [12, 24, 48, 96]).join(', ');
    const tierBEl = document.getElementById('aifu-drip-tier-b');
    if (tierBEl) tierBEl.value = (this.settings.drip_cadence_tier_b || [24, 72, 168]).join(', ');
    const tierCEl = document.getElementById('aifu-drip-tier-c');
    if (tierCEl) tierCEl.value = (this.settings.drip_cadence_tier_c || [48, 168]).join(', ');
  },

  // ── KOL Channel Auto-Forward Settings ──────────────────────────────
  async loadKolChannelSettings() {
    try {
      const res = await fetch('/api/ai-followup/kol-channel-settings');
      if (!res.ok) return;
      const d = await res.json();
      const el = id => document.getElementById(id);
      if (el('kolch-enabled')) el('kolch-enabled').checked = !!d.enabled;
      if (el('kolch-source')) el('kolch-source').value = d.source || '';
      if (el('kolch-account')) el('kolch-account').value = d.account_id || '';
    } catch (_) {}
  },

  async saveKolChannelSettings() {
    try {
      const el = id => document.getElementById(id);
      const res = await fetch('/api/ai-followup/kol-channel-settings', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          enabled: el('kolch-enabled')?.checked || false,
          source: (el('kolch-source')?.value || '').trim(),
          account_id: (el('kolch-account')?.value || '').trim()
        })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Lưu thất bại');
      App.toast(data.message || 'Đã lưu', 'success');
    } catch (e) { App.toast(e.message, 'error'); }
  },

  // ── C5: Referral / Viral Loop KOL ──────────────────────────────────
  async loadReferrals() {
    const tb = document.getElementById('aifu-ref-codes-body');
    try {
      const [codesRes, statsRes] = await Promise.all([
        fetch('/api/referrals/codes'),
        fetch('/api/referrals/stats')
      ]);
      if (statsRes.ok) {
        const st = await statsRes.json();
        const stEl = document.getElementById('aifu-ref-stats');
        if (stEl) {
          stEl.innerHTML = `<span>Mã đang hoạt động: <strong style="color:var(--text)">${st.active_codes || 0}</strong></span>`
            + `<span>Lượt giới thiệu: <strong style="color:#60a5fa">${st.total_attributions || 0}</strong></span>`
            + `<span>Đã chốt (Signed): <strong style="color:#4ade80">${st.conversions || 0}</strong> (${st.conversion_rate || 0}%)</span>`;
        }
      }
      if (codesRes.ok) {
        const d = await codesRes.json();
        const codes = d.codes || [];
        if (!tb) return;
        if (!codes.length) {
          tb.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text2);padding:12px">Chưa có mã giới thiệu nào</td></tr>';
          return;
        }
        tb.innerHTML = codes.map(c => `<tr>
          <td><code style="color:#a5b4fc;font-weight:700">${esc(c.code)}</code></td>
          <td>${c.referrer_user_id} <small style="color:var(--text2)">(Acc #${c.account_id})</small></td>
          <td>${c.uses_count || 0} / ${c.max_uses || 10}</td>
          <td>${c.is_active ? '<span class="badge badge-green">Active</span>' : '<span class="badge badge-gray">Inactive</span>'}</td>
          <td>${c.is_active ? `<button class="btn btn-ghost btn-sm" style="color:#f87171" onclick="AIFollowUp.deactivateReferralCode(${c.id})">Vô hiệu</button>` : '—'}</td>
        </tr>`).join('');
      } else if (tb) {
        tb.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text2);padding:12px">Chưa có mã giới thiệu nào</td></tr>';
      }
    } catch (_) {
      if (tb) tb.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text2);padding:12px">Chưa có mã giới thiệu nào</td></tr>';
    }
  },

  async createReferralCode() {
    const account_id = parseInt(document.getElementById('ref-acc-id')?.value || '1', 10);
    const referrer_user_id = parseInt(document.getElementById('ref-user-id')?.value || '0', 10);
    const max_uses = parseInt(document.getElementById('ref-max-uses')?.value || '10', 10);
    if (!referrer_user_id) {
      App.toast('Vui lòng nhập KOL User ID', 'error');
      return;
    }
    try {
      const res = await fetch('/api/referrals/codes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ account_id, referrer_user_id, max_uses })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Tạo mã thất bại');
      App.toast(`Đã tạo mã giới thiệu: ${data.code}`, 'success');
      const uidEl = document.getElementById('ref-user-id');
      if (uidEl) uidEl.value = '';
      this.loadReferrals();
    } catch (e) {
      App.toast(e.message, 'error');
    }
  },

  async deactivateReferralCode(id) {
    try {
      const res = await fetch(`/api/referrals/codes/${id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error('Không thể vô hiệu mã');
      App.toast('Đã vô hiệu mã giới thiệu', 'success');
      this.loadReferrals();
    } catch (e) {
      App.toast(e.message, 'error');
    }
  },

  _parseHoursInput(id, fallback) {
    const raw = (document.getElementById(id)?.value || '')
      .split(',').map(s => parseInt(s.trim(), 10)).filter(n => Number.isFinite(n) && n > 0);
    return raw.length ? raw : fallback;
  },

  _collectSettingsForm() {
    return {
      enabled: document.getElementById('aifu-enabled')?.checked || false,
      system_prompt: (document.getElementById('aifu-sys-prompt')?.value || '').trim(),
      knowledge_base: (document.getElementById('aifu-kb')?.value || '').trim(),
      max_replies_per_user: parseInt(document.getElementById('aifu-max-replies')?.value || '5', 10),
      handover_keywords: (document.getElementById('aifu-handover-kw')?.value || '').split(',').map(s => s.trim()).filter(Boolean),
      drip_cadence_hours: this._parseHoursInput('aifu-drip-cadence', [24, 72, 168]),
      drip_cadence_tier_a: this._parseHoursInput('aifu-drip-tier-a', [12, 24, 48, 96]),
      drip_cadence_tier_b: this._parseHoursInput('aifu-drip-tier-b', [24, 72, 168]),
      drip_cadence_tier_c: this._parseHoursInput('aifu-drip-tier-c', [48, 168])
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
        handover_keywords: s.handover_keywords || [],
        drip_cadence_hours: s.drip_cadence_hours || [24, 72, 168],
        drip_cadence_tier_a: s.drip_cadence_tier_a || [12, 24, 48, 96],
        drip_cadence_tier_b: s.drip_cadence_tier_b || [24, 72, 168],
        drip_cadence_tier_c: s.drip_cadence_tier_c || [48, 168]
      });
      this.settings = { ...s, enabled };
      App.toast(enabled ? 'Đã BẬT AI Agent' : 'Đã TẮT AI Agent', enabled ? 'success' : 'info');
    } catch (e) {
      App.toast(e.message, 'error');
      this.renderSettings();
    }
  },

  // ── Leads: load + stats + chips + pagination ─────────────────────
  async loadChats() {
    try {
      const res = await fetch('/api/ai-followup/chats?limit=200&include_history=false');
      if (!res.ok) throw new Error('Lỗi tải danh sách lead');
      const data = await res.json();
      this._chats = data.chats || [];
      this._chatsLoadedAt = Date.now();
      this._page = 1;
      this.renderStats();
      this.renderChips();
      this.renderPage();
    } catch (e) {
      App.toast(e.message, 'error');
      const tb = document.getElementById('aifu-chats-table-body');
      if (tb) tb.innerHTML = `<tr><td colspan="8" style="text-align:center;padding:20px;color:var(--red)">${esc(e.message)}</td></tr>`;
    }
  },

  renderStats() {
    const count = s => this._chats.filter(c => c.status === s).length;
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    set('aifu-stat-human', count('needs_human'));
    set('aifu-stat-active', count('active'));
    set('aifu-stat-onboarded', this._chats.filter(c => this.stepRank(this.stepOf(c)) >= 3).length);
    set('aifu-stat-total', this._chats.length);
    set('aifu-stat-nudged', this._chats.filter(c => c.nudged).length);
    this.renderStepCards();
  },

  renderStepCards() {
    const el = document.getElementById('aifu-step-cards');
    if (!el) return;
    const counts = {};
    this._chats.forEach(c => { const s = this.stepOf(c); counts[s] = (counts[s] || 0) + 1; });
    const keys = [...this.STEPS, 'declined', 'dormant'].filter(k => this.STEPS.includes(k) || counts[k]);
    el.innerHTML = keys.map((k, i) => {
      const m = this.STEP_META[k];
      const active = this._stepFilter === k;
      const idx = this.STEPS.includes(k) ? `${i}.` : '';
      return `<div class="stat-card" style="cursor:pointer;padding:10px 12px;${active ? 'border-color:var(--accent)' : ''}" onclick="AIFollowUp.filterByStep('${k}')" title="Bấm để lọc theo bước">
        <div class="stat-label" style="font-size:11px">${idx} ${m.emoji} ${m.label}</div>
        <div class="stat-value" style="font-size:20px;color:${m.color}">${counts[k] || 0}</div>
      </div>`;
    }).join('');
  },

  renderChips() {
    const el = document.getElementById('aifu-status-chips');
    if (!el) return;
    const chips = [
      ['', 'Tất cả'],
      ['needs_human', '⚠️ Cần người thật'],
      ['active', '🤖 AI đang chat'],
      ['paused_admin', '⏸ Chat tay'],
      ['bot_ignored', '🚫 Ignored']
    ];
    let html = chips.map(([v, l]) =>
      `<button class="btn btn-sm ${this._statusFilter === v && !this._stepFilter ? 'btn-primary' : 'btn-ghost'}" onclick="AIFollowUp.filterByStatus('${v}')">${l}</button>`
    ).join('');
    if (this._stepFilter) {
      const lbl = this._stepFilter === 'link_confirmed+' ? '🔗 Đã gắn link trở lên' : this.stepLabel(this._stepFilter);
      html += `<button class="btn btn-sm btn-primary" onclick="AIFollowUp.filterByStep('')" title="Bỏ lọc bước">Bước: ${lbl} ✕</button>`;
    }
    el.innerHTML = html;
  },

  filterByStatus(s) {
    this._statusFilter = s;
    this._stepFilter = '';
    this._page = 1;
    this.renderChips();
    this.renderStepCards();
    this.renderPage();
  },

  filterByStep(s) {
    this._stepFilter = (this._stepFilter === s) ? '' : s;
    this._statusFilter = '';
    this._page = 1;
    this.renderChips();
    this.renderStepCards();
    this.renderPage();
  },

  _filtered() {
    const s = this._statusFilter;
    let rows = s ? this._chats.filter(c => c.status === s) : this._chats;
    const st = this._stepFilter;
    if (st === 'link_confirmed+') rows = rows.filter(c => this.stepRank(this.stepOf(c)) >= 3);
    else if (st) rows = rows.filter(c => this.stepOf(c) === st);
    return rows;
  },

  renderPage() {
    const filtered = this._filtered();
    const pages = Math.max(1, Math.ceil(filtered.length / this.PAGE_SIZE));
    if (this._page > pages) this._page = pages;
    this.renderChats(filtered.slice((this._page - 1) * this.PAGE_SIZE, this._page * this.PAGE_SIZE), filtered.length === 0);
    this.renderSelectionBar();
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
      tb.innerHTML = `<tr><td colspan="8" style="text-align:center;padding:32px"><div style="font-size:2rem;margin-bottom:8px">📭</div><div style="color:var(--text);font-weight:600;margin-bottom:4px">Chưa có lead nào</div><div style="color:var(--text2);font-size:.85rem">Khi có người trả lời DM, AI Agent sẽ tự chat và lead xuất hiện tại đây.</div></td></tr>`;
      return;
    }
    tb.innerHTML = rows.map(c => {
      const meta = this.STATUS_META[c.status] || { label: c.status, badge: 'badge badge-gray' };
      const uName = c.name || (c.username ? `@${c.username}` : `User #${c.user_id}`);
      const nudgeBadge = c.nudged ? ' <span class="badge badge-gray" title="\u0110\u00e3 nh\u1eafc l\u1ea7n 2" style="font-size:10px;vertical-align:middle">\ud83d\udd14 Nudged</span>' : '';
      const tier = c.lead_tier || 'Tier C';
      const score = c.intent_score || 0;
      const tierBadge = tier === 'Tier A'
        ? '<span class="badge" style="background:var(--orange-dim);color:var(--orange);font-weight:700">⭐ A</span>'
        : (tier === 'Tier B' ? '<span class="badge badge-blue">🔷 B</span>' : '<span class="badge badge-gray">⚪ C</span>');
      const updated = c.updated_at ? new Date(c.updated_at).toLocaleString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh' }) : '—';
      const toggleBtn = c.status === 'active'
        ? `<button class="btn btn-ghost btn-sm" onclick="AIFollowUp.updateStatus(${c.account_id},${c.user_id},'paused_admin')" title="Tắt AI, tự chat tay" aria-label="Tắt AI cho ${esc(uName)}">⏸ Chat tay</button>`
        : `<button class="btn btn-ghost btn-sm" onclick="AIFollowUp.updateStatus(${c.account_id},${c.user_id},'active')" title="Bật AI chat tiếp" aria-label="Bật AI cho ${esc(uName)}">▶️ Bật AI</button>`;
      const blacklistBtn = `<button class="btn btn-danger btn-sm" data-acc="${c.account_id}" data-uid="${c.user_id}" data-username="${esc(c.username||'')}" onclick="AIFollowUp.addToBlacklistDirect(Number(this.dataset.acc),Number(this.dataset.uid),this.dataset.username)" title="Đưa vào Blacklist (Cấm AI 100%)">🚫 Blacklist</button>`;
      const accLabel = c.account_name || `Acc #${c.account_id}`;
      const step = this.stepOf(c);
      const sm = this.STEP_META[step] || { label: step, emoji: '', color: 'var(--text2)' };
      const missing = Array.isArray(c.missing_items) ? c.missing_items : [];
      const missingTxt = missing.length ? `Thiếu: ${missing.map(k => this.MISSING_LABELS[k] || k).join(', ')}` : '';
      const remTxt = c.step_reminders_sent ? ` · 🔔${c.step_reminders_sent}` : '';
      const stepCell = `<div style="font-size:12px;font-weight:600;color:${sm.color};white-space:nowrap" title="${esc(missingTxt)}">${sm.emoji} ${esc(sm.label)}</div><div style="font-size:10px;color:var(--text2);font-family:monospace">${this.stepBar(step)}${remTxt}</div>`;
      return `<tr>
        <td style="width:34px;text-align:center"><input type="checkbox" aria-label="Chọn ${esc(uName)}" ${this._selected.has(`${c.account_id}:${c.user_id}`) ? 'checked' : ''} onchange="AIFollowUp.toggleSelection('${c.account_id}:${c.user_id}',this.checked)"></td>
        <td><div style="font-weight:600">${esc(uName)}${nudgeBadge}</div><div style="font-size:11px;color:var(--text2);font-family:monospace">ID ${c.user_id} • 👤 ${esc(accLabel)}</div></td>
        <td><div style="display:flex;align-items:center;gap:6px">${tierBadge}<span style="font-size:11px;color:var(--text2)">${score}%</span></div><div style="width:70px;height:4px;background:rgba(255,255,255,.08);border-radius:2px;overflow:hidden;margin-top:4px"><div style="width:${Math.min(100, Math.max(0, score))}%;height:100%;background:${score >= 80 ? 'var(--orange)' : score >= 50 ? 'var(--accent)' : 'var(--text3)'}"></div></div></td>
        <td>${c.reply_count || 0}</td>
        <td>${stepCell}</td>
        <td><span class="${meta.badge}">${meta.label}</span></td>
        <td style="font-size:12px;color:var(--text2);white-space:nowrap">${updated}</td>
        <td><div style="display:flex;gap:4px">${toggleBtn}${blacklistBtn}<button class="btn btn-primary btn-sm" onclick="AIFollowUp.openChat(${c.account_id},${c.user_id})">💬 Chat</button></div></td>
      </tr>`;
    }).join('');
  },

  async addToBlacklistDirect(accountId, userId, username) {
    if (!window.confirm(`Đưa lead ${username ? '@' + username : 'ID ' + userId} vào Blacklist để CẤM AI CHAT 100%?`)) return;
    try {
      const res = await fetch('/api/blacklist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId, username: username || undefined, reason: 'Chuyển chat tay thủ công từ AI Agent' })
      });
      if (!res.ok) throw new Error('Cập nhật Blacklist thất bại');
      App.toast(`Đã đưa ${username ? '@' + username : userId} vào Blacklist (Chat tay 100%)`, 'success');
      this.loadChats();
    } catch (e) { App.toast(e.message, 'error'); }
  },

  async bulkBlacklistSelected() {
    const selected = this._selectedChats();
    if (!selected.length) return;
    if (!window.confirm(`Thêm ${selected.length} lead đã chọn vào Blacklist (CẤM AI CHAT 100%)?`)) return;
    try {
      const responses = await Promise.all(selected.map(c => fetch('/api/blacklist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: c.user_id, username: c.username || undefined, reason: 'Blacklist hàng loạt từ AI Agent' })
      })));
      const failed = responses.filter(r => !r.ok).length;
      App.toast(`Đã Blacklist: ${selected.length - failed} OK${failed ? `, ${failed} lỗi` : ''}`, failed ? 'error' : 'success');
      this._selected.clear(); this.loadChats();
    } catch (e) { App.toast(e.message, 'error'); }
  },

  toggleSelection(key, checked) {
    checked ? this._selected.add(key) : this._selected.delete(key);
    this.renderSelectionBar();
  },

  togglePageSelection(checked) {
    this._filtered().slice((this._page - 1) * this.PAGE_SIZE, this._page * this.PAGE_SIZE).forEach(c => {
      const key = `${c.account_id}:${c.user_id}`;
      checked ? this._selected.add(key) : this._selected.delete(key);
    });
    this.renderPage();
  },

  renderSelectionBar() {
    const bar = document.getElementById('aifu-selection-bar');
    if (!bar) return;
    bar.classList.toggle('hidden', !this._selected.size);
    const count = document.getElementById('aifu-selected-count');
    if (count) count.textContent = `Đã chọn ${this._selected.size} lead`;
  },

  _selectedChats() {
    return [...this._selected].map(key => this._chats.find(c => `${c.account_id}:${c.user_id}` === key)).filter(Boolean);
  },

  async assignSelected() {
    const selected = this._selectedChats();
    if (!selected.length) return;
    const regions = window.prompt('Khu vực (phân cách dấu phẩy):', '');
    if (regions === null) return;
    const campaigns = window.prompt('Campaign (phân cách dấu phẩy):', '');
    if (campaigns === null) return;
    const payload = {
      distribution_enabled: window.confirm('Bật phân phối cho các lead đã chọn?'),
      distribution_regions: regions.split(',').map(v => v.trim()).filter(Boolean),
      distribution_campaigns: campaigns.split(',').map(v => v.trim()).filter(Boolean)
    };
    try {
      const responses = await Promise.all(selected.map(c => fetch(`/api/ai-followup/chats/${c.account_id}/${c.user_id}/profile`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload) })));
      if (responses.some(r => !r.ok)) throw new Error('Một số lead cập nhật thất bại');
      App.toast(`Đã phân công ${selected.length} lead`, 'success');
      this._selected.clear(); this.renderPage();
    } catch (e) { App.toast(e.message, 'error'); }
  },

  async bulkSendSelected() {
    const selected = this._selectedChats();
    if (!selected.length) return;
    const message = window.prompt(`Tin nhắn gửi tới ${selected.length} lead:`);
    if (!message?.trim()) return;
    try {
      const res = await fetch('/api/ai-followup/bulk-send', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ recipients:selected.map(c => ({account_id:c.account_id, user_id:c.user_id})), message:message.trim() }) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Gửi thất bại');
      App.toast(`Đã gửi ${data.sent?.length || 0}; bỏ qua ${data.skipped?.length || 0}; lỗi ${data.errors?.length || 0}`, data.errors?.length ? 'error' : 'success');
      this._selected.clear(); this.renderPage();
    } catch (e) { App.toast(e.message, 'error'); }
  },

  async bulkToggleAI(status) {
    const selected = this._selectedChats();
    if (!selected.length) return;
    const label = status === 'active' ? 'Bật AI' : 'Tắt AI';
    if (!window.confirm(`${label} cho ${selected.length} lead đã chọn?`)) return;
    try {
      const responses = await Promise.all(selected.map(c => fetch(`/api/ai-followup/chats/${c.account_id}/${c.user_id}/status`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ status }) })));
      const failed = responses.filter(r => !r.ok).length;
      App.toast(`${label}: ${selected.length - failed} OK${failed ? `, ${failed} lỗi` : ''}`, failed ? 'error' : 'success');
      this._selected.clear(); this.loadChats();
    } catch (e) { App.toast(e.message, 'error'); }
  },

  async editProfile(accountId, userId) {
    try {
      const res = await fetch(`/api/ai-followup/chats/${accountId}/${userId}/profile`);
      if (!res.ok) throw new Error('Không thể tải hồ sơ lead');
      const p = await res.json();
      const uid = window.prompt('WEEX UID của KOL (để trống nếu chưa có):', p.weex_uid || ''); if (uid === null) return;
      const volume = window.prompt('Volume KOL khai (M USD/tháng, VD 15 hoặc 15M; để trống = chưa biết):', p.volume_claimed || ''); if (volume === null) return;
      const vip = window.prompt('VIP code:', p.vip_code || ''); if (vip === null) return;
      const affiliate = window.prompt('Affiliate link:', p.affiliate_link || ''); if (affiliate === null) return;
      const selCampaign = window.prompt('Campaign KOL đã chọn (1 campaign):', p.selected_campaign || ''); if (selCampaign === null) return;
      const firstPost = window.prompt('Link bài đầu tiên (nếu đã có):', p.first_post_link || ''); if (firstPost === null) return;
      const regions = window.prompt('Khu vực phân phối (phân cách dấu phẩy):', (p.distribution_regions || []).join(', ')); if (regions === null) return;
      const campaigns = window.prompt('Campaign phân phối (phân cách dấu phẩy):', (p.distribution_campaigns || []).join(', ')); if (campaigns === null) return;
      const save = await fetch(`/api/ai-followup/chats/${accountId}/${userId}/profile`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ weex_uid:uid.trim(), volume_claimed:volume.trim(), vip_code:vip.trim(), affiliate_link:affiliate.trim(), selected_campaign:selCampaign.trim(), first_post_link:firstPost.trim(), distribution_enabled:window.confirm('Bật phân phối lead này?'), distribution_regions:regions.split(',').map(v=>v.trim()).filter(Boolean), distribution_campaigns:campaigns.split(',').map(v=>v.trim()).filter(Boolean) }) });
      if (!save.ok) throw new Error('Lưu hồ sơ thất bại');
      App.toast('Đã lưu hồ sơ và phân công lead', 'success');
      if (this._modalChat && this._modalChat.account_id === accountId && this._modalChat.user_id === userId) this.loadStepPanel(accountId, userId);
      this.loadChats();
    } catch (e) { App.toast(e.message, 'error'); }
  },

  // ── 5-step funnel: panel + actions ─────────────────────────────────
  async loadStepPanel(accountId, userId) {
    const panel = document.getElementById('aifu-modal-step-panel');
    if (!panel) return;
    try {
      const res = await fetch(`/api/ai-followup/chats/${accountId}/${userId}/step`);
      if (!res.ok) throw new Error('Không tải được trạng thái bước');
      const st = await res.json();
      this._modalStep = st;
      if (this._modalChat) { this._modalChat.kol_step = st.step; this._modalChat.missing_items = st.missing_items; }
      this.renderStepPanel(st);
      this._renderModalActions();
    } catch (e) {
      panel.innerHTML = `<div style="font-size:12px;color:var(--red)">${esc(e.message)}</div>`;
    }
  },

  renderStepPanel(st) {
    const panel = document.getElementById('aifu-modal-step-panel');
    if (!panel || !st) return;
    const cur = st.step || 'prospect';
    const rank = this.stepRank(cur);
    const bar = this.STEPS.slice(1).map((s, i) => {
      const n = i + 1;
      const done = rank >= n;
      const here = rank === n;
      const m = this.STEP_META[s];
      return `<div style="flex:1;text-align:center;min-width:70px">
        <div style="height:6px;border-radius:3px;background:${done ? 'var(--green)' : 'var(--border)'};${here ? 'box-shadow:0 0 0 2px var(--accent)' : ''}"></div>
        <div style="font-size:10px;margin-top:4px;color:${done ? 'var(--text)' : 'var(--text2)'};font-weight:${here ? 700 : 400}">${n}. ${m.emoji} ${esc(m.label)}</div>
      </div>`;
    }).join('<div style="width:4px"></div>');
    const missing = (st.missing_labels && st.missing_labels.length) ? st.missing_labels : (st.missing_items || []).map(k => this.MISSING_LABELS[k] || k);
    const have = st.have || {};
    const haveTxt = Object.entries(have).filter(([, v]) => v).map(([k, v]) => `${this.MISSING_LABELS[k] || k}: ${esc(String(v)).slice(0, 40)}`).join(' · ');
    const pred = st.predicted_tier;
    const predTxt = pred ? `Bậc dự kiến: Tier ${pred.tier} (${Number(pred.revshare_pct)}% + $${Number(pred.bonus_per_1m_taker_usd)}/1M taker) với ~${Number(pred.volume_m)}M/tháng khai` : 'Bậc dự kiến: chưa có volume khai → tháng đầu Tier 1';
    const terminal = rank < 0 ? `<span class="badge badge-red" style="margin-left:6px">${this.stepLabel(cur)}</span>` : '';
    const rem = st.step_reminders_sent ? ` · đã nhắc ${st.step_reminders_sent}/2 ở bước này` : '';
    panel.innerHTML = `
      <div style="background:var(--bg3);border:1px solid var(--border-hover);border-radius:var(--radius);padding:10px 12px">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px">
          <div style="font-size:12px;font-weight:700;color:var(--accent-text)">🪜 BƯỚC ${Math.max(rank, 0)}/5 — ${this.stepLabel(cur)}${terminal}</div>
          <div style="font-size:11px;color:var(--text2)">v${st.offer_version || 1}${rem}</div>
        </div>
        <div style="display:flex;align-items:flex-start;gap:0;margin-bottom:8px">${bar}</div>
        <div style="font-size:12px;line-height:1.5">
          <div><b>Còn thiếu:</b> ${missing.length ? missing.map(esc).join(', ') : '<span style="color:var(--green)">không — sẵn sàng sang bước tiếp</span>'}</div>
          ${haveTxt ? `<div style="color:var(--text2)"><b>Đã có:</b> ${haveTxt}</div>` : ''}
          <div style="color:var(--text2)">${predTxt}</div>
        </div>
      </div>`;
  },

  async setStep(accountId, userId, step, reason) {
    if (!step) return;
    const label = this.stepLabel(step);
    if (reason === undefined) {
      reason = window.prompt(`Chuyển lead sang bước "${label}". Lý do (tuỳ chọn):`, '');
      if (reason === null) return;
    }
    try {
      const res = await fetch(`/api/ai-followup/chats/${accountId}/${userId}/step`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ step, reason: reason || '' })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Đổi bước thất bại');
      App.toast(data.changed ? `Đã chuyển sang ${label}` : `Lead đã ở bước ${label}`, 'success');
      const c = this._chats.find(x => x.account_id === accountId && x.user_id === userId);
      if (c && data.state) { c.kol_step = data.state.step; c.missing_items = data.state.missing_items; c.step_reminders_sent = data.state.step_reminders_sent; }
      if (this._modalChat && this._modalChat.account_id === accountId && this._modalChat.user_id === userId) {
        this._modalStep = data.state; this.renderStepPanel(data.state); this._renderModalActions();
      }
      this.renderStats(); this.renderPage();
    } catch (e) { App.toast(e.message, 'error'); }
  },

  stepSelectChanged(sel, accountId, userId) {
    const v = sel.value;
    sel.value = '';
    if (v) this.setStep(accountId, userId, v);
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
  async openChat(accountId, userId) {
    const c = this._chats.find(x => x.account_id === accountId && x.user_id === userId);
    if (!c) return;
    this._modalChat = c;
    const modal = document.getElementById('aifu-history-modal');
    if (!modal) return;

    const uName = c.name || (c.username ? `@${c.username}` : `User #${c.user_id}`);
    const title = document.getElementById('aifu-modal-title');
    const accountLabel = c.account_name || `Account #${accountId}`;
    if (title) title.textContent = `💬 ${accountLabel} đang chat với ${uName}`;

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

    box.innerHTML = summaryBanner + '<div style="text-align:center;color:var(--text2);padding:20px">⏳ Đang tải lịch sử chat...</div>';
    const stepPanel = document.getElementById('aifu-modal-step-panel');
    if (stepPanel) stepPanel.innerHTML = '<div style="font-size:12px;color:var(--text2)">⏳ Đang tải trạng thái bước...</div>';
    this._modalStep = null;
    this.loadStepPanel(accountId, userId);
    try {
      const histRes = await fetch(`/api/ai-followup/chats/${accountId}/${userId}/history`);
      const histData = await histRes.json();
      const history = [...(histData.history || [])].sort((a,b) => new Date(a.timestamp || 0) - new Date(b.timestamp || 0));
      if (!history.length) {
        box.innerHTML = summaryBanner + '<div style="text-align:center;color:var(--text2);padding:20px">Chưa có tin nhắn nào</div>';
      } else {
        box.innerHTML = summaryBanner + history.map(msg => {
          const isUser = msg.role === 'user';
          const timeText = msg.timestamp ? new Date(msg.timestamp).toLocaleString('vi-VN', {
            timeZone: 'Asia/Ho_Chi_Minh', day: '2-digit', month: '2-digit', year: 'numeric',
            hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false
          }) : 'Không rõ thời gian';
          return `
            <div style="display:flex;flex-direction:column;align-items:${isUser ? 'flex-start' : 'flex-end'};margin-bottom:12px">
              <div style="font-size:11px;color:var(--text2);margin-bottom:3px">${isUser ? '👤 ' + esc(uName) : '🤖 ' + esc(accountLabel) + ' · AI'} • ${timeText}</div>
              <div style="background:${isUser ? 'var(--accent-dim)' : 'var(--purple-dim)'};border:1px solid var(--border-hover);padding:10px 14px;border-radius:12px;max-width:85%;white-space:pre-wrap;font-size:13px;line-height:1.5">${esc(msg.content || '')}</div>
            </div>`;
        }).join('');
        box.scrollTop = box.scrollHeight;
      }
    } catch (e) {
      box.innerHTML = summaryBanner + '<div style="text-align:center;color:var(--red);padding:20px">Lỗi tải lịch sử chat</div>';
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
    const blkBtn = `<button class="btn btn-danger" data-acc="${c.account_id}" data-uid="${c.user_id}" data-username="${esc(c.username||'')}" onclick="AIFollowUp.addToBlacklistDirect(Number(this.dataset.acc),Number(this.dataset.uid),this.dataset.username);AIFollowUp.closeHistoryModal()" title="Cấm AI chat 100% với lead này">🚫 Blacklist</button>`;
    const vs = c.verification_status || 'none';
    const vsLabel = {none:'🔍 Chưa verify', requested:'📤 Đã yêu cầu', submitted:'📎 Đã nộp', verified:'✅ Verified', rejected:'❌ Từ chối'}[vs] || vs;
    const vsBtn = `<button class="btn btn-ghost" onclick="AIFollowUp.cycleVerification(${c.account_id},${c.user_id})" title="Click để đổi trạng thái verify">${vsLabel}</button>`;
    const cur = this.stepOf(c);
    const rank = this.stepRank(cur);
    const next = rank >= 0 && rank < this.STEPS.length - 1 ? this.STEPS[rank + 1] : null;
    const nextBtn = next
      ? `<button class="btn btn-green" onclick="AIFollowUp.setStep(${c.account_id},${c.user_id},'${next}')" title="Chuyển sang bước tiếp theo (ghi vào kol_step_events)">➡️ ${this.stepLabel(next)}</button>`
      : '';
    const opts = [...this.STEPS, 'declined', 'dormant'].map((s, i) =>
      `<option value="${s}" ${s === cur ? 'disabled' : ''}>${i < this.STEPS.length ? i + '. ' : ''}${this.stepLabel(s)}${s === cur ? ' (hiện tại)' : ''}</option>`).join('');
    const stepSel = `<select class="form-input" style="width:auto;padding:6px 8px;font-size:12px" title="Admin đặt bước bất kỳ (tiến/lùi)" onchange="AIFollowUp.stepSelectChanged(this,${c.account_id},${c.user_id})"><option value="">🪜 Đặt bước…</option>${opts}</select>`;
    el.innerHTML = `${toggle}${blkBtn}${vsBtn}<button class="btn btn-ghost" onclick="AIFollowUp.editProfile(${c.account_id},${c.user_id})">📍 Hồ sơ & phân công</button>${nextBtn}${stepSel}<button class="btn btn-primary" onclick="AIFollowUp.closeHistoryModal()">Đóng</button>`;
  },

  async cycleVerification(accountId, userId) {
    const c = this._modalChat;
    if (!c) return;
    const order = ['none','requested','submitted','verified','rejected'];
    const cur = c.verification_status || 'none';
    const next = order[(order.indexOf(cur) + 1) % order.length];
    try {
      const res = await fetch(`/api/ai-followup/chats/${accountId}/${userId}/verification`, {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({status: next})
      });
      if (!res.ok) throw new Error('Lỗi cập nhật verification');
      c.verification_status = next;
      this._renderModalActions();
      App.toast(`Verification: ${next}`, 'success');
    } catch (e) { App.toast(e.message, 'error'); }
  },

  async sendMessage() {
    const c = this._modalChat;
    const input = document.getElementById('aifu-chat-input');
    const btn = document.getElementById('aifu-chat-send-btn');
    if (!c || !input) return;
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    if (btn) { btn.disabled = true; btn.textContent = '⏳'; }
    try {
      const res = await fetch(`/api/ai-followup/chats/${c.account_id}/${c.user_id}/send`, {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({text})
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Gửi thất bại');
      // Append bubble vào chat box
      const box = document.getElementById('aifu-modal-chat-box');
      if (box) {
        const accLabel = c.account_name || `Account #${c.account_id}`;
        const timeText = new Date().toLocaleString('vi-VN', { timeZone:'Asia/Ho_Chi_Minh', day:'2-digit', month:'2-digit', year:'numeric', hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false });
        box.insertAdjacentHTML('beforeend', `
          <div style="display:flex;flex-direction:column;align-items:flex-end;margin-bottom:12px">
            <div style="font-size:11px;color:var(--text2);margin-bottom:3px">🧑‍💼 ${esc(accLabel)} · chat tay • ${timeText}</div>
            <div style="background:var(--purple-dim);border:1px solid var(--border-hover);padding:10px 14px;border-radius:12px;max-width:85%;white-space:pre-wrap;font-size:13px;line-height:1.5">${esc(text)}</div>
          </div>`);
        box.scrollTop = box.scrollHeight;
      }
      // Interceptor tự pause AI + ghi history — báo nhẹ
      App.toast('✅ Đã gửi — AI tạm dừng để bạn chat tay', 'success');
    } catch (e) {
      input.value = text; // giữ lại text nếu lỗi
      App.toast('❌ ' + e.message, 'error');
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Gửi ➤'; }
    }
  },

  closeHistoryModal() {
    document.getElementById('aifu-history-modal')?.classList.remove('open');
    this._modalChat = null;
    this._modalStep = null;
  }
};

if (typeof window !== 'undefined') {
  window.AIFollowUp = AIFollowUp;
}
