# NEH-CP: incumbent 스케줄에서 job insertion sequence 유도

작성일: 2026-07-31 / 대상 브랜치: `20260731_neh_cp`

이 문서는 **코드 변경 계획**이다. 별도 대화에서 이 문서만 읽고 구현할 수 있도록
현재 상태·이식 대상·설계 결정·작업 순서·테스트를 모두 담는다.

---

## 1. 배경

### 1.1 현재 저장소의 `neh_cp`

- 컨트롤러 스텝: `src/ffc_ddw_sum_et/orchestration/controller.py:2210` `neh_cp(...)`
- 삽입 순서는 **인스턴스 파라미터에서만** 유도한다:
  `NehCpOption.job_priority: ParamSortKey` → `neh_cp_job_sequence(instance, job_priority)`
  (`src/ffc_ddw_sum_et/algorithm/neh_cp/sequence.py:13`).
- incumbent 스케줄은 **전혀 읽지 않는다**. 다만 실전 flow에서는 incumbent가 이미 존재한다:
  `metadata/20260709/mcf_lb_fmm_neh_cp.yaml`의
  `calc_mcf_lb_and_derive_full_sch → run_flip_makespan_cp_from_incumbent → neh_cp`.
- **하부는 이미 준비되어 있다**:
  - `NehCpOption.custom_job_sequence: tuple[str, ...] | None`
    (`algorithm/neh_cp/option.py:41`)
  - `NehCpDispatcher`가 이를 permutation 검증 후 그대로 사용
    (`algorithm/neh_cp/dispatcher.py:80`, `_validate_custom_sequence` `:658`)
  - 즉 **dispatcher / option 변경은 필요 없다.** 새로 만들 것은
    "스케줄 → job 순서" 유도 함수와 컨트롤러 스텝 4개뿐이다.

### 1.2 참조 저장소 (`/home/hjt/code/hybridflowshop`)

`hybridflowshop/controller/hfs_cp_lns.py:21874 def neh_cp`는 시작 시
`ref_schedule = self.solution_manager.get_incumbent()`를 **필수로** 요구하고
(없으면 `ValueError`), 거기서 삽입 순서를 유도한다. 순서 유도 함수 3종은
`hybridflowshop/schedule_lite.py`에 있다:

| 참조 함수 | 위치 | 정렬 키 |
|---|---|---|
| `get_midpoint_sequence` | `schedule_lite.py:2337` | `(첫 stage 시작 + 마지막 stage 종료)/2`, tie: 첫 stage 시작 → tiebreak rank → 원래 job 인덱스 |
| `get_bottleneck_stage_job_sequence` | `schedule_lite.py:2378` | 병목 stage의 시작시각, tie: 그 stage의 `(시작+종료)/2` → tiebreak rank → 원래 job 인덱스 |
| `get_first_stage_start_sequence` | `schedule_lite.py:2435` | 첫 stage 시작시각, tie: 원래 job 인덱스 |

병목 stage 정의: `get_stage_2_mc_2_idle_time_map()`의 machine별 idle 합이
stage 단위로 **최소**인 stage (`min(stage_2_total_idle_time, key=...)`).

이식하지 않는 참조 기능:

- `job_seq_by_last_retained_cp` / `_get_last_retained_cp_consensus_job_sequence`
  (`hfs_cp_lns.py:21773`) — retained-stage CP LB 산출물에 의존. 이 저장소에는
  대응 개념이 없다(자리를 `mcf_lb`가 차지). **범위 밖.**
- `job_sequence_order_rule` / `_get_sequence_insertion_order`
  (`hfs_cp_lns.py:24584`) — 처리시간 기반 Johnson류 인스턴스 규칙. 이 저장소는
  이미 `ParamSortKey` / `DispatchSeqKey`로 더 풍부한 인스턴스 규칙군을 갖고 있다.
  **범위 밖.**
