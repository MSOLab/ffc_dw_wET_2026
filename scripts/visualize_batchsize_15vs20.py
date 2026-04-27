"""
Visualize bs=15 vs bs=20 comparison from a 4-way batch size experiment.

Generates:
  - batch_size_5_10_15_20_evidence_15vs20.png  (4-panel: 15 vs 20 focus)
  - batch_size_5_10_15_20_evidence_all.png      (overview of all 4 batch sizes)
"""
import sys
from itertools import combinations
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
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
    csv_filename = "batch_size_5_10_15_20.csv"

csv_path = ANALYSIS_DIR / Path(csv_filename).name
prefix = csv_path.stem
df = pd.read_csv(csv_path)
instance_params = ["n", "c", "totalMcCount", "T", "R", "W"]
batch_sizes = sorted(df["batchSize"].unique().tolist())  # [5, 10, 15, 20]

# Load analysis outputs
diff_desc = pd.read_csv(ANALYSIS_DIR / f"{prefix}_diff_descriptive.csv")
diff_reg = pd.read_csv(ANALYSIS_DIR / f"{prefix}_diff_regression.csv")
slicing = pd.read_csv(ANALYSIS_DIR / f"{prefix}_slicing_analysis.csv")
interaction = pd.read_csv(ANALYSIS_DIR / f"{prefix}_interaction_effects.csv")
rec_matrix = pd.read_csv(ANALYSIS_DIR / f"{prefix}_recommendation_matrix.csv")
rec_full = pd.read_csv(ANALYSIS_DIR / f"{prefix}_recommendations_full.csv")

# Build pivot
pivot = df.pivot(index="insIndex", columns="batchSize", values="RPDf")
total = len(pivot)

# ------------------------------------------------------------------
# Style
# ------------------------------------------------------------------
sns.set_theme(style="whitegrid", font_scale=1.05)
BS_COLORS = {5: "#2196F3", 10: "#4CAF50", 15: "#FF9800", 20: "#9C27B0"}
BS_LABELS = {bs: f"bs={bs}" for bs in batch_sizes}

# =========================================================================
# Figure 1: bs15 vs bs20 focused evidence (4 panels)
# =========================================================================
fig1, axes = plt.subplots(2, 2, figsize=(16, 12))
fig1.suptitle(f"bs=15 vs bs=20: Is Larger Always Better? ({prefix})", fontsize=16, y=0.98)

# -- 1A: diff_15vs20 distribution --
ax1a = axes[0, 0]
diff_15vs20 = pivot[15] - pivot[20]
ax1a.hist(diff_15vs20, bins=60, color="#FF9800", alpha=0.7, edgecolor="white",
          linewidth=0.5, density=True)
ax1a.axvline(x=0, color="red", linestyle="--", linewidth=2, label="threshold (no difference)")
ax1a.axvline(x=diff_15vs20.mean(), color="blue", linestyle="-", linewidth=1.5,
             label=f"mean={diff_15vs20.mean():.4f}")
ax1a.axvline(x=diff_15vs20.median(), color="orange", linestyle="-.", linewidth=1.5,
             label=f"median={diff_15vs20.median():.4f}")
ax1a.set_xlabel("RPDf(bs=15) - RPDf(bs=20)")
ax1a.set_ylabel("Density")
ax1a.set_title("Distribution: bs=15 vs bs=20 difference\n(positive = bs15 worse, negative = bs15 better)")
ax1a.legend(fontsize=8)
ax1a.axvspan(diff_15vs20.min(), 0, alpha=0.12, color="green")  # bs15 better
ax1a.axvspan(0, diff_15vs20.max(), alpha=0.12, color="red")    # bs20 better

# -- 1B: Key stats text --
ax1b = axes[0, 1]
ax1b.axis("off")

d15vs20 = diff_desc[diff_desc["diff_pair"] == "diff_15vs20"].iloc[0]
d5vs15 = diff_desc[diff_desc["diff_pair"] == "diff_5vs15"].iloc[0]
d5vs20 = diff_desc[diff_desc["diff_pair"] == "diff_5vs20"].iloc[0]

# Win rate
pivot_copy = pivot.copy()
pivot_copy["best_bs"] = pivot_copy[batch_sizes].idxmin(axis=1)
winner_counts = pivot_copy["best_bs"].value_counts().sort_index()

# Slicing winners
slice_bs15_wins = len(slicing[slicing["winner"] == 15])
slice_bs20_wins = len(slicing[slicing["winner"] == 20])

