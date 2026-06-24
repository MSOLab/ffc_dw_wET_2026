"""Post-hoc method-comparison analysis for the dispatch-sequence sweep runs.

Reads the per-run ``*_rpdf_comparison.csv`` (one row per instance x scenario)
and answers the recurring questions about dispatch methods. Two metrics are
supported, both minimization (lower is better):

  * ``rpdf`` (default) -- ``RPDf_BKS_data``, relative percentage deviation vs
    BKS. Scale-free, so every instance contributes equally; this is the fair
    metric for "which method is better overall".
  * ``obj`` -- ``bestObj``, the absolute weighted E+T objective. Useful for a
    total-cost view, but the mean is dominated by the large instances
    (n up to 200) because objective magnitude scales with instance size. Read
    ``obj`` rankings as "total cost", not "per-instance quality".

Questions answered:

  (1) Which single method has the best mean metric?
        - overall                       -> ``mean_by_method``
        - over a (T) slice              -> ``--t 0.6``
        - over a (T, R) slice           -> ``--t 0.6 --r 0.2``
  (2) Which method *pair* is best when, per instance, we keep the better of
      the two (oracle / virtual-best-solver)?   -> ``best_combos(k=2)``
  (3) Which method *triple* is best under the same per-instance-best rule?
        -> ``best_combos(k=3)``

The oracle combination metric for a method subset S is::

    mean_over_instances( min_{m in S} metric[instance, m] )

i.e. for each instance you are allowed to pick whichever method in S did best,
then average across instances. Minimizing this picks the most *complementary*
set, not just the set of individually-best methods.

Restrict the candidate methods with ``--methods`` (a scenarioName prefix), e.g.
``--methods sd_`` keeps only simple-dispatch scenarios -- handy for a
like-for-like comparison against a paper that only uses one decode family.

Usage
-----
    # answer all of (1-1), (1-2), (1-3), (2), (3) for the run below (RPDf)
    uv run python scripts/analyze_dispatch_sweep.py \
        output/20260624/20260624T153836_407384

    # absolute objective, (T, R) slice, simple-dispatch methods only, top-10
    uv run python scripts/analyze_dispatch_sweep.py <run_dir> \
        --metric obj --t 0.6 --r 0.2 --methods sd_ --top 10

The positional argument is the run directory (the timestamp folder); the
``*_rpdf_comparison.csv`` inside it is located automatically. A direct path to a
CSV also works.
"""

from __future__ import annotations

import argparse
from itertools import combinations
from pathlib import Path

import pandas as pd

METHOD_COL = "scenarioName"
INSTANCE_COL = "insIndex"

# metric key -> (csv column, human label). Both are minimization metrics.
METRICS: dict[str, tuple[str, str]] = {
    "rpdf": ("RPDf_BKS_data", "RPDf vs BKS"),
    "obj": ("bestObj", "absolute objective"),
}


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def resolve_rpdf_csv(path: Path) -> Path:
    """Accept either the run directory or a direct CSV path."""
    if path.is_dir():
        matches = sorted(path.glob("*_rpdf_comparison.csv"))
        if not matches:
            raise FileNotFoundError(f"No *_rpdf_comparison.csv under {path}")
        if len(matches) > 1:
            raise ValueError(f"Multiple rpdf_comparison CSVs under {path}: {matches}")
        return matches[0]
    if path.is_file():
        return path
    raise FileNotFoundError(path)


def load_rpdf(run_path: Path) -> pd.DataFrame:
    """Load the rpdf_comparison CSV (no per-metric validation here)."""
    return pd.read_csv(resolve_rpdf_csv(run_path))


def _check_metric(df: pd.DataFrame, metric_col: str) -> None:
    """Fail loudly on an unknown column or a partial (null) metric column."""
    if metric_col not in df.columns:
        raise KeyError(f"Column {metric_col!r} not in CSV; have {list(df.columns)}")
    missing = df[metric_col].isna().sum()
    if missing:
        raise ValueError(
            f"{missing} rows have null {metric_col}; sweep is incomplete, refusing "
            "to average over a partial result set."
        )


def filter_slice(
    df: pd.DataFrame, t: float | None = None, r: float | None = None
) -> pd.DataFrame:
    """Restrict to a tardiness-factor T and/or due-range R slice."""
    if t is not None:
        df = df[df["T"] == t]
    if r is not None:
        df = df[df["R"] == r]
    if df.empty:
        raise ValueError(f"No rows for T={t}, R={r}")
    return df


# --------------------------------------------------------------------------- #
# (1) single-method ranking
# --------------------------------------------------------------------------- #
def mean_by_method(df: pd.DataFrame, metric_col: str) -> pd.Series:
    """Mean metric per method, ascending (best first)."""
    _check_metric(df, metric_col)
    return df.groupby(METHOD_COL)[metric_col].mean().sort_values()


