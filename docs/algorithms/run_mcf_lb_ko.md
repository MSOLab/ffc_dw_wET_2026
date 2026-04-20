# `run_mcf_lb`

`FFcDDWSubroutineController.run_mcf_lb` — MCF 선점 하한을 시드로 삼아
FFcDDW 풀 스케줄 인컴번트를 만들어내는 엔드투엔드 파이프라인.

정의 위치: [controller.py](../../src/ffc_ddw_sum_et/orchestration/controller.py).

## 시그니처

```python
def run_mcf_lb(
    self,
    last_stage_only_timelimit: float | str | None = None,
    profile_fix_by_machine: bool = False,
    machine_precedence_stride: int = 1,
) -> SubroutineReport: ...
```

- `last_stage_only_timelimit` — Step 1 CP-SAT 솔버의 시간 예산.
  `_parse_nc_timelimit`로 파싱:
  - `None` → 제한 없음,
  - `float`/`int` → 초,
  - `"<x>nc"` → `float(x) * n * c` 초 (예: `"0.01nc"`).
- `profile_fix_by_machine`, `machine_precedence_stride` — Step 2-3에서
  `BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule`에
  그대로 전달.
- Step 2-3 CP-SAT 솔버는 현재 **시간 제한 없이** 실행됨.
- 두 CP-SAT 솔브 모두 `num_search_workers = 1`로 고정.

## 파이프라인

`n = instance.job_count`, `c = instance.stage_count`,
`last_stage_id = instance.stage_id_list[-1]`로 두고 진행.

### Step 1-1 — MCF 선점 하한

- `ParallelMachinePreemptionMcf.from_instance(instance)`를 만들고 solve.
- optimal 아니면 `RuntimeError`.
- `mcf_lb = mcf.get_obj_value()` 저장 (전체 과정의 `obj_bound`로 사용) 및
  우선순위 점수
  `mcf.get_job_2_avg_time_minus_half_processing_time_sum_map()` 계산
  (평균 flow 중점 − `(r_j + p_j)/2`; flow가 없는 job은 `None`).

### Step 1-2 — 마지막 스테이지 전용 디스패치 시드

- 우선순위 점수 오름차순으로 job 정렬; 동률은 native `job_id_list` 순서로
  타이브레이크; `None` 점수는 뒤로 밀림.
- 마지막 스테이지 전용 CP-SAT 모델 빌드:
  - `BaseModelBuilder.build(..., last_stage_only=True, job_2_release=r_j_map, obj_lb=mcf_lb)`,
    여기서 `r_j_map = instance.get_job_2_p_sum_except_last_stage()`.
  - `obj_lb=mcf_lb`는 `sum(et_terms) >= ceil(mcf_lb)` 컷을 추가.
- `FFcSchedule.dispatch_stage_by_jobs(last_stage_id, …, job_2_release=r_j_map)`로
  `mcf_job_sequence`를 마지막 스테이지에 디스패치해 `last_stage_only_init_schedule`을
  구성 (마지막 스테이지만 채워진 상태).

### Step 1-3 — 마지막 스테이지 전용 CP-SAT warm-start 및 solve

- `BaseModelBuilder.apply_{start,end}_hints_from_*_map`로 `last_stage_only_init_schedule`의
  start/end 값을 CP-SAT 힌트로 적용.
- `last_stage_only_timelimit` 아래에서 solve.
- `OPTIMAL`/`FEASIBLE` 둘 다 아니면: warning 로그 후
  `SubroutineReport(obj_value=None, obj_bound=mcf_lb)` 반환 — 인컴번트는
  등록되지 않음.
- 그 외에는 마지막 스테이지의 `(j, last_stage_id) → start/end`를 추출하고,
  `_build_schedule_from_op_starts(..., stages=[last_stage_id])`로 부분
  스케줄 `last_stage_only_schedule`을 만든 뒤
  `self.last_stage_cp_sat_solution: FFcDDWSolution`에 저장.

### Step 2-1 — 마지막 스테이지 고정 역방향 디스패치

`c == 1`이면 `dispatched_schedule = last_stage_only_schedule`로 단축. 그 외:

- CP-SAT 마지막 스테이지 종료 시각 내림차순으로 job 정렬, 동률은 native
  순서로 타이브레이크.
- `reversed_instance = FFcDDWParameters.reverse_stages(instance)`와, 그
  스테이지 구조에 맞춘 빈 `reversed_seed` 스케줄을 준비.
- CP-SAT 마지막 스테이지의 모든 op `(mc_id, s, e, j)`에 대해 **반사된**
  값을 `reversed_seed`에 삽입:
  `start = ls_makespan - e`, `end = ls_makespan - s`, 동일 머신.
- `MixedDispatcher(reversed_instance).get_best_mixed_schedule_by_sequence(...,
  schedule=reversed_seed, from_stage=reversed_instance.stage_id_list[1],
  criteria="makespan")`로 남은 역방향 스테이지를 채움.
