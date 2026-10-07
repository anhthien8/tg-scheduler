// Self-check for static/js/help.js markdown rendering + TOC filtering (no browser).
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const repo = path.join(__dirname, '..');
const tocNode = { innerHTML: '' };
const sandbox = { window: {}, console, document: { getElementById: id => id === 'help-toc-list' ? tocNode : null } };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(repo, 'static/js/help.js'), 'utf8'), sandbox);
const Help = sandbox.window.Help;

Help._toc = JSON.parse(fs.readFileSync(path.join(repo, 'docs/help-toc.json'), 'utf8'));
const html = Help.markdown(fs.readFileSync(path.join(repo, 'docs/help-content.md'), 'utf8'));

function assert(cond, msg) { if (!cond) { throw new Error('FAIL: ' + msg); } }

assert(html.includes('id="1-gioi-thieu"'), 'H2 anchor from toc applied');
assert(html.includes('id="huong-dan-su-dung-tg-scheduler"'), 'H1 anchor applied');
assert(/<pre><code>API_ID=2040/.test(html), 'fenced code block rendered');
assert(html.includes('<div class="help-table-wrap"><table>'), 'markdown table rendered');
assert(html.includes('<li>'), 'lists rendered');
assert(!/<script/i.test(html), 'no raw script tags leaked');

// XSS: raw HTML in markdown must be escaped, not executed.
const evil = Help.markdown('Xin chào <img src=x onerror=alert(1)> **đậm**\n\n<script>alert(2)</script>');
assert(evil.includes('&lt;img src=x onerror=alert(1)&gt;'), 'inline HTML escaped');
assert(evil.includes('&lt;script&gt;'), 'script tag escaped');
assert(evil.includes('<strong>đậm</strong>'), 'bold still rendered');

// TOC filter is a pure data operation; renderToc is a no-op without DOM.
const before = Help._toc.length;
Help.filter('giới thiệu');
assert(Help._toc.length === before, 'filter must not mutate the cached toc');

console.log(JSON.stringify({ html_chars: html.length, toc_entries: before }));
console.log('OK');
