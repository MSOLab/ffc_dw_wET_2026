# flow comparison 차트 — payload 좌표 반올림 + 해상도 상향 (사전 작성, 코드 변경 계획)

**작성일**: 2026-07-27 · **종류**: 코드 변경 계획 (TDD)
**상태**: C1 / C1-b / C2 / C3 **구현 완료** (§9 구현 결과 보고 참조)
**선행**: 없음 · **후속**: 없음 (실험 아님, 리포트 렌더링 변경)

---

## 1. 문제

`multi_scenario_subroutine_flow_comparison.html`과 `csr_inner_flow_comparison.html`
(둘 다 `export_multi_scenario_method_rpdf_comparison_html`이 생성)의 payload에는
좌표가 **float repr 전체 자리수**로 직렬화된다.

```txt
output/20260726_csr_init_tl_curve/20260726T231158_246105/
  ..._csr_inner_flow_comparison.html                 1,323,925 B  (payload 1,320,160 B)
  ..._multi_scenario_subroutine_flow_comparison.html   986,089 B
```

payload의 99.7%가 `step_x`/`step_y` 숫자 문자열이고, `0.4834094857291839` 같은
17자리 값이 그대로 들어간다. **차트가 실제로 표시하는 해상도는 그보다 훨씬 낮다**:

| 표시 지점 | 코드 | 해상도 |
|---|---|---|
| y축 눈금 | `tickformat: ".1%"` (`multi_scenario_method_chart.py:360`) | 1e-3 |
| x축 눈금 | `tickformat: ".1%"` (`:359`) | 1e-3 |
| hover y | `%{y:.4%}` (`:337`) | 1e-6 |
| hover x | `%{x:.4%}` (`:336`) | 1e-6 |

즉 **1e-6보다 아래 자리는 어떤 경로로도 화면에 나타나지 않는데** 파일에는 실려 있다.

동시에, 점 개수 상한(`_MEAN_SERIES_MAX_POINTS = 4000`)은 곡선의 계단 디테일을
필요 이상으로 뭉갠다. 실측 결과 현재 trace당 decimate 출력은 상한의 38~57%
수준이지만, quantum(`y_span/4000`)이 f40 기준 1.5e-4 = **0.015 %p**로 y축 눈금
(0.1 %p)보다는 촘촘해도 CSR 시나리오 간 미세 차이를 비교하기에는 거칠다.

## 2. 진단 — "점 개수"와 "점당 바이트"는 별개 손잡이다

사전 조사에서 두 축을 분리해 실측했다 (`csr_inner_flow_comparison.html` 기준,
step-path 확장 후 총 33,854점).

**(a) y 반올림은 현행 quantum보다 촘촘하면 점을 하나도 못 줄인다.**

| y 반올림 단위 | 반올림 후 flat-run 접기 결과 | 비율 |
|---|---|---|
| 0.001 %p (1e-5) | 33,854 pts | 100 % |
| 0.01 %p (1e-4) | 33,269 pts | 98 % |
| 0.1 %p (1e-3) | 7,242 pts | 21 % |
| 1 %p (1e-2) | 860 pts | 3 % |

현행 quantum이 6.5e-5 ~ 1.5e-4이므로 1e-5 격자는 **이미 걸려 있는 필터보다
6~15배 촘촘**하다. 점 개수 감소 효과는 0이다.

**(b) x 반올림도 점을 줄이지 못한다.** x는 초가 아니라 정규화 시간
`norm_time ∈ [0,1]`이고, 평균 곡선의 x는 인스턴스별 변화시각의 **합집합**이다.
1e-6 격자는 10^6개 값을 표현할 수 있어 4천 점과 충돌하지 않는다. 원리적으로도
점을 버리려면 *y가 같은 구간의 내부 점*을 지워야 하므로 판정 기준은 y다.

**(c) 반올림의 실제 이득은 점당 바이트다.** 좌표만 반올림하고 점은 하나도 버리지
않았을 때:

```txt
payload 1,320,160 B → 583,832 B (44%)     파일 1.32 MB → 0.59 MB
숫자 문자 수 (f40 trace) 166k → 67k
```

→ **결론: (1)은 "점 수 축소"가 아니라 "표시되지 않는 자리수 제거"로 정의하고,
점 수는 (2)에서 별도 손잡이(`_MEAN_SERIES_MAX_POINTS`)로 다룬다.**

## 3. 변경 사항

### C1 — 평균 시계열 좌표를 표시 해상도로 반올림 (필수)

`np_utils.py`에 순수 헬퍼를 추가한다. `decimate_step_series` 바로 옆에 두는
이유는 둘 다 "표시용 시계열 축약"이라는 같은 책임이고, 항상 연달아 호출되기
때문이다.

```python
def round_step_series(
    xs: list[float], ys: list[float], *, x_decimals: int, y_decimals: int
) -> tuple[list[float], list[float]]:
    """표시 해상도로 좌표를 반올림한다. 점은 하나도 버리지 않는다."""
```

호출 위치는 **`decimate_step_series` 직후, `build_step_path` 직전**
(`multi_scenario_method_chart.py:214-218`):

```python
mean_x, mean_y = decimate_step_series(mean_x, mean_y, max_points=_MEAN_SERIES_MAX_POINTS)
mean_x, mean_y = round_step_series(
    mean_x, mean_y, x_decimals=_X_ROUND_DECIMALS, y_decimals=_Y_ROUND_DECIMALS
)
step_x, step_y = build_step_path(mean_x, mean_y)
```

**순서가 중요하다 — 세 가지 이유로 이 위치여야 한다.**

1. `build_step_path`는 `y < prev_y`일 때만 수직 낙차용 중복점을 넣는다
   (`step_path.py:31`). 먼저 반올림하면 반올림 후 같은 값이 된 낙차에는 중복점이
   생기지 않는다 — 반대 순서면 y가 동일한 0-길이 수직선분이 남아 낭비된다.
2. `_build_payload:279-285`가 `step_x`/`step_y`에서 축 범위(`x_max`, `y_min`,
   `y_max`)를 뽑으므로, 이 위치에서 반올림하면 축과 데이터가 같은 값을 본다
   (단일 소스).
3. `decimate` 이전에 반올림하면 quantum 계산의 입력(`y_span`)이 흔들린다.

`guide_marker_x`(`:224`)와 그로부터 파생되는 `vertical_guides`도 같은
`x_decimals`로 반올림한다. 점 수는 서브루틴당 1개(3~12개)라 용량 기여는
없지만, 가이드 선과 계단이 다른 x 격자에 놓이면 안 된다.

**상수** (`multi_scenario_method_chart.py` 상단):

```python
# hover가 x/y를 `.4%`(퍼센트 소수 4자리 = 1e-6)로 찍으므로 6자리 아래는
# 어떤 경로로도 화면에 나타나지 않는다.
_X_ROUND_DECIMALS = 6
# y는 hover 표기도 `.3%`로 함께 낮춘다 (C1-b) — 저장 정밀도와 표시 정밀도를 일치.
_Y_ROUND_DECIMALS = 5
```

x축 해상도 참고: 이 계열 런의 TL은 `0.09nc`라 인스턴스마다 22.5s(n=50,c=5) ~
180s(n=200,c=10)로 다르다. `norm_time` 1e-6은 각각 **22.5 µs / 180 µs**에
해당하므로, TL이 1000s 이하인 한 모든 인스턴스에서 **ms보다 촘촘하다.**
(정규화 시간에는 단일 "ms 격자"가 존재하지 않는다 — 인스턴스마다 TL이 다르고
평균 곡선의 x는 그 합집합이므로, 고정 소수 자리수로 상한을 두는 것이 유일한
일관된 방법이다.)

### C1-b — y hover 표기를 `.3%`로 낮춤 (필수, C1에 종속)

