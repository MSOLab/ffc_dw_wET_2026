# flow comparison 차트 — back-fill 제거 + mean trace 시작점 마커 (사전 작성, 코드 변경 계획)

**작성일**: 2026-07-29 · **종류**: 코드 변경 계획 (TDD) · **상태**: 계획 (C2 개정)
**선행**: `28f5ff5` (2026-07-27, "fix(report): fix CSR inner flow curve and labels")
· **후속**: 없음 (실험 아님, 리포트 렌더링 변경)

> **개정 (2026-07-29, 1차 구현 후)**: C2의 마커 밀도를 "모든 mean 샘플점" →
> **"시리즈 시작점 1개"**로 바꾼다. 근거는 §C2 개정 근거 참조.

---

## 1. 문제

`<run>_multi_scenario_subroutine_flow_comparison.html`에서
`b30_csr_k1_f30_batch_m_plus_2` 시나리오의 곡선이 **(0.00%, −5.110%)**에서 시작한다.
같은 런의 `<run>_multi_scenario_method_mean_rpdf_and_mean_norm_time_scatter.html`
에서 CSR 종료점의 평균은 **(28.078%, −5.110%)**다. 즉 flow 차트는 CSR이 평균
28.078%의 정규화 시간에 도달한 시점의 −5.110% mean RPDf를 **t=0으로 잘라 내렸다.**

mean 시리즈의 시작점은 "1440개 모든 instance가 첫 valid schedule을 가진 순간"
(`max(first_times)`)이어야 한다 — `np_utils.py:34`의 docstring이 그렇게 서술한다.
그러나 28f5ff5가 넣은 back-fill이 모든 instance trajectory를 `t=0`으로 강제
시작시켜 이 `max(first_times)`를 항상 0으로 만든다.

## 2. 진단 — 28f5ff5의 back-fill이 `max(first_times)` 의미를 무력화했다

`trajectory_utils.py:53-54` (28f5ff5 추가):

```python
if points and points[0].time > 0.0:
    points.insert(0, ProgressionPoint(time=0.0, rpd_f=points[0].rpd_f))
```

이 줄이 없던 시절엔 `step_function_mean_over_union`(`np_utils.py:34`)의
`start_time = max(first_times)`가 각 instance의 **첫 관측 시각**을 반영했다.
28f5ff5 이후 모든 instance의 `times[0]`가 0이 되어 `start_time`이 의미를 잃었고,
첫 관측(예: CSR의 finished 시점)의 평균 RPDf가 `t=0` 좌표로 덮어쓰인다.

### 28f5ff5가 back-fill을 넣은 원래 이유

`tests/report/test_trajectory_utils.py` 모듈 docstring이 명시하는 원 버그:

> 어떤 instance가 stop time(`norm_time == 1.0`)에서 단 하나의 progression
> point만 기여하면, `step_function_mean_over_union`의 sample grid가
> `[start_time, end_time] = [1.0, 1.0]`으로 붕괴해 **전체 mean 시리즈가
> 1개 점으로** 줄어든다. Plotly의 `mode="lines"`는 1점 trace를 그리지 않아
> 차트에 선이 안 보였다.

즉 "단일 샘플 → 보이지 않는 선"을 고치려고 trajectory를 임의로 `t=0`까지
늘린 것인데, 이것이 시작점의 의미("모든 instance 준비됨")를 훼손했다.

### 핵심 — "단일 샘플 붕괴"는 시각화 레이어 문제, 시계열 값은 아니다

단일 샘플 상황에서 곡선이 사라지는 것은 chart 렌더링(`mode="lines"`가
1점에서 아무것도 안 그림)의 문제다. 시계열 데이터 자체는 올바르게 1점을
반환하고 있다. 따라서 고치는 자리도 시각화 레이어다.

## 3. 변경 사항

### C1 — back-fill 제거 (필수)

