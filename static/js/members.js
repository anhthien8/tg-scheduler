var me_dummy = me_dummy || {}; var me_dummy_style = me_dummy_style || {};
/**
 * Members Module — Scraping + DM Campaign frontend logic.
 * Loaded BEFORE app.js so App can call Members.init() / Members.populateAccounts()
 */
const Members = {
  _scrapeJobs: [],
  _campaigns: [],
  _inviteCampaigns: [],
  _accounts: [],
  _groupsCache: {},
  _lastCampaignsUpdate: null,
  _lastInviteCampaignsUpdate: null,
  _deepCrawlPollInterval: 3000,
  _deepCrawlPrevState: null,
  _editingInviteCampaignId: null,
  _membersDetailCache: {},   // jobId -> members[] (tránh fetch lại khi đổi trang)
  _membersDetailPage: 1,     // trang hiện tại của modal members
  _membersDetailCurrent: null, // members[] đang hiển thị (đã lọc) cho job hiện tại
  _membersDetailSearch: '',
  _campaignLogsCache: {},    // campaignId -> logs[]
  _campaignLogsPage: 1,
  _membersDetailPerPage: 50,
  _campaignLogsPerPage: 50,

  // ── Init: load data when navigating to members page ──
  async init() {
    this._lastCampaignsUpdate = null;
    this._lastInviteCampaignsUpdate = null;
    Polling.stop('members:campaigns');
    Polling.stop('members:inviteCampaigns');
    await Promise.all([
      this.loadScrapeJobs(),
      this.loadCampaigns(),
      this.loadInviteCampaigns(),
    ]);
    if (this._campaigns.some(c => c.status === 'running')) {
      this._pollCampaign();
    }
    if (this._inviteCampaigns.some(c => c.status === 'running')) {
      this._pollInviteCampaign();
    }

    // Restore deep crawl polling and UI state on page refresh if active or has pending queue/results
    if (!this._deepCrawlPolling) {
      this.restoreDeepCrawlState();
    }
  },

  // ── Account dropdown populate ──
  async populateAccounts() {
    const now = Date.now();
    if (!this._accounts?.length || !this._accountsCachedAt || (now - this._accountsCachedAt) > 30000) {
      try {
        const d = await API.getAccounts();
        this._accounts = d.accounts || [];
        this._accountsCachedAt = now;
      } catch (e) { this._accounts = []; }
    }
    const sel = document.getElementById('ms-account-select');
    if (!sel) return;
    const accounts = this._accounts;
    sel.innerHTML = accounts.map(a => {
      const ui = a.user_info;
      const name = ui ? [ui.first_name, ui.last_name].filter(Boolean).join(' ') : a.name;
      const uname = ui && ui.username ? '@' + ui.username : (a.phone || '');
      const label = name ? `${name} (${uname})` : (uname || `ID ${a.id}`);
      return `<option value="${a.id}">${esc(label)}</option>`;
    }).join('');
    if (accounts.length > 0 && !sel.value) sel.value = accounts[0].id;
    // ponytail: lazy load groups khi user click thay vì gọi get_dialogs RPC (2+ giây) mỗi khi navigate
    const groupSel = document.getElementById('ms-group-select');
    if (groupSel && !this._groupsCache[sel.value]) {
      groupSel.innerHTML = '<option value="">— Bấm để tải danh sách group —</option>';
      groupSel.onfocus = () => { if (!this._groupsCache[sel.value]) this.loadGroups(); };
    }
  },

  // ── Load groups for selected account ──
  async loadGroups() {
    const sel = document.getElementById('ms-account-select');
    const groupSel = document.getElementById('ms-group-select');
    if (!sel || !groupSel) return;
    const accountId = parseInt(sel.value);
    if (!accountId) return;

    // Use cache if available
    if (this._groupsCache[accountId]) {
      this._renderGroupOptions(this._groupsCache[accountId]);
      return;
    }

    groupSel.innerHTML = '<option value="">Đang tải...</option>';
    try {
      const d = await API.getChats(accountId);
      const chats = Array.isArray(d?.chats)
        ? d.chats
        : (d?.command_id && typeof API.pollCommand === 'function'
            ? ((await API.pollCommand(d.command_id))?.result?.chats || [])
            : []);
      const groups = chats.filter(c =>
        c.chat_type === 'group' || c.chat_type === 'supergroup' || c.chat_type === 'megagroup'
      );
      this._groupsCache[accountId] = groups;
      this._renderGroupOptions(groups);
    } catch (e) {
      groupSel.innerHTML = '<option value="">Lỗi tải groups</option>';
    }
  },

  _renderGroupOptions(groups) {
    const sel = document.getElementById('ms-group-select');
    if (!sel) return;
    sel.innerHTML = '<option value="">— Chọn group —</option>' +
      groups.map(g => `<option value="${g.chat_id}" data-title="${esc(g.chat_title || '')}">${esc(g.chat_title || g.chat_id)} (${g.participants_count || '?'} members)</option>`).join('');
  },

  // ── Start Scrape ──
  async startScrape() {
    const accountId = parseInt(document.getElementById('ms-account-select')?.value);
    const groupSel = document.getElementById('ms-group-select');
    const groupId = parseInt(groupSel.value);
    const groupTitle = groupSel.options[groupSel.selectedIndex]?.text || '';
    const filterDays = document.getElementById('ms-filter-active')?.value;
    const scrapeMethod = document.getElementById('ms-scrape-method')?.value;
    const maxMessages = parseInt(document.getElementById('ms-max-messages')?.value);

    if (!accountId || !groupId) {
      App.toast('Chọn tài khoản và group trước', 'error');
      return;
    }

    const btn = document.getElementById('ms-btn-scrape');
    btn.disabled = true;
    btn.textContent = '⏳ Đang cào...';

    try {
      const r = await MembersAPI.startScrape({
        account_id: accountId,
        group_id: groupId,
        group_title: groupTitle.replace(/\s*\(.*?\)\s*$/, ''),
        filter_active_days: filterDays ? parseInt(filterDays) : null,
        exclude_bots: true,
        scrape_method: scrapeMethod,
        max_messages: maxMessages,
      });
      App.toast(r.message || 'Đã bắt đầu cào!', 'success');
      // Poll for results after a few seconds
      setTimeout(() => this.loadScrapeJobs(), 5000);
      setTimeout(() => this.loadScrapeJobs(), 15000);
      setTimeout(() => this.loadScrapeJobs(), 30000);
    } catch (e) {
      App.toast(e.message || 'Lỗi cào members', 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = '🔍 Bắt đầu cào';
    }
  },

  onScrapeMethodChange() {
    const method = document.getElementById('ms-scrape-method')?.value;
    const msgGroup = document.getElementById('ms-max-messages-group');
    if (method === 'history') {
      msgGroup.classList.remove('hidden');
    } else {
      msgGroup.classList.add('hidden');
    }
  },

  // ── Scrape Mode Tab Switching ──
  switchScrapeMode(mode) {
    document.querySelectorAll('.ms-scrape-mode-tab').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.mode === mode);
      if (btn.dataset.mode !== mode) btn.classList.add('btn-ghost');
      else btn.classList.remove('btn-ghost');
    });
    const singleMode = document.getElementById('ms-scrape-single-mode');
    const batchMode = document.getElementById('ms-scrape-batch-mode');
    if (mode === 'batch') {
      singleMode.classList.add('hidden');
      batchMode.classList.remove('hidden');
      // Sync account dropdown
      const batchAccSel = document.getElementById('ms-batch-account');
      const mainAccSel = document.getElementById('ms-account-select');
      if (batchAccSel && mainAccSel) batchAccSel.innerHTML = mainAccSel.innerHTML;
    } else {
      singleMode.classList.remove('hidden');
      batchMode.classList.add('hidden');
    }
  },

  // ── Resolve Channel Links ──
  async resolveChannelLinks() {
    const textarea = document.getElementById('ms-batch-links');
    const accountId = parseInt(document.getElementById('ms-batch-account')?.value);
    if (!textarea || !accountId) {
      App.toast('Chọn tài khoản và nhập link channel', 'error');
      return;
    }

    const lines = textarea.value.split('\n').map(l => l.trim()).filter(Boolean);
    if (!lines.length) {
      App.toast('Nhập ít nhất 1 link channel', 'error');
      return;
    }

    const btn = document.getElementById('ms-btn-resolve');
    btn.disabled = true;
    btn.textContent = '⏳ Đang kiểm tra...';

    try {
      const r = await MembersAPI.resolveChannels({
        account_id: accountId,
        channels: lines,
      });

      const results = r.results || [];
      this._batchResolvedChannels = results;

      const previewDiv = document.getElementById('ms-batch-preview');
      const tbody = document.getElementById('ms-batch-preview-tbody');
      previewDiv.classList.remove('hidden');

      tbody.innerHTML = results.map((ch, i) => {
        const statusBadge = ch.success
          ? '<span class="badge badge-green">✅ OK</span>'
          : `<span class="badge" style="background:var(--danger-bg);color:var(--danger)">❌ ${esc(ch.error || 'Lỗi')}</span>`;
        return `<tr>
          <td>${i + 1}</td>
          <td style="font-family:monospace;font-size:12px">${esc(ch.username || ch.input)}</td>
          <td>${ch.success ? esc(ch.title || '') : '—'}</td>
          <td>${ch.success ? `<span class="badge badge-blue">${ch.participants_count || '?'}</span>` : '—'}</td>
          <td>${statusBadge}</td>
        </tr>`;
      }).join('');

      // Enable batch scrape if at least 1 resolved
      const hasValid = results.some(r => r.success);
      (document.getElementById('ms-btn-batch-scrape') || me_dummy).disabled = !hasValid;

      if (hasValid) {
        const validCount = results.filter(r => r.success).length;
        App.toast(`Đã xác minh ${validCount}/${results.length} channel`, 'success');
      } else {
        App.toast('Không có channel nào hợp lệ', 'error');
      }
    } catch (e) {
      App.toast(e.message || 'Lỗi kiểm tra channels', 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = '🔍 Kiểm tra';
    }
  },

  // ── Start Batch Scrape ──
  async startBatchScrape() {
    const accountId = parseInt(document.getElementById('ms-batch-account')?.value);
    const textarea = document.getElementById('ms-batch-links');
    const method = document.getElementById('ms-batch-method')?.value;
    const filterDays = document.getElementById('ms-batch-filter')?.value;

    const lines = textarea.value.split('\n').map(l => l.trim()).filter(Boolean);
    if (!lines.length || !accountId) {
      App.toast('Nhập link channel và chọn tài khoản', 'error');
      return;
    }

    const btn = document.getElementById('ms-btn-batch-scrape');
    btn.disabled = true;
    btn.textContent = '⏳ Đang khởi tạo...';

    try {
      const r = await MembersAPI.batchScrape({
        account_id: accountId,
        channels: lines,
        filter_active_days: filterDays ? parseInt(filterDays) : null,
        exclude_bots: true,
        scrape_method: method,
      });

      App.toast(r.message || 'Đã bắt đầu cào hàng loạt!', 'success');

      // Show progress panel
      (document.getElementById('ms-batch-progress')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('hidden');
      (document.getElementById('ms-batch-preview')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');

      // Start polling
      this._pollBatchProgress(r.batch_job_id);
    } catch (e) {
      App.toast(e.message || 'Lỗi bắt đầu cào', 'error');
      btn.disabled = false;
      btn.textContent = '🚀 Bắt đầu cào hàng loạt';
    }
  },

  // ── Poll Batch Progress ──
  async _pollBatchProgress(batchJobId) {
    let finished = false;
    const poll = async () => {
      try {
        const r = await MembersAPI.getBatchProgress(batchJobId);

        // Update stats badges
        (document.getElementById('ms-batch-stat-total') || me_dummy).textContent = `${r.total_channels} channels`;
        (document.getElementById('ms-batch-stat-done') || me_dummy).textContent = `${r.done} xong`;
        (document.getElementById('ms-batch-stat-running') || me_dummy).textContent = `${r.running} đang chạy`;
        (document.getElementById('ms-batch-stat-errors') || me_dummy).textContent = `${r.errors} lỗi`;
        (document.getElementById('ms-batch-stat-members') || me_dummy).textContent = r.total_members;

        // Update progress table
        const tbody = document.getElementById('ms-batch-progress-tbody');
        tbody.innerHTML = (r.channels || []).map((ch, i) => {
          let statusBadge;
          switch (ch.status) {
            case 'done':
              statusBadge = '<span class="badge badge-green">✅ Xong</span>'; break;
            case 'running':
              statusBadge = '<span class="badge badge-yellow">⏳ Đang cào...</span>'; break;
            case 'error':
              statusBadge = '<span class="badge" style="background:var(--danger-bg);color:var(--danger)">❌ Lỗi</span>'; break;
            default:
              statusBadge = '<span class="badge">⏸ Chờ</span>';
          }
          return `<tr>
            <td>${i + 1}</td>
            <td>${esc(ch.channel_title || ch.channel_username)}</td>
            <td>${statusBadge}</td>
            <td>${ch.member_count > 0 ? `<span class="badge badge-blue">${ch.member_count}</span>` : '—'}</td>
            <td style="font-size:12px;color:var(--text2)">${ch.error_message ? esc(ch.error_message).substring(0, 50) : '—'}</td>
          </tr>`;
        }).join('');

        // Stop polling when done
        if (r.status === 'done') {
          finished = true;
          Polling.stop('members:batchProgress');
          const btn = document.getElementById('ms-btn-batch-scrape');
          btn.disabled = false;
          btn.textContent = '🚀 Bắt đầu cào hàng loạt';
          App.toast(`Hoàn tất! ${r.total_members} members từ ${r.done} channels (đã loại trùng)`, 'success');
          // Reload scrape jobs to show the new batch job
          this.loadScrapeJobs();
        }
      } catch (e) {
        console.error('Batch poll error:', e);
      }
    };

    // Poll immediately then every 5 seconds
    await poll();
    if (!finished) Polling.start('members:batchProgress', poll, 5000);
  },

  // ── Load Scrape Jobs ──
  async loadScrapeJobs() {
    try {
      const d = await MembersAPI.getScrapeJobs();
      this._scrapeJobs = d.jobs || [];
      this._renderScrapeJobs();
    } catch (e) {
      console.error('Load scrape jobs error:', e);
    }
  },

  _renderScrapeJobs() {
    const tbody = document.getElementById('ms-jobs-tbody');
    const empty = document.getElementById('ms-jobs-empty');
    if (!tbody) return;

    const jobs = this._scrapeJobs;
    (document.getElementById('ms-total-jobs') || me_dummy).textContent = jobs.length;
    (document.getElementById('ms-total-members') || me_dummy).textContent = 
      jobs.reduce((sum, j) => sum + (j.member_count || 0), 0);

    if (!jobs.length) {
      tbody.innerHTML = '';
      if (empty) empty.classList.remove('hidden');
      return;
    }
    if (empty) empty.classList.add('hidden');

    // Diff render theo scrape_job_id: polling gọi hàm này liên tục, chỉ ghi lại
    // dòng khi dữ liệu thật sự đổi — tránh dựng lại toàn bộ DOM mỗi lần.
    const idsInState = new Set(jobs.map(j => String(j.scrape_job_id)));
    Array.from(tbody.children).forEach(row => {
      const id = row.getAttribute('data-id');
      if (id && !idsInState.has(id)) row.remove();
    });

    jobs.forEach((j, i) => {
      const date = j.scraped_at ? new Date(j.scraped_at + 'Z').toLocaleString('vi-VN') : '—';
      const rowHtml = `
        <td>${i + 1}</td>
        <td>${esc(j.group_title || j.group_id)}</td>
        <td><span class="badge badge-blue">${j.member_count}</span></td>
        <td style="font-size:12px;color:var(--text2)">${date}</td>
        <td>
          <div class="btn-group">
            <button class="btn btn-ghost btn-sm" onclick="Members.viewMembers('${esc(j.scrape_job_id)}','${esc(j.group_title || '')}')" title="Xem">👁 Xem</button>
            <button class="btn btn-ghost btn-sm" onclick="Members.renameScrapeJob('${esc(j.scrape_job_id)}')" title="Đổi tên hiển thị">✏️</button>
            <button class="btn btn-ghost btn-sm" onclick="window.open(AnalyticsAPI.exportMembers('${esc(j.scrape_job_id)}'),'_blank')" title="Export CSV">📥</button>
            <button class="btn btn-danger btn-sm" onclick="Members.deleteScrapeJob('${esc(j.scrape_job_id)}')">🗑</button>
          </div>
        </td>`;

      const key = String(j.scrape_job_id);
      let existingRow = tbody.querySelector(`tr[data-id="${key}"]`);
      if (existingRow) {
        const changed =
          parseInt(existingRow.getAttribute('data-index')) !== (i + 1) ||
          existingRow.getAttribute('data-count') !== String(j.member_count) ||
          existingRow.getAttribute('data-scraped-at') !== String(j.scraped_at || '') ||
          existingRow.getAttribute('data-title') !== String(j.group_title || '');
        if (changed) {
          existingRow.innerHTML = rowHtml;
          existingRow.setAttribute('data-index', i + 1);
          existingRow.setAttribute('data-count', j.member_count);
          existingRow.setAttribute('data-scraped-at', j.scraped_at || '');
          existingRow.setAttribute('data-title', j.group_title || '');
        }
      } else {
        const tr = document.createElement('tr');
        tr.setAttribute('data-id', key);
        tr.setAttribute('data-index', i + 1);
        tr.setAttribute('data-count', j.member_count);
        tr.setAttribute('data-scraped-at', j.scraped_at || '');
        tr.setAttribute('data-title', j.group_title || '');
        tr.innerHTML = rowHtml;
        if (i >= tbody.children.length) tbody.appendChild(tr);
        else tbody.insertBefore(tr, tbody.children[i]);
      }
    });
    // Dọn dòng thừa nếu state co lại nhanh hơn DOM (an toàn khi xóa/xếp lại)
    while (tbody.children.length > jobs.length) tbody.lastElementChild.remove();
  },

  // ── View Members Detail (phân trang client, 50 dòng/trang) ──
  async viewMembers(jobId, title) {
    (document.getElementById('members-detail-title') || me_dummy).textContent = `Members: ${title || jobId}`;
    const tbody = document.getElementById('members-detail-tbody');
    const pager = document.getElementById('members-detail-pager');
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center">⏳ Đang tải...</td></tr>';
    if (pager) pager.innerHTML = '';
    (document.getElementById('members-detail-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('open');

    this._membersDetailPage = 1;
    this._membersDetailSearch = '';
    const searchEl = document.getElementById('members-detail-search');
    if (searchEl) searchEl.value = '';
    // Dùng cache nếu đã tải job này trước đó — không fetch lại khi mở lại
    let members = this._membersDetailCache[jobId];
    if (!members) {
      try {
        const d = await MembersAPI.getScrapeMembers(jobId, 500);
        members = d.members || [];
        this._membersDetailCache[jobId] = members;
      } catch (e) {
        tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--danger)">${esc(e.message)}</td></tr>`;
        return;
      }
    }
    this._membersDetailCurrent = members;   // giữ full list để lọc
    this._renderMembersDetail();
  },

  // Lọc theo tên / @username / user_id, không phân biệt hoa thường.
  _filterMembersDetail() {
    const all = this._membersDetailCurrent || [];
    const q = (this._membersDetailSearch || '').trim().toLowerCase().replace(/^@/, '');
    if (!q) return all;
    return all.filter(m => {
      const name = [m.first_name, m.last_name].filter(Boolean).join(' ').toLowerCase();
      const uname = (m.username || '').toLowerCase();
      const uid = String(m.user_id || '');
      return name.includes(q) || uname.includes(q) || uid.includes(q);
    });
  },

  onDetailSearch(value) {
    this._membersDetailSearch = value || '';
    this._membersDetailPage = 1;
    this._renderMembersDetail();
  },

  // Render 1 trang members (đã lọc) + pager. Bấm trang/ô search chỉ re-render, không fetch lại.
  _renderMembersDetail() {
    const tbody = document.getElementById('members-detail-tbody');
    const pager = document.getElementById('members-detail-pager');
    const countEl = document.getElementById('members-detail-count');
    if (!tbody) return;
    const all = this._membersDetailCurrent || [];
    const members = this._filterMembersDetail();
    if (countEl) countEl.textContent = members.length === all.length ? `${all.length} members` : `${members.length}/${all.length} members`;
    if (!members.length) {
      const msg = all.length ? 'Không tìm thấy member nào khớp' : 'Không có members';
      tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--text2)">${msg}</td></tr>`;
      if (pager) pager.innerHTML = '';
      return;
    }
    const st = paginate(members, this._membersDetailPage, this._membersDetailPerPage);
    this._membersDetailPage = st.page;
    const offset = st.start;
    tbody.innerHTML = st.slice.map((m, i) => {
      const name = [m.first_name, m.last_name].filter(Boolean).join(' ') || '—';
      const ls = m.last_seen || '—';
      return `<tr>
        <td>${offset + i + 1}</td>
        <td style="font-size:12px">${m.user_id}</td>
        <td>${m.username ? '@' + esc(m.username) : '<span style="color:var(--text2)">—</span>'}</td>
        <td>${esc(name)}</td>
        <td>${m.is_premium ? '<span class="badge badge-green">⭐</span>' : '—'}</td>
        <td style="font-size:12px;color:var(--text2)">${esc(ls)}</td>
      </tr>`;
    }).join('');
    renderPager(pager, st, (p) => { this._membersDetailPage = p; this._renderMembersDetail(); });
  },

  closeDetailModal() {
    (document.getElementById('members-detail-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('open');
  },

  // ── Delete Scrape Job ──
  // Old Deep Crawl lists were titled "Deep Crawl Contacts (deep_https___t_me_xxx)" → suggest "xxx · Deep Crawl"
  _friendlyJobTitle(j) {
    const t = String(j.group_title || j.scrape_job_id || '');
    const m = t.match(/^Deep Crawl Contacts \((.*)\)(.*)$/);
    if (!m) return t;
    let inner = m[1].replace(/^deep_https?___t_me_/i, '').replace(/^deep_/i, '');
    return `${inner} · Deep Crawl${m[2] ? ' ' + m[2].trim() : ''}`.trim();
  },

  async renameScrapeJob(jobId) {
    const j = (this._scrapeJobs || []).find(x => x.scrape_job_id === jobId);
    if (!j) return;
    const name = prompt('Tên hiển thị mới cho danh sách:', this._friendlyJobTitle(j));
    if (!name || !name.trim() || name.trim() === j.group_title) return;
    try {
      const r = await fetch(`/api/members/scrape-jobs/${encodeURIComponent(jobId)}/rename`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ group_title: name.trim() }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || 'Không đổi tên được');
      App.toast('✏️ Đã đổi tên danh sách', 'success');
      this.loadScrapeJobs();
    } catch (e) { App.toast(e.message, 'error'); }
  },

  async tidyScrapeJobTitles() {
    const todo = (this._scrapeJobs || []).filter(j => this._friendlyJobTitle(j) !== (j.group_title || ''));
    if (!todo.length) { App.toast('Tên các danh sách đã gọn rồi', 'info'); return; }
    if (!confirm(`Đổi tên ${todo.length} danh sách Deep Crawl cũ cho dễ đọc?\n\n` +
      todo.slice(0, 6).map(j => `• ${j.group_title} → ${this._friendlyJobTitle(j)}`).join('\n') +
      (todo.length > 6 ? `\n… và ${todo.length - 6} danh sách khác` : ''))) return;
    let ok = 0;
    for (const j of todo) {
      const r = await fetch(`/api/members/scrape-jobs/${encodeURIComponent(j.scrape_job_id)}/rename`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ group_title: this._friendlyJobTitle(j) }),
      }).catch(() => null);
      if (r && r.ok) ok++;
    }
    App.toast(`✏️ Đã đổi tên ${ok}/${todo.length} danh sách`, 'success');
    this.loadScrapeJobs();
  },

  // ══ Danh bạ KOL: channels + contacts + trading score, for ad booking / export ══
  _kol: { q: '', status: '', minScore: 0, hasContact: 1, sort: 'score', page: 0, limit: 50, kind: '', country: '' },
  _KOL_KINDS: {
    futures: ['📈 Futures / Trading', 'var(--green)'], news: ['📰 Tin tức', 'var(--orange)'],
    airdrop: ['🪂 Airdrop / Bonus', 'var(--text3)'], other: ['💬 Khác', 'var(--text3)'],
  },
  _KOL_STATUS: {
    new: ['Mới', 'var(--text3)'], contacted: ['Đã liên hệ', 'var(--accent)'],
    negotiating: ['Đang thương lượng', 'var(--orange)'], booked: ['Đã book', 'var(--green)'],
    rejected: ['Từ chối', 'var(--red)'],
  },

  _kolQuery(extra = {}) {
    const k = { ...this._kol, ...extra };
    const p = new URLSearchParams({ q: k.q, status: k.status, min_score: k.minScore || 0,
      has_contact: k.hasContact ? 1 : 0, sort: k.sort, page: k.page, limit: k.limit,
      kind: k.kind || '', country: k.country || '' });
    return p.toString();
  },

  kolSet(field, value) {
    this._kol[field] = value;
    if (field !== 'page') this._kol.page = 0;
    clearTimeout(this._kolTimer);
    this._kolTimer = setTimeout(() => this.loadKolDirectory(), field === 'q' ? 300 : 0);
  },

  async loadKolDirectory() {
    const tbody = document.getElementById('kol-tbody');
    if (!tbody) return;
    try {
      const r = await fetch(`/api/members/kol-directory?${this._kolQuery()}`);
      if (!r.ok) throw new Error('Không tải được danh bạ');
      const d = await r.json();
      this._kolItems = d.items || [];
      if (!d.total && !this._kol.q && !this._kol.status && !this._kolAutoSynced) {
        // First visit: fill the directory from the current Deep Crawl result once
        this._kolAutoSynced = true;
        const s = await fetch('/api/members/kol-directory/sync', { method: 'POST' }).then(x => x.json()).catch(() => null);
        if (s && s.count) return this.loadKolDirectory();
      }
      this._renderKol(d);
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="9" style="color:var(--red)">${esc(e.message)}</td></tr>`;
    }
  },

  _renderKol(d) {
    const tbody = document.getElementById('kol-tbody');
    const items = d.items || [];
    const counts = d.counts || {};
    const all = Object.values(counts).reduce((a, b) => a + b, 0);
    const chips = document.getElementById('kol-status-chips');
    if (chips) {
      const chip = (key, label, n, col) => `<button class="tab-btn btn-sm ${this._kol.status === key ? 'active' : ''}" style="border-left:3px solid ${col}" onclick="Members.kolSet('status','${key}')">${label} (${n || 0})</button>`;
      chips.innerHTML = chip('', 'Tất cả', all, 'var(--text3)') +
        Object.entries(this._KOL_STATUS).map(([k, [l, c]]) => chip(k, l, counts[k], c)).join('');
    }
    const kc = d.kind_counts || {};
    const kindEl = document.getElementById('kol-kind-chips');
    if (kindEl) {
      const kAll = Object.values(kc).reduce((a, b) => a + b, 0);
      kindEl.innerHTML = `<button class="kol-chip ${!this._kol.kind ? 'active' : ''}" onclick="Members.kolSet('kind','')">Tất cả <b>${kAll}</b></button>` +
        Object.entries(this._KOL_KINDS).map(([k, [l, c]]) =>
          `<button class="kol-chip ${this._kol.kind === k ? 'active' : ''}" style="--chip:${c}" onclick="Members.kolSet('kind','${k}')">${l} <b>${kc[k] || 0}</b></button>`).join('');
    }
    const cc = d.country_counts || {};
    const labels = d.country_labels || {};
    const ctryEl = document.getElementById('kol-country-chips');
    if (ctryEl) {
      const cAll = Object.values(cc).reduce((a, b) => a + b, 0);
      const cur = this._kol.country;
      const keys = Object.keys(cc).filter(c => c).sort((a, b) => cc[b] - cc[a]);
      if (cur && cur !== 'none' && !keys.includes(cur)) keys.unshift(cur);
      ctryEl.innerHTML = `<button class="kol-chip ${!cur ? 'active' : ''}" onclick="Members.kolSet('country','')">Tất cả <b>${cAll}</b></button>` +
        keys.map(c => `<button class="kol-chip ${cur === c ? 'active' : ''}" onclick="Members.kolSet('country','${esc(c)}')">${esc(labels[c] || c)} <b>${cc[c] || 0}</b></button>`).join('') +
        (cc[''] ? `<button class="kol-chip ${cur === 'none' ? 'active' : ''}" onclick="Members.kolSet('country','none')">❔ Chưa rõ <b>${cc['']}</b></button>` : '');
    }
    this._kolCountryLabels = labels;
    (document.getElementById('kol-total') || me_dummy).textContent = `${d.total} kênh`;
    if (!items.length) {
      tbody.innerHTML = `<tr><td colspan="9" style="text-align:center;color:var(--text-muted);padding:24px">Chưa có kênh nào khớp bộ lọc. Bấm “🔄 Đồng bộ từ Deep Crawl” để nạp kết quả cào gần nhất.</td></tr>`;
    } else {
      tbody.innerHTML = items.map(it => {
        const score = it.trading_score || 0;
        const sc = score >= 80 ? 'var(--green)' : score >= 60 ? 'var(--purple)' : score >= 45 ? 'var(--accent)' : 'var(--text3)';
        const link = it.username ? `https://t.me/${encodeURIComponent(it.username)}` : '';
        const contacts = (it.contacts || []).map(c => {
          const u = String(c).replace(/^@/, '');
          return `<a href="https://t.me/${encodeURIComponent(u)}" target="_blank" rel="noopener" style="color:var(--accent);font-size:12px;white-space:nowrap">@${esc(u)}</a>`;
        }).join(' ') || '<span style="color:var(--text-muted);font-size:12px">—</span>';
        const opts = Object.entries(this._KOL_STATUS).map(([k, [l]]) => `<option value="${k}" ${it.status === k ? 'selected' : ''}>${l}</option>`).join('');
        const stCol = (this._KOL_STATUS[it.status] || [0, 'var(--text3)'])[1];
        return `<tr>
          <td style="max-width:240px"><strong style="font-size:13px">${esc(it.title || it.username)}</strong><br>
            ${link ? `<a href="${link}" target="_blank" rel="noopener" style="font-size:11.5px;color:var(--text-muted)">@${esc(it.username)}</a>` : `<small style="color:var(--text-muted)">id ${esc(it.channel_id)}</small>`}
            ${it.saved_job ? `<span title="Contact đã lưu vào danh sách ${esc(it.saved_job)}" style="font-size:10px;margin-left:4px">📥</span>` : ''}</td>
          <td style="text-align:right;font-size:12.5px">${(it.subscribers || 0).toLocaleString()}</td>
          <td><span style="font-weight:700;color:${sc}">${score}</span></td>
          <td style="font-size:11.5px;color:var(--text2);white-space:nowrap">${esc(it.category_label || it.category || '—')}
            ${it.country ? `<div style="font-size:11px;color:var(--text3);margin-top:2px">${esc((this._kolCountryLabels || {})[it.country] || it.country)}</div>` : ''}</td>
          <td style="max-width:200px">${contacts}</td>
          <td><select class="form-input" style="min-width:140px;padding:3px 6px;font-size:12px;border-left:3px solid ${stCol}" onchange="Members.kolUpdate(${it.id},'status',this.value)">${opts}</select></td>
          <td><input class="form-input" style="width:90px;padding:3px 6px;font-size:12px" value="${esc(it.price || '')}" placeholder="vd 150$" onchange="Members.kolUpdate(${it.id},'price',this.value)"></td>
          <td><input class="form-input" style="width:160px;padding:3px 6px;font-size:12px" value="${esc(it.note || '')}" placeholder="Ghi chú…" onchange="Members.kolUpdate(${it.id},'note',this.value)"></td>
          <td><button style="background:none;border:none;color:var(--text-muted);cursor:pointer" title="Xóa khỏi danh bạ" onclick="Members.kolDelete(${it.id})">✕</button></td>
        </tr>`;
      }).join('');
    }
    const pag = document.getElementById('kol-pagination');
    if (pag) {
      const pages = Math.ceil((d.total || 0) / this._kol.limit);
      const p = this._kol.page;
      pag.innerHTML = pages > 1
        ? `<button class="btn btn-ghost btn-sm" ${p <= 0 ? 'disabled' : ''} onclick="Members.kolSet('page',${p - 1})">◀</button>
           <span style="font-size:12px;color:var(--text2);margin:0 8px">Trang ${p + 1}/${pages}</span>
           <button class="btn btn-ghost btn-sm" ${p >= pages - 1 ? 'disabled' : ''} onclick="Members.kolSet('page',${p + 1})">▶</button>`
        : '';
    }
  },

  async kolUpdate(id, field, value) {
    try {
      const r = await fetch(`/api/members/kol-directory/${id}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ [field]: value }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || 'Lỗi lưu');
      const it = (this._kolItems || []).find(x => x.id === id);
      if (it) it[field] = value;
      if (field === 'status') this.loadKolDirectory();
    } catch (e) { App.toast(e.message, 'error'); }
  },

  async kolDelete(id) {
    if (!confirm('Xóa kênh này khỏi danh bạ KOL? (Lần đồng bộ sau có thể thêm lại)')) return;
    await fetch('/api/members/kol-directory/delete', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids: [id] }),
    }).catch(() => {});
    this.loadKolDirectory();
  },

  async kolSync() {
    try {
      const r = await fetch('/api/members/kol-directory/sync', { method: 'POST' });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || 'Lỗi đồng bộ');
      App.toast(`🔄 Đã đồng bộ ${d.count} kênh có contact từ kết quả Deep Crawl`, 'success');
      this.loadKolDirectory();
    } catch (e) { App.toast(e.message, 'error'); }
  },

  async kolExport() {
    try {
      const r = await fetch(`/api/members/kol-directory/export.csv?${this._kolQuery()}`);
      if (!r.ok) throw new Error('Không xuất được CSV');
      const blob = await r.blob();
      const cd = r.headers.get('Content-Disposition') || '';
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = (cd.match(/filename="([^"]+)"/) || [0, 'kol_directory.csv'])[1];
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    } catch (e) { App.toast(e.message, 'error'); }
  },

  kolCopyContacts() {
    const list = [...new Set((this._kolItems || []).flatMap(it => it.contacts || []))];
    if (!list.length) { App.toast('Trang hiện tại không có contact', 'info'); return; }
    navigator.clipboard.writeText(list.join('\n')).then(
      () => App.toast(`📋 Đã copy ${list.length} contact (trang hiện tại)`, 'success'),
      () => App.toast('Không copy được', 'error'));
  },

  async deleteScrapeJob(jobId) {
    if (!confirm('Xóa dữ liệu cào này?')) return;
    try {
      await MembersAPI.deleteScrapeJob(jobId);
      delete this._membersDetailCache[jobId];   // tránh cache stale sau khi xóa
      App.toast('Đã xóa', 'success');
      this.loadScrapeJobs();
    } catch (e) {
      App.toast(e.message, 'error');
    }
  },

  // ═══════════════════════════════════════════════════════════════════════════
  // DM CAMPAIGNS
  // ═══════════════════════════════════════════════════════════════════════════

  async loadCampaigns() {
    try {
      const d = await MembersAPI.getCampaigns(this._lastCampaignsUpdate);
      const newCampaigns = d.campaigns || [];
      
      if (!this._campaigns) {
        this._campaigns = [];
      }

      if (this._lastCampaignsUpdate === null) {
        this._campaigns = newCampaigns;
      } else if (newCampaigns.length > 0) {
        newCampaigns.forEach(newC => {
          const idx = this._campaigns.findIndex(c => c.id === newC.id);
          if (idx !== -1) {
            this._campaigns[idx] = newC;
          } else {
            this._campaigns.push(newC);
          }
        });
        this._campaigns.sort((a, b) => b.id - a.id);
      }

      if (this._campaigns.length > 0) {
        const timestamps = this._campaigns.map(c => c.updated_at).filter(Boolean);
        if (timestamps.length > 0) {
          this._lastCampaignsUpdate = timestamps.reduce((max, t) => t > max ? t : max, timestamps[0]);
        }
      }

      this._renderCampaigns();
    } catch (e) {
      console.error('Load campaigns error:', e);
    }
  },

  _renderCampaigns() {
    const tbody = document.getElementById('ms-campaigns-tbody');
    const empty = document.getElementById('ms-campaigns-empty');
    if (!tbody) return;

    const campaigns = this._campaigns;
    (document.getElementById('ms-total-campaigns') || me_dummy).textContent = campaigns.length;
    (document.getElementById('ms-total-sent') || me_dummy).textContent = 
      campaigns.reduce((sum, c) => sum + (c.sent_count || 0), 0);

    if (!campaigns.length) {
      tbody.innerHTML = '';
      if (empty) empty.classList.remove('hidden');
      return;
    }
    if (empty) empty.classList.add('hidden');

    // Remove rows no longer in state
    const campaignIdsInState = new Set(campaigns.map(c => c.id));
    Array.from(tbody.children).forEach(row => {
      const idAttr = row.getAttribute('data-id');
      if (idAttr) {
        const cid = parseInt(idAttr);
        if (!campaignIdsInState.has(cid)) {
          row.remove();
        }
      }
    });

    campaigns.forEach((c, i) => {
      const statusBadge = this._statusBadge(c.status);
      const total = c.total_targets || 0;
      const sent = c.sent_count || 0;
      const failed = c.failed_count || 0;
      const skipped = c.skipped_count || 0;
      const processed = sent + failed + skipped;
      const progress = total > 0 ? Math.min(100, Math.round((processed / total) * 100)) : 0;
      const autoResume = c.auto_resume === undefined ? 1 : c.auto_resume;

      let actions = '';
      if (c.status === 'draft' || c.status === 'paused' || c.status === 'paused_auto' || c.status === 'error' || c.status === 'completed') {
        actions += `<button class="btn btn-primary btn-sm" onclick="Members.startCampaign(${c.id})" title="Chạy campaign">▶</button>`;
        actions += `<button class="btn btn-ghost btn-sm" onclick="Members.editCampaignMessages(${c.id})" title="Sửa tin nhắn & cài đặt">✏️</button>`;        actions += `<button class="btn btn-ghost btn-sm" onclick="Members.resetCampaign(${c.id})" title="Reset: chạy lại các lead đã bỏ qua/lỗi (giữ lead đã gửi)">🔄</button>`;
      }
      if (c.status === 'running') {
        actions += `<button class="btn btn-danger btn-sm" onclick="Members.stopCampaign(${c.id})" title="Dừng campaign">⏸</button>`;
      }
      if (c.status === 'scheduled') {
        actions += `<button class="btn btn-primary btn-sm" onclick="Members.startCampaign(${c.id})" title="Chạy ngay (bỏ lịch hẹn)">▶</button>`;
        actions += `<button class="btn btn-ghost btn-sm" onclick="Members.editCampaignMessages(${c.id})" title="Sửa campaign / đổi giờ chạy">✏️</button>`;
        actions += `<button class="btn btn-ghost btn-sm" onclick="Members.cancelSchedule(${c.id})" title="Hủy lịch hẹn giờ" style="color:var(--danger)">🚫</button>`;
      }
      actions += `<button class="btn btn-ghost btn-sm" onclick="Members.cloneCampaign(${c.id})" title="Nhân bản campaign">📑</button>`;
      actions += `<button class="btn btn-ghost btn-sm" onclick="Members.viewCampaignLogs(${c.id},'${esc(c.name)}')" title="Xem log gửi tin">📋</button>`;
      actions += `<button class="btn btn-danger btn-sm" onclick="Members.deleteCampaign(${c.id})" title="Xóa campaign">🗑</button>`;

      // Sub-line under name: source + schedule + auto-resume state
      const sourceFull = c.scrape_job_id || '';
      const sourceShort = sourceFull.length > 22 ? sourceFull.substring(0, 22) + '…' : sourceFull;
      const metaParts = [`<span title="Nguồn: ${esc(sourceFull)}">📁 ${esc(sourceShort)}</span>`];

      if (c.status === 'scheduled' && c.scheduled_at) {
        // scheduled_at is wall-clock time in target_timezone (not UTC).
        const [d, t] = String(c.scheduled_at).replace('T', ' ').split(' ');
        const [yy, mm, dd] = (d || '').split('-');
        const time = `${(t || '').slice(0, 5)} ${dd}/${mm}/${yy}`;
        const tz = c.target_timezone ? ` ${Members._tzLabel(c.target_timezone)}` : '';
        const left = Members._fmtLeft(Members._wallToUnix(c.scheduled_at, c.target_timezone || 'Asia/Ho_Chi_Minh') - Date.now() / 1000);
        metaParts.push(`<span style="color:var(--accent)" title="Tự chạy sau ${left}">⏰ ${time}${tz}</span>`);
      }
      if (c.status === 'paused_auto') {
        metaParts.push(autoResume
          ? `<span style="color:var(--green)" title="Hệ thống tự kiểm tra mỗi 150 phút và chạy tiếp khi account hết bị khóa">🔄 Auto-resume BẬT</span>`
          : `<span style="color:var(--text2)" title="Campaign sẽ không tự chạy tiếp — cần bấm ▶ thủ công">⏹ Auto-resume TẮT</span>`);
        metaParts.push(`<a href="javascript:void(0)" onclick="Members.toggleAutoResume(${c.id}, ${autoResume ? 0 : 1})" style="color:var(--primary);text-decoration:underline">${autoResume ? 'Tắt' : 'Bật'}</a>`);
      }

      const statsTitle = `Thành công: ${sent} • Lỗi: ${failed} • Bỏ qua: ${skipped}`;

      const rowHtml = `
        <td style="color:var(--text2);font-size:12px">${i + 1}</td>
        <td style="max-width:230px">
          <div style="font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${esc(c.name)}">${esc(c.name)}</div>
          <div style="font-size:11px;color:var(--text2);margin-top:2px;display:flex;gap:8px;flex-wrap:wrap">${metaParts.join('')}</div>
        </td>
        <td style="white-space:nowrap">${statusBadge}</td>
        <td style="min-width:190px" title="${statsTitle}">
          <div style="display:flex;align-items:center;gap:8px">
            <div style="flex:1;background:var(--bg2);border-radius:4px;height:6px;overflow:hidden">
              <div style="width:${progress}%;height:100%;background:linear-gradient(90deg, var(--accent), var(--accent));border-radius:4px;transition:width .3s"></div>
            </div>
            <span style="font-size:12px;font-weight:600;color:var(--text1);white-space:nowrap">${processed}/${total}</span>
          </div>
          <div style="font-size:11px;color:var(--text2);margin-top:3px;white-space:nowrap">
            <span style="color:var(--green)">${sent}</span> · <span style="color:var(--red)">${failed}</span> · <span style="color:var(--text2)">${skipped}</span>
          </div>
        </td>
        <td style="white-space:nowrap"><div class="btn-group">${actions}</div></td>
      `;

      let existingRow = tbody.querySelector(`tr[data-id="${c.id}"]`);
      if (existingRow) {
        const existingIdx = parseInt(existingRow.getAttribute('data-index'));
        if (existingIdx !== (i + 1) ||
            existingRow.getAttribute('data-updated-at') !== c.updated_at ||
            existingRow.getAttribute('data-status') !== c.status ||
            existingRow.getAttribute('data-autoresume') !== String(autoResume) ||
            existingRow.getAttribute('data-sent') !== String(sent) ||
            existingRow.getAttribute('data-failed') !== String(failed) ||
            existingRow.getAttribute('data-skipped') !== String(skipped)) {
          existingRow.innerHTML = rowHtml;
          existingRow.setAttribute('data-updated-at', c.updated_at);
          existingRow.setAttribute('data-status', c.status);
          existingRow.setAttribute('data-autoresume', String(autoResume));
          existingRow.setAttribute('data-index', i + 1);
          existingRow.setAttribute('data-sent', String(sent));
          existingRow.setAttribute('data-failed', String(failed));
          existingRow.setAttribute('data-skipped', String(skipped));
        }
      } else {
        const tr = document.createElement('tr');
        tr.setAttribute('data-id', c.id);
        tr.setAttribute('data-updated-at', c.updated_at);
        tr.setAttribute('data-status', c.status);
        tr.setAttribute('data-autoresume', String(autoResume));
        tr.setAttribute('data-index', i + 1);
        tr.setAttribute('data-sent', String(sent));
        tr.setAttribute('data-failed', String(failed));
        tr.setAttribute('data-skipped', String(skipped));
        tr.innerHTML = rowHtml;
        
        if (tbody.children.length === 0 || i >= tbody.children.length) {
          tbody.appendChild(tr);
        } else {
          tbody.insertBefore(tr, tbody.children[i]);
        }
      }
    });
  },

  _statusBadge(status) {
    const st = 'font-size:11px;padding:3px 8px;min-width:96px;display:inline-block;text-align:center';
    const map = {
      draft: `<span class="badge" style="background:var(--text2);${st}">📝 Nháp</span>`,
      running: `<span class="badge badge-blue" style="${st}">🔄 Đang chạy</span>`,
      paused: `<span class="badge" style="background:var(--orange);${st}" title="Tạm dừng thủ công">⏸ Tạm dừng</span>`,
      paused_auto: `<span class="badge" style="background:var(--purple);${st}" title="Dừng do SpamBot/limit — sẽ tự chạy tiếp khi account sẵn sàng">🔒 Chờ mở khóa</span>`,
      completed: `<span class="badge badge-green" style="${st}">✅ Hoàn tất</span>`,
      error: `<span class="badge badge-red" style="${st}">❌ Lỗi</span>`,
      scheduled: `<span class="badge" style="background:var(--accent);${st}">⏰ Hẹn giờ</span>`,
    };
    return map[status] || `<span class="badge" style="${st}">${status}</span>`;
  },

  async toggleAutoResume(id, enabled) {
    try {
      await MembersAPI.setAutoResume(id, !!enabled);
      App.toast(enabled ? '🔄 Đã bật auto-resume' : '⏹ Đã tắt auto-resume', 'success');
      this._lastCampaignsUpdate = null;
      this.loadCampaigns();
    } catch (e) {
      App.toast(e.message, 'error');
    }
  },

  // ── Campaign Actions ──
  async startCampaign(id) {
    try {
      // Preflight: make sure an AI model answers before burning DM quota.
      let chk = null;
      try {
        App.toast('Đang kiểm tra AI model…', 'info');
        chk = await apiGet(`/api/members/campaigns/${id}/ai-check`);
      } catch (e) {
        if (!confirm('Không kiểm tra được AI model (' + e.message + '). Vẫn chạy campaign?')) return;
      }
      if (chk && chk.needs_ai) {
        const lines = (chk.providers || []).map(p =>
          `${p.ok ? '✅' : '❌'} ${p.provider}${p.model ? ' (' + p.model + ')' : ''}${p.primary ? ' — đang chọn' : ''}` +
          (p.ok ? ` ${p.latency_ms}ms` : ` — ${p.error || 'lỗi'}`));
        if (!chk.ok) {
          alert('❌ Không có AI model nào hoạt động — campaign KHÔNG được chạy.\n\n' +
                (lines.join('\n') || 'Chưa cấu hình nhà cung cấp AI nào.') +
                '\n\nKiểm tra 9Router / API key rồi thử lại.');
          return;
        }
        if (!chk.primary_ok &&
            !confirm('⚠️ Model đang chọn (' + (chk.primary || '?') + ') không phản hồi, AI sẽ dùng model dự phòng.\n\n' +
                     lines.join('\n') + '\n\nVẫn chạy campaign?')) return;
        if (chk.primary_ok) App.toast('AI model OK: ' + lines.find(l => l.includes('đang chọn')), 'success');
      }
      const r = await API.startCampaignRuntime(id);
      if (r.status === 'queued') {
        App.toast('Lệnh đã được xếp hàng — worker sẽ chạy campaign', 'info');
        const result = await API.pollCommand(r.command_id);
        if (result.status !== 'succeeded') throw new Error(result.error_text || `Worker trả về ${result.status}`);
        App.toast('Campaign đã chạy', 'success');
      } else {
        App.toast(r.message || 'Campaign đã chạy!', 'success');
      }
      this._lastCampaignsUpdate = null;
      this.loadCampaigns();
      // Auto-refresh while running
      this._pollCampaign(id);
    } catch (e) {
      App.toast(e.message, 'error');
    }
  },

  async resetCampaign(id) {
    if (!confirm('Reset campaign? Các lead bị bỏ qua/lỗi sẽ được chạy lại khi bấm ▶. Lead đã gửi thành công vẫn giữ, không gửi trùng.')) return;
    try {
      const r = await apiPost(`/api/members/campaigns/${id}/reset`, {});
      App.toast(`Đã reset — ${r.cleared} lead sẽ được chạy lại`, 'success');
      this._lastCampaignsUpdate = null;
      this.loadCampaigns();
    } catch (e) {
      App.toast(e.message, 'error');
    }
  },

  async stopCampaign(id) {
    try {
      const r = await API.stopCampaignRuntime(id);
      if (r.status === 'queued') {
        App.toast('Lệnh dừng đã được xếp hàng', 'info');
        const result = await API.pollCommand(r.command_id);
        if (result.status !== 'succeeded') throw new Error(result.error_text || `Chưa xác nhận lệnh dừng: ${result.status}. Không tự gửi lại lệnh.`);
        App.toast('Campaign đã dừng', 'success');
      } else {
        App.toast('Campaign đã dừng', 'success');
      }
      this._lastCampaignsUpdate = null;
      this.loadCampaigns();
    } catch (e) {
      App.toast(e.message, 'error');
    }
  },

  async deleteCampaign(id) {
    if (!confirm('Xóa campaign này? Sẽ xóa cả logs.')) return;
    try {
      await MembersAPI.deleteCampaign(id);
      App.toast('Đã xóa', 'success');
      this._lastCampaignsUpdate = null;
      this.loadCampaigns();
    } catch (e) {
      App.toast(e.message, 'error');
    }
  },

  async cancelSchedule(id) {
    if (!confirm('Bạn có chắc muốn hủy lịch chạy campaign này không? Campaign sẽ chuyển về trạng thái Draft.')) return;
    try {
      await MembersAPI.cancelSchedule(id);
      App.toast('Đã hủy lịch thành công', 'success');
      this._lastCampaignsUpdate = null;
      this.loadCampaigns();
    } catch (e) {
      App.toast(e.message, 'error');
    }
  },

  _tzLabel(tz) {
    const map = {
      'Asia/Ho_Chi_Minh': '🇻🇳 VN',
      'Asia/Singapore': '🇸🇬 SG/MY',
      'Asia/Hong_Kong': '🇭🇰 HK',
      'Asia/Tokyo': '🇯🇵 JP',
      'Asia/Seoul': '🇰🇷 KR',
      'Asia/Kolkata': '🇮🇳 IN',
      'Asia/Dubai': '🇦🇪 AE',
      'Europe/Istanbul': '🇹🇷 TR',
      'Europe/London': '🇬🇧 UK',
      'Europe/Berlin': '🇩🇪 EU',
      'America/New_York': '🇺🇸 US-E',
      'America/Los_Angeles': '🇺🇸 US-W',
      'America/Sao_Paulo': '🇧🇷 BR',
      'Africa/Lagos': '🇳🇬 NG',
      'Australia/Sydney': '🇦🇺 AU'
    };
    return map[tz] || tz;
  },

  _pollCampaign(id) {
    if (Polling.isActive('members:campaigns')) return;

    Polling.start('members:campaigns', async () => {
      try {
        await this.loadCampaigns();
        const hasRunning = this._campaigns.some(c => c.status === 'running');
        if (!hasRunning) {
          Polling.stop('members:campaigns');
        }
      } catch (e) {
        Polling.stop('members:campaigns');
      }
    }, 10000);
  },

  // ── Edit Campaign Messages ──
  _editingCampaignId: null,

  async editCampaignMessages(id) {
    try {
      const d = await MembersAPI.getCampaign(id);
      const c = d.campaign;
      if (!c) { App.toast('Campaign không tồn tại', 'error'); return; }
      if (!['draft', 'paused', 'paused_auto', 'error', 'completed', 'scheduled'].includes(c.status)) {
        App.toast('Chỉ sửa được khi campaign tạm dừng, chưa chạy hoặc đã hoàn thành', 'error');
        return;
      }

      this._editingCampaignId = id;
      this._cmpSuggestSeq++;
      (document.getElementById('cmp-suggest-note') || me_dummy).style.display = 'none';

      // Set modal title to edit mode
      (document.getElementById('campaign-modal-title') || me_dummy).textContent = `✏️ Sửa Campaign: ${c.name}`;

      // Fill in settings
      (document.getElementById('cmp-name') || me_dummy).value = c.name;
      (document.getElementById('cmp-name') || me_dummy).disabled = true; // Can't change name
      (document.getElementById('cmp-delay-min') || me_dummy).value = c.delay_min || 30;
      (document.getElementById('cmp-delay-max') || me_dummy).value = c.delay_max || 90;
      const limitInput = document.getElementById('cmp-daily-limit') || document.getElementById('cmp-daily-limit-premium');
      if (limitInput) limitInput.value = c.daily_limit_premium || 50;
      (document.getElementById('cmp-ai-remix') || me_dummy).checked = !!c.use_ai_remix;
      // Populate AI Agent dropdown if available
      if (typeof AIAgents !== 'undefined' && AIAgents.populateAgentDropdown) {
        AIAgents.populateAgentDropdown('cmp-ai-agent', c.ai_agent_id || null);
      }
      const excludePrevEl = document.getElementById('cmp-exclude-previous');
      if (excludePrevEl) {
        excludePrevEl.checked = c.exclude_previous_dms !== undefined ? !!c.exclude_previous_dms : true;
      }
      const autoResumeEl = document.getElementById('cmp-auto-resume');
      if (autoResumeEl) {
        autoResumeEl.checked = c.auto_resume !== undefined ? !!c.auto_resume : true;
      }
      const minLeadScoreEl = document.getElementById('cmp-min-lead-score');
      if (minLeadScoreEl) {
        minLeadScoreEl.value = (c.min_lead_score !== undefined && c.min_lead_score !== null) ? c.min_lead_score : 0;
      }

      // Scrape job selector — allow changing source (linh động chạy tiếp với nguồn mới)
      const jobSel = document.getElementById('cmp-scrape-job');
      if (!this._scrapeJobs?.length) { try { await this.loadScrapeJobs(); } catch (e) {} }
      const jobOptions = (this._scrapeJobs || []).map(j =>
        `<option value="${esc(j.scrape_job_id)}">${esc(j.group_title || j.group_id)} (${j.member_count} members)</option>`
      );
      if (!this._scrapeJobs?.some(j => j.scrape_job_id === c.scrape_job_id)) {
        jobOptions.unshift(`<option value="${esc(c.scrape_job_id)}">${esc((c.scrape_job_id || '').substring(0, 30))}... (nguồn hiện tại)</option>`);
      }
      jobSel.innerHTML = jobOptions.join('');
      jobSel.value = c.scrape_job_id;
      // ponytail: cho phép đổi nguồn ở mọi trạng thái editable; nếu sau này cần guard chặt hơn, thêm lại whitelist ở đây
      jobSel.disabled = false;
      jobSel.title = 'Đổi nguồn Members — hệ thống tự tính lại total_targets khi lưu';

      // Load accounts & mark sender accounts
      const now = Date.now();
      if (!this._accounts?.length || !this._accountsCachedAt || (now - this._accountsCachedAt) > 30000) {
        try {
          const ad = await API.getAccounts();
          this._accounts = ad.accounts || [];
          this._accountsCachedAt = now;
        } catch (e) {}
      }

      await this._renderCampaignAccountPicker(c.sender_account_ids || []);

      // Schedule
      const schedToggle = document.getElementById('cmp-schedule-toggle');
      const isSched = c.status === 'scheduled' && !!c.scheduled_at;
      (document.getElementById('cmp-target-timezone') || me_dummy).value = (isSched && c.target_timezone) || 'Asia/Ho_Chi_Minh';
      (document.getElementById('cmp-scheduled-at') || me_dummy).value = isSched ? String(c.scheduled_at).replace(' ', 'T').slice(0, 16) : '';
      if (schedToggle) { schedToggle.checked = isSched; this.toggleScheduleSection(); }
      this._editingWasScheduled = isSched;

      // Fill messages
      const msgList = document.getElementById('cmp-messages-list');
      msgList.innerHTML = '';
      const messages = c.messages || [];
      if (messages.length === 0) {
        this.addCampaignMessage();
      } else {
        messages.forEach((msg, i) => {
          this.addCampaignMessage();
          const items = msgList.querySelectorAll('.cmp-msg-item');
          const item = items[items.length - 1];
          const textarea = item.querySelector('.cmp-msg-content');
          if (textarea) textarea.value = msg.content || '';

          // Restore media if present
          if (msg.media_path) {
            const mediaPathInput = item.querySelector('.cmp-msg-media-path');
            const mediaTypeInput = item.querySelector('.cmp-msg-media-type');
            const previewDiv = item.querySelector('.cmp-msg-media-preview');
            if (mediaPathInput) mediaPathInput.value = msg.media_path;
            if (mediaTypeInput) mediaTypeInput.value = msg.msg_type || 'text';
            if (previewDiv) {
              const fname = msg.media_path.split('/').pop().split('\\').pop();
              previewDiv.style.display = 'flex';
              previewDiv.innerHTML = `
                <span style="font-size:18px">${msg.msg_type === 'photo' ? '🖼️' : msg.msg_type === 'video' ? '🎬' : '📄'}</span>
                <span style="font-size:12px;color:var(--text1)">${esc(fname)}</span>
                <button class="btn btn-ghost btn-sm" onclick="Members.removeMsgMedia(this)" style="font-size:11px;padding:2px 6px;color:var(--danger)">✕</button>
              `;
            }
          }
        });
      }

      // Change save button behavior
      const saveBtn = document.querySelector('#campaign-modal .btn-primary[onclick*="saveCampaign"]');
      if (saveBtn) {
        saveBtn.setAttribute('onclick', 'Members.saveEditedCampaign()');
        saveBtn.textContent = '💾 Lưu thay đổi';
      }

      (document.getElementById('campaign-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('open');
    } catch (e) {
      App.toast(e.message, 'error');
    }
  },

  async saveEditedCampaign() {
    const id = this._editingCampaignId;
    if (!id) { App.toast('Lỗi: không có campaign để sửa', 'error'); return; }

    const jobId = document.getElementById('cmp-scrape-job')?.value;
    const delayMin = parseInt(document.getElementById('cmp-delay-min')?.value) || 30;
    const delayMax = parseInt(document.getElementById('cmp-delay-max')?.value) || 90;
    const dailyLimitPremium = parseInt(document.getElementById('cmp-daily-limit')?.value || document.getElementById('cmp-daily-limit-premium')?.value) || 50;
    const dailyLimitNormal = 10; // Auto set cứng tài khoản Telegram Free max 10 DM/ngày
    const useAi = document.getElementById('cmp-ai-remix')?.checked;
    const agentIdVal = document.getElementById('cmp-ai-agent')?.value;
    const aiAgentId = agentIdVal ? parseInt(agentIdVal) : null;
    const autoNative = document.getElementById('cmp-auto-translate-native')?.checked ? 1 : 0;
    const autoResume = document.getElementById('cmp-auto-resume')?.checked ?? true;
    const excludePrev = document.getElementById('cmp-exclude-previous')?.checked ?? true;
    const minLeadScore = parseInt(document.getElementById('cmp-min-lead-score')?.value) || 0;

    if (!jobId) { App.toast('Vui lòng chọn nguồn members', 'error'); return; }
    const schedOn = document.getElementById('cmp-schedule-toggle')?.checked || false;
    const schedAt = document.getElementById('cmp-scheduled-at')?.value;
    const schedTz = document.getElementById('cmp-target-timezone')?.value || 'Asia/Ho_Chi_Minh';
    if (schedOn && !schedAt) { App.toast('Vui lòng chọn ngày giờ chạy', 'error'); return; }

    // Collect sender accounts
    const accCheckboxes = document.querySelectorAll('.cmp-acc-checkbox:checked');
    const senderIds = Array.from(accCheckboxes).map(cb => parseInt(cb.value));
    if (!senderIds.length) { App.toast('Chọn ít nhất 1 tài khoản gửi', 'error'); return; }

    // Collect messages
    const msgItems = document.querySelectorAll('.cmp-msg-item');
    const messages = [];
    msgItems.forEach((item, i) => {
      const content = item.querySelector('.cmp-msg-content')?.value.trim() || '';
      const mediaPath = item.querySelector('.cmp-msg-media-path')?.value || '';
      const mediaType = item.querySelector('.cmp-msg-media-type')?.value || 'text';
      if (content || mediaPath) {
        messages.push({
          msg_order: i,
          msg_type: mediaPath ? mediaType : 'text',
          content,
          media_path: mediaPath || undefined
        });
      }
    });
    if (!messages.length) { App.toast('Thêm ít nhất 1 tin nhắn', 'error'); return; }

    try {
      await MembersAPI.updateCampaignMessages(id, {
        messages,
        scrape_job_id: jobId,
        sender_account_ids: senderIds,
        delay_min: delayMin,
        delay_max: delayMax,
        daily_limit_premium: dailyLimitPremium,
        daily_limit_normal: dailyLimitNormal,
        use_ai_remix: useAi,
        ai_agent_id: aiAgentId,
        auto_translate_native: autoNative,
        exclude_previous_dms: excludePrev,
        auto_resume: autoResume,
        min_lead_score: minLeadScore,
      });
      if (schedOn || this._editingWasScheduled) {
        await API.put(`/api/members/campaigns/${id}/schedule`, {
          scheduled_at: schedOn ? schedAt : null,
          target_timezone: schedOn ? schedTz : null,
        });
      }
      App.toast(schedOn ? '✅ Đã cập nhật Campaign & lịch hẹn giờ!' : '✅ Đã cập nhật Campaign (nguồn, tài khoản & tin nhắn)!', 'success');
      this._editingCampaignId = null;
      this.closeCampaignModal();
      this.loadCampaigns();
    } catch (e) {
      App.toast(e.message, 'error');
    }
  },

  // ── Campaign Logs (phân trang client) ──
  async viewCampaignLogs(id, name) {
    (document.getElementById('campaign-logs-title') || me_dummy).textContent = `Logs: ${name}`;
    const tbody = document.getElementById('campaign-logs-tbody');
    const pager = document.getElementById('campaign-logs-pager');
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center">⏳ Đang tải...</td></tr>';
    if (pager) pager.innerHTML = '';
    (document.getElementById('campaign-logs-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('open');

    this._campaignLogsPage = 1;
    let logs = this._campaignLogsCache[id];
    if (!logs) {
      try {
        const d = await MembersAPI.getCampaignLogs(id);
        logs = d.logs || [];
        this._campaignLogsCache[id] = logs;
      } catch (e) {
        tbody.innerHTML = `<tr><td colspan="5" style="color:var(--danger)">${esc(e.message)}</td></tr>`;
        return;
      }
    }
    this._renderCampaignLogs(logs);
  },

  _renderCampaignLogs(logs) {
    const tbody = document.getElementById('campaign-logs-tbody');
    const pager = document.getElementById('campaign-logs-pager');
    if (!tbody) return;
    if (!logs.length) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text2)">Chưa có logs</td></tr>';
      if (pager) pager.innerHTML = '';
      return;
    }
    const st = paginate(logs, this._campaignLogsPage, this._campaignLogsPerPage);
    this._campaignLogsPage = st.page;
    tbody.innerHTML = st.slice.map(l => {
      const statusBadge = l.status === 'success'
        ? '<span class="badge badge-green">✅</span>'
        : l.status === 'skipped'
          ? '<span class="badge" style="background:var(--orange)">⏭</span>'
          : '<span class="badge badge-red">❌</span>';
      const time = l.sent_at ? new Date(l.sent_at + 'Z').toLocaleString('vi-VN') : '—';
      return `<tr>
        <td>${l.target_username ? '@' + esc(l.target_username) : l.target_user_id}</td>
        <td>${l.account_name ? esc(l.account_name) : (l.account_id || '—')}</td>
        <td>${statusBadge}</td>
        <td style="font-size:12px;max-width:200px;overflow:hidden;text-overflow:ellipsis">${esc(l.error_message || '')}</td>
        <td style="font-size:12px;color:var(--text2)">${time}</td>
      </tr>`;
    }).join('');
    renderPager(pager, st, (p) => { this._campaignLogsPage = p; this._renderCampaignLogs(logs); });
  },

  closeCampaignLogs() {
    (document.getElementById('campaign-logs-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('open');
  },

  // ═══════════════════════════════════════════════════════════════════════════
  // CAMPAIGN MODAL
  // ═══════════════════════════════════════════════════════════════════════════

  async openCampaignModal(preselectJobId = null) {
    (document.getElementById('campaign-modal-title') || me_dummy).textContent = 'Tạo DM Campaign';
    (document.getElementById('cmp-name') || me_dummy).value = preselectJobId ? `DM — ${preselectJobId}` : '';
    (document.getElementById('cmp-delay-min') || me_dummy).value = '30';
    (document.getElementById('cmp-delay-max') || me_dummy).value = '90';
    const limitInput = document.getElementById('cmp-daily-limit') || document.getElementById('cmp-daily-limit-premium');
    if (limitInput) limitInput.value = '50';
    (document.getElementById('cmp-ai-remix') || me_dummy).checked = false;
    (document.getElementById('cmp-auto-translate-native') || me_dummy).checked = true;
    this._cmpAgentsReady = (typeof AIAgents !== 'undefined' && AIAgents.populateAgentDropdown)
      ? AIAgents.populateAgentDropdown('cmp-ai-agent', null) : Promise.resolve();
    const excludePrevEl = document.getElementById('cmp-exclude-previous');
    if (excludePrevEl) excludePrevEl.checked = true;
    const minLeadScoreEl = document.getElementById('cmp-min-lead-score');
    if (minLeadScoreEl) minLeadScoreEl.value = 0;
    (document.getElementById('cmp-messages-list') || me_dummy).innerHTML = '';
    this._cmpLastSuggest = null;

    const schedToggle = document.getElementById('cmp-schedule-toggle');
    if (schedToggle) {
      schedToggle.checked = false;
      this.toggleScheduleSection();
    }
    const schedAt = document.getElementById('cmp-scheduled-at');
    if (schedAt) schedAt.value = '';
    (document.getElementById('cmp-target-timezone') || me_dummy).value = 'Asia/Ho_Chi_Minh';
    this._availCache = null; // fresh countdowns each time the modal opens

    // Populate scrape jobs dropdown
    const jobSel = document.getElementById('cmp-scrape-job');
    if (this._scrapeJobs.length) {
      jobSel.innerHTML = this._scrapeJobs.map(j =>
        `<option value="${esc(j.scrape_job_id)}">${esc(j.group_title || j.group_id)} (${j.member_count} members)</option>`
      ).join('');
      if (preselectJobId && this._scrapeJobs.some(j => j.scrape_job_id === preselectJobId)) {
        jobSel.value = preselectJobId;
      }
    } else {
      jobSel.innerHTML = '<option value="">Chưa có dữ liệu cào</option>';
    }

    // Populate accounts as checkboxes
    // Use cached accounts if fresh (< 30s old)
    const now = Date.now();
    if (!this._accounts?.length || !this._accountsCachedAt || (now - this._accountsCachedAt) > 30000) {
      try {
        const d = await API.getAccounts();
        this._accounts = d.accounts || [];
        this._accountsCachedAt = now;
      } catch (e) {}
    }

    await this._renderCampaignAccountPicker(null);

    // Add one empty message by default, then fill it from the picked list
    this.addCampaignMessage();

    (document.getElementById('campaign-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('open');
    this._applyCampaignSuggestion();
  },

  // ── Auto-suggest message + Remix/Agent/Dịch from the picked list ──
  // Backend picks the best past campaign (reply rate) in the list's language;
  // we only overwrite textareas the user has not edited since the last fill.
  _cmpSuggestSeq: 0,
  _cmpLastSuggest: null,

  _onCampaignJobChange() {
    if (!this._editingCampaignId) this._applyCampaignSuggestion();
  },

  _cmpSenderBrand() {
    const E = typeof Engagement !== 'undefined' ? Engagement : null;
    if (!E?._accBrand) return '';
    const ids = new Set([...document.querySelectorAll('#cmp-accounts-list .cmp-acc-checkbox:checked')].map(cb => +cb.value));
    const cnt = {};
    (this._accounts || []).filter(a => ids.has(a.id)).forEach(a => {
      const b = (E._accBrand(a) || '').trim().toLowerCase();
      if (b) cnt[b] = (cnt[b] || 0) + 1;
    });
    return Object.entries(cnt).sort((x, y) => y[1] - x[1])[0]?.[0] || '';
  },

  _cmpMessagesUntouched() {
    const prev = (this._cmpLastSuggest?.messages || []).map(m => (m.content || '').trim());
    return [...document.querySelectorAll('#cmp-messages-list .cmp-msg-item')].every((it, i) => {
      const v = it.querySelector('.cmp-msg-content')?.value.trim() || '';
      return !it.querySelector('.cmp-msg-media-path')?.value && (!v || v === (prev[i] || ''));
    });
  },

  async _applyCampaignSuggestion(force = false) {
    const note = document.getElementById('cmp-suggest-note');
    const jobId = document.getElementById('cmp-scrape-job')?.value;
    if (!jobId || this._editingCampaignId) { if (note) note.style.display = 'none'; return; }
    if (force && !this._cmpMessagesUntouched()
        && !confirm('Thay nội dung bạn đã sửa bằng đề xuất mới?')) return;
    const seq = ++this._cmpSuggestSeq;
    if (note) {
      note.style.display = 'block';
      note.innerHTML = '<span style="color:var(--text2)">⏳ Đang đề xuất nội dung theo tệp đã cào…</span>';
    }
    let s;
    try {
      const brand = this._cmpSenderBrand();
      s = await API.get(`/api/members/campaigns/suggest?job_id=${encodeURIComponent(jobId)}` +
        (brand ? `&brand=${encodeURIComponent(brand)}` : ''));
    } catch (e) {
      if (seq === this._cmpSuggestSeq && note) note.innerHTML = `<span style="color:var(--danger)">Chưa đề xuất được: ${esc(e.message)}</span>`;
      return;
    }
    if (seq !== this._cmpSuggestSeq || this._editingCampaignId) return;

    const list = document.getElementById('cmp-messages-list');
    const filled = force || this._cmpMessagesUntouched();
    if (filled && list) {
      list.innerHTML = '';
      (s.messages?.length ? s.messages : [{ content: '' }]).forEach(m => {
        this.addCampaignMessage();
        const ta = list.lastElementChild?.querySelector('.cmp-msg-content');
        if (ta) { ta.value = m.content || ''; this.updateMsgCharCounter(ta); }
      });
      this._cmpLastSuggest = s;
    }
    (document.getElementById('cmp-ai-remix') || me_dummy).checked = !!s.use_ai_remix;
    (document.getElementById('cmp-auto-translate-native') || me_dummy).checked = !!s.auto_translate_native;
    try { await this._cmpAgentsReady; } catch (e) {}
    if (seq !== this._cmpSuggestSeq) return;
    const agentSel = document.getElementById('cmp-ai-agent');
    if (agentSel) agentSel.value = s.ai_agent_id ? String(s.ai_agent_id) : '';

    if (note) {
      const tip = 'Đề xuất dựa trên ngôn ngữ của tệp và tỉ lệ trả lời thật của các chiến dịch cũ (người đã trả lời ÷ tin đã gửi). Hệ thống chỉ điền vào ô bạn chưa sửa; bấm “Đề xuất lại” sau khi chọn tài khoản gửi để khớp brand của tài khoản.';
      note.innerHTML = `
        <div style="display:flex;align-items:center;gap:6px;font-weight:600;margin-bottom:4px">
          <span>✨ Đã tự đề xuất — bạn chỉ cần xem lại rồi bấm Lưu</span>
          <span class="info-i" tabindex="0" data-tip="${esc(tip)}">i</span>
          <button type="button" class="btn btn-ghost btn-sm" onclick="Members._applyCampaignSuggestion(true)" style="margin-left:auto;font-size:11px;padding:2px 8px;border:1px solid var(--border)">↺ Đề xuất lại</button>
        </div>
        ${filled ? '' : '<div style="color:var(--orange);margin-bottom:2px">Giữ nguyên nội dung bạn đã sửa — chỉ cập nhật Remix / Agent / Dịch.</div>'}
        <ul style="margin:0;padding-left:18px;color:var(--text2);line-height:1.55">${(s.reasons || []).map(r => `<li>${esc(r)}</li>`).join('')}</ul>`;
    }
  },

  closeCampaignModal() {
    (document.getElementById('campaign-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('open');
    clearInterval(this._cmpTimer);
    
    const schedToggle = document.getElementById('cmp-schedule-toggle');
    if (schedToggle) {
      schedToggle.checked = false;
      this.toggleScheduleSection();
    }
    const schedAt = document.getElementById('cmp-scheduled-at');
    if (schedAt) schedAt.value = '';

    // Reset edit mode state
    if (this._editingCampaignId) {
      this._editingCampaignId = null;
      (document.getElementById('cmp-name') || me_dummy).disabled = false;
      (document.getElementById('cmp-scrape-job') || me_dummy).disabled = false;
      const saveBtn = document.querySelector('#campaign-modal .btn-primary[onclick*="saveEditedCampaign"]');
      if (saveBtn) {
        saveBtn.setAttribute('onclick', 'Members.saveCampaign()');
        saveBtn.textContent = '🚀 Tạo Campaign';
      }
    }
  },

  // Sender picker for DM campaigns: each chip shows a health dot (+ SpamBot
  // result), unsafe accounts are dimmed and sorted last, and the quick row
  // picks a whole set in one click. checkedIds=null → tick healthy accounts.
  async _renderCampaignAccountPicker(checkedIds) {
    const accDiv = document.getElementById('cmp-accounts-list');
    if (!accDiv) return;
    const now = Date.now();
    if (!this._accounts?.length || !this._accountsCachedAt || (now - this._accountsCachedAt) > 30000) {
      try { this._accounts = (await API.getAccounts()).accounts || []; this._accountsCachedAt = now; } catch (e) {}
    }
    // SpamBot results change rarely → cache 10 min.
    if (!this._spamCache || now - this._spamCacheAt > 600000) {
      try { this._spamCache = (await API.get('/api/members/accounts/spam-status')).statuses || {}; this._spamCacheAt = now; }
      catch (e) { this._spamCache = this._spamCache || {}; }
    }
    // When restricted accounts can send again — fetched once per modal open (60s cache), no polling.
    if (!this._availCache || now - this._availAt > 60000) {
      try { this._availCache = (await API.get('/api/members/accounts/availability')).accounts || {}; this._availAt = now; }
      catch (e) { this._availCache = this._availCache || {}; }
    }
    // A SpamBot limit whose date has passed is stale → ask @SpamBot again in the
    // background (once per 10 min, only those accounts) and redraw the picker.
    const expiredIds = Object.keys(this._spamCache || {}).filter(k => this._spamCache[k]?.expired);
    if (expiredIds.length && !(this._spamRecheckAt && now - this._spamRecheckAt < 600000)) {
      this._spamRecheckAt = now;
      API.post('/api/members/accounts/spam-recheck-expired', {}).then(r => {
        if (!r?.checked) return;
        Object.assign(this._spamCache, r.statuses || {});
        App._spamStatus = Object.assign(App._spamStatus || {}, r.statuses || {});
        this._availAt = 0;  // availability depends on the SpamBot answer
        if (document.getElementById('campaign-modal')?.classList.contains('open')) {
          this._renderCampaignAccountPicker([...document.querySelectorAll('#cmp-accounts-chips .cmp-acc-checkbox:checked')].map(cb => +cb.value));
        }
      }).catch(() => {});
    }
    const E = typeof Engagement !== 'undefined' ? Engagement : null;
    const rows = (this._accounts || []).filter(a => a && a.id).map(a => {
      let st = E ? E._accStatus(a) : { ok: !!a.is_logged_in, color: a.is_logged_in ? 'var(--success,#3fb950)' : 'var(--text3)', label: a.is_logged_in ? 'Hoạt động' : 'Offline' };
      const sp = this._spamCache[a.id] || this._spamCache[String(a.id)];
      if (st.ok && sp && sp.status === 'limited' && !sp.expired) st = { ok: false, color: 'var(--red)', label: 'SpamBot giới hạn', detail: sp.message };
      else if (st.ok && sp?.expired) st = { ...st, detail: 'giới hạn SpamBot đã hết hạn — hỏi lại @SpamBot khi chạy' };
      return { a, st, sp, brand: E ? E._accBrand(a) : '' };
    });
    rows.sort((x, y) => (y.st.ok - x.st.ok) || ((y.a.is_premium || 0) - (x.a.is_premium || 0)));
    this._cmpPickerRows = rows;

    const ok = rows.filter(r => r.st.ok);
    const brands = {};
    ok.forEach(r => { if (r.brand) brands[r.brand] = (brands[r.brand] || 0) + 1; });
    const chip = (sel, label, tip) => `<button type="button" class="btn btn-ghost btn-sm" onclick="Members.quickPickCampaignAccounts('${sel}')" title="${esc(tip)}" style="font-size:11px;padding:2px 8px;border:1px solid var(--border)">${label}</button>`;
    accDiv.innerHTML = `
      <div style="display:flex;align-items:center;gap:6px;margin-bottom:8px;width:100%;flex-wrap:wrap">
        ${chip('ok', `✅ Khỏe (${ok.length})`, 'Chọn mọi account đang hoạt động, không tạm dừng/flag/PeerFlood/SpamBot giới hạn')}
        ${chip('premium', `⭐ Premium khỏe (${ok.filter(r => r.a.is_premium).length})`, 'Chỉ chọn account Premium đang khỏe')}
        ${Object.keys(brands).sort().map(b => chip('brand:' + b, `#${esc(b)} (${brands[b]})`, `Chỉ chọn account khỏe có tên "| ${b}"`)).join('')}
        ${chip('all', '☑ Tất cả', 'Chọn cả account không khỏe (hệ thống vẫn tự bỏ qua khi gửi)')}
        ${chip('none', '☐ Bỏ hết', 'Bỏ chọn tất cả')}
        <span style="flex:1"></span>
        <span id="cmp-acc-count" style="font-size:11px;color:var(--text2)"></span>
        <span class="info-i" title="Chấm xanh = khỏe. Vàng = tạm dừng / flag / PeerFlood. Đỏ = SpamBot giới hạn hoặc lỗi session. Xám = offline. Account không khỏe bị làm mờ, xếp cuối và mặc định không chọn.">i</span>
      </div>
      <div id="cmp-accounts-chips" style="display:flex;flex-wrap:wrap;gap:6px"></div>`;

    const isChecked = r => checkedIds ? checkedIds.includes(r.a.id) : r.st.ok;
    document.getElementById('cmp-accounts-chips').innerHTML = rows.map(r => {
      const a = r.a, st = r.st;
      const name = (a.user_info ? [a.user_info.first_name, a.user_info.last_name].filter(Boolean).join(' ') : '') || a.name || a.phone || ('#' + a.id);
      let tip = `${name} — ${st.label}${st.detail ? ': ' + st.detail : ''}`;
      if (r.sp) tip += ` · SpamBot: ${r.sp.expired ? 'hết hạn giới hạn' : r.sp.status}${r.sp.checked_at ? ' (' + new Date(r.sp.checked_at).toLocaleString('vi-VN') + ')' : ''}`;
      tip += ' · Mỗi lần chạy campaign đều hỏi lại @SpamBot';
      const av = this._availCache?.[String(a.id)];
      if (av) tip += ` · ${this._cmpAvailTip(av)}`;
      return `<label class="cmp-acc-chip" data-ok="${st.ok ? 1 : 0}" data-premium="${a.is_premium ? 1 : 0}" data-brand="${esc(r.brand)}" title="${esc(tip)}"
          style="${a.is_premium ? 'border-color:var(--orange);' : ''}${st.ok ? '' : 'opacity:.55;'}">
        <input type="checkbox" class="cmp-acc-checkbox" value="${a.id}" ${isChecked(r) ? 'checked' : ''} onchange="Members._cmpAccCount()">
        <span style="width:7px;height:7px;border-radius:50%;background:${st.color};flex:none"></span>
        <span>${a.is_premium ? '⭐ ' : ''}${esc(shortName(name))}${st.ok ? '' : ` <span style="color:${st.color};font-size:.85em">· ${esc(st.label)}</span>`}${av ? ` <span class="cmp-acc-cd" data-acc="${a.id}" style="font-size:.85em;color:var(--text2)"></span>` : ''}</span>
      </label>`;
    }).join('') || '<span style="color:var(--text3)">Chưa có tài khoản</span>';
    this._cmpAccCount();
    this._cmpTick();
    // Local 30s re-render of the countdown text only while the modal is open (no server calls).
    clearInterval(this._cmpTimer);
    this._cmpTimer = setInterval(() => {
      if (!document.getElementById('campaign-modal')?.classList.contains('open')) { clearInterval(this._cmpTimer); return; }
      this._cmpTick();
    }, 30000);
  },

  // ── Countdown / schedule helpers ──
  _fmtLeft(sec) {
    sec = Math.max(0, Math.round(sec));
    const d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60);
    if (d) return `${d}d${h}h`;
    if (h) return `${h}h${String(m).padStart(2, '0')}m`;
    return `${Math.max(1, m)}m`;
  },

  // Wall clock 'YYYY-MM-DD HH:MM' in IANA tz → unix seconds.
  _wallToUnix(wall, tz) {
    const [d, t] = String(wall).replace('T', ' ').split(' ');
    const [Y, M, D] = d.split('-').map(Number), [h, mi] = (t || '0:0').split(':').map(Number);
    const guess = Date.UTC(Y, M - 1, D, h, mi) / 1000;
    return guess - (this._unixToWall(guess, tz, true) - guess);
  },

  // unix seconds → 'YYYY-MM-DDTHH:MM' in tz (asUnix: that wall clock read as UTC, for offset math).
  _unixToWall(ts, tz, asUnix) {
    const p = {};
    new Intl.DateTimeFormat('en-GB', { timeZone: tz, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
      .formatToParts(new Date(ts * 1000)).forEach(x => { p[x.type] = x.value; });
    if (asUnix) return Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute) / 1000;
    return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;
  },

  _cmpCdText(av, now) {
    if (av.unknown) return '· không rõ hạn';
    if (av.available_at && av.available_at > now) return `· còn ${this._fmtLeft(av.available_at - now)}`;
    return '· mở lại khi chạy';
  },

  _cmpAvailTip(av) {
    const names = { flag: 'Flag (≥5 lỗi/24h)', peerflood: 'PeerFlood', spambot: 'SpamBot', paused: 'Tạm dừng' };
    const parts = (av.reasons || []).map(r => names[r.kind] + (r.until
      ? ` đến ${new Date(r.until * 1000).toLocaleString('vi-VN')}`
      : r.kind === 'paused' ? (r.auto ? ' (hỏi lại SpamBot trước khi chạy)' : ' (thủ công)') : ' (không rõ hạn)'));
    return 'Hạn chế: ' + parts.join(', ');
  },

  _cmpTick() {
    const now = Date.now() / 1000;
    document.querySelectorAll('#cmp-accounts-chips .cmp-acc-cd').forEach(el => {
      const av = this._availCache?.[el.dataset.acc];
      if (av) el.textContent = this._cmpCdText(av, now);
    });
    this._cmpScheduleHint();
  },

  // Ticked accounts that are restricted, with their release info.
  _cmpSelectedRestricted() {
    return [...document.querySelectorAll('#cmp-accounts-chips .cmp-acc-checkbox:checked')]
      .map(cb => ({ id: cb.value, av: this._availCache?.[cb.value], name: (cb.closest('.cmp-acc-chip')?.textContent || '').trim().split('·')[0].trim() }))
      .filter(x => x.av);
  },

  alignScheduleToAccounts() {
    const sel = this._cmpSelectedRestricted();
    const now = Date.now() / 1000;
    const timed = sel.filter(x => !x.av.unknown && x.av.available_at && x.av.available_at > now);
    if (!timed.length) {
      App.toast(sel.some(x => x.av.unknown)
        ? 'Acc hạn chế đã chọn không rõ hạn — không khớp giờ được'
        : 'Các acc đã chọn không còn hạn chế có thời hạn — chạy ngay được', 'info');
      return;
    }
    const target = Math.ceil((Math.max(...timed.map(x => x.av.available_at)) + 300) / 300) * 300; // +5 min, round to 5 min
    const toggle = document.getElementById('cmp-schedule-toggle');
    if (toggle && !toggle.checked) { toggle.checked = true; this.toggleScheduleSection(); }
    const tz = document.getElementById('cmp-target-timezone')?.value || 'Asia/Ho_Chi_Minh';
    document.getElementById('cmp-scheduled-at').value = this._unixToWall(target, tz);
    this._cmpScheduleHint();
  },

  _cmpScheduleHint() {
    const hint = document.getElementById('cmp-schedule-hint');
    if (!hint) return;
    const at = document.getElementById('cmp-scheduled-at')?.value;
    const tz = document.getElementById('cmp-target-timezone')?.value || 'Asia/Ho_Chi_Minh';
    const now = Date.now() / 1000;
    const sel = this._cmpSelectedRestricted();
    const bits = [];
    if (at) {
      const ts = this._wallToUnix(at, tz);
      bits.push(ts <= now ? '<span style="color:var(--red)">Giờ này đã qua</span>' : `Chạy sau <b>${this._fmtLeft(ts - now)}</b>`);
      const late = sel.filter(x => x.av.unknown || (x.av.available_at && x.av.available_at > ts));
      if (late.length) bits.push(`<span style="color:var(--orange)">⚠ ${late.length} acc vẫn bị hạn chế lúc đó: ${late.map(x => esc(x.name)).join(', ')} — bị bỏ qua tới khi hết hạn</span>`);
      else if (sel.length) bits.push('<span style="color:var(--green)">✓ Acc đã chọn đều hết hạn chế trước giờ chạy</span>');
    } else {
      const timed = sel.filter(x => !x.av.unknown && x.av.available_at > now);
      if (timed.length) bits.push(`Acc hạn chế cuối cùng hết hạn sau <b>${this._fmtLeft(Math.max(...timed.map(x => x.av.available_at)) - now)}</b>`);
    }
    hint.innerHTML = bits.join(' · ');
  },

  quickPickCampaignAccounts(sel) {
    document.querySelectorAll('#cmp-accounts-chips .cmp-acc-chip').forEach(l => {
      const cb = l.querySelector('.cmp-acc-checkbox'), d = l.dataset;
      cb.checked = sel === 'all' ? true : sel === 'none' ? false
        : d.ok === '1' && (sel === 'ok' || (sel === 'premium' && d.premium === '1') || (sel.startsWith('brand:') && d.brand === sel.slice(6)));
    });
    this._cmpAccCount();
  },

  _cmpAccCount() {
    const el = document.getElementById('cmp-acc-count');
    if (!el) return;
    const all = document.querySelectorAll('#cmp-accounts-chips .cmp-acc-checkbox');
    const on = [...all].filter(c => c.checked);
    const bad = on.filter(c => c.closest('.cmp-acc-chip').dataset.ok !== '1').length;
    el.innerHTML = `Đã chọn <b>${on.length}</b>/${all.length}${bad ? ` · <span style="color:var(--orange)">${bad} không khỏe</span>` : ''}`;
    this._cmpScheduleHint();
  },

  loadOutreachTemplate(type) {
    const msgList = document.getElementById('cmp-messages-list');
    if (!msgList) return;
    
    let text = '';
    if (type === 'crypto') {
      text = "Bác ơi, cho em hỏi bên bác có đang trade mảng Web3/Crypto không nhỉ? Em muốn hỏi kinh nghiệm chút với ạ...";
    } else if (type === 'networking') {
      text = "Chào bác, em thấy bác trong nhóm Telegram chung, cho em nhắn tin kết nối trao đổi chút chuyên môn công việc được không ạ?";
    }

    if (!msgList.children.length) {
      this.addCampaignMessage();
    }

    const firstTextarea = msgList.querySelector('.cmp-msg-content');
    if (firstTextarea) {
      firstTextarea.value = text;
      this.updateMsgCharCounter(firstTextarea);
      App.toast('✨ Đã tải mẫu kịch bản outreach!', 'success');
    }
  },

  addCampaignMessage() {
    const list = document.getElementById('cmp-messages-list');
    const idx = list.children.length;
    const div = document.createElement('div');
    div.className = 'cmp-msg-item';
    div.style.cssText = 'display:flex;gap:10px;align-items:flex-start;background:rgba(0,0,0,0.25);border:1px solid color-mix(in srgb, var(--text) 8%, transparent);border-radius:10px;padding:12px;';
    div.innerHTML = `
      <span style="color:var(--accent-text);font-size:12px;font-weight:700;margin-top:8px">#${idx + 1}</span>
      <div style="flex:1;display:flex;flex-direction:column;gap:6px">
        <textarea class="form-input cmp-msg-content" rows="4" style="width:100%;min-height:100px;resize:vertical;font-size:13px;line-height:1.5;background:rgba(0,0,0,0.3);border:1px solid color-mix(in srgb, var(--text) 12%, transparent);border-radius:8px;padding:10px;color:#e5e7eb" placeholder="Nội dung tin nhắn...&#10;Biến cá nhân hóa: {name}, {first_name}, {last_name}, {full_name}, {username}" oninput="Members.updateMsgCharCounter(this)"></textarea>
        <div class="cmp-msg-char-counter" style="display:flex;align-items:center;gap:8px;font-size:11px;color:var(--text2)">
          <span class="cmp-char-count">0 ký tự</span>
          <span class="cmp-caption-warn" style="display:none;color:var(--orange);font-weight:600">⚠️ Caption ảnh/video tối đa 1024 ký tự!</span>
        </div>
        <!-- Image attachment row -->
        <div class="cmp-msg-media-row" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:2px">
          <input type="file" class="cmp-msg-file-input" accept="image/*,video/*,.pdf,.doc,.docx" style="display:none" onchange="Members.handleMsgFileUpload(this)">
          <button type="button" class="btn btn-ghost btn-sm" onclick="this.parentElement.querySelector('.cmp-msg-file-input').click()" style="font-size:12px;padding:4px 10px;border:1px solid color-mix(in srgb, var(--text) 12%, transparent);background:color-mix(in srgb, var(--text) 4%, transparent);color:var(--text2)">
            📎 Đính kèm ảnh/file
          </button>
          <div class="cmp-msg-media-preview" style="display:none;align-items:center;gap:6px;padding:4px 8px;background:var(--bg2);border-radius:6px;border:1px solid var(--border)"></div>
        </div>
        <input type="hidden" class="cmp-msg-media-path" value="">
        <input type="hidden" class="cmp-msg-media-type" value="text">
      </div>
      <button class="btn btn-danger btn-sm" onclick="this.parentElement.remove()" style="margin-top:4px;padding:4px 8px;font-size:12px" title="Xóa tin nhắn này">✕</button>
    `;
    list.appendChild(div);
  },

  async handleMsgFileUpload(fileInput) {
    const file = fileInput.files[0];
    if (!file) return;

    const msgItem = fileInput.closest('.cmp-msg-item');
    if (!msgItem) return;

    const previewDiv = msgItem.querySelector('.cmp-msg-media-preview');
    const mediaPathInput = msgItem.querySelector('.cmp-msg-media-path');
    const mediaTypeInput = msgItem.querySelector('.cmp-msg-media-type');

    // Show uploading state
    previewDiv.style.display = 'flex';
    previewDiv.innerHTML = `<span style="font-size:12px;color:var(--text-muted)">⏳ Đang tải lên...</span>`;

    try {
      const formData = new FormData();
      formData.append('file', file);

      const res = await fetch('/api/upload', { method: 'POST', body: formData });
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail || 'Upload thất bại');

      // Determine media type from extension
      const ext = (d.filename || '').split('.').pop().toLowerCase();
      const imageExts = ['jpg', 'jpeg', 'png', 'gif', 'webp'];
      const videoExts = ['mp4', 'mov', 'avi', 'mkv', 'webm'];
      let msgType = 'document';
      if (imageExts.includes(ext)) msgType = 'photo';
      else if (videoExts.includes(ext)) msgType = 'video';

      // Store media info
      mediaPathInput.value = d.path;
      mediaTypeInput.value = msgType;

      // Show preview
      let previewHtml = '';
      if (msgType === 'photo') {
        previewHtml = `
          <img src="/api/media/${d.filename}" style="width:48px;height:48px;object-fit:cover;border-radius:6px;border:1px solid var(--border)">
          <div style="font-size:12px">
            <div style="font-weight:500;color:var(--text1)">${esc(d.original_name)}</div>
            <div style="color:var(--text-muted)">${(d.size / 1024).toFixed(1)} KB</div>
          </div>
        `;
      } else if (msgType === 'video') {
        previewHtml = `
          <span style="font-size:24px">🎬</span>
          <div style="font-size:12px">
            <div style="font-weight:500;color:var(--text1)">${esc(d.original_name)}</div>
            <div style="color:var(--text-muted)">${(d.size / 1024 / 1024).toFixed(2)} MB</div>
          </div>
        `;
      } else {
        previewHtml = `
          <span style="font-size:24px">📄</span>
          <div style="font-size:12px">
            <div style="font-weight:500;color:var(--text1)">${esc(d.original_name)}</div>
            <div style="color:var(--text-muted)">${(d.size / 1024).toFixed(1)} KB</div>
          </div>
        `;
      }
      previewHtml += `<button class="btn btn-ghost btn-sm" onclick="Members.removeMsgMedia(this)" style="font-size:11px;padding:2px 6px;color:var(--danger)">✕</button>`;
      previewDiv.innerHTML = previewHtml;
      previewDiv.style.display = 'flex';

      App.toast(`Đã tải lên: ${d.original_name}`, 'success');

      // Update char counter to show caption warning
      const textarea = msgItem.querySelector('.cmp-msg-content');
      if (textarea) Members.updateMsgCharCounter(textarea);
    } catch (e) {
      previewDiv.innerHTML = `<span style="font-size:12px;color:var(--danger)">❌ ${esc(e.message)}</span>`;
      App.toast(e.message, 'error');
    }

    // Reset file input so same file can be re-selected
    fileInput.value = '';
  },

  removeMsgMedia(btn) {
    const msgItem = btn.closest('.cmp-msg-item');
    if (!msgItem) return;
    const previewDiv = msgItem.querySelector('.cmp-msg-media-preview');
    const mediaPathInput = msgItem.querySelector('.cmp-msg-media-path');
    const mediaTypeInput = msgItem.querySelector('.cmp-msg-media-type');
    if (previewDiv) { previewDiv.style.display = 'none'; previewDiv.innerHTML = ''; }
    if (mediaPathInput) mediaPathInput.value = '';
    if (mediaTypeInput) mediaTypeInput.value = 'text';
    // Update char counter (caption warning no longer needed)
    const textarea = msgItem.querySelector('.cmp-msg-content');
    if (textarea) Members.updateMsgCharCounter(textarea);
  },

  updateMsgCharCounter(textarea) {
    const msgItem = textarea.closest('.cmp-msg-item');
    if (!msgItem) return;
    const len = textarea.value.length;
    const countEl = msgItem.querySelector('.cmp-char-count');
    const warnEl = msgItem.querySelector('.cmp-caption-warn');
    const mediaType = (msgItem.querySelector('.cmp-msg-media-type') || {}).value || 'text';
    const hasMedia = mediaType !== 'text';

    if (countEl) {
      countEl.textContent = len + ' ký tự';
      if (hasMedia && len > 1024) {
        countEl.style.color = 'var(--red)';
        countEl.style.fontWeight = '600';
      } else if (hasMedia && len > 900) {
        countEl.style.color = 'var(--orange)';
        countEl.style.fontWeight = '600';
      } else {
        countEl.style.color = 'var(--text2)';
        countEl.style.fontWeight = 'normal';
      }
    }
    if (warnEl) {
      if (hasMedia) {
        warnEl.style.display = 'inline';
        if (len > 1024) {
          warnEl.textContent = `🚫 Vượt ${len - 1024} ký tự! Caption ảnh/video tối đa 1024.`;
          warnEl.style.color = 'var(--red)';
        } else if (len > 900) {
          warnEl.textContent = `⚠️ Còn ${1024 - len} ký tự. Caption ảnh/video tối đa 1024.`;
          warnEl.style.color = 'var(--orange)';
        } else {
          warnEl.textContent = `📝 Caption ảnh/video tối đa 1024 ký tự (còn ${1024 - len})`;
          warnEl.style.color = 'var(--text2)';
        }
      } else {
        warnEl.style.display = 'none';
      }
    }
  },

  async saveCampaign() {
    const name = (document.getElementById('cmp-name')?.value || "").trim();
    const jobId = document.getElementById('cmp-scrape-job')?.value;
    const delayMin = parseInt(document.getElementById('cmp-delay-min')?.value) || 30;
    const delayMax = parseInt(document.getElementById('cmp-delay-max')?.value) || 90;
    const dailyLimitPremium = parseInt(document.getElementById('cmp-daily-limit')?.value || document.getElementById('cmp-daily-limit-premium')?.value) || 50;
    const dailyLimitNormal = 10; // Auto set cứng tài khoản Telegram Free max 10 DM/ngày
    const useAi = document.getElementById('cmp-ai-remix')?.checked;
    const agentIdVal2 = document.getElementById('cmp-ai-agent')?.value;
    const aiAgentId = agentIdVal2 ? parseInt(agentIdVal2) : null;
    const autoNative = document.getElementById('cmp-auto-translate-native')?.checked ? 1 : 0;
    const autoResume = document.getElementById('cmp-auto-resume')?.checked ?? true;
    const excludePrev = document.getElementById('cmp-exclude-previous')?.checked ?? true;
    const minLeadScore = parseInt(document.getElementById('cmp-min-lead-score')?.value) || 0;

    // --- NEW SCHEDULE FIELDS ---
    const scheduleEnabled = document.getElementById('cmp-schedule-toggle')?.checked || false;
    const scheduledAt = document.getElementById('cmp-scheduled-at')?.value;
    const targetTimezone = document.getElementById('cmp-target-timezone')?.value;

    if (!name) { App.toast('Nhập tên campaign', 'error'); return; }
    if (!jobId) { App.toast('Chọn nguồn members', 'error'); return; }
    if (scheduleEnabled && !scheduledAt) { App.toast('Vui lòng chọn ngày giờ chạy', 'error'); return; }

    // Collect sender accounts
    const accCheckboxes = document.querySelectorAll('.cmp-acc-checkbox:checked');
    const senderIds = Array.from(accCheckboxes).map(cb => parseInt(cb.value));
    if (!senderIds.length) { App.toast('Chọn ít nhất 1 tài khoản gửi', 'error'); return; }

    // Collect messages (with media support)
    const msgItems = document.querySelectorAll('.cmp-msg-item');
    const messages = [];
    msgItems.forEach((item, i) => {
      const content = item.querySelector('.cmp-msg-content')?.value.trim() || '';
      const mediaPath = item.querySelector('.cmp-msg-media-path')?.value || '';
      const mediaType = item.querySelector('.cmp-msg-media-type')?.value || 'text';

      if (content || mediaPath) {
        messages.push({
          msg_order: i,
          msg_type: mediaPath ? mediaType : 'text',
          content,
          media_path: mediaPath || undefined
        });
      }
    });
    if (!messages.length) { App.toast('Thêm ít nhất 1 tin nhắn', 'error'); return; }

    try {
      const r = await MembersAPI.createCampaign({
        name,
        scrape_job_id: jobId,
        sender_account_ids: senderIds,
        messages,
        delay_min: delayMin,
        delay_max: delayMax,
        daily_limit_premium: dailyLimitPremium,
        daily_limit_normal: dailyLimitNormal,
        use_ai_remix: useAi,
        ai_agent_id: aiAgentId,
        auto_translate_native: autoNative,
        exclude_previous_dms: excludePrev,
        auto_resume: autoResume,
        min_lead_score: minLeadScore,
        // --- NEW FIELDS ---
        schedule_enabled: scheduleEnabled,
        scheduled_at: scheduleEnabled ? scheduledAt : undefined,
        target_timezone: scheduleEnabled ? targetTimezone : undefined,
      });
      
      // Update success message
      if (scheduleEnabled) {
        App.toast(`Đã lên lịch campaign! (${r.total_targets} targets)`, 'success');
      } else {
        App.toast(`Campaign tạo thành công! (${r.total_targets} targets)`, 'success');
      }
      
      this.closeCampaignModal();
      this.loadCampaigns();
    } catch (e) {
      App.toast(e.message, 'error');
    }
  },

  toggleScheduleSection() {
    const enabled = document.getElementById('cmp-schedule-toggle')?.checked;
    const fields = document.getElementById('cmp-schedule-fields');
    if (enabled) {
      fields.style.display = 'block';
      const tzSelect = document.getElementById('cmp-target-timezone');
      if (tzSelect && !tzSelect.value) tzSelect.value = 'Asia/Ho_Chi_Minh';
      this._cmpScheduleHint();
    } else {
      fields.style.display = 'none';
    }
  },

  async cloneCampaign(campaignId) {
    const c = this._campaigns.find(x => x.id === campaignId);
    if (!c) {
      App.toast('Không tìm thấy campaign', 'error');
      return;
    }

    const successCount = c.sent_count || 0;
    const failedCount = c.failed_count || 0;
    const totalExcluded = successCount + failedCount;
    const remaining = Math.max(0, (c.total_targets || 0) - totalExcluded);

    const confirmMsg = `📑 Nhân bản chiến dịch "${c.name}"?\n\n` +
      `✅ Thành công: ${successCount}\n` +
      `❌ Lỗi: ${failedCount}\n` +
      `━━━━━━━━━━━━━━━━━\n` +
      `🚫 Loại trừ: ${totalExcluded} member\n` +
      `👥 Còn lại sẽ DM: ~${remaining} member\n\n` +
      `Chiến dịch mới sẽ chỉ DM những member chưa từng liên hệ.`;

    if (!confirm(confirmMsg)) return;

    try {
      const res = await MembersAPI.cloneCampaign(campaignId, {
        name: `${c.name} - Clone`,
        exclude_source_results: true
      });
      App.toast(
        `✅ Đã nhân bản! Campaign #${res.campaign_id} — ${res.total_targets} member (loại trừ ${res.excluded_count || 0})`,
        'success'
      );
      await this.loadCampaigns();
    } catch (e) {
      App.toast(`Lỗi clone: ${e.message}`, 'error');
    }
  },

  switchSubTab(tab) {
    const kolTab = document.getElementById('members-tab-kol');
    const kolView = document.getElementById('members-subview-kol');
    if (kolTab) kolTab.classList.toggle('active', tab === 'kol');
    if (kolView) kolView.classList.toggle('hidden', tab !== 'kol');
    if (tab === 'kol') {
      ['scrape', 'similar'].forEach(t => {
        document.getElementById(`members-tab-${t}`)?.classList.remove('active');
        document.getElementById(`members-subview-${t}`)?.classList.add('hidden');
      });
      this.loadKolDirectory();
      return;
    }
    const scrapeTab = document.getElementById('members-tab-scrape');
    const similarTab = document.getElementById('members-tab-similar');
    const scrapeView = document.getElementById('members-subview-scrape');
    const similarView = document.getElementById('members-subview-similar');
    
    if (tab === 'scrape') {
      scrapeTab.classList.add('active');
      similarTab.classList.remove('active');
      scrapeView.classList.remove('hidden');
      similarView.classList.add('hidden');
    } else {
      similarTab.classList.add('active');
      scrapeTab.classList.remove('active');
      similarView.classList.remove('hidden');
      scrapeView.classList.add('hidden');
      this.populatePremiumCheckboxes();
      this.restoreDeepCrawlState();
    }
  },

  async restoreDeepCrawlState() {
    try {
      const res = await fetch('/api/members/deep-crawl/status');
      if (!res.ok) return;
      const s = await res.json();
      this._restoreDeepCrawlUI(s);
    } catch (e) {
      console.error('Error restoring deep crawl status:', e);
    }
    this.loadCrawlHistory();
  },

  // ── Crawl history (incl. runs/queue interrupted by a server restart) ──
  async loadCrawlHistory() {
    if (!document.getElementById('sim-history-list')) return;
    try {
      const r = await fetch('/api/members/deep-crawl/history');
      if (!r.ok) return;
      this._crawlHist = (await r.json()).history || [];
    } catch (e) { return; }
    this._renderCrawlHistory();
  },

  // Rows with stored results open them (👁 or a click on the row); the one on screen is highlighted.
  _renderCrawlHistory() {
    const list = document.getElementById('sim-history-list');
    if (!list) return;
    const hist = this._crawlHist || [];
    const escH = (t) => String(t ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const interrupted = hist.filter(h => h.status === 'interrupted' && !h.rerun).length;
    const badge = document.getElementById('sim-history-badge');
    if (badge) badge.innerHTML = hist.length
      ? `(${hist.length}${interrupted ? ` · <span style="color:var(--danger)">${interrupted} bị ngắt</span>` : ''})` : '';
    const det = document.getElementById('sim-history');
    if (det && interrupted && !this._historyAutoOpened) { this._historyAutoOpened = true; det.open = true; }
    if (!hist.length) {
      list.innerHTML = '<div style="font-size:12px;color:var(--text-muted)">Chưa có lần cào nào.</div>';
      return;
    }
    const st = {
      running: ['Đang chạy', 'var(--accent)'], completed: ['Xong', 'var(--success)'],
      stopped: ['Đã dừng', 'var(--warning)'], error: ['Lỗi', 'var(--danger)'],
      flood_wait: ['FloodWait', 'var(--warning)'], interrupted: ['Bị ngắt', 'var(--danger)'],
    };
    list.innerHTML = hist.map(h => {
      const [lbl, col] = st[h.status] || [h.status, 'var(--text2)'];
      const when = (h.ended_at || h.started_at || '').replace('T', ' ').slice(5, 16);
      const link = h.channel_link.length > 40 ? h.channel_link.slice(0, 40) + '…' : h.channel_link;
      const live = h.status === 'running';
      const canView = !!h.has_results && !live;
      const viewing = canView && this._dcViewHid === h.id;
      const tip = [h.note, canView ? 'Bấm để xem lại kết quả của lần cào này' : ''].filter(Boolean).join(' · ');
      const bg = viewing ? 'color-mix(in srgb, var(--accent) 16%, transparent);box-shadow:inset 3px 0 0 var(--accent)'
        : 'color-mix(in srgb, var(--text) 4%, transparent)';
      return `<div${canView ? ` onclick="Members.viewCrawlRun('${h.id}')"` : ''} style="display:flex;align-items:center;gap:8px;padding:5px 8px;border-radius:6px;font-size:12px;${canView ? 'cursor:pointer;' : ''}background:${bg}" title="${escH(tip)}">
        <span style="padding:1px 7px;border-radius:9px;background:${col};color:#fff;font-weight:600;white-space:nowrap">${lbl}</span>
        <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escH(link)} <span style="color:var(--text-muted)">· depth ${h.max_depth} · <span${live ? ' id="sim-hist-live"' : ''}>${h.channels_found || 0} kênh · ${h.contacts_found || 0} contact</span>${h.rerun ? ' · đã chạy lại' : ''}</span></span>
        <span style="color:var(--text-muted);white-space:nowrap">${escH(when)}</span>
        ${canView ? `<button class="btn btn-ghost btn-sm" style="padding:1px 8px" onclick="event.stopPropagation();Members.viewCrawlRun('${h.id}')" title="Xem lại kết quả của lần cào này">👁</button>` : ''}
        ${!live ? `<button class="btn btn-ghost btn-sm" style="padding:1px 8px" onclick="event.stopPropagation();Members.rerunCrawl('${h.id}')" title="Chạy lại với cùng kênh và cài đặt (đang có lần cào chạy thì vào hàng đợi)">▶</button>` : ''}
        <button style="background:none;border:none;color:var(--text-muted);cursor:pointer" onclick="event.stopPropagation();Members.deleteCrawlHistory('${h.id}')" title="Xóa khỏi lịch sử (kèm kết quả đã lưu)">✕</button>
      </div>`;
    }).join('');
  },

  // ── Open an earlier crawl's stored results from the history ──
  async viewCrawlRun(id) {
    const prev = [this._dcViewHid, this._dcViewMeta];
    this._dcViewHid = id;
    this._dcViewMeta = (this._crawlHist || []).find(h => h.id === id) || null;
    this._renderCrawlHistory();
    const ok = await this._fetchDeepCrawlResults();
    if (ok === null) return;  // another view was opened meanwhile
    if (!ok) {
      [this._dcViewHid, this._dcViewMeta] = prev;
      this.loadCrawlHistory();
      this._updateViewBanner();
      return;
    }
    document.getElementById('sim-view-banner')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  },

  async exitCrawlView() {
    this._dcViewHid = null;
    this._dcViewMeta = null;
    this._renderCrawlHistory();
    await this._fetchDeepCrawlResults();
    const s = this._dcLastStatus;
    if (this._crawlLive(s) && !(this._similarLeads || []).length) this._showCrawlRunningEmpty(s);
  },

  // What the table shows: an older run opened from history, or the previous run's
  // results while the next (queued) crawl is running.
  _updateViewBanner() {
    const el = document.getElementById('sim-view-banner');
    if (!el) return;
    const escH = (t) => String(t ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const short = (u) => { u = String(u || ''); return u.length > 48 ? u.slice(0, 48) + '…' : u; };
    const s = this._dcLastStatus || {};
    let html = '';
    if (this._dcViewHid) {
      const h = this._dcViewMeta || {};
      const when = (h.ended_at || h.started_at || '').replace('T', ' ').slice(5, 16);
      html = `<span>🕘 Đang xem kết quả cũ: <b>${escH(short(this._dcShownSource || h.channel_link))}</b>${h.max_depth ? ` · depth ${h.max_depth}` : ''}${when ? ` · ${escH(when)}` : ''}</span>
        <button class="btn btn-ghost btn-sm" style="margin-left:auto;padding:1px 10px" onclick="Members.exitCrawlView()">↩ Về kết quả mới nhất</button>`;
    } else if (this._crawlLive(s) && s.source_url && this._dcShownSource && this._dcShownSource !== s.source_url
               && (this._similarLeads || []).length) {
      html = `<span>Đang xem kết quả của <b>${escH(short(this._dcShownSource))}</b> · đang cào <b>${escH(short(s.source_url))}</b> — kết quả mới tự hiện khi xong.</span>`;
    }
    el.innerHTML = html;
    el.classList.toggle('hidden', !html);
  },

  _showCrawlRunningEmpty(s) {
    const empty = document.getElementById('sim-empty-state');
    const p = empty && empty.querySelector('p');
    if (!p) return;
    empty.classList.remove('hidden');
    p.textContent = `Đang chạy Deep Crawl cho "${(s && s.source_url) || ''}"... Kết quả sẽ tự hiển thị khi xong. Kết quả các lần trước: bấm 👁 trong "Lịch sử cào".`;
  },

  // A crawl is live while it runs, waits out a FloodWait, or pauses before the next queued link.
  _crawlLive(s) { return !!s && (s.status === 'running' || !!s.busy); },

  // The start button never locks while a crawl runs: it adds the link to the queue instead.
  _setCrawlBtn(s) {
    const btn = document.getElementById('sim-btn-search');
    const stopBtn = document.getElementById('sim-btn-stop');
    const q = (s && s.queue_count) || 0;
    if (btn) {
      btn.disabled = false;
      if (this._crawlLive(s) || q) {
        btn.textContent = `➕ Thêm vào hàng đợi${q ? ` (${q} đang chờ)` : ''}`;
        btn.title = 'Đang có lần cào chạy — link mới vào hàng đợi và tự chạy khi lần hiện tại xong. Kết quả từng lần xem lại ở "Lịch sử cào".';
      } else {
        btn.textContent = '🚀 Bắt đầu Deep Crawl';
        btn.title = '';
      }
    }
    if (stopBtn) stopBtn.classList.toggle('hidden', !(s && (s.status === 'running' || s.status === 'flood_wait')));
  },

  async rerunCrawl(id) {
    try {
      const r = await fetch(`/api/members/deep-crawl/history/${id}/rerun`, { method: 'POST' });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || 'Không chạy lại được');
      App.toast(d.queued ? `📥 ${d.message}` : '🚀 Đã chạy lại Deep Crawl', 'success');
      if (!d.queued) this._dcLastStatus = { ...(this._dcLastStatus || {}), status: 'running', busy: true };
      document.getElementById('sim-progress-panel')?.classList.remove('hidden');
      this.loadCrawlHistory();
      this._startCrawlPolling();
    } catch (e) { App.toast(e.message, 'error'); }
  },

  async deleteCrawlHistory(id) {
    await fetch(`/api/members/deep-crawl/history/${id}`, { method: 'DELETE' }).catch(() => {});
    if (this._dcCaches) delete this._dcCaches[id];
    if (this._dcViewHid === id) this.exitCrawlView();
    this.loadCrawlHistory();
  },

  _restoreDeepCrawlUI(s) {
    if (!s) return;
    if (s.source_url) this._crawlSource = s.source_url;
    const progressPanel = document.getElementById('sim-progress-panel');
    this._dcLastStatus = s;
    this._lastHistKey = this._histKey(s);
    this._setCrawlBtn(s);
    this._renderQueuePanel(s.queue || [], s.queue_count || 0);

    if (this._crawlLive(s)) {
      if (progressPanel) progressPanel.classList.remove('hidden');
      this._renderDeepCrawlProgressFromState(s);
      // Keep what the table shows (an opened history run, or the previous run's results):
      // the running crawl has no results yet.
      if (this._dcViewHid) this._fetchDeepCrawlResults();
      else if (!(this._similarLeads || []).length) this._showCrawlRunningEmpty(s);
      this._updateViewBanner();
      this._startCrawlPolling();
      return;
    }

    if (s.results_count > 0 || this._dcViewHid || s.status === 'completed' || s.status === 'stopped' || s.status === 'interrupted') {
      if (progressPanel) progressPanel.classList.remove('hidden');
      this._renderDeepCrawlProgressFromState(s);
      this._fetchDeepCrawlResults();
    }
  },

  // ── Premium Account Checkboxes (multi-select for rotation) ──
  async populatePremiumCheckboxes() {
    const container = document.getElementById('sim-premium-checkboxes');
    if (!container) return;
    const now = Date.now();
    if (!this._accounts?.length || !this._accountsCachedAt || (now - this._accountsCachedAt) > 30000) {
      try {
        const d = await API.getAccounts();
        this._accounts = d.accounts || [];
        this._accountsCachedAt = now;
      } catch (e) { this._accounts = []; }
    }
    const premiums = this._accounts.filter(a => a.is_logged_in && a.is_premium);
    if (!premiums.length) {
      container.innerHTML = `<span style="color:var(--danger);font-size:13px">⚠️ Cần ít nhất 1 tài khoản Premium để sử dụng tính năng này</span>`;
      return;
    }
    // Paused accounts never crawl ("tạm dừng = dừng hẳn"). Flagged/PeerFlood accounts that are
    // not paused can: those limits only block messaging strangers, the crawl only reads.
    // Opt-in: accounts paused only because of PeerFlood may crawl too (read-only).
    if (this._pfCrawl === undefined) {
      try {
        const r = await fetch('/api/settings/deep_crawl_peerflood_paused');
        this._pfCrawl = (await r.json()).value === '1';
      } catch (e) { this._pfCrawl = false; }
    }
    const pfToggle = document.getElementById('sim-pf-crawl');
    if (pfToggle) pfToggle.checked = this._pfCrawl;
    const isPF = a => String(a.pause_reason || '').startsWith('PeerFlood');
    const blocked = a => !!a.is_paused && !(this._pfCrawl && isPF(a));
    const sorted = [...premiums].sort((a, b) => (+blocked(a)) - (+blocked(b)));
    container.innerHTML = sorted.map(a => {
      const ui = a.user_info;
      const name = ui ? [ui.first_name, ui.last_name].filter(Boolean).join(' ') : a.name;
      const uname = ui && ui.username ? '@' + ui.username : (a.phone || '');
      const label = name ? `${name} (${uname})` : (uname || `ID ${a.id}`);
      const paused = blocked(a);
      const pfRead = !!a.is_paused && !paused;
      const flagged = !a.is_paused && !!a.is_flagged;
      const tip = paused ? `Đang tạm dừng${a.pause_reason ? ': ' + a.pause_reason : ''} — không dùng để cào`
        : pfRead ? 'Tạm dừng vì PeerFlood — vẫn cào (chỉ đọc), không nhắn tin'
        : flagged ? `Bị flag${a.flag_reason ? ': ' + a.flag_reason : ''} — vẫn cào được (chỉ đọc)` : '';
      const tag = paused ? '<span style="font-size:11px;color:var(--text-muted)">⏸ tạm dừng</span>'
        : pfRead ? '<span style="font-size:11px;color:var(--orange)">⏸ PeerFlood · chỉ đọc</span>'
        : flagged ? '<span style="font-size:11px;color:var(--orange)">flag · chỉ đọc</span>' : '';
      return `
        <label title="${esc(tip)}" style="display:flex;align-items:center;gap:6px;cursor:${paused ? 'not-allowed' : 'pointer'};font-size:13px;padding:4px 8px;border-radius:6px;background:var(--bg1);border:1px solid var(--border)${paused ? ';opacity:.5' : ''}">
          <input type="checkbox" class="sim-premium-cb" value="${a.id}" ${paused ? 'disabled' : 'checked'}>
          <span>⭐ ${esc(label)}</span>${tag}
        </label>
      `;
    }).join('');
  },

  async togglePeerFloodCrawl(on) {
    try {
      const r = await fetch('/api/settings/deep_crawl_peerflood_paused', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value: on ? '1' : '0' }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      this._pfCrawl = on;
      App.toast(on ? 'Tài khoản tạm dừng vì PeerFlood sẽ được dùng để cào (chỉ đọc)' : 'Đã tắt: tài khoản tạm dừng không dùng để cào', 'success');
    } catch (e) {
      App.toast('Lưu tùy chọn thất bại: ' + e.message, 'error');
    }
    this.populatePremiumCheckboxes();
  },

  // ── Find Similar Channels (branches on depth) ──
  async findSimilarChannels() {
    const chanInput = document.getElementById('sim-channel-input');
    const depthSel = document.getElementById('sim-depth-select');
    const btn = document.getElementById('sim-btn-search');
    if (!chanInput || !depthSel || !btn) return;

    const channelLink = chanInput.value.trim();
    const depth = parseInt(depthSel.value) || 2;

    if (!channelLink) {
      App.toast('Vui lòng nhập link kênh hoặc username!', 'error');
      return;
    }

    // Collect selected premium accounts
    const checkboxes = document.querySelectorAll('.sim-premium-cb:checked');
    if (!checkboxes.length) {
      App.toast('Vui lòng chọn ít nhất 1 tài khoản Premium!', 'error');
      return;
    }
    const accountIds = Array.from(checkboxes).map(cb => parseInt(cb.value));

    if (depth === 1) {
      // Quick mode: use the original single-shot API with first selected account
      await this._quickSimilarChannels(accountIds[0], channelLink);
    } else {
      // Deep crawl mode
      await this.startDeepCrawl(accountIds, channelLink, depth);
    }
  },

  // ── Quick mode (depth 1, instant) ──
  async _quickSimilarChannels(accountId, channelLink) {
    const btn = document.getElementById('sim-btn-search');
    btn.disabled = true;
    btn.textContent = 'Đang quét...';

    try {
      const res = await fetch('/api/members/similar-channels', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ account_id: accountId, channel_link: channelLink })
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail || 'Không thể quét kênh tương tự');

      // Add depth=1 and parent info to each lead for consistent rendering
      const leads = (d.leads || []).map(l => ({
        ...l,
        depth: 1,
        parent_channel: channelLink
      }));
      this._dcViewHid = null;
      this._dcViewMeta = null;
      this._dcShownSource = channelLink;
      this._renderDeepCrawlResults(leads);
      this._renderCrawlHistory();
      this._updateViewBanner();
      App.toast(`Quét thành công! Tìm thấy ${leads.length} kênh tương tự.`, 'success');
    } catch (e) {
      App.toast(e.message, 'error');
    } finally {
      this._setCrawlBtn(this._dcLastStatus);
    }
  },

  // ── Start Deep Crawl (depth 2-4) ──
  async startDeepCrawl(accountIds, channelLink, depth) {
    const btn = document.getElementById('sim-btn-search');
    const progressPanel = document.getElementById('sim-progress-panel');

    btn.disabled = true;
    btn.textContent = '⏳ Đang gửi...';

    try {
      const res = await fetch('/api/members/deep-crawl', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          account_ids: accountIds,
          channel_link: channelLink,
          max_depth: depth,
          min_expand_score: Math.max(0, Math.min(100, parseInt(document.getElementById('sim-min-score')?.value, 10) || 0))
        })
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail || 'Không thể bắt đầu deep crawl');

      // Queued behind the running crawl: it starts on its own when that one ends.
      if (d.queued) {
        App.toast(`📥 ${d.message || 'Đã thêm link vào hàng chờ!'}`, 'success');
        this._startCrawlPolling();  // refreshes the queue panel + button right away
        return;
      }

      // A new crawl replaces the "latest" results: leave any history view and clear the
      // table (the previous run stays openable from the history).
      if (progressPanel) progressPanel.classList.remove('hidden');
      this._dcViewHid = null;
      this._dcViewMeta = null;
      this._dcShownSource = '';
      this._similarLeads = [];
      this._selectedContacts.clear();
      this._renderDeepCrawlResults([]);
      this._showCrawlRunningEmpty({ source_url: channelLink });
      this._renderCrawlHistory();
      this._updateViewBanner();

      App.toast(d.message || '🚀 Đã bắt đầu Deep Crawl!', 'success');
      // Remember a live crawl so the poll still reports it if it ends before the first tick.
      this._dcLastStatus = { ...(this._dcLastStatus || {}), status: 'running', busy: true, source_url: channelLink };
      this._setCrawlBtn(this._dcLastStatus);
      this._startCrawlPolling();
    } catch (e) {
      App.toast(e.message, 'error');
      this._setCrawlBtn(this._dcLastStatus);
      if (progressPanel && !this._crawlLive(this._dcLastStatus) && !(this._similarLeads || []).length) progressPanel.classList.add('hidden');
    }
  },

  // ── Render progress panel from a state object (used by restore + poll) ──
  _renderDeepCrawlProgressFromState(s) {
    const el = (id) => document.getElementById(id);
    const depthEl = el('sim-prog-depth');
    const foundEl = el('sim-prog-found');
    const processedEl = el('sim-prog-processed');
    const contactsEl = el('sim-prog-contacts');
    const queueEl = el('sim-prog-queue');
    const accountEl = el('sim-prog-account');
    const channelEl = el('sim-prog-channel');
    const statusEl = el('sim-progress-status');
    const barEl = el('sim-progress-bar');
    const errorsEl = el('sim-progress-errors');

    if (depthEl) depthEl.textContent = `${s.current_depth}/${s.max_depth}`;
    if (foundEl) foundEl.textContent = s.channels_found;
    if (processedEl) processedEl.textContent = s.channels_processed;
    if (contactsEl) {
      contactsEl.textContent = s.contacts_found;
      const extra = [];
      if (s.contacts_removed) extra.push(`đã loại ${s.contacts_removed} contact không phải người thật (kênh/nhóm/bot/đã xoá)`);
      if (s.branches_pruned) extra.push(`bỏ ${s.branches_pruned} nhánh điểm thấp`);
      contactsEl.title = extra.join(' · ');
      if (s.contacts_removed) contactsEl.textContent += ` (−${s.contacts_removed})`;
    }
    if (queueEl) queueEl.textContent = s.queue_remaining;
    if (accountEl) accountEl.textContent = s.current_account || '—';
    if (channelEl) channelEl.textContent = s.current_channel || '—';

    const totalWork = s.channels_processed + s.queue_remaining;
    const pct = totalWork > 0 ? Math.min(95, (s.channels_processed / totalWork) * 100) : 0;
    if (barEl) barEl.style.width = `${pct}%`;

    if (statusEl) {
      const statusMap = {
        idle: { text: 'Chờ lệnh', bg: 'var(--text2)' },
        running: { text: 'Running', bg: 'var(--accent)' },
        completed: { text: 'Hoàn thành ✓', bg: 'var(--success)' },
        stopped: { text: 'Đã dừng', bg: 'var(--warning)' },
        error: { text: 'Lỗi', bg: 'var(--danger)' },
        flood_wait: { text: 'FloodWait', bg: 'var(--warning)' },
        interrupted: { text: 'Bị ngắt (server dừng)', bg: 'var(--danger)' },
      };
      const info = statusMap[s.status] || statusMap.running;
      statusEl.textContent = info.text;
      statusEl.style.background = info.bg;
    }

    if (errorsEl) {
      if (s.errors && s.errors.length > 0) {
        errorsEl.classList.remove('hidden');
        errorsEl.innerHTML = s.errors.slice(-10).map(e => `<div>⚠️ ${typeof esc === 'function' ? esc(e) : e}</div>`).join('');
      } else {
        errorsEl.classList.add('hidden');
        errorsEl.innerHTML = '';
      }
    }

    // ── Render Queue Panel ──
    this._renderQueuePanel(s.queue || [], s.queue_count || 0);
    this._setCrawlBtn(s);
    const histLive = document.getElementById('sim-hist-live');
    if (histLive && s.status === 'running') histLive.textContent = `${s.channels_found || 0} kênh · ${s.contacts_found || 0} contact`;
  },

  _renderQueuePanel(queue, count) {
    let panel = document.getElementById('deep-crawl-queue-panel');
    if (count === 0 && (!queue || !queue.length)) {
      if (panel) panel.classList.add('hidden');
      return;
    }
    // Create panel if not exists
    if (!panel) {
      const progressPanel = document.getElementById('sim-progress-panel');
      const containerCard = document.querySelector('#members-subview-similar .card');
      if (!progressPanel && !containerCard) return;
      panel = document.createElement('div');
      panel.id = 'deep-crawl-queue-panel';
      panel.className = 'card';
      panel.style.cssText = 'margin-top:12px; margin-bottom:16px; padding:12px 16px; border:1px solid var(--border); border-radius:10px; background:color-mix(in srgb, var(--text) 3%, transparent);';
      if (progressPanel && progressPanel.parentNode) {
        progressPanel.parentNode.insertBefore(panel, progressPanel.nextSibling);
      } else if (containerCard) {
        containerCard.appendChild(panel);
      }
    }
    panel.classList.remove('hidden');

    let html = `<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">`;
    html += `<span style="font-weight:600; color:var(--text1)">📥 Hàng đợi chờ (${count}) <span style="font-weight:400;font-size:12px;color:var(--text-muted)">· tự chạy lần lượt khi lần hiện tại xong</span></span>`;
    if (count > 0) {
      html += `<button onclick="Members._clearQueue()" class="btn btn-small btn-danger" style="font-size:12px; padding:2px 10px;">Xóa tất cả</button>`;
    }
    html += `</div>`;

    if (queue && queue.length) {
      html += `<div style="display:flex; flex-direction:column; gap:6px;">`;
      queue.forEach((q, i) => {
        const raw = q.channel_link.length > 35 ? q.channel_link.substring(0, 35) + '...' : q.channel_link;
        const link = raw.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
        const meta = [`depth ${q.max_depth}`, q.min_expand_score != null ? `≥${q.min_expand_score}đ` : '', q.retry ? 'chờ hết FloodWait' : ''].filter(Boolean).join(' · ');
        html += `<div style="display:flex; justify-content:space-between; align-items:center; padding:6px 10px; background:color-mix(in srgb, var(--text) 4%, transparent); border-radius:6px; font-size:13px;">`;
        html += `<span><span style="color:var(--accent); font-weight:600;">#${i + 1}</span> ${link} <span style="color:var(--text2);">(${meta})</span></span>`;
        html += `<button onclick="Members._removeQueueItem(${i})" style="background:none; border:none; color:var(--danger); cursor:pointer; font-size:14px; padding:2px 6px;" title="Xóa">❌</button>`;
        html += `</div>`;
      });
      html += `</div>`;
    }
    panel.innerHTML = html;
  },

  async _removeQueueItem(index) {
    try {
      const res = await fetch(`/api/members/deep-crawl/queue/${index}`, { method: 'DELETE' });
      const d = await res.json();
      if (res.ok) {
        App.toast(`Đã xóa ${d.removed} khỏi hàng đợi`, 'success');
        this.restoreDeepCrawlState();
      }
    } catch (e) {
      App.toast('Lỗi xóa queue item', 'error');
    }
  },

  async _clearQueue() {
    if (!confirm('Xóa tất cả items trong hàng đợi?')) return;
    try {
      await fetch('/api/members/deep-crawl/queue', { method: 'DELETE' });
      App.toast('Đã xóa hàng đợi', 'success');
      const panel = document.getElementById('deep-crawl-queue-panel');
      if (panel) panel.classList.add('hidden');
      this.restoreDeepCrawlState();
    } catch (e) {
      App.toast('Lỗi xóa queue', 'error');
    }
  },

  // ── Poll Progress (3s, backing off to 30s while nothing changes) ──
  // Exactly one loop: every start bumps the generation, so an older loop (tab re-open,
  // queue add, rerun) ends at its next tick instead of doubling the requests.
  _deepCrawlPolling: false,
  _startCrawlPolling() {
    this._dcPollGen = (this._dcPollGen || 0) + 1;
    this._deepCrawlPolling = true;
    this._deepCrawlPollInterval = 3000;
    this._deepCrawlPrevState = null;
    this._pollDeepCrawlProgress(this._dcPollGen);
  },

  _histKey(s) { return `${s.status}|${s.history_id || s.source_url || ''}`; },

  async _pollDeepCrawlProgress(gen) {
    if (!this._deepCrawlPolling || gen !== this._dcPollGen) return;
    const again = (ms) => setTimeout(() => this._pollDeepCrawlProgress(gen), ms);

    try {
      const res = await fetch('/api/members/deep-crawl/status');
      if (!res.ok) {
        throw new Error(`HTTP error! status: ${res.status}`);
      }
      const s = await res.json();
      if (gen !== this._dcPollGen) return;
      const prev = this._dcLastStatus;
      this._dcLastStatus = s;
      if (s.source_url) this._crawlSource = s.source_url;

      // Update progress UI using shared helper
      this._renderDeepCrawlProgressFromState(s);

      // A run started or ended (incl. the next queued one) → refresh the history rows.
      const histKey = this._histKey(s);
      const changed = this._lastHistKey !== histKey;
      if (changed) {
        this._lastHistKey = histKey;
        this.loadCrawlHistory();
        this._updateViewBanner();
      }

      // Check if done or idle
      if (s.status === 'completed' || s.status === 'stopped' || s.status === 'error' || s.status === 'idle' || s.status === 'interrupted') {
        const barEl = document.getElementById('sim-progress-bar');
        if (barEl) barEl.style.width = s.status === 'completed' ? '100%' : '0%';

        // Only on the tick that sees the run end — not on every tick of the pause
        // before the next queued crawl.
        if (changed && this._crawlLive(prev)) {
          const viewingOld = !!this._dcViewHid;
          if ((s.status === 'completed' || s.status === 'stopped') && !viewingOld) {
            await this._fetchDeepCrawlResults();
            if (gen !== this._dcPollGen) return;
          }
          const tail = viewingOld ? ' — bấm "↩ Về kết quả mới nhất" để xem.' : '';
          if (s.status === 'completed') {
            App.toast(`Deep Crawl hoàn thành! Tìm thấy ${s.channels_found} kênh, ${s.contacts_found} contacts.${tail}`, 'success');
          } else if (s.status === 'stopped') {
            App.toast(`Deep Crawl đã dừng. Thu thập được ${s.channels_found} kênh.${tail}`, 'warning');
          }
        }

        // Pause before the next queued crawl (or a FloodWait wait): keep polling.
        if (this._crawlLive(s)) {
          this._deepCrawlPollInterval = 3000;
          again(3000);
          return;
        }

        // All done
        this._deepCrawlPolling = false;
        const progressPanel = document.getElementById('sim-progress-panel');
        if (s.status === 'idle' && progressPanel) progressPanel.classList.add('hidden');
        return;
      }

      // Exponential Backoff calculation
      let hasProgress = false;
      if (this._deepCrawlPrevState) {
        if (
          s.channels_found !== this._deepCrawlPrevState.channels_found ||
          s.channels_processed !== this._deepCrawlPrevState.channels_processed ||
          s.contacts_found !== this._deepCrawlPrevState.contacts_found ||
          s.queue_remaining !== this._deepCrawlPrevState.queue_remaining ||
          s.current_depth !== this._deepCrawlPrevState.current_depth ||
          s.current_channel !== this._deepCrawlPrevState.current_channel ||
          s.status !== this._deepCrawlPrevState.status
        ) {
          hasProgress = true;
        }
      } else {
        hasProgress = true; // Set to true on first successful poll
      }

      if (hasProgress) {
        this._deepCrawlPollInterval = 3000; // Reset to 3s
      } else {
        this._deepCrawlPollInterval = Math.min(30000, this._deepCrawlPollInterval * 1.5);
      }

      this._deepCrawlPrevState = {
        channels_found: s.channels_found,
        channels_processed: s.channels_processed,
        contacts_found: s.contacts_found,
        queue_remaining: s.queue_remaining,
        current_depth: s.current_depth,
        current_channel: s.current_channel,
        status: s.status
      };

      again(this._deepCrawlPollInterval);
    } catch (e) {
      // Back off on network or parsing error
      this._deepCrawlPollInterval = Math.min(30000, this._deepCrawlPollInterval * 1.5);
      again(this._deepCrawlPollInterval);
    }
  },

  // ── Fetch full results after deep crawl ──
  // The result set can be thousands of leads (~4 MB). Fetch page 0 slim first so
  // the table paints immediately, then stream the remaining pages in the
  // background — the user never waits for data they are not looking at.
  // Returns true when shown, false on error, null when a newer request took over.
  async _fetchDeepCrawlResults() {
    const PAGE = 100;
    const hid = this._dcViewHid || '';
    const hq = hid ? `&hid=${encodeURIComponent(hid)}` : '';
    const tok = this._dcFetchTok = (this._dcFetchTok || 0) + 1;
    const stale = () => tok !== this._dcFetchTok;
    this._dcCaches = this._dcCaches || {};
    try {
      // A stored run never changes: reopen it from memory without any request.
      const hc = hid && this._dcCaches[hid];
      if (hc) {
        this._dcShownSource = hc.source;
        this._renderDeepCrawlResults(hc.all, { counts: hc.counts, total: hc.total, keepPage: this._similarLeads === hc.all });
        this._updateViewBanner();
        return true;
      }

      // 1) First page — fast paint.
      const first = await fetch(`/api/members/deep-crawl/results?slim=1&page=0&limit=${PAGE}${hq}`);
      const d0 = await first.json();
      if (stale()) return null;
      if (!first.ok) throw new Error(d0.detail || 'Lỗi tải kết quả deep crawl');
      this._dcShownSource = d0.source_url || '';
      const total = d0.total || 0;
      if (!total) {
        delete this._dcCaches[hid];
        this._renderDeepCrawlResults([]);
        this._updateViewBanner();
        return true;
      }

      // Revisit with unchanged data: reuse the cached full set instead of
      // re-downloading every page (~1 MB, 30+ requests) on each navigation.
      const c = this._dcCaches[hid];
      if (c && c.total === total && c.firstId === (d0.leads || [])[0]?.id) {
        this._renderDeepCrawlResults(c.all, { counts: d0.counts, total, keepPage: this._similarLeads === c.all });
        this._updateViewBanner();
        return true;
      }

      this._renderDeepCrawlResults(d0.leads || [], { counts: d0.counts, total });
      this._updateViewBanner();

      // 2) Remaining pages — hydrate the rest without blocking the UI.
      // Fetch them in parallel (bounded) at the server's max page size and
      // render ONCE at the end: re-rendering the growing table after every
      // page made the tab take ~3.6s for ~7k leads.
      const BIG = 200;  // server caps slim pages at 200
      const skip = (d0.leads || []).length;
      const bigPages = Math.ceil(total / BIG);
      const chunks = new Array(bigPages);
      let next = 0;
      const worker = async () => {
        while (next < bigPages && !stale()) {
          const p = next++;
          const res = await fetch(`/api/members/deep-crawl/results?slim=1&page=${p}&limit=${BIG}${hq}`);
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const d = await res.json();
          chunks[p] = d.leads || [];
        }
      };
      try {
        await Promise.all(Array.from({ length: Math.min(4, bigPages) }, worker));
      } catch (e) {
        console.warn('[DeepCrawl] page load error', e);
        return stale() ? null : true;  // keep the first page on screen
      }
      if (stale()) return null;
      const all = chunks.flat();
      this._dcCaches[hid] = { total, firstId: (d0.leads || [])[0]?.id, all, counts: d0.counts, source: this._dcShownSource };
      // Keep at most 3 sets in memory (latest + 2 opened history runs).
      for (const k of Object.keys(this._dcCaches)) {
        if (Object.keys(this._dcCaches).length <= 3) break;
        if (k !== hid) delete this._dcCaches[k];
      }
      if (all.length > skip) {
        this._renderDeepCrawlResults(all, { counts: d0.counts, total, keepPage: true });
      }
      return true;
    } catch (e) {
      if (stale()) return null;
      App.toast(e.message || 'Lỗi tải kết quả deep crawl', 'error');
      return false;
    }
  },

  // ── Stop Deep Crawl ──
  async stopDeepCrawl() {
    try {
      const res = await fetch('/api/members/deep-crawl/stop', { method: 'POST' });
      const d = await res.json();
      App.toast(d.message, 'info');
    } catch (e) {
      App.toast('Lỗi dừng deep crawl', 'error');
    }
  },

  // ── Render Deep Crawl Results (with Depth, Parent, and Pagination) ──
  _similarLeads: [],
  _descTranslationCache: new Map(),
  _depthFilter: 'all',
  _tradingFilter: 'has_contacts',
  _simPage: 0,
  _simLimit: 20,
  _selectedContacts: new Set(),

  _isBotUsername(u) {
    if (!u) return false;
    const s = u.replace(/^@/, '').trim().toLowerCase();
    return s.endsWith('bot') || s.endsWith('_bot') || s.includes('_bot_') || s.startsWith('bot_');
  },

  _ensureTradingScore(lead) {
    if (lead.trading_score !== undefined && lead.trading_score !== null) return lead.trading_score;
    const text = `${lead.title || ''} ${lead.description || ''} ${lead.username || ''}`.toLowerCase();
    let score = 0;
    const tradingTerms = [
      "trading", "trader", "traders", "futures", "leverage", "margin", "scalping", "scalper",
      "day trading", "daytrader", "signals", "signal", "cscalp", "smart money", "smc", "ict", "alpha", "gem",
      "трейдинг", "трейдер", "трейдеры", "сигналы", "фьючерсы", "скальпинг", "скальп", "ордер", "биржа", "мосбиржа", "скринер", "проп", "стакан", "сделки", "депозит",
      "合约", "带单", "返佣", "量化", "现货", "开仓", "平仓", "止盈", "止损", "爆仓", "跟单", "实盘",
      "giao dịch", "đòn bẩy", "tín hiệu", "kèo", "lệnh", "phân tích kỹ thuật", "chốt lời", "cắt lỗ", "bắn kèo"
    ];
    const matches = tradingTerms.filter(k => text.includes(k));
    score += Math.min(matches.length * 20, 50);

    const exchangeTerms = ["binance", "bybit", "okx", "weex", "bitget", "mexc", "bingx", "gate.io", "kucoin", "cscalp", "partner", "kol", "affiliate", "vip", "поддержка", "support", "партнер"];
    const exMatches = exchangeTerms.filter(k => text.includes(k));
    score += Math.min(exMatches.length * 20, 40);

    const validContacts = (lead.contacts || []).filter(c => !this._isBotUsername(c));
    if (validContacts.length > 0) score += 15;
    if (text.includes("t.me/+") || text.includes("t.me/joinchat") || text.includes("чат") || text.includes("chat")) score += 15;

    const titleUser = `${lead.title || ''} ${lead.username || ''}`.toLowerCase();
    if (["trade", "signal", "future", "scalp", "скальп", "сигнал", "трейд", "биржа", "kèo", "合约"].some(k => titleUser.includes(k))) score += 20;

    const subs = lead.participants_count || 0;
    if (subs >= 20000) score += 20;
    else if (subs >= 5000) score += 15;
    else if (subs >= 1000) score += 10;

    lead.trading_score = Math.min(100, Math.max(0, score));
    // CEX BD only targets futures traders; airdrop / bonus hunters never qualify.
    const futuresTerms = ["futures", "leverage", "margin", "scalp", "perp", "signal", "copy trading", "tp/sl", "đòn bẩy", "kèo", "tín hiệu", "合约", "带单", "跟单", "фьючерс", "сигнал", "скальп"];
    const airdropTerms = ["airdrop", "giveaway", "bonus hunt", "free crypto", "free usdt", "retroactive", "testnet", "faucet", "galxe", "zealy", "săn airdrop", "săn bonus", "空投", "撸毛", "羊毛", "аирдроп", "раздача"];
    const hasFutures = futuresTerms.some(k => text.includes(k));
    const airdropHits = airdropTerms.filter(k => text.includes(k)).length;
    const isAirdrop = airdropHits > 0 && (!hasFutures || airdropHits >= 2);
    if (isAirdrop) lead.trading_score = Math.min(lead.trading_score, 25);
    else if (!hasFutures) lead.trading_score = Math.min(lead.trading_score, 44);
    if (isAirdrop) {
      lead.category = "airdrop_bonus";
      lead.category_label = "🪂 Airdrop / Bonus";
      lead.badge_color = "var(--text3)";
    } else if (lead.trading_score >= 80) {
      lead.category = "trading_signals";
      lead.category_label = "🔥 VIP Signals / KOL";
      lead.badge_color = "var(--green)";
    } else if (lead.trading_score >= 60) {
      lead.category = "crypto_trading";
      lead.category_label = "📈 Trading & Scalping";
      lead.badge_color = "var(--purple)";
    } else if (lead.trading_score >= 45) {
      lead.category = "potential_bd";
      lead.category_label = "💎 Tiềm năng BD";
      lead.badge_color = "var(--accent)";
    } else if (lead.trading_score >= 30) {
      lead.category = "crypto_community";
      lead.category_label = "📈 Cộng đồng Crypto";
      lead.badge_color = "#6366f1";
    } else {
      lead.category = "low_relevance";
      lead.category_label = "⚠️ Ít liên quan";
      lead.badge_color = "var(--text3)";
    }
    return lead.trading_score;
  },

  _renderDeepCrawlResults(leads, opts) {
    opts = opts || {};
    const container = document.getElementById('sim-results-container');
    const tbody = document.getElementById('sim-results-tbody');
    const empty = document.getElementById('sim-empty-state');
    const filterTabs = document.getElementById('sim-depth-filter-tabs');
    const tradingTabs = document.getElementById('sim-trading-filter-tabs');
    const pagEl = document.getElementById('sim-pagination');
    if (!container || !tbody || !empty) return;

    // Ensure all leads have clean contacts and calculated trading scores
    if (leads && leads.length) {
      leads.forEach(l => {
        if (l.contacts) {
          l.contacts = l.contacts.filter(c => !this._isBotUsername(c));
        }
        this._ensureTradingScore(l);
      });
    }

    // Detect fresh leads array and build select state. During progressive
    // hydration (keepPage) the user may already be paging/filtering, so keep
    // their position and selection instead of snapping back to page 0.
    if (this._similarLeads !== leads) {
      this._similarLeads = leads;
      if (!opts.keepPage) {
        this._simPage = 0;
        this._selectedContacts = new Set();
        leads.forEach(l => {
          if (l.contacts) {
            l.contacts.forEach(c => this._selectedContacts.add(c));
          }
        });
      } else {
        leads.forEach(l => {
          if (l.contacts) {
            l.contacts.forEach(c => this._selectedContacts.add(c));
          }
        });
      }
    }

    if (!leads || !leads.length) {
      tbody.innerHTML = '';
      container.classList.add('hidden');
      empty.classList.remove('hidden');
      empty.querySelector('p').textContent = 'Không tìm thấy kênh tương tự đề xuất nào.';
      if (pagEl) pagEl.innerHTML = '';
      return;
    }

    empty.classList.add('hidden');
    container.classList.remove('hidden');

    // ── Single-pass accumulator for all tab counts (P6 optimization) ──
    let contactCount = 0, vipCount = 0, tradeCount = 0, potentialCount = 0, communityCount = 0, newsCount = 0;
    const depthCounts = {};
    leads.forEach(l => {
      const hasC = l.contacts && l.contacts.length > 0;
      const s = l.trading_score || 0;
      const d = l.depth;
      depthCounts[d] = (depthCounts[d] || 0) + 1;
      if (hasC) {
        contactCount++;
        if (s >= 80) vipCount++;
        else if (s >= 60) tradeCount++;
        else if (s >= 45) potentialCount++;
        if (this._isCryptoCommunity(l)) communityCount++;
        if (l.category === 'news_general') newsCount++;
      }
    });
    let noContactCount = leads.length - contactCount;
    let allCount = leads.length;

    // When the server supplied counts, they cover the WHOLE result set (not just
    // the hydrated pages), so the tabs stay accurate while pages stream in.
    if (opts.counts) {
      const c = opts.counts;
      contactCount = c.has_contacts ?? contactCount;
      vipCount = c.trading_vip ?? vipCount;
      tradeCount = c.trading_all ?? tradeCount;
      potentialCount = c.potential_bd ?? potentialCount;
      communityCount = c.crypto_community ?? communityCount;
      newsCount = c.news ?? newsCount;
      noContactCount = c.no_contacts ?? noContactCount;
      allCount = c.all ?? opts.total ?? allCount;
    }

    // Build Contact-First Trading Filter Tabs
    if (tradingTabs) {
      tradingTabs.innerHTML = `
        <button class="tab-btn btn-sm ${this._tradingFilter === 'has_contacts' ? 'active' : ''}" onclick="Members.filterByTrading('has_contacts')" style="border-left:3px solid var(--accent);font-weight:600">🎯 Tất cả có Contact (${contactCount})</button>
        <button class="tab-btn btn-sm ${this._tradingFilter === 'trading_vip' ? 'active' : ''}" onclick="Members.filterByTrading('trading_vip')" style="border-left:3px solid var(--green)">🔥 VIP Signals (${vipCount})</button>
        <button class="tab-btn btn-sm ${this._tradingFilter === 'trading_all' ? 'active' : ''}" onclick="Members.filterByTrading('trading_all')" style="border-left:3px solid var(--purple)">📈 Trading & Scalping (${tradeCount})</button>
        <button class="tab-btn btn-sm ${this._tradingFilter === 'potential_bd' ? 'active' : ''}" onclick="Members.filterByTrading('potential_bd')" style="border-left:3px solid var(--accent)">💎 Tiềm năng BD (${potentialCount})</button>
        <button class="tab-btn btn-sm ${this._tradingFilter === 'crypto_community' ? 'active' : ''}" onclick="Members.filterByTrading('crypto_community')" style="border-left:3px solid #6366f1" title="Kênh/nhóm crypto chung (điểm 30–44): có nói về crypto nhưng chưa thấy dấu hiệu futures/signal rõ ràng">📈 Cộng đồng Crypto (${communityCount})</button>
        <button class="tab-btn btn-sm ${this._tradingFilter === 'news' ? 'active' : ''}" onclick="Members.filterByTrading('news')" style="border-left:3px solid var(--orange)">📰 Tin tức (${newsCount})</button>
        <button class="tab-btn btn-sm ${this._tradingFilter === 'no_contacts' ? 'active' : ''}" onclick="Members.filterByTrading('no_contacts')" style="border-left:3px solid var(--text3);opacity:0.75">❌ Chưa có Contact (${noContactCount})</button>
        <button class="tab-btn btn-sm ${this._tradingFilter === 'all' ? 'active' : ''}" onclick="Members.filterByTrading('all')" style="opacity:0.65">🌐 Xem tất cả (${allCount})</button>
      `;
    }

    // Build depth filter tabs (using pre-computed depthCounts)
    const depths = Object.keys(depthCounts).map(Number).sort();
    if (filterTabs) {
      const allActive = this._depthFilter === 'all' ? 'active' : '';
      filterTabs.innerHTML = `<button class="tab-btn btn-sm ${allActive}" onclick="Members.filterByDepth('all')">Tất cả độ sâu (${leads.length})</button>` +
        depths.map(d => {
          const count = depthCounts[d] || 0;
          const active = this._depthFilter === d ? 'active' : '';
          const colors = ['', 'var(--accent)', 'var(--purple)', 'var(--violet)', 'var(--orange)'];
          return `<button class="tab-btn btn-sm ${active}" onclick="Members.filterByDepth(${d})" style="border-left:3px solid ${colors[d] || 'var(--accent)'}">Lớp ${d} (${count})</button>`;
        }).join('');
    }

    // Combined Filter leads (Depth + Contact-First Trading Classification)
    const filtered = this._applySimFilters(leads);

    const total = filtered.length;
    const pages = Math.ceil(total / this._simLimit);
    const start = this._simPage * this._simLimit;
    const end = Math.min(start + this._simLimit, total);
    const pageItems = filtered.slice(start, end);

    // Update global "Select All" checkbox state for the filtered items
    const allFilteredContacts = [];
    filtered.forEach(l => {
      if (l.contacts) {
        l.contacts = l.contacts.filter(c => !this._isBotUsername(c));
        allFilteredContacts.push(...l.contacts);
      }
    });
    const allFilteredSelected = allFilteredContacts.length > 0 && allFilteredContacts.every(c => this._selectedContacts.has(c));
    const selectAllEl = document.getElementById('sim-select-all-channels');
    if (selectAllEl) {
      selectAllEl.checked = allFilteredSelected;
    }

    tbody.innerHTML = pageItems.map((lead) => {
      const idx = leads.indexOf(lead);
      const channelDisplay = lead.username ? `@${lead.username}` : `ID: ${lead.channel_id}`;
      const rawDesc = (lead.description || '').trim();

      // Description with Auto-translation to English
      let descHtml = '';
      if (!rawDesc) {
        descHtml = '<em style="color:var(--text-muted)">Không có mô tả</em>';
      } else if (this._descTranslationCache.has(rawDesc)) {
        const trans = this._descTranslationCache.get(rawDesc);
        const isDiff = trans.trim().toLowerCase() !== rawDesc.trim().toLowerCase();
        descHtml = `
          <div style="font-size:12px;line-height:1.45;color:var(--text);font-weight:500">
            ${esc(trans)}
          </div>
          ${isDiff ? `<div style="font-size:10.5px;color:var(--text-muted);margin-top:3px;font-style:italic;line-height:1.3" title="Gốc: ${esc(rawDesc)}">
            <span style="opacity:0.6">Gốc:</span> ${esc(rawDesc.length > 50 ? rawDesc.substring(0, 50) + '...' : rawDesc)}
          </div>` : ''}
        `;
      } else {
        descHtml = `
          <div style="font-size:12px;line-height:1.45;color:var(--text2)">
            ${esc(rawDesc)}
          </div>
          <div style="font-size:10px;color:var(--accent);margin-top:2px;opacity:0.8">🌐 Đang dịch EN...</div>
        `;
      }

      // Clean contacts of bots
      lead.contacts = (lead.contacts || []).filter(c => !this._isBotUsername(c));

      // Row checkbox state
      const hasContacts = lead.contacts && lead.contacts.length > 0;
      const allChecked = hasContacts && lead.contacts.every(c => this._selectedContacts.has(c));
      const rowChecked = (hasContacts && allChecked) || (!hasContacts) ? 'checked' : '';

      // Trading Score & Category Badge
      const score = lead.trading_score !== undefined ? lead.trading_score : 50;
      const categoryLabel = lead.category_label || (score >= 80 ? '🔥 Trading VIP' : (score >= 60 ? '📈 Crypto Trading' : (score >= 35 ? '📰 Tin tức' : '⚠️ Khác')));
      const badgeColor = lead.badge_color || (score >= 80 ? 'var(--green)' : (score >= 60 ? 'var(--purple)' : (score >= 35 ? 'var(--orange)' : 'var(--text3)')));

      const keywordsHtml = (lead.matched_keywords && lead.matched_keywords.length)
        ? `<div style="display:flex;flex-wrap:wrap;gap:3px;margin-top:4px;">` +
          lead.matched_keywords.slice(0, 3).map(k => `<span style="font-size:9.5px;background:color-mix(in srgb, var(--text) 6%, transparent);padding:1px 4px;border-radius:4px;color:var(--text2);">${esc(k)}</span>`).join('') +
          `</div>`
        : '';

      const scoreHtml = `
        <div style="display:flex;flex-direction:column;gap:3px;min-width:135px;">
          <div style="display:flex;align-items:center;justify-content:space-between;gap:4px;">
            <span style="font-weight:700;font-size:12.5px;color:${badgeColor}">${score} <span style="font-size:9.5px;color:var(--text2)">/ 100</span></span>
            <span style="display:inline-block;padding:2px 6px;border-radius:6px;font-size:10px;font-weight:600;color:white;background:${badgeColor}">${esc(categoryLabel)}</span>
          </div>
          <div style="background:color-mix(in srgb, var(--text) 10%, transparent);height:4px;border-radius:2px;overflow:hidden;width:100%">
            <div style="background:${badgeColor};height:100%;width:${score}%"></div>
          </div>
          ${keywordsHtml}
        </div>
      `;

      let contactsHtml = '';
      if (lead.contacts && lead.contacts.length) {
        contactsHtml = lead.contacts.map(c => {
          const isChecked = this._selectedContacts.has(c) ? 'checked' : '';
          return `
            <label style="display:flex;align-items:center;gap:6px;margin-bottom:4px;cursor:pointer;font-size:12px">
              <input type="checkbox" class="sim-contact-checkbox" data-channel-title="${esc(lead.title)}" value="${esc(c)}" ${isChecked} onchange="Members.onContactCheckboxChange(this, '${esc(c)}', ${idx})">
              <span style="color:var(--accent);font-weight:500">${esc(c)}</span>
            </label>
          `;
        }).join('');
      } else {
        contactsHtml = '<span style="font-size:12px;color:var(--text-muted)">Không tìm thấy contact</span>';
      }

      // Depth badge colors
      const depthColors = { 1: 'var(--accent)', 2: 'var(--purple)', 3: 'var(--violet)', 4: 'var(--orange)' };
      const depthColor = depthColors[lead.depth] || 'var(--accent)';

      return `
        <tr>
          <td>
            <input type="checkbox" class="sim-channel-row-checkbox" value="${idx}" ${rowChecked} onchange="Members.onSimilarChannelCheckboxChange(this, ${idx})">
          </td>
          <td>
            <strong>${esc(lead.title)}</strong><br>
            <small style="color:var(--text-muted)">${esc(channelDisplay)}</small>
          </td>
          <td>
            ${scoreHtml}
          </td>
          <td>
            <span class="badge badge-blue">${(lead.participants_count || 0).toLocaleString()}</span>
          </td>
          <td>
            <span style="display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;color:white;background:${depthColor}">L${lead.depth}</span>
          </td>
          <td style="font-size:12px;color:var(--text-muted);max-width:140px;word-break:break-word">
            ${esc(lead.parent_channel || '—')}
          </td>
          <td class="sim-desc-cell" data-desc-idx="${idx}" style="max-width:280px;min-width:220px;word-break:break-word;font-size:12px;vertical-align:top">
            ${descHtml}
          </td>
          <td>
            <div class="sim-contacts-list-cell">${contactsHtml}</div>
          </td>
          <td>
            <button class="btn btn-ghost btn-sm" id="btn-sim-join-${idx}" onclick="Members.joinSimilarChannel(${idx}, '${esc(lead.username || lead.channel_id)}')">
              ➕ Join Kênh
            </button>
          </td>
        </tr>
      `;
    }).join('');

    // Trigger async translation for visible descriptions
    this._translateVisibleDescriptions(pageItems);

    // Render pagination controls
    if (pagEl) {
      if (pages > 1) {
        let h = '';
        if (this._simPage > 0) {
          h += `<button class="btn btn-ghost btn-sm" onclick="Members.setPage(${this._simPage - 1})">◀ Trước</button>`;
        } else {
          h += `<button class="btn btn-ghost btn-sm" disabled style="opacity:0.5;cursor:not-allowed">◀ Trước</button>`;
        }
        h += `<span style="color:var(--text2);font-size:12px;margin:0 10px">Trang ${this._simPage + 1} / ${pages} (Hiển thị ${start + 1}-${end} trong số ${total})</span>`;
        if (this._simPage < pages - 1) {
          h += `<button class="btn btn-ghost btn-sm" onclick="Members.setPage(${this._simPage + 1})">Sau ▶</button>`;
        } else {
          h += `<button class="btn btn-ghost btn-sm" disabled style="opacity:0.5;cursor:not-allowed">Sau ▶</button>`;
        }
        pagEl.innerHTML = h;
      } else {
        pagEl.innerHTML = '';
      }
    }

    const jobInput = document.getElementById('sim-import-job-id');
    if (jobInput && !this._jobNameTouched) jobInput.value = this._suggestLeadListName();
  },

  // Tab key → short tag used in list names / titles
  _SIM_TAB_TAGS: {
    has_contacts: ['Contacts', 'Tất cả có contact'], trading_vip: ['VIP', 'VIP Signals'],
    trading_all: ['Trading', 'Trading & Scalping'], crypto_community: ['Community', 'Cộng đồng Crypto'],
    potential_bd: ['BD', 'Tiềm năng BD'],
    news: ['News', 'Tin tức'], no_contacts: ['NoContact', 'Chưa có contact'], all: ['All', 'Tất cả'],
  },

  // "https://t.me/H2Capitalchannel" → "H2Capitalchannel"
  _crawlSourceName() {
    const raw = (this._dcShownSource || this._crawlSource || document.getElementById('sim-channel-input')?.value || '').trim();
    const m = raw.match(/(?:t\.me\/|telegram\.me\/|@)(?:s\/)?([A-Za-z0-9_]{3,})/);
    const name = (m ? m[1] : raw).replace(/[^A-Za-z0-9_]/g, '').slice(0, 28);
    return name || 'DeepCrawl';
  },

  // e.g. "H2Capitalchannel_Trading_0611" (+ "_2" when that name already exists)
  _suggestLeadListName() {
    const tag = (this._SIM_TAB_TAGS[this._tradingFilter] || ['Leads'])[0];
    const d = new Date();
    const dd = String(d.getDate()).padStart(2, '0') + String(d.getMonth() + 1).padStart(2, '0');
    const depth = this._depthFilter && this._depthFilter !== 'all' ? `_L${this._depthFilter}` : '';
    const base = `${this._crawlSourceName()}_${tag}${depth}_${dd}`;
    const taken = new Set((this._scrapeJobs || []).map(j => String(j.scrape_job_id).toLowerCase()));
    let name = base, i = 2;
    while (taken.has(name.toLowerCase())) name = `${base}_${i++}`;
    return name;
  },

  // ── Batch Auto-Translate Visible Descriptions to English ──
  _translatePending: false,
  async _translateVisibleDescriptions(pageItems) {
    if (!pageItems || !pageItems.length || this._translatePending) return;
    const cache = this._descTranslationCache;
    const untranslated = [];
    pageItems.forEach(l => {
      const d = (l.description || '').trim();
      if (d.length > 0 && !cache.has(d)) untranslated.push(d);
    });

    if (!untranslated.length) return;
    const uniqueTexts = [...new Set(untranslated)];

    this._translatePending = true;
    try {
      const res = await fetch('/api/members/translate-descriptions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ texts: uniqueTexts, target_lang: 'en' })
      });
      if (!res.ok) { this._translatePending = false; return; }
      const data = await res.json();
      if (data.translations) {
        Object.entries(data.translations).forEach(([orig, trans]) => {
          cache.set(orig, trans);
        });
        // Cap cache at 2000 entries (FIFO eviction)
        if (cache.size > 2000) {
          const firstKey = cache.keys().next().value;
          cache.delete(firstKey);
        }

        // Update DOM cells by matching data-desc-idx
        document.querySelectorAll('.sim-desc-cell[data-desc-idx]').forEach(cell => {
          const descIdx = parseInt(cell.getAttribute('data-desc-idx'), 10);
          if (isNaN(descIdx)) return;
          const lead = this._similarLeads[descIdx];
          if (!lead) return;
          const orig = (lead.description || '').trim();
          if (!orig || !cache.has(orig)) return;
          const trans = cache.get(orig);
          const isDiff = trans.trim().toLowerCase() !== orig.trim().toLowerCase();
          const shortOrig = orig.length > 60 ? orig.substring(0, 60) + '…' : orig;
          cell.innerHTML = isDiff
            ? `<div style="font-size:12px;line-height:1.45;color:var(--text);font-weight:500">${esc(trans)}</div>
               <div style="font-size:10.5px;color:var(--text-muted);margin-top:3px;font-style:italic;line-height:1.3">
                 <span style="opacity:0.6">Gốc:</span> ${esc(shortOrig)}
               </div>`
            : `<div style="font-size:12px;line-height:1.45;color:var(--text)">${esc(trans)}</div>`;
        });
      }
    } catch (e) {
      console.warn('[Translation] Error:', e);
    } finally {
      this._translatePending = false;
    }
  },

  setPage(p) {
    this._simPage = p;
    this._renderDeepCrawlResults(this._similarLeads);
  },

  // ── Leads matching the current depth + trading tabs ──
  _applySimFilters(leads) {
    let filtered = leads || [];
    if (this._depthFilter !== 'all') {
      filtered = filtered.filter(l => l.depth === this._depthFilter);
    }
    if (this._tradingFilter === 'has_contacts') {
      filtered = filtered.filter(l => l.contacts && l.contacts.length > 0);
    } else if (this._tradingFilter === 'trading_vip') {
      filtered = filtered.filter(l => l.contacts && l.contacts.length > 0 && (l.trading_score || 0) >= 80);
    } else if (this._tradingFilter === 'trading_all') {
      filtered = filtered.filter(l => l.contacts && l.contacts.length > 0 && (l.trading_score || 0) >= 60 && (l.trading_score || 0) < 80);
    } else if (this._tradingFilter === 'potential_bd') {
      filtered = filtered.filter(l => l.contacts && l.contacts.length > 0 && (l.trading_score || 0) >= 45 && (l.trading_score || 0) < 60);
    } else if (this._tradingFilter === 'crypto_community') {
      filtered = filtered.filter(l => l.contacts && l.contacts.length > 0 && this._isCryptoCommunity(l));
    } else if (this._tradingFilter === 'news') {
      filtered = filtered.filter(l => l.contacts && l.contacts.length > 0 && l.category === 'news_general');
    } else if (this._tradingFilter === 'no_contacts') {
      filtered = filtered.filter(l => !l.contacts || l.contacts.length === 0);
    }
    return filtered;
  },

  // "Cộng đồng Crypto" (score 30–44). Older snapshots stored it under crypto_trading.
  _isCryptoCommunity(l) {
    return l.category === 'crypto_community' || (l.category === 'crypto_trading' && (l.trading_score || 0) < 60);
  },

  // ── Filter results by depth ──
  filterByDepth(depth) {
    this._depthFilter = depth;
    this._simPage = 0;
    this._renderDeepCrawlResults(this._similarLeads);
  },

  // ── Filter results by trading classification ──
  filterByTrading(cat) {
    this._tradingFilter = cat;
    this._simPage = 0;
    this._renderDeepCrawlResults(this._similarLeads);
  },

  // ── 1-Click Select Only High-Score Trading Contacts ──
  selectOnlyTradingContacts(minScore = 60) {
    this._selectedContacts.clear();
    let count = 0;
    (this._similarLeads || []).forEach(lead => {
      const score = lead.trading_score !== undefined ? lead.trading_score : 50;
      const validContacts = (lead.contacts || []).filter(c => !this._isBotUsername(c));
      if (score >= minScore && validContacts.length > 0) {
        validContacts.forEach(c => {
          this._selectedContacts.add(c);
          count++;
        });
      }
    });
    this._renderDeepCrawlResults(this._similarLeads);
    App.toast(`⚡ Đã chọn ${this._selectedContacts.size} admin contact từ các kênh trading chất lượng (≥${minScore}đ)!`, 'success');
  },

  toggleSelectAllChannels(el) {
    const filtered = this._applySimFilters(this._similarLeads);

    filtered.forEach(lead => {
      if (lead.contacts) {
        lead.contacts.filter(c => !this._isBotUsername(c)).forEach(c => {
          if (el.checked) {
            this._selectedContacts.add(c);
          } else {
            this._selectedContacts.delete(c);
          }
        });
      }
    });
    this._renderDeepCrawlResults(this._similarLeads);
  },

  onSimilarChannelCheckboxChange(el, idx) {
    const lead = this._similarLeads[idx];
    if (!lead || !lead.contacts) return;
    lead.contacts.filter(c => !this._isBotUsername(c)).forEach(c => {
      if (el.checked) {
        this._selectedContacts.add(c);
      } else {
        this._selectedContacts.delete(c);
      }
    });
    this._renderDeepCrawlResults(this._similarLeads);
  },

  onContactCheckboxChange(el, contact, leadIdx) {
    if (this._isBotUsername(contact)) return;
    if (el.checked) {
      this._selectedContacts.add(contact);
    } else {
      this._selectedContacts.delete(contact);
    }
    this._renderDeepCrawlResults(this._similarLeads);
  },

  async joinSimilarChannel(idx, channelLink) {
    // Use first checked premium account
    const cb = document.querySelector('.sim-premium-cb:checked');
    const btn = document.getElementById(`btn-sim-join-${idx}`);
    if (!cb || !btn) return;
    const accountId = parseInt(cb.value);
    if (!accountId) return;

    btn.disabled = true;
    btn.textContent = 'Đang join...';

    try {
      const res = await fetch('/api/members/join-channel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ account_id: accountId, channel_link: channelLink })
      });
      const d = await res.json();
      if (!res.ok) {
        throw new Error(d.detail || 'Không thể join kênh');
      }
      btn.className = 'btn btn-green btn-sm';
      btn.textContent = 'Đã Join ✓';
      App.toast(`Đã join kênh "${d.title || channelLink}" thành công!`, 'success');
    } catch (e) {
      App.toast(e.message, 'error');
      btn.disabled = false;
      btn.textContent = '➕ Join Kênh';
    }
  },

  async importCheckedContacts() {
    const jobIdInput = document.getElementById('sim-import-job-id');
    if (!jobIdInput) return;
    const jobId = jobIdInput.value.trim();
    if (!jobId) {
      App.toast('Vui lòng nhập tên Scrape Job để lưu!', 'error');
      return;
    }

    // Import only the selected contacts of leads in the current tab/depth filter,
    // so picking "Trading & Scalping" doesn't also import every other tab's contacts.
    const inView = new Set();
    const viewLeads = this._applySimFilters(this._similarLeads);
    viewLeads.forEach(l => (l.contacts || []).forEach(c => inView.add(c)));
    const cleanContacts = Array.from(this._selectedContacts).filter(c => inView.has(c) && !this._isBotUsername(c));
    if (!cleanContacts.length) {
      App.toast('Chưa chọn contact hợp lệ nào trong tab hiện tại!', 'error');
      return;
    }

    const contacts = cleanContacts.map(c => ({
      username: c,
      first_name: c,
      last_name: ''
    }));

    const tabLbl = (this._SIM_TAB_TAGS[this._tradingFilter] || [null, 'Leads'])[1];
    const d0 = new Date();
    const groupTitle = this._jobNameTouched
      ? jobId
      : `${this._crawlSourceName()} · ${tabLbl} (${String(d0.getDate()).padStart(2, '0')}/${String(d0.getMonth() + 1).padStart(2, '0')})`;

    const picked = new Set(cleanContacts);
    const nCh = viewLeads.filter(l => (l.contacts || []).some(c => picked.has(c))).length;
    const allWithContacts = viewLeads.filter(l => (l.contacts || []).length).length;
    if (!await App.confirmDialog({
      icon: '💾', title: 'Lưu contact vào danh sách',
      message: `Lưu vào job "${jobId}" (tab ${tabLbl})`,
      stats: [
        { value: cleanContacts.length, label: 'contact đã chọn' },
        { value: nCh, label: 'kênh', hint: `/${allWithContacts} kênh trong tab` },
      ],
      note: 'Chỉ lưu contact đã tick trong tab đang xem. Tự bỏ qua contact đã DM/đang chat hoặc đang nằm trong danh sách có chiến dịch; contact chỉ nằm ở danh sách chưa chạy chiến dịch nào vẫn được lưu. Muốn lấy nhiều hơn: chọn tab "Tất cả có contact" rồi tick hết.',
      okText: 'Lưu contact', cancelText: 'Huỷ',
    })) return;

    try {
      const res = await fetch('/api/members/import-contacts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scrape_job_id: jobId,
          group_title: groupTitle,
          contacts: contacts,
          skip_existing: true,
          source: this._dcShownSource || '',
          channels: viewLeads.filter(l => (l.contacts || []).some(c => picked.has(c))).map(l => ({
            channel_id: l.channel_id, username: l.username, title: l.title,
            participants_count: l.participants_count, trading_score: l.trading_score,
            category: l.category, category_label: l.category_label, contacts: l.contacts,
            description: (l.description || '').slice(0, 600), parent_channel: l.parent_channel, depth: l.depth,
          })),
        })
      });
      const d = await res.json();
      if (!res.ok) {
        throw new Error(d.detail || 'Không thể import contact');
      }

      const skips = [];
      if (d.skipped_existing > 0) {
        const where = (d.existing_jobs || []).slice(0, 2).map(([j, n]) => `${j}: ${n}`).join(', ');
        skips.push(`${d.skipped_existing} đã có trong danh sách đang chạy chiến dịch${where ? ` (${where})` : ''}`);
      }
      if (d.skipped_dmd > 0) skips.push(`${d.skipped_dmd} đã DM/đang trò chuyện`);
      if (d.skipped_dupe > 0) skips.push(`${d.skipped_dupe} trùng trong lựa chọn`);
      const resaved = d.resaved_unused > 0 ? ` (gồm ${d.resaved_unused} lấy lại từ danh sách chưa chạy)` : '';
      const msg = d.count > 0
        ? `✅ Đã lưu ${d.count} contact vào "${groupTitle}"${resaved}${skips.length ? ` · Bỏ qua ${skips.join(', ')}` : ''}`
        : `ℹ️ Không có contact mới — tất cả đã có sẵn${skips.length ? `: ${skips.join(', ')}` : ''}`;
      App.toast(msg, d.count > 0 ? 'success' : 'info');
      if (d.kol_upserted) App.toast(`📇 Đã cập nhật ${d.kol_upserted} kênh vào Danh bạ KOL`, 'info');
      this._jobNameTouched = false;
      await this.loadScrapeJobs();

      // Reset selection but KEEP results so user can re-filter and save other tabs
      this._selectedContacts.clear();
      this._renderDeepCrawlResults(this._similarLeads);

      // Shortcut: go straight from imported contacts to a DM campaign on that job
      const topJobs = ((d.existing_jobs || [])[0] || [])[0] || '';
      if (d.count > 0 && await App.confirmDialog({
        icon: '🚀', title: 'Tạo DM campaign luôn?',
        message: `Danh sách "${groupTitle}" đã sẵn sàng.`,
        stats: [
          { value: d.count, label: 'contact mới', ...(d.resaved_unused ? { hint: `gồm ${d.resaved_unused} từ list chưa chạy` } : {}) },
          ...(d.skipped_existing ? [{ value: d.skipped_existing, label: 'đã có ở list đang chạy', hint: topJobs || 'list đã có chiến dịch' }] : []),
          ...(d.skipped_dmd ? [{ value: d.skipped_dmd, label: 'đã DM / đang chat', hint: 'không nhắn lại lần 2' }] : []),
          ...(d.skipped_dupe ? [{ value: d.skipped_dupe, label: 'trùng', hint: 'lặp trong lựa chọn' }] : []),
        ],
        okText: 'Tạo campaign', cancelText: 'Để sau',
      })) {
        this.openCampaignModal(jobId);
      }
    } catch (e) {
      App.toast(e.message, 'error');
    }
  },

  // ── Manually / Programmatically Clear Deep Crawl Table Results ──
  async clearDeepCrawlResults(showToast = true) {
    if (this._dcViewHid) { await this.exitCrawlView(); return; }
    try {
      await fetch('/api/members/deep-crawl/clear', { method: 'POST' });
    } catch (e) {
      console.debug('Error calling deep-crawl/clear API:', e);
    }
    if (this._dcCaches) delete this._dcCaches[''];
    this._dcShownSource = '';
    this._updateViewBanner();
    this._similarLeads = [];
    this._selectedContacts.clear();
    this._renderDeepCrawlResults([]);

    const empty = document.getElementById('sim-empty-state');
    if (empty) {
      empty.classList.remove('hidden');
      empty.querySelector('p').textContent = 'Chưa có kết quả cào. Nhập link kênh và bắt đầu cào để xem danh sách mới.';
    }
    const container = document.getElementById('sim-results-container');
    if (container) container.classList.add('hidden');

    if (showToast) {
      App.toast('🗑️ Đã xóa sạch kết quả cào trên bảng!', 'success');
    }
  },

  // ═══════════════════════════════════════════════════════════════════════════
  // INVITE CAMPAIGNS
  // ═══════════════════════════════════════════════════════════════════════════

  switchCampaignTab(tab) {
    document.querySelectorAll('.ms-campaign-tab').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tab === tab);
      if (btn.dataset.tab === tab) {
        btn.classList.remove('btn-ghost');
      } else {
        btn.classList.add('btn-ghost');
      }
    });
    const dmSection = document.getElementById('ms-dm-campaigns-section');
    const invSection = document.getElementById('ms-invite-campaigns-section');
    const dmBtn = document.getElementById('ms-btn-create-dm-campaign');
    const invBtn = document.getElementById('ms-btn-create-invite-campaign');
    if (tab === 'dm') {
      dmSection.classList.remove('hidden');
      invSection.classList.add('hidden');
      dmBtn.classList.remove('hidden');
      invBtn.classList.add('hidden');
    } else {
      invSection.classList.remove('hidden');
      dmSection.classList.add('hidden');
      invBtn.classList.remove('hidden');
      dmBtn.classList.add('hidden');
      this.loadInviteCampaigns();
    }
  },

  async loadInviteCampaigns() {
    try {
      const d = await InviteAPI.getCampaigns(this._lastInviteCampaignsUpdate);
      const newCampaigns = d.campaigns || [];

      if (this._lastInviteCampaignsUpdate === null) {
        this._inviteCampaigns = newCampaigns;
      } else if (newCampaigns.length > 0) {
        newCampaigns.forEach(newC => {
          const idx = this._inviteCampaigns.findIndex(c => c.id === newC.id);
          if (idx !== -1) {
            this._inviteCampaigns[idx] = newC;
          } else {
            this._inviteCampaigns.push(newC);
          }
        });
        this._inviteCampaigns.sort((a, b) => b.id - a.id);
      }

      if (this._inviteCampaigns.length > 0) {
        const timestamps = this._inviteCampaigns.map(c => c.updated_at).filter(Boolean);
        if (timestamps.length > 0) {
          this._lastInviteCampaignsUpdate = timestamps.reduce((max, t) => t > max ? t : max, timestamps[0]);
        }
      }

      this._renderInviteCampaigns();
    } catch (e) {
      console.error('Load invite campaigns error:', e);
    }
  },

  _renderInviteCampaigns() {
    const tbody = document.getElementById('ms-invite-campaigns-tbody');
    const empty = document.getElementById('ms-invite-campaigns-empty');
    if (!tbody) return;

    const campaigns = this._inviteCampaigns;

    if (!campaigns.length) {
      tbody.innerHTML = '';
      if (empty) empty.classList.remove('hidden');
      return;
    }
    if (empty) empty.classList.add('hidden');

    // Remove rows no longer in state
    const campaignIdsInState = new Set(campaigns.map(c => c.id));
    Array.from(tbody.children).forEach(row => {
      const idAttr = row.getAttribute('data-id');
      if (idAttr) {
        const cid = parseInt(idAttr);
        if (!campaignIdsInState.has(cid)) {
          row.remove();
        }
      }
    });

    campaigns.forEach((c, i) => {
      const statusBadge = this._statusBadge(c.status);
      const total = c.total_targets || 0;
      const sent = c.sent_count || 0;
      const failed = c.failed_count || 0;
      const skipped = c.skipped_count || 0;
      const processed = sent + failed + skipped;
      const progress = total > 0 ? Math.min(100, Math.round((processed / total) * 100)) : 0;
      const modeBadge = c.invite_mode === 'dm_link'
        ? '<span class="badge" style="background:var(--accent)">💬 DM Link</span>'
        : '<span class="badge badge-blue">👥 Direct</span>';

      let actions = '';
      if (c.status === 'draft' || c.status === 'paused' || c.status === 'error') {
        actions += `<button class="btn btn-primary btn-sm" onclick="Members.startInviteCampaign(${c.id})">▶ Chạy</button>`;
        actions += `<button class="btn btn-ghost btn-sm" onclick="Members.openEditInviteCampaign(${c.id})" title="Sửa">✏️</button>`;
      }
      if (c.status === 'running') {
        actions += `<button class="btn btn-danger btn-sm" onclick="Members.stopInviteCampaign(${c.id})">⏸ Dừng</button>`;
      }
      actions += `<button class="btn btn-ghost btn-sm" onclick="Members.viewInviteCampaignLogs(${c.id},'${esc(c.name)}')">📋</button>`;
      actions += `<button class="btn btn-danger btn-sm" onclick="Members.deleteInviteCampaign(${c.id})">🗑</button>`;

      const targetGroup = c.target_group_title || c.target_group || '—';

      const rowHtml = `
        <td>${i + 1}</td>
        <td>${esc(c.name)}</td>
        <td style="font-size:12px">${esc((c.scrape_job_id || '').substring(0, 20))}...</td>
        <td style="font-size:12px">${esc(targetGroup)}</td>
        <td>${modeBadge}</td>
        <td>${statusBadge}</td>
        <td>
          <div style="display:flex;align-items:center;gap:8px">
            <div style="flex:1;background:var(--bg2);border-radius:4px;height:8px;overflow:hidden">
              <div style="width:${progress}%;height:100%;background:linear-gradient(90deg, var(--accent), var(--accent));border-radius:4px;transition:width .3s"></div>
            </div>
            <span style="font-size:12px;font-weight:600;color:var(--text1)">${processed}/${total} (${progress}%)</span>
          </div>
          <div style="font-size:11px;color:var(--text2);margin-top:3px">
            ✅ Thành công: <b>${sent}</b> | ❌ Lỗi: <b>${failed}</b> | ⏭ Bỏ qua: <b>${skipped}</b>
          </div>
        </td>
        <td><div class="btn-group">${actions}</div></td>
      `;

      let existingRow = tbody.querySelector(`tr[data-id="${c.id}"]`);
      if (existingRow) {
        const existingIdx = parseInt(existingRow.getAttribute('data-index'));
        if (existingIdx !== (i + 1) ||
            existingRow.getAttribute('data-updated-at') !== c.updated_at ||
            existingRow.getAttribute('data-status') !== c.status ||
            existingRow.getAttribute('data-sent') !== String(sent) ||
            existingRow.getAttribute('data-failed') !== String(failed) ||
            existingRow.getAttribute('data-skipped') !== String(skipped)) {
          existingRow.innerHTML = rowHtml;
          existingRow.setAttribute('data-updated-at', c.updated_at);
          existingRow.setAttribute('data-status', c.status);
          existingRow.setAttribute('data-index', i + 1);
          existingRow.setAttribute('data-sent', String(sent));
          existingRow.setAttribute('data-failed', String(failed));
          existingRow.setAttribute('data-skipped', String(skipped));
        }
      } else {
        const tr = document.createElement('tr');
        tr.setAttribute('data-id', c.id);
        tr.setAttribute('data-updated-at', c.updated_at);
        tr.setAttribute('data-status', c.status);
        tr.setAttribute('data-index', i + 1);
        tr.setAttribute('data-sent', String(sent));
        tr.setAttribute('data-failed', String(failed));
        tr.setAttribute('data-skipped', String(skipped));
        tr.innerHTML = rowHtml;
        
        if (tbody.children.length === 0 || i >= tbody.children.length) {
          tbody.appendChild(tr);
        } else {
          tbody.insertBefore(tr, tbody.children[i]);
        }
      }
    });
  },

  // ── Invite Campaign Actions ──
  async startInviteCampaign(id) {
    try {
      const r = await InviteAPI.startCampaign(id);
      App.toast(r.message || 'Invite campaign đã chạy!', 'success');
      this._lastInviteCampaignsUpdate = null;
      this.loadInviteCampaigns();
      this._pollInviteCampaign();
    } catch (e) {
      App.toast(e.message, 'error');
    }
  },

  async stopInviteCampaign(id) {
    try {
      await InviteAPI.stopCampaign(id);
      App.toast('Invite campaign đã dừng', 'success');
      this._lastInviteCampaignsUpdate = null;
      this.loadInviteCampaigns();
    } catch (e) {
      App.toast(e.message, 'error');
    }
  },

  async deleteInviteCampaign(id) {
    if (!confirm('Xóa invite campaign này? Sẽ xóa cả logs.')) return;
    try {
      await InviteAPI.deleteCampaign(id);
      App.toast('Đã xóa', 'success');
      this._inviteCampaigns = this._inviteCampaigns.filter(c => c.id !== id);
      this._lastInviteCampaignsUpdate = null;
      this.loadInviteCampaigns();
    } catch (e) {
      App.toast(e.message, 'error');
    }
  },

  _pollInviteCampaign() {
    if (Polling.isActive('members:inviteCampaigns')) return;

    Polling.start('members:inviteCampaigns', async () => {
      try {
        await this.loadInviteCampaigns();
        const hasRunning = this._inviteCampaigns.some(c => c.status === 'running');
        if (!hasRunning) {
          Polling.stop('members:inviteCampaigns');
        }
      } catch (e) {
        Polling.stop('members:inviteCampaigns');
      }
    }, 10000);
  },

  // ── Invite Campaign Logs ──
  async viewInviteCampaignLogs(id, name) {
    (document.getElementById('invite-campaign-logs-title') || me_dummy).textContent = `Logs: ${name}`;
    const tbody = document.getElementById('invite-campaign-logs-tbody');
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center">⏳ Đang tải...</td></tr>';
    (document.getElementById('invite-campaign-logs-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('open');

    try {
      const d = await InviteAPI.getCampaignLogs(id);
      const logs = d.logs || [];
      if (!logs.length) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text2)">Chưa có logs</td></tr>';
        return;
      }
      tbody.innerHTML = logs.map(l => {
        const statusBadge = l.status === 'success'
          ? '<span class="badge badge-green">✅</span>'
          : l.status === 'skipped'
            ? '<span class="badge" style="background:var(--orange)">⏭</span>'
            : '<span class="badge badge-red">❌</span>';
        const time = l.sent_at ? new Date(l.sent_at + 'Z').toLocaleString('vi-VN') : '—';
        return `<tr>
          <td>${l.target_username ? '@' + esc(l.target_username) : l.target_user_id}</td>
          <td>${l.account_name ? esc(l.account_name) : (l.account_id || '—')}</td>
          <td>${statusBadge}</td>
          <td style="font-size:12px;max-width:200px;overflow:hidden;text-overflow:ellipsis">${esc(l.error_message || '')}</td>
          <td style="font-size:12px;color:var(--text2)">${time}</td>
        </tr>`;
      }).join('');
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="5" style="color:var(--danger)">${esc(e.message)}</td></tr>`;
    }
  },

  closeInviteCampaignLogs() {
    (document.getElementById('invite-campaign-logs-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('open');
  },

  // ═══════════════════════════════════════════════════════════════════════════
  // INVITE CAMPAIGN MODAL
  // ═══════════════════════════════════════════════════════════════════════════

  onInviteModeChange() {
    const mode = document.querySelector('input[name="inv-mode"]:checked')?.value;
    const dmGroup = document.getElementById('inv-dm-message-group');
    if (mode === 'dm_link') {
      dmGroup.classList.remove('hidden');
    } else {
      dmGroup.classList.add('hidden');
    }
  },

  onInviteScheduleToggle() {
    const enabled = document.getElementById('inv-schedule-enabled')?.checked;
    const opts = document.getElementById('inv-schedule-options');
    if (enabled) {
      opts.classList.remove('hidden');
    } else {
      opts.classList.add('hidden');
    }
  },

  async resolveInviteGroup() {
    const input = (document.getElementById('inv-target-group')?.value || "").trim();
    if (!input) { App.toast('Nhập link hoặc username nhóm', 'error'); return; }

    const btn = document.getElementById('inv-resolve-btn');
    btn.disabled = true;
    btn.textContent = '⏳ Đang kiểm tra...';

    try {
      const d = await InviteAPI.resolveGroup(input);
      const info = d.group || d;
      (document.getElementById('inv-target-group-id') || me_dummy).value = info.id || info.group_id || '';
      (document.getElementById('inv-target-group-title') || me_dummy).value = info.title || info.name || '';
      
      const infoDiv = document.getElementById('inv-group-info');
      const infoText = document.getElementById('inv-group-info-text');
      infoDiv.classList.remove('hidden');
      infoText.innerHTML = `✅ <strong>${esc(info.title || info.name || 'Unknown')}</strong> — ${info.members_count || info.participants_count || '?'} members`;
    } catch (e) {
      App.toast(e.message || 'Không thể kiểm tra nhóm', 'error');
      (document.getElementById('inv-group-info')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');
    } finally {
      btn.disabled = false;
      btn.textContent = '🔍 Kiểm tra';
    }
  },

  async openInviteCampaignModal() {
    this._editingInviteCampaignId = null;
    (document.getElementById('invite-campaign-modal-title') || me_dummy).textContent = 'Tạo Invite Campaign';
    (document.getElementById('inv-name') || me_dummy).value = '';
    (document.getElementById('inv-name') || me_dummy).disabled = false;
    (document.getElementById('inv-target-group') || me_dummy).value = '';
    (document.getElementById('inv-target-group-id') || me_dummy).value = '';
    (document.getElementById('inv-target-group-title') || me_dummy).value = '';
    (document.getElementById('inv-group-info')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');
    (document.getElementById('inv-delay-min') || me_dummy).value = '45';
    (document.getElementById('inv-delay-max') || me_dummy).value = '120';
    (document.getElementById('inv-daily-limit') || me_dummy).value = '50';
    (document.getElementById('inv-dm-message') || me_dummy).value = '';
    (document.getElementById('inv-dm-message-group')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');
    (document.getElementById('inv-schedule-enabled') || me_dummy).checked = false;
    (document.getElementById('inv-schedule-options')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');
    (document.getElementById('inv-schedule-time') || me_dummy).value = '09:00';
    (document.getElementById('inv-schedule-days') || me_dummy).value = '7';

    // Reset invite mode to direct
    document.querySelectorAll('input[name="inv-mode"]').forEach(r => r.checked = r.value === 'direct');

    // Populate scrape jobs dropdown
    const jobSel = document.getElementById('inv-scrape-job');
    jobSel.disabled = false;
    if (this._scrapeJobs.length) {
      jobSel.innerHTML = this._scrapeJobs.map(j =>
        `<option value="${esc(j.scrape_job_id)}">${esc(j.group_title || j.group_id)} (${j.member_count} members)</option>`
      ).join('');
    } else {
      jobSel.innerHTML = '<option value="">Chưa có dữ liệu cào</option>';
    }

    // Populate accounts
    await this._populateInviteAccounts();

    // Reset save button
    const saveBtn = document.getElementById('inv-save-btn');
    saveBtn.setAttribute('onclick', 'Members.saveInviteCampaign()');
    saveBtn.textContent = '🚀 Tạo Campaign';

    (document.getElementById('invite-campaign-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('open');
  },

  async _populateInviteAccounts(selectedIds = null) {
    const now = Date.now();
    if (!this._accounts?.length || !this._accountsCachedAt || (now - this._accountsCachedAt) > 30000) {
      try {
        const d = await API.getAccounts();
        this._accounts = d.accounts || [];
        this._accountsCachedAt = now;
      } catch (e) {}
    }

    const sortedAccounts = [...this._accounts].sort((a, b) => (b.is_premium || 0) - (a.is_premium || 0));
    const accDiv = document.getElementById('inv-accounts-list');
    accDiv.innerHTML = `
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;width:100%;flex-wrap:wrap">
        <button type="button" class="btn btn-ghost btn-sm inv-acc-filter active" data-filter="all" onclick="Members.filterInviteAccounts('all')" style="font-size:12px">Tất cả (${sortedAccounts.length})</button>
        <button type="button" class="btn btn-ghost btn-sm inv-acc-filter" data-filter="premium" onclick="Members.filterInviteAccounts('premium')" style="font-size:12px">⭐ Premium (${sortedAccounts.filter(a=>a.is_premium).length})</button>
        <span style="flex:1"></span>
        <button type="button" class="btn btn-ghost btn-sm" onclick="Members.toggleAllInviteAccounts(true)" style="font-size:11px;color:var(--accent);padding:2px 8px" title="Chọn tất cả">☑ Chọn hết</button>
        <button type="button" class="btn btn-ghost btn-sm" onclick="Members.selectInvitePremiumOnly()" style="font-size:11px;color:var(--orange);padding:2px 8px" title="Chỉ chọn Premium">⭐ Chọn Premium</button>
        <button type="button" class="btn btn-ghost btn-sm" onclick="Members.toggleAllInviteAccounts(false)" style="font-size:11px;color:var(--danger);padding:2px 8px" title="Bỏ chọn tất cả">☐ Bỏ hết</button>
      </div>
      <div id="inv-accounts-chips" style="display:flex;flex-wrap:wrap;gap:8px"></div>
    `;
    this._sortedInviteAccounts = sortedAccounts;
    this._renderInviteAccountChips(sortedAccounts, selectedIds);
  },

  _renderInviteAccountChips(accounts, selectedIds = null) {
    const container = document.getElementById('inv-accounts-chips');
    if (!container) return;
    container.innerHTML = accounts.map(a => {
      const name = a.user_info
        ? [a.user_info.first_name, a.user_info.last_name].filter(Boolean).join(' ')
        : a.name;
      const phone = a.phone || '';
      const premiumBadge = a.is_premium ? '⭐ ' : '';
      const premiumStyle = a.is_premium ? 'border-color:var(--orange);' : '';
      const checked = selectedIds ? (selectedIds.includes(a.id) ? 'checked' : '') : 'checked';
      return `<label class="cmp-acc-chip" style="${premiumStyle}" data-premium="${a.is_premium ? 1 : 0}">
        <input type="checkbox" class="inv-acc-checkbox" value="${a.id}" ${checked}>
        <span title="${esc(name || phone)}">${premiumBadge}${esc(shortName(name || phone))}</span>
      </label>`;
    }).join('');
  },

  filterInviteAccounts(filter) {
    document.querySelectorAll('.inv-acc-filter').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.filter === filter);
      btn.style.background = btn.dataset.filter === filter ? 'var(--primary)' : '';
      btn.style.color = btn.dataset.filter === filter ? '#fff' : '';
    });
    const accounts = filter === 'premium'
      ? this._sortedInviteAccounts.filter(a => a.is_premium)
      : this._sortedInviteAccounts;
    this._renderInviteAccountChips(accounts);
  },

  toggleAllInviteAccounts(check) {
    const chips = document.getElementById('inv-accounts-chips');
    if (!chips) return;
    chips.querySelectorAll('.inv-acc-checkbox').forEach(cb => {
      cb.checked = check;
    });
  },

  selectInvitePremiumOnly() {
    const chips = document.getElementById('inv-accounts-chips');
    if (!chips) return;
    chips.querySelectorAll('label[data-premium]').forEach(label => {
      const cb = label.querySelector('.inv-acc-checkbox');
      if (!cb) return;
      cb.checked = label.dataset.premium === '1';
    });
  },

  closeInviteCampaignModal() {
    (document.getElementById('invite-campaign-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('open');
    this._editingInviteCampaignId = null;
  },

  async saveInviteCampaign() {
    const name = (document.getElementById('inv-name')?.value || "").trim();
    const jobId = document.getElementById('inv-scrape-job')?.value;
    const targetGroup = (document.getElementById('inv-target-group')?.value || "").trim();
    const targetGroupId = document.getElementById('inv-target-group-id')?.value;
    const targetGroupTitle = document.getElementById('inv-target-group-title')?.value;
    const inviteMode = document.querySelector('input[name="inv-mode"]:checked')?.value || 'direct';
    const dmMessage = (document.getElementById('inv-dm-message')?.value || "").trim();
    const delayMin = parseInt(document.getElementById('inv-delay-min')?.value) || 45;
    const delayMax = parseInt(document.getElementById('inv-delay-max')?.value) || 120;
    const dailyLimit = parseInt(document.getElementById('inv-daily-limit')?.value) || 50;

    if (!name) { App.toast('Nhập tên campaign', 'error'); return; }
    if (!jobId) { App.toast('Chọn nguồn members', 'error'); return; }
    if (!targetGroup && !targetGroupId) { App.toast('Nhập nhóm/kênh đích', 'error'); return; }
    if (inviteMode === 'dm_link' && !dmMessage) { App.toast('Nhập nội dung DM khi chọn chế độ DM Link', 'error'); return; }

    // Collect sender accounts
    const accCheckboxes = document.querySelectorAll('.inv-acc-checkbox:checked');
    const senderIds = Array.from(accCheckboxes).map(cb => parseInt(cb.value));
    if (!senderIds.length) { App.toast('Chọn ít nhất 1 tài khoản', 'error'); return; }

    // Schedule
    const scheduleEnabled = document.getElementById('inv-schedule-enabled')?.checked;
    const scheduleTime = document.getElementById('inv-schedule-time')?.value;
    const scheduleDays = parseInt(document.getElementById('inv-schedule-days')?.value) || 7;

    const data = {
      name,
      scrape_job_id: jobId,
      target_group: targetGroup || targetGroupId,
      target_group_id: targetGroupId || undefined,
      target_group_title: targetGroupTitle || undefined,
      invite_mode: inviteMode,
      dm_message: inviteMode === 'dm_link' ? dmMessage : undefined,
      sender_account_ids: senderIds,
      delay_min: delayMin,
      delay_max: delayMax,
      daily_limit: dailyLimit,
      schedule_enabled: scheduleEnabled,
      schedule_time: scheduleEnabled ? scheduleTime : undefined,
      schedule_days: scheduleEnabled ? scheduleDays : undefined,
    };

    try {
      const r = await InviteAPI.createCampaign(data);
      App.toast(`Invite campaign tạo thành công! (${r.total_targets || 0} targets)`, 'success');
      this.closeInviteCampaignModal();
      this._lastInviteCampaignsUpdate = null;
      this.loadInviteCampaigns();
    } catch (e) {
      App.toast(e.message, 'error');
    }
  },

  async openEditInviteCampaign(id) {
    try {
      const d = await InviteAPI.getCampaign(id);
      const c = d.campaign;
      if (!c) { App.toast('Campaign không tồn tại', 'error'); return; }
      if (!['draft', 'paused', 'paused_auto', 'error'].includes(c.status)) {
        App.toast('Chỉ sửa được khi campaign đang tạm dừng', 'error');
        return;
      }

      this._editingInviteCampaignId = id;

      (document.getElementById('invite-campaign-modal-title') || me_dummy).textContent = `✏️ Sửa Invite Campaign: ${c.name}`;
      (document.getElementById('inv-name') || me_dummy).value = c.name;
      (document.getElementById('inv-name') || me_dummy).disabled = true;

      // Scrape job (locked)
      const jobSel = document.getElementById('inv-scrape-job');
      jobSel.innerHTML = `<option value="${esc(c.scrape_job_id)}" selected>${esc((c.scrape_job_id || '').substring(0, 30))}...</option>`;
      jobSel.disabled = true;

      // Target group
      (document.getElementById('inv-target-group') || me_dummy).value = c.target_group || '';
      (document.getElementById('inv-target-group-id') || me_dummy).value = c.target_group_id || '';
      (document.getElementById('inv-target-group-title') || me_dummy).value = c.target_group_title || '';
      if (c.target_group_title) {
        const infoDiv = document.getElementById('inv-group-info');
        const infoText = document.getElementById('inv-group-info-text');
        infoDiv.classList.remove('hidden');
        infoText.innerHTML = `✅ <strong>${esc(c.target_group_title)}</strong>`;
      }

      // Mode
      document.querySelectorAll('input[name="inv-mode"]').forEach(r => r.checked = r.value === (c.invite_mode || 'direct'));
      this.onInviteModeChange();
      if (c.invite_mode === 'dm_link' && c.dm_message) {
        (document.getElementById('inv-dm-message') || me_dummy).value = c.dm_message;
      }

      // Settings
      (document.getElementById('inv-delay-min') || me_dummy).value = c.delay_min || 45;
      (document.getElementById('inv-delay-max') || me_dummy).value = c.delay_max || 120;
      (document.getElementById('inv-daily-limit') || me_dummy).value = c.daily_limit || 50;

      // Schedule
      (document.getElementById('inv-schedule-enabled') || me_dummy).checked = !!c.schedule_enabled;
      this.onInviteScheduleToggle();
      if (c.schedule_time) (document.getElementById('inv-schedule-time') || me_dummy).value = c.schedule_time;
      if (c.schedule_days) (document.getElementById('inv-schedule-days') || me_dummy).value = c.schedule_days;

      // Accounts
      await this._populateInviteAccounts(c.sender_account_ids || []);

      // Change save button to edit mode
      const saveBtn = document.getElementById('inv-save-btn');
      saveBtn.setAttribute('onclick', 'Members.saveEditedInviteCampaign()');
      saveBtn.textContent = '💾 Lưu thay đổi';

      (document.getElementById('invite-campaign-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('open');
    } catch (e) {
      App.toast(e.message, 'error');
    }
  },

  async saveEditedInviteCampaign() {
    const id = this._editingInviteCampaignId;
    if (!id) { App.toast('Lỗi: không có campaign để sửa', 'error'); return; }

    const targetGroup = (document.getElementById('inv-target-group')?.value || "").trim();
    const targetGroupId = document.getElementById('inv-target-group-id')?.value;
    const targetGroupTitle = document.getElementById('inv-target-group-title')?.value;
    const inviteMode = document.querySelector('input[name="inv-mode"]:checked')?.value || 'direct';
    const dmMessage = (document.getElementById('inv-dm-message')?.value || "").trim();
    const delayMin = parseInt(document.getElementById('inv-delay-min')?.value) || 45;
    const delayMax = parseInt(document.getElementById('inv-delay-max')?.value) || 120;
    const dailyLimit = parseInt(document.getElementById('inv-daily-limit')?.value) || 50;

    const accCheckboxes = document.querySelectorAll('.inv-acc-checkbox:checked');
    const senderIds = Array.from(accCheckboxes).map(cb => parseInt(cb.value));
    if (!senderIds.length) { App.toast('Chọn ít nhất 1 tài khoản', 'error'); return; }

    const scheduleEnabled = document.getElementById('inv-schedule-enabled')?.checked;
    const scheduleTime = document.getElementById('inv-schedule-time')?.value;
    const scheduleDays = parseInt(document.getElementById('inv-schedule-days')?.value) || 7;

    try {
      await InviteAPI.updateCampaign(id, {
        target_group: targetGroup || targetGroupId,
        target_group_id: targetGroupId || undefined,
        target_group_title: targetGroupTitle || undefined,
        invite_mode: inviteMode,
        dm_message: inviteMode === 'dm_link' ? dmMessage : undefined,
        sender_account_ids: senderIds,
        delay_min: delayMin,
        delay_max: delayMax,
        daily_limit: dailyLimit,
        schedule_enabled: scheduleEnabled,
        schedule_time: scheduleEnabled ? scheduleTime : undefined,
        schedule_days: scheduleEnabled ? scheduleDays : undefined,
      });
      App.toast('✅ Đã cập nhật invite campaign!', 'success');
      this._editingInviteCampaignId = null;
      this.closeInviteCampaignModal();
      this._lastInviteCampaignsUpdate = null;
      this.loadInviteCampaigns();
    } catch (e) {
      App.toast(e.message, 'error');
    }
  },

  // ═══════════════════════════════════════════════════════════════════════════
  // SPAM CHECK
  // ═══════════════════════════════════════════════════════════════════════════

  // SpamBot result badge. A result is only a snapshot: older than 12h it is
  // shown faded with its age ("Sạch · 1 ngày trước") so it is not mistaken
  // for the current state. Click = re-check this account now.
  _spamBadgeHtml(status, message, checkedAt, accountId, expired) {
    const map = {
      free: { text: '✅ Sạch', c: 'var(--green)' },
      limited: { text: '🚫 Bị giới hạn', c: 'var(--red)' },
      expired: { text: '⌛ Hết hạn giới hạn', c: 'var(--orange)' },
      unknown: { text: '⚠️ Không rõ', c: 'var(--orange)' },
    };
    const info = map[status === 'limited' && expired ? 'expired' : status] || map.unknown;
    const ts = checkedAt ? new Date(checkedAt).getTime() : 0;
    const ageH = ts ? (Date.now() - ts) / 3600000 : null;
    const stale = ageH !== null && ageH > 12;
    const ago = ageH === null ? '' : ageH < 1 ? 'vừa xong' : ageH < 24 ? `${Math.floor(ageH)} giờ trước` : `${Math.floor(ageH / 24)} ngày trước`;
    const c = stale ? 'var(--text3)' : info.c;
    const tip = `SpamBot lúc ${ts ? new Date(ts).toLocaleString('vi-VN') : '?'}${message ? ' — ' + message : ''}${status === 'limited' && expired ? ' · Ngày hết giới hạn đã qua — lần chạy campaign tới sẽ hỏi lại @SpamBot' : ''}${accountId ? ' · Bấm để kiểm tra lại ngay' : ''}`;
    const click = accountId ? ` onclick="event.stopPropagation();Members.recheckSpam(${accountId},this)" ` : ' ';
    return `<span${click}style="cursor:${accountId ? 'pointer' : 'default'};background:color-mix(in srgb, ${c} 16%, transparent);border:1px ${stale ? 'dashed' : 'solid'} color-mix(in srgb, ${c} 50%, transparent);border-radius:4px;padding:1px 7px;font-size:.72rem;font-weight:600;color:${c}" title="${esc(tip)}">${info.text}${stale ? ` <span style="font-weight:400">· ${ago}</span>` : ''}</span>`;
  },

  async recheckSpam(accountId, el) {
    if (el) { el.style.opacity = '.5'; el.textContent = '⏳ Đang kiểm tra'; }
    try {
      const res = await fetch(`/api/members/accounts/${accountId}/spam-check`, { method: 'POST', headers: API.getHeaders() });
      const r = await res.json();
      if (!res.ok) throw new Error(r.detail || `HTTP ${res.status}`);
      App._spamStatus = App._spamStatus || {};
      App._spamStatus[accountId] = r;
      if (el) el.outerHTML = this._spamBadgeHtml(r.status, r.message, r.checked_at, accountId);
    } catch (e) {
      App.toast('Lỗi kiểm tra spam: ' + (e.message || e), 'error');
      if (el) el.style.opacity = '';
    }
  },

  async checkAllAccountsSpam() {
    const btn = document.getElementById('btn-spam-check');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Đang kiểm tra...'; }

    try {
      const res = await fetch('/api/members/accounts/spam-check-all', {
        method: 'POST',
        headers: API.getHeaders()
      });
      let d = {};
      try {
        d = await res.json();
      } catch (jsonErr) {
        throw new Error(`Server gặp lỗi HTTP ${res.status}`);
      }
      if (!res.ok) throw new Error(d.detail || `Lỗi kiểm tra spam (HTTP ${res.status})`);

      const results = d.results || [];
      results.forEach(r => {
        const card = document.querySelector(`.account-card[data-account-id="${r.account_id}"]`);
        if (!card) return;
        const slot = card.querySelector('.spam-badge-slot');
        if (slot) slot.innerHTML = this._spamBadgeHtml(r.status, r.message, r.checked_at, r.account_id);
        App._spamStatus = App._spamStatus || {};
        App._spamStatus[r.account_id] = r;
      });

      const freeCount = results.filter(r => r.status === 'free').length;
      const limitedCount = results.filter(r => r.status === 'limited').length;
      App.toast(`Spam check: ${freeCount} sạch, ${limitedCount} bị giới hạn`, freeCount > 0 && limitedCount === 0 ? 'success' : 'error');
    } catch (e) {
      App.toast(e.message || 'Lỗi kiểm tra spam', 'error');
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = '🛡️ Kiểm tra Spam'; }
    }
  },
};

if (typeof window !== 'undefined') {
  window.Members = Members;
}

// ── Helper: HTML escape (reuse App.esc if available, fallback) ──
if (typeof esc !== 'function') {
  function esc(s) {
    if (s == null) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
}
