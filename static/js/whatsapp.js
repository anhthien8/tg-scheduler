/**
 * WhatsApp Outreach Module
 * ────────────────────────
 * Several numbers (QR login each), scrape group members, join by invite link,
 * watch groups for newcomers / active speakers, DM campaigns shared across numbers.
 */
const WA = (() => {
  const API = '/api/whatsapp';
  let _loginPoll = null;
  let _sources = [];
  let _groups = [];
  let _editingId = null;
  let _accounts = [];
  let _campaigns = [];
  let _watches = [];
  let _sourceFilter = '';

  const STATUS_LABEL = {
    new: ['Chưa liên hệ', ''],
    sent: ['Đã gửi tin mở đầu', 'badge-purple'],
    replied: ['Đã trả lời', 'badge-blue'],
    followed: ['Đã gửi tin 2', 'badge-green'],
    handled: ['Bạn tự xử lý', 'badge-blue'],
    failed: ['Lỗi', 'badge-red'],
    skipped: ['Bỏ qua', ''],
    blocked: ['Đã chặn', 'badge-red'],
  };
  const SOURCE_TYPE = { group: 'Nhóm', active: 'Đang active', join: 'Mới vào' };

  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  const toast = (m, t = 'info') => (window.App && App.toast ? App.toast(m, t) : console.log(m));
  const el = (id) => document.getElementById(id);
  const tip = (t) => `<span class="info-i" tabindex="0" data-tip="${esc(t)}">i</span>`;
  const gpath = (id) => encodeURIComponent(id);

  async function init() {
    await loadAccounts();
    await Promise.all([loadSources(), loadCampaigns(), loadContacts()]);
    loadWatches();
  }

  // ── Numbers ──
  const LOGIN_BUSY = ['waiting_qr', 'qr_ready', 'scanned'];
  const numName = (id) => {
    const a = _accounts.find((x) => x.id === id);
    return a && a.phone ? `+${a.phone}${a.name ? ` (${a.name})` : ''}` : id;
  };

  function qrBlock(l) {
    if (l.state === 'qr_ready' && l.qr) {
      return `<div style="display:flex;gap:16px;align-items:center">
        <img src="${esc(l.qr)}" alt="QR liên kết WhatsApp" style="width:190px;height:190px;background:#fff;border-radius:8px;padding:6px">
        <div style="color:var(--text2);font-size:.9rem">Trên điện thoại: WhatsApp → <b>Cài đặt</b> → <b>Thiết bị đã liên kết</b> → <b>Liên kết thiết bị</b> → quét mã này.<br>Mã tự đổi mỗi ~20 giây. Nên dùng số phụ.</div></div>`;
    }
    if (l.state === 'scanned') return '✅ Đã quét — đang kết nối...';
    if (l.state === 'waiting_qr') return 'Đang tạo mã QR...';
    return `<span style="color:var(--text2)">${l.error ? '⚠️ ' + esc(l.error) : 'Chưa kết nối'}</span>`;
  }

  function accountRow(a) {
    const l = a.login || {};
    const id = esc(a.id);
    const del = `<button class="btn btn-danger btn-sm" onclick="WA.removeAccount('${id}')">Xoá</button>`;
    const head = `<div style="min-width:170px"><b>${a.phone ? '+' + esc(a.phone) : '(số mới)'}</b>
      <div style="color:var(--text2);font-size:.8rem">${esc(a.name || '')}</div></div>`;
    const row = 'display:flex;align-items:center;gap:12px;padding:8px 0;border-bottom:1px solid var(--border);flex-wrap:wrap';

    if (!a.logged_in) {
      const rescan = LOGIN_BUSY.includes(l.state) ? ''
        : `<button class="btn btn-primary btn-sm" onclick="WA.relogin('${id}')">Quét QR</button>`;
      return `<div style="${row}">${a.pending ? '' : head}<div style="flex:1">${qrBlock(l)}</div>
        <div style="white-space:nowrap">${rescan} ${del}</div></div>`;
    }

    let state;
    if (!a.enabled) state = '<span class="badge">Đã tắt</span>';
    else if (a.resting) state = `<span class="badge badge-red" title="${esc(a.last_error || '')}">Nghỉ đến ${esc(a.paused_until)}</span>`;
    else if (a.sent_today >= a.cap_today) state = '<span class="badge badge-blue">Đủ hạn mức hôm nay</span>';
    else state = '<span class="badge badge-green">Sẵn sàng</span>';
    const rate = a.sent_total ? `${a.replied_total}/${a.sent_total} người trả lời (${Math.round(100 * a.replied_total / a.sent_total)}%)` : 'chưa nhắn ai';
    return `<div style="${row}">${head}
      <div style="flex:1;min-width:200px;font-size:.85rem">
        ${state}${a.restricted ? ' <span class="badge badge-red">WhatsApp đang hạn chế</span>' : ''}
        <div>Hôm nay <b>${a.sent_today}/${a.cap_today}</b> tin mở đầu
          ${a.cap_note ? `<span style="color:var(--text2)">· ${esc(a.cap_note)}</span>` : ''}
          <span style="color:var(--text2)">· giới hạn số ${a.daily_cap}/ngày · vào ${a.joins_today}/3 nhóm</span></div>
        <div style="color:var(--text2)">${rate}</div>
        ${a.last_error ? `<div style="color:var(--red);font-size:.8rem">⚠️ ${esc(a.last_error)}</div>` : ''}
      </div>
      <div style="white-space:nowrap">
        ${a.resting ? `<button class="btn btn-secondary btn-sm" onclick="WA.resumeAccount('${id}')">Cho chạy lại</button>` : ''}
        <button class="btn btn-ghost btn-sm" onclick="WA.editCap('${id}')">Giới hạn</button>
        <button class="btn btn-ghost btn-sm" onclick="WA.toggleAccount('${id}')">${a.enabled ? 'Tắt' : 'Bật'}</button>
        ${del}</div></div>`;
  }

  async function loadAccounts() {
    const box = el('wa-account');
    if (!box) return;
    try {
      _accounts = await apiGet(`${API}/accounts`);
    } catch (e) {
      box.innerHTML = `<div class="empty-state">⚠️ ${esc(e.message)}</div>`;
      return;
    }
    box.innerHTML = _accounts.length ? _accounts.map(accountRow).join('')
      : '<div class="empty-state">Chưa có số nào — bấm "+ Thêm số WhatsApp" rồi quét QR.</div>';

    const sel = el('wa-group-account');
    if (sel) {
      const cur = sel.value;
      const live = _accounts.filter((a) => a.logged_in);
      sel.innerHTML = live.map((a) => `<option value="${esc(a.id)}">${esc(numName(a.id))}</option>`).join('')
        || '<option value="">(chưa có số)</option>';
      if (live.some((a) => a.id === cur)) sel.value = cur;
    }
    if (_accounts.some((a) => !a.logged_in && LOGIN_BUSY.includes((a.login || {}).state))) startPoll();
    else stopPoll();
  }

  function startPoll() {
    if (_loginPoll) return;
    _loginPoll = setInterval(() => {
      if (!el('wa-account') || el('view-whatsapp')?.classList.contains('hidden')) return stopPoll();
      loadAccounts();
    }, 2500);
  }

  function stopPoll() {
    if (_loginPoll) clearInterval(_loginPoll);
    _loginPoll = null;
  }

  // Run an API call, toast its error, then refresh.
  async function act(fn, after) {
    try {
      await fn();
    } catch (e) {
      return toast(e.message, 'error');
    }
    if (after) after();
  }

  const addAccount = () => act(() => apiPost(`${API}/accounts`, {}), loadAccounts);
  const relogin = (id) => act(() => apiPost(`${API}/accounts/${id}/login`, {}), loadAccounts);
  const resumeAccount = (id) => act(() => apiPost(`${API}/accounts/${id}/resume`, {}), loadAccounts);

  function removeAccount(id) {
    if (!confirm(`Xoá số "${numName(id)}"? Thiết bị liên kết bị gỡ khỏi điện thoại; người số này đã nhắn sẽ không nhận tin 2 nữa.`)) return;
    act(() => apiDelete(`${API}/accounts/${id}`), () => { loadAccounts(); loadWatches(); });
  }

  function saveAccount(id, patch) {
    const a = _accounts.find((x) => x.id === id);
    if (a) act(() => apiPut(`${API}/accounts/${id}`, { enabled: !!a.enabled, daily_cap: a.daily_cap, ...patch }), loadAccounts);
  }

  function toggleAccount(id) {
    const a = _accounts.find((x) => x.id === id);
    if (a) saveAccount(id, { enabled: !a.enabled });
  }

  function editCap(id) {
    const a = _accounts.find((x) => x.id === id);
    if (!a) return;
    const v = prompt(`Số tin mở đầu tối đa mỗi ngày cho "${numName(id)}" sau khi làm ấm (1–60).\nSố mới: 10–15 · số dùng lâu: 20–30. Nhắn người lạ càng nhiều càng dễ bị khoá.`, a.daily_cap);
    if (v == null) return;
    const n = parseInt(v, 10);
    if (!(n >= 1 && n <= 60)) return toast('Nhập số từ 1 đến 60', 'error');
    saveAccount(id, { daily_cap: n });
  }

  // ── Groups ──
  const groupAccount = () => el('wa-group-account')?.value || '';
  const groupOf = (id) => _groups.find((g) => g.id === id) || {};
  const watchOf = (id) => _watches.find((w) => w.group_id === id);
  const watchLabel = (id) => {
    const w = watchOf(id);
    if (!w) return '👁 Theo dõi';
    return `${w.watch_joins ? '👁' : ''}${w.track_active ? '🔥' : ''} Đang theo dõi`;
  };

  async function loadGroups(refresh) {
    const box = el('wa-groups');
    if (!groupAccount()) return toast('Chưa có số WhatsApp nào kết nối', 'error');
    box.innerHTML = '<div class="empty-state">Đang tải nhóm...</div>';
    try {
      _groups = await apiGet(`${API}/groups?account=${encodeURIComponent(groupAccount())}${refresh ? '&refresh=true' : ''}`);
    } catch (e) {
      box.innerHTML = `<div class="empty-state">⚠️ ${esc(e.message)}</div>`;
      return;
    }
    if (!_groups.length) {
      box.innerHTML = '<div class="empty-state">Số này chưa ở trong nhóm nào — dán link mời ở trên để vào nhóm.</div>';
      return;
    }
    box.innerHTML = `<input class="form-input" placeholder="Lọc nhóm..." style="margin-bottom:8px" oninput="WA.filterGroups(this.value)">
      <div style="max-height:420px;overflow:auto"><table class="data-table" style="width:100%"><thead><tr>
      <th>Nhóm</th><th style="width:90px">Thành viên</th><th style="width:300px"></th></tr></thead>
      <tbody id="wa-group-rows">${_groups.map(groupRow).join('')}</tbody></table></div>`;
  }

  function groupRow(g) {
    const id = esc(g.id);
    return `<tr data-name="${esc((g.name || '').toLowerCase())}"><td>${esc(g.name)}
        ${g.announce ? '<span class="badge" title="Chỉ quản trị viên được nhắn trong nhóm này">chỉ admin nhắn</span>' : ''}</td><td>${g.total}</td>
      <td style="text-align:right;white-space:nowrap">
        <button class="btn btn-secondary btn-sm" onclick="WA.scrapeGroup('${id}', this)">Quét thành viên</button>
        <button class="btn btn-ghost btn-sm" data-watch="${id}" onclick="WA.openWatch('${id}')">${watchLabel(g.id)}</button></td></tr>
      <tr class="hidden" id="wa-ev-${id}"><td colspan="3"></td></tr>`;
  }

  function filterGroups(q) {
    q = q.toLowerCase();
    document.querySelectorAll('#wa-group-rows tr[data-name]').forEach((tr) => {
      const show = tr.dataset.name.includes(q);
      tr.classList.toggle('hidden', !show);
      if (!show) tr.nextElementSibling.classList.add('hidden');
    });
  }

  async function scrapeGroup(id, btn) {
    btn.disabled = true;
    btn.textContent = 'Đang quét...';
    try {
      const r = await apiPost(`${API}/groups/${gpath(id)}/scrape`, { group_name: groupOf(id).name || '', account: groupAccount() });
      toast(`Đã lấy ${r.total} thành viên (${r.new} mới, loại ${r.admins_skipped} quản trị viên${r.hidden_numbers ? `, ${r.hidden_numbers} người ẩn số` : ''})`, 'success');
      loadSources();
      loadContacts();
    } catch (e) {
      toast(e.message, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Quét thành viên';
    }
  }

  // ── Join by invite link ──
  async function previewInvite() {
    const link = el('wa-invite').value.trim();
    const box = el('wa-invite-box');
    if (!link) return toast('Dán link mời nhóm (https://chat.whatsapp.com/…)', 'error');
    if (!groupAccount()) return toast('Chưa có số WhatsApp nào kết nối', 'error');
    box.innerHTML = 'Đang xem nhóm...';
    try {
      const g = await apiGet(`${API}/invite?link=${encodeURIComponent(link)}&account=${encodeURIComponent(groupAccount())}`);
      box.innerHTML = `<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:6px 0">
        <span><b>${esc(g.name || '(không tên)')}</b> · ${g.total} thành viên
          ${g.needs_approval ? ' · <span style="color:var(--text2)">cần quản trị viên duyệt</span>' : ''}</span>
        ${g.joined ? '<span class="badge badge-green">Đã ở trong nhóm</span>'
          : `<button class="btn btn-primary btn-sm" onclick="WA.joinGroup(this)">Vào nhóm bằng ${esc(numName(groupAccount()))}</button>`}</div>`;
    } catch (e) {
      box.innerHTML = `<span style="color:var(--red)">⚠️ ${esc(e.message)}</span>`;
    }
  }

  async function joinGroup(btn) {
    btn.disabled = true;
    try {
      const r = await apiPost(`${API}/groups/join`, { link: el('wa-invite').value.trim(), account: groupAccount() });
      toast(r.pending ? 'Đã gửi yêu cầu — chờ quản trị viên nhóm duyệt' : 'Đã vào nhóm', 'success');
      el('wa-invite').value = '';
      el('wa-invite-box').innerHTML = '';
      loadAccounts();
      if (!r.pending) loadGroups(true);
    } catch (e) {
      toast(e.message, 'error');
      btn.disabled = false;
    }
  }

  // ── Watches ──
  async function openWatch(id) {
    const row = el(`wa-ev-${id}`);
    if (!row) return;
    row.classList.remove('hidden');
    if (!_campaigns.length) {
      try { _campaigns = await apiGet(`${API}/campaigns`); } catch { /* picker shows the hint below */ }
    }
    const w = watchOf(id) || {};
    const g = groupOf(id);
    const acc = w.account_id || groupAccount();
    const nums = _accounts.filter((a) => a.logged_in);
    const eid = esc(id);
    row.firstElementChild.innerHTML = `<div style="display:grid;gap:6px;padding:6px 0">
      <label><input type="checkbox" id="ww-join-${eid}" ${w.watch_joins ? 'checked' : ''} ${_campaigns.length ? '' : 'disabled'}>
        👁 Nhắn người mới vào nhóm bằng chiến dịch
        <select class="form-input" id="ww-camp-${eid}" style="width:200px;display:inline-block">${_campaigns.map((c) =>
          `<option value="${c.id}" ${w.campaign_id === c.id ? 'selected' : ''}>${esc(c.name)}</option>`).join('') || '<option value="">(chưa có chiến dịch)</option>'}</select>
        ${tip('Ai mới vào nhóm sẽ được xếp lên đầu hàng chờ của chiến dịch này và nhận tin mở đầu theo nhịp / giới hạn của chiến dịch. Chiến dịch đã hết danh sách tự chạy lại; chiến dịch bạn tạm dừng thì giữ nguyên.')}</label>
      <label><input type="checkbox" id="ww-active-${eid}" ${w.track_active ? 'checked' : ''} ${g.announce ? 'disabled' : ''}>
        🔥 Ghi nhận người đang nhắn trong nhóm
        ${tip('WhatsApp không cho đọc lại tin cũ của nhóm, nên app chỉ ghi nhận từ lúc bật: ai nhắn trong nhóm sẽ vào nguồn "🔥 Active". Cần vài ngày để có danh sách đáng kể.')}</label>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><span>Nghe bằng số
        ${tip('Số này phải ở trong nhóm và đang kết nối. Chỉ nhóm đang theo dõi mới được app đọc tin, các nhóm khác bị bỏ qua để nhẹ máy.')}</span>
        <select class="form-input" id="ww-acc-${eid}" style="width:220px">${nums.map((a) =>
          `<option value="${esc(a.id)}" ${a.id === acc ? 'selected' : ''}>${esc(numName(a.id))}</option>`).join('')}</select>
        <button class="btn btn-primary btn-sm" onclick="WA.saveWatch('${eid}')">Lưu</button>
        <button class="btn btn-ghost btn-sm" onclick="this.closest('tr').classList.add('hidden')">Đóng</button></div></div>`;
  }

  function saveWatch(id) {
    const body = {
      group_name: groupOf(id).name || (watchOf(id) || {}).group_name || '',
      account: el(`ww-acc-${id}`)?.value || '',
      campaign_id: +el(`ww-camp-${id}`)?.value || null,
      watch_joins: !!el(`ww-join-${id}`)?.checked,
      track_active: !!el(`ww-active-${id}`)?.checked,
    };
    if (!body.account) return toast('Chọn số để nghe nhóm này', 'error');
    act(() => apiPut(`${API}/watches/${gpath(id)}`, body), () => {
      toast(body.watch_joins || body.track_active ? 'Đã lưu theo dõi nhóm' : 'Đã bỏ theo dõi nhóm', 'success');
      el(`wa-ev-${id}`)?.classList.add('hidden');
      loadWatches();
      loadCampaigns();
    });
  }

  function deleteWatch(id) {
    if (!confirm('Bỏ theo dõi nhóm này?')) return;
    act(() => apiDelete(`${API}/watches/${gpath(id)}`), loadWatches);
  }

  async function loadWatches() {
    const box = el('wa-watches');
    try {
      _watches = await apiGet(`${API}/watches`);
    } catch (e) {
      if (box) box.innerHTML = `<div class="empty-state">⚠️ ${esc(e.message)}</div>`;
      return;
    }
    document.querySelectorAll('#wa-group-rows button[data-watch]').forEach((b) => { b.textContent = watchLabel(b.dataset.watch); });
    if (!box) return;
    if (!_watches.length) {
      box.innerHTML = '<div class="empty-state">Chưa theo dõi nhóm nào. Bấm "👁 Theo dõi" ở một nhóm phía trên.</div>';
      return;
    }
    box.innerHTML = `<table class="data-table" style="width:100%"><thead><tr><th>Nhóm</th><th>Số nghe</th>
      <th>Người mới ${tip('Số người mới vào nhóm đã được đưa vào hàng chờ của chiến dịch kể từ khi bật')}</th>
      <th>Active ${tip('Số người đã nhắn trong nhóm kể từ khi bật ghi nhận. Bấm Xem để lọc danh bạ theo nguồn này.')}</th><th></th></tr></thead><tbody>
      ${_watches.map((w) => {
        const a = _accounts.find((x) => x.id === w.account_id);
        const live = a && a.logged_in;
        const gid = esc(w.group_id);
        const joins = !w.watch_joins ? '<span style="color:var(--text2)">—</span>'
          : `<b>${w.joined_count || 0}</b> → ${w.campaign_name ? esc(w.campaign_name) : '<span style="color:var(--red)">chiến dịch đã xoá</span>'}
             ${w.campaign_status === 'paused' ? '<span style="color:var(--text2);font-size:.8rem">(đang tạm dừng)</span>' : ''}
             ${w.last_join_at ? `<div style="font-size:.8rem;color:var(--text2)">gần nhất ${esc(w.last_join_at)}</div>` : ''}`;
        const active = !w.track_active ? '<span style="color:var(--text2)">—</span>'
          : `<b>${w.active_count || 0}</b> ${w.active_count ? `<a href="#" onclick="WA.filterSource('active:${gid}');return false">Xem</a>` : ''}`;
        return `<tr><td><b>${esc(w.group_name || w.group_id)}</b></td>
          <td>${esc(numName(w.account_id))} <span class="badge ${live ? 'badge-green' : 'badge-red'}">${live ? 'Đang nghe' : 'Mất kết nối'}</span></td>
          <td>${joins}</td><td>${active}</td>
          <td style="text-align:right"><button class="btn btn-ghost btn-sm" onclick="WA.deleteWatch('${gid}')">Bỏ theo dõi</button></td></tr>`;
      }).join('')}</tbody></table>`;
  }

  // ── Sources ──
  async function loadSources() {
    const box = el('wa-sources');
    try {
      _sources = await apiGet(`${API}/sources`);
    } catch (e) {
      box.innerHTML = `<div class="empty-state">⚠️ ${esc(e.message)}</div>`;
      return;
    }
    box.innerHTML = _sources.length
      ? `<table class="data-table" style="width:100%"><thead><tr><th>Nguồn</th><th>Loại</th><th>Tổng</th><th>Chưa liên hệ</th><th>Cập nhật</th><th></th></tr></thead><tbody>
        ${_sources.map((s) => `<tr><td>${esc(s.source_name)}</td><td>${SOURCE_TYPE[s.source_type] || esc(s.source_type)}</td>
          <td>${s.total}</td><td>${s.pending}</td><td>${esc(s.last_scraped || '')}</td>
          <td><button class="btn btn-ghost btn-sm" onclick="WA.filterSource('${esc(s.source_type)}:${esc(s.source_id)}')">Xem</button></td></tr>`).join('')}
        </tbody></table>`
      : '<div class="empty-state">Chưa quét nhóm nào.</div>';
  }

  // ── Contacts ──
  function filterSource(src) {
    _sourceFilter = src;
    loadContacts();
    el('wa-contacts')?.scrollIntoView({ behavior: 'smooth' });
  }

  async function loadContacts() {
    const box = el('wa-contacts');
    if (!box) return;
    const params = new URLSearchParams({
      status: el('wa-contact-status')?.value || '',
      q: el('wa-contact-q')?.value || '',
      source: _sourceFilter,
      limit: 200,
    });
    let data;
    try {
      data = await apiGet(`${API}/contacts?${params}`);
    } catch (e) {
      box.innerHTML = `<div class="empty-state">⚠️ ${esc(e.message)}</div>`;
      return;
    }
    const src = _sources.find((s) => `${s.source_type}:${s.source_id}` === _sourceFilter);
    const filterNote = _sourceFilter
      ? `<div style="margin-bottom:6px;font-size:.85rem">Lọc theo: <b>${esc(src ? src.source_name : _sourceFilter)}</b>
         <a href="#" onclick="WA.filterSource('');return false">✕ bỏ lọc</a></div>` : '';
    if (!data.items.length) {
      box.innerHTML = filterNote + '<div class="empty-state">Không có liên hệ.</div>';
      return;
    }
    box.innerHTML = `${filterNote}<div style="color:var(--text2);font-size:.85rem;margin-bottom:6px">${data.total} liên hệ${data.total > data.items.length ? ` (hiện ${data.items.length})` : ''}</div>
      <div style="max-height:480px;overflow:auto"><table class="data-table" style="width:100%"><thead><tr><th>Số</th><th>Tên</th><th>Nguồn</th><th>Trạng thái</th><th>Số gửi</th><th>Cập nhật</th><th></th></tr></thead><tbody>
      ${data.items.map((c) => {
        const [label, cls] = STATUS_LABEL[c.status] || [c.status, ''];
        const uid = esc(c.uid);
        return `<tr><td>${c.phone ? '+' + esc(c.phone) : '<span style="color:var(--text2)" title="Người này ẩn số điện thoại — vẫn nhắn được">(ẩn số)</span>'}</td>
          <td>${esc(c.name || '')}</td>
          <td style="font-size:.8rem;color:var(--text2)">${esc(c.source_names || '')}</td>
          <td><span class="badge ${cls}" ${c.last_error ? `title="${esc(c.last_error)}"` : ''}>${label}</span></td>
          <td style="font-size:.8rem">${c.account_id ? esc(numName(c.account_id)) : ''}</td>
          <td style="font-size:.8rem">${esc(c.updated_at || '')}</td>
          <td>${c.status === 'blocked'
            ? `<button class="btn btn-ghost btn-sm" onclick="WA.blockContact('${uid}', false)">Bỏ chặn</button>`
            : c.status === 'new' ? `<button class="btn btn-ghost btn-sm" title="Không số nào được nhắn người này" onclick="WA.blockContact('${uid}', true)">Chặn</button>` : ''}</td></tr>`;
      }).join('')}</tbody></table></div>`;
  }

  const blockContact = (uid, blocked) =>
    act(() => apiPost(`${API}/contacts/${encodeURIComponent(uid)}/block`, { blocked }), loadContacts);

  // ── Campaigns ──
  async function loadCampaigns() {
    const box = el('wa-campaigns');
    try {
      _campaigns = await apiGet(`${API}/campaigns`);
    } catch (e) {
      box.innerHTML = `<div class="empty-state">⚠️ ${esc(e.message)}</div>`;
      return;
    }
    if (!_campaigns.length) {
      box.innerHTML = '<div class="empty-state">Chưa có chiến dịch.</div>';
      return;
    }
    const stBadge = { running: ['Đang chạy', 'badge-green'], paused: ['Tạm dừng', 'badge-purple'], done: ['Hết danh sách', 'badge-blue'] };
    box.innerHTML = `<table class="data-table" style="width:100%"><thead><tr><th>Chiến dịch</th><th>Trạng thái</th>
      <th>Hôm nay ${tip('Số tin mở đầu đã gửi hôm nay (cộng tất cả số) / giới hạn tổng của chiến dịch')}</th>
      <th>Đã nhắn</th><th>Trả lời</th><th>Đã gửi tin 2</th><th></th></tr></thead><tbody>
      ${_campaigns.map((c) => {
        const [label, cls] = stBadge[c.status] || [c.status, ''];
        const srcNames = c.sources.length
          ? c.sources.map((k) => (_sources.find((s) => `${s.source_type}:${s.source_id}` === k) || {}).source_name || k).join(', ')
          : 'Tất cả liên hệ';
        const nums = c.accounts.length ? c.accounts.map(numName).join(', ') : 'Tất cả số';
        return `<tr><td><b>${esc(c.name)}</b><div style="font-size:.8rem;color:var(--text2)">${esc(srcNames)} · 📱 ${esc(nums)}${c.countries.length ? ` · đầu số +${esc(c.countries.join(', +'))}` : ''}</div>
            ${c.last_error ? `<div style="font-size:.8rem;color:var(--red)">⚠️ ${esc(c.last_error)}</div>` : ''}</td>
          <td><span class="badge ${cls}">${label}</span></td>
          <td>${c.sent_today}/${c.daily_limit}</td><td>${c.sent}</td><td>${c.replied}</td><td>${c.followed}</td>
          <td style="white-space:nowrap;text-align:right">
            ${c.status === 'running'
              ? `<button class="btn btn-secondary btn-sm" onclick="WA.pauseCampaign(${c.id})">Dừng</button>`
              : `<button class="btn btn-primary btn-sm" onclick="WA.startCampaign(${c.id})">Chạy</button>`}
            <button class="btn btn-ghost btn-sm" onclick="WA.openCampaign(${c.id})">Sửa</button>
            <button class="btn btn-ghost btn-sm" onclick="WA.showLogs(${c.id})">Log</button>
            <button class="btn btn-danger btn-sm" onclick="WA.deleteCampaign(${c.id})">Xoá</button></td></tr>
          <tr class="hidden" id="wa-logs-${c.id}"><td colspan="7"></td></tr>`;
      }).join('')}</tbody></table>`;
  }

  async function openCampaign(id) {
    const form = el('wa-campaign-form');
    _editingId = id || null;
    const c = (id && _campaigns.find((x) => x.id === id)) || { name: '', sources: [], accounts: [], countries: [],
      open_msg: '{Hi|Hello|Hey} {name}, {saw you in|we are both in} the group — {mind if I ask you something|can I ask you a quick question}?',
      reply_msg: '', daily_limit: 40, min_delay_sec: 300, max_delay_sec: 900, start_hour: 9, end_hour: 21 };
    if (!_sources.length) await loadSources();
    if (!_accounts.length) await loadAccounts();
    const numOpts = _accounts.filter((a) => !a.pending).map((a) => `<label style="display:block;font-size:.9rem">
      <input type="checkbox" class="wc-acc" value="${esc(a.id)}" ${c.accounts.includes(a.id) ? 'checked' : ''}> ${esc(numName(a.id))}
      <span style="color:var(--text2)">(${a.logged_in ? `${a.cap_today}/ngày hôm nay` : 'chưa kết nối'})</span></label>`).join('');
    const num = (fid, label, val, attrs, hint) => `<div class="form-group"><label class="form-label" for="${fid}">${label} ${hint ? tip(hint) : ''}</label>
      <input type="number" class="form-input" id="${fid}" ${attrs} value="${val}"></div>`;
    form.classList.remove('hidden');
    form.innerHTML = `<div style="border:1px solid var(--border);border-radius:8px;padding:12px;margin-bottom:12px">
      <div class="form-group"><label class="form-label" for="wc-name">Tên chiến dịch</label>
        <input class="form-input" id="wc-name" value="${esc(c.name)}" placeholder="VD: Nhóm Crypto PH 11/2026"></div>
      <div class="form-group"><label class="form-label">Nguồn liên hệ
        ${tip('Chỉ nhắn người thuộc các nguồn được chọn. Không chọn gì = tất cả liên hệ chưa được nhắn. Mỗi người chỉ nhận 1 tin mở đầu dù nằm ở nhiều nguồn.')}</label>
        <div style="max-height:140px;overflow:auto;border:1px solid var(--border);border-radius:6px;padding:6px">
        ${_sources.map((s) => { const k = `${s.source_type}:${s.source_id}`; return `<label style="display:block;font-size:.9rem">
          <input type="checkbox" class="wc-src" value="${esc(k)}" ${c.sources.includes(k) ? 'checked' : ''}> ${esc(s.source_name)} <span style="color:var(--text2)">(${s.pending} chưa liên hệ)</span></label>`; }).join('') || '<span style="color:var(--text2)">Chưa có nguồn — quét một nhóm trước.</span>'}
        </div></div>
      <div class="form-group"><label class="form-label">Số gửi
        ${tip('Các số được phép gửi cho chiến dịch này. Không chọn gì = tất cả số đang bật. Mỗi số có giới hạn và thời gian nghỉ riêng; các số gửi xen kẽ, không bao giờ cùng lúc.')}</label>
        <div style="max-height:120px;overflow:auto;border:1px solid var(--border);border-radius:6px;padding:6px">
        ${numOpts || '<span style="color:var(--text2)">Chưa có số — thêm ở mục Số WhatsApp.</span>'}</div></div>
      <div class="form-group"><label class="form-label" for="wc-countries">Chỉ nhắn đầu số quốc gia
        ${tip('Để tin đúng ngôn ngữ người nhận: nhập đầu số, cách nhau bằng dấu phẩy (VD 84 = Việt Nam, 63 = Philippines, 66 = Thái Lan). Khi có đầu số, người ẩn số điện thoại sẽ bị bỏ qua vì không biết họ ở đâu. Để trống = nhắn tất cả.')}</label>
        <input class="form-input" id="wc-countries" value="${esc(c.countries.join(', '))}" placeholder="VD: 63, 66 — để trống = tất cả"></div>
      <div class="form-group"><label class="form-label" for="wc-open">Tin mở đầu
        ${tip('Tin đầu tiên gửi cho người lạ: ngắn, không link, như người thật gõ. {name} = tên WhatsApp của họ (thường trống với người vừa cào — khi đó được bỏ đi), {a|b} = chọn ngẫu nhiên để mỗi người nhận câu hơi khác.')}</label>
        <textarea class="form-input" id="wc-open" rows="2" maxlength="2000">${esc(c.open_msg)}</textarea></div>
      <div class="form-group"><label class="form-label" for="wc-reply">Tin 2 — gửi khi họ trả lời
        ${tip('Tự gửi 30–120 giây sau khi người đó trả lời tin mở đầu (nội dung chính, link). Không gửi nếu bạn đã tự trả lời bằng điện thoại, hoặc nếu câu trả lời đã cũ hơn 48 giờ. Để trống = bạn tự trả lời tay.')}</label>
        <textarea class="form-input" id="wc-reply" rows="3" maxlength="2000">${esc(c.reply_msg)}</textarea></div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px">
        ${num('wc-limit', 'Tổng tin mở đầu / ngày', c.daily_limit, 'min="1" max="500"', 'Trần tổng của cả chiến dịch (cộng mọi số). Giới hạn an toàn của từng số đặt ở mục Số WhatsApp — VD 3 số × 20 = 60/ngày.')}
        ${num('wc-min', 'Nghỉ tối thiểu (giây)', c.min_delay_sec, 'min="60"', 'Mỗi số chờ ngẫu nhiên trong khoảng này giữa 2 tin mở đầu của chính nó.')}
        ${num('wc-max', 'Nghỉ tối đa (giây)', c.max_delay_sec, 'min="60"')}
        ${num('wc-start', 'Giờ bắt đầu', c.start_hour, 'min="0" max="23"')}
        ${num('wc-end', 'Giờ kết thúc', c.end_hour, 'min="0" max="24"')}
      </div>
      <div style="display:flex;gap:8px;justify-content:flex-end">
        <button class="btn btn-ghost btn-sm" onclick="WA.closeCampaign()">Huỷ</button>
        <button class="btn btn-primary btn-sm" onclick="WA.saveCampaign()">Lưu</button></div></div>`;
  }

  function closeCampaign() {
    el('wa-campaign-form').classList.add('hidden');
    _editingId = null;
  }

  function saveCampaign() {
    const checked = (cls) => [...document.querySelectorAll(`.${cls}:checked`)].map((x) => x.value);
    const body = {
      name: el('wc-name').value.trim(),
      sources: checked('wc-src'),
      accounts: checked('wc-acc'),
      countries: el('wc-countries').value.split(',').map((x) => x.trim()).filter(Boolean),
      open_msg: el('wc-open').value.trim(),
      reply_msg: el('wc-reply').value.trim(),
      daily_limit: +el('wc-limit').value,
      min_delay_sec: +el('wc-min').value,
      max_delay_sec: +el('wc-max').value,
      start_hour: +el('wc-start').value,
      end_hour: +el('wc-end').value,
    };
    if (!body.name) return toast('Nhập tên chiến dịch', 'error');
    if (!body.open_msg) return toast('Nhập tin mở đầu', 'error');
    if (body.countries.some((c) => !/^\+?\d{1,4}$/.test(c))) return toast('Đầu số quốc gia chỉ gồm 1–4 chữ số, ví dụ 84', 'error');
    act(() => (_editingId ? apiPut(`${API}/campaigns/${_editingId}`, body) : apiPost(`${API}/campaigns`, body)), () => {
      toast('Đã lưu chiến dịch', 'success');
      closeCampaign();
      loadCampaigns();
    });
  }

  const startCampaign = (id) => act(() => apiPost(`${API}/campaigns/${id}/start`, {}), () => {
    toast('Chiến dịch đã chạy — tin đầu tiên gửi trong ~30 giây', 'success');
    loadCampaigns();
  });
  const pauseCampaign = (id) => act(() => apiPost(`${API}/campaigns/${id}/pause`, {}), loadCampaigns);

  function deleteCampaign(id) {
    if (!confirm('Xoá chiến dịch này? Danh bạ vẫn được giữ.')) return;
    act(() => apiDelete(`${API}/campaigns/${id}`), () => { loadCampaigns(); loadWatches(); });
  }

  async function showLogs(id) {
    const row = el(`wa-logs-${id}`);
    if (!row.classList.contains('hidden')) return row.classList.add('hidden');
    row.classList.remove('hidden');
    const cell = row.firstElementChild;
    const logs = await apiGet(`${API}/campaigns/${id}/logs`);
    const actName = { open: 'Tin mở đầu', followup: 'Tin 2' };
    cell.innerHTML = logs.length
      ? `<div style="max-height:260px;overflow:auto;font-size:.85rem">${logs.map((l) => `<div>${esc(l.created_at)} · ${l.ok ? '✅' : '❌'} ${actName[l.action] || esc(l.action)} · ${esc(l.name || l.uid)}${l.error ? ` — <span style="color:var(--text2)">${esc(l.error)}</span>` : ''}</div>`).join('')}</div>`
      : '<div class="empty-state">Chưa có hoạt động.</div>';
  }

  return {
    init, addAccount, relogin, removeAccount, toggleAccount, editCap, resumeAccount,
    loadGroups, filterGroups, scrapeGroup, previewInvite, joinGroup,
    openWatch, saveWatch, deleteWatch, loadWatches,
    loadContacts, filterSource, blockContact,
    openCampaign, closeCampaign, saveCampaign, startCampaign, pauseCampaign, deleteCampaign, showLogs,
  };
})();
window.WA = WA;