`src/ffc_ddw_sum_et/report/trajectory_utils.py:52-54`를 제거해 원래대로
`return _dedupe_progression_points(points)`로 되돌린다. 이로써
`np_utils.py:34`의 `start_time = max(first_times)`가 "모든 instance가
첫 valid schedule을 가진 순간" 의미를 되찾는다.

영향받는 chart:
- `multi_scenario_method_chart.py` (run-level flow 비교) — **target**.
- `rpdf_scatter_chart.py` (per-scenario (T,R) 평균) — 같은 도구를 공유하므로
  부수 영향. per-scenario에서 CSR-inner退化 instance가 (T,R) 그룹 곡선을
  단일 점으로 붕괴시킬 수 있다 — 이 경우 C2 마커로 점은 보이지만 N−1
  곡선 손실은 남는다. 별건(option 3, NaN-aware)이 아니면 이번에 손대지 않는다.

### C2 — mean 시리즈 **시작점**에 open-circle 마커 (필수, 개정됨)

`mode="lines"`만으로는 단일 샘플일 때 아무것도 안 보인다. mean 시리즈의
**첫 샘플점 1개**에만 **빈 동그라미(open circle) 마커 trace**를 얹는다.
이 점은 `max(first_times)` — "모든 instance가 첫 valid schedule을 가진
순간" — 이라는 고유한 의미를 가지며, C1이 복원하려는 바로 그 값이다.

- **정상 시나리오**: 곡선 시작점에 원 하나. 선을 가리지 않으면서 "여기부터
  전원 준비 완료"를 눈으로 짚어준다.
- **붕괴 시나리오**(단일 샘플): 그 1점이 곧 시작점이므로 마커가 그대로
  찍힌다 → line이 안 그려져도 위치를 참조할 수 있다.

#### C2 개정 근거 — 전 구간 마커는 왜 틀렸나

1차 구현은 decimate 후의 `mean_x`/`mean_y` **전체**를 마커로 찍었다. 실제
런(`output/20260728_init_budget_merge/20260729T041116_435991`)에서 시나리오당
3,298 ~ 7,901개의 빈 원이 찍혀 **선 자체를 덮었다**. 마커의 목적(단일 샘플일
때 참조점 제공)에 필요한 건 1개인데 수천 개를 그린 셈이다.

또한 전 구간 마커는 원래 목표였던 csr_inner 붕괴 케이스를 **구제하지도
못한다**: 붕괴 시 남는 유일한 점의 좌표가 `(1.0, y_min)`인데 축 범위가
`x:[0, x_max=1.0]`, `y:[y_min, ...]`로 그 점 자신에 맞춰 잡혀(`_x_axis_upper`
/ `_y_axis_lower`) 마커가 플롯 오른쪽-아래 꼭짓점에 반쯤 잘려 사실상 보이지
않는다. 붕괴의 근본 해결은 §8의 option 3이며 이번 범위 밖이다 — 마커는
"보조 참조점"이라는 제 몫만 한다.

#### C2-1 — `multi_scenario_method_chart.py`

`_build_scenario_mean_series`가 반환하는 dict에 `start_marker_x`/
`start_marker_y`(= `mean_x[:1]`/`mean_y[:1]`)를 추가한다. HTML 템플릿의
`traces.flatMap(...)`에서 시나리오마다 ① line(`step_x`/`step_y`) ② guide
marker(y=0, 기존) ③ **신규** start marker(`mode:"markers"`,
`marker:{symbol:"circle-open", size:9, color:seriesColor}`,
`showlegend:false`)를 내보낸다. hover는 실제 RPDf y 값을 갖는 별도
template을 쓴다(guide marker는 y=0이라 공유 불가).

축 범위 계산(`_build_payload`의 `all_x`/`all_y`)에는 마커를 더하지 않는다 —
시작점은 `step_x`/`step_y`의 부분집합이라 이미 반영돼 있다.

#### C2-2 — `rpdf_scatter_chart.py`

mean 모드 분기(`modeVal === "mean"`)에 동일한 시작점 마커 trace를 추가한다.
payload의 `mean_series[*]`는 이미 `x`/`y`/`customdata`를 가지므로 템플릿에서
`.slice(0, 1)`로 첫 점만 취한다(별도 payload 필드 불필요). raw 모드는 이미
관측 마커를 가지므로 그대로 둔다.

