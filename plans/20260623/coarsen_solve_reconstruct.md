# Plan: `coarsen_solve_reconstruct`

## Context

사용자 목표는 FFc-DDW 인스턴스의 시간 해상도를 강하게 낮춘 뒤 base CP로 풀고,
그 결과를 원래 해상도 schedule로 복원했을 때의 성능을 확인하는 것이다.

- RQ1: processing time과 due window를 극단적으로 coarsen하면 base CP가 빠르게
  optimal을 증명하는가?
- RQ2: coarsened CP 해를 reconstruct한 원척도 schedule은 NEH-CP나 LB-init 대비
  얼마나 좋은가/나쁜가?

참조 구현은 hybridflowshop의
`initialize_by_tau_coarsened_cp`이다. 다만 이번 FFc-DDW 작업에서는 이름을
`coarsen_solve_reconstruct`로 두고, 실험 범위를 다음으로 고정한다.

- processing time: `ceil(p_ij / 50)` 정수값 사용. `p_ij > 0`이므로 결과는
  `>= 1`이어야 한다.
- due window lower/upper: `ceil(d^-_j / 50)`, `ceil(d^+_j / 50)` 사용.
  예: due window 범위가 `96..749`이면 `2..15`.
- solve 단계: coarsened instance에 FFc-DDW base CP를 적용한다. v1은 primary
  solution 1개만 reconstruct한다(아래 "v1 범위" 참조). CP-SAT solution pool은
  단일 해 RQ2 결과가 정당화한 뒤에만 도입한다.
- reconstruct 단계: coarsened CP solution의 operation start/end time을 `factor`
  배로 inflate한 뒤, 모든 operation의 **start time은 유지**하고 operation별 duration만
  원래 `p_ij`로 되돌린다. 이후 `make_semi_active`와 `insert_idle_time`을 적용하고,
  원래 due window/weight로 weighted E/T를 재계산한다.

읽은 문서/코드:

- `docs/problem-description.md`: 목적함수는 마지막 stage completion time 기준
  `sum_j w^-_j E_j + w^+_j T_j`.
- `docs/algorithm-principles.md`: 새 알고리즘 실행 단위는 `AlgSpec -> run -> AlgRecord`
  계약 안에 두고, controller/reporting concern을 algorithm 내부로 끌어오지 않는다.
- `TODOS.md`: 이번 작업과 충돌하는 deferred TODO 없음.
- `src/ffc_ddw_sum_et/algorithm/cpsat_adapter.py`: full-instance base CP adapter.
- `src/ffc_ddw_sum_et/algorithm/cumulative.py`: base CP model builder.
- `src/ffc_ddw_sum_et/orchestration/controller.py`: `solve_base_model_cpsat`,
  `neh_cp`, `pw_cp` step contract 및 `_register` 패턴.
- `/home/hjt/code/hybridflowshop/.../hfs_cp_lns.py`: tau coarsening은
  `ceil(p/tau)` surrogate를 푸는 선례로만 참조한다. 이번 FFc-DDW 설계는
  hybridflowshop의 `machine_sequence`/`stage_sequence` restore를 쓰지 않고,
  coarsened solution의 inflated start time을 기준으로 duration을 원래 값으로
  되돌리는 reconstruct를 사용한다.

## Design

### 1. Coarsened instance 생성

새 helper를 `FFcDDWParameters` 쪽에 두는 방향이 가장 단순하다.

후보 이름:

- `FFcDDWParameters.coarsen_time_resolution(instance, factor: int) -> Self`

동작:

- `factor > 0` 검증.
- `p_manager.df`를 복사해 모든 processing time에 `ceil(value / factor)` 적용.
- due window map의 `(lower, upper)` 각각에 `ceil(value / factor)` 적용.
- earliness/tardiness weights, job/stage/machine layout, generation params는 보존.
- 새 instance name은 예: `"{instance.name}_coarsen50"`.
- due window coarsening 후에도 `lower <= upper`이어야 한다. 원래 `lower <= upper`이고
  같은 양의 factor로 ceil하므로 유지된다.

