# Batch Size Analysis Scripts

RPDf 성능에 미치는 batch size 영향을 통계적으로 분석하고 시각화하는 스크립트 모음.

## 분석 폴더 (`ANALYSIS_DIR`)

각 스크립트 상단에 `ANALYSIS_DIR` 상수가 정의되어 있고, 모든 입출력 경로(입력 CSV, 결과 CSV, 결과 PNG)가 이 상수를 기준으로 동작한다. 현재 값:

```python
ANALYSIS_DIR = Path("analysis/diff/20260426_batch_size")
```

- 커맨드 라인 인자로 전달되는 CSV 이름은 **파일명만** 사용되어 `ANALYSIS_DIR / <basename>` 으로 해석된다 (경로 prefix가 붙어 있어도 무시되고 basename만 사용).
- 다른 분석 폴더로 옮겨가려면 4개 스크립트 모두에서 `ANALYSIS_DIR` 줄을 새 경로로 바꾸면 된다.
- 출력 파일도 모두 `ANALYSIS_DIR` 안에 저장된다.

## 실행 방법

```bash
# 기본 (ANALYSIS_DIR/batch_size_5_10_15.csv 사용)
uv run python scripts/analyze_batchsize_deep_dive.py

# 커스텀 CSV (파일은 ANALYSIS_DIR 안에 있어야 함)
uv run python scripts/analyze_batchsize_deep_dive.py batch_size_5_10_15_20.csv
```

## 스크립트 목록

### 1. analyze_batchsize_regression.py

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

### 2. analyze_batchsize_deep_dive.py

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

### 3. visualize_batchsize_evidence.py

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

### 4. visualize_batchsize_15vs20.py

4-way 실험 (5/10/15/20)에서 bs=15와 bs=20의 차이에 집중. "더 큰 batch size가 항상 더 나은가"라는 질문에 답변.

**입력**: `analyze_batchsize_deep_dive.py batch_size_5_10_15_20.csv`가 생성한 CSV 파일들 (`ANALYSIS_DIR` 안에서 자동 로드)

**출력 파일** (`ANALYSIS_DIR` 안에 저장):
- `{prefix}_evidence_15vs20.png` — 4패널 (bs15 vs bs20 집중)
  - diff_15vs20 히스토그램
  - Key statistics (차이, win rate, diminishing returns)
  - 4개 batch size 예측 곡선 (vs R)
  - R×n 추천 행렬
- `{prefix}_evidence_all.png` — 6패널 (모든 batch size 비교)

## 실행 순서

```
analyze_batchsize_deep_dive.py  →  visualize_batchsize_evidence.py
         (CSV 생성)                        (CSV 읽어서 그림 생성)
```

`analyze_batchsize_regression.py`는 독립적으로 실행 가능한 빠른 탐색용 스크립트.
`visualize_batchsize_15vs20.py`는 4-way 데이터 전용.