y를 5자리(=퍼센트 소수 3자리)로 저장하면 hover의 `%{y:.4%}`는 항상 마지막
자리가 `0`인 유령 숫자를 보여준다 (`48.3409%` → `48.3410%`). `:337`의
hovertemplate을 `%{y:.3%}`로 바꿔 **저장 정밀도 = 표시 정밀도**를 맞춘다.

x는 6자리 저장 ↔ `.4%` 표시가 정확히 일치하므로 **바꾸지 않는다.**

> 참고: `export_multi_scenario_method_rpdf_comparison_html`의
> `y_percent_decimals` 인자(기본 1)는 **축 눈금** 포맷이지 hover가 아니다.
> hovertemplate은 템플릿에 하드코딩되어 있으므로 그쪽을 직접 고친다.

### C2 — 점 개수 상한 상향: 4000 → 10000 (필수)

`multi_scenario_method_chart.py:49`

```python
_MEAN_SERIES_MAX_POINTS = 10000
```

quantum이 2.5배 촘촘해지므로(f40 기준 1.5e-4 → 6.0e-5 = 0.006 %p) 계단
디테일이 늘어난다. docstring/주석의 "~1MB" 언급도 갱신할 것.

**C1과의 상호작용 — 유효 y 해상도는 `max(quantum, 1e-5)`가 된다.**
y_span이 작은 시나리오에서는 quantum이 반올림 격자보다 촘촘해질 수 있다
(`y_span < 0.1`이면 `quantum < 1e-5`). 그 경우 반올림이 사실상 상한 역할을
하며 점 수가 `y_span/1e-5`로 묶인다. 이는 바람직한 동작(표시 불가능한 해상도로
점을 늘리지 않음)이지만, **의도된 것임을 `round_step_series` docstring에
명시**해야 한다.

### C3 — hover 소수점 표기를 5개 차트에서 통일 (필수, C1-b의 일반화)

C1-b는 flow comparison의 y hover만 손댔다. 리포트 전체를 보면 **hover 표기가
차트마다 제각각**이라 같은 지표(RPDf, Time%)를 차트를 옮겨 볼 때마다 자리수가
달라진다. 조사 결과:

| # | 차트 (artifact) | writer | 축 눈금 | hover x | hover y |
|---|---|---|---|---|---|
| 1 | `summary_method_rpdf_and_norm_time_scatter` | `rpdf_scatter_chart.py` | `.1%` | `.4%` | `.4%` |
| 2 | `summary_method_mean_rpdf_and_mean_norm_time_scatter` | `method_mean_scatter.py` | `.1%` | **`.1%`** | **`.1%`** |
| 3 | `<run>_multi_scenario_method_mean_..._scatter` | `method_mean_scatter.py` | `.1%` | **`.1%`** | **`.1%`** |
| 4 | `<run>_multi_scenario_subroutine_flow_comparison` | `multi_scenario_method_chart.py` | `.1%` | `.4%` | `.3%`(C1-b) |
| 5 | `<run>_csr_inner_flow_comparison` | `multi_scenario_method_chart.py` | `.1%` | `.4%` | `.3%`(C1-b) |

**결정된 규칙**: **hover는 x·y 모두 `.3%`(퍼센트 소수 3자리), 축 눈금은 `.1%`
유지.** 축은 스케일을 읽는 용도라 `0.000%`/`10.000%` 같은 라벨은 길기만 하고
x축에서는 tick이 겹칠 수 있다. 데이터 판독은 hover가 담당한다.

#### C3-1 — 단일 소스 상수

`_chart_constants.py`에 추가한다. 이 모듈은 이미 세 chart writer가 공유하는
표현 상수(`SERIES_COLORS`, `SUBROUTINE_SYMBOL_MAP`)의 소유자이므로 자리수 규칙도
여기가 맞다.

```python
# Hover readouts show percent values at this many decimals across every
# chart (RPDf and Time% alike). Axis ticks stay at 1 decimal — they read
# the scale, hover reads the data.
HOVER_PERCENT_DECIMALS = 3
```

세 writer가 각자의 템플릿 치환 방식(`.format` / `string.Template`)으로 주입한다
— `_chart_constants`의 기존 주석이 설명하는 그 차이를 그대로 따른다.

