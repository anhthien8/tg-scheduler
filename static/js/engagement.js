/**
 * engagement.js — Community Engagement Engine UI
 * Tab: "Cộng Đồng" — Campaign management, logs, health panel
 */
const Engagement = {
    campaigns: [],
    currentCampaignId: null,
    agents: [],

    _lastStats: null,

    async init() {
        await Promise.all([
            this.loadAgents(),
            this.loadPickers(),
            this.loadCampaigns(),
            this.loadLogs(),
            this.loadStats(),
        ]);
        this.renderCampaigns();
        this.renderForm();
        if (this._lastStats) this.renderHealth(this._lastStats);
    },

    async loadAgents() {
        try {
            const res = await API.get('/api/ai-agents');
            this.agents = res.agents || [];
        } catch (e) {
            this.agents = [];
        }
    },

    async loadPickers() {
        const [acc, grp] = await Promise.all([
            API.get('/api/auth/accounts').catch(() => ({})),
            API.get('/api/warmup/groups').catch(() => ({})),
        ]);
        const list = Array.isArray(acc) ? acc : (acc.accounts || []);
        this.accounts = list.filter(a => a && a.id);
        this.knownGroups = (grp.groups || []).filter(g => g.chat_id);
    },

    // One-click presets for "Kiến thức thêm cho AI". They are appended to the
    // engine's COMMUNITY_TA_PROMPT, so they must respect its rules: no links,
    // no exchange names, no ref codes, no "chắc chắn/cam kết/x100/all-in"
    // (_contains_shill drops those replies). Conversions go "ib" → AI DM.
    // kw = trigger keywords merged into "Từ khóa kích hoạt" on click.
    KNOWLEDGE_TEMPLATES: [
        {
            name: 'TA trung lập',
            kw: 'chart, phân tích, hỗ trợ, kháng cự, support, resistance, breakout, entry, long hay short, xu hướng',
            text: `[MẪU 1 — TA TRUNG LẬP]
Vai trò: trader swing 4-5 năm kinh nghiệm, trả lời như người thật trong nhóm, giọng bình tĩnh, hơi kiệm lời.
Cách trả lời:
- Câu 1: nêu xu hướng hiện tại theo khung 4H hoặc 1D (tăng / giảm / sideway).
- Câu 2: 1 vùng hỗ trợ và 1 vùng kháng cự quan trọng (dạng vùng, VD "quanh 92-93k"), chỉ dùng số có trong tin nhắn/ngữ cảnh; không có dữ liệu thì nói "phải xem lại chart khung X" thay vì bịa số.
- Câu 3: kịch bản: "giữ được vùng A thì có thể test B; mất A thì quay về C". Luôn có mốc invalidation.
- Câu cuối: nhắc ngắn về quản lý vốn/SL, mỗi lần diễn đạt khác nhau.
Khi người hỏi xin lệnh cụ thể ("vào lệnh ở đâu", "long được không"): không hô lệnh, chỉ nêu vùng mình đang để ý và điều kiện xác nhận (VD nến 4H đóng trên X).
Khi bị phản bác: công nhận góc nhìn khác hợp lý, không cãi tay đôi, không cố thắng.
Tránh: icon tên lửa, "to the moon", dự đoán giá theo ngày, khoe lệnh lãi.
Ví dụ: "BTC 4H vẫn đang sideway 92-96k. Giữ được 92k thì khả năng test lại 96k, thủng 92k với volume lớn thì nhiều khả năng về 89-90k. Mình chờ nến 4H xác nhận rồi mới tính, SL để ngoài vùng nhé."`,
        },
        {
            name: 'Hỏi sàn',
            kw: 'sàn nào, sàn gì, kyc, rút tiền, phí giao dịch, no kyc, bị khoá, đóng băng, thuế, tax',
            text: `[MẪU 2 — KHI CÓ NGƯỜI HỎI VỀ SÀN]
Chỉ dùng khi có người HỎI TRỰC TIẾP về sàn, phí, KYC, rút tiền, bị khoá tài khoản hoặc thuế. Không tự mở chủ đề này.
Trong nhóm công khai KHÔNG nêu tên sàn, KHÔNG gửi link hay mã giới thiệu. Thay vào đó:
- Chia sẻ TIÊU CHÍ chọn sàn từ trải nghiệm cá nhân: không bắt KYC (NON KYC), không khấu trừ thuế (NON TAX), phí futures thấp, rút về ví nhanh, thanh khoản đủ sâu, app ổn định khi thị trường biến động.
- Nêu 1 kinh nghiệm thật ngắn, VD "mình từng bị treo rút 3 ngày vì KYC lại nên giờ ưu tiên sàn không bắt KYC".
- Kết bằng lời mời nhẹ, không ép: "ai cần mình ib chia sẻ sàn đang dùng" / "cần thì nhắn riêng mình".
Nếu có người nói xấu một sàn: không tranh luận, chỉ khuyên rút bớt về ví riêng và chia vốn ra nhiều nơi.
Tối đa 1 lần nhắc lời mời ib trong mỗi cuộc hội thoại; nếu người hỏi không phản hồi thì thôi.
Ví dụ: "Mình chọn sàn theo 3 tiêu chí: không bắt KYC, không trừ thuế, rút về ví trong vài phút. Từng bị treo tiền vì KYC lại nên giờ né hẳn mấy sàn đó. Bạn cần thì ib mình chia sẻ chỗ mình đang dùng."`,
        },
        {
            name: 'Mời nhóm Signal',
            kw: 'kèo, signal, tín hiệu, nhóm kèo, nhóm signal, có ai share kèo, xin kèo, theo ai, cháy tài khoản, thua lỗ',
            text: `[MẪU 3 — KHI CÓ NGƯỜI TÌM KÈO / TÌM NHÓM]
Chỉ dùng khi có người hỏi tìm kèo, tìm nhóm signal, hỏi "theo ai", hoặc than thua lỗ vì trade một mình.
Trong nhóm công khai KHÔNG gửi link nhóm, KHÔNG nêu tên nhóm, KHÔNG nói "nhóm VIP" hay "kèo chuẩn".
Cách trả lời theo 3 nhịp:
1. Đồng cảm/đúng vấn đề: "Trade một mình dễ FOMO lắm" / "Mình cũng từng cháy vì theo kèo bừa".
2. Cho giá trị trước: 1 mẹo cụ thể để chọn nguồn kèo (có entry, SL, TP rõ ràng; có lịch sử công khai cả lệnh thua; không bắt nạp tiền trước; không hứa % lợi nhuận).
3. Mời nhẹ qua tin nhắn riêng: "mình đang theo một nhóm nhỏ chia sẻ lệnh có SL rõ ràng, ai muốn xem thử thì ib mình gửi". Không lặp lại lời mời nếu người ta không hỏi tiếp.
Không hứa lợi nhuận, không nói winrate, không khoe lệnh.
Nếu nhiều người cùng hỏi: chỉ trả lời 1 lần chung, không trả lời từng người.
Ví dụ: "Theo kèo thì nhìn 2 thứ trước: có SL rõ không, và có dám công khai cả lệnh thua không. Không có 2 cái đó thì né. Mình đang theo một nhóm nhỏ làm đúng kiểu đó, ai muốn xem thử thì ib mình nhé."`,
        },
        {
            name: 'Hỗ trợ newbie',
            kw: 'newbie, người mới, mới chơi, spot, futures, đòn bẩy, leverage, thanh lý, liquidation, funding, cháy tài khoản',
            text: `[MẪU 4 — HỖ TRỢ NGƯỜI MỚI]
Nhận diện người mới: hỏi khái niệm cơ bản (spot/futures, đòn bẩy, funding, thanh lý, ví), dùng từ chưa chính xác, hoặc vừa cháy tài khoản.
Cách trả lời:
- Giải thích bằng ví dụ đời thường, 2-3 câu, không thuật ngữ khó; nếu buộc phải dùng thì giải nghĩa luôn trong ngoặc.
- Luôn kèm 1 lời khuyên an toàn đúng ngữ cảnh: đòn bẩy thấp (x2-x5), luôn đặt SL, mỗi lệnh rủi ro tối đa 1-2% vốn, không nạp tiền đi vay.
- Với người vừa cháy: an ủi ngắn, không phán xét, gợi ý nghỉ vài ngày rồi tập lại với vốn nhỏ.
- Nếu người mới hỏi nên chơi sàn nào: áp dụng Mẫu 2 (tiêu chí + mời ib), không nêu tên.
Không bao giờ: khuyên vào lệnh cụ thể, khuyên "gồng lỗ", khuyên nhồi lệnh để gỡ.
Ví dụ: "Đòn bẩy x10 nghĩa là giá đi ngược 10% là mất sạch tiền ký quỹ của lệnh đó. Mới chơi thì để x2-x3 và luôn đặt SL, mỗi lệnh chỉ chịu mất tối đa 1-2% tổng vốn thôi."`,
        },
        {
            name: 'Tin tức & vĩ mô',
            kw: 'fed, cpi, lãi suất, fomc, etf, unlock, halving, vĩ mô, macro, nfp',
            text: `[MẪU 5 — TIN TỨC & VĨ MÔ]
Chỉ dùng khi trong nhóm đang bàn về tin: Fed/FOMC, CPI, NFP, lãi suất, ETF, SEC, unlock token, halving, chính sách.
Cách trả lời:
- Tóm tắt tin bằng 1 câu (chỉ dựa trên thông tin người trong nhóm đã đưa ra, không tự bịa số liệu hay ngày giờ).
- Nêu 2 kịch bản tác động: "nếu ra tốt hơn dự báo thì…, nếu xấu hơn thì…". Dùng từ "có thể", "nhiều khả năng", không dùng từ khẳng định tuyệt đối.
- Lời khuyên hành động trung lập: giảm đòn bẩy trước giờ ra tin, tránh vào lệnh trong 15-30 phút đầu, chờ thị trường chọn hướng.
- Unlock token: nhắc quy mô so với lượng lưu hành (nếu có số), khả năng áp lực bán ngắn hạn.
Không đưa tin đồn, không chính trị hoá, không dự đoán giá sau tin theo con số cụ thể.
Ví dụ: "Tối nay ra CPI. Ra thấp hơn dự báo thì thị trường có thể kỳ vọng giảm lãi suất, crypto hay hồi; cao hơn thì dễ có cú quét 2 đầu. Mình hạ đòn bẩy trước giờ ra tin và chờ 30 phút đầu rồi mới tính."`,
        },
    ],

    applyKnowledge(i) {
        const tpl = this.KNOWLEDGE_TEMPLATES[i];
        const el = document.getElementById('eng-knowledge');
        if (!el || !tpl) return;
        if (!el.value.includes(tpl.text)) {
            el.value = el.value.trim() ? el.value.trim() + '\n\n' + tpl.text : tpl.text;
        }
        // Merge the template's trigger keywords without duplicates.
        const kwEl = document.getElementById('eng-keywords');
        if (kwEl && tpl.kw) {
            const cur = kwEl.value.split(',').map(s => s.trim()).filter(Boolean);
            const seen = new Set(cur.map(s => s.toLowerCase()));
            tpl.kw.split(',').map(s => s.trim()).forEach(k => { if (k && !seen.has(k.toLowerCase())) { cur.push(k); seen.add(k.toLowerCase()); } });
            kwEl.value = cur.join(', ');
        }
        el.rows = Math.min(14, Math.max(3, el.value.split('\n').length + 1));
        el.focus();
        App.toast(`Đã chèn mẫu #${i + 1} + từ khóa`, 'success');
    },

    // Health of an account for the picker: ok=true means safe to use right now.
    _accStatus(a) {
        // peerflood_until is a unix timestamp in seconds (REAL column).
        const pfTs = Number(a.peerflood_until) || 0;
        const pf = pfTs * 1000 > Date.now();
        if (a.session_error) return { ok: false, color: 'var(--danger,#e5534b)', label: 'Lỗi session', detail: a.session_error };
        if (!a.is_logged_in) return { ok: false, color: 'var(--text3)', label: 'Offline' };
        if (a.is_paused) return { ok: false, color: '#d29922', label: 'Tạm dừng', detail: a.pause_reason };
        if (a.is_flagged) return { ok: false, color: '#d29922', label: 'Bị flag', detail: a.flag_reason };
        if (pf) return { ok: false, color: '#d29922', label: 'PeerFlood', detail: 'Hết hạn: ' + new Date(pfTs * 1000).toLocaleString('vi-VN') };
        return { ok: true, color: 'var(--success,#3fb950)', label: 'Hoạt động' };
    },

    // "Near | Weex" → "Weex"; used for one-click brand tags.
    _accBrand(a) {
        const parts = (a.name || '').split('|');
        if (parts.length < 2) return '';
        return parts.slice(1).join(' ').replace(/[^\p{L}\p{N} ]/gu, '').trim().toUpperCase();
    },

    quickPick(sel, col) {
        document.querySelectorAll('.eng-acc tbody tr').forEach(tr => {
            const on = sel === 'none' ? false
                : sel === 'ok' ? tr.dataset.ok === '1'
                : tr.dataset.ok === '1' && tr.dataset.brand === sel.slice(6);
            const o = tr.querySelector('input[name="eng-obs"]'), r = tr.querySelector('input[name="eng-rep"]');
            if (!o) return;
            if (col !== 'rep') o.checked = on;
            if (col === 'both' || col === 'rep') r.checked = on;
            if (col === 'obs') r.checked = false;
        });
    },

    _tip(t) { return `<span class="info-i" tabindex="0" data-tip="${this._esc(t)}">i</span>`; },

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
        const tip = (t) => this._tip(t);

        const agentOptions = this.agents.map(a =>
            `<option value="${a.id}" ${c.ai_agent_id == a.id ? 'selected' : ''}>${this._esc(a.name)}</option>`
        ).join('');

        // Groups: tick from the Warmup list; anything else goes in the "other" box as "chat_id | tên".
        const ids = (c.group_ids || []).map(String);
        const titles = c.group_titles || [];
        const known = this.knownGroups || [];
        const knownIds = new Set(known.map(g => String(g.chat_id)));
        const groupChecks = known.map(g => {
            const t = g.chat_title || g.name || '';
            return `<label class="eng-chip"><input type="checkbox" name="eng-grp" value="${this._esc(String(g.chat_id))}"
                data-title="${this._esc(t)}" ${ids.includes(String(g.chat_id)) ? 'checked' : ''}> ${this._esc(t || g.chat_id)}</label>`;
        }).join('');
        const otherLines = ids.map((id, i) => knownIds.has(id) ? null : `${id} | ${titles[i] || ''}`).filter(Boolean).join('\n');

        // Accounts: one row per account with two ticks instead of typing IDs.
        const obs = new Set((c.observer_account_ids || []).map(Number));
        const rep = new Set((c.reply_account_ids || []).map(Number));
        const accs = (this.accounts || []).map(a => ({ a, st: this._accStatus(a), brand: this._accBrand(a) }));
        const accRows = accs.map(({ a, st, brand }) => {
            const nm = a.name || a.user_info?.first_name || a.phone || `#${a.id}`;
            return `<tr data-ok="${st.ok ? 1 : 0}" data-brand="${this._esc(brand)}"><td>${this._esc(nm)}${a.is_premium ? ' ⭐' : ''}</td>
                <td><span class="eng-st" style="color:${st.color}" title="${this._esc(st.detail || '')}">● ${st.label}</span></td>
                <td style="text-align:center"><input type="checkbox" name="eng-obs" value="${a.id}" ${obs.has(a.id) ? 'checked' : ''}></td>
                <td style="text-align:center"><input type="checkbox" name="eng-rep" value="${a.id}" ${rep.has(a.id) ? 'checked' : ''}></td></tr>`;
        }).join('') || '<tr><td colspan="4" class="text-muted">Chưa có account</td></tr>';
        const nOk = accs.filter(x => x.st.ok).length;
        const brands = [...new Set(accs.map(x => x.brand).filter(Boolean))].sort();
        const qBtn = (label, sel, col) => `<button type="button" class="eng-q" onclick="Engagement.quickPick('${sel}','${col}')">${label}</button>`;
        const quick = `<div class="eng-chips" style="margin-bottom:6px;align-items:center">
            <span style="font-size:.78em;color:var(--text3)">Chọn nhanh:</span>
            ${qBtn(`✅ Hoạt động (${nOk})`, 'ok', 'both')}
            ${brands.map(b => qBtn(this._esc(b), 'brand:' + this._esc(b), 'both')).join('')}
            ${qBtn('👁 Hoạt động → chỉ Theo dõi', 'ok', 'obs')}
            ${qBtn('✖ Bỏ chọn', 'none', 'both')}
        </div>`;

        const dayLabels = ['T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'CN'];
        const activeDays = c.active_days || [1, 2, 3, 4, 5, 6, 7];
        const dayCheckboxes = dayLabels.map((label, i) => {
            const day = i + 1;
            return `<label class="eng-chip"><input type="checkbox" name="active_day" value="${day}" ${activeDays.includes(day) ? 'checked' : ''}> ${label}</label>`;
        }).join('');

        form.innerHTML = `
            <style>
                .eng-step{font-weight:600;margin:18px 0 8px;display:flex;align-items:center;gap:6px}
                .eng-step b{display:inline-grid;place-items:center;width:20px;height:20px;border-radius:50%;background:var(--accent);color:#000;font-size:11px}
                .eng-chips{display:flex;flex-wrap:wrap;gap:6px}
                .eng-chip{display:inline-flex;align-items:center;gap:5px;padding:4px 10px;border:1px solid var(--border);border-radius:14px;font-size:.85em;cursor:pointer;margin:0;text-transform:none;font-weight:400}
                .eng-chip:has(input:checked){border-color:var(--accent);background:color-mix(in srgb,var(--accent) 15%,transparent)}
                .eng-acc{width:100%;font-size:.85em;border-collapse:collapse}
                .eng-acc th{position:sticky;top:0;background:var(--bg-card,var(--bg));font-weight:500;color:var(--text3);text-align:center}.eng-acc th:first-child{text-align:left}
                .eng-q{padding:3px 10px;border:1px solid var(--border);border-radius:14px;background:transparent;color:var(--text);font-size:.78em;cursor:pointer}
                .eng-q:hover{border-color:var(--accent);color:var(--accent)}
                .eng-st{font-size:.85em;white-space:nowrap}
                .eng-acc tr[data-ok="0"] td:first-child{color:var(--text3)}
                .eng-acc td,.eng-acc th{padding:5px 8px;border-bottom:1px solid var(--border)}
                .eng-form .lbl{display:flex;align-items:center;gap:6px}
            </style>
            <div class="eng-form">
            <h3>${isEdit ? 'Sửa chiến dịch' : 'Tạo chiến dịch mới'}</h3>

            <div class="form-row" style="display:flex;gap:12px">
                <div class="form-group" style="flex:2">
                    <label class="lbl">Tên chiến dịch *</label>
                    <input type="text" id="eng-name" class="form-control" value="${this._esc(c.name || '')}" placeholder="VD: Nhóm support Binance">
                </div>
                <div class="form-group" style="flex:1">
                    <label class="lbl">AI Agent ${tip('Agent soạn câu trả lời (giọng văn + kiến thức). Cấu hình ở mục Cấu hình AI Agents.')}</label>
                    <select id="eng-agent" class="form-control"><option value="">-- Chọn agent --</option>${agentOptions}</select>
                </div>
            </div>

            <div class="eng-step"><b>1</b> Chọn nhóm ${tip('Danh sách lấy từ Warmup Nhóm. Muốn thêm nhóm mới: vào Warmup Nhóm, dán link, rồi quay lại đây tick.')}</div>
            <div class="eng-chips">${groupChecks || '<span class="text-muted" style="font-size:.85em">Chưa có nhóm — thêm ở Warmup Nhóm (chỉ cần dán link).</span>'}</div>
            <details style="margin-top:6px" ${otherLines ? 'open' : ''}><summary style="font-size:.8em;cursor:pointer;color:var(--text3)">Nhóm khác (nhập tay chat_id)</summary>
                <textarea id="eng-groups-other" class="form-control" rows="2" placeholder="-1001234567890 | Binance VN Support">${this._esc(otherLines)}</textarea>
            </details>

            <div class="eng-step"><b>2</b> Chọn account ${tip('Theo dõi = account ngồi trong nhóm đọc tin để bắt từ khóa. Trả lời = account gửi reply. Không tick ô Trả lời nào thì dùng luôn các account Theo dõi.')}</div>
            ${quick}<div style="max-height:240px;overflow:auto;border:1px solid var(--border);border-radius:8px"><table class="eng-acc"><thead><tr><th>Account</th><th style="text-align:left">Tình trạng</th><th>👁 Theo dõi</th><th>💬 Trả lời</th></tr></thead><tbody>${accRows}</tbody></table></div>

            <div class="eng-step"><b>3</b> Khi nào AI trả lời</div>
            <div class="form-group">
                <label class="lbl">Từ khóa kích hoạt ${tip('Tin trong nhóm chứa 1 trong các từ này thì AI cân nhắc trả lời. Cách nhau bằng dấu phẩy.')}</label>
                <input type="text" id="eng-keywords" class="form-control" value="${this._esc((c.trigger_keywords || []).join(', '))}" placeholder="btc, eth, sol, phân tích, chart, target, sl, entry">
            </div>
            <div class="form-group">
                <label class="lbl">Kiến thức thêm cho AI ${tip('Ghi chú riêng cho chiến dịch này, VD nhận định thị trường, quan điểm TA. AI dựa vào đây khi trả lời.')}</label>
                <textarea id="eng-knowledge" class="form-control" rows="2" placeholder="Nhận định TA riêng, view thị trường...">${this._esc(c.knowledge_extra || '')}</textarea>
                <div class="eng-chips" style="margin-top:6px;align-items:center">
                    <span style="font-size:.78em;color:var(--text3)">Mẫu:</span>
                    ${this.KNOWLEDGE_TEMPLATES.map((t, i) => `<button type="button" class="eng-q" title="${this._esc(t.text)}" onclick="Engagement.applyKnowledge(${i})">#${i + 1} ${this._esc(t.name)}</button>`).join('')}
                </div>
            </div>
            <div class="form-group">
                <label class="lbl">Link nhóm Signal ${tip('Link mời mà AI có thể nhắc tới khi phù hợp. Để trống nếu không cần.')}</label>
                <input type="text" id="eng-signal-link" class="form-control" value="${this._esc(c.signal_group_link || '')}" placeholder="https://t.me/+xxx">
            </div>

            <div class="eng-step"><b>4</b> Lịch chạy ${tip('Giờ Việt Nam. Ngoài khung giờ và ngày đã chọn, chiến dịch không trả lời.')}</div>
            <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
                <input type="time" id="eng-sched-start" class="form-control" style="width:120px" value="${c.schedule_start || '13:00'}">
                <span>→</span>
                <input type="time" id="eng-sched-end" class="form-control" style="width:120px" value="${c.schedule_end || '23:00'}">
                <div class="eng-chips">${dayCheckboxes}</div>
            </div>

            <details style="margin-top:16px"><summary style="cursor:pointer;font-weight:600">⚙️ Giới hạn an toàn (nâng cao)</summary>
            <div class="form-row" style="display:flex;gap:12px;margin-top:8px">
                <div class="form-group" style="flex:1">
                    <label class="lbl">Reply/giờ/nhóm ${tip('Tối đa 6. Mặc định 4.')}</label>
                    <input type="number" id="eng-rph" class="form-control" value="${c.max_replies_per_hour_per_group || 4}" min="1" max="6">
                </div>
                <div class="form-group" style="flex:1">
                    <label class="lbl">Reply/ngày/nhóm ${tip('Tối đa 20. Mặc định 12.')}</label>
                    <input type="number" id="eng-rpd" class="form-control" value="${c.max_replies_per_day_per_group || 12}" min="1" max="20">
                </div>
                <div class="form-group" style="flex:1">
                    <label class="lbl">Cách nhau tối thiểu (giây) ${tip('Khoảng nghỉ ít nhất giữa 2 reply trong cùng nhóm cho tự nhiên. Tối thiểu 60.')}</label>
                    <input type="number" id="eng-gap" class="form-control" value="${c.min_gap_seconds || 180}" min="60">
                </div>
            </div>
            </details>

            <label style="display:flex;align-items:flex-start;gap:8px;cursor:pointer;margin-top:14px;font-size:.82em;color:var(--text3);text-transform:none;font-weight:400">
                <input type="checkbox" id="eng-consent" ${c.operator_consent_confirmed ? 'checked' : ''} style="margin-top:3px">
                <span>Tôi xác nhận nhóm cho phép thảo luận công khai (hoặc admin đồng ý) và tự chịu rủi ro account theo ToS Telegram.</span>
            </label>

            <div style="margin-top:16px;display:flex;gap:8px">
                <button class="btn btn-primary" onclick="Engagement.saveCampaign(${c.id || 'null'})" id="eng-save-btn">
                    ${isEdit ? 'Cập nhật' : 'Tạo mới'}
                </button>
                ${isEdit ? `<button class="btn btn-success" onclick="Engagement.saveAndActivate(${c.id})" id="eng-activate-btn">Lưu &amp; Bật</button>` : ''}
                ${isEdit ? `<button class="btn btn-secondary" onclick="Engagement.cancelEdit()">Huỷ</button>` : ''}
            </div>
            </div>
        `;
    },

    _collectFormData() {
        const name = document.getElementById('eng-name')?.value?.trim();
        if (!name) {
            App.toast('Tên chiến dịch không được để trống', 'error');
            return null;
        }

        const groupIds = [], groupTitles = [];
        document.querySelectorAll('input[name="eng-grp"]:checked').forEach(el => {
            groupIds.push(parseInt(el.value)); groupTitles.push(el.dataset.title || '');
        });
        (document.getElementById('eng-groups-other')?.value || '').split('\n').forEach(line => {
            const [id, ...rest] = line.split('|');
            const n = parseInt((id || '').trim());
            if (!isNaN(n) && !groupIds.includes(n)) { groupIds.push(n); groupTitles.push(rest.join('|').trim()); }
        });

        const pick = (name) => Array.from(document.querySelectorAll(`input[name="${name}"]:checked`)).map(el => parseInt(el.value));
        const observerIds = pick('eng-obs');
        const replyIds = pick('eng-rep');

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
            await API.del(`/api/engagement/campaigns/${id}`);
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
            this.renderLogs([], 0);
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
            this._lastStats = stats || {};
            this.renderHealth(this._lastStats);
        } catch (e) {
            console.error('Failed to load engagement stats:', e);
            this._lastStats = {};
            this.renderHealth({});
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

if (typeof window !== 'undefined') {
    window.Engagement = Engagement;
}

// Auto-init when page navigated
if (typeof App !== 'undefined') {
    App._engagementInit = () => {
        if (document.getElementById('view-engagement')) {
            Engagement.init();
        }
    };
}
