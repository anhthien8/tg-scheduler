/**
 * Task center (v3.40): sidebar "🔔 Việc đang chạy" button + slide-out panel listing every
 * background job the user started (DM, story, warmup, cào, deep crawl, Zalo, lịch gửi...).
 *
 * Light by design: one GET /api/tasks/summary (server-cached 30 s) through the shared
 * Polling timer — every 2 min while something is running or scheduled, every 10 min when
 * idle, paused while the tab is hidden. Opening the panel refreshes right away.
 * When a job moves from running/scheduled to done or needs attention, a toast pops up.
 */
const TaskCenter = (function () {
  const KEY = 'tasks:summary';
  const ACTIVE_MS = 120000, IDLE_MS = 600000;
  const SEEN_KEY = 'tc_seen_v1';
  const BUCKETS = [
    ['warn', '⚠️ Cần xem', 'Việc bị lỗi, tự dừng hoặc quá giờ. Bấm để mở trang của việc đó.'],
    ['run', '🔄 Đang chạy', ''],
    ['plan', '⏰ Đã hẹn / đang chờ', 'Việc đã hẹn giờ, đang xếp hàng hoặc chờ nick mở khoá.'],
    ['done', '✅ Xong trong 24 giờ', ''],
  ];
  let data = null, prev = null, interval = 0, open = false, loading = false;

  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const seen = () => { try { return JSON.parse(localStorage.getItem(SEEN_KEY) || '{}'); } catch (e) { return {}; } };
  const sig = t => `${t.key}|${t.status}`;

  function hm(ts) {
    const d = new Date(ts * 1000), n = new Date();
    const t = d.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' });
    if (d.toDateString() === n.toDateString()) return t;
    const tm = new Date(n.getTime() + 864e5);
    return d.toDateString() === tm.toDateString() ? `${t} mai` : `${t} ${d.getDate()}/${d.getMonth() + 1}`;
  }
  function rel(ts) {
    const m = Math.round((ts * 1000 - Date.now()) / 60000), a = Math.abs(m);
    const s = a < 60 ? a + '′' : a < 1440 ? Math.round(a / 60) + ' giờ' : Math.round(a / 1440) + ' ngày';
    return m > 0 ? 'còn ' + s : a < 1 ? 'vừa xong' : s + ' trước';
  }

  function ensureDom() {
    if ($('tc-panel')) return;
    const ov = document.createElement('div');
    ov.id = 'tc-overlay'; ov.className = 'tc-overlay'; ov.onclick = () => toggle(false);
    const p = document.createElement('aside');
    p.id = 'tc-panel'; p.className = 'tc-panel'; p.setAttribute('role', 'dialog'); p.setAttribute('aria-label', 'Việc đang chạy');
    p.innerHTML = `
      <div class="tc-head">
        <strong>🔔 Việc đang chạy</strong>
        <span class="info-i tc-tip" tabindex="0" data-tip="Tất cả việc bạn đã giao cho hệ thống: chiến dịch DM, story, warmup, cào thành viên, deep crawl, Zalo, lịch gửi 1 lần... Keyword DM và Cộng đồng chỉ hiện khi tự dừng. Tự cập nhật 2 phút/lần khi có việc đang chạy, 10 phút/lần khi rảnh, dừng khi bạn chuyển tab. Bấm 1 dòng để mở trang của việc đó.">i</span>
        <button type="button" class="btn btn-sm tc-refresh" onclick="TaskCenter.refresh(true)" title="Làm mới ngay">🔄</button>
        <button type="button" class="tc-x" onclick="TaskCenter.toggle(false)" aria-label="Đóng">×</button>
      </div>
      <div id="tc-meta" class="tc-meta"></div>
      <div id="tc-body" class="tc-body"><div class="tc-empty">Đang tải...</div></div>`;
    document.body.append(ov, p);
    document.addEventListener('keydown', e => { if (open && e.key === 'Escape') toggle(false); });
  }

  function rowHtml(t) {
    const pct = t.total ? Math.min(100, Math.round((t.done || 0) / t.total * 100)) : null;
    let when = '';
    if (t.bucket === 'plan' && t.ts_scheduled) when = `🕒 ${hm(t.ts_scheduled)} · ${rel(t.ts_scheduled)}`;
    else if (t.bucket === 'run' && t.ts_scheduled && t.ts_scheduled * 1000 > Date.now()) when = `tiếp theo ${hm(t.ts_scheduled)}`;
    else if (t.ts) when = rel(t.ts);
    return `<button type="button" class="tc-row tc-${t.bucket}" data-page="${esc(t.page)}" title="Mở trang ${esc(t.kind_label)}">
      <span class="tc-ic">${t.icon}</span>
      <span class="tc-main">
        <span class="tc-name">${esc(t.name)}</span>
        <span class="tc-sub">${esc(t.kind_label)} · ${esc(t.label)}${when ? ` · ${esc(when)}` : ''}</span>
        ${pct !== null && t.bucket !== 'plan' ? `<span class="tc-bar"><b style="width:${pct}%"></b></span>` : ''}
        ${t.note && t.bucket === 'warn' ? `<span class="tc-note">${esc(t.note)}</span>` : ''}
      </span></button>`;
  }

  function render() {
    if (!data) return;
    const c = data.counts || {};
    const s = seen(), unseenWarn = data.tasks.some(t => t.bucket === 'warn' && !s[sig(t)]);
    const unseenDone = data.tasks.some(t => t.bucket === 'done' && !s[sig(t)]);
    const n = $('tc-run'), dot = $('tc-dot'), btn = $('tc-btn');
    if (n) { n.textContent = c.run > 99 ? '99+' : String(c.run || 0); n.classList.toggle('hidden', !c.run); }
    if (dot) { dot.classList.toggle('hidden', !(unseenWarn || unseenDone)); dot.classList.toggle('warn', unseenWarn); }
    if (btn) btn.title = `${c.run || 0} việc đang chạy · ${c.plan || 0} đã hẹn · ${c.warn || 0} cần xem · ${c.done || 0} xong trong 24 giờ`;
    if (!open) return;
    $('tc-meta').textContent = `Cập nhật lúc ${hm(data.generated_at)} · ${c.run || 0} đang chạy · ${c.plan || 0} đã hẹn · ${c.warn || 0} cần xem`;
    const by = b => data.tasks.filter(t => t.bucket === b)
      .sort((x, y) => b === 'plan' ? (x.ts_scheduled || 9e9) - (y.ts_scheduled || 9e9) : (y.ts || 0) - (x.ts || 0));
    const html = BUCKETS.map(([b, title, tip]) => {
      const rows = by(b);
      if (!rows.length) return '';
      return `<section class="tc-sec"><div class="tc-sec-t">${title} <span>${rows.length}</span>${tip ? ` <span class="info-i tc-tip" tabindex="0" data-tip="${esc(tip)}">i</span>` : ''}</div>${rows.map(rowHtml).join('')}</section>`;
    }).join('');
    $('tc-body').innerHTML = html || '<div class="tc-empty">Không có việc nào đang chạy hay xong trong 24 giờ qua.</div>';
  }

  function markSeen() {
    if (!data) return;
    const s = {};
    data.tasks.filter(t => t.bucket === 'warn' || t.bucket === 'done').forEach(t => { s[sig(t)] = 1; });
    try { localStorage.setItem(SEEN_KEY, JSON.stringify(s)); } catch (e) { /* storage full / private mode */ }
  }

  // Toast once when a job we saw running/scheduled finishes or needs attention.
  function notify(next) {
    if (!prev || typeof App === 'undefined' || !App.toast) return;
    const was = new Map(prev.tasks.map(t => [t.key, t]));
    const msgs = [];
    next.tasks.forEach(t => {
      const p = was.get(t.key);
      const fromActive = p ? (p.bucket === 'run' || p.bucket === 'plan') : false;
      if (t.bucket === 'warn' && (!p || p.status !== t.status)) msgs.push([`⚠️ ${t.kind_label} “${t.name}”: ${t.label}`, 'error']);
      else if (t.bucket === 'done' && fromActive) msgs.push([`✅ ${t.kind_label} “${t.name}”: ${t.label}`, 'success']);
    });
    msgs.slice(0, 3).forEach(([m, k]) => App.toast(m, k));
    if (msgs.length > 3) App.toast(`+${msgs.length - 3} việc khác vừa đổi trạng thái — xem 🔔 Việc đang chạy`, 'info');
  }

  async function refresh(fresh) {
    if (loading || typeof API === 'undefined') return;
    loading = true;
    try {
      const next = await API.get('/api/tasks/summary' + (fresh ? '?fresh=1' : ''));
      notify(next);
      prev = data = next;
      if (open) markSeen();
      render();
      const want = (next.counts.run || next.counts.plan) ? ACTIVE_MS : IDLE_MS;
      if (want !== interval && typeof Polling !== 'undefined') { interval = want; Polling.start(KEY, () => refresh(false), want); }
      if (fresh && open && typeof App !== 'undefined') App.toast('Đã làm mới', 'info');
    } catch (e) {
      // not logged in yet / server down: stay quiet, the next tick retries
      if (open && $('tc-body') && !data) $('tc-body').innerHTML = `<div class="tc-empty">${esc(e.message || 'Không tải được')}</div>`;
    } finally { loading = false; }
  }

  function toggle(on) {
    ensureDom();
    open = on === undefined ? !open : !!on;
    $('tc-panel').classList.toggle('open', open);
    $('tc-overlay').classList.toggle('open', open);
    $('tc-btn')?.setAttribute('aria-expanded', String(open));
    if (open) { markSeen(); render(); refresh(false); }
    else render();
  }

  function init() {
    ensureDom();
    $('tc-body').addEventListener('click', e => {
      const r = e.target.closest('.tc-row');
      if (!r || e.target.closest('.info-i')) return;
      toggle(false);
      if (typeof App !== 'undefined' && App.navigate) App.navigate(r.dataset.page);
    });
    setTimeout(() => refresh(false), 3000);   // after the dashboard's own first loads
    if (typeof Polling !== 'undefined') { interval = IDLE_MS; Polling.start(KEY, () => refresh(false), IDLE_MS); }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
  return { refresh, toggle };
})();
window.TaskCenter = TaskCenter;
