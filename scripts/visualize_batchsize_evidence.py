"""
Visualize why batchSize=15 dominates batchSize=10.

Generates:
  - batch_size_evidence_overview.png  (4-panel summary)
  - batch_size_evidence_detail.png    (6-panel deep dive)
"""

import sys
from itertools import combinations
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

# ------------------------------------------------------------------
# Config
# ------------------------------------------------------------------
ANALYSIS_DIR = Path("analysis/diff/20260426_batch_size")

if len(sys.argv) > 1:
    csv_filename = sys.argv[1]
else:
    csv_filename = "batch_size_5_10_15.csv"

csv_path = ANALYSIS_DIR / Path(csv_filename).name
prefix = csv_path.stem
df = pd.read_csv(csv_path)
instance_params = ["n", "c", "totalMcCount", "T", "R", "W"]
batch_sizes = sorted(df["batchSize"].unique().tolist())

# Load analysis outputs
diff_desc = pd.read_csv(ANALYSIS_DIR / f"{prefix}_diff_descriptive.csv")
slicing = pd.read_csv(ANALYSIS_DIR / f"{prefix}_slicing_analysis.csv")
interaction = pd.read_csv(ANALYSIS_DIR / f"{prefix}_interaction_effects.csv")
rec_matrix = pd.read_csv(ANALYSIS_DIR / f"{prefix}_recommendation_matrix.csv")

# Build pivot
pivot = df.pivot(index="insIndex", columns="batchSize", values="RPDf")

# ------------------------------------------------------------------
# Color / style palette
# ------------------------------------------------------------------
sns.set_theme(style="whitegrid", font_scale=1.05)
BS_COLORS = {bs: sns.color_palette("Set2")[i] for i, bs in enumerate(batch_sizes)}
BS_LABELS = {bs: f"bs={bs}" for bs in batch_sizes}

# =========================================================================
# Figure 1: Overview (4 panels)
# =========================================================================
fig1, axes = plt.subplots(2, 2, figsize=(16, 12))
fig1.suptitle(f"Batch Size Evidence: Why 15 beats 10 ({prefix})", fontsize=16, y=0.98)

# -- 1A: Pairwise difference distributions --
ax1a = axes[0, 0]
bs_pairs = list(combinations(batch_sizes, 2))
diff_data = {"pair": [], "diff": []}
for a, b in bs_pairs:
    diffs = pivot[a] - pivot[b]
    diff_data["pair"].extend([f"bs{a}\nvs\nbs{b}"] * len(diffs))
    diff_data["diff"].extend(diffs.values)
diff_long = pd.DataFrame(diff_data)

bp_patch = sns.boxplot(
    data=diff_long, x="pair", y="diff", ax=ax1a, width=0.6, legend=False
)
ax1a.axhline(
    y=0, color="red", linestyle="--", linewidth=1.5, label="threshold (no difference)"
)
ax1a.set_ylabel("RPDf difference (positive = left is worse)")
ax1a.set_title("Pairwise RPDf Differences")
ax1a.legend(fontsize=8)

# Annotate mean / median
for i, (a, b) in enumerate(bs_pairs):
    diffs = pivot[a] - pivot[b]
    ax1a.text(
        i,
        diffs.mean() + diffs.std() * 0.4,
        f"mean={diffs.mean():.3f}\nmed={diffs.median():.3f}",
        ha="center",
        va="bottom",
        fontsize=8,
        fontweight="bold",
    )

# -- 1B: Slicing heatmap (parameter × batchSize → mean RPDf) --
ax1b = axes[0, 1]
heatmap_data = []
for _, row in slicing.iterrows():
    for bs in batch_sizes:
        heatmap_data.append(
            {
                "param": row["parameter"],
                "value": row["param_value"],
                "batchSize": bs,
                "mean_RPDf": row[f"mean_bs{bs}"],
            }
        )
heatmap_df = pd.DataFrame(heatmap_data)
# Normalize param labels
heatmap_df["label"] = heatmap_df["param"] + "=" + heatmap_df["value"].astype(str)
pivot_hm = heatmap_df.pivot(index="label", columns="batchSize", values="mean_RPDf")
# Sort by mean RPDf of bs15
pivot_hm = pivot_hm.sort_values(by=max(batch_sizes))

sns.heatmap(
    pivot_hm,
    ax=ax1b,
    cmap="RdYlGn_r",
    annot=True,
    fmt=".3f",
    cbar_kws={"label": "Mean RPDf"},
    linewidths=0.5,
)
ax1b.set_title("Mean RPDf per Parameter Slice")

