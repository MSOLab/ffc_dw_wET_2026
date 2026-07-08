# Plan: `coarsen_solve_reconstruct` 시각화 추가

## Context

`coarsen_solve_reconstruct` 플로우(plan: `coarsen_solve_reconstruct.md`)에 대해
사용자가 두 종류의 그림을 추가 요청했다.

- **그림 A — coarsened CP-SAT UB/LB line graph**: coarsened instance solving 동안
  CP-SAT가 제공한 upper bound(`obj_value`)와 lower bound(`obj_bound`)의 시간 추이.
  x축은 **solve elapsed seconds**.
- **그림 B — reconstruct 변환 3-Gantt**: 한 solve의 schedule 변환 과정을 3개의
  Gantt로 보여준다.
  1. `coarse_solver_result` — solver가 낸 coarsened schedule (coarsened 시간축, 자기 스케일).
  2. `reconstructed_raw` — inflated start 고정 + duration을 원래 `p_ij`로 되돌린,
     `make_semi_active`/`insert_idle_time` **이전** schedule (원척도 시간축).
  3. `final` — `make_semi_active` → `insert_idle_time` 적용 후 최종 schedule (원척도 시간축).

### 확정된 디자인 결정

1. **전용 artifact로 분리.** coarsened CP 추이는 *coarsened-scale* objective 단위라,
   공유 `_obj_log.json`(원척도, cross-step RPD/optimality-gap에 사용)에 섞으면 안 된다.
   그림 A는 별도 trajectory JSON + 별도 line-graph renderer로 emit한다.
2. **algorithm base 계약(AlgRecord/AlgResult/AlgSpec)은 건드리지 않는다.** CLAUDE.md의
   composite-step 계약을 따른다: reconstruct를 **pure pipeline 함수**로 빼서 final +
   3 schedule snapshot + CP trajectory를 담은 trace를 반환하고, controller step이 그 trace를
   받아 snapshot을 controller attribute(`mcf_lb_phase_schedules` 선례와 동일)에 capture한 뒤
   `_register`를 정확히 한 번 호출한다. adapter(`CoarsenSolveReconstructAdapter.run`)는 Protocol
   사용자를 위해 pipeline을 wrap한 채 유지한다.
3. **두 그림을 독립 flag로 gating**한다 (MCF-LB 컨벤션과 동일). 그림 B(3-Gantt)는
   `emit_phase_schedules: bool`, 그림 A(CP trajectory line graph)는
   `draw_cp_trajectory: bool`로 따로 켠다. 둘은 서로 독립이며 기본값은 모두 `False`.
   (`draw_gantt` step 파라미터도 유지하되, CSR 그림 gating은 위 두 flag가 담당한다.)
4. **Gantt x축**: `coarse_solver_result`는 coarsened 시간축(자기 스케일, force 없음).
   `reconstructed_raw`와 `final`은 공유 원척도 축 `force_start=0`,
   `force_end=max(두 schedule의 makespan)` — MCF-LB phase Gantt의 shared-axis 방식
   (`reporting.py`의 force_start/force_end)과 동일.
5. **Line graph x축**: solve elapsed seconds (RQ1의 "얼마나 빨리 optimal 증명"을 직접 표시).

### 읽은 코드 (사실 확인 완료)

- `src/ffc_ddw_sum_et/algorithm/coarsen_solve_reconstruct.py`: 현재 `_solve_coarsened_model`은
  `solver.solve(mdl)`만 호출 — **callback 없음**이라 CP 추이를 버린다. 중간 schedule 2개도
  버리고 final만 반환.
- `src/ffc_ddw_sum_et/algorithm/cpsat_adapter.py`: `ObjectiveValueRecorder`(solution callback) +
  `ObjectiveBoundRecorder`(`solver.best_bound_callback`)로 progress_log를 만드는 선례
  (`_build_progress_log` static). 그림 A의 trajectory capture는 이 패턴을 그대로 쓴다.