#### C3-2 — `method_mean_scatter.py`: hover와 눈금 변수를 **분리**

현재 `$x_percent_decimals` / `$y_percent_decimals` **하나가 눈금과 hover 양쪽에**
쓰인다 (`:405-406` hover, `:412-413` tickformat). 기본값이 1이라 hover가 1자리로
잘리는 원인이다. 템플릿 치환을 **두 쌍으로 분리**한다:

- `$x_tick_decimals` / `$y_tick_decimals` ← 기존 `x/y_percent_decimals` 인자 (기본 1)
- `$x_hover_decimals` / `$y_hover_decimals` ← `HOVER_PERCENT_DECIMALS`

`export_method_mean_scatter_html`의 **공개 시그니처는 바꾸지 않는다** —
`x_percent_decimals`/`y_percent_decimals`는 계속 눈금만 제어한다. hover는 상수라
인자로 노출할 이유가 없다 (YAGNI).

#### C3-3 — `rpdf_scatter_chart.py`: hover `.4%` → `.3%`

`:428-429`, `:443-444`, `:462` 다섯 자리. 이 모듈은 `.format` 템플릿이라 리터럴
중괄호가 `{{ }}`로 escape돼 있다 — 새 필드를 넣을 때 `%{{y:.{hover_decimals}%}}`
형태가 되어 읽기 어렵다. **이 파일만은 `HOVER_PERCENT_DECIMALS`를 f-string으로
미리 포맷한 문자열 상수 하나를 만들어 주입**하는 편이 낫다.

> ⚠ **이 권고는 틀렸다 (구현 중 정정, §9 참조).** "미리 포맷한 문자열을
> 주입"이 렌더 결과 전체에 `str.replace`를 거는 구현으로 이어졌고, 그러면
> payload에 실린 라벨까지 다시 쓴다. escape는 실제로 문제가 되지 않는다 —
> `"%{{x:{hover_fmt}}}"`가 `%{x:.3%}`로 정확히 전개되므로 **평범한 `.format`
> 필드를 쓸 것.**

#### C3-4 — `multi_scenario_method_chart.py`: x hover `.4%` → `.3%`

`:349`(line trace), `:365`(guide marker) 두 자리. y는 C1-b에서 이미 `.3%`.
`_render_html`이 `string.Template`이므로 `$hover_decimals` 치환을 추가한다.

#### C3-5 — 결정 필요: `_X_ROUND_DECIMALS`를 6 → 5로 내릴지

C1의 상수 근거는 "hover가 `.4%`(=1e-6)로 찍으니 6자리 아래는 안 보인다"였다.
C3로 x hover가 `.3%`(=1e-5)가 되면 **그 근거상 x도 5자리면 충분**해지고,
`_X_ROUND_DECIMALS`와 `_Y_ROUND_DECIMALS`가 하나로 합쳐진다.

| | 유지 (x=6) | 내림 (x=5) |
|---|---|---|
| payload 절감 | — | 추가 ~4 % |
| x 저장 해상도 (TL=180s / 22.5s) | 180 µs / 22.5 µs | 1.8 ms / 0.225 ms |
| 상수 | 2개 | 1개 (`_ROUND_DECIMALS`) |

**유지(x=6) 권장.** 절감폭이 4%로 미미한 반면, 최초 요구였던 "시간을 ms 자리까지"
조건이 TL=180s에서 1.8 ms로 깨진다. hover 정밀도를 나중에 올릴 여지도 남는다.
내리기로 한다면 staged 테스트 중 `test_flow_chart_payload_coord_precision`의
`step_x ≤ 6` 단언과 `test_round_step_series_*`의 `x_decimals=6` 픽스처를 함께
고쳐야 한다.

#### C3에서 하지 않는 것

**scatter 차트의 좌표 반올림은 이번에도 범위 밖이다.** 다만 조사 중 드러난
사실은 기록해 둔다 — `summary_method_rpdf_and_norm_time_scatter.html`은
**2.61 MB로 flow 차트(1.32 MB)보다 크다**:

```txt
csr_init_tau1_f05/summary_method_rpdf_and_norm_time_scatter.html
  파일 2,611,807 B · DATA payload 2,605,263 B
  float 53,818개 / 987,957자  →  round(6) 적용 시 payload 2.04 MB (78 %)
```

C1과 같은 반올림으로 ~0.57 MB를 줄일 수 있다. 다만 (a) 이 차트는
`decimate_step_series`를 쓰지 않아 반올림을 끼울 지점이 별도로 필요하고,
(b) payload의 절반 이상이 instance_id·라벨 문자열이라 이득 비율이 낮으며,
(c) 이번 요청 범위(표기 통일)와 동기가 다르다. **별도 건으로 다룰 것.**

## 4. 예상 결과 (검증 대상)

| | 변경 전 | C1만 (실측) | C1+C2 (추정) | **C1+C2 실측** |
|---|---|---|---|---|
| csr_inner — step 점 수 | 33,854 | 33,854 | ~85,000 | **70,488** (2.08×) |
| csr_inner — 파일 | 1.32 MB | 0.59 MB | ~1.4–1.5 MB | **1.20 MB (−9 %)** |
| multi_scenario — step 점 수 | 24,187 | — | — | **37,929** (1.57×) |
| multi_scenario — 파일 | 0.99 MB | — | — | **0.66 MB (−33 %)** |

C2의 점 수는 계획 시점에 **추정치**였다 — decimate 출력이 상한 4000에 걸리지
않고 quantum 조건에서 자연 수렴한 상태(상한의 38~57%)라 상한을 2.5배 올려도
점이 2.5배 늘어난다는 보장이 없었다. 실측 결과 **2.08× / 1.57×로 선형보다
완만**했고, 그만큼 파일도 추정보다 작게 나왔다.

**결과적으로 계단 해상도는 1.6~2.1배가 되면서 파일은 오히려 9~33 % 줄었다** —
계획이 예상한 "+10~15 %"보다 나은 결과다.

## 5. 대상 파일

| 파일 | 변경 |
|---|---|
| `src/ffc_ddw_sum_et/report/np_utils.py` | C1 — `round_step_series` 추가 ✅ |
| `src/ffc_ddw_sum_et/report/multi_scenario_method_chart.py` | C1 (상수, `:221-226` 호출, `:234` guide) ✅, C1-b (`:350` hovertemplate) ✅, C2 (`:46-56`) ✅, **C3-4 (`:349`·`:365` x hover)** |
| `src/ffc_ddw_sum_et/report/_chart_constants.py` | **C3-1 — `HOVER_PERCENT_DECIMALS` 추가** |
| `src/ffc_ddw_sum_et/report/method_mean_scatter.py` | **C3-2 — hover/tick 치환 변수 분리 (`:401-413`, `:428-434`)** |
| `src/ffc_ddw_sum_et/report/rpdf_scatter_chart.py` | **C3-3 — hover `.4%` → `.3%` (`:428-429`, `:443-444`, `:462`)** |
| `tests/report/test_np_utils.py` | C1 유닛 테스트 ✅ |
| `tests/report/test_post_run_chart_writer.py` | C1/C1-b/C2 통합·회귀 테스트 ✅, **C3 회귀** |
| `tests/report/` (scatter 모듈 테스트) | **C3-2 / C3-3 테스트** |

**scatter 차트의 좌표 반올림은 범위 밖이다** (§3 "C3에서 하지 않는 것" 참조) —
C3는 두 scatter 파일의 **hover 문자열만** 건드리고 payload 숫자에는 손대지
않는다.

## 6. 검증 (TDD — 각 테스트가 red를 거쳐야 함)

**C1 유닛** (`test_np_utils.py`):

1. `round_step_series`가 주어진 자리수로 반올림하고 **길이를 보존**한다
   (점을 버리지 않는다).
