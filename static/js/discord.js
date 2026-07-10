/**
 * Discord Management Module
 * ─────────────────────────
 * Bot CRUD, connection management, guild browser, watcher setup.
 * Follows the same patterns as app.js for consistency.
 */
const Discord = (() => {

  // ── State ──
  let _bots = [];
  let _watchers = [];
  let _stats = {};

  // ── Init ──
  async function init() {
    await loadStats();
    await loadBots();
    await loadWatchers();
  }

  // ── Stats ──
  async function loadStats() {
    try {
      _stats = await DiscordAPI.getStats();
      renderStats();
    } catch (e) {
      console.warn('Discord stats error:', e);
    }
  }

  function renderStats() {
    const el = document.getElementById('discord-stats');
    if (!el) return;
    el.innerHTML = `
      <div class="stat-card">
        <div class="stat-label">Discord Bots</div>
        <div class="stat-value accent">${_stats.total_bots || 0}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Đang kết nối</div>
        <div class="stat-value" style="color: var(--success)">${_stats.connected_bots || 0}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Watchers</div>
        <div class="stat-value accent">${_stats.total_watchers || 0}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Đang hoạt động</div>
        <div class="stat-value" style="color: var(--success)">${_stats.active_watchers || 0}</div>
      </div>
    `;
  }

  // ── Bot Management ──
  async function loadBots() {
    try {
      _bots = await DiscordAPI.getBots();
      renderBots();
    } catch (e) {
      console.warn('Discord bots error:', e);
    }
  }

  function renderBots() {
    const el = document.getElementById('discord-bot-list');
    if (!el) return;

    if (_bots.length === 0) {
      el.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">🤖</div>
          <h3>Chưa có Discord Bot</h3>
          <p>Thêm bot đầu tiên để bắt đầu</p>
        </div>
      `;
      return;
    }

    el.innerHTML = _bots.map(bot => `
      <div class="card" data-bot-id="${bot.id}">
        <div class="card-header" style="display:flex; justify-content:space-between; align-items:center">
          <div>
            <strong>🤖 ${escHtml(bot.name)}</strong>
            <span class="badge ${bot.is_connected ? 'badge-success' : 'badge-secondary'}" style="margin-left:8px">
              ${bot.is_connected ? '● Online' : '○ Offline'}
            </span>
          </div>
          <div style="display:flex; gap:6px">
            ${bot.is_connected
              ? `<button class="btn btn-sm btn-secondary" onclick="Discord.disconnectBot(${bot.id})">Ngắt kết nối</button>`
              : `<button class="btn btn-sm btn-primary" onclick="Discord.connectBot(${bot.id})">Kết nối</button>`
            }
            <button class="btn btn-sm btn-danger" onclick="Discord.deleteBot(${bot.id})">🗑</button>
          </div>
        </div>
        <div class="card-body" style="font-size: 13px; color: var(--text-secondary)">
          <div>Username: <strong>${escHtml(bot.bot_username || '—')}</strong></div>
          <div>Servers: <strong>${bot.guild_count || 0}</strong></div>
          <div>Token: <code style="font-size:11px">${maskToken(bot.bot_token)}</code></div>
          ${bot.is_connected ? `
            <button class="btn btn-sm btn-secondary" style="margin-top:8px"
              onclick="Discord.showGuilds(${bot.id})">📋 Xem Servers</button>
          ` : ''}
        </div>
      </div>
    `).join('');
  }

  function maskToken(token) {
    if (!token || token.length < 20) return '***';
    return token.substring(0, 8) + '...' + token.substring(token.length - 4);
  }

  async function addBot() {
    const name = prompt('Tên bot (VD: "Main Bot", "Outreach Bot"):');
    if (!name) return;
    const token = prompt('Bot Token (từ Discord Developer Portal):');
    if (!token) return;
    try {
      await DiscordAPI.addBot({ name, bot_token: token });
      showToast('Bot đã được thêm!', 'success');
      await loadBots();
      await loadStats();
    } catch (e) {
      showToast('Lỗi: ' + e.message, 'error');
    }
  }

  async function connectBot(botId) {
    try {
      showToast('Đang kết nối...', 'info');
      const result = await DiscordAPI.connectBot(botId);
      showToast(`Connected: ${result.info?.username || 'OK'}`, 'success');
      await loadBots();
      await loadStats();
    } catch (e) {
      showToast('Kết nối thất bại: ' + e.message, 'error');
    }
  }

  async function disconnectBot(botId) {
    try {
      await DiscordAPI.disconnectBot(botId);
      showToast('Đã ngắt kết nối', 'success');
      await loadBots();
      await loadStats();
    } catch (e) {
      showToast('Lỗi: ' + e.message, 'error');
    }
  }

  async function deleteBot(botId) {
    if (!confirm('Xóa bot này?')) return;
    try {
      await DiscordAPI.deleteBot(botId);
      showToast('Đã xóa bot', 'success');
      await loadBots();
      await loadStats();
    } catch (e) {
      showToast('Lỗi: ' + e.message, 'error');
    }
  }

  async function showGuilds(botId) {
    try {
      const guilds = await DiscordAPI.getBotGuilds(botId);
      if (guilds.length === 0) {
        showToast('Bot chưa join server nào', 'warning');
        return;
      }
      let html = '<div style="max-height:400px; overflow-y:auto">';
      guilds.forEach(g => {
        html += `<div class="card" style="margin-bottom:8px; padding:10px">
          <strong>${escHtml(g.name)}</strong> (${g.member_count || '?'} members)
          <div style="font-size:12px; color:var(--text-secondary); margin-top:4px">
            ${(g.channels || []).map(ch => `#${escHtml(ch.name)} <code style="font-size:10px">${ch.id}</code>`).join(', ')}
          </div>
        </div>`;
      });
      html += '</div>';
      showModal('Servers & Channels', html);
    } catch (e) {
      showToast('Lỗi: ' + e.message, 'error');
    }
  }

  // ── Watchers ──
  async function loadWatchers() {
    try {
      _watchers = await DiscordAPI.getWatchers();
      renderWatchers();
    } catch (e) {
      console.warn('Discord watchers error:', e);
    }
  }

  function renderWatchers() {
    const el = document.getElementById('discord-watcher-list');
    if (!el) return;

    if (_watchers.length === 0) {
      el.innerHTML = `
        <div class="empty-state" style="padding:20px; text-align:center; color:var(--text-secondary)">
          Chưa có Discord Watcher nào
        </div>
      `;
      return;
    }

    el.innerHTML = _watchers.map(w => {
      const keywords = JSON.parse(w.keywords || '[]');
      const channels = JSON.parse(w.group_ids || '[]');
      const bots = JSON.parse(w.sender_account_ids || '[]');
      return `
        <div class="card" style="margin-bottom:8px; padding:12px">
          <div style="display:flex; justify-content:space-between; align-items:center">
            <strong>${escHtml(w.name)}</strong>
            <span class="badge ${w.is_active ? 'badge-success' : 'badge-secondary'}">
              ${w.is_active ? 'Active' : 'Paused'}
            </span>
          </div>
          <div style="font-size:12px; margin-top:6px; color:var(--text-secondary)">
            Keywords: ${keywords.map(k => `<code>${escHtml(k)}</code>`).join(', ') || '—'}<br>
            Channels: ${channels.length} | Bots: ${bots.length} | Cooldown: ${w.cooldown_hours}h
          </div>
        </div>
      `;
    }).join('');
  }

  // ── Helpers ──
  function escHtml(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
  }

  function showToast(msg, type = 'info') {
    if (typeof App !== 'undefined' && App.showToast) {
      App.showToast(msg, type);
    } else {
      console.log(`[${type}] ${msg}`);
    }
  }

  function showModal(title, bodyHtml) {
    // Use existing modal if available, otherwise create a simple one
    let overlay = document.getElementById('discord-modal-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'discord-modal-overlay';
      overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.6);display:flex;align-items:center;justify-content:center;z-index:10000';
      overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
      document.body.appendChild(overlay);
    }
    overlay.innerHTML = `
      <div style="background:var(--surface);border-radius:12px;padding:24px;max-width:600px;width:90%;max-height:80vh;overflow-y:auto">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
          <h3 style="margin:0">${title}</h3>
          <button onclick="document.getElementById('discord-modal-overlay').remove()" 
            style="background:none;border:none;font-size:20px;cursor:pointer;color:var(--text-secondary)">✕</button>
        </div>
        ${bodyHtml}
      </div>
    `;
  }

  // ── Public API ──
  return {
    init,
    loadBots,
    loadWatchers,
    loadStats,
    addBot,
    connectBot,
    disconnectBot,
    deleteBot,
    showGuilds,
  };

})();