- `src/ffc_ddw_sum_et/algorithm/cpsat_callbacks/obj_value_recorder.py`,
  `cpsat_callbacks/obj_bound_recorder.py`: recorder 구현.
- `src/ffc_ddw_sum_et/solution/schedule_build.py`: `build_schedule_from_op_starts`로 coarse/
  reconstructed schedule을 build.
- `src/ffc_ddw_sum_et/orchestration/controller_core.py`: `mcf_lb_phase_schedules` 상태 +
  `_record_mcf_lb_phase`/`_record_mcf_lb_phases`(call_context prefix) 선례.
- `src/ffc_ddw_sum_et/orchestration/ffcddw_single_instance_runner.py`:
  `phase_schedules` 루프(`dump_solution_json` → `mcf_lb_phase_schedule` artifact),
  `_save_obj_log`.
- `src/ffc_ddw_sum_et/orchestration/reporting.py`: `_render_phase_gantt_from_json`
  (operations[] JSON → PNG), `_phase_makespan_from_json`(shared horizon), MCF-LB phase Gantt가
  `0..max_makespan` 공유 축으로 렌더되는 부분(`reporting.py:1607` 부근).
- `metadata/artifact_layout/ffc_ddw_sum_et_v1.yaml`: `mcf_lb_phase_schedule` 등 artifact kind
  등록부. 새 kind는 여기에 추가.
- `src/ffc_ddw_sum_et/io/schedule_json.py::dump_solution_json`,
  `src/ffc_ddw_sum_et/solution/objectives.py::compute_phase_obj_value`.
- `src/ffc_ddw_sum_et/io/gantt.py`: `GanttPlotter.export(...)` (force_start/force_end 지원).
- 기존에는 단일 solve의 **UB/LB-vs-time line graph renderer가 없다**(obj_log 계열은 cross-instance
  RPD scatter용). 그림 A는 작은 신규 matplotlib renderer가 필요하다.

---

## Work Packages (per-file, sonnet subagent 단위)

각 WP는 가능한 한 **하나의 production 파일 + 전용 test**를 단위로 한다. 모든 subagent는
시작 시 이 plan 전체 + `CLAUDE.md`(특히 "Subroutine step contract") +
`coarsen_solve_reconstruct.md`를 읽는다. 실행은 `uv run python`, 변경 후
`uv run ruff check`(benchmarks/scripts의 기존 E402는 무시), 필요 시 `uv run ruff format`.
TDD로 진행.

### 의존성 / 실행 순서

```
WP-1 (algorithm: trace+trajectory)
   └─> WP-2 (controller: capture)
WP-3 (artifact_layout kinds, 독립)
WP-1, WP-2, WP-3 ──> WP-4 (runner emit) ──> WP-5 (reporting render) ──> WP-6 (config flags)
```

- WP-3은 독립(yaml만)이라 언제든 실행 가능.
- WP-4는 WP-2(controller 속성)와 WP-3(artifact kind) 둘 다 필요.
- WP-5는 WP-4(emit된 JSON)와 WP-3(png kind) 필요.

---

### WP-1 — algorithm: CP trajectory + 3 schedule snapshot을 내는 pure pipeline

- **대상 파일**: `src/ffc_ddw_sum_et/algorithm/coarsen_solve_reconstruct.py`
- **테스트 파일**: `tests/algorithm/test_coarsen_solve_reconstruct.py` (기존에 추가)
- **의존**: 없음.
- **먼저 읽기**: `cpsat_adapter.py`의 callback 사용 + `_build_progress_log`,
  `cpsat_callbacks/obj_value_recorder.py`, `cpsat_callbacks/obj_bound_recorder.py`,
  `schedule_build.py`, `base/alg_record.py`(`ProgressLogEntry`).
