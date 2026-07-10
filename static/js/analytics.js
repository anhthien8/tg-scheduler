/**
 * Analytics, Templates, Auto-Reply, CSV Export — Growth Features Module
 */
const Analytics = {
  _overview: null,
  _daily: [],
  _health: [],
  _campaigns: [],

  // ── Analytics Dashboard ─────────────────────────────────────────────
  async init() {
    try {
      const [ov, daily, health, camps] = await Promise.all([
        AnalyticsAPI.overview(),
        AnalyticsAPI.dailyStats(30),
        AnalyticsAPI.accountHealth(),
        AnalyticsAPI.campaignPerformance(),
      ]);
      this._overview = ov;
      this._daily = daily;
      this._health = health;
      this._campaigns = camps;
      this.renderOverview();
      this.renderChart();
      this.renderHealth();
      this.renderCampaigns();
    } catch (e) {
      console.error('Analytics load error:', e);
    }
  },

  renderOverview() {
    const o = this._overview;
    if (!o) return;
    const rate = o.response_rate?.toFixed(1) || '0.0';
    document.getElementById('an-total-sent').textContent = o.total_dm_sent || 0;
    document.getElementById('an-total-replies').textContent = o.total_replies || 0;
    document.getElementById('an-response-rate').textContent = rate + '%';
    document.getElementById('an-total-contacts').textContent = o.total_contacts || 0;
    document.getElementById('an-active-campaigns').textContent = o.active_campaigns || 0;
    document.getElementById('an-total-reactions').textContent = o.total_reactions || 0;
  },

  renderChart() {
    const canvas = document.getElementById('an-chart');
    if (!canvas || !this._daily.length) return;
    const ctx = canvas.getContext('2d');
    const data = this._daily.slice(-30);
    const W = canvas.width = canvas.parentElement.clientWidth;
    const H = canvas.height = 220;
    const pad = { top: 20, right: 20, bottom: 40, left: 50 };
    const chartW = W - pad.left - pad.right;
    const chartH = H - pad.top - pad.bottom;
    ctx.clearRect(0, 0, W, H);

    const maxVal = Math.max(1, ...data.map(d => Math.max(d.sent || 0, d.failed || 0, d.replies || 0)));
    const xStep = data.length > 1 ? chartW / (data.length - 1) : chartW;

    const drawLine = (key, color) => {
      ctx.beginPath();
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      data.forEach((d, i) => {
        const x = pad.left + i * xStep;
        const y = pad.top + chartH - (d[key] || 0) / maxVal * chartH;
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      });
      ctx.stroke();
      // Area fill
      ctx.lineTo(pad.left + (data.length - 1) * xStep, pad.top + chartH);
      ctx.lineTo(pad.left, pad.top + chartH);
      ctx.closePath();
      ctx.fillStyle = color.replace('1)', '0.08)');
      ctx.fill();
    };

    // Grid lines
    ctx.strokeStyle = 'rgba(255,255,255,0.06)';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = pad.top + chartH * i / 4;
      ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(W - pad.right, y); ctx.stroke();
      ctx.fillStyle = 'rgba(255,255,255,0.4)';
      ctx.font = '10px Inter,sans-serif';
      ctx.textAlign = 'right';
      ctx.fillText(Math.round(maxVal * (4 - i) / 4), pad.left - 8, y + 4);
    }

    drawLine('sent', 'rgba(99,102,241,1)');
    drawLine('replies', 'rgba(34,197,94,1)');
    drawLine('failed', 'rgba(239,68,68,1)');

    // X labels
    ctx.fillStyle = 'rgba(255,255,255,0.4)';
    ctx.font = '9px Inter,sans-serif';
    ctx.textAlign = 'center';
    const labelStep = Math.max(1, Math.floor(data.length / 7));
    data.forEach((d, i) => {
      if (i % labelStep === 0) {
        const x = pad.left + i * xStep;
        ctx.fillText(d.date?.slice(5) || '', x, H - 8);
      }
    });

    // Legend
    const legends = [
      { label: 'Sent', color: '#6366f1' },
      { label: 'Replies', color: '#22c55e' },
      { label: 'Failed', color: '#ef4444' },
    ];
    let lx = pad.left;
    legends.forEach(l => {
      ctx.fillStyle = l.color;
      ctx.fillRect(lx, H - 26, 10, 3);
      ctx.fillStyle = 'rgba(255,255,255,0.6)';
      ctx.font = '10px Inter,sans-serif';
      ctx.textAlign = 'left';
      ctx.fillText(l.label, lx + 14, H - 22);
      lx += ctx.measureText(l.label).width + 30;
    });
  },

  renderHealth() {
    const el = document.getElementById('an-health-list');
    if (!el) return;
    if (!this._health.length) {
      el.innerHTML = '<div class="empty-state"><div class="empty-state-icon">🏥</div><div class="empty-state-text">Chưa có dữ liệu</div></div>';
      return;
    }
    el.innerHTML = this._health.map(a => {
      const score = a.health_score || 0;
      const color = score >= 80 ? 'var(--green)' : score >= 50 ? 'var(--orange)' : 'var(--red)';
      const bar = `<div style="height:3px;border-radius:1.5px;background:var(--bg3);margin-top:4px"><div style="height:100%;width:${score}%;border-radius:1.5px;background:${color}"></div></div>`;
      return `<div class="card" style="padding:10px;margin-bottom:0">
        <div style="display:flex;justify-content:space-between;align-items:center;font-size:13px">
          <strong style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:150px">${a.account_name || 'Account #' + a.account_id}</strong>
          <span class="badge ${score >= 80 ? 'badge-green' : score >= 50 ? 'badge-blue' : 'badge-red'}" style="padding:2px 6px;font-size:10px">${score}/100</span>
        </div>
        ${bar}
        <div style="display:flex;gap:12px;margin-top:6px;font-size:11px;color:var(--text2);flex-wrap:wrap">
          <span>📤 ${a.dm_sent_today || 0}</span>
          <span>✅ ${(a.success_rate || 0).toFixed(0)}%</span>
          <span>⚠️ ${a.flood_count || 0}</span>
          ${a.is_flagged ? '<span style="color:var(--red)" title="Flagged">🚩</span>' : ''}
        </div>
      </div>`;
    }).join('');
  },

  renderCampaigns() {
    const el = document.getElementById('an-campaign-list');
    if (!el) return;
    if (!this._campaigns.length) {
      el.innerHTML = '<div class="empty-state"><div class="empty-state-icon">📋</div><div class="empty-state-text">Chưa có campaign</div></div>';
      return;
    }
    el.innerHTML = `<div class="table-wrap"><table>
      <thead><tr><th>Campaign</th><th>Status</th><th>Sent</th><th>Failed</th><th>Replies</th><th>Rate</th><th>Export</th></tr></thead>
      <tbody>${this._campaigns.map(c => {
        const rate = (c.success_rate || 0).toFixed(1);
        const badge = c.status === 'running' ? 'badge-green' : c.status === 'completed' ? 'badge-blue' : 'badge-gray';
        return `<tr>
          <td><strong>${c.name || ''}</strong></td>
          <td><span class="badge ${badge}">${c.status}</span></td>
          <td>${c.sent_count || 0}</td>
          <td>${c.failed_count || 0}</td>
          <td>${c.reply_count || 0}</td>
          <td>${rate}%</td>
          <td><button class="btn btn-ghost btn-sm" onclick="Analytics.exportCampaignLogs(${c.id})">📥 CSV</button></td>
        </tr>`;
      }).join('')}</tbody>
    </table></div>`;
  },

  exportCampaignLogs(campId) {
    window.open(AnalyticsAPI.exportCampaignLogs(campId), '_blank');
  },

  exportAllContacts() {
    window.open(AnalyticsAPI.exportContacts(), '_blank');
  },
};


