function accDisplayName(a){if(!a)return'?';const ui=a.user_info;if(ui&&(ui.first_name||ui.last_name)){return[ui.first_name,ui.last_name].filter(Boolean).join(' ');}return a.name||'?';}
function customConfirm(msg){return new Promise(r=>{const o=document.getElementById('confirm-modal');document.getElementById('confirm-msg').textContent=msg;o.classList.add('open');document.getElementById('confirm-yes').onclick=()=>{o.classList.remove('open');r(true)};document.getElementById('confirm-no').onclick=()=>{o.classList.remove('open');r(false)}})}

const App={currentPage:'dashboard',chats:[],schedules:[],accounts:[],phoneCodeHash:'',loginAccountId:null,loginPhone:'',logOffset:0,logLimit:30,

async init(){try{const s=await API.authStatus();if(s.authenticated){this.showDashboard(s.user);return}}catch{}

try{const a=await API.getAccounts();App._accounts=a.accounts||[];if(a.accounts&&a.accounts.length>0){this.showDashboard(null);return}}catch{}

this.showLogin()},

toast(m,t='info'){const e=document.createElement('div');e.className=`toast ${t}`;e.textContent=m;document.getElementById('toasts').appendChild(e);setTimeout(()=>e.remove(),4000)},

showLogin(){document.getElementById('login-page').classList.remove('hidden');document.getElementById('dashboard-page').classList.add('hidden')},

showDashboard(user){document.getElementById('login-page').classList.add('hidden');document.getElementById('dashboard-page').classList.remove('hidden');

if(user){const n=[user.first_name,user.last_name].filter(Boolean).join(' ');document.getElementById('user-info').innerHTML=`<strong>${n}</strong><span>@${user.username||user.phone||''}</span>`}

this.navigate('dashboard');document.querySelectorAll('.day-btn').forEach(b=>b.addEventListener('click',()=>b.classList.toggle('active')))},

async addFirstAccount(){const name=document.getElementById('setup-name').value.trim();const apiId=document.getElementById('setup-api-id').value.trim();const apiHash=document.getElementById('setup-api-hash').value.trim();const phone=document.getElementById('setup-phone').value.trim();

if(!name||!apiId||!apiHash||!phone)return this.toast('Điền đầy đủ thông tin','error');

const btn=document.getElementById('btn-add-account');btn.disabled=true;btn.textContent='Đang xử lý...';

try{const r=await API.addAccount({name,phone,api_id:apiId,api_hash:apiHash});this.loginAccountId=r.account_id;this.loginPhone=phone;

const c=await API.sendCode(phone,r.account_id);this.phoneCodeHash=c.phone_code_hash;

document.getElementById('login-step-setup').classList.add('hidden');document.getElementById('login-step-otp').classList.remove('hidden');document.getElementById('login-code').focus();this.toast('Mã OTP đã gửi','success')}catch(e){this.toast(e.message,'error')}

btn.disabled=false;btn.textContent='Thêm tài khoản'},

async verifyFirstAccount(){const code=document.getElementById('login-code').value.trim();if(!code)return this.toast('Nhập mã OTP','error');

try{const r=await API.verify(this.loginPhone,code,this.phoneCodeHash,this.loginAccountId);

if(r.needs_password){document.getElementById('login-step-otp').classList.add('hidden');document.getElementById('login-step-2fa').classList.remove('hidden');return}

this.toast('Đăng nhập thành công!','success');this.showDashboard(r)}catch(e){

if(e.message.includes('2FA')){document.getElementById('login-step-otp').classList.add('hidden');document.getElementById('login-step-2fa').classList.remove('hidden')}else this.toast(e.message,'error')}},

async verify2FAFirst(){const pw=document.getElementById('login-password').value;try{const r=await API.verify(this.loginPhone,document.getElementById('login-code').value.trim(),this.phoneCodeHash,this.loginAccountId,pw);this.toast('Đăng nhập thành công!','success');this.showDashboard(r)}catch(e){this.toast(e.message,'error')}},

navigate(page){this.currentPage=page;if(page==='channels'){this._populateChAccountSelect();}document.querySelectorAll('.nav-item').forEach(el=>el.classList.toggle('active',el.dataset.page===page));document.querySelectorAll('[id^="view-"]').forEach(el=>el.classList.add('hidden'));document.getElementById(`view-${page}`).classList.remove('hidden');

if(page==='dashboard')this.loadDashboard();else if(page==='schedules')this.loadSchedules();else if(page==='accounts')this.loadAccounts();else if(page==='logs')this.loadLogs();else if(page==='watchers')this.loadWatchers();else if(page==='watcher-logs')this.loadWatcherLogs();else if(page==='channels')this.loadChannels();else if(page==='settings')this.loadSettings();else if(page==='reactions')Reactions.init()},

async loadDashboard(){try{const[stats,sd]=await Promise.all([API.getStats(),API.getSchedules()]);

document.getElementById('stat-accounts').textContent=stats.total_accounts;document.getElementById('stat-active').textContent=stats.active_schedules;document.getElementById('stat-total').textContent=stats.total_schedules;document.getElementById('stat-today').textContent=stats.today;document.getElementById('stat-success').textContent=stats.success;document.getElementById('stat-failed').textContent=stats.failed;

const active=sd.schedules.filter(s=>s.is_active&&s.next_run);const tbody=document.getElementById('upcoming-body');

if(!active.length){tbody.innerHTML='<tr><td colspan="6" style="text-align:center;color:var(--text2);padding:24px">Không có lịch nào sắp tới</td></tr>';return}

tbody.innerHTML=active.slice(0,10).map(s=>{const sends=s.max_sends?`${s.current_sends||0}/${s.max_sends}`:(s.current_sends||0);return`<tr><td>${esc(s.name)}</td><td>${esc(s.account_name||'—')}</td><td><span class="badge badge-blue">${s.schedule_type}</span></td><td>${s.time_of_day}</td><td>${formatDate(s.next_run)}</td><td>${sends}</td></tr>`}).join('')}catch(e){this.toast('Lỗi: '+e.message,'error')}},

async loadAccounts(){try{const d=await API.getAccounts();this.accounts=d.accounts;App._accounts=d.accounts;const grid=document.getElementById('accounts-grid');

if(!this.accounts.length){grid.innerHTML='<div class="empty-state"><div class="empty-state-icon">👤</div><p class="empty-state-text">Chưa có tài khoản nào</p></div>';return}

grid.innerHTML=this.accounts.map(a=>{const logged=a.is_logged_in;const ui=a.user_info;const name=ui?[ui.first_name,ui.last_name].filter(Boolean).join(' '):a.name;const uname=ui?`@${ui.username||ui.phone}`:`@${a.phone}`;

return`<div class="account-card"><div class="account-card-header"><div class="account-avatar">${logged?'🟢':'🔴'}</div><div><strong>${esc(name)}</strong><br><small style="color:var(--text2)">${esc(uname)}</small></div></div><div class="account-card-body"><div><small>API ID: ${a.api_id}</small></div><div><small>Session: ${a.session_name}</small></div>${a.proxy_url?`<div style="font-size:.75rem;color:#a78bfa;margin-top:.2rem">🔒 ${esc(a.proxy_url.replace(/:([^:@]+)@/,':***@'))}</div>`:''}<div style="margin-top:6px;display:flex;align-items:center;gap:6px;flex-wrap:wrap"><span class="badge ${logged?'badge-green':'badge-red'}">${logged?'Online':'Offline'}</span><span style="cursor:pointer;background:${a.is_premium?'rgba(251,191,36,.18)':'rgba(255,255,255,.07)'};border:1px solid ${a.is_premium?'rgba(251,191,36,.5)':'rgba(255,255,255,.12)'};border-radius:4px;padding:1px 7px;font-size:.72rem;font-weight:600;color:${a.is_premium?'#fbbf24':'#888'}" onclick="App.togglePremium(${a.id},${a.is_premium?'false':'true'})" title="Click để ${a.is_premium?'bỏ':'bật'} Premium (${a.is_premium?50:10}→${a.is_premium?10:50} DM/ngày)">${a.is_premium?'⭐ Premium':'⬜ Thường'}</span>${a.is_flagged?`<span style="background:rgba(239,68,68,.18);border:1px solid rgba(239,68,68,.5);border-radius:4px;padding:1px 7px;font-size:.72rem;font-weight:600;color:#f87171;cursor:pointer" onclick="App.unflagAccount(${a.id})" title="${esc(a.flag_reason||'')}
Click để bỏ cảnh báo">⚠️ Cảnh báo</span>`:''}</div><div style="margin-top:4px"><small style="color:#6b7280">DM limit: ${a.is_premium?'50':'10'}/ngày</small></div></div><div class="account-card-actions">${!logged?`<button class="btn btn-primary btn-sm" onclick="App.loginAccount(${a.id},'${a.phone}')">Login</button>`:''}<button class="btn btn-danger btn-sm" onclick="App.deleteAccount(${a.id})">Xóa</button></div></div>`}).join('')}catch(e){this.toast(e.message,'error')}},

openAddAccountModal(){document.getElementById('acc-step-info').classList.remove('hidden');document.getElementById('acc-step-otp').classList.add('hidden');document.getElementById('acc-step-2fa').classList.add('hidden');document.getElementById('acc-phone').value='';const proxyEl=document.getElementById('acc-proxy');if(proxyEl)proxyEl.value='';document.getElementById('account-modal').classList.add('open')},

closeAccountModal(){document.getElementById('account-modal').classList.remove('open')},

async unflagAccount(accId){if(!await customConfirm('Bỏ cảnh báo tài khoản này?'))return;try{await fetch(`/api/auth/accounts/${accId}/unflag`,{method:'POST'});this.toast('Đã bỏ cảnh báo','success');this.loadAccounts();}catch(e){this.toast(e.message,'error')}},

async _populateLogAccountFilter(){const sel=document.getElementById('log-filter-account');if(!sel)return;const accs=this.accounts||App._accounts||[];if(!sel.options.length||sel.options.length===1){while(sel.options.length>1)sel.remove(1);accs.forEach(a=>{const opt=document.createElement('option');opt.value=a.id;opt.textContent=a.name||a.phone;sel.appendChild(opt);});} },

async loadBlacklist(){try{const data=await fetch('/api/blacklist').then(r=>r.json());const tbody=document.getElementById('blacklist-body');if(!data.length){tbody.innerHTML='<tr><td colspan="5" style="text-align:center;color:var(--text2);padding:24px">Chưa có user nào trong blacklist</td></tr>';return;}tbody.innerHTML=data.map(b=>`<tr><td>${b.user_id||'—'}</td><td style="color:#a78bfa">${esc(b.username||'—')}</td><td style="color:var(--text2);font-size:.85rem">${esc(b.reason||'—')}</td><td style="font-size:.8rem">${formatDate(b.created_at)}</td><td><button onclick="App.removeBlacklist(${b.id})" style="background:rgba(239,68,68,.15);border:1px solid rgba(239,68,68,.4);color:#f87171;border-radius:6px;padding:3px 10px;cursor:pointer;font-size:.78rem">🗑️ Xóa</button></td></tr>`).join('');}catch(e){this.toast(e.message,'error')}},

showAddBlacklist(){const uid=prompt('Nhập User ID (số):');const uname=prompt('Nhập username (không cần @):');const reason=prompt('Lý do (tuỳ chọn):')||'';if(!uid&&!uname)return;this.addBlacklist(uid?parseInt(uid):null,uname||null,reason)},

async addBlacklist(userId,username,reason){try{await fetch('/api/blacklist',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:userId,username,reason})});this.toast('Đã thêm vào blacklist','success');this.loadBlacklist();}catch(e){this.toast(e.message,'error')}},

async removeBlacklist(id){if(!await customConfirm('Xóa user này khỏi blacklist?'))return;try{await fetch(`/api/blacklist/${id}`,{method:'DELETE'});this.toast('Đã xóa khỏi blacklist','success');this.loadBlacklist();}catch(e){this.toast(e.message,'error')}},

async addAccount(){const phone=document.getElementById('acc-phone').value.trim();if(!phone)return this.toast('Nhập số điện thoại','error');const proxyUrl=document.getElementById('acc-proxy')?.value?.trim()||null;try{const r=await API.addAccount({phone,proxy_url:proxyUrl||null});this.loginAccountId=r.account_id;this.loginPhone=phone;const c=await API.sendCode(phone,r.account_id);this.phoneCodeHash=c.phone_code_hash;document.getElementById('acc-step-info').classList.add('hidden');document.getElementById('acc-step-otp').classList.remove('hidden');this.toast('OTP đã gửi','success')}catch(e){this.toast(e.message||String(e),'error')}},

async verifyAccount(){const code=document.getElementById('acc-code').value.trim();if(!code)return this.toast('Nhập OTP','error');

try{const r=await API.verify(this.loginPhone,code,this.phoneCodeHash,this.loginAccountId);if(r.needs_password){document.getElementById('acc-step-otp').classList.add('hidden');document.getElementById('acc-step-2fa').classList.remove('hidden');return}

this.toast('Đăng nhập thành công!','success');this.closeAccountModal();this.loadAccounts()}catch(e){if(e.message.includes('2FA')){document.getElementById('acc-step-otp').classList.add('hidden');document.getElementById('acc-step-2fa').classList.remove('hidden')}else this.toast(e.message,'error')}},

async verify2FAAccount(){const pw=document.getElementById('acc-password').value;try{await API.verify(this.loginPhone,document.getElementById('acc-code').value.trim(),this.phoneCodeHash,this.loginAccountId,pw);this.toast('OK!','success');this.closeAccountModal();this.loadAccounts()}catch(e){this.toast(e.message,'error')}},

async loginAccount(id,phone){this.loginAccountId=id;this.loginPhone=phone;try{const c=await API.sendCode(phone,id);this.phoneCodeHash=c.phone_code_hash;this.openAddAccountModal();document.getElementById('acc-step-info').classList.add('hidden');document.getElementById('acc-step-otp').classList.remove('hidden');this.toast('OTP đã gửi','success')}catch(e){this.toast(e.message,'error')}},

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
        <button onclick="document.getElementById('blocked-targets-overlay').remove()" 
                style="background:none;border:none;color:var(--text-secondary);font-size:1.5rem;cursor:pointer;line-height:1">×</button>
      </div>
      ${html}
      <div style="text-align:right;margin-top:1rem">
        <button onclick="document.getElementById('blocked-targets-overlay').remove()" class="btn btn-primary">Đóng</button>
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

async openCreateModal(){document.getElementById('modal-title').textContent='Tạo lịch gửi';document.getElementById('edit-schedule-id').value='';document.getElementById('sch-name').value='';document.getElementById('sch-type').value='daily';document.getElementById('sch-time').value='08:00';document.getElementById('sch-day-of-month').value='1';document.getElementById('sch-once-date').value='';document.getElementById('sch-max-sends').value='';document.querySelectorAll('.day-btn').forEach(b=>b.classList.remove('active'));document.getElementById('messages-list').innerHTML='';

this.onScheduleTypeChange();await this.loadAccountSelector();await this.loadChatList();

document.querySelectorAll('#chat-list input[type="checkbox"]').forEach(c=>c.checked=false);document.getElementById('schedule-modal').classList.add('open')},

async editSchedule(id){try{const s=await API.getSchedule(id);document.getElementById('modal-title').textContent='Sửa lịch gửi';document.getElementById('edit-schedule-id').value=s.id;document.getElementById('sch-name').value=s.name;document.getElementById('sch-type').value=s.schedule_type;document.getElementById('sch-time').value=s.time_of_day;document.getElementById('sch-day-of-month').value=s.day_of_month||1;document.getElementById('sch-once-date').value=s.once_date||'';document.getElementById('sch-max-sends').value=s.max_sends||'';

document.querySelectorAll('.day-btn').forEach(b=>b.classList.remove('active'));if(s.days_of_week)s.days_of_week.split(',').forEach(d=>{const btn=document.querySelector(`.day-btn[data-day="${d.trim()}"]`);if(btn)btn.classList.add('active')});

this.onScheduleTypeChange();document.getElementById('messages-list').innerHTML='';(s.messages||[]).forEach(m=>this.addMessage(m.msg_type,m));

await this.loadAccountSelector();document.getElementById('sch-account').value=s.account_id;await this.loadChatList();

const tids=new Set((s.targets||[]).map(t=>t.chat_id));document.querySelectorAll('#chat-list input[type="checkbox"]').forEach(cb=>cb.checked=tids.has(parseInt(cb.value)));

document.getElementById('schedule-modal').classList.add('open')}catch(e){this.toast(e.message,'error')}},

closeModal(){document.getElementById('schedule-modal').classList.remove('open')},

onScheduleTypeChange(){const t=document.getElementById('sch-type').value;document.getElementById('weekly-days-group').classList.toggle('hidden',t!=='weekly');document.getElementById('monthly-day-group').classList.toggle('hidden',t!=='monthly');document.getElementById('once-date-group').classList.toggle('hidden',t!=='once');

const lbl=document.getElementById('time-label');lbl.textContent=t==='hourly'?'Phút gửi (mỗi giờ)':'Giờ gửi'},

async loadAccountSelector(){try{const d=await API.getAccounts();this.accounts=d.accounts;const sel=document.getElementById('sch-account');sel.innerHTML=this.accounts.map(a=>`<option value="${a.id}">${esc(accDisplayName(a))} (${a.phone})</option>`).join('')}catch(e){console.error(e)}},

async loadChatList(){const sel=document.getElementById('sch-account');const accId=sel?sel.value:1;const el=document.getElementById('chat-list');el.innerHTML='<div class="loading-overlay"><span class="spinner"></span> Đang tải...</div>';

try{const d=await API.getChats(accId);this.chats=d.chats;this.renderChatList(this.chats)}catch(e){el.innerHTML=`<div style="padding:12px;color:var(--red)">Lỗi: ${e.message}</div>`}},

renderChatList(chats){const el=document.getElementById('chat-list');if(!chats.length){el.innerHTML='<div style="padding:12px;color:var(--text2)">Không tìm thấy nhóm/kênh</div>';return}

el.innerHTML=chats.map(c=>{const icon=c.chat_type==='channel'?'📢':c.chat_type==='supergroup'?'👥':'💬';return`<label class="chat-item"><input type="checkbox" value="${c.chat_id}" data-title="${esc(c.chat_title)}" data-type="${c.chat_type}"><span class="chat-type-icon">${icon}</span><span class="chat-name">${esc(c.chat_title)}</span><span class="chat-type-badge">${c.chat_type}</span></label>`}).join('')},

filterChats(){const q=document.getElementById('chat-search').value.toLowerCase();this.renderChatList(this.chats.filter(c=>c.chat_title.toLowerCase().includes(q)))},

addMessage(type,data=null){const list=document.getElementById('messages-list');const div=document.createElement('div');div.className='msg-item';div.dataset.type=type;

let inner=`<div class="msg-item-header"><span class="msg-item-type">${type.toUpperCase()}</span><button class="msg-remove" onclick="this.closest('.msg-item').remove()">✕</button></div>`;

if(type==='text')inner+=`<textarea class="form-textarea msg-content" placeholder="Nội dung (hỗ trợ HTML)">${data?.content||''}</textarea>`;

else if(['photo','video','document'].includes(type))inner+=`<div class="form-group" style="margin-bottom:8px"><input type="file" class="form-input msg-file" onchange="App.handleFileUpload(this)"><input type="hidden" class="msg-media-path" value="${data?.media_path||''}">${data?.media_path?`<small style="color:var(--green)">✓ ${data.media_path.split('/').pop()}</small>`:''}</div><textarea class="form-textarea msg-content" placeholder="Caption" rows="2">${data?.content||''}</textarea>`;

else if(type==='poll'){const opts=data?.poll_options?JSON.parse(data.poll_options):['',''];inner+=`<div class="form-group" style="margin-bottom:8px"><input type="text" class="form-input msg-poll-question" placeholder="Câu hỏi" value="${data?.poll_question||''}"></div><div class="poll-options">${opts.map((o,i)=>`<div class="poll-option-row"><input type="text" class="form-input poll-opt" placeholder="Lựa chọn ${i+1}" value="${o}">${i>=2?'<button class="poll-option-remove" onclick="this.parentElement.remove()">✕</button>':''}</div>`).join('')}</div><button class="btn btn-ghost btn-sm" style="margin-top:6px" onclick="App.addPollOption(this)">+ Thêm</button><div style="margin-top:8px"><label style="font-size:12px;color:var(--text2);cursor:pointer"><input type="checkbox" class="msg-poll-multiple" ${data?.poll_multiple?'checked':''}> Cho phép chọn nhiều</label></div>`}

div.innerHTML=inner;list.appendChild(div)},

addPollOption(btn){const c=btn.previousElementSibling;const n=c.children.length;const r=document.createElement('div');r.className='poll-option-row';r.innerHTML=`<input type="text" class="form-input poll-opt" placeholder="Lựa chọn ${n+1}"><button class="poll-option-remove" onclick="this.parentElement.remove()">✕</button>`;c.appendChild(r)},

async handleFileUpload(input){const file=input.files[0];if(!file)return;try{const r=await API.upload(file);input.parentElement.querySelector('.msg-media-path').value=r.path;const ex=input.parentElement.querySelector('small');if(ex)ex.remove();const s=document.createElement('small');s.style.color='var(--green)';s.textContent=`✓ ${r.original_name}`;input.parentElement.appendChild(s);this.toast('Uploaded','success')}catch(e){this.toast('Upload lỗi: '+e.message,'error')}},

async saveSchedule(){const editId=document.getElementById('edit-schedule-id').value;const name=document.getElementById('sch-name').value.trim();const type=document.getElementById('sch-type').value;const time=document.getElementById('sch-time').value;const accountId=parseInt(document.getElementById('sch-account').value);const maxSendsVal=document.getElementById('sch-max-sends').value.trim();const maxSends=maxSendsVal?parseInt(maxSendsVal):null;

if(!name)return this.toast('Nhập tên lịch','error');if(!time)return this.toast('Chọn giờ','error');

let days_of_week=null;if(type==='weekly'){const sel=[...document.querySelectorAll('.day-btn.active')].map(b=>b.dataset.day);if(!sel.length)return this.toast('Chọn ít nhất 1 ngày','error');days_of_week=sel.join(',')}

let day_of_month=null;if(type==='monthly')day_of_month=parseInt(document.getElementById('sch-day-of-month').value)||1;

let once_date=null;if(type==='once'){once_date=document.getElementById('sch-once-date').value;if(!once_date)return this.toast('Chọn ngày','error')}

const targets=[];document.querySelectorAll('#chat-list input[type="checkbox"]:checked').forEach(cb=>targets.push({chat_id:parseInt(cb.value),chat_title:cb.dataset.title,chat_type:cb.dataset.type}));

if(!targets.length)return this.toast('Chọn ít nhất 1 đích','error');

const messages=[];document.querySelectorAll('#messages-list .msg-item').forEach((item,i)=>{const mt=item.dataset.type;const msg={msg_order:i,msg_type:mt};

if(mt==='text'){msg.content=item.querySelector('.msg-content').value;if(!msg.content.trim())return}

else if(['photo','video','document'].includes(mt)){msg.media_path=item.querySelector('.msg-media-path').value;msg.content=item.querySelector('.msg-content').value;if(!msg.media_path)return}

else if(mt==='poll'){msg.poll_question=item.querySelector('.msg-poll-question').value;const opts=[...item.querySelectorAll('.poll-opt')].map(i=>i.value).filter(Boolean);if(opts.length<2||!msg.poll_question)return;msg.poll_options=JSON.stringify(opts);msg.poll_multiple=item.querySelector('.msg-poll-multiple')?.checked||false}

messages.push(msg)});

if(!messages.length)return this.toast('Thêm ít nhất 1 tin nhắn','error');

const payload={account_id:accountId,name,schedule_type:type,time_of_day:time,days_of_week,day_of_month,once_date,max_sends:maxSends,is_active:true,messages,targets};

const btn=document.getElementById('btn-save-schedule');btn.disabled=true;btn.textContent='Đang lưu...';

try{if(editId)await API.updateSchedule(editId,payload);else await API.createSchedule(payload);this.toast(editId?'Đã cập nhật':'Đã tạo mới','success');this.closeModal();this.loadSchedules()}catch(e){this.toast(e.message,'error')}

btn.disabled=false;btn.textContent='Lưu lịch'},

async loadLogs(){const status=document.getElementById('log-filter-status').value;try{const d=await API.getLogs({limit:this.logLimit,offset:this.logOffset,...(status?{status}:{})});const tbody=document.getElementById('logs-body');

if(!d.logs.length){tbody.innerHTML='<tr><td colspan="6" style="text-align:center;color:var(--text2);padding:24px">Chưa có log</td></tr>';return}

tbody.innerHTML=d.logs.map(l=>`<tr><td style="font-size:12px">${formatDate(l.sent_at)}</td><td>${l.schedule_id}</td><td style="color:#a78bfa;font-weight:600">${esc(l.account_name||('Acc '+l.account_id))}</td><td>${esc(l.chat_title||String(l.chat_id))}</td><td><span class="badge ${l.status==='success'?'badge-green':'badge-red'}">${l.status}</span></td><td style="font-size:12px;color:var(--text2);max-width:200px;overflow:hidden;text-overflow:ellipsis">${esc(l.error_message||'—')}</td></tr>`).join('');

const pagEl=document.getElementById('logs-pagination');const pages=Math.ceil(d.total/this.logLimit);const cur=Math.floor(this.logOffset/this.logLimit);

if(pages>1){let h='';if(cur>0)h+=`<button class="btn btn-ghost btn-sm" onclick="App.logPage(${cur-1})">← Trước</button>`;h+=`<span style="color:var(--text2);font-size:12px">Trang ${cur+1}/${pages}</span>`;if(cur<pages-1)h+=`<button class="btn btn-ghost btn-sm" onclick="App.logPage(${cur+1})">Sau →</button>`;pagEl.innerHTML=h}else pagEl.innerHTML=''}catch(e){this.toast('Lỗi: '+e.message,'error')}},

logPage(p){this.logOffset=p*this.logLimit;this.loadLogs()},



// ── Watcher ──────────────────────────────────────────────────────

_watcherKeywords:[],_watcherExcludes:[],_watcherChats:[],_watcherSelectedGroups:new Set(),_watcherAccountOrder:[],_wlOffset:0,_wlLimit:40,

async loadWatchers(){try{

  const[ws,stats]=await Promise.all([API.getWatchers(),API.getWatcherStats()]);

  document.getElementById('ws-active').textContent=stats.active_watchers||0;

  document.getElementById('ws-success').textContent=stats.success||0;

  document.getElementById('ws-today').textContent=stats.today||0;

  document.getElementById('ws-failed').textContent=stats.failed||0;

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

  document.getElementById('watcher-modal-title').textContent='Tạo Keyword DM Rule';

  document.getElementById('edit-watcher-id').value='';

  document.getElementById('w-name').value='';

  document.getElementById('w-cooldown').value='24';

  document.getElementById('w-dm-once').checked=false;
  document.getElementById('w-reply-in-group').checked=false;
  document.getElementById('w-group-reply-text').value='Check my DM 😊';
  document.getElementById('w-group-reply-section').style.display='none';

  document.getElementById('w-messages-list').innerHTML='';

  this._watcherKeywords=[];this._watcherExcludes=[];this._watcherSelectedGroups=new Set();

  this._watcherActiveAccountId=null;

  this._renderWatcherKeywords();this._renderWatcherExcludes();

  await this._loadWatcherAccounts([]);await this._loadWatcherChatList([]);

  document.getElementById('watcher-modal').classList.add('open')},

async editWatcher(id){try{

  const w=await API.getWatcher(id);

  document.getElementById('watcher-modal-title').textContent='Sửa Keyword DM Rule';

  document.getElementById('edit-watcher-id').value=w.id;

  document.getElementById('w-name').value=w.name;

  document.getElementById('w-cooldown').value=w.cooldown_hours||24;

  document.getElementById('w-dm-once').checked=!!w.dm_once;
  document.getElementById('w-reply-in-group').checked=!!w.reply_in_group;
  document.getElementById('w-group-reply-text').value=w.group_reply_text||'Check my DM 😊';
  document.getElementById('w-group-reply-section').style.display=w.reply_in_group?'block':'none';

  this._watcherKeywords=[...w.keywords];this._renderWatcherKeywords();

  this._watcherExcludes=[...(w.excluded_usernames||[])];this._renderWatcherExcludes();

  this._watcherSelectedGroups=new Set(w.group_ids.map(Number));

  document.getElementById('w-messages-list').innerHTML='';

  (w.messages||[]).forEach(m=>this.addWatcherMessage(m.msg_type,m));

  this._watcherActiveAccountId = w.sender_account_ids && w.sender_account_ids.length ? w.sender_account_ids[0] : null;

  await this._loadWatcherAccounts(w.sender_account_ids||[]);await this._loadWatcherChatList(w.group_ids||[]);

  document.getElementById('watcher-modal').classList.add('open')

}catch(e){this.toast(e.message,'error')}},

closeWatcherModal(){document.getElementById('watcher-modal').classList.remove('open')},

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

async _loadWatcherChatList(selectedIds=[]){const el=document.getElementById('w-chat-list');el.innerHTML='<div class="loading-overlay"><span class="spinner"></span> Đang tải...</div>';

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

filterWatcherChats(){const q=document.getElementById('w-chat-search').value.toLowerCase();this._renderWatcherChatList(this._watcherChats.filter(c=>c.chat_title.toLowerCase().includes(q)))},

addWatcherKeyword(){const inp=document.getElementById('w-keyword-input');const v=inp.value.trim();if(!v)return;if(!this._watcherKeywords.includes(v))this._watcherKeywords.push(v);inp.value='';this._renderWatcherKeywords()},

_renderWatcherKeywords(){const c=document.getElementById('w-keywords-tags');c.innerHTML=this._watcherKeywords.map((k,i)=>`<span style="display:inline-flex;align-items:center;gap:4px;background:var(--accent);color:#fff;border-radius:20px;padding:3px 10px;font-size:12px">${esc(k)}<button onclick="App._removeWatcherKeyword(${i})" style="background:none;border:none;color:#fff;cursor:pointer;font-size:14px;line-height:1">×</button></span>`).join('')},

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

  const editId=document.getElementById('edit-watcher-id').value;

  const name=document.getElementById('w-name').value.trim();

  const cooldown=parseInt(document.getElementById('w-cooldown').value)||24;

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

  const dmOnce=document.getElementById('w-dm-once').checked;
  const replyInGroup=document.getElementById('w-reply-in-group').checked;
  const groupReplyText=document.getElementById('w-group-reply-text').value.trim()||'Check my DM 😊';

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
      html += `<div style="color:var(--text-secondary);font-size:.83rem;padding-left:.5rem;">• Chưa join: <span style="color:#f87171">${this._esc(g.group_title)}</span></div>`;
    });
    html += `</div>`;
  });
  html += `<p style="color:var(--text-secondary);font-size:.82rem;margin-top:.8rem;">
    💡 <strong>Cách fix:</strong> Dùng Telegram để join các nhóm trên bằng tài khoản được liệt kê.
  </p></div>`;

  // Show in a modal
  const modal = document.getElementById('membership-warn-modal');
  if(modal) {
    document.getElementById('membership-warn-body').innerHTML = html;
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



    document.getElementById('ai-provider-select').value = provider;

    this._renderAiKeysList('gemini', geminiKeys);

    this._renderAiKeysList('deepseek', deepseekKeys);

    this.onProviderChange();

  }catch(e){this.toast('Lỗi tải cài đặt: ' + e.message, 'error')}

},