- 디스패처가 `None`을 반환하면: warning 로그 후
  `SubroutineReport(obj_value=None, obj_bound=mcf_lb)` 반환 — 인컴번트
  없음.

### Step 2-2 — 언플립

`dispatched_schedule = reversed_full.as_reversed()`.

마지막 스테이지 op들은 `s + (M - ls_makespan)` 위치로 이동 (`M =
reversed_full.makespan`; 추가 `right_shift`는 수행하지 않음). 따라서
Step 1-3 CP-SAT 시각보다 오른쪽으로 밀릴 수 있음. 앞쪽 스테이지들은
구성상 feasible.

`compute_window_et(dispatched_schedule, instance)`로 `step2_obj` 계산 →
`solution_manager`에 중간 인컴번트로 등록 (`obj_bound=mcf_lb`).

### Step 2-3 — profile-fix CP-SAT 풀 솔브

- 풀 CP-SAT 모델 빌드 후
  `BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule`를
  `dispatched_schedule`에 적용.
- `dispatched_schedule`의 start/end 힌트로 warm-start.
- `num_search_workers = 1`로 solve.
- `obj_bound_final = max(mcf_lb, pf_solver.best_objective_bound)`
  (bound 획득 실패 시 `mcf_lb`로 fallback).
- `OPTIMAL`/`FEASIBLE` 둘 다 아니면: warning 로그 후 **Step 2-2** 인컴번트를
  `obj_value=step2_obj, obj_bound=obj_bound_final`로 반환.
- 그 외에는 모든 스테이지의 `(j, i) → start/end`를 추출,
  `_build_schedule_from_op_starts`로 `final_schedule`을 만들고
  `compute_window_et`로 ET 재계산, `solution_manager`에 최종 인컴번트 등록
  후 최종 `SubroutineReport` 반환.

재계산한 ET가 `pf_solver.objective_value`와 다르면 warning 로그는 남기되,
`compute_window_et` 값을 사용.

## 부수 효과

| 상태 | 설정 시점 |
| --- | --- |
| `self.solution_manager` (중간) | Step 2-2, `register(step2_obj)` |
| `self.solution_manager` (최종) | Step 2-3, `OPTIMAL`/`FEASIBLE`일 때 |
| `self.last_stage_cp_sat_solution` | Step 1-3, `OPTIMAL`/`FEASIBLE`일 때 |

직접적인 파일 I/O는 없음. `FFcDDWSingleInstanceRunner`가 이후에
`self.last_stage_cp_sat_solution.schedule`을
`<working_dir>/<ins>_last_stage_cp_sat_schedule.yaml`로 덤프.

## 조기 반환 경로

| 조건 | 반환 |
| --- | --- |
| MCF 비최적 | `RuntimeError` |
| Step 1-3 솔버 비가용 해 | `obj_value=None, obj_bound=mcf_lb` |
| Step 2-1 디스패처가 `None` 반환 | `obj_value=None, obj_bound=mcf_lb` |
| Step 2-3 솔버 비가용 해 | `obj_value=step2_obj, obj_bound=obj_bound_final` |

위 3개 경로에서는 `run_mcf_lb`가 최종 인컴번트를 등록하지 않음 (Step 2-2의
중간 인컴번트만 등록됨).

## 의존성

- [`ParallelMachinePreemptionMcf`](../../src/ffc_ddw_sum_et/algorithm/parallel_mc_pmtn.py)
- [`BaseModelBuilder`](../../src/ffc_ddw_sum_et/algorithm/cumulative.py) —
  `build(last_stage_only=, job_2_release=, obj_lb=)`,
  `apply_{start,end}_hints_from_*`,
  `add_stage_ops_precedence_constraints_after_dispatch_from_schedule`,
  `make_params`.
- [`MixedDispatcher`](../../src/ffc_ddw_sum_et/algorithm/dispatcher/mixed.py)
- [`FFcSchedule`](../../src/ffc_ddw_sum_et/solution/ffc_schedule.py) —
  `dispatch_stage_by_jobs`, `iter_operations_on_stage`,
  `add_ops_times_2_mc`, `as_reversed`,
  `get_jik_2_{start,end}_time_map`.
- [`FFcDDWParameters.reverse_stages`](../../src/ffc_ddw_sum_et/parameters/ffc_ddw_params.py)
- [`compute_window_et`](../../src/ffc_ddw_sum_et/solution/objectives.py)

## 관련

- `run_last_stage_cp_sat_lb` — Step 1-1~1-3의 독립판 (Step 2 없음).
  차이점: 디스패치 시드를 MCF **선점 시작 시각** 기준으로 정렬 (우선순위
  점수 아님); 디스패처에 넘기는 job별 release는 MCF 시작값이 `None`이면
  `r_j`로 폴백; CP-SAT 시간 예산은 `0.01 * n * c`로 고정.
  `self.last_stage_cp_sat_solution`은 동일하게 채우며, 인컴번트는 등록하지
  않음.
