"""Post-run RPDf comparison + PivotTable.js HTML helpers.

Builds a long-format DataFrame joining a run summary with the PRA2017 hybrid
match (filename → insIndex) and BKS table (insIndex → instance metadata +
BKS_data), then renders self-contained pivot dashboards.

The HTML template is adapted from pivottablejs (Nicolas Kruchten, MIT license,
https://github.com/nicolaskruchten/jupyter_pivottablejs). Inlined directly so
that we don't pull in the IPython runtime that the package imports at module
load.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd
from routix.io import ArtifactLayout

from ffc_ddw_sum_et._calc import rpd_f

logger = logging.getLogger(__name__)


COMPARISON_COLUMNS: tuple[str, ...] = (
    "insIndex",
    "scenarioName",
    "n",
    "c",
    "totalMcCount",
    "T",
    "R",
    "W",
    "BKS_data",
    "bestObj",
    "RPDf_BKS_data",
    "elapsedTime",
    "timelimit",
    "time%",
)

_INSTANCE_META_COLUMNS: tuple[str, ...] = (
    "n",
    "c",
    "totalMcCount",
    "T",
    "R",
    "W",
)

CP_GAP_COMPARISON_COLUMNS: tuple[str, ...] = (
    "insIndex",
    "scenarioName",
    "factor",
    "n",
    "c",
    "totalMcCount",
    "T",
    "R",
    "W",
    "cp_ub",
    "cp_lb",
    "lb_gap",
    "solver_gap",
    "cp_elapsed",
)


def _merge_instance_meta(
    df: pd.DataFrame,
    hybrid_match_csv: Path,
    bks_table_csv: Path,
    *,
    how: str = "left",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Merge df (must contain instanceName) with insIndex + instance metadata + BKS_data.

    Returns (meta_df, bks_df) where:
      - meta_df has instanceName + insIndex + _INSTANCE_META_COLUMNS
      - bks_df has insIndex + BKS_data

    ``how`` controls the join strategy: ``"left"`` keeps all rows (metadata
    columns will be NaN for unmatched rows); ``"inner"`` drops unmatched rows.
    """
    match = pd.read_csv(hybrid_match_csv, dtype={"insIndex": str})
    match["instanceName"] = match["ffc_ddw_sum_et_filename"].str.removesuffix(".txt")
    match = match[["instanceName", "insIndex"]]

    bks = pd.read_csv(bks_table_csv, dtype={"insIndex": str})
    bks = bks[["insIndex", *_INSTANCE_META_COLUMNS, "BKS_data"]]

    meta_df = df.merge(match, on="instanceName", how=how)
    bks_df = meta_df.merge(bks, on="insIndex", how=how)
    return meta_df, bks_df


def read_csr_cp_trajectory_endpoint(
    path: Path,
) -> tuple[float | None, float | None, float | None]:
    """Deprecated — csr_cp_trajectory superseded by csr_inner_obj_log_json.

    Always returns (None, None, None).
    """
    return (None, None, None)


def compute_cp_gaps(
    ub: float | None, lb: float | None
) -> tuple[float | None, float | None]:
    """Compute lb_gap and solver_gap from coarsened UB/LB.

    Rules:
      - ub is None → (None, None)  (no-solution trajectory)
      - lb is None → (None, None)  (no bound logged; gaps undefined)
      - ub == 0 and lb == 0 → (0.0, 0.0)
      - lb != 0 → lb_gap = (ub-lb)/lb
      - lb == 0 and ub > 0 → lb_gap is None (blank)
      - solver_gap = (ub-lb)/ub when ub != 0, else None
    """
    if ub is None or lb is None:
        return (None, None)
    if ub == 0 and lb == 0:
        return (0.0, 0.0)
    lb_gap = (ub - lb) / lb if lb != 0 else None
    solver_gap = (ub - lb) / ub if ub != 0 else None
    return (lb_gap, solver_gap)


def collect_cp_gap_rows(
    run_root: Path,
    *,
    init_filter: str | None = "v3",
) -> pd.DataFrame:
    """Deprecated — csr_cp_trajectory superseded by csr_inner_obj_log_json.

    Always returns an empty DataFrame.
    """
    return pd.DataFrame(
        columns=[
            "instanceName",
            "scenarioName",
            "cp_ub",
            "cp_lb",
            "cp_elapsed",
            "factor",
            "init",
        ],
    )


