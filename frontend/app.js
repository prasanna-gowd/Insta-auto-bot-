/**
 * app.js - Frontend JavaScript for the Instagram Auto-Reply Bot Dashboard
 *
 * Modules:
 *   API      - Fetch wrapper (get, post, del)
 *   Toast    - Notification toasts (success, error, info)
 *   Auth     - Login / logout / status
 *   Bot      - Start / stop / status
 *   Stats    - Load and render stat cards
 *   Reels    - Load, render grid, open/close modal, save config
 *   Logs     - Load, render table, pagination, clear
 *   Settings - Login form, logout (handlers in inline script)
 */

'use strict';

/* ============================================================
   API – Fetch wrapper
   ============================================================ */
const API = {
  async get(url) {
    const res = await fetch(url);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || res.statusText);
    }
    return res.json();
  },

  async post(url, data = {}) {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || res.statusText);
    }
    return res.json();
  },

  async del(url) {
    const res = await fetch(url, { method: 'DELETE' });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || res.statusText);
    }
    return res.json();
  },
};

/* ============================================================
   Toast – Notification system
   ============================================================ */
const Toast = {
  _show(message, type) {
    const container = document.getElementById('toastContainer');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;

    const icons = { success: '✅', error: '❌', info: 'ℹ️' };
    toast.innerHTML = `
      <span class="toast-icon">${icons[type] || 'ℹ️'}</span>
      <span class="toast-message">${message}</span>
      <button class="toast-close" onclick="this.parentElement.remove()">✕</button>
    `;

    container.appendChild(toast);
    // Trigger animation
    requestAnimationFrame(() => toast.classList.add('toast-visible'));

    // Auto-remove after 4s
    setTimeout(() => {
      toast.classList.remove('toast-visible');
      setTimeout(() => toast.remove(), 300);
    }, 4000);
  },

  success(msg) { this._show(msg, 'success'); },
  error(msg)   { this._show(msg, 'error'); },
  info(msg)    { this._show(msg, 'info'); },
};

/* ============================================================
   Auth – Login / logout / status
   ============================================================ */
const Auth = {
  async check() {
    try {
      return await API.get('/api/auth/status');
    } catch {
      return { logged_in: false, username: null };
    }
  },

  async login(username, password) {
    return await API.post('/api/auth/login', { username, password });
  },

  async logout() {
    return await API.post('/api/auth/logout');
  },
};

/* ============================================================
   Bot – Start / stop / status
   ============================================================ */
const Bot = {
  _running: false,

  async getStatus() {
    try {
      const data = await API.get('/api/bot/status');
      this._running = data.running;
      this._updateUI(data.running);
      return data;
    } catch {
      return { running: false, logged_in: false };
    }
  },

  async start() {
    try {
      const data = await API.post('/api/bot/start');
      this._running = true;
      this._updateUI(true);
      Toast.success('Bot started! Now monitoring comments.');
      return data;
    } catch (e) {
      Toast.error('Failed to start bot: ' + e.message);
    }
  },

  async stop() {
    try {
      const data = await API.post('/api/bot/stop');
      this._running = false;
      this._updateUI(false);
      Toast.info('Bot stopped.');
      return data;
    } catch (e) {
      Toast.error('Failed to stop bot: ' + e.message);
    }
  },

  async toggle() {
    if (this._running) {
      await this.stop();
    } else {
      await this.start();
    }
  },

  _updateUI(running) {
    const dot  = document.getElementById('statusDot');
    const text = document.getElementById('statusText');
    const btn  = document.getElementById('botToggleBtn');

    if (dot) {
      dot.className = 'status-dot ' + (running ? 'dot-green' : 'dot-red');
    }
    if (text) {
      text.textContent = running ? 'Bot Running' : 'Bot Stopped';
    }
    if (btn) {
      btn.textContent = running ? '⏹️ Stop Bot' : '▶️ Start Bot';
      btn.className   = running ? 'btn btn-danger' : 'btn btn-success';
    }
  },
};

/* ============================================================
   Stats – Load and render stat cards
   ============================================================ */
const Stats = {
  async load() {
    try {
      const data = await API.get('/api/stats');
      this.render(data);
    } catch { /* silently fail */ }
  },

  render(data) {
    const set = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.textContent = val ?? '0';
    };
    set('statTotal',        data.total_interactions);
    set('statComments',     data.comment_replies_sent);
    set('statDMs',          data.dms_sent);
    set('statEnabledReels', data.enabled_reels);
  },
};

