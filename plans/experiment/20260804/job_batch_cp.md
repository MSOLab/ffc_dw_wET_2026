# `job_batch_cp` — 순서 배치를 destroy-repair로 훑어 스케줄을 다시 만드는 스텝

작성일: 2026-08-04 / 대상 브랜치: `20260731_neh_cp`

이 문서는 **코드 변경 + 실험 실행 계획**이다. 별도 대화에서 이 문서만 읽고 구현·실행
할 수 있도록 현재 상태·설계 결정·작업 순서·테스트·분석 계획을 모두 담는다.

**선행 문서 (읽는 순서대로)**

- `docs/algorithms/neh_cp.md` — 배치 구성·TL 배분·순서 유도의 기존 어휘
- `plans/experiment/20260728/incremental_job_contrib_cp.md` — destroy-repair 스텝의
  기존 설계와 그 파일럿 후속
- `src/ffc_ddw_sum_et/orchestration/AGENTS.md` — 스텝 계약 (register 1회, elapsed 측정)
- `docs/algorithm-principles.md` — dispatcher 실행 계약

**형제 문서**: `plans/experiment/20260804/neh_cp_last1_stage_seq.md` — 순서 키
(`seq_end_stage`)를 추가한다. 이 문서의 `_seq` 스텝들이 그 파라미터를 그대로 받는다.
두 계획은 **독립적으로 착수 가능**하다 (이쪽이 먼저 끝나면 `seq_end_stage`만 나중에
붙이면 된다).

---

## 1. 아이디어

### 1.1 지금의 두 스텝은 서로 다른 두 축을 쓴다

| 스텝 | 시작점 | 매 CP 모델에 들어가는 job | 자유로운 job | 선택 규칙 |
|---|---|---|---|---|
| `neh_cp` | **빈 스케줄** | 지금까지 삽입된 job만 (하위 인스턴스) | 이번 배치 | 순서에서 앞에서부터 |
| `job_contrib_cp` | **완성된 incumbent** | 전체 n개 | 목적함수 기여 상위 `jd`개 | 기여도 |

`neh_cp`는 스케줄을 **처음부터 새로 짓는다**. 그래서 incumbent가 아무리 좋아도 그
구조를 버리고(순서만 물려받고) 다시 짓는다 — 실제로 1440 그리드에서 NEH가 seed를
못 이기는 인스턴스가 31.5–34.9 %다 (`plans/analysis/20260801/neh_cp_seq_source_full.md`).

`job_contrib_cp`는 incumbent를 **유지한 채** 나쁜 job 몇 개만 뽑아 다시 꽂는다.
그런데 선택이 기여도 기준이라 **같은 job이 반복해서 뽑히고**, 기여도 0인 job은
한 번도 움직이지 않는다 (`incremental_job_contrib_cp`가 "직전과 동일한 destroy set"을
건너뛰는 로직을 갖고 있는 것이 그 증거다, `controller.py:3915`).

### 1.2 새 스텝이 메우는 자리

**`job_batch_cp`**: incumbent를 유지한 채, **job 순서를 배치로 잘라 배치마다
그 배치의 job만 unfix(destroy)하고 나머지는 profile-fix한 뒤 CP-SAT가 다시 꽂게
하는 것을 전 배치에 걸쳐 반복**한다. 한 pass가 끝나면 **모든 job이 정확히 한 번씩**
재배치된 새 스케줄이 된다.

| | `neh_cp` | `job_contrib_cp` | **`job_batch_cp`** |
|---|---|---|---|
| 시작점 | 빈 스케줄 | incumbent | incumbent |
| CP 모델 크기 | 점증 (하위 인스턴스) | 전체 n | 전체 n |
| destroy 대상 | — | 기여 상위 | **순서상 배치** |
| 커버리지 | 전 job 1회 삽입 | 편중 (일부만 반복) | **전 job 정확히 1회** |
| 목적함수 | 하위 인스턴스 E/T | 전체 E/T | **전체 E/T** |

세 번째 열의 "전체 E/T"가 중요하다 — `neh_cp`의 초기 배치들은 **하위 인스턴스의**
E/T를 최소화하므로, 나중에 들어올 job을 모르는 상태에서 최적화한다. `job_batch_cp`는
매 배치에서 항상 전체 목적함수를 본다.

**순서(`job_seq_source`)의 역할이 `neh_cp`와 다르다.** 여기서 순서는 위치를 정하지
않고 **어떤 job들이 같은 배치에 묶이는가**만 정한다. `midpoint` 순서로 자르면 배치는
"incumbent에서 시간축상 서로 겹치는 job들"이 되어, 이 스텝은 사실상 **job 단위
sliding window**가 된다 (`sw_cp`는 op을 시간창으로 자르고, 이쪽은 job을 통째로
자른다). `job_priority` 순서로 자르면 배치는 시간축에 흩어진다. **이 대비를 재는
것이 실험의 2번 질문이다** (§6.1 arm 2).

### 1.3 단조성

각 배치의 CP 모델에는 destroy된 job까지 포함해 **incumbent 전체의 hint**가 들어간다
(`job_contrib_cp/dispatcher.py:156`–`:167`). 즉 incumbent 자신이 항상 실현 가능한
hint이므로 **CP 수준에서는 결코 나빠지지 않는다.** 유일한 퇴행 경로는 CP 해에
`make_semi_active` + `insert_idle_time`을 다시 적용하는 후처리(`:325`)이고, 그것은
§2.4의 수락 규칙이 막는다. 따라서 이 스텝은 **incumbent에 대해 단조 개선**이다.

---

## 2. 설계

### 2.1 재사용 경계 — `JobContribCpDispatcher`를 조합으로 재사용한다

한 배치의 처리(destroy → profile-fix → 전체 hint → CP → 후처리 → 목적함수 평가)는
`JobContribCpDispatcher.run`이 **이미 하는 일 그대로**다. 다른 것은 **destroy 집합을
누가 고르는가** 하나뿐이다. 따라서:

- `JobContribCpDispatcher`를 **수정하지 않고 감싸지도 않는다.** 새 dispatcher가
  배치마다 `JobContribCpOption`을 만들어 `JobContribCpDispatcher().run(...)`을
  호출한다 (조합 재사용).
