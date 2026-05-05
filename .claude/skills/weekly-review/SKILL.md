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

**`--oneline`만 보지 말 것.** RUN 시점과 머신 정보가 commit body에 들어가는 경우가 있다. body까지 읽어야 누락이 없다.

```bash
# 제목 + body 함께
git log --format="%n===== %h =====%n%s%n---body---%n%b" <from_commit>..HEAD
```

분류 기준 — 두 가지 경로 모두 확인:

1. **제목에 timestamp가 있는 경우**: `20260428T165900_623730 run setting` 형식 → 실험 RUN 시점.
2. **commit body에 timestamp가 있는 경우**: 기능 commit(`feat(...)`, `refactor(...)` 등)이라도 body에 `<timestamp> run setting on <machine>` 같은 줄이 있으면 그 commit도 RUN 시점이다. 절대 빠뜨리지 말 것.

다음 grep으로 누락 검출 (제목·body 통틀어 모든 timestamp):

```bash
git log --format="%H%n%s%n%b" <from_commit>..HEAD | grep -Eo "[0-9]{8}T[0-9]{6}_[0-9]+" | sort -u
```

이 결과에 나타나는 모든 timestamp가 RUN 시점이다.

그 외 commit → 기능 변경/버그 수정 (phase 경계 구분 재료).

### Step 1-2. 각 RUN 정보 수집

run setting commit 각각에 대해:

```bash
# 해당 commit 시점의 main.py에서 CONFIG_PATH 확인
git show <commit>:main.py | grep CONFIG_PATH

# config YAML 읽기
git show <commit>:<config_path>

# commit body에서 머신 정보 추출
git log -1 --format="%b" <commit>
```

수집 항목:
| 항목 | 출처 |
|---|---|
| timestamp | commit 제목 OR commit body (둘 다 검색) |
| config 경로 | main.py `CONFIG_PATH` |
| scenarios / subroutine_flow | config YAML |
| timelimit, instance_worker_cnt | config YAML |
| scope | config `# ins_index` 주석 유무 → full / tail N |
| output_dir | config `output_dir` |
| machine | **commit body에서 `computer: <name>` 또는 `on <machine>` 패턴 검색**. 못 찾으면 TBD → 사용자 확인 |

머신 이름 정규화:
- `mso2` → `mso02` (사용자 표기 차이 — 동일 머신).
- 신규 이름 발견 시 사용자에게 확인.

**자동 정규화 점검**: body에서 추출한 머신 이름과 RUN별 timestamp가 일관된지 자동 grep:

```bash
# body의 모든 머신 표기 — 표기 차이 자동 감지
git log --format="%b" <from_commit>..HEAD | grep -oE "(computer:|on) [a-zA-Z0-9_]+" | sort -u

# 출력에 mso2/mso02/MSO02 등이 섞여 있으면 정규화 필요 — 사용자에게 확인.
```

새 표기 발견 시: 사용자에게 "이 표기가 기존 머신의 다른 이름인가, 신규 머신인가?" 1회 확인 후 normalization map에 추가.

output 존재 확인:
```bash
find output/ -maxdepth 3 -name "<timestamp>_summary.csv" 2>/dev/null
```

**시나리오 실측 검증 (필수)**: commit body의 `=` 표기(예: `T030753 = T004917`)는 "비교 대상"을 뜻할 뿐 실제 시나리오/config가 같다는 보장이 아니다. **반드시 `summary.csv`의 실제 시나리오를 확인**해서 Run Index의 config·scope 필드를 보정한다:

```bash
# RUN의 실제 시나리오 목록과 행 수
uv run python -c "
import pandas as pd
df = pd.read_csv('output/<date>/<timestamp>/<timestamp>_summary.csv')
print(f'rows={len(df)}, scenarios={df[\"scenarioName\"].unique().tolist()}')
"
```

config YAML이 시나리오 N개인데 summary.csv가 1개만 있으면 → 실제 실행은 1개 시나리오만 (사용자가 임시 수정한 config 또는 다른 config 사용). Run Index의 config 컬럼에 "(`<actual_scen_name>` scen만)" 같은 단서 추가.

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

### Step 2-0. Phase 1 초안 commit 권유

Phase 2는 doc을 대량으로 수정한다 (RUN별 결과 표 + 결과 요약 섹션). 삽입 스크립트 실수 시 `git restore`로 되돌릴 수 있도록 **Phase 1 끝나면 사용자에게 commit 권유**:

```bash
git status docs/reviews/<date>_weekly_experiments.md
# → untracked이면 사용자에게 한 줄 권유:
#   "Phase 2 들어가기 전에 초안을 commit 하시겠습니까? (선택)"
```

사용자가 거절하면 그대로 진행. 강제하지 않는다.

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

`scripts/aggregate_results_index.py`로 (RUN, scenario)별 평균 + no-incumbent fallback 처리:

```bash
uv run python scripts/aggregate_results_index.py analysis/results_index_<date>.csv
# → analysis/results_index_<date>_agg.{csv,json}
# 각 record: {run, scen, metric, mean_RPDf, mean_value, n}
#   metric == "bestObj"             → 정상 (mean_value = mean bestObj)
#   metric == "mcfLb (no incumbent)" → AlgRecord 미등록 (mean_value = mean mcfLb, mean_RPDf = None)
```

집계는 **모든 인스턴스 포함** (1440개 전부). BKS_data=0 + bestObj=0 케이스는 builder가 이미 RPDf=0으로 처리; 나머지 BKS=0 케이스는 RPDf=2.0 (max symmetric distance)로 평균에 반영. 이전에는 `BKS>0` 필터를 권장했으나 사용자 의견에 따라 제거 — BKS=0 인스턴스의 lose도 알고리즘 평가의 일부로 본다.

