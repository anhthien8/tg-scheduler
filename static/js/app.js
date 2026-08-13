var me_dummy = me_dummy || {}; var me_dummy_style = me_dummy_style || {};
function debounce(func, delay = 250) {
  let timeoutId;
  return function (...args) {
    if (timeoutId) clearTimeout(timeoutId);
    timeoutId = setTimeout(() => {
      func.apply(this, args);
    }, delay);
  };
}

function accDisplayName(a){if(!a)return'?';const ui=a.user_info;if(ui&&(ui.first_name||ui.last_name)){return[ui.first_name,ui.last_name].filter(Boolean).join(' ');}return a.name||'?';}
function customConfirm(msg){return new Promise(r=>{const o=document.getElementById('confirm-modal');(document.getElementById('confirm-msg') || me_dummy).textContent = msg;o.classList.add('open');(document.getElementById('confirm-yes') || document.createElement("div")).onclick=()=>{o.classList.remove('open');r(true)};(document.getElementById('confirm-no') || document.createElement("div")).onclick=()=>{o.classList.remove('open');r(false)}})}

const App={currentPage:'dashboard',chats:[],schedules:[],accounts:[],phoneCodeHash:'',loginAccountId:null,loginPhone:'',logOffset:0,logLimit:30,

async init(){try{const s=await API.authStatus();if(s.authenticated){this.showDashboard(s.user);return}}catch{}

try{const a=await API.getAccounts();App._accounts=a.accounts||[];if(a.accounts&&a.accounts.length>0){this.showDashboard(null);return}}catch{}

this.showLogin()},

toast(m,t='info'){const e=document.createElement('div');e.className=`toast ${t}`;e.textContent=m;(document.getElementById('toasts') || document.createElement("div")).appendChild(e);setTimeout(()=>e.remove(),4000)},

toggleSidebar(){const sb=document.getElementById('sidebar');const ov=document.getElementById('sidebar-overlay');if(sb){sb.classList.toggle('open');}if(ov){ov.classList.toggle('open');}},


showLogin(){(document.getElementById('login-page')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('hidden');(document.getElementById('dashboard-page')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden')},

showDashboard(user){(document.getElementById('login-page')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');(document.getElementById('dashboard-page')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('hidden');

if(user){const n=[user.first_name,user.last_name].filter(Boolean).join(' ');(document.getElementById('user-info') || me_dummy).innerHTML = `<strong>${n}</strong><span>@${user.username||user.phone||''}</span>`}

this.navigate('dashboard');document.querySelectorAll('.day-btn').forEach(b=>b.addEventListener('click',()=>b.classList.toggle('active')))},

async addFirstAccount(){const name=(document.getElementById('setup-name')?.value || "").trim();const apiId=(document.getElementById('setup-api-id')?.value || "").trim();const apiHash=(document.getElementById('setup-api-hash')?.value || "").trim();const phone=(document.getElementById('setup-phone')?.value || "").trim();

if(!name||!apiId||!apiHash||!phone)return this.toast('Điền đầy đủ thông tin','error');

const btn=document.getElementById('btn-add-account');if(btn){btn.disabled=true;btn.textContent='Đang xử lý...';}

try{const r=await API.addAccount({name,phone,api_id:apiId,api_hash:apiHash});this.loginAccountId=r.account_id;this.loginPhone=phone;

const c=await API.sendCode(phone,r.account_id);this.phoneCodeHash=c.phone_code_hash;

(document.getElementById('login-step-setup')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');(document.getElementById('login-step-otp')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('hidden');document.getElementById('login-code')?.focus();this.toast('Mã OTP đã gửi','success')}catch(e){this.toast(e.message,'error')}

btn.disabled=false;btn.textContent='Thêm tài khoản'},

async verifyFirstAccount(){const code=(document.getElementById('login-code')?.value || "").trim();if(!code)return this.toast('Nhập mã OTP','error');

try{const r=await API.verify(this.loginPhone,code,this.phoneCodeHash,this.loginAccountId);

if(r.needs_password){(document.getElementById('login-step-otp')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');(document.getElementById('login-step-2fa')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('hidden');return}

this.toast('Đăng nhập thành công!','success');this.showDashboard(r)}catch(e){

if(e.message.includes('2FA')){(document.getElementById('login-step-otp')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');(document.getElementById('login-step-2fa')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('hidden')}else this.toast(e.message,'error')}},

async verify2FAFirst(){const pw=document.getElementById('login-password')?.value;try{const r=await API.verify(this.loginPhone,(document.getElementById('login-code')?.value || "").trim(),this.phoneCodeHash,this.loginAccountId,pw);this.toast('Đăng nhập thành công!','success');this.showDashboard(r)}catch(e){this.toast(e.message,'error')}},

navigate(page){this.currentPage=page;try{if(page==='channels'){this._populateChAccountSelect();}}catch(e){console.error('channels populate error:',e);}try{if(page==='members'){Members.populateAccounts();}}catch(e){console.error('members populate error:',e);}document.querySelectorAll('.nav-item').forEach(el=>el.classList.toggle('active',el.dataset.page===page));
// Close sidebar on mobile after navigation
if(window.innerWidth<=768){const sb=document.getElementById('sidebar');const ov=document.getElementById('sidebar-overlay');if(sb)sb.classList.remove('open');if(ov)ov.classList.remove('open');}


// Inbox uses a <template> — inject it once if not yet present
if(page==='inbox'){
  if(!document.getElementById('view-inbox')){
    const tpl=document.getElementById('tpl-inbox-view');
    const clone=tpl.content.cloneNode(true);
    document.querySelector('.main').appendChild(clone);
  }
  document.querySelectorAll('[id^="view-"]').forEach(el=>el.classList.add('hidden'));
  (document.getElementById('view-inbox')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('hidden');
  this._populateInboxAccountFilter();
  this.inboxLoad();
  return;
}

// Discord uses dynamic injection like inbox
if(page==='discord'){
  if(!document.getElementById('view-discord')){
    const discordHtml = `<div id="view-discord">
      <h2 class="page-title">🎮 Discord</h2>
      <div class="stats-grid" id="discord-stats"></div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin:20px 0 12px">
        <h3 style="margin:0">Bot Management</h3>
        <button class="btn btn-primary btn-sm" onclick="Discord.addBot()">+ Thêm Bot</button>
      </div>
      <div id="discord-bot-list"></div>
      <div style="margin-top:24px"><h3>Keyword Watchers (Discord)</h3></div>
      <div id="discord-watcher-list"></div>
    </div>`;
    const wrapper = document.createElement('div');
    wrapper.innerHTML = discordHtml;
    document.querySelector('.main').appendChild(wrapper.firstElementChild);
  }
  document.querySelectorAll('[id^="view-"]').forEach(el=>el.classList.add('hidden'));
  (document.getElementById('view-discord')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('hidden');
  Discord.init();
  return;
}

// AI Agents Management Page
if(page==='ai-agents'){
  document.querySelectorAll('[id^="view-"]').forEach(el=>el.classList.add('hidden'));
  const agentsView = document.getElementById('view-ai-agents');
  if(agentsView) agentsView.classList.remove('hidden');
  AIAgents.init();
  return;
}

// Changelog Release Notes Page
if(page==='changelog'){
  document.querySelectorAll('[id^="view-"]').forEach(el=>el.classList.add('hidden'));
  const changelogView = document.getElementById('view-changelog');
  if(changelogView) changelogView.classList.remove('hidden');
  if(window.Changelog) window.Changelog.init();
  return;
}

// AI Follow-Up Sales Agent Page
if(page==='ai-followup'){
  if(!document.getElementById('view-ai-followup')){
    const aiHtml = `<div id="view-ai-followup">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px">
        <div>
          <h2 class="page-title" style="margin-bottom:4px">🤖 AI Sales Agent (Follow-Up & Onboarding)</h2>
          <p class="page-subtitle" style="margin:0">Tự động nhắn tin tương tác, giải đáp thắc mắc và chốt deal/onboard người dùng khi họ phản hồi DM</p>
        </div>
      </div>

      <!-- Agent Settings Card -->
      <div class="card" style="padding:20px;margin-bottom:24px;border:1px solid rgba(139,92,246,0.3);border-radius:14px">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
          <h3 style="margin:0;font-size:16px;display:flex;align-items:center;gap:8px">
            <span>⚙️ Kịch Bản & Cấu Hình Agent</span>
          </h3>
          <label class="toggle">
            <input type="checkbox" id="aifu-enabled">
            <span class="toggle-slider"></span>
          </label>
        </div>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
          <div>
            <label class="form-label" style="font-weight:600">🎯 Sales Persona & Strategy (Kịch bản tư vấn)</label>
            <textarea id="aifu-sys-prompt" class="form-input" rows="6" placeholder="Nhập vai AI chuyên gia tư vấn..."></textarea>
            <div style="font-size:11px;color:var(--text2);margin-top:4px">Mô tả tính cách, xưng hô và cách khéo léo chốt deal/gửi link onboard.</div>
          </div>
          <div>
            <label class="form-label" style="font-weight:600">📚 Knowledge Base (Thông tin Sản Phẩm & Giá)</label>
            <textarea id="aifu-kb" class="form-input" rows="6" placeholder="Thông tin sản phẩm, bảng giá, FAQ, link onboard..."></textarea>
            <div style="font-size:11px;color:var(--text2);margin-top:4px">AI sẽ sử dụng thông tin này để trả lời thắc mắc của khách hàng.</div>
          </div>
        </div>

        <div style="display:grid;grid-template-columns:1fr 2fr;gap:16px;margin-top:16px">
          <div>
            <label class="form-label" style="font-weight:600">🔢 Số câu AI tự trả lời tối đa / user</label>
            <input type="number" id="aifu-max-replies" class="form-input" min="1" max="20" value="5">
          </div>
          <div>
            <label class="form-label" style="font-weight:600">🛑 Từ khóa bàn giao người thật (phân cách bằng dấu phẩy)</label>
            <input type="text" id="aifu-handover-kw" class="form-input" placeholder="gặp admin, tư vấn viên, số điện thoại, lừa đảo...">
          </div>
        </div>

        <div style="text-align:right;margin-top:16px">
          <button class="btn btn-primary" onclick="AIFollowUp.saveSettings()">💾 Lưu Cấu Hình Sales Agent</button>
        </div>
      </div>

      <!-- Live Lead Chat History Table -->
      <div class="card" style="padding:20px;border-radius:14px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
          <h3 style="margin:0;font-size:16px">💬 Danh Sách Lead Tương Tác & Handover</h3>
          <div style="display:flex;gap:10px;align-items:center">
            <select id="aifu-chat-status-filter" class="form-select" style="max-width:180px" onchange="AIFollowUp.loadChats()">
              <option value="">Tất cả trạng thái</option>
              <option value="active">🤖 AI Active</option>
              <option value="needs_human">⚠️ Cần Người Thật</option>
              <option value="onboarded">✅ Onboarded</option>
              <option value="paused_admin">⏸ Tắt AI (Handover)</option>
            </select>
            <button class="btn btn-sm btn-ghost" onclick="AIFollowUp.loadChats()">🔄 Làm mới</button>
          </div>
        </div>

        <div class="table-wrap">
          <table class="data-table">
            <thead>
              <tr>
                <th>Người Dùng</th>
                <th>Tài Khoản Tele</th>
                <th>Số Lượt Chat</th>
                <th>Trạng Thái</th>
                <th>Cập Nhật Lần Cuối</th>
                <th>Hành Động</th>
              </tr>
            </thead>
            <tbody id="aifu-chats-table-body">
              <tr><td colspan="6" style="text-align:center;padding:20px;color:var(--text2)">Đang tải cuộc trò chuyện...</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Chat History Modal -->
      <div id="aifu-history-modal" class="modal-overlay">
        <div class="modal" style="max-width:650px">
          <div class="modal-header">
            <h3 id="aifu-modal-title" style="margin:0">Lịch sử trò chuyện</h3>
            <button class="modal-close" onclick="AIFollowUp.closeHistoryModal()">&times;</button>
          </div>
          <div class="modal-body" style="max-height:450px;overflow-y:auto;padding:16px" id="aifu-modal-chat-box">
          </div>
          <div class="modal-footer">
            <button class="btn btn-secondary" onclick="AIFollowUp.closeHistoryModal()">Đóng</button>
          </div>
        </div>
      </div>
    </div>`;
    const wrapper = document.createElement('div');
    wrapper.innerHTML = aiHtml;
    document.querySelector('.main').appendChild(wrapper.firstElementChild);
  }
  document.querySelectorAll('[id^="view-"]').forEach(el=>el.classList.add('hidden'));
  (document.getElementById('view-ai-followup')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('hidden');
  AIFollowUp.init();
  return;
}


// Analytics Dashboard
if(page==='analytics'){
  if(!document.getElementById('view-analytics')){
    const html = `<div id="view-analytics">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px">
        <h2 class="page-title" style="margin:0">📊 Analytics Dashboard</h2>
        <button class="btn btn-primary btn-sm" onclick="Analytics.exportAllContacts()">📥 Export All Contacts</button>
      </div>
      <div class="stats-grid" style="margin-bottom:24px">
        <div class="stat-card"><div class="stat-label">DM Sent</div><div class="stat-value accent" id="an-total-sent">–</div></div>
        <div class="stat-card"><div class="stat-label">Replies</div><div class="stat-value green" id="an-total-replies">–</div></div>
        <div class="stat-card"><div class="stat-label">Response Rate</div><div class="stat-value" id="an-response-rate">–</div></div>
        <div class="stat-card"><div class="stat-label">Contacts</div><div class="stat-value accent" id="an-total-contacts">–</div></div>
        <div class="stat-card"><div class="stat-label">Active Campaigns</div><div class="stat-value green" id="an-active-campaigns">–</div></div>
        <div class="stat-card"><div class="stat-label">Reactions</div><div class="stat-value" id="an-total-reactions">–</div></div>
      </div>
      <div class="card" style="padding:16px;margin-bottom:24px">
        <h3 style="margin:0 0 12px">📈 DM Activity (30 ngày)</h3>
        <canvas id="an-chart"></canvas>
      </div>
      <h3>🏥 Account Health</h3>
      <div id="an-health-list" style="display:grid;grid-template-columns:repeat(auto-fill, minmax(240px, 1fr));gap:10px;margin-bottom:24px"></div>
      <h3>🎯 Campaign Performance</h3>
      <div id="an-campaign-list"></div>
    </div>`;
    const w = document.createElement('div'); w.innerHTML = html;
    document.querySelector('.main').appendChild(w.firstElementChild);
  }
  document.querySelectorAll('[id^="view-"]').forEach(el=>el.classList.add('hidden'));
  (document.getElementById('view-analytics')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('hidden');
  Analytics.init();
  return;
}

// Templates Library
if(page==='templates'){
  if(!document.getElementById('view-templates')){
    const html = `<div id="view-templates">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px">
        <h2 class="page-title" style="margin:0">📋 Template Library</h2>
        <button class="btn btn-primary btn-sm" onclick="Templates.openCreate()">+ Tạo Template</button>
      </div>
      <div id="tpl-list"></div>
      <div id="tpl-modal" class="modal-overlay">
        <div class="modal">
          <div class="modal-header"><h3 class="modal-title" id="tpl-modal-title">Tạo Template</h3><button class="modal-close" onclick="(document.getElementById('tpl-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('open')">×</button></div>
          <div class="modal-body">
            <div class="form-group"><label class="form-label">Tên Template</label><input type="text" id="tpl-name" class="form-input" placeholder="VD: Crypto Outreach"></div>
            <div class="form-group"><label class="form-label">Category</label><select id="tpl-category" class="form-select"><option value="general">General</option><option value="crypto">Crypto</option><option value="finance">Finance</option><option value="marketing">Marketing</option><option value="business">Business</option></select></div>
            <div class="form-group"><label class="form-label">Nội dung tin nhắn</label><textarea id="tpl-content" class="form-textarea" rows="8" placeholder="Nhập nội dung template...&#10;Dùng {name} để chèn tên người nhận"></textarea></div>
          </div>
          <div class="modal-footer"><button class="btn btn-primary" onclick="Templates.save()">💾 Lưu</button></div>
        </div>
      </div>
    </div>`;
    const w = document.createElement('div'); w.innerHTML = html;
    document.querySelector('.main').appendChild(w.firstElementChild);
  }
  document.querySelectorAll('[id^="view-"]').forEach(el=>el.classList.add('hidden'));
  (document.getElementById('view-templates')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('hidden');
  Templates.init();
  return;
}

// Auto-Reply Chatbot
if(page==='autoreply'){
  if(!document.getElementById('view-autoreply')){
    const html = `<div id="view-autoreply">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px">
        <h2 class="page-title" style="margin:0">🤖 Auto-Reply Chatbot</h2>
        <button class="btn btn-primary btn-sm" onclick="AutoReply.openCreate()">+ Tạo Rule</button>
      </div>
      <div id="ar-rules-list" style="display:grid;gap:10px"></div>

      <!-- Create/Edit Rule Modal -->
      <div id="ar-modal" class="modal-overlay">
        <div class="modal">
          <div class="modal-header"><h3 class="modal-title" id="ar-modal-title">Tạo Auto-Reply Rule</h3><button class="modal-close" onclick="(document.getElementById('ar-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('open')">×</button></div>
          <div class="modal-body">
            <div class="form-group"><label class="form-label">Tên Rule <span style="cursor:help;opacity:.6" title="Đặt tên để dễ quản lý, VD: Chào mừng khách mới">ℹ️</span></label><input type="text" id="ar-name" class="form-input" placeholder="VD: Welcome Reply"></div>
            <div class="form-group"><label class="form-label">Trigger Type <span style="cursor:help;opacity:.6" title="Keyword Match: Bot reply khi tin nhắn chứa từ khóa nhất định\nAny Message: Bot reply mọi tin nhắn đến (không cần keyword)">ℹ️</span></label><select id="ar-trigger-type" class="form-select" onchange="((document.getElementById('ar-keywords-group') && document.getElementById('ar-keywords-group')?.style) || me_dummy_style).display = this.value==='keyword'?'block':'none'"><option value="keyword">Keyword Match</option><option value="any">Any Message</option></select></div>
            <div class="form-group" id="ar-keywords-group"><label class="form-label">Keywords <span style="cursor:help;opacity:.6" title="Danh sách từ khóa, phân cách bởi dấu phẩy.\nKhi ai đó nhắn tin chứa 1 trong các từ này → bot sẽ tự động reply">ℹ️</span></label><input type="text" id="ar-keywords" class="form-input" placeholder="hello, hi, xin chào, hợp tác"></div>
            <div class="form-group"><label class="form-label">Tài khoản áp dụng <span style="cursor:help;opacity:.6" title="Chọn tài khoản Telegram nào sẽ tự động reply.\nKhi có người nhắn tin đến tài khoản được chọn → bot auto reply.\nBỏ trống = áp dụng cho TẤT CẢ tài khoản">ℹ️</span></label><div id="ar-accounts-list" style="display:flex;flex-wrap:wrap;gap:8px;margin-top:6px"><span style="color:var(--text2);font-size:12px">Đang tải...</span></div></div>
            <div class="form-group"><label class="form-label">Nội dung Reply <span style="cursor:help;opacity:.6" title="Tin nhắn bot sẽ tự động gửi lại.\nDùng --- (trên 1 dòng riêng) để tách thành nhiều tin nhắn.\nVD:\nChào bạn!\n---\nMình có thể giúp gì?">ℹ️</span></label><textarea id="ar-reply-content" class="form-textarea" rows="6" placeholder="Nhập tin nhắn reply...&#10;Dùng --- để tách nhiều tin"></textarea></div>
            <div class="form-group"><label class="form-label">Max replies per user <span style="cursor:help;opacity:.6" title="Số lần reply tối đa cho mỗi user.\nTránh spam: bot chỉ reply tối đa N lần cho cùng 1 người">ℹ️</span></label><input type="number" id="ar-max-replies" class="form-input" value="3" min="1" max="50"></div>
          </div>
          <div class="modal-footer"><button class="btn btn-primary" onclick="AutoReply.save()">💾 Lưu</button></div>
        </div>
      </div>

      <!-- Logs Modal -->
      <div id="ar-logs-modal" class="modal-overlay">
        <div class="modal" style="max-width:700px">
          <div class="modal-header"><h3 class="modal-title">Auto-Reply Logs</h3><button class="modal-close" onclick="(document.getElementById('ar-logs-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('open')">×</button></div>
          <div class="modal-body"><div class="table-wrap"><table><thead><tr><th>User</th><th>Trigger</th><th>Reply</th><th>Status</th><th>Time</th></tr></thead><tbody id="ar-logs-body"></tbody></table></div></div>
        </div>
      </div>
    </div>`;
    const w = document.createElement('div'); w.innerHTML = html;
    document.querySelector('.main').appendChild(w.firstElementChild);
  }
  document.querySelectorAll('[id^="view-"]').forEach(el=>el.classList.add('hidden'));
  (document.getElementById('view-autoreply')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('hidden');
  AutoReply.init();
  return;
}

// Warmup Nhom
if(page==='warmup'){
  if(!document.getElementById('view-warmup')){
    const html = `<div id="view-warmup">
      <h2 class="page-title">Warmup Nhom</h2>

      <!-- Stats -->
      <div class="stats-grid" id="warmup-stats" style="margin-bottom:20px"></div>

      <!-- Groups -->
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
        <h3 style="margin:0">Nhom</h3>
        <button class="btn btn-primary btn-sm" onclick="Warmup.openAddGroup()">+ Them nhom</button>
      </div>
      <div id="warmup-group-list"></div>

      <!-- Scripts (shown after selecting group) -->
      <div id="warmup-scripts-section" class="hidden" style="margin-top:24px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
          <h3 style="margin:0" id="warmup-scripts-title">Scripts</h3>
          <button class="btn btn-primary btn-sm" onclick="Warmup.openAddScript()">+ Them script</button>
        </div>
        <div id="warmup-script-list"></div>
      </div>

      <!-- Jobs -->
      <div style="margin-top:24px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
          <h3 style="margin:0">Jobs</h3>
          <button class="btn btn-primary btn-sm" onclick="Warmup.openAddJob()">+ Tao job</button>
        </div>
        <div id="warmup-job-list"></div>
      </div>

      <!-- Logs -->
      <div style="margin-top:24px">
        <h3>Logs gan day</h3>
        <div class="table-wrap">
          <table class="data-table"><thead><tr>
            <th>Thoi gian</th><th>Job</th><th>Account</th><th>Noi dung</th><th>Trang thai</th>
          </tr></thead><tbody id="warmup-log-body"></tbody></table>
        </div>
      </div>

      <!-- Add Group Modal -->
      <div id="warmup-group-modal" class="modal-overlay">
        <div class="modal">
          <div class="modal-header"><h3 class="modal-title">Them nhom warmup</h3><button class="modal-close" onclick="(document.getElementById('warmup-group-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('open')">x</button></div>
          <div class="modal-body">
            <div class="form-group"><label class="form-label">Ten nhom</label><input type="text" id="wg-name" class="form-input" placeholder="VD: Crypto VN"></div>
            <div class="form-group"><label class="form-label">Chat ID</label><input type="text" id="wg-chat-id" class="form-input" placeholder="ID nhom Telegram"></div>
            <div class="form-group"><label class="form-label">Tieu de</label><input type="text" id="wg-chat-title" class="form-input" placeholder="Tieu de nhom (tuy chon)"></div>
            <div class="form-group"><label class="form-label">Username</label><input type="text" id="wg-chat-username" class="form-input" placeholder="@username (tuy chon)"></div>
          </div>
          <div class="modal-footer"><button class="btn btn-primary" onclick="Warmup.saveGroup()">Luu</button></div>
        </div>
      </div>

      <!-- Add Script Modal -->
      <div id="warmup-script-modal" class="modal-overlay">
        <div class="modal">
          <div class="modal-header"><h3 class="modal-title">Them script</h3><button class="modal-close" onclick="(document.getElementById('warmup-script-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('open')">x</button></div>
          <div class="modal-body">
            <div class="form-group"><label class="form-label">Noi dung</label><textarea id="ws-content" class="form-textarea" rows="5" placeholder="Noi dung tin nhan warmup..."></textarea></div>
            <div class="form-group" style="display:flex;align-items:center;gap:8px">
              <input type="checkbox" id="ws-ai-remix" checked>
              <label for="ws-ai-remix" style="cursor:pointer">AI Remix</label>
            </div>
          </div>
          <div class="modal-footer"><button class="btn btn-primary" onclick="Warmup.saveScript()">Luu</button></div>
        </div>
      </div>

      <!-- Add Job Modal -->
      <div id="warmup-job-modal" class="modal-overlay">
        <div class="modal">
          <div class="modal-header"><h3 class="modal-title">Tao warmup job</h3><button class="modal-close" onclick="(document.getElementById('warmup-job-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('open')">x</button></div>
          <div class="modal-body">
            <div class="form-group"><label class="form-label">Nhom</label><select id="wj-group" class="form-select"></select></div>
            <div class="form-group"><label class="form-label">Tai khoan</label><div id="wj-accounts" style="display:flex;flex-wrap:wrap;gap:8px;margin-top:6px"></div></div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
              <div class="form-group"><label class="form-label">Interval min (phut)</label><input type="number" id="wj-interval-min" class="form-input" value="30"></div>
              <div class="form-group"><label class="form-label">Interval max (phut)</label><input type="number" id="wj-interval-max" class="form-input" value="120"></div>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
              <div class="form-group"><label class="form-label">Bat dau</label><input type="time" id="wj-start" class="form-input" value="09:00"></div>
              <div class="form-group"><label class="form-label">Ket thuc</label><input type="time" id="wj-end" class="form-input" value="22:00"></div>
            </div>
            <div class="form-group"><label class="form-label">Gioi han/ngay</label><input type="number" id="wj-daily-limit" class="form-input" value="10"></div>
          </div>
          <div class="modal-footer"><button class="btn btn-primary" onclick="Warmup.saveJob()">Tao</button></div>
        </div>
      </div>

      <!-- Job Logs Modal -->
      <div id="warmup-logs-modal" class="modal-overlay">
        <div class="modal" style="max-width:700px">
          <div class="modal-header"><h3 class="modal-title">Job Logs</h3><button class="modal-close" onclick="(document.getElementById('warmup-logs-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('open')">x</button></div>
          <div class="modal-body"><div class="table-wrap"><table><thead><tr><th>Thoi gian</th><th>Account</th><th>Noi dung</th><th>Trang thai</th></tr></thead><tbody id="warmup-job-logs-body"></tbody></table></div></div>
        </div>
      </div>

    </div>`;
    const w = document.createElement('div'); w.innerHTML = html;
    document.querySelector('.main').appendChild(w.firstElementChild);
  }
  document.querySelectorAll('[id^="view-"]').forEach(el=>el.classList.add('hidden'));
  (document.getElementById('view-warmup')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('hidden');
  Warmup.init();
  return;
}

// ── Changelog page ──
if(page==='changelog'){
  if(!document.getElementById('view-changelog')){
    const CHANGELOG = [
      {
        version: '2.8.0', date: '2026-08-01', tag: 'latest',
        changes: [
          { type: 'fix', text: 'Sửa lỗi campaign dừng sớm — chỉ gửi 66/1600+ members do accounts exhausted ghi đè status thành "completed"' },
          { type: 'fix', text: 'PeerFlood accounts giờ sẽ tự phục hồi sau cooldown 2-5 phút thay vì bị loại vĩnh viễn' },
          { type: 'fix', text: 'Daily limit không còn co rút khi accounts bị flood — dùng tổng sender ban đầu' },
          { type: 'improve', text: 'Thêm diagnostic logging chi tiết trước mỗi campaign: tổng members, đã gửi, cross-excluded, blacklisted' },
          { type: 'new', text: 'Thêm trang Changelog để theo dõi lịch sử cập nhật phần mềm' },
        ]
      },
      {
        version: '2.7.0', date: '2026-07-24',
        changes: [
          { type: 'new', text: 'AI Sales Agent — tự động phản hồi DM bằng AI để chốt deal & onboard khách hàng' },
          { type: 'new', text: 'Hỗ trợ đa nhà cung cấp AI: Gemini, Groq, OpenAI, DeepSeek, OpenAI-Compatible' },
          { type: 'improve', text: 'Auto-fallback AI provider — tự chuyển sang provider khác nếu provider chính thiếu key' },
          { type: 'fix', text: 'Sửa lỗi Settings không lưu được khi F5 (save_setting alias)' },
        ]
      },
      {
        version: '2.6.0', date: '2026-07-22',
        changes: [
          { type: 'new', text: 'AI Remix nội dung DM — mỗi tin nhắn được AI viết lại khác nhau chống spam' },
          { type: 'new', text: 'SpamBot pre-check — tự kiểm tra account bị spam-limited trước khi chạy campaign' },
          { type: 'improve', text: 'Cross-campaign dedup — loại trừ user đã nhận DM từ campaign/watcher khác' },
          { type: 'improve', text: 'Auto-join nhóm nếu account chưa join source group khi gửi DM' },
        ]
      },
      {
        version: '2.5.0', date: '2026-07-17',
        changes: [
          { type: 'new', text: 'Smart Template Rotation — xoay vòng variant template thông minh dựa trên hiệu suất' },
          { type: 'new', text: 'Warmup Module — tự join/chat nhóm để làm nóng account mới' },
          { type: 'new', text: 'Daily Summary — báo cáo tổng hợp cuối ngày tự động' },
          { type: 'new', text: 'Auto-Pause khi phát hiện account bị restricted' },
        ]
      },
      {
        version: '2.4.0', date: '2026-07-15',
        changes: [
          { type: 'new', text: 'Batch Scrape — scrape members từ nhiều nhóm cùng lúc' },
          { type: 'new', text: 'Invite Module — mời members vào nhóm/kênh chỉ định' },
          { type: 'improve', text: 'Cải thiện round-robin account selection với anti-ban backoff' },
        ]
      },
      {
        version: '2.3.0', date: '2026-07-12',
        changes: [
          { type: 'new', text: 'DM Campaign — gửi DM hàng loạt đến members đã scrape' },
          { type: 'new', text: 'Blacklist management — quản lý danh sách chặn user' },
          { type: 'new', text: 'Image randomization — thay đổi hash ảnh chống phát hiện trùng lặp' },
          { type: 'fix', text: 'Sửa lỗi FloodWait không đợi đúng thời gian Telegram yêu cầu' },
        ]
      },
      {
        version: '2.2.0', date: '2026-07-09',
        changes: [
          { type: 'new', text: 'Analytics Dashboard — thống kê hiệu suất gửi tin, biểu đồ trực quan' },
          { type: 'new', text: 'Template Library — lưu trữ & quản lý mẫu tin nhắn' },
          { type: 'new', text: 'Auto-Reply Chatbot — tự động trả lời keyword trong nhóm' },
        ]
      },
      {
        version: '2.1.0', date: '2026-07-04',
        changes: [
          { type: 'new', text: 'Discord Bot integration — quản lý bot Discord, keyword watchers' },
          { type: 'improve', text: 'Reactions Module — tự động react tin nhắn trong kênh/nhóm' },
        ]
      },
      {
        version: '2.0.0', date: '2026-06-20',
        changes: [
          { type: 'new', text: 'Thiết kế lại toàn bộ UI — Dark theme premium với Glassmorphism' },
          { type: 'new', text: 'Multi-account management — quản lý nhiều tài khoản Telegram' },
          { type: 'new', text: 'Scheduled posting — đặt lịch gửi tin theo múi giờ quốc gia' },
          { type: 'new', text: 'Keyword Watchers — theo dõi keyword & auto-DM members mới' },
          { type: 'new', text: 'Inbox — xem & quản lý tin nhắn đến từ tất cả accounts' },
        ]
      },
    ];

    const typeIcons = { new: '✨', fix: '🐛', improve: '⚡', security: '🔒', breaking: '💥' };
    const typeLabels = { new: 'Tính năng mới', fix: 'Sửa lỗi', improve: 'Cải thiện', security: 'Bảo mật', breaking: 'Breaking' };
    const typeBadgeClass = { new: 'cl-badge-new', fix: 'cl-badge-fix', improve: 'cl-badge-improve', security: 'cl-badge-security', breaking: 'cl-badge-breaking' };

    const entriesHtml = CHANGELOG.map((entry, idx) => {
      const changesHtml = entry.changes.map(c => `
        <div class="cl-change">
          <span class="cl-badge ${typeBadgeClass[c.type] || 'cl-badge-new'}">${typeIcons[c.type] || '📌'} ${typeLabels[c.type] || c.type}</span>
          <span class="cl-change-text">${c.text}</span>
        </div>
      `).join('');
      const tagHtml = entry.tag === 'latest' ? '<span class="cl-tag-latest">LATEST</span>' : '';
      return `
        <div class="cl-entry ${idx === 0 ? 'cl-entry-latest' : ''}">
          <div class="cl-entry-header">
            <div class="cl-version-row">
              <span class="cl-version">v${entry.version}</span>
              ${tagHtml}
            </div>
            <span class="cl-date">${entry.date}</span>
          </div>
          <div class="cl-changes">${changesHtml}</div>
        </div>
      `;
    }).join('');

    const clHtml = `<div id="view-changelog">
      <div class="cl-header">
        <div>
          <h2 class="page-title" style="margin:0">📝 Changelog</h2>
          <p class="cl-subtitle">Lịch sử cập nhật & phiên bản mới nhất</p>
        </div>
        <div class="cl-current-version">
          <span class="cl-cv-label">Phiên bản hiện tại</span>
          <span class="cl-cv-number">v${CHANGELOG[0].version}</span>
        </div>
      </div>
      <div class="cl-timeline">${entriesHtml}</div>
    </div>`;
    const w = document.createElement('div'); w.innerHTML = clHtml;
    document.querySelector('.main').appendChild(w.firstElementChild);
  }
  document.querySelectorAll('[id^="view-"]').forEach(el=>el.classList.add('hidden'));
  (document.getElementById('view-changelog')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('hidden');
  return;
}

document.querySelectorAll('[id^="view-"]').forEach(el=>el.classList.add('hidden'));
const viewEl=document.getElementById(`view-${page}`);
if(viewEl)viewEl.classList.remove('hidden');

if(page==='dashboard')this.loadDashboard();else if(page==='schedules')this.loadSchedules();else if(page==='accounts')this.loadAccounts();else if(page==='logs')this.loadLogs();else if(page==='watchers')this.loadWatchers();else if(page==='watcher-logs')this.loadWatcherLogs();else if(page==='channels')this.loadChannels();else if(page==='settings')this.loadSettings();else if(page==='reactions')Reactions.init();else if(page==='members')Members.init()},


async loadDashboard(){try{const[stats,sd]=await Promise.all([API.getStats(),API.getSchedules({active_only: true, limit: 10})]);

(document.getElementById('stat-accounts') || me_dummy).textContent = stats.total_accounts;(document.getElementById('stat-active') || document.createElement("div")).textContent=stats.active_schedules;(document.getElementById('stat-total') || document.createElement("div")).textContent=stats.total_schedules;(document.getElementById('stat-today') || document.createElement("div")).textContent=stats.today;(document.getElementById('stat-success') || document.createElement("div")).textContent=stats.success;(document.getElementById('stat-failed') || document.createElement("div")).textContent=stats.failed;

const active=sd.schedules.filter(s=>s.next_run);const tbody=document.getElementById('upcoming-body');

if(!active.length){tbody.innerHTML='<tr><td colspan="6" style="text-align:center;color:var(--text2);padding:24px">Không có lịch nào sắp tới</td></tr>';return}

tbody.innerHTML=active.map(s=>{const sends=s.max_sends?`${s.current_sends||0}/${s.max_sends}`:(s.current_sends||0);return`<tr><td>${esc(s.name)}</td><td>${esc(s.account_name||'—')}</td><td><span class="badge badge-blue">${s.schedule_type}</span></td><td>${s.time_of_day}</td><td>${formatDate(s.next_run)}</td><td>${sends}</td></tr>`}).join('')}catch(e){this.toast('Lỗi: '+e.message,'error')}},

async loadAccounts(){try{const d=await API.getAccounts();this.accounts=d.accounts;App._accounts=d.accounts;const grid=document.getElementById('accounts-grid');

if(!this.accounts.length){grid.innerHTML='<div class="empty-state"><div class="empty-state-icon">👤</div><p class="empty-state-text">Chưa có tài khoản nào</p></div>';return}

let aiAgents = [];
try { const agentData = await API.get('/api/ai-agents'); aiAgents = agentData.agents || []; } catch(agErr){}

grid.innerHTML = this.accounts.map(a => {
  const logged = a.is_logged_in;
  const isOff = Boolean(a.is_paused);
  const ui = a.user_info;
  const name = ui ? [ui.first_name, ui.last_name].filter(Boolean).join(' ') : a.name;
  const uname = ui ? `@${ui.username || ui.phone}` : `@${a.phone}`;
  const dmLimit = a.is_premium ? 50 : 10;
  const initial = name ? name.trim().charAt(0).toUpperCase() : 'T';

  const agentOptions = aiAgents.map(ag => 
    `<option value="${ag.id}" ${a.ai_agent_id === ag.id ? 'selected' : ''}>${ag.avatar_emoji || '🤖'} ${esc(ag.name)}</option>`
  ).join('');

  return `
  <div class="account-card ${isOff ? 'account-card-disabled' : ''}" data-account-id="${a.id}" style="background:var(--bg-card);border:1px solid ${isOff ? 'rgba(239,68,68,0.3)' : 'rgba(255,255,255,0.08)'};border-radius:12px;padding:16px;display:flex;flex-direction:column;gap:12px;position:relative;${isOff ? 'opacity:0.82;' : ''}">
    
    <!-- Top Header: Avatar + Info + Badges -->
    <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:10px">
      <div style="display:flex;align-items:center;gap:10px">
        <div style="position:relative;width:42px;height:42px;border-radius:50%;background:rgba(99,102,241,0.15);border:1px solid rgba(99,102,241,0.3);display:flex;align-items:center;justify-content:center;font-weight:700;font-size:16px;color:#818cf8">
          ${esc(initial)}
          <span style="position:absolute;bottom:0;right:0;width:11px;height:11px;border-radius:50%;background:${isOff ? '#ef4444' : (logged ? '#10b981' : '#f59e0b')};border:2px solid var(--bg-card)"></span>
        </div>
        <div>
          <div style="font-weight:700;font-size:15px;color:var(--text1);line-height:1.2">${esc(name)}</div>
          <div style="font-size:12px;color:var(--text2);margin-top:2px">${esc(uname)}</div>
        </div>
      </div>

      <div style="display:flex;flex-direction:column;align-items:flex-end;gap:4px">
        <span class="badge ${isOff ? 'badge-red' : (logged ? 'badge-green' : 'badge-amber')}" style="font-size:11px;padding:2px 8px">
          ${isOff ? '🔴 Đã tắt' : (logged ? '🟢 Online' : '🟠 Disconnected')}
        </span>
        <span style="cursor:pointer;background:${a.is_premium ? 'rgba(251,191,36,0.15)' : 'rgba(255,255,255,0.06)'};border:1px solid ${a.is_premium ? 'rgba(251,191,36,0.4)' : 'rgba(255,255,255,0.1)'};border-radius:4px;padding:1px 6px;font-size:11px;font-weight:600;color:${a.is_premium ? '#fbbf24' : '#9ca3af'}" onclick="App.togglePremium(${a.id}, ${!a.is_premium})" title="Click để chuyển trạng thái Premium (${a.is_premium ? 50 : 10}→${a.is_premium ? 10 : 50} DM/ngày)">
          ${a.is_premium ? '⭐ Premium' : '⬜ Thường'}
        </span>
        ${a.is_flagged ? `<span style="background:rgba(239,68,68,0.18);border:1px solid rgba(239,68,68,0.5);border-radius:4px;padding:1px 6px;font-size:11px;font-weight:600;color:#f87171;cursor:pointer" onclick="App.unflagAccount(${a.id})" title="${esc(a.flag_reason || '')}">⚠️ Cảnh báo</span>` : ''}
      </div>
    </div>

    <!-- Technical Meta Strip -->
    <div style="background:rgba(0,0,0,0.25);border:1px solid rgba(255,255,255,0.05);border-radius:8px;padding:8px 12px;display:flex;align-items:center;justify-content:space-between;font-size:12px;color:var(--text2)">
      <div>ID: <span style="color:var(--text1);font-family:monospace;font-weight:600">${a.api_id}</span></div>
      <div>Giới hạn: <span style="color:${a.is_premium ? '#fbbf24' : 'var(--text1)'};font-weight:600">${dmLimit} DM/ngày</span></div>
    </div>

    <!-- AI Agent Selector Box -->
    <div style="background:rgba(15,23,42,0.5);border:1px solid rgba(255,255,255,0.08);border-radius:8px;padding:10px 12px">
      <div style="font-size:11px;font-weight:600;color:var(--text2);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px">🤖 AI Agent Phụ Trách</div>
      <select class="form-input" style="width:100%;height:32px;font-size:12px;background:rgba(0,0,0,0.3);border:1px solid rgba(255,255,255,0.12);border-radius:6px;color:#f3f4f6;padding:0 8px" onchange="App.setAccountAiAgent(${a.id}, this.value)">
        <option value="">🚫 Tắt (Không dùng AI)</option>
        ${agentOptions}
      </select>
    </div>

    <!-- Action Buttons Row -->
    <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;padding-top:4px;border-top:1px solid rgba(255,255,255,0.06)">
      ${isOff ? 
        `<button class="btn btn-success btn-sm" style="flex:1" onclick="App.toggleAccountActive(${a.id}, true)" title="Bật lại tài khoản">▶️ Bật Account</button>` : 
        `<button class="btn btn-ghost btn-sm" style="flex:1;border:1px solid rgba(239,68,68,0.3);color:#f87171;background:rgba(239,68,68,0.08)" onclick="App.toggleAccountActive(${a.id}, false)" title="Tắt tạm dừng">⏸️ Tắt Account</button>`
      }
      ${!logged ? `<button class="btn btn-primary btn-sm" onclick="App.loginAccount(${a.id}, '${a.phone}')">🔑 Login</button>` : ''}
      <button class="btn btn-ghost btn-sm" style="color:#ef4444;border:1px solid rgba(239,68,68,0.2)" onclick="App.deleteAccount(${a.id})" title="Xóa tài khoản">🗑️ Xóa</button>
    </div>

  </div>`;
}).join('')}catch(e){this.toast(e.message,'error')}},

openAddAccountModal(){(document.getElementById('acc-step-info')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('hidden');(document.getElementById('acc-step-otp')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');(document.getElementById('acc-step-2fa')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');(document.getElementById('acc-phone') || me_dummy).value = '';const proxyEl=document.getElementById('acc-proxy');if(proxyEl)proxyEl.value='';(document.getElementById('account-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('open')},

closeAccountModal(){(document.getElementById('account-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('open')},

async toggleAccountActive(accId,isActive){const actionText=isActive?'Bật (ON)':'Tắt (OFF)';const confirmMsg=isActive?'Bật lại tài khoản này? Tài khoản sẽ bắt đầu nhận tin nhắn và chạy tự động hóa trở lại.':'Bạn có chắc muốn TẮT tài khoản này?\nKhi TẮT: Tất cả AI Agent, Chiến dịch DM, Keyword Watcher và Tự động hóa của tài khoản này sẽ BỊ NGẮT HOÀN TOÀN để tránh ảnh hưởng danh bạ khách hàng.';if(!await customConfirm(confirmMsg))return;try{const res=await API.toggleAccountActive(accId,isActive);this.toast(res.message||`Đã ${actionText} tài khoản`,isActive?'success':'error');this.loadAccounts();}catch(e){this.toast(e.message,'error')}},

async setAccountAiAgent(accId,agentId){try{const res=await API.setAccountAiAgent(accId,agentId);this.toast(res.message||'Đã cập nhật AI Agent cho tài khoản','success');this.loadAccounts();}catch(e){this.toast(e.message,'error')}},

async unflagAccount(accId){if(!await customConfirm('Bỏ cảnh báo tài khoản này?'))return;try{await fetch(`/api/auth/accounts/${accId}/unflag`,{method:'POST'});API.clearAccountsCache();this.toast('Đã bỏ cảnh báo','success');this.loadAccounts();}catch(e){this.toast(e.message,'error')}},

async unpauseAccount(accId){if(!await customConfirm('Bỏ tạm dừng tài khoản này? Tài khoản sẽ hoạt động lại bình thường.'))return;try{await fetch(`/api/auth/accounts/${accId}/unpause`,{method:'POST'});API.clearAccountsCache();this.toast('Đã bỏ tạm dừng','success');this.loadAccounts();}catch(e){this.toast(e.message,'error')}},

async _populateLogAccountFilter(){const sel=document.getElementById('log-filter-account');if(!sel)return;const accs=this.accounts||App._accounts||[];if(!sel.options.length||sel.options.length===1){while(sel.options.length>1)sel.remove(1);accs.forEach(a=>{const opt=document.createElement('option');opt.value=a.id;opt.textContent=a.name||a.phone;sel.appendChild(opt);});} },

async loadBlacklist(){try{const data=await fetch('/api/blacklist').then(r=>r.json());const tbody=document.getElementById('blacklist-body');if(!data.length){tbody.innerHTML='<tr><td colspan="5" style="text-align:center;color:var(--text2);padding:24px">Chưa có user nào trong blacklist</td></tr>';return;}tbody.innerHTML=data.map(b=>`<tr><td>${b.user_id||'—'}</td><td style="color:#a78bfa">${esc(b.username||'—')}</td><td style="color:var(--text2);font-size:.85rem">${esc(b.reason||'—')}</td><td style="font-size:.8rem">${formatDate(b.created_at)}</td><td><button onclick="App.removeBlacklist(${b.id})" style="background:rgba(239,68,68,.15);border:1px solid rgba(239,68,68,.4);color:#f87171;border-radius:6px;padding:3px 10px;cursor:pointer;font-size:.78rem">🗑️ Xóa</button></td></tr>`).join('');}catch(e){this.toast(e.message,'error')}},

showAddBlacklist(){const uid=prompt('Nhập User ID (số):');const uname=prompt('Nhập username (không cần @):');const reason=prompt('Lý do (tuỳ chọn):')||'';if(!uid&&!uname)return;this.addBlacklist(uid?parseInt(uid):null,uname||null,reason)},

async addBlacklist(userId,username,reason){try{await fetch('/api/blacklist',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:userId,username,reason})});this.toast('Đã thêm vào blacklist','success');this.loadBlacklist();}catch(e){this.toast(e.message,'error')}},

async removeBlacklist(id){if(!await customConfirm('Xóa user này khỏi blacklist?'))return;try{await fetch(`/api/blacklist/${id}`,{method:'DELETE'});this.toast('Đã xóa khỏi blacklist','success');this.loadBlacklist();}catch(e){this.toast(e.message,'error')}},

async addAccount(){const phone=(document.getElementById('acc-phone')?.value || "").trim();if(!phone)return this.toast('Nhập số điện thoại','error');const proxyUrl=(document.getElementById('acc-proxy')?.value || "").trim()||null;try{const r=await API.addAccount({phone,proxy_url:proxyUrl||null});this.loginAccountId=r.account_id;this.loginPhone=phone;const c=await API.sendCode(phone,r.account_id);this.phoneCodeHash=c.phone_code_hash;(document.getElementById('acc-step-info')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');(document.getElementById('acc-step-otp')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('hidden');this.toast('OTP đã gửi','success')}catch(e){this.toast(e.message||String(e),'error')}},

async verifyAccount(){const code=(document.getElementById('acc-code')?.value || "").trim();if(!code)return this.toast('Nhập OTP','error');

try{const r=await API.verify(this.loginPhone,code,this.phoneCodeHash,this.loginAccountId);if(r.needs_password){(document.getElementById('acc-step-otp')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');(document.getElementById('acc-step-2fa')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('hidden');return}

this.toast('Đăng nhập thành công!','success');this.closeAccountModal();this.loadAccounts()}catch(e){if(e.message.includes('2FA')){(document.getElementById('acc-step-otp')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');(document.getElementById('acc-step-2fa')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('hidden')}else this.toast(e.message,'error')}},

async verify2FAAccount(){const pw=document.getElementById('acc-password')?.value;try{await API.verify(this.loginPhone,(document.getElementById('acc-code')?.value || "").trim(),this.phoneCodeHash,this.loginAccountId,pw);this.toast('OK!','success');this.closeAccountModal();this.loadAccounts()}catch(e){this.toast(e.message,'error')}},

async loginAccount(id,phone){this.loginAccountId=id;this.loginPhone=phone;try{const c=await API.sendCode(phone,id);this.phoneCodeHash=c.phone_code_hash;this.openAddAccountModal();(document.getElementById('acc-step-info')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');(document.getElementById('acc-step-otp')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('hidden');this.toast('OTP đã gửi','success')}catch(e){this.toast(e.message,'error')}},

async togglePremium(accountId, makePremium) {
  try {
    const res = await API.togglePremium(accountId, makePremium);
    this.toast(res.message, 'success');
    this.loadAccounts();
  } catch(e) { this.toast(e.message, 'error'); }
},

async deleteAccount(id){if(!await customConfirm('Xóa tài khoản này? Tất cả lịch liên quan sẽ bị xóa.'))return;try{await API.deleteAccount(id);this.toast('Đã xóa','success');this.loadAccounts()}catch(e){this.toast(e.message,'error')}},

async loadSchedules(){try{const d=await API.getSchedules();this.schedules=d.schedules;const tbody=document.getElementById('schedules-body');const empty=document.getElementById('schedules-empty');

if(!this.schedules.length){tbody.innerHTML='';empty.classList.remove('hidden');return}

empty.classList.add('hidden');tbody.innerHTML=this.schedules.map(s=>{const sends=s.max_sends?`${s.current_sends||0}/${s.max_sends}`:(s.current_sends||0);

return`<tr><td><label class="toggle"><input type="checkbox" ${s.is_active?'checked':''} onchange="App.toggleSchedule(${s.id})"><span class="toggle-slider"></span></label></td><td><strong>${esc(s.name)}</strong>${s.blocked_count > 0 ? `<span title="Có ${s.blocked_count} target bị block do lỗi quá 3 lần" onclick="App.showBlockedTargets(${s.id})" style="cursor:pointer;margin-left:.4rem;background:rgba(239,68,68,.15);border:1px solid rgba(239,68,68,.4);color:#f87171;border-radius:4px;padding:1px 6px;font-size:.7rem;font-weight:600;">⛔ ${s.blocked_count} blocked</span>` : ''}</td><td><small>${esc(s.account_name||'—')}</small></td><td><span class="badge badge-blue">${s.schedule_type}</span></td><td>${s.time_of_day}${s.schedule_type==='weekly'?'<br><small style="color:var(--text2)">'+formatDays(s.days_of_week)+'</small>':''}</td><td>${(s.messages||[]).length}</td><td>${(s.targets||[]).length}</td><td>${sends}</td><td style="font-size:12px;color:var(--text2)">${s.next_run?formatDate(s.next_run):'—'}</td><td><div class="btn-group"><button class="btn btn-ghost btn-sm" onclick="App.editSchedule(${s.id})" title="Sửa">✏️</button><button class="btn btn-green btn-sm" onclick="App.previewSchedule(${s.id})" title="Preview">👁</button><button class="btn btn-ghost btn-sm" onclick="App.sendNow(${s.id})" title="Gửi ngay">🚀</button><button class="btn btn-ghost btn-sm" onclick="App.resetCount(${s.id})" title="Reset count">🔄</button><button class="btn btn-danger btn-sm" onclick="App.deleteSchedule(${s.id})" title="Xóa">🗑</button></div></td></tr>`}).join('')}catch(e){this.toast('Lỗi: '+e.message,'error')}},

async toggleSchedule(id){try{const r=await API.toggleSchedule(id);this.toast(r.is_active?'Đã bật':'Đã tắt','success');this.loadSchedules()}catch(e){this.toast(e.message,'error')}},

async showBlockedTargets(scheduleId) {
  try {
    const res = await API.getBlockedTargets(scheduleId);
    if (!res.count) { this.toast('Không có target nào bị block', 'info'); return; }
    const blocks = res.blocked;
    let html = `<div style="background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.3);border-radius:12px;padding:1.2rem;margin-bottom:1rem;">
      <div style="display:flex;align-items:center;gap:.6rem;margin-bottom:.8rem;">
        <span style="font-size:1.3rem;">⛔</span>
        <strong style="color:#f87171;font-size:.95rem;">Targets bị tắt do lỗi quá 3 lần (${blocks.length})</strong>
      </div>
      <table style="width:100%;border-collapse:collapse;font-size:.83rem;">
        <thead><tr style="border-bottom:1px solid rgba(255,255,255,.1)">
          <th style="text-align:left;padding:.4rem .6rem;color:var(--text-secondary)">Tài khoản</th>
          <th style="text-align:left;padding:.4rem .6rem;color:var(--text-secondary)">Nhóm/Kênh</th>
          <th style="text-align:center;padding:.4rem .6rem;color:var(--text-secondary)">Số lỗi</th>
          <th style="text-align:right;padding:.4rem .6rem;color:var(--text-secondary)">Hành động</th>
        </tr></thead><tbody>`;
    blocks.forEach(b => {
      html += `<tr style="border-bottom:1px solid rgba(255,255,255,.06)">
        <td style="padding:.5rem .6rem">${this._esc(b.account_name || 'Acc ' + b.account_id)}</td>
        <td style="padding:.5rem .6rem;color:#fbbf24">${this._esc(b.chat_title || 'Chat ' + b.chat_id)}</td>
        <td style="padding:.5rem .6rem;text-align:center;color:#f87171;font-weight:700">${b.fail_count}</td>
        <td style="padding:.5rem .6rem;text-align:right">
          <button onclick="App._unblockTarget(${scheduleId},${b.account_id},${b.chat_id},this)" 
                  style="background:rgba(34,197,94,.15);border:1px solid rgba(34,197,94,.4);color:#4ade80;border-radius:6px;padding:3px 10px;cursor:pointer;font-size:.78rem">
            🔓 Mở khóa
          </button>
        </td>
      </tr>`;
    });
    html += `</tbody></table>
      <p style="color:var(--text-secondary);font-size:.8rem;margin-top:.8rem;">
        💡 Sau khi mở khóa, hệ thống sẽ thử gửi lại vào lần chạy tiếp theo.
      </p></div>`;

    const div = document.createElement('div');
    div.id = 'blocked-targets-overlay';
    div.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:9999;display:flex;align-items:center;justify-content:center;padding:1rem';
    div.innerHTML = `<div style="background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:1.5rem;max-width:560px;width:100%;max-height:80vh;overflow-y:auto">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1.2rem">
        <h3 style="margin:0;font-size:1.05rem">⛔ Targets bị block</h3>
        <button onclick="document.getElementById('blocked-targets-overlay')?.remove()" 
                style="background:none;border:none;color:var(--text-secondary);font-size:1.5rem;cursor:pointer;line-height:1">×</button>
      </div>
      ${html}
      <div style="text-align:right;margin-top:1rem">
        <button onclick="document.getElementById('blocked-targets-overlay')?.remove()" class="btn btn-primary">Đóng</button>
      </div>
    </div>`;
    document.getElementById('blocked-targets-overlay')?.remove();
    document.body.appendChild(div);
  } catch(e) { this.toast('Lỗi: ' + e.message, 'error'); }
},

async _unblockTarget(scheduleId, accountId, chatId, btn) {
  try {
    btn.disabled = true; btn.textContent = '⏳';
    await API.unblockTarget(scheduleId, accountId, chatId);
    btn.closest('tr').style.opacity = '.4';
    btn.textContent = '✅ Đã mở';
    this.toast('Đã mở khóa! Target sẽ được gửi lại lần sau.', 'success');
    this.loadSchedules();
  } catch(e) { btn.disabled = false; btn.textContent = '🔓 Mở khóa'; this.toast(e.message, 'error'); }
},

async deleteSchedule(id){if(!await customConfirm('Xóa lịch này?'))return;try{await API.deleteSchedule(id);this.toast('Đã xóa','success');this.loadSchedules()}catch(e){this.toast(e.message,'error')}},

async sendNow(id){if(!await customConfirm('Gửi ngay?'))return;try{await API.sendNow(id);this.toast('Đã đưa vào hàng đợi','success')}catch(e){this.toast(e.message,'error')}},

async previewSchedule(id){try{const r=await API.previewSchedule(id);this.toast(r.message,'success')}catch(e){this.toast(e.message,'error')}},

async resetCount(id){try{await API.resetCount(id);this.toast('Đã reset','success');this.loadSchedules()}catch(e){this.toast(e.message,'error')}},

async openCreateModal(){(document.getElementById('modal-title') || me_dummy).textContent = 'Tạo lịch gửi';(document.getElementById('edit-schedule-id') || document.createElement("div")).value='';(document.getElementById('sch-name') || document.createElement("div")).value='';(document.getElementById('sch-type') || document.createElement("div")).value='daily';(document.getElementById('sch-time') || document.createElement("div")).value='08:00';(document.getElementById('sch-day-of-month') || document.createElement("div")).value='1';(document.getElementById('sch-once-date') || document.createElement("div")).value='';(document.getElementById('sch-max-sends') || document.createElement("div")).value='';document.querySelectorAll('.day-btn').forEach(b=>b.classList.remove('active'));(document.getElementById('messages-list') || document.createElement("div")).innerHTML='';

this.onScheduleTypeChange();await this.loadAccountSelector();await this.loadChatList();

document.querySelectorAll('#chat-list input[type="checkbox"]').forEach(c=>c.checked=false);(document.getElementById('schedule-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('open')},

async editSchedule(id){try{const s=await API.getSchedule(id);(document.getElementById('modal-title') || me_dummy).textContent = 'Sửa lịch gửi';(document.getElementById('edit-schedule-id') || document.createElement("div")).value=s.id;(document.getElementById('sch-name') || document.createElement("div")).value=s.name;(document.getElementById('sch-type') || document.createElement("div")).value=s.schedule_type;(document.getElementById('sch-time') || document.createElement("div")).value=s.time_of_day;(document.getElementById('sch-day-of-month') || document.createElement("div")).value=s.day_of_month||1;(document.getElementById('sch-once-date') || document.createElement("div")).value=s.once_date||'';(document.getElementById('sch-max-sends') || document.createElement("div")).value=s.max_sends||'';

document.querySelectorAll('.day-btn').forEach(b=>b.classList.remove('active'));if(s.days_of_week)s.days_of_week.split(',').forEach(d=>{const btn=document.querySelector(`.day-btn[data-day="${d.trim()}"]`);if(btn)btn.classList.add('active')});

this.onScheduleTypeChange();(document.getElementById('messages-list') || me_dummy).innerHTML = '';(s.messages||[]).forEach(m=>this.addMessage(m.msg_type,m));

await this.loadAccountSelector();(document.getElementById('sch-account') || me_dummy).value = s.account_id;await this.loadChatList();

const tids=new Set((s.targets||[]).map(t=>t.chat_id));document.querySelectorAll('#chat-list input[type="checkbox"]').forEach(cb=>cb.checked=tids.has(parseInt(cb.value)));

(document.getElementById('schedule-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('open')}catch(e){this.toast(e.message,'error')}},

closeModal(){(document.getElementById('schedule-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('open')},

onScheduleTypeChange(){const t=document.getElementById('sch-type')?.value;(document.getElementById('weekly-days-group')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).toggle('hidden',t!=='weekly');(document.getElementById('monthly-day-group')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).toggle('hidden',t!=='monthly');(document.getElementById('once-date-group')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).toggle('hidden',t!=='once');

const lbl=document.getElementById('time-label');if(lbl){lbl.textContent=t==='hourly'?'Phút gửi (mỗi giờ)':'Giờ gửi';}},

async loadAccountSelector(){try{const d=await API.getAccounts();this.accounts=d.accounts;const sel=document.getElementById('sch-account');if(sel)sel.innerHTML=this.accounts.map(a=>`<option value="${a.id}">${esc(accDisplayName(a))} (${a.phone})</option>`).join('')}catch(e){console.error(e)}},

async loadChatList(){const sel=document.getElementById('sch-account');const accId=sel?sel.value:1;const el=document.getElementById('chat-list');if(el)el.innerHTML='<div class="loading-overlay"><span class="spinner"></span> Đang tải...</div>';

try{const d=await API.getChats(accId);this.chats=d.chats;this.renderChatList(this.chats)}catch(e){el.innerHTML=`<div style="padding:12px;color:var(--red)">Lỗi: ${e.message}</div>`}},

renderChatList(chats){const el=document.getElementById('chat-list');if(!chats.length){el.innerHTML='<div style="padding:12px;color:var(--text2)">Không tìm thấy nhóm/kênh</div>';return}

el.innerHTML=chats.map(c=>{const icon=c.chat_type==='channel'?'📢':c.chat_type==='supergroup'?'👥':'💬';return`<label class="chat-item"><input type="checkbox" value="${c.chat_id}" data-title="${esc(c.chat_title)}" data-type="${c.chat_type}"><span class="chat-type-icon">${icon}</span><span class="chat-name">${esc(c.chat_title)}</span><span class="chat-type-badge">${c.chat_type}</span></label>`}).join('')},

filterChats: debounce(function(){const q=document.getElementById('chat-search')?.value.toLowerCase();this.renderChatList(this.chats.filter(c=>c.chat_title.toLowerCase().includes(q)))}, 250),

addMessage(type,data=null){const list=document.getElementById('messages-list');const div=document.createElement('div');div.className='msg-item';div.dataset.type=type;

let inner=`<div class="msg-item-header"><span class="msg-item-type">${type.toUpperCase()}</span><button class="msg-remove" onclick="this.closest('.msg-item').remove()">✕</button></div>`;

if(type==='text')inner+=`<textarea class="form-textarea msg-content" placeholder="Nội dung (hỗ trợ HTML)">${data?.content||''}</textarea>`;

else if(['photo','video','document'].includes(type))inner+=`<div class="form-group" style="margin-bottom:8px"><input type="file" class="form-input msg-file" onchange="App.handleFileUpload(this)"><input type="hidden" class="msg-media-path" value="${data?.media_path||''}">${data?.media_path?`<small style="color:var(--green)">✓ ${data.media_path.split('/').pop()}</small>`:''}</div><textarea class="form-textarea msg-content" placeholder="Caption" rows="2">${data?.content||''}</textarea>`;

else if(type==='poll'){const opts=data?.poll_options?JSON.parse(data.poll_options):['',''];inner+=`<div class="form-group" style="margin-bottom:8px"><input type="text" class="form-input msg-poll-question" placeholder="Câu hỏi" value="${data?.poll_question||''}"></div><div class="poll-options">${opts.map((o,i)=>`<div class="poll-option-row"><input type="text" class="form-input poll-opt" placeholder="Lựa chọn ${i+1}" value="${o}">${i>=2?'<button class="poll-option-remove" onclick="this.parentElement.remove()">✕</button>':''}</div>`).join('')}</div><button class="btn btn-ghost btn-sm" style="margin-top:6px" onclick="App.addPollOption(this)">+ Thêm</button><div style="margin-top:8px"><label style="font-size:12px;color:var(--text2);cursor:pointer"><input type="checkbox" class="msg-poll-multiple" ${data?.poll_multiple?'checked':''}> Cho phép chọn nhiều</label></div>`}

div.innerHTML=inner;list.appendChild(div)},

addPollOption(btn){const c=btn.previousElementSibling;const n=c.children.length;const r=document.createElement('div');r.className='poll-option-row';r.innerHTML=`<input type="text" class="form-input poll-opt" placeholder="Lựa chọn ${n+1}"><button class="poll-option-remove" onclick="this.parentElement.remove()">✕</button>`;c.appendChild(r)},

async handleFileUpload(input){const file=input.files[0];if(!file)return;try{const r=await API.upload(file);input.parentElement.querySelector('.msg-media-path').value=r.path;const ex=input.parentElement.querySelector('small');if(ex)ex.remove();const s=document.createElement('small');s.style.color='var(--green)';s.textContent=`✓ ${r.original_name}`;input.parentElement.appendChild(s);this.toast('Uploaded','success')}catch(e){this.toast('Upload lỗi: '+e.message,'error')}},

async saveSchedule(){const editId=document.getElementById('edit-schedule-id')?.value;const name=(document.getElementById('sch-name')?.value || "").trim();const type=document.getElementById('sch-type')?.value;const time=document.getElementById('sch-time')?.value;const accountId=parseInt(document.getElementById('sch-account')?.value);const maxSendsVal=(document.getElementById('sch-max-sends')?.value || "").trim();const maxSends=maxSendsVal?parseInt(maxSendsVal):null;

if(!name)return this.toast('Nhập tên lịch','error');if(!time)return this.toast('Chọn giờ','error');

let days_of_week=null;if(type==='weekly'){const sel=[...document.querySelectorAll('.day-btn.active')].map(b=>b.dataset.day);if(!sel.length)return this.toast('Chọn ít nhất 1 ngày','error');days_of_week=sel.join(',')}

let day_of_month=null;if(type==='monthly')day_of_month=parseInt(document.getElementById('sch-day-of-month')?.value)||1;

let once_date=null;if(type==='once'){once_date=document.getElementById('sch-once-date')?.value;if(!once_date)return this.toast('Chọn ngày','error')}

const targets=[];document.querySelectorAll('#chat-list input[type="checkbox"]:checked').forEach(cb=>targets.push({chat_id:parseInt(cb.value),chat_title:cb.dataset.title,chat_type:cb.dataset.type}));

if(!targets.length)return this.toast('Chọn ít nhất 1 đích','error');

const messages=[];document.querySelectorAll('#messages-list .msg-item').forEach((item,i)=>{const mt=item.dataset.type;const msg={msg_order:i,msg_type:mt};

if(mt==='text'){msg.content=item.querySelector('.msg-content').value;if(!msg.content.trim())return}

else if(['photo','video','document'].includes(mt)){msg.media_path=item.querySelector('.msg-media-path').value;msg.content=item.querySelector('.msg-content').value;if(!msg.media_path)return}

else if(mt==='poll'){msg.poll_question=item.querySelector('.msg-poll-question').value;const opts=[...item.querySelectorAll('.poll-opt')].map(i=>i.value).filter(Boolean);if(opts.length<2||!msg.poll_question)return;msg.poll_options=JSON.stringify(opts);msg.poll_multiple=item.querySelector('.msg-poll-multiple')?.checked||false}

messages.push(msg)});

if(!messages.length)return this.toast('Thêm ít nhất 1 tin nhắn','error');

const payload={account_id:accountId,name,schedule_type:type,time_of_day:time,days_of_week,day_of_month,once_date,max_sends:maxSends,is_active:true,messages,targets};

const btn=document.getElementById('btn-save-schedule');if(btn){btn.disabled=true;btn.textContent='Đang lưu...';}

try{if(editId)await API.updateSchedule(editId,payload);else await API.createSchedule(payload);this.toast(editId?'Đã cập nhật':'Đã tạo mới','success');this.closeModal();this.loadSchedules()}catch(e){this.toast(e.message,'error')}

btn.disabled=false;btn.textContent='Lưu lịch'},

async loadLogs(){const status=document.getElementById('log-filter-status')?.value;try{const d=await API.getLogs({limit:this.logLimit,offset:this.logOffset,...(status?{status}:{})});const tbody=document.getElementById('logs-body');

if(!d.logs.length){tbody.innerHTML='<tr><td colspan="6" style="text-align:center;color:var(--text2);padding:24px">Chưa có log</td></tr>';return}

tbody.innerHTML=d.logs.map(l=>`<tr><td style="font-size:12px">${formatDate(l.sent_at)}</td><td>${l.schedule_id}</td><td style="color:#a78bfa;font-weight:600">${esc(l.account_name||('Acc '+l.account_id))}</td><td>${esc(l.chat_title||String(l.chat_id))}</td><td><span class="badge ${l.status==='success'?'badge-green':'badge-red'}">${l.status}</span></td><td style="font-size:12px;color:var(--text2);max-width:200px;overflow:hidden;text-overflow:ellipsis">${esc(l.error_message||'—')}</td></tr>`).join('');

const pagEl=document.getElementById('logs-pagination');const pages=Math.ceil(d.total/this.logLimit);const cur=Math.floor(this.logOffset/this.logLimit);

if(pages>1){let h='';if(cur>0)h+=`<button class="btn btn-ghost btn-sm" onclick="App.logPage(${cur-1})">← Trước</button>`;h+=`<span style="color:var(--text2);font-size:12px">Trang ${cur+1}/${pages}</span>`;if(cur<pages-1)h+=`<button class="btn btn-ghost btn-sm" onclick="App.logPage(${cur+1})">Sau →</button>`;pagEl.innerHTML=h}else pagEl.innerHTML=''}catch(e){this.toast('Lỗi: '+e.message,'error')}},

logPage(p){this.logOffset=p*this.logLimit;this.loadLogs()},



// ── Watcher ──────────────────────────────────────────────────────

_watcherKeywords:[],_watcherExcludes:[],_watcherChats:[],_watcherSelectedGroups:new Set(),_watcherAccountOrder:[],_wlOffset:0,_wlLimit:40,

async loadWatchers(){try{

  const[ws,stats]=await Promise.all([API.getWatchers(),API.getWatcherStats()]);

  (document.getElementById('ws-active') || me_dummy).textContent = stats.active_watchers||0;

  (document.getElementById('ws-success') || me_dummy).textContent = stats.success||0;

  (document.getElementById('ws-today') || me_dummy).textContent = stats.today||0;

  (document.getElementById('ws-failed') || me_dummy).textContent = stats.failed||0;

  const tbody=document.getElementById('watchers-body');

  const empty=document.getElementById('watchers-empty');

  if(!ws.length){tbody.innerHTML='';empty.classList.remove('hidden');return}

  empty.classList.add('hidden');

  const accs=await API.getAccounts();this.accounts=accs.accounts;

  const accMap=Object.fromEntries(this.accounts.map(a=>[a.id,a]));

  tbody.innerHTML=ws.map(w=>{

    const kws=w.keywords.map(k=>`<span class="badge badge-blue" style="font-size:11px">${esc(k)}</span>`).join(' ');

    const grpCount=w.group_ids.length;

    const accNames=(w.sender_account_ids||[]).map(id=>accMap[id]?esc(accDisplayName(accMap[id])):'?').join(', ');

    const dmOnceBadge=w.dm_once?'<span class="badge badge-red" style="font-size:10px;margin-left:4px">🔒 1 lần</span>':'';

    return`<tr><td><label class="toggle"><input type="checkbox" ${w.is_active?'checked':''} onchange="App.toggleWatcher(${w.id})"><span class="toggle-slider"></span></label></td><td><strong>${esc(w.name)}</strong>${dmOnceBadge}</td><td style="max-width:200px">${kws}</td><td>${grpCount} nhóm</td><td style="font-size:12px">${accNames}</td><td>${w.dm_once?'∞ (1 lần)':w.cooldown_hours+'h'}</td><td><div class="btn-group"><button class="btn btn-ghost btn-sm" onclick="App.openTestDM(${w.id})" title="Test DM">🧪</button><button class="btn btn-ghost btn-sm" onclick="App.editWatcher(${w.id})" title="Sửa">✏️</button><button class="btn btn-danger btn-sm" onclick="App.deleteWatcher(${w.id})" title="Xóa">🗑</button></div></td></tr>`}).join('')

}catch(e){this.toast('Lỗi: '+e.message,'error')}},

async openWatcherModal(){

  (document.getElementById('watcher-modal-title') || me_dummy).textContent = 'Tạo Keyword DM Rule';

  (document.getElementById('edit-watcher-id') || me_dummy).value = '';

  (document.getElementById('w-name') || me_dummy).value = '';

  (document.getElementById('w-cooldown') || me_dummy).value = '24';

  (document.getElementById('w-dm-once') || me_dummy).checked = false;
  (document.getElementById('w-reply-in-group') || me_dummy).checked = false;
  (document.getElementById('w-group-reply-text') || me_dummy).value = 'Check my DM 😊';
  ((document.getElementById('w-group-reply-section') && document.getElementById('w-group-reply-section')?.style) || me_dummy_style).display = 'none';

  (document.getElementById('w-messages-list') || me_dummy).innerHTML = '';

  this._watcherKeywords=[];this._watcherExcludes=[];this._watcherSelectedGroups=new Set();

  this._watcherActiveAccountId=null;

  this._renderWatcherKeywords();this._renderWatcherExcludes();

  await this._loadWatcherAccounts([]);await this._loadWatcherChatList([]);

  (document.getElementById('watcher-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('open')},

async editWatcher(id){try{

  const w=await API.getWatcher(id);

  (document.getElementById('watcher-modal-title') || me_dummy).textContent = 'Sửa Keyword DM Rule';

  (document.getElementById('edit-watcher-id') || me_dummy).value = w.id;

  (document.getElementById('w-name') || me_dummy).value = w.name;

  (document.getElementById('w-cooldown') || me_dummy).value = w.cooldown_hours||24;

  (document.getElementById('w-dm-once') || me_dummy).checked = !!w.dm_once;
  (document.getElementById('w-reply-in-group') || me_dummy).checked = !!w.reply_in_group;
  (document.getElementById('w-group-reply-text') || me_dummy).value = w.group_reply_text||'Check my DM 😊';
  ((document.getElementById('w-group-reply-section') && document.getElementById('w-group-reply-section')?.style) || me_dummy_style).display = w.reply_in_group?'block':'none';

  this._watcherKeywords=[...w.keywords];this._renderWatcherKeywords();

  this._watcherExcludes=[...(w.excluded_usernames||[])];this._renderWatcherExcludes();

  this._watcherSelectedGroups=new Set(w.group_ids.map(Number));

  (document.getElementById('w-messages-list') || me_dummy).innerHTML = '';

  (w.messages||[]).forEach(m=>this.addWatcherMessage(m.msg_type,m));

  this._watcherActiveAccountId = w.sender_account_ids && w.sender_account_ids.length ? w.sender_account_ids[0] : null;

  await this._loadWatcherAccounts(w.sender_account_ids||[]);await this._loadWatcherChatList(w.group_ids||[]);

  (document.getElementById('watcher-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('open')

}catch(e){this.toast(e.message,'error')}},

closeWatcherModal(){(document.getElementById('watcher-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('open')},

async _loadWatcherAccounts(selected=[]){try{

  if(!this.accounts.length){const d=await API.getAccounts();this.accounts=d.accounts;}

  if(!this._watcherActiveAccountId && this.accounts.length){

    this._watcherActiveAccountId = selected.length ? selected[0] : this.accounts[0].id;

  }

  this._renderWatcherAccounts(selected);

}catch(e){console.error(e)}},

_renderWatcherAccounts(selectedIds){

  const container=document.getElementById('w-accounts-list');

  container.innerHTML=this.accounts.map(a=>{

    const chk=selectedIds.includes(a.id)?'checked':'';

    const isActive = a.id === this._watcherActiveAccountId;

    const borderStyle = isActive ? '1px solid var(--accent)' : '1px solid var(--border)';

    const bgStyle = isActive ? 'rgba(91, 141, 239, 0.12)' : 'var(--bg)';

    return `<div onclick="App.selectWatcherAccount(${a.id})" style="display:flex;align-items:center;gap:6px;background:${bgStyle};border:${borderStyle};border-radius:8px;padding:6px 10px;cursor:pointer;font-size:13px;transition:all 0.15s">

      <input type="checkbox" value="${a.id}" ${chk} style="accent-color:var(--accent)" onclick="event.stopPropagation(); App.toggleWatcherAccount(${a.id}, this)">

      <span>${esc(accDisplayName(a))}</span>

      <small style="color:var(--text2);margin-left:4px">${a.phone}</small>

    </div>`;

  }).join('')

},

async selectWatcherAccount(accId){

  this._watcherActiveAccountId = accId;

  const checkedIds = [...document.querySelectorAll('#w-accounts-list input[type="checkbox"]:checked')].map(cb => parseInt(cb.value));

  this._renderWatcherAccounts(checkedIds);

  const el=document.getElementById('w-chat-list');

  el.innerHTML='<div class="loading-overlay"><span class="spinner"></span> Đang tải...</div>';

  try{

    const d=await API.getChats(accId);

    this._watcherChats=d.chats;

    this._renderWatcherChatList(this._watcherChats)

  }catch(e){

    el.innerHTML=`<div style="padding:12px;color:var(--red)">Lỗi: ${e.message}</div>`

  }

},

toggleWatcherAccount(accId, cb){

  if(cb.checked){

    this.selectWatcherAccount(accId);

  } else {

    if(this._watcherActiveAccountId === accId){

      const checkedBoxes = document.querySelectorAll('#w-accounts-list input[type="checkbox"]:checked');

      if(checkedBoxes.length > 0){

        const checkedIds = [...checkedBoxes].map(c => parseInt(c.value));

        this.selectWatcherAccount(checkedIds[0]);

      }

    }

  }

},

async _loadWatcherChatList(selectedIds=[]){const el=document.getElementById('w-chat-list');if(el)el.innerHTML='<div class="loading-overlay"><span class="spinner"></span> Đang tải...</div>';

  const selSet=new Set(selectedIds.map(Number));this._watcherSelectedGroups=new Set(selSet);

  try{

    const accId=this._watcherActiveAccountId||(this.accounts[0]?.id||1);

    const d=await API.getChats(accId);this._watcherChats=d.chats;

    this._renderWatcherChatList(this._watcherChats,selSet)

  }catch(e){el.innerHTML=`<div style="padding:12px;color:var(--red)">Lỗi: ${e.message}</div>`}},

_renderWatcherChatList(chats,preSelected=null){

  const sel=preSelected||this._watcherSelectedGroups;

  const el=document.getElementById('w-chat-list');

  if(!chats.length){el.innerHTML='<div style="padding:12px;color:var(--text2)">Không tìm thấy nhóm</div>';return}

  el.innerHTML=chats.map(c=>{const icon=c.chat_type==='channel'?'📢':c.chat_type==='supergroup'?'👥':'💬';const chk=sel.has(Number(c.chat_id))?'checked':'';return`<label class="chat-item"><input type="checkbox" value="${c.chat_id}" data-title="${esc(c.chat_title)}" ${chk} onchange="App._onWatcherGroupToggle(this)"><span class="chat-type-icon">${icon}</span><span class="chat-name">${esc(c.chat_title)}</span><span class="chat-type-badge">${c.chat_type}</span></label>`}).join('')},

_onWatcherGroupToggle(cb){const id=parseInt(cb.value);if(cb.checked)this._watcherSelectedGroups.add(id);else this._watcherSelectedGroups.delete(id)},

filterWatcherChats: debounce(function(){const q=document.getElementById('w-chat-search')?.value.toLowerCase();this._renderWatcherChatList(this._watcherChats.filter(c=>c.chat_title.toLowerCase().includes(q)))}, 250),

addWatcherKeyword(){const inp=document.getElementById('w-keyword-input');const v=inp.value.trim();if(!v)return;if(!this._watcherKeywords.includes(v))this._watcherKeywords.push(v);inp.value='';this._renderWatcherKeywords()},

_renderWatcherKeywords(){const c=document.getElementById('w-keywords-tags');if(c)c.innerHTML=this._watcherKeywords.map((k,i)=>`<span style="display:inline-flex;align-items:center;gap:4px;background:var(--accent);color:#fff;border-radius:20px;padding:3px 10px;font-size:12px">${esc(k)}<button onclick="App._removeWatcherKeyword(${i})" style="background:none;border:none;color:#fff;cursor:pointer;font-size:14px;line-height:1">×</button></span>`).join('')},

_removeWatcherKeyword(i){this._watcherKeywords.splice(i,1);this._renderWatcherKeywords()},

addWatcherExclude(){const inp=document.getElementById('w-exclude-input');const v=inp.value.replace('@','').trim().toLowerCase();if(!v)return;if(!this._watcherExcludes.includes(v))this._watcherExcludes.push(v);inp.value='';this._renderWatcherExcludes()},

_renderWatcherExcludes(){const c=document.getElementById('w-exclude-tags');if(!c)return;c.innerHTML=this._watcherExcludes.map((u,i)=>`<span style="display:inline-flex;align-items:center;gap:4px;background:#e53e3e;color:#fff;border-radius:20px;padding:3px 10px;font-size:12px">🚫 @${esc(u)}<button onclick="App._removeWatcherExclude(${i})" style="background:none;border:none;color:#fff;cursor:pointer;font-size:14px;line-height:1">×</button></span>`).join('')},

_removeWatcherExclude(i){this._watcherExcludes.splice(i,1);this._renderWatcherExcludes()},

addWatcherMessage(type,data=null){

  const list=document.getElementById('w-messages-list');

  const div=document.createElement('div');div.className='msg-item';div.dataset.type=type;

  let inner=`<div class="msg-item-header"><span class="msg-item-type">${type.toUpperCase()}</span><button class="msg-remove" onclick="this.closest('.msg-item').remove()">✕</button></div>`;

  if(type==='text')inner+=`<textarea class="form-textarea msg-content" placeholder="Nội dung DM">${data?.content||''}</textarea>`;

  else if(['photo','video','document'].includes(type))inner+=`<div class="form-group" style="margin-bottom:8px"><input type="file" class="form-input msg-file" onchange="App.handleFileUpload(this)"><input type="hidden" class="msg-media-path" value="${data?.media_path||''}">${data?.media_path?`<small style="color:var(--green)">✓ ${data.media_path.split('/').pop()}</small>`:''}</div><textarea class="form-textarea msg-content" placeholder="Caption" rows="2">${data?.content||''}</textarea>`;

  div.innerHTML=inner;list.appendChild(div)},

async saveWatcher(){

  const editId=document.getElementById('edit-watcher-id')?.value;

  const name=(document.getElementById('w-name')?.value || "").trim();

  const cooldown=parseInt(document.getElementById('w-cooldown')?.value)||24;

  if(!name)return this.toast('Nhập tên rule','error');

  if(!this._watcherKeywords.length)return this.toast('Thêm ít nhất 1 từ khóa','error');

  if(!this._watcherSelectedGroups.size)return this.toast('Chọn ít nhất 1 nhóm','error');

  const accIds=[...document.querySelectorAll('#w-accounts-list input[type="checkbox"]:checked')].map(cb=>parseInt(cb.value));

  if(!accIds.length)return this.toast('Chọn ít nhất 1 tài khoản','error');

  const messages=[];document.querySelectorAll('#w-messages-list .msg-item').forEach((item,i)=>{const mt=item.dataset.type;const msg={msg_order:i,msg_type:mt};

    if(mt==='text'){msg.content=item.querySelector('.msg-content').value;if(!msg.content.trim())return}

    else if(['photo','video','document'].includes(mt)){msg.media_path=item.querySelector('.msg-media-path').value;msg.content=item.querySelector('.msg-content').value;if(!msg.media_path)return}

    messages.push(msg)});

  if(!messages.length)return this.toast('Thêm ít nhất 1 tin nhắn DM','error');

  const dmOnce=document.getElementById('w-dm-once')?.checked;
  const replyInGroup=document.getElementById('w-reply-in-group')?.checked;
  const groupReplyText=(document.getElementById('w-group-reply-text')?.value || "").trim()||'Check my DM 😊';

  const payload={name,sender_account_ids:accIds,keywords:this._watcherKeywords,excluded_usernames:this._watcherExcludes,group_ids:[...this._watcherSelectedGroups],cooldown_hours:cooldown,dm_once:dmOnce,reply_in_group:replyInGroup,group_reply_text:groupReplyText,is_active:1,messages};

  try{
    const savedId = editId ? parseInt(editId) : null;
    if(editId) await API.updateWatcher(editId,payload); else { const r = await API.createWatcher(payload); }
    this.toast(editId?'Đã cập nhật rule':'Đã tạo rule mới','success');
    this.closeWatcherModal();
    this.loadWatchers();
    // ── Membership check: warn if any account not in the group ──────────────
    try {
      const chkRes = await API.checkMembership(accIds, [...this._watcherSelectedGroups]);
      if(chkRes && chkRes.warnings && chkRes.warnings.length > 0) {
        this._showMembershipWarning(chkRes.warnings, payload.name);
      }
    } catch(_) {}
  }catch(e){this.toast(e.message,'error')}},

_showMembershipWarning(warnings, ruleName) {
  // Build the warning modal content
  let html = `<div style="background:rgba(251,146,60,.08);border:1px solid rgba(251,146,60,.4);border-radius:12px;padding:1.2rem;margin-bottom:1rem;">
    <div style="display:flex;align-items:center;gap:.6rem;margin-bottom:.8rem;">
      <span style="font-size:1.4rem;">⚠️</span>
      <strong style="color:#fb923c;font-size:1rem;">Cảnh báo: Tài khoản chưa join nhóm</strong>
    </div>
    <p style="color:var(--text-secondary);font-size:.88rem;margin-bottom:.8rem;">
      Rule <strong style="color:var(--text)">"${ruleName}"</strong> có tài khoản chưa tham gia nhóm đang theo dõi. 
      Các tài khoản này sẽ không thể DM user từ nhóm đó.
    </p>`;
  warnings.forEach(w => {
    html += `<div style="background:rgba(0,0,0,.2);border-radius:8px;padding:.7rem .9rem;margin-bottom:.5rem;">
      <div style="font-weight:600;color:#fbbf24;margin-bottom:.3rem;">📱 ${this._esc(w.account_name)} (ID: ${w.account_id})</div>`;
    w.missing_groups.forEach(g => {
      const btnId = `join-btn-${w.account_id}-${g.group_id}`;
      html += `<div style="display:flex;align-items:center;gap:.5rem;padding-left:.5rem;margin-bottom:2px;">
        <span style="color:var(--text-secondary);font-size:.83rem;flex:1;">• Chưa join: <span style="color:#f87171">${this._esc(g.group_title)}</span></span>
        <button id="${btnId}" onclick="App._joinFromWarning(this,${w.account_id},'${g.group_id}')"
          style="background:#6d28d9;color:#fff;border:none;border-radius:6px;padding:2px 10px;font-size:.75rem;cursor:pointer;white-space:nowrap;font-weight:600;">
          Join
        </button>
      </div>`;
    });
    html += `</div>`;
  });
  // "Join tất cả" bulk button
  html += `<div style="margin-top:.8rem;display:flex;align-items:center;gap:.6rem;">
    <button id="join-all-missing-btn" onclick="App._joinAllMissing()" 
      style="background:#6d28d9;color:#fff;border:none;border-radius:8px;padding:6px 16px;font-size:.85rem;cursor:pointer;font-weight:600;">
      🚀 Join tất cả
    </button>
    <span style="color:var(--text-secondary);font-size:.82rem;">Tự động join tất cả nhóm còn thiếu</span>
  </div></div>`;

  // Store warnings data for bulk join
  this._membershipWarnings = warnings;

  // Show in a modal
  const modal = document.getElementById('membership-warn-modal');
  if(modal) {
    (document.getElementById('membership-warn-body') || me_dummy).innerHTML = html;
    modal.classList.add('open');
  } else {
    // Fallback: create and show ad-hoc modal
    const div = document.createElement('div');
    div.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:9999;display:flex;align-items:center;justify-content:center;padding:1rem';
    div.innerHTML = `<div style="background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:1.5rem;max-width:500px;width:100%;max-height:80vh;overflow-y:auto">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem">
        <h3 style="margin:0;font-size:1.1rem">Cảnh báo Membership</h3>
        <button onclick="this.closest('[style*=fixed]').remove()" style="background:none;border:none;color:var(--text-secondary);font-size:1.4rem;cursor:pointer">×</button>
      </div>
      ${html}
      <div style="text-align:right;margin-top:1rem">
        <button onclick="this.closest('[style*=fixed]').remove()" class="btn btn-primary">Đã hiểu</button>
      </div>
    </div>`;
    document.body.appendChild(div);
  }
},

async _joinFromWarning(btn, accountId, groupId) {
  btn.disabled = true;
  btn.textContent = '⏳';
  btn.style.background = '#555';
  try {
    await API.joinChannel(accountId, groupId);
    btn.textContent = '✅ Joined';
    btn.style.background = '#16a34a';
  } catch(e) {
    btn.textContent = '❌ Lỗi';
    btn.style.background = '#dc2626';
    btn.title = e.message || 'Join failed';
    setTimeout(() => { btn.disabled = false; btn.textContent = 'Join'; btn.style.background = '#6d28d9'; }, 3000);
  }
},

async _joinAllMissing() {
  const btn = document.getElementById('join-all-missing-btn');
  if(!btn || !this._membershipWarnings) return;
  btn.disabled = true;
  btn.textContent = '⏳ Đang join...';
  btn.style.background = '#555';
  let ok = 0, fail = 0;
  for(const w of this._membershipWarnings) {
    for(const g of w.missing_groups) {
      const itemBtn = document.getElementById(`join-btn-${w.account_id}-${g.group_id}`);
      if(itemBtn && itemBtn.textContent.includes('✅')) continue; // Already joined
      if(itemBtn) { itemBtn.disabled = true; itemBtn.textContent = '⏳'; itemBtn.style.background = '#555'; }
      try {
        await API.joinChannel(w.account_id, String(g.group_id));
        ok++;
        if(itemBtn) { itemBtn.textContent = '✅ Joined'; itemBtn.style.background = '#16a34a'; }
      } catch(e) {
        fail++;
        if(itemBtn) { itemBtn.textContent = '❌ Lỗi'; itemBtn.style.background = '#dc2626'; itemBtn.title = e.message || ''; }
      }
      await new Promise(r => setTimeout(r, 2000)); // Delay between joins
    }
  }
  btn.textContent = `✅ Xong (${ok} joined, ${fail} lỗi)`;
  btn.style.background = ok > 0 ? '#16a34a' : '#dc2626';
  if(ok > 0) this.toast(`Đã join ${ok} nhóm thành công${fail ? `, ${fail} lỗi` : ''}`, 'success');
},


_esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')},

async toggleWatcher(id){try{const r=await API.toggleWatcher(id);this.toast(r.is_active?'Đã bật':'Đã tắt','success');this.loadWatchers()}catch(e){this.toast(e.message,'error')}},

async deleteWatcher(id){if(!await customConfirm('Xóa rule này?'))return;try{await API.deleteWatcher(id);this.toast('Đã xóa','success');this.loadWatchers()}catch(e){this.toast(e.message,'error')}},



async loadSettings(){

  try{

    const [provRes, gKeys, dsKeys] = await Promise.all([

      API.getSetting('ai_provider'),

      API.getSetting('ai_keys_gemini'),

      API.getSetting('ai_keys_deepseek')

    ]);

    const provider = provRes.value || '';

    const geminiKeys = JSON.parse(gKeys.value || '[]');

    const deepseekKeys = JSON.parse(dsKeys.value || '[]');



    (document.getElementById('ai-provider-select') || me_dummy).value = provider;

    this._renderAiKeysList('gemini', geminiKeys);

    this._renderAiKeysList('deepseek', deepseekKeys);

    this.onProviderChange();

  }catch(e){this.toast('Lỗi tải cài đặt: ' + e.message, 'error')}

},

onProviderChange(){

  const p = document.getElementById('ai-provider-select')?.value;

  (document.getElementById('ai-gemini-section')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).toggle('hidden', p !== 'gemini');

  (document.getElementById('ai-deepseek-section')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).toggle('hidden', p !== 'deepseek');

  (document.getElementById('test-remix-result')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');

},

_renderAiKeysList(provider, keys){

  const container = document.getElementById(provider + '-keys-list');

  if(!container) return;

  container.innerHTML = '';

  (keys.length ? keys : ['']).forEach((k, i) => {

    const row = document.createElement('div');

    row.style.cssText = 'display:flex;gap:8px;align-items:center';

    row.innerHTML = `

      <input type="password" class="form-input ai-key-input" data-provider="${provider}" data-idx="${i}"

        value="${esc(k)}" placeholder="API Key ${i+1}"

        style="flex:1;font-family:monospace;font-size:12px">

      <button class="btn btn-ghost btn-sm" onclick="App.toggleKeyVisibility(this)" title="Hiện/Ẩn">👁</button>

      <button class="btn btn-danger btn-sm" onclick="App.removeAiKeyRow('${provider}', ${i})" title="Xóa">✕</button>`;

    container.appendChild(row);

  });

},

addAiKeyRow(provider){

  const keys = this._collectKeys(provider);

  keys.push('');

  this._renderAiKeysList(provider, keys);

  // Focus last input

  const inputs = document.querySelectorAll(`#${provider}-keys-list .ai-key-input`);

  if(inputs.length) inputs[inputs.length-1].focus();

},

removeAiKeyRow(provider, idx){

  const keys = this._collectKeys(provider);

  keys.splice(idx, 1);

  this._renderAiKeysList(provider, keys.length ? keys : ['']);

},

toggleKeyVisibility(btn){

  const inp = btn.previousElementSibling;

  if(inp.type === 'password'){inp.type='text'; btn.textContent='🙈';}

  else{inp.type='password'; btn.textContent='👁';}

},

_collectKeys(provider){

  return [...document.querySelectorAll(`#${provider}-keys-list .ai-key-input`)]

    .map(inp => inp.value.trim()).filter(v => v);

},

async saveSettings(){

  const btn = document.getElementById('btn-save-settings');

  const statusEl = document.getElementById('settings-status');

  const provider = document.getElementById('ai-provider-select')?.value;

  const geminiKeys = this._collectKeys('gemini');

  const deepseekKeys = this._collectKeys('deepseek');



  if(provider === 'gemini' && !geminiKeys.length){

    return this.toast('Vui lòng thêm ít nhất 1 Gemini API Key', 'error');

  }

  if(provider === 'deepseek' && !deepseekKeys.length){

    return this.toast('Vui lòng thêm ít nhất 1 DeepSeek API Key', 'error');

  }



  btn.disabled = true; btn.textContent = 'Đang lưu...';

  statusEl.textContent = '';

  try{

    await Promise.all([

      API.setSetting('ai_provider', provider),

      API.setSetting('ai_keys_gemini', JSON.stringify(geminiKeys)),

      API.setSetting('ai_keys_deepseek', JSON.stringify(deepseekKeys))

    ]);

    this.toast('Đã lưu cài đặt AI!', 'success');

    statusEl.textContent = provider ? ('✅ Đang dùng: ' + (provider === 'gemini' ? 'Gemini' : 'DeepSeek') + ' (' + (provider==='gemini'?geminiKeys.length:deepseekKeys.length) + ' key)') : '🚫 AI Remix đang tắt';

  }catch(e){this.toast('Lỗi lưu: ' + e.message, 'error');}

  finally{btn.disabled=false; btn.textContent='💾 Lưu cài đặt';}

},

async testAiRemix(){

  const btn = document.getElementById('btn-test-remix');

  const provider = document.getElementById('ai-provider-select')?.value;

  const resultBox = document.getElementById('test-remix-result');

  const outputEl = document.getElementById('test-remix-output');

  if(!provider){return this.toast('Chọn provider AI trước', 'error');}

  const keys = this._collectKeys(provider);

  if(!keys.length){return this.toast('Thêm API Key trước khi test', 'error');}

  const sampleText = 'Chào bạn! Mình đang tìm đối tác BD cho dự án Weex. Bạn có quan tâm không? 🚀';

  btn.disabled=true; btn.textContent='Đang remix...';

  resultBox.classList.add('hidden');

  try{

    const r = await API.setSetting('_test_remix_trigger', JSON.stringify({provider, keys, text: sampleText}));

    // Actually call the test endpoint

    const resp = await fetch('/api/settings/test-remix', {

      method:'POST',

      headers:{'Content-Type':'application/json'},

      body: JSON.stringify({provider, keys, text: sampleText})

    });

    const data = await resp.json();

    if(data.remixed){

      outputEl.textContent = data.remixed;

      resultBox.classList.remove('hidden');

      this.toast('AI remix thành công!', 'success');

    }else{

      this.toast('Lỗi: ' + (data.error||'Unknown'), 'error');

    }

  }catch(e){this.toast('Test thất bại: ' + e.message, 'error');}

  finally{btn.disabled=false; btn.textContent='🧪 Test Remix';}

},

async loadWatcherLogs(){const status=document.getElementById('wl-filter-status')?.value||'';

  try{const d=await API.getWatcherLogs({limit:this._wlLimit,offset:this._wlOffset,...(status?{status}:{})});

    const tbody=document.getElementById('watcher-logs-body');

    if(!d.logs.length){tbody.innerHTML='<tr><td colspan="8" style="text-align:center;color:var(--text2);padding:24px">Chưa có log DM</td></tr>';return}

    const accs=this.accounts.length?this.accounts:(await API.getAccounts()).accounts;

    const accMap=Object.fromEntries(accs.map(a=>[a.id,a]));

    tbody.innerHTML=d.logs.map(l=>{

      const statusCls=l.status==='success'?'badge-green':l.status==='skipped'?'badge-blue':'badge-red';

      const accName=l.account_id&&accMap[l.account_id]?esc(accMap[l.account_id].name):(l.account_id||'—');

      return`<tr><td style="font-size:12px">${formatDate(l.sent_at)}</td><td>${l.watcher_id}</td><td>@${esc(l.target_username||String(l.target_user_id))}</td><td style="font-size:12px">${esc(l.group_title||String(l.group_id||''))}</td><td><span class="badge badge-blue" style="font-size:11px">${esc(l.matched_keyword||'')}</span></td><td style="font-size:12px">${accName}</td><td><span class="badge ${statusCls}">${l.status}</span></td><td style="font-size:12px;color:var(--text2)">${esc(l.error_message||'—')}</td></tr>`}).join('');

    const pag=document.getElementById('watcher-logs-pagination');const pages=Math.ceil(d.total/this._wlLimit);const cur=Math.floor(this._wlOffset/this._wlLimit);

    if(pages>1){let h='';if(cur>0)h+=`<button class="btn btn-ghost btn-sm" onclick="App._wlPage(${cur-1})">← Trước</button>`;h+=`<span style="color:var(--text2);font-size:12px">Trang ${cur+1}/${pages}</span>`;if(cur<pages-1)h+=`<button class="btn btn-ghost btn-sm" onclick="App._wlPage(${cur+1})">Sau →</button>`;pag.innerHTML=h}else pag.innerHTML=''

  }catch(e){this.toast('Lỗi: '+e.message,'error')}},

_wlPage(p){this._wlOffset=p*this._wlLimit;this.loadWatcherLogs()},

openTestDM(watcherId){

  (document.getElementById('test-dm-watcher-id') || me_dummy).value = watcherId;

  (document.getElementById('test-dm-target') || me_dummy).value = '';

  (document.getElementById('test-dm-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('open');

  setTimeout(()=>document.getElementById('test-dm-target')?.focus(),100)},

async sendTestDM(){

  const wId=document.getElementById('test-dm-watcher-id')?.value;

  const target=(document.getElementById('test-dm-target')?.value || "").trim();

  if(!target)return this.toast('Nhập username hoặc User ID','error');

  const btn=document.getElementById('btn-send-test-dm');

  btn.disabled=true;btn.textContent='Đang gửi...';

  try{

    const r=await API.testWatcherDM(wId,target);

    this.toast(r.message||'Đã gửi!','success');

    (document.getElementById('test-dm-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('open');

    this.loadWatcherLogs()

  }catch(e){this.toast('Lỗi: '+e.message,'error')}

  btn.disabled=false;btn.textContent='📨 Gửi ngay'}};

// ══════════════════════════════════════════════════════════
//  AI SETTINGS (standalone functions outside App object)
// ══════════════════════════════════════════════════════════

App.loadSettings = async function() {
  try {
    const res = await Promise.all([
      API.getSetting('ai_provider'),
      API.getSetting('ai_keys_gemini'),
      API.getSetting('ai_keys_deepseek'),
      API.getSetting('ai_keys_openai'),
      API.getSetting('ai_keys_groq'),
      API.getSetting('ai_keys_openai_compatible'),
      API.getSetting('ai_oai_compat_base_url'),
      API.getSetting('ai_oai_compat_model'),
      API.getSetting('ai_custom_prompt'),
      API.getSetting('ai_keys_chatgpt_oauth'),
      API.getSetting('ai_chatgpt_oauth_base_url'),
      API.getSetting('ai_chatgpt_oauth_model')
    ]);
    const provider = res[0].value || '';
    let geminiKeys = [];
    let deepseekKeys = [];
    let openaiKeys = [];
    let groqKeys = [];
    try { geminiKeys   = JSON.parse(res[1].value || '[]'); } catch(e) {}
    try { deepseekKeys = JSON.parse(res[2].value || '[]'); } catch(e) {}
    try { openaiKeys   = JSON.parse(res[3].value || '[]'); } catch(e) {}
    try { groqKeys     = JSON.parse(res[4].value || '[]'); } catch(e) {}
    let oaiCompatKeys = [];
    try { oaiCompatKeys = JSON.parse(res[5].value || '[]'); } catch(e) {}
    const oaiCompatBaseUrl = res[6].value || '';
    const oaiCompatModel   = res[7].value || '';
    const customPrompt     = res[8].value || '';
    let chatgptOauthKeys = [];
    try { chatgptOauthKeys = JSON.parse(res[9].value || '[]'); } catch(e) {}
    const chatgptOauthBaseUrl = res[10].value || '';
    const chatgptOauthModel   = res[11].value || 'gpt-4o';

    (document.getElementById('ai-provider-select') || me_dummy).value = provider;
    App.renderAiKeysList('gemini',   geminiKeys);
    App.renderAiKeysList('deepseek', deepseekKeys);
    App.renderAiKeysList('openai',   openaiKeys);
    App.renderAiKeysList('groq',     groqKeys);
    App.renderAiKeysList('openai_compatible', oaiCompatKeys);
    App.renderAiKeysList('chatgpt_oauth', chatgptOauthKeys);
    (document.getElementById('oai-compat-base-url') || me_dummy).value = oaiCompatBaseUrl;
    (document.getElementById('oai-compat-model') || me_dummy).value = oaiCompatModel;
    (document.getElementById('chatgpt-oauth-base-url') || me_dummy).value = chatgptOauthBaseUrl;
    (document.getElementById('chatgpt-oauth-model') || me_dummy).value = chatgptOauthModel;
    const customPromptEl = document.getElementById('ai-custom-prompt');
    if (customPromptEl) customPromptEl.value = customPrompt;

    App.onProviderChange();
    // Also load proxy status when settings page loads
    App.loadProxyStatus();
  } catch(e) {
    App.toast('Loi tai cai dat: ' + e.message, 'error');
  }
};

App.onProviderChange = function() {
  const p = document.getElementById('ai-provider-select')?.value;
  (document.getElementById('ai-gemini-section')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).toggle('hidden', p !== 'gemini');
  (document.getElementById('ai-deepseek-section')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).toggle('hidden', p !== 'deepseek');
  const openaiEl = document.getElementById('ai-openai-section');
  if (openaiEl) openaiEl.classList.toggle('hidden', p !== 'openai');
  const chatgptOauthEl = document.getElementById('ai-chatgpt_oauth-section');
  if (chatgptOauthEl) chatgptOauthEl.classList.toggle('hidden', p !== 'chatgpt_oauth');
  const groqEl = document.getElementById('ai-groq-section');
  if (groqEl) groqEl.classList.toggle('hidden', p !== 'groq');
  const oaiCompatEl = document.getElementById('ai-openai_compatible-section');
  if (oaiCompatEl) oaiCompatEl.classList.toggle('hidden', p !== 'openai_compatible');
  (document.getElementById('test-remix-result')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');
};

// Get current model value from either dropdown or text input
App.getOaiCompatModel = function() {
  const selectEl = document.getElementById('oai-compat-model-select');
  const inputEl = document.getElementById('oai-compat-model');
  // If select is visible and has a value, use it
  if (selectEl && selectEl.style.display !== 'none' && selectEl.value) {
    return selectEl.value;
  }
  return inputEl ? inputEl.value.trim() : '';
};

// Fetch available models from OpenAI-compatible API
App.fetchOaiModels = async function() {
  const baseUrl = (document.getElementById('oai-compat-base-url')?.value || "").trim();
  const statusEl = document.getElementById('oai-models-status');
  const selectEl = document.getElementById('oai-compat-model-select');
  const inputEl = document.getElementById('oai-compat-model');
  const btn = document.getElementById('oai-load-models-btn');

  if (!baseUrl) {
    App.toast('Nhập Base URL trước', 'error');
    return;
  }

  // Get first API key if available
  const keys = App.collectAiKeys('openai_compatible');
  const apiKey = keys.length > 0 ? keys[0] : '';

  btn.disabled = true;
  btn.textContent = '⏳';
  statusEl.textContent = 'Đang tải danh sách models...';
  statusEl.style.color = 'var(--text2)';

  try {
    const resp = await fetch('/api/settings/fetch-models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ base_url: baseUrl, api_key: apiKey })
    });
    const data = await resp.json();

    if (!data.success) {
      statusEl.textContent = '❌ ' + (data.error || 'Lỗi không xác định');
      statusEl.style.color = '#ef4444';
      return;
    }

    const models = data.models || [];
    if (models.length === 0) {
      statusEl.textContent = '⚠️ API trả về 0 models. Nhập tên model thủ công.';
      statusEl.style.color = '#f59e0b';
      return;
    }

    // Remember current selection
    const currentModel = inputEl.value.trim() || (selectEl.value || '');

    // Populate dropdown
    selectEl.innerHTML = '<option value="">-- Chọn model (' + models.length + ' models) --</option>';
    models.forEach(function(m) {
      const opt = document.createElement('option');
      opt.value = m.id;
      opt.textContent = m.id + (m.owned_by ? ' (' + m.owned_by + ')' : '');
      if (m.id === currentModel) opt.selected = true;
      selectEl.appendChild(opt);
    });

    // Unhide dropdown select
    (selectEl.classList || {remove:()=>{}}).remove('hidden');
    selectEl.style.display = 'block';

    // Sync select → hidden input for save
    selectEl.onchange = function() {
      inputEl.value = selectEl.value;
    };
    if (currentModel) {
      selectEl.value = currentModel;
      inputEl.value = currentModel;
    }

    statusEl.textContent = '✅ Đã tải ' + models.length + ' models';
    statusEl.style.color = '#22c55e';

    // Auto-clear status after 5s
    setTimeout(() => { statusEl.textContent = ''; }, 5000);
  } catch (e) {
    statusEl.textContent = '❌ Lỗi kết nối: ' + e.message;
    statusEl.style.color = '#ef4444';
  } finally {
    btn.disabled = false;
    btn.textContent = '🔄 Load';
  }
};

// Switch back to manual input mode
App.switchToManualModelInput = function() {
  const selectEl = document.getElementById('oai-compat-model-select');
  const inputEl = document.getElementById('oai-compat-model');
  selectEl.style.display = 'none';
  inputEl.style.display = '';
  inputEl.focus();
};

App.openChatgptOAuthModal = function() {
  const modal = document.getElementById('chatgpt-oauth-modal');
  if (!modal) return;
  const input = document.getElementById('chatgpt-token-paste-input');
  const status = document.getElementById('chatgpt-verify-status');
  if (input) input.value = '';
  if (status) { status.textContent = ''; status.innerHTML = ''; }
  modal.classList.add('open');
};

// ── PKCE Helpers ──
App._generateRandomString = function(length) {
  var chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~';
  var values = new Uint8Array(length);
  window.crypto.getRandomValues(values);
  var result = '';
  for (var i = 0; i < length; i++) result += chars[values[i] % chars.length];
  return result;
};

App._sha256 = async function(plain) {
  var encoder = new TextEncoder();
  return window.crypto.subtle.digest('SHA-256', encoder.encode(plain));
};

App._base64urlencode = function(buffer) {
  return btoa(String.fromCharCode.apply(null, new Uint8Array(buffer)))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
};

// ── Open ChatGPT Login → Session Token Popup ──
App.openChatgptAuthTab = function() {
  var status = document.getElementById('chatgpt-verify-status');
  var sessionUrl = 'https://chatgpt.com/api/auth/session';

  // Open as centered popup window (like Codex login)
  var width = 520, height = 680;
  var left = window.screenX + (window.innerWidth - width) / 2;
  var top = window.screenY + (window.innerHeight - height) / 2;
  var popup = window.open(
    sessionUrl,
    'ChatGPTLogin',
    'width=' + width + ',height=' + height + ',top=' + top + ',left=' + left + ',scrollbars=yes,menubar=no,toolbar=no'
  );

  if (!popup) {
    App.toast('Trình duyệt đã chặn popup. Vui lòng cho phép popup và thử lại.', 'error');
    return;
  }

  if (status) {
    status.innerHTML =
      '<div style="background:rgba(16,163,127,0.08);border-left:3px solid #10a37f;border-radius:6px;padding:10px 12px;font-size:12px;line-height:1.7">' +
        '<strong style="color:#10a37f">🔐 Cửa sổ đăng nhập ChatGPT đã mở!</strong><br>' +
        '① Đăng nhập tài khoản ChatGPT Plus/Team trên popup vừa mở<br>' +
        '② Sau khi đăng nhập, popup sẽ hiện JSON — bấm <kbd style="background:var(--bg);padding:1px 5px;border-radius:3px;font-size:11px">Ctrl+A</kbd> rồi <kbd style="background:var(--bg);padding:1px 5px;border-radius:3px;font-size:11px">Ctrl+C</kbd><br>' +
        '③ Quay lại đây dán vào ô bên dưới → bấm <strong>"🚀 Xác thực"</strong>' +
      '</div>';
  }

  // Auto-focus the paste textarea
  setTimeout(function() {
    var input = document.getElementById('chatgpt-token-paste-input');
    if (input) input.focus();
  }, 500);
};

// ── Apply token after OAuth or manual paste ──
App._applyChatgptOAuthToken = async function(token) {
  var status = document.getElementById('chatgpt-verify-status');
  var modal = document.getElementById('chatgpt-oauth-modal');

  // Auto-populate UI
  var providerSelect = document.getElementById('ai-provider-select');
  if (providerSelect) providerSelect.value = 'chatgpt_oauth';
  App.onProviderChange();

  var baseUrlEl = document.getElementById('chatgpt-oauth-base-url');
  if (baseUrlEl) baseUrlEl.value = 'https://api.openai.com/v1';
  var modelEl = document.getElementById('chatgpt-oauth-model');
  if (modelEl) modelEl.value = 'gpt-4o';

  // Set token into keys list
  App.renderAiKeysList('chatgpt_oauth', [token]);

  // Auto-save
  await App.saveSettings();

  if (status) {
    status.innerHTML = '<span style="color:#22c55e">✅ Đăng nhập ChatGPT Subscription thành công! Cấu hình đã được tự động điền.</span>';
  }
  App.toast('✅ Đã đăng nhập & tự động cấu hình ChatGPT Subscription thành công!', 'success');

  setTimeout(function() {
    if (modal) modal.classList.remove('open');
  }, 1500);
};

// ── Manual paste flow (Step 2 button) ──
App.processChatgptOAuthLogin = async function() {
  var input = document.getElementById('chatgpt-token-paste-input');
  var status = document.getElementById('chatgpt-verify-status');
  var btn = document.getElementById('btn-verify-chatgpt-oauth');

  var raw = (input ? input.value : '').trim();
  if (!raw) {
    App.toast('Vui lòng dán Access Token hoặc JSON session', 'error');
    return;
  }

  var token = raw;
  if (raw.startsWith('{') && raw.endsWith('}')) {
    try {
      var parsed = JSON.parse(raw);
      token = parsed.accessToken || parsed.access_token || parsed.token || raw;
    } catch(e) {}
  }

  if (token.toLowerCase().startsWith('bearer ')) {
    token = token.substring(7).trim();
  }

  var baseUrl = (document.getElementById('chatgpt-oauth-base-url') || {}).value || 'https://api.openai.com/v1';
  baseUrl = baseUrl.trim() || 'https://api.openai.com/v1';

  btn.disabled = true;
  btn.textContent = '⏳ Đang xác thực...';
  if (status) {
    status.innerHTML = '<span style="color:var(--text2)">⏳ Đang kiểm tra Access Token với OpenAI ChatGPT...</span>';
  }

  try {
    var resp = await fetch('/api/settings/verify-chatgpt-oauth', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ access_token: token, base_url: baseUrl })
    });
    var data = await resp.json();

    if (!data.success) {
      if (status) {
        status.innerHTML = '<span style="color:#ef4444">❌ ' + (data.error || 'Xác thực thất bại') + '</span>';
      }
      App.toast(data.error || 'Token không hợp lệ', 'error');
      return;
    }

    // Use the verified token
    App._applyChatgptOAuthToken(data.token);

  } catch(e) {
    if (status) {
      status.innerHTML = '<span style="color:#ef4444">❌ Lỗi kết nối: ' + e.message + '</span>';
    }
  } finally {
    btn.disabled = false;
    btn.textContent = '🚀 Xác thực & Tự động điền cấu hình';
  }
};


App.renderAiKeysList = function(provider, keys) {
  const container = document.getElementById(provider + '-keys-list');
  if (!container) return;
  container.innerHTML = '';
  const list = (keys && keys.length) ? keys : [''];
  list.forEach(function(k, i) {
    const row = document.createElement('div');
    row.style.cssText = 'display:flex;gap:8px;align-items:center;margin-bottom:6px';
    const safeK = (k || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;');
    row.innerHTML =
      '<input type="password" class="form-input ai-key-input" data-provider="' + provider + '" data-idx="' + i + '"' +
      ' value="' + safeK + '" placeholder="API Key ' + (i + 1) + '"' +
      ' style="flex:1;font-family:monospace;font-size:12px">' +
      '<button class="btn btn-ghost btn-sm" onclick="App.toggleKeyVisibility(this)" title="Hien/An">&#128065;</button>' +
      '<button class="btn btn-danger btn-sm" onclick="App.removeAiKeyRow(\'' + provider + '\',' + i + ')" title="Xoa">&#x2715;</button>';
    container.appendChild(row);
  });
};

App.addAiKeyRow = function(provider) {
  const keys = App.collectAiKeys(provider);
  keys.push('');
  App.renderAiKeysList(provider, keys);
  const inputs = document.querySelectorAll('#' + provider + '-keys-list .ai-key-input');
  if (inputs.length) { inputs[inputs.length - 1].focus(); }
};

App.removeAiKeyRow = function(provider, idx) {
  const keys = App.collectAiKeys(provider);
  keys.splice(idx, 1);
  App.renderAiKeysList(provider, keys.length ? keys : ['']);
};

App.toggleKeyVisibility = function(btn) {
  const inp = btn.previousElementSibling;
  if (inp.type === 'password') { inp.type = 'text'; btn.innerHTML = '&#128584;'; }
  else { inp.type = 'password'; btn.innerHTML = '&#128065;'; }
};

App.collectAiKeys = function(provider) {
  return Array.from(
    document.querySelectorAll('#' + provider + '-keys-list .ai-key-input')
  ).map(function(inp) { return inp.value.trim(); }).filter(function(v) { return v.length > 0; });
};

App.saveSettings = async function() {
  const btn = document.getElementById('btn-save-settings');
  const statusEl = document.getElementById('settings-status');
  const provider = document.getElementById('ai-provider-select')?.value;
  const geminiKeys   = App.collectAiKeys('gemini');
  const deepseekKeys = App.collectAiKeys('deepseek');
  const openaiKeys   = App.collectAiKeys('openai');
  const groqKeys     = App.collectAiKeys('groq');
  const oaiCompatKeys = App.collectAiKeys('openai_compatible');
  const chatgptOauthKeys = App.collectAiKeys('chatgpt_oauth');
  if (provider === 'gemini'   && geminiKeys.length   === 0) { App.toast('Thêm ít nhất 1 Gemini API Key', 'error'); return; }
  if (provider === 'deepseek' && deepseekKeys.length === 0) { App.toast('Thêm ít nhất 1 DeepSeek API Key', 'error'); return; }
  if (provider === 'openai'   && openaiKeys.length   === 0) { App.toast('Thêm ít nhất 1 OpenAI API Key', 'error'); return; }
  if (provider === 'groq'     && groqKeys.length     === 0) { App.toast('Thêm ít nhất 1 Groq API Key', 'error'); return; }
  if (provider === 'chatgpt_oauth' && chatgptOauthKeys.length === 0) { App.toast('Thêm ít nhất 1 ChatGPT Access Token (OAuth)', 'error'); return; }
  if (provider === 'openai_compatible') {
    const baseUrl = (document.getElementById('oai-compat-base-url')?.value || "").trim();
    const model = App.getOaiCompatModel();
    if (!baseUrl) { App.toast('Nhập Base URL cho OpenAI Compatible', 'error'); return; }
    if (!model) { App.toast('Nhập Model Name cho OpenAI Compatible', 'error'); return; }
    if (oaiCompatKeys.length === 0) { App.toast('Thêm ít nhất 1 API Key', 'error'); return; }
  }
  btn.disabled = true;
  btn.textContent = 'Đang lưu...';
  statusEl.textContent = '';
  try {
    const customPromptVal = (document.getElementById('ai-custom-prompt')?.value || '').trim();
    await Promise.all([
      API.setSetting('ai_provider',       provider),
      API.setSetting('ai_keys_gemini',   JSON.stringify(geminiKeys)),
      API.setSetting('ai_keys_deepseek', JSON.stringify(deepseekKeys)),
      API.setSetting('ai_keys_openai',   JSON.stringify(openaiKeys)),
      API.setSetting('ai_keys_groq',     JSON.stringify(groqKeys)),
      API.setSetting('ai_keys_openai_compatible', JSON.stringify(oaiCompatKeys)),
      API.setSetting('ai_oai_compat_base_url', (document.getElementById('oai-compat-base-url')?.value || "").trim()),
      API.setSetting('ai_oai_compat_model', App.getOaiCompatModel()),
      API.setSetting('ai_custom_prompt', customPromptVal),
      API.setSetting('ai_keys_chatgpt_oauth', JSON.stringify(chatgptOauthKeys)),
      API.setSetting('ai_chatgpt_oauth_base_url', (document.getElementById('chatgpt-oauth-base-url')?.value || "").trim()),
      API.setSetting('ai_chatgpt_oauth_model', (document.getElementById('chatgpt-oauth-model')?.value || "").trim() || "gpt-4o")
    ]);
    App.toast('Đã lưu cài đặt AI!', 'success');
    const cnt = provider === 'gemini' ? geminiKeys.length : (provider === 'deepseek' ? deepseekKeys.length : (provider === 'groq' ? groqKeys.length : (provider === 'openai_compatible' ? oaiCompatKeys.length : (provider === 'chatgpt_oauth' ? chatgptOauthKeys.length : openaiKeys.length))));
    const labelMap = { gemini: 'Gemini', deepseek: 'DeepSeek', openai: 'OpenAI', groq: 'Groq', openai_compatible: 'OpenAI Compatible', chatgpt_oauth: 'ChatGPT Subscription (OAuth)' };
    statusEl.textContent = provider
      ? ('Đang dùng: ' + (labelMap[provider] || provider) + ' (' + cnt + ' key)')
      : 'AI Remix đang tắt';
  } catch(e) {
    App.toast('Lỗi lưu: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Lưu cài đặt';
  }
};

App.applyAiPromptPreset = function(type) {
  const el = document.getElementById('ai-custom-prompt');
  if (!el) return;

  if (type === 'curiosity') {
    el.value = "Hãy viết lại tin nhắn theo phong cách cực kỳ ngắn gọn (1-2 câu max), giọng điệu tự nhiên như đồng nghiệp nhắn tin trao đổi kinh nghiệm. Đặt câu hỏi mở ở cuối để kích thích người nhận nhắn tin trả lời lại. Tuyệt đối không dùng từ ngữ chào hàng hay quảng cáo đơn phương.";
  } else if (type === 'peer') {
    el.value = "Hãy viết lại tin nhắn ngắn gọn, thân thiện như bạn bè/đồng nghiệp lâu ngày hỏi thăm công việc. Dùng ngôn từ tự nhiên, gần gũi, tuyệt đối không dùng emoji hay từ ngữ bán hàng.";
  } else if (type === 'clear') {
    el.value = "";
  }
};

App.testAiRemix = async function() {
  const btn = document.getElementById('btn-test-remix');
  const provider = document.getElementById('ai-provider-select')?.value;
  const resultBox = document.getElementById('test-remix-result');
  const outputEl  = document.getElementById('test-remix-output');
  if (!provider) { App.toast('Chon AI provider truoc', 'error'); return; }
  // Try DOM keys first, fallback to DB-stored keys
  let keys = App.collectAiKeys(provider);
  if (keys.length === 0) {
    try {
      const stored = await API.getSetting('ai_keys_' + provider);
      keys = JSON.parse(stored.value || '[]');
    } catch(e) { keys = []; }
  }
  if (keys.length === 0) { App.toast('Them API Key truoc khi test', 'error'); return; }
  const sampleText = 'Hi! Looking for BD partners for Weex. Interested in collaborating?';
  btn.disabled = true;
  btn.textContent = 'Dang remix...';
  resultBox.classList.add('hidden');
  try {
    const body = { provider: provider, keys: keys, text: sampleText };
    if (provider === 'openai_compatible') {
      body.base_url = (document.getElementById('oai-compat-base-url')?.value || "").trim();
      body.model = App.getOaiCompatModel();
    }
    const resp = await fetch('/api/settings/test-remix', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    const data = await resp.json();
    if (data.remixed) {
      outputEl.textContent = data.remixed;
      resultBox.classList.remove('hidden');
      App.toast('AI remix thanh cong!', 'success');
    } else {
      App.toast('Loi: ' + (data.detail || data.error || 'Unknown'), 'error');
    }
  } catch(e) {
    App.toast('Test that bai: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Test Remix';
  }
};


function esc(s){if(!s)return'';const d=document.createElement('div');d.textContent=s;return d.innerHTML}

function formatDate(s){if(!s)return'—';try{return new Date(s).toLocaleString('vi-VN',{timeZone:'Asia/Ho_Chi_Minh'})}catch{return s}}

function formatDays(s){if(!s)return'';const n={'1':'T2','2':'T3','3':'T4','4':'T5','5':'T6','6':'T7','7':'CN'};return s.split(',').map(d=>n[d.trim()]||d).join(', ')}



// ══════════════════════════════════════════════════════════

//  CHANNEL MANAGER

// ══════════════════════════════════════════════════════════

App._chChannels = [];

App._chFiltered = [];

App._chSelected = new Set();

App._chAccountId = null;



App._populateChAccountSelect = async function() {

  const sel = document.getElementById('ch-account-select');

  if (!sel) return;

  if (!App._accounts || App._accounts.length === 0) {

    try { const d = await API.getAccounts(); App._accounts = d.accounts || []; } catch(e) {}

  }

  const accounts = App._accounts || [];

  sel.innerHTML = accounts.map(a => {

    const ui = a.user_info;

    const name = ui ? [ui.first_name, ui.last_name].filter(Boolean).join(' ') : '';

    const uname = ui && ui.username ? '@' + ui.username : (ui && ui.phone ? ui.phone : '');

    const label = name ? `${name} (${uname || 'id=' + a.id})` : (uname ? `${uname} (id=${a.id})` : 'Account ' + a.id);

    return `<option value="${a.id}">${esc(label)}</option>`;

  }).join('');

  if (accounts.length > 0 && !sel.value) sel.value = accounts[0].id;

  App.loadChannels();

};



App.loadChannels = async function() {

  const sel = document.getElementById('ch-account-select');

  if (!sel) return;

  const accountId = parseInt(sel.value);

  if (!accountId) return;

  App._chAccountId = accountId;

  App._chSelected.clear();

  App._updateActionBar();



  (document.getElementById('ch-loading')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('hidden');

  (document.getElementById('ch-loading') || me_dummy).textContent = '\u0110ang t\u1EA3i danh s\u00E1ch k\u00EAnh...';

  (document.getElementById('ch-table')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');

  (document.getElementById('ch-empty')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');

  (document.getElementById('ch-status-banner')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');

  (document.getElementById('ch-search') || me_dummy).value = '';

  (document.getElementById('ch-type-filter') || me_dummy).value = 'all';



  try {

    const res = await fetch(`/api/chats?account_id=${accountId}`);

    const data = await res.json();

    App._chChannels = data.chats || [];

    App._filterChannels();

  } catch(e) {

    (document.getElementById('ch-loading') || me_dummy).textContent = 'L\u1ED7i: ' + e.message;

  }

};



App._filterChannels = function() {

  const typeFilter = document.getElementById('ch-type-filter')?.value;

  const search = (document.getElementById('ch-search')?.value || '').toLowerCase().trim();



  let filtered = App._chChannels;

  if (typeFilter !== 'all') {

    filtered = filtered.filter(ch => ch.chat_type === typeFilter);

  }

  if (search) {

    filtered = filtered.filter(ch =>

      (ch.chat_title || '').toLowerCase().includes(search) ||

      (ch.username || '').toLowerCase().includes(search)

    );

  }

  App._chFiltered = filtered;



  // Update count

  const countEl = document.getElementById('ch-count');

  if (countEl) {

    countEl.textContent = `Hi\u1EC3n th\u1ECB ${filtered.length} / ${App._chChannels.length} k\u00EAnh`;

  }



  // Keep only valid selections

  const validIds = new Set(filtered.map(c => c.chat_id));

  App._chSelected = new Set([...App._chSelected].filter(id => validIds.has(id)));

  App._updateActionBar();



  App._renderChannelTable(filtered);

};

App.filterChannelsSearch = debounce(function() {
  App._filterChannels();
}, 250);



App._renderChannelTable = function(channels) {

  const tbody = document.getElementById('ch-tbody');

  const loading = document.getElementById('ch-loading');

  const table = document.getElementById('ch-table');

  const empty = document.getElementById('ch-empty');

  const selectAll = document.getElementById('ch-select-all');



  loading.classList.add('hidden');



  if (!channels || channels.length === 0) {

    empty.classList.remove('hidden');

    table.classList.add('hidden');

    return;

  }



  const typeLabel = {channel: '📢 Kênh', supergroup: '👥 Siêu nhóm', group: '💬 Nhóm', bot: '🤖 Bot'};

  const typeColor = {channel: '#f59e0b', supergroup: '#6366f1', group: '#22c55e', bot: '#ec4899'};



  tbody.innerHTML = channels.map((ch, i) => {

    const checked = App._chSelected.has(ch.chat_id) ? 'checked' : '';

    return `

    <tr style="border-bottom:1px solid var(--border);transition:background .15s;"

        onmouseover="this.style.background='rgba(255,255,255,.04)'"

        onmouseout="this.style.background=''"

        id="ch-row-${ch.chat_id}">

      <td style="padding:.6rem .5rem;text-align:center;">

        <input type="checkbox" class="ch-checkbox" data-id="${ch.chat_id}" ${checked}

               onchange="App._onCheckboxChange(${ch.chat_id}, this.checked)">

      </td>

      <td style="padding:.6rem .75rem;color:var(--text-secondary);font-size:.8rem;">${i+1}</td>

      <td style="padding:.6rem .75rem;font-weight:500;">${esc(ch.chat_title)}</td>

      <td style="padding:.6rem .75rem;font-size:.82rem;">

        <span style="color:${typeColor[ch.chat_type] || '#aaa'}">${typeLabel[ch.chat_type] || ch.chat_type}</span>

      </td>

      <td style="padding:.6rem .75rem;color:var(--text-secondary);font-size:.82rem;">${ch.participants_count != null ? ch.participants_count.toLocaleString() : '—'}</td>

      <td style="padding:.6rem .75rem;color:var(--accent);font-size:.82rem;">${ch.username ? '@' + ch.username : '—'}</td>

      <td style="padding:.6rem .75rem;text-align:right;">

        <button class="btn btn-sm btn-danger" onclick="App.leaveOne(${ch.chat_id}, '${esc(ch.chat_title).replace(/'/g, "\\'")}', '${ch.chat_type}')"

                id="ch-btn-${ch.chat_id}">${ch.chat_type === 'bot' ? 'Xoá' : 'Rời'}</button>

      </td>

    </tr>`;

  }).join('');



  if (selectAll) selectAll.checked = (App._chSelected.size === channels.length && channels.length > 0);

  table.classList.remove('hidden');

};



App._onCheckboxChange = function(chatId, checked) {

  if (checked) App._chSelected.add(chatId);

  else App._chSelected.delete(chatId);

  App._updateActionBar();

  const selectAll = document.getElementById('ch-select-all');

  if (selectAll) selectAll.checked = (App._chSelected.size === App._chFiltered.length && App._chFiltered.length > 0);

};



App._toggleSelectAll = function(checked) {

  App._chFiltered.forEach(ch => {

    if (checked) App._chSelected.add(ch.chat_id);

    else App._chSelected.delete(ch.chat_id);

  });

  // Update all visible checkboxes

  document.querySelectorAll('.ch-checkbox').forEach(cb => cb.checked = checked);

  App._updateActionBar();

};



App._clearSelection = function() {

  App._chSelected.clear();

  document.querySelectorAll('.ch-checkbox').forEach(cb => cb.checked = false);

  const selectAll = document.getElementById('ch-select-all');

  if (selectAll) selectAll.checked = false;

  App._updateActionBar();

};



App._updateActionBar = function() {

  const bar = document.getElementById('ch-action-bar');

  const countEl = document.getElementById('ch-selected-count');

  if (App._chSelected.size > 0) {

    bar.classList.remove('hidden');

    countEl.textContent = `\u0110\u00E3 ch\u1ECDn ${App._chSelected.size} k\u00EAnh`;

  } else {

    bar.classList.add('hidden');

  }

};



App.leaveOne = async function(chatId, chatTitle, chatType = '') {

  const isBot = chatType === 'bot';

  const confirmMsg = isBot ? `Dừng và xoá cuộc trò chuyện với "${chatTitle}"?` : `Rời khỏi "${chatTitle}"?`;

  if (!confirm(confirmMsg)) return;

  const btn = document.getElementById('ch-btn-' + chatId);

  const row = document.getElementById('ch-row-' + chatId);

  if (btn) { btn.disabled = true; btn.textContent = '...'; }

  if (row) row.style.opacity = '0.4';

  try {

    const res = await fetch('/api/chats/leave-channel', {

      method: 'POST',

      headers: {'Content-Type': 'application/json'},

      body: JSON.stringify({account_id: App._chAccountId, chat_id: chatId})

    });

    if (res.ok) {

      if (row) row.remove();

      App._chChannels = App._chChannels.filter(c => c.chat_id !== chatId);

      App._chSelected.delete(chatId);

      App._filterChannels();

      App._showChBanner(isBot ? `✅ Đã dừng & xoá "${chatTitle}"` : `✅ Đã rời "${chatTitle}"`, 'success');

    } else {

      const err = await res.json();

      App._showChBanner(`❌ Lỗi: ${err.detail || 'Unknown'}`, 'error');

      if (btn) { btn.disabled = false; btn.textContent = isBot ? 'Xoá' : 'Rời'; }

      if (row) row.style.opacity = '1';

    }

  } catch(e) {

    App._showChBanner('\u274C L\u1ED7i k\u1EBFt n\u1ED1i: ' + e.message, 'error');

    if (btn) { btn.disabled = false; btn.textContent = 'R\u1EDDi'; }

    if (row) row.style.opacity = '1';

  }

};



App.leaveSelected = async function() {

  const selected = [...App._chSelected];

  if (selected.length === 0) { alert('Ch\u01B0a ch\u1ECDn k\u00EAnh n\u00E0o.'); return; }



  const names = App._chChannels.filter(c => selected.includes(c.chat_id)).map(c => c.chat_title);

  if (!confirm(`\u26A0\uFE0F R\u1EDDi ${selected.length} k\u00EAnh/nh\u00F3m \u0111\u00E3 ch\u1ECDn?\n\n${names.join('\n')}\n\nThao t\u00E1c n\u00E0y kh\u00F4ng th\u1EC3 ho\u00E0n t\u00E1c!`)) return;



  App._showChBanner(`\u23F3 \u0110ang r\u1EDDi ${selected.length} k\u00EAnh, vui l\u00F2ng ch\u1EDD...`, 'info');



  let successCount = 0, failCount = 0;

  for (const chatId of selected) {

    const row = document.getElementById('ch-row-' + chatId);

    if (row) row.style.opacity = '0.4';

    try {

      const res = await fetch('/api/chats/leave-channel', {

        method: 'POST',

        headers: {'Content-Type': 'application/json'},

        body: JSON.stringify({account_id: App._chAccountId, chat_id: chatId})

      });

      if (res.ok) {

        if (row) row.remove();

        App._chChannels = App._chChannels.filter(c => c.chat_id !== chatId);

        successCount++;

      } else { failCount++; if (row) row.style.opacity = '1'; }

    } catch(e) { failCount++; if (row) row.style.opacity = '1'; }

    // Small delay to avoid flood

    await new Promise(r => setTimeout(r, 1500));

  }



  App._chSelected.clear();

  App._filterChannels();

  App._showChBanner(

    `\u2705 Ho\u00E0n t\u1EA5t: r\u1EDDi ${successCount}/${selected.length} k\u00EAnh` + (failCount > 0 ? `, ${failCount} th\u1EA5t b\u1EA1i` : ''),

    failCount === 0 ? 'success' : 'warning'

  );

};



App.leaveAllChannels = async function() {

  // Redirect to select all then leave

  App._toggleSelectAll(true);

  App.leaveSelected();

};



App._showChBanner = function(msg, type) {

  const el = document.getElementById('ch-status-banner');

  const colors = { success: 'rgba(34,197,94,.15)', error: 'rgba(239,68,68,.15)', warning: 'rgba(251,191,36,.15)', info: 'rgba(99,102,241,.15)' };

  const borders = { success: '#22c55e', error: '#ef4444', warning: '#fbbf24', info: '#6366f1' };

  el.style.background = colors[type] || colors.info;

  el.style.border = '1px solid ' + (borders[type] || borders.info);

  el.style.color = borders[type] || borders.info;

  el.textContent = msg;

  el.classList.remove('hidden');

};



// ══════════════════════════════════════════════════════════
//  API KEY MANAGEMENT (stored in localStorage)
// ══════════════════════════════════════════════════════════

App.loadApiKeyUi = function() {
  const inp = document.getElementById('api-key-input');
  const statusEl = document.getElementById('api-key-status');
  if (!inp) return;
  const stored = localStorage.getItem('tgs_api_key') || '';
  inp.value = stored;
  statusEl.textContent = stored
    ? '✅ API Key đang được dùng (đã lưu trong trình duyệt)'
    : '⚠️ Chưa đặt API Key – mọi request đều không có xác thực';
  statusEl.style.color = stored ? 'var(--accent)' : 'var(--text2)';
};

App.saveApiKey = function() {
  const val = (document.getElementById('api-key-input')?.value || '').trim();
  if (val) {
    localStorage.setItem('tgs_api_key', val);
    App.toast('Đã lưu API Key!', 'success');
  } else {
    localStorage.removeItem('tgs_api_key');
    App.toast('Đã xóa API Key', 'info');
  }
  App.loadApiKeyUi();
};

App.clearApiKey = function() {
  localStorage.removeItem('tgs_api_key');
  const inp = document.getElementById('api-key-input');
  if (inp) inp.value = '';
  App.toast('Đã xóa API Key', 'info');
  App.loadApiKeyUi();
};

App.toggleApiKeyVisibility = function() {
  const inp = document.getElementById('api-key-input');
  const btn = document.getElementById('api-key-toggle');
  if (!inp) return;
  if (inp.type === 'password') {
    inp.type = 'text';
    btn.textContent = '🙈';
  } else {
    inp.type = 'password';
    btn.textContent = '👁';
  }
};

// Patch loadSettings to also load API key UI + Daily Summary
const _origLoadSettings = App.loadSettings;
App.loadSettings = async function() {
  if (_origLoadSettings) await _origLoadSettings.call(this);
  App.loadApiKeyUi();
  App.loadDailySummary();
};

// ══════════════════════════════════════════════════════════
//  DAILY SUMMARY SETTINGS
// ══════════════════════════════════════════════════════════

App.loadDailySummary = async function() {
  try {
    // Populate account dropdown
    const sel = document.getElementById('daily-summary-account');
    if (sel) {
      const accs = App._accounts || [];
      sel.innerHTML = '<option value="">Tu dong (tai khoan dau tien)</option>';
      accs.forEach(a => {
        const name = a.name || a.phone || ('ID ' + a.id);
        sel.innerHTML += `<option value="${a.id}">${name} (ID ${a.id})</option>`;
      });
    }
    // Load saved settings
    const resp = await fetch('/api/settings/daily-summary', {
      headers: API.getHeaders()
    });
    if (resp.ok) {
      const data = await resp.json();
      const cb = document.getElementById('daily-summary-enabled');
      if (cb) cb.checked = data.enabled === '1';
      const timeEl = document.getElementById('daily-summary-time');
      if (timeEl) timeEl.value = data.time || '21:00';
      if (sel && data.account_id) sel.value = data.account_id;
    }
  } catch(e) {
    console.error('loadDailySummary error:', e);
  }
};

App.saveDailySummary = async function() {
  const statusEl = document.getElementById('daily-summary-status');
  const enabled = document.getElementById('daily-summary-enabled')?.checked ? '1' : '0';
  const time = document.getElementById('daily-summary-time')?.value || '21:00';
  const accountId = document.getElementById('daily-summary-account')?.value;
  statusEl.textContent = 'Dang luu...';
  try {
    const headers = { ...API.getHeaders(), 'Content-Type': 'application/json' };
    const resp = await fetch('/api/settings/daily-summary', {
      method: 'POST',
      headers,
      body: JSON.stringify({ enabled, time, account_id: accountId })
    });
    if (!resp.ok) {
      const err = await resp.json();
      throw new Error(err.detail || 'Loi luu');
    }
    App.toast('Da luu cai dat bao cao!', 'success');
    statusEl.textContent = enabled === '1' ? ('Gui luc ' + time + ' moi ngay') : 'Dang tat';
  } catch(e) {
    App.toast('Loi: ' + e.message, 'error');
    statusEl.textContent = '';
  }
};

document.addEventListener('DOMContentLoaded',()=>App.init());


// ══════════════════════════════════════════════════════════════════════════════
// Reactions Module
// ══════════════════════════════════════════════════════════════════════════════
const Reactions = (() => {
  // Danh sách emoji Telegram reaction hợp lệ (standard reactions)
  // ❤ KHÔNG có variation selector U+FE0F (❤️ sẽ báo lỗi Invalid reaction)
  const EMOJIS = ['👍','❤','🔥','🎉','😮','👏','🥳','💯','😍','🤩','🤔','😢','👎','🙏','🤣','😱','💔','🥰','😁','👌'];
  let selectedEmojis = new Set(['👍']);
  let accountsData = [];

  // Loại bỏ variation selectors (U+FE0F, U+FE0E) khỏi emoji trước khi gửi lên API
  // Telegram chỉ chấp nhận emoji thuần, không có variation selector
  function _sanitizeEmoji(e) {
    return e.replace(/[\uFE0E\uFE0F]/g, '');
  }

  async function init() {
    _buildEmojiPicker();
    await _buildAccountList();
    await loadTargets();
    await loadLogs();
  }

  function _buildEmojiPicker() {
    const container = document.getElementById('rt-emoji-picker');
    if (!container) return;
    container.innerHTML = '';
    EMOJIS.forEach(e => {
      const btn = document.createElement('button');
      const sel = selectedEmojis.has(e);
      btn.textContent = e;
      btn.title = e;
      btn.style.cssText = `font-size:1.4rem;padding:.3rem .5rem;border-radius:.5rem;cursor:pointer;border:2px solid ${sel?'var(--accent)':'transparent'};background:${sel?'rgba(0,212,170,.15)':'var(--surface)'};transition:all .15s;`;
      btn.onclick = () => {
        if (selectedEmojis.has(e)) {
          if (selectedEmojis.size === 1) return;
          selectedEmojis.delete(e);
          btn.style.border = '2px solid transparent';
          btn.style.background = 'var(--surface)';
        } else {
          selectedEmojis.add(e);
          btn.style.border = '2px solid var(--accent)';
          btn.style.background = 'rgba(0,212,170,.15)';
        }
        const el = document.getElementById('rt-selected-emojis');
        if (el) el.textContent = [...selectedEmojis].join(' ');
      };
      container.appendChild(btn);
    });
  }

  async function _buildAccountList() {
    const container = document.getElementById('rt-account-list');
    if (!container) return;
    let allAccounts = [];
    try {
      const data = await API.getAccounts();
      // Hiển thị TẤT CẢ accounts, không filter is_logged_in
      // (account có thể offline tạm thời khi page load nhưng vẫn hoạt động)
      allAccounts = data.accounts || [];
      accountsData = allAccounts;
    } catch { accountsData = []; allAccounts = []; }
    container.innerHTML = '';
    if (allAccounts.length === 0) {
      container.innerHTML = '<span style="color:var(--text2);font-size:.85rem;">⚠️ Chưa có tài khoản nào. Vui lòng thêm tài khoản ở mục Tài khoản.</span>';
      return;
    }
    allAccounts.forEach(acc => {
      const label = document.createElement('label');
      const isOnline = acc.is_logged_in;
      label.style.cssText = `display:flex;align-items:center;gap:.4rem;cursor:pointer;padding:.4rem .8rem;border-radius:.5rem;background:var(--surface);border:2px solid var(--accent);transition:all .15s;font-size:.85rem;`;
      const cb = document.createElement('input');
      cb.type = 'checkbox'; cb.value = acc.id; cb.checked = true;
      cb.onchange = () => { label.style.borderColor = cb.checked ? 'var(--accent)' : 'transparent'; };
      label.appendChild(cb);
      const statusDot = document.createElement('span');
      statusDot.title = isOnline ? 'Online' : 'Offline';
      statusDot.style.cssText = `display:inline-block;width:7px;height:7px;border-radius:50%;background:${isOnline ? '#22c55e' : '#6b7280'};flex-shrink:0;`;
      label.appendChild(statusDot);
      label.appendChild(document.createTextNode(' ' + (acc.name || `acc#${acc.id}`)));
      container.appendChild(label);
    });
  }

  function _getSelectedAccounts() {
    return [...document.querySelectorAll('#rt-account-list input[type=checkbox]:checked')].map(c => parseInt(c.value));
  }

  async function addTarget() {
    const link = (document.getElementById('rt-link')?.value || "").trim();
    const delayMin = parseInt(document.getElementById('rt-delay-min')?.value) || 5;
    const delayMax = parseInt(document.getElementById('rt-delay-max')?.value) || 30;
    const viewEnabled = document.getElementById('rt-view-enabled')?.checked ? 1 : 0;
    const viewRatio = parseFloat(document.getElementById('rt-view-ratio')?.value || '1.0');
    const accIds = _getSelectedAccounts();
    if (!link) { alert('Vui lòng nhập link kênh'); return; }
    if (!accIds.length) { alert('Vui lòng chọn ít nhất 1 tài khoản'); return; }

    const btn = document.querySelector('#view-reactions .btn-primary');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Đang join...'; }
    try {
      const res = await ReactionsAPI.addTarget({
        channel_link: link, account_ids: accIds,
        reactions: [...selectedEmojis].map(_sanitizeEmoji), delay_min: delayMin, delay_max: delayMax, auto_join: true,
        view_enabled: viewEnabled, view_ratio: viewRatio
      });
      if (res.ok) {
        (document.getElementById('rt-link') || me_dummy).value = '';
        function fmtJoin(s) {
          if (s === 'ok') return '✅ Đã join';
          if (s === 'already_member') return '✅ Đã vào (sẵn)';
          if (s === 'join_request_sent') return '⏳ Đợi duyệt';
          if (s === 'client_not_connected') return '⚠️ Client chưa kết nối';
          return '❌ ' + s;
        }
        const summary = Object.entries(res.join_results || {}).map(([id,s]) => `acc ${id}: ${fmtJoin(s)}`).join('\n');
        alert('✅ Đã thêm kênh!\n\nKết quả join:\n' + (summary || '(không có)'));
        await loadTargets();
      } else { alert('❌ ' + JSON.stringify(res)); }
    } catch(e) { alert('❌ ' + e.message); }
    finally { if(btn){btn.disabled=false;btn.textContent='⚡ Thêm & Auto-Join';} }
  }

  async function loadTargets() {
    const tbody = document.getElementById('rt-targets-body');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center">Đang tải...</td></tr>';
    try {
      const { targets } = await ReactionsAPI.getTargets();
      if (!targets || !targets.length) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text2)">Chưa có kênh nào</td></tr>'; return;
      }
      tbody.innerHTML = targets.map(t => `
        <tr>
          <td><strong>${_esc(t.channel_title||t.channel_link)}</strong><br><small style="color:var(--text2)">${_esc(t.channel_link)}</small></td>
          <td>${(t.account_ids||[]).length} acc</td>
          <td style="font-size:1.2rem">${(t.reactions||['👍']).join(' ')}</td>
          <td style="color:var(--text2)">${t.delay_min}s – ${t.delay_max}s</td>
          <td id="rt-views-${t.id}"><span style="color:var(--text2);font-size:.8rem">⏳</span></td>
          <td><span class="status-badge ${t.is_active?'success':'failed'}" onclick="Reactions.toggleActive(${t.id},${t.is_active})" style="cursor:pointer">${t.is_active?'● Bật':'○ Tắt'}</span></td>
          <td>
            <button class="btn btn-sm" onclick="Reactions.manualJoin(${t.id})" title="Re-join">🔗</button>
            <button class="btn btn-sm" style="background:var(--red,.8)" onclick="Reactions.deleteTarget(${t.id})" title="Xóa">🗑</button>
          </td>
        </tr>`).join('');
    } catch(e) { tbody.innerHTML = `<tr><td colspan="6" style="color:red">Lỗi: ${e.message}</td></tr>`; }
    // Async: load view counts for each active target in background
    setTimeout(async () => {
      try {
        const { targets } = await ReactionsAPI.getTargets();
        (targets || []).filter(t => t.is_active).forEach(t => _fetchViews(t.id));
      } catch {}
    }, 200);
  }

  async function toggleActive(id, cur) {
    await ReactionsAPI.updateTarget(id, {is_active: cur ? 0 : 1});
    await loadTargets();
  }

  async function deleteTarget(id) {
    if (!confirm('Xóa kênh này?')) return;
    await ReactionsAPI.deleteTarget(id);
    await loadTargets();
  }

  async function manualJoin(id) {
    const res = await ReactionsAPI.joinTarget(id);
    function fmtJoin(s) {
      if (s === 'ok') return '✅ Đã join';
      if (s === 'already_member') return '✅ Đã vào (sẵn)';
      if (s === 'join_request_sent') return '⏳ Đợi duyệt';
      if (s === 'client_not_connected') return '⚠️ Client chưa kết nối';
      return '❌ ' + s;
    }
    alert('Kết quả join:\n' + Object.entries(res.join_results||{}).map(([a,s])=>`acc ${a}: ${fmtJoin(s)}`).join('\n'));
  }

  async function loadLogs() {
    const tbody = document.getElementById('rt-logs-body');
    if (!tbody) return;
    try {
      const { logs } = await ReactionsAPI.getLogs(null, 50);
      if (!logs || !logs.length) { tbody.innerHTML='<tr><td colspan="7" style="text-align:center;color:var(--text2)">Chưa có lịch sử</td></tr>'; return; }
      tbody.innerHTML = logs.map(l=>`
        <tr>
          <td style="font-size:.8rem;white-space:nowrap">${_esc(l.sent_at)}</td>
          <td>${l.channel_id||'—'}</td>
          <td>${l.msg_id||'—'}</td>
          <td>acc#${l.account_id}</td>
          <td style="font-size:1.2rem">${l.reaction||'—'}</td>
          <td><span class="status-badge ${l.status}">${l.status}</span></td>
          <td style="font-size:.75rem;color:var(--text2)">${_esc(l.error_msg||'—')}</td>
        </tr>`).join('');
    } catch(e) { tbody.innerHTML=`<tr><td colspan="7">Lỗi: ${e.message}</td></tr>`; }
  }

  function _esc(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }


  async function _fetchViews(targetId) {
    const cell = document.getElementById(`rt-views-${targetId}`);
    if (!cell) return;
    try {
      const res = await ReactionsAPI.getViews(targetId, 3);
      if (res && res.ok) {
        const avg = (res.avg_views || 0).toLocaleString('vi-VN');
        const max = (res.max_views || 0).toLocaleString('vi-VN');
        cell.innerHTML = `
          <div style="line-height:1.5;text-align:center">
            <div style="font-size:1rem;font-weight:700;color:var(--accent)">👁 ${avg}</div>
            <div style="color:var(--text2);font-size:.72rem">avg · max ${max}</div>
          </div>`;
      } else {
        cell.innerHTML = '<span style="color:var(--text2);font-size:.8rem">—</span>';
      }
    } catch {
      cell.innerHTML = '<span style="color:var(--text2);font-size:.8rem">—</span>';
    }
  }

  return { init, addTarget, loadTargets, loadLogs, toggleActive, deleteTarget, manualJoin, fetchViews: _fetchViews };
})();

// ══════════════════════════════════════════════════════════════════
// INBOX — DM Reply Tracker UI
// ══════════════════════════════════════════════════════════════════
Object.assign(App, {
  _inboxFilter: 'all',   // 'all' | 'unread' | 'read'
  _inboxAccountFilter: '', // empty means all accounts
  _inboxOffset: 0,
  _inboxLimit: 50,
  _inboxHasMore: false,

  // ── Badge polling ──────────────────────────────────────────────
  _inboxBadgeTimer: null,

  startInboxBadgePolling(){
    if(this._inboxBadgeTimer) return;
    const poll = async () => {
      try{
        const r = await fetch('/api/inbox/unread-count');
        const d = await r.json();
        const badge = document.getElementById('inbox-badge');
        if(!badge) return;
        if(d.count > 0){
          badge.textContent = d.count > 99 ? '99+' : d.count;
          badge.classList.remove('hidden');
        } else {
          badge.classList.add('hidden');
        }
      } catch{}
    };
    poll();
    this._inboxBadgeTimer = setInterval(poll, 10000);
  },

  async _populateInboxAccountFilter() {
    const sel = document.getElementById('inbox-account-filter');
    if (!sel) return;
    try {
      const d = await API.getAccounts();
      const accounts = d.accounts || [];
      const currentVal = sel.value;
      sel.innerHTML = '<option value="">Tất cả tài khoản</option>' + accounts.map(a => {
        const ui = a.user_info;
        const name = ui ? [ui.first_name, ui.last_name].filter(Boolean).join(' ') : a.name;
        const uname = ui && ui.username ? '@' + ui.username : (a.phone || '');
        const label = name ? `${name} (${uname})` : (uname || `ID ${a.id}`);
        return `<option value="${a.id}">${esc(label)}</option>`;
      }).join('');
      if (currentVal && accounts.some(a => String(a.id) === currentVal)) {
        sel.value = currentVal;
      } else {
        sel.value = "";
      }
    } catch (e) {
      sel.innerHTML = '<option value="">Tất cả tài khoản</option>';
    }
  },

  inboxAccountFilterChange(){
    const sel = document.getElementById('inbox-account-filter');
    if(sel){
      this._inboxAccountFilter = sel.value;
    }
    this.inboxLoad(true);
  },

  // ── Load inbox ─────────────────────────────────────────────────
  async inboxLoad(reset=true){
    if(reset){ this._inboxOffset = 0; }
    const isRead = this._inboxFilter === 'unread' ? 0
                 : this._inboxFilter === 'read'   ? 1
                 : undefined;
    let url = `/api/inbox?limit=${this._inboxLimit}&offset=${this._inboxOffset}`;
    if(isRead !== undefined) url += `&is_read=${isRead}`;
    if(this._inboxAccountFilter) url += `&account_id=${this._inboxAccountFilter}`;

    try{
      const r = await fetch(url);
      const d = await r.json();
      const replies = d.replies || [];
      this._inboxHasMore = replies.length === this._inboxLimit;
      if(reset){
        this._inboxRenderRows(replies, true);
      } else {
        this._inboxRenderRows(replies, false);
      }
      this._inboxOffset += replies.length;
    } catch(e){
      App.toast('Lỗi tải inbox: ' + e.message, 'error');
    }
  },

  // ── Filter switch ──────────────────────────────────────────────
  inboxFilter(f){
    this._inboxFilter = f;
    ['all','unread','read'].forEach(k=>{
      const btn = document.getElementById(`inbox-tab-${k}`);
      if(btn) btn.classList.toggle('active', k === f);
    });
    this.inboxLoad(true);
  },

  // ── Render rows ────────────────────────────────────────────────
  _inboxRenderRows(replies, reset){
    const tbody = document.getElementById('inbox-tbody');
    const empty = document.getElementById('inbox-empty');
    const more  = document.getElementById('btn-inbox-more');
    if(!tbody) return;
    if(reset) tbody.innerHTML = '';

    if(reset && replies.length === 0){
      tbody.innerHTML = '';
      if(empty) empty.classList.remove('hidden');
      if(more)  more.style.display = 'none';
      return;
    }
    if(empty) empty.classList.add('hidden');

    replies.forEach(rep => {
      const isUnread = rep.is_read === 0;
      const name = rep.sender_name || (rep.sender_username ? '@'+rep.sender_username : `ID ${rep.sender_user_id}`);
      const username = rep.sender_username ? `<br><span style="color:var(--text2);font-size:.78rem">@${rep.sender_username}</span>` : '';
      const msg  = (rep.message_text||'').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      const short = msg.length > 80 ? msg.slice(0,80)+'…' : msg;
      const watcher = rep.watcher_name
        ? `<span style="font-size:.8rem;padding:2px 6px;border-radius:4px;background:var(--bg3);color:var(--text2)">${rep.watcher_name}</span>`
        : `<span style="color:var(--text2);font-size:.8rem">—</span>`;
      const acc = rep.account_name || `acc#${rep.account_id}`;
      const dt  = rep.received_at ? rep.received_at.replace('T',' ').slice(0,16) : '';
      const rowStyle = isUnread ? 'background:rgba(var(--accent-rgb,99,102,241),.07)' : '';

      const tr = document.createElement('tr');
      tr.id = `inbox-row-${rep.id}`;
      tr.style.cssText = rowStyle;
      tr.innerHTML = `
        <td style="font-weight:${isUnread?'600':'400'}">${name}${username}</td>
        <td style="max-width:260px;word-break:break-word;font-size:.85rem">${short}</td>
        <td>${watcher}</td>
        <td style="font-size:.82rem;color:var(--text2)">${acc}</td>
        <td style="font-size:.78rem;color:var(--text2);white-space:nowrap">${dt}</td>
        <td>${isUnread
          ? `<button class="btn btn-ghost" style="padding:4px 10px;font-size:.75rem" onclick="App.inboxMarkRead(${rep.id})">✓ Đọc</button>`
          : `<span style="font-size:.75rem;color:var(--text2)">✓</span>`
        }</td>
      `;
      tbody.appendChild(tr);
    });

    if(more) more.style.display = this._inboxHasMore ? 'inline-block' : 'none';
  },

  // ── Mark single as read ────────────────────────────────────────
  async inboxMarkRead(id){
    await fetch(`/api/inbox/${id}/read`, {method:'POST'});
    const row = document.getElementById(`inbox-row-${id}`);
    if(row){
      row.style.background = '';
      const td = row.querySelector('td:last-child');
      if(td) td.innerHTML = '<span style="font-size:.75rem;color:var(--text2)">✓</span>';
      const nameCell = row.querySelector('td:first-child');
      if(nameCell) nameCell.style.fontWeight = '400';
    }
    // Refresh badge
    this.startInboxBadgePolling && clearInterval(this._inboxBadgeTimer);
    this._inboxBadgeTimer = null;
    this.startInboxBadgePolling();
  },

  // ── Mark all as read ──────────────────────────────────────────
  async inboxReadAll(){
    await fetch('/api/inbox/read-all', {method:'POST'});
    App.toast('Đã đánh dấu tất cả đã đọc', 'success');
    this.inboxLoad(true);
    const badge = document.getElementById('inbox-badge');
    if(badge) badge.classList.add('hidden');
  },

  // ── Load more (pagination) ────────────────────────────────────
  async inboxLoadMore(){
    await this.inboxLoad(false);
  },
});

// Start badge polling once dashboard is visible
const _origShowDashboard = App.showDashboard.bind(App);
App.showDashboard = function(user){
  _origShowDashboard(user);
  App.startInboxBadgePolling();
};

// ══════════════════════════════════════════════════════════
//  PROXY POOL MANAGEMENT
// ══════════════════════════════════════════════════════════

App.switchProxyTab = function(tab) {
  const wsTab = document.getElementById('proxy-webshare-tab');
  const pasteTab = document.getElementById('proxy-paste-tab');
  const wsBtn = document.getElementById('proxy-tab-webshare');
  const pasteBtn = document.getElementById('proxy-tab-paste');
  if (tab === 'webshare') {
    wsTab.classList.remove('hidden');
    pasteTab.classList.add('hidden');
    wsBtn.style.background = 'var(--accent)'; wsBtn.style.color = '#fff'; wsBtn.className = 'btn btn-sm';
    pasteBtn.style.background = ''; pasteBtn.style.color = ''; pasteBtn.className = 'btn btn-ghost btn-sm';
  } else {
    wsTab.classList.add('hidden');
    pasteTab.classList.remove('hidden');
    pasteBtn.style.background = 'var(--accent)'; pasteBtn.style.color = '#fff'; pasteBtn.className = 'btn btn-sm';
    wsBtn.style.background = ''; wsBtn.style.color = ''; wsBtn.className = 'btn btn-ghost btn-sm';
  }
};

App.fetchWebshare = async function() {
  const apiKey = (document.getElementById('webshare-api-key')?.value || "").trim();
  if (!apiKey) { App.toast('Nhập Webshare API Key', 'error'); return; }
  const proxyType = document.getElementById('webshare-proxy-type')?.value;
  const btn = document.getElementById('btn-fetch-webshare');
  const progress = document.getElementById('proxy-progress');
  const progressText = document.getElementById('proxy-progress-text');

  btn.disabled = true;
  btn.textContent = 'Đang xử lý...';
  progress.classList.remove('hidden');
  progressText.textContent = 'Đang fetch proxy từ Webshare → test → assign...';
  (document.getElementById('proxy-test-results')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');

  try {
    const resp = await fetch('/api/proxy/webshare', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...API.getHeaders() },
      body: JSON.stringify({ api_key: apiKey, proxy_type: proxyType, auto_assign: true })
    });
    const data = await resp.json();
    progress.classList.add('hidden');

    if (data.success) {
      App.toast(`✅ ${data.passed}/${data.fetched} proxy passed → đã assign!`, 'success');
      App._renderProxyTestResults(data);
      App.loadProxyStatus();
    } else {
      App.toast('❌ ' + (data.error || 'Lỗi không xác định'), 'error');
      if (data.test_results) App._renderProxyTestResults(data);
    }
  } catch(e) {
    progress.classList.add('hidden');
    App.toast('Lỗi: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '🚀 Fetch & Test & Assign';
  }
};

App.importProxyList = async function() {
  const rawText = (document.getElementById('proxy-paste-input')?.value || "").trim();
  if (!rawText) { App.toast('Paste proxy list vào textarea', 'error'); return; }
  const scheme = document.getElementById('paste-default-scheme')?.value;
  const btn = document.getElementById('btn-import-proxies');
  const progress = document.getElementById('proxy-progress');
  const progressText = document.getElementById('proxy-progress-text');

  btn.disabled = true;
  btn.textContent = 'Đang xử lý...';
  progress.classList.remove('hidden');
  progressText.textContent = 'Đang parse → test → assign...';
  (document.getElementById('proxy-test-results')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');

  try {
    const resp = await fetch('/api/proxy/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...API.getHeaders() },
      body: JSON.stringify({ raw_text: rawText, default_scheme: scheme, auto_assign: true })
    });
    const data = await resp.json();
    progress.classList.add('hidden');

    if (data.success) {
      App.toast(`✅ ${data.passed}/${data.parsed} proxy passed → pool: ${data.pool_total}`, 'success');
      App._renderProxyTestResults(data);
      App.loadProxyStatus();
    } else {
      App.toast('❌ ' + (data.error || 'Lỗi không xác định'), 'error');
      if (data.test_results) App._renderProxyTestResults(data);
    }
  } catch(e) {
    progress.classList.add('hidden');
    App.toast('Lỗi: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '📥 Import & Test & Assign';
  }
};

App._renderProxyTestResults = function(data) {
  const container = document.getElementById('proxy-test-results');
  const list = document.getElementById('proxy-test-list');
  const passedEl = document.getElementById('proxy-stat-passed');
  const failedEl = document.getElementById('proxy-stat-failed');

  passedEl.textContent = '✅ ' + (data.passed || 0) + ' passed';
  failedEl.textContent = '❌ ' + (data.failed || 0) + ' failed';

  if (data.test_results && data.test_results.length) {
    list.innerHTML = data.test_results.map(function(r) {
      const masked = (r.proxy || '').replace(/:([^:@]{3})[^@]*@/, ':$1***@');
      const icon = r.ok ? '✅' : '❌';
      const latency = r.latency_ms ? r.latency_ms + 'ms' : '';
      const err = r.error ? ' — ' + r.error.substring(0, 40) : '';
      const color = r.ok ? '#22c55e' : '#ef4444';
      return '<div style="padding:3px 0;color:' + color + '">' + icon + ' ' + masked + ' ' + latency + err + '</div>';
    }).join('');
  }
  container.classList.remove('hidden');
};

App.loadProxyStatus = async function() {
  try {
    const resp = await fetch('/api/proxy/status', { headers: API.getHeaders() });
    const data = await resp.json();

    // Load webshare key if stored
    if (data.webshare_configured) {
      try {
        const keyResp = await API.getSetting('webshare_api_key');
        if (keyResp.value) (document.getElementById('webshare-api-key') || me_dummy).value = keyResp.value;
      } catch(e) {}
    }

    // Render mapping
    const listEl = document.getElementById('proxy-mapping-list');
    if (listEl) {
      if (!data.accounts || !data.accounts.length) {
        listEl.innerHTML = '<p style="color:var(--text2);text-align:center;padding:12px">Chưa có tài khoản nào</p>';
      } else {
        listEl.innerHTML = data.accounts.map(function(a) {
          const masked = a.proxy_url ? a.proxy_url.replace(/:([^:@]{3})[^@]*@/, ':$1***@') : '';
          const statusIcon = a.has_proxy ? '🔒' : '⚠️';
          const statusColor = a.has_proxy ? '#a78bfa' : '#f59e0b';
          const removeBtn = a.has_proxy
            ? '<button class="btn btn-ghost btn-sm" onclick="App.removeAccountProxy(' + a.account_id + ')" style="font-size:.7rem;padding:1px 6px" title="Xóa proxy">✕</button>'
            : '';
          return '<div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid var(--border)">' +
            '<div><span style="margin-right:6px">' + (a.is_logged_in ? '🟢' : '🔴') + '</span>' +
            '<strong style="font-size:13px">' + (a.account_name || 'Acc #' + a.account_id) + '</strong></div>' +
            '<div style="display:flex;align-items:center;gap:6px">' +
            '<span style="color:' + statusColor + ';font-size:12px;font-family:monospace">' +
            statusIcon + ' ' + (masked || 'Không có proxy') + '</span>' + removeBtn + '</div></div>';
        }).join('');
      }
    }

    // Pool info
    const infoEl = document.getElementById('proxy-pool-info');
    if (infoEl) {
      infoEl.textContent = 'Pool: ' + data.pool_size + ' proxy | ' +
        data.accounts_with_proxy + ' có proxy / ' + data.accounts_without_proxy + ' chưa có';
    }
  } catch(e) {
    console.error('loadProxyStatus error:', e);
  }
};

App.removeAccountProxy = async function(accountId) {
  if (!await customConfirm('Xóa proxy khỏi account #' + accountId + '?')) return;
  try {
    const resp = await fetch('/api/proxy/remove/' + accountId, {
      method: 'POST',
      headers: API.getHeaders()
    });
    const data = await resp.json();
    if (data.success) {
      App.toast('Đã xóa proxy', 'success');
      App.loadProxyStatus();
    } else {
      App.toast(data.error || 'Lỗi', 'error');
    }
  } catch(e) { App.toast(e.message, 'error'); }
};

App.clearProxyPool = async function() {
  if (!await customConfirm('Xóa TẤT CẢ proxy khỏi tất cả accounts và pool?')) return;
  try {
    const resp = await fetch('/api/proxy/clear-pool', {
      method: 'POST',
      headers: API.getHeaders()
    });
    const data = await resp.json();
    if (data.success) {
      App.toast('Da xoa tat ca proxy', 'success');
      App.loadProxyStatus();
      (document.getElementById('proxy-test-results')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');
    }
  } catch(e) { App.toast(e.message, 'error'); }
};

// ══════════════════════════════════════════════════════════════════════════════
// ══ Warmup Module ═══════════════════════════════════════════════════════════
// ══════════════════════════════════════════════════════════════════════════════

const Warmup = {
  _selectedGroupId: null,
  _groups: [],
  _jobs: [],

  async init() {
    await Promise.all([this.loadGroups(), this.loadJobs(), this.loadRecentLogs()]);
  },

  // ── Groups ──
  async loadGroups() {
    try {
      const data = await API.get('/api/warmup/groups');
      this._groups = data.groups || [];
      const el = document.getElementById('warmup-group-list');
      if (!this._groups.length) {
        el.innerHTML = '<div class="empty-state"><p class="empty-state-text">Chua co nhom nao</p></div>';
        return;
      }
      el.innerHTML = this._groups.map(g => {
        const active = this._selectedGroupId === g.id;
        return `<div class="card" style="padding:12px;margin-bottom:8px;cursor:pointer;border:1px solid ${active ? 'var(--accent)' : 'var(--border)'}" onclick="Warmup.selectGroup(${g.id})">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <div><strong>${this._esc(g.name)}</strong>
              <span style="color:var(--text2);font-size:.85rem;margin-left:8px">${this._esc(g.chat_title || '')} ${g.chat_username ? '@' + g.chat_username : ''}</span>
              <span style="color:var(--text2);font-size:.78rem;margin-left:8px">ID: ${g.chat_id}</span>
            </div>
            <button onclick="event.stopPropagation();Warmup.deleteGroup(${g.id})" style="background:rgba(239,68,68,.15);border:1px solid rgba(239,68,68,.4);color:#f87171;border-radius:6px;padding:3px 10px;cursor:pointer;font-size:.78rem">Xoa</button>
          </div>
        </div>`;
      }).join('');
    } catch(e) { App.toast(e.message, 'error'); }
  },

  selectGroup(id) {
    this._selectedGroupId = id;
    this.loadGroups();
    this.loadScripts();
    (document.getElementById('warmup-scripts-section')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('hidden');
    const g = this._groups.find(x => x.id === id);
    (document.getElementById('warmup-scripts-title') || me_dummy).textContent = 'Scripts - ' + (g ? g.name : '');
  },

  openAddGroup() {
    (document.getElementById('wg-name') || me_dummy).value = '';
    (document.getElementById('wg-chat-id') || me_dummy).value = '';
    (document.getElementById('wg-chat-title') || me_dummy).value = '';
    (document.getElementById('wg-chat-username') || me_dummy).value = '';
    (document.getElementById('warmup-group-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('open');
  },

  async saveGroup() {
    const name = (document.getElementById('wg-name')?.value || "").trim();
    const chatId = (document.getElementById('wg-chat-id')?.value || "").trim();
    if (!name || !chatId) { App.toast('Nhap ten va chat ID', 'error'); return; }
    try {
      await API.post('/api/warmup/groups', {
        name,
        chat_id: chatId,
        chat_title: (document.getElementById('wg-chat-title')?.value || "").trim(),
        chat_username: (document.getElementById('wg-chat-username')?.value || "").trim()
      });
      (document.getElementById('warmup-group-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('open');
      App.toast('Da them nhom', 'success');
      this.loadGroups();
    } catch(e) { App.toast(e.message, 'error'); }
  },

  async deleteGroup(id) {
    if (!await customConfirm('Xoa nhom nay va tat ca scripts/jobs?')) return;
    try {
      await API.del('/api/warmup/groups/' + id);
      if (this._selectedGroupId === id) {
        this._selectedGroupId = null;
        (document.getElementById('warmup-scripts-section')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');
      }
      App.toast('Da xoa', 'success');
      this.loadGroups();
      this.loadJobs();
    } catch(e) { App.toast(e.message, 'error'); }
  },

  // ── Scripts ──
  async loadScripts() {
    if (!this._selectedGroupId) return;
    try {
      const data = await API.get('/api/warmup/groups/' + this._selectedGroupId + '/scripts');
      const scripts = data.scripts || [];
      const el = document.getElementById('warmup-script-list');
      if (!scripts.length) {
        el.innerHTML = '<div style="color:var(--text2);padding:12px">Chua co script nao. Them script de bat dau warmup.</div>';
        return;
      }
      el.innerHTML = scripts.map((s, i) => {
        const short = s.content.length > 120 ? s.content.substring(0, 120) + '...' : s.content;
        return `<div class="card" style="padding:10px;margin-bottom:6px">
          <div style="display:flex;justify-content:space-between;align-items:flex-start">
            <div style="flex:1">
              <div style="font-size:.85rem;white-space:pre-wrap">${this._esc(short)}</div>
              <div style="margin-top:4px"><span class="badge ${s.use_ai_remix ? 'badge-green' : 'badge-red'}">${s.use_ai_remix ? 'AI Remix' : 'Nguyen ban'}</span></div>
            </div>
            <button onclick="Warmup.deleteScript(${s.id})" style="background:rgba(239,68,68,.15);border:1px solid rgba(239,68,68,.4);color:#f87171;border-radius:6px;padding:3px 10px;cursor:pointer;font-size:.78rem;margin-left:8px">Xoa</button>
          </div>
        </div>`;
      }).join('');
    } catch(e) { App.toast(e.message, 'error'); }
  },

  openAddScript() {
    if (!this._selectedGroupId) { App.toast('Chon nhom truoc', 'error'); return; }
    (document.getElementById('ws-content') || me_dummy).value = '';
    (document.getElementById('ws-ai-remix') || me_dummy).checked = true;
    (document.getElementById('warmup-script-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('open');
  },

  async saveScript() {
    const content = (document.getElementById('ws-content')?.value || "").trim();
    if (!content) { App.toast('Nhap noi dung', 'error'); return; }
    try {
      await API.post('/api/warmup/groups/' + this._selectedGroupId + '/scripts', {
        content,
        use_ai_remix: document.getElementById('ws-ai-remix')?.checked ? 1 : 0
      });
      (document.getElementById('warmup-script-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('open');
      App.toast('Da them script', 'success');
      this.loadScripts();
    } catch(e) { App.toast(e.message, 'error'); }
  },

  async deleteScript(id) {
    if (!await customConfirm('Xoa script nay?')) return;
    try {
      await API.del('/api/warmup/scripts/' + id);
      App.toast('Da xoa', 'success');
      this.loadScripts();
    } catch(e) { App.toast(e.message, 'error'); }
  },

  // ── Jobs ──
  async loadJobs() {
    try {
      const data = await API.get('/api/warmup/jobs');
      this._jobs = data.jobs || [];
      const el = document.getElementById('warmup-job-list');
      if (!this._jobs.length) {
        el.innerHTML = '<div style="color:var(--text2);padding:12px">Chua co job nao.</div>';

        // Update stats
        const statsEl = document.getElementById('warmup-stats');
        statsEl.innerHTML = `
          <div class="stat-card"><div class="stat-label">Nhom</div><div class="stat-value accent">${this._groups.length}</div></div>
          <div class="stat-card"><div class="stat-label">Jobs</div><div class="stat-value">0</div></div>
          <div class="stat-card"><div class="stat-label">Dang chay</div><div class="stat-value green">0</div></div>
        `;
        return;
      }

      const running = this._jobs.filter(j => j.is_running || j.status === 'running').length;
      const statsEl = document.getElementById('warmup-stats');
      statsEl.innerHTML = `
        <div class="stat-card"><div class="stat-label">Nhom</div><div class="stat-value accent">${this._groups.length}</div></div>
        <div class="stat-card"><div class="stat-label">Jobs</div><div class="stat-value">${this._jobs.length}</div></div>
        <div class="stat-card"><div class="stat-label">Dang chay</div><div class="stat-value green">${running}</div></div>
      `;

      el.innerHTML = this._jobs.map(j => {
        const grp = this._groups.find(g => g.id === j.group_id);
        const grpName = grp ? grp.name : 'Nhom #' + j.group_id;
        const isRunning = j.is_running || j.status === 'running';
        const statusBadge = isRunning
          ? '<span class="badge badge-green">Dang chay</span>'
          : j.status === 'error'
            ? '<span class="badge badge-red">Loi</span>'
            : '<span class="badge">Dung</span>';
        const accIds = JSON.parse(j.account_ids || '[]');
        const lastPost = j.last_post_at ? j.last_post_at.replace('T', ' ').slice(0, 16) : 'Chua co';

        return `<div class="card" style="padding:12px;margin-bottom:8px">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <div>
              <strong>${this._esc(grpName)}</strong> ${statusBadge}
              <div style="font-size:.82rem;color:var(--text2);margin-top:4px">
                ${accIds.length} tai khoan | ${j.interval_min}-${j.interval_max} phut | ${j.schedule_start}-${j.schedule_end} | Limit: ${j.daily_post_limit}/ngay
              </div>
              <div style="font-size:.8rem;color:var(--text2)">
                Hom nay: ${j.posts_today}/${j.daily_post_limit} | Lan cuoi: ${lastPost}
              </div>
            </div>
            <div style="display:flex;gap:6px;flex-wrap:wrap">
              ${isRunning
                ? '<button class="btn btn-danger btn-sm" onclick="Warmup.stopJob(' + j.id + ')">Dung</button>'
                : '<button class="btn btn-primary btn-sm" onclick="Warmup.startJob(' + j.id + ')">Chay</button>'}
              <button class="btn btn-sm" style="background:rgba(99,102,241,.15);color:#818cf8;border:1px solid rgba(99,102,241,.3)" onclick="Warmup.viewJobLogs(${j.id})">Logs</button>
              <button onclick="Warmup.deleteJob(${j.id})" style="background:rgba(239,68,68,.15);border:1px solid rgba(239,68,68,.4);color:#f87171;border-radius:6px;padding:3px 10px;cursor:pointer;font-size:.78rem">Xoa</button>
            </div>
          </div>
        </div>`;
      }).join('');
    } catch(e) { App.toast(e.message, 'error'); }
  },

  async openAddJob() {
    // Populate groups select
    const sel = document.getElementById('wj-group');
    sel.innerHTML = this._groups.map(g => `<option value="${g.id}">${this._esc(g.name)}</option>`).join('');

    // Populate accounts
    const accEl = document.getElementById('wj-accounts');
    try {
      const d = await API.getAccounts();
      const accounts = d.accounts || [];
      accEl.innerHTML = accounts.filter(a => a.is_logged_in).map(a => {
        const name = a.user_info ? [a.user_info.first_name, a.user_info.last_name].filter(Boolean).join(' ') : a.name;
        return `<label style="display:flex;align-items:center;gap:4px;cursor:pointer;font-size:.85rem">
          <input type="checkbox" value="${a.id}" class="wj-acc-cb"> ${this._esc(name)}
        </label>`;
      }).join('');
    } catch(e) {
      accEl.innerHTML = '<span style="color:#f87171">Loi tai tai khoan</span>';
    }

    (document.getElementById('wj-interval-min') || me_dummy).value = '30';
    (document.getElementById('wj-interval-max') || me_dummy).value = '120';
    (document.getElementById('wj-start') || me_dummy).value = '09:00';
    (document.getElementById('wj-end') || me_dummy).value = '22:00';
    (document.getElementById('wj-daily-limit') || me_dummy).value = '10';
    (document.getElementById('warmup-job-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('open');
  },

  async saveJob() {
    const groupId = parseInt(document.getElementById('wj-group')?.value);
    if (!groupId) { App.toast('Chon nhom', 'error'); return; }
    const accIds = Array.from(document.querySelectorAll('.wj-acc-cb:checked')).map(cb => parseInt(cb.value));
    if (!accIds.length) { App.toast('Chon it nhat 1 tai khoan', 'error'); return; }
    try {
      await API.post('/api/warmup/jobs', {
        group_id: groupId,
        account_ids: accIds,
        interval_min: parseInt(document.getElementById('wj-interval-min')?.value) || 30,
        interval_max: parseInt(document.getElementById('wj-interval-max')?.value) || 120,
        schedule_start: document.getElementById('wj-start')?.value || '09:00',
        schedule_end: document.getElementById('wj-end')?.value || '22:00',
        daily_post_limit: parseInt(document.getElementById('wj-daily-limit')?.value) || 10
      });
      (document.getElementById('warmup-job-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('open');
      App.toast('Da tao job', 'success');
      this.loadJobs();
    } catch(e) { App.toast(e.message, 'error'); }
  },

  async startJob(id) {
    try {
      await API.post('/api/warmup/jobs/' + id + '/start');
      App.toast('Job da bat dau', 'success');
      setTimeout(() => this.loadJobs(), 1000);
    } catch(e) { App.toast(e.message, 'error'); }
  },

  async stopJob(id) {
    try {
      await API.post('/api/warmup/jobs/' + id + '/stop');
      App.toast('Dang dung job...', 'success');
      setTimeout(() => this.loadJobs(), 2000);
    } catch(e) { App.toast(e.message, 'error'); }
  },

  async deleteJob(id) {
    if (!await customConfirm('Xoa job nay?')) return;
    try {
      await API.del('/api/warmup/jobs/' + id);
      App.toast('Da xoa', 'success');
      this.loadJobs();
    } catch(e) { App.toast(e.message, 'error'); }
  },

  async viewJobLogs(jobId) {
    try {
      const data = await API.get('/api/warmup/jobs/' + jobId + '/logs?limit=50');
      const logs = data.logs || [];
      const tbody = document.getElementById('warmup-job-logs-body');
      if (!logs.length) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text2);padding:16px">Chua co log</td></tr>';
      } else {
        tbody.innerHTML = logs.map(l => {
          const time = l.posted_at ? l.posted_at.replace('T', ' ').slice(0, 16) : '';
          const msg = (l.message_sent || '').substring(0, 80);
          const statusColor = l.status === 'success' ? '#4ade80' : '#f87171';
          return `<tr>
            <td style="font-size:.82rem">${time}</td>
            <td>Acc #${l.account_id}</td>
            <td style="font-size:.82rem;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${this._esc(msg)}</td>
            <td style="color:${statusColor}">${l.status}${l.error_message ? ' - ' + this._esc(l.error_message.substring(0, 50)) : ''}</td>
          </tr>`;
        }).join('');
      }
      (document.getElementById('warmup-logs-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('open');
    } catch(e) { App.toast(e.message, 'error'); }
  },

  // ── Recent Logs (main page) ──
  async loadRecentLogs() {
    try {
      const data = await API.get('/api/warmup/jobs/0/logs?limit=20');
      // Fallback: load all jobs logs
      let logs = data.logs || [];
      if (!logs.length) {
        // Try loading logs from all jobs
        for (const j of this._jobs.slice(0, 5)) {
          const d = await API.get('/api/warmup/jobs/' + j.id + '/logs?limit=10');
          logs = logs.concat(d.logs || []);
        }
        logs.sort((a, b) => (b.posted_at || '').localeCompare(a.posted_at || ''));
        logs = logs.slice(0, 20);
      }
      const tbody = document.getElementById('warmup-log-body');
      if (!logs.length) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text2);padding:16px">Chua co log</td></tr>';
        return;
      }
      tbody.innerHTML = logs.map(l => {
        const time = l.posted_at ? l.posted_at.replace('T', ' ').slice(0, 16) : '';
        const msg = (l.message_sent || '').substring(0, 60);
        const statusColor = l.status === 'success' ? '#4ade80' : '#f87171';
        return `<tr>
          <td style="font-size:.82rem">${time}</td>
          <td>Job #${l.job_id}</td>
          <td>Acc #${l.account_id}</td>
          <td style="font-size:.82rem;max-width:250px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${this._esc(msg)}</td>
          <td style="color:${statusColor}">${l.status}</td>
        </tr>`;
      }).join('');
    } catch(e) {
      // Silent fail for initial load
    }
  },

  _esc(s) { return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
};
