# Scripts

분석 / 주간 리뷰 / 보고서 보조 스크립트 모음.

> Underscore (`_`) prefix가 붙은 스크립트(`_aggregate_*.py`, `_fix_*.py`,
> `_insert_*.py` 등)는 특정 weekly review 한 번에만 쓴 일회성 헬퍼다.
> 본 README의 상시 목록에서는 제외하며, 각 파일의 docstring에 용도와
> 폐기 조건이 명시되어 있다.

## 1. Batch Size Analysis

RPDf 성능에 미치는 batch size 영향을 통계적으로 분석하고 시각화하는 스크립트.

### 분석 폴더 (`ANALYSIS_DIR`)

각 스크립트 상단에 `ANALYSIS_DIR` 상수가 정의되어 있고, 모든 입출력 경로(입력 CSV, 결과 CSV, 결과 PNG)가 이 상수를 기준으로 동작한다. 현재 값:

```python
ANALYSIS_DIR = Path("analysis/diff/20260426_batch_size")
```

- 커맨드 라인 인자로 전달되는 CSV 이름은 **파일명만** 사용되어 `ANALYSIS_DIR / <basename>` 으로 해석된다 (경로 prefix가 붙어 있어도 무시되고 basename만 사용).
- 다른 분석 폴더로 옮겨가려면 4개 스크립트 모두에서 `ANALYSIS_DIR` 줄을 새 경로로 바꾸면 된다.
- 출력 파일도 모두 `ANALYSIS_DIR` 안에 저장된다.

### 실행 방법

```bash
# 기본 (ANALYSIS_DIR/batch_size_5_10_15.csv 사용)
uv run python scripts/analyze_batchsize_deep_dive.py

# 커스텀 CSV (파일은 ANALYSIS_DIR 안에 있어야 함)
uv run python scripts/analyze_batchsize_deep_dive.py batch_size_5_10_15_20.csv
```

### 스크립트 목록

#### analyze_batchsize_regression.py

두 개의 OLS 회귀 모델 (주효과 / 상호작용)을 피팅하고, 각 시나리오별 최적 batch size를 예측. 인스턴스별 실제 winner와 pairwise 비교도 수행.

**입력**: 커맨드 인자 또는 기본 `batch_size_5_10_15.csv` (`ANALYSIS_DIR` 안에 위치)

**출력** (콘솔):

- Model 1 / Model 2 회귀 요약
- VIF 값
- 예측 기반 최적 batch size 분포
- 실제 per-instance winner 및 pairwise 비교 결과
- R², RMSE 검증 지표

**출력 파일** (`ANALYSIS_DIR` 안에 저장):

- `batch_size_regression_recommendations.csv` — 예측 기반 추천
- `batch_size_actual_winner.csv` — 실제 winner

**용도**: batch size 효과의 빠른 탐색적 분석. 상호작용 항이 유의한지, 어떤 파라미터가 영향을 미치는지 파악.

---

#### analyze_batchsize_deep_dive.py

회귀 분석을 넘어 차이 회귀, 슬라이스별 ANOVA, 상호작용 분해, 모델 진단까지 수행하는 심화 분석. 파일명에서 자동으로 prefix를 추출하므로 3-way(5/10/15)나 4-way(5/10/15/20) 등 다양한 구성에 적용 가능.

**입력**: 커맨드 인자 또는 기본 `batch_size_5_10_15.csv` (`ANALYSIS_DIR` 안에 위치)

**분석 단계**:

- **Section 0**: 데이터 로드, pivot, batch size 쌍별 차이값 계산
- **Section 0.5**: Model 1 (주효과) / Model 2 (상호작용) 피팅
- **Section 1**: Difference Regression — `diff_avsb ~ params` 회귀로 batch size 간 차이를 설명하는 파라미터 탐색
- **Section 2**: Slicing Analysis — 각 파라미터 값별로 ANOVA + Tukey HSD 사후 검정
- **Section 3**: Interaction Effect Decomposition — Model 2 예측을 통한 상호작용 효과 분해 (z-score로 유의성 판단)
- **Section 4**: Recommendation Table — 파라미터 전 조합에 대한 예측 기반 추천 + R×n 행렬
- **Section 5**: Model Diagnostics — Nested F-test, Breusch-Pagan 검정, 잔차 분석

**출력 파일** (`ANALYSIS_DIR` 안에 저장, prefix는 입력 파일명에서 자동 추출):

