# v3 dispatch-initialization subroutine: paired P\* best-of-6, single point

> 작성일: 2026-06-25
> 근거: `analysis/20260625_dispatch_init_justification_3.md`
>       (P\* = {edd, wspt_twt, wxd2}, direction-symmetric paired oracle, m=3)
> 선행 step: `initialize_by_simple_dispatch`(sd) / `_dispatch_by_reversed_sequence_with_iit`(rd)

## Context

justification v3 의 채택안은 priority 집합 **P\* = {edd, wspt_twt, wxd2}** 를 각각
**두 방향**(simple `sd`, reverse `rd`)으로 디코드해 **6개 스케줄**을 만든 뒤
per-instance best 를 취하는 direction-symmetric 정책이다. 지금까지 이 6개는 sweep
에서 **6개 별도 scenario**(각자 register)로 흩뿌려져 있었다.

후속 알고리즘(local search / metaheuristic)의 **base incumbent** 로 쓰려면, 6개를
한 step 안에서 만들고 **하나만 register** 해서 `solution_manager.history` 에 **점
하나**로 찍혀야 한다. 이 plan 은 그 단일 step `initialize_by_dispatch_v3` 를
추가한다.

### 설계 핵심 (step contract 준수)

`docs/architecture/algorithm-principles.md` + CLAUDE.md "Subroutine step contract":
- **step 당 register 정확히 1회.** 6개 후보를 step 본문에서 만들고, best 하나만
  `self._register`. → 절대 후보별로 register 하지 않는다(그러면 6 register 위반).
- **elapsed = step 진입~register 직전 monotonic.** 6개 디코드 전부 그 사이에 둔다.
- 따라서 기존 `initialize_by_simple_dispatch` / `initialize_by_*`(각자 register)
  를 **그대로 호출하면 안 된다**(각 호출이 register 1회씩). 대신 decode 코어를
  register 없는 helper 로 추출해 재사용한다.

## Critical files to modify / create

- **modify** `src/ffc_ddw_sum_et/orchestration/controller.py`
  - (refactor) simple-decode 코어를 register 없는 helper 로 추출.
  - (new) `initialize_by_dispatch_v3` step method.
  - (new) 모듈 상수 `V3_PRIORITY_SET = ("edd", "wspt_twt", "wxd2")`.
- **modify** `tests/orchestration/test_controller.py`
  - v3 step 단일 register / best-of-6 / 재현성 테스트 추가.
- **create** `metadata/20260625_dispatch_v3_config.yaml`
  - 단일 scenario `dispatch_v3`, `subroutine_flow: [{method: initialize_by_dispatch_v3}]`.
- (run 시점) `main.py:CONFIG_PATH` 를 위 config 로 지정 — **plan 승인 후 실행 단계에서만**.

## Existing functions to reuse (재구현 금지)

- `dispatch_seq_job_sequence(instance, key)` — `parameters/sorter.py:73`.
  registry 에 `edd`(:78), `wxd2`(:84), `wspt_twt`(:93) 모두 존재 → 그대로 forward
  우선순위 sequence 획득.
- **sd 디코드**: `MixedDispatcher.get_job_centric_schedule_by_sequence` +
  `FFcSchedule.make_semi_active` + `FFcSchedule.insert_idle_time` —
  현재 `initialize_by_simple_dispatch`(controller.py:~1340) 본문에 인라인.
- **rd 디코드**: `_dispatch_by_reversed_sequence_with_iit(job_sequence)` —
  controller.py:1448. forward `job_sequence` 를 받아 내부에서 reverse, `(schedule,
  obj)` 반환. **재사용**(이미 helper 형태).
- 목적함수: `compute_weighted_earliness_tardiness` — `solution/objectives.py`
  (sd helper 안에서만 사용; rd helper 는 자체 계산).
- register: `self._register(report, FFcDDWSolution(...))` — 기존 dispatch init 과
  동일 시그니처(`obj_bound=None`).
- diagnostics(선택): `self._log_dispatch_seed_diagnostics(label, schedule)` —
  register **이후** 호출(타이밍 미오염).

## Implementation outline

1. **(refactor) simple-decode helper 추출.** `initialize_by_simple_dispatch` 의
   decode+IIT 본문을 register 없는 helper 로 분리, 기존 step 은 helper 를 호출하도록
   변경(동작 불변, DRY):
   ```python
   def _dispatch_by_simple_sequence_with_iit(
       self, job_sequence: Sequence[str]
   ) -> tuple[FFcSchedule, float]:
       """Forward job-centric decode + semi-active + IIT. (sd 파이프라인 코어;
       register 하지 않음 — caller 가 책임.)"""
       dispatcher = MixedDispatcher(self.instance, logger=self.logger)
       schedule = dispatcher.get_job_centric_schedule_by_sequence(job_sequence)
       schedule.make_semi_active(self.instance.stage_2_job_2_p_map)
       schedule.insert_idle_time(
           self.instance.job_2_due_window_map,
           self.instance.job_2_ewt_map,
           self.instance.job_2_twt_map,
       )
       sum_e, sum_t = compute_weighted_earliness_tardiness(schedule, self.instance)
       return schedule, float(sum_e + sum_t)
   ```
   `initialize_by_simple_dispatch` 본문 = `seq = dispatch_seq_job_sequence(...)` →
   `schedule, obj = self._dispatch_by_simple_sequence_with_iit(seq)` → 기존 report/
   register/diag 유지. (timing·register·diag 동작 동일.)