def build_cp_gap_comparison_df(
    run_root: Path,
    hybrid_match_csv: Path,
    bks_table_csv: Path,
    *,
    init_filter: str | None = "v3",
) -> pd.DataFrame:
    """Build the CSR CP gap comparison DataFrame.

    Joins trajectory endpoints with instance metadata via left merges,
    computes gap columns, and returns a DataFrame sorted by (insIndex, scenarioName).

    Unlike ``build_rpdf_comparison_df``, unmatched rows are kept (left join)
    so that CP gap is reported even when benchmark metadata is missing.
    """
    traj_df = collect_cp_gap_rows(run_root, init_filter=init_filter)

    if traj_df.empty:
        return pd.DataFrame(columns=[*CP_GAP_COMPARISON_COLUMNS, "BKS_data"])

    # Compute gap columns on traj_df first, then merge metadata so the whole
    # result lives on a single frame. This avoids cross-frame column assignment
    # (which would silently misalign if a left merge expanded rows, e.g. a
    # duplicate instanceName in the hybrid match).
    gaps = traj_df.apply(
        lambda row: pd.Series(
            compute_cp_gaps(row["cp_ub"], row["cp_lb"]),
            index=["lb_gap", "solver_gap"],
        ),
        axis=1,
    )
    traj_df = pd.concat([traj_df, gaps], axis=1)

    _, merged = _merge_instance_meta(traj_df, hybrid_match_csv, bks_table_csv)

    return (
        merged[list(CP_GAP_COMPARISON_COLUMNS) + ["BKS_data"]]
        .sort_values(["insIndex", "scenarioName"])
        .reset_index(drop=True)
    )


def write_cp_gap_artifacts(
    run_root: Path,
    layout: ArtifactLayout,
    hybrid_match_csv: Path,
    bks_table_csv: Path,
    *,
    init_filter: str | None = "v3",
) -> None:
    """Build the CP gap comparison CSV and PivotTable.js dashboard.

    Skips silently (writes nothing) when no CSR CP trajectory rows are found,
    e.g. on a non-CSR run. Instance metadata is left-merged, so unmatched
    instances keep their CP gap rows with empty metadata columns rather than
    being dropped.
    """
    comp_df = build_cp_gap_comparison_df(
        run_root, hybrid_match_csv, bks_table_csv, init_filter=init_filter
    )

    if comp_df.empty:
        logger.info("CP gap: no trajectory rows found; skipping artifacts.")
        return

    comp_path = layout.artifact_path("cp_gap_comparison_csv")
    comp_df.to_csv(comp_path, index=False)
    logger.info("Wrote %s (%d rows)", comp_path, len(comp_df))

    dashboard_path = layout.artifact_path("cp_gap_dashboard")
    # Default to solver_gap: it is bounded to [0, 1] so the heatmap reads
    # cleanly. lb_gap = (UB-LB)/LB can explode when LB is small, so it is
    # kept as a selectable value rather than the default.
    initial_state = {
        "rows": ["scenarioName", "R"],
        "cols": ["T"],
        "vals": ["solver_gap"],
        "aggregatorName": "Average",
        "rendererName": "Heatmap",
    }
    write_pivot_html(
        comp_df,
        dashboard_path,
        initial_state=initial_state,
        aggregators_js=PERCENT_AGGREGATORS_JS,
        title="CP gap Pivot",
    )


