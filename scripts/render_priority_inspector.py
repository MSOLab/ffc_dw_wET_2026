"""Render a priority-rule Inspector figure for a single instance.

Pipeline: read CLI / YAML config -> load a PRA2017 instance -> compute a
dispatch sequence -> simple-dispatch decode -> build three panels:

  Panel A  Priority Inspector (SVG, hand-drawn)
           job rows sorted by dispatch rank showing due-window band,
           weight glyph, completion marker, wxd2 partition overlay, sort key.
  Panel B  Decoded Schedule (reuses DDWGanttPlotter.export_ddw)
  Stats    weighted E+T, early/in-window/tardy counts, makespan, vs BKS.

Usage::

    uv run python scripts/render_priority_inspector.py \\
        --instance-index 60 --rule-key wxd2 \\
        [--output output/20260625/priority_viz/0060_wxd2.html]

See plans/20260625/priority_rule_simulator_viz.md for the design.
"""

from __future__ import annotations

import argparse
import csv
import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

from ffc_ddw_sum_et.algorithm.dispatcher import MixedDispatcher
from ffc_ddw_sum_et.io.gantt import DDWGanttPlotter
from ffc_ddw_sum_et.orchestration.benchmark_loader import BenchmarkLoader
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.parameters.sorter import dispatch_seq_job_sequence
from ffc_ddw_sum_et.solution.objectives import compute_weighted_earliness_tardiness

REPO_ROOT = Path(__file__).resolve().parent.parent
BKS_TABLE = REPO_ROOT / "benchmarks" / "PRA2017" / "pra2017_bks_table.csv"
DEFAULT_BENCHMARK_DIR = REPO_ROOT / "benchmarks" / "PRA2017" / "large"
DEFAULT_HYBRID_MATCH = REPO_ROOT / "benchmarks" / "PRA2017" / "pra2017_hybrid_match.csv"

EARLY_COLOR = "#1f77b4"  # blue
IN_COLOR = "#2ca02c"  # green
TARDY_COLOR = "#d62728"  # red
DUE_BAND_COLOR = "#cccccc"
DIVIDER_COLOR = "#999999"
D_BAR_COLOR = "#ff7f0e"

# Panel A weight-bar geometry (SSOT for header + row rendering)
W_BAR_MAX = 30
W_BAR_GAP = 35
ROW_BG_EARLY = "#f0f4ff"
ROW_BG_LATE = "#ffffff"
KEY_GUTTER_COLOR = "#333333"
RANK_GUTTER_COLOR = "#555555"
JOB_ID_COLOR = "#333333"
TEXT_COLOR = "#333333"
LIGHT_TEXT = "#888888"
HEADER_BG = "#f5f5f5"
BORDER_COLOR = "#e0e0e0"
RESERVED_COLORS = {EARLY_COLOR.lower(), TARDY_COLOR.lower(), IN_COLOR.lower()}


# ---------------------------------------------------------------------------
# BKS lookup
# ---------------------------------------------------------------------------


def load_bks_table(path: Path) -> dict[str, dict]:
    """Return {insIndex: {BKS_data, n, c, T, R, W}} from the CSV."""
    if not path.exists():
        return {}
    result: dict[str, dict] = {}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            result[row["insIndex"]] = {
                "BKS_data": int(row["BKS_data"]),
                "n": int(row["n"]),
                "c": int(row["c"]),
                "T": float(row["T"]),
                "R": float(row["R"]),
                "W": int(row["W"]),
            }
    return result


def get_bks(ins_index: int, bks_table: dict) -> int | None:
    key = f"{ins_index:04d}"
    row = bks_table.get(key)
    return row["BKS_data"] if row else None


# ---------------------------------------------------------------------------
# wxd2 partition helpers (extracted from params for Inspector overlay)
# ---------------------------------------------------------------------------


def _mean_midpoint(instance: FFcDDWParameters) -> float:
    """Return the mean of (dl+du)/2 across all jobs — d̄ shared by wxd2 and fallback."""
    ddw = instance.job_2_due_window_map
    return sum((dl + du) / 2 for dl, du in ddw.values()) / len(ddw)


def _wxd5_d_bar(instance: FFcDDWParameters) -> float:
    """wxd5 center: max(midpoint mean, min_j r_j + Σ_j p_last / (m_last·2)).

    Mirrors ``FFcDDWParameters.get_wxd5_job_sequence``. The second term is a
    last-stage completion lower bound; when it exceeds the midpoint mean it
    pushes d̄ to the right (otherwise d̄ == wxd2's midpoint mean).
    """
    mean_midpoint = _mean_midpoint(instance)
    last_stage_id = instance.stage_id_list[-1]
    p_last = instance.get_job_2_p_map_for_stage(last_stage_id)
    r_j = instance.get_job_2_p_sum_except_last_stage()
    p_last_total = sum(p_last.values())
    return max(
        mean_midpoint,
        min(r_j.values()) + p_last_total / (instance.last_stage_mc_count * 2),
    )