2. **(new) 상수.** 모듈 최상단(다른 상수 옆):
   `V3_PRIORITY_SET: tuple[DispatchSeqKey, ...] = ("edd", "wspt_twt", "wxd2")`.

3. **(new) step method** — single register, best-of-6:
   ```python
   def initialize_by_dispatch_v3(
       self, priorities: Sequence[DispatchSeqKey] = V3_PRIORITY_SET
   ) -> SubroutineReport:
       """Step: justification-v3 paired dispatch pool. 각 priority 를 sd/rd 두
       방향으로 디코드(2·len(priorities) 스케줄)한 뒤 weighted-ET 최소 incumbent
       하나만 register — history 에 점 하나. 기본 P* = {edd, wspt_twt, wxd2}."""
       start_elapsed = time.monotonic()
       candidates: list[tuple[float, str, FFcSchedule]] = []
       for p in priorities:
           seq = dispatch_seq_job_sequence(self.instance, p)
           sd_sch, sd_obj = self._dispatch_by_simple_sequence_with_iit(seq)
           candidates.append((sd_obj, f"sd:{p}", sd_sch))
           rd_sch, rd_obj = self._dispatch_by_reversed_sequence_with_iit(seq)
           candidates.append((rd_obj, f"rd:{p}", rd_sch))
       best_obj, best_label, best_sch = min(candidates, key=lambda c: c[0])
       elapsed = time.monotonic() - start_elapsed
       report = SubroutineReport(
           elapsed_time=elapsed, obj_value=best_obj, obj_bound=None
       )
       self._register(
           report,
           FFcDDWSolution(schedule=best_sch, obj_value=best_obj, obj_bound=None),
       )
       self.logger.info(
           "dispatch_v3: best=%s obj=%s of %d candidates [%s]",
           best_label, best_obj, len(candidates),
           ", ".join(f"{lab}={obj:.0f}" for obj, lab, _ in candidates),
       )
       self._log_dispatch_seed_diagnostics(f"v3:{best_label}", best_sch)
       return report
   ```
   - **min tie-break**: `min` 은 첫 최소를 유지 → 순회 순서(priorities × sd-먼저)가
     결정적이라 재현성 보장. obj 동률이면 먼저 나온 후보(=리스트 순서) 채택.
   - obj_bound=None (dispatch init 은 LB 없음; 기존 init 과 동일).

4. **import 확인.** `DispatchSeqKey`, `dispatch_seq_job_sequence`, `MixedDispatcher`,
   `compute_weighted_earliness_tardiness`, `FFcSchedule`, `FFcDDWSolution`,
   `SubroutineReport` — 모두 controller.py 에 이미 import 됨(기존 step 사용 중).
   추가 import 불필요.

## 비-목표 (YAGNI)

- 6개 후보별 schedule YAML 방출 안 함 — best incumbent 의 YAML 은 runner 가 평소처럼
  dump. (시각화가 필요해지면 그때 §5 two-phase 로 추가.)
- P\* 를 config 에서 바꾸는 기능은 method `priorities` 인자로 충분 — 별도 CLI/registry
  확장 안 함.
- `algorithm/` 신규 모듈 없음 — 기존 dispatcher/IIT 재사용만. matplotlib 미접근.

## Verification

`tests/orchestration/test_controller.py` (기존 `test_run_mcf_lb_*` 스타일, toy
instance fixture 재사용):

1. `test_initialize_by_dispatch_v3_registers_single_incumbent`:
   step 호출 후 `solution_manager.history` 길이 +1(정확히 하나), registered
   `obj_value == report.obj_value`, schedule feasible(`make_semi_active` 후
   end-time ≥ 0 등 기존 헬퍼로 검증).
2. `test_initialize_by_dispatch_v3_picks_min_of_six`:
   동일 toy 에서 3 priority 각각 sd/rd helper 를 직접 호출해 6 obj 를 모아
   `min` 과 step 의 `report.obj_value` 일치. (best-of-6 보장.)
3. `test_initialize_by_dispatch_v3_is_deterministic`:
   같은 instance 두 번 호출 → 동일 obj_value(재현성 / tie-break 결정성).
4. (regression) `initialize_by_simple_dispatch` 기존 테스트 green — refactor 가
   동작 보존했는지(없으면 toy 에서 refactor 전후 obj 동일 1건 추가).

실행 순서(CLAUDE.md 규약):
1. `uv run ruff check` clean.
2. `uv run ruff format`.
3. `uv run pytest tests/orchestration/test_controller.py -q` → 신규 + 기존 green.
4. `uv run pytest tests/ -q` 전체 green.
5. (run 검증, 선택) `metadata/20260625_dispatch_v3_config.yaml` 로 작은 `ins_index`
   슬라이스 `uv run python main.py` → 각 instance subdir 에
   `<ins>_solution.json`/`_schedule.yaml`/`_gantt.png`/`_statistics.*`/`_obj_log.yaml`
   생성, top-level `<ts>_summary.csv` 헤더가 hfs_summary 형태인지, `_obj_log` 에
   **점 하나**(single register)만 있는지 확인.

## 산출물 / 경로

- step: `FFcDDWSubroutineController.initialize_by_dispatch_v3` (+ helper
  `_dispatch_by_simple_sequence_with_iit`, 상수 `V3_PRIORITY_SET`).
- config: `metadata/20260625_dispatch_v3_config.yaml`.
- tests: `tests/orchestration/test_controller.py` (+3~4 케이스).