def build_rpdf_comparison_df(
    summary_df: pd.DataFrame,
    hybrid_match_csv: Path,
    bks_table_csv: Path,
    *,
    timelimit_factor: float = 0.09,
) -> pd.DataFrame:
    """Join summary with hybrid_match + bks_table; compute RPDf and time%.

    Returns a long-format DataFrame with columns from ``COMPARISON_COLUMNS``,
    sorted by (insIndex, scenarioName). ``insIndex`` stays a zero-padded
    string. Rows where ``error`` is set are dropped, as are rows whose
    instanceName has no insIndex match. Rows with NaN ``bestObj`` (e.g. LB-only
    runs) are kept so ``time%`` is reported; ``RPDf_BKS_data`` is NaN for them.
    """
    df = summary_df
    if "error" in df.columns:
        df = df[df["error"].isna() | (df["error"] == "")]

    _, merged = _merge_instance_meta(df, hybrid_match_csv, bks_table_csv, how="inner")

    merged["RPDf_BKS_data"] = [
        rpd_f(b, k) for b, k in zip(merged["bestObj"], merged["BKS_data"], strict=True)
    ]
    merged["timelimit"] = merged["n"] * merged["c"] * timelimit_factor
    merged["time%"] = merged["elapsedTime"] / merged["timelimit"]

    return (
        merged[list(COMPARISON_COLUMNS)]
        .sort_values(["insIndex", "scenarioName"])
        .reset_index(drop=True)
    )


_PIVOT_TEMPLATE = """\
<!DOCTYPE html>
<html>
    <head>
        <meta charset="UTF-8">
        <title>%(title)s</title>

        <link rel="stylesheet" type="text/css" href="https://cdnjs.cloudflare.com/ajax/libs/c3/0.4.11/c3.min.css">
        <script type="text/javascript" src="https://cdnjs.cloudflare.com/ajax/libs/d3/3.5.5/d3.min.js"></script>
        <script type="text/javascript" src="https://cdnjs.cloudflare.com/ajax/libs/c3/0.4.11/c3.min.js"></script>
        <script type="text/javascript" src="https://cdnjs.cloudflare.com/ajax/libs/jquery/1.11.2/jquery.min.js"></script>
        <script type="text/javascript" src="https://cdnjs.cloudflare.com/ajax/libs/jqueryui/1.11.4/jquery-ui.min.js"></script>
        <script type="text/javascript" src="https://cdnjs.cloudflare.com/ajax/libs/jquery-csv/0.71/jquery.csv-0.71.min.js"></script>

        <link rel="stylesheet" type="text/css" href="https://cdnjs.cloudflare.com/ajax/libs/pivottable/2.19.0/pivot.min.css">
        <script type="text/javascript" src="https://cdnjs.cloudflare.com/ajax/libs/pivottable/2.19.0/pivot.min.js"></script>
        <script type="text/javascript" src="https://cdnjs.cloudflare.com/ajax/libs/pivottable/2.19.0/d3_renderers.min.js"></script>
        <script type="text/javascript" src="https://cdnjs.cloudflare.com/ajax/libs/pivottable/2.19.0/c3_renderers.min.js"></script>
        <script type="text/javascript" src="https://cdnjs.cloudflare.com/ajax/libs/pivottable/2.19.0/export_renderers.min.js"></script>

        <style>body {font-family: Verdana;}</style>
    </head>
    <body>
        <script type="text/javascript">
            $(function(){
                %(aggregators_js)s
                $("#output").pivotUI(
                    $.csv.toArrays($("#output").text()),
                    $.extend({
                        renderers: $.extend(
                            $.pivotUtilities.renderers,
                            $.pivotUtilities.c3_renderers,
                            $.pivotUtilities.d3_renderers,
                            $.pivotUtilities.export_renderers
                        ),
                        aggregators: aggregators,
                        hiddenAttributes: [""]
                    }, %(initial_state)s)
                ).show();
            });
        </script>
        <div id="output" style="display: none;">%(csv)s</div>
    </body>
</html>
"""

DEFAULT_AGGREGATORS_JS = "var aggregators = $.pivotUtilities.aggregators;"

PERCENT_AGGREGATORS_JS = """\
var pctFmt = $.pivotUtilities.numberFormat(
                    {digitsAfterDecimal: 1, scaler: 100, suffix: "%"}
                );
                var tpl = $.pivotUtilities.aggregatorTemplates;
                var aggregators = $.extend({}, $.pivotUtilities.aggregators, {
                    "Sum":     tpl.sum(pctFmt),
                    "Average": tpl.average(pctFmt),
                    "Median":  tpl.median(pctFmt),
                    "Minimum": tpl.min(pctFmt),
                    "Maximum": tpl.max(pctFmt),
                    "First":   tpl.first(pctFmt),
                    "Last":    tpl.last(pctFmt)
                });"""