onProviderChange(){

  const p = document.getElementById('ai-provider-select').value;

  document.getElementById('ai-gemini-section').classList.toggle('hidden', p !== 'gemini');

  document.getElementById('ai-deepseek-section').classList.toggle('hidden', p !== 'deepseek');

  document.getElementById('test-remix-result').classList.add('hidden');

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

  const provider = document.getElementById('ai-provider-select').value;

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

  const provider = document.getElementById('ai-provider-select').value;

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

  document.getElementById('test-dm-watcher-id').value=watcherId;

  document.getElementById('test-dm-target').value='';

  document.getElementById('test-dm-modal').classList.add('open');

  setTimeout(()=>document.getElementById('test-dm-target').focus(),100)},

async sendTestDM(){

  const wId=document.getElementById('test-dm-watcher-id').value;

  const target=document.getElementById('test-dm-target').value.trim();

  if(!target)return this.toast('Nhập username hoặc User ID','error');

  const btn=document.getElementById('btn-send-test-dm');

  btn.disabled=true;btn.textContent='Đang gửi...';

  try{

    const r=await API.testWatcherDM(wId,target);

    this.toast(r.message||'Đã gửi!','success');

    document.getElementById('test-dm-modal').classList.remove('open');

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
      API.getSetting('ai_keys_groq')
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
    document.getElementById('ai-provider-select').value = provider;
    App.renderAiKeysList('gemini',   geminiKeys);
    App.renderAiKeysList('deepseek', deepseekKeys);
    App.renderAiKeysList('openai',   openaiKeys);
    App.renderAiKeysList('groq',     groqKeys);
    App.onProviderChange();
  } catch(e) {
    App.toast('Loi tai cai dat: ' + e.message, 'error');
  }
};

