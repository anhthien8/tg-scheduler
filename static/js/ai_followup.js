var me_dummy = me_dummy || {}; var me_dummy_style = me_dummy_style || {};
/**
 * static/js/ai_followup.js
 * Frontend logic for AI Follow-Up Sales Agent
 */

const AIFollowUp = {
  settings: null,

  async init() {
    await this.loadSettings();
    await this.loadChats();
  },

  async loadSettings() {
    try {
      const res = await fetch('/api/ai-followup/settings');
      if (!res.ok) throw new Error('Không thể tải cài đặt AI Follow-Up');
      this.settings = await res.json();
      this.renderSettings();
    } catch (e) {
      App.toast(e.message, 'error');
    }
  },

  renderSettings() {
    if (!this.settings) return;
    const enabledEl = document.getElementById('aifu-enabled');
    if (enabledEl) enabledEl.checked = !!this.settings.enabled;

    const promptEl = document.getElementById('aifu-sys-prompt');
    if (promptEl) promptEl.value = this.settings.system_prompt || '';

    const kbEl = document.getElementById('aifu-kb');
    if (kbEl) kbEl.value = this.settings.knowledge_base || '';

    const maxRepliesEl = document.getElementById('aifu-max-replies');
    if (maxRepliesEl) maxRepliesEl.value = this.settings.max_replies_per_user || 5;

    const handoverEl = document.getElementById('aifu-handover-kw');
    if (handoverEl) {
      const kws = this.settings.handover_keywords || [];
      handoverEl.value = kws.join(', ');
    }
  },

  async saveSettings() {
    const enabled = document.getElementById('aifu-enabled')?.checked || false;
    const system_prompt = document.getElementById('aifu-sys-prompt')?.value.trim() || '';
    const knowledge_base = document.getElementById('aifu-kb')?.value.trim() || '';
    const max_replies_per_user = parseInt(document.getElementById('aifu-max-replies')?.value || '5', 10);
    const handover_raw = document.getElementById('aifu-handover-kw')?.value || '';
    const handover_keywords = handover_raw.split(',').map(s => s.trim()).filter(Boolean);

    try {
      const res = await fetch('/api/ai-followup/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          enabled,
          system_prompt,
          knowledge_base,
          max_replies_per_user,
          handover_keywords
        })
      });
      if (!res.ok) throw new Error('Lưu cài đặt thất bại');
      const data = await res.json();
      App.toast(data.message || 'Đã lưu cài đặt AI Sales Agent!', 'success');
      this.settings = { enabled, system_prompt, knowledge_base, max_replies_per_user, handover_keywords };
    } catch (e) {
      App.toast(e.message, 'error');
    }
  },

  async loadChats() {
    const statusFilter = document.getElementById('aifu-chat-status-filter')?.value || '';
    const container = document.getElementById('aifu-chats-table-body');
    if (!container) return;

    container.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:20px;color:var(--text2)">Đang tải cuộc trò chuyện...</td></tr>';

    try {
      const url = statusFilter ? `/api/ai-followup/chats?status=${encodeURIComponent(statusFilter)}` : '/api/ai-followup/chats';
      const res = await fetch(url);
      if (!res.ok) throw new Error('Lỗi tải danh sách chat');
      const data = await res.json();
      this.renderChats(data.chats || []);
    } catch (e) {
      container.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:20px;color:var(--danger)">${e.message}</td></tr>`;
    }
  },

  renderChats(chats) {
    const container = document.getElementById('aifu-chats-table-body');
    if (!container) return;

    if (!chats || chats.length === 0) {
      container.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:20px;color:var(--text2)">Chưa có cuộc trò chuyện AI Follow-Up nào</td></tr>';
      return;
    }

    const badgeMap = {
      active: '<span class="badge badge-blue">🤖 AI Active</span>',
      needs_human: '<span class="badge badge-red" style="animation:pulse 1.5s infinite">⚠️ Cần Người Thật</span>',
      onboarded: '<span class="badge badge-green">✅ Onboarded</span>',
      paused_admin: '<span class="badge" style="background:var(--text2)">⏸ Tắt AI (Handover)</span>'
    };

    container.innerHTML = chats.map(c => {
      const uName = c.name || (c.username ? `@${c.username}` : `User #${c.user_id}`);
      const statusBadge = badgeMap[c.status] || `<span class="badge">${c.status}</span>`;
      const lastUpdated = c.updated_at ? new Date(c.updated_at).toLocaleString('vi-VN') : 'N/A';

      const jsonStr = JSON.stringify(c.history || []).replace(/'/g, "&apos;").replace(/"/g, "&quot;");

      return `
        <tr>
          <td>
            <strong>${uName}</strong>
            <div style="font-size:11px;color:var(--text2)">ID: ${c.user_id}</div>
          </td>
          <td>Acc #${c.account_id}</td>
          <td>${c.reply_count} câu</td>
          <td>${statusBadge}</td>
          <td style="font-size:12px;color:var(--text2)">${lastUpdated}</td>
          <td>
            <div style="display:flex;gap:6px">
              <button class="btn btn-sm" style="background:var(--card-hover)" onclick="AIFollowUp.openHistoryModal('${uName}', ${c.account_id}, ${c.user_id}, '${c.status}', ${jsonStr})">💬 Xem Chat</button>
              ${c.status === 'active' ? `
                <button class="btn btn-sm btn-warning" onclick="AIFollowUp.updateStatus(${c.account_id}, ${c.user_id}, 'paused_admin')">⏸ Chat Tay</button>
              ` : `
                <button class="btn btn-sm btn-primary" onclick="AIFollowUp.updateStatus(${c.account_id}, ${c.user_id}, 'active')">▶️ Bật AI</button>
              `}
              <button class="btn btn-sm btn-success" onclick="AIFollowUp.updateStatus(${c.account_id}, ${c.user_id}, 'onboarded')">✅ Onboarded</button>
            </div>
          </td>
        </tr>
      `;
    }).join('');
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
      this.loadChats();
    } catch (e) {
      App.toast(e.message, 'error');
    }
  },

  openHistoryModal(userName, accountId, userId, currentStatus, history) {
    const modal = document.getElementById('aifu-history-modal');
    if (!modal) return;

    (document.getElementById('aifu-modal-title') || me_dummy).textContent = `Lịch sử chat với ${userName} (Acc #${accountId})`;

    const historyBox = document.getElementById('aifu-modal-chat-box');
    if (!history || history.length === 0) {
      historyBox.innerHTML = '<div style="text-align:center;color:var(--text2);padding:20px">Chưa có tin nhắn nào</div>';
    } else {
      historyBox.innerHTML = history.map(msg => {
        const isUser = msg.role === 'user';
        const bg = isUser ? 'rgba(59, 130, 246, 0.15)' : 'rgba(139, 92, 246, 0.15)';
        const border = isUser ? 'rgba(59, 130, 246, 0.3)' : 'rgba(139, 92, 246, 0.3)';
        const align = isUser ? 'flex-start' : 'flex-end';
        const roleText = isUser ? `👤 ${userName}` : '🤖 AI Sales Agent';
        const timeText = msg.timestamp ? new Date(msg.timestamp).toLocaleTimeString('vi-VN') : '';

        return `
          <div style="display:flex;flex-direction:column;align-items:${align};margin-bottom:12px">
            <div style="font-size:11px;color:var(--text2);margin-bottom:3px">${roleText} • ${timeText}</div>
            <div style="background:${bg};border:1px solid ${border};padding:10px 14px;border-radius:12px;max-width:85%;white-space:pre-wrap;font-size:13px;line-height:1.4">
              ${msg.content}
            </div>
          </div>
        `;
      }).join('');
    }

    modal.classList.add('open');
  },

  closeHistoryModal() {
    const modal = document.getElementById('aifu-history-modal');
    if (modal) modal.classList.remove('open');
  }
};
