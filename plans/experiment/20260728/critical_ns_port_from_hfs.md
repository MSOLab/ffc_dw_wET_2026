# 기여도 상위 job D&C 이웃 (`job_contrib_cp`) 구현 계획

**작성일**: 2026-07-28 · **종류**: 코드 변경 계획(사전 작성) · **상태**: **구현 완료**
(2026-07-28, 미커밋). 스모크 런 1회 완료 — §5 Phase A 참조.
**설계 대비 이탈 없음**; 사소한 구현 차이만 각 절에 「구현 노트」로 표기.
**참고**: `../hybridflowshop` `7013e236` — **구성 요소만 참고**, 코드 이식 아님
**대상**: `src/ffc_ddw_sum_et/algorithm/job_contrib_cp/` (신규), `orchestration/controller.py`

---

## 0. 이 이웃의 정의 (한 문장)

> incumbent에서 **목적식 기여도 상위 최대 `jd_count_target`개 job을 제거(destruct)** 하고,
> **나머지 job의 상대 순서를 profile fix로 보존한 채**, incumbent를
> **complete hint**로 주어 **CP-SAT가 그 job들을 재삽입(construct)** 하게 한다.
> 들어낼 job이 하나도 없으면 CP를 만들지 않고 incumbent를 그대로 반환한다.

파괴 규모(`jd_target`)가 **유일한 1급 알고리즘 파라미터**다.

hybridflowshop에서 참고한 것은 "job 단위로 뜯고 CP로 다시 채운다"는 **구성 요소**
하나뿐이고, 그쪽의 criticality 정의(makespan CPM slack)는 **쓰지 않는다**.
이 문제는 각 job이 `C_j`를 통해 목적식에 **독립적으로** 기여하므로 slack 대리변수가
필요 없고 기여도를 직접 쓰면 된다.

---

## 1. 절차 명세

### (0) 입력: incumbent

`solution_manager.get_incumbent()` 의 `schedule`. 없으면 `RuntimeError`
(`run_profile_fixed_ns`, `sw_cp`와 동일 규약 — seeding 스텝 뒤에 체인).

### (1) 파괴: 기여도 상위 **최대** `jd_count_target`개 job 선택

`jd_count_target`은 **상한(at most)** 이지 목표치가 아니다. $f_j(C_j) > 0$인 job이
그보다 적으면 그만큼만 들어낸다 — 실제 파괴 수가 `jd_count_eff`다
($0 \le$ `jd_count_eff` $\le$ `jd_count_target`).

#### 표기 규약 (수식 ↔ 코드)

**기여도**

| 층위 | 표기 |
|---|---|
| 수식 | $f_j(C_j) = w^-_j \cdot \max(0,\ d^-_j - \tau C_j) + w^+_j \cdot \max(0,\ \tau C_j - d^+_j)$ |
| 코드 (용어) | `obj_contrib` — 저장소에 이미 정착한 용어 (`FFcSchedule.delay_job_latest_leq_obj_contrib` 등) |
| 코드 (자료구조) | `job_2_obj_contrib_map` — `job_2_*_map` 관례 (`job_2_dw_ub_map`, `job_2_ewt_map`) |

**파괴 규모** — `k`는 수식에서 아무 의미로나 쓰이는 기호이므로 **코드에서는 절대
쓰지 않는다.** 세 단계를 서로 다른 이름으로 구분한다 (`jd` = job destroy):

| 단계 | 코드 이름 | 타입 | 사는 곳 |
|---|---|---|---|
| config 표기 (미해석) | **`jd_target`** | `int \| str` (`1`, `"2"`, `"0.05n"`) | 시나리오 YAML → `controller.job_contrib_cp` 인자 |
| 정수 해석 후 (상한) | **`jd_count_target`** | `int` (≥1) | `JobContribCpOption` 필드 · 디스패처 |
| 실제 파괴 수 | **`jd_count_eff`** | `int` (≥0) | 디스패처 지역변수 · `AlgResult.metrics` |

수식 문맥에서 $k$ / $k'$를 쓰는 것은 무방하지만, **식별자·로그 키·metrics 키·
문서의 코드 블록은 위 세 이름만 쓴다.**

`C_j` = incumbent의 마지막 스테이지 종료시각, `τ` = `time_factor`.
`solution/objectives.py:compute_weighted_earliness_tardiness`가 이미 같은 식을
**합계**로 계산하므로 — $\sum_j f_j(C_j)$ — **job별 분해판**을 같은 모듈에
추가한다(SSOT):

```python
def compute_job_2_obj_contrib_map(
    schedule: FFcSchedule, instance: FFcDDWParameters, *, time_factor: int = 1
) -> dict[JobIdType, int]:
    """job_id -> f_j(C_j). 합은 compute_weighted_earliness_tardiness의 총합과 같다."""
```

**결측 가중치 규약**: `compute_weighted_earliness_tardiness`는 `job_2_ewt_map` /
`job_2_twt_map`에 없는 job의 가중치를 **1로 기본값 처리**한다(FAM 원본 계산과 맞추기
위한 규약). 새 함수도 **반드시 동일하게** 처리해야 P1 테스트(job별 합 == 총합)가 성립한다.

#### 선택 절차 — **모델 구축 이전에, 이 순서로**

이 블록은 전부 $O(n \log n)$이고 CP 모델·horizon·solver를 **전혀 건드리지 않는다.**
early-exit 판정이 가장 싼 지점에서 끝나도록 순서를 고정한다.

