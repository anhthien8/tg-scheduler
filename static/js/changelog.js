/**
 * Changelog & Release Notes Module
 * Displays interactive timeline of system updates, new features, and improvements.
 */
const ChangelogAPI = {
  get: () => apiGet('/api/changelog'),
  getLatest: () => apiGet('/api/changelog/latest'),
};

const Changelog = {
  _data: [],
  _activeFilter: 'all',

  async init() {
    await this.load();
  },

  async load() {
    try {
      const res = await ChangelogAPI.get();
      this._data = res.changelog || [];
      this.render();
    } catch (e) {
      console.error('Changelog load error:', e);
    }
  },

  setFilter(filter) {
    this._activeFilter = filter;
    this.render();
  },

  render() {
    const container = document.getElementById('changelog-content');
    if (!container) return;

    let html = `
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;margin-bottom:24px">
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn btn-sm ${this._activeFilter === 'all' ? 'btn-primary' : 'btn-outline'}" onclick="window.Changelog.setFilter('all')">🌟 Tất cả</button>
          <button class="btn btn-sm ${this._activeFilter === 'feature' ? 'btn-primary' : 'btn-outline'}" onclick="window.Changelog.setFilter('feature')">🚀 Tính năng mới</button>
          <button class="btn btn-sm ${this._activeFilter === 'improvement' ? 'btn-primary' : 'btn-outline'}" onclick="window.Changelog.setFilter('improvement')">⚡ Cải tiến</button>
          <button class="btn btn-sm ${this._activeFilter === 'fix' ? 'btn-primary' : 'btn-outline'}" onclick="window.Changelog.setFilter('fix')">🐞 Sửa lỗi</button>
        </div>
        <div style="font-size:13px;color:var(--text2)">
          <span>Phiên bản hiện tại: <strong style="color:var(--primary)">v2.4.0</strong></span>
        </div>
      </div>

      <div class="changelog-timeline" style="position:relative;padding-left:24px;border-left:2px solid var(--border)">
    `;

    for (const release of this._data) {
      // Filter changes
      const filteredChanges = this._activeFilter === 'all'
        ? release.changes
        : release.changes.filter(c => c.type === this._activeFilter);

      if (filteredChanges.length === 0 && this._activeFilter !== 'all') continue;

      const isLatest = release.is_latest;
      const badgeStyle = isLatest
        ? 'background:linear-gradient(135deg, #8b5cf6, #ec4899);color:#fff;font-weight:700'
        : 'background:var(--bg3);color:var(--text2)';

      html += `
        <div class="changelog-item" style="position:relative;margin-bottom:36px">
          <!-- Timeline Node dot -->
          <div style="position:absolute;left:-31px;top:4px;width:14px;height:14px;border-radius:50%;background:${isLatest ? 'var(--primary)' : 'var(--border)'};border:3px solid var(--bg2);box-shadow:${isLatest ? '0 0 10px var(--primary)' : 'none'}"></div>

          <!-- Header Card -->
          <div class="card" style="padding:20px;border-radius:14px;border:${isLatest ? '1px solid rgba(139,92,246,0.4)' : '1px solid var(--border)'};background:${isLatest ? 'linear-gradient(180deg, rgba(139,92,246,0.06) 0%, var(--bg2) 100%)' : 'var(--bg2)'}">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px;margin-bottom:12px">
              <div>
                <span style="font-size:12px;padding:3px 10px;border-radius:20px;${badgeStyle}">${release.badge}</span>
                <span style="font-weight:700;font-size:16px;margin-left:8px;color:var(--text1)">${release.version}</span>
                <span style="font-size:13px;color:var(--text2);margin-left:8px">• ${release.date}</span>
              </div>
            </div>

            <h3 style="margin:0 0 10px 0;font-size:17px;color:var(--text1)">${this._esc(release.title)}</h3>
            <p style="margin:0 0 16px 0;font-size:14px;color:var(--text2);line-height:1.5">${this._esc(release.summary)}</p>

            <!-- Changes List -->
            <div style="display:flex;flex-direction:column;gap:12px">
      `;

      for (const item of filteredChanges) {
        let typeBadge = '⚡ Cải tiến';
        let typeClass = 'background:rgba(59,130,246,0.15);color:#60a5fa;border:1px solid rgba(59,130,246,0.3)';
        if (item.type === 'feature') {
          typeBadge = '🚀 Feature';
          typeClass = 'background:rgba(139,92,246,0.15);color:#a78bfa;border:1px solid rgba(139,92,246,0.3)';
        } else if (item.type === 'fix') {
          typeBadge = '🐞 Fix';
          typeClass = 'background:rgba(239,68,68,0.15);color:#f87171;border:1px solid rgba(239,68,68,0.3)';
        }

        html += `
          <div style="display:flex;align-items:flex-start;gap:12px;padding:12px;background:var(--bg3);border-radius:10px;border:1px solid rgba(255,255,255,0.03)">
            <span style="font-size:11px;font-weight:600;padding:2px 8px;border-radius:6px;white-space:nowrap;margin-top:2px;${typeClass}">${typeBadge}</span>
            <div style="flex:1">
              <div style="font-weight:600;font-size:14px;color:var(--text1);margin-bottom:2px">${this._esc(item.title)}</div>
              <div style="font-size:13px;color:var(--text2);line-height:1.5">${this._esc(item.desc)}</div>
            </div>
            ${item.tag ? `<span style="font-size:11px;color:var(--text2);background:var(--bg2);padding:2px 8px;border-radius:6px;border:1px solid var(--border)">${item.tag}</span>` : ''}
          </div>
        `;
      }

      html += `
            </div>
          </div>
        </div>
      `;
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

  // ── Quick Modal Preview ────────────────────────────────────────────────
  async showModal() {
    document.getElementById('changelog-modal')?.remove();

    if (this._data.length === 0) {
      await this.load();
    }

    const latest = this._data[0] || {};

    const overlay = document.createElement('div');
    overlay.id = 'changelog-modal';
    overlay.className = 'modal-overlay open';
    overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.7);backdrop-filter:blur(4px);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px';

    let changesHtml = '';
    for (const item of (latest.changes || [])) {
      let icon = '⚡';
      if (item.type === 'feature') icon = '🚀';
      else if (item.type === 'fix') icon = '🐞';

      changesHtml += `
        <div style="margin-bottom:12px;padding:10px;background:var(--bg3);border-radius:8px;border:1px solid var(--border)">
          <div style="font-weight:600;font-size:13px;margin-bottom:2px;color:var(--text1)">${icon} ${this._esc(item.title)}</div>
          <div style="font-size:12px;color:var(--text2);line-height:1.4">${this._esc(item.desc)}</div>
        </div>
      `;
    }

    overlay.innerHTML = `
      <div style="background:var(--bg2);border-radius:16px;padding:24px;max-width:560px;width:100%;max-height:85vh;overflow-y:auto;border:1px solid var(--border);box-shadow:0 20px 40px rgba(0,0,0,0.5)">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid var(--border)">
          <h3 style="margin:0;font-size:18px;display:flex;align-items:center;gap:8px">
            <span>🚀</span> Cập nhật hệ thống: <span style="color:var(--primary)">${latest.version}</span>
          </h3>
          <button onclick="document.getElementById('changelog-modal').remove()" style="background:none;border:none;font-size:22px;cursor:pointer;color:var(--text2)">✕</button>
        </div>

        <div style="margin-bottom:16px">
          <div style="font-weight:600;font-size:15px;color:var(--text1);margin-bottom:4px">${this._esc(latest.title)}</div>
          <div style="font-size:13px;color:var(--text2);margin-bottom:12px">${this._esc(latest.summary)}</div>
          ${changesHtml}
        </div>

        <div style="display:flex;justify-content:space-between;align-items:center">
          <span style="font-size:12px;color:var(--text2)">Ngày cập nhật: ${latest.date}</span>
          <div style="display:flex;gap:8px">
            <button class="btn btn-secondary" onclick="document.getElementById('changelog-modal').remove()">Đóng</button>
            <button class="btn btn-primary" onclick="document.getElementById('changelog-modal').remove(); App.navigate('changelog')">📜 Xem tất cả nhật ký</button>
          </div>
        </div>
      </div>
    `;

    document.body.appendChild(overlay);
  }
};

window.Changelog = Changelog;