// ── Template Library ──────────────────────────────────────────────────────────
const Templates = {
  _list: [],
  _editing: null,

  async init() {
    try {
      this._list = await TemplatesAPI.getAll();
      this.render();
    } catch (e) { console.error('Templates load error:', e); }
  },

  render() {
    const el = document.getElementById('tpl-list');
    if (!el) return;
    if (!this._list.length) {
      el.innerHTML = '<div class="empty-state"><div class="empty-state-icon">📋</div><div class="empty-state-text">Chưa có template</div></div>';
      return;
    }
    const cats = { crypto: '🪙', finance: '💹', marketing: '📣', business: '💼', general: '📝' };
    el.innerHTML = `<div class="stats-grid">${this._list.map(t => {
      const msgs = typeof t.messages === 'string' ? JSON.parse(t.messages) : (t.messages || []);
      const icon = cats[t.category] || '📝';
      return `<div class="card" style="padding:16px;cursor:pointer" onclick="Templates.use(${t.id})">
        <div style="display:flex;justify-content:space-between;align-items:start">
          <div><span style="font-size:20px">${icon}</span> <strong>${t.name}</strong></div>
          <div class="btn-group">
            ${t.is_default ? '<span class="badge badge-purple">Default</span>' : `<button class="btn btn-ghost btn-sm" onclick="event.stopPropagation();Templates.remove(${t.id})" title="Xoá">🗑</button>`}
          </div>
        </div>
        <div style="color:var(--text2);font-size:12px;margin-top:6px">${t.category} · ${msgs.length} message${msgs.length > 1 ? 's' : ''}</div>
        <div style="margin-top:8px;font-size:12px;color:var(--text3);max-height:60px;overflow:hidden">${msgs[0]?.content?.slice(0, 120) || '(empty)'}</div>
      </div>`;
    }).join('')}</div>`;
  },

  async use(id) {
    const t = this._list.find(x => x.id === id);
    if (!t) return;
    const msgs = typeof t.messages === 'string' ? JSON.parse(t.messages) : (t.messages || []);
    // Copy to clipboard
    const text = msgs.map(m => m.content).join('\n---\n');
    try {
      await navigator.clipboard.writeText(text);
      App.showToast('Đã copy template vào clipboard!', 'success');
    } catch {
      App.showToast('Không thể copy. Hãy dùng template thủ công.', 'error');
    }
  },

  openCreate() {
    this._editing = null;
    document.getElementById('tpl-modal-title').textContent = 'Tạo Template Mới';
    document.getElementById('tpl-name').value = '';
    document.getElementById('tpl-category').value = 'general';
    document.getElementById('tpl-content').value = '';
    document.getElementById('tpl-modal').classList.add('open');
  },

  async save() {
    const name = document.getElementById('tpl-name').value.trim();
    const category = document.getElementById('tpl-category').value;
    const content = document.getElementById('tpl-content').value.trim();
    if (!name || !content) { App.showToast('Nhập tên và nội dung', 'error'); return; }
    const messages = [{ msg_type: 'text', content }];
    const data = { name, category, messages };
    try {
      if (this._editing) {
        await TemplatesAPI.update(this._editing, data);
        App.showToast('Template đã cập nhật!', 'success');
      } else {
        await TemplatesAPI.create(data);
        App.showToast('Template đã tạo!', 'success');
      }
      document.getElementById('tpl-modal').classList.remove('open');
      this.init();
    } catch (e) { App.showToast(e.message, 'error'); }
  },

  async remove(id) {
    if (!await customConfirm('Xoá template này?')) return;
    try {
      await TemplatesAPI.remove(id);
      App.showToast('Đã xoá template', 'success');
      this.init();
    } catch (e) { App.showToast(e.message, 'error'); }
  },
};