- 필요한 유일한 변경은 **옵션에 "명시적 destroy 집합" 필드를 추가**하는 것이다.
- `controller.job_contrib_cp` 스텝은 **호출하지 않는다** (사용자 지시). 그 스텝은
  자기 `_register`를 갖고 있어 호출당 1회 register 계약을 깬다.

> 대안(채택하지 않음): `JobContribCpDispatcher.run`에서 destroy-repair 코어를 순수
> 함수로 추출해 둘이 공유. 더 깔끔하지만 실험이 물려 있는 200줄짜리 검증된 파일을
> 지금 가르는 것은 위험 대비 이득이 적다. 세 번째 선택 규칙이 생기면 그때 추출한다
> (`TODO.md`에 남긴다).

### 2.2 `algorithm/job_contrib_cp/option.py` — 명시적 destroy 집합

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class JobContribCpOption(AlgOption):
    jd_count_target: int | None = None          # was: int (required)
    destroy_job_ids: tuple[str, ...] | None = None   # NEW
    ...
```

- `__post_init__` 검증: **정확히 하나만** 설정되어야 한다 (XOR). 둘 다 None이거나
  둘 다 설정되면 `ValueError` — "무엇이 destroy 집합을 정하는가"가 옵션 하나만
  읽어도 명확해야 한다.
- `jd_count_target`이 설정되면 기존 규칙 그대로 (≥ 1).
- `destroy_job_ids`가 설정되면 비어 있지 않고 중복이 없어야 한다.
- `jd_count_target: int` → `int | None`은 **기존 호출부와 호환**된다 (kw_only,
  기존 호출은 모두 명시적으로 넘긴다).

`dispatcher.py`의 변경은 선택 분기 하나뿐:

```python
if option.destroy_job_ids is not None:
    selected = [j for j in option.destroy_job_ids if j in instance_job_set]
    # 인스턴스에 없는 job이 섞이면 ValueError (호출자 버그)
else:
    selected = select_jd_jobs(...)
```

- 명시 경로에서는 `jd_count_eff == 0` 조기 반환(`:76`)에 도달할 수 없다(검증이 막음).
- metrics: `jd_count_target`에 `len(selected)`를 싣고, **`"destroy_selection":
  "explicit" | "contribution"`** 키를 추가한다 — 나중에 `_metrics.yaml`만 보고도
  어느 선택 규칙이 돌았는지 알 수 있게.
- metrics에 **`"setup_seconds"`** (solver.solve 직전까지의 경과)를 추가한다.
  §5의 파일럿이 재야 하는 값이 정확히 이것이다 (배치마다 전체 인스턴스 모델을 다시
  짓는 비용). 기존 스텝에도 무해한 관측치다.

### 2.3 신규 패키지 `algorithm/job_batch_cp/`

```
src/ffc_ddw_sum_et/algorithm/job_batch_cp/
  __init__.py     # JobBatchCpDispatcher, JobBatchCpOption 재수출 (job_contrib_cp와 동일 관례)
  option.py       # JobBatchCpOption
  dispatcher.py   # JobBatchCpDispatcher
  step_log.py     # JobBatchCpStepEntry
```

**`JobBatchCpOption`**

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class JobBatchCpOption(AlgOption):
    job_sequence: tuple[str, ...]            # 컨트롤러가 유도한 완전 permutation
    batch_size: int = 1                      # 배치당 destroy job 수 (사전 해석 완료)
    num_batches: int | None = None           # 설정되면 batch_size = ceil(n / num_batches)
    pf_method: PFMethod = "PF1"
    cp_tl_seconds: float | None = None
    total_timelimit_seconds: float | None = None
    batch_tl_mode: BatchTlMode = "constant"
    batch_tl_offset_seconds: float = 0.01
    horizon_multiplier: float = 1.25
    wall_clock_deadline_sec: float | None = None
    solver_thread_cnt: int = 1
    time_factor: int = 1
    error_if_infeasible: bool = False
```

검증: `job_sequence` 비어 있지 않고 중복 없음, `batch_size >= 1`,
`num_batches >= 1`, `pf_method is not None`(이웃 정의의 정체성),
`time_factor >= 1`, `horizon_multiplier > 0`.

**`JobBatchCpDispatcher.run(spec)`**

```
algorithm_id = "job_batch_cp"

1. instance / option 검증. spec.ref_solution 없으면 RuntimeError
   ("job_batch_cp requires an incumbent schedule; chain it after a seeding
     subroutine such as calc_mcf_lb_and_derive_full_sch.")
2. job_sequence가 instance.job_id_list의 permutation인지 검증
   (NehCpDispatcher._validate_custom_sequence:658과 동일 계약·동일 메시지 형식).
3. 배치 분할: num_batches가 있으면 size = ceil(n / num_batches), 없으면 batch_size.
   균등 분할 — neh_cp의 first_batch_size = max(size, 2·max_m) 규칙은 **쓰지 않는다**
   (그 규칙은 "첫 배치가 기계를 채울 만큼은 커야 한다"는 빈 스케줄 전용 사정이고,
   여기서는 스케줄이 항상 완전하다).
4. per_batch_tl = resolve_per_step_tl(...)  # neh_cp와 동일 함수·동일 의미
5. current = spec.ref_solution; current_obj = compute_weighted_earliness_tardiness(...)
6. for step, batch in enumerate(batches):
     - stop_predicate() 또는 wall_clock_deadline 초과 → stopped_early, break
     - sub_option = JobContribCpOption(destroy_job_ids=tuple(batch),
           cp_tl_mode="constant", cp_tl_seconds=per_batch_tl[step],
           wall_clock_deadline_sec=..., pf_method=..., horizon_multiplier=...,
           solver_thread_cnt=..., time_factor=..., error_if_infeasible=...,
           log_search_progress=False)        # §2.7
     - rec = JobContribCpDispatcher().run(AlgSpec(instance=instance,
               option=sub_option, ref_solution=current, logger=logger,
               stop_predicate=spec.stop_predicate))
     - rec.progress_log의 각 항목을 (배치 시작 − 루프 시작)만큼 오프셋해 누적
     - 수락 규칙(§2.4) 적용 → current / current_obj 갱신
     - JobBatchCpStepEntry 추가
7. AlgRecord(result=AlgResult(schedule=current, obj_value=current_obj,
       obj_bound=None, metrics={..., "step_log": tuple(step_entries)}),
       progress_log=..., termination_reason=COMPLETED | STOP_REQUESTED)
```