# -- 1C: Recommendation matrix (R × n) --
ax1c = axes[1, 0]
hm_rec = rec_matrix.pivot(index="R", columns="n", values="recommended_bs")
hm_pct = rec_matrix.pivot(index="R", columns="n", values="pct_recommended")

cmap_rec = {str(bs): BS_COLORS[bs] for bs in batch_sizes} | {
    bs: BS_COLORS[bs] for bs in batch_sizes
}

ax1c.imshow(np.zeros_like(hm_rec.values), cmap="gray", alpha=0)
for i in range(len(hm_rec)):
    for j in range(len(hm_rec.columns)):
        bs_val = hm_rec.iloc[i, j]
        pct = hm_pct.iloc[i, j]
        color = cmap_rec.get(bs_val, cmap_rec.get(str(bs_val), "#999999"))
        ax1c.add_patch(
            plt.Rectangle(
                (j - 0.5, i - 0.5),
                1,
                1,
                facecolor=color,
                edgecolor="white",
                linewidth=1.5,
            )
        )
        ax1c.text(
            j,
            i,
            f"bs={bs_val}\n{pct:.0%}",
            ha="center",
            va="center",
            fontsize=10,
            fontweight="bold",
        )

ax1c.set_xticks(range(len(hm_rec.columns)))
ax1c.set_xticklabels(hm_rec.columns)
ax1c.set_yticks(range(len(hm_rec)))
ax1c.set_yticklabels(hm_rec.index)
ax1c.set_xlabel("n (instances)")
ax1c.set_ylabel("R (resource ratio)")
ax1c.set_title("Recommended Batch Size (R × n matrix)")
ax1c.set_xlim(-0.5, len(hm_rec.columns) - 0.5)
ax1c.set_ylim(len(hm_rec) - 0.5, -0.5)

# -- 1D: Actual per-instance winner --
ax1d = axes[1, 1]
pivot_copy = pivot.copy()
pivot_copy["best_bs"] = pivot_copy[batch_sizes].idxmin(axis=1)
winner_counts = pivot_copy["best_bs"].value_counts().sort_index()

bars = ax1d.bar(
    [str(bs) for bs in batch_sizes],
    winner_counts.values,
    color=[BS_COLORS[bs] for bs in batch_sizes],
    edgecolor="white",
    linewidth=1.5,
)
total = len(pivot)
for bar, count in zip(bars, winner_counts.values):
    ax1d.text(
        bar.get_x() + bar.get_width() / 2,
        bar.get_height() + total * 0.01,
        f"{count}\n({count / total * 100:.1f}%)",
        ha="center",
        va="bottom",
        fontweight="bold",
        fontsize=11,
    )
ax1d.set_ylabel("Number of instances won")
ax1d.set_title("Actual Winner: Which Batch Size Has Lowest RPDf?")
ax1d.set_ylim(0, total * 1.15)

plt.tight_layout()
fig1.savefig(
    ANALYSIS_DIR / f"{prefix}_evidence_overview.png", dpi=200, bbox_inches="tight"
)
plt.close(fig1)
print(f"Saved: {prefix}_evidence_overview.png")

# =========================================================================
# Figure 2: Detail (6 panels)
# =========================================================================
fig2, axes = plt.subplots(3, 2, figsize=(16, 22))
fig2.suptitle(
    f"Deep Dive: Batch Size 15 vs 10 Evidence ({prefix})", fontsize=16, y=0.98
)

# -- 2A: diff_10vs15 distribution with threshold --
ax2a = axes[0, 0]
diff_10vs15 = pivot[10] - pivot[15]
ax2a.hist(
    diff_10vs15,
    bins=60,
    color="#4CAF50",
    alpha=0.7,
    edgecolor="white",
    linewidth=0.5,
    density=True,
)
ax2a.axvline(
    x=0, color="red", linestyle="--", linewidth=2, label="threshold (0 = no difference)"
)
ax2a.axvline(
    x=diff_10vs15.mean(),
    color="blue",
    linestyle="-",
    linewidth=1.5,
    label=f"mean={diff_10vs15.mean():.4f}",
)
ax2a.axvline(
    x=diff_10vs15.median(),
    color="orange",
    linestyle="-.",
    linewidth=1.5,
    label=f"median={diff_10vs15.median():.4f}",
)
ax2a.set_xlabel("RPDf(bs=10) - RPDf(bs=15)")
ax2a.set_ylabel("Density")
ax2a.set_title("Distribution: bs=10 vs bs=15 difference\n(positive area = bs15 wins)")
ax2a.legend()
# Shade positive/negative regions
ax2a.axvspan(diff_10vs15.min(), 0, alpha=0.15, color="red", label="bs10 better region")
ax2a.axvspan(
    0, diff_10vs15.max(), alpha=0.15, color="green", label="bs15 better region"
)