```py
# 1. 기여도 계산 (O(n))
job_2_obj_contrib_map = compute_job_2_obj_contrib_map(incumbent, instance, time_factor=...)

# 2. 양의 기여도를 가진 job만 후보 (Q5: 0인 job은 채우지 않는다)
positive_jobs = [j for j, v in job_2_obj_contrib_map.items() if v > 0]

# 3. 상한 적용 (jd_count_target 은 컨트롤러가 이미 해석해 넘긴 int)
selected = sorted(
    positive_jobs, key=lambda j: (-job_2_obj_contrib_map[j], j)
)[: option.jd_count_target]
jd_count_eff = len(selected)

# 4. EARLY EXIT — 들어낼 job이 없으면 incumbent를 그대로 반환
if jd_count_eff == 0:
    logger.info(
        "job_contrib_cp: no job with positive objective contribution; "
        "returning the incumbent unchanged (obj=%.1f).", incumbent_obj,
    )
    return <incumbent 그대로 담은 AlgRecord>

# 5. 로그 — 목표와 실제를 항상 함께 남긴다
logger.info(
    "job_contrib_cp: jd_count_target=%d, jd_count_eff=%d "
    "(positive-contrib jobs=%d, n=%d, incumbent obj=%.1f)",
    option.jd_count_target, jd_count_eff, len(positive_jobs),
    instance.job_count, incumbent_obj,
)
```

- **earliness / tardiness를 구분하지 않는다** (사용자 명세 그대로). $f_j(C_j)$는
  부호가 없는 "이 job이 물고 있는 페널티"이고, 그 job을 완전히 고쳤을 때 얻을 수
  있는 **개선의 상한**이라는 명확한 의미를 갖는다.
- 동점은 `job_id`로 결정론적으로 깬다 (재현성).
- `jd_count_eff < jd_count_target`은 **정상 경로**다. 경고가 아니라 `log.info`로
  남긴다 — `jd_target`을 크게 준 시나리오에서 흔하게 발생하고(§5 Phase B), 실험
  사후 분석에서 "이 시나리오의 유효 파괴 규모는 얼마였나"를 되짚는 자료가 된다.

##### `jd_count_eff == 0` 의 의미와 반환 규약

`jd_count_eff == 0` $\iff \forall j,\ f_j(C_j) = 0 \iff$ **incumbent의 목적값이 0**
— 즉 이미 전 job이 due window 안에 있는 최적해다. 개선의 여지가 정의상 없으므로:

- CP 모델을 **만들지 않는다** (horizon 계산·`BaseModelBuilder.build`·solve 전부 skip).
- 반환 레코드는 다음 형태 (`algorithm/base/alg_record.py`):

  ```python
  AlgRecord(
      work_status=WorkStatus.OPTIMAL,     # obj=0 은 목적식이 비음수이므로 최적 증명
      algorithm_id="job_contrib_cp",
      option=option,
      result=AlgResult(
          schedule=incumbent,
          obj_value=0.0,
          obj_bound=0.0,                  # Q7 확정: 0 은 항상 유효한 전역 하한
          metrics={
              "jd_count_target": option.jd_count_target,
              "jd_count_eff": 0,
              "positive_contrib_job_count": 0,
              "incumbent_obj": 0.0,
          },
      ),
      termination_reason=TerminationReason.COMPLETED,
  )
  ```

- 컨트롤러 스텝은 `SubroutineReport(elapsed_time=<측정값>, obj_value=0.0,
  obj_bound=0.0)` 로 `_register`를 **1회** 호출한다 (`AGENTS.md` 계약 유지).
  `solution_manager`는 동일 목적값이면 incumbent를 유지하므로 부작용이 없다.
  **이 스텝에서 `obj_bound`가 `None`이 아닌 유일한 경로다** — `obj_value == obj_bound`
  가 성립해 `CLAUDE.md`의 최적성 판정에서 이 인스턴스가 최적 증명됨으로 기록된다.
- `metrics`에도 `jd_count_eff=0`을 남긴다. 그래야 §5 Phase B 집계에서 이 인스턴스가
  **누락되지 않고 "손댈 것이 없었다"로 식별**된다.
- 이 경로는 **에러가 아니다**. `warning`/`error`가 아니라 `log.info`.

#### `jd_target` → `jd_count_target` 해석 규칙 (Q2: 단일 표기)

`sw_cp`의 `batch_size`와 동일한 패턴 — config에는 `str` 또는 `int` 하나만 쓴다:

| `jd_target` 표기 | 의미 | 예시 |
|---|---|---|
| `int` (양의 정수) | 절대 개수 | `2` → `jd_count_target = 2` |
| `str` (양의 정수) | 절대 개수 | `"2"` → `jd_count_target = 2` |
| `str` (`"<ratio>n"` 꼴) | 비율 | `"0.05n"` → `jd_count_target = ceil(n · 0.05)` |

형식 검증 (정규식 `^(\d+|\d+\.?\d*n)$`), 분기 `n` 포함 → `ceil(n * ratio)`,
아니면 `int(jd_target)`. 결과는 `[1, n]`으로 클램프.
`jd_count_target`은 어디까지나 **상한**이고, 실제 파괴 수 `jd_count_eff`는
위 선택 절차에서 $\#\{j : f_j(C_j) > 0\}$에 의해 더 줄어들 수 있다.
로그에 `jd_target`, `n`, `jd_count_target`, 해석 경로를 남긴다
(`sw_cp`의 `batch_size` 해석 로그와 동일 스타일).

> **구현 노트** — `resolve_jd_count_target`은 `orchestration/value_resolver.py`에
> `resolve_value_expr` 옆에 두었다. 최초 구현은 `math` / `logging`을 함수 본문에서
> import했으나, 모듈 상단 `import math` / `import logging` +
> `logger = logging.getLogger(__name__)`로 옮겼다 (저장소 관례, 동작 차이 없음).