- **구현**:
  - `_solve_coarsened_model`에 `ObjectiveValueRecorder`(solution callback)와
    `ObjectiveBoundRecorder`(`solver.best_bound_callback`)를 붙여 coarsened-scale 추이를
    capture하고, `cpsat_adapter._build_progress_log`와 동일한 merge 규칙으로
    `tuple[ProgressLogEntry, ...]`(coarsened obj_value/obj_bound, elapsed_sec)을 만든다.
    merge 로직은 `CpsatAdapter._build_progress_log`를 작은 module-level 헬퍼로 추출해
    양쪽에서 재사용(DRY)하거나, 중복이 작으면 동일 규칙으로 inline. 또한 raw
    `coarse_start`/`coarse_end`를 그대로 반환(이미 함).
  - reconstruct를 pure pipeline 함수로 정리: 예) `run_coarsen_solve_reconstruct(instance,
    option, logger) -> CoarsenSolveReconstructTrace`. `CoarsenSolveReconstructTrace`
    (frozen dataclass) 필드:
    - `work_status: WorkStatus`, `termination_reason: TerminationReason`
    - `final_schedule: FFcSchedule | None`
    - `coarse_schedule: FFcSchedule | None` —
      `build_schedule_from_op_starts(coarsened, coarse_start, coarse_end)` (coarsened 인스턴스로
      build, coarsened 시간 스케일).
    - `reconstructed_raw_schedule: FFcSchedule | None` —
      `build_schedule_from_op_starts(instance, reconstructed_start, reconstructed_end)`를
      **make_semi_active/insert_idle_time 적용 전** 그대로 보존(별도 객체).
    - `cp_progress_log: tuple[ProgressLogEntry, ...]` (coarsened scale)
    - `obj_value: float | None` (원척도 final)
    - `metrics: dict` (기존 metrics와 동일 키)
  - `final_schedule`은 `reconstructed_raw_schedule`를 복제한 뒤 postprocess하거나, 별도로
    build한 뒤 postprocess해서 raw snapshot이 mutate되지 않게 한다(둘이 서로 다른 객체여야 함).
  - `CoarsenSolveReconstructAdapter.run(spec)`는 이 pipeline을 호출해 `AlgRecord`로 pack:
    `result.schedule = trace.final_schedule`, `AlgRecord.progress_log = trace.cp_progress_log`,
    metrics 동일. (no-solution 경로도 trace로 표현.)
- **Acceptance**:
  - trace의 `coarse_schedule`/`reconstructed_raw_schedule`/`final_schedule`이 서로 다른 객체이고,
    `final`은 postprocess 반영, `reconstructed_raw`는 미반영(서로 makespan/시작시각이 다를 수 있음).
  - solver가 진행을 로깅하는 작은 인스턴스에서 `cp_progress_log`가 비어있지 않고 마지막 entry의
    obj_value/obj_bound가 solver의 coarsened objective/bound와 일치.
  - `reconstructed_raw`의 op duration이 원래 `p_ij`, 시작이 coarse_start*factor.
  - 기존 WP-B 테스트 전부 green(`adapter.run` 여전히 동일 schedule/metrics 반환,
    이제 `AlgRecord.progress_log` 채워짐).
  - `uv run ruff check <두 파일>` + `uv run pytest tests/algorithm/test_coarsen_solve_reconstruct.py`.

---

### WP-2 — controller: snapshot + trajectory capture

- **대상 파일**: `src/ffc_ddw_sum_et/orchestration/controller_core.py`(상태/헬퍼),
  `src/ffc_ddw_sum_et/orchestration/controller.py`(step 수정)
- **테스트 파일**: `tests/orchestration/test_coarsen_solve_reconstruct_step.py`(기존에 추가)
- **의존**: WP-1의 pure pipeline + `CoarsenSolveReconstructTrace`.
- **먼저 읽기**: `controller_core.py`의 `_define_states`,
  `mcf_lb_phase_schedules`/`_record_mcf_lb_phase`/`_mcf_lb_phase_name`,
  `controller.py`의 `coarsen_solve_reconstruct` step과 `solve_base_model_cpsat`,
  `CLAUDE.md` "Subroutine step contract".
