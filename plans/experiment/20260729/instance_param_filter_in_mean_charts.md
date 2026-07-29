# mean 계열 차트에 instance 생성 파라미터 필터 추가 (사전 작성, 코드 변경 계획)

**작성일**: 2026-07-29 · **종류**: 코드 변경 계획 (TDD) · **상태**: 계획
**선행**: `8306f72` (2026-07-29, "feat(report): mark mean flow trace start point")
· **후속**: 없음 (실험 아님, 리포트 렌더링 변경)

---

## 1. 문제

`rpdf_scatter_chart.export_method_rpdf_scatter_html`(per-scenario scatter)에만
(T, R) 필터 드롭다운이 있고, 아래 세 산출물에는 없다:

| 산출물 | writer | 현재 크기 |
|---|---|---|
| `<run>_multi_scenario_method_mean_rpdf_and_mean_norm_time_scatter.html` | `method_mean_scatter.py` | 10.8 KB |
| `<scenario>/summary_method_mean_rpdf_and_mean_norm_time_scatter.html` | 같은 writer (1-시나리오 호출) | 4.2 KB |
| `<run>_multi_scenario_subroutine_flow_comparison.html` | `multi_scenario_method_chart.py` | 1.16 MB |

같은 writer를 쓰는 `<run>_csr_inner_flow_comparison.html`도 자동으로 대상이 된다.

필터를 순진하게 붙이면(=instance별 개별 시계열을 payload에 담고 JS에서
필터링) 크기가 instance 수에 비례해 폭발한다. 실측 근거가 이미 런 안에 있다:
필터 기능이 있는 `<scenario>/summary_method_rpdf_and_norm_time_scatter.html`이
**시나리오당 ~300 MB(런 전체 2.2 GB)**이며, 그 대부분이 raw 모드의 instance
1,440개 개별 시계열 + 마커마다 4개 문자열 `customdata`다.

## 2. 설계 결정 (확정)

| 항목 | 값 | 근거 |
|---|---|---|
| 필터 차원 | **T, R, n, c** | 사용자 확정. `pra2017_instance_table.csv`의 `T`(3)·`R`(3)·`n`(4)·`c`(2) |
| 셀 수 | **72** | 3×3×4×2. 전체 격자에서 **모든 셀이 정확히 20 instance** (실측, 빈 셀·불균형 없음) |
| 저장 단위 | 셀별 집계 시계열 + instance 수 | instance별 시계열 저장 금지 |
| flow 차트 셀 해상도 | 셀당 **M=200** breakpoint | §5 크기 예산 |
| flow 차트 All 해상도 | **N=2000** | 사용자 확정. 기존 cap 10,000(실측 유효값 ~4,600)에서 축소 |
| raw 모드 gating | **하지 않음** | 사용자 확정 — 개별 instance 결과를 봐야 할 때가 있음. `rpdf_scatter_chart.py`는 이번 범위 밖 |

### 2.1 왜 가중평균 재조합이 정확한가

두 차트가 그리는 값은 모두 **instance에 대한 단순 산술평균**이다.
셀 `g`의 평균을 `μ_g`, 원소 수를 `n_g`라 하면 선택 집합 `S`에 대해

```
μ_S = Σ_{g∈S} n_g·μ_g / Σ_{g∈S} n_g
```

가 정확히 성립한다(근사가 아님). flow 차트의 계단함수도 시각 `t`마다 같은
식이 성립하므로, 셀별 계단함수를 JS에서 병합하며 가중합하면 된다.

경계값도 정확히 보존된다. `step_function_mean_over_union`의
`start_time = max(first_times)` / `end_time = max(last_times)`는 모두 max이고,
`max_{i∈S} = max_{g∈S} max_{i∈g}`이므로 **셀별 시작/끝의 max = 선택 집합
전체의 시작/끝**이다.

유일한 오차원은 **셀별 decimation**(M=200)이며, 오차는 셀당 quantum
(= 그 셀 y-range / 200) 이하로 유계다. 72셀 가중평균에서는 셀 오차가
평균되어 더 줄어든다.

### 2.2 왜 dense 공유 격자가 아니라 sparse 셀 시계열인가