##### `jd_count_target >= 1` 은 **입력 검증만으로 보장된다** (Q9)

`n`을 몰라도 사전 판정이 가능하다 — `pf_method is None` 거부와 같은 층에서
처리할 수 있다:

| `jd_target` 형태 | 양수 보장 조건 | 근거 |
|---|---|---|
| 절대 정수 | `jd_target >= 1` | `n`과 무관, 그 자체가 결과 |
| 비율 `"<ratio>n"` | `ratio > 0` | `n >= 1`이므로 $\lceil n \cdot ratio \rceil \ge 1$ — 아무리 작은 양의 비율이어도 `ceil`이 1로 올린다 |

즉 **`jd_count_target <= 0`을 만들 수 있는 입력은 `0` / `"0"` / `"0n"` / `"0.0n"`
뿐이고, 전부 `n` 없이 걸러진다.** 따라서:

- 현재 정규식 `^(\d+|\d+\.?\d*n)$`은 `0`과 `"0n"`을 통과시키므로 **부족하다.**
  `resolve_jd_count_target`은 파싱 후 **절대값 `>= 1`, 비율 `> 0`을 명시적으로
  검사하고 `ValueError`를 던진다** (정규식만으로 표현하려 하지 말 것 — 읽기 어렵고
  `"0.0n"` 같은 변형을 놓친다).
- **하한 클램프는 하지 않는다.** `max(1, ·)`로 조용히 올리면 `jd_target=0`이라는
  명백한 설정 실수가 "1개 파괴"로 둔갑한다. 하한은 **거부**, 상한만 `min(·, n)`
  으로 포화시키고 그 사실을 로그에 남긴다 (상한 초과는 "전부 파괴"라는 의미가
  분명해 실수로 보기 어렵다).
- `JobContribCpOption.__post_init__`의 `jd_count_target >= 1`은 **최후 방어선**으로
  남긴다 (디스패처를 컨트롤러 없이 직접 호출하는 테스트 경로 대비).

**한계 (정직하게)**: 이 저장소에는 시나리오 파라미터를 실행 전에 검사하는
config-schema 계층이 없다 — `routix`의 `SubroutineFlowKeys.parse_step`은 스텝의
*형태*만 보고 `params`는 보지 않는다. 따라서 실제 발화 시점은 **컨트롤러 스텝
진입 직후**다. 다만 검사 자체가 `n`에 의존하지 않으므로 **첫 인스턴스에서
결정론적으로** 터진다 — 특정 크기의 인스턴스에서만 뒤늦게 드러나는 종류의
버그가 아니다.

### (2) 구성 제약: 나머지 job profile fix

**핵심 구현 (기존 패턴 재사용)** — `sw_cp/cp_model.py:_add_profile_fix_precedence_constraints`
가 쓰는 방식을 그대로 따른다:

```python
pf_schedule = incumbent.deepcopy()
pf_schedule.remove_jobs(set(selected))          # ffc_schedule.py:1003
by_machine, stride_set = decode_pf_method(pf_method)   # 기본 "PF1"
BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule(
    mdl, params, op_vars, pf_schedule,
    profile_fix_by_machine=by_machine,
    machine_precedence_stride_set=stride_set,
)
```

이 방식의 중요한 성질 — **arc가 자동으로 이어붙는다(bridging)**:
머신 순서가 `… A → X → B …`이고 `X`가 선택되었다면, `remove_jobs` 후 시퀀스는
`… A → B …`가 되어 헬퍼가 `A → B` arc를 만든다. 즉

- 선택되지 **않은** job들의 **상대 순서는 완전히 보존**되고,
- 선택된 job은 **어느 머신 어느 위치로도 재삽입 가능**하다
  (cumulative 정식화라 머신 배정 변수가 없으므로 배정도 자유),
- 선택되지 않은 job들도 **시각은 자유롭게 이동**한다(profile fix는 순서만 고정).
  E/T 개선의 상당 부분이 여기서 나온다.

> 만약 arc를 "선택 job에 붙은 것만 제거"하는 식으로 짜면 `A`와 `B`가 서로
> 풀려버려 의도보다 훨씬 넓은 이웃이 된다. `remove_jobs` 경유가 이 함정을
> 구조적으로 피한다.

- `pf_method: PFMethod = "PF1"` (기본). `"PF0" | "PF1" | "PF2" | "MPF23"` 선택 가능
  (`cumulative.py:18`). `None`은 **config 검사에서 `ValueError`로 거부** (Q3: full CP re-solve와
  동치되므로 이 이웃의 정체성을 벗어남).
- **PF1 + `jd_target=1`** 은 "나머지 순서 완전 고정 + job 하나 최적 재삽입" — 가장
  좁고 가장 싼 이웃. 이것이 자연스러운 하한 baseline이다.

### (3) Complete hint

모델의 정수 변수는 정확히 4종뿐이다 (`cumulative.py`):

| 변수 | 개수 | hint 헬퍼 |
|---|---|---|
| `op_start[j,i]` | `n·c` | `apply_start_hints_from_start_time_map` |
| `op_end[j,i]` | `n·c` | `apply_end_hints_from_end_time_map` |
| `E[j]` | `n` | `apply_et_hints_from_ref_schedule` |
| `T[j]` | `n` | 〃 |

`op_intvl`은 `new_interval_var(start, 상수 size, end)`라 새 변수를 만들지 않는다.
따라서 위 3개 헬퍼를 **선택된 job을 포함한 전체 job**에 대해 호출하면 complete hint가 된다
(`run_profile_fixed_ns`가 이미 이 조합을 쓴다 — 거기서는 hint 완전성을 확인한 적이 없다).

