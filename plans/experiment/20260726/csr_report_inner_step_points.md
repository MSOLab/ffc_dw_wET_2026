# W1 — CSR 내부 단계 점을 method-mean scatter에 표시 (사전 작성, 코드 변경 계획)

**작성일**: 2026-07-26 · **종류**: 코드 변경 계획 (TDD) · **상태**: 미착수
**상위**: `plans/experiment/20260726/csr_init_roadmap.md` (W1)
**선행**: 없음 · **후속**: W2(`csr_init_tl_f35_f40.md`)가 이 변경 후의 차트를 쓴다

---

## 1. 문제

`coarsen_solve_reconstruct`는 **컨트롤러 스텝 하나**로 등록되므로
per-scenario method-mean scatter에서 **점 하나로 뭉개진다.** 실제 관측:

```
output/20260722_csr_b30_vs_a_v1_v2/20260722T090801_437425/
  b30_csr_k1_f30_batch_m/summary_method_mean_rpdf_and_mean_norm_time_scatter.html
→ payload.traces[0].method = ["coarsen_solve_reconstruct", "incremental_sw_cp", ...]
   x[0] = 0.2805  (CSR 전체가 x=0.28의 점 하나)
```

CSR 안에서는 `calc_mcf_lb_and_derive_full_sch → run_flip_makespan_cp_from_incumbent
→ neh_cp → incremental_sw_cp → solve_base_model_cpsat`가 순서대로 돌면서 incumbent를
개선하는데, **그 궤적이 차트에 전혀 보이지 않는다.**

**τ=1이면 coarsening이 항등이므로 그 내부 값들은 원본 스케일에서 그대로 유효하다** —
UB도 LB도. 그러므로 감출 이유가 없다.

## 2. 진단 — 데이터는 이미 있고, 라벨이 없어서 버려진다

**(a) 내부 궤적은 이미 부모 obj_log에 기록되고 있다.**
`controller.py:3041-3061`(`_coarsen_solve_reconstruct_via_flow`)이 candidate row마다
`ProgressLogEntry`를 만든다:

```python
elapsed_sec = child_offset + float(row["sec_elapsed_step"])   # 부모 시계로 재기준
obj_bound_val = None
if factor == 1 and row.get("coarse_bound") is not None:       # ← τ=1 게이트 이미 존재
    obj_bound_val = float(row["coarse_bound"])
progress_log_entries.append(
    ProgressLogEntry(elapsed_sec=elapsed_sec, obj_value=running_min_obj,
                     obj_bound=obj_bound_val)                 # note= 없음  ← 문제
)
```

실측 (`output/20260725_crossover_ladder/20260726T173841_347539/m1_k1_f01/
Instance_200_5_3_0,6_0,2_10_Rep2/..._obj_log.json`):

```json
obj_value.data  = {"1.6931541506201029": 613187.0, "1.75897772598546": 613187.0}
obj_value.notes = {"1.75897772598546": "1-coarsen_solve_reconstruct"}
obj_bound.data  = {"1.6931541506201029": 184525.0}
obj_bound.notes = {}
```

t=1.693의 두 점(내부 UB·내부 LB)이 **데이터에는 있는데 note가 없다.**

**(b) 구조화 로더가 note 없는 점을 버린다.**
`report/obj_log_loader.py:78`은 라벨이 `"<idx>-<subroutine_name>"` 형식이길 요구하고,
`method_mean_scatter.load_method_mean_metrics`는 `subroutine_name`으로 group by 한 뒤
그룹당 `global_end_sec` 최대점 하나만 남긴다. note 없는 점은 애초에 endpoint frame에
들어오지 못한다. (CLAUDE.md의 "structured loader drops LB points that carry no note"가
바로 이 현상이다.)

→ **결론: 새 데이터를 만들 필요가 없다. 이미 있는 점에 라벨과 마커를 주면 된다.**

## 3. 변경 사항

### C1 — 내부 progress 점에 라벨 부여 (필수)

`controller.py:3041-3061`에서 `ProgressLogEntry(note=...)`를 채운다.
**기존 명명 규약을 그대로 재사용**한다 — `incremental_sw_cp`가 배치별 점을
`incremental_sw_cp.<n>-batch_<id>`로 내는 것과 같은 `.` 접미 방식이며,
`method_mean_scatter.py:130`이 `full_name.split(".", 1)[0]`로 base name을 뽑으므로
**로더·차트의 파싱 코드는 손대지 않아도 된다.**

```
note = f"{step_idx}-coarsen_solve_reconstruct.inner-{k:02d}-{row['source']}"
```

`row["source"]`는 `csr_candidate_rows`의 기존 필드(`controller.py:3018`)로, 내부 승자
단계 이름(`1-calc_mcf_lb_and_derive_full_sch` 등)을 담고 있다. `k`는 candidate 순번
(dedup 후 시간순)으로, 같은 source가 여러 번 나와도 라벨이 충돌하지 않게 한다.

> ⚠ `step_idx` 접두는 부모 obj_log 규약을 따라야 한다. `_fold_history_into_obj_log_dicts`가
> 스텝 라벨을 어떻게 붙이는지 확인하고 **동일한 인덱스**를 쓸 것. 접두가 어긋나면
> 로더가 `ValueError`를 던진다(`obj_log_loader.py:78`).

