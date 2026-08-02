/**
 * AI Agents Management Module
 * Manages multiple AI BD agents with custom prompts and knowledge bases.
 * AI Provider and API Keys are managed globally in system AI Settings.
 */
const AIAgents = {
  _agents: [],
  _editingId: null,

  async init() {
    await this.load();
  },

  async load() {
    try {
      const res = await AIAgentsAPI.getAll();
      this._agents = res.agents || [];
      this.render();
    } catch (e) {
      console.error('AIAgents load error:', e);
      App.toast('Lỗi tải danh sách AI Agents', 'error');
    }
  },

  render() {
    const container = document.getElementById('ai-agents-list');
    if (!container) return;

    if (this._agents.length === 0) {
      container.innerHTML = `
        <div style="text-align:center;padding:60px 20px;color:var(--text2)">
          <div style="font-size:48px;margin-bottom:16px">🤖</div>
          <h3 style="margin-bottom:8px;color:var(--text1)">Chưa có AI Agent nào</h3>
          <p style="margin-bottom:20px">Tạo AI Agent đầu tiên để bắt đầu tự động DM outreach & reply</p>
          <button class="btn btn-primary" onclick="AIAgents.openForm()">➕ Tạo AI Agent</button>
        </div>`;
      return;
    }

    let html = '<div class="ai-agents-grid">';
    for (const agent of this._agents) {
      const emoji = agent.avatar_emoji || '🤖';
      const campCount = agent.campaign_count || 0;

      html += `
        <div class="ai-agent-card" data-id="${agent.id}">
          <div class="ai-agent-card-header">
            <div class="ai-agent-avatar">${emoji}</div>
            <div class="ai-agent-info">
              <h3 class="ai-agent-name">${this._esc(agent.name)}</h3>
              <span class="ai-agent-provider">🤖 Custom Agent</span>
            </div>
            <div class="ai-agent-status ${campCount > 0 ? 'active' : ''}">
              ${campCount > 0 ? `${campCount} chiến dịch` : 'Chưa dùng'}
            </div>
          </div>
          ${agent.description ? `<p class="ai-agent-desc">${this._esc(agent.description)}</p>` : ''}
          <div class="ai-agent-meta">
            <span title="Max replies">💬 ${agent.max_replies || 10} replies</span>
            <span title="Tone">🎭 ${agent.tone || 'friendly'}</span>
            <span title="Handover keywords">🔑 ${(agent.handover_keywords || []).length} keywords</span>
          </div>
          <div class="ai-agent-actions">
            <button class="btn btn-sm btn-outline" onclick="AIAgents.openForm(${agent.id})" title="Chỉnh sửa">
              ✏️ Sửa
            </button>
            <button class="btn btn-sm btn-outline" onclick="AIAgents.testAgent(${agent.id})" title="Test AI">
              🧪 Test
            </button>
            <button class="btn btn-sm btn-outline" onclick="AIAgents.duplicateAgent(${agent.id})" title="Nhân bản">
              📑 Clone
            </button>
            <button class="btn btn-sm btn-outline btn-danger" onclick="AIAgents.deleteAgent(${agent.id})" title="Xoá">
              🗑️
            </button>
          </div>
        </div>`;
    }
    html += '</div>';
    container.innerHTML = html;
  },

  _esc(str) {
    if (!str) return '';
    const el = document.createElement('span');
    el.textContent = str;
    return el.innerHTML;
  },

  // ── Form: Create / Edit ──────────────────────────────────────────────
  openForm(agentId = null) {
    this._editingId = agentId;
    const agent = agentId ? this._agents.find(a => a.id === agentId) : null;
    const isEdit = !!agent;
    const title = isEdit ? `✏️ Sửa "${agent.name}"` : '➕ Tạo AI Agent mới';

    const modal = document.getElementById('ai-agent-modal');
    if (!modal) return;

    const titleEl = document.getElementById('ai-agent-modal-title');
    if (titleEl) titleEl.textContent = title;

    const setVal = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.value = val;
    };

    setVal('agent-name', agent?.name || '');
    setVal('agent-description', agent?.description || '');
    setVal('agent-emoji', agent?.avatar_emoji || '🤖');
    setVal('agent-system-prompt', agent?.system_prompt || '');
    setVal('agent-remix-instruction', agent?.remix_instruction || '');
    setVal('agent-knowledge-base', agent?.knowledge_base || '');
    setVal('agent-handover-keywords', (agent?.handover_keywords || []).join(', '));
    setVal('agent-max-replies', agent?.max_replies || 10);
    setVal('agent-tone', agent?.tone || 'friendly');

    modal.classList.remove('hidden');
  },

  closeForm() {
    const modal = document.getElementById('ai-agent-modal');
    if (modal) modal.classList.add('hidden');
    this._editingId = null;
  },

  async saveAgent() {
    const getVal = (id) => document.getElementById(id)?.value?.trim() || '';

    const name = getVal('agent-name');
    if (!name) {
      App.toast('Tên agent không được để trống', 'error');
      return;
    }

    const handoverRaw = getVal('agent-handover-keywords');
    const handoverKws = handoverRaw ? handoverRaw.split(',').map(k => k.trim()).filter(k => k) : [];

    const data = {
      name,
      description: getVal('agent-description'),
      avatar_emoji: getVal('agent-emoji') || '🤖',
      system_prompt: document.getElementById('agent-system-prompt')?.value || '',
      remix_instruction: document.getElementById('agent-remix-instruction')?.value || '',
      knowledge_base: document.getElementById('agent-knowledge-base')?.value || '',
      handover_keywords: handoverKws,
      max_replies: parseInt(getVal('agent-max-replies')) || 10,
      tone: getVal('agent-tone') || 'friendly',
    };

    try {
      if (this._editingId) {
        await AIAgentsAPI.update(this._editingId, data);
        App.toast(`✅ Đã cập nhật "${name}"`, 'success');
      } else {
        await AIAgentsAPI.create(data);
        App.toast(`✅ Đã tạo AI Agent "${name}"`, 'success');
      }
      this.closeForm();
      await this.load();
    } catch (e) {
      App.toast(`Lỗi: ${e.message}`, 'error');
    }
  },

  // ── Actions ──────────────────────────────────────────────────────────
  async deleteAgent(id) {
    const agent = this._agents.find(a => a.id === id);
    if (!agent) return;
    if (!confirm(`🗑️ Xoá AI Agent "${agent.name}"?

Agent sẽ bị vô hiệu hoá (soft delete).`)) return;
    try {
      await AIAgentsAPI.remove(id);
      App.toast(`Đã xoá "${agent.name}"`, 'success');
      await this.load();
    } catch (e) {
      App.toast(`Lỗi: ${e.message}`, 'error');
    }
  },

  async duplicateAgent(id) {
    try {
      const res = await AIAgentsAPI.duplicate(id);
      App.toast(`📑 Đã nhân bản! Agent mới #${res.id}`, 'success');
      await this.load();
    } catch (e) {
      App.toast(`Lỗi: ${e.message}`, 'error');
    }
  },

  async testAgent(id) {
    const agent = this._agents.find(a => a.id === id);
    if (!agent) return;

    const testText = prompt(
      `🧪 Test AI Agent "${agent.name}"

Nhập tin nhắn test (giả lập user gửi DM):`,
      'Chào bạn, mình đang tìm hiểu về sản phẩm. Cho mình biết thêm thông tin được không?'
    );
    if (!testText) return;

    App.toast('⏳ Đang gọi AI test...', 'info');
    try {
      const res = await AIAgentsAPI.test(id, testText);
      if (!res || !res.reply) {
        App.toast('⚠️ AI không trả về kết quả. Kiểm tra lại API Key trong Cài đặt AI!', 'error');
        return;
      }
      const resultHtml = `
        <div style="text-align:left">
          <div style="margin-bottom:12px">
            <strong>📩 User gửi:</strong><br>
            <div style="background:var(--bg3);padding:10px;border-radius:8px;margin-top:4px">${this._esc(testText)}</div>
          </div>
          <div>
            <strong>🤖 AI trả lời (${res.provider || 'AI System'}):</strong><br>
            <div style="background:var(--bg3);padding:10px;border-radius:8px;margin-top:4px;white-space:pre-wrap">${this._esc(res.reply)}</div>
          </div>
        </div>`;

      const overlay = document.createElement('div');
      overlay.className = 'modal-overlay';
      overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.6);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px';
      overlay.innerHTML = `
        <div style="background:var(--bg2);border-radius:16px;padding:24px;max-width:600px;width:100%;max-height:80vh;overflow-y:auto;border:1px solid var(--border)">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
            <h3 style="margin:0">🧪 Kết quả Test - ${this._esc(agent.name)}</h3>
            <button onclick="this.closest('.modal-overlay').remove()" style="background:none;border:none;font-size:20px;cursor:pointer;color:var(--text2)">✕</button>
          </div>
          ${resultHtml}
        </div>`;
      document.body.appendChild(overlay);
    } catch (e) {
      App.toast(`Lỗi test AI: ${e.message}`, 'error');
    }
  }
};