**검증 (필수)**: `log_search_progress=True`로 돌려 CP-SAT 로그의
`The solution hint is complete...` / `... is incomplete: X out of Y` 줄을 확인한다.
테스트에서는 `solver.parameters.log_to_stdout` 대신 로그 콜백을 캡처해
`"incomplete"`가 없음을 assert 한다.

**부수 효과**: hint가 완전하고 feasible하면 CP-SAT의 초기 incumbent가 곧
현재 incumbent이므로, **CP 목적값 ≤ incumbent 목적값**이 보장된다.

### (4) Solve

여기 도달했다는 것은 §1-(1)의 early exit를 통과했다는 뜻 — 즉 `jd_count_eff >= 1`이
보장된다.

- `mdl, params, op_vars, et_vars = BaseModelBuilder().build(instance, horizon, time_factor=…)`
- **horizon**: `run_profile_fixed_ns`는 `ceil(makespan · multiplier)`를 쓰는데,
  이 이웃에서는 **부족할 수 있다**. 선택된 job을 자기 due window까지 **뒤로** 밀
  자리가 필요하기 때문이다. 따라서

  ```
  horizon = ceil(max(incumbent.makespan, max_j d⁺_j) · horizon_multiplier)
  ```

  로 잡는다 (`horizon_multiplier: float = 1.25` 기본). `τ > 1`이면 `d⁺`는
  원 스케일이므로 `d⁺/τ`로 환산해 비교한다.
- TL: `cp_tl: float | str | None` → `resolve_value_expr(cp_tl, n, c, m_last)`
  (`"<x>nc"` 표기 지원, `orchestration/value_resolver.py`).
- `solver.parameters.num_workers = solver_thread_cnt`.
- 상태가 `OPTIMAL`/`FEASIBLE`이 아니면 등록 없이 경고 후 반환
  (complete hint가 있으므로 사실상 발생하면 안 된다 — 발생 시 **버그 신호**로
  로그를 `error` 레벨로 남긴다).

### (5) 재구성

`flip_makespan_cp/dispatcher.py:296~` 의 기존 순서를 그대로 따른다:

```python
schedule = build_schedule_from_op_starts(instance, j_i_2_start, j_i_2_end)
schedule.make_semi_active(instance.stage_2_job_2_p_map)
schedule.insert_idle_time(
    instance.job_2_due_window_map, instance.job_2_ewt_map, instance.job_2_twt_map,
    time_factor=…,
)
sum_e, sum_t = compute_weighted_earliness_tardiness(schedule, instance, time_factor=…)
```

**주의 (테스트로 잡을 것)**: `make_semi_active`는 좌측 정렬이라 **earliness를
늘릴 수 있고**, `insert_idle_time`은 마지막 스테이지만 우측 이동시킨다. 따라서
후처리 결과가 CP 목적값보다 **나빠질 수 있다**. 대응:

- `post_obj > cp_obj + tol` 이면 `warning` 로그 (`run_profile_fixed_ns`가 CP 목적값
  불일치에 대해 하는 것과 같은 방식).
- 등록은 `solution_manager`가 더 나은 쪽만 채택하므로 안전하지만, 이 스텝의
  `SubroutineReport.obj_value`는 실제 후처리 목적값을 그대로 보고한다.
- `obj_bound`는 **`None`** — profile-fixed 모델의 bound는 전역 하한이 아니다
  (`run_profile_fixed_ns`의 주석과 동일). §1-(1)의 `jd_count_eff == 0` early-exit
  경로만 예외적으로 `0.0`을 보고한다.

### (6) 진단 페이로드 — `AlgResult.metrics` (Q8 확정)

`jd_count_eff`는 **로그 문자열이 아니라 구조화된 값으로** 실어 보낸다. 로그 포맷은
계약이 아니지만 `metrics`는 계약이고, §5 Phase B 전체가 이 값에 의존한다.

```python
metrics = {
    "jd_count_target": option.jd_count_target,   # 해석된 상한
    "jd_count_eff": jd_count_eff,                # 실제 파괴한 job 수
    "positive_contrib_job_count": len(positive_jobs),
    "incumbent_obj": incumbent_obj,
}
```

- **모든 경로에서 채운다** — early exit(`jd_count_eff=0`) 포함. 한 경로라도 비면
  Phase B의 분모가 깨진다.
- 원시 표기 `jd_target`은 넣지 않는다 — 시나리오 config에 이미 있고, `metrics`는
  **해석 결과**만 담는다 (`jd_count_target`이 그 결과다).

> **구현 노트** — 실제 `metrics`는 위 4개의 **상위집합**이다. 모든 경로가
> `selected_jobs`(파괴된 job id 목록)를 추가로 싣고, 정상 경로는
> `cpsat_status` / `cpsat_obj` / `horizon` / `sum_earliness` / `sum_tardiness` /
> `makespan`을, fallback 경로는 `cpsat_status` / `fallback: incumbent`를 더 싣는다.
> `makespan`은 `int(...)`로 감싼다 — `FFcSchedule`의 시각 값은 numpy 스칼라라
> 그대로 두면 컨트롤러의 `dump_yaml`이 `RepresenterError`로 스텝 전체를 실패시킨다
> (§4 P6 회귀 테스트가 이 경로를 지킨다).
- 컨트롤러는 `_register` **이후**(계약: 등록 후 진단) `result.metrics`를
  `try_get_file_path_for_subroutine("_metrics.yaml")`에 `dump_yaml`로 떨어뜨린다.
  `sw_cp`가 `_step_log.yaml`을 쓰는 것과 같은 패턴 (`controller.py:sw_cp` 말미).
  경로가 `None`이면(테스트/스크립트 실행) 조용히 건너뛴다.