### 2. Coarsened base CP solve

새 algorithm adapter를 두는 것을 우선한다.

후보 파일:

- `src/ffc_ddw_sum_et/algorithm/coarsen_solve_reconstruct.py`

후보 타입:

- `CoarsenSolveReconstructOption`
  - `factor: int = 50`
  - `timelimit_sec: float | None = None`
  - `solver_thread_cnt: int = 1`
  - `log_search_progress: bool = False`
  - `error_if_infeasible: bool = False`
- `CoarsenSolveReconstructAdapter`
  - `algorithm_id = "coarsen_solve_reconstruct"`

`run(spec)` 흐름:

1. 원본 `FFcDDWParameters` 검증.
2. factor=50 coarsened instance 생성.
3. coarsened instance를 base CP로 solve(primary solution 1개).
4. coarsened solve가 infeasible/no-solution이면 `AlgRecord`에 상태와 metrics만 담아 반환.
5. coarsened CP의 raw `(job, stage)` op start/end를 원척도 schedule로 reconstruct한다.
6. 원래 instance 기준 weighted E/T를 계산해 결과 schedule로 둔다.

#### v1 범위와 DRY

- v1은 **primary solution 1개만** 다룬다. CP-SAT `solution_pool_size` /
  `fill_additional_solutions_in_response` / `additional_solutions`는 현재 코드베이스에
  전혀 없고, additional solution 값은 `solver.value(var)`가 아니라
  `response_proto.additional_solutions[k].values[var.index]`로 인덱스 접근해야 하며,
  pool은 중복/소수 해만 반환하는 경우가 많아 신뢰성이 낮다. 따라서 pool은 단일 해
  RQ2 결과가 도입을 정당화한 뒤에만 추가한다.
- `CoarsenSolveReconstructAdapter`는 `CpsatAdapter`의 build/solve/extract/postprocess를
  통째로 복붙하지 않는다. v1처럼 단일 해만 필요하면, coarsened instance에 대해
  base CP를 푸는 **raw op-start 추출 헬퍼**(예: `cumulative.BaseModelBuilder`로 build →
  solve → `op_start`/`op_end` dict 반환)를 공유하고, reconstruct만 CSR 고유 로직으로
  둔다. `CpsatAdapter`가 돌려주는 schedule은 coarsened 스케일에서 이미
  `make_semi_active`+`insert_idle_time`(coarsened due window)을 적용한 후처리 결과라
  raw start가 아니므로, adapter 자체를 그대로 wrap하지는 않는다.

주의:

- coarsened solve의 `obj_value`/`obj_bound`는 coarsened scale의 surrogate objective이므로
  원척도 objective bound로 사용하지 않는다.
- controller의 현재 valid LB를 coarsened CP에 그대로 넘기면 scale이 맞지 않는다.
  이번 실험에서는 coarsened CP에 원척도 `obj_lb`를 주지 않는다.
- coarsened `ref_solution` warm-start는 원척도 schedule과 시간이 달라 바로 쓸 수
  없으므로 1차 구현에서는 지원하지 않는다. ref_solution이 없으므로 coarsened solve의
  horizon은 `BaseModelBuilder` 기본값(`sum(p)`)을 그대로 쓰고, `horizon_makespan_multiplier`
  옵션은 두지 않는다(YAGNI).

### 3. Reconstruct

reconstruct:

1. coarsened solution에서 `(job, stage)`별 raw start/end를 읽는다.
2. 각 operation의 시작 시각을 `factor`배로 inflate해 고정한다.
   - `reconstructed_start[j,i] = coarse_start[j,i] * factor`
3. operation duration은 원래 processing time `p_ij`로 되돌린다.
   - `reconstructed_end[j,i] = reconstructed_start[j,i] + original_p[j,i]`
4. machine 배정은 기존 `build_schedule_from_op_starts(instance,
   reconstructed_start, reconstructed_end)`를 그대로 재사용해 coloring한다.
   별도 builder/helper는 두지 않는다.

