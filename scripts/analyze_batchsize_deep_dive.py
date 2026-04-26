import sys
from itertools import combinations, product
from pathlib import Path

import numpy as np
import pandas as pd

import statsmodels.formula.api as smf
from statsmodels.stats.anova import anova_lm
from statsmodels.stats.diagnostic import het_breuschpagan
from statsmodels.stats.multicomp import pairwise_tukeyhsd

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------
if len(sys.argv) > 1:
    csv_path = sys.argv[1]
else:
    csv_path = "batch_size_5_10_15.csv"

prefix = Path(csv_path).stem  # e.g. "batch_size_5_10_15" or "batch_size_5_10_15_20"

df = pd.read_csv(csv_path)
instance_params = ["n", "c", "totalMcCount", "T", "R", "W"]
batch_sizes = sorted(df["batchSize"].unique().tolist())
n_bs = len(batch_sizes)

# ------------------------------------------------------------------
# Helper: generate pairwise diff names
# ------------------------------------------------------------------
bs_pairs = list(combinations(batch_sizes, 2))
diff_col_names = [f"diff_{a}vs{b}" for a, b in bs_pairs]


# ======================================================================
# Section 0: Load data, pivot, compute differences
# ======================================================================
pivot = df.pivot(index="insIndex", columns="batchSize", values="RPDf")
instance_params_df = df[["insIndex"] + instance_params].drop_duplicates()

diff_data = {"insIndex": pivot.index.to_numpy()}
for a, b in bs_pairs:
    diff_data[f"diff_{a}vs{b}"] = (pivot[a] - pivot[b]).to_numpy()
diff_df = pd.DataFrame(diff_data)
instance_df = diff_df.merge(instance_params_df, on="insIndex")

print("=" * 72)
print(f"Section 0: Data loaded from {csv_path}")
print(f"  {len(df)} rows, {len(pivot)} instances, batch_sizes={batch_sizes}")
print("=" * 72)

# ======================================================================
# Section 0.5: Fit Model 1 (main effects) and Model 2 (interactions)
# ======================================================================
formula_main = "RPDf ~ C(batchSize) + " + " + ".join(instance_params)
model1 = smf.ols(formula_main, data=df).fit()

formula_full = (
    "RPDf ~ C(batchSize) + "
    + " + ".join(instance_params)
    + " + "
    + " + ".join(f"C(batchSize):{p}" for p in instance_params)
)
model2 = smf.ols(formula_full, data=df).fit()
rse = np.sqrt(model2.scale)

print("=" * 72)
print("Models fitted")
print(f"  Model 1 (main) R²={model1.rsquared:.4f}")
print(f"  Model 2 (full)  R²={model2.rsquared:.4f}, RSE={rse:.4f}")
print("=" * 72)

# Save Model 2 coefficients
coef_df = model2.summary2().tables[1]
coef_df.to_csv(f"{prefix}_model2_summary.csv")
print(f"Saved: {prefix}_model2_summary.csv")

# ======================================================================
# Section 1: Difference Regression
# ======================================================================
print("\n" + "=" * 72)
print("Section 1: Difference Regression")
print("=" * 72)

diff_regressions = {}
diff_descriptive_rows = []

for diff_col in diff_col_names:
    # Distribution stats
    series = instance_df[diff_col]
    desc = {
        "diff_pair": diff_col,
        "n": len(series),
        "mean": series.mean(),
        "std": series.std(),
        "median": series.median(),
        "q25": series.quantile(0.25),
        "q75": series.quantile(0.75),
        "min": series.min(),
        "max": series.max(),
        "pct_positive": (series > 0).mean(),
        "pct_negative": (series < 0).mean(),
    }
    for param in instance_params:
        desc[f"corr_{param}"] = series.corr(instance_df[param])
    diff_descriptive_rows.append(desc)

    print(f"\n--- {diff_col} ---")
    print(
        f"  mean={desc['mean']:.4f}, std={desc['std']:.4f}, median={desc['median']:.4f}"
    )
    print(f"  positive={desc['pct_positive']:.1%}, negative={desc['pct_negative']:.1%}")
    corr_parts = [f"{p}={desc[f'corr_{p}']:.3f}" for p in instance_params]
    print(f"  correlations: {', '.join(corr_parts)}")

    # Regression
    formula = f"{diff_col} ~ n + c + totalMcCount + T + R + W"
    m = smf.ols(formula, data=instance_df).fit()
    diff_regressions[diff_col] = m
    print(f"  R²={m.rsquared:.4f}")
    coefs = m.params.reset_index()
    coefs.columns = ["parameter", "coefficient"]
    coefs["std_err"] = m.bse.values
    coefs["p_value"] = m.pvalues.values
    coefs["diff_pair"] = diff_col
    coefs = coefs.rename(columns={"parameter": "param"})
    sig_cols = coefs[coefs["p_value"] < 0.05]
    sig_text = "; ".join(
        f"{r['param']}={r['coefficient']:.4f}" for _, r in sig_cols.iterrows()
    )
    print(f"  significant: {sig_text}")

