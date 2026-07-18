# Plan — per-instance progress plot from `_obj_log.json`

**Purpose:** written self-contained so a *fresh* conversation can read this file
alone and execute it. Port the `*_progress_plot.png` feature from
`hybridflowshop` to this repo: after each instance run, automatically render a
matplotlib step chart showing controller-frame **UB** (obj_value, solid,
best-so-far) and **LB** (obj_bound, dashed) vs elapsed time, saved as a PNG
beside the `_obj_log.json`.

If told "do what this file says", execute.

**Do NOT:** `git add`/commit (keep changes unstaged).
Do not let subagents run git (a stray checkout once deleted work).
Use `uv run python`; after editing code run `uv run ruff check` / `uv run ruff format` if needed.

---

## 0. Locked decisions

- **Separate config gate:** `draw_progress_plot: false` (default OFF) in YAML
  config, independent of `draw_gantt`. Follows the same propagation path as
  `draw_gantt`: `main.py` → `FFcDDWMultiScenarioRunner` → `FFcDDWReporter`.
- **Data source:** `_obj_log.json` (already written unconditionally by
  `_save_obj_log` in `ffcddw_single_instance_runner.py:515-578`). Contains
  `obj_value.data` (UB) and `obj_bound.data` (LB) keyed by `repr(float)`
  controller-frame seconds, plus `obj_value.notes` / `obj_bound.notes` for
  step-boundary labels.
- **Rendering:** matplotlib step chart (`plt.step(..., where="post")`), same
  style as `_render_csr_cp_trajectory_line` (reporting.py:307-373) but reading
  the `_obj_log.json` dict shape instead of parallel arrays.
- **UB best-so-far:** the UB is minimized, so plot only the running minimum —
  drop any point that increases over the current best (`_running_min` helper).
  The LB series is left as-is (its oscillation is kept).
- **Legend labels:** `objValue` (UB) and `objBound` (LB).
- **Annotations:** step-boundary labels from `obj_value.notes` rendered as
  rotated vertical annotations (same pattern as `plot_ub_lb_vs_time.py:230-245`).
- **Output artifact:** `{instance_name}_progress_plot.png` in the `report` zone,
  registered as a new artifact kind `progress_plot_png`.
- **No window-end markers** in the base implementation (the standalone script's
  `load_window_ends` depends on external CSV data that is not available in the
  reporter context; can be added later as a follow-up).

---

## 1. Files to edit

### 1A. `metadata/example_config.yaml` — add default config key

Add `draw_progress_plot: false` next to `draw_gantt`.

### 1B. `main.py` — propagate config value

At line 86 (after `draw_gantt`), add:

```python
draw_progress_plot = bool(config.get("draw_progress_plot", False))
```

At line 128 (inside `FFcDDWMultiScenarioRunner(...)` call), add:

```python
draw_progress_plot=draw_progress_plot,
```

### 1C. `src/ffc_ddw_sum_et/orchestration/reporting.py` — 3 changes

#### i. `FFcDDWMultiScenarioRunner.__init__` (line 413)

Add `draw_progress_plot: bool = False` parameter; store as
`self.draw_progress_plot = draw_progress_plot`.

#### ii. `FFcDDWMultiScenarioRunner.post_run_process` (line 573)

Pass `draw_progress_plot=self.draw_progress_plot` to `FFcDDWReporter(...)`.

#### iii. `FFcDDWReporter.__init__` (line 590)

Add `draw_progress_plot: bool = False` parameter; store as
`self.draw_progress_plot = draw_progress_plot`.

#### iv. `FFcDDWReporter.generate` (line 680)

After `self._generate_gantt_charts()`, add:

```python
self._generate_progress_plots()
```

#### v. New method: `FFcDDWReporter._generate_progress_plots`

Gated by `self.draw_progress_plot`. Iterates `self.scenario_results` →
`sc.instance_results`, reads `obj_log_json` via `self.layout.artifact_path`,
renders via `_render_progress_plot`, writes to `progress_plot_png` artifact.