def compute_wxd2_partition(
    instance: FFcDDWParameters,
    d_bar: float | None = None,
) -> dict[str, dict]:
    """Compute wxd2/wxd5 partition data for every job.

    The partition criterion and sort keys are identical for wxd2 and wxd5;
    only the center ``d_bar`` differs. Pass ``d_bar=None`` (default) for wxd2's
    midpoint mean, or ``_wxd5_d_bar(instance)`` for wxd5.

    Returns {job_id: {
        'earliness_aversion': float,
        'tardiness_aversion': float,
        'partition': 'early' | 'late',
        'sort_key': float,
        'd_mid': float,
        'd_bar': float,
        'ewt': int,
        'twt': int,
        'd_lower': int,
        'd_upper': int,
    }}
    """
    ewt = instance.job_2_ewt_map
    twt = instance.job_2_twt_map
    ddw = instance.job_2_due_window_map
    job_id_list = instance.job_id_list

    d_mid = {j: (ddw[j][0] + ddw[j][1]) / 2 for j in job_id_list}
    if d_bar is None:
        d_bar = _mean_midpoint(instance)
    ew_max = max(ewt.values())
    tw_max = max(twt.values())

    earliness_av = {j: ewt[j] + (ddw[j][0] - d_bar) for j in job_id_list}
    tardiness_av = {j: twt[j] + (d_bar - ddw[j][1]) for j in job_id_list}

    result = {}
    for j in job_id_list:
        ea = earliness_av[j]
        ta = tardiness_av[j]
        if ta > ea:
            partition = "early"
            sort_key = (twt[j] - 2 * ewt[j] + 2 * ew_max) * (ddw[j][0] - d_bar)
        else:
            partition = "late"
            sort_key = (ewt[j] - 2 * twt[j] + 2 * tw_max) * (ddw[j][1] - d_bar)
        result[j] = {
            "earliness_aversion": ea,
            "tardiness_aversion": ta,
            "partition": partition,
            "sort_key": sort_key,
            "d_mid": d_mid[j],
            "d_bar": d_bar,
            "ewt": ewt[j],
            "twt": twt[j],
            "d_lower": ddw[j][0],
            "d_upper": ddw[j][1],
        }
    return result


def compute_wxd7_partition(instance: FFcDDWParameters) -> dict[str, dict]:
    """Compute wxd7 partition data for the Inspector overlay.

    wxd7's partition is identical to wxd5 (same floored center d̄), but the
    intra-group sort keys use group-specific centers rather than d̄:
        early_center = min_j r_j + Σ_j p_last_j / m_last   (no ÷2, no floor)
        late_center  = min_j r_j
    Mirrors ``FFcDDWParameters.get_wxd7_job_sequence``. The returned ``sort_key``
    is the actual within-group sort scalar (early: ``-tp_j(early_center)``,
    late: ``ep_j(late_center)``) so Panel A's Key gutter matches dispatch order.
    """
    d_bar = _wxd5_d_bar(instance)
    data = compute_wxd2_partition(instance, d_bar=d_bar)

    ewt = instance.job_2_ewt_map
    twt = instance.job_2_twt_map
    ddw = instance.job_2_due_window_map
    last_stage_id = instance.stage_id_list[-1]
    p_last = instance.get_job_2_p_map_for_stage(last_stage_id)
    r_j = instance.get_job_2_p_sum_except_last_stage()
    min_r = min(r_j.values())
    early_center = min_r + sum(p_last.values()) / instance.last_stage_mc_count
    late_center = min_r

    for j, entry in data.items():
        dl, du = ddw[j]
        if entry["partition"] == "early":
            entry["sort_key"] = -twt[j] * max(early_center - du, 0)
        else:
            entry["sort_key"] = ewt[j] * max(dl - late_center, 0)
    return data


# ---------------------------------------------------------------------------
# Panel A: Priority Inspector SVG
# ---------------------------------------------------------------------------


def _fmt_key(v: float) -> str:
    """Format a sort key value for display."""
    if v == 0:
        return "0"
    if abs(v) >= 1e6:
        return f"{v / 1e6:.1f}M"
    if abs(v) >= 1e3:
        return f"{v / 1e3:.1f}k"
    return f"{v:.0f}"


