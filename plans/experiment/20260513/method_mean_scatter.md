# Method-level mean RPDf × mean time% scatter dashboards

## 목표

`flowshop-tardiness`에서 방금 추가한 method-mean scatter 차트(아래 참조)를 이
저장소에도 포팅한다. 결과로 두 종류 HTML이 새로 생긴다.

- 시나리오별: `<scenario>/summary_method_mean_rpdf_and_mean_norm_time_scatter.html`
- 런 단위: `<run_dir>/{run_id}_multi_scenario_method_mean_rpdf_and_mean_norm_time_scatter.html`

각 점은 **top-level controller method 한 개**의 `(mean Time%, mean RPDf)` —
인스턴스 평균. 같은 시나리오의 점들은 method 실행 순서로 line+marker로 잇는다.

이 차트가 기존 두 차트와 다른 핵심: 기존 `multi_scenario_method_chart`와
`rpdf_scatter_chart`는 "모든 인스턴스가 등장한 첫 시점"부터 mean line을
그려서 첫 몇 method가 시각적으로 묻힌다. 새 차트는 method별로 평균 한 점만
찍으므로 첫 method 끝점부터 다 보인다.

## 참조 구현 (flowshop-tardiness)

새 차트 모듈과 와이어링은 `~/code/flowshop-tardiness`에 이미 머지됨. 거의
그대로 옮겨 데이터 모델만 어댑트하면 됨.

| flowshop-tardiness                                                                 | ffc_ddw_sum_et 대응                                                              |
| ---------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| `flowshop_tardiness/report/dashboards/method_mean_scatter.py` (신규)                | `src/ffc_ddw_sum_et/report/method_mean_scatter.py` (신규)                         |
| `flowshop_tardiness/report/dashboards/_chart_internals.py` (색·심볼 팔레트)         | `src/ffc_ddw_sum_et/report/_chart_constants.py` 재사용 (같은 팔레트가 이미 있음) |
| `flowshop_tardiness/report/dashboards/post_run.py` 의 section 5 (와이어링)          | `src/ffc_ddw_sum_et/report/post_run_chart_writer.py::write_post_run_subroutine_chart_artifacts` 끝에 와이어링 |
| `scripts/process_logs.py` → `summary_method_end_time_and_obj_value.csv` SSOT       | **없음** — `<instance>_obj_log.json` notes에서 직접 파생 필요 (아래 참조)         |
| 입력: `summary_method_end_time_and_obj_value.csv` (wide format)                    | 입력: `build_endpoint_df(progressions)` 결과를 `call_index`별로 collapse          |

## 데이터 모델 차이 — 가장 중요한 부분

`flowshop-tardiness`는 `scripts/process_logs.py::process_scenario`가 시나리오
폴더마다 `summary_method_end_time_and_obj_value.csv`를 미리 써둔다 (top-level
method별 end_sec / obj_value를 wide 포맷으로). 새 차트는 그 CSV를 그냥 읽는다.

`ffc_ddw_sum_et`엔 그 CSV가 없다. 대신 SSOT는 `<instance>_obj_log.json` 의
`notes` 매핑이고, `obj_log_loader.iter_scenario_instance_progressions` →
`build_endpoint_df`로 이미 디코드된다. 다만 endpoint_df는 **nested step 단위**
(예: `3-incremental_sw_cp.1-batch_002`)로 한 행씩이라서, **top-level method 단위**
로 collapse 해야 새 차트의 한 점이 만들어진다.

### Top-level method 추출 알고리즘

`obj_log.json` notes 예시:

```json
"notes": {
  "0.349": "1-calc_mcf_lb_and_derive_full_sch",
  "7.265": "2-neh_cp",
  "12.015": "3-incremental_sw_cp.1-batch_002",
  "16.693": "3-incremental_sw_cp.2-batch_003",
  "21.389": "3-incremental_sw_cp.3-batch_004",
  "22.501": "3-incremental_sw_cp.4-batch_005"
}
```