```python
def _generate_progress_plots(self) -> None:
    if not self.draw_progress_plot:
        logger.info("draw_progress_plot=False; skipping progress plot rendering")
        return
    jobs: list[tuple[Any, Path, Path]] = []
    for sc in self.scenario_results:
        for ir in sc.instance_results:
            ins = ir.instance_name
            scope = {"scenario_name": sc.name, "instance_name": ins}
            obj_log = self.layout.artifact_path("obj_log_json", **scope)
            if obj_log.exists():
                png = self.layout.artifact_path("progress_plot_png", **scope)
                jobs.append((_render_progress_plot, obj_log, png))
    if not jobs:
        return
    worker_cnt = max(1, min(self.painter_thread_cnt, len(jobs)))
    logger.info("Rendering %d progress plots with %d worker(s)", len(jobs), worker_cnt)
    if worker_cnt == 1:
        for fn, src, dst in jobs:
            fn(src, dst)
        return
    with ProcessPoolExecutor(max_workers=worker_cnt) as executor:
        futures = [executor.submit(fn, src, dst) for fn, src, dst in jobs]
        for f in futures:
            f.result()
```

#### vi. Module-level function: `_render_progress_plot`

Reads `_obj_log.json`, plots controller-frame UB/LB step chart. Placed near
`_render_csr_cp_trajectory_line` (around line 374) for co-location.

```python
def _render_progress_plot(json_path: Path, png_path: Path) -> None:
    """Render controller-frame UB/LB-vs-time step chart from ``_obj_log.json``.

    Reads ``{"obj_value":{"data":{...},"notes":{...}},
    "obj_bound":{"data":{...}}}`` and plots two step-style lines: UB
    (``objValue``) from ``obj_value.data`` as best-so-far (solid) and LB
    (``objBound``) from ``obj_bound.data`` (dashed).  Step-boundary annotations
    from ``obj_value.notes`` are rendered as rotated vertical labels.
    Module-level for ``ProcessPoolExecutor`` picklability.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib not available, skipping %s", json_path)
        return

    try:
        with open(json_path) as f:
            log = json.load(f)
    except Exception:
        logger.exception("Failed to load obj_log %s", json_path)
        return

    obj_value = log.get("obj_value", {})
    obj_bound = log.get("obj_bound", {})
    value_data = obj_value.get("data", {})
    bound_data = obj_bound.get("data", {})
    notes = obj_value.get("notes", {})

    def _sorted_pairs(d: dict[str, float]) -> tuple[list[float], list[float]]:
        items = sorted(((float(k), v) for k, v in d.items()), key=lambda kv: kv[0])
        if not items:
            return [], []
        xs, ys = zip(*items)
        return list(xs), list(ys)

    ub_t, ub_y = _sorted_pairs(value_data)
    lb_t, lb_y = _sorted_pairs(bound_data)

    # UB is minimized: keep only best-so-far points, dropping any increase.
    def _running_min(
        ts: list[float], ys: list[float]
    ) -> tuple[list[float], list[float]]:
        out_t: list[float] = []
        out_y: list[float] = []
        best: float | None = None
        for t, y in zip(ts, ys):
            if best is None or y <= best:
                best = y
                out_t.append(t)
                out_y.append(y)
        return out_t, out_y

    ub_t, ub_y = _running_min(ub_t, ub_y)

    if not ub_t and not lb_t:
        return

    note_items = sorted(
        ((float(k), v) for k, v in notes.items()), key=lambda kv: kv[0]
    )

    # Derive label from filename: "MyInstance_progress_plot" -> "MyInstance"
    instance_label = png_path.stem.replace("_progress_plot", "")

    try:
        png_path.parent.mkdir(parents=True, exist_ok=True)
        fig, ax = plt.subplots(figsize=(10, 6))

        if ub_t:
            ax.step(ub_t, ub_y, where="post", label="objValue", linewidth=1.5)
        if lb_t:
            ax.step(lb_t, lb_y, where="post", label="objBound",
                    linestyle="--", linewidth=1.0, alpha=0.7)

        # Step-boundary annotations (rotated vertical labels at top of chart)
        if note_items and ub_t:
            all_t = ub_t + lb_t + [t for t, _ in note_items]
            span = max(all_t) - min(all_t) or 1.0
            pad = span * 0.04
            ax.set_xlim(min(all_t) - pad, max(all_t) + pad)
            ymin, ymax = ax.get_ylim()
            ymax_padded = ymax + (ymax - ymin) * 0.18
            ax.set_ylim(ymin, ymax_padded)
            for t, label in note_items:
                ax.axvline(t, linestyle=":", linewidth=0.8, alpha=0.5)
                ax.annotate(
                    label, xy=(t, ymax_padded),
                    xytext=(-4, -2), textcoords="offset points",
                    rotation=90, va="top", ha="right", fontsize=7,
                    clip_on=False,
                )

        ax.set_title(instance_label)
        ax.set_xlabel("controller time (s)")
        ax.set_ylabel("objective")
        ax.legend(loc="lower right")
        ax.grid(True, linestyle="--", alpha=0.6)
        fig.tight_layout()
        fig.savefig(str(png_path), dpi=150)
        plt.close(fig)
    except Exception:
        logger.exception("Failed to render progress plot for %s", json_path)
```