stats_text = f"""
KEY FINDING: bs=15 and bs=20 are nearly equivalent
{'='*55}

1. DIFFERENCE DISTRIBUTION (bs15 - bs20)
   Mean:     {d15vs20['mean']:.4f}  (near zero → almost no gap)
   Median:   {d15vs20['median']:.4f}
   Std:      {d15vs20['std']:.4f}

   bs15 better: {(diff_15vs20 < 0).mean():.1%}  ({(diff_15vs20 < 0).sum()}/{total})
   bs20 better: {(diff_15vs20 > 0).mean():.1%}  ({(diff_15vs20 > 0).sum()}/{total})
   Tied:        {(diff_15vs20 == 0).mean():.1%}

   → Essentially a coin flip. No meaningful advantage.

2. WIN RATE (actual per-instance)
"""
for bs in batch_sizes:
    w = winner_counts.get(bs, 0)
    stats_text += f"   bs={bs}: {w}/{total} ({w/total*100:.1f}%)\n"

stats_text += f"""
   → bs15 wins {winner_counts.get(15, 0)/total*100:.1f}%, bs20 wins {winner_counts.get(20, 0)/total*100:.1f}%

3. SLICING ANALYSIS (per-parameter-level winner)
   bs15 wins: {slice_bs15_wins}/{len(slicing)} slices
   bs20 wins: {slice_bs20_wins}/{len(slicing)} slices

4. DIMINISHING RETURNS
   bs5→bs15 improvement: mean diff = {d5vs15['mean']:.4f}  (large gain)
   bs5→bs20 improvement: mean diff = {d5vs20['mean']:.4f}  (similar)
   bs15→bs20 gain:       mean diff = {-d15vs20['mean']:.4f}  (negligible)

   → Most gain comes from bs5→bs15. bs15→bs20 adds almost nothing.

5. RECOMMENDATION MATRIX (R x n)
"""
for _, row in rec_matrix.iterrows():
    marker = " <—" if row["recommended_bs"] in [15, 20] else ""
    stats_text += f"   R={row['R']}, n={row['n']}: bs={row['recommended_bs']} ({row['pct_recommended']:.0%}){marker}\n"

ax1b.text(0.02, 0.98, stats_text, transform=ax1b.transAxes,
          fontsize=9.5, verticalalignment="top", family="monospace",
          bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.3))

# -- 1C: All 4 batch size prediction curves vs R --
ax1c = axes[1, 0]
r_data = interaction[interaction["parameter"] == "R"]
for bs in batch_sizes:
    vals = r_data["param_value"].values
    preds = r_data[f"pred_bs{bs}"].values
    ax1c.plot(vals, preds, "o-", label=f"bs={bs}", color=BS_COLORS[bs],
              linewidth=2.5, markersize=9)
ax1c.set_xlabel("R (resource ratio)")
ax1c.set_ylabel("Predicted RPDf")
ax1c.set_title("Model Prediction: RPDf vs R — all batch sizes")
ax1c.legend()
ax1c.grid(True, alpha=0.3)

# -- 1D: Recommendation matrix heatmap (R × n) --
ax1d = axes[1, 1]
hm_rec = rec_matrix.pivot(index="R", columns="n", values="recommended_bs")
hm_pct = rec_matrix.pivot(index="R", columns="n", values="pct_recommended")

cmap_rec = {bs: BS_COLORS[bs] for bs in batch_sizes}
for i in range(len(hm_rec)):
    for j in range(len(hm_rec.columns)):
        bs_val = hm_rec.iloc[i, j]
        pct = hm_pct.iloc[i, j]
        color = cmap_rec.get(bs_val, "#999999")
        ax1d.add_patch(plt.Rectangle((j - 0.5, i - 0.5), 1, 1,
                                      facecolor=color, edgecolor="white", linewidth=2))
        # Color the text based on dominance
        text_color = "white" if pct > 0.7 else "black"
        ax1d.text(j, i, f"bs={bs_val}\n{pct:.0%}", ha="center", va="center",
                  fontsize=11, fontweight="bold", color=text_color)