72셀 × N=2000을 **조밀 벡터**로 저장하면 시나리오당
`(1 + 72) × 2000 × ~9 B ≈ 1.3 MB` → 7시나리오 **~9.2 MB**다.
셀별 계단함수는 변화점이 M개뿐인 sparse 객체이므로, 변화점만 `(x, y)` 쌍으로
저장하면 `72 × 200 × 2 × 9 B ≈ 259 KB/시나리오`로 **5배 작으면서 셀 해상도는
오히려 높다**. 병합은 JS k-way merge(포인터 스윕) 한 함수로 끝난다.

### 2.3 "All" 뷰는 재조합하지 않는다

기본 뷰(모든 드롭다운 = All)는 **기존과 동일한 전체-instance 평균 시계열**을
그대로 저장해 쓴다(단 decimation cap만 10,000 → N=2000). 비용은
`2000 × 2 × 9 B ≈ 36 KB/시나리오`뿐이고, 대신 가장 많이 보는 뷰가 과거
분석 문서가 인용한 곡선과 수치적으로 어긋나지 않는다. 필터가 걸린 뷰만
셀 재조합을 쓴다.

`method_mean_scatter`도 같은 원칙 — 기존 top-level 집계 필드를 그대로 두고
`cells`를 **추가**한다(하위 호환 + All 뷰 정확).

## 3. 변경 사항

### C0 — instance 셀 키 공통 모듈 (신규)

`src/ffc_ddw_sum_et/report/instance_cells.py` 신설. 두 차트가 같은 셀 정의를
공유해야 하므로 단일 소스로 둔다.

```python
CELL_DIMS = ("t_factor", "r_factor", "job_cnt", "stage_cnt")  # 표시 라벨: T, R, n, c

def cell_key_by_instance(baseline_df) -> dict[str, tuple[str, str, str, str]]
def cell_dim_values(baseline_df) -> dict[str, list[str]]   # 드롭다운 옵션(숫자 정렬)
def format_cell_value(dim, value) -> str                   # T/R은 1자리 소수, n/c는 정수
```

- 4개 차원 중 하나라도 NaN인 instance는 셀 없음 → 셀 payload에서 제외하되,
  All 시계열에는 그대로 포함한다(기존 동작 유지). 이 불일치가 발생하면
  `logger.warning`으로 개수를 남긴다.

### C1 — baseline에 `job_cnt`, `stage_cnt` 추가

`post_run_chart_writer.load_baseline_df`가 `instance_table_csv`에서 `n`, `c`도
읽어 `job_cnt`, `stage_cnt`로 내보낸다. 반환 컬럼:
`instance_id, t_factor, r_factor, job_cnt, stage_cnt, ref_obj`.
`attach_rpdf_columns`도 두 컬럼을 함께 join한다.

> 이 이름은 `post_run_chart_writer.py` 모듈 docstring 13행이 이미 서술하고
> 있는 것(`instance_id -> ref_obj/job_cnt/stage_cnt`)이다 — 지금은 구현이
> 따라오지 못해 stale한 상태라 이번에 일치시킨다.

부수 영향: `rpdf_scatter_chart.REQUIRED_COLUMNS`는 그대로 두면 되고
(추가 컬럼은 무시), `build_cross_run_flow_chart.py`는 `load_baseline_df` +
`attach_rpdf_columns`를 그대로 통과시키므로 **호출부 수정 불필요**.

### C2 — 공용 필터 UI/JS (신규, DRY)

`src/ffc_ddw_sum_et/report/_cell_filter.py` 신설:

- `cell_filter_toolbar_html(dim_values) -> str` — T/R/n/c 4개 `<select>` (각각
  `All` + 값들). 두 템플릿이 같은 마크업을 쓴다.
- `CELL_FILTER_JS: str` — 선택 상태 → 선택된 셀 키 목록으로 바꾸는 함수와
  가중 병합 함수를 담은 JS 문자열. 두 템플릿에 `$cell_filter_js`로 주입한다.

두 템플릿 모두 `string.Template`을 쓰므로 주입이 안전하다
(`rpdf_scatter_chart.py`만 `str.format`을 쓰는데, 이번에 건드리지 않는다).