diff_descriptive_df = pd.DataFrame(diff_descriptive_rows)
diff_descriptive_df.to_csv(f"{prefix}_diff_descriptive.csv", index=False)
print(f"\nSaved: {prefix}_diff_descriptive.csv")

diff_reg_rows = []
for diff_col, m in diff_regressions.items():
    for i in range(len(m.params)):
        diff_reg_rows.append(
            {
                "diff_pair": diff_col,
                "param": m.params.index[i],
                "coefficient": m.params.iloc[i],
                "std_err": m.bse.iloc[i],
                "p_value": m.pvalues.iloc[i],
            }
        )
diff_reg_df = pd.DataFrame(diff_reg_rows)
diff_reg_df.to_csv(f"{prefix}_diff_regression.csv", index=False)
print(f"Saved: {prefix}_diff_regression.csv")

# ======================================================================
# Section 2: Slicing Analysis — ANOVA + Tukey per parameter level
# ======================================================================
print("\n" + "=" * 72)
print("Section 2: Slicing Analysis (ANOVA + Tukey per parameter level)")
print("=" * 72)

slicing_rows = []
for param in instance_params:
    for val in sorted(df[param].unique()):
        subset = df[df[param] == val]
        n_obs = len(subset)
        means = (
            subset.groupby("batchSize")["RPDf"]
            .agg(["mean", "std"])
            .reindex(batch_sizes)
        )

        # ANOVA: RPDf ~ C(batchSize) within this slice
        anova_result = anova_lm(
            smf.ols("RPDf ~ C(batchSize)", data=subset).fit(), type=2
        )
        f_stat = anova_result["F"].iloc[0]
        f_p = anova_result["PR(>F)"].iloc[0]

        # Tukey HSD
        tukey = pairwise_tukeyhsd(
            endog=subset["RPDf"], groups=subset["batchSize"], alpha=0.05
        )
        tukey_pvals = {}
        unique_groups = tukey.groupsunique
        idx = 0
        for i in range(len(unique_groups)):
            for j in range(i + 1, len(unique_groups)):
                pair = f"({unique_groups[j]}, {unique_groups[i]})"
                tukey_pvals[pair] = tukey.pvalues[idx]
                idx += 1

        # Find winner
        mean_series = means["mean"]
        winner = int(mean_series.idxmin())

        row = {
            "parameter": param,
            "param_value": val,
            "n_obs": n_obs,
            "anova_F": f_stat,
            "anova_p": f_p,
            "winner": winner,
        }
        for bs in batch_sizes:
            row[f"mean_bs{bs}"] = means.loc[bs, "mean"]
            row[f"std_bs{bs}"] = means.loc[bs, "std"]
        for a, b in bs_pairs:
            pair_key = f"({b}, {a})"
            row[f"tukey_{a}vs{b}_p"] = tukey_pvals.get(pair_key, np.nan)

        slicing_rows.append(row)

        if f_p < 0.05:
            print(
                f"  {param}={val}: ANOVA F={f_stat:.2f}, p={f_p:.4f}, winner=bs{winner}"
            )

slicing_df = pd.DataFrame(slicing_rows)
slicing_df.to_csv(f"{prefix}_slicing_analysis.csv", index=False)
print(f"\nSaved: {prefix}_slicing_analysis.csv ({len(slicing_df)} rows)")