def render_priority_inspector_svg(
    instance: FFcDDWParameters,
    dispatch_seq: list[str],
    job_2_end_time: dict[str, int],
    wxd2_data: dict[str, dict] | None = None,
    d_bar_value: float | None = None,
    rule_key: str = "wxd2",
    width: int = 1200,
    rank_gutter_w: int = 45,
    job_gutter_w: int = 45,
    weight_glyph_w: int = 80,
    body_left_margin: int = 0,  # computed below
    row_height: int = 16,
    header_height: int = 40,
    key_gutter_w: int = 80,
    time_padding: int = 100,
) -> tuple[str, int, int, int, int]:
    """Render Panel A as an SVG string.

    Returns (svg_string, min_time, max_time, dw_left, dw_right).
    """
    ddw = instance.job_2_due_window_map
    ewt = instance.job_2_ewt_map
    twt = instance.job_2_twt_map
    n = len(dispatch_seq)

    # Compute time range from due windows and completion times
    all_times: list[int] = []
    for j in dispatch_seq:
        dl, du = ddw[j]
        all_times.extend([dl, du])
        c = job_2_end_time.get(j, 0)
        if c > 0:
            all_times.append(c)
    min_time = min(all_times) if all_times else 0
    max_time = max(all_times) if all_times else 1
    # Add padding
    time_range = max_time - min_time
    if time_range == 0:
        time_range = 100
    min_time -= time_padding
    max_time += time_padding
    if min_time < 0:
        min_time = 0

    # Due window display range (for the body)
    due_min = min(ddw[j][0] for j in dispatch_seq)
    due_max = max(ddw[j][1] for j in dispatch_seq)
    due_range = due_max - due_min if due_max > due_min else 100
    dw_left = due_min - int(due_range * 0.05)
    dw_right = due_max + int(due_range * 0.05)

    # Keep the d̄ overlay on-canvas: wxd5/wxd7 can floor d̄ past the last due
    # window, so widen the display range to include it rather than clipping it.
    if d_bar_value is not None:
        if d_bar_value > dw_right:
            dw_right = int(d_bar_value + due_range * 0.05)
        elif d_bar_value < dw_left:
            dw_left = int(d_bar_value - due_range * 0.05)

    # Layout constants
    body_left = rank_gutter_w + job_gutter_w + weight_glyph_w + 10
    body_right = width - key_gutter_w - 10
    svg_height = header_height + n * row_height + 10

    def time_to_x(
        t: float, x_min: float, x_max: float, t_min: float, t_max: float
    ) -> float:
        """Map time value to x coordinate."""
        if t_max == t_min:
            return (x_min + x_max) / 2
        return x_min + (t - t_min) / (t_max - t_min) * (x_max - x_min)

    # SVG content
    svg_parts: list[str] = []
    svg_parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{svg_height}" font-family="monospace" font-size="10">'
    )

    # Background
    svg_parts.append(f'<rect width="{width}" height="{svg_height}" fill="white"/>')

    # Header
    svg_parts.append(
        f'<rect x="0" y="0" width="{width}" height="{header_height}" fill="{HEADER_BG}"/>'
    )
    svg_parts.append(
        f'<text x="10" y="{header_height // 2 + 4}" font-weight="bold" fill="{TEXT_COLOR}">{rule_key} — Priority Inspector (n={n})</text>'
    )

    # Column headers
    svg_parts.append(
        f'<text x={rank_gutter_w // 2 + 5} y="{header_height - 5}" fill="{LIGHT_TEXT}" text-anchor="middle">#</text>'
    )
    svg_parts.append(
        f'<text x={rank_gutter_w + job_gutter_w // 2 + 5} y="{header_height - 5}" fill="{LIGHT_TEXT}" text-anchor="middle">Job</text>'
    )

    # Weight glyph header — center over bars: w- at x0+max/2, w+ at x0+gap+max/2
    w_bar_x0 = rank_gutter_w + job_gutter_w + 5
    w_minus_center = w_bar_x0 + W_BAR_MAX // 2
    w_plus_center = w_bar_x0 + W_BAR_GAP + W_BAR_MAX // 2
    svg_parts.append(
        f'<text x="{w_minus_center}" y="{header_height - 5}" fill="{EARLY_COLOR}" text-anchor="middle">w-</text>'
    )
    svg_parts.append(
        f'<text x="{w_plus_center}" y="{header_height - 5}" fill="{TARDY_COLOR}" text-anchor="middle">w+</text>'
    )

    # Due band header
    svg_parts.append(
        f'<text x="{body_left + 5}" y="{header_height - 5}" fill="{DUE_BAND_COLOR}" text-anchor="start">Due Window</text>'
    )

    # Key gutter header — label as aversion proxy for non-partition rules (W7)
    key_header = "Key" if rule_key in ("wxd2", "wxd5", "wxd7") else "T-E@d̄"
    svg_parts.append(
        f'<text x={width - key_gutter_w // 2 - 5} y="{header_height - 5}" fill="{KEY_GUTTER_COLOR}" text-anchor="middle">{key_header}</text>'
    )

    # d_bar line: rule-specific d̄ (wxd2 midpoint mean, wxd5/wxd7 floored center),
    # falling back to the partition d̄ when an explicit value isn't supplied.
    d_bar_val = d_bar_value
    if d_bar_val is None and wxd2_data and dispatch_seq:
        d_bar_val = wxd2_data[dispatch_seq[0]]["d_bar"]

    # Pre-compute row-invariant values once, out of the row loop (W6/W7).
    max_w = max(max(ewt.values()), max(twt.values()))  # weight-bar scaling
    # d̄ used by the non-partition "T-E@d̄" key gutter — keep it consistent with
    # the drawn d̄ line so the displayed aversion deltas match the overlay.
    d_bar = d_bar_val if d_bar_val is not None else _mean_midpoint(instance)

    # Draw rows
    for rank, job_id in enumerate(dispatch_seq):
        y = header_height + rank * row_height
        dl, du = ddw[job_id]
        c = job_2_end_time.get(job_id, 0)
        ew = ewt[job_id]
        tw = twt[job_id]

        # Row background
        partition = None
        if wxd2_data and job_id in wxd2_data:
            partition = wxd2_data[job_id]["partition"]
        bg = ROW_BG_EARLY if partition == "early" else ROW_BG_LATE
        svg_parts.append(
            f'<rect x="0" y="{y}" width="{width}" height="{row_height}" fill="{bg}" opacity="0.5"/>'
        )

        # Row border
        svg_parts.append(
            f'<line x1="0" y1="{y}" x2="{width}" y2="{y}" stroke="{BORDER_COLOR}" stroke-width="0.5"/>'
        )

        # Rank
        svg_parts.append(
            f'<text x="{rank_gutter_w // 2 + 5}" y="{y + row_height // 2 + 3}" fill="{RANK_GUTTER_COLOR}" text-anchor="middle">{rank + 1}</text>'
        )

        # Job ID
        svg_parts.append(
            f'<text x={rank_gutter_w + 5} y="{y + row_height // 2 + 3}" fill="{JOB_ID_COLOR}">{job_id}</text>'
        )

        # Weight glyph: w- bar (blue), w+ bar (red)
        if max_w > 0:
            ew_ratio = ew / max_w
            tw_ratio = tw / max_w
            svg_parts.append(
                f'<rect x="{w_bar_x0}" y="{y + row_height // 2 - 4}" width="{W_BAR_MAX * ew_ratio:.0f}" height="8" fill="{EARLY_COLOR}" opacity="0.7"/>'
            )
            svg_parts.append(
                f'<rect x="{w_bar_x0 + W_BAR_GAP}" y="{y + row_height // 2 - 4}" width="{W_BAR_MAX * tw_ratio:.0f}" height="8" fill="{TARDY_COLOR}" opacity="0.7"/>'
            )

        # Due window band (gray bar)
        band_x1 = time_to_x(dl, body_left, body_right, dw_left, dw_right)
        band_x2 = time_to_x(du, body_left, body_right, dw_left, dw_right)
        band_w = max(band_x2 - band_x1, 2)
        svg_parts.append(
            f'<rect x="{band_x1:.0f}" y="{y + 2}" width="{band_w:.0f}" height="{row_height - 4}" fill="none" stroke="{DUE_BAND_COLOR}" stroke-width="1.5"/>'
        )

        # Midpoint tick
        mid_x = time_to_x((dl + du) / 2, body_left, body_right, dw_left, dw_right)
        svg_parts.append(
            f'<line x1="{mid_x:.0f}" y1="{y + row_height // 2 - 3}" x2="{mid_x:.0f}" y2="{y + row_height // 2 + 3}" stroke="{DUE_BAND_COLOR}" stroke-width="1"/>'
        )

        # Completion marker
        if c > 0:
            cx = time_to_x(c, body_left, body_right, dw_left, dw_right)
            if dl > c:
                marker_color = EARLY_COLOR  # early
            elif du < c:
                marker_color = TARDY_COLOR  # tardy
            else:
                marker_color = IN_COLOR  # in-window
            svg_parts.append(
                f'<circle cx="{cx:.0f}" cy="{y + row_height // 2}" r="4" fill="{marker_color}"/>'
            )

        # d_bar vertical line
        if d_bar_val is not None and rank == 0:
            d_bar_x = time_to_x(d_bar_val, body_left, body_right, dw_left, dw_right)
            svg_parts.append(
                f'<line x1="{d_bar_x:.0f}" y1="{header_height}" x2="{d_bar_x:.0f}" y2="{svg_height}" stroke="{D_BAR_COLOR}" stroke-width="1" stroke-dasharray="4,3"/>'
            )
            svg_parts.append(
                f'<text x="{d_bar_x:.0f}" y="{header_height - 10}" fill="{D_BAR_COLOR}" text-anchor="middle" font-size="9">d̄</text>'
            )

        # wxd2 partition separator line
        if wxd2_data and rank == 0:
            # Find first late job after early jobs
            first_late_rank = None
            prev_partition = None
            for r2, j2 in enumerate(dispatch_seq):
                p2 = wxd2_data.get(j2, {}).get("partition")
                if p2 == "late" and prev_partition == "early":
                    first_late_rank = r2
                    break
                prev_partition = p2
            if first_late_rank is not None:
                sep_y = header_height + first_late_rank * row_height
                svg_parts.append(
                    f'<line x1="0" y1="{sep_y}" x2="{width}" y2="{sep_y}" stroke="{DIVIDER_COLOR}" stroke-width="1.5" stroke-dasharray="6,3"/>'
                )

        # Key gutter value
        key_val = ""
        if wxd2_data and job_id in wxd2_data:
            key_val = _fmt_key(wxd2_data[job_id]["sort_key"])
        else:
            # Generic: show tardiness_aversion - earliness_aversion delta
            # Use true d̄ (mean midpoint, hoisted above), not display-range midpoint (W7)
            ea = ew + (dl - d_bar)
            ta = tw + (d_bar - du)
            key_val = _fmt_key(ta - ea)
        svg_parts.append(
            f'<text x={width - key_gutter_w // 2 - 5} y="{y + row_height // 2 + 3}" fill="{KEY_GUTTER_COLOR}" text-anchor="middle">{key_val}</text>'
        )

    # Axis labels at bottom
    svg_parts.append(
        f'<text x="{body_left}" y="{svg_height - 2}" fill="{LIGHT_TEXT}" font-size="8">0</text>'
    )
    svg_parts.append(
        f'<text x="{body_right}" y="{svg_height - 2}" fill="{LIGHT_TEXT}" font-size="8">{dw_right:.0f}</text>'
    )

    svg_str = "\n".join(svg_parts)
    svg_str += "</svg>"
    return svg_str, min_time, max_time, dw_left, dw_right


