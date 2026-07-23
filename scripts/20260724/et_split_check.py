"""Quick E/T-split diagnostic: split obj into weighted earliness vs tardiness
for semi vs active reconstruction, on one (kappa, TL) cell, to test whether
active degrades by packing jobs EARLY (=> earliness-dominated)."""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from ffc_ddw_sum_et.orchestration import BenchmarkLoader  # noqa: E402

RUN = Path("output/20260724_csr_k_f_cumulative_recon_ab/20260724T005703_124252")
BENCH = Path("benchmarks/PRA2017/large")
SRC = Path("benchmarks/PRA2017/pra2017_hybrid_match.csv")


def score(sol_path: str, inst) -> tuple[int, int]:
    d = json.load(open(sol_path))
    last = d["stages"][-1]
    cj: dict[str, int] = {}
    for op in d["operations"]:
        if op["stage"] == last and op["end"] > cj.get(op["job"], 0):
            cj[op["job"]] = op["end"]
    ewt, twt = inst.job_2_ewt_map, inst.job_2_twt_map
    dw = inst.job_2_due_window_map
    se = st = 0
    for j in inst.job_id_list:
        c = cj.get(j, 0)
        lo, hi = dw[j]
        se += ewt.get(j, 1) * max(lo - c, 0)
        st += twt.get(j, 1) * max(c - hi, 0)
    return se, st


def main() -> int:
    cell = sys.argv[1] if len(sys.argv) > 1 else "k1_tl15"
    loader = BenchmarkLoader(BENCH, ins_index_source=SRC)
    insts = {i.name: i for i in loader.load_all()}

    tot = {"semi": [0, 0], "active": [0, 0]}
    n = 0
    print(f"cell csr_{cell}  (per-instance sum_E / sum_T)")
    print(f"{'instance':40s} {'semi_E':>9} {'semi_T':>9} {'act_E':>9} {'act_T':>9}")
    semi_dirs = sorted(glob.glob(str(RUN / f"csr_{cell}_semi" / "Instance_*")))
    for sd in semi_dirs:
        name = Path(sd).name
        inst = insts.get(name)
        if inst is None:
            continue
        semi_sol = glob.glob(f"{sd}/*_solution.json")
        act_sol = glob.glob(str(RUN / f"csr_{cell}_active" / name / "*_solution.json"))
        if not semi_sol or not act_sol:
            continue
        se_s, st_s = score(semi_sol[0], inst)
        se_a, st_a = score(act_sol[0], inst)
        tot["semi"][0] += se_s
        tot["semi"][1] += st_s
        tot["active"][0] += se_a
        tot["active"][1] += st_a
        n += 1
        if n <= 15:
            print(f"{name:40s} {se_s:9d} {st_s:9d} {se_a:9d} {st_a:9d}")

    print(f"\nTOTALS over {n} instances:")
    for m in ("semi", "active"):
        e, t = tot[m]
        print(f"  {m:6s}  sumE={e:>12,}  sumT={t:>12,}  obj={e + t:>12,}  "
              f"E-share={e / (e + t) * 100:5.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
