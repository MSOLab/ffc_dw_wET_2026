"""Decompose active-vs-semi obj delta into earliness / tardiness per instance.

For each (k, f, instance) pair present in both the *_active and *_semi
scenarios of the merged run, load each solution.json, compute weighted
(earliness, tardiness) against the original-scale due window, and rank by
earliness increase (active - semi).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters

RUN = Path(
    "output/20260724_merge_recon_ab_vs_prior/20260724T073347_605861"
)
BENCH = Path("benchmarks/PRA2017/large")

_SCN = re.compile(r"^csr_k(\d+)_tl(\d+)_(active|semi)$")

# ---- instance cache: name -> (job_id_list, last_stage_id, dw, ewt, twt) ----
_inst_cache: dict[str, tuple] = {}


def load_instance(name: str):
    if name in _inst_cache:
        return _inst_cache[name]
    with open(BENCH / f"{name}.txt") as stream:
        inst = FFcDDWParameters.from_pra_2017_data(name, stream)
    last_stage = inst.stage_id_list[-1]
    payload = (
        list(inst.job_id_list),
        last_stage,
        dict(inst.job_2_due_window_map),
        dict(inst.job_2_ewt_map),
        dict(inst.job_2_twt_map),
    )
    _inst_cache[name] = payload
    return payload


def et_from_solution(sol_path: Path, name: str) -> tuple[int, int]:
    jobs, last_stage, dw, ewt_map, twt_map = load_instance(name)
    with open(sol_path) as f:
        data = json.load(f)
    # last-stage completion per job = max end among last-stage ops
    comp: dict[str, int] = {}
    for op in data["operations"]:
        if op["stage"] != last_stage:
            continue
        j = op["job"]
        e = int(op["end"])
        if e > comp.get(j, 0):
            comp[j] = e
    sum_e = 0
    sum_t = 0
    for j in jobs:
        c = comp.get(j, 0)
        lo, hi = dw[j]
        ewt = ewt_map.get(j, 1)
        twt = twt_map.get(j, 1)
        sum_e += ewt * max(lo - c, 0)
        sum_t += twt * max(c - hi, 0)
    return sum_e, sum_t


def main() -> int:
    # gather scenarios by (k,f) -> {mode: scndir}
    cells: dict[tuple[int, int], dict[str, Path]] = {}
    for d in sorted(RUN.iterdir()):
        if not d.is_dir():
            continue
        m = _SCN.match(d.name)
        if not m:
            continue
        k, f, mode = int(m.group(1)), int(m.group(2)), m.group(3)
        cells.setdefault((k, f), {})[mode] = d

    rows = []
    for (k, f), modes in sorted(cells.items()):
        if "active" not in modes or "semi" not in modes:
            continue
        a_dir, s_dir = modes["active"], modes["semi"]
        # instances present in active dir
        for inst_dir in sorted(a_dir.iterdir()):
            if not inst_dir.is_dir():
                continue
            name = inst_dir.name
            a_sol = inst_dir / f"{name}_solution.json"
            s_sol = s_dir / name / f"{name}_solution.json"
            if not (a_sol.exists() and s_sol.exists()):
                continue
            ea, ta = et_from_solution(a_sol, name)
            es, ts = et_from_solution(s_sol, name)
            rows.append(
                {
                    "k": k, "f": f, "name": name,
                    "E_semi": es, "T_semi": ts, "obj_semi": es + ts,
                    "E_active": ea, "T_active": ta, "obj_active": ea + ta,
                    "dE": ea - es, "dT": ta - ts, "dObj": (ea + ta) - (es + ts),
                }
            )
        print(f"  done cell k={k} f={f}: {len(rows)} rows so far", file=sys.stderr)

    import pandas as pd

    df = pd.DataFrame(rows)
    df.to_csv("scratch_et_decomp.csv", index=False)
    print(f"\nTotal paired instances: {len(df)}")
    print("\n=== aggregate (active - semi) ===")
    print(f"mean dE   = {df['dE'].mean():+.1f}")
    print(f"mean dT   = {df['dT'].mean():+.1f}")
    print(f"mean dObj = {df['dObj'].mean():+.1f}")
    print(f"instances with dE>0 (earliness up): {(df['dE']>0).sum()} / {len(df)}")
    print(f"instances with dT>0 (tardiness up): {(df['dT']>0).sum()} / {len(df)}")

    print("\n=== TOP 10 by earliness increase (dE) ===")
    cols = ["k", "f", "name", "E_semi", "E_active", "dE",
            "T_semi", "T_active", "dT", "dObj"]
    top = df.sort_values("dE", ascending=False).head(10)
    print(top[cols].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
