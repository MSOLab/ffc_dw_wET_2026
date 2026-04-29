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


def _rpdf(best_obj: float, bks: float) -> float:
    denom = best_obj + bks
    return 0.0 if denom == 0 else 2 * (best_obj - bks) / denom


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
    string. Rows where ``error`` is set or ``bestObj`` is NaN are dropped, as
    are rows whose instanceName has no insIndex match.
    """
    df = summary_df
    if "error" in df.columns:
        df = df[df["error"].isna() | (df["error"] == "")]
    df = df.dropna(subset=["bestObj"])

    match = pd.read_csv(hybrid_match_csv, dtype={"insIndex": str})
    match["instanceName"] = match["ffc_ddw_sum_et_filename"].str.removesuffix(".txt")
    match = match[["instanceName", "insIndex"]]

    bks = pd.read_csv(bks_table_csv, dtype={"insIndex": str})
    bks = bks[["insIndex", *_INSTANCE_META_COLUMNS, "BKS_data"]]

    merged = df.merge(match, on="instanceName", how="inner").merge(
        bks, on="insIndex", how="inner"
    )
    merged["RPDf_BKS_data"] = [
        _rpdf(b, k) for b, k in zip(merged["bestObj"], merged["BKS_data"], strict=True)
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