/* ============================================================
   Reels – Grid, modal, config save
   ============================================================ */
const Reels = {
  _data: [],

  async load() {
    const overlay     = document.getElementById('loadingOverlay');
    const emptyState  = document.getElementById('emptyState');
    const reelsContainer = document.getElementById('reelsContainer');

    if (overlay) overlay.style.display = 'flex';

    try {
      const data = await API.get('/api/reels');
      this._data = data.reels || [];

      if (reelsContainer) {
        reelsContainer.innerHTML = '';
        if (this._data.length === 0) {
          reelsContainer.appendChild(this._buildEmptyState());
        } else {
          const grid = this.renderGrid(this._data);
          reelsContainer.appendChild(grid);
        }
      }

      const countEl = document.getElementById('reelCount');
      if (countEl) countEl.textContent = `${this._data.length} reel${this._data.length !== 1 ? 's' : ''}`;

      // Refresh stats after loading reels
      await Stats.load();

    } catch (e) {
      Toast.error('Failed to fetch reels: ' + e.message);
      if (reelsContainer && this._data.length === 0) {
        reelsContainer.innerHTML = '';
        reelsContainer.appendChild(this._buildEmptyState());
      }
    } finally {
      if (overlay) overlay.style.display = 'none';
    }
  },

  _buildEmptyState() {
    const div = document.createElement('div');
    div.className = 'empty-state';
    div.innerHTML = `
      <div class="empty-icon">🎬</div>
      <h3>No reels found</h3>
      <p>Make sure you are logged in and have posted reels on your account.</p>
      <button class="btn btn-primary" onclick="Reels.load()">🔄 Try Again</button>
    `;
    return div;
  },

  renderGrid(reels) {
    const grid = document.createElement('div');
    grid.className = 'reels-grid';
    reels.forEach(reel => grid.appendChild(this.renderCard(reel)));
    return grid;
  },

  renderCard(reel) {
    const card = document.createElement('div');
    card.className = 'reel-card';
    card.dataset.reelId = reel.reel_id;

    const enabled   = Boolean(reel.is_enabled);
    const hasReply  = Boolean(reel.comment_reply);
    const hasDm     = Boolean(reel.dm_message);
    const caption   = reel.caption || '(No caption)';
    const shortTrunc = caption.length > 80 ? caption.substring(0, 80) + '…' : caption;

    // Thumbnail
    const thumbnailHtml = reel.thumbnail_url
      ? `<img class="reel-thumbnail" src="${this._escapeHtml(reel.thumbnail_url)}" alt="Reel thumbnail" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';" /><div class="reel-thumbnail-placeholder" style="display:none;">🎬</div>`
      : `<div class="reel-thumbnail-placeholder">🎬</div>`;

    card.innerHTML = `
      <div class="reel-thumbnail-wrapper">
        ${thumbnailHtml}
        <div class="reel-status-badge ${enabled ? 'badge-enabled' : 'badge-disabled'}">
          ${enabled ? '✅ Active' : '⏸️ Inactive'}
        </div>
      </div>
      <div class="reel-body">
        <p class="reel-caption" title="${this._escapeHtml(caption)}">${this._escapeHtml(shortTrunc)}</p>
        <div class="reel-meta">
          <span>💬 ${reel.comment_count ?? 0}</span>
          <span>❤️ ${reel.like_count ?? 0}</span>
          <span class="reel-code">${reel.shortcode || ''}</span>
        </div>
        <div class="reel-config-status">
          <span class="config-tag ${hasReply ? 'tag-set' : 'tag-unset'}">
            ${hasReply ? '✅ Reply set' : '⬜ No reply'}
          </span>
          <span class="config-tag ${hasDm ? 'tag-set' : 'tag-unset'}">
            ${hasDm ? '✅ DM set' : '⬜ No DM'}
          </span>
        </div>
        <button class="btn btn-primary btn-full" onclick="Reels.openModal('${reel.reel_id}')">
          ✏️ Edit Messages
        </button>
      </div>
    `;
    return card;
  },

  async openModal(reelId) {
    const stringId = String(reelId);
    let reel = this._data.find(r => String(r.reel_id) === stringId);

    // Fallback if not found in cache
    if (!reel) {
      try {
        reel = await API.get(`/api/reels/${stringId}/config`);
      } catch (e) {
        Toast.error('Reel details not found.');
        return;
      }
    }

    const modalIdEl = document.getElementById('modalReelId');
    const commentEl = document.getElementById('commentReplyInput');
    const dmEl      = document.getElementById('dmMessageInput');
    const toggleEl  = document.getElementById('enabledToggle');

    if (modalIdEl) modalIdEl.value = stringId;
    if (commentEl) commentEl.value = reel.comment_reply || '';
    if (dmEl)      dmEl.value      = reel.dm_message || '';
    if (toggleEl)  toggleEl.checked = Boolean(reel.is_enabled);

    // Preview
    const preview = document.getElementById('modalReelPreview');
    if (preview) {
      const caption = reel.caption || '(No caption)';
      preview.innerHTML = `
        <div class="modal-reel-info">
          ${reel.thumbnail_url
            ? `<img src="${this._escapeHtml(reel.thumbnail_url)}" class="modal-thumbnail" onerror="this.style.display='none'" />`
            : `<div class="modal-thumbnail-placeholder">🎬</div>`}
          <div>
            <div class="modal-reel-code">📎 ${reel.shortcode || stringId}</div>
            <div class="modal-reel-caption">${this._escapeHtml(caption.substring(0, 60))}${caption.length > 60 ? '…' : ''}</div>
          </div>
        </div>
      `;
    }

    const modalOverlay = document.getElementById('modalOverlay');
    if (modalOverlay) {
      modalOverlay.style.display = 'flex';
      modalOverlay.style.opacity = '1';
      modalOverlay.style.pointerEvents = 'all';
      modalOverlay.classList.add('open', 'active');
    }
    if (commentEl) commentEl.focus();
  },

  closeModal(e) {
    if (e && e.target && e.target !== document.getElementById('modalOverlay')) {
      return;
    }
    const modalOverlay = document.getElementById('modalOverlay');
    if (modalOverlay) {
      modalOverlay.style.display = 'none';
      modalOverlay.style.opacity = '0';
      modalOverlay.style.pointerEvents = 'none';
      modalOverlay.classList.remove('open', 'active');
    }
  },

  async saveConfig() {
    const reelId       = document.getElementById('modalReelId')?.value;
    const commentReply = (document.getElementById('commentReplyInput')?.value || '').trim();
    const dmMessage    = (document.getElementById('dmMessageInput')?.value || '').trim();
    const isEnabled    = document.getElementById('enabledToggle')?.checked || false;

    if (!reelId) {
      Toast.error('No reel selected.');
      return;
    }

    const btn = document.getElementById('saveConfigBtn');
    if (btn) {
      btn.disabled = true;
      btn.textContent = '⏳ Saving...';
    }

    try {
      await API.post(`/api/reels/${reelId}/config`, {
        comment_reply: commentReply,
        dm_message:    dmMessage,
        is_enabled:    isEnabled,
      });

      // Update local data
      const stringId = String(reelId);
      const reel = this._data.find(r => String(r.reel_id) === stringId);
      if (reel) {
        reel.comment_reply = commentReply;
        reel.dm_message    = dmMessage;
        reel.is_enabled    = isEnabled ? 1 : 0;
      }

      // Re-render updated card
      const card = document.querySelector(`.reel-card[data-reel-id="${stringId}"]`);
      if (card && reel) {
        const newCard = this.renderCard(reel);
        card.replaceWith(newCard);
      }

      this.closeModal();
      Toast.success('Configuration saved!');
      await Stats.load();

    } catch (e) {
      Toast.error('Save failed: ' + e.message);
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = '💾 Save Configuration';
      }
    }
  },

  _escapeHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  },
};