---

## 2. 반복 구조에서의 함정 (설계 시 반드시 고려)

선택 규칙이 **결정론적 top-`jd_count_target`** 이므로:

- CP가 개선하면 → $f_j(C_j)$ 순위가 바뀌어 다음 회차의 job 집합이 달라진다. 정상.
- CP가 **개선하지 못하면** → incumbent가 그대로 → 다음 회차의 모델이 **완전히
  동일** → TL만 낭비. **고정점(fixed point)에 갇힌다.**

따라서 반복 스텝(`incremental_job_contrib_cp`)의 정책은 최소한 다음 중 하나여야 한다:

| 정책 | 내용 |
|---|---|
| `stop_on_no_improvement` | 개선 없으면 즉시 종료 (가장 단순, 기본값 후보) |
| `ramp_jd` | 개선 없으면 `jd_count_target`을 키워 재시도 (`incremental_sw_cp`의 count ramp와 같은 꼴) |
| `randomize` | $f_j(C_j)$ 가중 비복원 추출로 job 집합을 흔든다 (hybridflowshop `weighted_random`) |

**1차 범위는 단발 스텝으로 확정 (Q4).** `ramp_jd` / `randomize`는 파일럿 결과를 보고 붙인다 (YAGNI).

---

## 3. 배치

```
src/ffc_ddw_sum_et/algorithm/job_contrib_cp/
    __init__.py     # 빈 surface (CLAUDE.md 규약: algorithm 패키지는 재수출 안 함)
    option.py       # JobContribCpOption(AlgOption)
    dispatcher.py   # JobContribCpDispatcher(Algorithm)  — (1)~(5) 전 과정
```

`solution/objectives.py` 에 `compute_job_2_obj_contrib_map` 추가 (§1-(1)).
그 외 신규 코드 없음 — 나머지는 전부 기존 헬퍼 호출.

### `JobContribCpOption` 초안

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class JobContribCpOption(AlgOption):
    jd_count_target: int                # 컨트롤러가 pre-resolve한 상한 (≥1)
    pf_method: PFMethod = "PF1"         # "PF0" | "PF1" | "PF2" | "MPF23" (None 불가)
    horizon_multiplier: float = 1.25
    cp_tl_seconds: float | None = None  # 컨트롤러가 pre-resolve
    wall_clock_deadline_sec: float | None = None
    solver_thread_cnt: int = 1
    time_factor: int = 1
    error_if_infeasible: bool = False
    log_search_progress: bool = False
```

`pf_method is None` 거부와 `jd_count_target >= 1` 검사는 `__post_init__`에서 수행.
원시 표기 `jd_target`(`"0.05n"` 등)의 형식 검증은 **컨트롤러 쪽
`resolve_jd_count_target`** 책임 — 옵션은 해석된 `int`만 받는다.

### 컨트롤러 스텝

`orchestration/AGENTS.md`의 subroutine step 계약(호출당 `_register` 최대 1회,
`elapsed_time` 측정과 `_register` 사이에 작업 금지)을 **착수 전에 읽고** 지킨다.
`docs/algorithm-principles.md`도 선행 필독.

**어댑터는 `controller.py:sw_cp`(2316~)를 골격 그대로 축약한 것이다.** 아래 순서를 지킨다:

```python
def job_contrib_cp(
    self,
    jd_target: int | str = 1,           # 시나리오 YAML에 그대로 노출되는 이름
    pf_method: PFMethod = "PF1",
    cp_tl: float | str | None = None,
    solver_thread_cnt: int = 1,
    horizon_multiplier: float = 1.25,
    error_if_infeasible: bool = False,
    log_search_progress: bool = False,
    draw_gantt: bool = False,
) -> SubroutineReport:
    start_elapsed = time.monotonic()
    if self.is_stopping_condition():                 # ← sw_cp와 동일, 빠뜨리기 쉬움
        return self._make_stop_report(start_elapsed)

    incumbent = self.solution_manager.get_incumbent()
    if incumbent is None or incumbent.schedule is None:
        raise RuntimeError("job_contrib_cp requires an incumbent schedule; ...")

    n, c, m = instance.job_count, instance.stage_count, instance.last_stage_mc_count
    cp_tl_seconds = resolve_value_expr(cp_tl, n, c, m)
    jd_count_target = resolve_jd_count_target(jd_target, n)        # 여기서만 해석
    self.logger.info(
        "job_contrib_cp: jd_target=%r -> jd_count_target=%d (n=%d)",
        jd_target, jd_count_target, n,
    )
    remaining_sec = self.timer.get_remaining_sec(self.stopping_criteria.timelimit)
    wall_clock_deadline_sec = time.monotonic() + remaining_sec     # ← 총 TL 초과 방지

    option = JobContribCpOption(jd_count_target=jd_count_target, ...,
                                time_factor=self.time_factor,
                                wall_clock_deadline_sec=wall_clock_deadline_sec)
    spec = AlgSpec(instance=instance, option=option,
                   ref_solution=incumbent.schedule, logger=self.logger,
                   stop_predicate=self.is_stopping_condition)
    record = JobContribCpDispatcher().run(spec)

    elapsed = time.monotonic() - start_elapsed        # ← 이 아래로 작업 금지
    report = SubroutineReport(
        elapsed_time=elapsed,
        obj_value=result.obj_value,
        obj_bound=result.obj_bound,   # 하드코딩 금지 — early-exit 경로만 0.0, 그 외 None
    )
    self._register(report, FFcDDWSolution(...) or None)

    # 등록 후 진단 (계약: _register 이후)
    if result is not None and result.metrics:
        path = self.try_get_file_path_for_subroutine("_metrics.yaml")
        if path is not None:
            dump_yaml(dict(result.metrics), path)
    return report