- `save_input_midpoint_sequence_as` / `job_seq_by_saved_sequence` — 호출 간 순서
  저장·재사용. 현재 flow에 `neh_cp` 반복 호출이 없어 YAGNI. **범위 밖.**
- `preserved_head_job_portion`, profile-fix 계열 파라미터 — 순서와 무관한 별개 기능.
  **범위 밖.**

### 1.3 사용자 결정 사항 (확정)

1. **이식할 모드 4종**: `midpoint`, `first_stage`, `bottleneck`, `completion`
   (`completion` = 마지막 stage 종료시각 순. 참조에는 없는 추가안 —
   목적함수가 weighted E+T이므로 due window와 가장 직접 대응).
2. **노출 형태**: 모드별 **컨트롤러 스텝 메서드 4개**를 신설한다.
   (routix `subroutine_flow_validator.py:98`가 config kwargs를 메서드의 **명시
   시그니처**로 검증하고 `_fill_method_defaults`가 기본값도 시그니처에서 채우므로
   `**kwargs` wrapper는 불가 → 시그니처 복제는 불가피. 대신 본문은 공통 private
   코어 1벌로 유지한다. 이점: `report/method_mean_scatter.py`가 base method 이름으로
   계열을 나누므로 차트에서 모드별로 분리되어 보인다.)
3. **incumbent 부재 시**: 예외가 아니라 `job_priority`로 **fallback + warning 로그**.
4. **부가 기능 3종 포함**: (a) 동률 tie-break을 `job_priority` 순위로,
   (b) sequence 다양성 진단 로깅, (c) 사용된 sequence를 `_step_log.yaml`에 기록.

---

## 2. 설계

### 2.1 신설 모듈: `src/ffc_ddw_sum_et/solution/schedule_sequence.py`

순서 유도는 **스케줄에서 job 순서를 뽑는 범용 유틸**이므로 `algorithm/neh_cp/`가
아니라 `solution/`에 둔다 (참조 저장소도 `schedule_lite.py`에 두었다).
`algorithm/neh_cp/dispatcher.py`가 이미 `solution.ffc_schedule`을 import 하므로
알고리즘 경계 규칙(`docs/algorithm-principles.md`)에 위배되지 않는다.

```python
"""Job-order extraction from an existing FFcSchedule."""

ScheduleSeqSource = Literal["midpoint", "first_stage", "bottleneck", "completion"]

def schedule_job_sequence(
    schedule: FFcSchedule,
    source: ScheduleSeqSource,
    *,
    tiebreak_rank: Mapping[str, int] | None = None,
) -> list[str]: ...

def normalized_mean_rank_distance(
    reference_sequence: Sequence[str],
    candidate_sequence: Sequence[str],
) -> float: ...
```

**정렬 키 정의** (모두 오름차순, tie-break은 아래 §2.2):

| `source` | 1차 키 | 2차 키 |
|---|---|---|
| `midpoint` | `(첫 stage 시작 + 마지막 stage 종료) / 2` | 첫 stage 시작 |
| `first_stage` | 첫 stage 시작 | 마지막 stage 종료 |
| `bottleneck` | 병목 stage 시작 | 병목 stage `(시작+종료)/2` |
| `completion` | 마지막 stage 종료 | 첫 stage 시작 |

`first_stage`의 2차 키는 참조(원래 인덱스)와 다르게 "마지막 stage 종료"를 쓴다 —
동률 시 더 의미 있는 신호이고, 그다음 tie-break이 어차피 rank이므로 안전하다.
(참조와 완전히 동일하게 가려면 2차 키를 빼면 된다. 구현 시 이 문서 기준을 따른다.)

**병목 stage 선정**: `schedule.get_stage_2_mc_2_idle_time_map()`
(`solution/ffc_schedule.py:367`, 기본 `include_idle_before_first_op=False`)의
machine별 idle을 stage 단위로 합산해 **최소** stage. 동률이면 `schedule.stages`
순서상 앞선 stage.

