"""SW-CP TL-policy analysis: per-window UB(t) segmentation, time-to-p%,
regressions, and an equal-budget constant-vs-proportional comparison.

See `scripts/20260706/ANALYSIS_DESIGN.md` for the full
design spec (this is the authoritative source; read it before touching this
file). Summary of what this script does, per instance-run
(`<run_dir>/<scenario>/<instance>/`):

1. Reads `<instance>_obj_log.json` **raw** (`json.load`, not
   `src/.../report/obj_log_loader.py` -- that structured loader drops
   per-point series without notes; here we only need the raw UB map).
2. Reads `progress/<N>-sw_cp_step_log.yaml` (one row per SW-CP window) and
   re-bases its cumulative algorithm-frame `elapsed_time` onto the
   controller clock using the *same anchor* as
   `plot_ub_lb_vs_time.load_window_ends`: `offset = note_t -
   rows[-1].elapsed_time`, where `note_t` is the `obj_value.notes` timestamp
   labelled `f"{N}-sw_cp"`. This module reuses that plotter's
   `STEP_LOG_RE` filename regex directly instead of re-deriving it.
3. For each window, slices the obj_log UB points into a within-window curve
   `(tau, ub)`, prepends `(0, incumbent_obj_before)`.

   Deliberate extension beyond the literal spec text: the curve also has
   `(wall_seconds, incumbent_obj_after)` appended. This is needed because in
   the real data the CP solver's last accepted value is sometimes logged a
   few hundredths of a second *past* the computed `window_end` (per-step
   bookkeeping overhead between the solve finishing and the next window
   starting); under a strict half-open `[window_start, window_end)` slice
   that point gets attributed to the *next* window, and the current
   window's curve would then never reach its own known final incumbent --
   making the tighter time-to-p% targets (e.g. p=99) spuriously "not
   reached". `incumbent_obj_after` and `wall_seconds` are already trusted
   fields on the step_log row, so anchoring the curve's end there is a safe,
   disclosed way to make time-to-p% well-defined for every I>0 window
   without changing the span/anchor formula itself.
4. Computes time-to-p% (`t_50..t_99`, abs + fraction of `wall_seconds`) and
   `reached_cap` per window; writes `window_metrics.csv`.
5. Fits the Step C regressions (statsmodels OLS if available, else numpy
   `lstsq`); writes `regression_summary.md`.
6. Runs the Step D equal-budget captured-improvement comparison (constant
   vs size-proportional per-window time limit, same total budget per
   instance); writes `captured_comparison.csv`.
7. Renders two PNGs: a t_90-vs-size scatter with fit line, and a
   constant-vs-proportional captured-improvement bar chart per (n, c).

CLI:
    uv run python scripts/20260706/analyze_tl_policy.py \\
        <run_dir> [<run_dir> ...] [--out-dir DIR] [--constant-cap 120]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
import yaml

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

try:
    import statsmodels.api as sm

    HAVE_STATSMODELS = True
except ImportError:  # pragma: no cover - environment dependent
    HAVE_STATSMODELS = False

# Reuse the exact sw_cp/incremental_sw_cp step_log filename regex and anchor
# convention from the existing plotter instead of re-deriving it.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from plot_ub_lb_vs_time import STEP_LOG_RE  # noqa: E402

# Instance_{n}_{c}_{m}_{T}_{R}_{W}_Rep{rep} (T, R use a comma decimal
# separator) -- same regex as scripts/select_representative_instances.py.
INSTANCE_RE = re.compile(
    r"^Instance_(?P<n>\d+)_(?P<c>\d+)_(?P<m>\d+)_"
    r"(?P<T>[\d,]+)_(?P<R>[\d,]+)_(?P<W>\d+)_Rep(?P<rep>\d+)$"
)

P_LEVELS: tuple[int, ...] = (50, 80, 90, 95, 99)
DEFAULT_CONSTANT_CAP = 120.0
DEFAULT_OUT_DIR = Path("analysis/20260705_sw_cp_tl_profile")
REACHED_CAP_FRACTION = 0.98
CELL_FIELDS = ("n", "c", "m", "T", "R", "W", "rep")

CAPTURED_CAVEAT = (
    "CAVEAT: this is an OFFLINE approximation. SW-CP windows are solved "
    "sequentially, so cutting window i short shifts window i+1's starting "
    "incumbent -- a real early-stop would change the whole downstream "
    "trajectory. Per-window captured-improvement sums computed here ignore "
    "that coupling; they replay the *actually observed* within-window curve "
    "under a hypothetical shorter/longer per-window clock, which is only "
    "valid if the rest of the run's trajectory were unaffected. This "
    "comparison is directional evidence only -- the real proof is the "
    "end-to-end A/B run (plan Sec 8 item 5)."
)


def parse_instance_name(name: str) -> dict[str, Any] | None:
    """Return the cell fields for an instance stem, or None if no match."""
    match = INSTANCE_RE.match(name)
    if match is None:
        return None
    g = match.groupdict()
    return {
        "n": int(g["n"]),
        "c": int(g["c"]),
        "m": int(g["m"]),
        "T": float(g["T"].replace(",", ".")),
        "R": float(g["R"].replace(",", ".")),
        "W": int(g["W"]),
        "rep": int(g["rep"]),
    }


def find_instance_dirs(
    run_dirs: list[str],
) -> list[tuple[str, Path, Path, list[Path]]]:
    """Discover `<scenario>/<instance>/` dirs with both an obj_log and at
    least one sw_cp step_log. Returns (scenario, instance_dir, obj_log_path,
    step_log_paths); instances missing either are skipped with a printed
    note."""
    results: list[tuple[str, Path, Path, list[Path]]] = []
    for raw in run_dirs:
        root = Path(raw)
        if not root.is_dir():
            print(f"skip: not a directory: {root}")
            continue
        for scenario_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            scenario = scenario_dir.name
            for inst_dir in sorted(p for p in scenario_dir.iterdir() if p.is_dir()):
                obj_log = inst_dir / f"{inst_dir.name}_obj_log.json"
                progress_dir = inst_dir / "progress"
                step_logs = (
                    sorted(
                        p
                        for p in progress_dir.glob("*_step_log.yaml")
                        if STEP_LOG_RE.match(p.name)
                    )
                    if progress_dir.is_dir()
                    else []
                )
                if not obj_log.is_file() or not step_logs:
                    print(
                        f"skip {scenario}/{inst_dir.name}: "
                        f"{'missing obj_log' if not obj_log.is_file() else 'missing sw_cp step_log'}"
                    )
                    continue
                results.append((scenario, inst_dir, obj_log, step_logs))
    return results


def load_obj_log(
    path: Path,
) -> tuple[list[tuple[float, float]], list[tuple[float, str]]]:
    """Read the raw obj_log JSON and return (sorted UB points, sorted note
    items). Deliberately uses `json.load` directly -- see module docstring."""
    with path.open() as f:
        data = json.load(f)
    ub_data: dict[str, float] = data["obj_value"]["data"]
    ub_points = sorted(((float(t), float(v)) for t, v in ub_data.items()))
    notes: dict[str, str] = data["obj_value"].get("notes", {})
    note_items = sorted(((float(t), label) for t, label in notes.items()))
    return ub_points, note_items


def build_windows(
    step_log_path: Path,
    ub_points: list[tuple[float, float]],
    note_items: list[tuple[float, str]],
) -> list[dict[str, Any]]:
    """Segment one sw_cp step_log file into per-window records with a
    controller-time-anchored UB(tau) curve. Same anchor formula as
    `plot_ub_lb_vs_time.load_window_ends`."""
    m = STEP_LOG_RE.match(step_log_path.name)
    assert m is not None
    step_idx, subroutine = m.group(1), m.group(2)

    note_by_label = {label: t for t, label in note_items}
    note_t = note_by_label.get(f"{step_idx}-{subroutine}")
    if note_t is None:
        for t, label in note_items:
            if label.split("-", 1)[0] == step_idx:
                note_t = t
                break
    if note_t is None:
        print(f"  warn: no matching step-end note for {step_log_path}; skipping")
        return []

    try:
        with step_log_path.open() as f:
            rows = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:
        print(f"  warn: cannot read {step_log_path}: {exc}")
        return []
    if not rows:
        return []

    last_elapsed = rows[-1].get("elapsed_time")
    if last_elapsed is None:
        print(f"  warn: last row of {step_log_path} has no elapsed_time; skipping")
        return []
    offset = note_t - last_elapsed

    windows: list[dict[str, Any]] = []
    prev_elapsed = 0.0
    for row in rows:
        elapsed = row.get("elapsed_time")
        if elapsed is None:
            continue
        window_start = offset + prev_elapsed
        window_end = offset + elapsed
        before = row["incumbent_obj_before"]
        after = row["incumbent_obj_after"]
        wall_seconds = row.get("wall_seconds", window_end - window_start)

        curve: list[tuple[float, float]] = [(0.0, before)]
        for t, ub in ub_points:
            if window_start <= t < window_end:
                curve.append((t - window_start, ub))
        # See module docstring: close the curve at the row's own trusted
        # final incumbent so time-to-p% is always well-defined for I>0.
        curve.append((wall_seconds, after))
        curve.sort(key=lambda p: p[0])

        windows.append(
            {
                "step_idx": step_idx,
                "window_index": row.get("step"),
                "wall_seconds": wall_seconds,
                "TL": row.get("TL"),
                "unfixed_op_count": row.get("unfixed_op_count"),
                "profile_fixed_op_count": row.get("profile_fixed_op_count"),
                "non_time_fixed_op_count": row.get("non_time_fixed_op_count"),
                "sub_job_count": row.get("sub_job_count"),
                "incumbent_obj_before": before,
                "incumbent_obj_after": after,
                "accepted": bool(row.get("accepted")),
                "status": row.get("status"),
                "curve": curve,
            }
        )
        prev_elapsed = elapsed
    return windows


def compute_metrics(window: dict[str, Any]) -> dict[str, Any]:
    """Step B: I, reached_cap, and time-to-p% (abs + fraction) per window."""
    before = window["incumbent_obj_before"]
    after = window["incumbent_obj_after"]
    accepted = window["accepted"]
    obj_improvement = (before - after) if accepted else 0.0
    wall = window["wall_seconds"]
    tl = window["TL"]
    reached_cap = tl is not None and wall >= REACHED_CAP_FRACTION * tl

    metrics: dict[str, Any] = {"I": obj_improvement, "reached_cap": reached_cap}
    for p in P_LEVELS:
        if obj_improvement <= 0:
            metrics[f"t_{p}_abs"] = None
            metrics[f"t_{p}_frac"] = None
            continue
        target = before - (p / 100.0) * obj_improvement
        t_abs = None
        for tau, ub in window["curve"]:
            if ub <= target:
                t_abs = tau
                break
        metrics[f"t_{p}_abs"] = t_abs
        metrics[f"t_{p}_frac"] = (
            (t_abs / wall) if (t_abs is not None and wall) else None
        )
    return metrics


def collect_rows(run_dirs: list[str]) -> list[dict[str, Any]]:
    """Build one row per (scenario, instance, sw_cp window)."""
    rows: list[dict[str, Any]] = []
    for scenario, inst_dir, obj_log_path, step_logs in find_instance_dirs(run_dirs):
        cell = parse_instance_name(inst_dir.name)
        if cell is None:
            print(
                f"skip {scenario}/{inst_dir.name}: name doesn't match Instance_ regex"
            )
            continue
        ub_points, note_items = load_obj_log(obj_log_path)
        for step_log_path in step_logs:
            windows = build_windows(step_log_path, ub_points, note_items)
            for w in windows:
                metrics = compute_metrics(w)
                row = {
                    "scenario": scenario,
                    "instance": inst_dir.name,
                    **cell,
                    "sw_cp_step_idx": w["step_idx"],
                    "window_index": w["window_index"],
                    "wall_seconds": w["wall_seconds"],
                    "TL": w["TL"],
                    "unfixed_op_count": w["unfixed_op_count"],
                    "profile_fixed_op_count": w["profile_fixed_op_count"],
                    "non_time_fixed_op_count": w["non_time_fixed_op_count"],
                    "sub_job_count": w["sub_job_count"],
                    "incumbent_obj_before": w["incumbent_obj_before"],
                    "incumbent_obj_after": w["incumbent_obj_after"],
                    "accepted": w["accepted"],
                    "status": w["status"],
                    **metrics,
                }
                row["_curve"] = w["curve"]  # in-memory only; not written to CSV
                rows.append(row)
    return rows


# --------------------------------------------------------------------------
# CSV output
# --------------------------------------------------------------------------

WINDOW_METRICS_FIELDS = [
    "scenario",
    "instance",
    "n",
    "c",
    "m",
    "T",
    "R",
    "W",
    "rep",
    "sw_cp_step_idx",
    "window_index",
    "unfixed_op_count",
    "profile_fixed_op_count",
    "non_time_fixed_op_count",
    "sub_job_count",
    "wall_seconds",
    "TL",
    "reached_cap",
    "accepted",
    "status",
    "incumbent_obj_before",
    "incumbent_obj_after",
    "I",
    *[f"t_{p}_{suffix}" for p in P_LEVELS for suffix in ("abs", "frac")],
]


def write_window_metrics_csv(rows: list[dict[str, Any]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=WINDOW_METRICS_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in WINDOW_METRICS_FIELDS})


# --------------------------------------------------------------------------
# Step C: regressions
# --------------------------------------------------------------------------


def ols_fit(
    y: np.ndarray, x_cols: dict[str, np.ndarray]
) -> tuple[dict[str, float], float]:
    """Fit `y ~ x_cols` with an intercept. Returns (coef_dict incl. 'const',
    r_squared). Uses statsmodels OLS if available, else numpy lstsq."""
    names = list(x_cols.keys())
    n = len(y)
    design = np.column_stack(
        [np.ones(n)] + [np.asarray(x_cols[k], dtype=float) for k in names]
    )
    y_arr = np.asarray(y, dtype=float)
    if HAVE_STATSMODELS:
        model = sm.OLS(y_arr, design).fit()
        coefs = dict(zip(["const", *names], model.params))
        r2 = float(model.rsquared)
    else:
        beta, *_ = np.linalg.lstsq(design, y_arr, rcond=None)
        coefs = dict(zip(["const", *names], beta))
        y_hat = design @ beta
        ss_res = float(np.sum((y_arr - y_hat) ** 2))
        ss_tot = float(np.sum((y_arr - y_arr.mean()) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return {k: float(v) for k, v in coefs.items()}, r2


def _fmt_coefs(coefs: dict[str, float]) -> str:
    return "\n".join(f"- `{k}` = {v:.6g}" for k, v in coefs.items())


def build_regression_summary(rows: list[dict[str, Any]]) -> str:
    positive_i_rows = [r for r in rows if r["I"] and r["I"] > 0]
    lines: list[str] = []
    lines.append("# SW-CP TL-policy regression summary")
    lines.append("")
    lines.append(f"- total windows: {len(rows)}")
    lines.append(
        f"- windows with I>0 (used for regressions 1-3): {len(positive_i_rows)}"
    )
    engine = (
        "statsmodels OLS"
        if HAVE_STATSMODELS
        else "numpy lstsq (statsmodels not available)"
    )
    lines.append(f"- regression engine: {engine}")
    lines.append("")

    if not positive_i_rows:
        lines.append("No I>0 windows found -- regressions skipped.")
        return "\n".join(lines)

    def col(name: str) -> np.ndarray:
        return np.array([r[name] for r in positive_i_rows], dtype=float)

    # 1. Headline: t_90 ~ non_time_fixed_op_count
    lines.append("## 1. Headline: `t_90 ~ non_time_fixed_op_count`")
    lines.append("")
    y90 = col("t_90_abs")
    coefs, r2 = ols_fit(
        y90, {"non_time_fixed_op_count": col("non_time_fixed_op_count")}
    )
    lines.append(_fmt_coefs(coefs))
    lines.append(f"- R^2 = {r2:.4f}")
    lines.append("")

    # 1b. Stability across p in {80, 90, 95}
    lines.append("## 1b. Stability of the headline slope across p in {80, 90, 95}")
    lines.append("")
    lines.append("| p | slope (non_time_fixed_op_count) | intercept | R^2 |")
    lines.append("|---|---:|---:|---:|")
    for p in (80, 90, 95):
        y = col(f"t_{p}_abs")
        c, r2p = ols_fit(y, {"non_time_fixed_op_count": col("non_time_fixed_op_count")})
        lines.append(
            f"| {p} | {c['non_time_fixed_op_count']:.6g} | {c['const']:.6g} | {r2p:.4f} |"
        )
    lines.append("")

    # 2. Diagnostic: t_90 ~ unfixed_op_count + profile_fixed_op_count
    lines.append("## 2. Diagnostic: `t_90 ~ unfixed_op_count + profile_fixed_op_count`")
    lines.append("")
    coefs2, r2_2 = ols_fit(
        y90,
        {
            "unfixed_op_count": col("unfixed_op_count"),
            "profile_fixed_op_count": col("profile_fixed_op_count"),
        },
    )
    lines.append(_fmt_coefs(coefs2))
    lines.append(f"- R^2 = {r2_2:.4f}")
    diff = coefs2["unfixed_op_count"] - coefs2["profile_fixed_op_count"]
    rel = abs(diff) / max(
        abs(coefs2["unfixed_op_count"]), abs(coefs2["profile_fixed_op_count"]), 1e-12
    )
    verdict = "differ materially" if rel > 0.25 else "are close"
    lines.append(
        f"- coefficients {verdict} (unfixed={coefs2['unfixed_op_count']:.6g}, "
        f"profile_fixed={coefs2['profile_fixed_op_count']:.6g}, "
        f"relative diff={rel:.2%}) -- "
        f"{'splitting k by op type may help' if rel > 0.25 else 'a single combined k (non_time_fixed_op_count) looks adequate'}."
    )
    lines.append("")

    # 3. Difficulty-augmented
    lines.append(
        "## 3. Difficulty-augmented: "
        "`t_90 ~ non_time_fixed_op_count + T + m + window_index`"
    )
    lines.append("")
    coefs3, r2_3 = ols_fit(
        y90,
        {
            "non_time_fixed_op_count": col("non_time_fixed_op_count"),
            "T": col("T"),
            "m": col("m"),
            "window_index": col("window_index"),
        },
    )
    lines.append(_fmt_coefs(coefs3))
    lines.append(f"- R^2 = {r2_3:.4f}")
    lines.append(
        "- signs: "
        + ", ".join(
            f"{k}={'+' if v >= 0 else '-'}{abs(v):.6g}"
            for k, v in coefs3.items()
            if k != "const"
        )
    )
    lines.append("")

    # 4. Regime model
    lines.append("## 4. Regime model: `reached_cap ~ T + m + non_time_fixed_op_count`")
    lines.append("")
    lines.append(
        "Fraction table (mean `reached_cap` by T, m), all windows (incl. I=0):"
    )
    lines.append("")
    lines.append("| T | m | n_windows | frac_reached_cap |")
    lines.append("|---|---|---:|---:|")
    table: dict[tuple[float, int], list[bool]] = defaultdict(list)
    for r in rows:
        table[(r["T"], r["m"])].append(bool(r["reached_cap"]))
    for (t_val, m_val), vals in sorted(table.items()):
        frac = sum(vals) / len(vals)
        lines.append(f"| {t_val} | {m_val} | {len(vals)} | {frac:.3f} |")
    lines.append("")
    lines.append("Linear-probability regression (all windows, incl. I=0):")
    lines.append("")
    y_cap = np.array([1.0 if r["reached_cap"] else 0.0 for r in rows])
    coefs4, r2_4 = ols_fit(
        y_cap,
        {
            "T": np.array([r["T"] for r in rows], dtype=float),
            "m": np.array([r["m"] for r in rows], dtype=float),
            "non_time_fixed_op_count": np.array(
                [r["non_time_fixed_op_count"] for r in rows], dtype=float
            ),
        },
    )
    lines.append(_fmt_coefs(coefs4))
    lines.append(f"- R^2 = {r2_4:.4f}")
    lines.append(
        "- interpretation: a larger |T| or |m| coefficient relative to "
        "non_time_fixed_op_count's supports plan Sec 3.1 obs 1 (difficulty, "
        "not window size, drives whether a window exhausts its time budget)."
    )
    lines.append("")

    lines.append("## Step D caveat")
    lines.append("")
    lines.append(CAPTURED_CAVEAT)
    lines.append("")

    return "\n".join(lines)


# --------------------------------------------------------------------------
# Step D: equal-budget captured-improvement comparison
# --------------------------------------------------------------------------


def captured_at(window_row: dict[str, Any], tau: float) -> float:
    """UB improvement captured by time `tau` within a window, read from its
    Step-A curve.

    Boundary note: CP-SAT logs its final accepted incumbent a few hundredths of
    a second *past* the nominal solve budget (per-step overhead), so
    `wall_seconds` is typically 120.0-120.8 s for a 120 s cap. Crediting full `I`
    only when `tau >= wall_seconds` therefore *under-counts* any policy whose
    budget equals the actual cap (it misses the end-of-window jump) — which
    systematically penalised the constant baseline and inflated the proportional
    policy at `constant_cap == TL`. We instead credit full `I` when the granted
    budget reaches the window's actual solve budget `TL` (the run that produced
    `I` used exactly `TL` seconds of solve): `tau >= TL` ⇒ full `I`. Below `TL`
    we read the trajectory at `tau`."""
    tau = max(0.0, tau)
    wall = window_row["wall_seconds"]
    tl = window_row.get("TL")
    if (tl and tau >= tl) or (wall and tau >= wall):
        return window_row["I"]
    before = window_row["incumbent_obj_before"]
    best_ub = before
    for t, ub in window_row["_curve"]:
        if t <= tau:
            best_ub = min(best_ub, ub)
        else:
            break
    return before - best_ub


CAPTURED_FIELDS = [
    "level",
    "scenario",
    "instance",
    "n",
    "c",
    "m",
    "T",
    "R",
    "W",
    "rep",
    "n_instances",
    "num_windows",
    "budget_B",
    "total_I",
    "captured_constant",
    "captured_proportional",
    "delta_prop_minus_const",
]


def equal_budget_comparison(
    rows: list[dict[str, Any]], constant_cap: float
) -> list[dict[str, Any]]:
    by_instance: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_instance[(r["scenario"], r["instance"])].append(r)

    per_instance: list[dict[str, Any]] = []
    for (scenario, instance), wrows in by_instance.items():
        num_windows = len(wrows)
        budget = constant_cap * num_windows
        sum_ntf = sum(r["non_time_fixed_op_count"] for r in wrows)
        k = budget / sum_ntf if sum_ntf else 0.0

        sum_const = 0.0
        sum_prop = 0.0
        total_i = 0.0
        for r in wrows:
            sum_const += captured_at(r, constant_cap)
            sum_prop += captured_at(r, k * r["non_time_fixed_op_count"])
            total_i += r["I"]

        cell = {f: wrows[0][f] for f in CELL_FIELDS}
        per_instance.append(
            {
                "level": "instance",
                "scenario": scenario,
                "instance": instance,
                **cell,
                "n_instances": 1,
                "num_windows": num_windows,
                "budget_B": budget,
                "total_I": total_i,
                "captured_constant": sum_const,
                "captured_proportional": sum_prop,
                "delta_prop_minus_const": sum_prop - sum_const,
            }
        )
    return per_instance


def aggregate_captured(per_instance: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[int, int, str], dict[str, float]] = defaultdict(
        lambda: {
            "num_windows": 0,
            "budget_B": 0.0,
            "total_I": 0.0,
            "captured_constant": 0.0,
            "captured_proportional": 0.0,
            "n_instances": 0,
        }
    )
    for row in per_instance:
        key = (row["n"], row["c"], row["scenario"])
        g = groups[key]
        g["num_windows"] += row["num_windows"]
        g["budget_B"] += row["budget_B"]
        g["total_I"] += row["total_I"]
        g["captured_constant"] += row["captured_constant"]
        g["captured_proportional"] += row["captured_proportional"]
        g["n_instances"] += 1

    out: list[dict[str, Any]] = []
    for (n_val, c_val, scenario), g in sorted(groups.items()):
        out.append(
            {
                "level": "aggregate",
                "scenario": scenario,
                "instance": "",
                "n": n_val,
                "c": c_val,
                "m": "",
                "T": "",
                "R": "",
                "W": "",
                "rep": "",
                "n_instances": g["n_instances"],
                "num_windows": g["num_windows"],
                "budget_B": g["budget_B"],
                "total_I": g["total_I"],
                "captured_constant": g["captured_constant"],
                "captured_proportional": g["captured_proportional"],
                "delta_prop_minus_const": g["captured_proportional"]
                - g["captured_constant"],
            }
        )
    return out


# Sub-cap budgets sweep the honest regime. At `constant_cap == TL` (=120 here)
# the comparison is DEGENERATE: constant == the actual run, so it captures 100%
# of `I` by construction and any "advantage" is an artifact. Sub-cap budgets
# (well below TL) exercise the real redistribution tradeoff, where the boundary
# note in `captured_at` no longer matters.
DEFAULT_BUDGET_SWEEP = (15.0, 30.0, 60.0, 120.0)


def budget_cap_sweep(
    rows: list[dict[str, Any]], caps: tuple[float, ...] = DEFAULT_BUDGET_SWEEP
) -> list[dict[str, float]]:
    """Aggregate constant-vs-proportional capture % across several equal-budget
    per-window caps. Returns one row per cap with overall capture percentages."""
    table: list[dict[str, float]] = []
    for cap in caps:
        agg = aggregate_captured(equal_budget_comparison(rows, cap))
        total_i = sum(g["total_I"] for g in agg)
        const = sum(g["captured_constant"] for g in agg)
        prop = sum(g["captured_proportional"] for g in agg)
        if not total_i:
            continue
        table.append(
            {
                "cap": cap,
                "constant_pct": 100.0 * const / total_i,
                "proportional_pct": 100.0 * prop / total_i,
                "delta_pp": 100.0 * (prop - const) / total_i,
            }
        )
    return table


def render_budget_sweep_md(table: list[dict[str, float]]) -> str:
    lines = [
        "## Step D — equal-budget capture across per-window caps",
        "",
        "Fraction of total achievable UB improvement captured at equal total",
        "budget, constant (`tau=cap`) vs size-proportional (`tau=k*non_time_fixed`).",
        "`cap == TL` (120 s) is DEGENERATE (constant == the actual run); read the",
        "sub-cap rows for the real signal.",
        "",
        "| per-window cap (s) | constant % | proportional % | delta (pp) |",
        "|---:|---:|---:|---:|",
    ]
    for r in table:
        degen = "  *(degenerate)*" if r["cap"] >= 120.0 else ""
        lines.append(
            f"| {r['cap']:.0f}{degen} | {r['constant_pct']:.1f} | "
            f"{r['proportional_pct']:.1f} | {r['delta_pp']:+.1f} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_captured_csv(
    per_instance: list[dict[str, Any]], aggregate: list[dict[str, Any]], out_path: Path
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CAPTURED_FIELDS)
        writer.writeheader()
        for row in per_instance:
            writer.writerow({k: row.get(k) for k in CAPTURED_FIELDS})
        for row in aggregate:
            writer.writerow({k: row.get(k) for k in CAPTURED_FIELDS})


# --------------------------------------------------------------------------
# Plots
# --------------------------------------------------------------------------

SCENARIO_COLORS = {
    "u2_pf2": "#1f77b4",
    "u4_pf2": "#d62728",
}
FALLBACK_COLOR_CYCLE = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#8c564b"]


def _color_for(scenario: str, seen: dict[str, str]) -> str:
    if scenario in SCENARIO_COLORS:
        return SCENARIO_COLORS[scenario]
    if scenario not in seen:
        seen[scenario] = FALLBACK_COLOR_CYCLE[len(seen) % len(FALLBACK_COLOR_CYCLE)]
    return seen[scenario]


def plot_t90_scatter(rows: list[dict[str, Any]], out_path: Path) -> Path:
    positive_i_rows = [
        r for r in rows if r["I"] and r["I"] > 0 and r["t_90_abs"] is not None
    ]
    fig, ax = plt.subplots(figsize=(8, 6))
    seen_colors: dict[str, str] = {}
    scenarios = sorted({r["scenario"] for r in positive_i_rows})
    for scenario in scenarios:
        sub = [r for r in positive_i_rows if r["scenario"] == scenario]
        xs = [r["non_time_fixed_op_count"] for r in sub]
        ys = [r["t_90_abs"] for r in sub]
        ax.scatter(
            xs,
            ys,
            s=14,
            alpha=0.6,
            color=_color_for(scenario, seen_colors),
            label=scenario,
        )

    if positive_i_rows:
        x_all = np.array(
            [r["non_time_fixed_op_count"] for r in positive_i_rows], dtype=float
        )
        y_all = np.array([r["t_90_abs"] for r in positive_i_rows], dtype=float)
        coefs, r2 = ols_fit(y_all, {"non_time_fixed_op_count": x_all})
        x_line = np.linspace(x_all.min(), x_all.max(), 50)
        y_line = coefs["const"] + coefs["non_time_fixed_op_count"] * x_line
        ax.plot(
            x_line,
            y_line,
            color="black",
            linewidth=1.5,
            linestyle="--",
            label=(
                f"fit: t_90 = {coefs['const']:.1f} + "
                f"{coefs['non_time_fixed_op_count']:.3f}*x  (R^2={r2:.3f})"
            ),
        )

    ax.set_xlabel("non_time_fixed_op_count")
    ax.set_ylabel("t_90 (s, time to 90% of window's UB improvement)")
    ax.set_title("SW-CP window size vs time-to-90%-improvement")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return out_path


def plot_captured_bars(aggregate: list[dict[str, Any]], out_path: Path) -> Path:
    groups = sorted({(r["n"], r["c"]) for r in aggregate})
    scenarios = sorted({r["scenario"] for r in aggregate})

    fig, ax = plt.subplots(figsize=(9, 6))
    bar_width = 0.8 / max(len(scenarios) * 2, 1)
    x_base = np.arange(len(groups))
    seen_colors: dict[str, str] = {}

    for s_idx, scenario in enumerate(scenarios):
        const_pcts = []
        prop_pcts = []
        for n_val, c_val in groups:
            match = next(
                (
                    r
                    for r in aggregate
                    if r["n"] == n_val and r["c"] == c_val and r["scenario"] == scenario
                ),
                None,
            )
            if match is None or not match["total_I"]:
                const_pcts.append(0.0)
                prop_pcts.append(0.0)
                continue
            const_pcts.append(100.0 * match["captured_constant"] / match["total_I"])
            prop_pcts.append(100.0 * match["captured_proportional"] / match["total_I"])

        base_color = _color_for(scenario, seen_colors)
        offset_const = (
            x_base + s_idx * 2 * bar_width - bar_width * (len(scenarios) - 0.5)
        )
        offset_prop = offset_const + bar_width
        ax.bar(
            offset_const,
            const_pcts,
            width=bar_width,
            color=base_color,
            alpha=0.55,
            label=f"{scenario} constant",
        )
        ax.bar(
            offset_prop,
            prop_pcts,
            width=bar_width,
            color=base_color,
            alpha=0.95,
            hatch="//",
            label=f"{scenario} proportional",
        )

    ax.set_xticks(x_base)
    ax.set_xticklabels([f"n={n_val},c={c_val}" for n_val, c_val in groups])
    ax.set_ylabel("captured improvement (% of achievable I), summed over instances")
    ax.set_title(
        "Equal-budget captured improvement: constant vs proportional per-window TL\n"
        "(offline approximation -- see report caveat)"
    )
    ax.legend(loc="lower right", fontsize=8, ncol=2)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return out_path


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "run_dirs",
        nargs="+",
        help="Run directory(ies) containing <scenario>/<instance>/ subdirs.",
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--constant-cap", type=float, default=DEFAULT_CONSTANT_CAP)
    args = parser.parse_args(argv)

    rows = collect_rows(args.run_dirs)
    if not rows:
        print("no sw_cp windows found; nothing to do")
        return
    print(f"collected {len(rows)} window rows from {len(args.run_dirs)} run dir(s)")

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    window_metrics_path = out_dir / "window_metrics.csv"
    write_window_metrics_csv(rows, window_metrics_path)
    print(f"wrote {window_metrics_path}")

    sweep = budget_cap_sweep(rows)
    sweep_md = render_budget_sweep_md(sweep)

    regression_summary_path = out_dir / "regression_summary.md"
    regression_summary_path.write_text(build_regression_summary(rows) + "\n" + sweep_md)
    print(f"wrote {regression_summary_path}")
    print(sweep_md)

    per_instance = equal_budget_comparison(rows, args.constant_cap)
    aggregate = aggregate_captured(per_instance)
    captured_csv_path = out_dir / "captured_comparison.csv"
    write_captured_csv(per_instance, aggregate, captured_csv_path)
    print(f"wrote {captured_csv_path}")
    print(CAPTURED_CAVEAT)

    scatter_path = plot_t90_scatter(rows, out_dir / "t90_vs_non_time_fixed.png")
    print(f"wrote {scatter_path}")
    bars_path = plot_captured_bars(
        aggregate, out_dir / "captured_constant_vs_proportional.png"
    )
    print(f"wrote {bars_path}")


if __name__ == "__main__":
    main()
