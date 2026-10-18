/**
 * engagement.js — Community Engagement Engine UI
 * Tab: "Cộng Đồng" — Campaign management, logs, health panel
 */
const Engagement = {
    campaigns: [],
    currentCampaignId: null,
    agents: [],

    async init() {
        await this.loadAgents();
        await this.loadCampaigns();
        this.renderCampaigns();
        this.renderForm();
        this.loadLogs();
        this.loadStats();
    },

    async loadAgents() {
        try {
            const res = await API.get('/api/ai-agents');
            this.agents = res.agents || [];
        } catch (e) {
            this.agents = [];
        }
    },

    async loadCampaigns() {
        try {
            const res = await API.get('/api/engagement/campaigns');
            this.campaigns = res.campaigns || [];
        } catch (e) {
            this.campaigns = [];
        }
    },

    renderCampaigns() {
        const container = document.getElementById('engagement-campaign-list');
        if (!container) return;

        if (!this.campaigns.length) {
            container.innerHTML = '<div class="empty-state">Chưa có chiến dịch nào. Tạo mới bên dưới.</div>';
            return;
        }

        const statusBadge = (s) => {
            const map = {
                'active': '<span class="badge badge-success">Đang chạy</span>',
                'draft': '<span class="badge badge-secondary">Nháp</span>',
                'paused': '<span class="badge badge-warning">Tạm dừng</span>',
                'auto_paused': '<span class="badge badge-danger">Tự dừng</span>',
            };
            return map[s] || `<span class="badge">${s}</span>`;
        };

        container.innerHTML = this.campaigns.map(c => `
            <div class="card campaign-card ${c.status === 'auto_paused' ? 'card-danger' : ''}" data-id="${c.id}">
                <div class="card-header" style="display:flex;justify-content:space-between;align-items:center">
                    <strong>${this._esc(c.name)}</strong>
                    ${statusBadge(c.status)}
                </div>
                <div class="card-body" style="font-size:0.85em">
                    <div>Nhóm: ${(c.group_titles || []).join(', ') || 'Chưa cấu hình'}</div>
                    <div>Từ khóa: ${(c.trigger_keywords || []).slice(0, 5).join(', ')}${(c.trigger_keywords || []).length > 5 ? '...' : ''}</div>
                    <div>Rate: ${c.max_replies_per_hour_per_group}/giờ, ${c.max_replies_per_day_per_group}/ngày</div>
                    ${c.pause_reason ? `<div class="text-danger">Lý do: ${this._esc(c.pause_reason)}</div>` : ''}
                </div>
                <div class="card-footer" style="display:flex;gap:6px">
                    <button class="btn btn-sm btn-primary" onclick="Engagement.editCampaign(${c.id})">Sửa</button>
                    ${c.status === 'active'
                        ? `<button class="btn btn-sm btn-warning" onclick="Engagement.toggleCampaign(${c.id}, false)">Tạm dừng</button>`
                        : `<button class="btn btn-sm btn-success" onclick="Engagement.toggleCampaign(${c.id}, true)">Bật</button>`
                    }
                    <button class="btn btn-sm btn-danger" onclick="Engagement.deleteCampaign(${c.id})">Xoá</button>
                </div>
            </div>
        `).join('');
    },

    renderForm(campaign = null) {
        const form = document.getElementById('engagement-form');
        if (!form) return;

        const c = campaign || {};
        const isEdit = !!c.id;

        const agentOptions = this.agents.map(a =>
            `<option value="${a.id}" ${c.ai_agent_id == a.id ? 'selected' : ''}>${this._esc(a.name)}</option>`
        ).join('');

        const dayLabels = ['T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'CN'];
        const activeDays = c.active_days || [1, 2, 3, 4, 5, 6, 7];
        const dayCheckboxes = dayLabels.map((label, i) => {
            const day = i + 1;
            return `<label class="checkbox-inline"><input type="checkbox" name="active_day" value="${day}" ${activeDays.includes(day) ? 'checked' : ''}> ${label}</label>`;
        }).join(' ');

        form.innerHTML = `
            <h3>${isEdit ? 'Sửa chiến dịch' : 'Tạo chiến dịch mới'}</h3>

            <div class="form-group">
                <label>Tên chiến dịch *</label>
                <input type="text" id="eng-name" class="form-control" value="${this._esc(c.name || '')}" placeholder="VD: Nhóm support Binance">
            </div>

            <div class="form-group">
                <label>Nhóm mục tiêu (chat_id, mỗi dòng 1 ID)</label>
                <textarea id="eng-groups" class="form-control" rows="3" placeholder="-1001234567890&#10;-1009876543210">${(c.group_ids || []).join('\\n')}</textarea>
            </div>

            <div class="form-group">
                <label>Tên nhóm (tương ứng, mỗi dòng 1 tên)</label>
                <textarea id="eng-group-titles" class="form-control" rows="3" placeholder="Binance VN Support&#10;OKX Community">${(c.group_titles || []).join('\\n')}</textarea>
            </div>

            <div class="form-group">
                <label>Account Observer (ID, phẩy cách)</label>
                <input type="text" id="eng-observers" class="form-control" value="${(c.observer_account_ids || []).join(', ')}" placeholder="1, 2, 3">
            </div>

            <div class="form-group">
                <label>Account Reply (rỗng = dùng Observer)</label>
                <input type="text" id="eng-repliers" class="form-control" value="${(c.reply_account_ids || []).join(', ')}" placeholder="1, 2">
            </div>

            <div class="form-group">
                <label>AI Agent</label>
                <select id="eng-agent" class="form-control">
                    <option value="">-- Chọn agent --</option>
                    ${agentOptions}
                </select>
            </div>

            <div class="form-group">
                <label>Từ khóa trigger (phẩy cách)</label>
                <input type="text" id="eng-keywords" class="form-control"
                    value="${(c.trigger_keywords || []).join(', ')}"
                    placeholder="btc, eth, sol, phân tích, chart, target, sl, entry">
            </div>

            <div class="form-group">
                <label>Knowledge bổ sung</label>
                <textarea id="eng-knowledge" class="form-control" rows="3" placeholder="Nhận định TA riêng, view thị trường...">${this._esc(c.knowledge_extra || '')}</textarea>
            </div>

            <div class="form-row" style="display:flex;gap:12px">
                <div class="form-group" style="flex:1">
                    <label>Reply/giờ/nhóm (tối đa 6)</label>
                    <input type="number" id="eng-rph" class="form-control" value="${c.max_replies_per_hour_per_group || 4}" min="1" max="6">
                </div>
                <div class="form-group" style="flex:1">
                    <label>Reply/ngày/nhóm (tối đa 20)</label>
                    <input type="number" id="eng-rpd" class="form-control" value="${c.max_replies_per_day_per_group || 12}" min="1" max="20">
                </div>
                <div class="form-group" style="flex:1">
                    <label>Gap tối thiểu (giây)</label>
                    <input type="number" id="eng-gap" class="form-control" value="${c.min_gap_seconds || 180}" min="60">
                </div>
            </div>

            <div class="form-row" style="display:flex;gap:12px">
                <div class="form-group" style="flex:1">
                    <label>Giờ bắt đầu (VN)</label>
                    <input type="text" id="eng-sched-start" class="form-control" value="${c.schedule_start || '13:00'}" placeholder="13:00">
                </div>
                <div class="form-group" style="flex:1">
                    <label>Giờ kết thúc (VN)</label>
                    <input type="text" id="eng-sched-end" class="form-control" value="${c.schedule_end || '23:00'}" placeholder="23:00">
                </div>
            </div>

            <div class="form-group">
                <label>Ngày hoạt động</label>
                <div>${dayCheckboxes}</div>
            </div>

            <div class="form-group">
                <label>Link nhóm Signal</label>
                <input type="text" id="eng-signal-link" class="form-control" value="${this._esc(c.signal_group_link || '')}" placeholder="https://t.me/+xxx">
            </div>

            <div class="form-group" style="border:1px solid #dc3545;padding:12px;border-radius:6px;margin-top:12px">
                <label class="checkbox-label" style="display:flex;align-items:flex-start;gap:8px;cursor:pointer">
                    <input type="checkbox" id="eng-consent" ${c.operator_consent_confirmed ? 'checked' : ''} style="margin-top:4px">
                    <span style="font-size:0.85em"><strong>Tôi xác nhận:</strong> nhóm này cho phép thảo luận công khai, HOẶC tôi có quan hệ/được admin đồng ý. Tôi hiểu tính năng này vi phạm ToS Telegram và tự chịu rủi ro ban account.</span>
                </label>
            </div>

            <div style="margin-top:16px;display:flex;gap:8px">
                <button class="btn btn-primary" onclick="Engagement.saveCampaign(${c.id || 'null'})" id="eng-save-btn">
                    ${isEdit ? 'Cập nhật' : 'Tạo mới'}
                </button>
                ${isEdit ? `<button class="btn btn-success" onclick="Engagement.saveAndActivate(${c.id})" id="eng-activate-btn">Lưu &amp; Bật</button>` : ''}
                ${isEdit ? `<button class="btn btn-secondary" onclick="Engagement.cancelEdit()">Huỷ</button>` : ''}
            </div>
        `;
    },

    _collectFormData() {
        const name = document.getElementById('eng-name')?.value?.trim();
        if (!name) {
            App.toast('Tên chiến dịch không được để trống', 'error');
            return null;
        }

        const groupsText = document.getElementById('eng-groups')?.value?.trim() || '';
        const groupIds = groupsText.split(/[\n,]+/).map(s => parseInt(s.trim())).filter(n => !isNaN(n));

        const titlesText = document.getElementById('eng-group-titles')?.value?.trim() || '';
        const groupTitles = titlesText.split(/\n/).map(s => s.trim()).filter(Boolean);

        const observersText = document.getElementById('eng-observers')?.value?.trim() || '';
        const observerIds = observersText.split(/[,\s]+/).map(s => parseInt(s.trim())).filter(n => !isNaN(n));

        const repliersText = document.getElementById('eng-repliers')?.value?.trim() || '';
        const replyIds = repliersText.split(/[,\s]+/).map(s => parseInt(s.trim())).filter(n => !isNaN(n));

        const keywordsText = document.getElementById('eng-keywords')?.value?.trim() || '';
        const keywords = keywordsText.split(/,/).map(s => s.trim()).filter(Boolean);

        const activeDayEls = document.querySelectorAll('input[name="active_day"]:checked');
        const activeDays = Array.from(activeDayEls).map(el => parseInt(el.value));

        return {
            name,
            group_ids: groupIds,
            group_titles: groupTitles,
            observer_account_ids: observerIds,
            reply_account_ids: replyIds,
            ai_agent_id: parseInt(document.getElementById('eng-agent')?.value) || null,
            trigger_keywords: keywords,
            knowledge_extra: document.getElementById('eng-knowledge')?.value || '',
            max_replies_per_hour_per_group: parseInt(document.getElementById('eng-rph')?.value) || 4,
            max_replies_per_day_per_group: parseInt(document.getElementById('eng-rpd')?.value) || 12,
            min_gap_seconds: parseInt(document.getElementById('eng-gap')?.value) || 180,
            schedule_start: document.getElementById('eng-sched-start')?.value || '13:00',
            schedule_end: document.getElementById('eng-sched-end')?.value || '23:00',
            active_days: activeDays.length ? activeDays : [1, 2, 3, 4, 5, 6, 7],
            signal_group_link: document.getElementById('eng-signal-link')?.value || '',
            operator_consent_confirmed: document.getElementById('eng-consent')?.checked ? 1 : 0,
            operator_consent_by: 'dashboard',
        };
    },

    async saveCampaign(id) {
        const data = this._collectFormData();
        if (!data) return;

        try {
            if (id) {
                await API.put(`/api/engagement/campaigns/${id}`, data);
                App.toast('Đã cập nhật chiến dịch', 'success');
            } else {
                await API.post('/api/engagement/campaigns', data);
                App.toast('Đã tạo chiến dịch', 'success');
            }
            await this.loadCampaigns();
            this.renderCampaigns();
            this.renderForm();
        } catch (e) {
            App.toast('Lỗi: ' + (e.message || e), 'error');
        }
    },

    async saveAndActivate(id) {
        const data = this._collectFormData();
        if (!data) return;

        if (!data.operator_consent_confirmed) {
            App.toast('Vui lòng xác nhận đồng ý trước khi bật chiến dịch', 'error');
            return;
        }

        try {
            await API.put(`/api/engagement/campaigns/${id}`, data);
            await API.post(`/api/engagement/campaigns/${id}/activate`);
            App.toast('Đã bật chiến dịch', 'success');
            await this.loadCampaigns();
            this.renderCampaigns();
            this.renderForm();
        } catch (e) {
            App.toast('Lỗi: ' + (e.message || e), 'error');
        }
    },

    async editCampaign(id) {
        const campaign = this.campaigns.find(c => c.id === id);
        if (campaign) {
            this.currentCampaignId = id;
            this.renderForm(campaign);
            document.getElementById('engagement-form')?.scrollIntoView({ behavior: 'smooth' });
        }
    },

    cancelEdit() {
        this.currentCampaignId = null;
        this.renderForm();
    },

    async toggleCampaign(id, active) {
        try {
            if (active) {
                await API.post(`/api/engagement/campaigns/${id}/activate`);
                App.toast('Đã bật chiến dịch', 'success');
            } else {
                await API.post(`/api/engagement/campaigns/${id}/pause`);
                App.toast('Đã tạm dừng chiến dịch', 'success');
            }
            await this.loadCampaigns();
            this.renderCampaigns();
        } catch (e) {
            App.toast('Lỗi: ' + (e.message || e), 'error');
        }
    },

    async deleteCampaign(id) {
        if (!confirm('Xoá chiến dịch này?')) return;
        try {
            await API.delete(`/api/engagement/campaigns/${id}`);
            App.toast('Đã xoá chiến dịch', 'success');
            await this.loadCampaigns();
            this.renderCampaigns();
        } catch (e) {
            App.toast('Lỗi: ' + (e.message || e), 'error');
        }
    },

    async loadLogs() {
        try {
            const res = await API.get('/api/engagement/logs?limit=50');
            this.renderLogs(res.logs || [], res.total || 0);
        } catch (e) {
            console.error('Failed to load engagement logs:', e);
        }
    },

    renderLogs(logs, total) {
        const container = document.getElementById('engagement-log-table');
        if (!container) return;

        if (!logs.length) {
            container.innerHTML = '<div class="empty-state">Chưa có log nào.</div>';
            return;
        }

        const statusBadge = (s) => {
            const map = {
                'success': '<span class="badge badge-success">OK</span>',
                'failed': '<span class="badge badge-danger">Lỗi</span>',
                'skipped': '<span class="badge badge-warning">Bỏ qua</span>',
            };
            return map[s] || `<span class="badge">${s}</span>`;
        };

        container.innerHTML = `
            <div style="margin-bottom:8px;font-size:0.85em;color:var(--text-secondary)">Tổng: ${total} bản ghi</div>
            <div class="table-wrap">
                <table class="data-table">
                    <thead>
                        <tr>
                            <th>Thời gian</th>
                            <th>Loại</th>
                            <th>Nhóm</th>
                            <th>Nick</th>
                            <th>Câu hỏi</th>
                            <th>Reply</th>
                            <th>Trạng thái</th>
                            <th>Lý do</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${logs.map(l => `
                            <tr>
                                <td style="white-space:nowrap">${this._formatTime(l.created_at)}</td>
                                <td>${l.kind || ''}</td>
                                <td>${l.group_title || l.group_id || ''}</td>
                                <td>${l.account_id || ''}</td>
                                <td title="${this._esc(l.trigger_text || '')}">${this._truncate(l.trigger_text, 40)}</td>
                                <td title="${this._esc(l.reply_text || '')}">${this._truncate(l.reply_text, 40)}</td>
                                <td>${statusBadge(l.status)}</td>
                                <td>${l.skip_reason || ''}</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
        `;
    },

    async loadStats() {
        try {
            const stats = await API.get('/api/engagement/stats?days=1');
            this.renderHealth(stats);
        } catch (e) {
            console.error('Failed to load engagement stats:', e);
        }
    },

    renderHealth(stats) {
        const container = document.getElementById('engagement-health');
        if (!container) return;

        const hourPct = stats.hour_replies ? Math.round((stats.hour_replies / 4) * 100) : 0;
        const isWarning = hourPct >= 75;
        const hasPaused = this.campaigns.some(c => c.status === 'auto_paused');

        container.innerHTML = `
            ${hasPaused ? '<div class="alert alert-danger" style="margin-bottom:12px">⚠️ Có chiến dịch bị tự dừng do mute/ban. Kiểm tra và xử lý.</div>' : ''}
            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px">
                <div class="stat-card">
                    <div class="stat-value">${stats.today_replies || 0}</div>
                    <div class="stat-label">Reply hôm nay</div>
                </div>
                <div class="stat-card ${isWarning ? 'stat-warning' : ''}">
                    <div class="stat-value">${stats.hour_replies || 0}</div>
                    <div class="stat-label">Reply giờ vừa rồi</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">${stats.muted_accounts_24h || 0}</div>
                    <div class="stat-label">Nick bị mute 24h</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">${stats.skipped || 0}</div>
                    <div class="stat-label">Bỏ qua (${Object.keys(stats.skip_reasons || {}).length} lý do)</div>
                </div>
            </div>
        `;
    },

    _esc(str) {
        if (!str) return '';
        return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    },

    _truncate(str, len) {
        if (!str) return '';
        str = String(str);
        return str.length > len ? this._esc(str.substring(0, len)) + '…' : this._esc(str);
    },

    _formatTime(iso) {
        if (!iso) return '';
        try {
            const d = new Date(iso + 'Z');
            return d.toLocaleString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh', hour12: false });
        } catch {
            return iso;
        }
    },
};

// Auto-init when page navigated
if (typeof App !== 'undefined') {
    const _origNav = App._onPageLoad || (() => {});
    App._engagementInit = () => {
        if (document.getElementById('engagement-page')) {
            Engagement.init();
        }
    };
}
