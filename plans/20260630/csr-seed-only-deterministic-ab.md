# CSR: `solve` knob — seed-only deterministic mode for A/B testing

Status: **PLAN — for review.** No source edits until approved.
Date: 2026-06-30
Motivation: plans/20260630 의 coarse-grid `insert_idle_time` 변경들(F/L)은 전부
**seed 생성 단계 안에서만** 효과를 낸다 (`csr-nbm-lookahead-coarse-shift.md` §6).
그런데 `run_coarsen_solve_reconstruct`는 항상 (1) seed hint → (2) CP-SAT 개선 →
(3) reconstruct 를 거치며, (2)는 재실행마다 결과가 달라 변경 전/후 비교를 가린다.
Target: `algorithm/coarsen_solve_reconstruct.py`, `orchestration/controller.py`.

## Goal

CP-SAT solve `(2)`를 생략하고 `(1) seed → (3) reconstruct`만 수행하는
**결정론적 모드**를 추가한다. 그러면 출력 = reconstruct된 seed 스케줄이 되고,
변경의 diff가 곧 seed 품질 변화이며 재실행 노이즈가 없다. 이 노브는
`subroutine_flow` config 에서 그대로 켜고 끌 수 있다 (컨트롤러 메서드 시그니처가
config 스키마이기 때문 — `seed_dispatch`/`factor`와 동일 메커니즘).

## Decisions (확정)

- 노브 이름/형태: **`solve: bool = True`** (긍정형, 기본 동작 불변).
- `solve=False` + `draw_cp_trajectory: true` 동시 지정 시: **빈 trajectory로 무시.**
  `trace.cp_progress_log = ()` → 컨트롤러가 `self.csr_cp_trajectory = ()` 설정 →
  runner `if traj:` (`ffcddw_single_instance_runner.py:624`) 가 falsy 로 스킵.
  → 추가 분기 불필요, 공짜로 성립.

## Design

`solve=False` 경로는 기존 파이프라인의 (2)만 건너뛴다. seed 스케줄은 이미
coarsened 인스턴스 위의 `FFcSchedule` 이므로, 별도 `build_schedule_from_op_starts`
재구성 없이 **그대로 coarse 해로 사용**한다 (KISS). reconstruct 두 갈래
(`reconstructed_raw_schedule`, `final_schedule`)는 지금처럼 각각 별도 호출로
생성해 postprocess 가 seed/raw 를 mutate 하지 않게 유지한다.

seed-only 모드에서의 trace 값:

| 필드 | 값 |
|---|---|
| `coarse_schedule` | seed 스케줄 (그대로) |
| `coarsened_status` (metrics) | `"SEED_ONLY"` |
| `coarsened_obj_value` (metrics) | `dispatch_seed_obj` (= seed wET) |
| `coarsened_obj_bound` (metrics) | `None` |
| `coarsened_elapsed` (metrics) | seed 빌드 elapsed |
| `cp_progress_log` | `()` (빈 튜플) |
| `work_status` / `termination_reason` | `FEASIBLE` / `COMPLETED` |
| `obj_value`, `reconstructed_*` metrics | 기존 solve 경로와 동일하게 계산 |

`error_if_infeasible` 는 seed-only 경로에선 무의미 (seed 는 항상 feasible) →
관여하지 않는다.

## File-by-file changes

### `algorithm/coarsen_solve_reconstruct.py`

1. **`CoarsenSolveReconstructOption`** (L82~): `solve: bool = True` 필드 추가.
   docstring 에 "seed-only 결정론 모드" 한 줄.
2. **seed 빌드를 solve 에서 분리**. 현재 `_build_dispatch_seed_schedule` +
   seed wET 평가가 `_solve_coarsened_model` 내부(L233~245)에 묶여 있다.
   `run_coarsen_solve_reconstruct` 본문에서 **seed 와 seed_obj 를 먼저 계산**한 뒤
   `solve` 분기를 태우는 구조로 소폭 리팩터. (옵션 A) 가장 단순한 형태는
   `run_coarsen_solve_reconstruct` 안에서:
   - `coarsened` 생성 후
   - `if option.solve:` → 기존 `_solve_coarsened_model` 경로 (변경 없음)
   - `else:` → `_build_dispatch_seed_schedule` 로 seed 생성, seed wET 평가,
     `coarse_schedule = seed`, status=`"SEED_ONLY"`, log=`()` 구성
   - 이후 reconstruct + metrics 조립은 **두 경로 공유** (has_solution 분기 아래
     코드를 그대로 사용).

   중복을 피하려면 seed 빌드/평가를 작은 헬퍼 `_seed_and_obj(coarsened, factor,
   strategy)` 로 추출해 solve 경로와 seed-only 경로가 동일 함수를 호출하게 한다
   (DRY — solve 경로의 L233~245 도 이 헬퍼로 치환). 이게 권장.