> **도메인 주의(구현 시 주석으로 남길 것)**: 이 저장소의 스케줄은 E/T 목적을 위해
> `insert_idle_time`으로 **의도적 유휴시간이 삽입**되어 있다. 따라서 "idle 합이
> 최소인 stage = 병목"이라는 makespan 기반 참조의 해석이 그대로 성립하지 않을 수
> 있다. 기능은 그대로 이식하되, 실험에서 `bottleneck` 모드가 기대와 다르게
> 동작하면 이 점을 먼저 의심할 것.

**성능**: 참조 구현은 job마다 `next(t for (job, stage, _), t in start_map.items() ...)`
로 전체 맵을 훑어 O(n²·c·m)이다. **그대로 옮기지 말 것.** 이 저장소에는
`get_ji_2_end_time_map()`(`ffc_schedule.py:350`)이 있고, 대칭으로
`get_ji_2_start_time_map()`을 추가해 한 번의 O(n·c) 패스로 (job, stage) → 시각
맵을 만든 뒤 조회한다.

**작업 A**: `FFcSchedule.get_ji_2_start_time_map()` 추가
(`get_ji_2_end_time_map` 바로 위/아래, 동일 구현 패턴, `_iter_operations` 사용).

**누락 job 방어**: incumbent는 모든 job을 담고 있어야 정상이나, dispatcher가
permutation을 강제하므로(`dispatcher.py:658`) 유도 결과가 인스턴스의 job 집합과
일치하지 않으면 컨트롤러 쪽에서 보정한다 (§2.3 4단계).

### 2.2 tie-break을 `job_priority` 순위로

`schedule_job_sequence(..., tiebreak_rank=rank_map)`의 `rank_map`은 컨트롤러가
`{job_id: idx for idx, job_id in enumerate(neh_cp_job_sequence(instance, job_priority))}`
로 만들어 넘긴다. 정렬 키 튜플의 **마지막 요소**로 rank를 넣는다:

```python
key = (primary, secondary, tiebreak_rank.get(job_id, fallback_idx), job_id)
```

`tiebreak_rank=None`이면 `schedule.jobs`의 원래 인덱스를 쓴다(참조와 동일).
마지막에 `job_id`를 넣어 완전 결정론을 보장한다.

### 2.3 컨트롤러 변경 (`orchestration/controller.py`)

현재 `neh_cp`의 본문(`:2239`–`:2357`)을 **private 코어로 이동**하고, public 스텝
5개는 얇은 위임자가 된다. 단일 소스 원칙(CLAUDE.md) 준수.

```python
def _run_neh_cp(
    self,
    *,
    job_seq_source: ScheduleSeqSource | None,
    step_label: str,          # 로그/step_log에 남길 이름
    job_priority: NehCpJobPriority,
    solver_thread_cnt: int,
    ...  # 기존 neh_cp의 나머지 파라미터 전부, 전부 keyword-only
) -> SubroutineReport:
```

`_run_neh_cp` 본문은 기존 `neh_cp` 본문과 동일하되 다음이 추가된다
(**스텝 계약 준수**: `orchestration/AGENTS.md` — `_register`는 호출당 정확히 1회,
`elapsed` 측정과 `_register` 사이에 작업 금지. 순서 유도는 전부 `elapsed` 측정
**이전**에 수행한다):

1. `start_elapsed` / `is_stopping_condition()` 프리플라이트 — 기존과 동일.
2. `priority_sequence = neh_cp_job_sequence(instance, job_priority)` (항상 계산;
   tie-break rank 및 fallback 양쪽에 쓰인다).