ax1d.set_xticks(range(len(hm_rec.columns)))
ax1d.set_xticklabels(hm_rec.columns)
ax1d.set_yticks(range(len(hm_rec)))
ax1d.set_yticklabels(hm_rec.index)
ax1d.set_xlabel("n (instances)")
ax1d.set_ylabel("R (resource ratio)")
ax1d.set_title("Recommended Batch Size (R × n)\nbs15 vs bs20 split is clear")
ax1d.set_xlim(-0.5, len(hm_rec.columns) - 0.5)
ax1d.set_ylim(len(hm_rec) - 0.5, -0.5)

# Legend for colors
from matplotlib.patches import Patch
legend_elements = [Patch(facecolor=BS_COLORS[bs], edgecolor="white", label=f"bs={bs}") for bs in batch_sizes]
ax1d.legend(handles=legend_elements, loc="upper right", fontsize=9)

plt.tight_layout()
fig1.savefig(ANALYSIS_DIR / f"{prefix}_evidence_15vs20.png", dpi=200, bbox_inches="tight")
plt.close(fig1)
print(f"Saved: {prefix}_evidence_15vs20.png")

# =========================================================================
# Figure 2: All 4 batch sizes overview (6 panels)
# =========================================================================
fig2, axes = plt.subplots(3, 2, figsize=(16, 22))
fig2.suptitle(f"All Batch Sizes Comparison ({prefix})", fontsize=16, y=0.98)

# -- 2A: Pairwise box plots --
ax2a = axes[0, 0]
bs_pairs = list(combinations(batch_sizes, 2))
diff_long_data = {"pair": [], "diff": []}
for a, b in bs_pairs:
    diffs = pivot[a] - pivot[b]
    diff_long_data["pair"].extend([f"bs{a} vs\nbs{b}"] * len(diffs))
    diff_long_data["diff"].extend(diffs.values)
diff_long = pd.DataFrame(diff_long_data)

sns.boxplot(data=diff_long, x="pair", y="diff", ax=ax2a, width=0.55, legend=False)
ax2a.axhline(y=0, color="red", linestyle="--", linewidth=1.5)
ax2a.set_ylabel("RPDf difference (positive = left worse)")
ax2a.set_title("Pairwise RPDf Differences")
for i, (a, b) in enumerate(bs_pairs):
    diffs = pivot[a] - pivot[b]
    ax2a.text(i, diffs.mean() + diffs.std() * 0.35,
              f"μ={diffs.mean():.3f}\nmed={diffs.median():.3f}",
              ha="center", va="bottom", fontsize=7.5, fontweight="bold")

# -- 2B: Actual winner bar chart --
ax2b = axes[0, 1]
bars = ax2b.bar([str(bs) for bs in batch_sizes],
                [winner_counts.get(bs, 0) for bs in batch_sizes],
                color=[BS_COLORS[bs] for bs in batch_sizes],
                edgecolor="white", linewidth=1.5)
for bar, count in zip(bars, [winner_counts.get(bs, 0) for bs in batch_sizes]):
    ax2b.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + total * 0.01,
              f"{count}\n({count / total * 100:.1f}%)",
              ha="center", va="bottom", fontweight="bold", fontsize=11)
ax2b.set_ylabel("Number of instances won")
ax2b.set_title("Actual Winner per Instance")
ax2b.set_ylim(0, total * 1.15)

# -- 2C: Slicing heatmap --
ax2c = axes[1, 0]
heatmap_data = []
for _, row in slicing.iterrows():
    for bs in batch_sizes:
        heatmap_data.append({
            "param": row["parameter"],
            "value": row["param_value"],
            "batchSize": bs,
            "mean_RPDf": row[f"mean_bs{bs}"],
        })
heatmap_df = pd.DataFrame(heatmap_data)
heatmap_df["label"] = heatmap_df["param"] + "=" + heatmap_df["value"].astype(str)
pivot_hm = heatmap_df.pivot(index="label", columns="batchSize", values="mean_RPDf")
pivot_hm = pivot_hm.sort_values(by=max(batch_sizes))

sns.heatmap(pivot_hm, ax=ax2c, cmap="RdYlGn_r", annot=True, fmt=".3f",
            cbar_kws={"label": "Mean RPDf"}, linewidths=0.5)
ax2c.set_title("Mean RPDf per Parameter Slice")

# -- 2D: Prediction vs T --
ax2d = axes[1, 1]
t_data = interaction[interaction["parameter"] == "T"]
for bs in batch_sizes:
    vals = t_data["param_value"].values
    preds = t_data[f"pred_bs{bs}"].values
    ax2d.plot(vals, preds, "o-", label=f"bs={bs}", color=BS_COLORS[bs],
              linewidth=2.5, markersize=9)
