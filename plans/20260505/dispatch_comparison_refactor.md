# Dispatch Comparison — 3-Part Refactor

Branch: `20260505_dispatch_comparison`

## Goal

Refactor the schedule-construction pipelines so that "주어진 instance + job
sequence → full schedule"의 모든 경로가 같은 3단계 골격을 공유하게 만든다.
이 골격은 다음 비교 실험을 자연스럽게 지원해야 한다:

- **(1) forward**: forward sequence → full mixed dispatch + IIT *(deferred)*
- **(2) reverse**: reverse instance, reverse sequence, full mixed dispatch,
  unflip + IIT *(현재 `_dispatch_by_reversed_sequence_with_iit` — refactor 대상)*
- **(3) MCF midpoint**: last-stage MCF midpoint warm-start → reverse-dispatch
  나머지 stage *(현재 `heuristic_last_stage_only_sch_from_mcf_lb` +
  `build_full_sch_from_last_stage_only_sch`)*
- **(3') sequence-LS-first**: last-stage sequence dispatch + IIT →
  reverse-dispatch 나머지 stage *(신규)*

골격:

| step | 책임 |
|---|---|
| 1 | instance 준비 (as-is or augmented) |
| 2 | job sequence 생성 (MCF window 기반 or 문제 parameter 기반) |
| 3a | sequence-based **full** dispatch (forward or reverse) |
| 3b-1 | last-stage-only schedule 생성 (sequence dispatch or MCF midpoint) |
| 3b-2 | 부분 last-stage schedule → full schedule (`reverse_dispatch_full_schedule`) |

## 사용자 합의 사항

1. **3a는 `reverse: bool` 플래그로 forward/reverse 통합.** Forward도
   reverse와 동일하게 jtm/mtj 양쪽 시도 후 weighted E+T로 best 선택 (현재
   reverse만 그렇게 함).
2. **3b-1는 두 함수로 분리** — sequence dispatch variant와 MCF midpoint
   variant. 입력 시그니처가 본질적으로 다르므로 모드 플래그 대신 별도 함수.
3. **(1)은 deferred** — 3a에 forward 경로는 만들지만, 별도 subroutine 추가
   는 미룸.

## 현재 상태 (refactor 출발점)

### Algorithm layer (이미 존재)

- `algorithm/dispatcher/mixed.py::MixedDispatcher.get_best_mixed_schedule_by_sequence`
  — sequence dispatch + np-스윕 + makespan/E+T criterion
- `algorithm/mcf_lb/last_stage_only.py::heuristic_last_stage_only_from_mcf_lb`
  — **3b-1 MCF midpoint variant 본체** (현재 시그니처가 `job_priority` —
  내부에서 sequence를 만든다)
- `algorithm/mcf_lb/phase3_dispatch.py::reverse_dispatch_full_schedule`
  — **3b-2 본체** (이미 `(instance, last_stage_only_schedule)`만 받는 순수
  함수). 변경 불필요.
- `algorithm/pm_pmtn_sorter.py::pm_pmtn_sort_job_sequence` — MCF window
  기반 step 2의 본체.
- `parameters/ffc_ddw_params.py::get_*_job_sequence` (8종) — 문제 parameter
  기반 step 2.

### Controller layer (이미 존재)

- `_dispatch_by_reversed_sequence_with_iit` (controller.py:1925) — **3a
  reverse variant 본체**. Algorithm 레이어로 이동 + 일반화 대상.
- `_initialize_by_reversed_sequence` (controller.py:2051) — sequence_getter
  를 받아 (2) 파이프라인을 돌리는 헬퍼. **5개 subroutine이 호출**:
  `initialize_by_due2_weight_pos`, `initialize_by_w1`, `initialize_by_wxd1`,
  `initialize_by_wxd2` (+ MCF-LB 4단계 내부에서도 사용).
- `heuristic_last_stage_only_sch_from_mcf_lb` (controller.py:964) — 3b-1
  MCF midpoint wrapper. `job_priority: PmPrmpSortKey`를 받아 algorithm
  레이어 함수로 위임.
- `build_full_sch_from_last_stage_only_sch` (controller.py:1235) — 3b-2
  wrapper. 변경 불필요.

## 목표 상태

### Algorithm layer (refactor 후)

신규/변경 함수들 (모두 algorithm 레이어):

1. **신규**: `algorithm/dispatcher/full_dispatch.py::dispatch_full_schedule_by_sequence`
   ```python
   def dispatch_full_schedule_by_sequence(
       instance: FFcDDWParameters,
       job_sequence: Sequence[str],
       *,
       reverse: bool,
       logger: logging.Logger | None = None,
   ) -> tuple[FFcSchedule, float]:
       """3a — sequence-based full dispatch. reverse=True 면 instance 뒤집고
       sequence 뒤집어 dispatch 후 unflip; False 면 forward로 그대로 dispatch.
       양 경우 모두 jtm/mtj 둘 다 시도 후 weighted E+T로 best 선택. 마무리로
       make_semi_active + insert_idle_time. (schedule, weighted E+T) 반환."""
   ```
   - 본체는 `_dispatch_by_reversed_sequence_with_iit`을 일반화한 형태.
   - reverse=False 분기: `MixedDispatcher(instance)` 위에서 jtm/mtj 둘 다
     `get_best_mixed_schedule_by_sequence(job_sequence, criteria="makespan")`
     호출 → forward instance 기준 weighted E+T 비교 → 더 작은 쪽 선택.
   - reverse=True 분기: 기존 `_dispatch_by_reversed_sequence_with_iit`
     로직 그대로 (`reverse_stages` → `reversed(job_sequence)` → 두 dispatch
     → `as_reversed`로 unflip → forward instance 기준 weighted E+T 비교).
   - 공통 마무리: `schedule.make_semi_active(stage_2_job_2_p_map)` +
     `schedule.insert_idle_time(due_window, ewt, twt)`.

2. **신규**: `algorithm/mcf_lb/last_stage_only.py::last_stage_only_from_sequence`
   ```python
   def last_stage_only_from_sequence(
       instance: FFcDDWParameters,
       job_sequence: Sequence[str],
       *,
       logger: logging.Logger | None = None,
   ) -> FFcSchedule:
       """3b-1 sequence variant — last stage에 sequence를 단순 dispatch
       (upstream stage processing 합을 release time으로 사용) 후
       make_semi_active + insert_idle_time. last-stage-only schedule 반환."""
   ```
   - `FFcSchedule.dispatch_stage_by_jobs(last_stage_id, job_sequence,
     job_2_release=...)` 사용 (`phase1_mcf._build_seed`와 동일 패턴).
   - `make_semi_active(start_from_stage=last_stage_id, job_2_release_map=...)`
     + `insert_idle_time(...)`로 마무리.
   - 출력: 다른 stage가 비어있는 last-stage-only `FFcSchedule` —
     `reverse_dispatch_full_schedule`이 그대로 받을 수 있는 형태.

3. **변경**: `heuristic_last_stage_only_from_mcf_lb` (`last_stage_only.py:96`)
   - 시그니처 변경: `job_priority: PmPrmpSortKey` 매개변수 제거,
     `job_sequence: Sequence[str]` 추가.
   - 내부 `pm_pmtn_sort_job_sequence_with_log` 호출 제거 (line 149-155);
     호출자가 sequence를 미리 준비.
   - 본체의 `_insert_jobs_at_desired_starts(appended=job_sequence, ...)`은
     그대로 — midpoint warm-start에는 여전히 `mcf_preemptive_schedule` +
     sequence 둘 다 필요.

4. **변경 없음**: `reverse_dispatch_full_schedule` (3b-2). 이미 깔끔.

### Controller layer (refactor 후)

1. **변경**: `_dispatch_by_reversed_sequence_with_iit` (controller.py:1925)
   → 본체를 `dispatch_full_schedule_by_sequence(..., reverse=True)` 호출로
   교체 (얇은 wrapper). 이름은 유지 (내부 헬퍼이므로).
2. **변경**: `heuristic_last_stage_only_sch_from_mcf_lb` (controller.py:964)
   → 시그니처는 유지 (`job_priority` 외부 API). 내부에서 step 2(`window_map`
   계산 + `pm_pmtn_sort_job_sequence_with_log`)를 명시적으로 수행 후 새
   시그니처의 `heuristic_last_stage_only_from_mcf_lb`에 sequence 전달.
   → 즉 controller 레이어에서 step 2를 들고, algorithm 레이어는 sequence
   를 받기만 함.
3. **신규**: `dispatch_last_stage_only_by_sequence(job_sequence)` 단계
   메서드. `last_stage_only_from_sequence`을 호출해 `self.last_stage_only_sol`
   에 저장. 그러면 기존 `build_full_sch_from_last_stage_only_sch`이 그대로
   소비 → (3') 파이프라인 완성.
4. **변경 없음**: `build_full_sch_from_last_stage_only_sch`,
   `_initialize_by_reversed_sequence`, 기존 `initialize_by_*` 5개.

### 비교 실험 surface (out of scope of this plan, but enabled by it)

이 refactor 후 다음이 자연스럽게 가능해진다:
- (2) 변형: 기존 `initialize_by_*`은 그대로.
- (3) 변형: 기존 `run_*`은 그대로.
- (3') 신규 subroutine: `apply_lb_by_mcf` 없이도 `dispatch_last_stage_only_by_sequence`
  + `build_full_sch_from_last_stage_only_sch`로 구성 가능.

비교 실험용 `run_*` subroutine 추가, experiment config 추가, main.py 와이어업
은 **별도 plan**에서 진행 (이 plan은 refactor만).

## 작업 순서

### Phase A — algorithm layer

1. `algorithm/dispatcher/full_dispatch.py` 생성 + `dispatch_full_schedule_by_sequence`
   구현. 본체는 controller의 `_dispatch_by_reversed_sequence_with_iit`을
   참조해 reverse 분기 그대로 옮기고, forward 분기 추가.
2. `algorithm/mcf_lb/last_stage_only.py`에 `last_stage_only_from_sequence`
   추가.
3. `algorithm/mcf_lb/last_stage_only.py::heuristic_last_stage_only_from_mcf_lb`
   시그니처 변경: `job_priority` 제거, `job_sequence` 추가, 내부 sort 호출
   제거.
4. `uv run ruff check && uv run ruff format`.

### Phase B — controller layer

5. `heuristic_last_stage_only_sch_from_mcf_lb` 본체에 step 2 추출 (window
   map 계산 + `pm_pmtn_sort_job_sequence_with_log` 호출), 새 시그니처의
   algorithm 함수에 sequence 전달. 외부 API (`job_priority` 인자) 보존.
6. `_dispatch_by_reversed_sequence_with_iit` 본체를
   `dispatch_full_schedule_by_sequence(..., reverse=True)` 호출로 교체.
7. 신규 `dispatch_last_stage_only_by_sequence` step 메서드 추가 — `(3')`이
   필요로 할 마지막 퍼즐 조각. 기존 `_initialize_by_reversed_sequence` 패턴
   참고해 timing/registration 정확히 매칭.
8. `uv run ruff check && uv run ruff format`.

### Phase C — 회귀 검증

9. 기존 (2) 사용처 — `initialize_by_due2_weight_pos` 등 — 와 (3) 사용처 —
   `run_mcf_lb_4`, `calc_mcf_lb_and_derive_full_sch` 등 — 의 동작이 바뀌지
   않았는지 작은 instance에서 확인. AlgRecord obj_value 변하지 않아야 함.
10. 가능하면 기존 회귀 테스트가 있는지 찾아 실행 (`tests/`, `pytest`).

## 비고 / 결정 미루는 사항

- **augmented instance** (`p_increment` / `r_multiplier` / `r_increment`):
  현재 (3)에만 적용. step 1을 별도로 떼어내지 않고 `heuristic_last_stage_only_sch_from_mcf_lb`
  내부에 그대로 둔다 (YAGNI). 3'에서도 필요해지면 그때 끌어올린다.
- **3b-2의 `rebuild_last_stage_with_original_p` 플래그**: augmented instance
  와 짝이 맞아 동작. (3) 경로만 유지하고 3'는 augmented 안 쓰므로 영향 없음.
- 명명 규칙: `dispatch_full_schedule_by_sequence`, `last_stage_only_from_sequence`,
  `dispatch_last_stage_only_by_sequence` — 함수와 step 메서드 모두 동사로
  시작, 각 layer 위치 명확. 더 좋은 이름 제안 환영.
