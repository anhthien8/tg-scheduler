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
    dormant:        { label: 'Ngủ đông',      emoji: '💤', color: 'var(--text3)' },
    media:          { label: 'Media',         emoji: '📣', color: 'var(--text3)', tip: 'Bên bán gói quảng cáo (AMA, Space, post). AI đã từ chối lịch sự: chỉ làm với KOL trade futures/CFD/stocks/vàng, trả theo hiệu suất.' }
  },
  MISSING_LABELS: {
    deal_agreement: 'KOL đồng ý thang', weex_uid: 'WEEX UID', vip_code: 'vipCode (ops)',
    affiliate_link: 'Link affiliate (ops)', link_confirmation: 'Xác nhận đã gắn link (ops)',
    selected_campaign: 'Chọn campaign', first_post_link: 'Link bài đầu tiên', volume_verified: 'Volume hợp lệ kỳ đầu (ops)'
  },
  HAVE_LABELS: {
    channel_link: 'Kênh', platform: 'Nền tảng', followers: 'Followers', volume_claimed: 'Volume khai',
    monthly_volume: 'Volume/tháng (AI ghi từ chat)',
    offer_version: 'Phiên bản offer', vip_code: 'vipCode', affiliate_link: 'Link affiliate'
  },

  stepOf(c) { return (c && c.kol_step) || 'prospect'; },
  // Blacklisted / ignored leads (status bot_ignored) are out of the funnel: they
  // are not counted in the step boxes and do not show under a step filter.
  inFunnel(c) { return c && c.status !== 'bot_ignored'; },
  stepRank(s) { const i = this.STEPS.indexOf(s || 'prospect'); return i; },
  stepLabel(s) { const m = this.STEP_META[s] || { label: s, emoji: '' }; return `${m.emoji} ${m.label}`.trim(); },
  stepBar(s) {
    const r = this.stepRank(s);
    if (r < 0) return this.stepLabel(s);
    return '●'.repeat(r) + '○'.repeat(5 - r) + ` ${r}/5`;
  },

  async init(force = false) {
    // The page template is rebuilt on every visit, so the settings tab is empty again.
    this._settingsTabLoaded = false;
    this.switchTab('leads');
    // First visit in a session opens on the reply queue (the work list), not "all leads".
    if (!this._defaulted) { this._defaulted = true; this._statusFilter = this.QUEUE_FILTER; }
    // Cache: chỉ fetch lại 86KB chats nếu quá 30s hoặc bị yêu cầu (nút 🔄 / force).
    const fresh = this._chatsLoadedAt && (Date.now() - this._chatsLoadedAt) < 30000 && this._chats.length;
    // Only the settings read needed for the global on/off switch. The rest of the
    // settings tab (channel settings, referrals, ladder, KOL contacts) loads when
    // that tab is first opened — see switchTab().
    const tasks = [this.loadSettings()];
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
    if (!leads && !this._settingsTabLoaded) {
      this._settingsTabLoaded = true;
      this.loadKolChannelSettings(); this.loadReferrals(); this.loadLadder(); this.loadKolContacts();
    }
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

  async _renderAgentsList() {
    const box = document.getElementById('aifu-agents-list');
    if (!box) return;
    let agents = [];
    try { agents = (await AIAgentsAPI.getAll()).agents || []; } catch (e) { /* keep loader text */ }
    const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const active = agents.filter(a => a.is_active);
    box.innerHTML = active.length
      ? active.map(a => `<button type="button" class="btn btn-ghost btn-sm" title="Sửa kịch bản & Knowledge Base của ${esc(a.name)}" onclick="AIFollowUp.editAgent(${Number(a.id)})">${esc(a.avatar_emoji || '🤖')} ${esc(a.name)} ✏️</button>`).join('')
        + `<button type="button" class="btn btn-ghost btn-sm" onclick="App.navigate('ai-agents')">Tất cả agent →</button>`
      : `<button type="button" class="btn btn-primary btn-sm" onclick="App.navigate('ai-agents')">➕ Tạo AI Agent</button>`;
  },

  async editAgent(id) {
    App.navigate('ai-agents');
    await AIAgents.load();
    AIAgents.openForm(id);
  },

  renderSettings() {
    if (!this.settings) return;
    const enabledEl = document.getElementById('aifu-enabled');
    if (enabledEl) enabledEl.checked = !!this.settings.enabled;
    this._renderAgentsList();
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
    const careEn = document.getElementById('aifu-care-enabled');
    if (careEn) careEn.checked = this.settings.kol_care_enabled !== false;
    const careDays = document.getElementById('aifu-care-days');
    if (careDays) careDays.value = String(this.settings.kol_care_interval_days || 30);
    const careCap = document.getElementById('aifu-care-cap');
    if (careCap) careCap.value = this.settings.kol_care_daily_cap ?? 5;
    const mirrorEl = document.getElementById('aifu-weex-mirrors');
    if (mirrorEl) mirrorEl.value = (this.settings.weex_mirror_domains || []).join(', ');
  },

  // ── v3.40 Contact KOL (lead đã gửi proof → lưu "<tên>_KOL") ─────────
  async loadKolContacts() {
    const body = document.getElementById('kolc-body');
    if (!body) return;
    try {
      const res = await fetch('/api/kol-contacts');
      if (!res.ok) throw new Error('Không tải được danh sách contact KOL');
      const d = await res.json();
      const rows = d.contacts || [];
      const saved = rows.filter(r => r.status === 'saved').length;
      const bf = d.backfill || {};
      const st = document.getElementById('kolc-stats');
      if (st) st.textContent = `${saved} đã lưu / ${rows.length}` + (bf.running ? ` · đang lưu lead cũ ${bf.saved + bf.skipped + bf.failed}/${bf.total}` : '');
      const label = { saved: '✅ Đã lưu', pending: '⏳ Chờ', failed: '⚠️ Lỗi', skipped: '⏭ Bỏ qua' };
      body.innerHTML = rows.length ? rows.slice(0, 200).map(r => {
        const who = r.contact_name || (r.username ? '@' + r.username : String(r.user_id));
        const proof = r.last_proof_link ? `<a href="${esc(r.last_proof_link)}" target="_blank" rel="noopener">${r.proof_count || 1} ↗</a>` : String(r.proof_count || 0);
        return `<tr><td>${esc(who)}${r.username ? ` <span style="color:var(--text2)">@${esc(r.username)}</span>` : ''}</td>
          <td>#${r.account_id} ${esc(r.account_name || '')}</td><td>${proof}</td>
          <td title="${esc(r.reason || '')}">${label[r.status] || esc(r.status)}</td><td>${esc(r.saved_at || '')}</td></tr>`;
      }).join('') : '<tr><td colspan="5" style="color:var(--text2)">Chưa có lead nào gửi proof.</td></tr>';
    } catch (e) {
      body.innerHTML = `<tr><td colspan="5" style="color:var(--red)">${esc(e.message)}</td></tr>`;
    }
  },

  async backfillKolContacts() {
    try {
      const p = await (await fetch('/api/kol-contacts/backfill/preview')).json();
      if (!p.count) { App.toast('Không còn lead cũ nào cần lưu contact', 'info'); return; }
      if (!window.confirm(`Lưu contact _KOL cho ${p.count} lead đã gửi proof? Mỗi người cách nhau vài giây, không gửi tin nhắn.`)) return;
      const res = await fetch('/api/kol-contacts/backfill', { method: 'POST' });
      if (!res.ok) throw new Error('Không bắt đầu được');
      App.toast(`Đang lưu ${p.count} contact trong nền`, 'success');
      setTimeout(() => this.loadKolContacts(), 5000);
    } catch (e) {
      App.toast(e.message, 'error');
    }
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
            + `<span>Lượt giới thiệu: <strong style="color:var(--purple)">${st.total_attributions || 0}</strong></span>`
            + `<span>Đã chốt (Signed): <strong style="color:var(--green)">${st.conversions || 0}</strong> (${st.conversion_rate || 0}%)</span>`;
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
          <td><code style="color:var(--accent-text);font-weight:700">${esc(c.code)}</code></td>
          <td>${c.referrer_user_id} <small style="color:var(--text2)">(Acc #${c.account_id})</small></td>
          <td>${c.uses_count || 0} / ${c.max_uses || 10}</td>
          <td>${c.is_active ? '<span class="badge badge-green">Active</span>' : '<span class="badge badge-gray">Inactive</span>'}</td>
          <td>${c.is_active ? `<button class="btn btn-ghost btn-sm" style="color:var(--red)" onclick="AIFollowUp.deactivateReferralCode(${c.id})">Vô hiệu</button>` : '—'}</td>
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
      // Global persona/KB are only a last-resort fallback now; keep stored values untouched.
      system_prompt: this.settings?.system_prompt || '',
      knowledge_base: this.settings?.knowledge_base || '',
      max_replies_per_user: parseInt(document.getElementById('aifu-max-replies')?.value || '5', 10),
      handover_keywords: (document.getElementById('aifu-handover-kw')?.value || '').split(',').map(s => s.trim()).filter(Boolean),
      drip_cadence_hours: this._parseHoursInput('aifu-drip-cadence', [24, 72, 168]),
      drip_cadence_tier_a: this._parseHoursInput('aifu-drip-tier-a', [12, 24, 48, 96]),
      drip_cadence_tier_b: this._parseHoursInput('aifu-drip-tier-b', [24, 72, 168]),
      drip_cadence_tier_c: this._parseHoursInput('aifu-drip-tier-c', [48, 168]),
      kol_care_enabled: document.getElementById('aifu-care-enabled')?.checked ?? true,
      kol_care_interval_days: parseInt(document.getElementById('aifu-care-days')?.value || '30', 10),
      kol_care_daily_cap: parseInt(document.getElementById('aifu-care-cap')?.value || '5', 10),
      weex_mirror_domains: (document.getElementById('aifu-weex-mirrors')?.value || '').split(/[\s,;]+/).map(s => s.trim()).filter(Boolean)
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
    const box = document.getElementById('aifu-enabled');
    const enabled = box?.checked || false;
    // Settings not loaded yet: saving now would overwrite the prompt and knowledge
    // base with blanks. Refuse, put the switch back, and load them.
    if (!this.settings || !Object.keys(this.settings).length) {
      if (box) box.checked = !enabled;
      App.toast('Cài đặt AI chưa tải xong — thử lại sau vài giây', 'error');
      this.loadSettings();
      return;
    }
    const s = this.settings;
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
  async loadChats(opts = {}) {
    this._chatsTriedAt = Date.now();
    try {
      const res = await fetch('/api/ai-followup/chats?limit=2000&include_history=false');
      if (!res.ok) throw new Error('Lỗi tải danh sách lead');
      const data = await res.json();
      this._chats = data.chats || [];
      this._chatsLoadedAt = Date.now();
      // Page is kept (renderPage clamps it); only a filter or search change resets it.
      const open = this._modalChat;
      if (open) {
        const again = this._chats.find(x => x.account_id === open.account_id && x.user_id === open.user_id);
        if (again) this._modalChat = again;
        else this._chats.push(open); // e.g. a supported KOL opened from the KOL list
      }
      this.renderStats();
      this.renderChips();
      this.renderPage();
      this._updateBadge();
      if (this._modalChat) { this._renderModalActions(); this._paintNav(); }
    } catch (e) {
      if (opts.silent) return; // sidebar badge refresh: fail quietly
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
    set('aifu-stat-onboarded', this._chats.filter(c => this.inFunnel(c) && this.stepRank(this.stepOf(c)) >= 3).length);
    set('aifu-stat-total', this._chats.length);
    set('aifu-stat-nudged', this._chats.filter(c => c.nudged).length);
    this.renderTodo();
    this.renderStepCards();
    // Only when the page is on screen — the sidebar badge refresh also runs loadChats().
    if (document.getElementById('aifu-stat-supported')) this.loadSupportedCount();
  },

  renderStepCards() {
    const el = document.getElementById('aifu-step-cards');
    if (!el) return;
    const counts = {};
    this._chats.forEach(c => { if (!this.inFunnel(c)) return; const s = this.stepOf(c); counts[s] = (counts[s] || 0) + 1; });
    const keys = [...this.STEPS, 'declined', 'dormant', 'media'].filter(k => this.STEPS.includes(k) || counts[k]);
    el.innerHTML = keys.map((k, i) => {
      const m = this.STEP_META[k];
      const active = this._stepFilter === k;
      const idx = this.STEPS.includes(k) ? `${i}.` : '';
      return `<div class="stat-card" style="cursor:pointer;padding:10px 12px;${active ? 'border-color:var(--accent)' : ''}" onclick="AIFollowUp.filterByStep('${k}')" title="Bấm để lọc theo bước. Không tính lead đã blacklist / Ignored (xem ở chip 🚫 Ignored).">
        <div class="stat-label" style="font-size:11px">${idx} ${m.emoji} ${m.label}</div>
        <div class="stat-value" style="font-size:20px;color:${m.color}">${counts[k] || 0}</div>
      </div>`;
    }).join('');
  },

  renderChips() {
    const el = document.getElementById('aifu-status-chips');
    if (!el) return;
    const f = this._statusFilter;
    const tip = t => `<span class="info-i" tabindex="0" data-tip="${esc(t)}">i</span>`;
    const chip = (key, label, extra = '') =>
      `<button class="btn btn-sm ${f === key ? 'btn-primary' : 'btn-ghost'}" aria-pressed="${f === key}" onclick="AIFollowUp.filterByStatus('${key}')"${extra}>${label}</button>`;
    const queue = this._queueRows();
    const late = queue.filter(c => this._queueGroup(c) === 1).length;
    const proof = this._todoRows(this.TODO.find(t => t.key === '__proof')).length;
    const answered = this._chats.filter(c => this._isAnswered(c)).length;
    const cleanup = this._chats.filter(c => this._isCleanup(c)).length;
    const count = st => this._chats.filter(c => c.status === st).length;
    const noFilter = !f && !this._stepFilter;

    let html = chip(this.QUEUE_FILTER, `📥 Hàng chờ (${queue.length}${late ? ` · ${late} quá hạn` : ''})`,
        late && f !== this.QUEUE_FILTER ? ' style="color:var(--red);font-weight:700"' : '')
      + tip('Những KOL đang chờ NGƯỜI THẬT trả lời. Gồm: (1) lời hứa “team sẽ báo lại” đã quá 24 giờ, (2) lời hứa chưa quá 24 giờ, (3) lead “cần người thật” hoặc “chat tay” mà KOL là người nhắn sau cùng, (4) lead AI vừa chuyển cho người thật và chưa ai trả lời. Xếp theo mức gấp, rồi Tier A → B → C → Cold, rồi chờ lâu nhất. Không tính lead đã Bỏ qua, Từ chối, Ngủ đông và tài khoản của chính mình. Lead tự rời hàng chờ khi bạn nhắn cho KOL.')
      + chip('__proof', `🧾 Proof (${proof})`)
      + `<button class="btn btn-sm ${noFilter ? 'btn-primary' : 'btn-ghost'}" onclick="AIFollowUp.clearFilters()" title="Bỏ mọi bộ lọc, xem toàn bộ lead">Tất cả (${this._chats.length})</button>`;

    const moreKeys = ['active', this.ANSWERED_FILTER, 'needs_human', 'paused_admin', this.OWED_FILTER, 'bot_ignored', this.CLEANUP_FILTER];
    const todo = this.TODO.find(t => t.key === f && t.key.startsWith('__') && t.key !== '__proof');
    const open = this._moreOpen || moreKeys.includes(f) || !!todo;
    html += `<button class="btn btn-sm btn-ghost" aria-expanded="${open}" onclick="AIFollowUp.toggleMoreChips()" title="Các bộ lọc ít dùng hơn">Thêm ${open ? '▴' : '▾'}</button>`;
    if (open) {
      html += '<span style="flex-basis:100%;height:0"></span>'
        + chip('active', `🤖 AI đang chat (${count('active')})`)
        + chip(this.ANSWERED_FILTER, `↩️ Đã trả lời, chờ KOL (${answered})`)
        + chip('needs_human', `⚠️ Cần người thật — tất cả (${count('needs_human')})`)
        + chip('paused_admin', `⏸ Chat tay (${count('paused_admin')})`)
        + chip(this.OWED_FILTER, `⏳ Lời hứa (${this._chats.filter(c => this.isOwed(c)).length})`)
        + chip('bot_ignored', `🚫 Ignored (${count('bot_ignored')})`)
        + chip(this.CLEANUP_FILTER, `🧹 Dọn rác (${cleanup})`)
        + tip('Những dòng còn mang cờ “cần người thật” hoặc lời hứa nhưng KHÔNG cần trả lời nữa: lead đã Từ chối / Ngủ đông, hoặc là tài khoản của chính mình. Chúng đã được ẩn khỏi Hàng chờ; vào đây để bấm ✅ hoặc 🙈 cho sạch dữ liệu. Không có gì bị xoá tự động.');
      if (todo) html += `<button class="btn btn-sm btn-primary" onclick="AIFollowUp.filterByStatus('')" title="Bỏ lọc này">${todo.icon} ${todo.label} ✕</button>`;
    }
    if (this._stepFilter) {
      const lbl = this._stepFilter === 'link_confirmed+' ? '🔗 Đã gắn link trở lên' : this.stepLabel(this._stepFilter);
      html += `<button class="btn btn-sm btn-primary" onclick="AIFollowUp.filterByStep('')" title="Bỏ lọc bước">Bước: ${lbl} ✕</button>`;
    }
    if (f && this._stepFilter) html += `<span style="font-size:12px;color:var(--text2);align-self:center">Đang lọc kết hợp trạng thái + bước</span>`;
    el.innerHTML = html;
  },

  toggleMoreChips() { this._moreOpen = !this._moreOpen; this.renderChips(); },

  // ── Reply queue: the one list a human works through ──────────────────
  // Built from this._chats (already loaded) — no extra request, no polling.
  QUEUE_FILTER: '__queue',
  CLEANUP_FILTER: '__cleanup',
  ANSWERED_FILTER: '__answered',

  _deadStep(c) { const s = this.stepOf(c); return s === 'declined' || s === 'dormant' || s === 'media'; },

  // A real lead that can still be worked: not ignored, not closed, not one of our own accounts.
  _isLive(c) { return !!c && c.status !== 'bot_ignored' && !this._deadStep(c) && !c.is_own; },

  // "[Human Admin]: ..." / "[Human Admin via Forum]: ..." = typed by a real person.
  HUMAN_PREFIX: /^\s*\[Human Admin[^\]]*\]:\s*/,
  _humanLast(c) { return c.last_role === 'assistant' && this.HUMAN_PREFIX.test(c.last_text || ''); },

  // 0 = not waiting on us · 1 = promise past 24h · 2 = promise under 24h, or the
  // KOL spoke last on a human-owned lead (needs_human / chat tay) · 3 = the AI
  // handed the lead over and its own message is still the last one.
  _queueGroup(c) {
    if (!this._isLive(c)) return 0;
    if (this.isOwed(c)) return this.owedHours(c) >= this.OWED_DEADLINE_HOURS ? 1 : 2;
    const humanOwned = c.status === 'needs_human' || c.status === 'paused_admin';
    if (humanOwned && c.last_role === 'user') return 2;
    return (c.status === 'needs_human' && c.last_role === 'assistant' && !this._humanLast(c)) ? 3 : 0;
  },

  _queueWait(c) { return Math.max(this.isOwed(c) ? this.owedHours(c) : 0, this.silentHours(c)); },

  _queueRows() {
    return this._chats.filter(c => this._queueGroup(c) > 0).sort((a, b) =>
      (this._queueGroup(a) - this._queueGroup(b)) || (this._tierRank(a) - this._tierRank(b))
      || (this._queueWait(b) - this._queueWait(a)));
  },

  // We (a human) answered last and the ball is with the KOL.
  _isAnswered(c) {
    if (!this._isLive(c) || this.isOwed(c) || c.last_role !== 'assistant') return false;
    // Chat tay: any message from our side counts. needs_human: only a real person's.
    return c.status === 'paused_admin' || (c.status === 'needs_human' && this._humanLast(c));
  },

  // Still flagged for a human, but nobody needs to answer (closed lead / our own account).
  _isCleanup(c) {
    return c.status !== 'bot_ignored' && (this._deadStep(c) || c.is_own)
      && (c.status === 'needs_human' || !!c.owed_since);
  },

  _updateBadge() {
    const el = document.getElementById('inbox-badge');
    if (!el || !this._chatsLoadedAt) return;
    const n = this._queueRows().length;
    el.textContent = n > 99 ? '99+' : String(n);
    el.classList.toggle('hidden', n === 0);
    el.title = `${n} KOL đang chờ người thật trả lời`;
  },

  // Change one lead in place and redraw — no reload, the chat window stays open.
  _patchChat(accountId, userId, fields) {
    const c = this._chats.find(x => x.account_id === accountId && x.user_id === userId);
    if (!c) return null;
    Object.assign(c, fields);
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    set('aifu-stat-human', this._chats.filter(x => x.status === 'needs_human').length);
    set('aifu-stat-active', this._chats.filter(x => x.status === 'active').length);
    this.renderTodo();
    this.renderStepCards();
    this.renderChips();
    this.renderPage();
    this._updateBadge();
    if (this._sameLead(c)) { this._modalChat = c; this._renderModalActions(); this._paintNav(); }
    return c;
  },

  // ── Extra work lists (proof, ops, silent Tier A) used as filters ──
  HOT_SILENT_HOURS: 48,
  TODO: [
    { key: 'needs_human', icon: '⚠️', label: 'Cần người thật trả lời', urgent: true,
      tip: 'AI đã dừng và chuyển cho người thật (KOL hỏi điều AI không được tự quyết, hoặc đã hết số câu AI). Mở chat và trả lời.',
      test: (c, A) => c.status === 'needs_human' },
    { key: '__owed_late', icon: '⏳', label: 'Lời hứa quá hạn 24 giờ', urgent: true,
      tip: 'Bên mình đã nói “team sẽ xem và báo lại” hơn 24 giờ trước mà chưa có người thật trả lời KOL.',
      test: (c, A) => A.isOwed(c) && A.owedHours(c) >= A.OWED_DEADLINE_HOURS,
      sort: (a, b, A) => A.owedHours(b) - A.owedHours(a) },
    { key: '__proof', icon: '🧾', label: 'Proof chờ duyệt',
      tip: 'KOL đã gửi ảnh/video chứng minh volume hoặc kênh; cần người xem và bấm Verified hoặc Từ chối trong cửa sổ chat.',
      test: (c, A) => A.inFunnel(c) && c.verification_status === 'submitted' },
    { key: '__ops', icon: '🔗', label: 'Đã nhận UID, chờ gắn link',
      tip: 'KOL đã gửi UID. Việc tiếp theo là của bên mình: cấp vipCode / link affiliate và xác nhận đã gắn link, rồi chuyển bước.',
      test: (c, A) => A.inFunnel(c) && A.stepOf(c) === 'uid_received' },
    { key: '__hot_silent', icon: '⭐', label: 'Tier A im lặng quá 2 ngày',
      tip: 'Lead Tier A chưa gắn link, AI vẫn đang phụ trách nhưng KOL đã không nhắn gì hơn 48 giờ. Nên có người thật nhắn một câu riêng.',
      test: (c, A) => A.inFunnel(c) && c.lead_tier === 'Tier A' && c.status === 'active'
        && A.stepRank(A.stepOf(c)) >= 0 && A.stepRank(A.stepOf(c)) < 3 && A.silentHours(c) >= A.HOT_SILENT_HOURS,
      sort: (a, b, A) => A.silentHours(b) - A.silentHours(a) }
  ],

  _utcHoursAgo(ts) {
    // SQLite UTC "YYYY-MM-DD HH:MM:SS"
    const t = Date.parse(String(ts || '').replace(' ', 'T') + 'Z');
    return Number.isFinite(t) ? Math.max(0, (Date.now() - t) / 3600000) : 0;
  },

  silentHours(c) { return this._utcHoursAgo(c.last_user_message_at || c.updated_at); },

  // Tier A, B, C (also "no tier yet"), then Cold at the bottom.
  _tierRank(c) { return ({ 'Tier A': 0, 'Tier B': 1, 'Cold': 3 })[c.lead_tier] ?? 2; },

  _todoRows(t) {
    const rows = this._chats.filter(c => t.test(c, this));
    // Tier A first; inside a tier the list's own order (longest wait) or most recent.
    return rows.sort((a, b) => (this._tierRank(a) - this._tierRank(b)) || (t.sort ? t.sort(a, b, this) : 0));
  },

  renderTodo() {
    const el = document.getElementById('aifu-todo');
    if (!el) return;
    if (!this._chats.length) { el.innerHTML = ''; return; }
    const items = this.TODO.map(t => ({ t, rows: this._todoRows(t) }));
    const total = items.reduce((n, i) => n + i.rows.length, 0);
    const head = `<div style="display:flex;align-items:center;gap:6px;margin-bottom:8px">
        <strong style="font-size:14px">📌 Việc cần làm</strong>
        <span class="info-i" tabindex="0" data-tip="Những lead đang chờ NGƯỜI THẬT xử lý, xếp theo mức gấp. Bấm một ô để lọc bảng bên dưới đúng các lead đó (Tier A nằm trên cùng). Một lead có thể nằm ở nhiều ô. Danh sách tính từ dữ liệu đã tải, bấm 🔄 để cập nhật.">i</span>
        <span style="font-size:12px;color:var(--text2)">${total ? '' : 'Không có việc tồn đọng 🎉'}</span>
      </div>`;
    const name = c => c.name || (c.username ? '@' + c.username : 'ID ' + c.user_id);
    const cards = items.map(({ t, rows }) => {
      const n = rows.length;
      const on = this._statusFilter === t.key;
      const color = !n ? 'var(--text3)' : (t.urgent ? 'var(--red)' : 'var(--accent)');
      const who = n ? rows.slice(0, 2).map(name).join(', ') + (n > 2 ? ` +${n - 2}` : '') : 'Không có';
      return `<button type="button" class="stat-card" ${n ? '' : 'disabled'} onclick="AIFollowUp.filterByStatus('${t.key}')" title="${esc(t.tip)}"
          aria-pressed="${on}" aria-label="${esc(t.label)}: ${n} lead"
          style="text-align:left;padding:10px 12px;font:inherit;color:inherit;cursor:${n ? 'pointer' : 'default'};opacity:${n ? 1 : .55};${on ? 'border-color:var(--accent);' : (n && t.urgent ? 'border-color:var(--red);' : '')}">
          <div class="stat-label" style="font-size:11px">${t.icon} ${t.label}</div>
          <div class="stat-value" style="font-size:20px;color:${color}">${n}</div>
          <div style="font-size:11px;color:var(--text2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(who)}</div>
        </button>`;
    }).join('');
    el.innerHTML = `<div class="card" style="padding:12px 14px;margin-bottom:var(--sp-4)">${head}
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:8px">${cards}</div></div>`;
  },

  clearFilters() {
    this._statusFilter = '';
    this._stepFilter = '';
    this._page = 1;
    this.renderTodo();
    this.renderChips();
    this.renderStepCards();
    this.renderPage();
  },

  // ── Owed answers: we promised "the team will get back to you" ──
  OWED_FILTER: '__owed',
  OWED_DEADLINE_HOURS: 24,

  isOwed(c) {
    return !!c.owed_since && c.status !== 'bot_ignored';
  },

  owedHours(c) {
    // owed_since is SQLite UTC ("YYYY-MM-DD HH:MM:SS")
    const t = Date.parse(String(c.owed_since || '').replace(' ', 'T') + 'Z');
    return Number.isFinite(t) ? Math.max(0, (Date.now() - t) / 3600000) : 0;
  },

  owedBadge(c) {
    if (!this.isOwed(c)) return '';
    const h = this.owedHours(c);
    const late = h >= this.OWED_DEADLINE_HOURS;
    const age = h >= 48 ? `${Math.floor(h / 24)} ngày` : `${Math.floor(h)} giờ`;
    const tip = `Đã hứa với KOL: “${c.owed_note || 'sẽ báo lại'}” — chờ ${age}${late ? ' (quá hạn 24 giờ)' : ''}`;
    const style = late ? 'background:var(--red-dim, rgba(239,68,68,.15));color:var(--red);font-weight:700' : '';
    return `<div style="margin-top:3px"><span class="badge ${late ? '' : 'badge-gray'}" style="font-size:10px;${style}" title="${esc(tip)}">⏳ Nợ trả lời · ${age}</span></div>`;
  },

  async resolveOwed(accountId, userId) {
    try {
      const res = await fetch(`/api/ai-followup/chats/${accountId}/${userId}/owed?owed=false`, { method: 'POST' });
      if (!res.ok) throw new Error('Không cập nhật được');
      this._patchChat(accountId, userId, { owed_since: null, owed_note: null });
      App.toast('Đã đánh dấu là đã trả lời', 'success');
    } catch (e) {
      App.toast(e.message, 'error');
    }
  },

  // Status and step filters combine (e.g. "Cần người thật" + "Đồng ý deal").
  // Clicking the active one again turns it off; "Tất cả" clears both.
  filterByStatus(s) {
    this._statusFilter = (s && this._statusFilter === s) ? '' : s;
    this._page = 1;
    this.renderTodo();
    this.renderChips();
    this.renderStepCards();
    this.renderPage();
  },

  filterByStep(s) {
    this._stepFilter = (this._stepFilter === s) ? '' : s;
    this._page = 1;
    this.renderChips();
    this.renderStepCards();
    this.renderPage();
  },

  _filtered() {
    const s = this._statusFilter;
    const todo = s ? this.TODO.find(t => t.key === s) : null;
    let rows = !s ? this._chats
      : s === this.QUEUE_FILTER ? this._queueRows()
      : s === this.CLEANUP_FILTER ? this._chats.filter(c => this._isCleanup(c))
      : s === this.ANSWERED_FILTER ? this._chats.filter(c => this._isAnswered(c))
      : (todo ? this._todoRows(todo)
        : (s === this.OWED_FILTER
          ? this._chats.filter(c => this.isOwed(c)).sort((a, b) => this.owedHours(b) - this.owedHours(a))
          : this._chats.filter(c => c.status === s)));
    const st = this._stepFilter;
    if (st) rows = rows.filter(c => this.inFunnel(c));
    if (st === 'link_confirmed+') rows = rows.filter(c => this.stepRank(this.stepOf(c)) >= 3);
    else if (st) rows = rows.filter(c => this.stepOf(c) === st);
    const q = this._search;
    if (q) rows = rows.filter(c => [c.username ? '@' + c.username : '', c.name, c.user_id, c.account_name]
      .some(v => v != null && String(v).toLowerCase().includes(q)));
    return rows;
  },

  _search: '',
  _searchTimer: null,
  onSearch(v) {
    clearTimeout(this._searchTimer);
    this._searchTimer = setTimeout(() => {
      this._search = String(v || '').trim().toLowerCase();
      this._page = 1;
      this.renderPage();
    }, 150);
  },

  renderPage() {
    const filtered = this._filtered();
    const pages = Math.max(1, Math.ceil(filtered.length / this.PAGE_SIZE));
    if (this._page > pages) this._page = pages;
    this.renderChats(filtered.slice((this._page - 1) * this.PAGE_SIZE, this._page * this.PAGE_SIZE), filtered.length === 0);
    this.renderSelectionBar();
    const cnt = document.getElementById('aifu-count');
    if (cnt) cnt.textContent = (this._statusFilter || this._stepFilter || this._search)
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
    if (isEmpty && this._chats.length && this._statusFilter === this.QUEUE_FILTER && !this._stepFilter && !this._search) {
      tb.innerHTML = `<tr><td colspan="8" style="text-align:center;padding:32px"><div style="font-size:2rem;margin-bottom:8px">🎉</div><div style="color:var(--text);font-weight:600;margin-bottom:6px">Hàng chờ trống — không KOL nào đang chờ bạn</div><button class="btn btn-ghost btn-sm" onclick="AIFollowUp.clearFilters()">Xem tất cả lead</button></td></tr>`;
      return;
    }
    if (isEmpty && this._chats.length && (this._statusFilter || this._stepFilter || this._search)) {
      tb.innerHTML = `<tr><td colspan="8" style="text-align:center;padding:28px"><div style="color:var(--text);font-weight:600;margin-bottom:6px">Không có lead nào khớp bộ lọc đang chọn</div><button class="btn btn-ghost btn-sm" onclick="AIFollowUp.clearFilters()">Bỏ mọi bộ lọc</button></td></tr>`;
      return;
    }
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
        : (tier === 'Tier B' ? '<span class="badge badge-blue">🔷 B</span>'
          : (tier === 'Cold' ? '<span class="badge badge-gray" title="Cold: lead nguội, ít khả năng chốt">🧊 Cold</span>' : '<span class="badge badge-gray">⚪ C</span>'));
      const lastTxt = String(c.last_text || '').replace(this.HUMAN_PREFIX, '').replace(/\s+/g, ' ').trim();
      const lastLine = lastTxt
        ? `<div style="font-size:11px;color:${c.last_role === 'user' ? 'var(--text)' : 'var(--text2)'};max-width:260px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${esc(lastTxt).replace(/"/g, '&quot;')}">${c.last_role === 'user' ? '👤' : '↩️'} ${esc(lastTxt)}</div>`
        : '';
      const ownBadge = c.is_own ? ' <span class="badge badge-gray" style="font-size:10px;vertical-align:middle" title="Đây là một tài khoản của chính mình, không phải KOL. AI không trả lời và dòng này không nằm trong Hàng chờ.">🏠 Nick nhà</span>' : '';
      const updated = c.updated_at ? new Date(c.updated_at).toLocaleString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh' }) : '—';
      const toggleBtn = c.status === 'active'
        ? `<button class="btn btn-ghost btn-sm" onclick="AIFollowUp.updateStatus(${c.account_id},${c.user_id},'paused_admin')" style="padding:4px 7px;min-width:0" title="Chat tay (tắt AI)" aria-label="Tắt AI cho ${esc(uName)}">⏸</button>`
        : `<button class="btn btn-ghost btn-sm" onclick="AIFollowUp.updateStatus(${c.account_id},${c.user_id},'active')" style="padding:4px 7px;min-width:0" title="Bật AI chat tiếp" aria-label="Bật AI cho ${esc(uName)}">▶️</button>`;
      const blacklistBtn = `<button class="btn btn-danger btn-sm" data-acc="${c.account_id}" data-uid="${c.user_id}" data-username="${esc(c.username||'')}" onclick="AIFollowUp.addToBlacklistDirect(Number(this.dataset.acc),Number(this.dataset.uid),this.dataset.username)" style="padding:4px 7px;min-width:0" title="Blacklist (cấm AI 100%)" aria-label="Blacklist">🚫</button>`;
      const ignoreBtn = c.status === 'bot_ignored' ? '' : `<button class="btn btn-ghost btn-sm" style="padding:4px 7px;min-width:0" onclick="AIFollowUp.updateStatus(${c.account_id},${c.user_id},'bot_ignored')" title="Ignore (bỏ qua lead, AI không chat)" aria-label="Ignore ${esc(uName)}">🙈</button>`;
      const accLabel = c.account_name || `Acc #${c.account_id}`;
      const owedBtn = this.isOwed(c)
        ? `<button class="btn btn-ghost btn-sm" onclick="AIFollowUp.resolveOwed(${c.account_id},${c.user_id})" title="Đã trả lời KOL về việc đã hứa — bỏ khỏi danh sách Nợ trả lời" style="padding:4px 7px;min-width:0" aria-label="Đã trả lời ${esc(uName)}">✅</button>`
        : '';
      const step = this.stepOf(c);
      const sm = this.STEP_META[step] || { label: step, emoji: '', color: 'var(--text2)' };
      const missing = Array.isArray(c.missing_items) ? c.missing_items : [];
      const missingTxt = missing.length ? `Thiếu: ${missing.map(k => this.MISSING_LABELS[k] || k).join(', ')}` : '';
      const remTxt = c.step_reminders_sent ? ` · 🔔${c.step_reminders_sent}` : '';
      const stepCell = `<div style="font-size:12px;font-weight:600;color:${sm.color};white-space:nowrap" title="${esc(missingTxt)}">${sm.emoji} ${esc(sm.label)}</div><div style="font-size:10px;color:var(--text2);font-family:monospace">${this.stepBar(step)}${remTxt}</div>`;
      return `<tr>
        <td style="width:34px;text-align:center"><input type="checkbox" aria-label="Chọn ${esc(uName)}" ${this._selected.has(`${c.account_id}:${c.user_id}`) ? 'checked' : ''} onchange="AIFollowUp.toggleSelection('${c.account_id}:${c.user_id}',this.checked)"></td>
        <td><div style="font-weight:600">${esc(uName)}${nudgeBadge}${ownBadge}</div><div style="font-size:11px;color:var(--text2);font-family:monospace">ID ${c.user_id} • 👤 ${esc(accLabel)}</div>${lastLine}</td>
        <td><div style="display:flex;align-items:center;gap:6px">${tierBadge}<span style="font-size:11px;color:var(--text2)">${score}%</span></div><div style="width:70px;height:4px;background:color-mix(in srgb, var(--text) 8%, transparent);border-radius:2px;overflow:hidden;margin-top:4px"><div style="width:${Math.min(100, Math.max(0, score))}%;height:100%;background:${score >= 80 ? 'var(--orange)' : score >= 50 ? 'var(--accent)' : 'var(--text3)'}"></div></div></td>
        <td>${c.reply_count || 0}</td>
        <td>${stepCell}</td>
        <td><span class="${meta.badge}">${meta.label}</span>${this.owedBadge(c)}</td>
        <td style="font-size:12px;color:var(--text2);white-space:nowrap">${updated}</td>
        <td><div style="display:flex;gap:4px;justify-content:flex-end;white-space:nowrap">${owedBtn}${toggleBtn}${ignoreBtn}${blacklistBtn}<button class="btn btn-primary btn-sm" onclick="AIFollowUp.openChat(${c.account_id},${c.user_id})">💬 Chat</button></div></td>
      </tr>`;
    }).join('');
  },

  async addToBlacklistDirect(accountId, userId, username) {
    if (!window.confirm(`Đưa lead ${username ? '@' + username : 'ID ' + userId} vào Blacklist để CẤM AI CHAT 100%?`)) return false;
    try {
      const res = await fetch('/api/blacklist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId, username: username || undefined, reason: 'Chuyển chat tay thủ công từ AI Agent' })
      });
      if (!res.ok) throw new Error('Cập nhật Blacklist thất bại');
      App.toast(`Đã đưa ${username ? '@' + username : userId} vào Blacklist (Chat tay 100%)`, 'success');
      await this.loadChats();
      return true;
    } catch (e) { App.toast(e.message, 'error'); return false; }
  },

  // Blacklist from the chat window: only after it really went through, move on to
  // the next lead in the list (or close when there is none).
  async blacklistFromModal() {
    const c = this._modalChat;
    if (!c) return;
    const next = this._neighbor(1);
    if (!(await this.addToBlacklistDirect(c.account_id, c.user_id, c.username || ''))) return;
    if (!this._isOpen(c.account_id, c.user_id)) return; // user moved on meanwhile
    if (next) this.openChat(next.account_id, next.user_id); else this.closeHistoryModal();
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

  // ── One form instead of a chain of browser prompt() boxes ──
  // formDialog({title, intro, fields:[{key,label,type,value,placeholder,hint,tip,required,wide}], submitLabel})
  // → Promise<{key: value}> or null when cancelled. Nothing is saved until the user presses the submit button,
  // every field stays visible and editable, Esc / Huỷ cancels, Enter submits (Ctrl+Enter inside a textarea).
  formDialog({ title, intro = '', fields = [], submitLabel = 'Lưu', validate = null }) {
    return new Promise(resolve => {
      const prevFocus = document.activeElement;
      const uid = 'aifu-fd-' + Date.now();
      const ov = document.createElement('div');
      ov.className = 'modal-overlay open';
      ov.style.zIndex = '400';
      const fieldHtml = fields.map((f, i) => {
        const id = `${uid}-${i}`;
        const tip = f.tip ? ` <span class="info-i" tabindex="0" data-tip="${esc(f.tip)}">i</span>` : '';
        const hint = f.hint ? `<div id="${id}-h" style="font-size:11px;color:var(--text2);margin-top:4px;line-height:1.45">${esc(f.hint)}</div>` : '';
        const aria = f.hint ? ` aria-describedby="${id}-h"` : '';
        const span = (f.wide || f.type === 'textarea' || f.type === 'checkbox') ? 'grid-column:1/-1;' : '';
        if (f.type === 'checkbox') {
          return `<div style="${span}"><label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:13px">
              <input type="checkbox" id="${id}" data-key="${esc(f.key)}" ${f.value ? 'checked' : ''}${aria} style="width:16px;height:16px;flex:none">
              <span>${esc(f.label)}</span>${tip}</label>${hint}</div>`;
        }
        const req = f.required ? ' required aria-required="true"' : '';
        const ph = f.placeholder ? ` placeholder="${esc(f.placeholder)}"` : '';
        const input = f.type === 'select'
          ? `<select class="form-input" id="${id}" data-key="${esc(f.key)}"${req}${aria} style="width:100%">${(f.options || []).map(o => `<option value="${esc(o.value)}"${String(o.value) === String(f.value ?? '') ? ' selected' : ''}>${esc(o.label)}</option>`).join('')}</select>`
          : f.type === 'textarea'
          ? `<textarea class="form-input form-textarea" id="${id}" data-key="${esc(f.key)}" rows="${f.rows || 5}"${ph}${req}${aria} style="width:100%;resize:vertical">${esc(f.value || '')}</textarea>`
          : `<input class="form-input" type="text" id="${id}" data-key="${esc(f.key)}" value="${esc(f.value || '')}" autocomplete="off"${ph}${req}${aria} style="width:100%">`;
        return `<div style="${span}min-width:0"><label class="form-label" for="${id}" style="display:flex;align-items:center;gap:6px">${esc(f.label)}${f.required ? ' <span style="color:var(--red)" aria-hidden="true">*</span>' : ''}${tip}</label>${input}${hint}</div>`;
      }).join('');
      ov.innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-labelledby="${uid}-t" style="width:620px;overflow:visible;display:flex;flex-direction:column">
          <div class="modal-header"><span class="modal-title" id="${uid}-t">${esc(title)}</span><button type="button" class="modal-close" data-fd="cancel" aria-label="Đóng">&times;</button></div>
          <form novalidate style="display:flex;flex-direction:column;min-height:0">
            <div class="modal-body" style="overflow-y:auto;max-height:calc(85vh - 140px)">
              ${intro ? `<div style="font-size:13px;color:var(--text2);margin-bottom:12px;line-height:1.5">${intro}</div>` : ''}
              <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px 14px">${fieldHtml}</div>
              <div data-fd="error" role="alert" style="display:none;margin-top:12px;font-size:13px;color:var(--red)"></div>
            </div>
            <div style="display:flex;justify-content:flex-end;gap:8px;padding:12px 20px;border-top:1px solid var(--border)">
              <button type="button" class="btn btn-ghost" data-fd="cancel">Huỷ</button>
              <button type="submit" class="btn btn-primary">${esc(submitLabel)}</button>
            </div>
          </form>
        </div>`;
      document.body.appendChild(ov);
      const form = ov.querySelector('form');
      const errEl = ov.querySelector('[data-fd="error"]');
      const done = result => {
        document.removeEventListener('keydown', onKey, true);
        ov.remove();
        try { prevFocus && prevFocus.focus && prevFocus.focus(); } catch (_) {}
        resolve(result);
      };
      const collect = () => {
        const out = {};
        ov.querySelectorAll('[data-key]').forEach(el => { out[el.dataset.key] = el.type === 'checkbox' ? el.checked : el.value.trim(); });
        return out;
      };
      const submit = () => {
        const values = collect();
        const missing = fields.find(f => f.required && !values[f.key]);
        const msg = missing ? `Chưa nhập “${missing.label}”.` : (validate ? validate(values) : '');
        if (msg) {
          errEl.textContent = msg; errEl.style.display = 'block';
          const bad = missing && ov.querySelector(`[data-key="${missing.key}"]`);
          if (bad) bad.focus();
          return;
        }
        done(values);
      };
      const onKey = e => {
        if (!document.body.contains(ov)) return;
        if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); done(null); return; }
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && e.target.tagName === 'TEXTAREA') { e.preventDefault(); submit(); return; }
        if (e.key === 'Tab') {   // keep focus inside the dialog
          const f = [...ov.querySelectorAll('input,select,textarea,button,[tabindex="0"]')].filter(x => !x.disabled);
          if (!f.length) return;
          const first = f[0], last = f[f.length - 1];
          if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
          else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
          else if (!ov.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
        }
      };
      document.addEventListener('keydown', onKey, true);
      form.addEventListener('submit', e => { e.preventDefault(); submit(); });
      ov.querySelectorAll('[data-fd="cancel"]').forEach(b => b.addEventListener('click', () => done(null)));
      const firstField = ov.querySelector('[data-key]:not([type="checkbox"])') || ov.querySelector('[data-key]');
      if (firstField) { firstField.focus(); if (firstField.select && firstField.tagName === 'INPUT') firstField.select(); }
    });
  },

  _csv(v) { return String(v || '').split(',').map(x => x.trim()).filter(Boolean); },

  _leadNames(list, max = 4) {
    const name = c => c.name || (c.username ? '@' + c.username : 'ID ' + c.user_id);
    return list.slice(0, max).map(name).join(', ') + (list.length > max ? ` và ${list.length - max} lead khác` : '');
  },

  async assignSelected() {
    const selected = this._selectedChats();
    if (!selected.length) return;
    const v = await this.formDialog({
      title: `📍 Phân công ${selected.length} lead`,
      intro: `Áp dụng cho: <strong>${esc(this._leadNames(selected))}</strong>.`,
      submitLabel: `Lưu cho ${selected.length} lead`,
      fields: [
        { key: 'regions', label: 'Khu vực phân phối', placeholder: 'VD: Vietnam, SEA', hint: 'Phân cách bằng dấu phẩy. Để trống = giữ nguyên khu vực hiện có của từng lead.' },
        { key: 'campaigns', label: 'Campaign phân phối', placeholder: 'VD: Futures Q4', hint: 'Phân cách bằng dấu phẩy. Để trống = giữ nguyên.' },
        { key: 'enabled', type: 'checkbox', label: 'Bật phân phối cho các lead này', value: true,
          tip: 'Khi bật, lead được đưa vào danh sách nhận thông tin campaign theo khu vực / campaign đã gán. Bỏ chọn để tắt phân phối cho các lead đã chọn.' }
      ]
    });
    if (!v) return;
    const payload = { distribution_enabled: v.enabled };
    if (v.regions) payload.distribution_regions = this._csv(v.regions);
    if (v.campaigns) payload.distribution_campaigns = this._csv(v.campaigns);
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
    const v = await this.formDialog({
      title: `✉️ Gửi tin cho ${selected.length} lead`,
      intro: `Gửi tới: <strong>${esc(this._leadNames(selected))}</strong>.<br>Tin được gửi ngay, cùng một nội dung, từ nick đang phụ trách từng lead.`,
      submitLabel: `Gửi tới ${selected.length} lead`,
      fields: [
        { key: 'message', type: 'textarea', label: 'Nội dung tin nhắn', required: true, rows: 6,
          hint: 'Xuống dòng bằng Enter. Bấm Ctrl+Enter để gửi.' }
      ]
    });
    if (!v) return;
    try {
      const res = await fetch('/api/ai-followup/bulk-send', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ recipients:selected.map(c => ({account_id:c.account_id, user_id:c.user_id})), message:v.message }) });
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
      const ex = p.exchange || 'WEEX';
      const chat = this._chats.find(x => x.account_id === accountId && x.user_id === userId);
      const cur = {
        weex_uid: p.weex_uid || '', volume_claimed: p.volume_claimed || '', vip_code: p.vip_code || '',
        affiliate_link: p.affiliate_link || '', selected_campaign: p.selected_campaign || '',
        first_post_link: p.first_post_link || '',
        distribution_regions: (p.distribution_regions || []).join(', '),
        distribution_campaigns: (p.distribution_campaigns || []).join(', '),
        distribution_enabled: !!p.distribution_enabled
      };
      const v = await this.formDialog({
        title: '📍 Hồ sơ & phân công' + (chat ? ` — ${this._leadNames([chat])}` : ''),
        intro: 'Sửa ô nào cần sửa rồi bấm <strong>Lưu hồ sơ</strong>. Ô để trống nghĩa là chưa có thông tin.',
        submitLabel: 'Lưu hồ sơ',
        fields: [
          { key: 'weex_uid', label: `${ex} UID của KOL`, value: cur.weex_uid, placeholder: 'Chưa có',
            tip: `Nhập UID ở đây nghĩa là admin xác nhận UID này đúng. Lead đang ở bước “Đồng ý deal” sẽ tự chuyển sang “Đã nhận UID”.` },
          { key: 'volume_claimed', label: 'Volume KOL khai (triệu USD/tháng)', value: cur.volume_claimed, placeholder: 'VD: 15 hoặc 15M',
            tip: 'Con số KOL tự khai, dùng để chọn bậc deal khởi điểm. Đây chưa phải volume đã xác minh qua proof.' },
          { key: 'vip_code', label: 'VIP code', value: cur.vip_code, placeholder: 'Chưa cấp',
            tip: `Mã mời (vipCode) bên mình cấp cho KOL để ${ex} ghi nhận người dùng KOL mang về.` },
          { key: 'affiliate_link', label: 'Affiliate link', value: cur.affiliate_link, placeholder: 'https://…' },
          { key: 'selected_campaign', label: 'Campaign KOL đã chọn', value: cur.selected_campaign, hint: 'Chỉ một campaign.' },
          { key: 'first_post_link', label: 'Link bài đăng đầu tiên', value: cur.first_post_link, placeholder: 'https://…', hint: 'Điền khi KOL đã đăng bài.' },
          { key: 'distribution_regions', label: 'Khu vực phân phối', value: cur.distribution_regions, placeholder: 'VD: Vietnam, SEA', hint: 'Phân cách bằng dấu phẩy.' },
          { key: 'distribution_campaigns', label: 'Campaign phân phối', value: cur.distribution_campaigns, hint: 'Phân cách bằng dấu phẩy.' },
          { key: 'distribution_enabled', type: 'checkbox', label: 'Bật phân phối cho lead này', value: cur.distribution_enabled,
            tip: 'Khi bật, lead được đưa vào danh sách nhận thông tin campaign theo khu vực / campaign đã gán ở trên.' }
        ]
      });
      if (!v) return;
      // Only what actually changed is sent, so an untouched UID does not re-trigger the step change.
      const payload = {};
      Object.keys(cur).forEach(k => { if (v[k] !== cur[k]) payload[k] = v[k]; });
      if ('distribution_regions' in payload) payload.distribution_regions = this._csv(v.distribution_regions);
      if ('distribution_campaigns' in payload) payload.distribution_campaigns = this._csv(v.distribution_campaigns);
      if (!Object.keys(payload).length) { App.toast('Không có thay đổi nào để lưu', 'info'); return; }
      const save = await fetch(`/api/ai-followup/chats/${accountId}/${userId}/profile`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload) });
      if (!save.ok) throw new Error('Lưu hồ sơ thất bại');
      App.toast('Đã lưu hồ sơ và phân công lead', 'success');
      if (this._modalChat && this._modalChat.account_id === accountId && this._modalChat.user_id === userId) this.loadStepPanel(accountId, userId);
      this.loadChats();
    } catch (e) { App.toast(e.message, 'error'); }
  },

  // Hand the lead to another of our nicks (the KOL blocked this one, the nick is limited…).
  async transferLead(accountId, userId) {
    if (this._transferring) { App.toast('Đang chuyển lead, vui lòng đợi…', 'info'); return; }
    try {
      const chat = this._chats.find(x => x.account_id === accountId && x.user_id === userId);
      API.clearAccountsCache();   // a nick connected or dropped since the tab loaded
      const accounts = ((await API.getAccounts()).accounts || []).filter(a => a.id !== accountId && a.is_logged_in && !a.session_error);
      if (!accounts.length) { App.toast('Không có nick nào khác đang kết nối để chuyển', 'info'); return; }
      const v = await this.formDialog({
        title: '🔀 Chuyển lead sang nick khác' + (chat ? ` — ${this._leadNames([chat])}` : ''),
        intro: 'Dùng khi KOL đã chặn nick hiện tại hoặc nick bị hạn chế. App chuyển hồ sơ, tóm tắt và bước sang nick mới, <strong>tắt AI</strong> ở đó, ẩn cuộc chat cũ, rồi mở topic mới trên forum kèm lịch sử của nick cũ. Cuộc chat mới bắt đầu trống. <strong>Không tin nào được gửi cho KOL</strong> — bạn tự gõ tin đầu tiên từ nick mới.',
        submitLabel: 'Chuyển lead',
        fields: [
          { key: 'to', type: 'select', label: 'Nick nhận', wide: true, required: true,
            options: [{ value: '', label: 'Chọn nick…' }, ...accounts.map(a => ({ value: a.id, label: `${accDisplayName(a)}${a.phone ? ` (${a.phone})` : ''}` }))],
            tip: 'Chỉ liệt kê nick đang kết nối. KOL phải có @username để nick mới tìm được họ trên Telegram. Phân phối bài channel bị tắt ở nick mới — bật lại trong Hồ sơ sau khi đã nhắn tay. Nếu KOL chặn vì không muốn nhận tin, chỉ nên gửi một tin, nói rõ mình là ai.' }
        ]
      });
      if (!v) return;
      this._transferring = true;
      App.toast('Đang chuyển lead…', 'info');
      const res = await fetch(`/api/ai-followup/chats/${accountId}/${userId}/transfer`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ to_account_id: +v.to }) });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { this.loadChats(); throw new Error(data.detail || 'Chuyển lead thất bại'); }
      const forumNote = data.forum === 'relaying' ? ' Topic mới đang được mở trên forum.' : ' Forum không mở topic (bot forum không chạy).';
      App.toast(`Đã chuyển lead sang nick mới, AI đang tắt.${forumNote}`, 'success');
      await this.loadChats();
      if (this._chats.some(x => x.account_id === data.account_id && x.user_id === userId)) this.openChat(data.account_id, userId);
      else this.closeHistoryModal();
    } catch (e) { App.toast(e.message, 'error'); }
    finally { this._transferring = false; }
  },

  // ── 5-step funnel: panel + actions ─────────────────────────────────
  async loadStepPanel(accountId, userId) {
    const panel = document.getElementById('aifu-modal-step-panel');
    if (!panel) return;
    try {
      const res = await fetch(`/api/ai-followup/chats/${accountId}/${userId}/step`);
      if (!res.ok) throw new Error('Không tải được trạng thái bước');
      const st = await res.json();
      if (!this._isOpen(accountId, userId)) return; // another lead was opened meanwhile
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
    const haveLabel = k => k === 'weex_uid' ? (st.uid_label || this.MISSING_LABELS.weex_uid) : (this.HAVE_LABELS[k] || this.MISSING_LABELS[k] || k);
    const haveTxt = Object.entries(have).filter(([, v]) => v).map(([k, v]) => `${esc(haveLabel(k))}: ${esc(String(v)).slice(0, 40)}`).join(' · ');
    const pred = st.predicted_tier;
    const predTip = 'Bậc khởi điểm theo volume TRUNG BÌNH/THÁNG của 3 tháng gần nhất ở sàn KOL đang làm (video quay màn hình dashboard), đối chiếu bảng bậc (VD 10–20M → Tier 2). Không có bằng chứng → Tier 1. AI chỉ nói “nhiều khả năng”; team xem video xác minh rồi mới áp dụng. Nếu volume thật ở WEEX thấp hơn thì điều chỉnh ở kỳ review quý.';
    const predTxt = (pred
      ? `Bậc khởi điểm dự kiến: Tier ${pred.tier} (${Number(pred.revshare_pct)}% + $${Number(pred.bonus_per_1m_taker_usd)}/1M volume futures) với ~${Number(pred.volume_m)}M/tháng khai — chờ team xác minh dashboard 3 tháng`
      : 'Bậc khởi điểm: chưa có volume → bắt đầu Tier 1')
      + ` <span class="info-i" tabindex="0" data-tip="${esc(predTip)}">i</span>`;
    const deal = st.current_deal || {};
    const dealVal = [deal.current_cex, deal.current_deal].filter(Boolean).map(v => esc(String(v)).slice(0, 160)).join(' — ');
    const dealTip = 'Deal KOL đang nhận ở sàn hiện tại (revshare/rebate, bonus volume, phí cố định/upfront, thời hạn/độc quyền, lịch trả). AI hỏi 1 lần khi KOL nhắc sàn khác, đòi deal tốt hơn hoặc đòi upfront, rồi tự ghi vào đây. Dùng để team xin deal tốt hơn; AI không hứa match.';
    const dealTxt = dealVal ? `<div style="color:var(--text2)"><b>💼 Deal hiện tại:</b> ${dealVal} <span class="info-i" tabindex="0" data-tip="${esc(dealTip)}">i</span></div>` : '';
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
          ${dealTxt}
          <div style="color:var(--text2)">${predTxt}</div>
        </div>
      </div>`;
  },

  async setStep(accountId, userId, step, reason) {
    if (!step) return;
    const label = this.stepLabel(step);
    if (reason === undefined) {
      const v = await this.formDialog({
        title: `Chuyển lead sang bước “${label}”`,
        submitLabel: 'Chuyển bước',
        fields: [{ key: 'reason', label: 'Lý do (không bắt buộc)', wide: true, placeholder: 'VD: KOL đã xác nhận qua gọi điện',
          hint: 'Lý do được lưu vào lịch sử chuyển bước của lead.' }]
      });
      if (!v) return;
      reason = v.reason;
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
  // Proof a lead sent: photo inline (click = full size), video/file = link to the forum topic message.
  _mediaHtml(media) {
    if (!media) return '';
    const safeUrl = u => (typeof u === 'string' && /^(\/api\/ai-followup\/proof-media\/|https:\/\/t\.me\/)/.test(u)) ? u : '';
    const img = safeUrl(media.image_url);
    const link = safeUrl(media.topic_link);
    const parts = [];
    if (img) {
      parts.push(`<a href="${esc(img)}" target="_blank" rel="noopener"><img src="${esc(img)}" loading="lazy" alt="Ảnh proof" style="display:block;max-width:260px;max-height:260px;border-radius:8px;margin-bottom:6px;object-fit:contain;background:var(--bg2)"></a>`);
    }
    if (link) {
      const label = media.type === 'video' ? '🎬 Xem video trong topic' : media.type === 'photo' ? '🗂️ Mở trong topic' : '📎 Mở file trong topic';
      parts.push(`<a href="${esc(link)}" target="_blank" rel="noopener" style="display:inline-block;margin-bottom:6px;color:var(--accent);font-weight:600">${label}</a>`);
    } else if (!img && (media.forward || media.error)) {
      const why = media.error === 'too_large' ? 'file quá lớn (>200MB)' : 'chưa forward được vào nhóm';
      parts.push(`<div style="font-size:12px;color:var(--text2);margin-bottom:6px" title="${esc(media.forward || media.error || '')}">⚠️ Proof ${why}</div>`);
    }
    return parts.length ? `<div style="white-space:normal">${parts.join('')}</div>` : '';
  },

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
      if (this._isOpen(accountId, userId)) {
        // Chat window is open on this lead: change the row in place, keep the window.
        const next = status === 'bot_ignored' ? this._neighbor(1) : null;
        this._patchChat(accountId, userId, { status });
        if (status === 'bot_ignored') {
          if (next) this.openChat(next.account_id, next.user_id); else this.closeHistoryModal();
        }
      } else {
        this.loadChats();
      }
    } catch (e) {
      App.toast(e.message, 'error');
    }
  },

  // ── Supported KOLs (deal_agreed+ and dormant) ─────────────────────
  _kols: [],
  _kolFilter: 'current',

  async loadSupportedCount() {
    try {
      const d = await (await fetch('/api/ai-followup/supported-kols')).json();
      this._kols = d.kols || [];
      const el = document.getElementById('aifu-stat-supported');
      if (el) el.textContent = d.past ? `${d.current} · ${d.past}` : String(d.current);
      if (el) el.title = `${d.current} đang support · ${d.past} đã support`;
    } catch (e) { /* card stays – */ }
  },

  async openSupportedKols() {
    document.getElementById('aifu-kol-modal')?.classList.add('open');
    document.getElementById('aifu-kol-body').innerHTML = '<div style="text-align:center;color:var(--text2);padding:20px">⏳ Đang tải...</div>';
    await this.loadSupportedCount();
    this.renderSupportedKols();
  },

  filterSupported(f) { this._kolFilter = f; this.renderSupportedKols(); },

  renderSupportedKols() {
    const cur = this._kols.filter(k => k.support_state === 'current');
    const past = this._kols.filter(k => k.support_state === 'past');
    const chip = (v, l) => `<button class="btn btn-sm ${this._kolFilter === v ? 'btn-primary' : 'btn-ghost'}" onclick="AIFollowUp.filterSupported('${v}')">${l}</button>`;
    document.getElementById('aifu-kol-chips').innerHTML =
      chip('current', `🟢 Đang support (${cur.length})`) + chip('past', `💤 Đã support (${past.length})`) + chip('all', `Tất cả (${this._kols.length})`);
    const rows = this._kolFilter === 'current' ? cur : this._kolFilter === 'past' ? past : this._kols;
    const body = document.getElementById('aifu-kol-body');
    if (!rows.length) { body.innerHTML = '<div style="text-align:center;color:var(--text2);padding:20px">Chưa có KOL nào</div>'; return; }
    const d = v => v ? new Date(String(v).replace(' ', 'T') + (String(v).includes('Z') || String(v).includes('+') ? '' : 'Z')).toLocaleDateString('vi-VN') : '–';
    const link = (u, t) => u ? `<a href="${esc(u)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${esc(t)}</a>` : '–';
    body.innerHTML = `<table class="table" style="width:100%;font-size:12px"><thead><tr>
        <th>KOL</th><th>Account</th><th>Bước</th><th>UID / VIP</th><th>Kênh</th><th>Ngày chốt</th><th>Chăm sóc gần nhất</th>
      </tr></thead><tbody>${rows.map(k => {
        const sm = this.STEP_META[k.kol_step] || { label: k.kol_step, emoji: '', color: 'var(--text2)' };
        const who = esc(k.name || '') + (k.username ? ` <span style="color:var(--text2)">@${esc(k.username)}</span>` : '');
        const chan = k.channel_link ? link(k.channel_link, k.followers ? `${k.followers} followers` : 'Kênh') : (k.followers ? esc(String(k.followers)) : '–');
        return `<tr style="cursor:pointer" onclick="AIFollowUp.openKolChat(${k.account_id},${k.user_id})" title="Mở chat">
          <td>${who || '#' + k.user_id}</td>
          <td>${esc(k.account_name || '#' + k.account_id)}</td>
          <td style="color:${sm.color};white-space:nowrap;font-weight:600">${sm.emoji} ${esc(sm.label)}</td>
          <td style="font-family:monospace">${esc(k.weex_uid || '–')}${k.vip_code ? ` / ${esc(k.vip_code)}` : ''}</td>
          <td>${chan}</td>
          <td>${d(k.deal_at)}</td>
          <td>${d(k.last_care_at)}</td>
        </tr>`;
      }).join('')}</tbody></table>`;
  },

  openKolChat(accountId, userId) {
    if (!this._chats.find(x => x.account_id === accountId && x.user_id === userId)) {
      const k = this._kols.find(x => x.account_id === accountId && x.user_id === userId);
      if (k) this._chats.push({ ...k, missing_items: [] });
    }
    document.getElementById('aifu-kol-modal')?.classList.remove('open');
    this.openChat(accountId, userId);
  },

  // ── Chat translation (read-only aid, never sent) ──────────────────
  _trCache: new Map(), // trimmed text -> {tr, lang}; successful translations only

  translateOn() {
    return localStorage.getItem('aifu_tr_en') !== '0'; // on by default
  },

  _ensureTranslateBtn() {
    if (document.getElementById('aifu-tr-btn')) { this._paintAIBtn(); this._paintNav(); return this._paintTranslateBtn(); }
    const close = document.querySelector('#aifu-history-modal .modal-header .modal-close');
    if (!close) return;
    close.insertAdjacentHTML('beforebegin', `<span style="margin-left:auto;margin-right:8px;display:inline-flex;align-items:center;gap:4px;flex-wrap:wrap;justify-content:flex-end">
      <span id="aifu-nav" style="display:inline-flex;align-items:center;gap:4px;margin-right:8px"></span>
      <button id="aifu-ai-btn" class="btn btn-sm" onclick="AIFollowUp.toggleModalAI()"></button>
      <span class="info-i" tabindex="0" data-tip="Công tắc AI cho riêng lead này. Bật: AI tự trả lời; nếu KOL là người nhắn sau cùng thì AI sẽ trả lời NGAY. Tắt: chỉ người thật chat. Khi bạn tự gửi một tin, AI tự tắt cho lead này. Lưu ý: lead còn ở bước 0 (chưa chốt) mà bị tắt AI sẽ được tự bật lại sau 12 giờ im lặng.">i</span>
      <button id="aifu-tr-btn" class="btn btn-sm" onclick="AIFollowUp.toggleTranslate()"></button>
      <span class="info-i" tabindex="0" data-tip="Tự dịch tin nhắn sang tiếng Anh, hiện ngay dưới mỗi tin. Chỉ để bạn đọc — không gửi gì cho lead. Tin vốn là tiếng Anh/tiếng Việt thì không dịch. Bản dịch được lưu đệm nên mở lại rất nhanh.">i</span>
    </span>`);
    this._paintAIBtn();
    this._paintNav();
    this._paintTranslateBtn();
  },

  // ── Prev / Next inside the chat window ─────────────────────────────
  // Walks the list currently shown in the table (same filter, search and order).
  // Same lead as the one in the chat window (the row object is replaced on reload).
  _sameLead(c) {
    const m = this._modalChat;
    return !!m && !!c && m.account_id === c.account_id && m.user_id === c.user_id;
  },

  _isOpen(accountId, userId) {
    const c = this._modalChat;
    return !!c && c.account_id === accountId && c.user_id === userId
      && !!document.getElementById('aifu-history-modal')?.classList.contains('open');
  },

  // The order Prev / Next walks is frozen while the chat window is open: a reload puts
  // the lead just answered back on top (the server sorts by last update), and "Next"
  // would then land on leads already handled. Leads that left the filter drop out,
  // new ones join at the end; a filter or search change takes a fresh order.
  _navList() {
    const cur = this._filtered();
    const key = x => x.account_id + ':' + x.user_id;
    const sig = [this._statusFilter, this._stepFilter, this._search].join('|');
    if (!this._navKeys || this._navSig !== sig) {
      this._navKeys = cur.map(key);
      this._navSig = sig;
      return cur;
    }
    const byKey = new Map(cur.map(x => [key(x), x]));
    const list = this._navKeys.map(k => byKey.get(k)).filter(Boolean);
    if (list.length < cur.length) {
      const known = new Set(this._navKeys);
      for (const x of cur) if (!known.has(key(x))) { list.push(x); this._navKeys.push(key(x)); }
    }
    return list;
  },

  _navState() {
    const list = this._navList();
    const c = this._modalChat;
    const i = c ? list.findIndex(x => x.account_id === c.account_id && x.user_id === c.user_id) : -1;
    if (i >= 0) this._modalIdx = i;
    return { list, i };
  },

  // The lead `delta` steps away. When the open lead has just left the list (it was
  // answered, ignored…), "next" is whoever moved into its place.
  _neighbor(delta) {
    const { list, i } = this._navState();
    if (i >= 0) return list[i + delta] || null;
    const at = Math.min(this._modalIdx ?? 0, list.length);
    return (delta > 0 ? list[at] : list[at - 1]) || null;
  },

  openNeighbor(delta) {
    const n = this._neighbor(delta);
    if (n) this.openChat(n.account_id, n.user_id);
  },

  _paintNav() {
    const el = document.getElementById('aifu-nav');
    if (!el) return;
    if (!this._modalChat) { el.innerHTML = ''; return; }
    const { list, i } = this._navState();
    const prev = this._neighbor(-1), next = this._neighbor(1);
    const pos = i >= 0 ? `${i + 1} / ${list.length}` : `– / ${list.length}`;
    const tip = i >= 0 ? 'Vị trí của lead này trong danh sách đang lọc' : 'Lead này đã rời danh sách đang lọc (ví dụ vừa được trả lời). Bấm Tiếp để sang lead kế tiếp.';
    el.innerHTML = `<button class="btn btn-ghost btn-sm" ${prev ? '' : 'disabled'} onclick="AIFollowUp.openNeighbor(-1)" title="Lead trước trong danh sách đang lọc">◀ Trước</button>`
      + `<span style="font-size:12px;color:var(--text2);white-space:nowrap" title="${tip}">${pos}</span>`
      + `<button class="btn btn-ghost btn-sm" ${next ? '' : 'disabled'} onclick="AIFollowUp.openNeighbor(1)" title="Lead kế tiếp trong danh sách đang lọc">Tiếp ▶</button>`;
  },

  _paintAIBtn(busy) {
    const btn = document.getElementById('aifu-ai-btn');
    const c = this._modalChat;
    if (!btn) return;
    btn.hidden = !c;
    if (!c) return;
    const on = c.status === 'active';
    btn.className = `btn btn-sm ${on ? 'btn-green' : 'btn-ghost'}`;
    btn.textContent = busy ? '🤖 …' : `🤖 AI ${on ? 'bật' : 'tắt'}`;
    btn.title = on ? 'AI đang tự trả lời lead này — bấm để chuyển sang chat tay' : 'Đang chat tay — bấm để AI tự trả lời tiếp';
  },

  async toggleModalAI() {
    const c = this._modalChat;
    if (!c) return;
    const status = c.status === 'active' ? 'paused_admin' : 'active';
    this._paintAIBtn(true);
    try {
      const res = await fetch(`/api/ai-followup/chats/${c.account_id}/${c.user_id}/status`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status })
      });
      if (!res.ok) throw new Error('Cập nhật trạng thái thất bại');
      const data = await res.json().catch(() => ({}));
      this._patchChat(c.account_id, c.user_id, { status });
      App.toast(status === 'active' ? (data.message || 'Đã bật AI') : 'Đã tắt AI — chat tay', 'success');
    } catch (e) {
      App.toast(e.message, 'error');
    }
    this._paintAIBtn();
  },

  _paintTranslateBtn(busy) {
    const btn = document.getElementById('aifu-tr-btn');
    if (!btn) return;
    const on = this.translateOn();
    btn.className = `btn btn-sm ${on ? 'btn-primary' : 'btn-ghost'}`;
    btn.textContent = busy ? '🌐 …' : `🌐 EN ${on ? 'bật' : 'tắt'}`;
    btn.title = on ? 'Đang tự dịch sang tiếng Anh — bấm để tắt' : 'Bấm để tự dịch tin nhắn sang tiếng Anh';
  },

  toggleTranslate() {
    localStorage.setItem('aifu_tr_en', this.translateOn() ? '0' : '1');
    this._paintTranslateBtn();
    if (this._modalChat) this._applyTranslations(this._modalChat);
  },

  async _applyTranslations(c) {
    const box = document.getElementById('aifu-modal-chat-box');
    if (!box) return;
    const slots = [...box.querySelectorAll('.aifu-tr[data-tr-idx]')];
    if (!this.translateOn()) { slots.forEach(el => { el.hidden = true; }); return; }
    const texts = this._modalTexts || [];
    const need = [...new Set(texts.filter(t => t && !this._trCache.has(t)))];
    if (need.length) this._paintTranslateBtn(true);
    for (let i = 0; i < need.length; i += 50) {
      const chunk = need.slice(i, i + 50);
      try {
        const res = await fetch('/api/members/translate-descriptions', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ texts: chunk, target_lang: 'en' })
        });
        if (!res.ok) break;
        const data = await res.json();
        for (const t of chunk) {
          const tr = data.translations?.[t];
          const lang = data.langs?.[t] || '';
          if (tr !== undefined && lang) this._trCache.set(t, { tr, lang }); // lang '' = failed, retry next open
        }
      } catch (e) { break; }
      if (!this._sameLead(c)) return;
    }
    this._paintTranslateBtn();
    if (!this._sameLead(c) || !this.translateOn()) return;
    const norm = x => (x || '').toLowerCase().replace(/\s+/g, ' ').trim();
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    slots.forEach(el => {
      const src = texts[+el.dataset.trIdx];
      const hit = src && this._trCache.get(src);
      const show = !!hit && !['en', 'vi'].includes(hit.lang.split('-')[0]) && norm(hit.tr) !== norm(src);
      el.hidden = !show;
      if (show) el.textContent = `🌐 ${hit.tr}`;
    });
    if (atBottom) box.scrollTop = box.scrollHeight;
  },

  // ── Chat modal ────────────────────────────────────────────────────
  async openChat(accountId, userId) {
    const c = this._chats.find(x => x.account_id === accountId && x.user_id === userId);
    if (!c) return;
    const modal = document.getElementById('aifu-history-modal');
    if (!modal) return;
    // A draft typed for another lead must never be sent to this one.
    const switching = !this._modalChat || this._modalChat.account_id !== accountId || this._modalChat.user_id !== userId;
    this._modalChat = c;
    const draft = document.getElementById('aifu-chat-input');
    if (draft && switching) { draft.value = ''; draft.style.height = 'auto'; }

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
    this._modalTexts = [];
    this._modalLeadTexts = [];
    this._ensureTranslateBtn();
    this.loadStepPanel(accountId, userId);
    try {
      const histRes = await fetch(`/api/ai-followup/chats/${accountId}/${userId}/history`);
      const histData = await histRes.json();
      if (!this._sameLead(c)) return; // another lead was opened meanwhile
      const history = [...(histData.history || [])].sort((a,b) => new Date(a.timestamp || 0) - new Date(b.timestamp || 0));
      if (!history.length) {
        box.innerHTML = summaryBanner + '<div style="text-align:center;color:var(--text2);padding:20px">Chưa có tin nhắn nào</div>';
      } else {
        if (!this._sameLead(c)) return; // user already moved to another lead
        // Our side: a real person ("[Human Admin]:" prefix), the AI (has a model), or unknown.
        const HUMAN = this.HUMAN_PREFIX;
        history.forEach(m => {
          m._human = m.role !== 'user' && HUMAN.test(m.content || '');
          m._shown = m._human ? (m.content || '').replace(HUMAN, '') : (m.content || '');
        });
        const who = m => m.role === 'user' ? '👤 ' + esc(uName)
          : (m._human ? '✍️ Người thật · ' + esc(accountLabel)
            : (m.model ? '🤖 AI · ' + esc(accountLabel)
              : '📨 Bên mình (tin tự động / không rõ) · ' + esc(accountLabel)));
        this._modalTexts = history.map(msg => (msg._shown || '').trim());
        this._modalLeadTexts = history.filter(m => m.role === 'user').map(m => (m.content || '').trim()).filter(Boolean);
        this._modalHistory = history;
        const ops = i => `<div class="aifu-msg-ops" style="display:flex;gap:10px;justify-content:flex-end;margin-top:3px;font-size:11px">`
          + `<a href="javascript:void 0" onclick="AIFollowUp.editMsg(${i})" title="Sửa tin trên Telegram (lead thấy bản mới, có chữ 'edited')">✏️ Sửa</a>`
          + `<a href="javascript:void 0" onclick="AIFollowUp.msgOp(${i},'revoke')" title="Thu hồi: xoá tin ở CẢ 2 phía trên Telegram">↩️ Thu hồi</a>`
          + `<a href="javascript:void 0" onclick="AIFollowUp.msgOp(${i},'delete')" title="Xoá chỉ phía mình: lead vẫn còn thấy tin">🗑 Xoá</a></div>`;
        box.innerHTML = summaryBanner + history.map((msg, i) => {
          const isUser = msg.role === 'user';
          const timeText = msg.timestamp ? new Date(msg.timestamp).toLocaleString('vi-VN', {
            timeZone: 'Asia/Ho_Chi_Minh', day: '2-digit', month: '2-digit', year: 'numeric',
            hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false
          }) : 'Không rõ thời gian';
          return `
            <div id="aifu-msg-${i}" style="display:flex;flex-direction:column;align-items:${isUser ? 'flex-start' : 'flex-end'};margin-bottom:12px">
              <div style="font-size:11px;color:var(--text2);margin-bottom:3px">${who(msg)} • ${timeText}${msg.edited ? ' • ✏️ đã sửa' : ''}</div>
              <div class="aifu-msg-body" style="background:${isUser ? 'var(--accent-dim)' : (msg._human ? 'var(--green-dim, var(--purple-dim))' : 'var(--purple-dim)')};border:1px solid var(--border-hover);padding:10px 14px;border-radius:12px;max-width:85%;white-space:pre-wrap;font-size:13px;line-height:1.5">${this._mediaHtml(msg.media)}${esc(msg._shown)}<div class="aifu-tr" data-tr-idx="${i}" hidden style="margin-top:6px;padding-top:6px;border-top:1px dashed var(--border-hover);font-size:12px;font-style:italic;color:var(--text2)"></div></div>
              ${isUser || !msg.timestamp ? '' : ops(i)}
            </div>`;
        }).join('');
        box.scrollTop = box.scrollHeight;
        this._applyTranslations(c); // async, does not block opening the modal
        if (!this._mediaTried) this._mediaTried = new Set();
        const mk = accountId + ':' + userId;
        if (!this._mediaTried.has(mk) && history.some(m => m.role === 'user' && !m.media && /^\s*\[Attached/.test(m.content || ''))) {
          this._mediaTried.add(mk);  // old proofs: fetch the photos from Telegram once, then redraw
          fetch(`/api/ai-followup/chats/${accountId}/${userId}/fetch-media`, {method: 'POST'})
            .then(r => r.json()).then(d => { if (d.added && this._isOpen(accountId, userId)) this.openChat(accountId, userId); }).catch(() => {});
        }
      }
      this._paintPending(histData.pending_send);
    } catch (e) {
      if (!this._sameLead(c)) return;
      box.innerHTML = summaryBanner + '<div style="text-align:center;color:var(--red);padding:20px">Lỗi tải lịch sử chat</div>';
    }

    if (!this._sameLead(c)) return;
    this._renderModalActions();
    modal.classList.add('open');
  },

  _renderModalActions() {
    const c = this._modalChat;
    this._paintAIBtn();
    const el = document.getElementById('aifu-modal-actions');
    if (!el || !c) return;
    // The AI on/off switch lives in the header only (toggleModalAI).
    const toggle = (c.status === 'bot_ignored' ? '' : `<button class="btn btn-ghost btn-sm" onclick="AIFollowUp.updateStatus(${c.account_id},${c.user_id},'bot_ignored')" title="Bỏ qua lead này rồi sang lead kế tiếp">🙈 Bỏ qua</button>`)
      + (this.isOwed(c) ? `<button class="btn btn-ghost btn-sm" onclick="AIFollowUp.resolveOwed(${c.account_id},${c.user_id})" title="Đã trả lời KOL về việc đã hứa (qua kênh khác) — xoá lời hứa">✅ Đã trả lời</button>` : '');
    const blkBtn = `<button class="btn btn-danger btn-sm" onclick="AIFollowUp.blacklistFromModal()" title="Cấm AI chat 100% với người này">🚫 Blacklist</button>`
      + `<span class="info-i" tabindex="0" data-tip="Bỏ qua: chỉ ẩn cuộc chat này khỏi Hàng chờ và tắt AI cho nó; đổi lại được bất cứ lúc nào ở bộ lọc 🚫 Ignored. Blacklist: cấm AI nhắn người này vĩnh viễn trên MỌI tài khoản và mọi chiến dịch. Cả hai nút đều chuyển sang lead kế tiếp.">i</span>`;
    const vs = c.verification_status || 'none';
    const vsLabel = {none:'🔍 Chưa verify', requested:'📤 Đã yêu cầu', submitted:'📎 Đã nộp', verified:'✅ Verified', rejected:'❌ Từ chối'}[vs] || vs;
    const vsBtn = `<button class="btn btn-ghost btn-sm" onclick="AIFollowUp.cycleVerification(${c.account_id},${c.user_id})" title="Click để đổi trạng thái verify">${vsLabel}</button>`;
    const cur = this.stepOf(c);
    const rank = this.stepRank(cur);
    const next = rank >= 0 && rank < this.STEPS.length - 1 ? this.STEPS[rank + 1] : null;
    const nextBtn = next
      ? `<button class="btn btn-green btn-sm" onclick="AIFollowUp.setStep(${c.account_id},${c.user_id},'${next}')" title="Chuyển sang bước tiếp theo (ghi vào kol_step_events)">➡️ ${this.stepLabel(next)}</button>`
      : '';
    const opts = [...this.STEPS, 'declined', 'dormant', 'media'].map((s, i) =>
      `<option value="${s}" ${s === cur ? 'disabled' : ''}>${i < this.STEPS.length ? i + '. ' : ''}${this.stepLabel(s)}${s === cur ? ' (hiện tại)' : ''}</option>`).join('');
    const stepSel = `<select class="form-input" style="width:auto;flex:0 0 auto;padding:4px 8px;font-size:12px" title="Admin đặt bước bất kỳ (tiến/lùi)" onchange="AIFollowUp.stepSelectChanged(this,${c.account_id},${c.user_id})"><option value="">Đặt bước…</option>${opts}</select>`;
    el.style.cssText = 'display:flex;flex-wrap:wrap;gap:6px;justify-content:flex-end;align-items:center';
    el.innerHTML = `${toggle}${blkBtn}${vsBtn}<button class="btn btn-ghost btn-sm" onclick="AIFollowUp.editProfile(${c.account_id},${c.user_id})">📍 Hồ sơ & phân công</button><button class="btn btn-ghost btn-sm" onclick="AIFollowUp.transferLead(${c.account_id},${c.user_id})" title="KOL chặn nick này? Chuyển lead sang nick khác: chép lịch sử + hồ sơ, tắt AI, không gửi tin nào">🔀 Chuyển nick</button>${nextBtn}${stepSel}<button class="btn btn-primary btn-sm" onclick="AIFollowUp.closeHistoryModal()">Đóng</button>`;
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

  // Returns {lang, text} when the lead writes in a language other than en/vi and the text needs translating; else null.
  async _translateOutgoing(text) {
    const tr = async (texts, target) => {
      const res = await fetch('/api/members/translate-descriptions', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ texts, target_lang: target })
      });
      if (!res.ok) throw new Error('translate failed');
      return res.json();
    };
    const recent = (this._modalLeadTexts || []).filter(t => t.length >= 2).slice(-5);
    if (!recent.length) return null;
    const missing = recent.filter(t => !this._trCache.has(t));
    if (missing.length) {
      const d = await tr(missing, 'en');
      for (const t of missing) {
        const lang = d.langs?.[t] || '';
        if (lang) this._trCache.set(t, { tr: d.translations?.[t] ?? t, lang });
      }
    }
    const counts = {};
    for (const t of recent) {
      const l = (this._trCache.get(t)?.lang || '').split('-')[0];
      if (l && l !== 'und') counts[l] = (counts[l] || 0) + 1;
    }
    const lang = Object.keys(counts).sort((a, b) => counts[b] - counts[a])[0];
    if (!lang || ['en', 'vi'].includes(lang)) return null;
    // Paragraph by paragraph: keeps line breaks and stays under the backend's 1200-char cap.
    const paras = text.split(/\n/);
    const d = await tr(paras.filter(p => p.trim()), lang);
    const out = paras.map(p => p.trim() ? (d.translations?.[p.trim()] ?? p) : p).join('\n');
    return out.trim() === text.trim() ? null : { lang, text: out };
  },

  async sendMessage() {
    const c = this._modalChat;
    const input = document.getElementById('aifu-chat-input');
    const btn = document.getElementById('aifu-chat-send-btn');
    if (!c || !input || this._sending) return;
    let text = input.value.trim();
    if (!text) return;
    this._sending = true;
    try { await this._sendMessage(c, input, btn, text); } finally { this._sending = false; }
  },

  async _sendMessage(c, input, btn, text) {
    if (btn) { btn.disabled = true; btn.textContent = '⏳'; }
    // Translate into the lead's language (detected from their recent messages) and let the admin confirm.
    try {
      const tr = await this._translateOutgoing(text);
      if (tr) {
        if (!confirm(`Lead nói ${tr.lang.toUpperCase()} — gửi bản dịch này?\n\n${tr.text}\n\n(Cancel = không gửi, sửa lại tin)`)) {
          if (btn) { btn.disabled = false; btn.textContent = 'Gửi ➤'; }
          return;
        }
        text = tr.text;
      }
    } catch (e) { /* translation unavailable: fall through and send original */ }
    if (this._sameLead(c)) { input.value = ''; input.style.height = 'auto'; }
    try {
      const res = await fetch(`/api/ai-followup/chats/${c.account_id}/${c.user_id}/send`, {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({text})
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Gửi thất bại');
      if (data.queued) {
        // Nick is spam-limited: nothing was sent, the reply waits and goes out by itself.
        if (this._sameLead(c)) this._paintPending({ text, status: 'waiting', until: data.until });
        App.toast(`⏳ Nick đang bị Telegram giới hạn — tin sẽ tự gửi khi nick mở khoá${data.replaced ? ' (đã thay tin chờ cũ)' : ''}`, 'info');
        return;
      }
      if (this._sameLead(c)) document.getElementById('aifu-pending')?.remove(); // sent now: an older waiting reply is gone
      // Append bubble vào chat box
      const box = this._sameLead(c) ? document.getElementById('aifu-modal-chat-box') : null;
      if (box) {
        const accLabel = c.account_name || `Account #${c.account_id}`;
        const timeText = new Date().toLocaleString('vi-VN', { timeZone:'Asia/Ho_Chi_Minh', day:'2-digit', month:'2-digit', year:'numeric', hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false });
        box.insertAdjacentHTML('beforeend', `
          <div style="display:flex;flex-direction:column;align-items:flex-end;margin-bottom:12px">
            <div style="font-size:11px;color:var(--text2);margin-bottom:3px">✍️ Người thật · ${esc(accLabel)} • ${timeText}</div>
            <div style="background:var(--green-dim, var(--purple-dim));border:1px solid var(--border-hover);padding:10px 14px;border-radius:12px;max-width:85%;white-space:pre-wrap;font-size:13px;line-height:1.5">${esc(text)}</div>
          </div>`);
        box.scrollTop = box.scrollHeight;
      }
      // Server: a hand-sent message turns the AI off for this lead and closes the
      // promise. Mirror that here so the lead leaves the queue without a reload.
      this._patchChat(c.account_id, c.user_id, {
        status: 'needs_human',
        owed_since: null, owed_note: null, last_role: 'assistant', last_text: ('[Human Admin]: ' + text).slice(0, 140)
      });
      App.toast('✅ Đã gửi — AI tạm dừng để bạn chat tay', 'success');
    } catch (e) {
      if (this._sameLead(c)) input.value = text; // giữ lại text nếu lỗi (và vẫn đang mở lead đó)
      App.toast('❌ ' + e.message, 'error');
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Gửi ➤'; }
    }
  },

  // ── Reply waiting for a spam-limited nick ─────────────────────────
  // p = {text, status: 'waiting' | 'dropped', reason, until (unix s)} or null.
  _paintPending(p) {
    document.getElementById('aifu-pending')?.remove();
    const box = document.getElementById('aifu-modal-chat-box');
    if (!box || !p || !p.text) return;
    const waiting = p.status !== 'dropped';
    const when = p.until ? new Date(p.until * 1000).toLocaleString('vi-VN', {
      timeZone: 'Asia/Ho_Chi_Minh', hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit', hour12: false }) : '';
    const head = waiting
      ? `⏳ Chờ gửi — nick đang bị Telegram giới hạn${when ? `, dự kiến mở khoá ${when}` : ' (chưa rõ ngày mở khoá)'}`
      : `⚠️ Tin này KHÔNG được gửi: ${esc(p.reason || 'không rõ lý do')}`;
    const tip = 'Telegram đang giới hạn nick này nên tin chưa gửi được. App hỏi @SpamBot và chỉ gửi khi nick đã mở khoá, không thử gửi mò. '
      + 'Tin sẽ KHÔNG được gửi nếu trong lúc chờ cuộc trò chuyện có tin mới, lead bị bỏ qua / chuyển nick / blacklist, hoặc đã chờ quá 7 ngày.';
    box.insertAdjacentHTML('beforeend', `
      <div id="aifu-pending" style="display:flex;flex-direction:column;align-items:flex-end;margin-bottom:12px">
        <div style="font-size:11px;color:${waiting ? 'var(--orange)' : 'var(--red)'};margin-bottom:3px">${head}
          <span class="info-i" tabindex="0" data-tip="${esc(tip)}">i</span></div>
        <div style="border:1px dashed ${waiting ? 'var(--orange)' : 'var(--red)'};padding:10px 14px;border-radius:12px;max-width:85%;white-space:pre-wrap;font-size:13px;line-height:1.5;opacity:.85">${esc(p.text)}</div>
        <div style="display:flex;gap:10px;margin-top:3px;font-size:11px">
          <a href="javascript:void 0" onclick="AIFollowUp.cancelPending(true)" title="Bỏ tin chờ và đưa nội dung về ô nhập để sửa">${waiting ? '✏️ Huỷ & sửa lại' : '✏️ Dùng lại nội dung'}</a>
          <a href="javascript:void 0" onclick="AIFollowUp.cancelPending(false)" title="Bỏ hẳn tin này">🗑 ${waiting ? 'Huỷ' : 'Xoá'}</a>
        </div>
      </div>`);
    box.scrollTop = box.scrollHeight;
  },

  async cancelPending(reuse) {
    const c = this._modalChat;
    if (!c) return;
    try {
      const res = await fetch(`/api/ai-followup/chats/${c.account_id}/${c.user_id}/pending-send`, { method: 'DELETE' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok && res.status !== 404) throw new Error(data.detail || 'Không huỷ được');
      if (!this._sameLead(c)) return;
      document.getElementById('aifu-pending')?.remove();
      const input = document.getElementById('aifu-chat-input');
      if (reuse && input && data.text) {  // never lose the text: keep what is already typed below it
        const typed = input.value.trim();
        input.value = typed ? data.text + '\n' + typed : data.text;
        input.style.height = 'auto'; input.style.height = input.scrollHeight + 'px';
        input.focus();
      }
      App.toast(res.status === 404 ? 'Tin chờ này đã được gửi hoặc đã huỷ' : 'Đã huỷ tin chờ', 'info');
    } catch (e) { App.toast('❌ ' + e.message, 'error'); }
  },

  // ── Edit / revoke / delete one of our messages ───────────────────
  editMsg(i) {
    const m = this._modalHistory?.[i];
    const row = document.getElementById(`aifu-msg-${i}`);
    if (!m || !row || row.querySelector('textarea')) return;
    const body = row.querySelector('.aifu-msg-body');
    const ops = row.querySelector('.aifu-msg-ops');
    body.hidden = true; if (ops) ops.hidden = true;
    row.insertAdjacentHTML('beforeend', `<div class="aifu-msg-edit" style="width:85%;display:flex;flex-direction:column;gap:6px">
      <textarea class="form-input" rows="5" style="font-size:13px;line-height:1.5">${esc(m._shown)}</textarea>
      <div style="display:flex;gap:6px;justify-content:flex-end">
        <button class="btn btn-ghost btn-sm" onclick="AIFollowUp.openChat(${this._modalChat.account_id},${this._modalChat.user_id})">Huỷ</button>
        <button class="btn btn-primary btn-sm">💾 Lưu & sửa trên Telegram</button></div></div>`);
    const ta = row.querySelector('textarea');
    ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length);
    row.querySelector('.aifu-msg-edit .btn-primary').onclick = () => this.msgOp(i, 'edit', ta.value);
  },

  async msgOp(i, action, newText = '') {
    const c = this._modalChat, m = this._modalHistory?.[i];
    if (!c || !m || this._msgOpBusy) return;
    if (action === 'edit' && !newText.trim()) return App.toast('Nội dung trống', 'error');
    if (action === 'edit' && newText.trim() === (m._shown || '').trim()) return this.openChat(c.account_id, c.user_id);
    const ask = { revoke: 'Thu hồi tin này ở CẢ 2 phía? Lead sẽ không còn thấy nó.',
                  delete: 'Xoá tin này chỉ phía mình? Lead VẪN còn thấy nó.' }[action];
    if (ask && !confirm(ask)) return;
    this._msgOpBusy = true;
    try {
      const res = await fetch(`/api/ai-followup/chats/${c.account_id}/${c.user_id}/message-op`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ timestamp: m.timestamp, content: m.content, action, new_text: newText })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Thất bại');
      App.toast({ edit: '✏️ Đã sửa', revoke: '↩️ Đã thu hồi', delete: '🗑 Đã xoá phía mình' }[action], 'success');
      if (this._sameLead(c)) this.openChat(c.account_id, c.user_id);
    } catch (e) { App.toast('❌ ' + e.message, 'error'); }
    finally { this._msgOpBusy = false; }
  },

  closeHistoryModal() {
    document.getElementById('aifu-history-modal')?.classList.remove('open');
    this._modalChat = null;
    this._modalStep = null;
    this._navKeys = null; // next open walks the table's current order
  }
};

if (typeof window !== 'undefined') {
  window.AIFollowUp = AIFollowUp;
}