// ── Auto-Reply Chatbot ────────────────────────────────────────────────────────
const AutoReply = {
  _rules: [],
  _editing: null,

  async init() {
    try {
      this._rules = await AutoReplyAPI.getRules();
      this.render();
    } catch (e) { console.error('AutoReply load error:', e); }
  },

  render() {
    const el = document.getElementById('ar-rules-list');
    if (!el) return;
    if (!this._rules.length) {
      el.innerHTML = '<div class="empty-state"><div class="empty-state-icon">🤖</div><div class="empty-state-text">Chưa có auto-reply rule</div></div>';
      return;
    }
    el.innerHTML = this._rules.map(r => {
      const keywords = typeof r.trigger_keywords === 'string' ? JSON.parse(r.trigger_keywords) : (r.trigger_keywords || []);
      const msgs = typeof r.reply_messages === 'string' ? JSON.parse(r.reply_messages) : (r.reply_messages || []);
      const active = r.is_active;
      return `<div class="card" style="padding:14px;border-left:3px solid ${active ? 'var(--green)' : 'var(--text3)'}">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <div>
            <strong>${r.name}</strong>
            <span class="badge ${active ? 'badge-green' : 'badge-gray'}" style="margin-left:8px">${active ? 'Active' : 'Off'}</span>
          </div>
          <div class="btn-group">
            <button class="btn btn-ghost btn-sm" onclick="AutoReply.toggle(${r.id})">${active ? '⏸' : '▶'}</button>
            <button class="btn btn-ghost btn-sm" onclick="AutoReply.edit(${r.id})">✏️</button>
            <button class="btn btn-ghost btn-sm" onclick="AutoReply.remove(${r.id})">🗑</button>
            <button class="btn btn-ghost btn-sm" onclick="AutoReply.viewLogs(${r.id})">📋</button>
          </div>
        </div>
        <div style="display:flex;gap:16px;margin-top:8px;font-size:12px;color:var(--text2)">
          <span>Trigger: <strong>${r.trigger_type}</strong></span>
          ${keywords.length ? `<span>Keywords: ${keywords.slice(0, 3).join(', ')}${keywords.length > 3 ? '...' : ''}</span>` : ''}
          <span>${msgs.length} reply msg${msgs.length > 1 ? 's' : ''}</span>
          <span>Max ${r.max_replies_per_user}/user</span>
        </div>
      </div>`;
    }).join('');
  },

  openCreate() {
    this._editing = null;
    document.getElementById('ar-modal-title').textContent = 'Tạo Auto-Reply Rule';
    document.getElementById('ar-name').value = '';
    document.getElementById('ar-trigger-type').value = 'keyword';
    document.getElementById('ar-keywords').value = '';
    document.getElementById('ar-reply-content').value = '';
    document.getElementById('ar-max-replies').value = '3';
    document.getElementById('ar-modal').classList.add('open');
  },

  edit(id) {
    const r = this._rules.find(x => x.id === id);
    if (!r) return;
    this._editing = id;
    const keywords = typeof r.trigger_keywords === 'string' ? JSON.parse(r.trigger_keywords) : (r.trigger_keywords || []);
    const msgs = typeof r.reply_messages === 'string' ? JSON.parse(r.reply_messages) : (r.reply_messages || []);
    document.getElementById('ar-modal-title').textContent = 'Sửa Auto-Reply Rule';
    document.getElementById('ar-name').value = r.name;
    document.getElementById('ar-trigger-type').value = r.trigger_type || 'keyword';
    document.getElementById('ar-keywords').value = keywords.join(', ');
    document.getElementById('ar-reply-content').value = msgs.map(m => m.content).join('\n---\n');
    document.getElementById('ar-max-replies').value = r.max_replies_per_user || 3;
    document.getElementById('ar-modal').classList.add('open');
  },

  async save() {
    const name = document.getElementById('ar-name').value.trim();
    const triggerType = document.getElementById('ar-trigger-type').value;
    const keywordsStr = document.getElementById('ar-keywords').value.trim();
    const replyContent = document.getElementById('ar-reply-content').value.trim();
    const maxReplies = parseInt(document.getElementById('ar-max-replies').value) || 3;
    if (!name) { App.showToast('Nhập tên rule', 'error'); return; }
    if (!replyContent) { App.showToast('Nhập nội dung reply', 'error'); return; }

    const keywords = keywordsStr ? keywordsStr.split(',').map(k => k.trim()).filter(Boolean) : [];
    const replies = replyContent.split('\n---\n').map(c => ({ msg_type: 'text', content: c.trim(), delay_seconds: 0 }));

    const data = {
      name,
      trigger_type: triggerType,
      trigger_keywords: keywords,
      reply_messages: replies,
      account_ids: [],
      max_replies_per_user: maxReplies,
    };
    try {
      if (this._editing) {
        await AutoReplyAPI.updateRule(this._editing, data);
        App.showToast('Rule đã cập nhật!', 'success');
      } else {
        await AutoReplyAPI.createRule(data);
        App.showToast('Rule đã tạo!', 'success');
      }
      document.getElementById('ar-modal').classList.remove('open');
      this.init();
    } catch (e) { App.showToast(e.message, 'error'); }
  },

  async toggle(id) {
    try {
      await AutoReplyAPI.toggleRule(id);
      this.init();
    } catch (e) { App.showToast(e.message, 'error'); }
  },

  async remove(id) {
    if (!await customConfirm('Xoá rule này?')) return;
    try {
      await AutoReplyAPI.deleteRule(id);
      App.showToast('Đã xoá rule', 'success');
      this.init();
    } catch (e) { App.showToast(e.message, 'error'); }
  },

  async viewLogs(ruleId) {
    try {
      const logs = await AutoReplyAPI.getLogs(ruleId);
      const el = document.getElementById('ar-logs-body');
      if (!el) return;
      el.innerHTML = logs.length ? logs.map(l => `<tr>
        <td>${l.username || l.user_id}</td>
        <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis">${l.trigger_text || ''}</td>
        <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis">${l.reply_text || ''}</td>
        <td><span class="badge ${l.status === 'success' ? 'badge-green' : 'badge-red'}">${l.status}</span></td>
        <td>${l.sent_at?.slice(0, 16) || ''}</td>
      </tr>`).join('') : '<tr><td colspan="5" style="text-align:center;color:var(--text2)">Chưa có log</td></tr>';
      document.getElementById('ar-logs-modal').classList.add('open');
    } catch (e) { App.showToast(e.message, 'error'); }
  },
};