2. y의 약한 단조성이 보존된다 — 입력이 비증가면 출력도 비증가.
   (반올림이 순서를 뒤집으면 `build_step_path`가 유령 낙차를 그린다.)
3. 첫/마지막 x가 반올림된 원값과 일치한다.
4. 이미 격자에 정렬된 입력은 **그대로 반환**된다 (idempotent).

**C1 통합** (`test_post_run_chart_writer.py` 또는 chart 모듈 테스트):

5. 생성된 HTML payload의 `step_x`/`step_y`/`guide_marker_x` 어떤 값도
   소수점 6자리(y는 5자리)를 초과하지 않는다 — JSON을 파싱해 단언.
6. **순서 계약**: 반올림 후 같은 값이 되는 미세 낙차를 담은 입력에서
   `step_x`에 중복 x가 생기지 않는다 (C1의 근거 1을 고정).
7. **축 일관성**: `payload["y_min"]`/`y_max`/`x_max`가 반올림된 좌표에서
   유도된 값과 일치한다.

**C1-b**: 렌더된 HTML에 `%{y:.3%}`가 있고 `%{y:.4%}`는 없다. x는 `.4%` 유지.

**C2**: `_MEAN_SERIES_MAX_POINTS == 10000`이고, 동일 입력에 대해 4000일 때보다
decimate 출력 점 수가 **크거나 같다** (합성 조밀 시계열로 단언).

**C3** (writer 3개 모두, 렌더된 HTML 문자열 단언):

8. **hover 통일**: 5개 artifact 각각의 HTML에서 hover 포맷이 x·y 모두
   퍼센트 소수 3자리다. `.4%`/`.1%` 잔재가 hovertemplate 안에 **하나도 없다.**
   — writer별 정규식이 아니라 `HOVER_PERCENT_DECIMALS`로부터 기대 문자열을
   조립해 단언할 것 (상수를 바꾸면 테스트도 따라오도록).
9. **눈금 회귀**: 같은 HTML의 `tickformat`이 x·y 모두 `.1%`로 **남아 있다.**
   C3-2의 변수 분리에서 눈금까지 3자리로 끌려가는 것이 이 변경의 유일한
   실질 위험이므로 반드시 red를 확인할 것.
10. **C3-2 시그니처 회귀**: `export_method_mean_scatter_html(...,
    x_percent_decimals=2, y_percent_decimals=2)`를 넘기면 **눈금만** 2자리가
    되고 hover는 3자리로 유지된다.

**엔드투엔드 실측** (수치를 §4 표에 채워 넣을 것):

```sh
uv run python scripts/build_subroutine_flow_charts.py \
  output/20260726_csr_init_tl_curve/20260726T231158_246105
```

- 두 flow HTML의 **파일 크기와 trace별 step 점 수**를 변경 전후로 기록.
- 브라우저로 열어 (a) 계단 모양이 변경 전과 육안상 동일한지, (b) hover의
  x/y 숫자가 잘리지 않고 자연스러운지, (c) vertical guide 선이 계단과
  어긋나지 않는지 확인.
- **C3는 5개 차트를 모두 열어** hover 자리수가 같은지 눈으로 대조한다.
  위 스크립트 한 번으로 5개가 전부 다시 쓰인다 —
  `write_post_run_subroutine_chart_artifacts`가 #1(`:229`), #2·#3(`:246`,
  `:277`), #4(`:268`), #5(`:327`)를 모두 내보낸다. (스크립트 docstring은
  산출물을 2개로만 적고 있으니 **함께 갱신할 것.**)

**정리**: `uv run ruff check`, `uv run ruff format`.

## 7. 소급 적용 범위

**과거 런에 그대로 소급된다.** 이 변경은 obj_log 기록 형식이 아니라 렌더링
단계만 건드리므로, §6의 `build_subroutine_flow_charts.py` 명령으로 기존 런의
HTML을 언제든 다시 뽑을 수 있다. 재생성된 차트는 신규 런의 차트와 동일한
규약을 따른다.

