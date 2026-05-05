# Weekly Algorithms Review: 2026-04-29 ~ 2026-05-05

chat: `claude --resume ae4ae82d-7774-42bb-a939-c8db011fae87`

이번 주 실험을 RUN-by-RUN 시간순이 아니라 **두 개의 연구 질문**으로 다시 묶은 요약.
chronological view는 [`20260505_weekly_experiments.md`](./20260505_weekly_experiments.md) 참고.

---

## Q1 — Last-stage-only schedule만으로 BKS를 이길 수 있는가?

### 채택된 흐름

```
apply_lb_by_mcf
  → heuristic_last_stage_only_sch_from_mcf_lb
       (job_priority="end_time", placement_priority="dist")
```

CP solver를 안 쓰는 빠른 휴리스틱 경로. CP 변종(`single_pass_*`, `neh_cp_*`)은 controller integration이 미완으로 incumbent 미등록(이번 주 RUNs 4–9) 또는 CP cost 대비 개선 미미했고, 결국 heuristic 경로가 모든 후속 RUN의 표준이 됨.

### 알고리즘 동작 (`heuristic_last_stage_only_from_mcf_lb`)

`src/ffc_ddw_sum_et/algorithm/mcf_lb/last_stage_only.py:96`

1. **MCF preemptive 해**에서 각 job j의 last-stage activity window `(t_min_j, t_max_j)` 추출.
2. **`job_priority`**로 정렬 → 이 순서대로 한 job씩 배치 (`_insert_jobs_at_desired_starts`).
3. 각 job j: 원하는 시작점

   ```
   desired_start_j = max((t_min_j + t_max_j - p_j) // 2,  job_2_release[j])
   ```

   = "operation을 window 중앙에 정렬"을 release time으로 floor한 값. 즉 `(desired_start + desired_end) / 2 ≈ (t_min + t_max) / 2`.
4. 만약 `[desired_start, desired_start + p_j)` 슬롯이 **단 하나의 last-stage 머신에서라도** 비어 있으면 거기 그대로 박는다.
5. 모두 occupied면 두 후보를 만든다:
   - (A) `desired_start` **이후** 가장 빨리 시작 가능한 슬롯
   - (B) `desired_start + p_j` **이전**에 가장 늦게 끝나는 슬롯 (없을 수도 있음)
   - **`placement_priority`**로 lex tie-break하여 둘 중 하나 선택.
6. 모든 job 배치 후:
   - `make_semi_active(start_from=last_stage, job_2_release_map)` — upstream release 기준으로 last-stage left-shift.
   - `insert_idle_time(...)` — ET-optimal 위치로 idle 삽입.
7. 최종 schedule에서 `weighted_earliness + tardiness` 계산 → `obj_value`.

### `job_priority="end_time"`의 의미

`src/ffc_ddw_sum_et/algorithm/pm_pmtn_sorter.py:88`

```python
sort_key(j) = (
    0 if window is not None else 1,    # window 있는 job 먼저
    float(window[1]),                  # ← t_max ASC (preemptive window 끝)
    -(ewt[j] + twt[j]),                # 같으면 총 가중치 큰 job 먼저
    job_2_pos[j],                      # 같으면 native order
)
```

→ **MCF preemptive 해에서 last-stage activity가 가장 일찍 끝나는 job을 가장 먼저 배치**한다. 직관: MCF가 "이 job은 빨리 끝나야 한다"고 본 job (보통 due가 타이트한 job)에게 우선권을 줘서, 후순위 job들의 충돌을 흡수한다.

비교용 다른 키들 (모두 동일한 호출 구조, 2번째 sort 컴포넌트만 다름):