WIN_TIE_AGGREGATORS_JS = """\
var winTieAgg = function(attrs) {
                    var wAttr = attrs[0], tAttr = attrs[1];
                    return function() {
                        return {
                            wonSum: 0,
                            tiedSum: 0,
                            push: function(record) {
                                var w = parseFloat(record[wAttr]);
                                var t = parseFloat(record[tAttr]);
                                if (!isNaN(w)) this.wonSum  += w;
                                if (!isNaN(t)) this.tiedSum += t;
                            },
                            value: function() { return [this.wonSum, this.tiedSum]; },
                            format: function(x) { return "(" + x[0] + ", " + x[1] + ")"; },
                            numInputs: (null != wAttr && null != tAttr) ? 0 : 2
                        };
                    };
                };
                var aggregators = $.extend({}, $.pivotUtilities.aggregators, {
                    "Win / Tie sum": winTieAgg
                });"""


def write_pivot_html(
    df: pd.DataFrame,
    outfile: Path,
    *,
    initial_state: dict,
    aggregators_js: str = DEFAULT_AGGREGATORS_JS,
    title: str = "Pivot",
) -> None:
    """Render ``df`` as a self-contained PivotTable.js HTML at ``outfile``."""
    payload = _PIVOT_TEMPLATE % {
        "title": title,
        "aggregators_js": aggregators_js,
        "initial_state": json.dumps(initial_state),
        "csv": df.to_csv(index=False),
    }
    outfile.write_text(payload, encoding="utf8")


def write_post_run_pivot_artifacts(
    summary_csv: Path,
    layout: ArtifactLayout,
    hybrid_match_csv: Path,
    bks_table_csv: Path,
) -> None:
    """Build the comparison CSV and three pivot HTMLs at run scope.

    Skipped silently when ``hybrid_match_csv`` or ``bks_table_csv`` does not
    exist.
    """
    if not hybrid_match_csv.exists():
        logger.info("Skipping post-run pivot artifacts: %s not found", hybrid_match_csv)
        return
    if not bks_table_csv.exists():
        logger.info("Skipping post-run pivot artifacts: %s not found", bks_table_csv)
        return

    summary_df = pd.read_csv(summary_csv)
    comp_df = build_rpdf_comparison_df(summary_df, hybrid_match_csv, bks_table_csv)

    comp_path = layout.artifact_path("rpdf_comparison_csv")
    comp_df.to_csv(comp_path, index=False)
    logger.info("Wrote %s (%d rows)", comp_path, len(comp_df))

    common_axes = {"rows": ["scenarioName", "R"], "cols": ["T"]}

    write_pivot_html(
        comp_df,
        layout.artifact_path("rpdf_dashboard"),
        initial_state={
            **common_axes,
            "vals": ["RPDf_BKS_data"],
            "aggregatorName": "Average",
            "rendererName": "Heatmap",
        },
        aggregators_js=PERCENT_AGGREGATORS_JS,
        title="RPDf Pivot",
    )

    win_tie_df = comp_df.copy()
    win_tie_df["won"] = (win_tie_df["bestObj"] < win_tie_df["BKS_data"]).astype(int)
    win_tie_df["tied"] = (win_tie_df["bestObj"] == win_tie_df["BKS_data"]).astype(int)
    win_tie_df = win_tie_df.drop(columns=["bestObj", "BKS_data", "RPDf_BKS_data"])
    write_pivot_html(
        win_tie_df,
        layout.artifact_path("win_tie_dashboard"),
        initial_state={
            **common_axes,
            "vals": ["won", "tied"],
            "aggregatorName": "Win / Tie sum",
            "rendererName": "Table",
        },
        aggregators_js=WIN_TIE_AGGREGATORS_JS,
        title="Win/Tie Pivot",
    )

    write_pivot_html(
        comp_df,
        layout.artifact_path("time_p_dashboard"),
        initial_state={
            **common_axes,
            "vals": ["time%"],
            "aggregatorName": "Average",
            "rendererName": "Heatmap",
        },
        aggregators_js=PERCENT_AGGREGATORS_JS,
        title="time% Pivot",
    )