# --------------------------------------------------------------------------- #
# (2, 3) oracle combination ranking
# --------------------------------------------------------------------------- #
def metric_matrix(
    df: pd.DataFrame, metric_col: str, method_prefix: str | None = None
) -> pd.DataFrame:
    """Pivot to an [instance x method] matrix of the chosen metric.

    Every method must cover every instance in the slice (a complete sweep), so
    the pivot has no holes; raise otherwise rather than silently dropping rows.
    ``method_prefix`` keeps only scenarios whose name starts with it.
    """
    _check_metric(df, metric_col)
    mat = df.pivot(index=INSTANCE_COL, columns=METHOD_COL, values=metric_col)
    if method_prefix:
        keep = [c for c in mat.columns if c.startswith(method_prefix)]
        if not keep:
            raise ValueError(f"No methods match prefix {method_prefix!r}")
        mat = mat[keep]
    if mat.isna().any().any():
        bad = mat.columns[mat.isna().any()].tolist()
        raise ValueError(f"Incomplete instance coverage for methods: {bad}")
    return mat


def best_combos(
    mat: pd.DataFrame, k: int, top: int = 5
) -> list[tuple[tuple[str, ...], float]]:
    """Rank every k-method combination by oracle mean (best first).

    Oracle value of a combo = mean over instances of the per-instance minimum
    of the combo's methods. Returns the ``top`` best combos.
    """
    methods = list(mat.columns)
    values = mat.to_numpy()  # [n_instances, n_methods]
    col_index = {m: i for i, m in enumerate(methods)}

    scored: list[tuple[tuple[str, ...], float]] = []
    for combo in combinations(methods, k):
        cols = [col_index[m] for m in combo]
        oracle_mean = float(values[:, cols].min(axis=1).mean())
        scored.append((combo, oracle_mean))
    scored.sort(key=lambda x: x[1])
    return scored[:top]


def oracle_value(mat: pd.DataFrame, combo: tuple[str, ...]) -> float:
    """Oracle mean (per-instance best) of a *specific* combo.

    Use this to score a named baseline (e.g. the 2017 paper's triple) on the
    same footing as ``best_combos``.
    """
    return float(mat[list(combo)].min(axis=1).mean())


def marginal_contribution(mat: pd.DataFrame, base: tuple[str, ...]) -> pd.Series:
    """How much each *additional* method would improve a fixed base combo.

    For a base set B, returns, per candidate method m not in B, the oracle mean
    of B ∪ {m}. Useful for reading *why* the best triple extends the best pair.
    """
    methods = [m for m in mat.columns if m not in base]
    base_cols = list(base)
    out = {}
    for m in methods:
        sub = mat[base_cols + [m]]
        out[m] = float(sub.min(axis=1).mean())
    return pd.Series(out).sort_values()


# --------------------------------------------------------------------------- #
# Named-combo scoring + gain (baseline vs chosen)
# --------------------------------------------------------------------------- #
def parse_combo(spec: str) -> tuple[str, ...]:
    """Parse a comma-separated scenario list into a combo tuple."""
    names = tuple(s.strip() for s in spec.split(",") if s.strip())
    if not names:
        raise ValueError(f"empty combo spec: {spec!r}")
    return names


def _validate_names(df: pd.DataFrame, names: tuple[str, ...]) -> None:
    present = set(df[METHOD_COL].unique())
    missing = [m for m in names if m not in present]
    if missing:
        raise ValueError(f"scenarios not in sweep: {missing}; have {sorted(present)}")


def score_combo_by_n(
    df: pd.DataFrame, metric_col: str, combo: tuple[str, ...]
) -> tuple[float, pd.Series]:
    """Oracle mean (per-instance best) of an explicit combo: overall + per-n.

    Per-n breakdown matters for the ``obj`` metric, whose overall mean is
    dominated by large instances; reading the gain per size avoids reporting a
    size-skewed single number.
    """
    _check_metric(df, metric_col)
    _validate_names(df, combo)
    overall = oracle_value(metric_matrix(df, metric_col), combo)
    per_n = {
        int(n_val): oracle_value(metric_matrix(sub, metric_col), combo)
        for n_val, sub in df.groupby("n")
    }
    return overall, pd.Series(per_n).sort_index()


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def _fmt_num(val: float) -> str:
    """Compact for fractional metrics, thousands-grouped for large objectives."""
    return f"{val:.4f}" if abs(val) < 1000 else f"{val:,.0f}"


def _fmt_series(s: pd.Series, n: int | None = None) -> str:
    s = s if n is None else s.head(n)
    width = max(len(str(i)) for i in s.index)
    return "\n".join(f"  {idx:<{width}}  {_fmt_num(val)}" for idx, val in s.items())


def _fmt_combos(combos: list[tuple[tuple[str, ...], float]]) -> str:
    return "\n".join(f"  {_fmt_num(v)}  {' + '.join(c)}" for c, v in combos)


def _fmt_per_n(s: pd.Series) -> str:
    return "  ".join(f"n={int(idx)}:{_fmt_num(val)}" for idx, val in s.items())


