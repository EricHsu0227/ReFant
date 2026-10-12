// Click any header in a table.stats to sort. Totals in <tfoot> stay put.
document.addEventListener("click", (e) => {
  const th = e.target.closest("table.stats thead th:not(.grp)");
  if (!th) return;
  const table = th.closest("table");
  const row = th.parentElement;
  const idx = Array.from(row.children).reduce((acc, cell, i) => {
    if (i < Array.from(row.children).indexOf(th)) acc += cell.colSpan || 1;
    return acc;
  }, 0);
  const tbody = table.tBodies[0];
  const asc = !th.classList.contains("desc") && th.classList.contains("asc") ? false
            : th.classList.contains("desc") ? true : null;
  row.querySelectorAll("th").forEach((h) => h.classList.remove("asc", "desc"));
  const rows = Array.from(tbody.rows);
  const val = (r) => {
    const c = r.cells[idx];
    if (!c) return "";
    const t = (c.dataset.v ?? c.textContent).trim().replace("%", "").replace(/^\+/, "");
    const n = parseFloat(t.startsWith(".") ? "0" + t : t.startsWith("-.") ? "-0" + t.slice(1) : t);
    return isNaN(n) ? t.toLowerCase() : n;
  };
  // first click: numbers descending, text ascending
  const sample = rows.map(val).find((v) => v !== "");
  const dir = asc === null ? (typeof sample === "number" ? -1 : 1) : asc ? 1 : -1;
  rows.sort((a, b) => {
    const x = val(a), y = val(b);
    if (x === "" && y !== "") return 1;
    if (y === "" && x !== "") return -1;
    return x > y ? dir : x < y ? -dir : 0;
  });
  rows.forEach((r) => tbody.appendChild(r));
  th.classList.add(dir === 1 ? "asc" : "desc");
});