`CELL_FILTER_JS`의 핵심 — 계단함수 가중 병합(포인터 스윕, O(전체 점수)):

```js
function mergeCells(cells) {                      // [{x, y, n, guide_x}]
  const start = Math.max(...cells.map(c => c.x[0]));
  const grid = [...new Set(cells.flatMap(c => c.x))]
                 .sort((a, b) => a - b).filter(t => t >= start);
  const total = cells.reduce((s, c) => s + c.n, 0);
  const ptr = cells.map(() => 0);
  const y = grid.map(t => {
    let acc = 0;
    cells.forEach((c, i) => {
      while (ptr[i] + 1 < c.x.length && c.x[ptr[i] + 1] <= t) ptr[i] += 1;
      acc += c.n * c.y[ptr[i]];
    });
    return acc / total;
  });
  return { x: grid, y, n: total };
}
```

### C3 — `method_mean_scatter.py` (10.8 KB / 4.2 KB → ~250 KB / ~40 KB)

#### C3-1 데이터 (`load_method_mean_metrics`)

시그니처에 `cell_by_instance: dict[str, tuple[str, ...]] | None = None`을
**키워드 인자로 추가**한다(기본 `None` → 현재 동작 그대로, 기존 테스트 20개
무수정 통과).

기존 per-instance carry-forward 로직(`instance_data` 구축까지)은 **그대로
둔다.** 바꾸는 곳은 마지막 집계 루프(라인 234–286) 하나뿐 —
`time_pcts`/`rpdfs`를 단일 버킷이 아니라 셀별 버킷으로도 누적한다.

반환 dict에 `cells` 필드를 추가:

```python
{
  "method": ..., "label": ..., "is_top_level": ...,
  "mean_time_pct": ..., "mean_rpdf": ..., "instance_count": ...,   # 기존 = All
  "cells": {"0.2|0.2|50|5": {"x": ..., "y": ..., "n": ..., "reached": ...}, ...},
}
```

반드시 지켜야 할 두 가지:

1. **method 순서·목록은 시나리오 전체 기준으로 한 번만 계산한다.**
   `step_order`(라인 170)와 `_order_parents_after_children`(라인 227)을
   셀별로 다시 계산하면 셀마다 method 목록이 달라져 재조합이 불가능해진다.
2. **`if not reached: continue`(라인 253) 규칙은 셀별 `reached` 카운트로
   옮긴다.** 시나리오 레벨의 skip 판정은 지금 그대로 두고(→ All 뷰 불변),
   셀에는 `reached`를 함께 저장해 JS가 **선택된 셀들의 `reached` 합이 0일
   때만** 그 점을 생략하게 한다. 이러면 All 선택 시 오늘 곡선과 완전히 동일.

#### C3-2 렌더 (`export_method_mean_scatter_html`)

- 툴바 4개 드롭다운 추가, `Plotly.newPlot` → `Plotly.react` + `applyFilters()`.
- All 선택 → 기존 top-level 필드 사용. 그 외 → `cells` 가중평균.
- hover의 `instance_cnt`는 선택 집합 합계로 바꾼다.
- 축 범위(`x_max`/`y_min`/`y_max`)는 **모든 셀의 값까지 포함해 한 번에** 잡아
  필터를 바꿔도 축이 튀지 않게 한다(현재 `_build_payload`는 All 값만 본다).

#### C3-3 호출부

`post_run_chart_writer`에서 `cell_by_instance=cell_key_by_instance(baseline_df)`
를 넘긴다. run-level·per-scenario 두 호출 모두 같은 map을 쓴다.

### C4 — `multi_scenario_method_chart.py` (1.16 MB → ~2.1 MB)

`_build_scenario_mean_series`가 반환하는 dict를 다음 구조로 확장한다.

```python
{
  "scenario": ...,
  "all":  {"x": [...], "y": [...], "n": 1440,          # N=2000로 decimate
           "guide_x": [...], "guide_text": [...]},
  "cells": {"0.2|0.2|50|5": {"x": [...], "y": [...],   # M=200로 decimate
                             "n": 20, "guide_x": [...]}, ...},
  "meta": [scenario_label, 1440],
}
```