### C3 — back-fill 전용 테스트 갱신 (필수)

`tests/report/test_trajectory_utils.py`의 모듈 docstring과 계약을 새로
맞춘다:

- `test_trajectory_starting_after_zero_gets_synthetic_origin_point` →
  **반전**: `[(0.4,0.9),(0.8,0.5)]` → `[(0.4,0.9),(0.8,0.5)]` (synthetic point 없음).
- `test_single_point_trajectory_spans_zero_to_one` → **제거** (1점은 1점).
- `test_trajectory_already_starting_at_zero_is_untouched`,
  `test_empty_group_stays_empty` → 유지.
- `test_mean_series_is_drawable_despite_a_single_point_instance` →
  back-fill이 사라지면 mean 시리즈가 다시 1점으로 붕괴하므로 **계약 갱신**:
  `step_function_mean_over_union`이 `len == 1`을 반환함을 단언하고, 마커
  표시 검증은 chart 레벨 테스트로 이동.

### C4 — chart 레벨 테스트 추가 (필수, 개정됨)

`tests/report/test_post_run_chart_writer.py`(flow chart payload를 실제
export 경로로 만들어 보는 기존 테스트 파일)에 추가한다:

- 정상(다중 샘플) 시나리오: `payload["traces"][i]["start_marker_x"]`의 길이가
  **1**이고 값이 `step_x[0]`(= mean 시리즈 첫 샘플)과 같다.
- 단일 샘플 시나리오: `start_marker_x`가 길이 1로 살아남고 `x == 1.0`
  (line `step_x`도 `build_step_path` 특성상 길이 1).
- 시작점이 `max(first_times)`임을 확인하는 C1 통합 테스트(기존 유지).

## 4. 대상 파일

| 파일 | 변경 |
|---|---|
| `src/ffc_ddw_sum_et/report/trajectory_utils.py` | C1 — back-fill(52-54) 제거 |
| `src/ffc_ddw_sum_et/report/multi_scenario_method_chart.py` | C2-1 — `_build_scenario_mean_series` 반환에 `start_marker_x`/`start_marker_y`, HTML 템플릿에 시작점 마커 trace |
| `src/ffc_ddw_sum_et/report/rpdf_scatter_chart.py` | C2-2 — mean 모드에 시작점 open-circle 마커 trace 추가 |
| `tests/report/test_trajectory_utils.py` | C3 — 계약 반전/제거 및 docstring 갱신 |
| `tests/report/test_post_run_chart_writer.py` | C4 — 단일/다중 샘플 시작점 마커 통합 테스트 |
| `tests/report/test_rpdf_scatter_chart.py` | C2-2 회귀 — mean 모드 시작점 마커 trace 존재 단언 |

`np_utils.py`는 변경하지 않는다 — `step_function_mean_over_union`의
`max(first_times)`/`max(last_times)` 그대로.

## 5. 검증 (TDD — 각 테스트가 red를 거쳐야 함)

**C1 단위** (`test_trajectory_utils.py`):
1. `[(0.4, 0.9), (0.8, 0.5)]` 입력 → `(time, rpd_f)` 쌍이 입력 그대로
   (synthetic t=0 점 삽입 없음).
2. 빈 DataFrame → 빈 리스트.

**C1 회귀** (`test_trajectory_utils.py` + `test_np_utils.py`):
3. 단일 instance `[(1.0, 0.75)]` → `step_function_mean_over_union` 반환
   `len == 1`, `mean_x == [1.0]`, `mean_y == [0.75]` (이전에는 `[0.0, 1.0]`).

**C2 chart** (`test_post_run_chart_writer.py`):
4. 정상 시나리오: 생성된 HTML/JSON payload에서 `start_marker_x`/
   `start_marker_y`가 **길이 1**이고 `step_x[0]`/`step_y[0]`과 일치한다.
