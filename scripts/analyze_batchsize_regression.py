import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from statsmodels.stats.outliers_influence import variance_inflation_factor

df = pd.read_csv("batch_size_5_10_15.csv")

instance_params = ["n", "c", "totalMcCount", "T", "R", "W"]

# ---------------------------------------------------------------
# Model 1: Main effects only  (RPDf ~ batchSize + instance params)
# ---------------------------------------------------------------
X1 = df[["batchSize"] + instance_params]
X1 = sm.add_constant(X1)
model1 = sm.OLS(df["RPDf"], X1).fit()
print("=" * 72)
print("Model 1: Main effects only")
print("=" * 72)
print(model1.summary())

# ---------------------------------------------------------------
# Model 2: Full interactions (batchSize x each instance param)
# ---------------------------------------------------------------
formula = (
    "RPDf ~ C(batchSize) + "
    + " + ".join(instance_params)
    + " + "
    + " + ".join(f"C(batchSize):{p}" for p in instance_params)
)
model2 = smf.ols(formula, data=df).fit()
print("=" * 72)
print("Model 2: batchSize + instance params + batchSize x instance interactions")
print("=" * 72)
print(model2.summary())

# ---------------------------------------------------------------
# VIF check for Model 2
# ---------------------------------------------------------------
X2 = model2.model.exog
X2_df = pd.DataFrame(X2, columns=model2.model.exog_names)
vifs = pd.Series(
    [variance_inflation_factor(X2_df.values, i) for i in range(X2_df.shape[1])],
    index=X2_df.columns,
).rename("VIF")
vifs = vifs.replace([float("inf"), -float("inf")], float("nan")).dropna()
vifs = vifs.sort_values(ascending=False)
print("=" * 72)
print("VIF values (Model 2)")
print("=" * 72)
print(vifs)

# ---------------------------------------------------------------
# Predict best batchSize per scenario (from Model 2)
# ---------------------------------------------------------------
scenarios = df[instance_params].drop_duplicates()
results = []
for _, row in scenarios.iterrows():
    preds = []
    for bs in [5, 10, 15]:
        scenario_df = pd.DataFrame([{**row.to_dict(), "batchSize": bs}])
        pred = model2.predict(scenario_df).iloc[0]
        preds.append((bs, pred))
    best_bs = min(preds, key=lambda x: x[1])
    results.append(
        {
            **row.to_dict(),
            "pred_RPDf_bs5": preds[0][1],
            "pred_RPDf_bs10": preds[1][1],
            "pred_RPDf_bs15": preds[2][1],
            "best_batchSize": best_bs[0],
            "best_pred_RPDf": best_bs[1],
        }
    )

result_df = pd.DataFrame(results)
distribution = result_df["best_batchSize"].value_counts().sort_index()
print("=" * 72)
print("Predicted best batchSize distribution (Model 2)")
print("=" * 72)
print(distribution)
print(f"\nTotal scenarios: {len(result_df)}")

# Show scenarios where each batchSize is best — what makes them different?
for bs in [5, 10, 15]:
    subset = result_df[result_df["best_batchSize"] == bs]
    print(f"\n--- batchSize={bs} is best for {len(subset)} scenarios ---")
    print(subset[instance_params].describe().T)

# ---------------------------------------------------------------
# Actual RPDf comparison per batchSize
# ---------------------------------------------------------------
print("\n" + "=" * 72)
print("Actual RPDf statistics by batchSize")
print("=" * 72)
print(df.groupby("batchSize")["RPDf"].describe().T)

# ---------------------------------------------------------------
# Per-instance: which batchSize wins?
# ---------------------------------------------------------------
print("\n" + "=" * 72)
print("Per-instance winner (actual RPDf)")
print("=" * 72)
pivot = df.pivot(index="insIndex", columns="batchSize", values="RPDf")
pivot["best_bs"] = pivot[[5, 10, 15]].idxmin(axis=1)
actual_dist = pivot["best_bs"].value_counts().sort_index()
print(actual_dist)
print(
    f"\nTies (where min is shared): {(pivot[[5, 10, 15]].min(axis=1) == pivot[[5, 10, 15]].median(axis=1)).sum()}"
)

# How often does each batchSize win?
total = len(pivot)
for bs in [5, 10, 15]:
    wins = (pivot["best_bs"] == bs).sum()
    print(f"  batchSize={bs}: wins {wins}/{total} ({100 * wins / total:.1f}%)")

# ---------------------------------------------------------------
# Pairwise comparison: bs5 vs bs10, bs5 vs bs15, bs10 vs bs15
# ---------------------------------------------------------------
print("\n" + "=" * 72)
print("Pairwise RPDf comparison (how often is A < B?)")
print("=" * 72)
for a, b in [(5, 10), (5, 15), (10, 15)]:
    a_wins = (pivot[a] < pivot[b]).sum()
    b_wins = (pivot[b] < pivot[a]).sum()
    ties = (pivot[a] == pivot[b]).sum()
    diff_mean = (pivot[a] - pivot[b]).mean()
    print(
        f"  bs{a} < bs{b}: {a_wins} ({100 * a_wins / total:.1f}%), "
        f"bs{b} < bs{a}: {b_wins} ({100 * b_wins / total:.1f}%), "
        f"ties: {ties}, mean diff (a-b): {diff_mean:.4f}"
    )

# ---------------------------------------------------------------
# Model 2: predicted vs actual RPDf validation
# ---------------------------------------------------------------
df["pred_RPDf"] = model2.predict(df)
ss_res = ((df["RPDf"] - df["pred_RPDf"]) ** 2).sum()
ss_tot = ((df["RPDf"] - df["RPDf"].mean()) ** 2).sum()
r2 = 1 - ss_res / ss_tot
rmse = np.sqrt(((df["RPDf"] - df["pred_RPDf"]) ** 2).mean())
print("\n" + "=" * 72)
print("Model 2 validation (predicted vs actual RPDf)")
print("=" * 72)
print(f"R²={r2:.4f}, RMSE={rmse:.4f}")

# ---------------------------------------------------------------
# When does each batchSize actually win? — parameter profile
# ---------------------------------------------------------------
print("\n" + "=" * 72)
print("Parameter profile when each batchSize wins (actual)")
print("=" * 72)
for bs in [5, 10, 15]:
    winners = pivot[pivot["best_bs"] == bs].index
    winner_df = df[df["insIndex"].isin(winners)][instance_params].drop_duplicates()
    if len(winner_df) == 0:
        print(f"\n--- batchSize={bs}: no wins ---")
        continue
    print(f"\n--- batchSize={bs} wins for {len(winner_df)} scenarios ---")
    print(winner_df.describe().T)

# ---------------------------------------------------------------
# Save per-scenario recommendations
# ---------------------------------------------------------------
result_df.to_csv("batch_size_regression_recommendations.csv", index=False)
print("\nRecommendations saved to batch_size_regression_recommendations.csv")

# Save actual per-instance winner
pivot[["best_bs", 5, 10, 15]].to_csv("batch_size_actual_winner.csv")
print("Actual winner saved to batch_size_actual_winner.csv")
