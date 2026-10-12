// ReFant: tabs, sortable tables, player search, phone menu.
(function () {
  const BASE = document.documentElement.dataset.base || '/';

  // ---- tabs: <div data-tabs> <div class="tabbar"><button>..</button></div> <div class="pane">..</div> ...
  document.querySelectorAll('[data-tabs]').forEach((box) => {
    const btns = box.querySelectorAll(':scope > .tabbar > button');
    const panes = box.querySelectorAll(':scope > .pane');
    const key = 'tab:' + (box.dataset.tabs || '');
    const show = (i) => {
      btns.forEach((b, j) => b.classList.toggle('on', i === j));
      panes.forEach((p, j) => { p.hidden = i !== j; });
      try { if (box.dataset.tabs) localStorage.setItem(key, i); } catch (e) {}
    };
    btns.forEach((b, i) => b.addEventListener('click', () => show(i)));
    let start = 0;
    try { const v = +localStorage.getItem(key); if (v >= 0 && v < btns.length) start = v; } catch (e) {}
    show(start);
  });

  // ---- sortable tables (click a header). tfoot / .tot / .grp rows stay put.
  document.addEventListener('click', (e) => {
    const th = e.target.closest('table.st thead th, table.stats thead th:not(.grp)');
    if (!th || th.closest('[data-js-sort]')) return;
    const table = th.closest('table');
    const row = th.parentElement;
    let idx = 0;
    for (const c of row.children) { if (c === th) break; idx += c.colSpan || 1; }
    const tb = table.tBodies[0];
    const rows = Array.from(tb.rows).filter((r) => !r.classList.contains('tot') && !r.classList.contains('grp'));
    if (!rows.length) return;
    const fixed = Array.from(tb.rows).filter((r) => r.classList.contains('tot'));
    const val = (r) => {
      const c = r.cells[idx]; if (!c) return '';
      const t = (c.dataset.v ?? c.textContent).trim().replace('%', '').replace(/^\+/, '');
      if (t === '' || t === '—') return '';
      const n = parseFloat(t.startsWith('.') ? '0' + t : t.startsWith('-.') ? '-0' + t.slice(1) : t);
      return isNaN(n) ? t.toLowerCase() : n;
    };
    const was = th.classList.contains('desc') ? 'desc' : th.classList.contains('asc') ? 'asc' : '';
    row.querySelectorAll('th').forEach((h) => h.classList.remove('asc', 'desc'));
    const sample = rows.map(val).find((v) => v !== '');
    let dir = was === 'desc' ? 1 : was === 'asc' ? -1 : (typeof sample === 'number' ? -1 : 1);
    rows.sort((a, b) => { const x = val(a), y = val(b); if (x === '' && y !== '') return 1; if (y === '' && x !== '') return -1; return x > y ? dir : x < y ? -dir : 0; });
    rows.forEach((r) => tb.appendChild(r));
    fixed.forEach((r) => tb.appendChild(r));
    th.classList.add(dir === 1 ? 'asc' : 'desc');
  });

  // ---- phone menu
  const mb = document.querySelector('.menu-btn');
  if (mb) mb.addEventListener('click', () => { const o = document.body.classList.toggle('nav-open'); mb.setAttribute('aria-expanded', o); });

  // ---- player search
  const inp = document.getElementById('q');
  const box = document.getElementById('sugg');
  let players = null, act = -1;
  const load = () => players ? Promise.resolve(players) :
    fetch(BASE + 'players.json').then((r) => r.json()).then((d) => (players = d)).catch(() => (players = []));
  const norm = (s) => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
  function render(list) {
    act = -1;
    if (!list.length) { box.hidden = true; return; }
    box.innerHTML = list.map((p) => `<a href="${BASE}player/${p.id}/"><span>${p.n}</span><small>${p.t} · ${p.y}</small></a>`).join('');
    box.hidden = false;
  }
  if (inp) {
    inp.addEventListener('focus', load);
    inp.addEventListener('input', () => {
      const q = norm(inp.value.trim());
      if (q.length < 2) { box.hidden = true; return; }
      load().then((ps) => {
        const parts = q.split(/\s+/);
        render(ps.filter((p) => { const n = p.k || (p.k = norm(p.n)); return parts.every((x) => n.includes(x)); }).slice(0, 12));
      });
    });
    inp.addEventListener('keydown', (e) => {
      const items = box.querySelectorAll('a');
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault(); if (!items.length) return;
        act = (act + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
        items.forEach((a, i) => a.classList.toggle('act', i === act));
      } else if (e.key === 'Enter') {
        const a = items[act >= 0 ? act : 0]; if (a) { e.preventDefault(); location.href = a.href; }
      } else if (e.key === 'Escape') { box.hidden = true; }
    });
    document.addEventListener('click', (e) => { if (!e.target.closest('.search')) box.hidden = true; });
  }
})();

if ('serviceWorker' in navigator && document.documentElement.dataset.static === '1') {
  window.addEventListener('load', () => navigator.serviceWorker.register(document.documentElement.dataset.base + 'sw.js').catch(() => {}));
}