**`JobBatchCpStepEntry`** (`NehCpStepEntry`와 같은 스타일, `as_dict()` 제공):

```python
step: int
batch_size: int
batch_head: str            # 배치 첫 job (배치가 순서 어디쯤인지 보게)
elapsed_time: float        # 루프 시작 기준 누적
TL: float | None
elapsed_portion: float | None
obj_before: float
obj_after: float           # 이 배치의 CP+후처리 결과 (수락 여부와 무관)
accepted: bool
cpsat_status: str | None
setup_seconds: float | None
makespan: int
```

### 2.4 수락 규칙

`obj_after < obj_before`일 때만 `current`를 교체한다 (**엄격 부등호**). 동률에서
교체하지 않는 이유: `insert_idle_time` 후처리는 목적함수가 같아도 다른 스케줄을
낼 수 있어, 동률 교체는 다음 배치의 profile-fix 기준면을 이유 없이 흔든다.
`accepted=False`가 step log에 남으므로 후처리 퇴행 빈도를 사후에 셀 수 있다.

### 2.5 컨트롤러 (`orchestration/controller.py`)

**public 스텝 4개** — `neh_cp` 계열과 같은 표면 (사용자 확정, 2026-08-04):

| 메서드 | `job_seq_source` | 추가 파라미터 |
|---|---|---|
| `job_batch_cp` | `None` (= `job_priority` 규칙) | — |
| `job_batch_cp_midpoint_seq` | `"midpoint"` | `seq_tiebreak`, `seq_end_stage` |
| `job_batch_cp_first_stage_seq` | `"first_stage"` | — |
| `job_batch_cp_completion_seq` | `"completion"` | `seq_end_stage` |

**`bottleneck` 변형은 만들지 않는다.** `TODO.md` 항목 7이 그 모드가
`first_stage`의 별칭임을 증명했고(`_find_bottleneck_stage`는 항상 첫 stage를 고른다),
삭제가 기본값으로 적혀 있다. 죽은 모드의 별칭을 새로 늘리지 않는다.
(`seq_tiebreak` / `seq_end_stage`의 노출 범위는 형제 문서 §3.2의 표와 동일한
축퇴 논거를 따른다.)

네 메서드는 전부 **얇은 위임자**이고 본문은 공통 private 코어 `_run_job_batch_cp`
하나다 (`_run_neh_cp`와 같은 구조 — routix `subroutine_flow_validator.py:98`이 config
kwargs를 메서드의 **명시 시그니처**로 검증하므로 `**kwargs` wrapper는 불가).

**공통 시그니처**:

```python
def job_batch_cp(
    self,
    job_priority: NehCpJobPriority = "weight-due-pos",
    batch_size: int | str = 1,          # resolve_jd_count_target 문법 ("1" / "0.05n")
    num_batches: int | None = None,
    pf_method: PFMethod = "PF1",
    cp_tl: float | str | None = None,
    total_timelimit: float | str | None = None,
    batch_tl_mode: BatchTlMode = "constant",
    batch_tl_offset_seconds: float = 0.01,
    solver_thread_cnt: int = 1,
    horizon_multiplier: float = 1.25,
    error_if_infeasible: bool = False,
) -> SubroutineReport: ...
```

`batch_size`가 `resolve_jd_count_target`(`orchestration/value_resolver.py`) 문법을
쓰는 것은 형제 스텝 `job_contrib_cp`의 `jd_target`과 어휘를 맞추기 위해서다
(`"0.05n"` 같은 인스턴스 상대 크기를 공짜로 얻는다).

**`_run_job_batch_cp` 본문** (스텝 계약 준수 — `orchestration/AGENTS.md`):

1. `start_elapsed = time.monotonic()`; `is_stopping_condition()` 프리플라이트 →
   `_make_stop_report`.
2. incumbent 확보. 없으면 **`RuntimeError`** (`job_contrib_cp:3616`과 같은 규약).
   `neh_cp_*_seq`의 "fallback + warning"과 다른 이유: `neh_cp`는 incumbent 없이도
   할 일(빈 스케줄에서 짓기)이 있지만, 이 스텝은 incumbent가 없으면 **할 일 자체가
   없다.**
3. 표현식 해석 (`cp_tl` / `total_timelimit` / `batch_size`).
4. **순서 유도** — §2.6의 공유 헬퍼. `elapsed` 측정 이전에 전부 끝낸다.
5. `JobBatchCpOption` 구성 → `AlgSpec(ref_solution=incumbent.schedule, ...)` →
   `JobBatchCpDispatcher().run(spec)`.
6. `elapsed` 측정 → `SubroutineReport` → **`_register` 정확히 1회**
   (`progress_log=record.progress_log`).
7. `_register` **이후에** `_step_log.yaml` 덤프 (§2.8) — 로그 IO가 측정 구간에
   들어가지 않도록 (`job_contrib_cp:3689`가 같은 주석을 달고 있다).

### 2.6 순서 유도 코드는 `_run_neh_cp`와 **공유한다**

`_run_neh_cp`(`controller.py:2549`–`:2640`)의 다음 블록은 두 스텝이 글자 그대로 같은
일을 한다: `priority_sequence` 계산 → `rank_map` → incumbent 유도 →
permutation 보정 → 진단 로깅 → step-log용 라벨/폴백 플래그.

**약 80줄을 private 헬퍼로 추출한다** (단일 소스 원칙):

```python
@dataclass(frozen=True, slots=True)
class ResolvedJobSequence:
    sequence: tuple[str, ...] | None   # None = job_priority로 진행하라
    priority_sequence: tuple[str, ...]
    source_label: str                  # "midpoint" | "job_priority:due2-weight-pos"
    is_fallback: bool
    end_stage: int | None
    tiebreak: str | None

def _resolve_job_sequence(
    self, *, step_label: str, job_seq_source: ScheduleSeqSource | None,
    job_priority: NehCpJobPriority, seq_tiebreak: ScheduleSeqSource | None,
    seq_end_stage: int, require_incumbent: bool,
) -> ResolvedJobSequence: ...
```

