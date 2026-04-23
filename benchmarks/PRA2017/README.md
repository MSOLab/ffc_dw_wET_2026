# PRA2017 Benchmark Scripts

## Execution Order

| Step | Script | Output |
|------|--------|--------|
| 1 | `match_hybrid.py` | `pra2017_hybrid_match.csv` |
| 2 | `setup_large.py all` | `InstanceNameLarge.txt` + `best_seq_large/*.txt` |
| 3 | `gen_instance_table.py` | `pra2017_instance_table.csv` |
| 4 | `verify_ins_index.py` | (optional sanity check) |
| 5 | `compute_bks.py` | `pra2017_bks_table.csv` |

Each command must complete successfully before the next step.

## Commands

```bash
# Step 1: Match PRA2017 instances to hybridflowshop file numbers
uv run benchmarks/PRA2017/match_hybrid.py

# Step 2: Generate InstanceNameLarge.txt and split bestSeq_Large.txt
uv run benchmarks/PRA2017/setup_large.py all
# or individually:
uv run benchmarks/PRA2017/setup_large.py create
uv run benchmarks/PRA2017/setup_large.py split

# Step 3: Generate instance table CSV
uv run benchmarks/PRA2017/gen_instance_table.py

# Step 4: Verify ins_index consistency (optional)
uv run benchmarks/PRA2017/verify_ins_index.py

# Step 5: Compute BKS_T, BKS_F, BKS_calc for all 1440 instances
uv run benchmarks/PRA2017/compute_bks.py
```

## Standalone Visualization

```bash
# Render parallel_mc_pmtn C cost heatmap (default: due-window sort)
uv run benchmarks/PRA2017/visualize_parallel_mc_cost.py --instance <path_to_instance.txt>

# Render with NEH-CP priority sort
uv run benchmarks/PRA2017/visualize_parallel_mc_cost.py --instance <path_to_instance.txt> --sort neh-cp

# Render wET (weighted earliness + tardiness) penalty heatmap
uv run benchmarks/PRA2017/visualize_wET_cost.py --instance <path_to_instance.txt>

# Render wET heatmap with NEH-CP priority sort
uv run benchmarks/PRA2017/visualize_wET_cost.py --instance <path_to_instance.txt> --sort neh-cp
```

## Dependency Graph

```plaintext
match_hybrid.py
    │
    ▼ pra2017_hybrid_match.csv
setup_large.py split ────────► best_seq_large/*.txt
    │
    ├─────────────────────────► InstanceNameLarge.txt
    │
    ▼
gen_instance_table.py
    │
    ▼ pra2017_instance_table.csv
compute_bks.py ──────────────► pra2017_bks_table.csv
```

## Files Produced

| File | Produced by | Consumed by |
|------|-------------|-------------|
| `pra2017_hybrid_match.csv` | `match_hybrid.py` | `setup_large.py`, `gen_instance_table.py`, `verify_ins_index.py` |
| `InstanceNameLarge.txt` | `setup_large.py create` | `setup_large.py split` |
| `best_seq_large/*.txt` | `setup_large.py split` | `gen_instance_table.py`, `verify_ins_index.py` |
| `pra2017_instance_table.csv` | `gen_instance_table.py` | `compute_bks.py` |
| `pra2017_bks_table.csv` | `compute_bks.py` | — |
| `<instance_stem>_C_heatmap.html` | `visualize_parallel_mc_cost.py` | — |
| `<instance_stem>_wET_heatmap.html` | `visualize_wET_cost.py` | — |

### `pra2017_hybrid_match.csv`

1440 rows mapping PRA2017 filenames to hybridflowshop file numbers. Columns: `insIndex`, `ffc_ddw_sum_et_filename`, `hybridflowshop_filename`.

### `InstanceNameLarge.txt`

1440 lines of instance filenames in execution order. Used as the ordering reference for splitting `bestSeq_Large.txt`.

### `best_seq_large/*.txt`

Per-instance solution files. Each starts with a header `ins_index, num_jobs, num_stages, obj_value, method`, followed by stage-by-stage job sequences.

### `pra2017_instance_table.csv`

Summary table with columns `insIndex, n, c, totalMcCount, T, R, W, BKS` for all 1440 instances.

### `pra2017_bks_table.csv`

Computed benchmark results with columns `insIndex, n, c, totalMcCount, T, R, W, BKS_data, BKS_calc, BKS_T, BKS_F`.

- `BKS_T`: objective with `force_job_id_seq=True` (preserves best_seq order)
- `BKS_F`: objective with `force_job_id_seq=False` (FAM reordering)
- `BKS_calc`: minimum of BKS_T and BKS_F

### `<instance_stem>_C_heatmap.html`

Signed cost heatmap rendered by `visualize_parallel_mc_cost.py`. Blue = earliness cost, red = tardiness cost, white = in-window (zero cost). Job rows can be sorted by `due-window` (default) or `neh-cp` (`--sort neh-cp`).

### `<instance_stem>_wET_heatmap.html`

wET (weighted earliness + tardiness) penalty heatmap rendered by `visualize_wET_cost.py`. Each cell shows the actual penalty when a job completes at time t: `w⁻ × (d⁻ − t)` for earliness (blue), `w⁺ × (t − d⁺)` for tardiness (red), 0 in-window (white). Same `--sort` options as the C cost heatmap.