# ======================================================================
# Section 3: Interaction Effect Decomposition via Model Predictions
# ======================================================================
print("\n" + "=" * 72)
print("Section 3: Interaction Effect Decomposition (Model 2 predictions)")
print("=" * 72)

# Fix other params at median/mode
fixed_values = {}
for param in instance_params:
    if df[param].dtype == "float64":
        fixed_values[param] = df[param].median()
    else:
        fixed_values[param] = df[param].mode().iloc[0]

interaction_rows = []
for param in instance_params:
    for val in sorted(df[param].unique()):
        row_vals = fixed_values.copy()
        row_vals[param] = val

        preds = []
        for bs in batch_sizes:
            pred_input = pd.DataFrame([{**row_vals, "batchSize": bs}])
            pred_result = model2.get_prediction(pred_input)
            summary = pred_result.summary_frame()
            preds.append(
                {
                    "batchSize": bs,
                    "mean": summary["mean"].iloc[0],
                    "se": summary["mean_se"].iloc[0],
                }
            )

        best_bs = min(preds, key=lambda x: x["mean"])["batchSize"]

        row = {
            "parameter": param,
            "param_value": val,
            "best_batchSize": best_bs,
        }
        for bs in batch_sizes:
            row[f"pred_bs{bs}"] = next(p["mean"] for p in preds if p["batchSize"] == bs)

        for a, b in bs_pairs:
            p_a = next(p for p in preds if p["batchSize"] == a)
            p_b = next(p for p in preds if p["batchSize"] == b)
            diff_val = p_a["mean"] - p_b["mean"]
            se_val = np.sqrt(p_a["se"] ** 2 + p_b["se"] ** 2)
            z_val = diff_val / se_val if se_val > 0 else np.nan
            sig_model = abs(z_val) > 1.96
            sig_conservative = abs(diff_val) > 1.96 * np.sqrt(se_val**2 + rse**2)
            row[f"diff_{a}vs{b}"] = diff_val
            row[f"se_diff_{a}vs{b}"] = se_val
            row[f"z_{a}vs{b}"] = z_val
            row[f"sig_model_{a}vs{b}"] = sig_model
            row[f"sig_conservative_{a}vs{b}"] = sig_conservative

        interaction_rows.append(row)

        if param in ["R", "T"]:
            pred_strs = " ".join(f"bs{p['batchSize']}={p['mean']:.3f}" for p in preds)
            print(f"  {param}={val}: {pred_strs} → best=bs{best_bs}")

interaction_df = pd.DataFrame(interaction_rows)
interaction_df.to_csv(f"{prefix}_interaction_effects.csv", index=False)
print(f"\nSaved: {prefix}_interaction_effects.csv ({len(interaction_df)} rows)")

# ======================================================================
# Section 4: Practical Recommendation Table
# ======================================================================
print("\n" + "=" * 72)
print("Section 4: Practical Recommendation Table")
print("=" * 72)

grid_values = {
    "n": [50, 100, 150, 200],
    "c": [5, 10],
    "totalMcCount": [15, 25, 30, 50],
    "T": [0.2, 0.4, 0.6],
    "R": [0.2, 0.6, 1.0],
    "W": [10, 20],
}
grid_keys = list(grid_values.keys())
grid_rows = []
for vals in product(*grid_values.values()):
    grid_rows.append(dict(zip(grid_keys, vals)))
grid_df = pd.DataFrame(grid_rows)

for bs in batch_sizes:
    pred_input = grid_df.copy()
    pred_input["batchSize"] = bs
    grid_df[f"pred_RPDf_bs{bs}"] = model2.predict(pred_input)

pred_cols = [f"pred_RPDf_bs{bs}" for bs in batch_sizes]
grid_df["recommended_bs"] = (
    grid_df[pred_cols].idxmin(axis=1).str.replace("pred_RPDf_bs", "")
)
grid_df["best_pred_RPDf"] = grid_df[pred_cols].min(axis=1)
grid_df["worst_pred_RPDf"] = grid_df[pred_cols].max(axis=1)
grid_df["savings"] = grid_df["worst_pred_RPDf"] - grid_df["best_pred_RPDf"]