3. `custom_job_sequence` 결정:
   - `job_seq_source is None` → `None` (기존 `neh_cp`의 동작 그대로).
   - `job_seq_source is not None`:
     - `incumbent = self.solution_manager.get_incumbent()`
     - `incumbent is None or incumbent.schedule is None` →
       `self.logger.warning(...)` 후 `None`으로 두어 `job_priority` fallback.
       메시지에 스텝 이름·요청 모드·"chain it after a seeding subroutine such as
       calc_mcf_lb_and_derive_full_sch"를 포함.
     - 아니면 `schedule_job_sequence(incumbent.schedule, job_seq_source,
       tiebreak_rank=rank_map)`.
4. 유도 결과 보정: 인스턴스 job 집합과 다르면(방어) 순서를 유지하며 중복·이물 제거 후
   누락분을 `priority_sequence` 순서로 뒤에 덧붙이고 `warning` 로그.
   (참조의 `complete_sequence_for_log`와 같은 역할.)
5. **다양성 진단 로깅** (`job_seq_source is not None` 이고 incumbent가 있을 때만):
   4개 모드 순서를 모두 계산해 선택된 순서와의 `normalized_mean_rank_distance`,
   `job_priority` 순서와의 거리, 직전 NEH 순서와의 거리를 한 줄로 로깅.
   ```
   neh_cp[<step_label>]: seq source=%s dist_to_midpoint=%.4f
   dist_to_first_stage=%.4f dist_to_bottleneck=%.4f dist_to_completion=%.4f
   dist_to_job_priority=%.4f dist_to_prev_neh=%s head=%s
   ```
   직전 순서는 컨트롤러 인스턴스 속성 `self._last_neh_job_sequence: list[str] | None`
   (`__init__`에서 `None` 초기화)에 보관하고 매 호출 끝에 갱신.
   4개 모드 전부 계산하는 비용은 O(n·c + n log n)로 CP 시간 대비 무시 가능하다.
6. `NehCpOption(..., custom_job_sequence=tuple(seq) if seq else None, ...)`
   — 나머지 필드는 기존과 동일.
7. dispatch → `elapsed` 측정 → `_register` (기존과 동일, 변경 없음).
8. `_step_log.yaml` 덤프에 사용된 sequence 기록 (§2.4).

**public 스텝 5개** (모두 동일한 파라미터 목록: 기존 `neh_cp`의 18개):

| 메서드 | `job_seq_source` |
|---|---|
| `neh_cp` (기존, 시그니처 불변) | `None` |
| `neh_cp_midpoint_seq` | `"midpoint"` |
| `neh_cp_first_stage_seq` | `"first_stage"` |
| `neh_cp_bottleneck_seq` | `"bottleneck"` |
| `neh_cp_completion_seq` | `"completion"` |

새 4개의 docstring에는 (i) 어떤 정렬 키인지, (ii) incumbent가 없으면
`job_priority`로 fallback한다는 점, (iii) `job_priority`가 fallback 겸 tie-break에
쓰인다는 점을 명시한다.

### 2.4 `_step_log.yaml`에 사용된 sequence 기록

현재 덤프는 리스트다 (`controller.py:2352`, `[entry.as_dict() for entry in step_log]`).
저장소 내에 이 파일을 읽는 코드는 없다(문서만 언급). 그러나 과거 산출물과의
일관성을 위해 **기존 `neh_cp`의 리스트 형태는 유지**하고, **새 4개 메서드만**
자기서술적 매핑 형태로 덤프한다 (파일명 자체가 스텝별로 달라
`try_get_file_path_for_subroutine`가 `<step_idx>-<method_name>` 접두를 붙이므로
충돌 없음):

```yaml
job_sequence_source: bottleneck        # fallback 시 "job_priority:due2-weight-pos"
job_sequence_fallback: false
job_sequence: [j12, j3, j7, ...]
steps:
  - step: 1
    ...
```

`NehCpStepEntry`(`algorithm/neh_cp/step_log.py`)는 **변경하지 않는다** — 순서는
런 단위 정보이지 배치 단위 정보가 아니다.