| key | 2번째 sort 컴포넌트 | 직관 |
|---|---|---|
| `start_time` | `t_min` ASC | preemptive 시작이 이른 job 먼저 |
| `end_time` ✅ | `t_max` ASC | preemptive 끝이 이른 job 먼저 (tightest deadline) |
| `start_time_maxw` | `t_min` ASC, tie: `-max(ewt,twt)` | start_time + 한 쪽 weight 큰 job 우선 |
| `end_time_maxw` | `t_max` ASC, tie: `-max(ewt,twt)` | end_time + 한 쪽 weight 큰 job 우선 |
| `1_rj_prmp_rel_dev` | `(t_max-t_min)/p_j` ASC | window 폭 / 처리시간 비율 (tight slack) |
| `1_rj_prmp_abs_dev` | `(t_max-t_min) - p_j` ASC | window 절대 슬랙 |

### `placement_priority="dist"`의 의미

`src/ffc_ddw_sum_et/algorithm/mcf_lb/last_stage_only.py:443`

후보 (A) earliest-start vs (B) latest-end 사이 lex tie-break:

| value | 1차 | 2차 |
|---|---|---|
| `"dist"` ✅ | `start_distance`(desired_start와의 거리) MIN | `weighted_ET_contrib` MIN |
| `"contrib"` | `weighted_ET_contrib` MIN | `start_distance` MIN |

→ **`dist`: desired_start에 가까운 후보 우선, ET 영향은 동률일 때만 본다**. 직관: midpoint placement의 "원래 위치"를 최대한 보존, 이후 `make_semi_active` + `insert_idle_time`이 ET를 후처리로 교정.

### Exploration 흐름 (Phase 3 → 7)

| RUN | step | priority key | 결과 |
|---|---|---|---|
| 4 (5/2) | `neh_cp_last_stage_only_sch_from_mcf_lb` | `1_rj_prmp_rel_dev`, `start_time` | bestObj 미등록 (controller integration 미완) |
| 5 | NEH-CP last-stage + `placement_priority="dist"` | 동일 | 동일 |
| 6 | `single_pass_last_stage_only_sch_from_mcf_lb` (renamed) | 동일 | 동일 |
| 7 | single_pass + SSOT refactor | 동일 | 동일 |
| 8 (5/2) | single_pass | `end_time`, `*_maxw` 신규 | 동일 — 4개 priority 비교 무산 |
| 9 | single_pass | + `placement_priority` × {contrib, dist} 6셀 | 동일 |
| 15 (5/3, hjt5950x) | **`heuristic_last_stage_only_sch_from_mcf_lb`** 첫 시도 | `end_time`, `dist` (default→default 사실상) | 산출물 없음(별도 머신) |
| 16 (5/3, mso02) | heuristic | `end_time`, `dist` | bestObj 등록 — 본격 측정 시작 |

**Lesson**: CP-기반 step (single_pass / neh_cp)은 controller integration이 어려웠고, heuristic step은 CP-free라 자체 obj 평가가 쉽고 후속 step(`build_full_sch_*`)에 incumbent로 등록됨. **그 결과 priority/placement key 비교는 heuristic 경로에서만 의미 있게 측정됨** — 실질적 비교 데이터가 부족해서 `end_time` + `dist`가 default로 굳어졌고, 이후 RUN(16~36)에서는 priority/placement 변경 없이 다른 노브(p, r, adjust)만 sweep.

### 경험적 결과 — last-stage-only obj vs BKS (직접 비교)

**전제 정리**. `lastStageOnlyObj`(per-instance YAML의 `last_stage_only_obj` 필드 + 별도 `<timestamp>_last_stage_only_obj.csv`)는 **last-stage operations만 채워진 schedule에 대해** `compute_weighted_earliness_tardiness`를 돌린 값이다. 그 값과 full-schedule BKS를 직접 비교하는 건 fair하지 않다(서로 다른 schedule space) — 그러나 사용자가 지적한 대로 "쉬운 문제(last-stage-only)를 일단 잘 풀 수 있는가?"의 sanity check로는 의미 있는 비교다.

**데이터 출처**: `<timestamp>_last_stage_only_obj.csv` (RUN 10~36의 build_full_sch 흐름 RUN 21개에 존재; `single_pass_*` 만 돌린 RUN 4~9는 last-stage-only obj 기록 없음). 별도로 모아 `analysis/last_stage_only_vs_bks_20260505.csv`에 저장. **PRA2017 large 1440 인스턴스 전부 포함**. (BKS_data=0인 인스턴스 58개도 valid set의 일부로 본다 — 알고리즘이 그 인스턴스에서도 wET=0을 도달했는지가 중요한 평가 신호.)