- `step_x`/`step_y`(계단 경로) 저장을 **없애고** JS에서 만든다. 지금은 계단
  경로가 브레이크포인트의 2배 길이로 저장되고 있어 그 절반이 순수 중복이다.
  `build_step_path`의 JS 포팅 1개 함수 추가 — 이것만으로 기존 대비 절반 절약.
- `guide_text`(subroutine 이름 목록)는 시나리오당 1회만 저장하고, 셀은 같은
  순서의 `guide_x`만 갖는다. 가이드 마커의 재조합도 가중평균
  (`_fill_missing_subroutine_endpoints`가 instance마다 모든 subroutine 행을
  채워 두므로 셀 내 count가 균일 → 정확).
- 시작 마커: 선택 집합의 `max(x[0])`, y는 병합 곡선의 첫 값.
- 셀 시계열은 `_fill_missing_subroutine_endpoints` **이후**의 endpoint_df에서
  셀별로 잘라 만든다(현재 파이프라인을 셀 루프로 감싸는 형태).
- `_MEAN_SERIES_MAX_POINTS = 10000` → `_ALL_SERIES_MAX_POINTS = 2000`,
  신규 `_CELL_SERIES_MAX_POINTS = 200`.
- 축 범위는 All + 모든 셀을 포함해 계산.
- legend의 시나리오 토글(`groupclick: "togglegroup"`)과 가이드 shape 동기화
  (`buildVisibleGuideShapes`)는 유지하되, `TRACES_PER_SCENARIO` 스트라이드
  가정이 깨지지 않도록 트레이스 구성(line / guide / start = 3개)을 그대로 둔다.

### C5 — 호출부·스크립트

- `post_run_chart_writer.write_post_run_subroutine_chart_artifacts`: 두 writer에
  셀 map 전달. CSR inner flow(`_maybe_write_csr_inner_flow_comparison_html`)도
  같은 `baseline_df`를 쓰므로 **자동으로 필터를 얻는다.**
- `scripts/build_subroutine_flow_charts.py`: 변경 없음(writer만 호출).
- `scripts/build_cross_run_flow_chart.py`: 변경 없음(C1 참조). 다만 셀 map을
  넘기지 않으면 flow writer가 셀 없이 All만 그리는 경로가 되어야 하므로,
  **`cell_by_instance=None`일 때 필터 툴바를 아예 렌더하지 않는 폴백**을
  writer에 둔다(이 스크립트는 `attach_rpdf_columns` 결과를 넘기므로 실제로는
  셀 컬럼이 있어 필터가 붙는다 — 폴백은 계약 방어용).

## 4. 대상 파일

| 파일 | 변경 |
|---|---|
| `src/ffc_ddw_sum_et/report/instance_cells.py` | **신규** — C0 셀 키/차원/포맷 |
| `src/ffc_ddw_sum_et/report/_cell_filter.py` | **신규** — C2 툴바 HTML + 병합 JS |
| `src/ffc_ddw_sum_et/report/post_run_chart_writer.py` | C1 `job_cnt`/`stage_cnt` join, C5 셀 map 전달 |
| `src/ffc_ddw_sum_et/report/method_mean_scatter.py` | C3 셀별 집계 + 필터 UI |
| `src/ffc_ddw_sum_et/report/multi_scenario_method_chart.py` | C4 셀별 시계열 + 필터 UI + 계단 경로 JS 이관 |
| `src/ffc_ddw_sum_et/report/np_utils.py` | 변경 없음 (`decimate_step_series`를 cap만 달리해 재사용) |
| `src/ffc_ddw_sum_et/report/rpdf_scatter_chart.py` | **변경 없음** (§7 참조) |
| `tests/report/test_instance_cells.py` | **신규** |
| `tests/report/test_method_mean_scatter.py` | C3 테스트 추가 (기존 20개는 무수정 통과해야 함) |
| `tests/report/test_multi_scenario_method_chart.py` | **신규** — C4 payload/병합 계약 |
| `tests/report/test_post_run_chart_writer.py` | C1/C5 통합 |

## 5. 크기 예산

바이트/숫자는 실측 기준 ~9 B(x는 6자리 소수, y는 5자리 소수 반올림).

**flow comparison** (시나리오당):