- `require_incumbent=True`(job_batch_cp)면 incumbent 부재 시 `RuntimeError`,
  `False`(neh_cp)면 warning + fallback — 두 스텝의 유일한 정책 차이다.
- **`_run_neh_cp`의 동작은 한 비트도 바뀌면 안 된다.** 기존 테스트
  (`tests/orchestration/test_neh_cp_incumbent_sequence.py`,
  `test_neh_cp_stopping.py`)가 그대로 통과하는 것이 이 리팩터의 합격 기준이다.
- **진단 로그 라인 형식을 바꾸지 않는다** —
  `scripts/20260801/analyze_neh_pass_chain.py:94`의 `DIAG_RE`가
  `dist_to_job_priority=` 와 `dist_to_prev_neh=` 가 **붙어 있고 그 순서**임을
  가정한다. 새 스텝도 **같은 형식**으로 찍되 스텝 이름이 `job_batch_cp_*`라
  정규식(`neh_cp_\w+?_seq`)에 걸리지 않는다 — 의도된 안전 장치다.
- `_last_neh_job_sequence`(`controller_core.py:157`)는 **이름을 바꾸지 않는다**
  (같은 정규식이 `dist_to_prev_neh` 필드를 읽는다). 두 스텝이 이 속성을 공유하며,
  "직전에 유도된 순서"라는 뜻으로 계속 쓴다.

### 2.7 알려진 한계 (구현 시 주석·docstring에 남길 것)

1. **배치마다 전체 인스턴스 CP 모델을 다시 짓는다.** `neh_cp`의 초기 배치들이 작은
   하위 인스턴스를 푸는 것과 달리, 여기서는 매번 n·c개 op의 모델이다. n=200, c=10,
   `batch_size=15`면 14번의 2000-op 모델 구축이다. **§5의 파일럿이 재야 하는 1순위
   수치**이고, `setup_seconds` metric(§2.2)이 그 계측이다.
2. **`stop_predicate` 입도는 배치 단위다.** `JobContribCpDispatcher`는
   `spec.stop_predicate`를 읽지 않는다(현재 구현). 다만 `wall_clock_deadline_sec`를
   매 배치에 넘기므로 개별 solve는 데드라인 안에서 끝난다. 즉 최악의 초과는
   후처리(`make_semi_active` + `insert_idle_time`) 시간뿐이다.
3. **CP-SAT search log는 배치마다 같은 파일명을 쓴다** (`_job_contrib_cp_search.log`).
   덮어써서 마지막 배치만 남으므로 `log_search_progress=False`로 고정하고 노출하지
   않는다. 필요해지면 파일명에 배치 인덱스를 넣는 변경이 선행돼야 한다.
4. **1 pass만 돈다.** 예산이 남아도 순서를 다시 유도해 두 번째 pass를 돌지 않는다.
   반복은 flow에 스텝을 두 번 쓰면 되고(그러면 두 번째 pass는 갱신된 incumbent에서
   순서를 다시 유도한다), 그 편이 예산 배분이 config에 드러나 낫다.
   `neh_cp` 체인 실험(`plans/analysis/20260801/neh_cp_pass_chain.md`)이 같은 방식이다.
5. **`apply_cumulative_tl`을 노출하지 않는다.** 비교 대상인 NEH arm들이 전부
   `false`로 돌았다. 필요해지면 `neh_cp`와 같은 방식으로 추가한다.

### 2.8 산출물 `_step_log.yaml`

`neh_cp_*_seq`의 매핑 형태를 그대로 따른다 (파일명은
`try_get_file_path_for_subroutine`가 `<step_idx>-<method_name>` 접두를 붙이므로
충돌 없음):

```yaml
job_sequence_source: midpoint          # fallback 없음 (incumbent 필수)
job_sequence_tiebreak: completion
job_sequence_end_stage: -2
job_sequence: [j12, j3, ...]
batch_size: 15
batch_count: 14
steps:
  - step: 0
    batch_head: j12
    obj_before: 52346.0
    obj_after: 51902.0
    accepted: true
    ...
```

---

## 3. 작업 순서 (TDD)

각 단계는 "실패하는 테스트 → 최소 구현 → green". 매 단계 후 `uv run ruff check`,
마지막에 `uv run ruff format`.

### 단계 1 — `JobContribCpOption`의 명시적 destroy 집합
- 테스트 (`tests/algorithm/job_contrib_cp/test_option.py`): XOR 검증 4케이스
  (둘 다 None / 둘 다 설정 / 각각 단독), 빈 튜플, 중복 job.
- 테스트 (`tests/algorithm/job_contrib_cp/test_dispatcher.py`): `destroy_job_ids`를
  준 run이 **그 job들만** destroy했는지 (`metrics["selected_jobs"]`), 기여도가 0인
  job을 지정해도 destroy되는지(= `select_jd_jobs`를 타지 않았다는 증거),
  인스턴스에 없는 job → `ValueError`, `destroy_selection` / `setup_seconds` metric.
- 구현: §2.2.

### 단계 2 — `JobBatchCpOption` + `JobBatchCpDispatcher`
- 테스트 (`tests/algorithm/job_batch_cp/test_option.py`): 검증 규칙.
- 테스트 (`tests/algorithm/job_batch_cp/test_dispatcher.py`):
  1. `ref_solution` 없음 → `RuntimeError`.
  2. `job_sequence`가 permutation이 아님 → `ValueError`.
  3. **배치 분할**: n=10, `batch_size=3` → `[3,3,3,1]`; `num_batches=2` → `[5,5]`.
  4. **커버리지 불변식**: 모든 배치의 합집합 = 전체 job, 교집합 = ∅.
     `JobContribCpDispatcher.run`을 monkeypatch해 호출별 `destroy_job_ids`를
     캡처하는 방식(무거운 CP를 돌리지 않는다) — `test_neh_cp_stopping.py`의
     monkeypatch 스타일 참고.
  5. **수락 규칙**: 배치가 더 나쁜 결과를 내면 `current`가 유지되고
     `accepted=False`가 기록된다 (monkeypatch로 악화 결과를 주입).
  6. **progress_log 오프셋**: 두 배치의 항목이 루프 시작 기준으로 단조 증가한다.
  7. `wall_clock_deadline_sec` 초과 시 남은 배치를 건너뛰고
     `termination_reason=STOP_REQUESTED`, 그때까지의 `current`를 반환한다.
  8. **작은 실 CP 통합 테스트 1개** (3 job, 2 stage, 짧은 TL): 실제로 스케줄이
     나오고 목적함수가 incumbent 이하다.
