# SW-CP TL-policy — 세 가지 k 측정 기준 (A / B1 / B2)

**Date:** 2026-07-06 · **대상 스크립트:** `k_for_capture.py`
**관련 문서:** `ANALYSIS_DESIGN.md` (Step D), `regression_summary.md`,
`plans/experiment/20260705/sw_cp_tl_policy_investigation.md` §3.2 / §8 item 4

---

## TL;DR

`k_for_capture.py`는 size-proportional per-window time limit
`TL = k · non_time_fixed_op_count`에서 **k를 얼마로 잡아야
achievable UB improvement의 p%를 포착하는가**를 묻는다.
단일 k가 아니라 **세 가지 집계 기준**으로 k를 제시하며,
각 기준은 "p%를 포착했다"를 다르게 정의한다.

- **A** — 전체 objective 총합 기준 (I-weighted). 큰 window가 지배.
- **B1** — per-window 필요 k의 분포 (median / P75 / P90). 동일 가중, 분포로 제시.
- **B2** — per-window 포착 비율의 단순 평균 (unweighted mean). 모든 window 동등.

> ⚠️ 세 기준 모두 **OFFLINE replay** (plan §5 caveat). SW-CP window는
> 순차적으로 풀리기 때문에 window i를 일찍 자르면 window i+1의 시작
> incumbent가 바뀜 — 실제로는 trajectory 전체가 달라진다. 여기서는
> 관측된 within-window curve를 가상의 더 짧은/긴 시계 예산으로
> 재생(replay)한 방향성 증거일 뿐이다. 진짜 증명은 end-to-end A/B
> (plan §8 item 5).

---

## 배경: 왜 k를 구하는가

`sw_cp_tl_profile` run은 모든 window에 관대한 고정 cap (120 s)을 주어
돌린다. 이 trajectory에서 "만약 각 window에 `τ_i = k · ntf_i`초만 줬다면
얼마나 잡았을까"를 offline으로 재생할 수 있다. 그 k를 p% 포착 기준으로
맞추면, 실제 운용 시 120 s 대신 `k · ntf`를 per-window TL로 쓸 수 있다.

목표: 120 s 고정 정책을 size-proportional 정책으로 교체할 근거를
얻거나/기각하는 것. k의 절대값과 난이도(size vs difficulty)에 따른
분포를 본다.

---

## Per-window primitives (모든 기준이 공유)

각 window i에 대해 (from `analyze_tl_policy.py::collect_rows`):

| 기호 | 정의 | 소스 |
|---|---|---|
| `I_i` | achievable improvement = `incumbent_obj_before - incumbent_obj_after` (accepted row only; 그 외 `I=0`) | `step_log.yaml` |
| `ntf_i` | `non_time_fixed_op_count` = `unfixed + profile_fixed` | `step_log.yaml` |
| `t_p^i` | window i가 자기 `I_i`의 p%에 도달한 시점 (절대 초) | `window_metrics.csv::t_{p}_abs` |
| `captured_i(τ)` | τ초까지만 window를 돌렸을 때 잡는 improvement | `analyze_tl_policy.py::captured_at` |

`captured_at(r, τ)`의 핵심 경계 처리 (주석 인용):

> CP-SAT이 cap 120s 도달 후에도 마지막 accepted incumbent를 0.0–0.8s
> 늦게 기록. 그래서 `τ >= wall_seconds`로 full `I`를 인정하면 constant
> 기준이 인위적으로 깎임 (이전 "+10.3 pp" artifact의 원인). 대신
> `τ >= TL`이면 full `I`를 인정하고, 그 미만에서는 관측된 curve를
> 그대로 읽는다.

세 기준은 모두 이 `captured_i(τ)`와 `τ_i = k · ntf_i`를 결합하되,
**k를 어떤 단일 값으로 요약하는가**가 다르다.

---

## A — full-sweep total objective (I-weighted)

**질문:** "전체 run의 achievable improvement 총합 대비, k를 썼을 때
포착한 총합이 p%가 되는 k는?"

**정의:**

```
Σ_i captured_i(k · ntf_i) / Σ_i I_i  =  p
```

- 분자: `Σ_i captured_i(k · ntf_i)` — 모든 window의 포착량 합
- 분모: `Σ_i I_i` — 모든 window의 achievable improvement 합
- k는 `find_k(cap_a, target=p/100)`로 이진탐색. `cap_a(k)`는 k에 대해
  단조증가이므로 bisection이 잘 작동한다.

