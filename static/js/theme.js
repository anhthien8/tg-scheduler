/* Theme switcher. Loaded synchronously in <head> so the saved theme is applied
   before first paint. Palettes adapted from Hallmark themes (usehallmark.com);
   fonts swapped for faces that carry Vietnamese glyphs. */
(function () {
  var FONTS = {
    console:  'Geist:wght@400;500;600&family=JetBrains+Mono:wght@400;500',
    dusk:     'Wix+Madefor+Display:wght@500;600&family=Wix+Madefor+Text:wght@400;500;600',
    terminal: 'JetBrains+Mono:wght@400;500;600',
    midnight: 'Mona+Sans:wght@300;400;500;600',
    cobalt:   'Familjen+Grotesk:wght@500;600&family=IBM+Plex+Sans:wght@400;500;600&family=JetBrains+Mono:wght@400;500',
    dose:     'Manrope:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500',
    blofin:   ''
  };
  var THEMES = [
    { id: 'console',  name: 'Console',  note: 'Graphite tối, điểm nhấn hổ phách', dark: true,  sw: ['#1e2126', '#f2b53d', '#e9ecf1'], bar: '#1e2126' },
    { id: 'dusk',     name: 'Dusk',     note: 'Slate tối, tím oải hương và mơ',    dark: true,  sw: ['#1d1f2b', '#a9b1f2', '#f3c48c'], bar: '#1d1f2b' },
    { id: 'midnight', name: 'Midnight', note: 'Xanh đêm, chữ trắng lạnh',          dark: true,  sw: ['#0d1430', '#7ccaf0', '#eef2fb'], bar: '#0d1430' },
    { id: 'terminal', name: 'Terminal', note: 'Đen ánh lục, chữ đơn cách',         dark: true,  sw: ['#0a120c', '#7af27f', '#e3f3df'], bar: '#0a120c' },
    { id: 'cobalt',   name: 'Cobalt',   note: 'Sáng, xanh cobalt, gọn gàng',      dark: false, sw: ['#f9fafc', '#2f5fd8', '#1d2433'], bar: '#f9fafc' },
    { id: 'dose',     name: 'Dose',     note: 'Sáng ấm, xanh lá dịu',              dark: false, sw: ['#fbf8f1', '#1d7550', '#1a2a24'], bar: '#fbf8f1' },
    { id: 'blofin',   name: 'Gốc',      note: 'Giao diện cũ: đen, cam Blofin',     dark: true,  sw: ['#0a0a0a', '#ff8800', '#fcfdff'], bar: '#0a0a0a' }
  ];
  var KEY = 'tgs-theme', DEFAULT = 'console';

  function find(id) { for (var i = 0; i < THEMES.length; i++) if (THEMES[i].id === id) return THEMES[i]; return null; }

  function loadFont(id) {
    var fam = FONTS[id];
    if (!fam) return;
    var lid = 'theme-font-' + id;
    if (document.getElementById(lid)) return;
    var l = document.createElement('link');
    l.id = lid; l.rel = 'stylesheet';
    l.href = 'https://fonts.googleapis.com/css2?family=' + fam + '&display=swap';
    document.head.appendChild(l);
  }

  function apply(id) {
    var t = find(id) || find(DEFAULT);
    var root = document.documentElement;
    root.setAttribute('data-theme', t.id);
    root.style.colorScheme = t.dark ? 'dark' : 'light';
    loadFont(t.id);
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', t.bar);
    return t;
  }

  var saved = null;
  try { saved = localStorage.getItem(KEY); } catch (e) {}
  apply(saved || DEFAULT);

  window.Theme = {
    list: THEMES,
    current: function () { return document.documentElement.getAttribute('data-theme'); },
    set: function (id) {
      var t = apply(id);
      try { localStorage.setItem(KEY, t.id); } catch (e) {}
      document.dispatchEvent(new CustomEvent('themechange', { detail: t }));
      return t;
    },
    // Resolve a CSS variable to a concrete colour (for <canvas> drawing).
    color: function (name) {
      return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    },
    openPicker: function () {
      var old = document.getElementById('theme-picker');
      if (old) { old.remove(); return; }
      var cur = this.current();
      var wrap = document.createElement('div');
      wrap.id = 'theme-picker';
      wrap.className = 'theme-picker';
      wrap.setAttribute('role', 'dialog');
      wrap.setAttribute('aria-label', 'Chọn giao diện');
      var html = '<div class="theme-picker-panel"><div class="theme-picker-head"><strong>Giao diện</strong>' +
        '<button type="button" class="theme-picker-close" aria-label="Đóng">✕</button></div><div class="theme-picker-list">';
      THEMES.forEach(function (t) {
        html += '<button type="button" class="theme-opt' + (t.id === cur ? ' is-on' : '') + '" data-id="' + t.id + '" aria-pressed="' + (t.id === cur) + '">' +
          '<span class="theme-sw">' + t.sw.map(function (c) { return '<i style="background:' + c + '"></i>'; }).join('') + '</span>' +
          '<span class="theme-txt"><b>' + t.name + '</b><small>' + t.note + '</small></span>' +
          '<span class="theme-check" aria-hidden="true">✓</span></button>';
      });
      html += '</div></div>';
      wrap.innerHTML = html;
      var self = this;
      wrap.addEventListener('click', function (e) {
        var opt = e.target.closest('.theme-opt');
        if (opt) {
          self.set(opt.getAttribute('data-id'));
          wrap.querySelectorAll('.theme-opt').forEach(function (b) {
            var on = b === opt; b.classList.toggle('is-on', on); b.setAttribute('aria-pressed', on);
          });
          return;
        }
        if (e.target === wrap || e.target.closest('.theme-picker-close')) wrap.remove();
      });
      document.addEventListener('keydown', function esc(e) {
        if (e.key === 'Escape') { wrap.remove(); document.removeEventListener('keydown', esc); }
      });
      document.body.appendChild(wrap);
      var on = wrap.querySelector('.theme-opt.is-on'); if (on) on.focus();
    }
  };
})();