```

- **해석 위치**: `jd_target`은 `n`에만 의존하므로 컨트롤러에서 pre-resolve해도 되고
  디스패처에서 해도 된다. `sw_cp`가 `batch_size`를 **컨트롤러에서** 해석해 옵션에는
  스칼라만 넣으므로, **같은 규약을 따른다** — `jd_target`(원시 표기)은 컨트롤러
  시그니처에만 존재하고, 옵션·디스패처·`metrics`는 `jd_count_target`(해석된 `int`)만
  본다. 세 이름이 각자 한 층에만 사는 것이 이 분리의 핵심이다.
- **incumbent 전달 경계**: 컨트롤러가 `solution_manager`에서 꺼내
  `AlgSpec.ref_solution`으로 넘긴다. 디스패처는 `spec.ref_solution is None`이면
  `RuntimeError`. 디스패처는 `solution_manager`를 **모른다**.
- **시나리오 등록 불필요**: 플로우는 `method: <controller 메서드명>`으로 routix가
  이름 해석해 호출한다(`scripts/20260725/build_crossover_config.py`의
  `"method": "incremental_sw_cp"` 참조). 별도 레지스트리에 추가할 것이 없다 —
  컨트롤러에 메서드를 추가하면 곧바로 YAML에서 쓸 수 있다.
- **`wall_clock_deadline_sec`**: CP TL을 남은 전체 예산으로 클램프하는 데 쓴다.
  §5 Phase A가 "총 TL 예산 동일"을 전제로 하므로 **빠뜨리면 실험이 무효가 된다.**

> **구현 노트**
>
> - 테스트: `tests/orchestration/test_job_contrib_cp_step.py` **신규** (P6, 5개).
>   컨트롤러를 거치는 경로 — 특히 `_register` 이후의 `_metrics.yaml` 기록 — 는
>   디스패처 단위 테스트가 전혀 건드리지 않으므로 여기서만 커버된다.
>   `set_working_dir(tmp_path)`를 줘야 `try_get_file_path_for_subroutine`이
>   경로를 반환하고 `dump_yaml`이 실제로 돌아간다.
> - `draw_gantt=True` 지원: `_record_mcf_lb_phase(("job_contrib_cp_before", …))`를
>   `_register` **이전**에, `("job_contrib_cp_after", …)`를 **이후**에 기록한다
>   (`sw_cp`의 `sw_cp_before` / `sw_cp_after`와 같은 컨테이너·같은 규약). 러너가
>   이를 `mcf_lb_phase_schedule` JSON으로 떨어뜨리고 리포터가 PNG로 렌더한다.
> - `eff_tl`은 `cp_tl` 잔여분과 `wall_clock_deadline_sec` 잔여분 중 **더 작은
>   쪽**으로 잡고, 어느 쪽이 binding인지 로그와 `cpsat_status`
>   (`budget_exhausted_before_solve:<binding>`)에 남긴다.

---

## 4. 구현 단계 (TDD)

| 단계 | 내용 | Red로 먼저 세울 테스트 |
|---|---|---|
| **P1** | `compute_job_2_obj_contrib_map` | job별 합 == `compute_weighted_earliness_tardiness` 총합 / due window 안의 job은 0 / `time_factor=2` 환산 |
| **P2** | job 선택 | top-`jd_count_target` 결정론(동점 job_id) / `jd_count_target > #{f_j > 0}` 일 때 `jd_count_eff = #{f_j > 0}` 로 축소 |
| **P2a** | `resolve_jd_count_target` | `"5"`·`5`·`"0.05n"` 파싱과 `ceil` 경계 / **`0`·`"0"`·`"0n"`·`"0.0n"` → `ValueError`** (하한 클램프 금지) / `jd_target > n` → `min(·, n)` 포화 + 로그 / 잘못된 표기 → `ValueError` |
| **P2b** | `jd_count_eff == 0` early exit | 전 job이 due window 안인 incumbent 투입 시 ①`BaseModelBuilder.build`가 **호출되지 않음**(mock/spy) ②반환 schedule이 incumbent와 동일 ③`obj_value == 0.0`, `obj_bound == 0.0`, `work_status == OPTIMAL` ④`metrics["jd_count_eff"] == 0` |
| **P2c** | `metrics` 전 경로 충족 | 정상 경로와 early-exit 경로 **양쪽 모두** `metrics`에 `jd_count_target`/`jd_count_eff`/`positive_contrib_job_count`/`incumbent_obj` 4개 키가 존재 / `jd_count_eff == len(selected)` |
| **P3** | profile fix arc 구성 | **bridging 테스트**: `A→X→B`에서 `X` 선택 시 `A→B` arc가 생기고 `A→X`,`X→B`는 없음 |
| **P4** | complete hint | CP-SAT 로그에 `"incomplete"` 문자열이 없음 / CP 목적값 ≤ incumbent 목적값 |
| **P5** | 디스패처 end-to-end | 소형 인스턴스에서 `jd_target=1, PF1` 목적값 비악화 / horizon이 `max d⁺`를 덮는지 / 후처리 목적값 회귀 경고 발생 조건 |
| **P6** | 컨트롤러 스텝 배선 | `_register` 1회 / incumbent 없으면 `RuntimeError` |

각 단계 후 `uv run ruff check`, 필요 시 `uv run ruff format`.
테스트 위치 `tests/algorithm/job_contrib_cp/`, `tests/solution/test_objectives.py`,
`tests/orchestration/test_job_contrib_cp_step.py`.