**Baseline 시나리오** — `apply_lb_by_mcf(p_inc=0, r_mult=1.0, r_inc=0) → heuristic_last_stage_only_sch_from_mcf_lb(end_time, dist) → build_full_sch_from_last_stage_only_sch`. 이 흐름의 last-stage-only obj (RUN 27/29/33/34/35/36 base 시나리오 기준 — 동일 결과):

| 측정 | 값 |
|---|---|
| **n_beats_bks** (`last_stage_only_obj < BKS`) | **1155 / 1440 (80.2%)** |
| **n_ties** (양쪽 0) | 9 / 1440 (0.6%) — BKS=0 인스턴스 58개 중 9개에서 lso=0 도달 |
| **n_loses** | 276 / 1440 (19.2%) — 그 중 49개는 BKS=0 + lso>0 |
| **mean last_stage_only_obj** | **49,794** |
| **mean BKS_data** | **79,030** |
| **lso / BKS 평균 비율** | **0.63** (약 37% 작음) |

→ **쉬운 문제(last-stage-only)는 heuristic이 매우 잘 풀고 있다**. 1440개 중 1155개에서 last-stage 단독 wET가 BKS보다 작고, 평균적으로도 BKS의 63% 수준. `apply_lb_by_mcf → heuristic_last_stage_only(end_time, dist)`은 last-stage placement 자체에서는 BKS-like (혹은 그 이상의) 품질을 만든다.

→ **BKS=0 영역 (T=0.2 위주 58개)에서의 동작**: 9개에서 알고리즘도 wET=0 도달 (tie), 49개에서는 wET>0 (lose). 따라서 알고리즘이 "due window가 매우 넓어서 trivially 0 도달 가능한" 인스턴스에서도 항상 0을 만들지는 못함 — 이건 알고리즘의 한계 신호로, 본문에서 별도 다룰 가치가 있음.

**같은 알고리즘이지만 base가 동일한 모든 RUN**에서 동일한 결과 (deterministic). pct_beats=80.2% 시나리오들:

- RUN 27 `build_full_sch_p+0_rx2+0` (60-grid의 (0, 0) 셀, r_mult=1.0)
- RUN 29 `build_full_sch_p+0_r+0` (80-grid의 (0, 0) 셀)
- RUN 33 / 34 / 35 `build_full_sch_base` (adjust off, single pass)
- RUN 36 `calc_mcf_lb_and_derive_full_sch_adjust_none` (composite step round 1만)

→ **알고리즘이 base에서 deterministic하다는 것의 실증** — 6개 RUN이 정확히 같은 1155 wins / 9 ties / 49,794 mean을 낸다. (RUN 13/16/19는 직전 코드 버전이라 약간 다른 1152 wins / 13 ties / 49,835 mean — `4ca477d` 등의 부산물.)

**노브를 켜면 lso obj가 일부러 악화된다** — 이는 의도된 trade-off:

| 시나리오 | RUN | pct_beats_lso | mean_lso |
|---|---|---|---|
| base (p+0, rx2 1.0, r+0) | 27/29/33/34/35/36 | 80.2% | 49,794 |
| `r_inc=2` | 19 | 80.1% | 50,015 |
| `r_mult=1.5` (best r_mult cell) | 17 | 미세 감소 | (similar) |
| `p_inc=16` | 14, 16 | 13.0% | (heavy increase) |
| `p+16, rx2 (r_mult=2.0)` | 18, 22 | **~0.1%** | (very heavy) |
| `p+0, rx2 (r_mult=2.0)` | 28 | 26.3% | 98,327 |
| `p+0_r_adjust` (round 2 adjust_r) | 30, 31 | ~17% | (heavy) |
| `p_adjust` (round 2 adjust_p) | 32 | ~19% | (medium) |

