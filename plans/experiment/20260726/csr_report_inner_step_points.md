# W1 — CSR 내부 단계 점을 method-mean scatter에 표시 (사전 작성, 코드 변경 계획)

**작성일**: 2026-07-26 · **종류**: 코드 변경 계획 (TDD) · **상태**: 완료
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

---

## 8. 구현 결과 보고 (2026-07-26)

### 변경 파일

| 파일 | 변경 |
|---|---|
| `controller.py:3060-3089` | C1: progress_log에 `note` 부여 (`{context}.inner-{entry_idx:02d}-{source}`) |
| `controller.py:127-142` | `_best_valid_lb(bounds)` 모듈 헬퍼 — 유효 LB 중 **가장 tight한(= max)** 값 반환, 없으면 `None` |
| `controller.py:2839-2841` | C3(legacy): τ=1일 때 `trace.cp_progress_log`에서 `_best_valid_lb`로 best LB 추출 |
| `controller.py:3091-3098` | C3(solve_flow): τ=1일 때 `csr_child_history`에서 `_best_valid_lb`로 best LB 추출, `SubroutineReport.obj_bound`(3105)와 `FFcDDWSolution.obj_bound`(3113)에 설정 |
| `ffcddw_single_instance_runner.py:125-131` | C1: progress_log entry의 note를 obj_log의 `value_notes`/`bound_notes`로 전파 (`setdefault`) |
| `method_mean_scatter.py` | C2: `load_method_mean_metrics` 반환값에 `is_inner: bool` 추가 (`.inner-` 포함 여부 판정), `_build_payload`에 `is_inner` 배열 전달, HTML template에서 `is_inner=True` 점에 `"cross"` 심볼 고정 |

### 계획 대비 차이

- **note의 인덱스는 `candidate_rows`의 인덱스가 아니라 *실제로 emit된 entry*의 인덱스다.**
  §3 의사코드는 `enumerate(candidate_rows)`의 `k`를 썼으나, `restored_obj is None`인 행은
  `continue`로 건너뛰므로 그 `k`를 쓰면 라벨 번호에 구멍이 생긴다(`inner-00`, `inner-02`, …).
  별도 `entry_idx` 카운터로 교체해 연속 번호를 보장한다.
- **`source == "unknown"`인 행에는 note를 달지 않는다.** 라벨에 담을 의미 있는 단계명이
  없어 십자가만 뜨고 hover가 무의미해지기 때문. 해당 점은 일반 마커로 남는다.
- **커밋은 §7의 C1/C2/C3 분리 대신 단일 커밋**(`feat(csr): label inner points, emit τ=1 LB`).
  C2의 `is_inner`는 C1이 note를 달아야만 의미가 생기고, 검증 config·테스트가 셋을 함께
  건드려 논리 단위로 쪼개면 중간 커밋이 green이 아니게 된다.

### 테스트 (+7 tests)

| 파일 | 테스트 |
|---|---|
| `test_csr_solve_flow.py` | `test_solve_flow_progress_log_has_notes_at_factor_1` — note가 최소 1개 존재하고 `.inner-NN-` 패턴을 만족 |
| | `test_solve_flow_progress_log_notes_in_obj_log` — parent history를 `_fold_history_into_obj_log_dicts`에 통과시켜 `value_notes`/`bound_notes` 양쪽에 `.inner-` note가 실리는지 검증 |
| | `test_solve_flow_report_obj_bound_at_factor_1` — τ=1에서 report.obj_bound ≠ None **이고 `max(child LBs)`와 일치**, τ=2에서 None |
| | `test_best_valid_lb_picks_the_largest_bound` — `_best_valid_lb`가 max로 축약, `None` 스킵, 빈 입력 → `None`, int → float 정규화 |
| `test_method_mean_scatter.py` | `test_inner_points_have_is_inner_true` — `.inner-` 라벨 점에 is_inner=True |
| | `test_regular_points_have_is_inner_false` — 일반 flow의 모든 점에 is_inner=False (회귀) |
| | `test_batch_inner_mixed_regression` — inner 점 2개 + regular 점 3개 혼합 검증 |

### 검증 결과

- **ruff check**: clean
- **pytest**: 669 tests pass
- **단일 인스턴스 실험** (`insIndex=0`, τ=1, f ∈ {1,5,10,40}%),
  config: `metadata/20260726/w1_csr_single_instance_verify.yaml`:

