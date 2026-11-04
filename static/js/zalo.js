/**
 * Zalo Outreach Module
 * ────────────────────
 * QR login, scrape group members / event attendees, friend-request campaigns.
 */
const Zalo = (() => {
  let _loginPoll = null;
  let _sources = [];
  let _groups = [];
  let _editingId = null;

  const STATUS_LABEL = {
    new: ['Chưa liên hệ', ''],
    requested: ['Đã gửi lời mời', 'badge-purple'],
    friend: ['Đã là bạn', 'badge-blue'],
    greeted: ['Đã chào', 'badge-green'],
    failed: ['Lỗi', 'badge-red'],
    skipped: ['Bỏ qua', ''],
  };

  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }
  const toast = (m, t = 'info') => (window.App && App.toast ? App.toast(m, t) : console.log(m));
  const el = (id) => document.getElementById(id);

  async function init() {
    await loadAccount();
    await Promise.all([loadSources(), loadCampaigns(), loadContacts()]);
  }

  // ── Account ──
  async function loadAccount() {
    const box = el('zalo-account');
    if (!box) return;
    let st;
    try {
      st = await apiGet('/api/zalo/status');
    } catch (e) {
      box.innerHTML = `<div class="empty-state">⚠️ ${esc(e.message)}</div>`;
      return;
    }
    if (st.logged_in) {
      stopPoll();
      const me = st.me || {};
      box.innerHTML = `<div style="display:flex;align-items:center;gap:12px">
        ${me.avatar ? `<img src="${esc(me.avatar)}" alt="" style="width:40px;height:40px;border-radius:50%">` : ''}
        <div style="flex:1"><b>${esc(me.name || me.uid)}</b><div style="color:var(--text2);font-size:.85rem">UID ${esc(me.uid)} · đã đăng nhập</div></div>
        <button class="btn btn-ghost btn-sm" onclick="Zalo.logout()">Đăng xuất</button></div>`;
      return;
    }
    const l = st.login || {};
    if (l.state === 'qr_ready' && l.qr) {
      const src = l.qr.startsWith('data:') ? l.qr : `data:image/png;base64,${l.qr}`;
      box.innerHTML = `<div style="display:flex;gap:16px;align-items:center">
        <img src="${src}" alt="QR đăng nhập Zalo" style="width:180px;height:180px;background:#fff;border-radius:8px;padding:6px">
        <div style="color:var(--text2)">Mở Zalo trên điện thoại → biểu tượng QR → quét mã này.<br>Mã hết hạn sau khoảng 1 phút.</div></div>`;
    } else if (l.state === 'scanned') {
      box.innerHTML = `<div class="empty-state">✅ ${esc(l.scanned || '')} đã quét — xác nhận đăng nhập trên điện thoại...</div>`;
    } else if (l.state === 'waiting_qr') {
      box.innerHTML = `<div class="empty-state">Đang tạo mã QR...</div>`;
    } else {
      stopPoll();
      box.innerHTML = `<div style="display:flex;align-items:center;gap:12px">
        <div style="flex:1;color:var(--text2)">${l.error ? '⚠️ ' + esc(l.error) : 'Chưa đăng nhập Zalo.'}</div>
        <button class="btn btn-primary btn-sm" onclick="Zalo.login()">Đăng nhập bằng QR</button></div>`;
    }
  }

  function stopPoll() {
    if (_loginPoll) clearInterval(_loginPoll);
    _loginPoll = null;
  }

  async function login() {
    try {
      await apiPost('/api/zalo/login', {});
    } catch (e) {
      return toast(e.message, 'error');
    }
    stopPoll();
    _loginPoll = setInterval(async () => {
      if (!el('zalo-account') || el('view-zalo')?.classList.contains('hidden')) return stopPoll();
      await loadAccount();
    }, 2000);
    loadAccount();
  }

  async function logout() {
    if (!confirm('Đăng xuất Zalo? Chiến dịch đang chạy sẽ dừng gửi.')) return;
    await apiPost('/api/zalo/logout', {});
    loadAccount();
  }

  // ── Groups & events ──
  async function loadGroups() {
    const box = el('zalo-groups');
    box.innerHTML = '<div class="empty-state">Đang tải nhóm...</div>';
    try {
      _groups = await apiGet('/api/zalo/groups');
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
      <th>Nhóm</th><th style="width:90px">Thành viên</th><th style="width:250px"></th></tr></thead>
      <tbody id="zalo-group-rows">${_groups.map(groupRow).join('')}</tbody></table></div>`;
  }

  function groupRow(g) {
    return `<tr data-name="${esc((g.name || '').toLowerCase())}"><td>${esc(g.name)}</td><td>${g.total}</td>
      <td style="text-align:right;white-space:nowrap">
        <button class="btn btn-secondary btn-sm" onclick="Zalo.scrapeGroup('${esc(g.id)}', this)">Quét thành viên</button>
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

  const groupName = (id) => (_groups.find((g) => g.id === id) || {}).name || '';

  async function scrapeGroup(id, btn) {
    btn.disabled = true;
    btn.textContent = 'Đang quét...';
    try {
      const r = await apiPost(`/api/zalo/groups/${id}/scrape`, { group_name: groupName(id) });
      let msg = `Đã lấy ${r.total} thành viên (${r.new} mới)`;
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

  async function toggleEvents(id, btn) {
    const row = el(`zalo-ev-${id}`);
    if (!row.classList.contains('hidden')) return row.classList.add('hidden');
    row.classList.remove('hidden');
    const cell = row.firstElementChild;
    cell.innerHTML = '<div class="empty-state">Đang tải sự kiện...</div>';
    try {
      const evs = await apiGet(`/api/zalo/groups/${id}/events`);
      if (!evs.length) {
        cell.innerHTML = '<div class="empty-state">Nhóm không có sự kiện / nhắc hẹn.</div>';
        return;
      }
      cell.innerHTML = evs.map((ev) => `<div style="display:flex;align-items:center;gap:8px;padding:4px 0">
        <span style="flex:1">📅 ${esc(ev.title || '(không tiêu đề)')}
          <span style="color:var(--text2);font-size:.8rem">${ev.start_time ? new Date(ev.start_time).toLocaleString('vi-VN') : ''}
          ${ev.accept_count != null ? ` · ${ev.accept_count} tham gia` : ''}</span></span>
        <button class="btn btn-secondary btn-sm" data-title="${esc(ev.title)}"
          onclick="Zalo.scrapeEvent('${esc(ev.id)}','${esc(id)}',this)">Lấy người tham gia</button></div>`).join('');
    } catch (e) {
      cell.innerHTML = `<div class="empty-state">⚠️ ${esc(e.message)}</div>`;
    }
  }

  async function scrapeEvent(eventId, groupId, btn) {
    btn.disabled = true;
    btn.textContent = 'Đang lấy...';
    try {
      const r = await apiPost(`/api/zalo/events/${eventId}/scrape`, { title: btn.dataset.title || '', group_name: groupName(groupId) });
      toast(`Đã lấy ${r.total} người tham gia (${r.new} mới)`, 'success');
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
      <div style="max-height:480px;overflow:auto"><table class="data-table" style="width:100%"><thead><tr><th>Tên</th><th>Nguồn</th><th>Trạng thái</th><th>Cập nhật</th></tr></thead><tbody>
      ${data.items.map((c) => {
        const [label, cls] = STATUS_LABEL[c.status] || [c.status, ''];
        return `<tr><td>${c.avatar ? `<img src="${esc(c.avatar)}" alt="" style="width:22px;height:22px;border-radius:50%;vertical-align:middle;margin-right:6px">` : ''}${esc(c.name || c.uid)}</td>
          <td style="font-size:.8rem;color:var(--text2)">${esc(c.source_names || '')}</td>
          <td><span class="badge ${cls}" ${c.last_error ? `title="${esc(c.last_error)}"` : ''}>${label}</span></td>
          <td style="font-size:.8rem">${esc(c.updated_at || '')}</td></tr>`;
      }).join('')}</tbody></table></div>`;
  }

  // ── Campaigns ──
  async function loadCampaigns() {
    const box = el('zalo-campaigns');
    let list;
    try {
      list = await apiGet('/api/zalo/campaigns');
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
      <th>Hôm nay <span class="info-i" tabindex="0" data-tip="Số lời mời kết bạn đã gửi hôm nay / giới hạn mỗi ngày">i</span></th>
      <th>Đã mời</th><th>Đồng ý</th><th>Đã chào</th><th></th></tr></thead><tbody>
      ${list.map((c) => {
        const [label, cls] = stBadge[c.status] || [c.status, ''];
        const srcNames = c.sources.length
          ? c.sources.map((k) => (_sources.find((s) => `${s.source_type}:${s.source_id}` === k) || {}).source_name || k).join(', ')
          : 'Tất cả liên hệ';
        return `<tr><td><b>${esc(c.name)}</b><div style="font-size:.8rem;color:var(--text2)">${esc(srcNames)}</div>
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
    let c = { name: '', sources: [], friend_msg: 'Chào {name}, mình cũng trong nhóm {AFFILIATE CRYPTO|cộng đồng} — kết bạn giao lưu nhé!',
      greet_msg: 'Cảm ơn {name} đã đồng ý kết bạn 🙌', daily_limit: 20, min_delay_sec: 300, max_delay_sec: 900, start_hour: 8, end_hour: 22 };
    if (id) {
      const list = await apiGet('/api/zalo/campaigns');
      c = list.find((x) => x.id === id) || c;
    }
    if (!_sources.length) await loadSources();
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
      <div class="form-group"><label class="form-label" for="zc-friend">Lời nhắn kèm lời mời kết bạn
        <span class="info-i" tabindex="0" data-tip="Hiện trong lời mời kết bạn, tối đa 150 ký tự. {name} = tên gọi, {full_name} = họ tên đầy đủ, {a|b} = chọn ngẫu nhiên a hoặc b để mỗi người nhận câu hơi khác nhau.">i</span></label>
        <textarea class="form-input" id="zc-friend" rows="2" maxlength="150">${esc(c.friend_msg)}</textarea></div>
      <div class="form-group"><label class="form-label" for="zc-greet">Tin chào sau khi đồng ý kết bạn
        <span class="info-i" tabindex="0" data-tip="Tự gửi tin nhắn này khi người đó chấp nhận lời mời (kiểm tra mỗi 5 phút, trong giờ hoạt động). Để trống nếu không muốn gửi.">i</span></label>
        <textarea class="form-input" id="zc-greet" rows="3">${esc(c.greet_msg)}</textarea></div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px">
        <div class="form-group"><label class="form-label" for="zc-limit">Mời / ngày
          <span class="info-i" tabindex="0" data-tip="Tài khoản Zalo mới nên để 10–20, tài khoản lâu năm tối đa ~30–40 để tránh bị khoá tính năng kết bạn.">i</span></label>
          <input type="number" class="form-input" id="zc-limit" min="1" max="100" value="${c.daily_limit}"></div>
        <div class="form-group"><label class="form-label" for="zc-min">Nghỉ tối thiểu (giây)
          <span class="info-i" tabindex="0" data-tip="Thời gian chờ ngẫu nhiên giữa 2 lời mời, nằm giữa tối thiểu và tối đa.">i</span></label>
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
    init, login, logout, loadGroups, filterGroups, scrapeGroup, toggleEvents, scrapeEvent,
    loadContacts, filterSource, openCampaign, closeCampaign, saveCampaign,
    startCampaign, pauseCampaign, deleteCampaign, showLogs,
  };
})();
window.Zalo = Zalo;