# ---------------------------------------------------------------------------
# Panel B: Decoded Schedule (reuse DDWGanttPlotter)
# ---------------------------------------------------------------------------


def render_panel_b_svg(
    instance: FFcDDWParameters,
    schedule,
    drawn_jobs: list[str],
    output_dir: Path,
    d_bar: float | None = None,
) -> Path:
    """Render Panel B via DDWGanttPlotter.export_ddw. Returns the SVG path.

    When ``d_bar`` is given, an orange dashed d̄ reference line is overlaid on
    the schedule (matching Panel A's overlay color).
    """
    start_map = schedule.get_jik_2_start_time_map()
    end_map = schedule.get_jik_2_end_time_map()
    last_stage_id = instance.stage_id_list[-1]
    job_2_completion = {
        j: schedule.get_job_end_time(last_stage_id, j) for j in drawn_jobs
    }
    job_2_dw_map = {j: instance.job_2_due_window_map[j] for j in drawn_jobs}

    vlines = [(d_bar, D_BAR_COLOR, "d̄")] if d_bar is not None else None

    svg_path = output_dir / "panel_b_gantt.svg"
    DDWGanttPlotter().export_ddw(
        file_path=svg_path,
        start_time_map=start_map,
        end_time_map=end_map,
        job_2_dw_map=job_2_dw_map,
        job_2_completion=job_2_completion,
        drawn_job_list=drawn_jobs,
        all_job_list=list(instance.job_id_list),
        stage_list=list(instance.stage_id_list),
        machine_list_per_stage=instance.stage_2_machines_map,
        title=None,
        vlines=vlines,
    )
    return svg_path


