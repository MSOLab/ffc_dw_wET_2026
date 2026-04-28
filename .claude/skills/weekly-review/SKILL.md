---
name: weekly-review
description: >
  이 프로젝트(ffc_ddw_sum_et)의 주간 실험 리뷰 문서를 두 단계로 작성한다.
  사용자가 "주간 리뷰", "실험 정리", "weekly review", "docs/reviews 만들어",
  "실험 결과 보고서", "보고서 초안", "결과 분석 추가" 등을 언급하면
  반드시 이 skill을 사용한다.
  Phase 1: git commit 내역 분석 + 사용자 확인 → docs/reviews/<date>_weekly_experiments.md 초안.
  Phase 2: scripts/build_results_index.py 실행 → analysis/results_index_<date>.csv 로드
  → 각 RUN·시나리오 결과 분석 섹션 추가.
---

## § 0 — 시작 전 확인

사용자에게 확인:
- **날짜 범위**: `<from_commit>` (또는 날짜)부터 HEAD까지. 명시 안 됐으면 질문.
- **출력 파일명**: `docs/reviews/<end_date>_weekly_experiments.md`
- **결과 인덱스**: `analysis/results_index_<end_date>.csv`

---

## § 1 — Phase 1: 보고서 초안

### Step 1-1. Commit 탐색

```bash
git log --oneline <from_commit>..HEAD
```

분류 기준:
- `20260428T165900_623730 run setting` 형식 (`*T*_* run setting`) → 실험 RUN 시점
- 그 외 → 기능 변경/버그 수정 (phase 경계 구분 재료)

### Step 1-2. 각 RUN 정보 수집

run setting commit 각각에 대해:

```bash
# 해당 commit 시점의 main.py에서 CONFIG_PATH 확인
git show <commit>:main.py | grep CONFIG_PATH

# config YAML 읽기
git show <commit>:<config_path>
```

수집 항목:
| 항목 | 출처 |
|---|---|
| timestamp | commit 제목 |
| config 경로 | main.py `CONFIG_PATH` |
| scenarios / subroutine_flow | config YAML |
| timelimit, instance_worker_cnt | config YAML |
| scope | config `# ins_index` 주석 유무 → full / tail N |
| output_dir | config `output_dir` |
| machine | 알 수 없으면 TBD → 사용자 확인 |

output 존재 확인:
```bash
find output/ -maxdepth 3 -name "<timestamp>_summary.csv" 2>/dev/null
```

### Step 1-3. 사용자 확인 (초안 작성 전 필수)

**모호한 항목이 하나라도 있으면 반드시 먼저 질문한다.** 초안을 먼저 쓰지 않는다.

확인 목록 (해당 항목만):

| 상황 | 질문 내용 |
|---|---|
| config만으로 의도·가설 불명확한 RUN | 실험 의도·가설을 직접 서술해달라 |
| `find` 결과 없는 RUN | 출력이 로컬에 없는지, 폐기됐는지, 다른 RUN으로 대체됐는지 |
| run setting 없는 feature commit 시점 | 해당 commit 기준으로 실험을 수행했는지 |
| 동일 시간대 복수 machine | 병렬 실험 여부 및 machine 이름 |
| 결과가 겹치는 RUN | 어떤 RUN을 폐기·통합할지 |

정보가 충분해지면 Step 1-4로 진행.

### Step 1-4. 보고서 초안 작성

**형식**: `docs/reviews/20260428_weekly_experiments.md` 를 그대로 따른다. 아래는 섹션 구조 요약.

```
# Experiment Review: <start> ~ <end>

**Range**: <from_commit> (`<one-line description>`) → HEAD
**Benchmark**: PRA2017 large (1440 instances) · `benchmarks/PRA2017/pra2017_hybrid_match.csv`
**Reported objective**: weighted earliness + tardiness (wET)

---

## Tracked Experiments

총 **N개** 실험 시점.

[통합·폐기 규칙 — 사용자 확인 내용 반영]

---

## Run Index

| # | timestamp | machine | config file | source commit | scope | output_dir |
|---|---|---|---|---|---|---|
| 1 | ... | ... | ... | ... | full (1440) | output/... |

---

## Phase 1 — [제목]

기간 commit: `hash1`, `hash2`, ...

핵심 작업
- [변경 1]
- [변경 2]

### RUN 1 — `20260423T114900_417063` (machine)

- **Config**: `metadata/.../config.yaml`
- **변경**: [전 RUN 대비 변경사항]
- **의도**: [실험 가설]
- **비고**: [있을 때만]

...

---

## 큰 흐름 요약

1. Phase 1 — [한 줄]
...

---

## Appendix A — Output Layout
## Appendix B — Result Columns
## Appendix C — Cross-RUN Scenario Equivalence Map
## Appendix D — Results Index Builder
```

Phase 경계: 알고리즘·기능 단위로 묶인 feature commit 그룹.  
RUN subsection 필수: Config, 변경, 의도. 비고는 있을 때만.

---

## § 2 — Phase 2: 결과 분석 추가

Phase 1 초안이 완성된 후 사용자가 요청할 때 실행.

### Step 2-1. 인덱스 빌드

`analysis/results_index_<date>.csv` 존재 여부 확인:
```bash
ls -lh analysis/results_index_<date>.csv 2>/dev/null
```

없거나 재실행이 필요하면:
```bash
uv run python scripts/build_results_index.py
```

이미 있으면 재실행 여부를 사용자에게 확인한 후 진행.

### Step 2-2. 결과 집계

```python
import pandas as pd

df = pd.read_csv("analysis/results_index_<date>.csv", low_memory=False)

# BKS>0 인스턴스만 사용 (BKS=0이면 RPDf 해석 불안정)
valid = df[df["BKS_data"] > 0]

summary = (
    valid.groupby(["runNumber", "scenarioName"])
    .agg(
        mean_RPDf=("RPDf_BKS_data", "mean"),
        mean_bestObj=("bestObj", "mean"),
        n_instances=("instanceName", "count"),
    )
    .round(4)
    .reset_index()
)
```

특이 케이스:
- `bestObj` 전체 NaN인 RUN (mcf_lb_only 등) → "결과 없음 (LB only)" 표기
- `scope: tail5` RUN → "(tail N 인스턴스 기준)" 부기
- 복수 시나리오 RUN → 시나리오별 행 분리

### Step 2-3. 보고서에 삽입

**각 RUN subsection 하단**에 결과 항목 추가:
```markdown
- **결과**:

  | scenarioName | mean RPDf (BKS>0) | mean bestObj | n |
  |---|---|---|---|
  | scenario_a | 0.3207 | 143,019 | 1382 |
```

**큰 흐름 요약 바로 앞**에 `## 결과 요약` 섹션 삽입:
```markdown
## 결과 요약

### 전체 RUN 순위 (mean RPDf, BKS>0 기준, 낮을수록 우수)

| 순위 | RUN | timestamp | scenarioName | mean RPDf | mean bestObj |
|---|---|---|---|---|---|
| 1 | 13 | 20260426T174905_399637 | neh_cp_... | 0.3082 | 143,019 |
...

### 주요 관찰

- 최우수: ...
- 최하위: ...
- 특이 사항: ...
```