| 구성 | 숫자 개수 | 크기 |
|---|---|---|
| All 시계열 (N=2000, 브레이크포인트만) | 4,000 | 36 KB |
| 72셀 × M=200 × (x, y) | 28,800 | 259 KB |
| 가이드 마커 (72 × ~12) | 864 | 8 KB |
| **합계 / 시나리오** | | **~303 KB** |
| **7 시나리오 총합** | | **~2.1 MB** (현재 1.16 MB의 **1.8배**) |

M 감도: M=100 → 1.2 MB, M=200 → 2.1 MB, M=300 → 3.0 MB, M=500 → 4.8 MB.
비교: 조밀 공유격자(N=2000) 방식은 **9.2 MB**.
병합 후 All 필터 뷰의 브레이크포인트는 최대 72×200 = 14,400개로, 현재 실측
4,600개보다 오히려 촘촘하다.

**method mean scatter**: 12 method × 72 cell × 4 숫자 × ~10 B ≈ 35 KB/시나리오
→ run-level **~250 KB**, per-scenario **~40 KB**.

**게이트**: 구현 후 실측이 flow 3 MB / run-level scatter 400 KB를 넘으면
M을 낮춰 재조정한다.

## 6. 검증 (TDD — 각 테스트가 red를 거쳐야 함)

**C0 단위** (`test_instance_cells.py`)
1. `cell_key_by_instance`가 `(T, R, n, c)` 4-튜플을 문자열로 정규화한다
   (`0.2`/`1.0`은 1자리 소수, `50`/`5`는 정수 문자열).
2. 4개 차원 중 NaN이 있는 instance는 map에서 제외되고 warning이 남는다.
3. `cell_dim_values`가 숫자 정렬을 반환한다 (`50, 100, 150, 200` — 문자열
   정렬 `100, 150, 200, 50`이 아님).

**C1 단위** (`test_post_run_chart_writer.py`)
4. `load_baseline_df`가 `job_cnt`/`stage_cnt`를 포함하고 값이
   `pra2017_instance_table.csv`의 `n`/`c`와 일치.
5. `attach_rpdf_columns`가 두 컬럼을 전파하고, baseline에 없는 instance를
   드롭하는 기존 동작은 불변.

**C3 단위** (`test_method_mean_scatter.py`)
6. `cell_by_instance=None` → 반환 dict에 `cells` 없음, 나머지 필드 기존과 동일
   (**기존 20개 테스트가 무수정으로 green이어야 한다**).
7. 2셀 × 각 2 instance 픽스처: `Σ n_g·μ_g / Σ n_g`가 top-level
   `mean_time_pct`/`mean_rpdf`와 **부동소수 오차 내 완전 일치** (정확성 계약).
8. method 목록/순서가 모든 셀에서 동일하다(셀별 재계산 금지 회귀).
9. 한 셀에서만 도달한 method: 그 셀의 `reached > 0`, 다른 셀은 `reached == 0`
   이고 `n > 0`(carry-forward 값 보유). All 집계에는 점이 남는다.
10. 셀 `n`의 합이 top-level `instance_count`와 일치.

**C4 단위** (`test_multi_scenario_method_chart.py`, 신규)
11. payload에 `all`/`cells`가 있고 `step_x`/`step_y`가 **없다**(계단 경로
    JS 이관 회귀).
12. `mergeCells`의 파이썬 미러 구현으로 전체 셀을 병합한 결과가 `all`
    시계열과 **셀 quantum 허용오차 내** 일치. (JS는 pytest에서 못 돌리므로
    수치 계약은 파이썬 미러로 고정하고, JS 쪽은 13번으로 문자열 검증.)
13. 템플릿에 `mergeCells`/`buildStepPath`/4개 `<select>` id가 존재하고,
    `TRACES_PER_SCENARIO === 3`이 유지된다.
14. 단일 셀 선택 시 시작점 = **그 셀의** `max(first_times)`; 전체 셀 선택 시
    시작점 = 전체 `max(first_times)` (= `all.x[0]`).
15. 모든 셀 시계열의 길이 ≤ `_CELL_SERIES_MAX_POINTS`, All 길이 ≤
    `_ALL_SERIES_MAX_POINTS` (크기 예산 회귀).