> 대안(채택하지 않음): 5개 메서드 모두 매핑 형태로 통일. 더 일관되지만 기존
> `neh_cp` 산출물 계약을 바꾸므로 진행 중/과거 런 분석과 어긋난다. 필요하면
> 후속 변경으로 분리한다.

### 2.5 CSR / `time_factor` 영향

순서 유도는 **순위만** 쓰므로 스케일 불변이다. CSR 내부 flow에서
`time_factor > 1`인 coarse 인스턴스로 돌아도 incumbent가 같은 coarse 프레임의
스케줄이므로 추가 처리가 필요 없다. 계획상 별도 작업 없음(테스트에서 한 번 확인만).

---

## 3. 작업 순서 (TDD)

각 단계는 "실패하는 테스트 → 최소 구현 → green" 순서로 진행한다.
매 단계 후 `uv run ruff check`, 마지막에 `uv run ruff format`.

### 단계 1 — `FFcSchedule.get_ji_2_start_time_map()`
- 테스트: `tests/solution/` 에 (job, stage) → 시작시각 매핑이
  `get_jik_2_start_time_map()`와 일치하는지.
- 구현: `solution/ffc_schedule.py:350` `get_ji_2_end_time_map` 옆에 대칭 추가.

### 단계 2 — `solution/schedule_sequence.py`
- 테스트: `tests/solution/test_schedule_sequence.py`
  - 손으로 만든 3~4 job / 2~3 stage 스케줄에서 4개 모드가 각각 예상 순서를 내는지
    (모드마다 서로 다른 순서가 나오도록 시각을 설계할 것).
  - `tiebreak_rank`가 동률을 실제로 갈라내는지 (동률 케이스 명시적 구성).
  - `tiebreak_rank=None`이면 `schedule.jobs` 순서로 갈리는지.
  - `bottleneck` 선정이 idle 합 최소 stage를 고르는지 (stage별 idle을 다르게 구성).
  - `normalized_mean_rank_distance`: 동일 순서 → 0.0, 완전 역순 → 양수,
    공통 job ≤ 1 → 0.0.
- 구현: §2.1대로.

### 단계 3 — 컨트롤러 코어 리팩터
- 테스트: 기존 `tests/orchestration/test_neh_cp_stopping.py`,
  `tests/orchestration/test_controller.py`가 그대로 통과해야 한다(행동 불변 확인).
- 구현: `neh_cp` 본문 → `_run_neh_cp(job_seq_source=None, step_label="neh_cp", ...)`,
  `neh_cp`는 위임자로 축소. **이 단계에서 새 모드는 추가하지 않는다.**

### 단계 4 — 새 스텝 메서드 4개 + fallback + 보정
- 테스트: `tests/orchestration/test_neh_cp_incumbent_sequence.py`
  - incumbent가 있을 때 각 메서드가 `NehCpOption.custom_job_sequence`에 기대 순서를
    실어 dispatcher를 호출하는지 (`NehCpDispatcher.run`을 monkeypatch해 spec 캡처;
    `test_neh_cp_stopping.py`의 monkeypatch 스타일 참고).
  - incumbent가 없을 때 `custom_job_sequence is None`이고 `warning`이 남는지
    (`caplog`).
  - 유도 결과에 job이 빠졌을 때 permutation으로 보정되는지 (스케줄에서 job 하나를
    뺀 케이스).
  - 4개 메서드가 routix flow validator를 통과하는지 — 각 메서드를 담은 최소
    `subroutine_flow`로 `SubroutineFlowValidator(...).validate(...)` 호출.
  - `time_factor > 1` coarse 인스턴스에서도 정상 동작(순위 불변).
- 구현: §2.3대로.

### 단계 5 — 다양성 진단 로깅 + `_step_log.yaml` 기록
- 테스트: `caplog`으로 진단 라인에 4개 거리 필드가 모두 있는지;
  덤프된 YAML이 `job_sequence_source` / `job_sequence` / `steps` 키를 갖는지
  (`tmp_path`를 working dir로).