### 구현 결과 (2026-07-28)

| 항목 | 값 |
|---|---|
| 신규 테스트 | **44** — `tests/algorithm/job_contrib_cp/` 33 (P1–P5), `tests/solution/test_objectives.py` 6 (P1), `tests/orchestration/test_job_contrib_cp_step.py` 5 (P6) |
| 전체 스위트 | **738 passed, 0 skipped** |
| lint | `uv run ruff check src/ tests/` clean, `ruff format` clean |

P1–P6 전 단계 이행. 최초 구현 후 리뷰에서 잡아 고친 것 (전부 테스트로 고정):

1. **`metrics["makespan"]`이 numpy 스칼라** → 컨트롤러의 `dump_yaml`이
   `RepresenterError`로 스텝 전체를 실패시킴. 실전 경로에서만 터지는 버그였고
   P6 테스트가 없어 안 잡혔다. `int(...)`로 수정.
2. **`wall_clock_deadline_sec` 미사용** — 옵션에만 있고 디스패처가 안 읽어
   `cp_tl`만으로 클램프하고 있었다(§3 「빠뜨리면 실험이 무효」 항목).
3. **P2b(early-exit) 테스트 2개가 항상 skip** — 조건부 `pytest.skip`이었는데
   시드가 항상 양의 기여도를 가져 한 번도 실행되지 않았다. 전용 픽스처
   `_make_zero_contrib_instance()`(모든 due window `(0, 100)` → 구성상 전 job
   기여도 0)로 교체하고 skip을 제거.
4. **P4(complete hint) 테스트가 공허** — `log_search_progress`를 안 켜서
   `log_callback`이 아예 호출되지 않아 빈 로그에 대한 부정 단언이 무조건
   통과했다. 플래그를 켜고 "로그가 비어있지 않다" + "complete 라인이 있다"는
   긍정 단언을 앞에 세웠다. 디스패처에도 같은 자기검증(incomplete 보고 시
   warning)을 넣었다.

---

## 5. 실험 계획

### Phase A — `jd_target` 스윕 (이 계획의 본론)

파괴 규모가 유일한 1급 파라미터이므로 실험도 여기에 집중한다.

- **baseline**: 현행 최고 조합 (`calc_mcf_lb_and_derive_full_sch` → `incremental_sw_cp`), 총 TL 동일
- **arm**: baseline 뒤에 `job_contrib_cp` 단발 삽입, **총 TL 예산 동일** (추가 TL 금지)
- **스윕 축**: `jd_target ∈ {1, 2, "0.05n"}` — 절대 2개 + 비율 1개 (파일럿 수준)
- **부차 축**: `pf_method ∈ {PF1, PF0}` — PF1이 기본이지만 PF0(스테이지 시간 기반)이
  더 넓은 이웃이라 `jd_target`이 작을 때 대안이 될 수 있다. 2급.
- **인스턴스**: PRA2017 그리드. 층화는 `pra2017-instance-params` skill 기준
  (n, c, mps, W). 1차는 ~160개 부분집합, 확정 후 1440 전량.
- **노이즈**: 메모리 기록대로 8-worker wall-clock CP-SAT는 비결정적이고 1440 그리드
  평균 obj 기준 **±350 이하는 잡음**이다. 파일럿 규모에서는 **paired per-instance
  dRPDf 부호 검정**으로 판단한다. `solver_thread_cnt=1`로 두면 결정론에 가까워져
  스윕의 신호 대 잡음비가 좋아진다 — **파일럿은 1스레드 권장**.
- 산출물: `output/20260728_job_contrib_cp_jd_sweep/<timestamp>/` (repo 안, `/tmp` 금지)

#### 스모크 런 (구현 검증용, 2026-07-28 실행 완료)

Phase A 본 스윕에 앞서 **스텝이 실전 경로에서 도는지**를 확인하는 소규모 런.

| 항목 | 값 |
|---|---|
| config | `metadata/20260728/job_contrib_cp_resume_smoke.yaml` |
| 런 디렉터리 | `output/20260728_job_contrib_cp_smoke/20260728T112454_493695` |
| 방식 | `run_mode: RESUME`, `resume_dir: output/20260726_csr_init_tl_curve/20260726T231158_246105/c5_init_only` (3-step prefix 검증 → `flow_resume_idx=3`, `job_contrib_cp`만 재실행) |
| 인스턴스 | 8개 층화 슬라이스 `ins_index: [0, 80, 245, 470, 585, 690, 1145, 1370]` |
| 시나리오 | `jd1_pf1` (`jd_target: 1`, PF1) · `jd05n_pf1` (`0.05n`, PF1) · `jd05n_pf0` (`0.05n`, PF0) |
| 공통 | `cp_tl: 0.018nc`, `timelimit: 0.09nc`(base와 동일), `draw_gantt: true`, `log_search_progress: true`, `error_if_infeasible: true` |

**계획 대비 차이**: Phase A 본안의 스윕 축은 `jd_target ∈ {1, 2, "0.05n"}`인데
스모크에서는 **`jd_target=2`를 뺐다** (절대값 축 1점 + 비율 축 1점이면 파싱·동작
검증에 충분하고, 대신 `pf_method` 축을 하나 넣는 편이 낫다고 판단). 본 스윕에서는
`2`를 되살려야 한다.

**설정 의도 (프로덕션 스윕에서는 되돌릴 것)**: `log_search_progress: true`는
디스패처의 complete-hint 자기검증을 켜기 위한 것이고, `error_if_infeasible: true`는
complete feasible hint가 있는데 INFEASIBLE/UNKNOWN이 나오면 조용한 fallback 대신
크게 터뜨리기 위한 것이다.