16. 가이드 마커 가중평균이 `all.guide_x`와 일치.

**C5 통합** (`test_post_run_chart_writer.py`)
17. 픽스처 런에서 두 HTML이 모두 필터 툴바를 포함하고, 셀 payload가 비어있지
    않다.
18. 셀 map 없이 flow writer를 직접 호출(= `build_cross_run_flow_chart` 경로)
    하면 툴바 없이 All만 그린다.

**엔드투엔드**
```sh
uv run python scripts/build_subroutine_flow_charts.py \
  output/20260728_init_budget_merge/20260729T041116_435991
ls -la output/20260728_init_budget_merge/20260729T041116_435991/*.html
```
- 크기 게이트(§5) 확인.
- 브라우저에서 (a) All/All/All/All 곡선이 변경 전 곡선과 눈으로 동일한지,
  (b) `T=0.6, R=0.2` 선택 시 곡선이 위로 이동하는지(하드 셀),
  (c) `n=200, c=10` 선택 시 instance_cnt hover가 20의 배수인지,
  (d) 시나리오 legend 토글 시 세로 가이드 점선이 여전히 동기화되는지 확인.
- `<run>_csr_inner_flow_comparison.html`에도 툴바가 붙었는지 확인.

**정리**: `uv run ruff check`, `uv run ruff format`.
**회귀**: `uv run pytest tests/report -q`.

## 7. 범위 밖 / 위험

- **`rpdf_scatter_chart.py`(300 MB × 7)는 손대지 않는다.** raw 모드의
  instance별 시계열이 크기의 원인이지만, 개별 instance를 봐야 하는 용도가
  실재하므로 유지한다(사용자 확정). 다만 이 파일의 mean 모드는 이번에
  만드는 셀 payload와 사실상 동일한 물건이라, 나중에 (T,R)→(T,R,n,c)로
  넓히고 싶으면 mean 모드만 교체하면 된다. raw 모드에 n/c를 `customdata`로
  추가하면 마커당 문자열이 4→6개가 되어 그 300 MB가 ~15% 더 커지므로
  같이 하지 않는 편이 낫다.
- **72셀 전제는 "전체 1440 격자를 도는 런"에 한한다.** 부분 격자 런에서는
  셀 수가 줄고 셀당 instance가 20 미만이 된다. 셀 시계열은 데이터에서 나온
  키만 만들고, 드롭다운 옵션도 실제 존재하는 값만 노출한다. 셀당 instance가
  1~2개면 그 셀 곡선은 거칠지만 그건 통계의 성질이지 버그가 아니다.
- **필터 뷰는 셀 decimation(M=200) 오차를 가진다.** 단일 셀 선택 시 오차는
  그 셀 y-range의 0.5% 이하(760 px 차트에서 ~4 px). All 뷰는 §2.3대로
  재조합을 안 쓰므로 오차원이 없다.
- **차원을 더 넓히면 비용은 셀 수에 선형이다.** `W`(2)와
  `totalMcCount`(= `c × mps`, `mps ∈ {3,5}`)를 더하면 72 → 288셀이 되어
  flow 차트가 ~8 MB가 된다. 지금 구조는 `CELL_DIMS` 상수 하나로 확장
  가능하지만, 넓힐 때는 M을 함께 낮춰야 한다.
- **`_order_parents_after_children`를 셀별로 돌리는 실수**가 이 계획에서
  가장 깨지기 쉬운 지점이다 — 테스트 8번이 그 회귀를 잡는다.

## 8. 산출물

커밋(Conventional Commits, 논리 단위 3개, 순서대로 각각 green):

1. `feat(report): add instance-cell keys to baseline` — C0 + C1 + 테스트 1–5.
   차트는 아직 안 바뀜.
2. `feat(report): add param filter to mean scatter` — C2 + C3 + 테스트 6–10.
   `_cell_filter.py`가 여기서 처음 쓰인다.
3. `feat(report): add param filter to flow chart` — C4 + C5 + 테스트 11–18.

실험이 아니므로 별도 실행 결과물은 없다. 재생성한 HTML은 기존 run 디렉터리를
덮어쓴다(`output/`은 gitignore).
