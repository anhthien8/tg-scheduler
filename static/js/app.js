function shortName(s,n=20){s=String(s||'');return [...s].length>n?[...s].slice(0,n).join('').trimEnd()+'…':s}
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

async init(){
// Reveal the app shell immediately and drop the boot splash.
document.body.classList.add('app-ready');
const cachedAccs = sessionStorage.getItem('tgs_accounts_cache');
let hasCached = false;
if(cachedAccs){try{const parsed=JSON.parse(cachedAccs);if(parsed.accounts&&parsed.accounts.length>0){App._accounts=parsed.accounts;hasCached=true;(document.getElementById('login-page')?.classList||{add:()=>{},remove:()=>{}}).add('hidden');(document.getElementById('dashboard-page')?.classList||{add:()=>{},remove:()=>{}}).remove('hidden');const firstUi=parsed.accounts.find(a=>a.user_info)?.user_info;if(firstUi)this._setUserInfo(firstUi)}}catch{}}
// Defer initial API fetches until after window.load so browser tab spinner finishes immediately
if(document.readyState!=='complete'){await new Promise(r=>window.addEventListener('load',()=>setTimeout(r,0),{once:true}))}else{await new Promise(r=>setTimeout(r,0))}
if(hasCached){this.showDashboard(null);API.authStatus().then(s=>{if(s&&s.user)this._setUserInfo(s.user)}).catch(()=>{});return}
try{const s=await API.authStatus();if(s.authenticated){this.showDashboard(s.user);return}}catch{}

try{const a=await API.getAccounts();App._accounts=a.accounts||[];if(a.accounts&&a.accounts.length>0){this.showDashboard(null);return}}catch(e){if(e&&e.message==='Unauthorized'){/* Server cần đăng nhập: key cũ sai/hết hạn → xoá và hiện form username/mật khẩu */localStorage.removeItem('tgs_api_key');this.showLogin();return}}

this.showLogin()},

_setUserInfo(){const el=document.getElementById('user-info');if(!el)return;const esc=s=>String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');const draw=d=>{const u=d.username||'admin';el.innerHTML=`<span class="user-avatar">${esc(u[0].toUpperCase())}</span><span class="user-meta"><strong>${esc(u)}</strong><span>${d.role==='admin'?'Quản trị viên':'Thành viên'}</span></span>`};try{const c=JSON.parse(localStorage.getItem('tgs_whoami')||'null');if(c)draw(c)}catch(e){}
if(this._whoamiReq)return;this._whoamiReq=fetch('/api/whoami').then(r=>r.ok?r.json():null).then(d=>{if(d){localStorage.setItem('tgs_whoami',JSON.stringify(d));draw(d)}}).catch(()=>{})},

toast(m,t='info'){const e=document.createElement('div');e.className=`toast ${t}`;e.textContent=m;(document.getElementById('toasts') || document.createElement("div")).appendChild(e);setTimeout(()=>e.remove(),4000)},

toggleSidebar(){const sb=document.getElementById('sidebar');const ov=document.getElementById('sidebar-overlay');if(sb){sb.classList.toggle('open');}if(ov){ov.classList.toggle('open');}},


showLogin(){(document.getElementById('login-page')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('hidden');(document.getElementById('dashboard-page')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');
// Chưa có API key trong trình duyệt → bắt đăng nhập username/password trước; có rồi → form thêm tài khoản Telegram
const hasKey=!!localStorage.getItem('tgs_api_key');document.getElementById('dashboard-login-step')?.classList.toggle('hidden',hasKey);document.getElementById('login-step-setup')?.classList.toggle('hidden',!hasKey);
const sub=document.getElementById('login-subtitle');if(sub)sub.textContent=hasKey?'Thêm tài khoản Telegram đầu tiên':'Đăng nhập để tiếp tục';},

async dashboardLogin(){const u=(document.getElementById('dashboard-username')?.value||'').trim();const p=document.getElementById('dashboard-password')?.value||'';if(!u||!p)return this.toast('Nhập username và mật khẩu','error');
try{const res=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u,password:p})});const d=await res.json().catch(()=>({}));if(!res.ok)throw new Error(d.detail||'Đăng nhập thất bại');
localStorage.setItem('tgs_api_key',d.api_key);localStorage.setItem('tgs_whoami',JSON.stringify({username:d.username,role:d.role}));this.toast('Đăng nhập thành công!','success');location.reload()}catch(e){this.toast(e.message,'error')}},