`obj_log_loader._parse_step_label("3-incremental_sw_cp.1-batch_002")` →
`(3, "incremental_sw_cp.1-batch_002")`. 즉 `subroutine_name` 컬럼은 dot suffix
를 유지한다. Top-level method name은 dot 이전 부분: `"incremental_sw_cp"`.

Collapse 규칙 (instance별):

1. `endpoint_df`를 `call_index`로 group by — call_index가 동일하면 같은
   top-level method의 nested step들이다.
2. 각 group에서 `global_end_sec`가 최대인 row를 선택 (=top-level method의 마지막
   nested step의 종료 시점 = top-level method 자체의 종료 시점).
3. Top-level method name = `subroutine_name.split(".", 1)[0]`.
4. `obj_value` = 그 row의 endpoint obj_value (이미 best-so-far가 아니라 endpoint
   값이지만, controller가 monotone 개선만 기록하므로 endpoint가 곧 그 method
   종료 시점의 best이다 — 참고: `build_endpoint_df`는 `call.points[-1].value`).

### Filter: 비개선 method 제거 (예외 포함)

`flowshop-tardiness` 구현에서 검증된 규칙. 그대로 가져온다.

- 같은 인스턴스 안에서, 이전(top-level) method의 obj_value보다 strictly 낮아진
  적이 한 번도 없는 method는 드롭. 예: `set_cp_model_as_base_cp_model`이나
  스냅샷-성격의 step.
- 예외 1: instance에서 obj_value를 처음 기록한 method (prior가 없음) — 무조건
  포함.
- 예외 2: obj_value를 기록한 마지막 method — 무조건 포함. 비개선이라도 flow
  종료점을 보여줘야 함.
- 옵션 인자 `drop_non_improving_methods: bool = True`로 끄기 가능 (디버깅용).

## 변경 파일

### 신규
- `src/ffc_ddw_sum_et/report/method_mean_scatter.py`
  - `load_method_mean_metrics(progressions, *, baseline_obj_by_instance, drop_non_improving_methods=True) -> list[dict]`
    - 입력: `iter_scenario_instance_progressions` 결과 (=`list[InstanceProgression]`).
    - `build_endpoint_df` 호출 → instance별로 `call_index` group by → max `global_end_sec` row 선택 → top-level method name 추출.
    - method 순서는 `call_index` 오름차순.
    - 인스턴스마다 `time_pct = global_end_sec / timelimit_sec`, `rpd_f = 2*(obj-ref)/(obj+ref)` 계산 (`post_run_chart_writer._rpdf` 재사용 가능).
    - method별로 `mean_time_pct`, `mean_rpdf`, `instance_count` aggregate.
    - 비개선 method 필터 + 첫/마지막 예외.
    - 리턴 dict 키: `method`, `mean_time_pct`, `mean_rpdf`, `instance_count`.
  - `export_method_mean_scatter_html(scenarios, output_path, *, title, x_percent_decimals=1, y_percent_decimals=1) -> bool`
    - Plotly `scatter`, `mode: "lines+markers"`. 색=`SERIES_COLORS[idx % …]`,
      symbol=`_chart_constants.SUBROUTINE_SYMBOL_MAP[method] or "circle"`.
    - hover: scenario / method / instance_cnt / mean Time% / mean RPDf.
    - 축 범위 헬퍼는 `multi_scenario_method_chart` 의 `_x_axis_upper` /
      `_y_axis_lower` / `_positive_axis_upper` 스타일 그대로.

### 수정
- `src/ffc_ddw_sum_et/report/__init__.py`
  - 새 심볼 re-export.
- `src/ffc_ddw_sum_et/report/post_run_chart_writer.py`
  - `write_post_run_subroutine_chart_artifacts` 의 기존 시나리오 루프 안에서
    이미 로드된 `InstanceProgression` 리스트를 가지고 `load_method_mean_metrics`
    추가 호출. per-scenario HTML 한 개 + accumulated 리스트로 run-level HTML
    한 개.
  - baseline은 이미 같은 함수가 로드한 `baseline_df`에서 `instance_id -> ref_obj`
    매핑을 만들어 쓴다 (현존 코드의 RPDf 컬럼 attach 로직과 동일).