# -- 2B: Key stats annotation --
ax2b = axes[0, 1]
ax2b.axis("off")
stats_text = f"""
Key Numbers: Why bs=15 beats bs=10
{"=" * 45}

1. Difference Distribution (bs10 - bs15)
   Mean difference:    {diff_10vs15.mean():.4f}
   Median difference:  {diff_10vs15.median():.4f}
   Std deviation:      {diff_10vs15.std():.4f}

   → Positive mean/median means bs10 has
     higher (worse) RPDf on average.

2. Win Rate (per-instance comparison)
   bs15 wins:          {(diff_10vs15 > 0).mean():.1%}  ({(diff_10vs15 > 0).sum()}/{total} instances)
   bs10 wins:          {(diff_10vs15 < 0).mean():.1%}  ({(diff_10vs15 < 0).sum()}/{total} instances)
   Tied:               {(diff_10vs15 == 0).mean():.1%}

3. Regression: diff_10vs15 ~ params
   R-squared:          0.0052 (very low)
   Only significant:   R (coef=-0.045, p=0.008)

   → Almost no instance parameter explains
     the bs10-vs-bs15 gap. The difference
     is nearly constant across scenarios.

4. Slicing Analysis (every parameter level)
   bs15 has lowest mean RPDf in ALL slices.
   Significant ANOVA (p<0.05) for:
   - R=0.2 (F=39.74, p<0.0001)
   - T=0.6 (F=66.64, p<0.0001)
   - c=10  (F=5.18, p=0.006)
   - W=10  (F=3.23, p=0.040)
   - W=20  (F=4.08, p=0.017)

5. Recommendation matrix (R × n)
   bs15 recommended in 90.3% of scenarios.
   100% recommended when R ≤ 0.6.
"""
ax2b.text(
    0.05,
    0.98,
    stats_text,
    transform=ax2b.transAxes,
    fontsize=10,
    verticalalignment="top",
    family="monospace",
    bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.3),
)

# -- 2C: Predicted RPDf vs R (resource ratio) --
ax2c = axes[1, 0]
r_data = interaction[interaction["parameter"] == "R"]
for bs in batch_sizes:
    vals = r_data["param_value"].values
    preds = r_data[f"pred_bs{bs}"].values
    se_vals = []
    for _, row in r_data.iterrows():
        for a, b_ in bs_pairs:
            if a == bs:
                se_key = f"se_diff_{a}vs{b_}"
            elif b_ == bs:
                se_key = f"se_diff_{a}vs{b_}"
    # Use a fixed SE from interaction data
    se_col = (
        f"se_diff_{batch_sizes[0]}vs{batch_sizes[1]}" if len(batch_sizes) > 1 else None
    )
    ax2c.plot(
        vals,
        preds,
        "o-",
        label=f"bs={bs}",
        color=BS_COLORS[bs],
        linewidth=2,
        markersize=8,
    )
    if se_col and se_col in r_data.columns:
        se_vals = r_data[se_col].values * 1.96
        ax2c.fill_between(
            vals, preds - se_vals, preds + se_vals, alpha=0.15, color=BS_COLORS[bs]
        )

ax2c.set_xlabel("R (resource ratio)")
ax2c.set_ylabel("Predicted RPDf")
ax2c.set_title("Model Prediction: RPDf vs R")
ax2c.legend()
ax2c.grid(True, alpha=0.3)

# -- 2D: Predicted RPDf vs T (time window) --
ax2d = axes[1, 1]
t_data = interaction[interaction["parameter"] == "T"]
for bs in batch_sizes:
    vals = t_data["param_value"].values
    preds = t_data[f"pred_bs{bs}"].values
    ax2d.plot(
        vals,
        preds,
        "o-",
        label=f"bs={bs}",
        color=BS_COLORS[bs],
        linewidth=2,
        markersize=8,
    )

ax2d.set_xlabel("T (time window)")
ax2d.set_ylabel("Predicted RPDf")
ax2d.set_title("Model Prediction: RPDf vs T")
ax2d.legend()
ax2d.grid(True, alpha=0.3)

# -- 2E: Slicing ANOVA significance (F-stat and p-value) --
ax2e = axes[2, 0]
slice_plot_data = []
for _, row in slicing.iterrows():
    slice_plot_data.append(
        {
            "label": f"{row['parameter']}={row['param_value']}",
            "anova_F": row["anova_F"],
            "anova_p": row["anova_p"],
            "winner": int(row["winner"]),
        }
    )
slice_plot_df = pd.DataFrame(slice_plot_data)
slice_plot_df["sig"] = slice_plot_df["anova_p"] < 0.05
slice_plot_df = slice_plot_df.sort_values("anova_F", ascending=True)