- 구현: §2.3.

### 단계 3 — 순서 유도 헬퍼 추출 (행동 불변)
- 테스트: 기존 `tests/orchestration/test_neh_cp_incumbent_sequence.py` /
  `test_neh_cp_stopping.py` / `test_controller.py`가 **수정 없이** 통과.
  + 진단 라인이 `DIAG_RE`(§2.6)에 여전히 매치되는지 확인하는 테스트 1개.
- 구현: §2.6. **이 단계에서 새 스텝은 추가하지 않는다.**

### 단계 4 — 컨트롤러 스텝 4개
- 테스트 (`tests/orchestration/test_job_batch_cp_step.py`):
  1. 네 메서드가 기대 순서를 `JobBatchCpOption.job_sequence`에 실어 dispatcher를
     호출한다 (`JobBatchCpDispatcher.run` monkeypatch). **fixture는 3 stage 이상**
     — 2 stage에서는 모드들이 필연적으로 겹쳐 배선을 구분하지 못한다.
  2. incumbent 부재 → `RuntimeError` (fallback 아님).
  3. `_register`가 정확히 1회, `elapsed_time`이 dispatcher 소요를 포함한다.
  4. 네 메서드를 담은 최소 `subroutine_flow`가 routix `SubroutineFlowValidator`를
     통과한다.
  5. `batch_size="0.05n"` 표현식이 해석된다.
  6. `_step_log.yaml`이 §2.8의 키를 갖는다 (`tmp_path`를 working dir로).
- 구현: §2.5.

### 단계 5 — 문서
- `docs/algorithms/job_batch_cp.md` (신규): §1의 세 스텝 비교표, 순서의 역할이
  `neh_cp`와 다르다는 점, 파라미터 표, 산출물, §2.7의 한계.
- `README.md` 스텝 표(`README.md:27`)에 4행 추가.
- `docs/algorithms/pw_cp.md` / `job_contrib_cp` 관련 문서에서 이웃 스텝으로 상호 참조.
- `TODO.md`: destroy-repair 코어 추출(§2.1 대안)을 **Why / When to act**와 함께 기록.

---

## 4. 변경 파일 요약

| 파일 | 변경 |
|---|---|
| `algorithm/job_contrib_cp/option.py` | `destroy_job_ids` 추가, `jd_count_target`을 XOR로 |
| `algorithm/job_contrib_cp/dispatcher.py` | 선택 분기 + `destroy_selection` / `setup_seconds` metric |
| `algorithm/job_batch_cp/{__init__,option,dispatcher,step_log}.py` | **신규** |
| `orchestration/controller.py` | `_resolve_job_sequence` 추출, `_run_job_batch_cp` + 스텝 4개 |
| `tests/algorithm/job_contrib_cp/test_{option,dispatcher}.py` | 케이스 추가 |
| `tests/algorithm/job_batch_cp/test_{option,dispatcher}.py` | **신규** |
| `tests/orchestration/test_job_batch_cp_step.py` | **신규** |
| `docs/algorithms/job_batch_cp.md`, `README.md`, `TODO.md` | 문서 |
| `metadata/20260804/job_batch_cp_pilot.yaml` | **신규** (§5) |
| `metadata/20260804/job_batch_cp_compare.yaml` | **신규** (§6) |

**변경하지 않는 파일**: `algorithm/neh_cp/*` — 이 스텝은 NEH dispatcher를 전혀 쓰지
않는다. `controller.job_contrib_cp` / `incremental_job_contrib_cp` — 호출하지 않는다.

---

## 5. 파일럿 (필수, 1440 이전)

**질문 3개**: (a) 배치당 모델 재구축 비용이 예산을 얼마나 먹는가, (b) `batch_size`는
얼마가 좋은가, (c) 같은 예산에서 `neh_cp`와 견줄 만한가.

`metadata/20260804/job_batch_cp_pilot.yaml`:

- `ins_filter: {T: 0.6, R: 0.2}` → 160 인스턴스 (T=0.6은 척도가 포화되지 않고 cap이
  구속되는 슬라이스, `neh_cp_midpoint_tiebreak.md` §7.3).
- flow는 비교군과 동일: `initialize_by_dispatch_v4 → calc_mcf_lb_and_derive_full_sch
  → run_flip_makespan_cp_from_incumbent → <스텝>`.
- 예산도 동일: 시나리오 cap `0.09nc`, FMM `0.0036nc`, 스텝 `total_timelimit: 0.0108nc`,
  `batch_tl_mode: linear`.
- arm 4개: `job_batch_cp_midpoint_seq`의 `batch_size ∈ {1, 5, 15}` + 앵커
  `neh_cp_midpoint_seq`(`added_batch_size: 15`).
- 예상 소요: 4 × 160 × 15.2 s / 12 ≈ **13분**.

**판정 기준**:

1. `setup_seconds` 합 / 스텝 총 소요 > 0.35이면 `batch_size=1`은 버린다 (예산의
   1/3 이상을 모델 구축에 쓰는 설정은 CP 시간이 부족해 비교가 무의미하다).
2. 배치를 다 돌지 못하고 데드라인에 걸린 인스턴스 비율을 arm별로 본다. 50 %를
   넘으면 그 `batch_size`는 예산 대비 너무 잘다.
3. `accepted=False` 비율 — §1.3의 단조성 주장이 실제로 성립하는지 (후처리 퇴행이
   드문 사건인지) 확인.