ax2d.set_xlabel("T (time window)")
ax2d.set_ylabel("Predicted RPDf")
ax2d.set_title("Model Prediction: RPDf vs T")
ax2d.legend()
ax2d.grid(True, alpha=0.3)

# -- 2E: ANOVA F-stat per slice --
ax2e = axes[2, 0]
slice_plot_df = []
for _, row in slicing.iterrows():
    slice_plot_df.append({
        "label": f"{row['parameter']}={row['param_value']}",
        "anova_F": row["anova_F"],
        "anova_p": row["anova_p"],
        "winner": int(row["winner"]),
    })
slice_plot_df = pd.DataFrame(slice_plot_df)
slice_plot_df = slice_plot_df.sort_values("anova_F", ascending=True)
colors_sig = ["#F44336" if p < 0.05 else "#9E9E9E" for p in slice_plot_df["anova_p"]]

ax2e.barh(range(len(slice_plot_df)), slice_plot_df["anova_F"].values,
          color=colors_sig, edgecolor="white")
ax2e.set_yticks(range(len(slice_plot_df)))
ax2e.set_yticklabels(slice_plot_df["label"].values)
ax2e.set_xlabel("ANOVA F-statistic")
ax2e.set_title("ANOVA: Batch Size effect per slice\n(Red=significant, Gray=not)")
for i, (_, row) in enumerate(slice_plot_df.iterrows()):
    ax2e.text(row["anova_F"] + slice_plot_df["anova_F"].max() * 0.05, i,
              f"p={row['anova_p']:.4f}, winner=bs{row['winner']}",
              va="center", fontsize=8)

# -- 2F: Slice-level mean RPDf boxplot per batch size --
ax2f = axes[2, 1]
slice_means = []
for bs in batch_sizes:
    col_means = [row[f"mean_bs{bs}"] for _, row in slicing.iterrows()]
    slice_means.append(pd.Series(col_means, name=f"bs={bs}"))
slice_means_df = pd.DataFrame(slice_means).T
slice_means_df.columns = [f"bs={bs}" for bs in batch_sizes]

bp = ax2f.boxplot(slice_means_df.values, tick_labels=[f"bs={bs}" for bs in batch_sizes],
                  patch_artist=True, widths=0.6)
for patch, bs in zip(bp["boxes"], batch_sizes):
    patch.set_facecolor(BS_COLORS[bs])
ax2f.set_ylabel("Mean RPDf (per slice)")
ax2f.set_title("Distribution of slice-level mean RPDf")
for i, bs in enumerate(batch_sizes):
    mean_val = slice_means_df[f"bs={bs}"].mean()
    ax2f.plot([i + 1], [mean_val], "D", color="black", markersize=8)
    ax2f.text(i + 1, mean_val + slice_means_df[f"bs={bs}"].std() * 0.3,
              f"{mean_val:.3f}", ha="center", fontweight="bold", fontsize=9)

plt.tight_layout()
fig2.savefig(ANALYSIS_DIR / f"{prefix}_evidence_all.png", dpi=200, bbox_inches="tight")
plt.close(fig2)
print(f"Saved: {prefix}_evidence_all.png")

# =========================================================================
# Summary
# =========================================================================
print("\n" + "=" * 72)
print("EVIDENCE SUMMARY: bs=15 vs bs=20")
print("=" * 72)
print(f"""
1. DIFFERENCE (bs15 - bs20)
   Mean:   {d15vs20['mean']:.4f}  (near zero)
   Median: {d15vs20['median']:.4f}
   bs15 better: {(diff_15vs20 < 0).mean():.1%}, bs20 better: {(diff_15vs20 > 0).mean():.1%}

2. WIN RATE
""")
for bs in batch_sizes:
    w = winner_counts.get(bs, 0)
    print(f"   bs={bs}: {w}/{total} ({w/total*100:.1f}%)")

print(f"""
3. SLICING: bs15 wins {slice_bs15_wins}/{len(slicing)}, bs20 wins {slice_bs20_wins}/{len(slicing)}

4. DIMINISHING RETURNS
   bs5→bs15 gap: {d5vs15['mean']:.4f}
   bs5→bs20 gap: {d5vs20['mean']:.4f}
   bs15→bs20 gap: {-d15vs20['mean']:.4f}  (negligible)
""")
print("=" * 72)
