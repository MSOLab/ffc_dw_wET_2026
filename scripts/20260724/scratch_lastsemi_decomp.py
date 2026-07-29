"""E/T decomposition for the active_but_last_semi run: semi vs active vs lastsemi.

Tests the hypothesis: does keeping the LAST stage semi-active (while rebuilding
earlier stages active) recover the earliness blowup that pure `active` causes?
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters

RUN = Path("output/20260724_active_but_last_semi_csr_ab/20260724T135036_009404")
BENCH = Path("benchmarks/PRA2017/large")
_SCN = re.compile(r"^csr_k(\d+)_tl(\d+)_(semi|active|lastsemi)$")

_cache: dict[str, tuple] = {}


def load_instance(name: str):
    if name not in _cache:
        with open(BENCH / f"{name}.txt") as s:
            inst = FFcDDWParameters.from_pra_2017_data(name, s)
        _cache[name] = (
            list(inst.job_id_list),
            inst.stage_id_list[-1],
            dict(inst.job_2_due_window_map),
            dict(inst.job_2_ewt_map),
            dict(inst.job_2_twt_map),
        )
    return _cache[name]


def et(sol: Path, name: str) -> tuple[int, int]:
    jobs, last, dw, ewt, twt = load_instance(name)
    data = json.load(open(sol))
    comp: dict[str, int] = {}
    for op in data["operations"]:
        if op["stage"] == last:
            e = int(op["end"])
            if e > comp.get(op["job"], 0):
                comp[op["job"]] = e
    se = st = 0
    for j in jobs:
        c = comp.get(j, 0)
        lo, hi = dw[j]
        se += ewt.get(j, 1) * max(lo - c, 0)
        st += twt.get(j, 1) * max(c - hi, 0)
    return se, st


# gather (k,f) -> {mode: dir}
cells: dict[tuple[int, int], dict[str, Path]] = {}
for d in sorted(RUN.iterdir()):
    m = _SCN.match(d.name) if d.is_dir() else None
    if m:
        cells.setdefault((int(m[1]), int(m[2])), {})[m[3]] = d

rows = []
for (k, f), modes in sorted(cells.items()):
    if not {"semi", "active", "lastsemi"} <= set(modes):
        continue
    for inst_dir in sorted(modes["semi"].iterdir()):
        if not inst_dir.is_dir():
            continue
        name = inst_dir.name
        sols = {mo: modes[mo] / name / f"{name}_solution.json" for mo in modes}
        if not all(p.exists() for p in sols.values()):
            continue
        r = {"k": k, "f": f, "name": name}
        for mo in ("semi", "active", "lastsemi"):
            e, t = et(sols[mo], name)
            r[f"E_{mo}"], r[f"T_{mo}"], r[f"obj_{mo}"] = e, t, e + t
        rows.append(r)

df = pd.DataFrame(rows)
df.to_csv("scratch_lastsemi_decomp.csv", index=False)

for a, b in [("active", "semi"), ("lastsemi", "semi"), ("active", "lastsemi")]:
    dE = (df[f"E_{a}"] - df[f"E_{b}"]).mean()
    dT = (df[f"T_{a}"] - df[f"T_{b}"]).mean()
    dO = (df[f"obj_{a}"] - df[f"obj_{b}"]).mean()
    print(f"{a:>9} - {b:<9}: mean dE={dE:+9.1f}  dT={dT:+9.1f}  dObj={dO:+9.1f}")

print("\n=== mean obj by mode ===")
print(df[["obj_semi", "obj_active", "obj_lastsemi"]].mean().to_string())

print("\n=== per-cell mean dObj (active-semi | lastsemi-semi) ===")
g = df.groupby(["k", "f"]).apply(
    lambda x: pd.Series(
        {
            "act-semi": (x.obj_active - x.obj_semi).mean(),
            "last-semi": (x.obj_lastsemi - x.obj_semi).mean(),
            "recovery%": 100
            * (
                1
                - (x.obj_lastsemi - x.obj_semi).sum()
                / max((x.obj_active - x.obj_semi).sum(), 1)
            ),
        }
    ),
    include_groups=False,
)
print(g.to_string(float_format=lambda v: f"{v:+.1f}"))

print("\n=== the two headline instances (Rep3=1088, Rep4=1089) ===")
h = df[df.name.str.contains("Rep3|Rep4")][
    [
        "k",
        "f",
        "name",
        "E_semi",
        "E_active",
        "E_lastsemi",
        "obj_semi",
        "obj_active",
        "obj_lastsemi",
    ]
]
print(h.to_string(index=False))