`build_schedule_from_op_starts` 재사용이 안전한 이유:

- 이 빌더는 start/end만으로 greedy interval-graph coloring을 한다.
- reconstruct는 시작 시각을 고정한 채 duration만 `ceil(p/factor)*factor >= p`에서
  원래 `p`로 줄이므로, reconstructed interval은 inflated interval의 부분집합이다.
  동시성은 줄어들 뿐 늘지 않아 cumulative 용량(`<= |M_i|`)이 그대로 보장되고,
  coloring은 항상 성공한다(빌더의 "no free machine" 분기에 걸리지 않음).
- 목적함수는 last-stage completion time(`reconstructed_start + p`)에만 의존하고
  머신 식별자와 무관하므로, coarsened의 특정 머신 배정을 보존할 필요가 없다.
- 같은 이유로 stage precedence도 보존된다: coarsened에서
  `start[j,i+1] >= start[j,i] + ceil(p/factor)`이면 inflate 후
  `reconstructed_start[j,i+1] >= reconstructed_start[j,i] + factor*ceil(p/factor)
  >= reconstructed_end[j,i]`. 따라서 별도 schedule validation 단계도 필요 없다.

복원 후 후처리(base CP 경로 `cpsat_adapter.py`와 동일 순서):

- `schedule.make_semi_active(instance.stage_2_job_2_p_map)`
- `schedule.insert_idle_time(instance.job_2_due_window_map, instance.job_2_ewt_map,
  instance.job_2_twt_map)`
- `compute_weighted_earliness_tardiness(schedule, original_instance)`로 obj 재계산.

### 4. Controller step

`FFcDDWSubroutineController`에 step method 추가:

```python
def coarsen_solve_reconstruct(
    self,
    factor: int = 50,
    timelimit: float | str | None = None,
    solver_thread_cnt: int = 1,
    log_search_progress: bool = False,
    error_if_infeasible: bool = False,
    draw_gantt: bool = False,
) -> SubroutineReport:
    ...
```

계약:

- step entry에서 `start_elapsed = time.monotonic()`.
- stopping condition이면 `_make_stop_report(start_elapsed)` 반환.
- `timelimit`은 기존 `solve_base_model_cpsat`처럼 `resolve_value_expr` 후 remaining
  global time과 strict-min.
- `CoarsenSolveReconstructAdapter` 호출.
- `elapsed = time.monotonic() - start_elapsed` 직후 `SubroutineReport` 생성.
- `_register(...)`는 최대 한 번만 호출.
- `_register` 뒤에 artifact/gantt 저장 같은 post-work를 수행한다.

metrics에 남길 값:

- `factor`
- `coarsened_instance_name`
- coarsened CP `status`, `obj_value`(surrogate), `obj_bound`(surrogate), `elapsed`
- reconstructed schedule의 원척도 `obj_value`, `makespan`

### 5. Experiment config

`metadata/20260623/` 아래에 pilot config를 둔다.

후보 시나리오:

1. `csr50_only`
   - `coarsen_solve_reconstruct`
   - `factor: 50`
   - `solver_thread_cnt: 24`
2. `neh_cp_baseline`
   - 기존 `neh_cp` 설정 중 가장 비교 가능한 안전 baseline.
3. `lb_init_baseline`
   - 현재 repo에서 LB-init 역할을 하는 `calc_mcf_lb_and_derive_full_sch`
     또는 이미 쓰는 MCF-LB initialization flow.
4. 선택: `csr50_then_pw_cp`
   - RQ2에서 순수 reconstruct 품질과 후속 refinement seed 품질을 분리하고 싶을 때만.

CP-SAT solution pool 시나리오(`csr50_pool` 등)는 v1 범위에서 제외한다.
단일 해 RQ2 결과가 pool 도입을 정당화하면 그때 추가한다.

RQ1 지표:

- coarsened CP status (`OPTIMAL`/`FEASIBLE`/timeout)
- coarsened CP elapsed seconds
- coarsened CP first feasible time
- coarsened CP optimality gap
- 원래 base CP 대비 solve status/elapsed가 가능하면 함께 기록