→ **p/r 노브는 last-stage 단독 obj를 망친다 (의도)**. last-stage 자체로는 base가 가장 좋고, 노브를 켜는 이유는 **build_full_sch 단계에서 reverse-dispatch가 풀 schedule을 더 잘 만들도록 schedule space에 여유를 주기 위함**. adjust 라인은 한 술 더 떠서 "두 번째 패스에서 lso obj 80%대 손실을 감수하고" full wET을 줄이는 trade.

### 그럼 왜 full-schedule wET는 BKS를 못 이기는가?

이번 주 build_full_sch 흐름의 mean RPDf (full wET 기준, **n=1440**, BKS=0 포함):

| RUN | scenario | mean RPDf | n_beats_bks (full wET < BKS) |
|---|---|---|---|
| 35 | `build_full_sch_p_adjust_r_half_adjust` | 0.5889 | ~196 / 1440 (~13.6%) |
| 36 | `calc_mcf_lb_and_derive_full_sch_adjust_pr` | 0.5889 | (동치) |
| 35 | `build_full_sch_p_adjust_r_adjust` | 0.5973 | (similar order) |
| 1·2 | `best` (전주 NEH-CP+MCF-LB 직렬) | 0.2740 | (더 많은 wins) |

**대비**: last-stage-only 단독으로는 80.2% 인스턴스에서 BKS 이김 → reverse-dispatch + unflip으로 upstream까지 채우면 ~14% 인스턴스로 떨어진다. 즉 **upstream stages를 채우면서 last-stage 시작 시점이 뒤로 밀리는 손실**이 알고리즘의 주된 약점.

**해석**: heuristic이 만든 last-stage timing은 (mostly) due-aligned이지만, `build_full_sch_from_last_stage_only_sch`의 reverse-dispatch 단계에서 upstream stages가 last-stage start time을 밀어내면서 tardiness가 폭발한다. p_increment / r_increment / adjust_r 등의 노브들은 본질적으로 **이 밀림을 보정하려는 시도** (release/processing time을 부풀려서 last-stage가 더 늦게 시작하도록 사전 조정 → reverse-dispatch 후 upstream space 확보).

→ **다음 단계 후보는 reverse-dispatch 단계 자체 개선**이거나, last-stage 시점을 더 보수적으로 잡는 다른 priority/placement 키 탐색. 또는 NEH-CP를 끝에 한 번 더 붙여 CP-SAT으로 full schedule을 polish (`mcf_lb_then_neh_cp` RUN 3이 그 시도였으나 통합 step 자체가 후퇴 — 통합 방식의 문제이지 컨셉은 유효할 수 있음).

---

## Q2 — Last-stage-only로부터 full schedule 만들기: 최종 알고리즘과 그 과정

### 최종 채택: `calc_mcf_lb_and_derive_full_sch`

`src/ffc_ddw_sum_et/orchestration/controller.py:1351` (`af944e3` 도입, `metadata/20260505/mcf_lb_init_38_config.yaml`로 RUN 36 검증).

#### Round 1 (항상 실행)

```
1. apply_lb_by_mcf
     입력: instance
     출력: (mcf_lb, MCFPreemptiveSchedule, MCFLBDiagnostic)
2. heuristic_last_stage_only_sch_from_mcf_lb
     입력: instance, mcf_preemptive_schedule
            job_priority="end_time", placement_priority="dist"
     출력: (last_stage_only_schedule, last_stage_only_obj)
3. build_full_sch_from_last_stage_only_sch
     입력: last_stage_only_schedule (reverse-dispatch + unflip — Phase 3 of MCF-LB pipeline)
     출력: (full_schedule, full_wET) → AlgRecord incumbent로 등록
```

#### Round 2 (조건부)

발동 조건: `(adjust_p or adjust_r) and (incumbent_makespan − preemptive_makespan > 0)`.

- `adjust_p=True`이면 `apply_lb_by_mcf`에 `adjust_p_by_full_sch_and_last_stage_only_pmtn_sch=True` 인자 전달.
- `adjust_r=True`이면 `adjust_r_by_full_sch_and_last_stage_only_pmtn_sch=True` 인자 전달.