C3도 동일하게 소급된다 — hover 포맷은 HTML 템플릿에 있으므로 재생성만 하면
과거 런의 5개 차트가 새 규칙을 따른다.

## 8. 산출물

- 커밋 (Conventional Commits, 논리 단위 3개):
  - `perf(report): round flow chart payload coords` — C1 + C1-b
  - `feat(report): raise flow chart point cap to 10k` — C2
  - `style(report): unify hover decimals across charts` — C3
  - C1과 C2는 서로 독립이고 각각 단독으로 green이다. 다만 C2를 먼저 넣으면
    중간 상태의 파일이 3.3 MB가 되므로 **C1을 먼저 커밋할 것.**
  - C3는 C1-b가 만든 `.3%`를 나머지 4개 차트로 넓히는 변경이므로 **C1 이후**에
    온다. C3-5(x 자리수)를 "내림"으로 결정했다면 그 수정은 C1 커밋에 합치지
    말고 C3에 포함할 것 — 근거가 C3의 hover 규칙이기 때문이다.
- 별도 실행 결과물 없음 (실험 아님). §6의 재생성 결과는 기존 run 디렉터리를
  덮어쓴다.
- `metadata/20260726/csr_init_tl_curve.yaml`의 `POST_PROCESS_ONLY` +
  `analysis_timestamp` 전환은 재생성 편의를 위한 것으로 **별도 커밋**으로 둔다.

---

## 9. 구현 결과 보고 (2026-07-27)

### 변경 파일

| 파일 | 변경 |
|---|---|
| `np_utils.py:57-77` | C1 — `round_step_series` (길이 보존, docstring에 `max(quantum, 10**-y_decimals)` 계약 명시) |
| `multi_scenario_method_chart.py:56-62` | C1 — `_X_ROUND_DECIMALS=6` / `_Y_ROUND_DECIMALS=5` |
| `multi_scenario_method_chart.py:227-232` | C1 — decimate 직후 `round_step_series` 호출 |
| `multi_scenario_method_chart.py:240-243` | C1 — `guide_x` 반올림 |
| `multi_scenario_method_chart.py:50-54` | C2 — `_MEAN_SERIES_MAX_POINTS = 10000` |
| `multi_scenario_method_chart.py:355-356`, `:371`, `:414` | C1-b + C3-4 — 세 hovertemplate 모두 `$hover_decimals` |
| `_chart_constants.py:41-46` | C3-1 — `HOVER_PERCENT_DECIMALS = 3` + 규칙 주석 |
| `method_mean_scatter.py:405-406`, `:435-436` | C3-2 — hover(`$x/y_hover_decimals`)와 tick(`$x/y_percent_decimals`) 치환 분리 |
| `rpdf_scatter_chart.py:432-433`, `:447-448`, `:466`, `:508` | C3-3 — hover에 `{hover_fmt}` 필드 주입 |

### 계획 대비 차이 — 리뷰에서 잡힌 3건

**(1) `Mean RPDf` hover가 상수를 안 따라갔다.**
C1-b가 `%{y:.3%}`를 **리터럴로** 넣었고 C3-4는 x만 `$hover_decimals`로 바꿨다.
`HOVER_PERCENT_DECIMALS`를 4로 올리면 x만 따라가고 y는 3에 남는, 통일이
목적인 변경에서 규칙 밖에 남은 한 줄이었다. 기존 테스트는 `.3%`를 정규식에
하드코딩해 이 드리프트를 잡지 못했다 — **상수를 monkeypatch해 두 축이 함께
움직이는지 보는 테스트**를 추가해 red를 확인한 뒤 고쳤다.