# ---------------------------------------------------------------------------
# Stats computation
# ---------------------------------------------------------------------------


def compute_stats(
    schedule,
    instance: FFcDDWParameters,
    dispatch_seq: list[str],
    job_2_end_time: dict[str, int],
) -> dict:
    """Compute summary statistics."""
    sum_e, sum_t = compute_weighted_earliness_tardiness(schedule, instance)
    last_stage_id = instance.stage_id_list[-1]
    makespan = (
        max(schedule.get_job_end_time(last_stage_id, j) for j in instance.job_id_list)
        if instance.job_id_list
        else 0
    )

    ddw = instance.job_2_due_window_map
    early_count = 0
    in_count = 0
    tardy_count = 0
    for j in dispatch_seq:
        c = job_2_end_time.get(j, 0)
        if c == 0:
            continue
        dl, du = ddw[j]
        if c < dl:
            early_count += 1
        elif c > du:
            tardy_count += 1
        else:
            in_count += 1

    return {
        "sum_earliness": sum_e,
        "sum_tardiness": sum_t,
        "total_obj": sum_e + sum_t,
        "makespan": makespan,
        "early_count": early_count,
        "in_count": in_count,
        "tardy_count": tardy_count,
        "total_jobs": len(dispatch_seq),
    }


# ---------------------------------------------------------------------------
# HTML composition
# ---------------------------------------------------------------------------