3. seed-only 분기에서 `coarse_schedule = seed_schedule` 를 그대로 사용. reconstruct
   2-갈래는 `coarse_schedule` 기준으로 기존과 동일하게 호출하므로 추가 변경 없음.
4. `__all__` / 시그니처 외 export 변화 없음.

### `orchestration/controller.py` — `coarsen_solve_reconstruct` step (L2611~)

1. 시그니처에 `solve: bool = True` 추가.
2. `CoarsenSolveReconstructOption(...)` 생성(L2695)에 `solve=solve` 전달.
3. docstring 에 `solve` 한 단락 (False → CP-SAT 생략, 결정론적 seed-only; trajectory
   는 빈 값으로 무시됨).
4. **그 외 분기 불필요**: `draw_cp_trajectory` 처리(L2735)는 `trace.cp_progress_log`
   가 `()` 이므로 그대로 두면 빈 값이 들어가고 runner 가 스킵.
   `emit_phase_schedules` 는 `1_coarse_solver_result` 자리에 seed 스케줄이 기록되며
   의미상 자연스러움(coarse 해 = seed). 막지 않는다.

### config (별도, 소스 아님)

`metadata/<date>/csr_seed_only_config.yaml` 신규: 같은 factor 에 대해
`solve: false` 시나리오를 변경 전/후로 각각 돌려 seed 품질만 비교. (플랜 승인 후
실험 단계에서 작성.)

## Correctness obligations / tests

`tests/algorithm/test_coarsen_solve_reconstruct.py`:
1. **결정론**: `solve=False` 로 동일 인스턴스 2회 → `final_schedule` op start/end,
   `obj_value`, metrics 완전 동일.
2. **seed-only == reconstruct(seed)**: `solve=False` 의 `final_schedule` 가
   `_build_dispatch_seed_schedule` → `reconstruct_coarse_schedule` 직접 호출 결과와
   일치 (CP 가 개입하지 않음을 lock).
3. **metrics 계약**: `coarsened_status == "SEED_ONLY"`,
   `coarsened_obj_value == dispatch_seed_coarsened_obj`, `coarsened_obj_bound is None`,
   `cp_progress_log == ()`.
4. **기본값 불변**: `solve` 미지정 시 기존 solve 경로와 동일 (회귀 lock — 기존
   테스트가 그대로 green).
5. **trajectory 무시**: `solve=False` + `draw_cp_trajectory=True` 경로에서
   `csr_cp_trajectory` 가 falsy → JSON 미기록 (단위 또는 runner 레벨에서 확인).

## Execution order (TDD, 별도 대화에서)

1. (red) `solve=False` 결정론 + seed-only 동치 테스트 작성 → 옵션 필드 +
   `run_coarsen_solve_reconstruct` 분기 + `_seed_and_obj` 헬퍼 추가 → green.
2. metrics 계약 테스트, 기본값 회귀 lock.
3. 컨트롤러 step 에 `solve` 노출 + step-레벨 테스트(있으면).
4. `uv run ruff check` / `uv run ruff format`; 전체 `uv run pytest`.
5. config 작성 후 변경 전(현 floor)/후(F or L) A/B 실험.

## Notes

- 이 노브는 plans/20260630 의 F/L 변경과 **직교**한다: F/L 은 seed 품질을 바꾸고,
  `solve=False` 는 그 변화를 CP 노이즈 없이 관측 가능하게 만든다. 두 변경의 순서는
  무관하나, A/B 측정 인프라인 본 노브를 먼저 머지하면 F/L 평가가 쉬워진다.
- `solve=True` (기본) 경로는 byte-단위로 현재와 동일해야 한다 (헬퍼 추출이
  동작을 바꾸지 않음을 회귀 테스트로 보장).