`main.py`의 `CONFIG_PATH` 기본값도 이 config로 바꿔 두었다
(`metadata/20260727/csr_usability_sweep.yaml` → `metadata/20260728/job_contrib_cp_resume_smoke.yaml`).

### Phase B — 진단 (Phase A와 병행 가능, 저비용)

§1-(6)의 `_metrics.yaml`(`jd_count_target`, `jd_count_eff`,
`positive_contrib_job_count`, `incumbent_obj`)이 이 진단의 **1차 자료**다. 인스턴스
디렉터리에서 이 파일만 모으면 되고, 로그를 파싱하지 않는다. (`log.info` 줄은 사람이
런을 실시간으로 지켜볼 때의 보조 수단.)

- `positive_contrib_job_count` — 페널티를 물고 있는 job 수. **파괴 규모의 실질
  상한**이다. `jd_count_eff`가 `jd_count_target`에 붙어 있으면 `jd_target`을 더
  키워볼 여지가 있고, 한참 못 미치면 그 시나리오는 이미 포화 — 스윕 축을 낭비하고
  있다는 신호.
- `jd_count_eff == 0` 발생 빈도 — 이 인스턴스군은 이미 obj=0이라 이 이웃이 손댈 것이
  없다. Phase A 집계에서 **분모에서 빼야 하는** 인스턴스다.
- 파괴 대상 job들의 $\sum f_j(C_j) \big/ \sum_j f_j(C_j)$ — **한 회 이동의 개선 상한 비율**.
- $\sum_j w^-_j E_j$ vs $\sum_j w^+_j T_j$ 비중 — earliness 편중인지 tardiness 편중인지.

---

## 6. 결정 사항 (Q1–Q9 전부 확정)

1. **Q1 (이름)**: `cj_cp`보다 긴 이름으로. **→ `job_contrib_cp`로 확정.**
2. **Q2 (파괴 규모 표기)**: **단일 문자열/정수 하나로 통일** (확정).
   `1` / `"2"` / `"0.05n"`. `sw_cp`의 `batch_size`와 완전히 같은 패턴.
   이름은 Q9 참조.
3. **Q3 (`pf_method=None`)**: **config 검사에서 `ValueError`로 거부** (확정).
   허용 안 함 — 이 이웃의 정체성은 profile-fix 기반이므로 None은 의미가 없다.
4. **Q4 (반복)**: **단발 스텝만** (확정). §2 고정점 문제로 반복은 정책 결정이
   선행되어야 하므로 파일럿 이후로 연기.
5. **Q5 (기여도 0인 job)**: **안 채움** (확정). 이웃을 더 좁고 싸게 유지.
   `jd_count_target`이 $\#\{j : f_j(C_j) > 0\}$보다 크면 `jd_count_eff`로 축소하고
   로그에 남김.
6. **Q6 (표기)**: 수식은 $f_j(C_j)$, 코드는 저장소에 이미 있는
   `obj_contrib` 용어 + `job_2_*_map` 관례를 따라
   `compute_job_2_obj_contrib_map` / `job_2_obj_contrib_map`. 새 기호 도입 없음.

7. **Q7 (`jd_count_eff == 0` 경로의 `obj_bound`)**: **`obj_bound = 0.0`** (확정).
   목적식이 비음수 합이므로 `0`은 항상 유효한 전역 하한이고, `obj_value == 0`인
   이 경로에서는 tight하다. `CLAUDE.md`의 최적성 판정 규약
   (`obj_value == obj_bound` ⟺ 최적)에서 이 인스턴스가 정확히 최적 증명됨으로
   기록된다. **이 스텝에서 `obj_bound`가 `None`이 아닌 유일한 경로**임을
   §1-(1)·§1-(5)에 함께 명시.
8. **Q8 (진단 자료의 형태)**: **처음부터 `AlgResult.metrics`** (확정).
   로그 포맷은 계약이 아니므로 grep에 의존하지 않는다. 상세는 §1-(6).
9. **Q9 (파괴 규모의 코드 이름)**: `k`는 수식에서 아무 의미로나 쓰이는 기호이므로
   **코드에서 쓰지 않는다** (확정). 층별로 세 이름을 분리한다 —
   config/컨트롤러 인자 **`jd_target`**, 정수 해석 후(옵션·디스패처)
   **`jd_count_target`**, 실제 파괴 수(metrics) **`jd_count_eff`**.
   수식 문맥에서 $k$ / $k'$는 무방. 상세는 §1-(1) 표기 규약.
   부수 결정: `jd_count_target >= 1`은 **`n` 없이 입력 검증만으로 보장**되므로
   하한 클램프 대신 `ValueError`로 거부한다 (§1-(1) 해석 규칙 말미).

---

## 7. 관련 문서

- 문제 정의: `docs/problem-description.md`
- 알고리즘 계약: `docs/algorithm-principles.md`
- subroutine step 계약: `src/ffc_ddw_sum_et/orchestration/AGENTS.md`
- profile fix 헬퍼: `src/ffc_ddw_sum_et/algorithm/cumulative.py` (`PFMethod`,
  `add_stage_ops_precedence_constraints_after_dispatch_from_schedule`)
- 같은 골격의 선례: `orchestration/controller.py:run_profile_fixed_ns`
- 재구성 순서 선례: `src/ffc_ddw_sum_et/algorithm/flip_makespan_cp/dispatcher.py`
- 부분 고정 이웃의 선례: `src/ffc_ddw_sum_et/algorithm/sw_cp/`
- 인스턴스 파라미터: `.claude/skills/pra2017-instance-params/SKILL.md`