특이 케이스:
- **`mcf_lb_only` RUN**: 설계상 schedule 없음 → `bestObj` NaN, `mcfLb`만 채워짐 → "결과 없음 (LB only)" 표기.
- **신규 step 도입기 RUN — incumbent 미등록**: `hasIncumbent=False`, `reportCount=0`인 경우 (이번 주 RUNs 4–9가 그랬음 — `single_pass_last_stage_only_sch_from_mcf_lb` 도입 직후 controller가 결과를 `AlgRecord`에 등록하지 못한 케이스). `bestObj` 전체 NaN, `mcfLb`만 의미 있음 → 별도 표 형식으로:
  ```markdown
  - **결과** *(no incumbent — algorithm did not register a full schedule; only `mcfLb` populated)*:

    | scenarioName | mean mcfLb | n |
    |---|---|---|
    | scenario_a | 40,847 | 1382 |
  ```
  결과 요약의 "전체 RUN 순위"에서는 제외하고 별도 "결과 없음" 블록에 모은다.
- **`scope: tail5` RUN**: "(tail N 인스턴스 기준)" 부기.
- **복수 시나리오 RUN**: 시나리오별 행 분리.
- **시나리오 ≥ 20개인 RUN (큰 그리드)**: 전체 표 박지 말고 Top 5 + Bottom 5 + 1줄 요약 권장 (예: "최저: (p+8, rx2+128) RPDf 0.6214"). 전체 표는 `analysis/...agg.csv`에서 조회.

### Step 2-3. 보고서에 삽입

#### 각 RUN subsection 하단에 결과 항목 추가

```markdown
- **결과**:

  | scenarioName | mean RPDf (BKS>0) | mean bestObj | n |
  |---|---|---|---|
  | scenario_a | 0.3207 | 143,019 | 1382 |
```

**Insertion anchor — "다음 `---`"가 아니라 "다음 RUN 헤딩"**. Phase 안에 여러 RUN이 연속으로 있으면 `---`는 Phase 끝까지 안 나오므로 RUN N의 snippet이 다음 RUN(들)을 건너뛰고 Phase 끝에 박힐 수 있다. 정확한 anchor:

> 다음 `### RUN ` / `#### RUN ` / `## ` (Phase 헤딩) 중 가장 가까운 것 **직전**.

`### RUN`만 매칭하지 말 것 — `#### RUN ` (parent block 안의 sub-RUN, 예: Phase 9의 perf A/B/B/A) 도 반드시 처리. 정규식 예:

```python
import re
pattern = re.compile(
    r"^(#{3,4} RUN (\d+) — `([^`]+)` \([^)]+\)\n(?:.*\n)*?)"
    r"(?=^#{2,4} |\Z)",  # 다음 ##/###/#### 헤딩 또는 파일 끝까지
    re.MULTILINE,
)
```

삽입 후 다음 헤딩과 사이에 빈 줄 두 개(`\n\n`) 보장. 삽입 직전 idempotency 가드: `if "- **결과**" in matched_block: skip` — 단, 가드는 매칭된 RUN의 block 안에서만 검사 (다른 RUN의 misplaced 결과 표를 잡지 않도록).

#### `## 결과 요약` 섹션을 `## 큰 흐름 요약` 바로 앞에 삽입

표준 4-블록 구조 (이번 주에 자생적으로 만든 형식 — 일관성 위해 다음 주에도 동일하게):

```markdown
## 결과 요약

**Source**: `analysis/results_index_<date>.csv` (제외 RUN 명시 — 예: hjt5950x 산출물 미보유 시. N RUN × M valid 인스턴스, BKS>0).

### Top 20 시나리오 (mean RPDf 오름차, 낮을수록 우수)

| 순위 | RUN | timestamp | scenarioName | mean RPDf | mean bestObj |
|---|---|---|---|---|---|
| 1 | ... | ... | ... | 0.xxxx | xxx,xxx |

### Bottom 10 (mean RPDf 내림차)

| 순위 | RUN | scenarioName | mean RPDf | mean bestObj |
|---|---|---|---|---|

### RUN별 최우수 시나리오 (mean RPDf 기준)

| RUN | best scenario | mean RPDf | mean bestObj |
|---|---|---|---|

### 결과 없음 (incumbent 미등록 — `mcfLb`만 채워진 RUN)

RUN <list> — 사유 (예: 신규 step 도입기 controller 미완 / `mcf_lb_only` 설계상 schedule 없음).
모두 `mcfLb_mean ≈ <value>` (N valid 인스턴스, BKS>0).

### 주요 관찰

- **최우수**: RUN N `<scen>` — mean RPDf X.XXXX (배경 한 줄).
- **이번 주 알고리즘 라인의 최우수**: RUN N `<scen>` — RPDf X.XXXX (전주 best 대비 +YY 차이).
- **<knob 1> 효과** (해당 RUNs): 추세 한 줄.
- **<knob 2> 효과** (해당 RUNs): 추세 한 줄.
- **Cross-RUN delta** (같은 config·시나리오 셋 반복 RUN): N→N+1 변화량 한 줄.
- **perf change**: 시간 단축 % + wET 동일 확인.
```

**숫자 신중**: 주요 관찰의 RPDf·bestObj 수치는 **반드시 agg 데이터에서 인용**. body 메모(`60.90 → 33.05` 같은 timing 메모)나 commit message의 추정치는 별도로 표기.
