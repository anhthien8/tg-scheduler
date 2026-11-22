/**
 * Story photo library (Saved Messages) + autopilot — section on the 📸 Story page.
 * Thumbnails need the API key header, so they are fetched as blobs, lazily per category.
 */
const StoryLib = (() => {
  let _lib = null;
  let _accounts = [];
  let _poll = null;
  const _blobs = {};
  const el = (id) => document.getElementById(id);
  const esc = (s) => { const d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; };
  const toast = (m, t = 'info') => (window.App && App.toast ? App.toast(m, t) : console.log(m));

  async function init() {
    const box = el('st-library');
    if (!box) return;
    try {
      const [lib, acc] = await Promise.all([API.get('/api/stories/library'), API.get('/api/stories/accounts')]);
      _lib = lib;
      _accounts = acc.accounts || [];
    } catch (e) {
      box.innerHTML = `<div class="st-empty">⚠️ ${esc(e.message)}</div>`;
      return;
    }
    render();
    if (_lib.job.state !== 'idle') startPoll();
  }

  function render() {
    const { items, categories, counts, job, autopilot: ap } = _lib;
    const rejected = items.filter((i) => i.status === 'rejected');
    const errors = items.filter((i) => i.status === 'error').length;
    const pending = items.filter((i) => i.status === 'pending').length;
    const busy = job.state !== 'idle';
    const progress = busy
      ? `<span class="st-cnt on">${job.state === 'importing' ? `Đang tải ảnh từ Saved Messages... ${job.total}` : `AI đang phân loại ${job.done}/${job.total}`}</span>`
      : job.error ? `<span class="st-red st-small">⚠️ ${esc(job.error)}</span>` : '';
    const nickOpts = _accounts.map((a) => `<option value="${a.id}">${esc(a.name)}</option>`).join('');
    el('st-library').innerHTML = `
      <div class="st-comp">
        <div class="st-comp-h"><h3>🖼 Thư viện ảnh & story tự động</h3>
          <span class="info-i" tabindex="0" data-tip="Ảnh lấy từ mục Saved Messages (Tin nhắn đã lưu) của nick bạn chọn. AI tự xếp vào danh mục và LOẠI BỎ ảnh rủi ro cho tài khoản: vũ khí, tiền mặt, cờ bạc, mặt người, giấy tờ, mã QR, khoe tài sản, logo kênh khác... Ảnh bị loại không bao giờ được đăng.">i</span>
          <span style="margin-left:auto;display:flex;gap:6px;align-items:center">
            <select class="form-select" id="sl-src" style="width:auto">${nickOpts}</select>
            <button class="btn btn-sm btn-primary" ${busy ? 'disabled' : ''} onclick="StoryLib.sync()">⬇️ Lấy ảnh từ Saved Messages</button>
          </span></div>
        <div style="padding:12px 14px">
          <div style="margin-bottom:8px">${progress}
            ${!busy && (errors || pending) ? `<span class="st-muted">${errors + pending} ảnh chưa phân loại được</span> <a href="#" onclick="StoryLib.classify();return false">Phân loại lại</a>` : ''}</div>
          <div class="st-sec-t">Danh mục <span class="info-i" tabindex="0" data-tip="Tích chọn danh mục muốn dùng cho story tự động. Bấm tên danh mục để xem ảnh trong đó.">i</span></div>
          <div class="st-accs" style="max-height:none">${Object.entries(categories).map(([k, label]) => `
            <label class="st-acc"><input type="checkbox" class="sl-cat" value="${k}" ${ap.categories.includes(k) ? 'checked' : ''}>
              <a href="#" onclick="StoryLib.showCat('${k}');return false">${esc(label)}</a> <b>${counts[k] || 0}</b></label>`).join('')}</div>
          <div id="sl-grid" style="margin-top:8px"></div>
          ${rejected.length ? `<details style="margin-top:8px" ontoggle="if(this.open) StoryLib.showRejected()">
            <summary class="st-muted" style="cursor:pointer">🚫 Đã loại ${rejected.length} ảnh rủi ro</summary><div id="sl-rej" style="margin-top:6px"></div></details>` : ''}

          <div class="st-sec-t" style="margin-top:14px">Đăng tự động <span class="info-i" tabindex="0" data-tip="Mỗi ngày hệ thống tự lên lịch cho từng nick: chọn ảnh ít dùng nhất trong các danh mục đã tích, không nick nào trùng ảnh trong ngày, một nick không đăng lại ảnh cũ. AI viết caption như chủ nick tự chia sẻ. Giờ đăng ngẫu nhiên trong khung giờ. Lịch hiện ở mục 'Lịch đã tạo' bên dưới (tên bắt đầu bằng 🤖).">i</span></div>
          <div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center;font-size:.85rem">
            <label class="st-chk"><input type="checkbox" id="sl-on" ${ap.enabled ? 'checked' : ''}> Bật</label>
            <span>Mỗi nick <select class="form-select" id="sl-per" style="width:auto">${[1, 2, 3].map((n) => `<option ${ap.per_day === n ? 'selected' : ''}>${n}</option>`).join('')}</select> story/ngày</span>
            <span>từ <input type="number" id="sl-from" class="form-input" min="0" max="23" value="${ap.start_hour}" style="width:60px"> h
              đến <input type="number" id="sl-to" class="form-input" min="1" max="24" value="${ap.end_hour}" style="width:60px"> h</span>
            <span>Caption <select class="form-select" id="sl-lang" style="width:auto">${[['vi', 'Tiếng Việt'], ['en', 'English']].map(([v, l]) => `<option value="${v}" ${ap.lang === v ? 'selected' : ''}>${l}</option>`).join('')}</select></span>
            <label class="st-chk" title="Các nick nội bộ xem story của nhau"><input type="checkbox" id="sl-view" ${ap.cross_view ? 'checked' : ''}> Nick nội bộ xem chéo</label>
          </div>
          <div class="st-sec-t" style="margin-top:10px">Nick đăng</div>
          <div class="st-accs">${_accounts.map((a) => `<label class="st-acc" title="${esc(a.why)}" style="${a.ok ? '' : 'opacity:.5'}">
            <input type="checkbox" class="sl-acc" value="${a.id}" ${ap.account_ids.includes(a.id) ? 'checked' : ''}>${esc(a.name)}${a.is_premium ? ' ⭐' : ''}</label>`).join('')}</div>
          <div style="display:flex;gap:8px;align-items:center;margin-top:10px">
            <button class="btn btn-primary btn-sm" onclick="StoryLib.save()">💾 Lưu</button>
            <span class="st-muted">${ap.enabled ? `Đang bật${ap.last_planned ? ` · đã lên lịch ngày ${esc(ap.last_planned)}` : ''}` : 'Đang tắt'}</span>
          </div>
        </div>
      </div>`;
  }

  async function thumb(img) {
    const name = img.dataset.file;
    if (!_blobs[name]) {
      const r = await fetch(`/api/stories/library/file/${encodeURIComponent(name)}`, { headers: API.getHeaders() });
      if (!r.ok) return;
      _blobs[name] = URL.createObjectURL(await r.blob());
    }
    img.src = _blobs[name];
  }

  function tiles(list, withReason) {
    return `<div style="display:flex;flex-wrap:wrap;gap:6px">${list.slice(0, 60).map((i) => `
      <div style="width:84px" title="${esc(i.description || '')}">
        <img data-file="${esc(i.file)}" style="width:84px;height:120px;object-fit:cover;border-radius:6px;background:var(--bg3)">
        ${withReason ? `<div class="st-small st-red">${esc(i.reason)}</div>` : `<div class="st-small st-muted">${i.used_count ? `đã dùng ${i.used_count}` : 'chưa dùng'}</div>`}
      </div>`).join('')}${list.length > 60 ? `<span class="st-muted">+${list.length - 60} ảnh</span>` : ''}</div>`;
  }

  function fill(box, html) {
    box.innerHTML = html;
    box.querySelectorAll('img[data-file]').forEach(thumb);
  }

  function showCat(k) {
    const box = el('sl-grid');
    if (box.dataset.cat === k) { box.dataset.cat = ''; box.innerHTML = ''; return; }
    box.dataset.cat = k;
    const list = _lib.items.filter((i) => i.status === 'ok' && i.category === k);
    fill(box, list.length ? tiles(list, false) : '<div class="st-muted">Chưa có ảnh trong danh mục này.</div>');
  }

  function showRejected() {
    fill(el('sl-rej'), tiles(_lib.items.filter((i) => i.status === 'rejected'), true));
  }

  async function sync() {
    try {
      await API.post('/api/stories/library/sync', { account_id: +el('sl-src').value, limit: 300 });
      toast('Đang lấy ảnh từ Saved Messages, AI sẽ phân loại ngay sau đó', 'success');
      startPoll();
    } catch (e) { toast(e.message, 'error'); }
  }

  async function classify() {
    try {
      await API.post('/api/stories/library/classify', {});
      startPoll();
    } catch (e) { toast(e.message, 'error'); }
  }

  // Polls only while a sync/classify run is going, then stops.
  function startPoll() {
    clearInterval(_poll);
    _poll = setInterval(async () => {
      const s = await API.get('/api/stories/library/status').catch(() => null);
      if (!s) return;
      if (s.state === 'idle') {
        clearInterval(_poll);
        await init();
        if (!s.error) toast('Đã phân loại xong thư viện ảnh', 'success');
        return;
      }
      _lib.job = s;
      const busy = el('st-library').querySelector('.st-cnt.on');
      if (busy) busy.textContent = s.state === 'importing' ? `Đang tải ảnh từ Saved Messages... ${s.total}` : `AI đang phân loại ${s.done}/${s.total}`;
      else render();
    }, 4000);
  }

  async function save() {
    const vals = (sel) => [...document.querySelectorAll(sel)].filter((x) => x.checked).map((x) => x.value);
    const body = {
      enabled: el('sl-on').checked,
      categories: vals('.sl-cat'),
      account_ids: vals('.sl-acc').map(Number),
      per_day: +el('sl-per').value,
      start_hour: +el('sl-from').value,
      end_hour: +el('sl-to').value,
      lang: el('sl-lang').value,
      cross_view: el('sl-view').checked,
    };
    try {
      const r = await API.put('/api/stories/autopilot', body);
      toast(body.enabled ? `Đã lưu · lên lịch ${r.planned} story cho hôm nay` : `Đã tắt · huỷ ${r.cancelled || 0} story chưa đăng`, 'success');
      init();
      if (window.Stories && Stories.loadJobs) Stories.loadJobs(true);
    } catch (e) { toast(e.message, 'error'); }
  }

  return { init, sync, classify, showCat, showRejected, save };
})();
window.StoryLib = StoryLib;