- **구현**:
  - `_define_states`에 추가: `self.csr_phase_schedules: list[tuple[str, FFcSchedule]] = []`,
    `self.csr_cp_trajectory: tuple[ProgressLogEntry, ...] | None = None`.
  - `_record_mcf_lb_phase` 선례를 따른 `_record_csr_phase(name, sched)` 헬퍼(call_context prefix).
  - `coarsen_solve_reconstruct` step:
    - step 시그니처에 **독립 flag 2개** 추가(MCF-LB 컨벤션, 기본 `False`):
      `emit_phase_schedules: bool = False`(3-Gantt), `draw_cp_trajectory: bool = False`
      (CP UB/LB line graph). 기존 `draw_gantt`도 유지.
    - adapter 대신 WP-1 pure pipeline을 호출(composite-step 패턴). trace로부터 원척도
      obj_value/obj_bound, schedule을 꺼냄.
    - `elapsed` 측정 직후 `SubroutineReport` 생성 → `_register`를 **정확히 한 번**
      (no-solution 포함). report의 `progress_log`에는 coarsened 추이를 **넣지 않는다**
      (공유 obj_log 오염 방지; 전용 artifact로만 emit).
    - `_register` **이후** post-work로, solution이 있을 때:
      - `emit_phase_schedules`가 True면
        `_record_csr_phase("1_coarse_solver_result", trace.coarse_schedule)`,
        `"2_reconstructed_raw"`, `"3_final"` 3개 append.
      - `draw_cp_trajectory`가 True면 `self.csr_cp_trajectory = trace.cp_progress_log` 설정.
      - 두 flag는 독립적으로 평가한다(하나만 켤 수 있어야 함).
  - 필요한 import 추가.
- **Acceptance**:
  - 성공/무해(no-solution) 경로 모두 `_register` 정확히 1회.
  - stopping condition 시 register 없이 stop-report.
  - `emit_phase_schedules=True` + solution 존재 → `csr_phase_schedules` 길이 3,
    이름 순서 `1_/2_/3_`.
  - `draw_cp_trajectory=True` + solution 존재 → `csr_cp_trajectory` 설정됨.
  - 두 flag 독립 검증: `emit_phase_schedules=True, draw_cp_trajectory=False` →
    snapshot 3개 + trajectory None; 그 반대 → snapshot 0개 + trajectory 설정.
  - 둘 다 `False` → snapshot 0개, trajectory None.
  - `_register` 시점 report와 반환 report 동일(중간 작업으로 elapsed 왜곡 없음).
  - `uv run ruff check <파일들>` + `uv run pytest tests/orchestration/test_coarsen_solve_reconstruct_step.py`.

---

### WP-3 — artifact layout kinds 등록

- **대상 파일**: `metadata/artifact_layout/ffc_ddw_sum_et_v1.yaml`
- **테스트 파일**: 없음. `tests/orchestration/test_artifact_layout_overlay.py`가 있으면 회귀 확인.
- **의존**: 없음.
- **먼저 읽기**: 이 yaml 전체(특히 `mcf_lb_phase_schedule`와 phase Gantt **PNG** kind가
  어떻게 등록/명명됐는지 — 기존 png kind 이름을 그대로 재사용할지 결정).
- **구현**: instance/progress zone에 추가:
  - `csr_phase_schedule`: `file_template: "{phase_name}.json"` (mcf_lb_phase_schedule 미러).
  - `csr_cp_trajectory_json`: `file_template: "{instance_name}_csr_cp_trajectory.json"`.
  - `csr_cp_trajectory_png`: `file_template: "{instance_name}_csr_cp_trajectory.png"`.
  - Gantt PNG: 기존 phase-gantt png kind를 재사용할 수 있으면 재사용하고, `{phase_name}`로
    구분되지 않으면 `csr_phase_gantt_png`(`file_template: "{phase_name}.png"`) 추가.
    어느 쪽인지 yaml/`reporting.py`를 보고 결정해 한 줄 코멘트로 명시.
