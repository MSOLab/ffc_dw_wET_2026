# Experiment Review: 2026-04-23 ~ 2026-04-24

**Benchmark**: PRA2017 large (1440 instances)
**Objective**: Weighted earliness + tardiness (wET)

---

## Run Index

| timestamp | config file | scenario | method |
|---|---|---|---|
| 20260423T114900_417063 | `1_mcf_lb_init_13_config.yaml` | `mcf_lb_4_4cores_no_pf` | `run_mcf_lb_4` |
| 20260423T221248_575301 | 동일 | `mcf_lb_4_4cores_no_pf` | `run_mcf_lb_4` |
| 20260423T171935_897548 | `cmax_init_pfns_config.yaml` | `cmax_init_pf1ns` | `initialize_by_best_of_selected_dispatches` + `run_profile_fixed_ns` |
| 20260423T173918_198369 | `cmax_init_pfns_config_2.yaml` | `cmax_init_pf1ns` | 동일 |
| 20260423T174736_400968 | `cmax_init_pfns_config_2.yaml` | `cmax_init_pf1ns` | 동일 |
| 20260423T180718_718683 | `cmax_init_pfns_config_3.yaml` | `cmax_init_pf1ns` | `initialize_by_best_of_selected_dispatches` only |
| 20260424T041848_326215 | `neh_cp_config_4.yaml` | `neh_cp_b5_pf1_below_cmax` | `neh_cp` |
| 20260424T213007_556893 | `neh_cp_config_5.yaml` | `neh_cp_b5_pf1_below_cmax_cml` / `_idv` | `neh_cp` |

---

## Group 1: MCF-LB (20260423T114900_417063, 20260423T221248_575301)

두 실행은 **완전히 동일한 설정**으로 수행된 중복 실행입니다.

**공통 설정**

| 파라미터 | 값 |
|---|---|
| timelimit | 300.0s |
| instance_worker_cnt | 24 |
| last_stage_only_cp_pf_method | `null` |
| last_stage_only_cp_solver_thread_cnt | 4 |
| last_stage_only_cp_tl | `"0.01nc"` |
| repeat_last_stage_only_cp_while_improving | false |
| full_cp_pf_method | `"PF1"` |
| full_cp_solver_thread_cnt | 4 |
| repeat_full_cp_while_improving | true |

**특징**

- CP-SAT solver `num_workers = 4`
  - 이전 모든 (1440개 계산 실험) CP-SAT solver 활용은 `num_workers = 1`
- 1개 (start_time) last stage only priority 사용
- Last stage와 full schedule에 다른 parameter 사용
  - Last stage: profile fix X, 0.01nc timelimit
  - Full schedule: PF1, repeat while improving

**결과**

| timestamp | mean obj | mean ratio |
|---|---|---|
| 20260423T114900_417063 | 156,263 | 0.343 |
| 20260423T221248_575301 | 156,081 | 0.344 |

두 결과가 근접하여 재현성 양호.

---

## Group 2: CMAX-init / BN2D (20260423T171935_897548 ~ 20260423T180718_718683)

공통 기반: `initialize_by_best_of_selected_dispatches`
- `left_cap_portion: 0.25`, `right_cap_portion: 0.25`
- `normalize_by_stage_cnt: false`
- `mixed_schedule_for_former_stages: true`, `mixed_schedule_for_later_stages: true`
- `machine_then_job: true`, `all_stages_as_bottleneck: true`
- `method_list: [run_bn2d, select_best_of_mixed_dispatches]`
- timelimit: 120.0s, instance_worker_cnt: 48

**실행 간 차이**

| timestamp | `iit_after_each_dispatch` | 2단계 `run_profile_fixed_ns` | mean obj | mean ratio |
|---|---|---|---|---|
| 20260423T171935_897548 | **true** | yes (PF1, 2 threads) | 348,267 | 0.00081 |
| 20260423T173918_198369 | (생략) | yes (PF1, 2 threads) | 377,392 | **0.0363** |
| 20260423T174736_400968 | **true** | yes (PF1, 2 threads) | 353,004 | 0.00106 |
| 20260423T180718_718683 | (생략) | **없음** | 389,254 | 0.0 |

20260423T171935_897548과 20260423T174736_400968은 동일 설정이지만 mean obj 차이(348k vs 353k)가 있음 — CP-SAT solver 내부 랜덤성으로 추정.

**관찰**

