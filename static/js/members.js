/**
 * Members Module — Scraping + DM Campaign frontend logic.
 * Loaded BEFORE app.js so App can call Members.init() / Members.populateAccounts()
 */
const Members = {
  _scrapeJobs: [],
  _campaigns: [],
  _accounts: [],
  _groupsCache: {},

  // ── Init: load data when navigating to members page ──
  async init() {
    await Promise.all([
      this.loadScrapeJobs(),
      this.loadCampaigns(),
    ]);
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
    const accountId = parseInt(document.getElementById('ms-account-select').value);
    const groupSel = document.getElementById('ms-group-select');
    const groupId = parseInt(groupSel.value);
    const groupTitle = groupSel.options[groupSel.selectedIndex]?.text || '';
    const filterDays = document.getElementById('ms-filter-active').value;
    const scrapeMethod = document.getElementById('ms-scrape-method').value;
    const maxMessages = parseInt(document.getElementById('ms-max-messages').value);

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
    const method = document.getElementById('ms-scrape-method').value;
    const msgGroup = document.getElementById('ms-max-messages-group');
    if (method === 'history') {
      msgGroup.classList.remove('hidden');
    } else {
      msgGroup.classList.add('hidden');
    }
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
    document.getElementById('ms-total-jobs').textContent = jobs.length;
    document.getElementById('ms-total-members').textContent =
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
    document.getElementById('members-detail-title').textContent = `Members: ${title || jobId}`;
    const tbody = document.getElementById('members-detail-tbody');
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center">⏳ Đang tải...</td></tr>';
    document.getElementById('members-detail-modal').classList.add('open');

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
    document.getElementById('members-detail-modal').classList.remove('open');
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
      const d = await MembersAPI.getCampaigns();
      this._campaigns = d.campaigns || [];
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
    document.getElementById('ms-total-campaigns').textContent = campaigns.length;
    document.getElementById('ms-total-sent').textContent =
      campaigns.reduce((sum, c) => sum + (c.sent_count || 0), 0);

    if (!campaigns.length) {
      tbody.innerHTML = '';
      if (empty) empty.classList.remove('hidden');
      return;
    }
    if (empty) empty.classList.add('hidden');

    tbody.innerHTML = campaigns.map((c, i) => {
      const statusBadge = this._statusBadge(c.status);
      const total = c.total_targets || 0;
      const sent = c.sent_count || 0;
      const failed = c.failed_count || 0;
      const skipped = c.skipped_count || 0;
      const progress = total > 0 ? Math.round(((sent + failed + skipped) / total) * 100) : 0;

      let actions = '';
      if (c.status === 'draft' || c.status === 'paused' || c.status === 'error') {
        actions += `<button class="btn btn-primary btn-sm" onclick="Members.startCampaign(${c.id})">▶ Chạy</button>`;
      }
      if (c.status === 'running') {
        actions += `<button class="btn btn-danger btn-sm" onclick="Members.stopCampaign(${c.id})">⏸ Dừng</button>`;
      }
      actions += `<button class="btn btn-ghost btn-sm" onclick="Members.viewCampaignLogs(${c.id},'${esc(c.name)}')">📋</button>`;
      actions += `<button class="btn btn-danger btn-sm" onclick="Members.deleteCampaign(${c.id})">🗑</button>`;

      return `<tr>
        <td>${i + 1}</td>
        <td>${esc(c.name)}</td>
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
      </tr>`;
    }).join('');
  },

  _statusBadge(status) {
    const map = {
      draft: '<span class="badge" style="background:var(--text2)">📝 Draft</span>',
      running: '<span class="badge badge-blue">🔄 Running</span>',
      paused: '<span class="badge" style="background:#f59e0b">⏸ Paused</span>',
      completed: '<span class="badge badge-green">✅ Done</span>',
      error: '<span class="badge badge-red">❌ Error</span>',
    };
    return map[status] || `<span class="badge">${status}</span>`;
  },

  // ── Campaign Actions ──
  async startCampaign(id) {
    try {
      const r = await MembersAPI.startCampaign(id);
      App.toast(r.message || 'Campaign đã chạy!', 'success');
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
      this.loadCampaigns();
    } catch (e) {
      App.toast(e.message, 'error');
    }
  },

  _pollCampaign(id) {
    const poll = setInterval(async () => {
      try {
        const d = await MembersAPI.getCampaign(id);
        const c = d.campaign;
        if (!c || c.status !== 'running') {
          clearInterval(poll);
          this.loadCampaigns();
          return;
        }
        this.loadCampaigns();
      } catch (e) {
        clearInterval(poll);
      }
    }, 10000); // Poll every 10s
  },

  // ── Campaign Logs ──
  async viewCampaignLogs(id, name) {
    document.getElementById('campaign-logs-title').textContent = `Logs: ${name}`;
    const tbody = document.getElementById('campaign-logs-tbody');
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center">⏳ Đang tải...</td></tr>';
    document.getElementById('campaign-logs-modal').classList.add('open');

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
          <td>${l.account_id || '—'}</td>
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
    document.getElementById('campaign-logs-modal').classList.remove('open');
  },

  // ═══════════════════════════════════════════════════════════════════════════
  // CAMPAIGN MODAL
  // ═══════════════════════════════════════════════════════════════════════════

  async openCampaignModal() {
    document.getElementById('campaign-modal-title').textContent = 'Tạo DM Campaign';
    document.getElementById('cmp-name').value = '';
    document.getElementById('cmp-delay-min').value = '30';
    document.getElementById('cmp-delay-max').value = '90';
    document.getElementById('cmp-daily-limit').value = '30';
    document.getElementById('cmp-ai-remix').checked = false;
    document.getElementById('cmp-messages-list').innerHTML = '';

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
    try {
      const d = await API.getAccounts();
      this._accounts = d.accounts || [];
    } catch (e) {}
    const accDiv = document.getElementById('cmp-accounts-list');
    accDiv.innerHTML = this._accounts.map(a => {
      const name = a.user_info
        ? [a.user_info.first_name, a.user_info.last_name].filter(Boolean).join(' ')
        : a.name;
      const phone = a.phone || '';
      return `<label style="display:flex;align-items:center;gap:6px;padding:8px 12px;background:var(--bg2);border-radius:8px;cursor:pointer;border:1px solid var(--border);font-size:13px">
        <input type="checkbox" class="cmp-acc-checkbox" value="${a.id}" checked>
        <span>${esc(name || phone)}</span>
      </label>`;
    }).join('');

    // Add one empty message by default
    this.addCampaignMessage();

    document.getElementById('campaign-modal').classList.add('open');
  },

  closeCampaignModal() {
    document.getElementById('campaign-modal').classList.remove('open');
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
        <textarea class="form-input cmp-msg-content" rows="3" style="width:100%" placeholder="Nội dung tin nhắn... Dùng {name} để chèn tên user"></textarea>
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
  },

  async saveCampaign() {
    const name = document.getElementById('cmp-name').value.trim();
    const jobId = document.getElementById('cmp-scrape-job').value;
    const delayMin = parseInt(document.getElementById('cmp-delay-min').value) || 30;
    const delayMax = parseInt(document.getElementById('cmp-delay-max').value) || 90;
    const dailyLimit = parseInt(document.getElementById('cmp-daily-limit').value) || 30;
    const useAi = document.getElementById('cmp-ai-remix').checked;

    if (!name) { App.toast('Nhập tên campaign', 'error'); return; }
    if (!jobId) { App.toast('Chọn nguồn members', 'error'); return; }

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
        daily_limit: dailyLimit,
        use_ai_remix: useAi,
      });
      App.toast(`Campaign tạo thành công! (${r.total_targets} targets)`, 'success');
      this.closeCampaignModal();
      this.loadCampaigns();
    } catch (e) {
      App.toast(e.message, 'error');
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

      App.toast(d.message, 'success');
      btn.textContent = '⏳ Đang Deep Crawl...';

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

  // ── Poll Progress (every 3s) ──
  _deepCrawlPolling: false,
  async _pollDeepCrawlProgress() {
    if (!this._deepCrawlPolling) return;

    try {
      const res = await fetch('/api/members/deep-crawl/status');
      const s = await res.json();

      // Update progress UI
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

      // Progress bar: estimate based on processed vs total queue
      const totalWork = s.channels_processed + s.queue_remaining;
      const pct = totalWork > 0 ? Math.min(95, (s.channels_processed / totalWork) * 100) : 0;
      if (barEl) barEl.style.width = `${pct}%`;

      // Status badge
      if (statusEl) {
        const statusMap = {
          running: { text: 'Running', bg: 'var(--accent)' },
          completed: { text: 'Hoàn thành ✓', bg: 'var(--success)' },
          stopped: { text: 'Đã dừng', bg: 'var(--warning)' },
          error: { text: 'Lỗi', bg: 'var(--danger)' },
        };
        const info = statusMap[s.status] || statusMap.running;
        statusEl.textContent = info.text;
        statusEl.style.background = info.bg;
      }

      // Errors log
      if (errorsEl && s.errors && s.errors.length > 0) {
        errorsEl.classList.remove('hidden');
        errorsEl.innerHTML = s.errors.slice(-10).map(e => `<div>⚠️ ${esc(e)}</div>`).join('');
      }

      // Check if done
      if (s.status === 'completed' || s.status === 'stopped' || s.status === 'error') {
        this._deepCrawlPolling = false;
        if (barEl) barEl.style.width = '100%';

        // Fetch full results
        await this._fetchDeepCrawlResults();

        const btn = document.getElementById('sim-btn-search');
        const stopBtn = document.getElementById('sim-btn-stop');
        if (btn) { btn.disabled = false; btn.textContent = '🚀 Bắt đầu Deep Crawl'; }
        if (stopBtn) stopBtn.classList.add('hidden');

        if (s.status === 'completed') {
          App.toast(`Deep Crawl hoàn thành! Tìm thấy ${s.channels_found} kênh, ${s.contacts_found} contacts.`, 'success');
        } else if (s.status === 'stopped') {
          App.toast(`Deep Crawl đã dừng. Thu thập được ${s.channels_found} kênh.`, 'warning');
        }
        return;
      }

      // Continue polling
      setTimeout(() => this._pollDeepCrawlProgress(), 3000);
    } catch (e) {
      // Retry on network error
      setTimeout(() => this._pollDeepCrawlProgress(), 5000);
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

    const sourceInput = document.getElementById('sim-channel-input').value.trim();
    const cleanName = sourceInput.replace(/[^a-zA-Z0-9]/g, '_').toLowerCase();
    document.getElementById('sim-import-job-id').value = `deep_${cleanName || 'leads'}`;
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

      App.toast(`Đã import thành công ${d.count} contact vào job "${jobId}"!`, 'success');
      this.loadScrapeJobs();
    } catch (e) {
      App.toast(e.message, 'error');
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