**특성:**

- I-weighted → I가 큰 window가 분자를 지배한다.
- 논문이 말하는 "전체 objective 개선" 관점과 가장 가까운 방향.
- 같은 포착 비율 p에 대해선 세 기준 중 **가장 작은 k**를 요구하는 경향
  (B2보다 작거나 같음) — 큰 window는 보통 빨리 포화(saturate)하기
  때문.

**대응:** `analyze_tl_policy.py`의 Step D "captured %"가 바로
A 기준의 p=100% 변환 버전 (constant vs proportional 비교).

---

## B1 — per-subproblem required-k distribution

**질문:** "각 window가 **자기 자신의** p%에 도달하는 데 필요한 k를
직접 계산했을 때, 그 k 분포의 median / P75 / P90은 얼마인가?"

**정의:**

```
k_i = t_p^i / ntf_i           (window i가 자기 p%에 도달한 시간 / 그 window 크기)
→ median(k_i), P75(k_i), P90(k_i)
```

- I-weighting 없이 모든 window 동등취급하되, **분포**로 보여준다.
- P90 k를 쓰면 "전체 window의 90%가 자기 p%에 도달하는 k"가 된다.
  (나머지 10%는 자기 p%에 못 미치는 window.)
- 수치 하나가 아니라 범위로 제시 → policy 설계자가 보수도(분위수)를
  고를 수 있다.

**특성:**

- `t_p^i`가 정의 안 된 window (I=0 이거나 p%에 도달 못 한 경우)는
  스킵. I=0 window는 원래 from regression에서도 빠짐.
- 분포 통계이므로 outlier / 긴 꼬리를 그대로 노출한다. 극단적으로
  어려운 window 하나가 P90을 크게 올릴 수 있음.
- `find_k` bisection을 쓰지 않는다 — 각 window의 k_i를 O(N)으로
  계산하고 numpy `percentile`로 요약.

**언제 유용:** policy를 단일 k가 아니라 "P75 k" 같은 분위수로
설정하고 싶을 때. 또는 "어떤 window가 k를 크게 만드는 원인인가"를
추적할 때.

---

## B2 — per-subproblem unweighted-mean fraction

**질문:** "k를 얼마로 잡아야 **윈도우별 포착비율(captured_i/I_i)의
단순 평균**이 p%가 되는가?"

**정의:**

```
mean_i[ captured_i(k · ntf_i) / I_i ]  =  p
```

- 모든 window를 **동일 가중**으로 취급 (I 크기로 weighting 안 함).
- A와는 반대 방향: A는 큰 window에 민감, B2는 작은 window도 동등히
  반영.
- `find_k(cap_b2, target=p/100)`로 bisection. `cap_b2(k)`도 k에 대해
  단조증가.

**특성:**

- 작은 window가 빨리 포화(saturate)하므로 같은 p에 대해 **A ≤ B2** 경향.
  즉 B2가 더 큰 k를 요구.
- I=0인 window는 `captured_i/I_i = 0/0`이 되므로 `find_k`에
  넣기 전에 미리 제외해야 한다. `k_for_capture.py`는 `rows`에서
  `I>0`만 필터링한 상태로 들어간다.

**언제 유용:** small-window 성능을 보존하려 할 때. 모든 window가
최소한 p%는 잡게 하고 싶을 때.

---

## 세 기준 비교

| 속성 | A (I-weighted) | B1 (분포) | B2 (unweighted mean) |
|---|---|---|---|
| 가중 | I 크기로 가중 | 동일 가중 (분위수) | 동일 가중 (평균) |
| 출력 형태 | 단일 k | median / P75 / P90 | 단일 k |
| 계산 | bisection on `Σ captured / Σ I` | per-window k_i → numpy percentile | bisection on `mean(captured/I)` |
| 큰 window의 영향 | 지배적 | 동등 (단, outlier는 P90 부풀림) | 동등 |
| 같은 p에 대한 k | 가장 작음 (경향) | (분위수별로 다름) | 가장 큼 (경향) |
| 답하는 질문 | "전체 objective를 p% 잡는 k?" | "대다수 window가 p% 잡는 k?" | "평균 window가 p% 잡는 k?" |