rec_dist = grid_df["recommended_bs"].value_counts().sort_index()
print(f"Recommendation distribution across {len(grid_df)} param combinations:")
print(rec_dist)
print(
    f"\nSavings: mean={grid_df['savings'].mean():.4f}, "
    f"median={grid_df['savings'].median():.4f}"
)

grid_df.to_csv(f"{prefix}_recommendations_full.csv", index=False)
print(f"Saved: {prefix}_recommendations_full.csv")

# Collapse to 2D matrix: R × n (top interaction drivers)
matrix_rows = []
for r_val in grid_values["R"]:
    for n_val in grid_values["n"]:
        subset = grid_df[(grid_df["R"] == r_val) & (grid_df["n"] == n_val)]
        rec_counts = subset["recommended_bs"].value_counts()
        most_common_bs = rec_counts.idxmax()
        n_cells = len(subset)
        matrix_rows.append(
            {
                "R": r_val,
                "n": n_val,
                "recommended_bs": most_common_bs,
                "n_combinations": n_cells,
                "pct_recommended": rec_counts.get(most_common_bs, 0) / n_cells,
                "mean_savings": subset["savings"].mean(),
                "mean_best_RPDf": subset["best_pred_RPDf"].mean(),
            }
        )

matrix_df = pd.DataFrame(matrix_rows)
matrix_df.to_csv(f"{prefix}_recommendation_matrix.csv", index=False)
print(f"Saved: {prefix}_recommendation_matrix.csv")
print("\nRecommendation matrix (R × n):")
print(matrix_df.to_string(index=False))

# ======================================================================
# Section 5: Model Diagnostics
# ======================================================================
print("\n" + "=" * 72)
print("Section 5: Model Diagnostics")
print("=" * 72)

# Nested F-test: Model 1 vs Model 2
nested_f = anova_lm(model1, model2)
print("Nested F-test (Model 1 vs Model 2):")
print(nested_f)

# Breusch-Pagan heteroskedasticity test
bp = het_breuschpagan(model2.resid, model2.model.exog)
print("Breusch-Pagan heteroskedasticity test:")
print(f"  LR stat={bp[0]:.4f}, p-value={bp[1]:.4f}")

# Residual stats
resid = model2.resid
fitted = model2.fittedvalues
diag_rows = [
    {"metric": "residual_mean", "value": resid.mean()},
    {"metric": "residual_std", "value": resid.std()},
    {"metric": "residual_skew", "value": pd.Series(resid).skew()},
    {"metric": "residual_kurtosis", "value": pd.Series(resid).kurtosis()},
    {"metric": "residual_min", "value": resid.min()},
    {"metric": "residual_max", "value": resid.max()},
    {"metric": "fitted_mean", "value": fitted.mean()},
    {"metric": "fitted_std", "value": fitted.std()},
    {
        "metric": "corr_resid_vs_fitted",
        "value": np.corrcoef(resid, fitted)[0, 1],
    },
    {"metric": "bp_lr_stat", "value": bp[0]},
    {"metric": "bp_p_value", "value": bp[1]},
    {"metric": "heteroskedastic_detected", "value": bp[1] < 0.05},
]

# R² of difference regressions vs Model 2
for diff_col, m in diff_regressions.items():
    diag_rows.append({"metric": f"R2_{diff_col}_regression", "value": m.rsquared})

diag_df = pd.DataFrame(diag_rows)
diag_df.to_csv(f"{prefix}_model_diagnostics.csv", index=False)
print(f"\nSaved: {prefix}_model_diagnostics.csv")

print("\n" + "=" * 72)
print("Analysis complete. Output files:")
out_files = [
    f"{prefix}_model2_summary.csv",
    f"{prefix}_diff_descriptive.csv",
    f"{prefix}_diff_regression.csv",
    f"{prefix}_slicing_analysis.csv",
    f"{prefix}_interaction_effects.csv",
    f"{prefix}_recommendations_full.csv",
    f"{prefix}_recommendation_matrix.csv",
    f"{prefix}_model_diagnostics.csv",
]
for f in out_files:
    print(f"  {f}")
print("=" * 72)