- **Acceptance**: 레이아웃 로더가 새 kind를 등록(로드 에러 없음). 기존 overlay 테스트 green.

---

### WP-4 — runner: CSR artifact emit

- **대상 파일**: `src/ffc_ddw_sum_et/orchestration/ffcddw_single_instance_runner.py`
- **테스트 파일**: 기존 runner/orchestration 테스트 회귀 + 가능하면 작은 단위 테스트.
- **의존**: WP-2(controller 속성), WP-3(artifact kind).
- **먼저 읽기**: 같은 파일의 `mcf_lb` `phase_schedules` 루프(`dump_solution_json` →
  `mcf_lb_phase_schedule`), `_save_obj_log`, `compute_phase_obj_value`,
  `io/schedule_json.py::dump_solution_json`.
- **구현**:
  - mcf_lb phase 루프 다음에 `csr_phase_schedules` 루프 추가: 각 `(name, sched)`를
    `dump_solution_json(sched, layout.artifact_path("csr_phase_schedule", phase_name=name,
    **scope), instance_name=..., obj_value=..., compact=True)`로 dump.
    - obj_value: `final`/`reconstructed_raw`는 `compute_phase_obj_value(sched, instance)`(원척도).
      `coarse_solver_result`는 coarsened 스케일이라 원척도 obj가 무의미 → `obj_value=None`으로
      두고 title에 makespan만 나오게 한다.
  - `controller.csr_cp_trajectory`가 있으면 `csr_cp_trajectory_json`으로 dump:
    예) `{"elapsed_sec": [...], "obj_value": [...], "obj_bound": [...]}` 또는 entry 리스트.
    `None` 값은 그대로 두어(UB 없는 시점 등) renderer가 처리.
  - 모든 emit은 `getattr(controller, "csr_phase_schedules", None)`/`csr_cp_trajectory`로 방어,
    비면 skip. 예외는 mcf_lb 루프처럼 `logger.exception`으로 감싸 다른 산출물 막지 않기.
- **Acceptance**:
  - 합성 controller 상태(snapshot 3 + trajectory)로 호출 시 JSON 파일 3 + trajectory JSON 생성,
    내용이 schedule/trajectory와 일치.
  - 상태가 비면 아무 파일도 안 만들고 기존 흐름 회귀 없음.
  - `uv run ruff check <파일>` + `uv run pytest tests/orchestration -q`.

---

### WP-5 — reporting: 3-Gantt(공유축) + UB/LB line graph 렌더

- **대상 파일**: `src/ffc_ddw_sum_et/orchestration/reporting.py`
  (line renderer가 커지면 `src/ffc_ddw_sum_et/io/`에 작은 모듈로 분리 가능)
- **테스트 파일**: 가능한 범위에서 단위 테스트(JSON → PNG 생성 smoke). matplotlib Agg 사용.
- **의존**: WP-4(emit된 JSON), WP-3(png kind).
- **먼저 읽기**: `reporting.py`의 `_render_phase_gantt_from_json`,
  `_phase_makespan_from_json`, MCF-LB phase Gantt 공유축 렌더 부분(`reporting.py:1607` 부근),
  post-run 렌더 루프(어디서 mcf phase json을 png로 렌더하는지), `io/gantt.py::GanttPlotter.export`.