직관적 관계 (같은 p에 대해):

```
A_k  ≤  B2_k           (A는 큰 window에 의해 낮은 k로도 p% 도달)
B1 median_k  ≈  B2_k   (둘 다 동일 가중이지만, B1은 median, B2는 mean)
B1 P90_k  >  B2_k       (P90은 극단 어려운 window까지 커버)
```

---

## 결정 기준 / 언제 무엇을

- **논문/보고서용 headline이 필요하면 A.** "120 s 고정 대신
  k=0.X s/op를 쓰면 전체 objective의 p%를 잡는다"는 식의 단일
  statement.
- **policy를 분위수로 설정하고 싶으면 B1.** "P75 k로 설정하면 75%의
  window가 p%에 도달한다"는 식의 robust한 설계.
- **작은 window 성능을 보존해야 하면 B2.** 모든 window가 최소 p%를
  잡게 하려면 B2 k가 필요.
- **세 기준의 k가 크게 다르면** window difficulty 분포가 넓다는 뜻 —
  이땐 단일 k가 아니라 분위수(B1)나 난이도 feature 회귀(regression
  §3)로 가는 게 맞다. 세 기준의 k가 비슷하면 단일 k로 충분.

---

## 동작 흐름 (k_for_capture.py)

```
collect_rows(run_dir)                       # analyze_tl_policy 재사용
  → rows: list[dict], 각 dict에 I_i, ntf_i, t_p^i, _curve 가짐
filter I>0

for p in {50, 80, 90, 95, 99}:
    A:   find_k(cap_a,  p/100)              # bisection on Σ captured/Σ I
    B2:  find_k(cap_b2, p/100)              # bisection on mean(captured/I)
    B1:  k_i = t_p^i / ntf_i  (where t_p defined)
         median, P75, P90  of k_i

print table:  p | A_k A_TL@medNtf | B2_k B2_TL@medNtf |
                  B1_medk B1_P75k B1_P90k B1_TL@medNtf(P90)
```

`TL@medNtf = k * median(ntf)` — k가 초/op 단위이므로, 전형적인
window 크기(median ntf)에 대한 TL 초값. 현재 고정 120 s와 비교하기
위한 레퍼런스.

---

## 한계 / 주의사항

1. **OFFLINE replay** — sequential coupling 무시 (plan §5 caveat).
   window i를 일찍 자르면 window i+1의 시작 incumbent가 달라지므로
   실제로는 trajectory 전체가 바뀜. Bisection으로 구한 k는
   "관측된 curve를 가상으로 재생한" 방향성 증거.
2. **`captured_at` 경계 처리**에 의존. `τ >= TL`에서 full `I`
   인정. 이 규칙이 바뀌면 (예: 엄격히 `τ >= wall_seconds`)
   artifact가 다시 생길 수 있음 — analyze_tl_policy.py의 주석 참조.
3. **I=0 window 처리** — A는 자동으로 0/0 문제 회피 (Σ I 분모에 0
   기여 안 함). B2는 `I>0` filter로 사전 제거. B1은 `t_p`가 None인
   window를 스킵 (I=0이면 t_p도 None).
4. **B1 P90과 B2 mean의 관계** — 둘 다 동일 가중이지만 B1은
   percentile, B2는 mean. 분포가 좌우대칭이면 비슷하지만, 긴 꼬리가
   있으면 P90이 mean보다 큼. 해석 시 주의.
5. **median ntf로 TL 환산** — 인스턴스 내에서 ntf가 거의 일정하므로
   (plan §3.1 obs 1), median ntf × k가 실제 window TL과 거의
   일치하지만, cross-instance/cell 비교에서는 ntf 분포가 다를 수
   있음. `min/max`도 같이 출력되니 확인할 것.

---

## 참고 자료

- `analyze_tl_policy.py` — `captured_at()` (line 547), `equal_budget_comparison()` (line 597)
- `k_for_capture.py` — `cap_a`, `cap_b2`, B1 percentile 계산
- `ANALYSIS_DESIGN.md` §Step D — equal-budget 비교의 설계 spec
- `regression_summary.md` — 현재 run의 회귀 결과
- `plans/experiment/20260705/sw_cp_tl_policy_investigation.md` §3.2 — headline
  correction (artifact 수정 내역), §8 item 4-5 — remaining work