App.onProviderChange = function() {
  const p = document.getElementById('ai-provider-select').value;
  document.getElementById('ai-gemini-section').classList.toggle('hidden', p !== 'gemini');
  document.getElementById('ai-deepseek-section').classList.toggle('hidden', p !== 'deepseek');
  const openaiEl = document.getElementById('ai-openai-section');
  if (openaiEl) openaiEl.classList.toggle('hidden', p !== 'openai');
  const groqEl = document.getElementById('ai-groq-section');
  if (groqEl) groqEl.classList.toggle('hidden', p !== 'groq');
  document.getElementById('test-remix-result').classList.add('hidden');
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
  const provider = document.getElementById('ai-provider-select').value;
  const geminiKeys   = App.collectAiKeys('gemini');
  const deepseekKeys = App.collectAiKeys('deepseek');
  const openaiKeys   = App.collectAiKeys('openai');
  const groqKeys     = App.collectAiKeys('groq');
  if (provider === 'gemini'   && geminiKeys.length   === 0) { App.toast('Them it nhat 1 Gemini API Key', 'error'); return; }
  if (provider === 'deepseek' && deepseekKeys.length === 0) { App.toast('Them it nhat 1 DeepSeek API Key', 'error'); return; }
  if (provider === 'openai'   && openaiKeys.length   === 0) { App.toast('Them it nhat 1 OpenAI API Key', 'error'); return; }
  if (provider === 'groq'     && groqKeys.length     === 0) { App.toast('Them it nhat 1 Groq API Key', 'error'); return; }
  btn.disabled = true;
  btn.textContent = 'Dang luu...';
  statusEl.textContent = '';
  try {
    await Promise.all([
      API.setSetting('ai_provider',       provider),
      API.setSetting('ai_keys_gemini',   JSON.stringify(geminiKeys)),
      API.setSetting('ai_keys_deepseek', JSON.stringify(deepseekKeys)),
      API.setSetting('ai_keys_openai',   JSON.stringify(openaiKeys)),
      API.setSetting('ai_keys_groq',     JSON.stringify(groqKeys))
    ]);
    App.toast('Da luu cai dat AI!', 'success');
    const cnt = provider === 'gemini' ? geminiKeys.length : (provider === 'deepseek' ? deepseekKeys.length : (provider === 'groq' ? groqKeys.length : openaiKeys.length));
    const labelMap = { gemini: 'Gemini', deepseek: 'DeepSeek', openai: 'OpenAI', groq: 'Groq' };
    statusEl.textContent = provider
      ? ('Dang dung: ' + (labelMap[provider] || provider) + ' (' + cnt + ' key)')
      : 'AI Remix dang tat';
  } catch(e) {
    App.toast('Loi luu: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Luu cai dat';
  }
};

App.testAiRemix = async function() {
  const btn = document.getElementById('btn-test-remix');
  const provider = document.getElementById('ai-provider-select').value;
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
    const resp = await fetch('/api/settings/test-remix', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider: provider, keys: keys, text: sampleText })
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

    try { const d = await fetch('/api/auth/accounts').then(r=>r.json()); App._accounts = d.accounts || []; } catch(e) {}

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



  document.getElementById('ch-loading').classList.remove('hidden');

  document.getElementById('ch-loading').textContent = '\u0110ang t\u1EA3i danh s\u00E1ch k\u00EAnh...';

  document.getElementById('ch-table').classList.add('hidden');

  document.getElementById('ch-empty').classList.add('hidden');

  document.getElementById('ch-status-banner').classList.add('hidden');

  document.getElementById('ch-search').value = '';

  document.getElementById('ch-type-filter').value = 'all';



  try {

    const res = await fetch(`/api/chats?account_id=${accountId}`);

    const data = await res.json();

    App._chChannels = data.chats || [];

    App._filterChannels();

  } catch(e) {

    document.getElementById('ch-loading').textContent = 'L\u1ED7i: ' + e.message;

  }

};