### 변경 없음
- `obj_log_loader.py` — 이미 필요한 모든 디코드 제공.
- `_chart_constants.py` — 색 팔레트·심볼 맵 그대로 사용.
- `multi_scenario_method_chart.py`, `rpdf_scatter_chart.py` — 기존 차트 유지.

## SSOT / POST_PROCESS_ONLY 정합성

`write_post_run_subroutine_chart_artifacts`는 이미 모든 입력을 disk에서 읽고
있다 (`<run_id>_summary.csv`, `<instance>_obj_log.json`,
`<instance>_instance_result.yaml`, PRA2017 baseline CSVs). 새 차트도 그 안에서
호출되므로 자동으로 POST_PROCESS_ONLY 안전. 별도 standalone CLI가 필요하면
`scripts/build_subroutine_flow_charts.py`와 같은 패턴으로 추가 가능 (이번
스코프엔 미포함 — 필요하면 후속 작업).

## 검증

```bash
# 기존 런 디렉토리에서 재생성
uv run python scripts/build_subroutine_flow_charts.py \
    output/20260511/20260511T131001_441211

# 새 artifact:
#   output/20260511/20260511T131001_441211/{run_id}_multi_scenario_method_mean_rpdf_and_mean_norm_time_scatter.html
#   output/20260511/20260511T131001_441211/mcf_lb_neh_cp_incremental_sw_cp_m_base_cpsat/summary_method_mean_rpdf_and_mean_norm_time_scatter.html

# 시나리오 차트가 top-level method만 (3개: calc_mcf_lb_and_derive_full_sch, neh_cp, incremental_sw_cp) 보여야 함
grep -oE '"method":\[[^]]+\]' output/20260511/20260511T131001_441211/mcf_lb_neh_cp_incremental_sw_cp_m_base_cpsat/summary_method_mean_rpdf_and_mean_norm_time_scatter.html

# 비개선 method가 끼어 있다면 (시나리오에 따라) 자동 드롭 여부 확인
# 마지막 method는 비개선이어도 포함되는지 확인
```

## 참고: flowshop-tardiness 쪽 핵심 함수 (그대로 가져올 만한 코드)

```python
# load_method_mean_metrics 의 비개선 필터 로직 (검증됨)
# prev_obj_by_instance 는 "키프된 method 의" 값이 아니라 "마지막으로 기록된" 값
# (드롭된 method 의 obj 도 prev 로 들어가야 다음 method 의 improvement 판정이
# 옳음 — 안 그러면 비개선 method 들이 연쇄로 끼어들 수 있음).
prior = prev_obj_by_instance.get(ins_id)
if prior is None or obj < prior:
    improves = True
# ...candidates 다 모은 다음...
last_idx = len(candidates) - 1
kept = [c for i, c in enumerate(candidates) if c["improves"] or i == last_idx]
```

## 알려진 함정

1. **pandas iterrows의 dtype promotion**: `iterrows()`로 row를 가져오면 row 안에
   NaN이 하나라도 있으면 모든 컬럼이 float로 변환된다. flowshop-tardiness에서
   `instance_id=1` (int) 이 `"1.0"` 로 변해서 baseline 매핑이 다 실패한 버그가
   있었음. 컬럼별로 `df["instance_id"].astype(str).tolist()` 한 뒤 zip으로
   순회하는 패턴이 안전.
2. **`subroutine_name` 의 dot suffix**: `incremental_sw_cp.1-batch_002` 을 그대로
   group key로 쓰면 안 됨. `call_index`로 group한 뒤 method name은
   `subroutine_name.split(".", 1)[0]` 로 잘라 쓰기.
3. **RPDf 공식 일관성**: `post_run_chart_writer._rpdf` (= `2*(obj-ref)/(obj+ref)`,
   분모 0 → 0.0 또는 NaN) 와 동일한 식 사용. 새 모듈에서 새 헬퍼를 만들지 말고
   기존 함수를 import 해서 재사용.