/* ============================================================
   Logs – Table, pagination, clear
   ============================================================ */
const Logs = {
  _currentPage: 1,
  _perPage: 50,

  async load(page = 1) {
    this._currentPage = page;
    const offset = (page - 1) * this._perPage;

    try {
      const data = await API.get(`/api/logs?limit=${this._perPage}&offset=${offset}`);
      this.render(data.logs || []);
      this.renderPagination(data.total || 0, page);

      const totalEl = document.getElementById('totalLogsCount');
      if (totalEl) totalEl.textContent = data.total ?? 0;

      const lastEl = document.getElementById('lastRefreshed');
      if (lastEl) lastEl.textContent = 'Last updated: ' + new Date().toLocaleTimeString();

    } catch (e) {
      Toast.error('Failed to load logs: ' + e.message);
    }
  },

  render(logs) {
    const tbody = document.getElementById('logsTableBody');
    if (!tbody) return;

    if (logs.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" class="empty-row">📭 No logs yet. The bot will log all replies here.</td></tr>`;
      return;
    }

    const offset = (this._currentPage - 1) * this._perPage;
    tbody.innerHTML = logs.map((log, i) => {
      const reelLabel = log.shortcode
        ? `<a href="https://www.instagram.com/reel/${log.shortcode}/" target="_blank" class="reel-link">/${log.shortcode}</a>`
        : log.reel_id.substring(0, 12) + '…';

      const commentText = log.comment_text
        ? (log.comment_text.length > 50 ? log.comment_text.substring(0, 50) + '…' : log.comment_text)
        : '—';

      const time = log.replied_at
        ? new Date(log.replied_at).toLocaleString()
        : '—';

      return `
        <tr>
          <td>${offset + i + 1}</td>
          <td>${reelLabel}</td>
          <td><span class="commenter-tag">@${log.commenter_username || '?'}</span></td>
          <td class="comment-text-cell" title="${log.comment_text || ''}">${commentText}</td>
          <td class="center-cell">${log.comment_reply_sent ? '✅' : '❌'}</td>
          <td class="center-cell">${log.dm_sent ? '✅' : '❌'}</td>
          <td class="time-cell">${time}</td>
        </tr>
      `;
    }).join('');
  },

  renderPagination(total, currentPage) {
    const container = document.getElementById('paginationContainer');
    if (!container) return;

    const totalPages = Math.ceil(total / this._perPage);
    if (totalPages <= 1) {
      container.innerHTML = '';
      return;
    }

    let html = '';
    if (currentPage > 1) {
      html += `<button class="page-btn" onclick="Logs.load(${currentPage - 1})">← Prev</button>`;
    }

    // Show up to 5 page buttons
    const start = Math.max(1, currentPage - 2);
    const end   = Math.min(totalPages, currentPage + 2);
    for (let p = start; p <= end; p++) {
      html += `<button class="page-btn ${p === currentPage ? 'page-active' : ''}" onclick="Logs.load(${p})">${p}</button>`;
    }

    if (currentPage < totalPages) {
      html += `<button class="page-btn" onclick="Logs.load(${currentPage + 1})">Next →</button>`;
    }

    html += `<span class="page-info">Page ${currentPage} of ${totalPages} (${total} entries)</span>`;
    container.innerHTML = html;
  },

  async clear() {
    try {
      await API.del('/api/logs');
      Toast.success('All logs cleared.');
      await this.load(1);
    } catch (e) {
      Toast.error('Failed to clear logs: ' + e.message);
    } finally {
      const overlay = document.getElementById('confirmOverlay');
      if (overlay) overlay.style.display = 'none';
    }
  },
};