RQ2 지표:

- 원척도 weighted E/T objective
- NEH-CP 대비 `%gap = (CSR - NEHCP) / NEHCP * 100`
- LB-init 대비 `%gap = (CSR - LBInit) / LBInit * 100`
- makespan과 last-stage completion 분포

## Work Packages (per-file, sonnet subagent 단위)

각 work package(WP)는 **하나의 production 파일 + 그 전용 test 파일**을 단위로 하며,
독립 sonnet subagent가 cold start로 실행할 수 있도록 자족적으로 기술한다. 모든
subagent는 시작 시 아래 공통 컨텍스트를 먼저 읽는다.

### 모든 subagent 공통 사전 컨텍스트

- 이 plan 파일 전체(특히 `## Design`, `## Risks / Decisions`).
- `CLAUDE.md` 루트, `docs/algorithm-principles.md`(algorithm 경계 계약),
  `docs/problem-description.md`(목적함수 정의).
- 실행: `uv run python`, 변경 후 `uv run ruff check`, 필요 시 `uv run ruff format`.
- TDD: 각 WP는 test를 먼저 red로 만들고 production 코드로 green을 만든 뒤 refactor.

### 의존성 / 실행 순서

```
WP-A (parameters factory)
   └─> WP-B (CSR adapter+reconstruct)   ──┐
                                          ├─> WP-C (controller step) ──> WP-E (pilot config)
WP-D (reporting symbol, 독립/optional) ───┘
```

- WP-A → WP-B → WP-C → WP-E는 순차 의존(앞 WP의 public 시그니처에 의존).
- WP-D는 순수 문자열 등록이라 WP-C의 method 이름(`coarsen_solve_reconstruct`)만
  알면 언제든 병렬 실행 가능.
- 순차 의존 WP는 앞 WP가 green/ruff 통과한 뒤 dispatch한다.

---

### WP-A — coarsened instance factory

- **대상 파일**: `src/ffc_ddw_sum_et/parameters/ffc_ddw_params.py`
- **테스트 파일**: `tests/parameters/test_ffc_ddw_params.py` (기존 파일에 추가)
- **의존**: 없음.
- **먼저 읽기**: `ffc_ddw_params.py`의 기존 factory classmethod
  (`with_stage_processing_time_increment`, `create_instance_of_job_subset`) —
  컨벤션(classmethod, `Self` 반환, `df.copy()`, machine map 깊은 복사)을 그대로 따른다.
- **구현**: `§Design 1`의 `coarsen_time_resolution(cls, instance, factor: int) -> Self`
  classmethod 추가.
  - `factor > 0` 검증(아니면 `ValueError`).
  - `not isinstance(instance, FFcDDWParameters)`면 `TypeError`(기존 factory와 동일 패턴).
  - `p_manager.df`를 copy해 모든 값에 `ceil(value / factor)` 적용
    (예: `np.ceil(df / factor).astype(int)`; df는 RangeIndex 위치형 레이아웃).
    `JobStageProcessingTimeManager(name, new_df)`로 재생성.
  - due window map의 `(lower, upper)` 각각 `ceil(value / factor)`.
  - weights, layout, generation_params 보존. 새 name `f"{instance.name}_coarsen{factor}"`.
- **Acceptance**:
  - processing time / due window가 `ceil(value / factor)`인지.
  - `96..749`, factor=50 → `2..15`인지.
  - 모든 coarsened `p >= 1`(원래 `p > 0`), `lower <= upper` 유지.
  - `factor <= 0` → `ValueError`.
  - `uv run ruff check` 통과, `uv run pytest tests/parameters/test_ffc_ddw_params.py` green.

---

### WP-B — CSR adapter + option + reconstruct