showDashboard(user){(document.getElementById('login-page')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');(document.getElementById('dashboard-page')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('hidden');

this._setUserInfo();

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

// Community Engagement Page
if(page==='engagement'){
  document.querySelectorAll('[id^="view-"]').forEach(el=>el.classList.add('hidden'));
  const engView = document.getElementById('view-engagement');
  if(engView) engView.classList.remove('hidden');
  if(window.Engagement) Engagement.init();
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

// User guide page
if(page==='help'){
  document.querySelectorAll('[id^="view-"]').forEach(el=>el.classList.add('hidden'));
  const helpView = document.getElementById('view-help');
  if(helpView) helpView.classList.remove('hidden');
  if(window.Help) window.Help.init();
  return;
}

// AI Follow-Up Sales Agent Page
if(page==='ai-followup'){
  if(!document.getElementById('view-ai-followup')){
    const aiHtml = `<div id="view-ai-followup">
      <div class="toolbar">
        <div>
          <h2 class="page-title" style="margin-bottom:2px">💬 AI Agent</h2>
          <p style="margin:0;font-size:.85rem;color:var(--text2)">AI tự chat, giải đáp và chốt deal khi khách trả lời DM</p>
        </div>
        <div class="toolbar-left">
          <label class="toggle" title="Bật/tắt AI Follow-Up (lưu ngay)">
            <input type="checkbox" id="aifu-enabled" onchange="AIFollowUp.quickToggle()">
            <span class="toggle-slider"></span>
          </label>
          <button class="btn btn-ghost btn-sm" onclick="AIFollowUp.loadChats()" title="Tải lại danh sách lead">🔄 Làm mới</button>
        </div>
      </div>

      <!-- Tab bar -->
      <div style="display:flex;gap:var(--sp-2);margin-bottom:var(--sp-4)">
        <button class="btn btn-primary btn-sm" id="aifu-tab-leads-btn" onclick="AIFollowUp.switchTab('leads')">👥 Leads</button>
        <button class="btn btn-ghost btn-sm" id="aifu-tab-settings-btn" onclick="AIFollowUp.switchTab('settings')">⚙️ Cấu hình AI</button>
      </div>

      <!-- ══ TAB: LEADS ══ -->
      <div id="aifu-tab-leads">
        <div class="stats-grid" style="margin-bottom:var(--sp-4)">
          <div class="stat-card" style="cursor:pointer" onclick="AIFollowUp.filterByStatus('needs_human')" title="Bấm để lọc">
            <div class="stat-label">⚠️ Cần người thật</div>
            <div class="stat-value red" id="aifu-stat-human">–</div>
          </div>
          <div class="stat-card" style="cursor:pointer" onclick="AIFollowUp.filterByStatus('active')" title="Bấm để lọc">
            <div class="stat-label">🤖 AI đang chat</div>
            <div class="stat-value accent" id="aifu-stat-active">–</div>
          </div>
          <div class="stat-card" style="cursor:pointer" onclick="AIFollowUp.filterByStep('link_confirmed+')" title="KOL đã gắn link / có bài / đang hoạt động">
            <div class="stat-label">✅ Đã gắn link trở lên</div>
            <div class="stat-value green" id="aifu-stat-onboarded">–</div>
          </div>
          <div class="stat-card" style="cursor:pointer" onclick="AIFollowUp.filterByStatus('')" title="Bấm để xem tất cả">
            <div class="stat-label">Tổng lead</div>
            <div class="stat-value" id="aifu-stat-total">–</div>
          </div>
        </div>
        <!-- 5-step KOL funnel cards (kol_step) — click to filter -->
        <div id="aifu-step-cards" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin-bottom:var(--sp-4)"></div>

        <div id="aifu-status-chips" style="display:flex;gap:var(--sp-2);flex-wrap:wrap;margin-bottom:var(--sp-3)"></div>
        <div id="aifu-selection-bar" class="card hidden" style="padding:10px 12px;margin-bottom:var(--sp-3);border:1px solid var(--accent);display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <strong id="aifu-selected-count" style="margin-right:auto">Đã chọn 0 lead</strong>
          <button class="btn btn-ghost btn-sm" onclick="AIFollowUp.assignSelected()">📍 Phân công</button>
          <button class="btn btn-green btn-sm" onclick="AIFollowUp.bulkToggleAI('active')">▶️ Bật AI</button>
          <button class="btn btn-ghost btn-sm" onclick="AIFollowUp.bulkToggleAI('paused_admin')">⏸ Chat tay</button>
          <button class="btn btn-danger btn-sm" onclick="AIFollowUp.bulkBlacklistSelected()">🚫 Blacklist (Cấm AI 100%)</button>
          <button class="btn btn-primary btn-sm" onclick="AIFollowUp.bulkSendSelected()">✉️ Gửi hàng loạt</button>
          <button class="btn btn-ghost btn-sm" onclick="AIFollowUp._selected.clear();AIFollowUp.renderPage()">Bỏ chọn</button>
        </div>

        <div class="table-wrap">
          <table class="data-table">
            <thead>
              <tr>
                <th style="width:34px;text-align:center"><input type="checkbox" aria-label="Chọn tất cả lead trên trang" onchange="AIFollowUp.togglePageSelection(this.checked)"></th>
                <th>Lead</th>
                <th>Tier / Intent</th>
                <th>Số câu</th>
                <th>Bước KOL</th>
                <th>Trạng thái AI</th>
                <th>Cập nhật</th>
                <th style="width:170px">Hành động</th>
              </tr>
            </thead>
            <tbody id="aifu-chats-table-body">
              <tr><td colspan="8" style="text-align:center;padding:20px;color:var(--text2)">Đang tải lead...</td></tr>
            </tbody>
          </table>
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center;gap:var(--sp-3);margin-top:.5rem;flex-wrap:wrap">
          <div id="aifu-count" style="font-size:.8rem;color:var(--text2)" role="status"></div>
          <div id="aifu-pager" style="display:flex;gap:var(--sp-2);align-items:center;font-size:.8rem;color:var(--text2)"></div>
        </div>
      </div>

      <!-- ══ TAB: SETTINGS ══ -->
      <div id="aifu-tab-settings" class="hidden">
        <div class="card" style="padding:20px">
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
            <div>
              <label class="form-label" for="aifu-sys-prompt" style="font-weight:600">🎯 Kịch bản tư vấn (Sales Persona)</label>
              <textarea id="aifu-sys-prompt" class="form-input" rows="6" placeholder="Nhập vai AI chuyên gia tư vấn..."></textarea>
              <div style="font-size:11px;color:var(--text2);margin-top:4px">Tính cách, xưng hô và cách khéo léo chốt deal / gửi link onboard.</div>
            </div>
            <div>
              <label class="form-label" for="aifu-kb" style="font-weight:600">📚 Knowledge Base (Sản phẩm & Giá)</label>
              <textarea id="aifu-kb" class="form-input" rows="6" placeholder="Thông tin sản phẩm, bảng giá, FAQ, link onboard..."></textarea>
              <div style="font-size:11px;color:var(--text2);margin-top:4px">AI dùng thông tin này để trả lời thắc mắc của khách.</div>
            </div>
          </div>
          <div style="display:grid;grid-template-columns:1fr 2fr;gap:16px;margin-top:16px">
            <div>
              <label class="form-label" for="aifu-max-replies" style="font-weight:600">🔢 Số câu AI tối đa / user</label>
              <input type="number" id="aifu-max-replies" class="form-input" min="1" max="20" value="5">
            </div>
            <div>
              <label class="form-label" for="aifu-handover-kw" style="font-weight:600">🛑 Từ khóa bàn giao người thật (phân cách dấu phẩy)</label>
              <input type="text" id="aifu-handover-kw" class="form-input" placeholder="gặp admin, tư vấn viên, số điện thoại, lừa đảo...">
            </div>
            <div style="grid-column:1/-1">
              <label class="form-label" for="aifu-drip-cadence" style="font-weight:600">⏰ Nhịp follow-up mặc định (giờ cách nhau, phân cách dấu phẩy)</label>
              <input type="text" id="aifu-drip-cadence" class="form-input" placeholder="24, 72, 168">
              <small style="color:var(--text2);font-size:0.72rem;">Số giờ im lặng trước mỗi lần nhắc. Mặc định 24, 72, 168 = Ngày 1 / 3 / 7. Tăng dần, tối đa tùy ý.</small>
            </div>
            <div style="grid-column:1/-1;display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px">
              <div>
                <label class="form-label" for="aifu-drip-tier-a" style="font-weight:600;color:var(--green)">🔥 Nhịp Tier A (Hot Lead)</label>
                <input type="text" id="aifu-drip-tier-a" class="form-input" placeholder="12, 24, 48, 96">
                <small style="color:var(--text2);font-size:0.7rem;">Mặc định: 12, 24, 48, 96 (4 bước)</small>
              </div>
              <div>
                <label class="form-label" for="aifu-drip-tier-b" style="font-weight:600;color:var(--purple)">⚡ Nhịp Tier B (Warm Lead)</label>
                <input type="text" id="aifu-drip-tier-b" class="form-input" placeholder="24, 72, 168">
                <small style="color:var(--text2);font-size:0.7rem;">Mặc định: 24, 72, 168 (3 bước)</small>
              </div>
              <div>
                <label class="form-label" for="aifu-drip-tier-c" style="font-weight:600;color:var(--orange)">🌱 Nhịp Tier C (Cold Lead)</label>
                <input type="text" id="aifu-drip-tier-c" class="form-input" placeholder="48, 168">
                <small style="color:var(--text2);font-size:0.7rem;">Mặc định: 48, 168 (2 bước)</small>
              </div>
            </div>
          </div>
          <div style="text-align:right;margin-top:16px">
            <button class="btn btn-primary" onclick="AIFollowUp.saveSettings()">💾 Lưu cấu hình</button>
          </div>
        </div>
        <!-- Pre-approved commission ladder (kol_offer_ladder) -->
        <div class="card" style="padding:20px;margin-top:16px" id="aifu-ladder-card">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px">
            <div>
              <h3 style="margin:0 0 4px;font-size:14px">💰 Thang commission KOL (gói starter duyệt sẵn)</h3>
              <div style="font-size:12px;color:var(--text2)">Thang DUY NHẤT AI được trích số. Áp cho mọi KOL. Mỗi lần lưu tăng version, lưu lịch sử. Số % / $ ngoài thang trong reply AI sẽ bị chặn và chuyển người thật.</div>
            </div>
            <div id="aifu-ladder-meta" style="font-size:12px;color:var(--text2);white-space:nowrap">—</div>
          </div>
          <div class="table-wrap">
            <table class="data-table" style="font-size:13px">
              <thead><tr><th style="width:50px">Bậc</th><th>Mốc tổng volume / tháng (M USD)</th><th>RevShare %</th><th>Bonus / 1M taker ($)</th><th>Ghi chú</th><th style="width:40px"></th></tr></thead>
              <tbody id="aifu-ladder-tiers"><tr><td colspan="6" style="text-align:center;color:var(--text2);padding:12px">Đang tải...</td></tr></tbody>
            </table>
          </div>
          <div style="display:flex;gap:8px;margin:8px 0 12px;flex-wrap:wrap">
            <button class="btn btn-ghost btn-sm" onclick="AIFollowUp.ladderAddTier()">+ Thêm bậc</button>
            <button class="btn btn-ghost btn-sm" onclick="AIFollowUp.ladderResetDefault()" title="Nạp lại thang mặc định founder chốt 21/10 vào form (chưa lưu)">↺ Nạp mặc định</button>
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
            <div style="grid-column:1/-1;display:grid;grid-template-columns:1fr 1fr;gap:12px">
              <div>
                <label class="form-label" for="ladder-headline" style="font-weight:600">Câu mở (headline)</label>
                <input type="text" id="ladder-headline" class="form-input" placeholder="up to 80% revshare">
              </div>
              <div>
                <label class="form-label" for="ladder-effective-from" style="font-weight:600">Hiệu lực từ (YYYY-MM-DD)</label>
                <input type="text" id="ladder-effective-from" class="form-input" placeholder="2026-10-21">
              </div>
            </div>
            <div><label class="form-label" for="ladder-rule-upgrade" style="font-weight:600">Lên bậc</label><textarea id="ladder-rule-upgrade" class="form-input" rows="2"></textarea></div>
            <div><label class="form-label" for="ladder-rule-downgrade" style="font-weight:600">Hạ bậc</label><textarea id="ladder-rule-downgrade" class="form-input" rows="2"></textarea></div>
            <div><label class="form-label" for="ladder-rule-threshold_basis" style="font-weight:600">Cách tính mốc</label><textarea id="ladder-rule-threshold_basis" class="form-input" rows="2"></textarea></div>
            <div><label class="form-label" for="ladder-rule-bonus_basis" style="font-weight:600">Cách tính bonus</label><textarea id="ladder-rule-bonus_basis" class="form-input" rows="2"></textarea></div>
            <div><label class="form-label" for="ladder-rule-commission_payout" style="font-weight:600">Trả commission</label><textarea id="ladder-rule-commission_payout" class="form-input" rows="2"></textarea></div>
            <div><label class="form-label" for="ladder-rule-bonus_payout" style="font-weight:600">Trả bonus</label><textarea id="ladder-rule-bonus_payout" class="form-input" rows="2"></textarea></div>
            <div><label class="form-label" for="ladder-rule-exclusions" style="font-weight:600">Loại trừ</label><textarea id="ladder-rule-exclusions" class="form-input" rows="2"></textarea></div>
            <div><label class="form-label" for="ladder-rule-sub_affiliate" style="font-weight:600">Sub-affiliate</label><textarea id="ladder-rule-sub_affiliate" class="form-input" rows="2"></textarea></div>
            <div style="grid-column:1/-1"><label class="form-label" for="ladder-rule-bonus_dip_explanation" style="font-weight:600">Giải thích bonus giảm ở bậc 4–5</label><textarea id="ladder-rule-bonus_dip_explanation" class="form-input" rows="2"></textarea></div>
            <div style="grid-column:1/-1"><label class="form-label" for="ladder-rule-why_us" style="font-weight:600">Lý do chọn WEEX khi KOL so sánh (mỗi dòng 1 ý; không nhắc tên sàn khác)</label><textarea id="ladder-rule-why_us" class="form-input" rows="3"></textarea></div>
          </div>
          <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:12px;flex-wrap:wrap">
            <div style="display:flex;gap:8px">
              <button class="btn btn-ghost btn-sm" onclick="AIFollowUp.toggleLadderPreview()">👁 Xem khối prompt AI nhận</button>
              <button class="btn btn-ghost btn-sm" onclick="AIFollowUp.loadLadderHistory(true)">🕘 Lịch sử phiên bản</button>
            </div>
            <button class="btn btn-primary" onclick="AIFollowUp.saveLadder()">💾 Lưu thang (tăng version)</button>
          </div>
          <pre id="aifu-ladder-preview" class="hidden" style="margin-top:12px;max-height:260px;overflow:auto;font-size:11px;background:var(--bg3);border:1px solid var(--border);border-radius:var(--radius);padding:10px;white-space:pre-wrap"></pre>
          <div id="aifu-ladder-history" class="hidden" style="margin-top:12px;font-size:12px;color:var(--text2)"></div>
        </div>
        <div class="card" style="padding:20px;margin-top:16px">
          <h3 style="margin:0 0 4px;font-size:14px">📡 Auto-forward bài channel cho KOL</h3>
          <div style="font-size:12px;color:var(--text2);margin-bottom:12px">Bài mới từ channel nguồn sẽ tự gửi tới KOL đã onboard theo phân công khu vực/campaign, tự gắn vipCode vào link WEEX. Tag bài: [vn] [global] [campaign:ten].</div>
          <div style="display:grid;grid-template-columns:auto 1fr 1fr;gap:12px;align-items:end">
            <label class="toggle" title="Bật/tắt auto-forward">
              <input type="checkbox" id="kolch-enabled"><span class="toggle-slider"></span>
            </label>
            <div>
              <label class="form-label" for="kolch-source" style="font-weight:600">Channel nguồn</label>
              <input type="text" id="kolch-source" class="form-input" placeholder="@weexkolglobal">
            </div>
            <div>
              <label class="form-label" for="kolch-account" style="font-weight:600">Account ID lắng nghe</label>
              <input type="number" id="kolch-account" class="form-input" placeholder="1">
            </div>
          </div>
          <div style="text-align:right;margin-top:12px">
            <button class="btn btn-primary btn-sm" onclick="AIFollowUp.saveKolChannelSettings()">💾 Lưu KOL Channel</button>
          </div>
        </div>
        <div class="card" style="padding:20px;margin-top:16px">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
            <div>
              <h3 style="margin:0 0 4px;font-size:14px">🎁 Referral / Viral Loop KOL</h3>
              <div style="font-size:12px;color:var(--text2)">Tạo mã giới thiệu <code>REF-XXXXXXXX</code> cho KOL đã onboard. Khi KOL mới nhắn mã này vào DM, hệ thống tự động ghi nhận quy kết (attribution).</div>
            </div>
            <button class="btn btn-ghost btn-sm" onclick="AIFollowUp.loadReferrals()">🔄 Làm mới</button>
          </div>
          <div id="aifu-ref-stats" style="display:flex;gap:16px;font-size:12px;color:var(--text2);margin-bottom:12px"></div>
          <div style="display:grid;grid-template-columns:1fr 1fr 1fr auto;gap:10px;align-items:end;margin-bottom:12px">
            <div>
              <label class="form-label" for="ref-acc-id" style="font-weight:600">Account ID</label>
              <input type="number" id="ref-acc-id" class="form-input" placeholder="VD: 1" value="1">
            </div>
            <div>
              <label class="form-label" for="ref-user-id" style="font-weight:600">KOL User ID (Referrer)</label>
              <input type="number" id="ref-user-id" class="form-input" placeholder="Telegram User ID">
            </div>
            <div>
              <label class="form-label" for="ref-max-uses" style="font-weight:600">Số lượt tối đa</label>
              <input type="number" id="ref-max-uses" class="form-input" value="10" min="1">
            </div>
            <button class="btn btn-primary btn-sm" style="height:36px" onclick="AIFollowUp.createReferralCode()">+ Tạo mã REF</button>
          </div>
          <div class="table-wrap">
            <table class="data-table">
              <thead><tr><th>Mã REF</th><th>KOL User ID</th><th>Lượt dùng</th><th>Trạng thái</th><th>Thao tác</th></tr></thead>
              <tbody id="aifu-ref-codes-body"><tr><td colspan="5" style="text-align:center;color:var(--text2);padding:12px">Đang tải...</td></tr></tbody>
            </table>
          </div>
        </div>
      </div>

      <!-- Chat History Modal -->
      <div id="aifu-history-modal" class="modal-overlay">
        <div class="modal" style="max-width:650px">
          <div class="modal-header">
            <h3 id="aifu-modal-title" style="margin:0;font-size:15px">Lịch sử trò chuyện</h3>
            <button class="modal-close" onclick="AIFollowUp.closeHistoryModal()" aria-label="Đóng">&times;</button>
          </div>
          <div id="aifu-modal-step-panel" style="padding:10px 16px 0"></div>
          <div class="modal-body" style="max-height:400px;overflow-y:auto;padding:16px" id="aifu-modal-chat-box">
          </div>
          <div style="display:flex;gap:.5rem;padding:10px 16px;border-top:1px solid var(--border)">
            <input id="aifu-chat-input" class="form-input" type="text" placeholder="Nhập tin nhắn gửi tới lead..." style="flex:1" onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();AIFollowUp.sendMessage()}">
            <button class="btn btn-primary" onclick="AIFollowUp.sendMessage()" id="aifu-chat-send-btn">Gửi ➤</button>
          </div>
          <div class="modal-footer" id="aifu-modal-actions"></div>
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


// ── Agent Evolution (đo tiến hóa AI) ─────────────────────────────
window.AgentEvolution = {
  _data: null,
  init(){ this.load(); },
  async load(){
    const days = parseInt(document.getElementById('evo-days')?.value || '30', 10);
    try{
      const d = await API.get('/api/analytics/agent-evolution?days=' + days);
      this._data = d; this._render(d);
    }catch(e){
      const v = document.getElementById('evo-verdict');
      if(v) v.innerHTML = '<span style="color:var(--red)">Không tải được dữ liệu: ' + (e && e.message ? e.message : e) + '</span>';
    }
  },
  _bar(elId, rows, valueKey, color){
    const el = document.getElementById(elId); if(!el) return;
    if(!rows || !rows.length){ el.innerHTML = '<div style="color:var(--text2);font-size:13px">Chưa có dữ liệu</div>'; return; }
    const max = Math.max(1, ...rows.map(r => r[valueKey] || 0));
    el.innerHTML = rows.map(r => {
      const v = r[valueKey] || 0, pct = Math.round(100 * v / max);
      return `<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
        <span style="width:64px;font-size:11px;color:var(--text2);flex-shrink:0">${esc(r.wk || '')}</span>
        <div style="flex:1;height:16px;background:var(--bg3);border-radius:3px;overflow:hidden">
          <div style="height:100%;width:${pct}%;background:${color};border-radius:3px;transition:width .3s"></div>
        </div>
        <span style="width:auto;min-width:38px;text-align:right;font-size:12px;font-weight:600;flex-shrink:0">${v}${valueKey==='handover_pct'?'%':''}</span>
      </div>`;
    }).join('');
  },
  _chips(elId, obj){
    const el = document.getElementById(elId); if(!el) return;
    const entries = Object.entries(obj || {}).filter(([k]) => k && k !== 'null');
    if(!entries.length){ el.innerHTML = '<div style="color:var(--text2);font-size:13px">Chưa có dữ liệu</div>'; return; }
    const total = entries.reduce((s,[,v]) => s + v, 0) || 1;
    el.innerHTML = entries.sort((a,b)=>b[1]-a[1]).map(([k,v]) => {
      const pct = Math.round(100 * v / total);
      return `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border)">
        <span style="font-size:13px">${esc(k)}</span>
        <span style="font-size:13px;font-weight:600">${v} <span style="color:var(--text2);font-weight:400">(${pct}%)</span></span></div>`;
    }).join('');
  },
  _render(d){
    const rep = d.repetition || {};
    const dis = d.discipline || {};
    const hw = (d.handover_per_week || []);
    const lastH = hw.length ? hw[hw.length - 1].handover_pct : 0;

    const set = (id, val) => { const e = document.getElementById(id); if(e) e.textContent = val; };
    set('evo-total-rules', rep.total_rules || 0);
    set('evo-distinct', rep.distinct_questions || 0);
    set('evo-redundant', (rep.redundant || 0) + ' (' + (rep.redundant_pct || 0) + '%)');
    set('evo-handover', lastH + '%');
    set('evo-avg-replies', dis.avg_replies || 0);
    set('evo-engaged', (dis.engaged || 0) + '/' + (dis.chats || 0));

    this._bar('evo-learn-chart', d.learning_per_week || [], 'n', 'color-mix(in srgb, var(--accent) 85%, transparent)');
    this._bar('evo-handover-chart', hw, 'handover_pct', 'color-mix(in srgb, var(--red) 80%, transparent)');
    this._chips('evo-funnel', d.funnel || {});
    this._chips('evo-stages', d.stages || {});

    // Verdict — đánh giá tổng hợp bằng tiếng người
    const v = document.getElementById('evo-verdict');
    if(v){
      const tone = (rep.redundant_pct || 0) > 20 ? 'var(--red)' : (rep.redundant_pct || 0) > 8 ? 'var(--orange)' : 'var(--green)';
      v.innerHTML = `<div style="font-size:14px;line-height:1.6">
        🧠 Agent đã học <b>${rep.total_rules || 0}</b> luật từ <b>${rep.distinct_questions || 0}</b> câu hỏi khác nhau.
        Tỷ lệ <b style="color:${tone}">học trùng ${rep.redundant_pct || 0}%</b>
        ${(rep.redundant_pct || 0) > 8 ? '(AI chưa áp dụng được bài học cũ — đang quên → đã bật chọn luật theo độ liên quan)' : '(AI tận dụng tốt bài học cũ)'}.
        Tuần gần nhất <b>${lastH}%</b> hội thoại phải nhờ người can thiệp.
      </div>`;
    }
  }
};

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

// Agent Evolution — đo mức độ tiến hóa của AI agents
if(page==='agent-evolution'){
  if(!document.getElementById('view-agent-evolution')){
    const html = `<div id="view-agent-evolution">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;flex-wrap:wrap;gap:10px">
        <h2 class="page-title" style="margin:0">🧬 Agent Evolution</h2>
        <div style="display:flex;align-items:center;gap:8px">
          <label class="form-label" style="margin:0">Khoảng:</label>
          <select id="evo-days" class="form-select" style="width:auto" onchange="AgentEvolution.load()">
            <option value="30">30 ngày</option>
            <option value="60">60 ngày</option>
            <option value="90">90 ngày</option>
            <option value="180">180 ngày</option>
          </select>
          <button class="btn btn-primary btn-sm" onclick="AgentEvolution.load()">🔄 Làm mới</button>
        </div>
      </div>
      <div class="card" id="evo-verdict" style="padding:14px 16px;margin-bottom:20px"></div>
      <div class="stats-grid" style="margin-bottom:24px">
        <div class="stat-card"><div class="stat-label">Luật đã học</div><div class="stat-value accent" id="evo-total-rules">–</div></div>
        <div class="stat-card"><div class="stat-label">Câu hỏi phân biệt</div><div class="stat-value green" id="evo-distinct">–</div></div>
        <div class="stat-card"><div class="stat-label">Học trùng lặp (quên)</div><div class="stat-value" id="evo-redundant">–</div></div>
        <div class="stat-card"><div class="stat-label">% cần người (tuần mới nhất)</div><div class="stat-value" id="evo-handover">–</div></div>
        <div class="stat-card"><div class="stat-label">TB tin AI / hội thoại</div><div class="stat-value" id="evo-avg-replies">–</div></div>
        <div class="stat-card"><div class="stat-label">Hội thoại có AI trả lời</div><div class="stat-value green" id="evo-engaged">–</div></div>
      </div>
      <div class="card" style="padding:16px;margin-bottom:20px">
        <h3 style="margin:0 0 12px">📚 Tốc độ học (luật mới mỗi tuần)</h3>
        <div id="evo-learn-chart" style="min-height:90px"></div>
      </div>
      <div class="card" style="padding:16px;margin-bottom:20px">
        <h3 style="margin:0 0 12px">🛑 Tỷ lệ cần người can thiệp mỗi tuần <span style="font-weight:400;font-size:12px;color:#888">(càng thấp càng tốt)</span></h3>
        <div id="evo-handover-chart" style="min-height:90px"></div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:20px">
        <div class="card" style="padding:16px">
          <h3 style="margin:0 0 12px">🎯 Phân loại Lead</h3>
          <div id="evo-funnel"></div>
        </div>
        <div class="card" style="padding:16px">
          <h3 style="margin:0 0 12px">🚦 Giai đoạn Pipeline</h3>
          <div id="evo-stages"></div>
        </div>
      </div>
    </div>`;
    const w = document.createElement('div'); w.innerHTML = html;
    document.querySelector('.main').appendChild(w.firstElementChild);
  }
  document.querySelectorAll('[id^="view-"]').forEach(el=>el.classList.add('hidden'));
  (document.getElementById('view-agent-evolution')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('hidden');
  AgentEvolution.init();
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

      <!-- Safety Warning Banner -->
      <div class="card" style="background:color-mix(in srgb, var(--orange) 10%, transparent);border:1px solid color-mix(in srgb, var(--orange) 40%, transparent);padding:16px;margin-bottom:20px">
        <div style="display:flex;align-items:center;gap:10px">
          <span style="font-size:1.5rem">⚠️</span>
          <div>
            <strong style="color:var(--orange)">Lưu ý An toàn</strong>
            <p style="margin:4px 0 0;font-size:.85rem;color:var(--text2)">Chỉ thêm nhóm PUBLIC dùng cho mục đích warmup. KHÔNG thêm nhóm private hoặc nhóm có khách hàng tiềm năng (KOL) để tránh lộ bot.</p>
          </div>
        </div>
      </div>

      <!-- Stats (Enhanced) -->
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

      <!-- Account Warmup Progress Panel -->
      <div style="margin-top:24px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
          <h3 style="margin:0">📊 Account Progress</h3>
          <button class="btn btn-sm" style="background:color-mix(in srgb, var(--accent) 15%, transparent);color:var(--accent-text);border:1px solid color-mix(in srgb, var(--accent) 30%, transparent)" onclick="Warmup.loadProgress()">↻ Refresh</button>
        </div>
        <div id="warmup-progress-list" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:12px"></div>
      </div>

      <!-- Action Logs v2 -->
      <div style="margin-top:24px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px">
          <h3 style="margin:0">📋 Action Logs</h3>
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <select id="warmup-log-filter-account" class="form-select" style="width:auto;min-width:140px;padding:4px 8px;font-size:.82rem" onchange="Warmup.loadActionLogs()">
              <option value="">Tat ca accounts</option>
            </select>
            <select id="warmup-log-filter-status" class="form-select" style="width:auto;min-width:120px;padding:4px 8px;font-size:.82rem" onchange="Warmup.loadActionLogs()">
              <option value="">Tat ca status</option>
              <option value="success">✅ Success</option>
              <option value="error">❌ Error</option>
              <option value="flood">🟠 Flood</option>
            </select>
            <label style="display:flex;align-items:center;gap:4px;font-size:.82rem;cursor:pointer;color:var(--text2)">
              <input type="checkbox" id="warmup-log-autorefresh" onchange="Warmup.toggleAutoRefresh()"> Auto-refresh
            </label>
          </div>
        </div>
        <div class="table-wrap">
          <table class="data-table"><thead><tr>
            <th>Thoi gian</th><th>Account</th><th>Action</th><th>Target Chat</th><th>Status</th><th>Error</th>
          </tr></thead><tbody id="warmup-action-log-body"></tbody></table>
        </div>
      </div>

      <!-- Legacy Logs (kept for compatibility) -->
      <div style="margin-top:24px">
        <h3>Logs gan day (Jobs)</h3>
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
  Warmup.cleanup();
  Warmup.init();
  return;
}

// ── Changelog page ──
if(page==='changelog'){
  if(!document.getElementById('view-changelog')){
    const CHANGELOG = [
      {
        version: '2.11.0', date: '2026-09-29', tag: 'latest',
        changes: [
          { type: 'new', text: 'Forum Inbox: dán link nhóm là tự lấy ID — hỗ trợ t.me/c/<id>/<topic> (offline), t.me/<username> và link mời t.me/+... (resolve qua account đang kết nối)' },
          { type: 'new', text: 'Lọc Lead Chất Lượng (ICP Score) — chấm điểm lead TRƯỚC khi gửi (username, tên, Premium, hoạt động gần đây) và bỏ qua lead dưới ngưỡng; đặt ở ô "🎯 Lọc Lead Chất Lượng" trong campaign (0 = tắt)' },
          { type: 'new', text: 'Nhịp follow-up tùy chỉnh — từ 2 bước cố định thành cadence cấu hình được (mặc định Ngày 1/3/7) trong AI Follow-Up Settings' },
          { type: 'new', text: 'Tự động xoay Proxy khi bị ban — PeerFlood sẽ tự đổi IP mới từ pool để gỡ giới hạn (tắt bằng setting proxy_rotate_on_ban=0)' },
          { type: 'new', text: 'Cảnh báo Speed-to-Reply — ping khi lead nóng chờ phản hồi quá lâu (mặc định >30 phút, cấu hình reply_sla_minutes)' },
          { type: 'improve', text: 'Cá nhân hóa sâu hơn — AI remix ở watcher nay dùng tên + chủ đề nhóm nguồn + ngôn ngữ lead; thêm biến {group}/{community} trong template' },
          { type: 'improve', text: 'Watcher dùng template rotation + auto-translate-native giống campaign' },
        ]
      },
      {
        version: '2.9.0', date: '2026-09-28',
        changes: [
          { type: 'new', text: 'Backtester dữ liệu thật — replay lịch sử outreach, funnel reply/convert/onboard, đo tốc độ phản hồi (median/p90), so sánh variant với khoảng tin cậy Wilson 95% + kiểm định ý nghĩa (thay cho open-rate giả)' },
          { type: 'new', text: 'Smart Template Rotation cho Keyword Watcher — mỗi tin nhắn DM có ô "Variant"; các tin cùng số tạo thành 1 biến thể để tự động xoay vòng A/B dựa trên hiệu suất' },
          { type: 'improve', text: 'Ghi nhận variant_index vào watcher_dm_logs để quy kết reply chính xác theo biến thể' },
          { type: 'fix', text: 'Sửa lỗi ngầm trong campaign: import `re` cục bộ che module-level gây UnboundLocalError khi phát hiện FloodWait' },
          { type: 'fix', text: 'Sửa định nghĩa hàm trùng lặp find_campaign_log_for_user / find_watcher_log_for_user (hàm sau ghi đè hàm trước)' },
          { type: 'improve', text: 'Hạ tầng QC/QA: gom toàn bộ test về một runner (pytest + node), thêm ruff/mypy/pytest-cov, CI GitHub Actions, gỡ test trùng lặp' },
        ]
      },
      {
        version: '2.8.0', date: '2026-08-01',
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

if(page==='dashboard')this.loadDashboard();else if(page==='schedules')this.loadSchedules();else if(page==='accounts')this.loadAccounts();else if(page==='logs')this.loadLogs();else if(page==='watchers')this.loadWatchers();else if(page==='watcher-logs')this.loadWatcherLogs();else if(page==='settings')this.loadSettings();else if(page==='blacklist')this.loadBlacklist();else if(page==='reactions')Reactions.init();else if(page==='members')Members.init()},


async loadDashboard(){try{
const[stats,sd,daily,health]=await Promise.all([API.getStats(),API.getSchedules({active_only:true,limit:10}),AnalyticsAPI.dailyStats(14).catch(()=>[]),AnalyticsAPI.accountHealth().catch(()=>[])]);
const set=(id,v)=>{const el=document.getElementById(id);if(el)el.textContent=v};
const today=(Array.isArray(daily)?daily:[]).slice(-1)[0]||{};
set('dash-kpi-sent',today.sent||0);set('dash-kpi-sent-sub',`Toàn bộ: ${stats.success} OK · ${stats.failed} lỗi`);
set('dash-kpi-replies',today.replies||0);set('dash-kpi-replies-sub',today.failed?`${today.failed} thất bại hôm nay`:'Không có thất bại hôm nay');
set('dash-kpi-accounts',stats.total_accounts);set('dash-kpi-accounts-sub',`${stats.active_schedules}/${stats.total_schedules} lịch hoạt động`);
// Leads cần người thật — fetch riêng, không block dashboard
fetch('/api/ai-followup/stats').then(r=>r.json()).then(d=>{const need=(d.counts&&d.counts.needs_human)||0;set('dash-kpi-leads',need);const sub=document.getElementById('dash-kpi-leads-sub');if(sub)sub.textContent=`${d.total||0} lead đang theo dõi`;const val=document.getElementById('dash-kpi-leads');if(val)val.className='stat-value '+(need>0?'red':'green')}).catch(()=>{set('dash-kpi-leads','—');const sub=document.getElementById('dash-kpi-leads-sub');if(sub)sub.textContent='Chưa bật AI Agent'});
this._dashDaily=Array.isArray(daily)?daily:[];this._renderDashChart(Array.isArray(daily)?daily:[]);
this._renderDashHealth(Array.isArray(health)?health:[]);
this.loadDashboardTasks();
const active=sd.schedules.filter(s=>s.next_run);const tbody=document.getElementById('upcoming-body');
const cnt=document.getElementById('dash-schedules-count');if(cnt)cnt.textContent=active.length?`${active.length} lịch đang chạy`:'';
if(!active.length){tbody.innerHTML='<tr><td colspan="6" style="text-align:center;color:var(--text2);padding:24px">Không có lịch nào sắp tới</td></tr>'}
else{tbody.innerHTML=active.map(s=>{const sends=s.max_sends?`${s.current_sends||0}/${s.max_sends}`:(s.current_sends||0);return`<tr><td>${esc(s.name)}</td><td>${esc(s.account_name||'—')}</td><td><span class="badge badge-blue">${s.schedule_type}</span></td><td>${s.time_of_day}</td><td>${formatDate(s.next_run)}</td><td>${sends}</td></tr>`}).join('')}
}catch(e){this.toast('Lỗi: '+e.message,'error')}},

_renderDashChart(data){const canvas=document.getElementById('dash-chart');if(!canvas)return;const ctx=canvas.getContext('2d');const dpr=window.devicePixelRatio||1;const W=canvas.width=canvas.parentElement.clientWidth*dpr;const H=canvas.height=200*dpr;canvas.style.height='200px';ctx.clearRect(0,0,W,H);
if(!data.length){ctx.fillStyle=_cv('--text',.35);ctx.font=`${12*dpr}px sans-serif`;ctx.textAlign='center';ctx.fillText('Chưa có dữ liệu',W/2,H/2);return}
const pad={top:16*dpr,right:12*dpr,bottom:28*dpr,left:44*dpr};const chartW=W-pad.left-pad.right;const chartH=H-pad.top-pad.bottom;
const maxVal=Math.max(1,...data.map(x=>Math.max(x.sent||0,x.failed||0,x.replies||0)));const xStep=data.length>1?chartW/(data.length-1):chartW;
ctx.strokeStyle=_cv('--text',.08);ctx.lineWidth=1;ctx.font=`${10*dpr}px sans-serif`;
for(let i=0;i<=4;i++){const y=pad.top+chartH*i/4;ctx.beginPath();ctx.moveTo(pad.left,y);ctx.lineTo(W-pad.right,y);ctx.stroke();ctx.fillStyle=_cv('--text',.45);ctx.textAlign='right';ctx.fillText(Math.round(maxVal*(4-i)/4),pad.left-8*dpr,y+4*dpr)}
const drawLine=(key,color)=>{ctx.beginPath();ctx.strokeStyle=_cv(color,1);ctx.lineWidth=2*dpr;data.forEach((x,i)=>{const px=pad.left+i*xStep;const py=pad.top+chartH-(x[key]||0)/maxVal*chartH;i===0?ctx.moveTo(px,py):ctx.lineTo(px,py)});ctx.stroke();ctx.lineTo(pad.left+(data.length-1)*xStep,pad.top+chartH);ctx.lineTo(pad.left,pad.top+chartH);ctx.closePath();ctx.fillStyle=_cv(color,.08);ctx.fill()};
drawLine('sent','--accent');drawLine('replies','--green');drawLine('failed','--red');
ctx.fillStyle=_cv('--text',.45);ctx.textAlign='center';const labelStep=Math.max(1,Math.floor(data.length/7));
data.forEach((x,i)=>{if(i%labelStep===0)ctx.fillText((x.date||'').slice(5),pad.left+i*xStep,H-8*dpr)})},

_renderDashHealth(list){const el=document.getElementById('dash-health-list');if(!el)return;
if(!list.length){el.innerHTML='<div style="text-align:center;color:var(--text2);padding:20px;font-size:13px">Chưa có tài khoản nào</div>';return}
const sorted=[...list].sort((a,b)=>(a.health_score||0)-(b.health_score||0)).slice(0,8);
el.innerHTML=sorted.map(a=>{const score=a.health_score||0;const color=score>=80?'var(--green)':score>=50?'var(--orange)':'var(--red)';
return`<div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border)">
<div style="flex:1;min-width:0"><div style="font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(a.account_name||'Account #'+a.account_id)} ${a.is_flagged?'<span style="color:var(--red)" title="Bị cảnh báo">🚩</span>':''}</div>
<div style="height:3px;border-radius:2px;background:var(--bg3);margin-top:5px"><div style="height:100%;width:${score}%;border-radius:2px;background:${color}"></div></div>
<div style="font-size:11px;color:var(--text2);margin-top:4px">
<span title="Số DM đã gửi thành công trong hôm nay">📤 ${a.dm_sent_today||0} hôm nay</span> · <span title="Tỷ lệ gửi thành công: DM gửi được / tổng số DM đã thử">✅ ${(a.success_rate||0).toFixed(0)}%</span> · <span title="Số lần bị Telegram chặn do gửi quá nhanh (FloodWait/PeerFlood). Càng cao càng dễ bị khóa">⚠️ ${a.flood_count||0} flood</span></div></div>
<span class="badge ${score>=80?'badge-green':score>=50?'badge-blue':'badge-red'}" style="flex-shrink:0" title="Điểm sức khỏe tài khoản (0-100). Từ 80 trở lên là an toàn, dưới 50 nên giảm tốc độ gửi">${score}</span></div>`}).join('')},

// ── Dashboard Tasks (Việc cần làm) ──────────────────────────────
_dashTasks: [],

async loadDashboardTasks(){
  try{
    const r=await fetch('/api/settings/dashboard_tasks');const d=await r.json();
    this._dashTasks=d.value?JSON.parse(d.value):[];
  }catch{this._dashTasks=[];}
  try{const d=await API.getAccounts();this._accIssues=this._buildAccIssues(d.accounts||[]);}catch{this._accIssues=[];}
  this._renderDashboardTasks();
},

async _saveDashTasks(){
  await fetch('/api/settings/dashboard_tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({value:JSON.stringify(this._dashTasks)})});
},

_accIssues: [],

// Problems on accounts that need someone to fix them, newest-worst first.
_buildAccIssues(accs){
  const out=[];
  for(const a of accs){
    const nm=(a.user_info?[a.user_info.first_name,a.user_info.last_name].filter(Boolean).join(' '):a.name)||('#'+a.id);
    if(a.session_error||!a.is_logged_in)out.push({lv:0,icon:'🔑',name:nm,text:a.session_error?`Session lỗi: ${a.session_error}`:'Chưa đăng nhập — cần OTP',fix:`App.loginAccount(${a.id},'${a.phone||''}')`,btn:'Đăng nhập'});
    if(a.is_flagged)out.push({lv:1,icon:'🚩',name:nm,text:a.flag_reason||'Bị gắn cờ cảnh báo',fix:`App.unflagAccount(${a.id})`,btn:'Bỏ cờ'});
    if(a.is_paused&&a.pause_reason)out.push({lv:2,icon:'⏸',name:nm,text:`Đang tạm dừng: ${a.pause_reason}`,fix:"App.navigate('accounts')",btn:'Xem'});
  }
  return out.sort((x,y)=>x.lv-y.lv);
},

_renderDashboardTasks(){
  const el=document.getElementById('dash-tasks-list');if(!el)return;
  const esc=this._esc.bind(this);
  const issues=(this._accIssues||[]).map(t=>`
    <div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border)">
      <span style="flex-shrink:0">${t.icon}</span>
      <div style="flex:1;min-width:0;font-size:12px;line-height:1.35"><b>${esc(t.name)}</b><div style="color:var(--text2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(t.text)}">${esc(t.text)}</div></div>
      <button class="btn btn-ghost btn-sm" style="padding:2px 8px;font-size:11px;flex-shrink:0" onclick="${t.fix}">${t.btn}</button>
    </div>`).join('');
  if(!this._dashTasks.length&&!issues){el.innerHTML='<div style="text-align:center;color:var(--text2);padding:20px;font-size:13px">🎉 Không có việc cần làm</div>';return}
  el.innerHTML=(issues?`<div style="font-size:11px;font-weight:600;color:var(--orange);margin-bottom:2px">⚠️ Tài khoản cần xử lý (${this._accIssues.length})</div>${issues}`:'')+this._dashTasks.map((t,i)=>`
    <div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border);${t.done?'opacity:.5':''}">
      <input type="checkbox" ${t.done?'checked':''} onchange="App.toggleDashTask(${i})" style="width:16px;height:16px;cursor:pointer;accent-color:var(--primary);flex-shrink:0">
      <span style="flex:1;font-size:13px;${t.done?'text-decoration:line-through;color:var(--text2)':''}">${esc(t.text)}</span>
      <button class="btn btn-ghost btn-sm" style="padding:2px 4px;font-size:12px;color:var(--text2)" onclick="App.removeDashTask(${i})" title="Xóa">×</button>
    </div>`).join('');
},

async addDashboardTask(){
  const text=prompt('Nội dung việc cần làm:');if(!text?.trim())return;
  this._dashTasks.unshift({text:text.trim(),done:false,created:new Date().toISOString()});
  await this._saveDashTasks();this._renderDashboardTasks();
},

async toggleDashTask(i){
  if(this._dashTasks[i])this._dashTasks[i].done=!this._dashTasks[i].done;
  await this._saveDashTasks();this._renderDashboardTasks();
},

async removeDashTask(i){
  this._dashTasks.splice(i,1);
  await this._saveDashTasks();this._renderDashboardTasks();
},

async loadAccounts(){try{const[d,agentData]=await Promise.all([API.getAccounts(),API.get('/api/ai-agents').catch(()=>({agents:[]}))]);this.accounts=d.accounts||[];App._accounts=this.accounts;this._aiAgents=agentData.agents||[];this.renderAccounts()}catch(e){this.toast(e.message,'error')}},

_accState(a){if(a.is_paused)return 'paused';if(a.session_error||!a.is_logged_in)return 'login';return 'active'},

filterAccounts(q){this._accQuery=(q||'').trim().toLowerCase();this.renderAccounts()},

setAccFilter(f){this._accFilter=f;this.renderAccounts()},

toggleAccMenu(ev){ev.stopPropagation();const m=document.getElementById('acc-menu-list');if(!m)return;const open=!m.classList.contains('open');m.classList.toggle('open',open);if(open){const close=()=>{m.classList.remove('open');document.removeEventListener('click',close)};setTimeout(()=>document.addEventListener('click',close),0)}},

renderAccounts(){
  const grid=document.getElementById('accounts-grid');if(!grid)return;
  const all=this.accounts||[];const f=this._accFilter||'all';const q=this._accQuery||'';
  const count={all:all.length,active:0,paused:0,login:0};all.forEach(a=>count[this._accState(a)]++);
  const sum=document.getElementById('acc-summary');if(sum)sum.textContent=`${count.all} tài khoản · ${count.active} đang chạy`;
  const tabs=document.getElementById('acc-tabs');
  if(tabs)tabs.innerHTML=[['all','Tất cả'],['active','Hoạt động'],['login','Cần đăng nhập'],['paused','Tạm dừng']].map(([k,l])=>`<button role="tab" aria-selected="${f===k}" class="acc-tab${f===k?' is-on':''}" onclick="App.setAccFilter('${k}')">${l}<span class="acc-tab-n">${count[k]}</span></button>`).join('');
  if(!all.length){grid.innerHTML='<div class="acc-empty">Chưa có tài khoản nào. Bấm “+ Thêm tài khoản” để bắt đầu.</div>';return}
  const list=all.filter(a=>{if(f!=='all'&&this._accState(a)!==f)return false;if(!q)return true;const ui=a.user_info||{};return [a.name,ui.first_name,ui.last_name,ui.username,a.phone,String(a.id)].some(v=>v&&String(v).toLowerCase().includes(q))});
  const sel=this._accSelected||new Set();
  const head=`<div class="acc-row acc-row-head" role="row"><span></span><span>Tài khoản</span><span>Trạng thái</span><span>AI Agent</span><span>Giờ chạy</span><span class="acc-col-end">Thao tác</span></div>`;
  if(!list.length){grid.innerHTML=head+'<div class="acc-empty">Không có tài khoản nào khớp bộ lọc.</div>';return}
  const STATE={active:['Hoạt động','Đang online'],login:['Cần đăng nhập',''],paused:['Tạm dừng','']};
  grid.innerHTML=head+list.map(a=>{
    const st=this._accState(a);const ui=a.user_info;
    const name=ui?[ui.first_name,ui.last_name].filter(Boolean).join(' '):a.name;
    const sub=ui?.username?`@${ui.username}`:(a.phone||`#${a.id}`);
    const tip=`#${a.id} · SĐT ${a.phone||'—'} · API ${a.api_id} · ${a.is_premium?50:10} DM/ngày`;
    const why=st==='paused'?(a.pause_reason||'Đã tắt thủ công'):st==='login'?(a.session_error||'Chưa đăng nhập'):STATE.active[1];
    const opts=(this._aiAgents||[]).map(ag=>`<option value="${ag.id}" ${a.ai_agent_id===ag.id?'selected':''}>${esc(ag.name)}</option>`).join('');
    return `<div class="acc-row account-card is-${st}" role="row" data-account-id="${a.id}">
  <span class="acc-cell-cb"><input type="checkbox" class="acc-select-cb" data-acc-id="${a.id}" ${sel.has(a.id)?'checked':''} onchange="App.onAccountSelectChange()" aria-label="Chọn ${esc(name)}"></span>
  <span class="acc-who" title="${esc(tip)}">
    <span class="acc-ava">${esc((name||'T').trim().charAt(0).toUpperCase())}</span>
    <span class="acc-id">
      <span class="acc-name"><b>${esc(name)}</b>${a.is_premium?'<span class="acc-tag" title="Premium · 50 DM/ngày">Premium</span>':''}${a.is_flagged?`<button class="acc-flag" onclick="App.showAccountWarnings(${a.id})" title="${esc(a.flag_reason||'')} — bấm để xem chi tiết">Cảnh báo</button>`:''}<span class="spam-badge-slot"></span></span>
      <span class="acc-sub">${esc(sub)} <button class="acc-link" onclick="App.togglePremium(${a.id}, ${!a.is_premium})">${a.is_premium?'Hạ Thường':'Nâng Premium'}</button></span>
    </span>
  </span>
  <span class="acc-status" title="${esc(why)}"><i></i>${STATE[st][0]}</span>
  <span class="acc-cell" data-label="AI Agent"><select class="acc-input" onchange="App.setAccountAiAgent(${a.id}, this.value)" aria-label="AI Agent"><option value="">Tắt AI</option>${opts}</select></span>
  <span class="acc-cell acc-hours" data-label="Giờ chạy"><input type="time" class="acc-input" id="acc-ah-start-${a.id}" value="${esc(a.active_hours_start||'08:00')}" onchange="App.setAccountActiveHours(${a.id})" aria-label="Giờ bắt đầu"><span>–</span><input type="time" class="acc-input" id="acc-ah-end-${a.id}" value="${esc(a.active_hours_end||'22:00')}" onchange="App.setAccountActiveHours(${a.id})" aria-label="Giờ dừng"></span>
  <span class="acc-actions">
    ${st==='login'?`<button class="btn btn-primary btn-sm" onclick="App.loginAccount(${a.id}, '${a.phone}')">Đăng nhập</button>`:''}
    ${a.is_paused?`<button class="acc-ib" onclick="App.toggleAccountActive(${a.id}, true)" title="Bật lại" aria-label="Bật lại">▶</button>`:`<button class="acc-ib" onclick="App.toggleAccountActive(${a.id}, false)" title="Tạm dừng" aria-label="Tạm dừng">❚❚</button>`}
    <button class="acc-ib is-danger" onclick="App.deleteAccount(${a.id})" title="Xóa tài khoản" aria-label="Xóa">✕</button>
  </span>
</div>`}).join('');
},

