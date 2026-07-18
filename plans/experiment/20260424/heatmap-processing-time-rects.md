# Plan: Processing-time rectangles + label/clip/sort polish for wET & C heatmaps

## Context

두 heatmap 시각화 스크립트

- `benchmarks/PRA2017/visualize_parallel_mc_cost.py`
- `benchmarks/PRA2017/visualize_wET_cost.py`

를 동시에 개선한다. 각 job 행에서 "어디에 스케줄되는지" 와 "cost 의 상대 스케일"을
한눈에 보기 쉽도록:

1. 각 job 의 마지막 stage processing time `p_j` 를 길이로 가진 검은 테두리 사각형
   overlay 추가 — 사각형 끝이 `d⁺_j` 와 일치하도록 배치.
2. y-axis 라벨에 `p_j` 를 괄호로 병기 (우측정렬 폭 3), monospace tickfont 로
   공백 정렬 가시화.
3. Z-clipping threshold 를 고정값에서 |Z| 의 quantile 기반으로 변경 (인스턴스별
   자동 스케일).
4. `due-window` 정렬 키를 `(max(0, d⁺−p) asc, d⁺ asc, d⁻ asc)` 로 교체
   (사각형 시작 x 로 1차 정렬 → 행의 rect 가 왼쪽→오른쪽 단조 증가).

## 사각형 (rect) 정의

- **높이**: 행 index `i` 기준 `[i - 0.5, i + 0.5]` (categorical y-axis 위 numeric 오버레이)
- **길이**: `p_j`
- **시작 x**: `max(0, d⁺_j − p_j)`
- **끝 x**: `start + p_j` (완료 시점이 `d⁺_j` 와 일치)
- **스타일**: `line={"color": "black", "width": 1}`, `fillcolor="rgba(0,0,0,0)"`,
  `layer="above"`

## Y-axis 라벨

- 포맷: `f"{job_id}({p_j:>3})"` — 예: `j018(  3)`, `j000( 27)`, `j013( 89)`
- `yaxis.tickfont.family = "Courier New, monospace"` 로 정렬 확보
- `yaxis_title = "job (p)"`

## Clipping threshold

- 모듈 상수 `CLIP_QUANTILE` 로 분리.
  - `visualize_parallel_mc_cost.py`: `CLIP_QUANTILE = 0.25`
  - `visualize_wET_cost.py`: `CLIP_QUANTILE = 0.5`
- `threshold = float(np.quantile(np.abs(Z), CLIP_QUANTILE))` 계산 후
  `Z = np.clip(Z, -threshold, threshold)`.
- 기존 고정 `MAX_Z_ABS = 100` 제거.

Why 다른 quantile: C 는 `ceil(.../pj)` 로 스케일이 작고 분포가 얕아 Q1(25%)
이 적절. wET 는 선형이라 Q2(50%) 가 더 보기 좋음.

## 정렬 (`--sort due-window`)

- 키: `lambda j: (max(0, ddw[j][1] - p[j]), ddw[j][1], ddw[j][0])`
  - 1차: `max(0, d⁺ − p_j)` 오름차순 (= rect 시작 x, rect 좌단 위치)
  - 2차: `d⁺` 오름차순
  - 3차: `d⁻` 오름차순
- `_sort_jobs` 내부에서 `instance.get_job_2_p_map_for_stage(...)` 를 호출해
  `p` 확보 (build_* 와 동일 stage = `stage_id_list[-1]`).
- `--sort neh-cp` 는 기존대로 `instance.get_neh_cp_job_sequence()` 유지.
- CLI `--help` 문자열: `"max(0, d⁺−p) asc, then d⁺ asc, then d⁻ asc"`.

## 반환 시그니처

두 `build_*` 모두 `(y_labels, t_axis, Z, rects)` 반환.

- `y_labels: list[str]` (기존 `calJ` 자리)
- `rects: list[tuple[float, float, int]]` — `(x0, x1, row_index)`

