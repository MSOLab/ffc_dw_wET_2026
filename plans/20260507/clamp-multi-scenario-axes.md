# Plan: clamp axes of `multi_scenario_subroutine_flow_comparison.html`

## Context

차트 `*_multi_scenario_subroutine_flow_comparison.html`(생성: `src/ffc_ddw_sum_et/report/multi_scenario_method_chart.py`)
의 두 축 범위가 데이터에 비해 어색하다.

- 현재 x축은 `[0, max(time%) * 1.05]`로 설정되어, 항상 데이터 우측을 5% 비워둔다 → 사용자가 "더 오른쪽으로 scroll" 가능한 빈 공간이 생긴다.
- 현재 y축은 `[0, max(rpdf) * 1.05]`로 강제되므로, RPDf가 음수 영역으로 진입한 시나리오의 일부가 잘려 보인다.

요청한 동작:

- **y축 최소값** = `min(0, RPDf 최솟값)` — 음수 영역이 있으면 그만큼 아래로 확장, 항상 0 이하부터 시작.
- **x축 최대값** = `max(100%, time% 최대값)` — 100%(=1.0) 미만이면 100%까지 보여주고 (시각적 anchor 유지), 100% 초과면 그 최댓값까지만. 5% 패딩 제거.

## 변경 대상

단일 파일: `src/ffc_ddw_sum_et/report/multi_scenario_method_chart.py`

### 1. axis bound 헬퍼 함수 변경 (lines 29–35 부근)

`_positive_axis_upper(values)`는 현재 x/y 양쪽에 공유되며 `max * 1.05`(빈 시 `0.01`)을 반환한다. 의미가 축마다 달라지므로 두 개의 명시 헬퍼로 분리:

```python
def _x_axis_upper(values: list[float]) -> float:
    """X축(정규화 시간) 상한: max(100%, 데이터 최댓값). 패딩 없음."""
    if not values:
        return 1.0
    return max(1.0, max(values))


def _y_axis_lower(values: list[float]) -> float:
    """Y축(평균 RPDf) 하한: min(0, 데이터 최솟값)."""
    if not values:
        return 0.0
    return min(0.0, min(values))
```

`_positive_axis_upper`는 y축 상한 용도로 그대로 유지(현 동작 보존: `max * 1.05`, floor `0.01`).

### 2. `_build_payload` 반환 페이로드 (lines 223–227)

```python
return {
    "traces": traces,
    "x_max": _x_axis_upper(all_x),
    "y_min": _y_axis_lower(all_y),
    "y_max": _positive_axis_upper(all_y),
}
```

### 3. HTML 템플릿 layout (lines 312–313)

```js
xaxis: { title: { text: "Normalized time" }, tickformat: ".$x_percent_decimals%", range: [0, payload.x_max] },
yaxis: { title: { text: "Mean RPDf" }, tickformat: ".$y_percent_decimals%", range: [payload.y_min, payload.y_max] },
```

`x_max`는 변수 이름이 같으므로 템플릿 텍스트 자체는 안 건드려도 되지만, **y축은 `[0, payload.y_max]` → `[payload.y_min, payload.y_max]`로 교체**.

## 영향 범위 / 비변경 영역

- `rpdf_scatter_chart.py` 등 동일 디렉터리의 다른 차트는 `rangemode: "tozero"` 등 다른 전략을 쓰고 있고, 이번 요청은 multi-scenario 비교 차트만 대상이므로 **건드리지 않는다**.
- `_positive_axis_upper`는 그대로 유지 — y축 상한 산출에 계속 사용 (음수 데이터에서도 `max * 1.05`가 합리적; 모든 값이 음수면 0.01로 떨어지지만 `y_min`이 음수이므로 시각화는 여전히 유효).

## 검증

1. `uv run ruff check` / `uv run ruff format` — 정적 검사.
2. 보고서 재생성:

   ```bash
   uv run python main.py  # 또는 동일 시나리오 yaml로 multi-scenario 보고서 트리거
   ```

   (사용자가 이미 `output/20260507/20260507T191425_860284/`에 결과를 갖고 있으므로,
   해당 run을 다시 돌리거나 캐시된 endpoint/progression DataFrame이 있다면 재 export만으로 충분.)
3. 새 HTML을 브라우저로 열어 확인:
   - **x축**: 모든 step 끝 marker가 그래프 영역 안에 들어오고, max time% 또는 100% 지점에서 멈춰 우측 빈 공간이 사라진다.
   - **y축**: RPDf 곡선 중 가장 낮은 점이 잘리지 않고 0 이하 영역으로 연장된다 (음수 진입 시).
4. 회귀 확인: 모든 RPDf ≥ 0 이고 max time% ≥ 1.0인 일반 케이스에서 기존과 거의 동일하게 그려져야 한다 (단, x축은 5% 패딩이 사라져 약간 좁아짐 — 의도된 동작).

## 비고

- 사용자 메모리상 plan 파일은 `plans/<YYYYMMDD>/<slug>.md` 위치를 선호하나, 현재 plan 모드 harness는 `/home/hjt/.claude/plans/jazzy-nibbling-globe.md`만 편집 허용. 승인 후 수동 이동 또는 다음 비-plan 단계에서 복사 가능.
