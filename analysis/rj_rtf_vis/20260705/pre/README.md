# rj_rtf_vis — pre-fix partition Gantt SVGs (20260705)

## Run command

```
uv run python main.py --config metadata/20260705/rj_rtf_vis_compare.yaml
```

## Source output directory

```
/home/hjt/code/ffc_dw_wET_2026/output/20260705/20260705T172514_244116/compare_rj_rtf/Instance_20_2_2_0,2_0,2_10_Rep0/progress/
```

Files found there (and copied into this directory):
- `2-sw_cpstep_000_partition.svg`
- `2-sw_cpstep_001_partition.svg`
- `2-sw_cpstep_002_partition.svg`

## Code state

Pre-fix code (bbaf408): all-stages right-justify
(`delay_job_latest_leq_obj_contrib_all_stages`).

## Config

`metadata/20260705/rj_rtf_vis_compare.yaml`

## What to visually expect (pre-fix)

LTF (left-time-fixed) blocks pushed to the right, with a wide left dummy/gap
before them, because the pre-fix right-justify logic is applied at all
stages instead of only the last stage.

## Observations from the emitted SVGs

Each SVG has a legend mapping region name -> fill color:
`LTF=#90a4ae`, `LPF=#ffcc80`, `UNFIXED=#81c784`, `RPF=#ff9800`, `RTF=#607d8b`.
The x-axis maps to schedule time via `time = (x_px - 47.45) / 1.0714`
(horizon = 882 for all 3 steps).

- **step_000** (`unfixed=[0,2)`): only a single narrow `LTF`-colored block
  appears, at time ~627-642 (far right of the horizon), not at time 0.
  Meanwhile `RTF`-colored blocks already span from time ~312 all the way to
  the horizon end (882) — i.e. the right-fixed region already occupies more
  than half the horizon at the very first step.
- **step_001** (`unfixed=[2,4)`): `LTF` still confined to the same narrow
  ~627-642 slice; `LPF` (light-orange) has grown to span time 0-687;
  `RTF` still runs to the horizon end.
- **step_002** (`unfixed=[4,6)`): `LTF` finally expands to cover time
  0-641 (13 path segments), but `RTF` continues to span from time ~534
  to the horizon end (882).

This is consistent with the expected pre-fix symptom: in early steps the
`LTF` region's job blocks are absent/compressed near the left edge (a wide
apparent left dummy/gap from time 0 up to ~627) while content is packed
toward the right end of the horizon, and the `RTF` region eagerly claims
the full tail of the horizon from the very first step — i.e. right-justify
behavior bleeding into stages/regions where only the last stage should be
right-justified.