- **대상 파일**: `src/ffc_ddw_sum_et/algorithm/coarsen_solve_reconstruct.py` (신규)
- **테스트 파일**: `tests/algorithm/test_coarsen_solve_reconstruct.py` (신규)
- **의존**: WP-A의 `FFcDDWParameters.coarsen_time_resolution`.
- **먼저 읽기**: `algorithm/cpsat_adapter.py`(Algorithm Protocol 형태, AlgSpec/AlgRecord/
  AlgResult/WorkStatus/TerminationReason 사용법, postprocess 순서), `algorithm/cumulative.py`
  의 `BaseModelBuilder.build/make_params`, `solution/schedule_build.py`,
  `solution/objectives.py`, `algorithm/cpsat_solver_options.py`.
- **구현**: `§Design 2`, `§Design 3`을 그대로.
  - `CoarsenSolveReconstructOption(AlgOption)`: `factor:int=50`,
    `timelimit_sec:float|None=None`, `solver_thread_cnt:int=1`,
    `log_search_progress:bool=False`, `error_if_infeasible:bool=False`.
    (`reconstructed_solution_count`/`horizon_makespan_multiplier` 두지 않음 — v1 범위.)
  - `CoarsenSolveReconstructAdapter`, `algorithm_id = "coarsen_solve_reconstruct"`,
    `run(spec) -> AlgRecord`.
  - **DRY**: `CpsatAdapter`를 통째 복붙하지 말 것. coarsened instance를 build/solve해
    raw `op_start`/`op_end` dict를 돌려주는 작은 내부 헬퍼를 두고, reconstruct만 고유 로직.
    `CpsatAdapter`가 돌려주는 schedule은 coarsened-scale 후처리 결과(raw start 아님)라
    그대로 wrap하지 않는다.
  - reconstruct: raw coarse start를 `*factor`로 inflate해 시작 고정, duration은 원래
    `p_ij`. `build_schedule_from_op_starts(instance, reconstructed_start,
    reconstructed_end)` 재사용(별도 builder 없음). 이후 `make_semi_active` →
    `insert_idle_time`(원척도 due window/weights) → `compute_weighted_earliness_tardiness`.
  - infeasible/no-solution이면 schedule 없는 AlgRecord(상태+metrics)로 반환.
  - metrics: `factor`, `coarsened_instance_name`, coarsened CP status/surrogate
    obj_value/obj_bound/elapsed, reconstructed 원척도 obj_value/makespan.
  - coarsened CP에 원척도 `obj_lb` 주지 않음. ref_solution warm-start 미지원.
- **Acceptance**:
  - reconstructed start가 coarse start의 `factor`배인지.
  - 시작 시각 유지 + duration이 원래 `p_ij`로 되돌려지는지.
  - reconstruct 후 schedule이 feasible(`build_schedule_from_op_starts`가 예외 없이
    coloring)하고 원척도 objective로 평가되는지.
  - 작은 합성 인스턴스에서 `run`이 OPTIMAL/FEASIBLE schedule과 metrics를 반환하는지.
  - `uv run ruff check` 통과, `uv run pytest tests/algorithm/test_coarsen_solve_reconstruct.py` green.

---

### WP-C — controller step

- **대상 파일**: `src/ffc_ddw_sum_et/orchestration/controller.py`
- **테스트 파일**: `tests/orchestration/test_coarsen_solve_reconstruct_step.py` (신규)
- **의존**: WP-B의 `CoarsenSolveReconstructAdapter` / `CoarsenSolveReconstructOption`.
- **먼저 읽기**: `controller.py`의 `solve_base_model_cpsat`(step 계약의 레퍼런스 구현),
  `CLAUDE.md`의 "Subroutine step contract", `controller_core.py`의 `_make_stop_report`/
  `_register`/`get_remaining_sec`/`resolve_value_expr` 사용 예.
- **구현**: `§Design 4`의 `coarsen_solve_reconstruct` step method.
  - import 추가(WP-B 타입). 시그니처는 `§Design 4` 코드블록 그대로.
  - step entry `start_elapsed = time.monotonic()`; stopping이면 `_make_stop_report`.
  - `timelimit`을 `resolve_value_expr` 후 remaining global time과 strict-min.
  - adapter 호출 → `elapsed = monotonic - start_elapsed` 직후 `SubroutineReport` 생성 →
    `_register`를 **정확히 한 번**. gantt/artifact 같은 post-work는 `_register` 이후.