- `{prefix}_model2_summary.csv` — Model 2 계수
- `{prefix}_diff_descriptive.csv` — 차이값 분포 통계
- `{prefix}_diff_regression.csv` — 차이 회귀 계수
- `{prefix}_slicing_analysis.csv` — 슬라이스별 ANOVA/Tukey 결과
- `{prefix}_interaction_effects.csv` — 상호작용 분해 결과
- `{prefix}_recommendations_full.csv` — 전 조합 추천
- `{prefix}_recommendation_matrix.csv` — R×n 행렬
- `{prefix}_model_diagnostics.csv` — 모델 진단 지표

---

#### visualize_batchsize_evidence.py

`analyze_batchsize_deep_dive.py` 결과를 시각화. "왜 특정 batch size가 다른지"를 4패널 요약과 6패널 심화 그림으로 표현.

**입력**: `analyze_batchsize_deep_dive.py`가 생성한 CSV 파일들 (`ANALYSIS_DIR` 안에서 자동 로드, 기본 prefix `batch_size_5_10_15`)

**출력 파일** (`ANALYSIS_DIR` 안에 저장):

- `{prefix}_evidence_overview.png` — 4패널 요약
  - Pairwise box plot (차이값 분포)
  - 슬라이스별 heatmap
  - R×n 추천 행렬
  - Actual winner 바 차트
- `{prefix}_evidence_detail.png` — 6패널 심화
  - 차이값 히스토그램 (threshold + mean/median 표시)
  - Key statistics 텍스트 요약
  - RPDf vs R, RPDf vs T 예측 곡선
  - ANOVA F-stat 막대그래프
  - 슬라이스별 box plot

---

#### visualize_batchsize_15vs20.py

4-way 실험 (5/10/15/20)에서 bs=15와 bs=20의 차이에 집중. "더 큰 batch size가 항상 더 나은가"라는 질문에 답변.

**입력**: `analyze_batchsize_deep_dive.py batch_size_5_10_15_20.csv`가 생성한 CSV 파일들 (`ANALYSIS_DIR` 안에서 자동 로드)

**출력 파일** (`ANALYSIS_DIR` 안에 저장):

- `{prefix}_evidence_15vs20.png` — 4패널 (bs15 vs bs20 집중)
  - diff_15vs20 히스토그램
  - Key statistics (차이, win rate, diminishing returns)
  - 4개 batch size 예측 곡선 (vs R)
  - R×n 추천 행렬
- `{prefix}_evidence_all.png` — 6패널 (모든 batch size 비교)

### 실행 순서

```plaintext
analyze_batchsize_deep_dive.py  →  visualize_batchsize_evidence.py
         (CSV 생성)                        (CSV 읽어서 그림 생성)
```

`analyze_batchsize_regression.py`는 독립적으로 실행 가능한 빠른 탐색용 스크립트.
`visualize_batchsize_15vs20.py`는 4-way 데이터 전용.

## 2. Results Index Pipeline (주간 리뷰용)

`docs/reviews/<date>_weekly_experiments.md`에 카탈로그된 RUN들의
per-instance `<timestamp>_summary.csv`를 한 장의 long-form CSV로 모으고,
이를 `(RUN, scenario)` 단위 평균 테이블로 집약하는 파이프라인.

### build_results_index.py

`docs/reviews/20260428_weekly_experiments.md`의 23개 RUN 결과를 통합.
RUN 목록과 출력 경로(`analysis/results_index_20260428.csv`)는 스크립트 안에 하드코딩되어 있다.

**출력**: `analysis/results_index_20260428.csv` — 한 행당 `(RUN, scenario, instance)`,
run-level provenance + 원본 summary 컬럼 + BKS / RPDf 조인.

```bash
uv run python scripts/build_results_index.py
```

### build_results_index_20260505.py

`docs/reviews/20260505_weekly_experiments.md`의 35개 RUN 버전.
RUN 15는 hjt5950x 머신에 있어 로컬에 summary.csv가 없으므로 의도적으로 건너뛴다.

**출력**: `analysis/results_index_20260505.csv`.

```bash
uv run python scripts/build_results_index_20260505.py
```

### aggregate_results_index.py

위 두 스크립트가 만든 long-form CSV를 읽어 `(RUN, scenario)` 요약으로 집약.
`metric` 필드는 full-schedule wET가 있으면 `bestObj`, 없으면(예: `mcf_lb_only`)
`mcfLb (no incumbent)`로 표기되며 후자는 `mean_RPDf`가 `None`이다.
BKS=0 인스턴스는 RPDf 불안정성 때문에 집약에서 제외된다.

