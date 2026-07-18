# Plan: `coarsen_solve_reconstruct` v3 paired-dispatch seed (warm-start hint)

## Context

`coarsen_solve_reconstruct`(CSR)는 현재 coarsened instance 위에서 dispatch seed를
만들어 base CP-SAT에 warm-start hint로 주입한다. seed 전략은
`seed_dispatch ∈ {job_wise, mixed}` 두 가지로,
`_build_dispatch_seed_schedule(coarsened, strategy)`에서 **단일 EDD 시퀀스**
(`d^+_j ↑, w^+_j ↓, given index ↑`)를 dispatch한 뒤 마지막 stage idle insertion으로
만든다 (`src/ffc_ddw_sum_et/algorithm/coarsen_solve_reconstruct.py:128`).

커밋 `f59db9b feat(controller): add v3 paired dispatch init step`에서 **새 초기화
method `initialize_by_dispatch_v3`**(`controller.py:1797`)가 추가되었다. 이는
`V3_PRIORITY_SET = (edd, wspt_twt, wxd2)`의 각 priority를 **sd/rd 두 방향**으로
디코드해 `2·|P*| = 6`개 후보를 만들고, weighted-ET(wET) 최소 1개를 incumbent로
register한다(history에 점 하나). 대형 1440 인스턴스에서 obj 153,184 / RPDf 0.8263으로
justification-v3 paired oracle과 일치 검증됨.

**목표**: 이 v3 paired-dispatch pool을 CSR의 새 seed 전략
`seed_dispatch="v3"`로 추가해, 기존 `mixed`보다 강한 seed를 warm-start로 제공하고
효과를 비교한다.

### 핵심 제약: 스케일

CSR hint는 **coarsened 모델의 op_vars에** `apply_hints_from_schedule`로 적용되므로,
seed schedule은 반드시 **coarsened 스케일**이어야 한다. v3 init step은 원본
`self.instance` 위에서 동작하고 결과를 controller incumbent로 등록할 뿐이므로,
그 incumbent를 그대로 가져다 쓰는 것(original→coarsened start time 변환)은
침습적이다. 대신 **v3 pool 로직을 coarsened 인스턴스 위에서 그대로 재실행**하면
seed가 자동으로 coarsened 스케일이 된다. → 이 경로를 채택한다.

### 핵심 제약: 레이어 의존성

CSR(`algorithm/`)은 `orchestration/`을 import할 수 없다(CLAUDE.md). v3 pool 로직은
현재 controller 메서드(`_dispatch_by_simple_sequence_with_iit`,
`_dispatch_by_reversed_sequence_with_iit`)에 있으므로, **pure 함수로 algorithm
레이어에 추출**한 뒤 controller와 CSR이 **둘 다 그 함수를 호출**하도록 한다(SSOT/DRY).

### 읽은 코드/문서

- `controller.py:1797` `initialize_by_dispatch_v3`: priority p마다 sd/rd 후보 2개,
  6개 중 min-wET register. `_register` 단일 호출(step 계약 준수).
- `controller.py:1451` `_dispatch_by_simple_sequence_with_iit(job_sequence)`:
  forward job-centric decode(`MixedDispatcher.get_job_centric_schedule_by_sequence`)
  → `make_semi_active` → `insert_idle_time` → wET. **register-free.**
  단 `self.instance`를 하드코딩 → 추출 시 `instance` 인자화 필요.
- `controller.py:1467` `_dispatch_by_reversed_sequence_with_iit(job_sequence,
  instance=None)`: stage-reverse → reversed seq를 `get_best_mixed_schedule_by_sequence`
  (`criteria="makespan"`, machine_then_job True/False 둘 다) → `as_reversed` →
  더 나은 wET 선택 → `make_semi_active` → `insert_idle_time` → wET. **이미
  `instance` 파라미터 보유** → coarsened 전달 가능. register-free.
- `controller.py:98` `V3_PRIORITY_SET: tuple[DispatchSeqKey, ...] =
  ("edd", "wspt_twt", "wxd2")`.
- `parameters/sorter.py:73` `dispatch_seq_job_sequence(instance, key)`: **pure**
  함수, instance에서 priority sequence 도출. coarsened 전달 가능.
- `parameters/sorter.py:32` `DispatchSeqKey` Literal.
- `coarsen_solve_reconstruct.py:128` `_build_dispatch_seed_schedule(coarsened,
  strategy)`: `job_wise`/`mixed` 분기. 여기에 `v3` 분기 추가.
- `coarsen_solve_reconstruct.py:79` `CoarsenSolveReconstructOption.seed_dispatch:
  Literal["job_wise", "mixed"] = "mixed"`. `:174` `_solve_coarsened_model`,
  `:317` `run_coarsen_solve_reconstruct` 호출부. metrics에 이미 `seed_dispatch`,
  `dispatch_seed_coarsened_obj` 기록(`:352`, `:420`).
