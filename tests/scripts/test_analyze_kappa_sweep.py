"""Tests for the kappa-sweep merge/analysis helpers.

Two of these pin defects that were live in review:

- ``aggregate`` used to round to 4 dp before the caller ran ``idxmin()``, which
  tied ``p60`` with ``kappa_0.005`` on the T=0.6 slice (true gap: 1e-05) and let
  row order pick the winner.
- ``apply_slice`` compares floats with ``==``. If a slice key ever stops matching
  exactly it must fail loudly rather than silently aggregating zero rows.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

# scripts/ is not an importable package; load the module by path.
_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "20260706"
    / "analyze_kappa_sweep.py"
)
_spec = importlib.util.spec_from_file_location("analyze_kappa_sweep", _SCRIPT)
assert _spec and _spec.loader
A = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(A)


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("kappa_0.005", 0.005),
        ("kappa_0.002", 0.002),
        ("p60", None),
        ("kappa_", None),
        ("prefix_kappa_0.005", None),
    ],
)
def test_kappa_of(scenario: str, expected: float | None) -> None:
    """Given a scenario name, when parsed, then only `kappa_<float>` yields a kappa."""
    assert A.kappa_of(scenario) == expected


def test_slugify_makes_a_filename_safe_label() -> None:
    """Given a slice label, when slugified, then it carries no path-hostile chars."""
    assert A.slugify("T=0.6,R=0.2") == "T0p6_R0p2"
    assert A.slugify("all") == "all"


def _grid() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "instanceName": ["a", "b", "c", "d"],
            "T": [0.6, 0.6, 0.2, 0.2],
            "R": [0.2, 1.0, 0.2, 1.0],
        }
    )


def test_apply_slice_filters_every_key() -> None:
    """Given a two-key spec, when applied, then both keys narrow the frame."""
    assert list(A.apply_slice(_grid(), {"T": 0.6}).instanceName) == ["a", "b"]
    assert list(A.apply_slice(_grid(), {"T": 0.6, "R": 0.2}).instanceName) == ["a"]


def test_apply_slice_empty_spec_is_the_full_grid() -> None:
    """Given the `all` slice (empty spec), when applied, then nothing is dropped."""
    assert len(A.apply_slice(_grid(), {})) == 4


def test_apply_slice_raises_when_a_key_matches_nothing() -> None:
    """Given a spec no row matches, then it raises instead of aggregating zero rows.

    Guards the float `==` comparison: a silent empty frame would produce a NaN
    mean that reads like a real result.
    """
    with pytest.raises(ValueError, match="matched no instances"):
        A.apply_slice(_grid(), {"T": 0.7})


def test_aggregate_keeps_full_precision_so_idxmin_is_order_independent() -> None:
    """Given two scenarios 1e-05 apart, when aggregated, then idxmin ignores row order.

    Rounding to 4 dp inside aggregate() tied these and let the first row win.
    """
    df = pd.DataFrame(
        {
            "scenarioName": ["p60"] * 2 + ["kappa_0.005"] * 2,
            "instanceName": ["a", "b", "a", "b"],
            "RPDf_BKS_data": [0.14070, 0.14071, 0.14072, 0.14073],
            "bestObj": [1.0] * 4,
            "elapsedTime": [1.0] * 4,
        }
    )
    agg = A.aggregate(df, ["scenarioName"])

    # p60 mean 0.140705 < kappa mean 0.140725; both round to 0.1407.
    assert agg.mean_RPDf.round(4).nunique() == 1, "precondition: they tie at 4 dp"

    for ascending in (True, False):
        ordered = agg.sort_values("scenarioName", ascending=ascending).reset_index(
            drop=True
        )
        winner = ordered.loc[ordered.mean_RPDf.idxmin()].scenarioName
        assert winner == "p60"


def test_load_runs_rejects_a_scenario_present_in_two_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given the same scenario in two runs, then merging refuses rather than double-counting."""
    bench = pd.DataFrame({"instanceName": ["ins1"], "BKS_data": [100.0]})
    monkeypatch.setattr(A._bri, "load_benchmark_tables", lambda: bench)

    for ts in ("20260101T000000_000001", "20260102T000000_000002"):
        run_dir = tmp_path / ts
        run_dir.mkdir()
        pd.DataFrame(
            {"instanceName": ["ins1"], "scenarioName": ["p60"], "bestObj": [110.0]}
        ).to_csv(run_dir / f"{ts}_summary.csv", index=False)

    with pytest.raises(ValueError, match="more than one run"):
        A.load_runs(sorted(tmp_path.iterdir()))


def test_load_runs_computes_rpdf_and_keeps_bks_zero_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given BKS=0 rows, then they are scored (not dropped): +2 when bestObj>0, else 0."""
    bench = pd.DataFrame(
        {"instanceName": ["solved", "pinned", "normal"], "BKS_data": [0.0, 0.0, 100.0]}
    )
    monkeypatch.setattr(A._bri, "load_benchmark_tables", lambda: bench)

    ts = "20260101T000000_000001"
    run_dir = tmp_path / ts
    run_dir.mkdir()
    pd.DataFrame(
        {
            "instanceName": ["solved", "pinned", "normal"],
            "scenarioName": ["p60"] * 3,
            "bestObj": [0.0, 50.0, 100.0],
        }
    ).to_csv(run_dir / f"{ts}_summary.csv", index=False)

    merged = A.load_runs([run_dir]).set_index("instanceName")

    assert len(merged) == 3, "BKS=0 rows must not be dropped"
    assert merged.loc["solved", "RPDf_BKS_data"] == 0.0
    assert merged.loc["pinned", "RPDf_BKS_data"] == 2.0
    assert merged.loc["normal", "RPDf_BKS_data"] == 0.0