4. NEH 앵커 대비 NEH-스텝-수준 RPDf(§7.1의 측정면).

파일럿 결과는 `plans/analysis/20260804/job_batch_cp_pilot.md`에 남기고 **거기서
1440 run의 `batch_size`를 확정**한다.

---

## 6. 본 실험 (1440)

### 6.1 arm 구성 (4개)

`metadata/20260804/job_batch_cp_compare.yaml`. flow·예산은
`metadata/20260803/neh_cp_midpoint_tiebreak.yaml`에서 그대로 복사한다.

| # | 시나리오 이름 | 스텝 | 답하는 질문 |
|---|---|---|---|
| 1 | `dv4_mcf_fmm_job_batch_cp_midpoint_seq` | `job_batch_cp_midpoint_seq` | **처치군** — 재건 대신 sweep-repair가 나은가 |
| 2 | `dv4_mcf_fmm_job_batch_cp_job_priority` | `job_batch_cp` (인스턴스 규칙) | **배치의 시간 국소성이 중요한가** (§1.2) |
| 3 | `dv4_mcf_fmm_job_batch_cp_completion_seq` | `job_batch_cp_completion_seq` | 순서 모드 순위가 `neh_cp`와 같은가 |
| 4 | `dv4_mcf_fmm_neh_cp_midpoint_seq` | `neh_cp_midpoint_seq` | **동일-run 앵커** (head-to-head) |

arm 4를 같은 run 안에 두는 것이 핵심이다 — 교차 run 노이즈가 ±0.45 %p다
(`plans/analysis/20260801/neh_cp_seq_replicate.md`). arm 1 − arm 4가 이 실험의 주
대비이고, arm 1 − arm 2가 부 대비다.

**예상 소요**: 4 × 1440 × 15.2 s / 12 ≈ **122분** + 리포트. 96코어 단독 점유 전제.

### 6.2 실행

```bash
uv run python main.py --config metadata/20260804/job_batch_cp_pilot.yaml     # 먼저
uv run python main.py --config metadata/20260804/job_batch_cp_compare.yaml   # 파일럿 판정 후
```

> **`--config`를 반드시 명시할 것** (`main.py:31`의 하드코딩된 기본값을 잊어 다른
> config가 돌아간 사고 전례가 있다).

실행 후 `main.log` 확인: permutation 보정 warning 0건, `RuntimeError` 0건,
arm별 스텝 소요 균질성.

### 6.3 provenance 커밋

```
20260804_job_batch_cp_compare/<timestamp> run setting
computer: calop4

- question: at equal budget, is sweeping a destroy-repair over ordered job batches better than rebuilding the schedule with neh_cp
- 4 scenarios x 1440 PRA2017 large instances; flow is dispatch_v4 -> mcf_lb -> fmm -> <step>
- batch_size fixed from the pilot (plans/analysis/20260804/job_batch_cp_pilot.md)
- plan: plans/experiment/20260804/job_batch_cp.md
```

---

## 7. 분석 계획

`plans/analysis/20260804/job_batch_cp_compare.md`가 SSOT,
`analysis/20260804_job_batch_cp/`에 CSV (gitignored).

### 7.1 측정면

**flow `bestObj`로 결론을 내지 않는다** (`plans/analysis/20260801/neh_cp_seq_source_full.md`
결과 0 — flow 값은 `min(seed, 스텝)`이라 스텝이 seed를 못 이긴 인스턴스에서 모든
arm이 같은 숫자를 낸다). `report.obj_log_loader.build_step_registrations`로 스텝
경계를 잘라 `seed_obj` / `step_obj`(자체 출력) / `step_best` / `flow_best`를 낸다.
파싱 전 **`docs/artifacts/obj_log.md`를 읽을 것**. RPDf는 `ffc_ddw_sum_et._calc.rpd_f`를
import 해서 쓴다.

**이 스텝에서는 `step_obj`가 특히 중요하다.** §1.3의 단조성 때문에 `step_obj`는
정의상 `seed_obj` 이하이고, 따라서 `job_batch_cp` arm은 **seed를 절대 밑돌지 않는다**.
`neh_cp` arm은 밑돌 수 있다(1/3). 즉 두 arm의 "seed 대비 개선량" 분포가 근본적으로
다르므로, 평균만이 아니라 **개선한 인스턴스 비율과 개선폭 분포를 함께** 봐야 한다.

### 7.2 대비

| 대비 | 격리하는 것 |
|---|---|
| arm1 − arm4 (paired, 1440) | **재건 대 sweep-repair** (동일 순서·동일 예산) |
| arm1 − arm2 (paired) | **배치의 시간 국소성** |
| arm1 − arm3 (paired) | 순서 모드 (`neh_cp`에서의 순위와 비교) |
| seed 대비 개선율 (arm별) | §7.1의 분포 차이 |

### 7.3 T별·(n,c)별 분해 (필수)

`plans/analysis/20260802/neh_cp_budget_allocation.md` **조치 4**. 특히 이 실험은
**n이 클수록 배치 수가 늘어 모델 재구축 비용이 커지므로** (n,c) 셀 분해에 사전
예측이 있다: n=200 셀에서 `job_batch_cp`가 상대적으로 불리해야 한다. 그 예측이
빗나가면 §2.7-1의 비용 모형을 다시 본다.
인스턴스 파라미터 해석은 `pra2017-instance-params` 스킬을 먼저 읽는다.

### 7.4 step log 기반 진단

- 배치별 `accepted` 비율과 개선폭의 배치 인덱스 의존성 — 초반 배치가 대부분의
  개선을 내는가(그렇다면 pass를 끝까지 도는 것이 낭비다), 고르게 나는가.
- `setup_seconds` 합계 대 CP 시간 — §5 판정 기준 1의 1440 규모 재확인.
- 데드라인에 걸려 못 돈 배치 수.

---

## 8. 커밋 계획 (Conventional Commits, 제목 ≤49자)

계획서 자신이 첫 커밋이다.

