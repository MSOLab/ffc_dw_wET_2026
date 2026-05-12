"""Debugging gantt visualisation for pw_cp's five-region partition.

Renders a per-machine SVG gantt chart where every operation bar is
coloured by its partition region (LTF, LPF, Unfixed, RPF, RTF),
per-machine left/right time boundaries are drawn as vertical dashed
lines, and reserved-capacity zones are shown as background bands.
"""

from __future__ import annotations

import io

# Must set backend before importing pyplot.
import matplotlib

matplotlib.use("Agg")
matplotlib.rcParams["svg.fonttype"] = "none"  # Use <text> not font paths
import matplotlib.patches as mpatches  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

from ...solution.ffc_schedule import FFcSchedule, StageIdType  # noqa: E402
from .partition import OperationPartition  # noqa: E402

__all__ = ["REGION_COLORS", "render_partition_gantt_svg"]

REGION_COLORS: dict[str, str] = {
    "LTF": "#90A4AE",
    "LPF": "#FFCC80",
    "UNFIXED": "#81C784",
    "RPF": "#FF9800",
    "RTF": "#607D8B",
}
"""Hex colours for each partition region — blue-grays for time-fixed,
ambers for profile-fixed, green for the unfixed window."""

_REGION_ORDER = ("LTF", "LPF", "UNFIXED", "RPF", "RTF")


