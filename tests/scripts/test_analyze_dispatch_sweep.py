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