| 시나리오 | inner 점 수 | 첫 점 obj | winner |
|----------|------------|-----------|--------|
| f01 | 1 | 10611 | calc_mcf_lb (r1 only) |
| f05 | 4 | 10174 | isw_cp |
| f10 | 4 | 10174 | isw_cp |
| f40 | 4 | 10174 | isw_cp |

- C1: 모든 시나리오에서 obj_log에 `.inner-{NN}-{source}` 형식 note 기록 확인
- C2: inner 점 `is_inner=True`, CSR endpoint `is_inner=False`, HTML에서 cross symbol 선택
- C3: τ=1에서 `instance_result.yaml`의 `obj_bound: 7261.0` 확인 (이전엔 항상 `null`)
  — **단, 이 값은 아래 "LB 집계 방향 수정" 이전(`min` 집계) 런의 결과다.** `max`로
  바뀐 뒤에는 child 중 MCF-LB보다 tight한 bound가 있으면 값이 올라간다. 재측정은
  W2 런에 묻어가며, 그때까지 이 수치는 "min 기준"으로만 읽을 것.

### 리뷰 반영: LB 집계 방향 수정

C3의 최초 구현은 두 경로 모두 후보 bound를 **`min`**으로 축약했다. LB는 클수록 tight
하므로 이는 "best LB"가 아니라 **가장 느슨한** bound를 고르는 것이고, 코드베이스의 다른
두 집계 지점과도 어긋난다:

| 지점 | 집계 |
|---|---|
| `solution_manager.py:46` `_a_is_better_obj_bound` | `bound_a > bound_b` (max) |
| `ffcddw_single_instance_runner.py:441` `bestBound` | `max(bound_values)` |

특히 legacy 경로가 심각했다 — `trace.cp_progress_log`는 CP-SAT best-bound 콜백
시계열(`progress_log_builder.py:41-50`)이라 0 근처에서 시작해 증가하므로, `min`은
사실상 **최초 bound(≈0)**를 집어온다. 결과적으로 CLAUDE.md의 최적성 판정
(`obj_value == obj_bound`)에서 false negative가 난다 — sound하지만(과대주장 없음)
증명된 최적해를 non-optimal로 보고하게 된다.

두 경로의 축약 로직이 동일하므로 `_best_valid_lb` 헬퍼(`controller.py:127-142`)로
빼고 `max`로 교정했다. 헬퍼 docstring에 "호출자가 유효성을 게이팅한다"는
`_a_is_better_obj_bound`의 soundness 계약을 명시해 두었다.

`test_best_valid_lb_picks_the_largest_bound`는 헬퍼를 `min`으로 되돌린 상태에서 실패함을
확인했다 (`assert 0.0 == 10174.0`). 반면 `test_solve_flow_report_obj_bound_at_factor_1`의
`max(child_bounds)` 단언은 그 픽스처의 child bound가 1개뿐이라 `min == max`가 되어
red가 되지 않는다 — 방향을 실제로 고정하는 것은 유닛 테스트 쪽이다.

### 발견: f01 첫 점 RPDf가 다른 이유

f01의 첫 inner 점 obj=10611이 f05/f10/f40의 10174와 다른 것은 W1 변경과 무관하며, `calc_mcf_lb_and_derive_full_sch`의 **adjust round(r2) 스킵** 때문이다.

`calc_mcf_lb_and_derive_full_sch`은 `adjust_p/r=True`일 때 r2에서 last-stage processing time을 조정해 더 나은 schedule을 만든다. r1 → r2 사이에 `stop_predicate` 체크(`mcf_lb_pipeline.py:746`)가 있어, child_timelimit이 극단적으로 짧은 f01(0.225s)에서는 r1 완료 직후 stop이 걸려 r2가 스킵된다:

```
f01 (child_tl=0.225s):  r1 obj=10999  →  r2 skipped (stop_guard)
f05/f10/f40:            r1 obj=10999  →  r2 obj=10286 (개선)
```

자식 컨트롤러가 r1 schedule(f01)과 r2 schedule(f05/f10/f40)을 각각 등록하고, parent reconstruction도 당연히 다른 schedule에서 다른 obj를 복원한다. MCF-LB bound(7261)은 r1/r2와 무관하게 모든 run에서 일관되다. 이는 당초 계획 범위 밖이며, f05/10/40이 서로 일관되므로 검증 기준은 이 셋을 기준으로 한다.