### 1D. `metadata/artifact_layout/ffc_ddw_sum_et_v1.yaml` — register output kind

Under the `# ---- instance / report` section (after `phase_gantt_png`), add:

```yaml
  # Per-instance progress plot: controller-frame UB/LB vs time, rendered from
  # obj_log_json.  Gated by draw_progress_plot config flag.
  - scope: instance
    zone: report
    kind: progress_plot_png
    file_template: "{instance_name}_progress_plot.png"
```

---

## 2. Verification

### 2A. Lint

```bash
uv run ruff check src/ffc_ddw_sum_et/orchestration/reporting.py
uv run ruff format src/ffc_ddw_sum_et/orchestration/reporting.py
```

### 2B. Existing tests

```bash
uv run pytest tests/ -x --timeout=60
```

No existing test should break (new code is gated behind `draw_progress_plot=False`
by default).

### 2C. Smoke test with existing run (POST_PROCESS_ONLY)

Pick a run dir that has `_obj_log.json` files and regenerate with
`draw_progress_plot: true`:

```bash
# Find a candidate run (obj_log_json is zone=final but zone doesn't appear in path)
find output -name '*_obj_log.json' | head -3

# Test the render function directly:
uv run python -c "
from pathlib import Path
from ffc_ddw_sum_et.orchestration.reporting import _render_progress_plot
import glob
for p in glob.glob('output/*/*/*/*_obj_log.json')[:1]:
    out = Path(p).with_name(Path(p).stem.replace('_obj_log', '_progress_plot') + '.png')
    _render_progress_plot(Path(p), out)
    print('wrote', out)
"
```

Verify the PNG file exists and is non-empty.

---

## 3. Done criteria

1. `draw_progress_plot: false` appears in `example_config.yaml`.
2. `main.py` reads and forwards `draw_progress_plot` to the runner.
3. `FFcDDWMultiScenarioRunner` and `FFcDDWReporter` accept and store
   `draw_progress_plot`.
4. `_generate_progress_plots()` is called from `generate()`, gated by the flag.
5. `_render_progress_plot()` reads `_obj_log.json` and produces a PNG.
6. `progress_plot_png` artifact kind is registered in the layout YAML.
7. `uv run ruff check` passes.
8. Smoke test produces a valid PNG from an existing run's `_obj_log.json`.
9. All changes remain **unstaged** (no `git add`/commit).
