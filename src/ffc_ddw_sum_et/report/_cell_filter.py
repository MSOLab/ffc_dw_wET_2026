from __future__ import annotations

HTML_LABEL_MAP = {
    "t_factor": "T",
    "r_factor": "R",
    "job_cnt": "n",
    "stage_cnt": "c",
}


def cell_filter_toolbar_html(dim_values: dict[str, list[str]]) -> str:
    parts: list[str] = []
    parts.append(
        '<div id="cell-filter-toolbar" style="margin-bottom:12px;display:flex;gap:12px;flex-wrap:wrap;align-items:center;">'
    )
    parts.append('<span style="font-weight:600;font-size:14px;">Filter:</span>')
    for dim in ("t_factor", "r_factor", "job_cnt", "stage_cnt"):
        label = HTML_LABEL_MAP.get(dim, dim)
        vals = dim_values.get(dim, [])
        parts.append(
            f'<select id="filter-{dim}" data-dim="{dim}" style="font-size:13px;padding:2px 6px;">'
        )
        parts.append('<option value="All">All</option>')
        for v in vals:
            parts.append(f'<option value="{v}">{label}={v}</option>')
        parts.append("</select>")
    parts.append("</div>")
    return "\n".join(parts)


CELL_FILTER_JS = r"""
function getSelectedCellKeys() {
  const dims = ["t_factor","r_factor","job_cnt","stage_cnt"];
  const selected = dims.map(d => {
    const el = document.getElementById("filter-"+d);
    return el ? el.value : "All";
  });
  if (selected.every(v => v === "All")) return null;  // All
  return selected.join("|");
}

function buildStepPath(xs, ys) {
  const stepX = [], stepY = [];
  for (let i = 0; i < xs.length; i++) {
    if (i === 0) { stepX.push(xs[i]); stepY.push(ys[i]); continue; }
    const prevY = ys[i - 1];
    stepX.push(xs[i]); stepY.push(prevY);
    if (ys[i] < prevY) { stepX.push(xs[i]); stepY.push(ys[i]); }
  }
  return { x: stepX, y: stepY };
}

// Weighted mean of scalar (x, y) cells — one point per cell. Both coordinates
// are plain per-instance arithmetic means, so the exact combined mean is
// `sum(n*v) / sum(n)` on each axis independently. Do NOT use mergeCells for
// this: its step-function grid starts at `max(x[0])`, which would report the
// slowest cell's mean time instead of the weighted average.
function mergePointCells(cells) {
  if (!cells || cells.length === 0) return null;
  const total = cells.reduce((s, c) => s + c.n, 0);
  if (total === 0) return null;
  let accX = 0, accY = 0;
  cells.forEach(c => { accX += c.n * c.x[0]; accY += c.n * c.y[0]; });
  return { x: [accX / total], y: [accY / total], n: total };
}

// Weighted mean of piecewise-constant step-function cells, sampled at the
// union of their breakpoints from `max(x[0])` onward (each cell's step
// function is undefined before its own start).
function mergeCells(cells) {
  if (!cells || cells.length === 0) return null;
  if (cells.length === 1) return cells[0];
  const start = Math.max(...cells.map(c => c.x[0]));
  const grid = [...new Set(cells.flatMap(c => c.x))].sort((a, b) => a - b).filter(t => t >= start);
  const total = cells.reduce((s, c) => s + c.n, 0);
  const ptr = cells.map(() => 0);
  const y = grid.map(t => {
    let acc = 0;
    cells.forEach((c, i) => {
      while (ptr[i] + 1 < c.x.length && c.x[ptr[i] + 1] <= t) ptr[i] += 1;
      acc += c.n * c.y[ptr[i]];
    });
    return acc / total;
  });
  return { x: grid, y, n: total };
}
"""