def svg_to_data_uri(svg_str: str) -> str:
    """Encode an SVG string as a data URI."""
    import base64

    return "data:image/svg+xml;base64," + base64.b64encode(
        svg_str.encode("utf-8")
    ).decode("utf-8")


def compose_html(
    panel_a_svg: str,
    panel_b_svg_path: Path,
    stats: dict,
    rule_key: str,
    instance_label: str,
    bks: int | None,
    job_2_end_time: dict[str, int],
    dispatch_seq: list[str],
    wxd2_data: dict[str, dict] | None,
) -> str:
    """Compose the final self-contained HTML."""
    panel_b_data_uri = svg_to_data_uri(panel_b_svg_path.read_text(encoding="utf-8"))

    # BKS ratio
    if bks and bks > 0:
        bks_ratio = f"{stats['total_obj'] / bks:.4f}"
        bks_line = f'<span style="color:#666">vs BKS: {bks} (ratio={bks_ratio})</span>'
    else:
        bks_line = '<span style="color:#999">BKS: N/A</span>'

    # Legend
    legend = f"""
    <div style="display:flex; gap:16px; align-items:center;">
      <span style="display:flex;align-items:center;gap:4px;">
        <span style="display:inline-block;width:12px;height:12px;border-radius:50%;background:{EARLY_COLOR};"></span> Early (C &lt; d⁻)
      </span>
      <span style="display:flex;align-items:center;gap:4px;">
        <span style="display:inline-block;width:12px;height:12px;border-radius:50%;background:{IN_COLOR};"></span> In-window
      </span>
      <span style="display:flex;align-items:center;gap:4px;">
        <span style="display:inline-block;width:12px;height:12px;border-radius:50%;background:{TARDY_COLOR};"></span> Tardy (C &gt; d⁺)
      </span>
      <span style="display:flex;align-items:center;gap:4px;">
        <span style="display:inline-block;width:12px;height:3px;background:#cccccc;"></span> Due window
      </span>
      <span style="display:flex;align-items:center;gap:4px;">
        <span style="display:inline-block;width:2px;height:12px;background:#ff7f0e;border-left:1px dashed #ff7f0e;"></span> d̄ (mean midpoint)
      </span>
    </div>
    """

    # wxd2 partition info
    partition_info = ""
    if wxd2_data:
        early_jobs = [
            j for j in dispatch_seq if wxd2_data.get(j, {}).get("partition") == "early"
        ]
        late_jobs = [
            j for j in dispatch_seq if wxd2_data.get(j, {}).get("partition") == "late"
        ]
        partition_info = f"""
        <div style="margin-top:4px;font-size:11px;color:#666;">
          <span style="background:#e8eeff;padding:2px 6px;border-radius:3px;">early group: {len(early_jobs)} jobs</span>
          <span style="background:#fff;padding:2px 6px;border-radius:3px;border:1px solid #ddd;margin-left:4px;">late group: {len(late_jobs)} jobs</span>
        </div>
        """

    # Dispatch sequence preview (first 20 jobs)
    seq_preview = " ".join(dispatch_seq[:20])
    if len(dispatch_seq) > 20:
        seq_preview += f" ... (+{len(dispatch_seq) - 20})"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Priority Inspector — {rule_key} on {instance_label}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #fafafa; color: #333; }}
  .container {{ max-width: 1400px; margin: 0 auto; padding: 16px; }}
  h1 {{ font-size: 18px; font-weight: 600; margin-bottom: 4px; }}
  .subtitle {{ font-size: 13px; color: #666; margin-bottom: 12px; }}
  .stats-bar {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }}
  .stat-card {{ background: white; border: 1px solid #e0e0e0; border-radius: 6px; padding: 8px 16px; min-width: 120px; }}
  .stat-card .label {{ font-size: 10px; color: #888; text-transform: uppercase; letter-spacing: 0.5px; }}
  .stat-card .value {{ font-size: 20px; font-weight: 600; font-family: monospace; }}
  .stat-card .value.good {{ color: #2ca02c; }}
  .stat-card .value.warn {{ color: #d62728; }}
  .stat-card .value.info {{ color: #1f77b4; }}
  .panel {{ background: white; border: 1px solid #e0e0e0; border-radius: 8px; margin-bottom: 16px; overflow: hidden; }}
  .panel-header {{ background: #f5f5f5; padding: 8px 16px; font-size: 13px; font-weight: 600; border-bottom: 1px solid #e0e0e0; }}
  .panel-body {{ padding: 8px; overflow-x: auto; }}
  .panel-body svg {{ display: block; margin: 0 auto; }}
  .dispatch-seq {{ font-family: monospace; font-size: 11px; color: #666; background: #f9f9f9; padding: 6px 12px; border-radius: 4px; margin-bottom: 12px; word-break: break-all; }}
  .footer {{ font-size: 11px; color: #999; text-align: center; margin-top: 16px; }}
</style>
</head>
<body>
<div class="container">
  <h1>Priority Rule Inspector: {rule_key}</h1>
  <div class="subtitle">{instance_label} &middot; dispatch decode &middot; {stats["total_jobs"]} jobs</div>

  <!-- Stats Bar -->
  <div class="stats-bar">
    <div class="stat-card">
      <div class="label">Obj (wE+wT)</div>
      <div class="value info">{stats["total_obj"]:,}</div>
    </div>
    <div class="stat-card">
      <div class="label">E / T</div>
      <div class="value info">{stats["sum_earliness"]:,} / {stats["sum_tardiness"]:,}</div>
    </div>
    <div class="stat-card">
      <div class="label">Early</div>
      <div class="value info">{stats["early_count"]}</div>
    </div>
    <div class="stat-card">
      <div class="label">In-window</div>
      <div class="value good">{stats["in_count"]}</div>
    </div>
    <div class="stat-card">
      <div class="label">Tardy</div>
      <div class="value {"warn" if stats["tardy_count"] > 0 else "good"}">{stats["tardy_count"]}</div>
    </div>
    <div class="stat-card">
      <div class="label">Makespan</div>
      <div class="value">{stats["makespan"]:,}</div>
    </div>
    <div class="stat-card">
      <div class="label">vs BKS</div>
      <div class="value">{bks_line}</div>
    </div>
  </div>

  <!-- Dispatch Sequence -->
  <div style="font-size:11px;color:#888;margin-bottom:4px;">Dispatch sequence ({rule_key}):</div>
  <div class="dispatch-seq">{seq_preview}</div>

  <!-- Legend -->
  <div style="margin-bottom:12px;">
    {legend}
    {partition_info}
  </div>

  <!-- Panel A: Priority Inspector -->
  <div class="panel">
    <div class="panel-header">Panel A — Priority Inspector</div>
    <div class="panel-body">
      {panel_a_svg}
    </div>
  </div>

  <!-- Panel B: Decoded Schedule -->
  <div class="panel">
    <div class="panel-header">Panel B — Decoded Schedule (simple dispatch decode)</div>
    <div class="panel-body">
      <img src="{panel_b_data_uri}" alt="Decoded Schedule Gantt" style="max-width:100%;height:auto;">
    </div>
  </div>

  <div class="footer">
    Generated by render_priority_inspector.py &middot; {rule_key} on {instance_label}
  </div>
</div>
</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def decode_schedule(
    instance: FFcDDWParameters,
    rule_key: str,
) -> tuple:
    """Run simple-dispatch decode and return (schedule, dispatch_seq, job_2_end_time)."""
    job_sequence = dispatch_seq_job_sequence(instance, rule_key)
    dispatcher = MixedDispatcher(instance, logger=None)
    schedule = dispatcher.get_job_centric_schedule_by_sequence(job_sequence)
    schedule.make_semi_active(instance.stage_2_job_2_p_map)
    schedule.insert_idle_time(
        instance.job_2_due_window_map,
        instance.job_2_ewt_map,
        instance.job_2_twt_map,
    )
    last_stage_id = instance.stage_id_list[-1]
    job_2_end_time = {
        j: schedule.get_job_end_time(last_stage_id, j) for j in instance.job_id_list
    }
    return schedule, job_sequence, job_2_end_time


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logger = logging.getLogger(__name__)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--instance-index",
        type=int,
        required=True,
        help="Instance index from pra2017_hybrid_match.csv (e.g. 60).",
    )
    parser.add_argument(
        "--rule-key",
        type=str,
        default="wxd2",
        help="Priority rule key (default: wxd2).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output HTML path (default: output/20260625/priority_viz/<ins>_<rule>.html).",
    )
    parser.add_argument(
        "--benchmark-dir",
        type=Path,
        default=None,
        help="Benchmark directory (default: PRA2017/large/).",
    )
    parser.add_argument(
        "--hybrid-match-csv",
        type=Path,
        default=None,
        help="Path to pra2017_hybrid_match.csv.",
    )
    parser.add_argument(
        "--bks-csv",
        type=Path,
        default=BKS_TABLE,
        help="Path to pra2017_bks_table.csv.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to YAML config file.",
    )
    args = parser.parse_args()

    # Load BKS table
    bks_table = load_bks_table(args.bks_csv)

    # Resolve benchmark directory
    if args.benchmark_dir is not None:
        if not args.benchmark_dir.exists():
            raise FileNotFoundError(
                f"benchmark directory does not exist: {args.benchmark_dir}"
            )
        benchmark_dir = args.benchmark_dir
    else:
        benchmark_dir = DEFAULT_BENCHMARK_DIR

    hybrid_match = args.hybrid_match_csv or DEFAULT_HYBRID_MATCH
    loader = BenchmarkLoader(benchmark_dir, ins_index_source=hybrid_match)

    # Load instance
    instances = loader.load_all(ins_index=args.instance_index)
    if not instances:
        raise FileNotFoundError(
            f"No instance found for ins_index={args.instance_index} in {benchmark_dir}"
        )
    instance = instances[0]
    instance_label = f"ins{args.instance_index:04d} ({instance.job_count}j, {instance.stage_count}c, m={instance.last_stage_mc_count})"

    logger.info("Loaded %s", instance_label)

    # Load BKS
    bks = get_bks(args.instance_index, bks_table)
    logger.info("BKS = %s", bks if bks else "N/A")

    # Decode schedule for primary rule
    schedule, dispatch_seq, job_2_end_time = decode_schedule(instance, args.rule_key)
    stats = compute_stats(schedule, instance, dispatch_seq, job_2_end_time)

    # Compute partition + d̄ overlay data if applicable. wxd2 and wxd5 share the
    # partition/sort-key formulas; only the center d̄ differs (wxd5 floors it at
    # a last-stage completion bound). wxd7 reuses wxd5's partition but with
    # group-specific sort centers (compute_wxd7_partition).
    wxd2_data = None
    if args.rule_key in ("wxd2", "wxd5"):
        d_bar = _wxd5_d_bar(instance) if args.rule_key == "wxd5" else None
        wxd2_data = compute_wxd2_partition(instance, d_bar=d_bar)
    elif args.rule_key == "wxd7":
        wxd2_data = compute_wxd7_partition(instance)
    if wxd2_data:
        logger.info(
            "%s partition: %d early, %d late (d̄=%.1f)",
            args.rule_key,
            sum(
                1
                for j in dispatch_seq
                if wxd2_data.get(j, {}).get("partition") == "early"
            ),
            sum(
                1
                for j in dispatch_seq
                if wxd2_data.get(j, {}).get("partition") == "late"
            ),
            wxd2_data[dispatch_seq[0]]["d_bar"] if dispatch_seq else 0.0,
        )

    # Rule-aware d̄ for the Panel A / Panel B overlay, independent of the wxd2
    # partition machinery. wxd7 shares wxd5's floored partition center; every
    # other rule falls back to the plain midpoint mean (matches the key gutter).
    if args.rule_key in ("wxd5", "wxd7"):
        d_bar_value = _wxd5_d_bar(instance)
    else:
        d_bar_value = _mean_midpoint(instance)

    # Render Panel A
    panel_a_svg, *_ = render_priority_inspector_svg(
        instance,
        dispatch_seq,
        job_2_end_time,
        wxd2_data=wxd2_data,
        d_bar_value=d_bar_value,
        rule_key=args.rule_key,
    )

    # Render Panel B
    output_dir = (
        args.output.parent if args.output else Path("output/20260625/priority_viz")
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    # Overlay the same rule-aware d̄ line on Panel B, matching Panel A's overlay.
    panel_b_svg_path = render_panel_b_svg(
        instance, schedule, dispatch_seq, output_dir, d_bar=d_bar_value
    )
    logger.info("Panel B SVG -> %s", panel_b_svg_path)

    # Compose HTML
    html = compose_html(
        panel_a_svg=panel_a_svg,
        panel_b_svg_path=panel_b_svg_path,
        stats=stats,
        rule_key=args.rule_key,
        instance_label=instance_label,
        bks=bks,
        job_2_end_time=job_2_end_time,
        dispatch_seq=dispatch_seq,
        wxd2_data=wxd2_data,
    )

    # Write output
    if args.output is None:
        out_path = output_dir / f"{args.instance_index:04d}_{args.rule_key}.html"
    else:
        out_path = args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    logger.info("Wrote %s", out_path)


if __name__ == "__main__":
    main()
