# multi-scenario flow chart: fill missing-subroutine guide markers

## 목표

guide marker(세로 점선 + 심볼)의 x좌표를 "그 step을 실제로 실행한 instance"만의
평균이 아니라, **scenario 안의 모든 instance**의 평균으로 계산한다. 어떤 instance가
해당 step에 도달하지 못한 경우(=controller가 그 전에 timelimit/stop으로 종료) 그
instance에서는 step이 "마지막 실행된 method의 종료 시각에서 0초간 실행되고 끝났다"
로 간주.

## 배경 (정리)

`output/20260511/20260511T221827_515269/.../_2m_*` 9 instance 중

- 8개: 마지막 note = `incremental_pw_cp.4-batch_005` (norm ≈ 1.0)
- 1개: 마지막 note = `solve_base_model_cpsat` (norm = 0.3033),
  그 직전에 `incremental_pw_cp.5-batch_006` (0.2782)

현재 `multi_scenario_method_chart._build_scenario_mean_series` 의 guide_df는

```python
guide_df = (
    endpoint_df.sort_values([...])
    .groupby("subroutine_name", as_index=False, sort=False)
    .agg(avg_norm_time=("norm_time", "mean"))
)
```

`endpoint_df`는 "실제 실행되어 note가 찍힌 row만" 포함. 따라서
`solve_base_model_cpsat`의 mean = 0.3033 (1개 row), `batch_006`의 mean = 0.2782
(1개 row). marker가 chart 좌측에 어색하게 찍히는 원인.

## 범위

- 변경 파일
  - `src/ffc_ddw_sum_et/report/multi_scenario_method_chart.py`
- 변경하지 않음
  - `obj_log_loader.build_endpoint_df` — 그대로 두면 per-instance에서는 "실제
    실행된 step만" 의미가 유지되고, per-scenario scatter chart도 영향 없음.
  - `post_run_chart_writer` 인터페이스, raw_progression_df 처리.

## 설계

`multi_scenario_method_chart`에 helper `_fill_missing_subroutine_endpoints` 추가.
`_build_scenario_mean_series` 진입 직후 호출해 `endpoint_df`를 채운다.

```python
def _fill_missing_subroutine_endpoints(endpoint_df: pd.DataFrame) -> pd.DataFrame:
    """For each instance, add a synthetic endpoint row for every scenario-level
    subroutine the instance never reached. The synthetic row copies the
    instance's last actual endpoint (norm_time, obj_value, rpd_f), so it
    reads as 'step ran for 0 sec at controller stop time'.
    """
    if endpoint_df.empty:
        return endpoint_df
    all_subroutines = list(pd.unique(endpoint_df["subroutine_name"]))
    order_by_name = (
        endpoint_df[["subroutine_name", "subroutine_order"]]
        .drop_duplicates()
        .set_index("subroutine_name")["subroutine_order"]
        .to_dict()
    )
    synth_rows: list[dict] = []
    for _ins, grp in endpoint_df.groupby("instance_id", sort=False):
        present = set(grp["subroutine_name"])
        missing = [s for s in all_subroutines if s not in present]
        if not missing:
            continue
        last = grp.sort_values("norm_time").iloc[-1].to_dict()
        for s in missing:
            row = dict(last)
            row["subroutine_name"] = s
            row["subroutine_order"] = order_by_name[s]
            synth_rows.append(row)
    if not synth_rows:
        return endpoint_df
    return pd.concat(
        [endpoint_df, pd.DataFrame(synth_rows)], ignore_index=True
    )
```

`_build_scenario_mean_series` 패치:

```python
def _build_scenario_mean_series(scenario_label, endpoint_df, raw_progression_df):
    endpoint_df = _fill_missing_subroutine_endpoints(endpoint_df)
    models = _build_scenario_progression_models(endpoint_df, raw_progression_df)
    ...
    guide_df = (
        endpoint_df.sort_values([...])
        .groupby("subroutine_name", as_index=False, sort=False)
        .agg(avg_norm_time=("norm_time", "mean"))
    )
    ...
```

## 사이드 이펙트 확인

- `_build_scenario_progression_models`도 fill된 endpoint_df를 받는다. 다만
  `prog_grp` (raw_progression_df) 가 있으면 ep_grp는 무시됨. raw_progression_df는
  실제 trajectory만 갖고 있으므로 mean line에 synthetic row가 섞이지 않음.
- raw_progression_df가 빈 instance(가능성 낮음)에서는 synthetic row가 추가됨.
  하지만 synthetic의 rpd_f는 instance의 마지막 값과 동일 → running-min 변동 없음
  → mean line은 그 시각까지 평평하게 연장될 뿐.
- per-scenario scatter chart (`rpdf_scatter_chart.export_method_rpdf_scatter_html`)
  는 영향 없음 (helper 호출 안 함).
- `subroutine_order`는 원본 endpoint_df에서 미리 매겨진 값을 그대로 복사. 정렬은
  현재 동작 유지.

## 검증

- `uv run pytest tests/report -q`.
- `uv run python scripts/build_subroutine_flow_charts.py output/20260511/20260511T221827_515269`
  후 marker 위치가 batch_006/solve_base_model_cpsat 모두 1.0 근방으로 이동했는지
  사용자가 brow에서 확인.

## 비범위

- threshold-based marker suppression, hover에 ran-in-N/total 표기 등 옵션 2/3/4.
  필요 시 후속.
