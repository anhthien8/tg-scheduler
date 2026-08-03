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

    if (!this._agents || this._agents.length === 0) {
      container.innerHTML = `
        <div style="text-align:center;padding:60px 20px;color:var(--text2)">
          <div style="font-size:48px;margin-bottom:16px">🤖</div>
          <h3 style="margin-bottom:8px;color:var(--text1)">Chưa có AI Agent nào</h3>
          <p style="margin-bottom:20px">Tạo AI Agent đầu tiên để bắt đầu tự động DM outreach & reply</p>
          <button class="btn btn-primary" onclick="window.AIAgents.openForm()">➕ Tạo AI Agent</button>
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
              ${campCount > 0 ? `🟢 ${campCount} chiến dịch` : '⚪ Chưa dùng'}
            </div>
          </div>
          ${agent.description ? `<p class="ai-agent-desc">${this._esc(agent.description)}</p>` : ''}
          <div class="ai-agent-meta">
            <span title="Max replies">💬 ${agent.max_replies || 10} replies</span>
            <span title="Tone">🎭 ${agent.tone || 'friendly'}</span>
            <span title="Handover keywords">🔑 ${(agent.handover_keywords || []).length} keywords</span>
          </div>
          <div class="ai-agent-actions">
            <button class="btn btn-sm btn-outline" data-agent-action="edit" data-agent-id="${agent.id}" onclick="window.AIAgents.openForm(${agent.id})" title="Chỉnh sửa">
              ✏️ Sửa
            </button>
            <button class="btn btn-sm btn-outline" data-agent-action="test" data-agent-id="${agent.id}" onclick="window.AIAgents.testAgent(${agent.id})" title="Test AI">
              🧪 Test
            </button>
            <button class="btn btn-sm btn-outline" data-agent-action="clone" data-agent-id="${agent.id}" onclick="window.AIAgents.duplicateAgent(${agent.id})" title="Nhân bản">
              📑 Clone
            </button>
            <button class="btn btn-sm btn-outline btn-danger" data-agent-action="delete" data-agent-id="${agent.id}" onclick="window.AIAgents.deleteAgent(${agent.id})" title="Xoá">
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
    const agent = agentId ? this._agents.find(a => a.id == agentId) : null;
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
    const agent = this._agents.find(a => a.id == id);
    const agentName = agent ? agent.name : `#${id}`;
    if (!confirm(`🗑️ Xoá AI Agent "${agentName}"?\n\nAgent sẽ bị vô hiệu hoá (soft delete).`)) return;
    try {
      await AIAgentsAPI.remove(id);
      App.toast(`Đã xoá "${agentName}"`, 'success');
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

  // ── Interactive Test Modal ─────────────────────────────────────────────
  async testAgent(id) {
    console.log('[AIAgents] testAgent called for ID:', id);
    let agent = this._agents.find(a => a.id == id);
    if (!agent) {
      try {
        agent = await AIAgentsAPI.get(id);
      } catch (e) {
        console.error('Failed to get agent info:', e);
      }
    }

    if (!agent) {
      App.toast(`Không tìm thấy AI Agent #${id}`, 'error');
      return;
    }

    document.getElementById('ai-agent-test-modal')?.remove();

    const overlay = document.createElement('div');
    overlay.id = 'ai-agent-test-modal';
    overlay.className = 'modal-overlay';
    overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.7);backdrop-filter:blur(4px);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px';

    overlay.innerHTML = `
      <div style="background:var(--bg2);border-radius:16px;padding:24px;max-width:640px;width:100%;max-height:85vh;overflow-y:auto;border:1px solid var(--border);box-shadow:0 20px 40px rgba(0,0,0,0.5)">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid var(--border)">
          <h3 style="margin:0;font-size:18px;display:flex;align-items:center;gap:8px">
            <span>🧪</span> Test AI Agent: <span style="color:var(--primary)">${AIAgents._esc(agent.name)}</span>
          </h3>
          <button onclick="document.getElementById('ai-agent-test-modal').remove()" style="background:none;border:none;font-size:22px;cursor:pointer;color:var(--text2)">✕</button>
        </div>

        <div style="margin-bottom:16px">
          <label class="form-label" style="font-weight:600">📩 Tin nhắn giả lập từ khách hàng:</label>
          <textarea id="ai-test-input-text" class="form-input" rows="3" placeholder="Nhập tin nhắn test...">Chào bạn, mình đang muốn tìm hiểu về hợp tác với WEEX, cho mình xin thêm thông tin nhé!</textarea>
        </div>

        <div style="display:flex;justify-content:flex-end;gap:12px;margin-bottom:20px">
          <button class="btn btn-secondary" onclick="document.getElementById('ai-agent-test-modal').remove()">Đóng</button>
          <button class="btn btn-primary" id="btn-run-ai-test" onclick="window.AIAgents._executeTest(${agent.id})">🚀 Chạy Test</button>
        </div>

        <div id="ai-test-result-box" style="display:none;background:var(--bg3);border:1px solid var(--border);border-radius:12px;padding:16px">
          <div style="font-weight:600;margin-bottom:8px;font-size:13px;color:var(--primary)" id="ai-test-status-label">🤖 Kết quả AI trả lời:</div>
          <div id="ai-test-output-content" style="white-space:pre-wrap;font-size:14px;line-height:1.6;color:var(--text1)"></div>
        </div>
      </div>
    `;

    document.body.appendChild(overlay);
  },

  async _executeTest(agentId) {
    const textEl = document.getElementById('ai-test-input-text');
    const text = textEl?.value?.trim();
    if (!text) {
      App.toast('Vui lòng nhập tin nhắn test', 'error');
      return;
    }

    const btn = document.getElementById('btn-run-ai-test');
    const resultBox = document.getElementById('ai-test-result-box');
    const statusLabel = document.getElementById('ai-test-status-label');
    const outputContent = document.getElementById('ai-test-output-content');

    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '⏳ AI đang suy nghĩ...';
    }

    if (resultBox) {
      resultBox.style.display = 'block';
      statusLabel.textContent = '⏳ AI đang suy nghĩ...';
      outputContent.textContent = 'Đang kết nối tới AI Provider hệ thống...';
    }

    try {
      const res = await AIAgentsAPI.test(agentId, text);
      if (!res || !res.reply) {
        statusLabel.textContent = '❌ Lỗi phản hồi';
        outputContent.textContent = res?.error || 'AI Provider không trả về kết quả. Vui lòng kiểm tra lại Cài đặt AI.';
        App.toast('AI Provider không trả về kết quả', 'error');
      } else {
        statusLabel.textContent = `🤖 AI trả lời (Provider: ${res.provider || 'AI System'}, Model: ${res.model || 'Default'}):`;
        outputContent.textContent = res.reply;
        App.toast(`✅ Test hoàn tất! (Model: ${res.model || 'Default'})`, 'success');
      }
    } catch (e) {
      if (statusLabel) statusLabel.textContent = '❌ Lỗi gọi AI';
      if (outputContent) outputContent.textContent = `Lỗi: ${e.message}`;
      App.toast(`Lỗi test AI: ${e.message}`, 'error');
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = '🚀 Chạy Test lại';
      }
    }
  },

  async populateAgentDropdown(elementId = 'cmp-ai-agent', selectedId = null) {
    const el = document.getElementById(elementId);
    if (!el) return;

    if (!this._agents || this._agents.length === 0) {
      try {
        const res = await AIAgentsAPI.getAll();
        this._agents = res.agents || [];
      } catch (e) {
        console.error('Failed to load agents for dropdown:', e);
      }
    }

    let html = '<option value="">-- Dùng cài đặt AI chung hệ thống --</option>';
    for (const agent of this._agents) {
      const isSelected = selectedId && (agent.id == selectedId) ? 'selected' : '';
      html += `<option value="${agent.id}" ${isSelected}>${agent.avatar_emoji || '🤖'} ${this._esc(agent.name)}</option>`;
    }
    el.innerHTML = html;
  },

  async getAgentsForDropdown() {
    if (!this._agents || this._agents.length === 0) {
      try {
        const res = await AIAgentsAPI.getAll();
        this._agents = res.agents || [];
      } catch (e) {
        console.error('Failed to get agents for dropdown:', e);
      }
    }
    return this._agents;
  }
};

// Bind globally to window object for reliable inline event invocation
window.AIAgents = AIAgents;

// Global Event Delegation fallback for AI Agent actions
document.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-agent-action]');
  if (!btn) return;
  const action = btn.dataset.agentAction;
  const id = parseInt(btn.dataset.agentId);
  if (!id) return;

  if (action === 'test') {
    window.AIAgents.testAgent(id);
  } else if (action === 'edit') {
    window.AIAgents.openForm(id);
  } else if (action === 'clone') {
    window.AIAgents.duplicateAgent(id);
  } else if (action === 'delete') {
    window.AIAgents.deleteAgent(id);
  }
});