- `controller.py:2497` CSR step `seed_dispatch: str = "mixed"` kwarg → option 전달.
- `FFcDDWParameters.reverse_stages`, `FFcSchedule.as_reversed`,
  `MixedDispatcher.{get_job_centric_schedule_by_sequence,
  get_best_mixed_schedule_by_sequence}` 모두 존재.
- `TODO.md`: 충돌하는 deferred TODO 없음.

---

## Design

### D1. Pure helper (algorithm 레이어, 신규)

`src/ffc_ddw_sum_et/algorithm/dispatcher/paired.py`(신규)에:

```python
def dispatch_forward_with_iit(
    instance: FFcDDWParameters, job_sequence: Sequence[str],
    logger: logging.Logger | None = None,
) -> tuple[FFcSchedule, float]:
    """sd 파이프라인: forward job-centric decode + semi-active + IIT + wET."""
    # _dispatch_by_simple_sequence_with_iit 본문을 instance 인자화하여 이식

def dispatch_reversed_with_iit(
    instance: FFcDDWParameters, job_sequence: Sequence[str],
    logger: logging.Logger | None = None,
) -> tuple[FFcSchedule, float]:
    """rd 파이프라인: stage-reverse + reversed mixed(makespan) + unflip + IIT + wET."""
    # _dispatch_by_reversed_sequence_with_iit 본문을 이식

def build_v3_paired_dispatch_schedule(
    instance: FFcDDWParameters,
    priorities: Sequence[DispatchSeqKey] = V3_PRIORITY_SET,
    logger: logging.Logger | None = None,
) -> tuple[FFcSchedule, float, str]:
    """v3 paired pool: priority×{sd,rd} 6후보 중 min-wET (schedule, obj, label)."""
    candidates: list[tuple[float, str, FFcSchedule]] = []
    for p in priorities:
        seq = dispatch_seq_job_sequence(instance, p)
        sd_sch, sd_obj = dispatch_forward_with_iit(instance, seq, logger)
        candidates.append((sd_obj, f"sd:{p}", sd_sch))
        rd_sch, rd_obj = dispatch_reversed_with_iit(instance, seq, logger)
        candidates.append((rd_obj, f"rd:{p}", rd_sch))
    best_obj, best_label, best_sch = min(candidates, key=lambda c: c[0])
    return best_sch, best_obj, best_label
```

- `V3_PRIORITY_SET`은 `controller.py`에서 **`parameters/sorter.py`로 이전**하고
  controller·`dispatcher/paired.py`는 sorter에서 재-import (SSOT).
- 점수/선택 기준은 wET로 v3 원본과 동일 → 동작 보존.

### D2. controller 리팩터 (DRY, 동작 보존)

- `_dispatch_by_simple_sequence_with_iit(seq)` →
  `return dispatch_forward_with_iit(self.instance, seq, self.logger)` thin wrapper.
- `_dispatch_by_reversed_sequence_with_iit(seq, instance=None)` →
  `dispatch_reversed_with_iit(instance or self.instance, seq, self.logger)` 위임.
- `initialize_by_dispatch_v3`는 후보 enumeration을
  `build_v3_paired_dispatch_schedule(self.instance, priorities)`로 대체하되,
  **register 단일 호출 + 진단 로그**(`_log_dispatch_seed_diagnostics`)는 step에 유지.
  (step 계약: `_register` 1회, `elapsed_time` 측정 직전 무작업 — 변경 없음.)

### D3. CSR `v3` seed 분기

`coarsen_solve_reconstruct.py`:

```python
# option / _solve_coarsened_model / controller step 의 Literal 확장
seed_dispatch: Literal["job_wise", "mixed", "v3"] = "mixed"

# _build_dispatch_seed_schedule 내부
if strategy == "v3":
    seed, _obj, _label = build_v3_paired_dispatch_schedule(coarsened)
    return seed
```

- coarsened 위에서 pool을 돌리므로 seed는 coarsened 스케일 → `apply_hints_from_schedule`
  그대로 적용. metrics의 `dispatch_seed_coarsened_obj`는 기존 경로
  (`compute_weighted_earliness_tardiness(seed_schedule, coarsened)`)가 그대로 잰다.

---

## Work Packages

의존: **WP-1 → WP-2, WP-3** (병렬) → WP-4 → WP-5(config) / WP-6(tests).

> 협업 주의: 동일 worktree를 공유하는 서브에이전트는 `git`(checkout/stash 등)을
> 실행하지 않는다. 각 WP는 자기 파일만 편집한다.

### WP-1 — `algorithm/dispatcher/paired.py` (신규)
- D1의 세 pure 함수 작성. `_dispatch_by_*_with_iit` 본문을 instance 인자화하여 이식.
- `V3_PRIORITY_SET`을 `parameters/sorter.py`로 이전. `dispatch_seq_job_sequence`,
  `DispatchSeqKey`, `V3_PRIORITY_SET` 모두 `parameters.sorter`에서 import.
- **계약**: `build_v3_paired_dispatch_schedule(instance)` → instance 스케일 feasible
  `FFcSchedule` + 그 wET + best label. v3 원본과 동일한 후보·선택.