- **Acceptance**:
  - step이 `_register`를 정확히 한 번 호출하는지(no-solution 경로 포함).
  - stopping condition 시 register 없이 stop-report 반환하는지.
  - `elapsed_time`이 entry~register monotonic 측정과 일치(중간 작업 없음)하는지.
  - `uv run ruff check` 통과, `uv run pytest tests/orchestration/test_coarsen_solve_reconstruct_step.py` green.

---

### WP-D — reporting symbol 등록 (optional, 독립)

- **대상 파일**: `src/ffc_ddw_sum_et/report/_chart_constants.py`
- **테스트 파일**: 없음(상수 1줄). 변경 후 `uv run ruff check`만.
- **의존**: 없음(method 이름 문자열만 필요).
- **구현**: `SUBROUTINE_SYMBOL_MAP`에 `"coarsen_solve_reconstruct": "<미사용 symbol>"`
  추가(기존 값들과 겹치지 않는 plotly symbol). Gantt/obj_log 차트에서 step 마커가
  필요할 때만. 없어도 코어 기능은 동작하므로 우선순위 낮음.
- **Acceptance**: `uv run ruff check` 통과, 기존 report 테스트 회귀 없음.

---

### WP-E — pilot experiment config

- **대상 파일**: `metadata/20260623/coarsen_solve_reconstruct_config.yaml` (신규)
- **테스트 파일**: 없음. 스키마 검증은 로더로.
- **의존**: WP-C(scenario `method:`가 controller step 이름으로 dispatch되므로
  step이 존재해야 함).
- **먼저 읽기**: `metadata/20260623/increased_pr_last_stage_config.yaml`(필드 레이아웃:
  `run_mode`/`benchmark_dir`/`output_dir`/`scenarios[].subroutine_flow[].method` + kwargs).
- **구현**: `§Design 5`의 시나리오를 config로.
  - `csr50_only`: `method: coarsen_solve_reconstruct`, `factor: 50`, `solver_thread_cnt: 24`.
  - `neh_cp_baseline`, `lb_init_baseline`(`calc_mcf_lb_and_derive_full_sch`).
  - pool 시나리오(`csr50_pool`)는 제외(v1).
  - `output_dir: output/20260623`, instance source는 레퍼런스 config와 동일 패턴.
- **Acceptance**:
  - config 로더가 파싱/검증 성공(예: 기존 benchmark/scenario 로더로 dry-load).
  - `method` 문자열이 실제 controller step 이름과 일치.

## Risks / Decisions

- `ceil(d/50)`은 earliness/tardiness zero zone도 coarsen하므로 surrogate objective와
  원척도 objective가 단조적으로 대응하지 않을 수 있다. 따라서 coarsened bound는
  원척도 bound로 해석하지 않는다.
- factor=50은 매우 거친 설정이다. RQ1에는 좋지만 RQ2 품질은 나빠질 수 있으므로
  결과 CSV에 factor를 반드시 남긴다.
- `insert_idle_time`은 원척도 due window를 사용해야 한다. coarsened due window를
  후처리에 섞으면 RQ2 평가가 오염된다.
- CP-SAT solution pool은 v1 범위에서 제외한다(§2 "v1 범위와 DRY"). 도입 시
  `solution_pool_size` + `fill_additional_solutions_in_response`를 함께 켜고,
  additional solution 값은 `response_proto.additional_solutions[k].values[var.index]`로
  인덱스 접근하며, pool이 비면 primary만 복원하는 fallback이 필요하다.
- `draw_gantt`는 선택 기능으로 두고, step 계약상 `_register` 이후에만 수행한다.
- 처음에는 hybridflowshop의 surrogate dispatch/NEH/PW-CP/polish 옵션을 이식하지
  않는다. 이번 질문은 “극단적 coarsen + base CP + reconstruct”의 효과 검증이므로
  실험 변수를 최소화한다.