/* ============================================================
   Settings – Login / logout handlers
   ============================================================ */
const Settings = {
  async login() {
    const username = (document.getElementById('igUsername')?.value || '').trim();
    const password = (document.getElementById('igPassword')?.value || '').trim();

    if (!username || !password) {
      Toast.error('Please enter both username and password.');
      return;
    }

    const btn = document.getElementById('loginBtn');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Connecting...'; }

    try {
      const data = await Auth.login(username, password);
      Toast.success(`Connected as @${username}!`);
      // Redirect to dashboard after 1s
      setTimeout(() => { window.location.href = '/'; }, 1200);
    } catch (e) {
      Toast.error('Login failed: ' + e.message);
      if (btn) { btn.disabled = false; btn.textContent = '🔐 Connect Account'; }
    }
  },

  async logout() {
    if (!confirm('Are you sure you want to disconnect your Instagram account?')) return;
    try {
      await Auth.logout();
      Toast.info('Disconnected.');
      setTimeout(() => { window.location.href = '/settings'; }, 1000);
    } catch (e) {
      Toast.error('Logout failed: ' + e.message);
    }
  },

  // Placeholder — extended by inline script in settings.html
  updateAccountUI() {},
  togglePassword() {},
  clearLogs() {},
  stopBot() {},
};
