"""Unit tests for the named-combo scoring / gain logic in the sweep analyzer.

The gain number (baseline vs chosen) feeds the paper, so its arithmetic and the
per-n breakdown are pinned here on a tiny synthetic sweep with known values.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

# scripts/ is not an importable package; load the module by path.
_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "analyze_dispatch_sweep.py"
_spec = importlib.util.spec_from_file_location("analyze_dispatch_sweep", _SCRIPT)
assert _spec and _spec.loader
A = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(A)


def _synthetic_df() -> pd.DataFrame:
    """2 instances x 3 methods, distinct n per instance, known objectives.

    obj:        A    B    C
      i0(n=50)  10   20   5
      i1(n=100) 8    4    30
    """
    rows = [
        ("i0", "A", 50, 10.0),
        ("i0", "B", 50, 20.0),
        ("i0", "C", 50, 5.0),
        ("i1", "A", 100, 8.0),
        ("i1", "B", 100, 4.0),
        ("i1", "C", 100, 30.0),
    ]
    return pd.DataFrame(rows, columns=["insIndex", "scenarioName", "n", "bestObj"])


def test_parse_combo_splits_and_strips() -> None:
    assert A.parse_combo("A, B ,C") == ("A", "B", "C")


def test_parse_combo_empty_raises() -> None:
    with pytest.raises(ValueError, match="empty combo spec"):
        A.parse_combo("  ,  ")


def test_score_combo_by_n_overall_and_per_n() -> None:
    df = _synthetic_df()
    overall, per_n = A.score_combo_by_n(df, "bestObj", ("A", "B"))
    # oracle (per-instance min of A,B): i0=10, i1=4 -> mean 7
    assert overall == pytest.approx(7.0)
    assert per_n.to_dict() == {50: pytest.approx(10.0), 100: pytest.approx(4.0)}


def test_score_combo_by_n_unknown_name_raises() -> None:
    df = _synthetic_df()
    with pytest.raises(ValueError, match="scenarios not in sweep"):
        A.score_combo_by_n(df, "bestObj", ("A", "ZZZ"))


def test_oracle_gain_arithmetic() -> None:
    df = _synthetic_df()
    base, _ = A.score_combo_by_n(df, "bestObj", ("A", "B"))  # 7.0
    chosen, _ = A.score_combo_by_n(df, "bestObj", ("A", "B", "C"))
    # adding C: i0 min(10,20,5)=5, i1 min(8,4,30)=4 -> mean 4.5
    assert chosen == pytest.approx(4.5)
    gain_abs = base - chosen
    assert gain_abs == pytest.approx(2.5)
    assert gain_abs / base * 100 == pytest.approx(35.7142857)


# --------------------------------------------------------------------------- #
# v2 priority-key and best_unit_combos (paired-direction) tests
# --------------------------------------------------------------------------- #


def _paired_df() -> pd.DataFrame:
    """2 instances x 2 priorities x 2 directions = 4 methods.

    obj:              sd_A  rd_A  sd_B  rd_B
      i0(n=50)        10    20    30    40
      i1(n=100)       8     4     50    60
    """
    rows = [
        ("i0", "sd_A", 50, 10.0),
        ("i0", "rd_A", 50, 20.0),
        ("i0", "sd_B", 50, 30.0),
        ("i0", "rd_B", 50, 40.0),
        ("i1", "sd_A", 100, 8.0),
        ("i1", "rd_A", 100, 4.0),
        ("i1", "sd_B", 100, 50.0),
        ("i1", "rd_B", 100, 60.0),
    ]
    return pd.DataFrame(rows, columns=["insIndex", "scenarioName", "n", "bestObj"])


def test_priority_key_strips_direction() -> None:
    assert A.priority_key("sd_wxd2") == "wxd2"
    assert A.priority_key("rd_edd") == "edd"
    assert A.priority_key("foo") == "foo"
    assert A.priority_key("sd_due2_weight_pos") == "due2_weight_pos"
    assert A.priority_key("rd_lsl") == "lsl"


def test_best_unit_combos_pairs_directions() -> None:
    df = _paired_df()
    mat = A.metric_matrix(df, "bestObj")
    # k=1 priority: each priority's oracle = min of its two direction columns
    #   A: i0 min(10,20)=10, i1 min(8,4)=4 -> mean 7.0
    #   B: i0 min(30,40)=30, i1 min(50,60)=50 -> mean 40.0
    combos = A.best_unit_combos(mat, 1, "priority")
    assert len(combos) == 2
    assert combos[0][0] == ("A",)
    assert combos[0][1] == pytest.approx(7.0)
    assert combos[1][0] == ("B",)
    assert combos[1][1] == pytest.approx(40.0)

    # k=2 priority: union of all 4 columns (both directions of A and B).
    #   i0 min(10,20,30,40)=10, i1 min(8,4,50,60)=4 -> mean 7.0
    combos2 = A.best_unit_combos(mat, 2, "priority")
    assert combos2[0][0] == ("A", "B")
    assert combos2[0][1] == pytest.approx(7.0)


def test_best_unit_combos_scenario_mode_unchanged() -> None:
    """unit='scenario' should behave identically to the existing best_combos."""
    df = _paired_df()
    mat = A.metric_matrix(df, "bestObj")
    combos_scenario = A.best_unit_combos(mat, 2, "scenario")
    combos_orig = A.best_combos(mat, 2, top=5)
    # Both should produce the same combos and values (same unit = individual method)
    assert len(combos_scenario) == len(combos_orig)
    for (c1, v1), (c2, v2) in zip(combos_scenario, combos_orig):
        assert c1 == c2
        assert v1 == pytest.approx(v2)


def test_report_unit_priority_no_crash() -> None:
    """Smoke test: report with unit=priority runs without error."""
    df = _paired_df()
    # Should not raise
    A.report(
        df,
        metric_col="bestObj",
        metric_label="absolute objective",
        combo_sizes=[1, 2],
        top=3,
        method_prefix=None,
        unit="priority",
    )


# --------------------------------------------------------------------------- #
# Ablation path: priority units that carry a SINGLE direction.
#
# The (sd)-only / (rd)-only ablation arms run `--unit priority --methods sd_`,
# i.e. each priority key maps to exactly one column. The oracle must then reduce
# to that single column, and a k-priority combo must equal the plain k-method
# combo. Pinned because the paper's ablation gain depends on this reduction.
# --------------------------------------------------------------------------- #
def _single_direction_matrix() -> pd.DataFrame:
    """2 instances x {sd_A, sd_B} only (one direction per priority).

              sd_A  sd_B
    i0         10    20
    i1          8     4
    """
    rows = [
        ("i0", "sd_A", 50, 10.0),
        ("i0", "sd_B", 50, 20.0),
        ("i1", "sd_A", 100, 8.0),
        ("i1", "sd_B", 100, 4.0),
    ]
    df = pd.DataFrame(rows, columns=["insIndex", "scenarioName", "n", "bestObj"])
    return A.metric_matrix(df, "bestObj")


def test_best_unit_combos_single_direction_reduces_to_column() -> None:
    mat = _single_direction_matrix()
    # k=1 priority: each priority is its lone column.
    #   A: mean(10, 8) = 9.0 ; B: mean(20, 4) = 12.0
    combos = A.best_unit_combos(mat, 1, "priority")
    assert combos[0] == (("A",), pytest.approx(9.0))
    assert combos[1] == (("B",), pytest.approx(12.0))
    # k=2 priority over single-direction units == plain 2-method best_combos.
    #   i0 min(10,20)=10, i1 min(8,4)=4 -> mean 7.0
    assert A.best_unit_combos(mat, 2, "priority")[0] == (
        ("A", "B"),
        pytest.approx(7.0),
    )


def test_best_unit_combos_k_exceeds_priority_count_returns_empty() -> None:
    # _paired_df has 2 priorities (A, B); k=3 has no priority combo.
    mat = A.metric_matrix(_paired_df(), "bestObj")
    assert A.best_unit_combos(mat, 3, "priority") == []


def test_best_unit_combos_top_truncates_priority_mode() -> None:
    mat = A.metric_matrix(_paired_df(), "bestObj")
    # 2 priorities -> 2 single-priority combos; top=1 keeps only the best.
    combos = A.best_unit_combos(mat, 1, "priority", top=1)
    assert len(combos) == 1
    assert combos[0][0] == ("A",)  # A (7.0) beats B (40.0)
