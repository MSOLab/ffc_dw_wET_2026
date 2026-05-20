# multi-scenario flow chart: shrink 100MB+ HTML

## 목표

`multi_scenario_subroutine_flow_comparison.html` 크기를 1MB 미만 (per-scenario
scatter HTML 수준) 으로 줄인다. 차트의 시각적 의미 — scenario별 mean RPDf step
line + subroutine 종료 위치를 표시하는 guide marker — 는 동일하게 유지.

## 배경 (왜 큰가)

HTML 페이로드를 디코드해 보면:

```txt
scenario A: step_x len=759,601, step_y len=759,601, step_customdata len=759,601
scenario B: step_x len=554,813, step_y len=554,813, step_customdata len=554,813
```

원인 두 가지:

1. **Mean union-time 폭증.** `_build_scenario_mean_series`는 모든 instance의
   raw progression point의 합집합을 mean의 x축으로 쓴다. instance당 CP 콜백이
   수천 개, 인스턴스가 수백 개 ⇒ 합집합이 수십만. per-scenario scatter 차트
   (`rpdf_scatter_chart.py`)는 같은 문제를 `_keep_strict_global_improvements_or_endpoints`
   (call별 endpoint + global 개선점만 남김) 로 해결해 700KB대로 유지.
   multi-scenario 차트는 그 필터를 적용하지 않는다.

2. **`step_customdata` 통째 중복.** trace당 `[[scenario_label, instance_cnt]]`가
   `len(step_x)`개 — 전부 동일 값. Plotly는 `meta` 필드를 trace당 상수로 받아
   `%{meta[0]}` 식으로 hovertemplate에서 참조 가능. 760K 짜리 배열을 길이-2 짜리
   `meta`로 대체하면 그만큼 통째로 사라진다.

## 범위

- 변경 파일
  - `src/ffc_ddw_sum_et/report/multi_scenario_method_chart.py`
- 변경하지 않는 것
  - `rpdf_scatter_chart.py` — 이미 필터를 적용하고 있음. 단, 헬퍼
    `_keep_strict_global_improvements_or_endpoints`는 `multi_scenario_method_chart`
    에서도 import 해서 재사용 (이미 다른 헬퍼들을 import 중).
  - `post_run_chart_writer.py` 의 호출부 — 시그니처 유지.

## 설계

### (a) per-instance progression 필터링

`_build_scenario_progression_models` 에서 progression DataFrame을 instance별로
groupby 한 직후, `_keep_strict_global_improvements_or_endpoints`를 적용해 행 수를
줄인다.

```python
from .rpdf_scatter_chart import (
    _build_best_so_far_progression_points,
    _build_step_path,
    _extract_progression_times,
    _keep_strict_global_improvements_or_endpoints,   # new
    _lookup_rpdf_at_or_before_indexed,
)
...

progression_by_instance = {
    str(ins): _keep_strict_global_improvements_or_endpoints(grp.sort_values(sort_cols))
    for ins, grp in raw_progression_df.groupby("instance_id", sort=True)
}
```

이 필터는 `call_index`, `rpd_f`, `norm_time`을 요구.
`build_raw_progression_df` + `attach_rpdf_columns`를 거친 frame이 모두 갖고 있다.

### (b) per-trace `step_customdata` 제거 → `meta`

`_build_scenario_mean_series` 반환 dict에서 `step_customdata`를 빼고, 대신
`meta`(2-tuple)를 둔다:

```python
return {
    "scenario": scenario_label,
    "step_x": step_x,
    "step_y": step_y,
    "meta": [scenario_label, len(models)],   # was step_customdata
    "vertical_guides": [...],
    "guide_marker_x": guide_x,
    "guide_marker_text": guide_text,
    "guide_marker_customdata": _build_guide_marker_customdata(...),
}
```

HTML 템플릿의 line trace에서:

```js
{
  type: "scatter", mode: "lines",
  name: trace.scenario, legendgroup: trace.scenario,
  x: trace.step_x, y: trace.step_y,
  meta: trace.meta,                          // was customdata
  line: { width: 2, color: seriesColor },
  hovertemplate:
    "scenario=%{meta[0]}<br>" +              // was customdata[0]
    "instance_cnt=%{meta[1]}<br>" +          // was customdata[1]
    "Time%=%{x:.4%}<br>" +
    "Mean RPDf=%{y:.4%}<extra></extra>",
  showlegend: true
}
```

guide marker trace의 `customdata`는 길이 8 짜리라 그대로 둠.

## 검증

- `uv run pytest tests/report` (있다면) — chart writer 회귀.
- `uv run ruff check src/ffc_ddw_sum_et/report/multi_scenario_method_chart.py`.
- 기존 run 디렉토리에서 차트만 재생성:

  ```bash
  uv run python scripts/build_subroutine_flow_charts.py \
      output/20260511/20260511T131401_488223
  ```

  생성된 HTML 크기가 < 1MB이고 브라우저에서 정상 렌더 + hover에 scenario/
  instance_cnt가 보이는지 사용자가 확인.

## 비범위

- mean line 자체의 해석 방식(union vs grid 등): 합집합 필터 후 충분히 작아지면
  더 손대지 않음. 필요시 후속.
- per-scenario scatter HTML — 이미 같은 필터를 적용 중. 무변경.