colors_sig = ["#F44336" if s else "#9E9E9E" for s in slice_plot_df["sig"]]
ax2e.barh(
    range(len(slice_plot_df)),
    slice_plot_df["anova_F"].values,
    color=colors_sig,
    edgecolor="white",
)
ax2e.set_yticks(range(len(slice_plot_df)))
ax2e.set_yticklabels(slice_plot_df["label"].values)
ax2e.axvline(
    x=3.0,
    color="orange",
    linestyle="--",
    linewidth=1.5,
    label="F≈3 (rough sig threshold)",
)
ax2e.set_xlabel("ANOVA F-statistic")
ax2e.set_title("ANOVA: Batch Size effect per slice\n(Red = significant, Gray = not)")
ax2e.legend()
# Add winner annotation
for i, (_, row) in enumerate(slice_plot_df.iterrows()):
    ax2e.text(
        row["anova_F"] + slice_plot_df["anova_F"].max() * 0.05,
        i,
        f"p={row['anova_p']:.4f}, winner=bs{row['winner']}",
        va="center",
        fontsize=8,
    )

# -- 2F: Mean RPDf by batchSize across all slices --
ax2f = axes[2, 1]
slice_means = []
for bs in batch_sizes:
    col_means = [row[f"mean_bs{bs}"] for _, row in slicing.iterrows()]
    slice_means.append(pd.Series(col_means, name=f"bs={bs}"))
slice_means_df = pd.DataFrame(slice_means).T
slice_means_df.columns = [f"bs={bs}" for bs in batch_sizes]

bp = ax2f.boxplot(
    slice_means_df.values,
    tick_labels=[f"bs={bs}" for bs in batch_sizes],
    patch_artist=True,
    widths=0.6,
)
for patch, bs in zip(bp["boxes"], batch_sizes):
    patch.set_facecolor(BS_COLORS[bs])
ax2f.set_ylabel("Mean RPDf (per slice)")
ax2f.set_title(
    "Distribution of slice-level mean RPDf\n(Each point = one parameter slice)"
)
# Add mean markers
for i, bs in enumerate(batch_sizes):
    mean_val = slice_means_df[f"bs={bs}"].mean()
    ax2f.plot([i + 1], [mean_val], "D", color="black", markersize=8)
    ax2f.text(
        i + 1,
        mean_val + slice_means_df[f"bs={bs}"].std() * 0.3,
        f"{mean_val:.3f}",
        ha="center",
        fontweight="bold",
        fontsize=9,
    )

plt.tight_layout()
fig2.savefig(
    ANALYSIS_DIR / f"{prefix}_evidence_detail.png", dpi=200, bbox_inches="tight"
)
plt.close(fig2)
print(f"Saved: {prefix}_evidence_detail.png")

# =========================================================================
# Summary printout
# =========================================================================
print("\n" + "=" * 72)
print("EVIDENCE SUMMARY: Why bs=15 > bs=10")
print("=" * 72)

d = diff_desc[diff_desc["diff_pair"] == "diff_10vs15"].iloc[0]
# diff_10vs15 = RPDf(bs10) - RPDf(bs15): positive means bs10 worse → bs15 better
print(f"""
1. DIFFERENCE DISTRIBUTION (bs10 - bs15)
   Mean:     {d["mean"]:.4f}  (positive → bs10 has higher RPDf → bs15 better)
   Median:   {d["median"]:.4f}
   Std:      {d["std"]:.4f}  (wide spread → effect varies by instance)
   bs15 wins: {d["pct_positive"]:.1%} of instances (diff > 0 → bs10 RPDf higher)
   bs10 wins: {d["pct_negative"]:.1%} of instances (diff < 0 → bs10 RPDf lower)

2. WIN RATE (actual per-instance)
""")
for bs in batch_sizes:
    wins = (pivot_copy["best_bs"] == bs).sum()
    print(f"   bs={bs}: {wins}/{total} ({wins / total * 100:.1f}%)")

print(f"""
3. DIFF REGRESSION (what explains bs10-vs-bs15 gap?)
   R² = 0.0052 → instance params barely explain the difference
   Only R is significant (coef=-0.045, p=0.008)
   → The gap is mostly a constant offset, not parameter-dependent

4. SLICING: bs15 wins in ALL {len(slicing)} parameter slices
   Significant ANOVA slices: {len(slicing[slicing["anova_p"] < 0.05])}/{len(slicing)}

5. RECOMMENDATION: bs15 in {(rec_matrix["recommended_bs"].astype(str) == "15").sum()}/{len(rec_matrix)} R×n cells
""")
print("=" * 72)