def gain_report(
    df: pd.DataFrame,
    metric_col: str,
    metric_label: str,
    baseline: tuple[str, ...],
    chosen: tuple[str, ...] | None,
) -> None:
    """Score an explicit baseline combo and (optionally) the chosen combo,
    printing the gain (baseline - chosen) overall and per-n."""
    base_overall, base_per_n = score_combo_by_n(df, metric_col, baseline)
    print(f"\n{'=' * 70}")
    print(f"Baseline vs chosen  ({metric_label}, oracle per-instance best)")
    print("=" * 70)
    print(f"  baseline = {' + '.join(baseline)}")
    print(f"    overall: {_fmt_num(base_overall)}    [{_fmt_per_n(base_per_n)}]")
    if chosen is None:
        return
    ch_overall, ch_per_n = score_combo_by_n(df, metric_col, chosen)
    print(f"  chosen   = {' + '.join(chosen)}")
    print(f"    overall: {_fmt_num(ch_overall)}    [{_fmt_per_n(ch_per_n)}]")

    gain_abs = base_overall - ch_overall
    gain_pct = gain_abs / base_overall * 100 if base_overall else float("nan")
    per_n_gain = base_per_n - ch_per_n
    per_n_pct = per_n_gain / base_per_n * 100
    print("\n  >> gain (baseline - chosen, positive = chosen better):")
    print(f"     overall: {_fmt_num(gain_abs)}  ({gain_pct:+.2f}%)")
    for idx in per_n_gain.index:
        print(
            f"       n={int(idx)}: {_fmt_num(per_n_gain[idx])}  "
            f"({per_n_pct[idx]:+.2f}%)"
        )


def report(
    df: pd.DataFrame,
    metric_col: str,
    metric_label: str,
    combo_sizes: list[int],
    top: int,
    method_prefix: str | None = None,
) -> None:
    n_inst = df[INSTANCE_COL].nunique()
    scope = f", methods={method_prefix}*" if method_prefix else ""
    print(f"\n{'=' * 70}")
    print(
        f"Single-method mean {metric_label}  (instances={n_inst}{scope}, lower=better)"
    )
    print("=" * 70)
    ranking = mean_by_method(df, metric_col)
    if method_prefix:
        ranking = ranking[[m for m in ranking.index if m.startswith(method_prefix)]]
    print(_fmt_series(ranking))
    print(
        f"\n  >> best single method: {ranking.index[0]}  ({_fmt_num(ranking.iloc[0])})"
    )

    mat = metric_matrix(df, metric_col, method_prefix=method_prefix)
    for k in combo_sizes:
        if k < 2 or k > mat.shape[1]:
            continue
        print(f"\n{'=' * 70}")
        print(f"Best {k}-method combinations  (oracle: per-instance best of {k})")
        print("=" * 70)
        combos = best_combos(mat, k, top=top)
        print(_fmt_combos(combos))
        print(
            f"\n  >> best {k}-combo: {' + '.join(combos[0][0])}  "
            f"({_fmt_num(combos[0][1])})"
        )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("run", type=Path, help="run directory or rpdf_comparison.csv path")
    p.add_argument(
        "--metric",
        choices=sorted(METRICS),
        default="rpdf",
        help="ranking metric: rpdf (default, scale-free) or obj (absolute, "
        "size-dominated)",
    )
    p.add_argument("--t", type=float, default=None, help="filter tardiness factor T")
    p.add_argument("--r", type=float, default=None, help="filter due-range R")
    p.add_argument(
        "--methods",
        default=None,
        metavar="PREFIX",
        help="keep only scenarios whose name starts with PREFIX (e.g. sd_, rd_)",
    )
    p.add_argument(
        "--combo-size",
        type=int,
        nargs="+",
        default=[2, 3],
        help="combination sizes to rank (default: 2 3)",
    )
    p.add_argument("--top", type=int, default=5, help="how many combos to list")
    p.add_argument(
        "--baseline",
        default=None,
        metavar="NAME[,NAME...]",
        help="explicit scenario combo to score as oracle baseline "
        "(e.g. sd_edd,sd_lsl,sd_osl)",
    )
    p.add_argument(
        "--chosen",
        default=None,
        metavar="NAME[,NAME...]",
        help="explicit scenario combo to score and compare against --baseline "
        "(prints absolute + %% gain, per-n)",
    )
    args = p.parse_args()
    if args.chosen and not args.baseline:
        p.error("--chosen requires --baseline")

    df = load_rpdf(args.run)
    metric_col, metric_label = METRICS[args.metric]

    slice_desc = []
    if args.t is not None:
        slice_desc.append(f"T={args.t}")
    if args.r is not None:
        slice_desc.append(f"R={args.r}")
    label = ", ".join(slice_desc) if slice_desc else "all instances"
    print(f"\n### slice: {label}   metric: {metric_label}")

    sliced = filter_slice(df, t=args.t, r=args.r)
    report(
        sliced,
        metric_col=metric_col,
        metric_label=metric_label,
        combo_sizes=args.combo_size,
        top=args.top,
        method_prefix=args.methods,
    )
    if args.baseline:
        gain_report(
            sliced,
            metric_col=metric_col,
            metric_label=metric_label,
            baseline=parse_combo(args.baseline),
            chosen=parse_combo(args.chosen) if args.chosen else None,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