### C2 — 마커 구분: 내부 = 십자가, 기존 = 동그라미 (필수)

`load_method_mean_metrics`가 돌려주는 point dict에 `is_inner: bool`을 추가하고,
`export_method_mean_scatter_html`이 그 플래그로 symbol을 고른다.

- `is_inner=False` → 지금과 동일 (`_chart_constants.symbol_map_json`의 기존 심볼, 동그라미 계열)
- `is_inner=True` → **십자가(cross)** 고정, 색은 부모 스텝과 동일 계열 유지

판정 기준은 라벨에 `".inner-"`가 있는지 하나뿐이다 (base name이 아니라 full label).

### C3 — τ=1에서 `SubroutineReport.obj_bound` 유효화 (권장, 별건 결정)

`controller.py:3068-3070`은 **무조건** `obj_bound=None`을 등록한다:

```python
report = SubroutineReport(
    elapsed_time=elapsed, obj_value=winner_obj,
    obj_bound=None,  # a coarse solve is never a valid original-scale LB
)
```

주석은 τ>1에서만 참이다. **τ=1이면 내부 LB는 원본 스케일의 유효한 global LB**이므로,
C1과 같은 논리로 여기도 `factor == 1`일 때 자식의 best LB를 실어야 한다.
legacy 경로(`controller.py:2821`)도 동일.

**파급 (반드시 확인)**: `<instance>_instance_result.yaml`의 `obj_bound`가 채워지면
CLAUDE.md가 정의한 최적성 판정(`obj_value == obj_bound`)이 CSR 런에도 적용된다.
이는 **의도된 개선**이지만 과거 런과의 비교 시 "before에는 bound가 없었다"는 점을
반드시 병기해야 한다. τ>1에서는 절대 채우지 말 것 — 잘못된 LB는 최적성 오판을 낳는다.

> **결정 필요**: C3를 W1에 포함할지, 별도 커밋으로 미룰지. **포함 권장** — C1과
> 근거가 동일(τ=1은 항등)하고, 반쪽만 고치면 "차트에는 LB가 보이는데 manifest에는
> 없는" 불일치가 남는다.

## 4. 대상 파일

| 파일 | 변경 |
|---|---|
| `src/ffc_ddw_sum_et/orchestration/controller.py` | C1 (3041-3061), C3 (3068-3070, 2821) |
| `src/ffc_ddw_sum_et/report/method_mean_scatter.py` | C2 — point dict에 `is_inner`, 반환 docstring 갱신 |
| `src/ffc_ddw_sum_et/report/_chart_constants.py` | C2 — cross 심볼 상수 |
| `tests/orchestration/test_csr_solve_flow.py` | C1·C3 테스트 |
| `tests/report/` (해당 모듈) | C2 테스트 |

## 5. 검증 (TDD — 각 테스트가 red를 거쳐야 함)

1. **C1**: τ=1 CSR 스텝의 `progress_log` 각 엔트리가 `note`를 갖고, 그 형식이
   `obj_log_loader`의 라벨 정규식을 통과한다. τ>1에서도 UB 점은 라벨된다
   (재구성된 `restored_obj`는 원본 스케일이므로 τ와 무관하게 유효 — **UB는 게이트하지 않는다**).
2. **C1(로더)**: 라벨된 obj_log를 `iter_scenario_instance_progressions`로 읽으면
   내부 점이 endpoint frame에 **살아남는다** (현재는 사라짐).
3. **C2**: `load_method_mean_metrics` 반환에 `is_inner=True` 점이 내부 단계 수만큼
   있고, 생성된 HTML payload에서 그 점들의 symbol이 cross다.
4. **C2(회귀)**: CSR이 없는 시나리오의 payload가 **변경 전과 바이트 동일**하다.
5. **C3**: `factor=1`이면 `SubroutineReport.obj_bound`가 자식 best LB, `factor>1`이면
   `None`.

**엔드투엔드 확인**: `uv run python scripts/build_single_instance_trace.py`로
τ=1 CSR 단일 인스턴스를 돌려 scatter HTML을 열고, CSR 구간에 십자가 점이 시간순으로
찍히는지 눈으로 확인한다.

**정리**: `uv run ruff check`, `uv run ruff format`.

## 6. 소급 적용 범위

**신규 런부터만 적용된다.** 과거 런의 obj_log에는 내부 점의 *값*은 있으나 note가 없고,
어느 점이 어느 내부 단계였는지 복원할 근거가 파일 안에 없다.
`scripts/build_subroutine_flow_charts.py`로 과거 런의 차트를 다시 그려도 십자가는
나오지 않는다 — 이 점을 W2 분석에서 "before/after 차트를 나란히 두지 말 것"으로 병기한다.

## 7. 산출물

- 커밋 (Conventional Commits): `feat(csr): label inner progress points` 등 C1/C2/C3를
  논리 단위로 분리
- 별도 실행 결과물 없음 (실험 아님). W2 런의 차트가 첫 실사용처.