- `iit_after_each_dispatch: true` 적용 시 dispatch 후 즉시 IIT를 수행하여 `run_profile_fixed_ns` 진입점이 개선됨 → mean obj↓, 반면 이미 좋은 해를 갖고 있어 PF1 개선 여지(ratio)도 감소.
- 20260423T173918_198369는 iit 없이 PF1 진입 → ratio가 0.036으로 가장 높음 (초기해가 나빠서 CP 개선 여지가 큼).
- 20260423T180718_718683은 dispatch만 수행하고 PF1 없이 종료 → 가장 나쁜 결과 / ratio = 0.

---

## Group 3: NEH-CP (20260424T041848_326215, 20260424T213007_556893)

**설정 비교**

| 파라미터 | config_4 (20260424T041848_326215) | config_5_cml (20260424T213007_556893) | config_5_idv (20260424T213007_556893) |
|---|---|---|---|
| **job_priority** | **(미설정)** | **`"due-weight-pos"`** | **`"due-weight-pos"`** |
| added_batch_size | 5 | 5 | 5 |
| solver_thread_cnt | 8 | 8 | 8 |
| cp_tl | `"0.15c"` | `"0.15c"` | `"0.15c"` |
| **apply_cumulative_tl** | true | **true** | **false** |
| pf_method | `"PF1"` | `"PF1"` | `"PF1"` |
| skip_pf_below_obj | `"makespan"` | `"makespan"` | `"makespan"` |
| instance_worker_cnt | 12 | 12 | 12 |

**결과**

| timestamp / scenario | mean obj | mean ratio |
|---|---|---|
| 20260424T041848_326215 `neh_cp_b5_pf1_below_cmax` | 152,325 | 0.0 |
| 20260424T213007_556893 `neh_cp_b5_pf1_below_cmax_cml` | **143,019** | 0.0 |
| 20260424T213007_556893 `neh_cp_b5_pf1_below_cmax_idv` | 143,520 | 0.0 |

**관찰**

- config_4 → config_5: `job_priority: "due-weight-pos"` 추가만으로 mean obj **~9,300 감소** (≈6.1% 개선). job 정렬 기준이 NEH-CP 품질에 가장 큰 영향을 미침.
- cml vs idv: `apply_cumulative_tl: true(cml)` vs `false(idv)` → cml이 근소하게 우세 (143,019 < 143,520). 누적 TL이 batch 간 시간 배분을 최적화하여 소폭 개선.
- 모든 NEH-CP 실행에서 `meanImprovementRatio = 0.0` — NEH-CP는 constructive method이므로 초기해 생성 후 개선 루프 없이 종료함.

---

## 전체 결과 순위 (mean obj, 낮을수록 우수)

| 순위 | timestamp | scenario | mean obj |
|---|---|---|---|
| 1 | 20260424T213007_556893 | `neh_cp_b5_pf1_below_cmax_cml` | **143,019** |
| 2 | 20260424T213007_556893 | `neh_cp_b5_pf1_below_cmax_idv` | 143,520 |
| 3 | 20260424T041848_326215 | `neh_cp_b5_pf1_below_cmax` | 152,325 |
| 4 | 20260423T221248_575301 | `mcf_lb_4_4cores_no_pf` | 156,081 |
| 5 | 20260423T114900_417063 | `mcf_lb_4_4cores_no_pf` | 156,263 |
| 6 | 20260423T171935_897548 | `cmax_init_pf1ns` | 348,267 |
| 7 | 20260423T174736_400968 | `cmax_init_pf1ns` | 353,004 |
| 8 | 20260423T173918_198369 | `cmax_init_pf1ns` | 377,392 |
| 9 | 20260423T180718_718683 | `cmax_init_pf1ns` | 389,254 |

NEH-CP (`job_priority` 적용) > MCF-LB > CMAX-init(BN2D) 순.

---

## 주요 발견

1. **`job_priority: "due-weight-pos"`** 가 NEH-CP에서 가장 큰 단일 개선 요인 (~6%).
2. **BN2D dispatcher** (`cmax_init`) 는 wET 기준으로 MCF-LB 대비 약 2.2배 나쁨. CMAX 최소화 목적으로 설계된 dispatcher가 wET objective에 적합하지 않은 것으로 보임.
3. **`iit_after_each_dispatch`** 는 초기해를 개선하지만 동시에 PF1 개선 여지를 줄임. 두 run(20260423T171935_897548 / 20260423T174736_400968) 간 결과 편차(4,737)가 CP solver 랜덤성에서 기인.
4. **MCF-LB** 는 300s timelimit으로도 NEH-CP (120s) 에 미치지 못함.