- **구현**:
  - **3-Gantt**: `csr_phase_schedule` JSON들을 `_render_phase_gantt_from_json`으로 렌더.
    - `2_reconstructed_raw`, `3_final`: 공유 원척도 축 — `force_start=0`,
      `force_end=max(_phase_makespan_from_json(2), _phase_makespan_from_json(3))`.
    - `1_coarse_solver_result`: force 없이 자기 coarsened 축.
    - MCF-LB의 phase-family 렌더 로직을 참고하되 CSR 3개에 맞게 그룹핑.
  - **Line graph**: module-level picklable `_render_csr_cp_trajectory_line(json_path, png_path)`
    추가. matplotlib(`Agg`)로 x=elapsed_sec, **두 선** UB(`obj_value`) / LB(`obj_bound`)를
    step 형태로 그림. UB/LB가 None인 구간은 건너뛰고 선을 잇는다. 제목은 instance명 +
    coarsened status. y축은 coarsened-scale objective임을 라벨로 명시.
  - post-run 렌더 루프에 위 두 렌더를 wiring(존재하는 JSON에 대해서만, 예외는 감싸기).
- **Acceptance**:
  - 합성 `operations[]` JSON 3개 → PNG 3개 생성(2개는 동일 x-horizon).
  - 합성 trajectory JSON → line PNG 1개 생성(UB/LB 2선).
  - 입력이 없거나 비정상이면 조용히 skip(기존 phase-gantt 렌더 패턴과 동일).
  - `uv run ruff check <파일>` + 관련 report/orchestration 테스트 green.

---

### WP-6 — config flags

- **대상 파일**: `metadata/20260623/coarsen_solve_reconstruct_config.yaml`
- **의존**: WP-2(step의 `emit_phase_schedules`/`draw_cp_trajectory`), WP-5(렌더).
- **먼저 읽기**: 같은 파일과 `increased_pr_last_stage_config.yaml`의 `draw_gantt`/
  `painter_thread_cnt`/`emit_phase_schedules` 사용례. (현재 이 config에는 `csr50_only`,
  `csr25_only` 등 `coarsen_solve_reconstruct` 시나리오가 이미 있다.)
- **구현**: 모든 `coarsen_solve_reconstruct` step(`csr50_only`, `csr25_only` 등)에 두 flag를
  **독립적으로** 추가: `emit_phase_schedules: true`, `draw_cp_trajectory: true`. 파일 상단
  (또는 run-level)에서 `draw_gantt: true` 보장. 두 flag는 서로 켜고 끌 수 있어야 하므로,
  각 step에 별도 키로 명시한다.
- **Acceptance**: config 로더가 파싱/검증 성공, `method`/필드명이 실제 step 파라미터와 일치.

---

## Risks / Decisions

- coarsened CP 추이는 coarsened-scale objective다. 전용 artifact/renderer로만 emit하고
  공유 `_obj_log.json`이나 controller report의 `progress_log`에는 넣지 않는다(원척도 RPD/
  optimality-gap 오염 방지).
- `coarse_solver_result` Gantt는 coarsened 시간축이므로 다른 두 Gantt와 절대시간이 다르다.
  공유축은 `reconstructed_raw`/`final` 두 개에만 적용한다.
- 두 그림은 독립 flag로 gating해 기본 실행 비용을 늘리지 않는다: 3-Gantt는
  `emit_phase_schedules`, CP UB/LB line graph는 `draw_cp_trajectory`. 하나만 켜는 조합이
  가능해야 한다. WP-4/WP-5의 emit/render는 `csr_phase_schedules`/`csr_cp_trajectory` 상태
  존재 여부로 방어하므로, flag가 꺼지면 해당 상태가 비어 자연히 skip된다.
- algorithm base 계약(AlgRecord/AlgResult/AlgSpec)은 변경하지 않는다. 중간 산출물은
  composite-step 패턴(pure pipeline → controller capture)으로만 노출한다.
- `reconstructed_raw`와 `final`은 반드시 서로 다른 schedule 객체여야 한다(postprocess가
  raw snapshot을 mutate하면 두 Gantt가 동일해진다).