**출력**:

- `<input>_agg.csv` — `(RUN, scenario)` 한 행 단위 평면 테이블
- `<input>_agg.json` — 위 내용의 JSON

```bash
uv run python scripts/aggregate_results_index.py analysis/results_index_<date>.csv \
    [--top 10] [--bottom 5]
```

## 3. Report Rebuild

### build_subroutine_flow_charts.py

이미 존재하는 run 디렉터리에 대해 subroutine-flow HTML 산출물 두 개를
실시간 reporting 파이프라인과 동일한 writer로 다시 그린다. 차트 코드를
수정한 뒤 같은 데이터로 그림만 갱신할 때 유용.

**입력**: run 디렉터리 (예: `output/20260507/20260507T191425_860284`).
디렉터리 안 각 시나리오의 `<instance>_obj_log.json` + manifest를 직접 읽는다.

**출력 (덮어쓰기)**:

- `<run_dir>/<scenario>/summary_method_rpdf_and_norm_time_scatter.html`
- `<run_dir>/<run_id>_multi_scenario_subroutine_flow_comparison.html`

```bash
uv run python scripts/build_subroutine_flow_charts.py <run_dir> [-v]
```

벤치마크 CSV는 기본값 `benchmarks/PRA2017/`. 다른 family면
`--bks-csv` / `--hybrid-match-csv` / `--instance-table-csv`로 override.

### build_cross_run_flow_chart.py

여러 run 디렉터리의 시나리오를 한 차트에서 비교할 때 사용. 단일 run에
한정된 `build_subroutine_flow_charts.py`와 달리, 임의의 시나리오 디렉터리
N개를 직접 받아 하나의 multi-scenario flow comparison HTML만 그린다
(per-scenario scatter는 만들지 않으므로 원본 run 디렉터리에는 아무것도
쓰지 않는다).

**입력**: 시나리오 디렉터리 N개 (positional). 각각
`<run_dir>/<scenario_name>/` 형태이며, 그 안의 인스턴스 서브폴더에 있는
`<instance>_obj_log.json` + `<instance>_instance_result.yaml` 를 직접 읽는다.

**출력**: 단일 HTML. 기본 경로는
`analysis/<YYYYMMDDTHHMMSS_uuuuuu>/cross_run_flow.html` (실행 시점 timestamp
가 자동 생성됨). `--output` 으로 override 가능.

**라벨**: 기본값은 `<run_id>/<scenario_name>` (= `<scenario_dir.parent.name>/<scenario_dir.name>`)
이라 동일 시나리오 이름이 여러 run에 있어도 자동으로 구분된다. 필요하면
`--labels` 로 positional 개수만큼 커스텀 라벨을 넘길 수 있다.

```bash
# 기본 (출력은 analysis/<timestamp>/cross_run_flow.html)
uv run python scripts/build_cross_run_flow_chart.py \
    output/20260507/20260507T191425_860284/mcf_lb_best_neh_cp_best_base_cpsat \
    output/20260507/20260507T192835_679926/mcf_lb_best_neh_cp_best_base_cpsat

# 출력 경로 override + 커스텀 라벨
uv run python scripts/build_cross_run_flow_chart.py \
    --output analysis/my_cross_run_flow.html \
    --labels run-A run-B \
    output/.../scenarioA output/.../scenarioB
```

벤치마크 CSV 기본값과 override 플래그(`--bks-csv` / `--hybrid-match-csv`
/ `--instance-table-csv`)는 `build_subroutine_flow_charts.py`와 동일.

## 4. 기타 분석 유틸

### analyze_bestobj_randomness.py

같은 시나리오를 N번 반복 실행했을 때 `bestObj` 변동성을 평가하기 위한
일회성 스크립트(2026-04-23 실험용). `/tmp/new_dirs.txt`에 적힌 timestamp
디렉터리 목록(한 줄에 하나, `output/20260423/` 기준 상대경로)을 읽어
`(instanceName, scenarioName)` 쌍별로 bestObj를 집약한다.

**출력**: `output/20260424/bestobj_randomness_summary.csv`

```bash
# /tmp/new_dirs.txt 미리 작성 후
uv run python scripts/analyze_bestobj_randomness.py
```

> 입력 파일 경로(`/tmp/new_dirs.txt`)와 base dir(`output/20260423`),
> 출력 경로 모두 하드코딩이라 다른 배치에 재사용하려면 코드를 직접 수정해야 한다.