→ 두 번째 패스에서 `apply_lb_by_mcf`가 incumbent와 첫 패스 preemptive schedule의 makespan 차이를 보고 동적으로 `p_adjust`/`r_adjust`를 계산해서 `p_increment`/`r_increment`에 더한다. 그 위에서 round 1과 동일한 3-step 재실행.

→ **delta가 0이면 round 2 스킵** (이게 `af944e3`의 핵심 — Phase 11에서 6-step YAML 흐름이 했던 "무의미한 두 번째 패스"를 자동 회피).

#### `apply_lb_by_mcf`의 release/processing 노브 (`controller.py:426`)

| 노브 | 의미 | LB 글로벌 보장 |
|---|---|---|
| `p_increment` | last-stage processing time을 일률적으로 `p_j + p_increment`로 부풀림 | ❌ (≠ 0이면 LB 무효) |
| `r_multiplier` | release date를 `ceil(r_j × r_multiplier)`로 스케일 | ❌ (> 1.0이면 LB 무효) |
| `r_increment` | `r` 스케일 후 정수 가산 (`+ r_increment`) | ❌ (> 0이면 LB 무효) |
| `adjust_p_by_full_sch_and_last_stage_only_*_sch` | round 2에서 incumbent vs reference schedule의 makespan delta로 `p_adjust` 동적 계산 | ❌ |
| `adjust_r_by_full_sch_and_last_stage_only_*_sch` | 같은 구조의 r 보정 | ❌ |
| `adjust_r_by_half` | r_adjust를 절반만 적용 (보수적) | ❌ |

`_only_sch` vs `_only_pmtn_sch`:

- `_only_sch` (RUN 30~33): reference로 last-stage-only 결과 schedule의 makespan 사용.
- `_only_pmtn_sch` (RUN 34~36, `0a8a9b0`): reference로 MCF preemptive schedule의 makespan 사용. 두 flag는 mutually exclusive (`ValueError`).

### Exploration 흐름 (Phase 5 → 12)

큰 흐름은 4개 축의 점진 확장 + 마지막에 composite step으로 통합.

#### 축 1: full-schedule 확장 step 신설 (Phase 5, 5/2)

| RUN | 변경 | bestObj |
|---|---|---|
| 10 | `build_full_sch_from_last_stage_only_sch` 첫 도입 | 217,354 (mean) — 디버깅용 첫 측정 |
| 11 | method arg fix + heatmap 위치 + ddw cache | 153,015 |
| 12 | "delay ls-only ops before flip" | 152,781 |

→ Phase 5 디버깅 사이클 후 base RPDf ≈ 0.69.

#### 축 2: `p_increment` (Phase 6, 5/3)

| RUN | p_inc range | best RPDf | 추세 |
|---|---|---|---|
| 13 | 0/1/2/4/8 | 0.6524 (p_inc=8) | 0→8 단조 개선 |
| 14 | 16/32 | 0.6739/0.7985 | 16에서 turnaround, 32에서 악화 |
| 16 (heuristic) | 0/1/2/4/8/16/32/64 | 0.6509 (p_inc=8) | 64에서 1.06 폭락 |

→ p_increment 최적은 ~8 근방.

#### 축 3: `r_multiplier` + `r_increment` (Phase 8·9, 5/3 ~ 4)

| RUN | knob | 최적 | 한계 |
|---|---|---|---|
| 17 | r_mult ∈ {1.0, 1.1, 1.25, 1.5, 2.0, 3.0, 4.0, 8.0} | 1.5 → 0.6531 | 8.0 → 1.27 |
| 19 | r_inc ∈ {0..4096} | 256 → 0.6638 | 4096 → 1.16 |

→ r_mult은 1.5 근처, r_inc은 256 근처가 sweet spot.

#### 축 4: p × r 그리드 (Phase 10, 5/4)