- 구현: §2.3 5단계, §2.4.

### 단계 6 — 문서
- `docs/algorithms/neh_cp.md`: `## Signature` 절에 새 4개 메서드와 `job_seq_source`
  개념 추가, `## Pre-loop setup`에 순서 유도/폴백 경로 추가,
  `## Output conventions`에 새 `_step_log.yaml` 매핑 형태 추가.
- `README.md` 스텝 메서드 표(`README.md:27`)에 4행 추가.
- `CLAUDE.md`는 변경 불필요.

### 단계 7 — 실험 config (선택, 별도 커밋)
- `metadata/20260731/neh_cp_seq_source_compare.yaml`:
  `metadata/20260709/mcf_lb_fmm_neh_cp.yaml`을 베이스로 시나리오 5개
  (`neh_cp` 기존 + 새 4개), 나머지 파라미터는 전부 동일하게 두어 순서 효과만 분리.
- 주의: 8-worker wall-clock CP-SAT는 비결정적이다. 1440 그리드 기준 평균 obj
  차이가 ±350 미만이면 노이즈로 본다 (메모리: CSR batch CP noise floor).
  따라서 모드 간 비교는 RPDf 평균 + 인스턴스 파라미터(T/R/n/c)별 셀 비교로 볼 것.

---

## 4. 변경 파일 요약

| 파일 | 변경 |
|---|---|
| `src/ffc_ddw_sum_et/solution/ffc_schedule.py` | `get_ji_2_start_time_map()` 추가 |
| `src/ffc_ddw_sum_et/solution/schedule_sequence.py` | **신규** — `ScheduleSeqSource`, `schedule_job_sequence`, `normalized_mean_rank_distance` |
| `src/ffc_ddw_sum_et/solution/__init__.py` | 신규 심볼 re-export (기존 관례 확인 후) |
| `src/ffc_ddw_sum_et/orchestration/controller.py` | `_run_neh_cp` 코어 추출, `neh_cp` 위임화, 새 스텝 4개, `_last_neh_job_sequence` 속성 |
| `tests/solution/test_schedule_sequence.py` | **신규** |
| `tests/orchestration/test_neh_cp_incumbent_sequence.py` | **신규** |
| `docs/algorithms/neh_cp.md`, `README.md` | 문서 갱신 |
| `metadata/20260731/neh_cp_seq_source_compare.yaml` | **신규**(선택) |

**변경하지 않는 파일**: `algorithm/neh_cp/dispatcher.py`, `option.py`,
`sequence.py`, `step_log.py` — `custom_job_sequence` 경로가 이미 충분하다.

---

## 5. 커밋 계획 (Conventional Commits, 제목 ≤49자)

1. `feat(ffc-schedule): add get_ji_2_start_time_map`
2. `feat(schedule-sequence): derive job order from sch`
3. `refactor(controller): extract _run_neh_cp core`
4. `feat(controller): add incumbent-seq neh_cp steps`
5. `feat(controller): log neh_cp sequence diversity`
6. `docs(neh-cp): document incumbent sequence sources`

---

## 6. 열린 항목 / 후속

- `bottleneck` 모드의 병목 정의가 E/T 스케줄(의도적 idle 삽입)에서 타당한지는
  **실험으로 확인**할 사항이다. 결과가 나쁘면 "idle 합 최소" 대신 "machine 점유율
  최대" 같은 대안 정의를 `TODO.md`에 남기고 별도로 다룬다.
- `job_seq_by_last_retained_cp`에 대응하는 "mcf_lb 산출물 기반 consensus 순서"는
  잠재적 후속 아이디어다. 지금은 범위 밖 — 필요해지면 `TODO.md`에 기록 후 착수.
- 순서 저장/재사용(`save_..._as` / `job_seq_by_saved_sequence`)은 `neh_cp` 반복
  호출 flow가 생길 때 재검토.
