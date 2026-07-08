# Plan: `last_stage_rebuild_config` — round-2 last-stage 생성 정책 옵션

## Context

MCF-LB → full-schedule 합성 파이프라인(`calc_mcf_lb_and_derive_full_sch`)은 두
라운드로 동작한다.

- **Round 1** (feasible schedule 1): 원래 p/r로 MCF LB 계산 → last-stage-only
  schedule 생성 → reverse dispatch → 밀고 땡기고
  (`make_semi_active` + `insert_idle_time`).
- **Round 3 (= makespan 증가분 이용)**: round 1의 makespan 증가분으로
  `p_increment`/`r_increment`를 계산해 augmented instance에서 MCF LB 재계산
  (valid LB 아님).
- **Round 2 코드 (`calc_mcf_lb_r2_and_derive_full_sch`)** (feasible schedule 2):
  last-stage-only schedule 생성 → reverse dispatch → 밀고 땡기고.

### 사용자가 확정한 정의 (사용자 표현 "modified", config 값 `"increased_pr"`) = 현재 default 동작

1. 변경된(증가된) `p_j`, `r_j`로 마지막 stage schedule 생성.
2. 그 schedule에서 operation별 **completion time(종료시각)을 고정**한 채
   **processing time만 원래대로 복구** → operation 사이에 gap이 생김.
3. 이 상태에서 reverse-dispatch 및 조정 수행.

이는 현재 코드의 default 경로와 정확히 일치한다 (확인 완료):

- (1) round 2의 `heuristic_last_stage_only_from_mcf_lb`는 `p_increment`/
  `r_increment`로 augmented instance를 만들어 증가된 p/r로 생성
  (`last_stage_sch_builder.py:116-118,153-157`).
- (2) `build_full_sch_from_last_stage_only_sch(...,
  rebuild_last_stage_with_original_p=True)` →
  `reverse_dispatch_full_schedule`(`full_sch_builder.py:131-153`):
  `start = aug_end - p_orig`, `end = aug_end` (완료시각 고정, 원래 p 복구, gap).
- (3) 그 rebuilt schedule을 seed로 reverse-dispatch.

이 rebuild는 `p_increment != 0`일 때 동작.

## 설계: `last_stage_rebuild_config`

새 config `last_stage_rebuild_config: Literal["original_pr", "increased_pr", "best"]`.

round 2의 MCF LB(`apply`)는 **항상 augmented**로 계산된다. config는 **마지막
stage schedule을 어떻게 생성하는지**만 분기한다:

| 값 | round 2 last-stage 생성 |
|---|---|
| `"increased_pr"` (**default**) | 증가된 p/r로 생성 → completion 고정+원래 p 복구(gap) → reverse-dispatch. **현재/역사적 default 동작.** |
| `"original_pr"` | 원래 p/r로 생성(증분 미적용) → 복구 없이 곧바로 reverse-dispatch. 사용자 원문 "원래 p_j & r_j 이용"에 부합. |
| `"best"` | 위 두 변형 모두 실행 → **unflip(re-flip) 직전** `before_unflip_makespan`이 더 작은 것 선택 (tie → `"original_pr"`). |

> **default는 `"increased_pr"`** — 역사적 동작이 B이므로 하위호환(기존 config·테스트)
> 유지를 위해 default를 `"increased_pr"`로 둔다. `"increased_pr"` 경로는 기존
> `rebuild=(p_increment != 0)` + 증분 heuristic을 그대로 재현한다.

`"original_pr"`과 `"increased_pr"` 모두 최종 schedule은 problem-feasible (각각 생성
시점/복구 후 원래 p duration). 단일 stage(`make_semi_active` 생략)에서도 둘 다
feasible — fallback 불필요.

## 변경 항목 (구현 완료)

1. **`full_sch_builder.py` — `BuildFullSchResult`**: `"best"` 비교용
   `before_unflip_makespan: int | None` 필드 추가
   (`Phase3State.full_sch_before_unflip.makespan`; single-stage면 `None`).
2. **`mcf_lb_pipeline.py` — `calc_mcf_lb_r2_and_derive_full_sch`**:
   - 파라미터 `last_stage_rebuild_config="increased_pr"` 추가.
   - heuristic을 변형별로 호출 (`use_increments`: `"increased_pr"`=True,
     `"original_pr"`=False), build는 `rebuild`=(`"increased_pr"` & `p_increment!=0`).
   - `"best"`는 두 변형을 돌려 `before_unflip_makespan` 작은 쪽 선택.
   - phase drop: `lastS_only_before_rs`는 승자가 rebuild를 썼고
     `p_increment != 0`일 때만 keep, 그 외 drop (r1과 동일).
3. **`calc_mcf_lb_and_derive_full_sch` 합성**: 파라미터 추가 → r2로 forward.
4. **`controller.py`**: 파라미터 추가 → algo 호출 forward + diagnostic에
   `last_stage_rebuild_config_used` 기록(r2 실행 시).
5. **`diagnostic.py`**: `last_stage_rebuild_config_used: str | None` 필드 추가.

## 테스트 (구현 완료)

- 기존: default `"increased_pr"` = 역사적 동작 → 기존 테스트 전부 무수정 통과.
- 신규 (`tests/algorithm/mcf_lb/test_mcf_lb_pipeline.py`):
  - `"increased_pr"`(default): heuristic 마지막 stage duration = 원래 p + p_increment
    (증가 생성), `4_lastS_only_before_rs` 유지, 최종 feasible.
  - `"original_pr"`: heuristic duration = 원래 p (원래 생성), before_rs drop,
    최종 feasible.
  - `"best"`: `before_unflip_makespan` = min(original_pr, increased_pr).
  - single-stage: 세 값 모두 feasible.

## config (구현 완료)

- `metadata/20260623/increased_pr_last_stage_config.yaml`:
  `calc_mcf_lb_and_derive_full_sch`를 `last_stage_rebuild_config: "increased_pr"`로
  실행하는 단일 시나리오 + base CP-SAT.

## 검증

- `uv run ruff check`, `uv run ruff format`
- `uv run pytest tests/algorithm/mcf_lb tests/orchestration`