1. `docs(plan): add job_batch_cp plan`
2. `feat(job-contrib-cp): accept an explicit destroy set`
3. `feat(job-batch-cp): add the batch sweep dispatcher`
4. `refactor(controller): share job sequence resolution`
5. `feat(controller): add job_batch_cp steps`
6. `docs(job-batch-cp): document the batch sweep step`
7. `feat(job-batch-cp): add pilot config`
8. (파일럿 후) `20260804_job_batch_cp_pilot/<ts> run setting`
9. (판정 후) `feat(job-batch-cp): add 1440 compare config`
10. (본 런 후) `20260804_job_batch_cp_compare/<ts> run setting`
11. (분석 후) `analysis/20260804_job_batch_cp merged analysis`

각 커밋 시점에 테스트가 green이므로 bisect가 가능하다. 4번(리팩터)은 3번과 5번
사이에 두어, 행동 불변 리팩터가 새 기능과 같은 커밋에 섞이지 않게 한다.

---

## 9. 범위 밖 / 후속

- **destroy-repair 코어 추출** (§2.1 대안): 세 번째 선택 규칙이 생기면 착수.
  `TODO.md`에 Why / When to act와 함께 남긴다.
- **여러 pass**: §2.7-4대로 flow에 스텝을 두 번 쓰는 것으로 대신한다. pass별 순서
  재유도 효과는 `neh_cp` 체인 실험과 같은 방식으로 따로 잰다.
- **`job_contrib_cp`와의 교대(alternating)**: 순서 sweep으로 전 job을 한 번씩 훑은
  뒤 기여도 기반으로 나쁜 job을 집중 공략하는 조합은 자연스러운 후속이지만,
  단독 성능이 먼저다.
- **destroy 집합을 시간창으로**: `midpoint` 순서 배치는 시간 국소성의 *근사*다.
  진짜 시간창(윈도 안에 완전히 들어오는 job 전부)은 `sw_cp`의 어휘이므로, arm 2의
  결과가 "국소성이 중요하다"로 나오면 그쪽과의 합류를 검토한다.
- **`bottleneck` 모드 삭제**: 형제 문서 §8의 열린 결정. 이 계획은 새 별칭을 만들지
  않는 것으로 선반영했다.

---

## 10. 구현 결과 (2026-08-05)

§3 단계 1–4 구현 완료. 단계 5(문서)와 §5 파일럿 config는 **미착수** — §10.5 참조.

검증: `uv run ruff check` clean, `uv run pytest -q` → **981 passed**
(구현 전 912 → 신규 69).

### 10.1 계획대로 들어간 것

| 계획 | 구현 |
|---|---|
| §2.2 명시적 destroy 집합 | `JobContribCpOption.destroy_job_ids`, `jd_count_target`과 XOR 검증 |
| §2.2 metric | `destroy_selection: "explicit" \| "contribution"`, `setup_seconds` (solve 직전 경과) |
| §2.3 신규 패키지 | `algorithm/job_batch_cp/{__init__,option,dispatcher,step_log}.py` |
| §2.3 재사용 | `JobBatchCpDispatcher`가 배치마다 `JobContribCpOption`을 만들어 `JobContribCpDispatcher().run()` 호출 (조합, 하위 dispatcher 무수정) |
| §2.4 수락 규칙 | 엄격 부등호 (`obj_after < obj_before - 1e-6`), 거부 시 `accepted=False`만 기록 |
| §2.5 스텝 4개 | `job_batch_cp` / `_midpoint_seq` / `_first_stage_seq` / `_completion_seq`, 공통 코어 `_run_job_batch_cp`, `bottleneck` 없음 |
| §2.5 파라미터 노출 | `seq_tiebreak`는 midpoint만, `seq_end_stage`는 midpoint·completion만, `apply_cumulative_tl` 미노출 |
| §2.5 incumbent 부재 | `RuntimeError` (neh_cp의 fallback과 다른 정책) |
| §2.6 순서 유도 공유 | `_resolve_job_sequence` + `_ResolvedJobSequence`로 추출, `require_incumbent` 플래그 하나가 두 스텝의 유일한 정책 차이 |
| §2.7-3 | `log_search_progress=False` 고정, 미노출 |
| §2.8 산출물 | `_step_log.yaml` 매핑 (`job_sequence_*` + `batch_size` / `batch_count` / `steps`) |
| 스텝 계약 | `_register` 정확히 1회, `elapsed` 측정 직후 register, step-log 덤프는 register **이후** |

### 10.2 계획과 다르게 결정한 것

1. **`num_batches`의 이중 역할.** dispatcher에서 `batch_size = ceil(n / num_batches)`로
   쓰이는 동시에, `resolve_per_step_tl`의 TL 분모로도 넘어간다. 두 값은 대체로
   일치하지만 `n=10, num_batches=3`처럼 어긋날 수 있다(size=4 → 실제 3배치, 분모는 3).
   현재 실험 arm은 `batch_size`만 쓰므로 그대로 두었다.
2. **`jd_count_eff == 0` 조기 반환에는 `setup_seconds`를 싣지 않았다.** 그 경로엔
   solve가 없어 "모델 구축 비용"이라는 의미가 성립하지 않는다. §7.4의 합산은
   `.get()`으로 읽으면 된다.
3. **`_metrics.yaml`은 존재하지 않는다** (§2.2의 서술은 부정확). 컨트롤러는
   `AlgResult.metrics`를 yaml로 덤프하지 않는다. `destroy_selection`은 `main.log`의
   `job_contrib_cp: destroy_selection=...` 라인과 `job_batch_cp`의 step log로 읽는다.

### 10.3 리뷰에서 고친 것 (초안 → 현재)

초안 구현에는 다음이 있었고, 전부 회귀 테스트와 함께 고쳤다.

1. **`_run_neh_cp` 행동 변경 — §2.6 위반.** step-log 덤프 분기가
   `if job_seq_source is None:` → `if resolved.sequence is None:`로 바뀌어 있었다.
   `resolved.sequence`는 **fallback에서도 `None`**이라, `neh_cp_*_seq`를 incumbent
   없이 돌리면 매핑 대신 평평한 리스트가 나가 `job_sequence_fallback` 키가 통째로
   사라졌다. 요청(`job_seq_source`) 기준 분기로 되돌렸다.
   회귀 테스트: `test_step_log_yaml_keeps_mapping_format_on_fallback`,
   `test_plain_neh_cp_step_log_stays_a_list`.