**(2) C3-3의 치환 방식을 `str.replace` → `.format` 필드로 교체.**
§3 C3-3은 "`.format` 템플릿이라 `{{ }}` escape가 얽히니 미리 포맷한 문자열
상수를 주입하라"고 적었고, 최초 구현은 이를 `_HTML_TEMPLATE.format(...)
.replace("_HF_", _hf)`로 옮겼다. 그런데 이 `replace`는 **렌더 결과 전체**에
걸리므로 `data_json`에 실린 라벨까지 다시 쓴다. 실제로 재현됐다 —
`subroutine_name = "step_HF_marker"`인 endpoint를 넣자 HTML에서 그 라벨이
사라졌다(테스트가 red). escape를 피할 필요가 없었다: `"%{{x:{hover_fmt}}}"`가
`{{`→`{`, `{hover_fmt}`→`.3%`, `}}`→`}`로 정확히 전개된다. **계획의 §3 C3-3
권고는 틀렸고, 일반 `.format` 필드가 맞다.**

**(3) 공백 노이즈 2줄 복원.** `rpdf_scatter_chart.py`의 `hovertemplate:` /
`showlegend: false }},` 들여쓰기가 13→14칸으로 밀려 있던 것을 되돌렸다.
(`git diff HEAD`와 `git diff HEAD -w`가 동일함을 확인.)

### 계획 대비 차이 — 작은 것 2건

- `_chart_constants.py:41-46`: 상수에 주석이 없었다. "hover 3자리 / 눈금
  1자리"가 이번 결정의 핵심이고 다음 사람이 눈금까지 끌어올리기 쉬우므로,
  그 구분과 "리터럴을 하드코딩하지 말 것"을 상수 옆에 명시했다.
- `multi_scenario_method_chart.py:56-60`: 주석이 여전히 "Hover shows x as
  `.4%`"였다. C3-4로 `.3%`가 됐으므로 갱신하면서, `_X_ROUND_DECIMALS`만 6인
  이유를 **C3-5에서 "유지"로 판단한 근거**(정규화 시간 1e-6은 TL 1000 s까지
  ms보다 촘촘, 1e-5면 TL=180 s에서 1.8 ms)로 바꿔 적었다.

### C3-5 결정: x 자리수 6 유지

계획 §3 C3-5의 권장대로 `_X_ROUND_DECIMALS = 6`을 유지했다. 절감폭 4 %보다
"시간을 ms 자리까지"라는 최초 요구를 지키는 쪽이 낫다는 판단. 상수는 2개로
남는다.

### 테스트 (+16 tests, `tests/report` 51 passed)

| 파일 | 테스트 |
|---|---|
| `test_np_utils.py` | `round_step_series` 5건 — 반올림·길이 보존, 단조성 보존, 양끝 보존, idempotent, flat 구간 무순서변경 |
| `test_post_run_chart_writer.py` | `test_flow_chart_payload_coord_precision` (step_x ≤6 / step_y ≤5 / guide_marker_x ≤6), `test_round_step_series_preserves_step_path_order` (C1 순서 계약), `test_flow_chart_payload_axis_consistency`, `test_flow_chart_hover_template_y_3_percent`(상수 기반으로 교체), **`test_flow_chart_hover_follows_the_shared_constant`**(monkeypatch), `test_mean_series_max_points_is_10000` |
| `test_method_mean_scatter.py` | hover 3자리 / tick `.1%` 유지 / `x_percent_decimals=2`가 hover에 영향 없음 |
| `test_rpdf_scatter_chart.py` | hover 3자리 / tick `.1%` 유지 / **`test_hover_format_injection_leaves_payload_untouched`** |

두 신규 테스트(굵게)는 각각 위 차이 (1)·(2)를 red로 재현한 뒤 green이 됐다.

### 검증 결과

- `uv run ruff check`: clean · `uv run ruff format`: 260 files unchanged
- `uv run pytest`: **694 passed**
- 엔드투엔드 재생성(§6 명령) 후 실측치를 §4 표에 반영. 계단 해상도 1.6~2.1배,
  파일 −9 %/−33 %.
- 브라우저 육안 확인 **완료** — 계단 모양이 변경 전과 동일하고, hover 숫자가
  잘리지 않으며, vertical guide 선이 계단과 어긋나지 않음을 확인했다. 5개
  차트의 hover 자리수도 서로 대조해 모두 퍼센트 소수 3자리로 일치함을 확인.

§6의 검증 항목은 모두 통과했다.