App._filterChannels = function() {

  const typeFilter = document.getElementById('ch-type-filter').value;

  const search = (document.getElementById('ch-search').value || '').toLowerCase().trim();



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



  const typeLabel = {channel: '\uD83D\uDCE2 K\u00EAnh', supergroup: '\uD83D\uDC65 Si\u00EAu nh\u00F3m', group: '\uD83D\uDCAC Nh\u00F3m'};

  const typeColor = {channel: '#f59e0b', supergroup: '#6366f1', group: '#22c55e'};



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

      <td style="padding:.6rem .75rem;color:var(--text-secondary);font-size:.82rem;">${ch.participants_count != null ? ch.participants_count.toLocaleString() : '\u2014'}</td>

      <td style="padding:.6rem .75rem;color:var(--accent);font-size:.82rem;">${ch.username ? '@' + ch.username : '\u2014'}</td>

      <td style="padding:.6rem .75rem;text-align:right;">

        <button class="btn btn-sm btn-danger" onclick="App.leaveOne(${ch.chat_id}, '${esc(ch.chat_title).replace(/'/g, "\\'")}')"

                id="ch-btn-${ch.chat_id}">R\u1EDDi</button>

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



App.leaveOne = async function(chatId, chatTitle) {

  if (!confirm(`R\u1EDDi kh\u1ECFi "${chatTitle}"?`)) return;

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

      App._showChBanner(`\u2705 \u0110\u00E3 r\u1EDDi "${chatTitle}"`, 'success');

    } else {

      const err = await res.json();

      App._showChBanner(`\u274C L\u1ED7i: ${err.detail || 'Unknown'}`, 'error');

      if (btn) { btn.disabled = false; btn.textContent = 'R\u1EDDi'; }

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

// Patch loadSettings to also load API key UI
const _origLoadSettings = App.loadSettings;
App.loadSettings = async function() {
  if (_origLoadSettings) await _origLoadSettings.call(this);
  App.loadApiKeyUi();
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
      const data = await apiGet('/api/auth/accounts');
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
    const link = document.getElementById('rt-link').value.trim();
    const delayMin = parseInt(document.getElementById('rt-delay-min').value) || 5;
    const delayMax = parseInt(document.getElementById('rt-delay-max').value) || 30;
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
        document.getElementById('rt-link').value = '';
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