2. **progress_log 오프셋이 배치 *끝* 기준이었다.** `elapsed_since_loop`를 하위
   dispatcher 반환 **후에** 재서 그 값으로 하위 진행점을 밀었다. §2.3-6의
   "(배치 시작 − 루프 시작)"이 아니고 `neh_cp`
   (`neh_cp/dispatcher.py:289`, `value_recorder.time_started - start_elapsed`)와도
   달라, 진행점이 run 자신의 종료 항목보다 뒤로 밀려 **로그가 비단조**가 됐다.
   §7.1의 `build_step_registrations` 분석면이 이 로그를 직접 파싱하므로 실험 결론에
   영향이 갔을 값이다. `batch_start_offset`으로 교체.
   회귀 테스트: `test_entries_are_monotonic_in_the_loop_frame` (되돌리면 실패 확인함).
3. **`except RuntimeError: continue`가 배치 예외를 삼켰다.** MODEL_INVALID와
   `error_if_infeasible=True`가 던지는 버그 신호까지 함께 삼켜, 깨진 모델이
   "개선 없는 incumbent"로 위장됐다. 제거하고 전파시킨다.
   회귀 테스트: `test_sub_dispatcher_error_propagates`.
4. **하위 spec에 `stop_predicate=None`을 넘겼다.** §2.3-6대로 `spec.stop_predicate`를
   전달한다 (현재 하위 dispatcher가 읽지 않더라도 배선은 계획대로).
   회귀 테스트: `test_stop_predicate_is_forwarded_to_the_sub_dispatcher`.
5. **`makespan` 추출이 `metrics is not None` 안에 중첩**돼 있어, metrics가 없고
   schedule만 있는 기록에서 makespan이 갱신되지 않았다. 분리.
6. **`_incumbent_fallback_record`에 `destroy_selection` / `setup_seconds`가 없었다.**
   UNKNOWN·INFEASIBLE·budget-exhausted로 빠지면 §2.2가 노린 관측이 사라진다 —
   정작 진단이 필요한 run에서. 세 호출부 모두에 실었다.
   회귀 테스트: `test_fallback_record_keeps_the_selection_label`.
   같은 diff에서 지워졌던 `# int(): FFcSchedule.makespan can be a numpy scalar` 주석도
   복구했다.

### 10.4 테스트 (신규 69개)

| 파일 | 덮는 것 |
|---|---|
| `tests/algorithm/job_contrib_cp/test_option.py` | XOR 6케이스 (둘 다 없음 / 둘 다 / 각각 단독 / 빈 튜플 / 중복) |
| `tests/algorithm/job_contrib_cp/test_dispatcher.py` | 지정 job만 destroy, **기여도 0인 job도 destroy**(= `select_jd_jobs` 미경유의 증거), 인스턴스 밖 job → `ValueError`, `destroy_selection` 양쪽 라벨, `setup_seconds` 범위, fallback 기록의 라벨 보존 |
| `tests/algorithm/job_batch_cp/test_option.py` | 검증 규칙 8케이스 |
| `tests/algorithm/job_batch_cp/test_dispatcher.py` | 전제조건(ref_solution/타입/permutation 3종), 분할 `[3,3,3,1]`·`num_batches=2 → [5,5]`, 배치가 **job_sequence 순서**를 따름, **커버리지 불변식**(합집합=전 job, 중복 0), 수락 규칙 3종(악화/동률 거부·개선 수락) + 다음 배치에 갱신된 incumbent 전달, progress 단조성·종료 항목, 데드라인(만료/중도)·stop_predicate·예외 전파, step log 필드, **실 CP sweep 1개**(obj ≤ seed) |
| `tests/orchestration/test_job_batch_cp_step.py` | 네 메서드 순서 배선(3-stage fixture, 네 순서 pairwise distinct), `seq_tiebreak`/`seq_end_stage`가 실제로 순서를 바꿈, incumbent 부재 → `RuntimeError` ×4, `batch_size` 표현식 4케이스, `_register` 1회 + elapsed가 dispatcher 소요 포함, routix 검증, `_step_log.yaml` 형태 2종, 컨트롤러 경계에서의 단조성 |
| `tests/orchestration/test_neh_cp_incumbent_sequence.py` | (§3 단계 3) `DIAG_RE` 매치 유지, `job_batch_cp`의 같은 형식 진단 라인이 **그 정규식에 걸리지 않음**, fallback step-log 매핑 유지, 평 `neh_cp`는 리스트 유지 |

기존 `test_neh_cp_incumbent_sequence.py` / `test_neh_cp_stopping.py` /
`test_controller.py`는 **수정 없이** 통과 — §2.6 리팩터의 합격 기준.

### 10.5 남은 작업

- **§3 단계 5 (문서)**: `docs/algorithms/job_batch_cp.md`, `README.md:27` 스텝 표 4행,
  `job_contrib_cp`/`sw_cp` 문서와의 상호 참조, `TODO.md`에 destroy-repair 코어 추출
  (§2.1 대안 / §9) 기록 — **전부 미착수**.
- **§5 파일럿 config** `metadata/20260804/job_batch_cp_pilot.yaml` — 미작성.
  §5의 판정 기준 4개(setup 비중 > 0.35, 데드라인 미완주율, `accepted=False` 비율,
  NEH 앵커 대비)는 그대로 유효하다.
- **§6 본 실험 config**: `metadata/20260804/job_batch_cp_compare.yaml`이 작성돼
  있으나 §6.1과 **arm 구성이 다르다** — 2 arm(`completion3_seq`, `midpoint3_seq`,
  둘 다 `seq_end_stage: -2`, `batch_size: 15`)이고 §6.1의 arm 2(`job_priority`)와
  arm 4(**동일-run NEH 앵커**)가 없다. 앵커가 없으면 §7.2의 주 대비(arm1 − arm4)를
  같은 run 안에서 낼 수 없고 교차 run 노이즈 ±0.45 %p를 떠안는다. 파일럿 판정 후
  arm 구성을 확정할 것.
