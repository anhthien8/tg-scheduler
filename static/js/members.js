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
  _campaignPollInterval: null,
  _inviteCampaignPollInterval: null,
  _deepCrawlPollInterval: 3000,
  _deepCrawlPrevState: null,
  _editingInviteCampaignId: null,

  // ── Init: load data when navigating to members page ──
  async init() {
    this._lastCampaignsUpdate = null;
    this._lastInviteCampaignsUpdate = null;
    if (this._campaignPollInterval) {
      clearInterval(this._campaignPollInterval);
      this._campaignPollInterval = null;
    }
    if (this._inviteCampaignPollInterval) {
      clearInterval(this._inviteCampaignPollInterval);
      this._inviteCampaignPollInterval = null;
    }
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

    // Restore deep crawl polling and UI state on page refresh if active
    if (!this._deepCrawlPolling) {
      try {
        const res = await fetch('/api/members/deep-crawl/status');
        if (res.ok) {
          const s = await res.json();
          if (s.status === 'running') {
            this._deepCrawlPolling = true;
            this._deepCrawlPollInterval = 3000;
            this._deepCrawlPrevState = null;

            // Auto-switch to "Similar Channels" tab
            const simTab = document.getElementById('members-tab-similar');
            if (simTab) simTab.click();

            const btn = document.getElementById('sim-btn-search');
            const stopBtn = document.getElementById('sim-btn-stop');
            const progressPanel = document.getElementById('sim-progress-panel');
            if (btn) {
              btn.disabled = true;
              btn.textContent = '⏳ Đang Deep Crawl...';
            }
            if (stopBtn) stopBtn.classList.remove('hidden');
            if (progressPanel) progressPanel.classList.remove('hidden');

            // Immediately render current server state before starting poll
            this._renderDeepCrawlProgressFromState(s);

            this._pollDeepCrawlProgress();
          } else if (s.status === 'completed' || s.status === 'stopped') {
            // Crawl finished while tab was closed — show results
            const simTab = document.getElementById('members-tab-similar');
            if (simTab) simTab.click();
            const progressPanel = document.getElementById('sim-progress-panel');
            if (progressPanel) progressPanel.classList.remove('hidden');
            this._renderDeepCrawlProgressFromState(s);
            await this._fetchDeepCrawlResults();
          }
        }
      } catch (e) {
        console.error('Error restoring deep crawl status:', e);
      }
    }
  },

  // ── Account dropdown populate ──
  async populateAccounts() {
    try {
      const d = await API.getAccounts();
      this._accounts = d.accounts || [];
    } catch (e) { this._accounts = []; }
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
    this.loadGroups();
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
      const groups = (d.chats || []).filter(c =>
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
  _batchPollTimer: null,
  async _pollBatchProgress(batchJobId) {
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
          clearInterval(this._batchPollTimer);
          this._batchPollTimer = null;
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
    this._batchPollTimer = setInterval(poll, 5000);
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

    tbody.innerHTML = jobs.map((j, i) => {
      const date = j.scraped_at ? new Date(j.scraped_at + 'Z').toLocaleString('vi-VN') : '—';
      return `<tr>
        <td>${i + 1}</td>
        <td>${esc(j.group_title || j.group_id)}</td>
        <td><span class="badge badge-blue">${j.member_count}</span></td>
        <td style="font-size:12px;color:var(--text2)">${date}</td>
        <td>
          <div class="btn-group">
            <button class="btn btn-ghost btn-sm" onclick="Members.viewMembers('${esc(j.scrape_job_id)}','${esc(j.group_title || '')}')" title="Xem">👁 Xem</button>
            <button class="btn btn-ghost btn-sm" onclick="window.open(AnalyticsAPI.exportMembers('${esc(j.scrape_job_id)}'),'_blank')" title="Export CSV">📥</button>
            <button class="btn btn-danger btn-sm" onclick="Members.deleteScrapeJob('${esc(j.scrape_job_id)}')">🗑</button>
          </div>
        </td>
      </tr>`;
    }).join('');
  },

  // ── View Members Detail ──
  async viewMembers(jobId, title) {
    (document.getElementById('members-detail-title') || me_dummy).textContent = `Members: ${title || jobId}`;
    const tbody = document.getElementById('members-detail-tbody');
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center">⏳ Đang tải...</td></tr>';
    (document.getElementById('members-detail-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('open');

    try {
      const d = await MembersAPI.getScrapeMembers(jobId, 500);
      const members = d.members || [];
      if (!members.length) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text2)">Không có members</td></tr>';
        return;
      }
      tbody.innerHTML = members.map((m, i) => {
        const name = [m.first_name, m.last_name].filter(Boolean).join(' ') || '—';
        const ls = m.last_seen || '—';
        return `<tr>
          <td>${i + 1}</td>
          <td style="font-size:12px">${m.user_id}</td>
          <td>${m.username ? '@' + esc(m.username) : '<span style="color:var(--text2)">—</span>'}</td>
          <td>${esc(name)}</td>
          <td>${m.is_premium ? '<span class="badge badge-green">⭐</span>' : '—'}</td>
          <td style="font-size:12px;color:var(--text2)">${esc(ls)}</td>
        </tr>`;
      }).join('');
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--danger)">${esc(e.message)}</td></tr>`;
    }
  },

  closeDetailModal() {
    (document.getElementById('members-detail-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('open');
  },

  // ── Delete Scrape Job ──
  async deleteScrapeJob(jobId) {
    if (!confirm('Xóa dữ liệu cào này?')) return;
    try {
      await MembersAPI.deleteScrapeJob(jobId);
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
      const progress = total > 0 ? Math.round(((sent + failed + skipped) / total) * 100) : 0;

      let actions = '';
      if (c.status === 'draft' || c.status === 'paused' || c.status === 'error') {
        actions += `<button class="btn btn-primary btn-sm" onclick="Members.startCampaign(${c.id})">▶ Chạy</button>`;
        actions += `<button class="btn btn-ghost btn-sm" onclick="Members.editCampaignMessages(${c.id})" title="Sửa tin nhắn">✏️</button>`;
      }
      if (c.status === 'running') {
        actions += `<button class="btn btn-danger btn-sm" onclick="Members.stopCampaign(${c.id})">⏸ Dừng</button>`;
      }
      if (c.status === 'scheduled') {
        actions += `<button class="btn btn-ghost btn-sm" onclick="Members.cancelSchedule(${c.id})" style="color:var(--danger)">Hủy lịch</button>`;
      }
      actions += `<button class="btn btn-ghost btn-sm" onclick="Members.cloneCampaign(${c.id})" title="Nhân bản (Clone) chiến dịch">📑</button>`;
      actions += `<button class="btn btn-ghost btn-sm" onclick="Members.viewCampaignLogs(${c.id},'${esc(c.name)}')">📋</button>`;
      actions += `<button class="btn btn-danger btn-sm" onclick="Members.deleteCampaign(${c.id})">🗑</button>`;

      let scheduleInfo = '';
      if (c.status === 'scheduled' && c.scheduled_at) {
        const time = new Date(c.scheduled_at + 'Z').toLocaleString('vi-VN');
        const tz = c.target_timezone ? `<br><small style="color:var(--text2)">${Members._tzLabel(c.target_timezone)}</small>` : '';
        scheduleInfo = `<div style="font-size:11px;margin-top:4px;color:var(--accent)">⏰ ${time}${tz}</div>`;
      }

      const rowHtml = `
        <td>${i + 1}</td>
        <td>
          ${esc(c.name)}
          ${scheduleInfo}
        </td>
        <td style="font-size:12px">${esc(c.scrape_job_id.substring(0, 20))}...</td>
        <td>${statusBadge}</td>
        <td>
          <div style="display:flex;align-items:center;gap:8px">
            <div style="flex:1;background:var(--bg2);border-radius:4px;height:8px;overflow:hidden">
              <div style="width:${progress}%;height:100%;background:var(--accent);transition:width .3s"></div>
            </div>
            <span style="font-size:12px;color:var(--text2)">${sent}/${total}</span>
          </div>
          <div style="font-size:11px;color:var(--text2);margin-top:2px">
            ✅${sent} ❌${failed} ⏭${skipped}
          </div>
        </td>
        <td><div class="btn-group">${actions}</div></td>
      `;

      let existingRow = tbody.querySelector(`tr[data-id="${c.id}"]`);
      if (existingRow) {
        const existingIdx = parseInt(existingRow.getAttribute('data-index'));
        if (existingIdx !== (i + 1) || existingRow.getAttribute('data-updated-at') !== c.updated_at || existingRow.getAttribute('data-status') !== c.status) {
          existingRow.innerHTML = rowHtml;
          existingRow.setAttribute('data-updated-at', c.updated_at);
          existingRow.setAttribute('data-status', c.status);
          existingRow.setAttribute('data-index', i + 1);
        }
      } else {
        const tr = document.createElement('tr');
        tr.setAttribute('data-id', c.id);
        tr.setAttribute('data-updated-at', c.updated_at);
        tr.setAttribute('data-status', c.status);
        tr.setAttribute('data-index', i + 1);
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
    const map = {
      draft: '<span class="badge" style="background:var(--text2)">📝 Draft</span>',
      running: '<span class="badge badge-blue">🔄 Running</span>',
      paused: '<span class="badge" style="background:#f59e0b">⏸ Paused</span>',
      completed: '<span class="badge badge-green">✅ Done</span>',
      error: '<span class="badge badge-red">❌ Error</span>',
      scheduled: '<span class="badge" style="background:#8b5cf6">⏰ Scheduled</span>',
    };
    return map[status] || `<span class="badge">${status}</span>`;
  },

  // ── Campaign Actions ──
  async startCampaign(id) {
    try {
      const r = await MembersAPI.startCampaign(id);
      App.toast(r.message || 'Campaign đã chạy!', 'success');
      this._lastCampaignsUpdate = null;
      this.loadCampaigns();
      // Auto-refresh while running
      this._pollCampaign(id);
    } catch (e) {
      App.toast(e.message, 'error');
    }
  },

  async stopCampaign(id) {
    try {
      await MembersAPI.stopCampaign(id);
      App.toast('Campaign đã dừng', 'success');
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
    if (this._campaignPollInterval) return;

    this._campaignPollInterval = setInterval(async () => {
      try {
        await this.loadCampaigns();
        
        const hasRunning = this._campaigns.some(c => c.status === 'running');
        if (!hasRunning) {
          clearInterval(this._campaignPollInterval);
          this._campaignPollInterval = null;
        }
      } catch (e) {
        clearInterval(this._campaignPollInterval);
        this._campaignPollInterval = null;
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
      if (!['draft', 'paused', 'error'].includes(c.status)) {
        App.toast('Chỉ sửa được khi campaign đang tạm dừng', 'error');
        return;
      }

      this._editingCampaignId = id;

      // Set modal title to edit mode
      (document.getElementById('campaign-modal-title') || me_dummy).textContent = `✏️ Sửa Campaign: ${c.name}`;

      // Fill in settings
      (document.getElementById('cmp-name') || me_dummy).value = c.name;
      (document.getElementById('cmp-name') || me_dummy).disabled = true; // Can't change name
      (document.getElementById('cmp-delay-min') || me_dummy).value = c.delay_min || 30;
      (document.getElementById('cmp-delay-max') || me_dummy).value = c.delay_max || 90;
      (document.getElementById('cmp-daily-limit-premium') || me_dummy).value = c.daily_limit_premium || 60;
      (document.getElementById('cmp-daily-limit-normal') || me_dummy).value = c.daily_limit_normal || 10;
      (document.getElementById('cmp-ai-remix') || me_dummy).checked = !!c.use_ai_remix;
      // Populate AI Agent dropdown if available
      if (typeof AIAgents !== 'undefined') {
        AIAgents.getAgentsForDropdown().then(() => {
          AIAgents.populateAgentDropdown('cmp-ai-agent', c.ai_agent_id || null);
        });
      }
      const excludePrevEl = document.getElementById('cmp-exclude-previous');
      if (excludePrevEl) {
        excludePrevEl.checked = c.exclude_previous_dms !== undefined ? !!c.exclude_previous_dms : true;
      }

      // Hide scrape job selector (can't change target)
      const jobSel = document.getElementById('cmp-scrape-job');
      jobSel.innerHTML = `<option value="${esc(c.scrape_job_id)}" selected>${esc(c.scrape_job_id.substring(0, 30))}...</option>`;
      jobSel.disabled = true;

      // Load accounts & mark sender accounts
      const now = Date.now();
      if (!this._accounts?.length || !this._accountsCachedAt || (now - this._accountsCachedAt) > 30000) {
        try {
          const ad = await API.getAccounts();
          this._accounts = ad.accounts || [];
          this._accountsCachedAt = now;
        } catch (e) {}
      }

      const sortedAccounts = [...this._accounts].sort((a, b) => (b.is_premium || 0) - (a.is_premium || 0));
      const accDiv = document.getElementById('cmp-accounts-list');
      accDiv.innerHTML = `
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;width:100%;flex-wrap:wrap">
          <button type="button" class="btn btn-ghost btn-sm cmp-acc-filter active" data-filter="all" onclick="Members.filterCampaignAccounts('all')" style="font-size:12px">Tất cả (${sortedAccounts.length})</button>
          <button type="button" class="btn btn-ghost btn-sm cmp-acc-filter" data-filter="premium" onclick="Members.filterCampaignAccounts('premium')" style="font-size:12px">⭐ Premium (${sortedAccounts.filter(a=>a.is_premium).length})</button>
          <span style="flex:1"></span>
          <button type="button" class="btn btn-ghost btn-sm" onclick="Members.toggleAllCampaignAccounts(true)" style="font-size:11px;color:var(--accent);padding:2px 8px" title="Chọn tất cả">☑ Chọn hết</button>
          <button type="button" class="btn btn-ghost btn-sm" onclick="Members.selectPremiumOnly()" style="font-size:11px;color:#f59e0b;padding:2px 8px" title="Chỉ chọn Premium">⭐ Chọn Premium</button>
          <button type="button" class="btn btn-ghost btn-sm" onclick="Members.toggleAllCampaignAccounts(false)" style="font-size:11px;color:var(--danger);padding:2px 8px" title="Bỏ chọn tất cả">☐ Bỏ hết</button>
        </div>
        <div id="cmp-accounts-chips" style="display:flex;flex-wrap:wrap;gap:8px"></div>
      `;
      this._sortedCampaignAccounts = sortedAccounts;

      // Render account chips, pre-check sender accounts
      const senderIds = c.sender_account_ids || [];
      const container = document.getElementById('cmp-accounts-chips');
      container.innerHTML = sortedAccounts.map(a => {
        const name = a.user_info
          ? [a.user_info.first_name, a.user_info.last_name].filter(Boolean).join(' ')
          : a.name;
        const phone = a.phone || '';
        const premiumBadge = a.is_premium ? '⭐ ' : '';
        const premiumStyle = a.is_premium ? 'border-color:#f59e0b;' : '';
        const checked = senderIds.includes(a.id) ? 'checked' : '';
        return `<label style="display:flex;align-items:center;gap:6px;padding:8px 12px;background:var(--bg2);border-radius:8px;cursor:pointer;border:1px solid var(--border);font-size:13px;${premiumStyle}" data-premium="${a.is_premium ? 1 : 0}">
          <input type="checkbox" class="cmp-acc-checkbox" value="${a.id}" ${checked}>
          <span>${premiumBadge}${esc(name || phone)}</span>
        </label>`;
      }).join('');

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

    const delayMin = parseInt(document.getElementById('cmp-delay-min')?.value) || 30;
    const delayMax = parseInt(document.getElementById('cmp-delay-max')?.value) || 90;
    const dailyLimitPremium = parseInt(document.getElementById('cmp-daily-limit-premium')?.value) || 60;
    const dailyLimitNormal = parseInt(document.getElementById('cmp-daily-limit-normal')?.value) || 10;
    const useAi = document.getElementById('cmp-ai-remix')?.checked;
    const agentIdVal = document.getElementById('cmp-ai-agent')?.value;
    const aiAgentId = agentIdVal ? parseInt(agentIdVal) : null;
    const excludePrev = document.getElementById('cmp-exclude-previous')?.checked ?? true;

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
        delay_min: delayMin,
        delay_max: delayMax,
        daily_limit_premium: dailyLimitPremium,
        daily_limit_normal: dailyLimitNormal,
        use_ai_remix: useAi,
        ai_agent_id: aiAgentId,
        exclude_previous_dms: excludePrev,
      });
      App.toast('✅ Đã cập nhật tin nhắn campaign!', 'success');
      this._editingCampaignId = null;
      this.closeCampaignModal();
      this.loadCampaigns();
    } catch (e) {
      App.toast(e.message, 'error');
    }
  },

  // ── Campaign Logs ──
  async viewCampaignLogs(id, name) {
    (document.getElementById('campaign-logs-title') || me_dummy).textContent = `Logs: ${name}`;
    const tbody = document.getElementById('campaign-logs-tbody');
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center">⏳ Đang tải...</td></tr>';
    (document.getElementById('campaign-logs-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('open');

    try {
      const d = await MembersAPI.getCampaignLogs(id);
      const logs = d.logs || [];
      if (!logs.length) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text2)">Chưa có logs</td></tr>';
        return;
      }
      tbody.innerHTML = logs.map(l => {
        const statusBadge = l.status === 'success'
          ? '<span class="badge badge-green">✅</span>'
          : l.status === 'skipped'
            ? '<span class="badge" style="background:#f59e0b">⏭</span>'
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

  closeCampaignLogs() {
    (document.getElementById('campaign-logs-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('open');
  },

  // ═══════════════════════════════════════════════════════════════════════════
  // CAMPAIGN MODAL
  // ═══════════════════════════════════════════════════════════════════════════

  async openCampaignModal() {
    (document.getElementById('campaign-modal-title') || me_dummy).textContent = 'Tạo DM Campaign';
    (document.getElementById('cmp-name') || me_dummy).value = '';
    (document.getElementById('cmp-delay-min') || me_dummy).value = '30';
    (document.getElementById('cmp-delay-max') || me_dummy).value = '90';
    (document.getElementById('cmp-daily-limit-premium') || me_dummy).value = '60';
    (document.getElementById('cmp-daily-limit-normal') || me_dummy).value = '10';
    (document.getElementById('cmp-ai-remix') || me_dummy).checked = false;
    const excludePrevEl = document.getElementById('cmp-exclude-previous');
    if (excludePrevEl) excludePrevEl.checked = true;
    (document.getElementById('cmp-messages-list') || me_dummy).innerHTML = '';

    const schedToggle = document.getElementById('cmp-schedule-toggle');
    if (schedToggle) {
      schedToggle.checked = false;
      this.toggleScheduleSection();
    }
    const schedAt = document.getElementById('cmp-scheduled-at');
    if (schedAt) schedAt.value = '';

    // Populate scrape jobs dropdown
    const jobSel = document.getElementById('cmp-scrape-job');
    if (this._scrapeJobs.length) {
      jobSel.innerHTML = this._scrapeJobs.map(j =>
        `<option value="${esc(j.scrape_job_id)}">${esc(j.group_title || j.group_id)} (${j.member_count} members)</option>`
      ).join('');
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

    // Sort: premium first
    const sortedAccounts = [...this._accounts].sort((a, b) => (b.is_premium || 0) - (a.is_premium || 0));

    const accDiv = document.getElementById('cmp-accounts-list');
    // Add filter toggle
    accDiv.innerHTML = `
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;width:100%;flex-wrap:wrap">
        <button type="button" class="btn btn-ghost btn-sm cmp-acc-filter active" data-filter="all" onclick="Members.filterCampaignAccounts('all')" style="font-size:12px">Tất cả (${sortedAccounts.length})</button>
        <button type="button" class="btn btn-ghost btn-sm cmp-acc-filter" data-filter="premium" onclick="Members.filterCampaignAccounts('premium')" style="font-size:12px">⭐ Premium (${sortedAccounts.filter(a=>a.is_premium).length})</button>
        <span style="flex:1"></span>
        <button type="button" class="btn btn-ghost btn-sm" onclick="Members.toggleAllCampaignAccounts(true)" style="font-size:11px;color:var(--accent);padding:2px 8px" title="Chọn tất cả">☑ Chọn hết</button>
        <button type="button" class="btn btn-ghost btn-sm" onclick="Members.selectPremiumOnly()" style="font-size:11px;color:#f59e0b;padding:2px 8px" title="Chỉ chọn Premium">⭐ Chọn Premium</button>
        <button type="button" class="btn btn-ghost btn-sm" onclick="Members.toggleAllCampaignAccounts(false)" style="font-size:11px;color:var(--danger);padding:2px 8px" title="Bỏ chọn tất cả">☐ Bỏ hết</button>
      </div>
      <div id="cmp-accounts-chips" style="display:flex;flex-wrap:wrap;gap:8px"></div>
    `;
    this._sortedCampaignAccounts = sortedAccounts;
    this._renderCampaignAccountChips(sortedAccounts);

    // Add one empty message by default
    this.addCampaignMessage();

    (document.getElementById('campaign-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('open');
  },

  closeCampaignModal() {
    (document.getElementById('campaign-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('open');
    
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

  _renderCampaignAccountChips(accounts) {
    const container = document.getElementById('cmp-accounts-chips');
    if (!container) return;
    container.innerHTML = accounts.map(a => {
      const name = a.user_info
        ? [a.user_info.first_name, a.user_info.last_name].filter(Boolean).join(' ')
        : a.name;
      const phone = a.phone || '';
      const premiumBadge = a.is_premium ? '⭐ ' : '';
      const premiumStyle = a.is_premium ? 'border-color:#f59e0b;' : '';
      return `<label style="display:flex;align-items:center;gap:6px;padding:8px 12px;background:var(--bg2);border-radius:8px;cursor:pointer;border:1px solid var(--border);font-size:13px;${premiumStyle}" data-premium="${a.is_premium ? 1 : 0}">
        <input type="checkbox" class="cmp-acc-checkbox" value="${a.id}" checked>
        <span>${premiumBadge}${esc(name || phone)}</span>
      </label>`;
    }).join('');
  },

  filterCampaignAccounts(filter) {
    // Update active button
    document.querySelectorAll('.cmp-acc-filter').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.filter === filter);
      btn.style.background = btn.dataset.filter === filter ? 'var(--primary)' : '';
      btn.style.color = btn.dataset.filter === filter ? '#fff' : '';
    });
    const accounts = filter === 'premium'
      ? this._sortedCampaignAccounts.filter(a => a.is_premium)
      : this._sortedCampaignAccounts;
    this._renderCampaignAccountChips(accounts);
  },

  toggleAllCampaignAccounts(check) {
    const chips = document.getElementById('cmp-accounts-chips');
    if (!chips) return;
    chips.querySelectorAll('.cmp-acc-checkbox').forEach(cb => {
      cb.checked = check;
    });
  },

  selectPremiumOnly() {
    const chips = document.getElementById('cmp-accounts-chips');
    if (!chips) return;
    chips.querySelectorAll('label[data-premium]').forEach(label => {
      const cb = label.querySelector('.cmp-acc-checkbox');
      if (!cb) return;
      cb.checked = label.dataset.premium === '1';
    });
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
    div.style.cssText = 'display:flex;gap:8px;margin-bottom:12px;align-items:flex-start';
    div.innerHTML = `
      <span style="color:var(--text2);font-size:12px;margin-top:10px">#${idx + 1}</span>
      <div style="flex:1;display:flex;flex-direction:column;gap:6px">
        <textarea class="form-input cmp-msg-content" rows="5" style="width:100%;min-height:120px;resize:vertical" placeholder="Nội dung tin nhắn... Dùng {name} để chèn tên user" oninput="Members.updateMsgCharCounter(this)"></textarea>
        <div class="cmp-msg-char-counter" style="display:flex;align-items:center;gap:8px;font-size:11px;color:var(--text2)">
          <span class="cmp-char-count">0 ký tự</span>
          <span class="cmp-caption-warn" style="display:none;color:#f59e0b;font-weight:600">⚠️ Caption ảnh/video tối đa 1024 ký tự!</span>
        </div>
        <!-- Image attachment row -->
        <div class="cmp-msg-media-row" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <input type="file" class="cmp-msg-file-input" accept="image/*,video/*,.pdf,.doc,.docx" style="display:none" onchange="Members.handleMsgFileUpload(this)">
          <button type="button" class="btn btn-ghost btn-sm" onclick="this.parentElement.querySelector('.cmp-msg-file-input').click()" style="font-size:12px;padding:4px 10px">
            📎 Đính kèm ảnh/file
          </button>
          <div class="cmp-msg-media-preview" style="display:none;align-items:center;gap:6px;padding:4px 8px;background:var(--bg2);border-radius:6px;border:1px solid var(--border)"></div>
        </div>
        <input type="hidden" class="cmp-msg-media-path" value="">
        <input type="hidden" class="cmp-msg-media-type" value="text">
      </div>
      <button class="btn btn-danger btn-sm" onclick="this.parentElement.remove()" style="margin-top:4px">✕</button>
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
        countEl.style.color = '#ef4444';
        countEl.style.fontWeight = '600';
      } else if (hasMedia && len > 900) {
        countEl.style.color = '#f59e0b';
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
          warnEl.style.color = '#ef4444';
        } else if (len > 900) {
          warnEl.textContent = `⚠️ Còn ${1024 - len} ký tự. Caption ảnh/video tối đa 1024.`;
          warnEl.style.color = '#f59e0b';
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
    const dailyLimitPremium = parseInt(document.getElementById('cmp-daily-limit-premium')?.value) || 60;
    const dailyLimitNormal = parseInt(document.getElementById('cmp-daily-limit-normal')?.value) || 10;
    const useAi = document.getElementById('cmp-ai-remix')?.checked;
    const agentIdVal2 = document.getElementById('cmp-ai-agent')?.value;
    const aiAgentId = agentIdVal2 ? parseInt(agentIdVal2) : null;
    const excludePrev = document.getElementById('cmp-exclude-previous')?.checked ?? true;

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
        exclude_previous_dms: excludePrev,
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
      // Auto-set target timezone to system timezone if not set
      const tzSelect = document.getElementById('cmp-target-timezone');
      if (tzSelect && !tzSelect.value) {
        tzSelect.value = Intl.DateTimeFormat().resolvedOptions().timeZone;
      }
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
    }
  },

  // ── Premium Account Checkboxes (multi-select for rotation) ──
  async populatePremiumCheckboxes() {
    const container = document.getElementById('sim-premium-checkboxes');
    if (!container) return;
    if (!this._accounts.length) {
      try {
        const d = await API.getAccounts();
        this._accounts = d.accounts || [];
      } catch (e) { this._accounts = []; }
    }
    const premiums = this._accounts.filter(a => a.is_logged_in && a.is_premium);
    if (!premiums.length) {
      container.innerHTML = `<span style="color:var(--danger);font-size:13px">⚠️ Cần ít nhất 1 tài khoản Premium để sử dụng tính năng này</span>`;
      return;
    }
    container.innerHTML = premiums.map(a => {
      const ui = a.user_info;
      const name = ui ? [ui.first_name, ui.last_name].filter(Boolean).join(' ') : a.name;
      const uname = ui && ui.username ? '@' + ui.username : (a.phone || '');
      const label = name ? `${name} (${uname})` : (uname || `ID ${a.id}`);
      return `
        <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;padding:4px 8px;border-radius:6px;background:var(--bg1);border:1px solid var(--border)">
          <input type="checkbox" class="sim-premium-cb" value="${a.id}" checked>
          <span>⭐ ${esc(label)}</span>
        </label>
      `;
    }).join('');
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
      this._renderDeepCrawlResults(leads);
      App.toast(`Quét thành công! Tìm thấy ${leads.length} kênh tương tự.`, 'success');
    } catch (e) {
      App.toast(e.message, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = '🚀 Bắt đầu Deep Crawl';
    }
  },

  // ── Start Deep Crawl (depth 2-4) ──
  async startDeepCrawl(accountIds, channelLink, depth) {
    const btn = document.getElementById('sim-btn-search');
    const stopBtn = document.getElementById('sim-btn-stop');
    const progressPanel = document.getElementById('sim-progress-panel');

    btn.disabled = true;
    btn.textContent = 'Đang khởi tạo...';
    if (stopBtn) stopBtn.classList.remove('hidden');
    if (progressPanel) progressPanel.classList.remove('hidden');

    try {
      const res = await fetch('/api/members/deep-crawl', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          account_ids: accountIds,
          channel_link: channelLink,
          max_depth: depth
        })
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail || 'Không thể bắt đầu deep crawl');

      // Handle queued response
      if (d.queued) {
        App.toast(`📥 ${d.message}`, 'info');
        btn.disabled = false;
        btn.textContent = '🚀 Thêm vào Queue';
        // Refresh queue display via next poll
        return;
      }

      App.toast(d.message, 'success');
      btn.textContent = '⏳ Đang Deep Crawl...';

      // Reset backoff state
      this._deepCrawlPollInterval = 3000;
      this._deepCrawlPrevState = null;
      // Start polling progress
      this._deepCrawlPolling = true;
      this._pollDeepCrawlProgress();
    } catch (e) {
      App.toast(e.message, 'error');
      btn.disabled = false;
      btn.textContent = '🚀 Bắt đầu Deep Crawl';
      if (stopBtn) stopBtn.classList.add('hidden');
      if (progressPanel) progressPanel.classList.add('hidden');
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
    if (contactsEl) contactsEl.textContent = s.contacts_found;
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
  },

  _renderQueuePanel(queue, count) {
    let panel = document.getElementById('deep-crawl-queue-panel');
    if (count === 0 && !queue.length) {
      if (panel) panel.classList.add('hidden');
      return;
    }
    // Create panel if not exists
    if (!panel) {
      const progressPanel = document.getElementById('sim-progress-panel');
      if (!progressPanel) return;
      panel = document.createElement('div');
      panel.id = 'deep-crawl-queue-panel';
      panel.className = 'card';
      panel.style.cssText = 'margin-top:12px; padding:12px 16px; border:1px solid var(--border); border-radius:10px; background:rgba(255,255,255,0.03);';
      progressPanel.parentNode.insertBefore(panel, progressPanel.nextSibling);
    }
    panel.classList.remove('hidden');

    let html = `<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">`;
    html += `<span style="font-weight:600; color:var(--text1)">📥 Hàng đợi chờ (${count})</span>`;
    if (count > 0) {
      html += `<button onclick="Members._clearQueue()" class="btn btn-small btn-danger" style="font-size:12px; padding:2px 10px;">Xóa tất cả</button>`;
    }
    html += `</div>`;

    if (queue.length) {
      html += `<div style="display:flex; flex-direction:column; gap:6px;">`;
      queue.forEach((q, i) => {
        const link = q.channel_link.length > 35 ? q.channel_link.substring(0, 35) + '...' : q.channel_link;
        html += `<div style="display:flex; justify-content:space-between; align-items:center; padding:6px 10px; background:rgba(255,255,255,0.04); border-radius:6px; font-size:13px;">`;
        html += `<span><span style="color:var(--accent); font-weight:600;">#${i + 1}</span> ${link} <span style="color:var(--text2);">(depth ${q.max_depth})</span></span>`;
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
    } catch (e) {
      App.toast('Lỗi xóa queue', 'error');
    }
  },

  // ── Poll Progress (every 3s) ──
  _deepCrawlPolling: false,
  async _pollDeepCrawlProgress() {
    if (!this._deepCrawlPolling) return;

    try {
      const res = await fetch('/api/members/deep-crawl/status');
      if (!res.ok) {
        throw new Error(`HTTP error! status: ${res.status}`);
      }
      const s = await res.json();

      // Update progress UI using shared helper
      this._renderDeepCrawlProgressFromState(s);

      // Check if done or idle
      if (s.status === 'completed' || s.status === 'stopped' || s.status === 'error' || s.status === 'idle') {
        const barEl = document.getElementById('sim-progress-bar');
        if (barEl) barEl.style.width = s.status === 'completed' ? '100%' : '0%';

        // Fetch full results if it was a real execution
        if (s.status === 'completed' || s.status === 'stopped') {
          await this._fetchDeepCrawlResults();
        }

        if (s.status === 'completed') {
          App.toast(`Deep Crawl hoàn thành! Tìm thấy ${s.channels_found} kênh, ${s.contacts_found} contacts.`, 'success');
        } else if (s.status === 'stopped') {
          App.toast(`Deep Crawl đã dừng. Thu thập được ${s.channels_found} kênh.`, 'warning');
        }

        // If queue has items, keep polling — backend will auto-start next
        const queueCount = s.queue_count || 0;
        if (queueCount > 0 && s.status !== 'idle') {
          const btn = document.getElementById('sim-btn-search');
          if (btn) btn.textContent = `⏳ Chờ tiếp tục (${queueCount} trong queue)...`;
          this._deepCrawlPollInterval = 3000;
          setTimeout(() => this._pollDeepCrawlProgress(), 3000);
          return;
        }

        // All done — reset UI
        this._deepCrawlPolling = false;
        const btn = document.getElementById('sim-btn-search');
        const stopBtn = document.getElementById('sim-btn-stop');
        const progressPanel = document.getElementById('sim-progress-panel');
        if (btn) { btn.disabled = false; btn.textContent = '🚀 Bắt đầu Deep Crawl'; }
        if (stopBtn) stopBtn.classList.add('hidden');
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

      setTimeout(() => this._pollDeepCrawlProgress(), this._deepCrawlPollInterval);
    } catch (e) {
      // Back off on network or parsing error
      this._deepCrawlPollInterval = Math.min(30000, this._deepCrawlPollInterval * 1.5);
      setTimeout(() => this._pollDeepCrawlProgress(), this._deepCrawlPollInterval);
    }
  },

  // ── Fetch full results after deep crawl ──
  async _fetchDeepCrawlResults() {
    try {
      const res = await fetch('/api/members/deep-crawl/results');
      const d = await res.json();
      if (d.leads && d.leads.length > 0) {
        this._renderDeepCrawlResults(d.leads);
      }
    } catch (e) {
      App.toast('Lỗi tải kết quả deep crawl', 'error');
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
  _depthFilter: 'all',
  _simPage: 0,
  _simLimit: 50,
  _selectedContacts: new Set(),

  _renderDeepCrawlResults(leads) {
    const container = document.getElementById('sim-results-container');
    const tbody = document.getElementById('sim-results-tbody');
    const empty = document.getElementById('sim-empty-state');
    const filterTabs = document.getElementById('sim-depth-filter-tabs');
    const pagEl = document.getElementById('sim-pagination');
    if (!container || !tbody || !empty) return;

    // Detect fresh leads array and build select state
    if (this._similarLeads !== leads) {
      this._similarLeads = leads;
      this._simPage = 0;
      this._selectedContacts = new Set();
      leads.forEach(l => {
        if (l.contacts) {
          l.contacts.forEach(c => this._selectedContacts.add(c));
        }
      });
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

    // Build depth filter tabs
    const depths = [...new Set(leads.map(l => l.depth))].sort();
    if (filterTabs) {
      const allActive = this._depthFilter === 'all' ? 'active' : '';
      filterTabs.innerHTML = `<button class="tab-btn btn-sm ${allActive}" onclick="Members.filterByDepth('all')">Tất cả (${leads.length})</button>` +
        depths.map(d => {
          const count = leads.filter(l => l.depth === d).length;
          const active = this._depthFilter === d ? 'active' : '';
          const colors = ['', '#6366f1', '#a855f7', '#ec4899', '#f59e0b'];
          return `<button class="tab-btn btn-sm ${active}" onclick="Members.filterByDepth(${d})" style="border-left:3px solid ${colors[d] || '#6366f1'}">Lớp ${d} (${count})</button>`;
        }).join('');
    }

    // Filter leads
    const filtered = this._depthFilter === 'all' ? leads : leads.filter(l => l.depth === this._depthFilter);
    const total = filtered.length;
    const pages = Math.ceil(total / this._simLimit);
    const start = this._simPage * this._simLimit;
    const end = Math.min(start + this._simLimit, total);
    const pageItems = filtered.slice(start, end);

    // Update global "Select All" checkbox state for the filtered items
    const allFilteredContacts = [];
    filtered.forEach(l => {
      if (l.contacts) allFilteredContacts.push(...l.contacts);
    });
    const allFilteredSelected = allFilteredContacts.length > 0 && allFilteredContacts.every(c => this._selectedContacts.has(c));
    const selectAllEl = document.getElementById('sim-select-all-channels');
    if (selectAllEl) {
      selectAllEl.checked = allFilteredSelected;
    }

    tbody.innerHTML = pageItems.map((lead) => {
      const idx = leads.indexOf(lead);
      const channelDisplay = lead.username ? `@${lead.username}` : `ID: ${lead.channel_id}`;
      const description = lead.description ? lead.description : '<em style="color:var(--text-muted)">Không có mô tả</em>';

      // Row checkbox state
      const hasContacts = lead.contacts && lead.contacts.length > 0;
      const allChecked = hasContacts && lead.contacts.every(c => this._selectedContacts.has(c));
      const rowChecked = (hasContacts && allChecked) || (!hasContacts) ? 'checked' : '';

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
      const depthColors = { 1: '#6366f1', 2: '#a855f7', 3: '#ec4899', 4: '#f59e0b' };
      const depthColor = depthColors[lead.depth] || '#6366f1';

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
            <span class="badge badge-blue">${(lead.participants_count || 0).toLocaleString()}</span>
          </td>
          <td>
            <span style="display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;color:white;background:${depthColor}">L${lead.depth}</span>
          </td>
          <td style="font-size:12px;color:var(--text-muted);max-width:150px;word-break:break-word">
            ${esc(lead.parent_channel || '—')}
          </td>
          <td style="max-width:240px;word-break:break-word;font-size:12px;color:var(--text2)">
            ${esc(description)}
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

    const sourceInput = (document.getElementById('sim-channel-input')?.value || "").trim();
    const cleanName = sourceInput.replace(/[^a-zA-Z0-9]/g, '_').toLowerCase();
    (document.getElementById('sim-import-job-id') || me_dummy).value = `deep_${cleanName || 'leads'}`;
  },

  setPage(p) {
    this._simPage = p;
    this._renderDeepCrawlResults(this._similarLeads);
  },

  // ── Filter results by depth ──
  filterByDepth(depth) {
    this._depthFilter = depth;
    this._simPage = 0;
    this._renderDeepCrawlResults(this._similarLeads);
  },

  toggleSelectAllChannels(el) {
    const filtered = this._depthFilter === 'all' ? this._similarLeads : this._similarLeads.filter(l => l.depth === this._depthFilter);
    filtered.forEach(lead => {
      if (lead.contacts) {
        lead.contacts.forEach(c => {
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
    lead.contacts.forEach(c => {
      if (el.checked) {
        this._selectedContacts.add(c);
      } else {
        this._selectedContacts.delete(c);
      }
    });
    this._renderDeepCrawlResults(this._similarLeads);
  },

  onContactCheckboxChange(el, contact, leadIdx) {
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

    if (!this._selectedContacts.size) {
      App.toast('Chưa chọn contact nào để import!', 'error');
      return;
    }

    const contacts = Array.from(this._selectedContacts).map(c => ({
      username: c,
      first_name: c,
      last_name: ''
    }));

    const groupTitle = `Deep Crawl Contacts (${jobId})`;

    try {
      const res = await fetch('/api/members/import-contacts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scrape_job_id: jobId,
          group_title: groupTitle,
          contacts: contacts
        })
      });
      const d = await res.json();
      if (!res.ok) {
        throw new Error(d.detail || 'Không thể import contact');
      }

      let msg = `Imported ${d.count} contact vào job "${jobId}"`;
      if (d.skipped_dmd > 0) {
        msg += ` | Bỏ qua ${d.skipped_dmd} contact đã DM trước đó`;
      }
      App.toast(msg, 'success');
      this.loadScrapeJobs();
    } catch (e) {
      App.toast(e.message, 'error');
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
      const progress = total > 0 ? Math.round(((sent + failed + skipped) / total) * 100) : 0;
      const modeBadge = c.invite_mode === 'dm_link'
        ? '<span class="badge" style="background:#8b5cf6">💬 DM Link</span>'
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
              <div style="width:${progress}%;height:100%;background:var(--accent);transition:width .3s"></div>
            </div>
            <span style="font-size:12px;color:var(--text2)">${sent}/${total}</span>
          </div>
          <div style="font-size:11px;color:var(--text2);margin-top:2px">
            ✅${sent} ❌${failed} ⏭${skipped}
          </div>
        </td>
        <td><div class="btn-group">${actions}</div></td>
      `;

      let existingRow = tbody.querySelector(`tr[data-id="${c.id}"]`);
      if (existingRow) {
        const existingIdx = parseInt(existingRow.getAttribute('data-index'));
        if (existingIdx !== (i + 1) || existingRow.getAttribute('data-updated-at') !== c.updated_at || existingRow.getAttribute('data-status') !== c.status) {
          existingRow.innerHTML = rowHtml;
          existingRow.setAttribute('data-updated-at', c.updated_at);
          existingRow.setAttribute('data-status', c.status);
          existingRow.setAttribute('data-index', i + 1);
        }
      } else {
        const tr = document.createElement('tr');
        tr.setAttribute('data-id', c.id);
        tr.setAttribute('data-updated-at', c.updated_at);
        tr.setAttribute('data-status', c.status);
        tr.setAttribute('data-index', i + 1);
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
    if (this._inviteCampaignPollInterval) return;

    this._inviteCampaignPollInterval = setInterval(async () => {
      try {
        await this.loadInviteCampaigns();
        
        const hasRunning = this._inviteCampaigns.some(c => c.status === 'running');
        if (!hasRunning) {
          clearInterval(this._inviteCampaignPollInterval);
          this._inviteCampaignPollInterval = null;
        }
      } catch (e) {
        clearInterval(this._inviteCampaignPollInterval);
        this._inviteCampaignPollInterval = null;
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
            ? '<span class="badge" style="background:#f59e0b">⏭</span>'
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
        <button type="button" class="btn btn-ghost btn-sm" onclick="Members.selectInvitePremiumOnly()" style="font-size:11px;color:#f59e0b;padding:2px 8px" title="Chỉ chọn Premium">⭐ Chọn Premium</button>
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
      const premiumStyle = a.is_premium ? 'border-color:#f59e0b;' : '';
      const checked = selectedIds ? (selectedIds.includes(a.id) ? 'checked' : '') : 'checked';
      return `<label style="display:flex;align-items:center;gap:6px;padding:8px 12px;background:var(--bg2);border-radius:8px;cursor:pointer;border:1px solid var(--border);font-size:13px;${premiumStyle}" data-premium="${a.is_premium ? 1 : 0}">
        <input type="checkbox" class="inv-acc-checkbox" value="${a.id}" ${checked}>
        <span>${premiumBadge}${esc(name || phone)}</span>
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
      if (!['draft', 'paused', 'error'].includes(c.status)) {
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

  _spamBadgeHtml(status, message) {
    const map = {
      free: { text: '✅ Sạch', bg: 'rgba(34,197,94,.18)', border: 'rgba(34,197,94,.5)', color: '#4ade80' },
      limited: { text: '🚫 Bị giới hạn', bg: 'rgba(239,68,68,.18)', border: 'rgba(239,68,68,.5)', color: '#f87171' },
      unknown: { text: '⚠️ Không rõ', bg: 'rgba(245,158,11,.18)', border: 'rgba(245,158,11,.5)', color: '#fbbf24' },
    };
    const info = map[status] || map.unknown;
    return `<span style="background:${info.bg};border:1px solid ${info.border};border-radius:4px;padding:1px 7px;font-size:.72rem;font-weight:600;color:${info.color}" title="${esc(message || '')}">${info.text}</span>`;
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
        if (slot) slot.innerHTML = this._spamBadgeHtml(r.status, r.message);
      });

      const freeCount = results.filter(r => r.status === 'free').length;
      const limitedCount = results.filter(r => r.status === 'limited').length;
      App.toast(`Spam check: ${freeCount} sạch, ${limitedCount} bị giới hạn`, freeCount > 0 && limitedCount === 0 ? 'success' : 'warning');
    } catch (e) {
      App.toast(e.message || 'Lỗi kiểm tra spam', 'error');
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = '🛡️ Kiểm tra Spam'; }
    }
  },
};

// ── Helper: HTML escape (reuse App.esc if available, fallback) ──
if (typeof esc !== 'function') {
  function esc(s) {
    if (s == null) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
}
