/**
 * Zalo Outreach Module
 * ────────────────────
 * Several nicks (QR login each), scrape group members / event attendees,
 * friend-request campaigns shared across nicks.
 */
const Zalo = (() => {
  let _loginPoll = null;
  let _sources = [];
  let _groups = [];
  let _editingId = null;
  let _accounts = [];
  let _campaigns = [];
  let _watches = [];

  const STATUS_LABEL = {
    new: ['Chưa liên hệ', ''],
    requested: ['Đã gửi lời mời', 'badge-purple'],
    friend: ['Đã là bạn', 'badge-blue'],
    greeted: ['Đã chào', 'badge-green'],
    failed: ['Lỗi', 'badge-red'],
    skipped: ['Bỏ qua', ''],
    blocked: ['Đã chặn', 'badge-red'],
  };

  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }
  const toast = (m, t = 'info') => (window.App && App.toast ? App.toast(m, t) : console.log(m));
  const el = (id) => document.getElementById(id);

  async function init() {
    await loadAccounts();
    await Promise.all([loadSources(), loadCampaigns(), loadContacts()]);
    loadWatches();
  }

  // ── Nicks ──
  const LOGIN_BUSY = ['waiting_qr', 'qr_ready', 'scanned'];
  const nickName = (id) => {
    const a = _accounts.find((x) => x.id === id);
    return a ? (a.me && a.me.name) || a.name || a.uid || id : id;
  };

  function qrBlock(l) {
    if (l.state === 'qr_ready' && l.qr) {
      const src = l.qr.startsWith('data:') ? l.qr : `data:image/png;base64,${l.qr}`;
      return `<div style="display:flex;gap:16px;align-items:center">
        <img src="${src}" alt="QR đăng nhập Zalo" style="width:170px;height:170px;background:#fff;border-radius:8px;padding:6px">
        <div style="color:var(--text2);font-size:.9rem">Mở Zalo trên điện thoại của nick cần thêm → biểu tượng QR → quét mã này.<br>Mã hết hạn sau khoảng 1 phút.</div></div>`;
    }
    if (l.state === 'scanned') return `✅ ${esc(l.scanned || '')} đã quét — xác nhận đăng nhập trên điện thoại...`;
    if (l.state === 'waiting_qr') return 'Đang tạo mã QR...';
    return `<span style="color:var(--text2)">${l.error ? '⚠️ ' + esc(l.error) : 'Chưa đăng nhập'}</span>`;
  }

  function accountRow(a) {
    const l = a.login || {};
    const me = a.me || {};
    const name = me.name || a.name || '(nick mới)';
    const avatar = me.avatar || a.avatar;
    const head = `${avatar ? `<img src="${esc(avatar)}" alt="" style="width:36px;height:36px;border-radius:50%">` : '<div style="width:36px;height:36px;border-radius:50%;background:var(--border)"></div>'}
      <div style="min-width:160px"><b>${esc(name)}</b><div style="color:var(--text2);font-size:.8rem">${a.uid || me.uid ? 'UID ' + esc(me.uid || a.uid) : ''}</div></div>`;
    const del = `<button class="btn btn-danger btn-sm" onclick="Zalo.removeAccount('${esc(a.id)}')">Xoá</button>`;

    if (!a.logged_in) {
      const rescan = LOGIN_BUSY.includes(l.state) ? ''
        : `<button class="btn btn-primary btn-sm" onclick="Zalo.relogin('${esc(a.id)}')">Quét QR</button>`;
      return `<div class="zalo-nick" style="display:flex;align-items:center;gap:12px;padding:8px 0;border-bottom:1px solid var(--border)">
        ${a.pending ? '' : head}<div style="flex:1">${qrBlock(l)}</div>
        <div style="white-space:nowrap">${rescan} ${del}</div></div>`;
    }

    let state;
    if (!a.enabled) state = '<span class="badge">Đã tắt</span>';
    else if (a.resting) state = `<span class="badge badge-red" title="${esc(a.last_error || '')}">Nghỉ đến ${esc(a.paused_until)}</span>`;
    else if (a.sent_today >= a.cap_today) state = '<span class="badge badge-blue">Đủ hạn mức hôm nay</span>';
    else state = '<span class="badge badge-green">Sẵn sàng</span>';
    const rate = a.requested_total ? `${a.accepted_total}/${a.requested_total} đồng ý (${Math.round(100 * a.accepted_total / a.requested_total)}%)` : 'chưa mời ai';
    return `<div class="zalo-nick" style="display:flex;align-items:center;gap:12px;padding:8px 0;border-bottom:1px solid var(--border);flex-wrap:wrap">
      ${head}
      <div style="flex:1;min-width:200px;font-size:.85rem">
        ${state}
        <div>Hôm nay <b>${a.sent_today}/${a.cap_today}</b> lời mời
          ${a.cap_note ? `<span style="color:var(--text2)">· ${esc(a.cap_note)}</span>` : ''}
          <span style="color:var(--text2)">· giới hạn nick ${a.daily_cap}/ngày</span></div>
        <div style="color:var(--text2)">${rate}</div>
        ${a.last_error && !a.resting ? `<div style="color:var(--red);font-size:.8rem">⚠️ ${esc(a.last_error)}</div>` : ''}
      </div>
      <div style="white-space:nowrap">
        ${a.resting ? `<button class="btn btn-secondary btn-sm" onclick="Zalo.resumeAccount('${esc(a.id)}')">Cho chạy lại</button>` : ''}
        <button class="btn btn-ghost btn-sm" onclick="Zalo.editCap('${esc(a.id)}')">Giới hạn</button>
        <button class="btn btn-ghost btn-sm" onclick="Zalo.toggleAccount('${esc(a.id)}')">${a.enabled ? 'Tắt' : 'Bật'}</button>
        ${del}</div></div>`;
  }

  async function loadAccounts() {
    const box = el('zalo-account');
    if (!box) return;
    try {
      _accounts = await apiGet('/api/zalo/accounts');
    } catch (e) {
      box.innerHTML = `<div class="empty-state">⚠️ ${esc(e.message)}</div>`;
      return;
    }
    box.innerHTML = _accounts.length ? _accounts.map(accountRow).join('')
      : '<div class="empty-state">Chưa có nick nào — bấm "+ Thêm nick Zalo" rồi quét QR.</div>';

    const sel = el('zalo-group-account');
    if (sel) {
      const cur = sel.value;
      const live = _accounts.filter((a) => a.logged_in);
      sel.innerHTML = live.map((a) => `<option value="${esc(a.id)}">${esc(nickName(a.id))}</option>`).join('')
        || '<option value="">(chưa có nick)</option>';
      if (live.some((a) => a.id === cur)) sel.value = cur;
    }
    if (_accounts.some((a) => !a.logged_in && LOGIN_BUSY.includes((a.login || {}).state))) startPoll();
    else stopPoll();
  }

  function startPoll() {
    if (_loginPoll) return;
    _loginPoll = setInterval(() => {
      if (!el('zalo-account') || el('view-zalo')?.classList.contains('hidden')) return stopPoll();
      loadAccounts();
    }, 2000);
  }

  function stopPoll() {
    if (_loginPoll) clearInterval(_loginPoll);
    _loginPoll = null;
  }

  async function addAccount() {
    try {
      await apiPost('/api/zalo/accounts', {});
    } catch (e) {
      return toast(e.message, 'error');
    }
    loadAccounts();
  }

  async function relogin(id) {
    try {
      await apiPost(`/api/zalo/accounts/${id}/login`, {});
    } catch (e) {
      return toast(e.message, 'error');
    }
    loadAccounts();
  }

  async function removeAccount(id) {
    if (!confirm(`Xoá nick "${nickName(id)}"? Phiên đăng nhập bị xoá, người nick này đã mời sẽ không được gửi tin chào nữa.`)) return;
    try {
      await apiDelete(`/api/zalo/accounts/${id}`);
    } catch (e) {
      return toast(e.message, 'error');
    }
    loadAccounts();
  }

  async function saveAccount(id, patch) {
    const a = _accounts.find((x) => x.id === id);
    if (!a) return;
    try {
      await apiPut(`/api/zalo/accounts/${id}`, { enabled: !!a.enabled, daily_cap: a.daily_cap, ...patch });
    } catch (e) {
      return toast(e.message, 'error');
    }
    loadAccounts();
  }

  function toggleAccount(id) {
    const a = _accounts.find((x) => x.id === id);
    if (a) saveAccount(id, { enabled: !a.enabled });
  }

  function editCap(id) {
    const a = _accounts.find((x) => x.id === id);
    if (!a) return;
    const v = prompt(`Số lời mời tối đa mỗi ngày cho "${nickName(id)}" sau khi làm ấm (1–60).\nNick mới: 15–20 · nick lâu năm: 25–40.`, a.daily_cap);
    if (v == null) return;
    const n = parseInt(v, 10);
    if (!(n >= 1 && n <= 60)) return toast('Nhập số từ 1 đến 60', 'error');
    saveAccount(id, { daily_cap: n });
  }

  async function resumeAccount(id) {
    await apiPost(`/api/zalo/accounts/${id}/resume`, {});
    loadAccounts();
  }

  // ── Groups & events ──
  async function loadGroups() {
    const box = el('zalo-groups');
    box.innerHTML = '<div class="empty-state">Đang tải nhóm...</div>';
    try {
      _groups = await apiGet(`/api/zalo/groups?account=${encodeURIComponent(groupAccount())}`);
    } catch (e) {
      box.innerHTML = `<div class="empty-state">⚠️ ${esc(e.message)}</div>`;
      return;
    }
    if (!_groups.length) {
      box.innerHTML = '<div class="empty-state">Tài khoản chưa tham gia nhóm nào.</div>';
      return;
    }
    box.innerHTML = `<input class="form-input" placeholder="Lọc nhóm..." style="margin-bottom:8px" oninput="Zalo.filterGroups(this.value)">
      <div style="max-height:420px;overflow:auto"><table class="data-table" style="width:100%"><thead><tr>
      <th>Nhóm</th><th style="width:90px">Thành viên</th><th style="width:430px"></th></tr></thead>
      <tbody id="zalo-group-rows">${_groups.map(groupRow).join('')}</tbody></table></div>`;
  }

  function groupRow(g) {
    return `<tr data-name="${esc((g.name || '').toLowerCase())}"><td>${esc(g.name)}</td><td>${g.total}</td>
      <td style="text-align:right;white-space:nowrap">
        <button class="btn btn-secondary btn-sm" onclick="Zalo.scrapeGroup('${esc(g.id)}', this)">Quét thành viên</button>
        <button class="btn btn-secondary btn-sm" title="Chỉ lấy những người đã nhắn tin trong 1000 tin gần nhất của nhóm" onclick="Zalo.scrapeActive('${esc(g.id)}', this)">🔥 Quét active</button>
        <button class="btn btn-ghost btn-sm" onclick="Zalo.openWatch('${esc(g.id)}')">${_watches.some((w) => w.group_id === g.id) ? '👁 Đang theo dõi' : '👁 Theo dõi người mới'}</button>
        <button class="btn btn-ghost btn-sm" onclick="Zalo.toggleEvents('${esc(g.id)}', this)">Sự kiện ▾</button></td></tr>
      <tr class="hidden" id="zalo-ev-${esc(g.id)}"><td colspan="3"></td></tr>`;
  }

  function filterGroups(q) {
    q = q.toLowerCase();
    document.querySelectorAll('#zalo-group-rows tr[data-name]').forEach((tr) => {
      const show = tr.dataset.name.includes(q);
      tr.classList.toggle('hidden', !show);
      if (!show) tr.nextElementSibling.classList.add('hidden');
    });
  }

  const groupAccount = () => el('zalo-group-account')?.value || '';
  const groupName = (id) => (_groups.find((g) => g.id === id) || {}).name || '';

  async function scrapeGroup(id, btn) {
    btn.disabled = true;
    btn.textContent = 'Đang quét...';
    try {
      const r = await apiPost(`/api/zalo/groups/${id}/scrape`, { group_name: groupName(id), account: groupAccount() });
      let msg = `Đã lấy ${r.total} thành viên (${r.new} mới, đã loại ${r.admins_skipped || 0} ban quản trị)`;
      if (r.partial) msg += ` — Zalo chỉ trả ${r.total}/${r.group_total}, nhóm cộng đồng lớn có thể bị giới hạn`;
      toast(msg, r.partial ? 'warning' : 'success');
      loadSources();
      loadContacts();
    } catch (e) {
      toast(e.message, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Quét thành viên';
    }
  }

  async function scrapeActive(id, btn) {
    btn.disabled = true;
    btn.textContent = 'Đang đọc 1000 tin...';
    try {
      const r = await apiPost(`/api/zalo/groups/${id}/scrape-active`, { group_name: groupName(id), account: groupAccount(), count: 1000 });
      const since = r.oldest_ts ? ` (từ ${new Date(+r.oldest_ts).toLocaleString('vi-VN')})` : '';
      toast(`Đọc ${r.messages_scanned} tin${since}: ${r.total} người đang active (${r.new} mới, đã loại ${r.admins_skipped || 0} ban quản trị)`,
        r.messages_scanned < 1000 ? 'warning' : 'success');
      loadSources();
      loadContacts();
    } catch (e) {
      toast(e.message, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = '🔥 Quét active';
    }
  }

  // ── New-member watch ──
  async function openWatch(id) {
    const row = el(`zalo-ev-${id}`);
    row.classList.remove('hidden');
    if (!_campaigns.length) {
      try { _campaigns = await apiGet('/api/zalo/campaigns'); } catch { /* shown below */ }
    }
    const w = _watches.find((x) => x.group_id === id);
    const acc = w ? (w.account_id || '') : groupAccount();
    const nicks = _accounts.filter((a) => !a.pending && (a.uid || a.logged_in));
    row.firstElementChild.innerHTML = !_campaigns.length
      ? '<div class="empty-state">Tạo một chiến dịch trước (lời mời + tin chào), rồi quay lại bật theo dõi.</div>'
      : `<div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:4px 0">
        <span>👁 Người mới vào nhóm sẽ được mời kết bạn bằng
          <span class="info-i" tabindex="0" data-tip="Nick được chọn phải đang ở trong nhóm và đang online để 'nghe' sự kiện có người vào nhóm. Người mới được xếp lên đầu hàng chờ của chiến dịch và gửi theo nhịp / giới hạn của chiến dịch đó. Lưu ý: nếu nick đang mở Zalo Web/PC ở nơi khác, việc theo dõi có thể tạm ngắt 10 phút.">i</span></span>
        <select class="form-input" id="zw-camp-${esc(id)}" style="width:200px">${_campaigns.map((c) =>
          `<option value="${c.id}" ${w && w.campaign_id === c.id ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select>
        <span>nghe bằng nick</span>
        <select class="form-input" id="zw-acc-${esc(id)}" style="width:170px">${nicks.map((a) =>
          `<option value="${esc(a.id)}" ${a.id === acc ? 'selected' : ''}>${esc(nickName(a.id))}</option>`).join('')}</select>
        <button class="btn btn-primary btn-sm" onclick="Zalo.saveWatch('${esc(id)}')">${w ? 'Lưu' : 'Bật theo dõi'}</button>
        ${w ? `<button class="btn btn-ghost btn-sm" onclick="Zalo.deleteWatch('${esc(id)}')">Bỏ theo dõi</button>` : ''}
        <button class="btn btn-ghost btn-sm" onclick="this.closest('tr').classList.add('hidden')">Đóng</button></div>`;
  }

  async function saveWatch(id) {
    try {
      await apiPut(`/api/zalo/watches/${id}`, {
        group_name: groupName(id) || (_watches.find((w) => w.group_id === id) || {}).group_name || '',
        account: el(`zw-acc-${id}`)?.value || '',
        campaign_id: +el(`zw-camp-${id}`).value,
        enabled: true,
      });
      toast('Đã bật theo dõi người mới vào nhóm', 'success');
      el(`zalo-ev-${id}`)?.classList.add('hidden');
      await loadWatches();
      loadCampaigns();
    } catch (e) {
      toast(e.message, 'error');
    }
  }

  async function deleteWatch(id) {
    if (!confirm('Bỏ theo dõi người mới của nhóm này?')) return;
    await apiDelete(`/api/zalo/watches/${id}`);
    el(`zalo-ev-${id}`)?.classList.add('hidden');
    loadWatches();
  }

  async function toggleWatch(id, enabled) {
    const w = _watches.find((x) => x.group_id === id);
    if (!w) return;
    await apiPut(`/api/zalo/watches/${id}`, { group_name: w.group_name, account: w.account_id || '', campaign_id: w.campaign_id, enabled });
    loadWatches();
  }

  async function loadWatches() {
    const box = el('zalo-watches');
    try {
      _watches = await apiGet('/api/zalo/watches');
    } catch (e) {
      if (box) box.innerHTML = `<div class="empty-state">⚠️ ${esc(e.message)}</div>`;
      return;
    }
    _groups.forEach((g) => {  // refresh the watch buttons in the group table
      const btn = document.querySelector(`#zalo-group-rows button[onclick="Zalo.openWatch('${g.id}')"]`);
      if (btn) btn.textContent = _watches.some((w) => w.group_id === g.id) ? '👁 Đang theo dõi' : '👁 Theo dõi người mới';
    });
    if (!box) return;
    if (!_watches.length) {
      box.innerHTML = '<div class="empty-state">Chưa theo dõi nhóm nào. Bấm "👁 Theo dõi người mới" ở một nhóm phía trên.</div>';
      return;
    }
    box.innerHTML = `<table class="data-table" style="width:100%"><thead><tr><th>Nhóm</th><th>Chiến dịch</th><th>Nick nghe</th>
      <th>Trạng thái</th><th>Người mới <span class="info-i" tabindex="0" data-tip="Tổng số người mới vào nhóm đã được đưa vào hàng chờ mời kết bạn kể từ khi bật theo dõi">i</span></th><th></th></tr></thead><tbody>
      ${_watches.map((w) => {
        const st = !w.enabled ? '<span class="badge">Tắt</span>'
          : w.listening && !w.listen_error ? '<span class="badge badge-green">Đang nghe</span>'
          : `<span class="badge badge-red" title="${esc(w.listen_error || '')}">Chưa nghe được</span>`;
        const camp = w.campaign_name
          ? `${esc(w.campaign_name)}${w.campaign_status === 'paused' ? ' <span style="color:var(--text2);font-size:.8rem">(đang tạm dừng)</span>' : ''}`
          : '<span style="color:var(--red)">Chiến dịch đã xoá</span>';
        return `<tr><td><b>${esc(w.group_name || w.group_id)}</b>${w.listen_error && w.enabled ? `<div style="font-size:.8rem;color:var(--text2)">${esc(w.listen_error)}</div>` : ''}</td>
          <td>${camp}</td><td>${esc(w.account_id ? nickName(w.account_id) : 'Nick mặc định')}</td><td>${st}</td>
          <td>${w.joined_count || 0}${w.last_join_at ? `<div style="font-size:.8rem;color:var(--text2)">gần nhất ${esc(w.last_join_at)}</div>` : ''}</td>
          <td style="text-align:right;white-space:nowrap">
            <label class="toggle" title="Bật/tắt theo dõi"><input type="checkbox" ${w.enabled ? 'checked' : ''} onchange="Zalo.toggleWatch('${esc(w.group_id)}', this.checked)"><span class="toggle-slider"></span></label>
            <button class="btn btn-ghost btn-sm" onclick="Zalo.deleteWatch('${esc(w.group_id)}')">Xoá</button></td></tr>`;
      }).join('')}</tbody></table>`;
  }

  async function toggleEvents(id, btn) {
    const row = el(`zalo-ev-${id}`);
    if (!row.classList.contains('hidden')) return row.classList.add('hidden');
    row.classList.remove('hidden');
    const cell = row.firstElementChild;
    cell.innerHTML = '<div class="empty-state">Đang tải sự kiện...</div>';
    try {
      const evs = await apiGet(`/api/zalo/groups/${id}/events?account=${encodeURIComponent(groupAccount())}`);
      if (!evs.length) {
        cell.innerHTML = '<div class="empty-state">Nhóm không có sự kiện / nhắc hẹn.</div>';
        return;
      }
      cell.innerHTML = evs.map((ev) => `<div style="display:flex;align-items:center;gap:8px;padding:4px 0">
        <span style="flex:1">📅 ${esc(ev.title || '(không tiêu đề)')}
          <span style="color:var(--text2);font-size:.8rem">${ev.start_time ? new Date(ev.start_time).toLocaleString('vi-VN') : ''}
          ${ev.accept_count != null ? ` · ${ev.accept_count} tham gia` : ''}</span></span>
        <button class="btn btn-secondary btn-sm" data-title="${esc(ev.title)}" data-creator="${esc(ev.creator_id || '')}"
          onclick="Zalo.scrapeEvent('${esc(ev.id)}','${esc(id)}',this)">Lấy người tham gia</button></div>`).join('');
    } catch (e) {
      cell.innerHTML = `<div class="empty-state">⚠️ ${esc(e.message)}</div>`;
    }
  }

  async function scrapeEvent(eventId, groupId, btn) {
    btn.disabled = true;
    btn.textContent = 'Đang lấy...';
    try {
      const r = await apiPost(`/api/zalo/events/${eventId}/scrape`, {
        title: btn.dataset.title || '', group_name: groupName(groupId),
        group_id: groupId, creator_id: btn.dataset.creator || '', account: groupAccount(),
      });
      toast(`Đã lấy ${r.total} người tham gia (${r.new} mới, đã loại ${r.admins_skipped || 0} ban quản trị)`, 'success');
      loadSources();
      loadContacts();
    } catch (e) {
      toast(e.message, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Lấy người tham gia';
    }
  }

  // ── Sources ──
  async function loadSources() {
    const box = el('zalo-sources');
    try {
      _sources = await apiGet('/api/zalo/sources');
    } catch (e) {
      box.innerHTML = `<div class="empty-state">⚠️ ${esc(e.message)}</div>`;
      return;
    }
    const sel = el('zalo-contact-source');
    box.innerHTML = _sources.length
      ? `<table class="data-table" style="width:100%"><thead><tr><th>Nguồn</th><th>Loại</th><th>Tổng</th><th>Chưa liên hệ</th><th>Quét lúc</th><th></th></tr></thead><tbody>
        ${_sources.map((s) => `<tr><td>${esc(s.source_name)}</td><td>${s.source_type === 'event' ? 'Sự kiện' : 'Nhóm'}</td>
          <td>${s.total}</td><td>${s.pending}</td><td>${esc(s.last_scraped || '')}</td>
          <td><button class="btn btn-ghost btn-sm" onclick="Zalo.filterSource('${esc(s.source_type)}:${esc(s.source_id)}')">Xem</button></td></tr>`).join('')}
        </tbody></table>`
      : '<div class="empty-state">Chưa quét nguồn nào.</div>';
    if (sel) sel.value = '';
  }

  // ── Contacts ──
  let _sourceFilter = '';
  function filterSource(src) {
    _sourceFilter = src;
    loadContacts();
    el('zalo-contacts')?.scrollIntoView({ behavior: 'smooth' });
  }

  async function loadContacts() {
    const box = el('zalo-contacts');
    if (!box) return;
    const params = new URLSearchParams({
      status: el('zalo-contact-status')?.value || '',
      q: el('zalo-contact-q')?.value || '',
      source: _sourceFilter,
      limit: 200,
    });
    let data;
    try {
      data = await apiGet(`/api/zalo/contacts?${params}`);
    } catch (e) {
      box.innerHTML = `<div class="empty-state">⚠️ ${esc(e.message)}</div>`;
      return;
    }
    const src = _sources.find((s) => `${s.source_type}:${s.source_id}` === _sourceFilter);
    const filterNote = _sourceFilter
      ? `<div style="margin-bottom:6px;font-size:.85rem">Lọc theo: <b>${esc(src ? src.source_name : _sourceFilter)}</b>
         <a href="#" onclick="Zalo.filterSource('');return false">✕ bỏ lọc</a></div>` : '';
    if (!data.items.length) {
      box.innerHTML = filterNote + '<div class="empty-state">Không có liên hệ.</div>';
      return;
    }
    box.innerHTML = `${filterNote}<div style="color:var(--text2);font-size:.85rem;margin-bottom:6px">${data.total} liên hệ${data.total > data.items.length ? ` (hiện ${data.items.length})` : ''}</div>
      <div style="max-height:480px;overflow:auto"><table class="data-table" style="width:100%"><thead><tr><th>Tên</th><th>Nguồn</th><th>Trạng thái</th><th>Nick gửi</th><th>Cập nhật</th><th></th></tr></thead><tbody>
      ${data.items.map((c) => {
        const [label, cls] = STATUS_LABEL[c.status] || [c.status, ''];
        return `<tr><td>${c.avatar ? `<img src="${esc(c.avatar)}" alt="" style="width:22px;height:22px;border-radius:50%;vertical-align:middle;margin-right:6px">` : ''}${esc(c.name || c.uid)}</td>
          <td style="font-size:.8rem;color:var(--text2)">${esc(c.source_names || '')}</td>
          <td><span class="badge ${cls}" ${c.last_error ? `title="${esc(c.last_error)}"` : ''}>${label}</span></td>
          <td style="font-size:.8rem">${c.account_id ? esc(nickName(c.account_id)) : ''}</td>
          <td style="font-size:.8rem">${esc(c.updated_at || '')}</td>
          <td>${c.status === 'blocked'
            ? `<button class="btn btn-ghost btn-sm" onclick="Zalo.blockContact('${esc(c.uid)}', false)">Bỏ chặn</button>`
            : c.status === 'new' ? `<button class="btn btn-ghost btn-sm" title="Không nick nào được liên hệ người này" onclick="Zalo.blockContact('${esc(c.uid)}', true)">Chặn</button>` : ''}</td></tr>`;
      }).join('')}</tbody></table></div>`;
  }

  async function blockContact(uid, blocked) {
    try {
      await apiPost(`/api/zalo/contacts/${uid}/block`, { blocked });
    } catch (e) {
      return toast(e.message, 'error');
    }
    loadContacts();
  }

  // ── Campaigns ──
  async function loadCampaigns() {
    const box = el('zalo-campaigns');
    let list;
    try {
      list = await apiGet('/api/zalo/campaigns');
      _campaigns = list;
    } catch (e) {
      box.innerHTML = `<div class="empty-state">⚠️ ${esc(e.message)}</div>`;
      return;
    }
    if (!list.length) {
      box.innerHTML = '<div class="empty-state">Chưa có chiến dịch.</div>';
      return;
    }
    const stBadge = { running: ['Đang chạy', 'badge-green'], paused: ['Tạm dừng', 'badge-purple'], done: ['Hết danh sách', 'badge-blue'] };
    box.innerHTML = `<table class="data-table" style="width:100%"><thead><tr><th>Chiến dịch</th><th>Trạng thái</th>
      <th>Hôm nay <span class="info-i" tabindex="0" data-tip="Số lời mời kết bạn đã gửi hôm nay (cộng tất cả nick) / giới hạn tổng của chiến dịch">i</span></th>
      <th>Đã mời</th><th>Đồng ý</th><th>Đã chào</th><th></th></tr></thead><tbody>
      ${list.map((c) => {
        const [label, cls] = stBadge[c.status] || [c.status, ''];
        const srcNames = c.sources.length
          ? c.sources.map((k) => (_sources.find((s) => `${s.source_type}:${s.source_id}` === k) || {}).source_name || k).join(', ')
          : 'Tất cả liên hệ';
        const nicks = c.accounts && c.accounts.length ? c.accounts.map(nickName).join(', ') : 'Tất cả nick';
        return `<tr><td><b>${esc(c.name)}</b><div style="font-size:.8rem;color:var(--text2)">${esc(srcNames)} · 👤 ${esc(nicks)}</div>
            ${c.last_error ? `<div style="font-size:.8rem;color:var(--red)">⚠️ ${esc(c.last_error)}</div>` : ''}</td>
          <td><span class="badge ${cls}">${label}</span></td>
          <td>${c.sent_today}/${c.daily_limit}</td><td>${c.requested}</td><td>${c.accepted}</td><td>${c.greeted}</td>
          <td style="white-space:nowrap;text-align:right">
            ${c.status === 'running'
              ? `<button class="btn btn-secondary btn-sm" onclick="Zalo.pauseCampaign(${c.id})">Dừng</button>`
              : `<button class="btn btn-primary btn-sm" onclick="Zalo.startCampaign(${c.id})">Chạy</button>`}
            <button class="btn btn-ghost btn-sm" onclick="Zalo.openCampaign(${c.id})">Sửa</button>
            <button class="btn btn-ghost btn-sm" onclick="Zalo.showLogs(${c.id}, this)">Log</button>
            <button class="btn btn-danger btn-sm" onclick="Zalo.deleteCampaign(${c.id})">Xoá</button></td></tr>
          <tr class="hidden" id="zalo-logs-${c.id}"><td colspan="7"></td></tr>`;
      }).join('')}</tbody></table>`;
  }

  async function openCampaign(id) {
    const form = el('zalo-campaign-form');
    _editingId = id || null;
    let c = { name: '', sources: [], accounts: [], friend_msg: 'Chào {name}, mình cũng trong nhóm {AFFILIATE CRYPTO|cộng đồng} — kết bạn giao lưu nhé!',
      greet_msg: 'Cảm ơn {name} đã đồng ý kết bạn 🙌', daily_limit: 100, min_delay_sec: 300, max_delay_sec: 900, start_hour: 8, end_hour: 22 };
    if (id) {
      const list = await apiGet('/api/zalo/campaigns');
      c = list.find((x) => x.id === id) || c;
    }
    if (!_sources.length) await loadSources();
    if (!_accounts.length) await loadAccounts();
    const nickOpts = _accounts.filter((a) => !a.pending && (a.uid || a.logged_in)).map((a) => `<label style="display:block;font-size:.9rem">
      <input type="checkbox" class="zc-acc" value="${esc(a.id)}" ${(c.accounts || []).includes(a.id) ? 'checked' : ''}> ${esc(nickName(a.id))}
      <span style="color:var(--text2)">(${a.logged_in ? `${a.cap_today}/ngày hôm nay` : 'chưa đăng nhập'})</span></label>`).join('');
    form.classList.remove('hidden');
    form.innerHTML = `<div style="border:1px solid var(--border);border-radius:8px;padding:12px;margin-bottom:12px">
      <div class="form-group"><label class="form-label" for="zc-name">Tên chiến dịch</label>
        <input class="form-input" id="zc-name" value="${esc(c.name)}" placeholder="VD: Nhóm Affiliate Crypto 02/11"></div>
      <div class="form-group"><label class="form-label">Nguồn liên hệ
        <span class="info-i" tabindex="0" data-tip="Chỉ gửi cho người thuộc các nguồn được chọn. Không chọn gì = tất cả liên hệ chưa được mời. Mỗi người chỉ được mời 1 lần dù nằm ở nhiều nguồn.">i</span></label>
        <div style="max-height:140px;overflow:auto;border:1px solid var(--border);border-radius:6px;padding:6px">
        ${_sources.map((s) => { const k = `${s.source_type}:${s.source_id}`; return `<label style="display:block;font-size:.9rem">
          <input type="checkbox" class="zc-src" value="${esc(k)}" ${c.sources.includes(k) ? 'checked' : ''}> ${esc(s.source_name)} <span style="color:var(--text2)">(${s.pending} chưa liên hệ)</span></label>`; }).join('') || '<span style="color:var(--text2)">Chưa có nguồn — quét nhóm/sự kiện trước.</span>'}
        </div></div>
      <div class="form-group"><label class="form-label">Nick gửi
        <span class="info-i" tabindex="0" data-tip="Các nick được phép gửi cho chiến dịch này. Không chọn gì = tất cả nick đang bật. Mỗi nick có giới hạn và thời gian nghỉ riêng; các nick gửi xen kẽ, không bao giờ cùng lúc.">i</span></label>
        <div style="max-height:120px;overflow:auto;border:1px solid var(--border);border-radius:6px;padding:6px">
        ${nickOpts || '<span style="color:var(--text2)">Chưa có nick — thêm nick ở mục Nick Zalo.</span>'}</div></div>
      <div class="form-group"><label class="form-label" for="zc-friend">Lời nhắn kèm lời mời kết bạn
        <span class="info-i" tabindex="0" data-tip="Hiện trong lời mời kết bạn, tối đa 150 ký tự. {name} = tên gọi, {full_name} = họ tên đầy đủ, {a|b} = chọn ngẫu nhiên a hoặc b để mỗi người nhận câu hơi khác nhau.">i</span></label>
        <textarea class="form-input" id="zc-friend" rows="2" maxlength="150">${esc(c.friend_msg)}</textarea></div>
      <div class="form-group"><label class="form-label" for="zc-greet">Tin chào sau khi đồng ý kết bạn
        <span class="info-i" tabindex="0" data-tip="Tự gửi tin nhắn này khi người đó chấp nhận lời mời (kiểm tra mỗi 5 phút, trong giờ hoạt động). Để trống nếu không muốn gửi.">i</span></label>
        <textarea class="form-input" id="zc-greet" rows="3">${esc(c.greet_msg)}</textarea></div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px">
        <div class="form-group"><label class="form-label" for="zc-limit">Tổng mời / ngày
          <span class="info-i" tabindex="0" data-tip="Trần tổng của cả chiến dịch (cộng mọi nick). Giới hạn an toàn của từng nick đặt ở mục Nick Zalo — VD 4 nick × 25 = 100/ngày.">i</span></label>
          <input type="number" class="form-input" id="zc-limit" min="1" max="500" value="${c.daily_limit}"></div>
        <div class="form-group"><label class="form-label" for="zc-min">Nghỉ tối thiểu (giây)
          <span class="info-i" tabindex="0" data-tip="Mỗi nick chờ ngẫu nhiên trong khoảng này giữa 2 lời mời của chính nó.">i</span></label>
          <input type="number" class="form-input" id="zc-min" min="60" value="${c.min_delay_sec}"></div>
        <div class="form-group"><label class="form-label" for="zc-max">Nghỉ tối đa (giây)</label>
          <input type="number" class="form-input" id="zc-max" min="60" value="${c.max_delay_sec}"></div>
        <div class="form-group"><label class="form-label" for="zc-start">Giờ bắt đầu</label>
          <input type="number" class="form-input" id="zc-start" min="0" max="23" value="${c.start_hour}"></div>
        <div class="form-group"><label class="form-label" for="zc-end">Giờ kết thúc</label>
          <input type="number" class="form-input" id="zc-end" min="0" max="24" value="${c.end_hour}"></div>
      </div>
      <div style="display:flex;gap:8px;justify-content:flex-end">
        <button class="btn btn-ghost btn-sm" onclick="Zalo.closeCampaign()">Huỷ</button>
        <button class="btn btn-primary btn-sm" onclick="Zalo.saveCampaign()">Lưu</button></div></div>`;
  }

  function closeCampaign() {
    el('zalo-campaign-form').classList.add('hidden');
    _editingId = null;
  }

  async function saveCampaign() {
    const body = {
      name: el('zc-name').value.trim(),
      sources: [...document.querySelectorAll('.zc-src:checked')].map((x) => x.value),
      accounts: [...document.querySelectorAll('.zc-acc:checked')].map((x) => x.value),
      friend_msg: el('zc-friend').value.trim(),
      greet_msg: el('zc-greet').value.trim(),
      daily_limit: +el('zc-limit').value,
      min_delay_sec: +el('zc-min').value,
      max_delay_sec: +el('zc-max').value,
      start_hour: +el('zc-start').value,
      end_hour: +el('zc-end').value,
    };
    if (!body.name) return toast('Nhập tên chiến dịch', 'error');
    try {
      if (_editingId) await apiPut(`/api/zalo/campaigns/${_editingId}`, body);
      else await apiPost('/api/zalo/campaigns', body);
      toast('Đã lưu chiến dịch', 'success');
      closeCampaign();
      loadCampaigns();
    } catch (e) {
      toast(e.message, 'error');
    }
  }

  async function startCampaign(id) {
    try {
      await apiPost(`/api/zalo/campaigns/${id}/start`, {});
      toast('Chiến dịch đã chạy — lời mời đầu tiên gửi trong ~30 giây', 'success');
    } catch (e) {
      toast(e.message, 'error');
    }
    loadCampaigns();
  }

  async function pauseCampaign(id) {
    await apiPost(`/api/zalo/campaigns/${id}/pause`, {});
    loadCampaigns();
  }

  async function deleteCampaign(id) {
    if (!confirm('Xoá chiến dịch này? Danh bạ vẫn được giữ.')) return;
    await apiDelete(`/api/zalo/campaigns/${id}`);
    loadCampaigns();
  }

  async function showLogs(id) {
    const row = el(`zalo-logs-${id}`);
    if (!row.classList.contains('hidden')) return row.classList.add('hidden');
    row.classList.remove('hidden');
    const cell = row.firstElementChild;
    const logs = await apiGet(`/api/zalo/campaigns/${id}/logs`);
    const act = { friend_request: 'Mời kết bạn', greet: 'Gửi tin chào', skip: 'Bỏ qua' };
    cell.innerHTML = logs.length
      ? `<div style="max-height:260px;overflow:auto;font-size:.85rem">${logs.map((l) => `<div>${esc(l.created_at)} · ${l.ok ? '✅' : '❌'} ${act[l.action] || esc(l.action)} · ${esc(l.name || l.uid)}${l.error ? ` — <span style="color:var(--text2)">${esc(l.error)}</span>` : ''}</div>`).join('')}</div>`
      : '<div class="empty-state">Chưa có hoạt động.</div>';
  }

  return {
    init, addAccount, relogin, removeAccount, toggleAccount, editCap, resumeAccount, blockContact, loadGroups, filterGroups, scrapeGroup, scrapeActive, toggleEvents, scrapeEvent,
    openWatch, saveWatch, deleteWatch, toggleWatch, loadWatches,
    loadContacts, filterSource, openCampaign, closeCampaign, saveCampaign,
    startCampaign, pauseCampaign, deleteCampaign, showLogs,
  };
})();
window.Zalo = Zalo;
