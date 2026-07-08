"""Plot SW-CP UB/LB objective trajectories over controller-frame time.

Reads `<instance>_obj_log.json` files (one per instance) and renders a single
line chart per instance showing the objective UB (incumbent, step plot) and
LB (context only, faint dashed line) over controller time, with vertical
markers at each step-end boundary recorded in `obj_value.notes`.

This is a visual aid for spotting where UB improvement plateaus inside SW-CP.

**Do not** reuse `src/.../report/obj_log_loader.py` here: that structured
loader drops any series whose points carry no per-point `notes`, and sw_cp's
LB (`obj_bound`) has no per-point notes, so the loader silently drops the LB
series entirely. This script reads the raw JSON maps directly instead.

Usage:
    uv run python scripts/20260706/plot_ub_lb_vs_time.py \
        <run_dir_or_glob> [<run_dir_or_glob> ...] [--out-dir DIR]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import matplotlib
import yaml

matplotlib.use("Agg")
import matplotlib.pyplot as plt

UB_COLOR = "#1f77b4"
LB_COLOR = "#999999"
MARKER_COLOR = "#d62728"
WINDOW_MARKER_COLOR = "#6a5acd"  # muted slate-blue, lighter than the red step markers

DEFAULT_OUT_DIR = Path("analysis/20260705_sw_cp_tl_profile/plots")

# Matches `2-sw_cp_step_log.yaml` and `2-incremental_sw_cp*_step_log.yaml`.
STEP_LOG_RE = re.compile(r"^(\d+)-(sw_cp|incremental_sw_cp\w*)_step_log\.yaml$")


def find_obj_logs(inputs: list[str]) -> list[Path]:
    """Resolve CLI inputs (dirs or globs) to a de-duplicated, sorted list of
    `*_obj_log.json` paths found recursively."""
    found: set[Path] = set()
    for raw in inputs:
        path = Path(raw)
        if path.is_dir():
            found.update(path.rglob("*_obj_log.json"))
        else:
            # Treat as a glob pattern (may include directories with wildcards).
            for match in Path().glob(raw):
                if match.is_dir():
                    found.update(match.rglob("*_obj_log.json"))
                elif match.name.endswith("_obj_log.json"):
                    found.add(match)
    return sorted(found)


def load_series(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return json.load(f)


def sorted_points(data: dict[str, float]) -> tuple[list[float], list[float]]:
    items = sorted(((float(k), v) for k, v in data.items()), key=lambda kv: kv[0])
    if not items:
        return [], []
    xs, ys = zip(*items)
    return list(xs), list(ys)


def derive_names(path: Path) -> tuple[str, str]:
    """Derive (scenario, instance) from the artifact directory layout:

    .../<scenario>/<instance>/<instance>_obj_log.json
    """
    instance_dir = path.parent
    instance = instance_dir.name
    scenario = instance_dir.parent.name if instance_dir.parent else ""
    return scenario, instance


def load_window_ends(
    obj_log_path: Path, note_items: list[tuple[float, str]]
) -> list[tuple[float, int]]:
    """Return per-window (controller_end_time, unfixed_batch_start_idx) markers.

    For each `<inst_dir>/progress/<N>-sw_cp_step_log.yaml` (and the analogous
    `<N>-incremental_sw_cp*_step_log.yaml`), the step_log's `elapsed_time` is
    cumulative sw_cp-relative seconds. We re-base it into controller-frame time
    by anchoring to the matching step-end note whose label starts with `{N}-`:

        offset = note_t - rows[-1]["elapsed_time"]
        window_end_controller_t = offset + rows[i]["elapsed_time"]

    Missing progress dir / step_log / matching note are all tolerated: the
    affected step just contributes no window markers.
    """
    progress_dir = obj_log_path.parent / "progress"
    if not progress_dir.is_dir():
        return []

    # Index step-end note timestamps by their `{N}-<subroutine>` label.
    note_t_by_label = {label: t for t, label in note_items}

    markers: list[tuple[float, int]] = []
    for step_log in sorted(progress_dir.glob("*_step_log.yaml")):
        m = STEP_LOG_RE.match(step_log.name)
        if not m:
            continue
        step_idx, subroutine = m.group(1), m.group(2)

        # The note label is f"{N}-{subroutine}"; fall back to any note sharing
        # the same integer prefix (defensive against subroutine-name drift).
        note_t = note_t_by_label.get(f"{step_idx}-{subroutine}")
        if note_t is None:
            for label, t in ((lbl, tt) for tt, lbl in note_items):
                if label.split("-", 1)[0] == step_idx:
                    note_t = t
                    break
        if note_t is None:
            continue

        try:
            with step_log.open() as f:
                rows = yaml.safe_load(f)
        except (OSError, yaml.YAMLError):
            continue
        if not rows:
            continue

        last_elapsed = rows[-1].get("elapsed_time")
        if last_elapsed is None:
            continue
        offset = note_t - last_elapsed

        for row in rows:
            elapsed = row.get("elapsed_time")
            idx = row.get("unfixed_batch_start_idx")
            if elapsed is None or idx is None:
                continue
            markers.append((offset + elapsed, idx))

    markers.sort(key=lambda kv: kv[0])
    return markers


def plot_one(path: Path, out_dir: Path) -> Path:
    log = load_series(path)
    obj_value = log["obj_value"]
    obj_bound = log["obj_bound"]

    ub_t, ub_y = sorted_points(obj_value["data"])
    lb_t, lb_y = sorted_points(obj_bound["data"])
    notes: dict[str, str] = obj_value.get("notes", {})
    note_items = sorted(((float(k), v) for k, v in notes.items()), key=lambda kv: kv[0])
    window_ends = load_window_ends(path, note_items)

    scenario, instance = derive_names(path)

    fig, ax = plt.subplots(figsize=(12, 6))

    ax.plot(
        ub_t,
        ub_y,
        drawstyle="steps-post",
        color=UB_COLOR,
        linewidth=2.0,
        label="UB (obj_value)",
        zorder=3,
    )
    ax.plot(
        lb_t,
        lb_y,
        color=LB_COLOR,
        linewidth=1.0,
        linestyle="--",
        alpha=0.7,
        label="LB (context only)",
        zorder=2,
    )

    # Pad the x-range so markers near the first/last data point (a common
    # case: the last step-end note lands at the final timestamp) have room
    # for their rotated annotation instead of being clipped at the edge.
    all_t = ub_t + lb_t + [t for t, _ in note_items] + [t for t, _ in window_ends]
    if all_t:
        span = max(all_t) - min(all_t) or 1.0
        pad = span * 0.04
        ax.set_xlim(min(all_t) - pad, max(all_t) + pad)

    # Leave headroom above the highest curve so rotated annotation labels
    # don't collide with the axes top border or the legend.
    ymin, ymax = ax.get_ylim()
    ymax_padded = ymax + (ymax - ymin) * 0.18
    ax.set_ylim(ymin, ymax_padded)

    # Per-window (unfixed-batch) end markers: thin dotted lines in a muted
    # colour, labelled `ub <idx>` along the BOTTOM axis so they sit clear of
    # the red step-end labels along the top.
    window_label_added = False
    for t, idx in window_ends:
        ax.axvline(
            t,
            color=WINDOW_MARKER_COLOR,
            linestyle=":",
            linewidth=0.8,
            alpha=0.45,
            zorder=1,
            label="window end (ub idx)" if not window_label_added else None,
        )
        window_label_added = True
        ax.annotate(
            f"ub {idx}",
            xy=(t, ymin),
            xytext=(2, 2),
            textcoords="offset points",
            rotation=90,
            va="bottom",
            ha="left",
            fontsize=6,
            color=WINDOW_MARKER_COLOR,
            clip_on=False,
        )

    for t, label in note_items:
        ax.axvline(
            t, color=MARKER_COLOR, linestyle=":", linewidth=1.0, alpha=0.6, zorder=2
        )
        ax.annotate(
            label,
            xy=(t, ymax_padded),
            xytext=(-4, -2),
            textcoords="offset points",
            rotation=90,
            va="top",
            ha="right",
            fontsize=7,
            color=MARKER_COLOR,
            clip_on=False,
        )

    ax.set_title(f"{scenario} / {instance}")
    ax.set_xlabel("controller time (s)")
    ax.set_ylabel("objective (weighted earliness+tardiness)")
    # Legend sits below the axes (not "upper right") so it never collides
    # with a step-boundary annotation anchored near the plot's top edge.
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=3, frameon=False)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{scenario}__{instance}.png"
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return out_path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "inputs",
        nargs="+",
        help="Run directory (or directories) or glob pattern(s) to search "
        "recursively for *_obj_log.json files.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"Output directory for PNGs (default: {DEFAULT_OUT_DIR})",
    )
    args = parser.parse_args(argv)

    obj_logs = find_obj_logs(args.inputs)
    if not obj_logs:
        print("no *_obj_log.json files found")
        return

    for path in obj_logs:
        out_path = plot_one(path, args.out_dir)
        print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