def render_partition_gantt_svg(
    schedule: FFcSchedule,
    stage_2_partition: dict[StageIdType, OperationPartition],
    stage_id_list: list[str],
    machines_per_stage: dict[str, list[str]],
    horizon: int,
    *,
    step: int,
    unfixed_start: int,
    unfixed_batch_count: int,
) -> str:
    """Render one pw_cp step's five-region partition as an SVG string.

    Parameters
    ----------
    schedule : FFcSchedule
        The right-justified reference schedule (``rj_schedule`` in the
        dispatcher) whose ``(job, stage, machine) -> start/end`` maps
        drive bar placement.
    stage_2_partition : dict
        The per-stage :class:`OperationPartition` for this step.
    stage_id_list : list[str]
        Ordered stage IDs (from the full instance).
    machines_per_stage : dict
        Mapping ``stage_id -> [machine_id, ...]``.
    horizon : int
        CP-SAT horizon for the x-axis upper bound.
    step : int
        Zero-based step index.
    unfixed_start : int
        Batch index where the unfixed window starts.
    unfixed_batch_count : int
        Number of batches in the unfixed window.

    Returns
    -------
    str
        Self-contained SVG document.
    """
    # ── 1. build region lookup ──────────────────────────────────────
    region_of: dict[tuple[str, str, str], str] = {}
    for s_id, partition in stage_2_partition.items():
        for rname, ops in [
            ("LTF", partition.left_time_fixed),
            ("LPF", partition.left_profile_fixed),
            ("UNFIXED", partition.unfixed),
            ("RPF", partition.right_profile_fixed),
            ("RTF", partition.right_time_fixed),
        ]:
            for job, mc in ops:
                region_of[(s_id, job, mc)] = rname

    # ── 2. machine lanes ────────────────────────────────────────────
    lanes: list[tuple[str, str]] = []
    lane_labels: list[str] = []
    for s_id in stage_id_list:
        for mc in machines_per_stage.get(s_id, []):
            lanes.append((s_id, mc))
            lane_labels.append(f"{s_id}-{mc}")
    n_lanes = len(lanes)
    if n_lanes == 0:
        return "<svg xmlns='http://www.w3.org/2000/svg'/>"

    # ── 3. per-machine boundaries + ops ─────────────────────────────
    start_map = schedule.get_jik_2_start_time_map()
    end_map = schedule.get_jik_2_end_time_map()

    lane_boundaries: dict[tuple[str, str], tuple[int, int]] = {}
    lane_ops: dict[tuple[str, str], list[tuple[str, int, int, str]]] = {}

    for s_id, mc in lanes:
        partition = stage_2_partition.get(s_id)
        ltf_ends: list[int] = []
        rtf_starts: list[int] = []
        ops_on_mc: list[tuple[str, int, int, str]] = []

        if partition is not None:
            for job, k in partition.left_time_fixed:
                if k == mc:
                    e = end_map.get((job, s_id, k))
                    if e is not None:
                        ltf_ends.append(e)
            for job, k in partition.right_time_fixed:
                if k == mc:
                    s = start_map.get((job, s_id, k))
                    if s is not None:
                        rtf_starts.append(s)

            for rname in _REGION_ORDER:
                region_ops = getattr(
                    partition,
                    {
                        "LTF": "left_time_fixed",
                        "LPF": "left_profile_fixed",
                        "UNFIXED": "unfixed",
                        "RPF": "right_profile_fixed",
                        "RTF": "right_time_fixed",
                    }[rname],
                )
                for job, k in region_ops:
                    if k == mc:
                        s = start_map.get((job, s_id, k))
                        e = end_map.get((job, s_id, k))
                        if s is not None and e is not None:
                            ops_on_mc.append((job, s, e, rname))

        left_b = max(ltf_ends) if ltf_ends else 0
        right_b = min(rtf_starts) if rtf_starts else horizon
        lane_boundaries[(s_id, mc)] = (left_b, right_b)
        lane_ops[(s_id, mc)] = ops_on_mc

    # ── 4. draw ─────────────────────────────────────────────────────
    machine_height = 1.0
    bar_height = 0.8
    fig_height = max(4.0, 1.5 + n_lanes * 0.55)

    fig, ax = plt.subplots(figsize=(14.0, fig_height))

    # 4a. background bands
    for idx, (s_id, mc) in enumerate(lanes):
        y = float(idx)
        left_b, right_b = lane_boundaries[(s_id, mc)]

        if left_b > 0:
            ax.add_patch(
                mpatches.Rectangle(
                    (0, y),
                    left_b,
                    machine_height,
                    facecolor=REGION_COLORS["LTF"],
                    alpha=0.07,
                    edgecolor="none",
                )
            )
        if right_b > left_b:
            ax.add_patch(
                mpatches.Rectangle(
                    (left_b, y),
                    right_b - left_b,
                    machine_height,
                    facecolor=REGION_COLORS["UNFIXED"],
                    alpha=0.04,
                    edgecolor="none",
                )
            )
        if right_b < horizon:
            ax.add_patch(
                mpatches.Rectangle(
                    (right_b, y),
                    horizon - right_b,
                    machine_height,
                    facecolor=REGION_COLORS["RTF"],
                    alpha=0.07,
                    edgecolor="none",
                )
            )

    # 4b. operation bars
    for idx, (s_id, mc) in enumerate(lanes):
        y = float(idx)
        bar_bottom = y + 0.1
        for job, s, e, rname in lane_ops[(s_id, mc)]:
            dur = e - s
            if dur <= 0:
                continue
            color = REGION_COLORS[rname]
            unfixed = rname == "UNFIXED"
            alpha_val = 1.0 if unfixed else 0.72
            lw = 2.0 if unfixed else 1.0

            ax.add_patch(
                mpatches.Rectangle(
                    (s, bar_bottom),
                    dur,
                    bar_height,
                    facecolor=color,
                    edgecolor="black",
                    alpha=alpha_val,
                    linewidth=lw,
                )
            )
            mid = s + dur / 2.0
            ax.text(
                mid,
                bar_bottom + bar_height / 2.0,
                job,
                ha="center",
                va="center",
                fontsize=7,
                color="black",
            )
            ax.text(
                mid,
                bar_bottom + bar_height - 0.05,
                str(dur),
                ha="center",
                va="bottom",
                fontsize=6,
                color="gray",
            )

    # 4c. boundary lines (data coords, per-lane vertical segment)
    for idx, (s_id, mc) in enumerate(lanes):
        y = float(idx)
        left_b, right_b = lane_boundaries[(s_id, mc)]
        if left_b > 0:
            ax.plot(
                [left_b, left_b],
                [y, y + machine_height],
                color="#37474F",
                linestyle=(0, (6, 4)),
                linewidth=1.2,
            )
        if right_b < horizon:
            ax.plot(
                [right_b, right_b],
                [y, y + machine_height],
                color="#37474F",
                linestyle=(0, (6, 4)),
                linewidth=1.2,
            )

    # ── 5. axes ─────────────────────────────────────────────────────
    ax.set_yticks([i + 0.5 for i in range(n_lanes)])
    ax.set_yticklabels(lane_labels)
    ax.set_ylim(-0.5, float(n_lanes) + 0.5)
    ax.set_xlim(0, horizon + 1)
    ax.set_xlabel("Time")
    ax.invert_yaxis()
    ax.grid(True, axis="x", linestyle="--", alpha=0.3)

    title = (
        f"pw_cp  step={step}  "
        f"unfixed=[{unfixed_start},{unfixed_start + unfixed_batch_count})  "
        f"horizon={horizon}"
    )
    ax.set_title(title, fontsize=11, fontweight="bold")

    # 5a. region legend
    legend_patches = [
        mpatches.Patch(color=REGION_COLORS[r], label=r) for r in _REGION_ORDER
    ]
    ax.legend(
        handles=legend_patches,
        loc="upper right",
        fontsize=8,
        ncol=5,
        framealpha=0.7,
    )

    plt.tight_layout()

    # ── 6. export SVG string ────────────────────────────────────────
    buf = io.BytesIO()
    fig.savefig(buf, format="svg", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue().decode("utf-8")