| RUN | grid | r_mult | best cell |
|---|---|---|---|
| 27 | p ∈ 0..16 × r_inc ∈ 0..256 (60셀) | 1.0 | (p+8, rx2+128) → 0.6214 |
| 28 | 동일 그리드 | 2.0 | (p+4, rx2+1) → 0.6449 |
| 29 | p ∈ 0..64 × r ∈ 0..256 (80셀) | 1.0 | (p+8, r+128) → 0.6214 |

→ 정적 노브 sweep의 plateau ≈ 0.62. p≥32에서 일관 악화.

#### 축 5: incumbent-기반 동적 adjust (Phase 11, 5/4 ~ 5/5)

여기서 정적 노브의 한계를 넘어선다 — incumbent와 reference schedule의 makespan 차이로 p/r을 자동 보정.

| RUN | 시나리오 | base | 최우수 |
|---|---|---|---|
| 30 | adjust_r 단발 (오타 `aujust`) | — | 0.6180 |
| 31 | `p+{0..32}_r_adjust` sweep | — | 0.5836 (p+4) |
| 32 | `p_adjust` only / `p_adjust + r_adjust` | — | 0.6016 (p_adjust) |
| 33 | base / p_adj / r_adj / p+r adj / r_half adj / p+r_half adj (6 시나리오 정식 비교) | 0.6951 | **0.5761** (`p_adjust_r_half_adjust`) |

→ `r_half`(절반만 보정)가 fully 보정보다 우월 → adjust 폭이 너무 크면 over-correction. `p_adjust + r_half_adjust` 조합이 sweet spot.

#### 축 6: adjust 입력 source 변경 + dispatch 양방향 (Phase 12, 5/5)

| RUN | 변경 | 6 시나리오 비교 |
|---|---|---|
| 34 | `_only_sch` → `_only_pmtn_sch` (reference: ls-only 결과 → preemptive schedule 직접) | p_adjust 계열 -0.005~-0.018 개선, r_adjust 단독 +0.007 약간 후퇴 (mixed) |
| 35 | + `c039ceb` Phase 3에서 `machine_then_job` 양방향 시도 후 better-makespan 채택 | 6/6 모두 개선, `p_adjust_r_adjust` -0.057 (가장 큼) → **0.5413** (`p_adjust_r_half_adjust`) |
| 36 | composite step `calc_mcf_lb_and_derive_full_sch` (4 시나리오: `adjust_{none, p, r, pr}`) | round 2 스킵 로직 정합성 확인 — `adjust_pr` 0.5413 (RUN 35의 `p_adjust_r_adjust` 0.5514보다도 우월) |

→ **dispatch try-both이 단일 변경 중 가장 임팩트 큰 개선**. composite step은 동작 동등성 + delta>0 스킵으로 시간 절약.

### 결과 알고리즘 요약 한 문장

> MCF preemptive LB → end_time 순 + dist 동률 처리로 last-stage 휴리스틱 schedule 구성 → reverse-dispatch(양방향 시도, lower-makespan 채택)로 full schedule 확장 → incumbent와 preemptive schedule의 makespan delta가 양수이면 그 delta를 p/r에 동적으로 더해 한 번 더 같은 사이클 반복.

### 다음 후보

- adjust_r_by_half이 fully 보정보다 우월 → adjust_p에도 `_by_half` 옵션 검토 가치.
- composite step에서 round 2 발동 비율 측정 (얼마나 자주 스킵되나) — Phase 2 결과 분석에 추가하면 알고리즘 비용/효과 trade-off 명확.
- 14% 인스턴스에서 BKS 이김 → BKS 갱신 candidate. 그 인스턴스들의 (R, T, n) 분포 파악 → 어떤 인스턴스 family에 강한지.
- heuristic 경로가 NEH-CP+MCF-LB 직렬 (`best`)에 평균 0.26 RPDf 뒤짐 → "heuristic + adjust" 조합의 위에 NEH-CP를 얹는 hybrid 검토 (`mcf_lb_then_neh_cp` 통합 step의 0.7330 후퇴 원인 함께 진단).
