/** User guide page: safe Markdown rendering and client-side TOC search. */
const Help = {
  _toc: [],

  async init() {
    const content = document.getElementById('help-content');
    if (!content) return;
    content.innerHTML = '<p class="help-status">Đang tải hướng dẫn…</p>';
    try {
      const data = await API.get('/api/help');
      this._toc = Array.isArray(data.toc) ? data.toc : [];
      this.renderToc(this._toc);
      content.innerHTML = this.markdown(data.content || '');
    } catch (error) {
      content.innerHTML = `<p class="help-error">Không tải được hướng dẫn: ${this.escape(error.message)}</p>`;
    }
  },

  escape(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  },

  inline(value) {
    let text = this.escape(value);
    text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
    text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    text = text.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    return text;
  },

  markdown(markdown) {
    const lines = String(markdown).replace(/\r\n?/g, '\n').split('\n');
    const headings = new Map(this._toc.map(item => [`${item.level}\u0000${item.title}`, item.anchor]));
    const output = [];
    let paragraph = [];
    let listType = null;
    let inCode = false;
    let code = [];
    let table = [];

    const flushParagraph = () => {
      if (paragraph.length) output.push(`<p>${paragraph.map(line => this.inline(line)).join('<br>')}</p>`);
      paragraph = [];
    };
    const closeList = () => {
      if (listType) output.push(`</${listType}>`);
      listType = null;
    };
    const flushTable = () => {
      if (!table.length) return;
      const rows = table.map(line => line.replace(/^\||\|$/g, '').split('|').map(cell => cell.trim()));
      if (rows.length > 1 && rows[1].every(cell => /^:?-{3,}:?$/.test(cell))) {
        output.push('<div class="help-table-wrap"><table><thead><tr>' + rows[0].map(cell => `<th>${this.inline(cell)}</th>`).join('') + '</tr></thead><tbody>');
        rows.slice(2).forEach(row => output.push('<tr>' + row.map(cell => `<td>${this.inline(cell)}</td>`).join('') + '</tr>'));
        output.push('</tbody></table></div>');
      } else {
        table.forEach(line => output.push(`<p>${this.inline(line)}</p>`));
      }
      table = [];
    };

    for (const line of lines) {
      if (/^```/.test(line)) {
        flushParagraph(); closeList(); flushTable();
        if (inCode) {
          output.push(`<pre><code>${this.escape(code.join('\n'))}</code></pre>`);
          code = [];
        }
        inCode = !inCode;
        continue;
      }
      if (inCode) { code.push(line); continue; }

      if (/^\|.*\|\s*$/.test(line)) {
        flushParagraph(); closeList(); table.push(line); continue;
      }
      flushTable();

      const heading = /^(#{1,6})\s+(.+)$/.exec(line);
      if (heading) {
        flushParagraph(); closeList();
        const level = heading[1].length;
        const title = heading[2].trim();
        const anchor = headings.get(`${level}\u0000${title}`) || '';
        output.push(`<h${level}${anchor ? ` id="${this.escape(anchor)}"` : ''}>${this.inline(title)}</h${level}>`);
        continue;
      }

      const unordered = /^[-*]\s+(.+)$/.exec(line);
      const ordered = /^\d+\.\s+(.+)$/.exec(line);
      if (unordered || ordered) {
        flushParagraph();
        const wanted = ordered ? 'ol' : 'ul';
        if (listType !== wanted) { closeList(); output.push(`<${wanted}>`); listType = wanted; }
        output.push(`<li>${this.inline((unordered || ordered)[1])}</li>`);
        continue;
      }

      if (!line.trim()) { flushParagraph(); closeList(); continue; }
      if (/^---+$/.test(line.trim())) { flushParagraph(); closeList(); output.push('<hr>'); continue; }
      paragraph.push(line.trim());
    }
    flushParagraph(); closeList(); flushTable();
    if (inCode) output.push(`<pre><code>${this.escape(code.join('\n'))}</code></pre>`);
    return output.join('\n');
  },

  renderToc(items) {
    const toc = document.getElementById('help-toc-list');
    if (!toc) return;
    if (!items.length) {
      toc.innerHTML = '<p class="help-status">Không tìm thấy mục phù hợp.</p>';
      return;
    }
    toc.innerHTML = items.map(item =>
      `<a class="help-toc-item level-${Number(item.level) || 1}" href="#${this.escape(item.anchor)}">${this.escape(item.title)}</a>`
    ).join('');
  },

  filter(query) {
    const normalized = String(query || '').trim().toLocaleLowerCase('vi');
    this.renderToc(this._toc.filter(item => String(item.title).toLocaleLowerCase('vi').includes(normalized)));
  }
};

window.Help = Help;