5. 단일 샘플 시나리오: `start_marker_x`가 길이 1로 살아남는다
   (line `step_x`/`step_y`는 `build_step_path` 특성상 길이 1).
6. HTML 템플릿에서 시작점 마커 trace의 symbol이 `"circle-open"`이고
   `showlegend: false`.

**C2-2 scatter** (`test_rpdf_scatter_chart.py`):
7. mean 모드 템플릿 분기에 `.slice(0, 1)`을 쓰는 open-circle 마커 trace가
   존재. 회귀: raw 모드는 영향 없음.

**엔드투엔드**:
```sh
uv run python scripts/build_subroutine_flow_charts.py \
  output/20260728_init_budget_merge/20260729T041116_435991
```
- 브라우저로 열어 (a) `b30_csr_k1_f30_batch_m_plus_2` 곡선의 시작점이
  0.00%가 아니라 ~30% 부근에서 시작하는지(1차 구현에서 확인: 0.3024),
  (b) 각 곡선의 시작점에만 빈 원이 하나씩 찍히고 선을 덮지 않는지 확인.

**정리**: `uv run ruff check`, `uv run ruff format`.
**회귀**: `uv run pytest tests/report -q`.

## 6. 소급 적용 범위

**과거 런에 그대로 소급된다.** back-fill 제거와 마커 추가는 모두
렌더링 단계라 `scripts/build_subroutine_flow_charts.py`로 기존 런의
HTML을 다시 뽑으면 새 규약을 따른다. obj_log 자체는 건드리지 않는다.

## 7. 산출물

- 커밋(Conventional Commits, 논리 단위 2개):
  - `fix(report): drop t=0 back-fill from progression points` — C1 + C3
    (back-fill 제거와 그에 따른 단위 테스트 계약 갱신은 한 단위).
  - `feat(report): mark mean flow trace start point` — C2 + C4
    (마커 추가와 chart 테스트는 green이 되려면 함께).
  - C1을 먼저 커밋 — C2 없이도 기존 테스트(C3 제외)는 green이지만
    붕괴 시 line이 안 보이는 렌더링 결함이 잠시 드러나므로 길게 띄우지 않는다.
- 별도 실행 결과물 없음(실험 아님). 재생성 결과는 기존 run 디렉터리를
  덮어쓴다.

## 8. 위험 / 별건 (이번 범위 밖)

- **csr_inner 차트의 붕괴는 이번 범위에서 해결되지 않는다.** 실측:
  `b30_csr_k1_f30_batch_m`의 1440개 중 **14개** instance가 csr_inner
  obj point를 1개만 가진다(예: `Instance_50_5_3_0,2_1_20_Rep1`).
  inner 정규화(`_compute_inner_budget_per_instance`)가 instance별 max
  `global_sec`로 나누므로 그 14개의 첫 관측 시각이 정확히 `1.0`이 되고,
  `max(first_times) = 1.0 = end_time` → 시나리오 전체 mean이 1샘플로 붕괴한다.
  C2 시작점 마커는 그 1점을 찍지만 좌표가 축 꼭짓점 `(1.0, y_min)`이라
  반쯤 잘려 보인다. 근본 해결은 option 3(NaN-aware mean, ramp-up 표시 +
  "전원 준비" 가이드 마커)이며 이번에는 **별건으로 미룸**.
- **per-scenario `rpdf_scatter_chart`의 붕괴 손실**: 같은 이유로 CSR-inner
  退化 instance가 속한 (T,R) 그룹의 평균 곡선도 단일 점으로 붕괴할 수 있다.
  run-level `multi_scenario` 차트에서는 MCF-LB가 빠르게 첫 관측을
  만들어 退化가 거의 없어 영향 미미.
- **`b30_csr_k1_f30_batch_m_plus_2`에는 csr_inner obj log가 0개**라 해당
  차트에 트레이스가 1개만 뜬다. 그 로그를 남기지 않던 이전 런을 merge한
  디렉터리 탓이며 이번 변경과 무관한 기존 상태다.