### WP-2 — `orchestration/controller.py`
- **의존**: WP-1.
- `_dispatch_by_simple_sequence_with_iit` / `_dispatch_by_reversed_sequence_with_iit`를
  pure 함수 wrapper로 축약. `initialize_by_dispatch_v3`는 pool을
  `build_v3_paired_dispatch_schedule`로 위임(register/로그는 유지). `V3_PRIORITY_SET`
  import 경로 갱신.
- **계약**: `initialize_by_dispatch_v3`의 register 결과(best obj/schedule)와 기존
  init step들의 동작 불변(회귀 테스트로 보증).

### WP-3 — `algorithm/coarsen_solve_reconstruct.py`
- **의존**: WP-1.
- `seed_dispatch` Literal에 `"v3"` 추가(option `:79`, `_solve_coarsened_model` `:174`).
- `_build_dispatch_seed_schedule`에 `v3` 분기(D3). `build_v3_paired_dispatch_schedule`
  import.
- **계약**: `v3` seed는 coarsened scale feasible. 동일 timelimit에서 obj 비퇴행.

### WP-4 — `orchestration/controller.py` CSR step
- **의존**: WP-3.
- `coarsen_solve_reconstruct` step(`:2490`)의 `seed_dispatch: str` docstring에
  `"v3"` 의미 1줄 추가(타입은 이미 `str` → 코드 변경 불필요, 문서만).

### WP-5 — 실험 config `metadata/20260625/coarsen_solve_reconstruct_v3_seed_config.yaml`
- **의존**: WP-4.
- factor·timelimit·solver_thread_cnt를 동일 고정, `seed_dispatch`만
  `mixed` vs `v3`로 다른 두 scenario(seed 효과 격리):

```yaml
scenarios:
  - name: csrN_mixed
    output_subdir: csrN_mixed
    subroutine_flow:
      - method: coarsen_solve_reconstruct
        factor: <F>
        timelimit: "<TL>"
        solver_thread_cnt: <T>
        seed_dispatch: mixed
        emit_phase_schedules: true
        draw_cp_trajectory: true
  - name: csrN_v3
    output_subdir: csrN_v3
    subroutine_flow:
      - method: coarsen_solve_reconstruct
        factor: <F>
        timelimit: "<TL>"
        solver_thread_cnt: <T>
        seed_dispatch: v3
        emit_phase_schedules: true
        draw_cp_trajectory: true
```
(상단 공통 키는 `coarsen_solve_reconstruct_3_config.yaml`에서 복사.)

### WP-6 — 테스트
- **의존**: WP-1, WP-3.
1. `tests/algorithm/dispatcher/test_paired.py`(신규):
   - `build_v3_paired_dispatch_schedule`: 후보 6개 생성, feasible, min-wET 선택,
     determinism(동일 입력→동일 best).
   - 작은 instance에서 controller v3 결과와 pure 함수 결과 동치(회귀; WP-2 이식 보증).
2. `tests/algorithm/test_coarsen_solve_reconstruct.py`:
   - `_build_dispatch_seed_schedule(coarsened, "v3")` feasible(precedence/machine
     충돌 없음), coarsened 스케일.
   - 통합: `seed_dispatch="v3"`로 hint 적용 후 동일 timelimit 최종 obj 비퇴행.
3. `tests/orchestration/test_controller.py`: 기존 v3 테스트(single-register,
   best-of-6, determinism, sd regression) green 유지.
4. `uv run ruff check` / `uv run ruff format`.

---

## 검증 계획

1. WP별 단위 테스트 red→green, 특히 WP-2 이식 후 기존 v3 controller 테스트 회귀.
2. `uv run ruff check` / `uv run ruff format`.
3. WP-5 config로 인스턴스 스폿 실행 → `csrN_mixed` vs `csrN_v3`의
   `dispatch_seed_coarsened_obj`(seed 품질)와 `reconstructed_obj_value`(최종해) 비교.
   v3 seed의 coarsened wET가 mixed 이하인지, 최종 obj가 개선되는지 확인.

## Decisions

- **`seed_dispatch` 기본값**: `"mixed"` 유지(확정). v3는 config에서 명시적으로
  선택하며 비-config 경로의 기본 동작은 바꾸지 않는다.
- **`V3_PRIORITY_SET` 이전 위치**: `parameters/sorter.py`로 이전(확정). sorter가
  `DispatchSeqKey`/`dispatch_seq_job_sequence`의 소유지이므로 priority set도 같은
  곳에 둔다. `controller.py`와 `dispatcher/paired.py`는 sorter에서 재-import.
- **pure helper 위치**: `algorithm/dispatcher/paired.py` 신규 모듈(레이어 규칙 준수).
- **incumbent 재사용(Route B)**: 미채택(original→coarsened schedule 변환 침습적).
- **v3 priority set 변형**: `V3_PRIORITY_SET` 고정 사용. CSR에서 priority를 config로
  노출하는 것은 YAGNI(필요 시 후속).
