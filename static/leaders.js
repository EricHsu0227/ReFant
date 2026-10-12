// FanGraphs-style leaderboard: one JSON blob, tabs for stat groups, filters, sorting, paging.
(function () {
  const el = document.getElementById('lbdata');
  if (!el) return;
  const D = JSON.parse(el.textContent);
  const BASE = document.documentElement.dataset.base || '/';
  const root = document.getElementById('lb');
  const st = { group: D.groupOrder[0], sort: D.defaultSort, dir: -1, qual: 'q', team: '', pos: '', name: '', limit: 50, min: '' };
  try { Object.assign(st, JSON.parse(sessionStorage.getItem('lb:' + D.side) || '{}'), { limit: 50 }); } catch (e) {}
  if (!D.groups[st.group]) st.group = D.groupOrder[0];
  const ASC = new Set(D.lowerBetter || []);

  const f3 = (v) => { const s = v.toFixed(3); return s.startsWith('0.') ? s.slice(1) : s.startsWith('-0.') ? '-' + s.slice(2) : s; };
  function fmt(v, code) {
    if (v === null || v === undefined || (typeof v === 'number' && isNaN(v))) return '';
    switch (code) {
      case 'i': return Math.round(v).toString();
      case 'r3': return f3(v);
      case 'p1': return (v * 100).toFixed(1) + '%';
      case 'f0': return v.toFixed(0);
      case 'f1': return v.toFixed(1);
      case 'f2': return v.toFixed(2);
      case 'ip': return Math.floor(v / 3) + '.' + (v % 3);
      case 'wpa': return (v >= 0 ? '+' : '') + v.toFixed(2);
      default: return String(v);
    }
  }
  const save = () => { try { sessionStorage.setItem('lb:' + D.side, JSON.stringify({ group: st.group, sort: st.sort, dir: st.dir, qual: st.qual, team: st.team, pos: st.pos, min: st.min })); } catch (e) {} };

  function rows() {
    let r = D.rows;
    if (st.qual === 'q') r = r.filter((x) => x.q);
    if (st.min !== '' && !isNaN(+st.min)) r = r.filter((x) => (D.side === 'bat' ? x.PA : x.IP / 3) >= +st.min);
    if (st.team) r = r.filter((x) => (x.teams || []).includes(st.team));
    if (st.pos) r = r.filter((x) => (D.side === 'bat' ? x.Pos === st.pos : x.role === st.pos));
    if (st.name) { const q = st.name.toLowerCase(); r = r.filter((x) => x.Name.toLowerCase().includes(q)); }
    const k = st.sort, dir = st.dir;
    return r.slice().sort((a, b) => {
      const x = a[k], y = b[k];
      const xn = x === null || x === undefined || (typeof x === 'number' && isNaN(x));
      const yn = y === null || y === undefined || (typeof y === 'number' && isNaN(y));
      if (xn && !yn) return 1; if (yn && !xn) return -1; if (xn && yn) return 0;
      if (typeof x === 'string') return dir * x.localeCompare(y);
      return dir * (x - y);
    });
  }

  function draw() {
    const cols = D.groups[st.group];
    const list = rows();
    const shown = list.slice(0, st.limit);
    const head = `<tr><th class="rk">#</th><th class="l" data-k="Name">Name</th><th class="l" data-k="Team">Team</th>${D.side === 'bat' ? '<th class="l" data-k="Pos">Pos</th>' : '<th class="l" data-k="role">Role</th>'}` +
      cols.map((k) => `<th data-k="${k}" title="${(D.cols[k] || [])[2] || ''}" class="${st.sort === k ? (st.dir === 1 ? 'asc' : 'desc') : ''}">${(D.cols[k] || [k])[0]}</th>`).join('') + '</tr>';
    const body = shown.map((r, i) => `<tr><td class="rk">${i + 1}</td><td class="l"><a href="${BASE}player/${r.id}/">${r.Name}</a></td>` +
      `<td class="l">${r.teams && r.teams.length === 1 && D.teamPages.includes(r.teams[0]) ? `<a href="${BASE}team/${r.teams[0]}/${D.year}/">${r.Team}</a>` : r.Team}</td>` +
      `<td class="l">${D.side === 'bat' ? (r.Pos || '') : (r.role || '')}</td>` +
      cols.map((k) => `<td data-v="${r[k] ?? ''}">${fmt(r[k], (D.cols[k] || [])[1])}</td>`).join('') + '</tr>').join('');
    root.querySelector('.tbar').innerHTML = D.groupOrder.map((g) => `<button class="${g === st.group ? 'on' : ''}" data-g="${g}">${g}</button>`).join('');
    root.querySelector('thead').innerHTML = head;
    root.querySelector('tbody').innerHTML = body || `<tr><td class="l" colspan="${cols.length + 4}">No players match these filters.</td></tr>`;
    const more = root.querySelector('.more');
    more.hidden = list.length <= st.limit;
    more.textContent = `Show all ${list.length} players`;
    root.querySelector('.count').textContent = `${list.length} player${list.length === 1 ? '' : 's'}`;
    save();
  }

  // controls
  const q = (s) => root.querySelector(s);
  q('[name=qual]').value = st.qual; q('[name=team]').value = st.team; q('[name=pos]').value = st.pos; q('[name=min]').value = st.min;
  q('.tbar').addEventListener('click', (e) => { const b = e.target.closest('button'); if (!b) return; st.group = b.dataset.g; if (!D.groups[st.group].includes(st.sort) && !['Name', 'Team', 'Pos', 'role'].includes(st.sort)) { st.sort = D.groups[st.group].includes(D.defaultSort) ? D.defaultSort : D.groups[st.group].find((k) => k !== 'G' && k !== 'PA' && k !== 'IP') || D.groups[st.group][0]; st.dir = ASC.has(st.sort) ? 1 : -1; } draw(); });
  q('thead').addEventListener('click', (e) => { const th = e.target.closest('th[data-k]'); if (!th) return; const k = th.dataset.k; if (st.sort === k) st.dir = -st.dir; else { st.sort = k; st.dir = ['Name', 'Team', 'Pos', 'role'].includes(k) || ASC.has(k) ? 1 : -1; } draw(); });
  q('[name=qual]').addEventListener('change', (e) => { st.qual = e.target.value; draw(); });
  q('[name=team]').addEventListener('change', (e) => { st.team = e.target.value; draw(); });
  q('[name=pos]').addEventListener('change', (e) => { st.pos = e.target.value; draw(); });
  q('[name=min]').addEventListener('input', (e) => { st.min = e.target.value; draw(); });
  q('[name=name]').addEventListener('input', (e) => { st.name = e.target.value; draw(); });
  q('.more').addEventListener('click', () => { st.limit = 1e9; draw(); });
  draw();
})();
