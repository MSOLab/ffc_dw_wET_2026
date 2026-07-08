"""Sample test: 새 in-place ceiling vs 현 OLD(pre-coarsen window) — 정말 같은가?

두 방식:
  (OLD)  due window를 ⌈d/K⌉로 미리 coarsen 해두고, coarse 완료시각 C'를 그 coarsen된
         window와 직접 비교(plain Pan, K 없음).  partition:  C' vs ⌈d/K⌉
  (NEW)  window는 원본 d 그대로 두고, insert_idle_time 내부에서 K·C'를 원본 d와 비교
         (flooring/lookahead와 '동일 partition'), breakpoint만 ⌈d/K⌉.
                                              partition:  K·C' vs d

질문①: 두 partition이 job을 다르게 분류하는가?
질문②: 결과 스케줄(coarse 완료시각)과 원본-window obj가 같은가?

읽고, `uv run python ...` 로 직접 돌려보세요.
"""

import random

INF = 10**9


def ceil_div(x, K):  # ⌈x/K⌉  (정수 나눗셈)
    return -(-x // K)


# ---------------------------------------------------------------------------
# 질문① — 두 partition 규칙이 같은 집합을 만드는가? (job-by-job)
# ---------------------------------------------------------------------------
def partition_old(cprime, dlo, dhi, K):
    """OLD: coarse 완료 C'를 ⌈d/K⌉ window와 비교."""
    lo_c, hi_c = ceil_div(dlo, K), ceil_div(dhi, K)
    if cprime < lo_c:
        return "E"
    if cprime >= hi_c:
        return "T"
    return "D"


def partition_new(cprime, dlo, dhi, K):
    """NEW: K·C'를 원본 d와 비교 (flooring/lookahead와 동일 partition)."""
    kc = K * cprime
    if kc < dlo:
        return "E"
    if kc >= dhi:
        return "T"
    return "D"


def check_partition_equivalence(trials=200_000):
    rng = random.Random(0)
    mismatches = []
    for _ in range(trials):
        K = rng.randint(1, 64)
        dlo = rng.randint(0, 4000)
        dhi = dlo + rng.randint(0, 2000)
        cprime = rng.randint(0, 4000 // K + 5)
        a = partition_old(cprime, dlo, dhi, K)
        b = partition_new(cprime, dlo, dhi, K)
        if a != b:
            mismatches.append((K, dlo, dhi, cprime, a, b))
    return mismatches


# ---------------------------------------------------------------------------
# 질문② — 같은 partition을 쓰되, 스케줄 결과가 OLD와 같은가?
#   공유 skeleton: block/delta2 로직은 완전 동일, breakpoint만 인자로 받음.
#   OLD  = skeleton(window = ⌈d/K⌉,  K=1)      → partition C' vs ⌈d/K⌉, bp ⌈d/K⌉−C'
#   NEW  = skeleton(window = 원본 d, K=K)        → partition K·C' vs d,   bp ⌈d/K⌉−C'
# ---------------------------------------------------------------------------
def insert_idle_ceiling(starts, ends, dlo, dhi, ewt, twt, K):
    """단일 machine ceiling idle insertion (무조건 shift).

    partition:   K·C' vs d          (flooring/lookahead와 동일)
    breakpoint:  ⌈d/K⌉ − C'         (ceil)
    """
    starts, ends = starts[:], ends[:]
    n = len(ends)
    j = n - 1
    while j >= 0:
        be = j
        while be < n - 1 and starts[be + 1] == ends[be]:
            be += 1
        delta2 = starts[be + 1] - ends[be] if be < n - 1 else INF

        s_e, s_d, s_t = [], [], []
        for i in range(j, be + 1):
            kc = K * ends[i]
            if kc < dlo[i]:
                s_e.append(i)
            elif kc >= dhi[i]:
                s_t.append(i)
            else:
                s_d.append(i)
        sum_e = sum(ewt[i] for i in s_e)
        sum_t = sum(twt[i] for i in s_t)

        if sum_e > sum_t:
            bps = [ceil_div(dlo[i], K) - ends[i] for i in s_e]
            bps += [ceil_div(dhi[i], K) - ends[i] for i in s_d]
            d1 = min(bps) if bps else INF
            delta = min(d1, delta2)  # ceiling: delta ≥ 1 → 무조건 전진
            for i in range(j, be + 1):
                starts[i] += delta
                ends[i] += delta
            # j 고정, 재평가
        else:
            j -= 1
    return ends


def obj_original(ends, dlo, dhi, ewt, twt, K):
    """원본 window 기준 weighted E+T (K·C' vs d)."""
    tot = 0
    for i in range(len(ends)):
        kc = K * ends[i]
        tot += ewt[i] * max(0, dlo[i] - kc) + twt[i] * max(0, kc - dhi[i])
    return tot


def random_instance(rng):
    """coarse 그리드의 left-justified 단일-machine 초기 스케줄 하나 생성."""
    K = rng.randint(1, 32)
    n = rng.randint(1, 8)
    # coarse 처리시간 (>=1), left-justify → 초기엔 idle 0 (한 block)
    p = [rng.randint(1, 10) for _ in range(n)]
    ends, t = [], 0
    for pi in p:
        t += pi
        ends.append(t)
    starts = [ends[i] - p[i] for i in range(n)]
    # 원본 스케일 due window / 가중치
    dlo = [rng.randint(0, K * (t + 3)) for _ in range(n)]
    dhi = [dlo[i] + rng.randint(0, K * 5) for i in range(n)]
    ewt = [rng.randint(1, 9) for _ in range(n)]
    twt = [rng.randint(1, 9) for _ in range(n)]
    return K, starts, ends, dlo, dhi, ewt, twt


def check_schedule_equivalence(trials=100_000):
    rng = random.Random(1)
    fails = 0
    for _ in range(trials):
        K, starts, ends, dlo, dhi, ewt, twt = random_instance(rng)

        # NEW: 원본 d + K partition + ceil breakpoint
        new_ends = insert_idle_ceiling(starts, ends, dlo, dhi, ewt, twt, K)

        # OLD: window을 ⌈d/K⌉로 미리 coarsen + K=1 (plain Pan, coarse 완료 C' 직접 비교)
        dlo_c = [ceil_div(dlo[i], K) for i in range(len(dlo))]
        dhi_c = [ceil_div(dhi[i], K) for i in range(len(dhi))]
        old_ends = insert_idle_ceiling(starts, ends, dlo_c, dhi_c, ewt, twt, 1)

        # 비교: coarse 완료시각 자체 + 원본-window obj
        same_sched = new_ends == old_ends
        same_obj = obj_original(new_ends, dlo, dhi, ewt, twt, K) == obj_original(
            old_ends, dlo, dhi, ewt, twt, K
        )
        if not (same_sched and same_obj):
            fails += 1
            if fails <= 5:
                print("  MISMATCH:", K, starts, ends, dlo, dhi, ewt, twt)
                print("    new_ends:", new_ends, "old_ends:", old_ends)
    return fails


if __name__ == "__main__":
    print("질문① partition 동치성 (200k random):")
    mm = check_partition_equivalence()
    if not mm:
        print(
            "  → 불일치 0건. K·C' vs d  ==  C' vs ⌈d/K⌉ (모든 정수 C'에서 동일 partition)."
        )
    else:
        print(f"  → 불일치 {len(mm)}건! 예:", mm[:5])

    print("\n질문② 스케줄/obj 동치성 (100k random single-machine):")
    fails = check_schedule_equivalence()
    if fails == 0:
        print(
            "  → 불일치 0건. 새 in-place ceiling 결과 == OLD(pre-coarsen window) 결과."
        )
    else:
        print(f"  → 불일치 {fails}건.")