## 구현 포인트

- `build_*` 의 단일 job loop 안에서 `rects`, `y_labels` 를 함께 누적 (재조회 없음).
- `_weights_or_default` 덕분에 `wm`, `wp` 는 항상 ≥ 1 (현재 rect 는 무관하지만
  Z 계산에는 여전히 사용).
- `make_figure` 는 `rects` 를 받아 `fig.update_layout` 직후 각 shape 추가 —
  Plotly 는 categorical y-axis 에서도 shape 의 numeric 좌표를 category index 로
  처리 (`autorange=reversed` 와 무관).

## 변경 파일 / 함수

- `benchmarks/PRA2017/visualize_parallel_mc_cost.py`
  - `_sort_jobs` — key 교체
  - `build_signed_cost_matrix` — `y_labels`/`rects`/`threshold` 추가, 반환 튜플 확장
  - `make_figure` — signature 에 `y_labels`, `rects` 반영, monospace tickfont,
    shape loop 추가
  - `main` — unpacking 및 호출 업데이트, `--sort` help 문자열 수정
  - 모듈 상수: `MAX_Z_ABS` 제거, `CLIP_QUANTILE = 0.25` 추가
- `benchmarks/PRA2017/visualize_wET_cost.py` — 위와 대칭. `CLIP_QUANTILE = 0.5`.

## Verification

1. `uv run ruff check benchmarks/PRA2017/visualize_parallel_mc_cost.py
   benchmarks/PRA2017/visualize_wET_cost.py`
2. `uv run ruff format ...`
3. 렌더:
   ```
   uv run python benchmarks/PRA2017/visualize_wET_cost.py \
       --instance benchmarks/PRA2017/large/Instance_200_10_5_0,6_1_20_Rep4.txt
   uv run python benchmarks/PRA2017/visualize_parallel_mc_cost.py \
       --instance benchmarks/PRA2017/large/Instance_200_10_5_0,6_1_20_Rep4.txt
   ```
4. 결과 HTML 체크:
   - 각 행에 `jXXX(  p)` 포맷 라벨, monospace 로 정렬.
   - 각 행에 사각형 1개, 폭 = `p_j`, 우측 끝 = `d⁺_j` (또는 `p_j` if `d⁺_j < p_j`).
   - 색상 범위가 인스턴스-스케일에 맞춰 분포 (고정 ±100 아님).
   - `--sort neh-cp` 로 돌리면 라벨/사각형도 그 순서로 재배치되는지 확인.

## Non-goals

- `visualize_cost.py` (단일-mc) 등 다른 시각화 스크립트는 범위 밖.
- colorscale (`RdBu_r`), colorbar 구성은 그대로.
- `p_j` 가 4+ 자리인 경우는 현재 `:>3` 포맷이 확장되지 않음 (데이터상 문제 없음).

## History (iteration notes)

1. 최초: rect 시작 = `max(0, d*_j − p_j)` (`d*_j` = weighted due-window midpoint),
   `MAX_Z_ABS = 100` 고정.
2. y-axis 라벨에 `p_j` 병기 + monospace.
3. `MAX_Z_ABS` 폐기 → `CLIP_QUANTILE` 로 교체.
4. `due-window` 정렬 키를 `(d⁺ asc, d⁻ asc)` 로 변경; rect 기준점을 `d*_j` → `d⁺_j`
   로 이동 (`x0 = max(0, d_plus − p_j)`, 완료 시점 = `d⁺_j`).
5. 정렬 키 1차를 rect 좌단 `max(0, d⁺ − p_j)` 로 확장 —
   최종 `(max(0, d⁺−p) asc, d⁺ asc, d⁻ asc)`. 행마다 rect 위치가 좌→우
   단조 증가하게 정렬되어, 전체 heatmap 이 대각선에 가까운 배치를 가진다.
