# PW-CP: log full E+T (not partial) in `ProgressLogEntry`

## 목표

`PwCpDispatcher.run`이 CP-SAT 콜백마다 기록하는 `ProgressLogEntry.obj_value`를
**전체 인스턴스의 가중 E+T**로 맞춘다. 현재는 `cp_value + et_offset_partial`로
기록되는데, `et_offset_partial`은 sub-instance(=`sub_jobs`)에 한정된 부분 오프셋
이라 "모든 stage에서 time-fixed인 job"들의 E+T가 누락된다.

## 배경 (왜 잘못됐나)

`obj_log.json` 분석 결과(`output/20260511/20260511T131401_488223/.../Rep0`):

- Manifest의 최종 `obj_value = 50,399`.
- 각 PW-CP 배치 구간 내부의 trajectory는 631, 13K, 18K 같은 값으로 떨어졌다가
  배치 종료(컨트롤러 `_register` 시점)에서 50,806 → 50,522 → 50,444 → 50,399으로
  복귀.
- 이 "내부 저점"이 `_build_best_so_far_progression_points`의 running-min에 흡수
  되어 multi-scenario flow 차트의 Mean RPDf가 -128% / -164%로 무너짐
  (`rpd_f = 2(obj-ref)/(obj+ref)`는 obj≪ref일 때 -2로 포화).

`dispatcher.py:204-213`:

```python
for t_rec, vb in value_recorder.entries:
    progress_entries.append(
        ProgressLogEntry(
            elapsed_sec=t_rec + offset_sec,
            obj_value=float(vb.value)
            + float(build_result.et_offset_partial),
            obj_bound=None,
        )
    )
```

`et_offset_partial` 정의 (`cp_model.py:142-201`)는 `sub_params.j_list`만 순회.
`sub_params.j_list` ≡ `sub_jobs = {j | ∃stage s.t. (j,*) ∈ partition[stage].non_time_fixed}`.
모든 stage에서 time-fixed인 job은 sub_jobs에 없으므로 그들의 E+T 기여분이 통째로
누락. CP 콜백 시점의 진짜 full obj와의 격차가 이 누락 분량과 정확히 일치.

## 범위

- 변경 파일
  - `src/ffc_ddw_sum_et/algorithm/sw_cp/dispatcher.py` — 콜백 루프 직전에
    `full_offset` 계산, 루프 안에서 `vb.value + full_offset` 기록.
- 변경하지 않는 것
  - `PwCpBuildResult.et_offset_partial`은 그대로 둔다. 진단/스텝 로그용으로 의미
    있는 양이고, 다른 호출자가 없으니 시그니처 변경의 가치도 없음.
  - `_define_partial_et_objective`도 그대로 — CP 모델의 목적 자체는 옳다.
  - 차트 코드(`multi_scenario_method_chart.py`)와 차트 사이즈 이슈는 별도 작업.

## 설계

콜백이 보고하는 CP 목적값 `vb.value`는
`Σ_{j ∈ objective_jobs} (w_e·E_j + w_t·T_j)`. 여기에 더해야 할 상수 오프셋은
"objective_jobs를 제외한 모든 job의 (rj_schedule 기반) 가중 E+T"이며, 이는

```
full_offset = full_obj(rj_schedule)
            − Σ_{j ∈ objective_jobs} (w_e[j]·E_j(C_j^{rj}) + w_t[j]·T_j(C_j^{rj}))
```

로 계산한다. `_full_obj`는 이미 dispatcher에 있는 static helper.
`objective_jobs`는 `build_result.objective_jobs`로 노출되어 있다.
`C_j^{rj}`는 `rj_schedule.get_job_end_time(last_i, j)`.

### dispatcher.py 패치 위치

`build_result = builder.build(...)` 직후, `value_recorder` 루프 직전:

```python
full_offset = self._compute_full_progress_offset(
    instance, rj_schedule, build_result.objective_jobs
)
```

그리고 콜백 루프:

```python
for t_rec, vb in value_recorder.entries:
    progress_entries.append(
        ProgressLogEntry(
            elapsed_sec=t_rec + offset_sec,
            obj_value=float(vb.value) + full_offset,
            obj_bound=None,
        )
    )
```

새 helper:

```python
@staticmethod
def _compute_full_progress_offset(
    instance: FFcDDWParameters,
    rj_schedule: FFcSchedule,
    objective_jobs: tuple[str, ...],
) -> float:
    full = PwCpDispatcher._full_obj(rj_schedule, instance)
    last_i = instance.stage_id_list[-1]
    dw = instance.job_2_due_window_map
    ewt = instance.job_2_ewt_map
    twt = instance.job_2_twt_map
    cp_side = 0.0
    for j in objective_jobs:
        c_j = rj_schedule.get_job_end_time(last_i, j)
        d_lower, d_upper = dw[j]
        cp_side += ewt[j] * max(0, d_lower - c_j) + twt[j] * max(0, c_j - d_upper)
    return float(full - cp_side)
```

`ewt`/`twt`/`dw`는 프로젝트 규칙상 `m[j]`로 직접 인덱싱 (defensive `.get()` 금지).

## 정합성 체크

- `rj_schedule`는 `incumbent.deepcopy()` 후 `delay_job_latest_leq_obj_contrib_all_stages`
  로 우정렬된 사본. `delay_*` 메서드 이름이 "leq_obj_contrib"인 점에서 보장되듯
  E+T 기여를 보존하므로 `_full_obj(rj_schedule, instance) == _full_obj(incumbent, instance)`.
  따라서 `full_offset`은 신뢰 가능.
- `objective_jobs`는 last stage가 non-time-fixed인 job만 포함. 이들의
  `C_j^{rj} = rj_schedule.get_job_end_time(last_i, j)`는 CP 모델이 `op_end[j,last_i]`
  로 변동시키는 값과 동일 변수. CP 콜백 `vb.value`에 들어가는 항과 일치.
- `objective_jobs` ∉ "fully time-fixed jobs" 이므로 빼는 항과 빠뜨린 항이 disjoint.
- `vb.value + full_offset` 의 단위는 가중 E+T (정수 weights × 정수 시간), 매니페스트의
  `obj_value`와 단위 일치.

## 검증

- 기존 `tests/algorithm/sw_cp/` 회귀가 깨지지 않는지 `uv run pytest tests/algorithm/sw_cp -q`.
- 한 인스턴스에 대해 `scripts/build_subroutine_flow_charts.py`(혹은 동등 경로)로
  차트 재생성 후, 배치 구간 trajectory 값이 매니페스트 최종 obj_value 부근으로
  수렴하는지 한 번만 눈으로 확인. (사용자가 본인 환경에서 재실행 예정.)
- `uv run ruff check` / `uv run ruff format`.

## 비범위 (별도 작업)

- 차트 사이즈 문제(100MB): `_keep_strict_global_improvements_or_endpoints`를
  multi-scenario 경로에도 적용 + `step_customdata` 중복 제거. 사용자가 요청 시 별도
  진행.