openAddAccountModal(){this._bulkQueue=null;document.getElementById('acc-step-parallel')?.classList.add('hidden');document.getElementById('acc-step-relogin')?.classList.add('hidden');this._bulkDone=[];this._bulkFailed=[];(document.getElementById('acc-step-info')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('hidden');(document.getElementById('acc-step-otp')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');(document.getElementById('acc-step-2fa')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');(document.getElementById('acc-phone') || me_dummy).value = '';const proxyEl=document.getElementById('acc-proxy');if(proxyEl)proxyEl.value='';const bulk=document.getElementById('acc-bulk-input');if(bulk)bulk.value='';document.getElementById('acc-bulk-progress')?.classList.add('hidden');document.getElementById('acc-bulk-skip')?.classList.add('hidden');this.setAccountAddMode('single');(document.getElementById('account-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('open')},

closeAccountModal(){(document.getElementById('account-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('open')},

async toggleAccountActive(accId,isActive){const actionText=isActive?'Bật (ON)':'Tắt (OFF)';const confirmMsg=isActive?'Bật lại tài khoản này? Tài khoản sẽ bắt đầu nhận tin nhắn và chạy tự động hóa trở lại.':'Bạn có chắc muốn TẮT tài khoản này?\nKhi TẮT: Tất cả AI Agent, Chiến dịch DM, Keyword Watcher và Tự động hóa của tài khoản này sẽ BỊ NGẮT HOÀN TOÀN để tránh ảnh hưởng danh bạ khách hàng.';if(!await customConfirm(confirmMsg))return;try{const res=await API.toggleAccountActive(accId,isActive);this.toast(res.message||`Đã ${actionText} tài khoản`,isActive?'success':'error');this.loadAccounts();}catch(e){this.toast(e.message,'error')}},

async setAccountAiAgent(accId,agentId){try{const res=await API.setAccountAiAgent(accId,agentId);const parsedId=agentId?parseInt(agentId):null;const acc=(this.accounts||[]).find(a=>a.id===accId);if(acc)acc.ai_agent_id=parsedId;this.toast(res.message||'Đã cập nhật AI Agent cho tài khoản','success');}catch(e){this.toast(e.message,'error');this.loadAccounts();}},

async setAccountActiveHours(accId){const start=document.getElementById(`acc-ah-start-${accId}`)?.value||'08:00';const end=document.getElementById(`acc-ah-end-${accId}`)?.value||'22:00';try{const res=await API.post(`/api/auth/accounts/${accId}/active-hours`,{active_hours_start:start,active_hours_end:end,active_timezone:'Asia/Ho_Chi_Minh'});API.clearAccountsCache();this.toast(res.message||`Đã lưu khung giờ ${start}-${end}`,'success');}catch(e){this.toast(e.message,'error')}},

async showAccountWarnings(accId){
  const a=(this.accounts||[]).find(x=>x.id===accId)||{};
  let m=document.getElementById('acc-warn-modal');
  if(!m){m=document.createElement('div');m.className='modal-overlay';m.id='acc-warn-modal';
    m.innerHTML='<div class="modal" style="width:600px"><div class="modal-header"><span class="modal-title" id="aw-title"></span><button class="modal-close" onclick="document.getElementById(&quot;acc-warn-modal&quot;).classList.remove(&quot;open&quot;)">&times;</button></div><div class="modal-body" id="aw-body"></div></div>';
    m.addEventListener('click',e=>{if(e.target===m)m.classList.remove('open')});document.body.appendChild(m)}
  document.getElementById('aw-title').textContent='Cảnh báo: '+(a.name||('#'+accId));
  const body=document.getElementById('aw-body');body.innerHTML='<div style="color:var(--text3);font-size:13px">Đang tải…</div>';m.classList.add('open');
  try{const d=await API.get(`/api/auth/accounts/${accId}/warnings`);
    const sec=(title,rows,hint)=>rows.length?`<div style="font-size:12px;color:var(--text3);margin:14px 0 6px">${title}</div>`+rows.map(r=>`<div style="display:flex;gap:10px;align-items:flex-start;padding:8px 0;border-bottom:1px solid var(--border)"><span class="badge badge-red" style="flex:none">${r.n}×</span><div style="font-size:13px;flex:1;min-width:0">${esc(r.error_message||'(không có mô tả)')}<div style="font-size:11px;color:var(--text3);margin-top:2px">${hint}: ${esc(r.sources||'—')}${r.chats?' · Nhóm: '+esc(r.chats):''} · Gần nhất ${formatDate(r.last_at)}</div></div></div>`).join(''):'';
    let h=`<div style="padding:10px 12px;border:1px solid var(--red);border-radius:var(--radius-sm);background:var(--red-dim);font-size:13px">${esc(d.flag_reason||'Bị gắn cờ cảnh báo')}<div style="color:var(--text2);margin-top:4px">Tài khoản bị gắn cờ khi có từ 5 lần gửi lỗi trong 24h. Xem nguyên nhân bên dưới, sửa xong thì bấm "Bỏ cảnh báo".</div></div>`;
    h+=sec('Lỗi gửi lịch gần nhất',d.schedule_errors,'Lịch')+sec('Lỗi Keyword DM gần nhất',d.keyword_errors,'Rule');
    const lastAt=[...d.schedule_errors,...d.keyword_errors].map(r=>r.last_at).sort().pop();
    const ageH=lastAt?(Date.now()-new Date(lastAt.replace(' ','T')+'Z'))/36e5:Infinity;
    if(ageH>24)h+=`<div style="color:var(--green);font-size:13px;margin-top:12px">${lastAt?`Lỗi cuối cùng cách đây ${Math.floor(ageH/24)} ngày`:'Không còn lỗi nào'} — tài khoản đã ổn, có thể bỏ cảnh báo.</div>`;
    h+=`<div style="display:flex;justify-content:flex-end;gap:8px;margin-top:14px">${d.schedule_errors.length?`<button class="btn btn-ghost btn-sm" onclick="document.getElementById('acc-warn-modal').classList.remove('open');App.navigate('logs')">Xem log gửi</button>`:''}<button class="btn btn-primary btn-sm" onclick="document.getElementById('acc-warn-modal').classList.remove('open');App.unflagAccount(${accId})">Bỏ cảnh báo</button></div>`;
    body.innerHTML=h;
  }catch(e){body.innerHTML=`<div style="color:var(--red)">${esc(e.message)}</div>`}
},
async unflagAccount(accId){if(!await customConfirm('Bỏ cảnh báo tài khoản này?'))return;try{await fetch(`/api/auth/accounts/${accId}/unflag`,{method:'POST'});API.clearAccountsCache();this.toast('Đã bỏ cảnh báo','success');this.loadAccounts();}catch(e){this.toast(e.message,'error')}},

async unpauseAccount(accId){if(!await customConfirm('Bỏ tạm dừng tài khoản này? Tài khoản sẽ hoạt động lại bình thường.'))return;try{await fetch(`/api/auth/accounts/${accId}/unpause`,{method:'POST'});API.clearAccountsCache();this.toast('Đã bỏ tạm dừng','success');this.loadAccounts();}catch(e){this.toast(e.message,'error')}},

async _populateLogAccountFilter(){const sel=document.getElementById('log-filter-account');if(!sel)return;const accs=this.accounts||App._accounts||[];if(!sel.options.length||sel.options.length===1){while(sel.options.length>1)sel.remove(1);accs.forEach(a=>{const opt=document.createElement('option');opt.value=a.id;opt.textContent=a.name||a.phone;sel.appendChild(opt);});} },

async loadBlacklist(){try{const res=await fetch('/api/blacklist');const raw=res.ok?await res.json():[];const data=Array.isArray(raw)?raw:[];this._blacklistData=data;const badge=document.getElementById('blacklist-badge');if(badge){if(data.length>0){badge.textContent=data.length;badge.classList.remove('hidden')}else{badge.classList.add('hidden')}}this._blPage=1;this._renderBlacklistPage()}catch(e){this._blacklistData=[];this._blPage=1;this._renderBlacklistPage();this.toast(e.message,'error')}},

_renderBlacklistPage(){const q=(document.getElementById('blacklist-search')?.value||'').toLowerCase().trim();const all=this._blacklistData||[];const showBots=document.getElementById('blacklist-show-bots')?.checked;const base=showBots?all:all.filter(b=>!(b.user_id&&b.user_id<0));const hiddenBots=all.length-base.length;const filtered=q?base.filter(b=>(String(b.user_id||'').includes(q))||(b.username||'').toLowerCase().includes(q)||(b.reason||'').toLowerCase().includes(q)):base;const pages=Math.max(1,Math.ceil(filtered.length/20));if(this._blPage>pages)this._blPage=pages;this._renderBlacklistTable(filtered.slice((this._blPage-1)*20,this._blPage*20),filtered.length===0,q);const countEl=document.getElementById('blacklist-count');if(countEl){const botNote=hiddenBots&&!showBots?` · ẩn ${hiddenBots} group/bot`:'';countEl.textContent=q?`Hiển thị: ${filtered.length}/${base.length}${botNote}`:(base.length?`Tổng: ${base.length} user bị chặn${botNote}`:'')};const pager=document.getElementById('blacklist-pager');if(pager)pager.innerHTML=pages>1?`<button class="btn btn-ghost btn-sm" onclick="App.blPage(${this._blPage-1})" ${this._blPage<=1?'disabled':''}>‹ Trước</button><span>Trang ${this._blPage}/${pages}</span><button class="btn btn-ghost btn-sm" onclick="App.blPage(${this._blPage+1})" ${this._blPage>=pages?'disabled':''}>Sau ›</button>`:'';},

blPage(n){this._blPage=n;this._renderBlacklistPage()},

_renderBlacklistTable(data,isEmpty,q){const tbody=document.getElementById('blacklist-body');if(isEmpty){tbody.innerHTML=q?`<tr><td colspan="4" style="text-align:center;color:var(--text2);padding:32px">Không tìm thấy user nào khớp "<strong>${esc(q)}</strong>"</td></tr>`:`<tr><td colspan="4" style="text-align:center;padding:32px"><div style="font-size:2rem;margin-bottom:8px">🎉</div><div style="color:var(--text);font-weight:600;margin-bottom:4px">Blacklist trống</div><div style="color:var(--text2);font-size:.85rem;margin-bottom:12px">Chưa có user nào bị chặn DM. Thêm user khi cần loại trừ khỏi mọi chiến dịch.</div><button class="btn btn-primary btn-sm" onclick="App.showAddBlacklist()">+ Thêm user đầu tiên</button></td></tr>`;return;}const colors=['var(--accent)','var(--purple)','var(--violet)','var(--orange)','var(--green)','var(--cyan)'];tbody.innerHTML=data.map(b=>{const name=b.username||`ID ${b.user_id}`;const letter=esc((name[0]||'?').toUpperCase());const color=colors[[...String(name)].reduce((s,c)=>s+c.charCodeAt(0),0)%colors.length];const uname=b.username?`@${esc(b.username)}`:'<span style="color:var(--text3)">(không có username)</span>';const uid=b.user_id?`<div style="font-size:.75rem;color:var(--text2);font-family:monospace">ID: ${b.user_id}</div>`:'';return `<tr><td><div style="display:flex;align-items:center;gap:10px"><span aria-hidden="true" style="width:32px;height:32px;border-radius:50%;background:${_mix(color,13)};border:1px solid ${_mix(color,33)};color:${color};display:inline-flex;align-items:center;justify-content:center;font-weight:700;font-size:.85rem;flex-shrink:0">${letter}</span><div><div style="font-weight:600;color:var(--accent-text)">${uname}</div>${uid}</div></div></td><td style="color:var(--text2);font-size:.85rem;max-width:280px">${esc(b.reason||'—')}</td><td style="font-size:.8rem;color:var(--text2);white-space:nowrap">${formatDate(b.created_at)}</td><td><button class="btn btn-danger btn-sm" onclick="App.removeBlacklist(${b.id})" aria-label="Xóa ${esc(name)} khỏi blacklist">🗑️ Xóa</button></td></tr>`}).join('');},

filterBlacklist(){clearTimeout(this._blSearchTimer);this._blSearchTimer=setTimeout(()=>{if(!this._blacklistData)return;this._blPage=1;this._renderBlacklistPage()},250);},

toggleBlacklistBots(){if(!this._blacklistData)return;this._blPage=1;this._renderBlacklistPage()},

showAddBlacklist(){['bl-add-username','bl-add-userid','bl-add-reason'].forEach(id=>{const el=document.getElementById(id);if(el)el.value=''});const o=document.getElementById('blacklist-add-modal');if(!o)return;o.classList.add('open');if(!this._blEscBound){this._blEscBound=true;document.addEventListener('keydown',e=>{if(e.key==='Escape'&&document.getElementById('blacklist-add-modal')?.classList.contains('open'))this.closeAddBlacklist()})}setTimeout(()=>document.getElementById('bl-add-username')?.focus(),50)},

closeAddBlacklist(){document.getElementById('blacklist-add-modal')?.classList.remove('open')},

async submitAddBlacklist(){const uname=(document.getElementById('bl-add-username')?.value||'').trim().replace(/^@/,'');const uidRaw=(document.getElementById('bl-add-userid')?.value||'').trim();const reason=(document.getElementById('bl-add-reason')?.value||'').trim();if(!uname&&!uidRaw){this.toast('Nhập ít nhất username hoặc user ID','error');document.getElementById('bl-add-username')?.focus();return}if(uidRaw&&!/^\d+$/.test(uidRaw)){this.toast('User ID phải là số','error');document.getElementById('bl-add-userid')?.focus();return}await this.addBlacklist(uidRaw?parseInt(uidRaw):null,uname||null,reason);this.closeAddBlacklist()},

async addBlacklist(userId,username,reason){try{await fetch('/api/blacklist',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:userId,username,reason})});this.toast('Đã thêm vào blacklist','success');this.loadBlacklist();}catch(e){this.toast(e.message,'error')}},

toggleBulkBlacklist(){const panel=document.getElementById('blacklist-bulk-panel');if(panel)panel.classList.toggle('hidden');},

async addBulkBlacklist(){const raw=(document.getElementById('blacklist-bulk-input')?.value||'').trim();const reason=(document.getElementById('blacklist-bulk-reason')?.value||'').trim()||'Bulk add';if(!raw){this.toast('Nhập ít nhất 1 username','error');return}const usernames=raw.split(/[\n,;]+/).map(u=>u.trim().replace(/^@/,'')).filter(u=>u.length>0);if(!usernames.length){this.toast('Không tìm thấy username hợp lệ','error');return}let ok=0,fail=0;for(const uname of usernames){try{await fetch('/api/blacklist',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:uname,reason})});ok++}catch(e){fail++}}this.toast(`Đã thêm ${ok} username vào blacklist${fail?` (${fail} lỗi)`:''}`,ok?'success':'error');document.getElementById('blacklist-bulk-input').value='';document.getElementById('blacklist-bulk-reason').value='';this.loadBlacklist();},

async removeBlacklist(id){if(!await customConfirm('Xóa user này khỏi blacklist?'))return;try{await fetch(`/api/blacklist/${id}`,{method:'DELETE'});this.toast('Đã xóa khỏi blacklist','success');this.loadBlacklist();}catch(e){this.toast(e.message,'error')}},

async addAccount(){const phone=(document.getElementById('acc-phone')?.value || "").trim();if(!phone)return this.toast('Nhập số điện thoại','error');const proxyUrl=(document.getElementById('acc-proxy')?.value || "").trim()||null;try{const r=await API.addAccount({phone,proxy_url:proxyUrl||null});this.loginAccountId=r.account_id;this.loginPhone=phone;const c=await API.sendCode(phone,r.account_id);this.phoneCodeHash=c.phone_code_hash;(document.getElementById('acc-step-info')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');(document.getElementById('acc-step-otp')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('hidden');this.toast('OTP đã gửi','success')}catch(e){this.toast(e.message||String(e),'error')}},

setAccountAddMode(mode){this._accAddMode=mode;const single=mode==='single';document.getElementById('acc-single-fields')?.classList.toggle('hidden',!single);document.getElementById('acc-bulk-fields')?.classList.toggle('hidden',single);document.getElementById('acc-mode-single')?.classList.toggle('btn-primary',single);document.getElementById('acc-mode-single')?.classList.toggle('btn-ghost',!single);document.getElementById('acc-mode-bulk')?.classList.toggle('btn-primary',!single);document.getElementById('acc-mode-bulk')?.classList.toggle('btn-ghost',single);},

async startBulkAccounts(){const raw=(document.getElementById('acc-bulk-input')?.value||'').trim();if(!raw)return this.toast('Nhập ít nhất 1 số điện thoại','error');
  this._bulkQueue=raw.split('\n').map(l=>l.trim()).filter(Boolean).map(l=>{const[phone,proxy]=l.split('|').map(s=>(s||'').trim());return{phone,proxy:proxy||null}});
  if(!this._bulkQueue.length)return this.toast('Danh sách trống','error');
  this._bulkDone=[];this._bulkFailed=[];this._bulk2fa=(document.getElementById('acc-bulk-2fa')?.value||'')||undefined;
  await this._startParallelLogin();
},

async _bulkAdvance(){
  if(!this._bulkQueue.length){this.toast(`Xong: ${this._bulkDone.length} OK${this._bulkFailed.length?`, ${this._bulkFailed.length} lỗi`:''}`,'success');this.closeAccountModal();this.loadAccounts();return}
  const{phone,proxy,id}=this._bulkQueue[0];
  document.getElementById('acc-step-2fa')?.classList.add('hidden');
  const progEl=document.getElementById('acc-bulk-progress');if(progEl){progEl.classList.remove('hidden');progEl.textContent=`Đang xử lý ${this._bulkDone.length+this._bulkFailed.length+1}/${this._bulkDone.length+this._bulkFailed.length+this._bulkQueue.length}: ${phone}`}
  try{
    const r=id?{account_id:id}:await API.addAccount({phone,proxy_url:proxy});
    this.loginAccountId=r.account_id;this.loginPhone=phone;
    const c=await API.sendCode(phone,r.account_id);this.phoneCodeHash=c.phone_code_hash;
    (document.getElementById('acc-step-info')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');(document.getElementById('acc-step-otp')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('hidden');
    const otpPhoneEl=document.getElementById('acc-otp-phone');if(otpPhoneEl)otpPhoneEl.textContent=`(${phone})`;
    this.toast(`OTP đã gửi cho ${phone}`,'success');
  }catch(e){
    this.toast(`Lỗi ${phone}: ${e.message||e}`,'error');
    this._bulkFailed.push(phone);this._bulkQueue.shift();
    await this._bulkAdvance();
  }
},

openBulkRelogin(ids){
  const accs=(this.accounts||App._accounts||[]).filter(a=>ids?ids.includes(a.id):!a.is_logged_in);
  if(!accs.length)return this.toast(ids?'Chưa chọn tài khoản':'Không có tài khoản nào cần đăng nhập lại','info');
  this._reloginAccs=accs;
  this.openAddAccountModal();
  document.getElementById('acc-step-info')?.classList.add('hidden');
  document.getElementById('acc-step-relogin')?.classList.remove('hidden');
  const list=document.getElementById('acc-relogin-list');
  if(list)list.innerHTML=accs.map(a=>`<label style="display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:6px;cursor:pointer;font-size:12.5px"><input type="checkbox" class="acc-relogin-cb" value="${a.id}" checked onchange="App.updateReloginCount()" style="width:15px;height:15px;accent-color:var(--primary);flex-shrink:0"><span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(a.name||'')}</span><span style="font-family:monospace;color:var(--text2)">${esc(a.phone||'')}</span></label>`).join('');
  const all=document.getElementById('acc-relogin-all');if(all)all.checked=true;
  const cnt=document.getElementById('acc-relogin-count');if(cnt)cnt.textContent=accs.length;
},

toggleReloginAll(on){document.querySelectorAll('.acc-relogin-cb').forEach(cb=>cb.checked=on);this.updateReloginCount()},

updateReloginCount(){const cbs=[...document.querySelectorAll('.acc-relogin-cb')];const n=cbs.filter(c=>c.checked).length;const cnt=document.getElementById('acc-relogin-count');if(cnt)cnt.textContent=n;const all=document.getElementById('acc-relogin-all');if(all){all.checked=n===cbs.length;all.indeterminate=n>0&&n<cbs.length}},

async startBulkRelogin(){
  const picked=new Set([...document.querySelectorAll('.acc-relogin-cb:checked')].map(c=>+c.value));
  const accs=(this._reloginAccs||[]).filter(a=>picked.has(a.id));if(!accs.length)return this.toast('Chọn ít nhất 1 tài khoản','error');
  this._bulkQueue=accs.map(a=>({phone:a.phone,proxy:null,id:a.id,name:a.name}));
  this._bulkDone=[];this._bulkFailed=[];this._bulk2fa=(document.getElementById('acc-relogin-2fa')?.value||'')||undefined;
  document.getElementById('acc-step-relogin')?.classList.add('hidden');
  await this._startParallelLogin();
},

// ── Parallel bulk login: gửi OTP cho mọi số, nhập OTP từng dòng theo thứ tự tuỳ ý ──
async _startParallelLogin(){
  const items=this._bulkQueue||[];this._bulkQueue=null;
  this._par=items.map((it,i)=>({...it,i,state:'sending',hash:null,err:''}));
  document.getElementById('acc-step-info')?.classList.add('hidden');
  document.getElementById('acc-step-relogin')?.classList.add('hidden');
  document.getElementById('acc-step-parallel')?.classList.remove('hidden');
  this._renderPar();
  // Gửi OTP lần lượt (cách nhau 1.5s để tránh FloodWait) nhưng không chờ nhập OTP.
  for(const r of this._par){
    if(!document.getElementById('account-modal')?.classList.contains('open'))break;
    try{
      if(!r.id){const a=await API.addAccount({phone:r.phone,proxy_url:r.proxy});r.id=a.account_id}
      const c=await API.sendCode(r.phone,r.id);r.hash=c.phone_code_hash;r.state='otp';
    }catch(e){r.state='error';r.err=e.message||String(e)}
    this._renderParRow(r);
    await new Promise(res=>setTimeout(res,1500));
  }
},

_parStatus(r){return{sending:['⏳','Đang gửi OTP…','var(--text2)'],otp:['📩','Nhập OTP','var(--accent-text)'],pw:['🔐','Cần 2FA','var(--orange)'],verifying:['⏳','Đang xác nhận…','var(--text2)'],done:['✅','Đã đăng nhập','var(--green)'],error:['⚠️','Lỗi','var(--red)'],skipped:['⏭','Đã bỏ qua','var(--text3)']}[r.state]},

_renderPar(){
  const box=document.getElementById('acc-par-list');if(!box)return;
  box.innerHTML=this._par.map(r=>`<div id="acc-par-${r.i}"></div>`).join('');
  this._par.forEach(r=>this._renderParRow(r));
},

_renderParRow(r){
  const el=document.getElementById(`acc-par-${r.i}`);if(!el)return;
  const[ic,label,color]=this._parStatus(r);
  const input=r.state==='otp'?`<input class="form-input" id="acc-par-in-${r.i}" placeholder="OTP" inputmode="numeric" maxlength="6" style="width:90px;padding:6px 8px" onkeydown="if(event.key==='Enter')App.parVerify(${r.i})"><button class="btn btn-primary btn-sm" onclick="App.parVerify(${r.i})">OK</button>`
    :r.state==='pw'?`<input class="form-input" type="password" id="acc-par-in-${r.i}" placeholder="2FA" style="width:90px;padding:6px 8px" onkeydown="if(event.key==='Enter')App.parVerify(${r.i})"><button class="btn btn-primary btn-sm" onclick="App.parVerify(${r.i})">OK</button>`:'';
  const resend=r.state==='error'?`<button class="btn btn-ghost btn-sm" title="Gửi lại OTP" onclick="App.parResend(${r.i})">↻ Gửi lại</button>`:'';
  const skip=['otp','pw','error'].includes(r.state)?`<button class="btn btn-ghost btn-sm" title="Bỏ qua" onclick="App.parSkip(${r.i})">✕</button>`:'';
  el.style.cssText='display:flex;align-items:center;gap:8px;padding:7px 8px;border-bottom:1px solid var(--border);font-size:12.5px';
  el.innerHTML=`<div style="flex:1;min-width:0"><div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600">${esc(r.name||r.phone)}</div><div style="font-family:monospace;color:var(--text2);font-size:11.5px">${esc(r.name?r.phone:'')}${r.tgUser?' · @'+esc(r.tgUser):''}</div><div style="color:${color};font-size:11.5px" title="${esc(r.err)}">${ic} ${label}${r.err?': '+esc(r.err):''}</div></div>${input}${resend}${skip}`;
  const done=this._par.filter(x=>x.state==='done').length;
  const prog=document.getElementById('acc-par-progress');if(prog)prog.textContent=`Đã đăng nhập ${done}/${this._par.length}`;
},

async parVerify(i){
  const r=this._par[i];const v=(document.getElementById(`acc-par-in-${i}`)?.value||'').trim();
  if(!v)return;
  const wasPw=r.state==='pw';if(!wasPw)r.code=v;
  r.state='verifying';r.err='';this._renderParRow(r);
  try{
    const res=await API.verify(r.phone,r.code,r.hash,r.id,wasPw?v:this._bulk2fa);
    if(res.needs_password){r.state='pw'}else{r.state='done';const tn=[res.first_name,res.last_name].filter(Boolean).join(' ').trim();if(tn)r.name=tn;if(res.username)r.tgUser=res.username}
  }catch(e){
    const m=e.message||String(e);
    if(m.includes('2FA')){r.state='pw';r.err=m}
    else if(m.includes('hết hạn')){this._renderParRow(r);await this.parResend(i);r.err='Mã cũ hết hạn, đã gửi OTP mới';this._renderParRow(r);return}
    else{r.state='otp';r.err=m}
  }
  this._renderParRow(r);
  if(r.state==='pw'||r.state==='otp')document.getElementById(`acc-par-in-${i}`)?.focus();
  else{const next=this._par.find(x=>x.state==='otp'&&x.i>i)||this._par.find(x=>x.state==='otp');if(next)document.getElementById(`acc-par-in-${next.i}`)?.focus()}
  if(this._par.every(x=>['done','skipped','error'].includes(x.state)))this.loadAccounts();
},

async parResend(i){const r=this._par[i];r.state='sending';r.err='';this._renderParRow(r);try{if(!r.id){const a=await API.addAccount({phone:r.phone,proxy_url:r.proxy});r.id=a.account_id}const c=await API.sendCode(r.phone,r.id);r.hash=c.phone_code_hash;r.state='otp'}catch(e){r.state='error';r.err=e.message||String(e)}this._renderParRow(r);document.getElementById(`acc-par-in-${i}`)?.focus()},

parSkip(i){const r=this._par[i];r.state='skipped';r.err='';this._renderParRow(r)},

finishParallelLogin(){const d=(this._par||[]).filter(x=>x.state==='done').length;this.toast(`Đã đăng nhập ${d}/${(this._par||[]).length} tài khoản`,'success');this.closeAccountModal();this.loadAccounts()},

async skipBulkAccount(){if(!this._bulkQueue?.length)return;const{phone}=this._bulkQueue.shift();this._bulkFailed.push(phone);await this._bulkAdvance();},

async verifyAccount(){const code=(document.getElementById('acc-code')?.value || "").trim();if(!code)return this.toast('Nhập OTP','error');

try{const r=await API.verify(this.loginPhone,code,this.phoneCodeHash,this.loginAccountId,this._bulkQueue?.length?this._bulk2fa:undefined);if(r.needs_password){if(this._bulkQueue?.length&&this._bulk2fa)this.toast(`2FA chung sai cho ${this.loginPhone}, nhập tay hoặc bỏ qua`,'error');(document.getElementById('acc-step-otp')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');(document.getElementById('acc-step-2fa')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('hidden');return}

this.toast('Đăng nhập thành công!','success');
const otpInput=document.getElementById('acc-code');if(otpInput)otpInput.value='';
if(this._bulkQueue?.length){
  this._bulkDone.push(this.loginPhone);this._bulkQueue.shift();
  await this._bulkAdvance();
}else{
  this.closeAccountModal();this.loadAccounts();
}}catch(e){if(e.message.includes('hết hạn')){try{const c=await API.sendCode(this.loginPhone,this.loginAccountId);this.phoneCodeHash=c.phone_code_hash;const ci=document.getElementById('acc-code');if(ci)ci.value='';this.toast('Mã OTP đã hết hạn — đã gửi mã mới, nhập lại nhé','info')}catch(e2){this.toast(e2.message,'error')}return}if(e.message.includes('2FA')){if(this._bulkQueue?.length)this.toast(`${this.loginPhone}: ${e.message} — nhập tay hoặc bỏ qua`,'error');(document.getElementById('acc-step-otp')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');(document.getElementById('acc-step-2fa')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('hidden')}else this.toast(e.message,'error')}},

async verify2FAAccount(){const pw=document.getElementById('acc-password')?.value;try{await API.verify(this.loginPhone,(document.getElementById('acc-code')?.value || "").trim(),this.phoneCodeHash,this.loginAccountId,pw);this.toast('OK!','success');
const pwInput=document.getElementById('acc-password');if(pwInput)pwInput.value='';
if(this._bulkQueue?.length){
  this._bulkDone.push(this.loginPhone);this._bulkQueue.shift();
  await this._bulkAdvance();
}else{
  this.closeAccountModal();this.loadAccounts();
}}catch(e){this.toast(e.message,'error')}},

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

return`<tr><td><label class="toggle"><input type="checkbox" ${s.is_active?'checked':''} onchange="App.toggleSchedule(${s.id})"><span class="toggle-slider"></span></label></td><td><strong>${esc(s.name)}</strong>${s.blocked_count > 0 ? `<span title="Có ${s.blocked_count} target bị block do lỗi quá 3 lần" onclick="App.showBlockedTargets(${s.id})" style="cursor:pointer;margin-left:.4rem;background:color-mix(in srgb, var(--red) 15%, transparent);border:1px solid color-mix(in srgb, var(--red) 40%, transparent);color:var(--red);border-radius:4px;padding:1px 6px;font-size:.7rem;font-weight:600;">⛔ ${s.blocked_count} blocked</span>` : ''}</td><td><small>${esc(s.account_name||'—')}</small></td><td><span class="badge badge-blue">${s.schedule_type}</span></td><td>${s.time_of_day}${s.schedule_type==='weekly'?'<br><small style="color:var(--text2)">'+formatDays(s.days_of_week)+'</small>':''}</td><td>${(s.messages||[]).length}</td><td>${(s.targets||[]).length}</td><td>${sends}</td><td style="font-size:12px;color:var(--text2)">${s.next_run?formatDate(s.next_run):'—'}</td><td><div class="btn-group"><button class="btn btn-ghost btn-sm" onclick="App.editSchedule(${s.id})" title="Sửa">✏️</button><button class="btn btn-green btn-sm" onclick="App.previewSchedule(${s.id})" title="Preview">👁</button><button class="btn btn-ghost btn-sm" onclick="App.sendNow(${s.id})" title="Gửi ngay">🚀</button><button class="btn btn-ghost btn-sm" onclick="App.resetCount(${s.id})" title="Reset count">🔄</button><button class="btn btn-danger btn-sm" onclick="App.deleteSchedule(${s.id})" title="Xóa">🗑</button></div></td></tr>`}).join('')}catch(e){this.toast('Lỗi: '+e.message,'error')}},

async toggleSchedule(id){try{const r=await API.toggleSchedule(id);this.toast(r.is_active?'Đã bật':'Đã tắt','success');this.loadSchedules()}catch(e){this.toast(e.message,'error')}},

async showBlockedTargets(scheduleId) {
  try {
    const res = await API.getBlockedTargets(scheduleId);
    if (!res.count) { this.toast('Không có target nào bị block', 'info'); return; }
    const blocks = res.blocked;
    let html = `<div style="background:color-mix(in srgb, var(--red) 8%, transparent);border:1px solid color-mix(in srgb, var(--red) 30%, transparent);border-radius:12px;padding:1.2rem;margin-bottom:1rem;">
      <div style="display:flex;align-items:center;gap:.6rem;margin-bottom:.8rem;">
        <span style="font-size:1.3rem;">⛔</span>
        <strong style="color:var(--red);font-size:.95rem;">Targets bị tắt do lỗi quá 3 lần (${blocks.length})</strong>
      </div>
      <table style="width:100%;border-collapse:collapse;font-size:.83rem;">
        <thead><tr style="border-bottom:1px solid color-mix(in srgb, var(--text) 10%, transparent)">
          <th style="text-align:left;padding:.4rem .6rem;color:var(--text-secondary)">Tài khoản</th>
          <th style="text-align:left;padding:.4rem .6rem;color:var(--text-secondary)">Nhóm/Kênh</th>
          <th style="text-align:center;padding:.4rem .6rem;color:var(--text-secondary)">Số lỗi</th>
          <th style="text-align:right;padding:.4rem .6rem;color:var(--text-secondary)">Hành động</th>
        </tr></thead><tbody>`;
    blocks.forEach(b => {
      html += `<tr style="border-bottom:1px solid color-mix(in srgb, var(--text) 6%, transparent)">
        <td style="padding:.5rem .6rem">${this._esc(b.account_name || 'Acc ' + b.account_id)}</td>
        <td style="padding:.5rem .6rem;color:var(--orange)">${this._esc(b.chat_title || 'Chat ' + b.chat_id)}</td>
        <td style="padding:.5rem .6rem;text-align:center;color:var(--red);font-weight:700">${b.fail_count}</td>
        <td style="padding:.5rem .6rem;text-align:right">
          <button onclick="App._unblockTarget(${scheduleId},${b.account_id},${b.chat_id},this)" 
                  style="background:color-mix(in srgb, var(--green) 15%, transparent);border:1px solid color-mix(in srgb, var(--green) 40%, transparent);color:var(--green);border-radius:6px;padding:3px 10px;cursor:pointer;font-size:.78rem">
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

async _setWatcherRemix(on,agentId){const cb=document.getElementById('w-ai-remix'),sel=document.getElementById('w-ai-agent');if(!cb||!sel)return;cb.checked=!!on;document.getElementById('w-ai-agent-row').style.display=on?'flex':'none';try{const res=await AIAgentsAPI.getAll();sel.innerHTML='<option value="">— Mặc định (cấu hình AI chung) —</option>'+(res.agents||[]).map(a=>`<option value="${a.id}">${esc((a.avatar_emoji||'🤖')+' '+a.name)}</option>`).join('')}catch(e){}sel.value=agentId?String(agentId):''},
async _setSchRemix(on,agentId){const cb=document.getElementById('sch-ai-remix'),sel=document.getElementById('sch-ai-agent');if(!cb||!sel)return;cb.checked=!!on;document.getElementById('sch-ai-agent-row').style.display=on?'flex':'none';try{const res=await AIAgentsAPI.getAll();sel.innerHTML='<option value="">— Mặc định (không dùng Knowledge) —</option>'+(res.agents||[]).map(a=>`<option value="${a.id}">${esc((a.avatar_emoji||'🤖')+' '+a.name)}</option>`).join('')}catch(e){}sel.value=agentId?String(agentId):''},

async openCreateModal(){this._setSchRemix(false,null);(document.getElementById('modal-title') || me_dummy).textContent = 'Tạo lịch gửi';(document.getElementById('edit-schedule-id') || document.createElement("div")).value='';(document.getElementById('sch-name') || document.createElement("div")).value='';(document.getElementById('sch-type') || document.createElement("div")).value='daily';(document.getElementById('sch-time') || document.createElement("div")).value='08:00';(document.getElementById('sch-day-of-month') || document.createElement("div")).value='1';(document.getElementById('sch-once-date') || document.createElement("div")).value='';(document.getElementById('sch-max-sends') || document.createElement("div")).value='';document.querySelectorAll('.day-btn').forEach(b=>b.classList.remove('active'));(document.getElementById('messages-list') || document.createElement("div")).innerHTML='';

this.onScheduleTypeChange();(document.getElementById('schedule-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('open');await this.loadAccountSelector();await this.loadChatList();

this._chatSel=new Map();document.querySelectorAll('#chat-list input[type="checkbox"]').forEach(c=>c.checked=false);},

async editSchedule(id){try{const s=await API.getSchedule(id);this._setSchRemix(s.ai_remix,s.ai_agent_id);(document.getElementById('modal-title') || me_dummy).textContent = 'Sửa lịch gửi';(document.getElementById('edit-schedule-id') || document.createElement("div")).value=s.id;(document.getElementById('sch-name') || document.createElement("div")).value=s.name;(document.getElementById('sch-type') || document.createElement("div")).value=s.schedule_type;(document.getElementById('sch-time') || document.createElement("div")).value=s.time_of_day;(document.getElementById('sch-day-of-month') || document.createElement("div")).value=s.day_of_month||1;(document.getElementById('sch-once-date') || document.createElement("div")).value=s.once_date||'';(document.getElementById('sch-max-sends') || document.createElement("div")).value=s.max_sends||'';

document.querySelectorAll('.day-btn').forEach(b=>b.classList.remove('active'));if(s.days_of_week)s.days_of_week.split(',').forEach(d=>{const btn=document.querySelector(`.day-btn[data-day="${d.trim()}"]`);if(btn)btn.classList.add('active')});

this.onScheduleTypeChange();(document.getElementById('messages-list') || me_dummy).innerHTML = '';(s.messages||[]).forEach(m=>this.addMessage(m.msg_type,m));

(document.getElementById('schedule-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('open');await this.loadAccountSelector();(document.getElementById('sch-account') || me_dummy).value = s.account_id;await this.loadChatList();

this._chatSel=new Map((s.targets||[]).map(t=>[String(t.chat_id),{chat_id:t.chat_id,chat_title:t.chat_title||String(t.chat_id),chat_type:t.chat_type||'group',chat_link:t.chat_link||null}]));this.renderChatList(this.chats);

}catch(e){this.toast(e.message,'error')}},

closeModal(){(document.getElementById('schedule-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('open')},

onScheduleTypeChange(){const t=document.getElementById('sch-type')?.value;(document.getElementById('weekly-days-group')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).toggle('hidden',t!=='weekly');(document.getElementById('monthly-day-group')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).toggle('hidden',t!=='monthly');(document.getElementById('once-date-group')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).toggle('hidden',t!=='once');

const lbl=document.getElementById('time-label');if(lbl){lbl.textContent=t==='hourly'?'Phút gửi (mỗi giờ)':'Giờ gửi';}},

async loadAccountSelector(){try{const d=await API.getAccounts();this.accounts=d.accounts;const sel=document.getElementById('sch-account');if(sel)sel.innerHTML=this.accounts.map(a=>`<option value="${a.id}">${esc(accDisplayName(a))} (${a.phone})</option>`).join('')}catch(e){console.error(e)}},

async loadChatList(){const sel=document.getElementById('sch-account');const accId=sel?sel.value:1;const el=document.getElementById('chat-list');if(el)el.innerHTML='<div class="loading-overlay"><span class="spinner"></span> Đang tải...</div>';

try{const d=await API.getChats(accId);this.chats=Array.isArray(d?.chats)?d.chats:(d?.command_id&&typeof API.pollCommand==='function'?((await API.pollCommand(d.command_id))?.result?.chats||[]):[]);this.renderChatList(this.chats)}catch(e){if(el)el.innerHTML=`<div style="padding:12px;color:var(--red)">Lỗi: ${e.message}</div>`}},

// Selected targets live in _chatSel so they survive search re-renders and still show
// (pinned on top) when the account's dialog list no longer contains them.
renderChatList(chats){const el=document.getElementById('chat-list');if(!el)return;const sel=this._chatSel||(this._chatSel=new Map());const q=(document.getElementById('chat-search')?.value||'').toLowerCase();const all=Array.isArray(this.chats)?this.chats:[];const known=new Set(all.map(c=>String(c.chat_id)));for(const c of all){const t=sel.get(String(c.chat_id));if(t&&!t.chat_link&&c.username)t.chat_link='@'+c.username;}const base=(Array.isArray(chats)?chats:[]).filter(c=>c.chat_type==='group'||c.chat_type==='supergroup');const missing=[...sel.values()].filter(t=>!known.has(String(t.chat_id))&&(!q||String(t.chat_title).toLowerCase().includes(q))).map(t=>({...t,_missing:true}));const list=[...missing,...base].sort((a,b)=>sel.has(String(b.chat_id))-sel.has(String(a.chat_id)));
if(!list.length){el.innerHTML='<div style="padding:12px;color:var(--text2)">Không tìm thấy nhóm/kênh</div>';return}
el.innerHTML=list.map(c=>{const icon=c.chat_type==='channel'?'📢':c.chat_type==='supergroup'?'👥':'💬';const on=sel.has(String(c.chat_id));return`<label class="chat-item"${on?' style="background:color-mix(in srgb, var(--accent) 8%, transparent)"':''}><input type="checkbox" value="${c.chat_id}" data-title="${esc(c.chat_title)}" data-type="${c.chat_type}" data-link="${esc(c.chat_link||(c.username?'@'+c.username:''))}" ${on?'checked':''} onchange="App._toggleChatSel(this)"><span class="chat-type-icon">${icon}</span><span class="chat-name">${esc(c.chat_title)}</span>${c._missing?'<span class="chat-type-badge" style="color:var(--orange)" title="Tài khoản này không còn thấy nhóm/kênh trong danh sách — có thể đã rời nhóm, gửi sẽ lỗi">⚠ không tìm thấy</span>':`<span class="chat-type-badge">${c.chat_type}</span>`}</label>`}).join('')},

_toggleChatSel(cb){const sel=this._chatSel||(this._chatSel=new Map());if(cb.checked)sel.set(String(cb.value),{chat_id:cb.value,chat_title:cb.dataset.title,chat_type:cb.dataset.type,chat_link:cb.dataset.link||null});else sel.delete(String(cb.value));},

// Join a group by link with the schedule's account, then add it as a target.
async joinGroupByLink(){const inp=document.getElementById('chat-join-link');const link=(inp?.value||'').trim();if(!link)return;const accId=parseInt(document.getElementById('sch-account')?.value||'0');if(!accId){this.toast('Chọn tài khoản trước','error');return}const btn=document.getElementById('chat-join-btn');if(btn){btn.disabled=true;btn.textContent='⏳ Đang join...'}
try{const g=await API.post('/api/chats/join-group',{account_id:accId,link});const sel=this._chatSel||(this._chatSel=new Map());sel.set(String(g.chat_id),g);if(Array.isArray(this.chats)&&!this.chats.some(c=>String(c.chat_id)===String(g.chat_id)))this.chats.unshift(g);inp.value='';this.renderChatList(this.chats);this.toast(`Đã join & thêm: ${g.chat_title}`,'success')}catch(e){this.toast(e.message,'error')}finally{if(btn){btn.disabled=false;btn.textContent='➕ Join & thêm'}}},

filterChats: debounce(function(){const q=document.getElementById('chat-search')?.value.toLowerCase();this.renderChatList(this.chats.filter(c=>c.chat_title.toLowerCase().includes(q)))}, 250),

addMessage(type,data=null){const list=document.getElementById('messages-list');const div=document.createElement('div');div.className='msg-item';div.dataset.type=type;

let inner=`<div class="msg-item-header"><span class="msg-item-type">${type.toUpperCase()}</span><button class="msg-remove" onclick="this.closest('.msg-item').remove()">✕</button></div>`;

if(type==='text')inner+=`<textarea class="form-textarea msg-content" placeholder="Nội dung (hỗ trợ HTML)">${data?.content||''}</textarea>`;

else if(['photo','video','document'].includes(type))inner+=`<div class="form-group" style="margin-bottom:8px"><input type="file" class="form-input msg-file" onchange="App.handleFileUpload(this)"><input type="hidden" class="msg-media-path" value="${data?.media_path||''}">${App.mediaPreview(data?.media_path,type)}</div><textarea class="form-textarea msg-content" placeholder="Caption" rows="2">${data?.content||''}</textarea>`;

else if(type==='poll'){const opts=data?.poll_options?JSON.parse(data.poll_options):['',''];inner+=`<div class="form-group" style="margin-bottom:8px"><input type="text" class="form-input msg-poll-question" placeholder="Câu hỏi" value="${data?.poll_question||''}"></div><div class="poll-options">${opts.map((o,i)=>`<div class="poll-option-row"><input type="text" class="form-input poll-opt" placeholder="Lựa chọn ${i+1}" value="${o}">${i>=2?'<button class="poll-option-remove" onclick="this.parentElement.remove()">✕</button>':''}</div>`).join('')}</div><button class="btn btn-ghost btn-sm" style="margin-top:6px" onclick="App.addPollOption(this)">+ Thêm</button><div style="margin-top:8px"><label style="font-size:12px;color:var(--text2);cursor:pointer"><input type="checkbox" class="msg-poll-multiple" ${data?.poll_multiple?'checked':''}> Cho phép chọn nhiều</label></div>`}

div.innerHTML=inner;list.appendChild(div)},

addPollOption(btn){const c=btn.previousElementSibling;const n=c.children.length;const r=document.createElement('div');r.className='poll-option-row';r.innerHTML=`<input type="text" class="form-input poll-opt" placeholder="Lựa chọn ${n+1}"><button class="poll-option-remove" onclick="this.parentElement.remove()">✕</button>`;c.appendChild(r)},

mediaPreview(path,type,label){if(!path)return'';const f=String(path).split(/[\\/]/).pop();const url='/api/media/'+encodeURIComponent(f);const name=String(label||f).replace(/[<>&"]/g,'');if(type==='photo')return`<a class="media-prev" href="${url}" target="_blank" title="${name}"><img src="${url}" alt="" loading="lazy" onerror="this.parentElement.classList.add('is-missing')"><span>${name}</span></a>`;if(type==='video')return`<div class="media-prev"><video src="${url}" preload="metadata" muted controls></video><span>${name}</span></div>`;return`<a class="media-prev is-file" href="${url}" target="_blank"><span>📎 ${name}</span></a>`},
async handleFileUpload(input){const file=input.files[0];if(!file)return;try{const r=await API.upload(file);input.parentElement.querySelector('.msg-media-path').value=r.path;input.parentElement.querySelectorAll('small,.media-prev').forEach(e=>e.remove());const typ=(input.closest('.msg-item')?.querySelector('.msg-item-type')?.textContent||'').toLowerCase();input.parentElement.insertAdjacentHTML('beforeend',App.mediaPreview(r.path,typ,r.original_name));this.toast('Uploaded','success')}catch(e){this.toast('Upload lỗi: '+e.message,'error')}},

async saveSchedule(){const editId=document.getElementById('edit-schedule-id')?.value;const name=(document.getElementById('sch-name')?.value || "").trim();const type=document.getElementById('sch-type')?.value;const time=document.getElementById('sch-time')?.value;const accountId=parseInt(document.getElementById('sch-account')?.value);const maxSendsVal=(document.getElementById('sch-max-sends')?.value || "").trim();const maxSends=maxSendsVal?parseInt(maxSendsVal):null;

if(!name)return this.toast('Nhập tên lịch','error');if(!time)return this.toast('Chọn giờ','error');

let days_of_week=null;if(type==='weekly'){const sel=[...document.querySelectorAll('.day-btn.active')].map(b=>b.dataset.day);if(!sel.length)return this.toast('Chọn ít nhất 1 ngày','error');days_of_week=sel.join(',')}

let day_of_month=null;if(type==='monthly')day_of_month=parseInt(document.getElementById('sch-day-of-month')?.value)||1;

let once_date=null;if(type==='once'){once_date=document.getElementById('sch-once-date')?.value;if(!once_date)return this.toast('Chọn ngày','error')}

const targets=[...(this._chatSel||new Map()).values()].map(t=>({chat_id:parseInt(t.chat_id),chat_title:t.chat_title,chat_type:t.chat_type,chat_link:t.chat_link||null}));

if(!targets.length)return this.toast('Chọn ít nhất 1 đích','error');

const messages=[];document.querySelectorAll('#messages-list .msg-item').forEach((item,i)=>{const mt=item.dataset.type;const msg={msg_order:i,msg_type:mt};

if(mt==='text'){msg.content=item.querySelector('.msg-content').value;if(!msg.content.trim())return}

else if(['photo','video','document'].includes(mt)){msg.media_path=item.querySelector('.msg-media-path').value;msg.content=item.querySelector('.msg-content').value;if(!msg.media_path)return}

else if(mt==='poll'){msg.poll_question=item.querySelector('.msg-poll-question').value;const opts=[...item.querySelectorAll('.poll-opt')].map(i=>i.value).filter(Boolean);if(opts.length<2||!msg.poll_question)return;msg.poll_options=JSON.stringify(opts);msg.poll_multiple=item.querySelector('.msg-poll-multiple')?.checked||false}

messages.push(msg)});

if(!messages.length)return this.toast('Thêm ít nhất 1 tin nhắn','error');

const payload={account_id:accountId,name,schedule_type:type,time_of_day:time,days_of_week,day_of_month,once_date,max_sends:maxSends,is_active:true,ai_remix:!!document.getElementById('sch-ai-remix')?.checked,ai_agent_id:parseInt(document.getElementById('sch-ai-agent')?.value)||null,messages,targets};

const btn=document.getElementById('btn-save-schedule');if(btn){btn.disabled=true;btn.textContent='Đang lưu...';}

try{if(editId)await API.updateSchedule(editId,payload);else await API.createSchedule(payload);this.toast(editId?'Đã cập nhật':'Đã tạo mới','success');this.closeModal();this.loadSchedules()}catch(e){this.toast(e.message,'error')}

btn.disabled=false;btn.textContent='Lưu lịch'},

async loadLogs(){const status=document.getElementById('log-filter-status')?.value;try{const d=await API.getLogs({limit:this.logLimit,offset:this.logOffset,...(status?{status}:{})});const tbody=document.getElementById('logs-body');

if(!d.logs.length){tbody.innerHTML='<tr><td colspan="6" style="text-align:center;color:var(--text2);padding:24px">Chưa có log</td></tr>';return}

tbody.innerHTML=d.logs.map(l=>`<tr><td style="font-size:12px">${formatDate(l.sent_at)}</td><td>${l.schedule_id}</td><td style="color:var(--accent-text);font-weight:600">${esc(l.account_name||('Acc '+l.account_id))}</td><td>${esc(l.chat_title||String(l.chat_id))}</td><td><span class="badge ${l.status==='success'?'badge-green':'badge-red'}">${l.status}</span></td><td style="font-size:12px;color:var(--text2);max-width:200px;overflow:hidden;text-overflow:ellipsis">${esc(l.error_message||'—')}</td></tr>`).join('');

const pagEl=document.getElementById('logs-pagination');const pages=Math.ceil(d.total/this.logLimit);const cur=Math.floor(this.logOffset/this.logLimit);

if(pages>1){let h='';if(cur>0)h+=`<button class="btn btn-ghost btn-sm" onclick="App.logPage(${cur-1})">← Trước</button>`;h+=`<span style="color:var(--text2);font-size:12px">Trang ${cur+1}/${pages}</span>`;if(cur<pages-1)h+=`<button class="btn btn-ghost btn-sm" onclick="App.logPage(${cur+1})">Sau →</button>`;pagEl.innerHTML=h}else pagEl.innerHTML=''}catch(e){this.toast('Lỗi: '+e.message,'error')}},

logPage(p){this.logOffset=p*this.logLimit;this.loadLogs()},



// ── Watcher ──────────────────────────────────────────────────────

_watcherKeywords:[],_watcherExcludes:[],_watcherChats:[],_watcherSelectedGroups:new Set(),_watcherAccountOrder:[],_watcherObserverIds:[],_watcherType:'keyword',_wlOffset:0,_wlLimit:40,

setWatcherType(t,noReload){this._watcherType=t;
  document.getElementById('w-type-keyword')?.classList.toggle('active',t==='keyword');
  document.getElementById('w-type-join')?.classList.toggle('active',t==='join');
  document.getElementById('w-keywords-block')?.classList.toggle('hidden',t==='join');
  document.getElementById('w-join-delay-block')?.classList.toggle('hidden',t!=='join');
  // Observer picker only matters for join watchers.
  document.getElementById('w-observer-block')?.classList.toggle('hidden',t!=='join');
  const sl=document.getElementById('w-sender-label');
  if(sl)sl.textContent = t==='join'
    ? 'Nick gửi DM (không cần ở trong nhóm)'
    : 'Tài khoản gửi DM (chọn nhiều – ưu tiên theo thứ tự, fallback nếu lỗi)';
  // Keep the sender/observer lists in sync with the current checkbox state.
  const senders=[...document.querySelectorAll('#w-accounts-list input[type=checkbox]:checked')].map(cbx=>parseInt(cbx.value));
  const observers=[...document.querySelectorAll('#w-observers-list input[type=checkbox]:checked')].map(cbx=>parseInt(cbx.value));
  this._renderWatcherAccounts(senders);
  this._renderWatcherObservers(observers);
  this._watcherObserverIds=observers;
  if(!noReload)this._loadWatcherChatList([...(this._watcherSelectedGroups||[])])},

toggleWatcherObserver(id,cbx){
  const cur=new Set([...document.querySelectorAll('#w-observers-list input[type=checkbox]:checked')].map(c=>parseInt(c.value)));
  if(cbx.checked)cur.add(id);else cur.delete(id);
  this._watcherObserverIds=[...cur];
  this._loadWatcherChatList([...(this._watcherSelectedGroups||[])])},

_renderWatcherObservers(selectedIds=null){
  const el=document.getElementById('w-observers-list');
  if(!el)return;
  const sel=selectedIds||this._watcherObserverIds||[];
  el.innerHTML=this.accounts.map(a=>{
    const chk=sel.includes(a.id)?'checked':'';
    return `<div style="display:flex;align-items:center;gap:6px;background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:6px 10px;font-size:13px">
      <input type="checkbox" value="${a.id}" ${chk} style="accent-color:var(--accent)" onchange="App.toggleWatcherObserver(${a.id}, this)">
      <span title="${esc(accDisplayName(a))}">${esc(shortName(accDisplayName(a)))}</span>
      <small style="color:var(--text2);margin-left:4px">${a.phone}</small>
    </div>`;
  }).join('')},

async loadWatchers(){try{

  const[ws,stats,errs]=await Promise.all([API.getWatchers(),API.getWatcherStats(),API.get('/api/watchers/error-summary').catch(()=>({rules:{}}))]);
  this._wErrors=errs.rules||{};this._watchersList=ws;

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

    const isJoin=w.watch_type==='join';
    const er=this._wErrors[w.id]||{};
    const meta=[isJoin?'Join DM':'Keyword',`${w.group_ids.length} nhóm`,w.dm_once?'DM 1 lần':`Cooldown ${w.cooldown_hours}h`].join(' · ');
    const kws=isJoin?[`Delay ${w.join_dm_delay_min??3}–${w.join_dm_delay_max??15}p`]:w.keywords;
    const kwHtml=kws.slice(0,3).map(k=>`<span class="wt-chip">${esc(k)}</span>`).join('')+(kws.length>3?`<span class="wt-chip wt-more" title="${esc(kws.slice(3).join(', '))}">+${kws.length-3}</span>`:'');
    const accs=(w.sender_account_ids||[]).map(id=>accMap[id]?accDisplayName(accMap[id]):'?');
    const ava=n=>`<span class="wt-ava" title="${esc(n)}">${esc((n.trim()[0]||'?').toUpperCase())}</span>`;
    const accHtml=accs.length?`<div class="wt-accs" title="${esc(accs.join('\n'))}"><div class="wt-stack">${accs.slice(0,3).map(ava).join('')}${accs.length>3?`<span class="wt-ava wt-more">+${accs.length-3}</span>`:''}</div><span class="wt-acc-name">${esc(accs[0])}${accs.length>1?` <em>+${accs.length-1}</em>`:''}</span></div>`:'<span class="wt-muted">—</span>';
    const paused=er.paused_reason?`<span class="wt-paused" title="${esc(er.paused_reason)}" onclick="App.showWatcherErrors(${w.id})">Tự dừng do lỗi</span>`:'';
    const errCell=er.failed?`<button class="wt-err" onclick="App.showWatcherErrors(${w.id})" title="Xem lỗi chi tiết"><i></i>${er.failed.toLocaleString('vi-VN')}${er.failed_24h?`<small>${er.failed_24h}/24h</small>`:''}</button>`:'<span class="wt-muted">—</span>';
    return`<tr class="${w.is_active?'':'wt-off'}"><td><label class="toggle"><input type="checkbox" ${w.is_active?'checked':''} onchange="App.toggleWatcher(${w.id})"><span class="toggle-slider"></span></label></td><td><div class="wt-name">${esc(w.name)}${paused}</div><div class="wt-meta">${meta}</div></td><td><div class="wt-chips">${kwHtml}</div></td><td>${accHtml}</td><td>${errCell}</td><td><div class="wt-actions"><button class="acc-ib" onclick="App.openTestDM(${w.id})" title="Test DM">🧪</button><button class="acc-ib" onclick="App.editWatcher(${w.id})" title="Sửa">✏️</button><button class="acc-ib is-danger" onclick="App.deleteWatcher(${w.id})" title="Xóa">🗑</button></div></td></tr>`}).join('')

}catch(e){this.toast('Lỗi: '+e.message,'error')}},

async openWatcherModal(){

  (document.getElementById('watcher-modal-title') || me_dummy).textContent = 'Tạo Keyword DM Rule';

  (document.getElementById('edit-watcher-id') || me_dummy).value = '';

  (document.getElementById('w-name') || me_dummy).value = '';

  (document.getElementById('w-cooldown') || me_dummy).value = '24';

  (document.getElementById('w-dm-once') || me_dummy).checked = false;
  this._setWatcherRemix(true,null);
  (document.getElementById('w-reply-in-group') || me_dummy).checked = false;
  (document.getElementById('w-group-reply-text') || me_dummy).value = 'Check my DM 😊';
  ((document.getElementById('w-group-reply-section') && document.getElementById('w-group-reply-section')?.style) || me_dummy_style).display = 'none';

  (document.getElementById('w-messages-list') || me_dummy).innerHTML = '';

  this._watcherKeywords=[];this._watcherExcludes=[];this._watcherSelectedGroups=new Set();

  (document.getElementById('w-join-delay-min') || me_dummy).value = '3';
  (document.getElementById('w-join-delay-max') || me_dummy).value = '15';
  this.setWatcherType('keyword',true);

  this._watcherActiveAccountId=null;

  this._watcherObserverIds=[];

  const _gl=document.getElementById('w-group-link'); if(_gl) _gl.value='';
  const _gs=document.getElementById('w-link-status'); if(_gs) _gs.innerHTML='';

  this._renderWatcherKeywords();this._renderWatcherExcludes();

  (document.getElementById('watcher-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('open');

  await this._loadWatcherAccounts([]);await this._loadWatcherChatList([]);

  this._renderWatcherObservers([]);},

async editWatcher(id){try{

  const w=await API.getWatcher(id);

  (document.getElementById('watcher-modal-title') || me_dummy).textContent = 'Sửa Keyword DM Rule';

  (document.getElementById('edit-watcher-id') || me_dummy).value = w.id;

  (document.getElementById('w-name') || me_dummy).value = w.name;

  (document.getElementById('w-cooldown') || me_dummy).value = w.cooldown_hours||24;

  (document.getElementById('w-dm-once') || me_dummy).checked = !!w.dm_once;
  this._setWatcherRemix(w.ai_remix!==0,w.ai_agent_id);
  (document.getElementById('w-reply-in-group') || me_dummy).checked = !!w.reply_in_group;
  (document.getElementById('w-group-reply-text') || me_dummy).value = w.group_reply_text||'Check my DM 😊';
  ((document.getElementById('w-group-reply-section') && document.getElementById('w-group-reply-section')?.style) || me_dummy_style).display = w.reply_in_group?'block':'none';

  this._watcherKeywords=[...w.keywords];this._renderWatcherKeywords();

  (document.getElementById('w-join-delay-min') || me_dummy).value = w.join_dm_delay_min ?? 3;
  (document.getElementById('w-join-delay-max') || me_dummy).value = w.join_dm_delay_max ?? 15;
  this.setWatcherType(w.watch_type || 'keyword',true);

  this._watcherExcludes=[...(w.excluded_usernames||[])];this._renderWatcherExcludes();

  this._watcherSelectedGroups=new Set(w.group_ids.map(Number));

  (document.getElementById('w-messages-list') || me_dummy).innerHTML = '';

  (w.messages||[]).forEach(m=>this.addWatcherMessage(m.msg_type,m));

  this._watcherActiveAccountId = w.sender_account_ids && w.sender_account_ids.length ? w.sender_account_ids[0] : null;

  this._watcherObserverIds = [...(w.observer_account_ids||[])];

  (document.getElementById('watcher-modal')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('open');

  await this._loadWatcherAccounts(w.sender_account_ids||[]);await this._loadWatcherChatList(w.group_ids||[]);

  this._renderWatcherObservers(this._watcherObserverIds);

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

    const bgStyle = isActive ? 'color-mix(in srgb, var(--purple) 12%, transparent)' : 'var(--bg)';

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

    this._watcherChats=Array.isArray(d?.chats)?d.chats:(d?.command_id&&typeof API.pollCommand==='function'?((await API.pollCommand(d.command_id))?.result?.chats||[]):[]);

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

async _loadWatcherChatList(selectedIds=[]){const el=document.getElementById('w-chat-list');if(el)el.innerHTML='<div class="loading-overlay"><span class="spinner"></span> Đang tải nhóm từ nick '+((this._watcherType==='join'&&(this._watcherObserverIds||[]).length)?'theo dõi':'gửi')+'...</div>';const _seq=this._wChatSeq=(this._wChatSeq||0)+1;

  const selSet=new Set(selectedIds.map(Number));this._watcherSelectedGroups=new Set(selSet);

  try{

    // Join rules watch groups through the observer nick(s), so list THEIR groups;
    // keyword rules listen with the sender nick.
    const obs=(this._watcherType==='join')?(this._watcherObserverIds||[]):[];
    const accIds=obs.length?obs:[this._watcherActiveAccountId||(this.accounts[0]?.id||1)];
    const fetchChats=async id=>{const d=await API.getChats(id);return Array.isArray(d?.chats)?d.chats:(d?.command_id&&typeof API.pollCommand==='function'?((await API.pollCommand(d.command_id))?.result?.chats||[]):[])};
    const lists=await Promise.all(accIds.map(id=>fetchChats(id).catch(()=>[])));
    if(_seq!==this._wChatSeq)return; // a newer load (observer toggled again) supersedes this one
    const seen=new Set();this._watcherChats=lists.flat().filter(c=>{const k=Number(c.chat_id);if(seen.has(k))return false;seen.add(k);return true});

    // Re-apply whatever is typed in the search box — the user may have searched while this was loading.
    const q=(document.getElementById('w-chat-search')?.value||'').toLowerCase();
    this._renderWatcherChatList(q?this._watcherChats.filter(c=>String(c.chat_title||'').toLowerCase().includes(q)):this._watcherChats,selSet)

  }catch(e){if(el)el.innerHTML=`<div style="padding:12px;color:var(--red)">Lỗi: ${e.message}</div>`}},

// Only real groups can ever produce a member-join / keyword message we can act on.
// Channels (broadcast) have subscribers, not joinable members; bots have neither —
// so they must never appear in the "Nhóm cần theo dõi" picker.
_watcherGroupOnly(chats){return (chats||[]).filter(c=>{const t=(c.chat_type||'').toLowerCase();return t==='group'||t==='supergroup'})},

_renderWatcherChatList(chats,preSelected=null){

  const sel=preSelected||this._watcherSelectedGroups;

  const el=document.getElementById('w-chat-list');

  const groups=this._watcherGroupOnly(chats);

  if(!groups.length){el.innerHTML='<div style="padding:12px;color:var(--text2)">Không tìm thấy nhóm</div>';return}

  el.innerHTML=groups.map(c=>{const icon=c.chat_type==='supergroup'?'👥':'💬';const chk=sel.has(Number(c.chat_id))?'checked':'';return`<label class="chat-item"><input type="checkbox" value="${c.chat_id}" data-title="${esc(c.chat_title)}" ${chk} onchange="App._onWatcherGroupToggle(this)"><span class="chat-type-icon">${icon}</span><span class="chat-name">${esc(c.chat_title)}</span><span class="chat-type-badge">${c.chat_type}</span></label>`}).join('')},

_onWatcherGroupToggle(cb){const id=parseInt(cb.value);if(cb.checked)this._watcherSelectedGroups.add(id);else this._watcherSelectedGroups.delete(id)},

filterWatcherChats: debounce(function(){const q=(document.getElementById('w-chat-search')?.value||'').toLowerCase();this._renderWatcherChatList(this._watcherChats.filter(c=>String(c.chat_title||'').toLowerCase().includes(q)))}, 250),

async resolveWatcherGroupLink(){
  const inp=document.getElementById('w-group-link');
  const st=document.getElementById('w-link-status');
  const btn=document.getElementById('w-link-btn');
  const link=(inp?.value||'').trim();
  if(!link){st.innerHTML='<span style="color:var(--orange)">Dán link nhóm trước đã.</span>';return}
  const accId=this._watcherActiveAccountId||(this.accounts[0]?.id);
  if(!accId){st.innerHTML='<span style="color:var(--red)">Chọn nick gửi/theo dõi trước.</span>';return}
  btn.disabled=true;st.innerHTML='<span class="spinner"></span> Đang join &amp; đọc thông tin nhóm (nick #'+accId+')...';
  try{
    const r=await fetch('/api/watchers/resolve-link',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({account_id:accId,link})});
    const d=await r.json();
    if(!r.ok||!d.ok){throw new Error(d.error||d.detail||'Không resolve được nhóm')}
    const t=(d.chat_type||'').toLowerCase();
    if(t!=='group'&&t!=='supergroup'){throw new Error('Đây là '+t+' — chỉ nhận group/supergroup.')}
    // thêm vào danh sách (nếu chưa có) và tự tick chọn
    const cid=Number(d.chat_id);
    if(!this._watcherChats.some(c=>Number(c.chat_id)===cid)){
      this._watcherChats.unshift({chat_id:cid,chat_title:d.chat_title,chat_type:d.chat_type,username:d.username||''});
    }
    this._watcherSelectedGroups.add(cid);
    this._renderWatcherChatList(this._watcherChats);
    const forum=d.is_forum?' (nhóm dạng Topic)':'';
    st.innerHTML='<span style="color:var(--green)">✅ Đã thêm &amp; chọn: '+esc(d.chat_title)+forum+' — nick #'+accId+' đã join.</span>';
    inp.value='';
  }catch(e){
    st.innerHTML='<span style="color:var(--red)">❌ '+esc(e.message||String(e))+'</span>';
  }finally{btn.disabled=false}
},

addWatcherKeyword(){const inp=document.getElementById('w-keyword-input');const v=inp.value.trim();if(!v)return;if(!this._watcherKeywords.includes(v))this._watcherKeywords.push(v);inp.value='';this._renderWatcherKeywords()},

_renderWatcherKeywords(){const c=document.getElementById('w-keywords-tags');if(c)c.innerHTML=this._watcherKeywords.map((k,i)=>`<span style="display:inline-flex;align-items:center;gap:4px;background:var(--accent);color:#fff;border-radius:20px;padding:3px 10px;font-size:12px">${esc(k)}<button onclick="App._removeWatcherKeyword(${i})" style="background:none;border:none;color:#fff;cursor:pointer;font-size:14px;line-height:1">×</button></span>`).join('')},

_removeWatcherKeyword(i){this._watcherKeywords.splice(i,1);this._renderWatcherKeywords()},

addWatcherExclude(){const inp=document.getElementById('w-exclude-input');const v=inp.value.replace('@','').trim().toLowerCase();if(!v)return;if(!this._watcherExcludes.includes(v))this._watcherExcludes.push(v);inp.value='';this._renderWatcherExcludes()},

_renderWatcherExcludes(){const c=document.getElementById('w-exclude-tags');if(!c)return;c.innerHTML=this._watcherExcludes.map((u,i)=>`<span style="display:inline-flex;align-items:center;gap:4px;background:#e53e3e;color:#fff;border-radius:20px;padding:3px 10px;font-size:12px">🚫 @${esc(u)}<button onclick="App._removeWatcherExclude(${i})" style="background:none;border:none;color:#fff;cursor:pointer;font-size:14px;line-height:1">×</button></span>`).join('')},

_removeWatcherExclude(i){this._watcherExcludes.splice(i,1);this._renderWatcherExcludes()},

addWatcherMessage(type,data=null){

  const list=document.getElementById('w-messages-list');

  const div=document.createElement('div');div.className='msg-item';div.dataset.type=type;

  // Optional variant index: messages sharing a variant number form one template
  // variant that Smart Template Rotation will A/B test. Blank = 0 (single variant).
  const _variant=data?.variant_index||0;

  const _esc=s=>String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');

  let inner=`<div class="msg-item-header"><span class="msg-item-type">${type.toUpperCase()}</span><label class="msg-variant-label">Phiên bản <span class="info-i" tabindex="0" data-tip="Dùng để A/B test nhiều kịch bản. Bấm + Text để thêm tin thứ 2 và đặt số khác (vd tin 1 = 0, tin 2 = 1). Mỗi người nhận chỉ nhận 1 phiên bản; hệ thống xoay vòng và ưu tiên bản có nhiều người trả lời hơn. Đổi số trên 1 tin duy nhất sẽ không có tác dụng gì.">i</span> <input type="number" min="0" step="1" class="msg-variant" value="${_variant}" style="width:52px" oninput="App._updateVariantHint()"></label><button class="msg-remove" onclick="this.closest('.msg-item').remove()">✕</button></div>`;

  if(type==='text')inner+=`<textarea class="form-textarea msg-content" placeholder="Nội dung DM, vd: {Hey|Hi} {name}, saw you in {group}...">${_esc(data?.content)}</textarea>${App._msgTagBar()}`;

  else if(['photo','video','document'].includes(type))inner+=`<div class="form-group" style="margin-bottom:8px"><input type="file" class="form-input msg-file" onchange="App.handleFileUpload(this)"><input type="hidden" class="msg-media-path" value="${_esc(data?.media_path)}">${App.mediaPreview(data?.media_path,type)}</div><textarea class="form-textarea msg-content" placeholder="Caption" rows="2">${_esc(data?.content)}</textarea>${App._msgTagBar()}`;

  div.innerHTML=inner;list.appendChild(div);this._updateVariantHint()},

// Personalization tags (replaced per recipient by personalization.py) + spintax.
_msgTagBar(){
  const tags=[['{name}','Tên (first name, không có thì username)'],['{full_name}','Họ và tên'],['{username}','Username không có @'],['{group}','Tên nhóm nơi gặp người này'],['{Hey|Hi|Hello}','Spintax: chọn ngẫu nhiên 1 lựa chọn cho mỗi người, chống trùng lặp/spam']];
  return '<div class="msg-tags"><span class="info-i" tabindex="0" data-tip="Bấm để chèn vào vị trí con trỏ. {name} hoặc [Name] đều được thay bằng tên người nhận. Dạng {a|b|c} chọn ngẫu nhiên 1 câu cho mỗi người nên mỗi DM khác nhau, giảm bị đánh dấu spam.">i</span>'+tags.map(([t,d])=>`<button type="button" class="msg-tag" title="${d}" onmousedown="event.preventDefault()" onclick="App._insertMsgTag(this,'${t}')">${t}</button>`).join('')+'</div>'},

_insertMsgTag(btn,tag){
  const ta=btn.closest('.msg-item')?.querySelector('textarea.msg-content');if(!ta)return;
  const a=ta.selectionStart??ta.value.length,b=ta.selectionEnd??a;
  ta.value=ta.value.slice(0,a)+tag+ta.value.slice(b);ta.focus();
  // Select the spintax options so they can be overwritten straight away.
  if(tag.includes('|'))ta.setSelectionRange(a+1,a+tag.length-1);else ta.setSelectionRange(a+tag.length,a+tag.length)},

_updateVariantHint(){
  const list=document.getElementById('w-messages-list');if(!list)return;
  let hint=document.getElementById('w-variant-hint');
  if(!hint){hint=document.createElement('div');hint.id='w-variant-hint';hint.className='msg-variant-hint';list.after(hint)}
  const vs=new Set([...list.querySelectorAll('.msg-variant')].map(i=>parseInt(i.value)||0));
  hint.innerHTML=vs.size>1?`🔀 Đang A/B test <b>${vs.size} phiên bản</b> (${[...vs].sort((x,y)=>x-y).join(', ')}). Mỗi người nhận 1 phiên bản, ưu tiên bản có tỉ lệ trả lời cao hơn.`:(list.children.length?'Chỉ có 1 phiên bản. Muốn A/B test thì thêm tin và đặt số Phiên bản khác.':'');
},

async saveWatcher(){

  const editId=document.getElementById('edit-watcher-id')?.value;

  const name=(document.getElementById('w-name')?.value || "").trim();

  const cooldown=parseInt(document.getElementById('w-cooldown')?.value)||24;

  if(!name)return this.toast('Nhập tên rule','error');

  const watchType=this._watcherType||'keyword';

  const joinDelayMin=parseInt(document.getElementById('w-join-delay-min')?.value)||3;

  const joinDelayMax=parseInt(document.getElementById('w-join-delay-max')?.value)||15;

  if(watchType==='join'){
    if(joinDelayMin<1)return this.toast('Delay tối thiểu phải ≥ 1 phút','error');
    if(joinDelayMax<joinDelayMin)return this.toast('Delay tối đa phải ≥ delay tối thiểu','error');
  }else{
    if(!this._watcherKeywords.length)return this.toast('Thêm ít nhất 1 từ khóa','error');
  }

  if(!this._watcherSelectedGroups.size)return this.toast('Chọn ít nhất 1 nhóm','error');

  const accIds=[...document.querySelectorAll('#w-accounts-list input[type="checkbox"]:checked')].map(cbx=>parseInt(cbx.value));
  const observerIds=[...document.querySelectorAll('#w-observers-list input[type="checkbox"]:checked')].map(cbx=>parseInt(cbx.value));

  if(!accIds.length)return this.toast('Chọn ít nhất 1 tài khoản','error');

  const messages=[];document.querySelectorAll('#w-messages-list .msg-item').forEach((item,i)=>{const mt=item.dataset.type;const msg={msg_order:i,msg_type:mt};

    const _v=parseInt(item.querySelector('.msg-variant')?.value);msg.variant_index=Number.isFinite(_v)&&_v>0?_v:0;

    if(mt==='text'){msg.content=item.querySelector('.msg-content').value;if(!msg.content.trim())return}

    else if(['photo','video','document'].includes(mt)){msg.media_path=item.querySelector('.msg-media-path').value;msg.content=item.querySelector('.msg-content').value;if(!msg.media_path)return}

    messages.push(msg)});

  if(!messages.length)return this.toast('Thêm ít nhất 1 tin nhắn DM','error');

  const dmOnce=document.getElementById('w-dm-once')?.checked;
  const replyInGroup=document.getElementById('w-reply-in-group')?.checked;
  const groupReplyText=(document.getElementById('w-group-reply-text')?.value || "").trim()||'Check my DM 😊';

  const payload={name,sender_account_ids:accIds,observer_account_ids:watchType==='join'?observerIds:[],keywords:watchType==='join'?[]:this._watcherKeywords,excluded_usernames:this._watcherExcludes,group_ids:[...this._watcherSelectedGroups],cooldown_hours:cooldown,dm_once:dmOnce,reply_in_group:replyInGroup,group_reply_text:groupReplyText,is_active:1,messages,watch_type:watchType,join_dm_delay_min:joinDelayMin,join_dm_delay_max:joinDelayMax,ai_remix:!!document.getElementById('w-ai-remix')?.checked,ai_agent_id:parseInt(document.getElementById('w-ai-agent')?.value)||null};

  try{
    const savedId = editId ? parseInt(editId) : null;
    if(editId) await API.updateWatcher(editId,payload); else { const r = await API.createWatcher(payload); }
    this.toast(editId?'Đã cập nhật rule':'Đã tạo rule mới','success');
    this.closeWatcherModal();
    this.loadWatchers();
    // ── Membership check: warn if any account not in the group ──────────────
    try {
      // Join rules only need the OBSERVER nick(s) inside the group — the sender DMs
      // from outside. Keyword rules listen with the sender nick, so check those.
      const chkAccs = (watchType==='join' && observerIds.length) ? observerIds : accIds;
      const chkRes = await API.checkMembership(chkAccs, [...this._watcherSelectedGroups]);
      if(chkRes && chkRes.warnings && chkRes.warnings.length > 0) {
        this._showMembershipWarning(chkRes.warnings, payload.name);
      }
    } catch(_) {}
  }catch(e){this.toast(e.message,'error')}},

_showMembershipWarning(warnings, ruleName) {
  // Build the warning modal content
  let html = `<div style="background:color-mix(in srgb, var(--orange) 8%, transparent);border:1px solid color-mix(in srgb, var(--orange) 40%, transparent);border-radius:12px;padding:1.2rem;margin-bottom:1rem;">
    <div style="display:flex;align-items:center;gap:.6rem;margin-bottom:.8rem;">
      <span style="font-size:1.4rem;">⚠️</span>
      <strong style="color:var(--orange);font-size:1rem;">Cảnh báo: Tài khoản chưa join nhóm</strong>
    </div>
    <p style="color:var(--text-secondary);font-size:.88rem;margin-bottom:.8rem;">
      Rule <strong style="color:var(--text)">"${this._esc(ruleName)}"</strong> có tài khoản chưa tham gia nhóm đang theo dõi.
      Các tài khoản này sẽ không thể DM user từ nhóm đó.
    </p>`;
  warnings.forEach(w => {
    html += `<div style="background:rgba(0,0,0,.2);border-radius:8px;padding:.7rem .9rem;margin-bottom:.5rem;">
      <div style="font-weight:600;color:var(--orange);margin-bottom:.3rem;">📱 ${this._esc(w.account_name)} (ID: ${w.account_id})</div>`;
    w.missing_groups.forEach(g => {
      const btnId = `join-btn-${w.account_id}-${g.group_id}`;
      html += `<div style="display:flex;align-items:center;gap:.5rem;padding-left:.5rem;margin-bottom:2px;">
        <span style="color:var(--text-secondary);font-size:.83rem;flex:1;">• Chưa join: <span style="color:var(--red)">${this._esc(g.group_title)}</span></span>
        <button id="${btnId}" onclick="App._joinFromWarning(this,${w.account_id},'${g.group_id}')"
          style="background:var(--violet);color:#fff;border:none;border-radius:6px;padding:2px 10px;font-size:.75rem;cursor:pointer;white-space:nowrap;font-weight:600;">
          Join
        </button>
      </div>`;
    });
    html += `</div>`;
  });
  // "Join tất cả" bulk button
  html += `<div style="margin-top:.8rem;display:flex;align-items:center;gap:.6rem;">
    <button id="join-all-missing-btn" onclick="App._joinAllMissing()" 
      style="background:var(--violet);color:#fff;border:none;border-radius:8px;padding:6px 16px;font-size:.85rem;cursor:pointer;font-weight:600;">
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
    btn.style.background = 'var(--green)';
  } catch(e) {
    btn.textContent = '❌ Lỗi';
    btn.style.background = 'var(--red)';
    btn.title = e.message || 'Join failed';
    setTimeout(() => { btn.disabled = false; btn.textContent = 'Join'; btn.style.background = 'var(--violet)'; }, 3000);
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
        if(itemBtn) { itemBtn.textContent = '✅ Joined'; itemBtn.style.background = 'var(--green)'; }
      } catch(e) {
        fail++;
        if(itemBtn) { itemBtn.textContent = '❌ Lỗi'; itemBtn.style.background = 'var(--red)'; itemBtn.title = e.message || ''; }
      }
      await new Promise(r => setTimeout(r, 2000)); // Delay between joins
    }
  }
  btn.textContent = `✅ Xong (${ok} joined, ${fail} lỗi)`;
  btn.style.background = ok > 0 ? 'var(--green)' : 'var(--red)';
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

    const body = {provider, keys, text: sampleText};

    // Include base_url and model for providers that need them
    if (provider === 'openai_compatible') {
      body.base_url = (document.getElementById('oai-compat-base-url') || {}).value || '';
      body.model = (document.getElementById('oai-compat-model') || {}).value || '';
    } else if (provider === 'chatgpt_oauth') {
      body.base_url = (document.getElementById('chatgpt-oauth-base-url') || {}).value || 'https://api.openai.com/v1';
      body.model = (document.getElementById('chatgpt-oauth-model') || {}).value || 'gpt-4o';
    }

    const resp = await fetch('/api/settings/test-remix', {

      method:'POST',

      headers:{'Content-Type':'application/json'},

      body: JSON.stringify(body)

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

showWatcherErrors(id){
  const w=(this._watchersList||[]).find(x=>x.id===id)||{name:'#'+id};
  const er=(this._wErrors||{})[id]||{top_errors:[]};
  document.getElementById('we-title').textContent='Lỗi của rule: '+w.name;
  const rate=(er.failed+er.success)?Math.round(er.failed*100/(er.failed+er.success)):0;
  let h='';
  if(er.paused_reason)h+=`<div style="padding:10px 12px;border:1px solid var(--red);border-radius:var(--radius-sm);background:var(--red-dim);margin-bottom:12px;font-size:13px">${esc(er.paused_reason)}<div style="color:var(--text2);margin-top:4px">Sửa nguyên nhân rồi bật lại rule để chạy tiếp.</div></div>`;
  h+=`<div style="display:flex;gap:16px;font-size:13px;color:var(--text2);margin-bottom:12px"><span><b style="color:var(--red)">${er.failed||0}</b> lỗi</span><span><b>${er.failed_24h||0}</b> trong 24h</span><span><b style="color:var(--green)">${er.success||0}</b> thành công</span><span>Tỉ lệ lỗi <b>${rate}%</b></span></div>`;
  h+='<div style="font-size:12px;color:var(--text3);margin-bottom:6px">Nguyên nhân phổ biến</div>';
  h+=(er.top_errors||[]).map(e=>`<div style="display:flex;gap:10px;align-items:flex-start;padding:8px 0;border-bottom:1px solid var(--border)"><span class="badge badge-red" style="flex:none">${e.count}×</span><div style="font-size:13px;flex:1">${esc(e.error)}<div style="font-size:11px;color:var(--text3)">Gần nhất: ${formatDate(e.last_at)}</div></div></div>`).join('')||'<div style="color:var(--text3);font-size:13px">Không có lỗi.</div>';
  h+=`<div style="display:flex;justify-content:flex-end;gap:8px;margin-top:14px"><button class="btn btn-ghost btn-sm" onclick="App.editWatcher(${id});document.getElementById('watcher-errors-modal').classList.remove('open')">✏️ Sửa rule</button><button class="btn btn-primary btn-sm" onclick="App.openWatcherLogsFor(${id})">Xem log chi tiết →</button></div>`;
  document.getElementById('we-body').innerHTML=h;
  document.getElementById('watcher-errors-modal').classList.add('open');
},
openWatcherLogsFor(id){
  document.getElementById('watcher-errors-modal').classList.remove('open');
  this._wlRule=String(id);this._wlOffset=0;
  const st=document.getElementById('wl-filter-status');if(st)st.value='failed';
  this.navigate('watcher-logs');
},
async loadWatcherLogs(){const status=document.getElementById('wl-filter-status')?.value||'';
  const ruleSel=document.getElementById('wl-filter-rule');
  if(!this._watchersList)this._watchersList=await API.getWatchers().catch(()=>[]);
  const ruleNames=Object.fromEntries((this._watchersList||[]).map(w=>[w.id,w.name]));
  if(ruleSel){const want=this._wlRule!=null?this._wlRule:ruleSel.value;this._wlRule=null;
    ruleSel.innerHTML='<option value="">Tất cả rule</option>'+(this._watchersList||[]).map(w=>`<option value="${w.id}">${esc(w.name)}</option>`).join('');ruleSel.value=want}
  const rule=ruleSel?.value||'';

  try{const d=await API.getWatcherLogs({limit:this._wlLimit,offset:this._wlOffset,...(status?{status}:{}),...(rule?{watcher_id:rule}:{})});

    const tbody=document.getElementById('watcher-logs-body');

    if(!d.logs.length){tbody.innerHTML='<tr><td colspan="8" style="text-align:center;color:var(--text2);padding:24px">Chưa có log DM</td></tr>';return}

    const accs=this.accounts.length?this.accounts:(await API.getAccounts()).accounts;

    const accMap=Object.fromEntries(accs.map(a=>[a.id,a]));

    tbody.innerHTML=d.logs.map(l=>{

      const statusCls=l.status==='success'?'badge-green':l.status==='skipped'?'badge-blue':'badge-red';

      const accName=l.account_id&&accMap[l.account_id]?esc(accMap[l.account_id].name):(l.account_id||'—');

      return`<tr><td style="font-size:12px">${formatDate(l.sent_at)}</td><td style="font-size:12px">${ruleNames[l.watcher_id]?esc(ruleNames[l.watcher_id]):'#'+l.watcher_id}</td><td>@${esc(l.target_username||String(l.target_user_id))}</td><td style="font-size:12px">${esc(l.group_title||String(l.group_id||''))}</td><td><span class="badge ${l.matched_keyword==='[JOIN]'?'badge-purple':'badge-blue'}" style="font-size:11px">${l.matched_keyword==='[JOIN]'?'👋 Join':esc(l.matched_keyword||'')}</span></td><td style="font-size:12px">${accName}</td><td><span class="badge ${statusCls}">${l.status}</span></td><td style="font-size:12px;color:var(--text2)">${esc(l.error_message||'—')}</td></tr>`}).join('');

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
    // For select dropdown: ensure saved model exists as option before setting value
    var chatgptModelSelect = document.getElementById('chatgpt-oauth-model');
    if (chatgptModelSelect && chatgptOauthModel) {
      var hasOption = Array.from(chatgptModelSelect.options).some(function(o) { return o.value === chatgptOauthModel; });
      if (!hasOption) {
        var opt = document.createElement('option');
        opt.value = chatgptOauthModel;
        opt.textContent = chatgptOauthModel;
        chatgptModelSelect.appendChild(opt);
      }
      chatgptModelSelect.value = chatgptOauthModel;
    }
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
      statusEl.style.color = 'var(--red)';
      return;
    }

    const models = data.models || [];
    if (models.length === 0) {
      statusEl.textContent = '⚠️ API trả về 0 models. Nhập tên model thủ công.';
      statusEl.style.color = 'var(--orange)';
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
    statusEl.style.color = 'var(--green)';

    // Auto-clear status after 5s
    setTimeout(() => { statusEl.textContent = ''; }, 5000);
  } catch (e) {
    statusEl.textContent = '❌ Lỗi kết nối: ' + e.message;
    statusEl.style.color = 'var(--red)';
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
  if (baseUrlEl && !baseUrlEl.value.trim()) baseUrlEl.value = 'https://api.openai.com/v1';

  // Set token into keys list
  App.renderAiKeysList('chatgpt_oauth', [token]);

  // Auto-save
  await App.saveSettings();

  // Auto-load models using the new token
  await App.loadChatgptModels(token);

  if (status) {
    status.innerHTML = '<span style="color:var(--green)">✅ Đăng nhập ChatGPT Subscription thành công! Models đã được tải.</span>';
  }
  App.toast('✅ Đã đăng nhập & tự động cấu hình ChatGPT Subscription thành công!', 'success');

  setTimeout(function() {
    if (modal) modal.classList.remove('open');
  }, 1500);
};

// ── Load ChatGPT models into dropdown ──
App.loadChatgptModels = async function(tokenOverride) {
  var selectEl = document.getElementById('chatgpt-oauth-model');
  if (!selectEl) return;

  // Get token: from override, from keys list, or from saved settings
  var token = tokenOverride || '';
  if (!token) {
    var keysContainer = document.getElementById('chatgpt_oauth-keys-list');
    if (keysContainer) {
      var firstInput = keysContainer.querySelector('input[type="password"], input[type="text"]');
      if (firstInput) token = firstInput.value;
    }
  }

  if (!token) {
    App.toast('Chưa có Access Token. Vui lòng đăng nhập trước.', 'error');
    return;
  }

  var currentVal = selectEl.value;

  // Show loading state
  selectEl.disabled = true;
  selectEl.innerHTML = '<option value="">⏳ Đang tải models...</option>';

  try {
    var baseUrl = (document.getElementById('chatgpt-oauth-base-url') || {}).value || 'https://api.openai.com/v1';
    var resp = await fetch('/api/settings/chatgpt-oauth/models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ access_token: token, base_url: baseUrl.trim() })
    });
    var data = await resp.json();

    if (data.success && data.models && data.models.length > 0) {
      selectEl.innerHTML = '';
      data.models.forEach(function(m) {
        var opt = document.createElement('option');
        opt.value = m.id;
        opt.textContent = m.name || m.id;
        selectEl.appendChild(opt);
      });

      // Restore previous selection if it exists
      if (currentVal) {
        var found = Array.from(selectEl.options).some(function(o) { return o.value === currentVal; });
        if (found) selectEl.value = currentVal;
      }

      var source = data.source === 'chatgpt_backend' ? 'ChatGPT' : data.source === 'openai_api' ? 'OpenAI API' : 'mặc định';
      App.toast('Đã tải ' + data.models.length + ' models từ ' + source, 'success');
    } else {
      selectEl.innerHTML = '<option value="gpt-4o">gpt-4o</option>';
      App.toast('Không thể tải models. Đang dùng mặc định.', 'error');
    }
  } catch(e) {
    selectEl.innerHTML = '<option value="gpt-4o">gpt-4o</option>';
    App.toast('Lỗi tải models: ' + e.message, 'error');
  } finally {
    selectEl.disabled = false;
  }
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

  // Try to parse as JSON (from chatgpt.com/api/auth/session)
  try {
    // Find JSON object in pasted text (may have surrounding whitespace/text)
    var jsonMatch = raw.match(/\{[\s\S]*\}/);
    if (jsonMatch) {
      var parsed = JSON.parse(jsonMatch[0]);
      if (parsed.accessToken) {
        token = parsed.accessToken;
      } else if (parsed.access_token) {
        token = parsed.access_token;
      } else if (parsed.token) {
        token = parsed.token;
      }
    }
  } catch(e) {
    // Not valid JSON - try regex extraction for accessToken value
    var atMatch = raw.match(/"accessToken"\s*:\s*"([^"]+)"/);
    if (atMatch) {
      token = atMatch[1];
    }
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
        status.innerHTML = '<span style="color:var(--red)">❌ ' + (data.error || 'Xác thực thất bại') + '</span>';
      }
      App.toast(data.error || 'Token không hợp lệ', 'error');
      return;
    }

    // Use the verified token
    App._applyChatgptOAuthToken(data.token);

  } catch(e) {
    if (status) {
      status.innerHTML = '<span style="color:var(--red)">❌ Lỗi kết nối: ' + e.message + '</span>';
    }
    App.toast('Lỗi kết nối: ' + e.message, 'error');
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
  if (!provider) { App.toast('Chọn provider AI trước', 'error'); return; }
  // Try DOM keys first, fallback to DB-stored keys
  let keys = App.collectAiKeys(provider);
  if (keys.length === 0) {
    try {
      const stored = await API.getSetting('ai_keys_' + provider);
      keys = JSON.parse(stored.value || '[]');
    } catch(e) { keys = []; }
  }
  if (keys.length === 0) { App.toast('Thêm API Key trước khi test', 'error'); return; }
  const sampleText = 'Hi! Looking for BD partners for Weex. Interested in collaborating?';
  btn.disabled = true;
  btn.textContent = 'Đang remix...';
  resultBox.classList.add('hidden');
  try {
    const body = { provider: provider, keys: keys, text: sampleText };
    if (provider === 'openai_compatible') {
      body.base_url = (document.getElementById('oai-compat-base-url')?.value || "").trim();
      body.model = App.getOaiCompatModel();
    } else if (provider === 'chatgpt_oauth') {
      body.base_url = (document.getElementById('chatgpt-oauth-base-url')?.value || 'https://api.openai.com/v1').trim();
      body.model = (document.getElementById('chatgpt-oauth-model')?.value || 'gpt-4o').trim();
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
      App.toast('AI remix thành công!', 'success');
    } else {
      App.toast('Lỗi: ' + (data.detail || data.error || 'Unknown'), 'error');
    }
  } catch(e) {
    App.toast('Test thất bại: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '🧪 Test Remix';
  }
};


function esc(s){if(!s)return'';const d=document.createElement('div');d.textContent=s;return d.innerHTML}
function _mix(c,p){return `color-mix(in srgb, ${c} ${p}%, transparent)`}
function _cv(v,a){let c=getComputedStyle(document.documentElement).getPropertyValue(v).trim()||'#888';if(c[0]==='#'){if(c.length===4)c='#'+[...c.slice(1)].map(x=>x+x).join('');const n=parseInt(c.slice(1,7),16);return `rgba(${n>>16&255},${n>>8&255},${n&255},${a})`}return c}

function formatDate(s){if(!s)return'—';try{return new Date(s).toLocaleString('vi-VN',{timeZone:'Asia/Ho_Chi_Minh'})}catch{return s}}

function formatDays(s){if(!s)return'';const n={'1':'T2','2':'T3','3':'T4','4':'T5','5':'T6','6':'T7','7':'CN'};return s.split(',').map(d=>n[d.trim()]||d).join(', ')}



// ══════════════════════════════════════════════════════════

//  CHANNEL MANAGER

// ══════════════════════════════════════════════════════════

App._chChannels = [];

App._chFiltered = [];

App._chSelected = new Set();

App._chAccountId = null;
App._dialogsCache = new Map();
App._dialogsRequests = new Map();


App._populateChAccountSelect = async function() {

  const sel = document.getElementById('ch-account-select');

  if (!sel) return;

  // Nhớ account đang chọn trước khi rebuild <select> (innerHTML rebuild xoá sel.value)
  const prevSelected = sel.value || (App._chAccountId != null ? String(App._chAccountId) : '');

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

  // Giữ account đã chọn nếu vẫn còn trong danh sách, không thì fallback account đầu tiên
  if (accounts.some(a => String(a.id) === prevSelected)) sel.value = prevSelected;
  else if (accounts.length > 0) sel.value = accounts[0].id;

  App.loadChannels();

};



// ponytail: cache TTL 60s, stale-while-revalidate. Upgrade to indexedDB if data > 5MB.
App._DIALOGS_TTL = 60000;
// Epoch tăng mỗi lần mutation (leave/delete) → response bay trước đó bị bỏ, không hồi sinh item
App._dialogsEpoch = new Map();

App._getDialogsEpoch = function(accountId) {
  return App._dialogsEpoch.get(accountId) || 0;
};

App._fetchDialogs = async function(accountId, force) {
  // Dedupe in-flight: nếu đang fetch cho cùng account → trả cùng promise.
  // force=true (refresh explicit / sau mutation) bỏ qua dedupe vì request đang bay
  // có thể mang dữ liệu trước mutation → sẽ hồi sinh item đã xoá.
  const existing = App._dialogsRequests.get(accountId);
  if (existing && !force) return existing;

  const p = (async () => {
    const res = await fetch(`/api/chats?account_id=${accountId}`);
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try { const body = await res.json(); detail = body.detail || body.error || detail; } catch(_){}
      throw new Error(detail);
    }
    const data = await res.json();
    if (!data || !Array.isArray(data.chats)) throw new Error('Schema không hợp lệ: thiếu chats[]');
    return data.chats;
  })();

  App._dialogsRequests.set(accountId, p);
  try { return await p; } finally { App._dialogsRequests.delete(accountId); }
};

App.loadChannels = async function(force) {

  const sel = document.getElementById('ch-account-select');

  if (!sel) return;

  const accountId = parseInt(sel.value);

  if (!accountId) {
    (document.getElementById('ch-loading')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');
    (document.getElementById('ch-empty')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('hidden');
    return;
  }

  App._chAccountId = accountId;

  App._chSelected.clear();

  App._updateActionBar();

  // Cache hit: hiển thị stale ngay, refresh nền nếu hết TTL
  const cached = App._dialogsCache.get(accountId);
  const now = Date.now();
  if (cached && !force) {
    App._chChannels = cached.data;
    App._filterChannels();
    if (now - cached.ts < App._DIALOGS_TTL) return; // fresh → done
    // stale → refresh nền, KHÔNG show loading spinner
    const epochAtStart = App._getDialogsEpoch(accountId);
    App._fetchDialogs(accountId).then(chats => {
      // Guard: account có thể đã đổi trong lúc chờ
      if (App._chAccountId !== accountId) return;
      // Guard: mutation (leave/delete) xảy ra trong lúc chờ → bỏ response cũ, không hồi sinh item
      if (App._getDialogsEpoch(accountId) !== epochAtStart) return;
      App._dialogsCache.set(accountId, {data: chats, ts: Date.now()});
      App._chChannels = chats;
      App._filterChannels();
    }).catch(() => {}); // silent background refresh failure
    return;
  }



  (document.getElementById('ch-loading')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).remove('hidden');

  (document.getElementById('ch-loading') || me_dummy).textContent = '\u0110ang t\u1EA3i danh s\u00E1ch k\u00EAnh...';

  (document.getElementById('ch-table')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');

  (document.getElementById('ch-empty')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');

  (document.getElementById('ch-status-banner')?.classList || {add:()=>{},remove:()=>{},toggle:()=>{}}).add('hidden');

  (document.getElementById('ch-search') || me_dummy).value = '';

  (document.getElementById('ch-type-filter') || me_dummy).value = 'all';



  try {

    const epochAtStart = App._getDialogsEpoch(accountId);
    const chats = await App._fetchDialogs(accountId, force);
    // Guard: account đã đổi trong lúc await → bỏ kết quả
    if (App._chAccountId !== accountId) return;
    // Guard: mutation xảy ra trong lúc await → bỏ response cũ, không hồi sinh item
    if (App._getDialogsEpoch(accountId) !== epochAtStart) return;
    App._dialogsCache.set(accountId, {data: chats, ts: Date.now()});

    App._chChannels = chats;

    App._filterChannels();

  } catch(e) {

    if (App._chAccountId !== accountId) return;
    (document.getElementById('ch-loading') || me_dummy).textContent = 'L\u1ED7i: ' + e.message;

  }

};

// Refresh explicit: bỏ qua cache kể cả còn fresh.
// index.html line ~335 cần đổi onclick="App.loadChannels()" → "App.refreshChannels()" (parent sở hữu file đó)
App.refreshChannels = function() {
  const sel = document.getElementById('ch-account-select');
  const accountId = sel ? parseInt(sel.value) : null;
  if (accountId) App._dialogsCache.delete(accountId);
  return App.loadChannels(true);
};

// Invalidate cache của 1 account (gọi sau leave/delete thành công)
// Tăng epoch → mọi in-flight response bay trước đó sẽ bị bỏ, không hồi sinh item
App._invalidateDialogsCache = function(accountId) {
  App._dialogsCache.delete(accountId);
  App._dialogsEpoch.set(accountId, (App._dialogsEpoch.get(accountId) || 0) + 1);
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

  const typeColor = {channel: 'var(--orange)', supergroup: 'var(--accent)', group: 'var(--green)', bot: 'var(--violet)'};



  tbody.innerHTML = channels.map((ch, i) => {

    const checked = App._chSelected.has(ch.chat_id) ? 'checked' : '';

    return `

    <tr style="border-bottom:1px solid var(--border);transition:background .15s;"

        onmouseover="this.style.background='color-mix(in srgb, var(--text) 4%, transparent)'"

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
      App._invalidateDialogsCache(App._chAccountId);

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

  const botCount = App._chChannels.filter(c => selected.includes(c.chat_id) && c.chat_type === 'bot').length;
  const actionWord = botCount === selected.length ? 'Xo\u00E1' : 'R\u1EDDi/Xo\u00E1';
  const kindWord = botCount === selected.length ? 'bot' : 'k\u00EAnh/nh\u00F3m/bot';

  if (!confirm(`\u26A0\uFE0F ${actionWord} ${selected.length} ${kindWord} \u0111\u00E3 ch\u1ECDn?\n\n${names.join('\n')}\n\nThao t\u00E1c n\u00E0y kh\u00F4ng th\u1EC3 ho\u00E0n t\u00E1c!`)) return;



  App._showChBanner(`\u23F3 \u0110ang r\u1EDDi ${selected.length} k\u00EAnh, vui l\u00F2ng ch\u1EDD...`, 'info');



  let successCount = 0, failCount = 0;
  const failReasons = [];

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
        App._invalidateDialogsCache(App._chAccountId);

        successCount++;

      } else {
        failCount++; if (row) row.style.opacity = '1';
        const d = await res.json().catch(() => ({}));
        const t = (App._chChannels.find(c => c.chat_id === chatId) || {}).chat_title || chatId;
        failReasons.push(`${t}: ${d.detail || ('HTTP ' + res.status)}`);
      }

    } catch(e) { failCount++; if (row) row.style.opacity = '1'; failReasons.push(String(e.message || e)); }

    // Small delay to avoid flood

    await new Promise(r => setTimeout(r, 1500));

  }



  App._chSelected.clear();

  App._filterChannels();

  App._showChBanner(

    `\u2705 Ho\u00E0n t\u1EA5t: r\u1EDDi ${successCount}/${selected.length} k\u00EAnh` + (failCount > 0 ? `, ${failCount} th\u1EA5t b\u1EA1i \u2014 ${App._esc ? App._esc(failReasons.slice(0, 3).join(' \u00B7 ')) : failReasons.slice(0, 3).join(' \u00B7 ')}` : ''),

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

  const colors = { success: 'color-mix(in srgb, var(--green) 15%, transparent)', error: 'color-mix(in srgb, var(--red) 15%, transparent)', warning: 'color-mix(in srgb, var(--orange) 15%, transparent)', info: 'color-mix(in srgb, var(--accent) 15%, transparent)' };

  const borders = { success: 'var(--green)', error: 'var(--red)', warning: 'var(--orange)', info: 'var(--accent)' };

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

// Patch loadSettings to also load API key UI + Daily Summary + Command Bot + Forum Inbox
const _origLoadSettings = App.loadSettings;
App.loadSettings = async function() {
  if (_origLoadSettings) await _origLoadSettings.call(this);
  App.loadApiKeyUi();
  App.loadDailySummary();
  App.loadCommandBotConfig();
  App.loadForumInboxConfig();
  App.loadTeamUsers();
};

// ══════════════════════════════════════════════════════════
//  ACCOUNT BULK SELECT + TOGGLE
// ══════════════════════════════════════════════════════════

App._accSelected = new Set();

App.onAccountSelectChange = function() {
  App._accSelected = new Set();
  document.querySelectorAll('.acc-select-cb:checked').forEach(cb => App._accSelected.add(+cb.dataset.accId));
  const bar = document.getElementById('acc-selection-bar');
  const cnt = document.getElementById('acc-selected-count');
  if (bar) bar.classList.toggle('hidden', App._accSelected.size === 0);
  if (cnt) cnt.textContent = App._accSelected.size;
};

App.toggleSelectAllAccounts = function() {
  const cbs = document.querySelectorAll('.acc-select-cb');
  const allChecked = [...cbs].every(cb => cb.checked);
  cbs.forEach(cb => cb.checked = !allChecked);
  App.onAccountSelectChange();
};

App.clearAccountSelection = function() {
  document.querySelectorAll('.acc-select-cb').forEach(cb => cb.checked = false);
  App._accSelected.clear();
  App.onAccountSelectChange();
};

App.bulkToggleSelectedAccounts = async function(activate) {
  const ids = [...App._accSelected];
  if (!ids.length) return;
  const action = activate ? 'BẬT' : 'TẮT';
  if (!confirm(`${action} ${ids.length} tài khoản đã chọn?`)) return;
  let ok = 0, fail = 0;
  for (const id of ids) {
    try {
      await API.toggleAccountActive(id, activate);
      ok++;
    } catch { fail++; }
  }
  App.toast(`${action} ${ok}/${ids.length} tài khoản${fail ? ` (${fail} lỗi)` : ''}`, fail ? 'warning' : 'success');
  App.clearAccountSelection();
  App.loadAccounts();
};

App.bulkToggleAccounts = async function(activate) {
  const accounts = App._accounts || [];
  const targets = activate ? accounts.filter(a => a.is_paused) : accounts.filter(a => !a.is_paused);
  if (!targets.length) { App.toast(`Không có tài khoản nào cần ${activate ? 'bật' : 'tắt'}`, 'info'); return; }
  const action = activate ? 'BẬT' : 'TẮT';
  if (!confirm(`${action} tất cả ${targets.length} tài khoản ${activate ? 'đang tắt' : 'đang bật'}?`)) return;
  let ok = 0, fail = 0;
  for (const a of targets) {
    try {
      await API.toggleAccountActive(a.id, activate);
      ok++;
    } catch { fail++; }
  }
  App.toast(`${action} ${ok}/${targets.length} tài khoản${fail ? ` (${fail} lỗi)` : ''}`, fail ? 'warning' : 'success');
  App.loadAccounts();
};

// ══════════════════════════════════════════════════════════
//  TELEGRAM COMMAND BOT SETTINGS
// ══════════════════════════════════════════════════════════

App.toggleCommandBotEnabled = function(enabled) {
  const fields = document.getElementById('cmd-bot-fields');
  if (fields) fields.classList.toggle('hidden', !enabled);
};

App.setCommandBotBadge = function(running, enabled) {
  const badge = document.getElementById('cmd-bot-badge');
  if (!badge) return;
  badge.className = 'badge ' + (running ? 'badge-success' : enabled ? 'badge-warning' : 'badge-gray');
  badge.textContent = running ? 'Đang chạy' : enabled ? 'Chưa chạy' : 'Đang tắt';
};

App.loadCommandBotConfig = async function() {
  try {
    const res = await fetch('/api/settings/command-bot/config', { headers: API.getHeaders() });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const cfg = await res.json();
    const enabled = document.getElementById('cmd-bot-enabled');
    const token = document.getElementById('cmd-bot-token');
    const admins = document.getElementById('cmd-bot-admin-ids');
    if (enabled) enabled.checked = !!cfg.enabled;
    if (token) token.value = cfg.token || '';
    if (admins) admins.value = cfg.admin_ids || '';
    App.toggleCommandBotEnabled(!!cfg.enabled);
    App.checkCommandBotStatus(false);
  } catch (e) {
    console.warn('loadCommandBotConfig failed', e);
  }
};

App.saveCommandBotConfig = async function() {
  const msg = document.getElementById('cmd-bot-status-msg');
  const payload = {
    enabled: !!document.getElementById('cmd-bot-enabled')?.checked,
    token: document.getElementById('cmd-bot-token')?.value || '',
    admin_ids: document.getElementById('cmd-bot-admin-ids')?.value || ''
  };
  if (msg) msg.textContent = 'Đang lưu...';
  try {
    const res = await fetch('/api/settings/command-bot/config', {
      method: 'POST',
      headers: { ...API.getHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.error) throw new Error(data.error || ('HTTP ' + res.status));
    App.setCommandBotBadge(!!data.running, payload.enabled);
    if (msg) msg.textContent = payload.enabled
      ? (data.running ? '✅ Đã lưu và bot đang chạy.' : '⚠️ Đã lưu nhưng bot chưa chạy.')
      : '✅ Đã tắt bot.';
  } catch (e) {
    App.setCommandBotBadge(false, payload.enabled);
    if (msg) msg.textContent = '❌ Lỗi: ' + e.message;
  }
};

App.checkCommandBotStatus = async function(showMsg = true) {
  const msg = document.getElementById('cmd-bot-status-msg');
  try {
    const res = await fetch('/api/settings/command-bot/status', { headers: API.getHeaders() });
    const data = await res.json();
    const enabled = !!document.getElementById('cmd-bot-enabled')?.checked;
    App.setCommandBotBadge(!!data.running, enabled);
    if (showMsg && msg) msg.textContent = data.running ? '✅ Bot đang chạy.' : '⚠️ Bot chưa chạy.';
  } catch (e) {
    if (showMsg && msg) msg.textContent = '❌ Không kiểm tra được trạng thái bot.';
  }
};

// ══════════════════════════════════════════════════════════
//  FORUM INBOX (SALES WAR ROOM) SETTINGS
// ══════════════════════════════════════════════════════════

App.toggleForumInboxEnabled = function(enabled) {
  const badge = document.getElementById('forum-inbox-badge');
  if (badge && !enabled) {
    badge.className = 'badge badge-gray';
    badge.textContent = 'Đang tắt';
  }
};

App.setForumInboxBadge = function(enabled) {
  const badge = document.getElementById('forum-inbox-badge');
  if (!badge) return;
  badge.className = 'badge ' + (enabled ? 'badge-success' : 'badge-gray');
  badge.textContent = enabled ? 'Đang bật' : 'Đang tắt';
};

App.loadForumInboxConfig = async function() {
  try {
    const res = await fetch('/api/settings/forum-inbox/config', { headers: API.getHeaders() });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const cfg = await res.json();
    const enabled = document.getElementById('forum-inbox-enabled');
    const group = document.getElementById('forum-inbox-group-id');
    const topic = document.getElementById('forum-inbox-general-topic-id');
    if (enabled) enabled.checked = !!cfg.enabled;
    if (group) group.value = cfg.group_id ? String(cfg.group_id) : '';
    if (topic) topic.value = cfg.general_topic_id ? String(cfg.general_topic_id) : '1';
    App.setForumInboxBadge(!!cfg.enabled);
    App.loadForumInboxTopicCount();
  } catch (e) {
    console.warn('loadForumInboxConfig failed', e);
  }
};

App.resolveForumInboxLink = async function() {
  const linkEl = document.getElementById('forum-inbox-link');
  const msg = document.getElementById('forum-inbox-link-msg');
  const link = (linkEl?.value || '').trim();
  if (!link) { if (msg) msg.textContent = '❌ Dán link nhóm vào trước đã.'; return; }
  if (msg) msg.textContent = '⏳ Đang lấy ID...';
  try {
    const res = await fetch('/api/settings/forum-inbox/resolve-link', {
      method: 'POST',
      headers: { ...API.getHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ link })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || ('HTTP ' + res.status));
    const group = document.getElementById('forum-inbox-group-id');
    const topic = document.getElementById('forum-inbox-general-topic-id');
    if (group) group.value = String(data.group_id);
    if (topic) topic.value = String(data.general_topic_id || 1);
    if (msg) msg.textContent = '✅ ' + (data.message || 'Đã lấy ID') +
      (data.title ? ` — ${data.title}` : '') + ' (nhớ bấm Lưu cấu hình).';
  } catch (e) {
    if (msg) msg.textContent = '❌ ' + e.message;
  }
};

App.saveForumInboxConfig = async function() {
  const msg = document.getElementById('forum-inbox-status-msg');
  const groupVal = (document.getElementById('forum-inbox-group-id')?.value || '').trim();
  const topicVal = (document.getElementById('forum-inbox-general-topic-id')?.value || '1').trim();
  const enabled = !!document.getElementById('forum-inbox-enabled')?.checked;

  const groupId = parseInt(groupVal, 10);
  const generalId = parseInt(topicVal, 10) || 1;

  if (enabled && (!groupId || groupId >= 0)) {
    if (msg) msg.textContent = '❌ Group ID phải là số âm, ví dụ -1004352266125.';
    return;
  }

  const payload = {
    enabled: enabled,
    group_id: isNaN(groupId) ? 0 : groupId,
    general_topic_id: isNaN(generalId) ? 1 : generalId
  };

  if (msg) msg.textContent = 'Đang lưu...';
  try {
    const res = await fetch('/api/settings/forum-inbox/config', {
      method: 'POST',
      headers: { ...API.getHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || ('HTTP ' + res.status));
    App.setForumInboxBadge(payload.enabled);
    if (msg) msg.textContent = payload.enabled
      ? '✅ Đã lưu và bật Forum Inbox.'
      : '✅ Đã lưu (trạng thái: Đang tắt).';
  } catch (e) {
    if (msg) msg.textContent = '❌ Lỗi: ' + e.message;
  }
};

// ── Forum Inbox backfill: open topics for conversations that already replied ──
App.loadForumInboxTopicCount = async function() {
  const el = document.getElementById('forum-inbox-topic-count');
  if (!el) return;
  try {
    const res = await fetch('/api/settings/forum-inbox/topics?limit=1', { headers: API.getHeaders() });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    el.textContent = String(data.count ?? 0);
  } catch (e) {
    el.textContent = '—';
  }
};

App.backfillForumInboxTopics = async function() {
  const msg = document.getElementById('forum-inbox-backfill-msg');
  const daysEl = document.getElementById('forum-inbox-backfill-days');
  const days = Math.max(1, Math.min(parseInt(daysEl?.value || '7', 10) || 7, 90));
  if (msg) msg.textContent = `⏳ Đang tạo topic cho hội thoại ${days} ngày gần nhất...`;
  try {
    const res = await fetch('/api/settings/forum-inbox/backfill', {
      method: 'POST',
      headers: { ...API.getHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ days: days, limit: 500 })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || ('HTTP ' + res.status));
    if (data.reason === 'bot_offline') {
      if (msg) msg.textContent = '❌ Command Bot chưa chạy — bật bot ở mục trên rồi thử lại.';
      return;
    }
    if (data.reason === 'disabled') {
      if (msg) msg.textContent = '❌ Forum Inbox đang tắt — bật và Lưu cấu hình trước.';
      return;
    }
    if (msg) {
      msg.textContent = `✅ Tạo ${data.created || 0} topic (bỏ qua ${data.skipped || 0}, lỗi ${data.failed || 0}) `
        + `trong ${data.candidates || 0} hội thoại ${days} ngày gần nhất.`;
    }
    App.loadForumInboxTopicCount();
    if (typeof App.toast === 'function') App.toast(`Đã tạo ${data.created || 0} topic`, 'success');
  } catch (e) {
    if (msg) msg.textContent = '❌ Lỗi: ' + e.message;
  }
};

App.replayForumInboxHistory = async function() {
  const msg = document.getElementById('forum-inbox-backfill-msg');
  const daysEl = document.getElementById('forum-inbox-backfill-days');
  const days = Math.max(1, Math.min(parseInt(daysEl?.value || '30', 10) || 30, 365));
  if (msg) msg.textContent = `⏳ Đang nạp lịch sử ${days} ngày vào từng topic... (có thể mất vài phút)`;
  try {
    const res = await fetch('/api/settings/forum-inbox/replay-history', {
      method: 'POST',
      headers: { ...API.getHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ days: days, limit: 500 })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || ('HTTP ' + res.status));
    if (data.reason === 'bot_offline') {
      if (msg) msg.textContent = '❌ Command Bot chưa chạy — bật bot ở mục trên rồi thử lại.';
      return;
    }
    if (data.reason === 'disabled') {
      if (msg) msg.textContent = '❌ Forum Inbox đang tắt — bật và Lưu cấu hình trước.';
      return;
    }
    if (msg) {
      msg.textContent = `✅ Nạp lịch sử ${data.replayed || 0} topic (bỏ qua ${data.skipped || 0} — `
        + `không có tin trong ${days} ngày, lỗi ${data.failed || 0}).`;
    }
    if (typeof App.toast === 'function') App.toast(`Đã nạp lịch sử ${data.replayed || 0} topic`, 'success');
  } catch (e) {
    if (msg) msg.textContent = '❌ Lỗi: ' + e.message;
  }
};

App.toggleCmdBotTokenVisibility = function() {
  const input = document.getElementById('cmd-bot-token');
  if (input) input.type = input.type === 'password' ? 'text' : 'password';
};
App.openCommandBotLogs = async function() {
  try {
    const res = await fetch('/api/settings/command-bot/logs?limit=20', { headers: API.getHeaders() });
    const data = await res.json();
    const rows = (data.actions || []).map(a =>
      `#${a.id} ${a.created_at} | ${a.action} | acc=${a.account_id || '-'} | target=${a.target_username || '-'} | ${a.result}`
    ).join('\n') || 'Chưa có lệnh nào.';
    alert(rows);
  } catch (e) {
    alert('Không tải được nhật ký lệnh.');
  }
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

  let _targetsCache = [];

  async function init() {
    _buildEmojiPicker();
    // Song song — không chờ tuần tự nữa
    await Promise.all([_buildAccountList(), loadTargets(), loadLogs()]);
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
      statusDot.style.cssText = `display:inline-block;width:7px;height:7px;border-radius:50%;background:${isOnline ? 'var(--green)' : 'var(--text3)'};flex-shrink:0;`;
      label.appendChild(statusDot);
      label.appendChild(document.createTextNode(' ' + (acc.name || `acc#${acc.id}`)));
      container.appendChild(label);
    });
  }

  function _getSelectedAccounts() {
    return [...document.querySelectorAll('#rt-account-list input[type=checkbox]:checked')].map(c => parseInt(c.value));
  }

  // Client-side mirror of API validation for react_count_min/max
  function _validateReactCount(rcMin, rcMax) {
    if (rcMax > 0) {
      if (rcMin < 1) { alert('Số acc min phải ≥ 1 khi đặt max'); return false; }
      if (rcMax < rcMin) { alert('Số acc max phải ≥ min'); return false; }
    } else if (rcMin > 0) {
      alert('Thiếu số acc max (phải > 0 khi min > 0)'); return false;
    }
    return true;
  }

  async function addTarget() {
    const link = (document.getElementById('rt-link')?.value || "").trim();
    const delayMin = parseInt(document.getElementById('rt-delay-min')?.value) || 5;
    const delayMax = parseInt(document.getElementById('rt-delay-max')?.value) || 30;
    const viewEnabled = document.getElementById('rt-view-enabled')?.checked ? 1 : 0;
    const viewRatio = parseFloat(document.getElementById('rt-view-ratio')?.value || '1.0');
    const rcMin = parseInt(document.getElementById('rt-rc-min')?.value) || 0;
    const rcMax = parseInt(document.getElementById('rt-rc-max')?.value) || 0;
    const accIds = _getSelectedAccounts();
    if (!link) { alert('Vui lòng nhập link kênh'); return; }
    if (!accIds.length) { alert('Vui lòng chọn ít nhất 1 tài khoản'); return; }
    if (!_validateReactCount(rcMin, rcMax)) return;

    const btn = document.querySelector('#view-reactions .btn-primary');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Đang join...'; }
    try {
      const res = await ReactionsAPI.addTarget({
        channel_link: link, account_ids: accIds,
        reactions: [...selectedEmojis].map(_sanitizeEmoji), delay_min: delayMin, delay_max: delayMax, auto_join: true,
        view_enabled: viewEnabled, view_ratio: viewRatio,
        react_count_min: rcMin, react_count_max: rcMax
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

  const _RATIO_OPTS = [[1.0,'100%'],[0.75,'75%'],[0.5,'50%'],[0.25,'25%']];

  async function loadTargets() {
    const tbody = document.getElementById('rt-targets-body');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center">Đang tải...</td></tr>';
    try {
      const { targets } = await ReactionsAPI.getTargets();
      _targetsCache = targets || [];
      if (!_targetsCache.length) {
        tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--text2)">Chưa có kênh nào</td></tr>'; return;
      }
      tbody.innerHTML = _targetsCache.map(t => {
        const ratioOpts = _RATIO_OPTS.map(([v,l]) =>
          `<option value="${v}" ${Math.abs((t.view_ratio??1)-v)<0.001?'selected':''}>${l}</option>`).join('');
        return `
        <tr>
          <td><strong>${_esc(t.channel_title||t.channel_link)}</strong><br><small style="color:var(--text2)">${_esc(t.channel_link)}</small></td>
          <td>${(t.account_ids||[]).length} acc</td>
          <td style="font-size:1.2rem">${(t.reactions||['👍']).join(' ')}${t.react_count_max>0?`<br><span style="font-size:.7rem;color:var(--text2);background:var(--surface);border:1px solid var(--border);border-radius:.3rem;padding:.1rem .3rem;white-space:nowrap">🎲 ${t.react_count_min}–${t.react_count_max} acc</span>`:''}</td>
          <td>
            <div style="display:flex;align-items:center;gap:.3rem">
              <input id="rt-dmin-${t.id}" type="number" min="1" value="${t.delay_min}" class="form-input" style="width:58px;padding:.25rem .4rem;font-size:.85rem">
              <span style="color:var(--text2)">–</span>
              <input id="rt-dmax-${t.id}" type="number" min="1" value="${t.delay_max}" class="form-input" style="width:58px;padding:.25rem .4rem;font-size:.85rem">
              <span style="color:var(--text2);font-size:.75rem">s</span>
            </div>
            <div style="display:flex;align-items:center;gap:.3rem;margin-top:.3rem" title="Số acc thả mỗi bài (0 = tất cả)">
              <span style="font-size:.75rem">🎲</span>
              <input id="rt-rcmin-${t.id}" type="number" min="0" value="${t.react_count_min||0}" class="form-input" style="width:58px;padding:.25rem .4rem;font-size:.85rem">
              <span style="color:var(--text2)">–</span>
              <input id="rt-rcmax-${t.id}" type="number" min="0" value="${t.react_count_max||0}" class="form-input" style="width:58px;padding:.25rem .4rem;font-size:.85rem">
              <span style="color:var(--text2);font-size:.75rem">acc</span>
            </div>
          </td>
          <td>
            <div style="display:flex;align-items:center;gap:.4rem">
              <input id="rt-ven-${t.id}" type="checkbox" ${t.view_enabled?'checked':''} style="width:15px;height:15px;accent-color:var(--accent)">
              <select id="rt-vratio-${t.id}" class="form-input" style="width:74px;padding:.25rem .3rem;font-size:.8rem">${ratioOpts}</select>
            </div>
          </td>
          <td id="rt-views-${t.id}"><span style="color:var(--text2);font-size:.8rem">⏳</span></td>
          <td><span class="status-badge ${t.is_active?'success':'failed'}" onclick="Reactions.toggleActive(${t.id},${t.is_active})" style="cursor:pointer">${t.is_active?'● Bật':'○ Tắt'}</span></td>
          <td>
            <button class="btn btn-sm btn-primary" onclick="Reactions.saveTarget(${t.id})" title="Lưu delay + view">💾</button>
            <button class="btn btn-sm" onclick="Reactions.manualJoin(${t.id})" title="Re-join">🔗</button>
            <button class="btn btn-sm" style="background:var(--red,.8)" onclick="Reactions.deleteTarget(${t.id})" title="Xóa">🗑</button>
          </td>
        </tr>`;
      }).join('');
    } catch(e) { tbody.innerHTML = `<tr><td colspan="8" style="color:red">Lỗi: ${e.message}</td></tr>`; }
    // Views: fetch nền, không block render, dùng cache targets (không gọi lại API)
    _targetsCache.filter(t => t.is_active).forEach(t => _fetchViews(t.id));
  }

  async function saveTarget(id) {
    const dmin = parseInt(document.getElementById(`rt-dmin-${id}`)?.value) || 5;
    const dmax = parseInt(document.getElementById(`rt-dmax-${id}`)?.value) || 30;
    const ven  = document.getElementById(`rt-ven-${id}`)?.checked ? 1 : 0;
    const vr   = parseFloat(document.getElementById(`rt-vratio-${id}`)?.value || '1.0');
    const rcMin = parseInt(document.getElementById(`rt-rcmin-${id}`)?.value) || 0;
    const rcMax = parseInt(document.getElementById(`rt-rcmax-${id}`)?.value) || 0;
    if (dmax < dmin) { App.toast('Delay max phải ≥ delay min', 'error'); return; }
    if (!_validateReactCount(rcMin, rcMax)) return;
    try {
      const res = await ReactionsAPI.updateTarget(id, { delay_min: dmin, delay_max: dmax, view_enabled: ven, view_ratio: vr, react_count_min: rcMin, react_count_max: rcMax });
      if (res.ok) App.toast('✅ Đã lưu cấu hình kênh', 'success');
      else App.toast('❌ Lưu thất bại', 'error');
    } catch(e) { App.toast('❌ ' + e.message, 'error'); }
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

  let _rtLogs = [];       // toàn bộ log đã tải (fetch 200), phân trang client
  let _rtLogsPage = 1;
  const _RT_LOGS_PER_PAGE = 50;

  async function loadLogs() {
    const tbody = document.getElementById('rt-logs-body');
    if (!tbody) return;
    try {
      // Fetch 200 dòng 1 lần rồi phân trang client — tránh dựng hàng trăm DOM node.
      const { logs } = await ReactionsAPI.getLogs(null, 200);
      _rtLogs = logs || [];
      _rtLogsPage = 1;
      _renderRtLogs();
    } catch(e) { tbody.innerHTML=`<tr><td colspan="7">Lỗi: ${e.message}</td></tr>`; }
  }

  function _renderRtLogs() {
    const tbody = document.getElementById('rt-logs-body');
    const pager = document.getElementById('rt-logs-pager');
    if (!tbody) return;
    if (!_rtLogs.length) {
      tbody.innerHTML='<tr><td colspan="7" style="text-align:center;color:var(--text2)">Chưa có lịch sử</td></tr>';
      if (pager) pager.innerHTML = '';
      return;
    }
    const st = paginate(_rtLogs, _rtLogsPage, _RT_LOGS_PER_PAGE);
    _rtLogsPage = st.page;
    tbody.innerHTML = st.slice.map(l=>`
      <tr>
        <td style="font-size:.8rem;white-space:nowrap">${_esc(l.sent_at)}</td>
        <td>${l.channel_id||'—'}</td>
        <td>${l.msg_id||'—'}</td>
        <td>acc#${l.account_id}</td>
        <td style="font-size:1.2rem">${l.reaction||'—'}</td>
        <td><span class="status-badge ${l.status}">${l.status}</span></td>
        <td style="font-size:.75rem;color:var(--text2)">${_esc(l.error_msg||'—')}</td>
      </tr>`).join('');
    renderPager(pager, st, (p) => { _rtLogsPage = p; _renderRtLogs(); });
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

  return { init, addTarget, loadTargets, loadLogs, toggleActive, deleteTarget, manualJoin, saveTarget, fetchViews: _fetchViews };
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
  startInboxBadgePolling(){
    if(Polling.isActive('inbox:badge')) return;
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
    Polling.start('inbox:badge', poll, 10000);
    Polling.run('inbox:badge');
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
      const rowStyle = isUnread ? 'background:rgba(var(--accent-rgb,255,136,0),.07)' : '';

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
    if (Polling.isActive('inbox:badge')) Polling.run('inbox:badge');
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
      const color = r.ok ? 'var(--green)' : 'var(--red)';
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
          const statusColor = a.has_proxy ? 'var(--accent-text)' : 'var(--orange)';
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
  _refreshTimer: null,
  _actionLogSummary: null,

  async init() {
    await Promise.all([this.loadGroups(), this.loadJobs().then(() => this.loadRecentLogs()), this.loadProgress(), this.loadActionLogs(), this.loadActionLogSummary()]);
  },

  cleanup() {
    if (this._refreshTimer) {
      clearInterval(this._refreshTimer);
      this._refreshTimer = null;
    }
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
            <button onclick="event.stopPropagation();Warmup.deleteGroup(${g.id})" style="background:color-mix(in srgb, var(--red) 15%, transparent);border:1px solid color-mix(in srgb, var(--red) 40%, transparent);color:var(--red);border-radius:6px;padding:3px 10px;cursor:pointer;font-size:.78rem">Xoa</button>
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
            <button onclick="Warmup.deleteScript(${s.id})" style="background:color-mix(in srgb, var(--red) 15%, transparent);border:1px solid color-mix(in srgb, var(--red) 40%, transparent);color:var(--red);border-radius:6px;padding:3px 10px;cursor:pointer;font-size:.78rem;margin-left:8px">Xoa</button>
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

        // Update stats (enhanced)
        this._renderStats(0, 0);
        return;
      }

      const running = this._jobs.filter(j => j.is_running || j.status === 'running').length;
      this._renderStats(this._jobs.length, running);

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
              <button class="btn btn-sm" style="background:color-mix(in srgb, var(--accent) 15%, transparent);color:var(--accent-text);border:1px solid color-mix(in srgb, var(--accent) 30%, transparent)" onclick="Warmup.viewJobLogs(${j.id})">Logs</button>
              <button onclick="Warmup.deleteJob(${j.id})" style="background:color-mix(in srgb, var(--red) 15%, transparent);border:1px solid color-mix(in srgb, var(--red) 40%, transparent);color:var(--red);border-radius:6px;padding:3px 10px;cursor:pointer;font-size:.78rem">Xoa</button>
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
      accEl.innerHTML = '<span style="color:var(--red)">Loi tai tai khoan</span>';
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
          const statusColor = l.status === 'success' ? 'var(--green)' : 'var(--red)';
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
        const statusColor = l.status === 'success' ? 'var(--green)' : 'var(--red)';
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

  // ── Render Enhanced Stats ──
  _renderStats(jobCount, runningCount) {
    const statsEl = document.getElementById('warmup-stats');
    const s = this._actionLogSummary || {};
    statsEl.innerHTML = `
      <div class="stat-card"><div class="stat-label">Nhom</div><div class="stat-value accent">${this._groups.length}</div></div>
      <div class="stat-card"><div class="stat-label">Jobs</div><div class="stat-value">${jobCount}</div></div>
      <div class="stat-card"><div class="stat-label">Dang chay</div><div class="stat-value green">${runningCount}</div></div>
      <div class="stat-card"><div class="stat-label">Actions (hom nay)</div><div class="stat-value">${s.total_today || 0}</div></div>
      <div class="stat-card"><div class="stat-label">Dang warmup</div><div class="stat-value" style="color:var(--purple)">${s.accounts_active || 0}</div></div>
      <div class="stat-card"><div class="stat-label">🎓 Graduated</div><div class="stat-value green">${s.accounts_graduated || 0}</div></div>
      <div class="stat-card"><div class="stat-label">⚠️ Flood (3 ngay)</div><div class="stat-value" style="color:var(--orange)">${s.flood_events_3d || 0}</div></div>
    `;
  },

  // ── Account Warmup Progress ──
  async loadProgress() {
    try {
      const data = await API.get('/api/warmup/progress');
      const progress = data.progress || [];
      const el = document.getElementById('warmup-progress-list');
      if (!el) return;
      if (!progress.length) {
        el.innerHTML = '<div style="color:var(--text2);padding:12px">Chua co account nao trong warmup.</div>';
        return;
      }

      // Populate account filter dropdown
      const filterSel = document.getElementById('warmup-log-filter-account');
      if (filterSel && filterSel.options.length <= 1) {
        progress.forEach(p => {
          const opt = document.createElement('option');
          opt.value = p.account_id;
          opt.textContent = p.account_name || ('Acc #' + p.account_id);
          filterSel.appendChild(opt);
        });
      }

      el.innerHTML = progress.map(p => {
        const stageNames = {1: 'PASSIVE', 2: 'WARMING', 3: 'ACTIVE', 4: 'READY'};
        const stageColors = {1: 'var(--purple)', 2: 'var(--orange)', 3: 'var(--orange)', 4: 'var(--green)'};
        const stage = p.graduated_at ? 5 : (p.stage || 1);
        const stageColor = stage === 5 ? 'var(--green)' : (stageColors[stage] || 'var(--purple)');
        const stageName = stage === 5 ? 'GRADUATED' : (stageNames[stage] || 'PASSIVE');
        const statusBadge = stage === 5
          ? '<span style="background:color-mix(in srgb, var(--green) 15%, transparent);color:var(--green);padding:2px 8px;border-radius:12px;font-size:.78rem">🎓 Graduated</span>'
          : `<span style="background:${_mix(stageColor,13)};color:${stageColor};padding:2px 8px;border-radius:12px;font-size:.78rem">Stage ${p.stage || 1} - ${stageName}</span>`;

        const msgPct = Math.min(100, ((p.messages_sent || 0) / 10) * 100);
        const reactPct = Math.min(100, ((p.reactions_sent || 0) / 15) * 100);
        const chanPct = Math.min(100, ((p.channels_joined || 0) / 3) * 100);

        const started = p.started_at ? p.started_at.replace('T', ' ').slice(0, 10) : 'N/A';

        return `<div class="card" style="padding:14px;border-left:3px solid ${stageColor}">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
            <strong>${this._esc(p.account_name || 'Acc #' + p.account_id)}</strong>
            ${statusBadge}
          </div>
          <div style="font-size:.82rem;color:var(--text2);margin-bottom:8px">
            Bat dau: ${started} | ${p.days_in_warmup || 0} ngay${p.flood_count_3d ? ' | <span style="color:var(--orange)">⚠️ ' + p.flood_count_3d + ' flood</span>' : ''}
          </div>
          <div style="margin-bottom:4px">
            <div style="display:flex;justify-content:space-between;font-size:.78rem;color:var(--text2)"><span>💬 Messages</span><span>${p.messages_sent || 0}/10</span></div>
            <div style="background:var(--bg3);border-radius:4px;height:6px;margin-top:2px"><div style="background:var(--purple);height:100%;border-radius:4px;width:${msgPct}%;transition:width .3s"></div></div>
          </div>
          <div style="margin-bottom:4px">
            <div style="display:flex;justify-content:space-between;font-size:.78rem;color:var(--text2)"><span>😀 Reactions</span><span>${p.reactions_sent || 0}/15</span></div>
            <div style="background:var(--bg3);border-radius:4px;height:6px;margin-top:2px"><div style="background:var(--orange);height:100%;border-radius:4px;width:${reactPct}%;transition:width .3s"></div></div>
          </div>
          <div>
            <div style="display:flex;justify-content:space-between;font-size:.78rem;color:var(--text2)"><span>📢 Channels</span><span>${p.channels_joined || 0}/3</span></div>
            <div style="background:var(--bg3);border-radius:4px;height:6px;margin-top:2px"><div style="background:var(--green);height:100%;border-radius:4px;width:${chanPct}%;transition:width .3s"></div></div>
          </div>
        </div>`;
      }).join('');
    } catch(e) { /* silent - endpoint may not exist yet */ }
  },

  // ── Action Logs v2 ──
  async loadActionLogs() {
    try {
      const accFilter = (document.getElementById('warmup-log-filter-account') || {}).value || '';
      const statusFilter = (document.getElementById('warmup-log-filter-status') || {}).value || '';
      let url = '/api/warmup/action-logs?limit=50';
      if (accFilter) url += '&account_id=' + accFilter;
      if (statusFilter) url += '&status=' + statusFilter;
      const data = await API.get(url);
      const logs = data.logs || [];
      const tbody = document.getElementById('warmup-action-log-body');
      if (!tbody) return;
      if (!logs.length) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text2);padding:16px">Chua co action log</td></tr>';
        return;
      }
      const actionIcons = {read_history:'📖',react_emoji:'😀',view_profile:'👤',post_message:'💬',forward_message:'↗️',join_channel:'📢'};
      const statusColors = {success:'var(--green)',error:'var(--red)',flood:'var(--orange)'};
      tbody.innerHTML = logs.map(l => {
        const time = l.created_at ? l.created_at.replace('T', ' ').slice(0, 19) : '';
        const icon = actionIcons[l.action_type] || '🔹';
        const sColor = statusColors[l.status] || 'var(--text2)';
        return `<tr>
          <td style="font-size:.82rem;white-space:nowrap">${time}</td>
          <td>${this._esc(l.account_name || 'Acc #' + l.account_id)}</td>
          <td>${icon} ${this._esc(l.action_type || '')}</td>
          <td style="font-size:.82rem;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${l.target_chat_id || '-'}</td>
          <td style="color:${sColor};font-weight:500">${l.status || ''}</td>
          <td style="font-size:.78rem;color:var(--text2);max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${this._esc((l.error_detail || '').substring(0, 80))}</td>
        </tr>`;
      }).join('');
    } catch(e) { /* silent - endpoint may not exist yet */ }
  },

  // ── Action Log Summary ──
  async loadActionLogSummary() {
    try {
      const data = await API.get('/api/warmup/action-logs/summary');
      this._actionLogSummary = data;
      // Re-render stats if jobs already loaded
      const running = this._jobs.filter(j => j.is_running || j.status === 'running').length;
      this._renderStats(this._jobs.length, running);
    } catch(e) { /* silent - endpoint may not exist yet */ }
  },

  // ── Auto-refresh Toggle ──
  toggleAutoRefresh() {
    const cb = document.getElementById('warmup-log-autorefresh');
    if (cb && cb.checked) {
      this._refreshTimer = setInterval(() => {
        this.loadActionLogs();
        this.loadProgress();
        this.loadActionLogSummary();
      }, 30000);
    } else {
      if (this._refreshTimer) {
        clearInterval(this._refreshTimer);
        this._refreshTimer = null;
      }
    }
  },

  _esc(s) { return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
};
window.Warmup = Warmup;
window.Reactions = Reactions;
window.App = App;


// ══════════════════════════════════════════════════════════
//  TEAM ACCOUNTS (admin only)
// ══════════════════════════════════════════════════════════
App.loadTeamUsers = async function() {
  const card = document.getElementById('team-users-card');
  const box = document.getElementById('team-users-list');
  if (!card || !box) return;
  let data;
  try { data = await API.get('/api/team-users'); }
  catch (e) { card.style.display = 'none'; return; }  // không phải admin
  card.style.display = '';
  const fmt = t => t ? new Date(t * 1000).toLocaleString('vi-VN') : '—';
  const users = data.users || [];
  box.innerHTML = users.length ? `<table class="data-table" style="width:100%"><thead><tr><th>Username</th><th>Tạo lúc</th><th>Đăng nhập gần nhất</th><th></th></tr></thead><tbody>` +
    users.map(u => `<tr><td><b>${App._esc(u.username)}</b></td><td>${fmt(u.created_at)}</td><td>${fmt(u.last_login)}</td>` +
      `<td style="text-align:right"><button class="btn btn-danger btn-sm" data-u="${App._esc(u.username)}" onclick="App.deleteTeamUser(this.dataset.u)">🗑 Xoá</button></td></tr>`).join('') +
    `</tbody></table>` : '<p style="font-size:13px;color:var(--text2)">Chưa có tài khoản team nào.</p>';
};

App.saveTeamUser = async function() {
  const u = document.getElementById('team-user-name');
  const p = document.getElementById('team-user-pass');
  if (!u.value.trim() || !p.value) return this.toast('Nhập username và mật khẩu', 'error');
  try {
    await API.post('/api/team-users', { username: u.value.trim(), password: p.value });
    this.toast('Đã lưu tài khoản ' + u.value.trim(), 'success');
    u.value = ''; p.value = '';
    this.loadTeamUsers();
  } catch (e) { this.toast(e.message, 'error'); }
};

App.deleteTeamUser = async function(username) {
  if (!confirm('Xoá tài khoản team "' + username + '"? Người này sẽ bị đăng xuất ngay.')) return;
  try {
    await API.del('/api/team-users/' + encodeURIComponent(username));
    this.toast('Đã xoá ' + username, 'success');
    this.loadTeamUsers();
  } catch (e) { this.toast(e.message, 'error'); }
};


// ── Bulk active hours (giờ chạy AI) ─────────────────────────
App.openBulkActiveHours = function(ids) {
  const all = App._accounts || [];
  const targets = ids && ids.length ? all.filter(a => ids.includes(a.id)) : all;
  if (!targets.length) return App.toast('Không có tài khoản nào', 'info');
  document.getElementById('bulk-ah-modal')?.remove();
  const ov = document.createElement('div');
  ov.id = 'bulk-ah-modal';
  ov.className = 'modal-overlay open';
  ov.innerHTML = `<div class="modal" style="max-width:420px">
    <div class="modal-header"><h3 class="modal-title">🕘 Đặt giờ chạy AI</h3><button class="modal-close" onclick="document.getElementById('bulk-ah-modal').remove()">×</button></div>
    <p style="font-size:13px;color:var(--text2);margin-bottom:14px">Áp dụng cho <b>${targets.length}</b> tài khoản${ids && ids.length ? ' đã chọn' : ' (tất cả)'} · giờ Việt Nam</p>
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:18px">
      <input type="time" class="form-input" id="bulk-ah-start" value="08:00" style="flex:1">
      <span>→</span>
      <input type="time" class="form-input" id="bulk-ah-end" value="22:00" style="flex:1">
    </div>
    <button class="btn btn-primary" style="width:100%" id="bulk-ah-apply">Áp dụng</button>
  </div>`;
  ov.addEventListener('click', e => { if (e.target === ov) ov.remove(); });
  document.body.appendChild(ov);
  document.getElementById('bulk-ah-apply').onclick = () => App.applyBulkActiveHours(targets.map(a => a.id));
};

App.applyBulkActiveHours = async function(ids) {
  const start = document.getElementById('bulk-ah-start').value;
  const end = document.getElementById('bulk-ah-end').value;
  if (!start || !end) return App.toast('Chọn đủ giờ bắt đầu và kết thúc', 'error');
  const btn = document.getElementById('bulk-ah-apply');
  btn.disabled = true; btn.textContent = 'Đang lưu...';
  let ok = 0, fail = 0;
  for (const id of ids) {
    try {
      await API.post(`/api/auth/accounts/${id}/active-hours`, { active_hours_start: start, active_hours_end: end, active_timezone: 'Asia/Ho_Chi_Minh' });
      ok++;
    } catch { fail++; }
  }
  document.getElementById('bulk-ah-modal')?.remove();
  API.clearAccountsCache();
  App.toast(`Đã đặt giờ ${start}–${end} cho ${ok}/${ids.length} tài khoản${fail ? ` (${fail} lỗi)` : ''}`, fail ? 'warning' : 'success');
  App.clearAccountSelection();
  App.loadAccounts();
};

document.addEventListener('themechange',()=>{try{if(App._dashDaily)App._renderDashChart(App._dashDaily)}catch(e){}});

// ── Fallback provider chips (order mirrors ai_remix._auto_fallback_remix) ──
App.renderFallbackChips = function() {
  const box = document.getElementById('ai-fallback-chips'); if (!box) return;
  const cur = document.getElementById('ai-provider-select')?.value;
  const names = {chatgpt_oauth:'ChatGPT', gemini:'Gemini', groq:'Groq', openai_compatible:'9Router', openai:'OpenAI', deepseek:'DeepSeek'};
  const list = Object.keys(names).filter(p => p !== cur).map(p => [p, App._collectKeys(p).length]).filter(([, n]) => n);
  box.innerHTML = list.length
    ? list.map(([p, n]) => `<button type="button" class="fb-chip" onclick="App.pickProvider('${p}')">${names[p]} <b>${n}</b></button>`).join('<span class="fb-arrow">→</span>')
    : '<span class="fb-none">Chưa có dự phòng</span>';
};
App.pickProvider = function(p) {
  const sel = document.getElementById('ai-provider-select'); if (!sel) return;
  sel.value = p; App.onProviderChange();
};
(function(){
  const orig = App.onProviderChange;
  App.onProviderChange = function() { orig.apply(this, arguments); App.renderFallbackChips(); };
  document.addEventListener('input', e => { if (e.target.classList?.contains('ai-key-input')) App.renderFallbackChips(); });
})();

// Groq model select: list the chat models this key can use (Groq free tier)
App.loadGroqModels = async function() {
  const el = document.getElementById('groq-model'); if (!el) return;
  const saved = (await API.getSetting('ai_groq_model').catch(() => ({})))?.value || el.value || 'openai/gpt-oss-120b';
  const key = App._collectKeys('groq')[0];
  let ids = [];
  if (key) {
    try {
      const r = await API.post('/api/settings/fetch-models', {base_url: 'https://api.groq.com/openai/v1', api_key: key});
      if (r.success) ids = r.models.map(m => m.id).filter(id => !/whisper|orpheus|guard|tts|playai|allam/i.test(id));
    } catch (e) {}
  }
  if (ids.length) {
    if (!ids.includes(saved)) ids.unshift(saved);
    el.innerHTML = ids.map(id => `<option value="${esc(id)}">${esc(id)}${id === 'openai/gpt-oss-120b' ? ' ⭐' : ''}</option>`).join('');
  }
  el.value = saved;
};
(function(){
  const orig = App.onProviderChange;
  let loaded = false;
  App.onProviderChange = function() {
    orig.apply(this, arguments);
    if (loaded || document.getElementById('ai-provider-select')?.value !== 'groq') return;
    loaded = true; App.loadGroqModels();
  };
})();
