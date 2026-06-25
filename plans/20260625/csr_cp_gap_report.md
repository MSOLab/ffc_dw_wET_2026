# Plan: CSR 최종 CP gap 보고서 (`cp_gap_comparison.csv` + `cp_gap_dashboard.html`)

## Context

`coarsen_solve_reconstruct`(CSR) factor sweep 실험
(`output/20260625/20260625T032109_514704`)에서 각 인스턴스의 **최종 CP gap**을
계산해, 기존 `rpdf_dashboard.html` / `mcf_lb_dashboard.html`과 같은 스타일의
PivotTable.js 보고서를 만든다. RQ1("극단적으로 coarsen하면 base CP가 빠르게
optimal을 증명하는가?")을 factor별로 한눈에 보기 위한 산출물이다.

### 사용자 확정 사항

- **대상**: `_v3` 시나리오만 분석한다. `_v3`/`_mixed`는 initialization 차이이며
  이번 분석은 v3만 본다.
- **UB/LB 소스**: coarsened CP solver의 **최종 UB/LB** = per-instance
  `progress/<instance>_csr_cp_trajectory.json`의 끝점.
  `obj_value`의 마지막 non-null 값 = UB, `obj_bound`의 마지막 값 = LB.
  **coarsened scale**이다. summary.csv의 `bestObj`(원척도 reconstruct 목적값)나
  `bestBound`(v3 전부 0)는 쓰지 않는다.
- **gap 정의** (instance별):
  - `UB == 0 and LB == 0` → 모든 gap = `0`.
  - `LB != 0` → `lb_gap = (UB - LB) / LB`, `solver_gap = (UB - LB) / UB`.
  - `LB == 0` (이고 `UB > 0`) → `lb_gap`은 **빈칸(NaN)**, `solver_gap = (UB - LB) / UB`.
- **산출물 전달**: 공용 builder 함수를 두고 (a) standalone 스크립트와
  (b) reporting 파이프라인 양쪽에서 호출한다(DRY).

### 읽은 코드 / 데이터 (검증 완료)

- `output/.../20260625T032109_514704_summary.csv`: `bestObj`=원척도 reconstruct
  obj, `bestBound`=v3 전부 `0.0`. coarsened CP UB/LB는 여기 **없다**.
- `output/.../<scenario>/<instance>/progress/<instance>_csr_cp_trajectory.json`:
  `{"elapsed_sec": [...], "obj_value": [..,UB], "obj_bound": [..,LB]}`.
  예) factor=1 한 인스턴스 → 끝점 UB=115492.0, LB=2902.0.
  (reconstruct 후처리로 instance_result obj_value=111991은 CP UB와 다름 → CP gap은
  trajectory 끝점으로 계산해야 함을 확인.)
- `src/ffc_ddw_sum_et/orchestration/post_run_pivot.py`:
  `build_rpdf_comparison_df`(summary + hybrid_match + bks_table join 패턴),
  `write_pivot_html`, `PERCENT_AGGREGATORS_JS`, `write_post_run_pivot_artifacts`.
  → 본 작업의 직접 레퍼런스. cp_gap builder를 같은 파일에 추가하고 join 로직을 재사용.
- `src/ffc_ddw_sum_et/orchestration/reporting.py`:
  - `_write_post_run_pivot_artifacts`(685–706): rpdf/win_tie/time% 호출 지점.
  - `_render_mcf_lb_dashboard`(1405–1462): per-scenario CSV concat → `write_pivot_html`
    패턴(참고).
  - 1879–1896: `self.scenario_results` → `sc.instance_results` → `ir.instance_name`,
    `layout.artifact_path("csr_cp_trajectory_json", scenario_name=..., instance_name=...)`
    로 trajectory를 찾는 정석 iteration.
- `metadata/artifact_layout/ffc_ddw_sum_et_v1.yaml`: run-scope artifact kind 등록 형식
  (`rpdf_comparison_csv`, `rpdf_dashboard`, `mcf_lb_dashboard` 참고). 새 kind 2개 추가.
- `src/ffc_ddw_sum_et/orchestration/artifact_layout.py`: `restore_layout_from_run_dir`
  (스크립트가 기존 run 디렉터리에서 layout 복원).
- `scripts/build_cross_run_flow_chart.py`: run/scenario 디렉터리를 인자로 받는 스크립트
  argparse/로깅 컨벤션 레퍼런스.
- `benchmarks/PRA2017/pra2017_hybrid_match.csv`, `pra2017_bks_table.csv`: instanceName →
  insIndex → (n,c,totalMcCount,T,R,W) 메타. 피벗 행/열 축(R, T)에 사용(rpdf와 동일).

## Design

### 데이터 흐름

```
run_dir
  └─ <scenario>/<instance>/progress/<instance>_csr_cp_trajectory.json
        obj_value[-1 non-null] = UB,  obj_bound[-1] = LB   (coarsened scale)
                │
                ▼  (scenario, instance, UB, LB, cp_elapsed) long rows
        + scenarioName → factor, init 파싱  (csr{F}_{v3|mixed})
        + instanceName → insIndex → (n,c,totalMcCount,T,R,W)  [hybrid_match + bks_table join]
        + gap 계산 (lb_gap, solver_gap)
                │
                ▼
        cp_gap_comparison.csv  ──►  cp_gap_dashboard.html (PivotTable.js heatmap)
```

### 1. 공용 builder (`post_run_pivot.py`)

기존 `build_rpdf_comparison_df`의 hybrid_match+bks_table merge 패턴을 재사용한다.
중복을 줄이기 위해 merge 부분을 작은 helper로 추출한다(아래 DRY 참고).

추가 심볼:

- `CP_GAP_COMPARISON_COLUMNS: tuple[str, ...]`:
  `("insIndex", "scenarioName", "factor", "n", "c", "totalMcCount", "T", "R", "W",
    "cp_ub", "cp_lb", "lb_gap", "solver_gap", "cp_elapsed")`
- `read_csr_cp_trajectory_endpoint(path: Path) -> tuple[float | None, float | None, float | None]`
  - trajectory json을 읽어 `(ub, lb, elapsed)` 반환.
  - `ub` = `obj_value`의 마지막 non-null (전부 null/빈 배열이면 `None`).
  - `lb` = `obj_bound`의 마지막 값 (빈 배열이면 `None`).
  - `elapsed` = `elapsed_sec`의 마지막 값 (없으면 `None`).
- `compute_cp_gaps(ub, lb) -> tuple[float | None, float | None]`
  - 위 "gap 정의" 그대로. 반환 `(lb_gap, solver_gap)`.
  - `ub is None`(무해 해) 또는 `lb is None`(bound 미기록) → `(None, None)`.
  - `ub == 0 and lb == 0` → `(0.0, 0.0)`.
  - `lb != 0` → `lb_gap = (ub-lb)/lb`; `lb == 0 and ub > 0` → `lb_gap` 빈칸.
  - `solver_gap = (ub-lb)/ub` (단, `ub == 0`이면 None).
  - **단일 진실 소스**: gap 규칙은 이 함수 하나에만 둔다.
- `collect_cp_gap_rows(run_root: Path, *, init_filter: str | None = "v3") -> pd.DataFrame`
  - `run_root` 아래 `*/*/progress/*_csr_cp_trajectory.json`를 glob.
    경로에서 scenario = `parts[-4]`, instance = `parts[-3]`.
  - `init_filter`가 주어지면 `scenarioName.endswith(f"_{init_filter}")`만 채택
    (기본 `"v3"`; `None`이면 전체).
  - 각 파일에서 endpoint 읽어 long row `[instanceName, scenarioName, cp_ub, cp_lb, cp_elapsed]`.
  - `scenarioName`에서 `factor`(정수), `init`(`v3`/`mixed`) 파싱. 정규식 `csr(\d+)_(\w+)`.
- `build_cp_gap_comparison_df(run_root, hybrid_match_csv, bks_table_csv, *, init_filter="v3") -> pd.DataFrame`
  - `collect_cp_gap_rows` → instance 메타 merge → `compute_cp_gaps` 적용 →
    `CP_GAP_COMPARISON_COLUMNS` 순서로 정렬(`insIndex`, `scenarioName`).
- `write_cp_gap_artifacts(run_root, layout, hybrid_match_csv, bks_table_csv, *, init_filter="v3") -> None`
  - comp_df 생성 → `layout.artifact_path("cp_gap_comparison_csv")`에 CSV 저장.
  - `write_pivot_html(..., layout.artifact_path("cp_gap_dashboard"), ...)`로 대시보드 생성.
    - `initial_state = {"rows": ["scenarioName", "R"], "cols": ["T"],
       "vals": ["solver_gap"], "aggregatorName": "Average", "rendererName": "Heatmap"}`
    - `aggregators_js=PERCENT_AGGREGATORS_JS` (gap은 비율 → %로 표시; rpdf 대시보드와 동일).
    - `title="CP gap Pivot"`.
  - hybrid_match/bks가 없으면 (rpdf와 동일하게) 로그 남기고 skip — 단, 메타 없이도
    cp_gap은 계산되므로 join 실패 시 메타 컬럼을 비우고라도 CSV는 낸다(아래 Risks 참고).

#### DRY: instance 메타 merge 추출

`build_rpdf_comparison_df`(post_run_pivot.py:80–82)의
`df.merge(match,...).merge(bks,...)` 블록을 helper로 추출:

```python
def _merge_instance_meta(df, hybrid_match_csv, bks_table_csv) -> pd.DataFrame:
    """df(instanceName 포함)에 insIndex + (n,c,totalMcCount,T,R,W,BKS_data) 병합."""
```

`build_rpdf_comparison_df`와 `build_cp_gap_comparison_df`가 공유한다. BKS_data는
cp_gap에 불필요하므로 helper는 메타 + BKS_data를 모두 반환하고 호출 측에서 필요한
컬럼만 취한다.

### 2. Standalone 스크립트 (`scripts/build_cp_gap_report.py`)

- argparse: 위치 인자 `run_dir`(Path), 옵션:
  - `--hybrid-match` (기본 `benchmarks/PRA2017/pra2017_hybrid_match.csv`)
  - `--bks-table` (기본 `benchmarks/PRA2017/pra2017_bks_table.csv`)
  - `--init` (기본 `v3`; `all`이면 필터 없음)
- `RunRoot`/`restore_layout_from_run_dir(run_root)`로 layout 복원 후
  `write_cp_gap_artifacts(...)` 호출.
- 산출물: `<run_dir>/<run_id>_cp_gap_comparison.csv`,
  `<run_dir>/<run_id>_cp_gap_dashboard.html`.
- 즉시 사용: `uv run python scripts/build_cp_gap_report.py output/20260625/20260625T032109_514704`.

### 3. 파이프라인 통합 (`reporting.py`)

- `_write_post_run_pivot_artifacts` 바로 뒤(685행 부근)에서
  `write_cp_gap_artifacts(run_root, self.layout, self.ins_index_source,
  self.bks_table_csv_path, init_filter="v3")` 호출.
  - trajectory가 없는 일반(non-CSR) run에서는 `collect_cp_gap_rows`가 빈 df → CSV는
    헤더만/대시보드 skip. 빈 결과면 조용히 return(다른 실험 회귀 없음).
- run_root는 `self.layout`에서 얻는다(기존 코드의 layout 사용 방식 확인 후 맞춘다).

### 4. Artifact layout 등록 (`ffc_ddw_sum_et_v1.yaml`)

`rpdf_comparison_csv`/`rpdf_dashboard` 바로 아래에 run-scope kind 2개 추가:

```yaml
  - scope: run
    kind: cp_gap_comparison_csv
    file_template: "{run_id}_cp_gap_comparison.csv"
  - scope: run
    kind: cp_gap_dashboard
    file_template: "{run_id}_cp_gap_dashboard.html"
```

## 산출물 컬럼 (cp_gap_comparison.csv)

| 컬럼 | 의미 |
| ---- | ---- |
| `insIndex` | PRA2017 instance index (zero-padded str) |
| `scenarioName` | 예 `csr16_v3` |
| `factor` | coarsen factor (정수, scenarioName 파싱) |
| `n,c,totalMcCount,T,R,W` | bks_table instance 메타 (피벗 축) |
| `cp_ub` | coarsened CP 최종 UB (trajectory obj_value 끝점) |
| `cp_lb` | coarsened CP 최종 LB (trajectory obj_bound 끝점) |
| `lb_gap` | `(UB-LB)/LB`; LB=0이면 빈칸; UB=LB=0이면 0 |
| `solver_gap` | `(UB-LB)/UB`; UB=LB=0이면 0 |
| `cp_elapsed` | trajectory 마지막 elapsed_sec (CP solve 시간 근사) |

## Work Packages

WP는 production 파일 + 전용 test 단위. 공통: `uv run python`, 변경 후
`uv run ruff check`, 필요 시 `uv run ruff format`, TDD(red→green→refactor).

### 의존성 / 실행 순서

```
WP-A (builder + gap 함수, post_run_pivot.py)  ──┬─► WP-C (스크립트)
WP-B (artifact layout kind 2개, yaml)        ──┘     │
                                                     └─► WP-D (reporting 통합)
```

- WP-A는 독립. WP-B는 독립(yaml만). WP-C/WP-D는 WP-A의 `write_cp_gap_artifacts`와
  WP-B의 kind에 의존.

### WP-A — builder + gap 계산

- **대상**: `src/ffc_ddw_sum_et/orchestration/post_run_pivot.py`
- **테스트**: `tests/orchestration/test_post_run_pivot.py` (없으면 신규)
- **구현**: §Design 1 (`read_csr_cp_trajectory_endpoint`, `compute_cp_gaps`,
  `collect_cp_gap_rows`, `build_cp_gap_comparison_df`, `write_cp_gap_artifacts`,
  `_merge_instance_meta` 추출). `build_rpdf_comparison_df`는 추출한 helper를 쓰도록
  리팩터(동작 불변).
- **Acceptance**:
  - `compute_cp_gaps`: (0,0)→(0,0); (100,40)→(1.5,0.6); (100,0)→(None,1.0);
    (None,_)→(None,None).
  - `read_csr_cp_trajectory_endpoint`: 선행 null이 있는 obj_value에서 마지막
    non-null을 UB로; 빈 배열 → None.
  - `collect_cp_gap_rows`: 합성 run 트리(`<sc>/<ins>/progress/*_csr_cp_trajectory.json`)에서
    v3만 채택, factor/init 파싱 정확.
  - `build_rpdf_comparison_df` 회귀 없음(기존 테스트 green 유지; 없으면 골든 값 1개 추가).
  - `uv run pytest tests/orchestration/test_post_run_pivot.py` green, ruff 통과.

### WP-B — artifact layout kind 등록

- **대상**: `metadata/artifact_layout/ffc_ddw_sum_et_v1.yaml`
- **테스트**: 없음. layout 로드 + `artifact_path("cp_gap_comparison_csv")` 스모크.
- **구현**: §Design 4.
- **Acceptance**: `FFcArtifactLayout`이 새 kind 2개를 등록하고
  `artifact_path`가 `{run_id}_cp_gap_comparison.csv` / `..._cp_gap_dashboard.html`
  반환. ruff 통과.

### WP-C — standalone 스크립트

- **대상**: `scripts/build_cp_gap_report.py` (신규)
- **테스트**: 없음(scripts 관례). 스모크: 실제 run에 대해 실행해 두 파일 생성 확인.
- **의존**: WP-A, WP-B.
- **먼저 읽기**: `scripts/build_cross_run_flow_chart.py`(argparse/로깅),
  `restore_layout_from_run_dir`.
- **구현**: §Design 2.
- **Acceptance**:
  - `uv run python scripts/build_cp_gap_report.py output/20260625/20260625T032109_514704`
    → `..._cp_gap_comparison.csv`(v3 7개 시나리오 × 1440 인스턴스 = 10080행) +
    `..._cp_gap_dashboard.html` 생성.
  - CSV의 `cp_ub`/`cp_lb`가 무작위 표본 인스턴스의 trajectory 끝점과 일치.
  - ruff 통과.

### WP-D — reporting 파이프라인 통합

- **대상**: `src/ffc_ddw_sum_et/orchestration/reporting.py`
- **테스트**: `tests/orchestration/`의 reporting 테스트에 케이스 추가(있으면).
- **의존**: WP-A, WP-B.
- **먼저 읽기**: `reporting.py` `_write_post_run_pivot_artifacts`(685–706).
- **구현**: §Design 3. CSR이 아닌 run에서 빈 결과면 조용히 skip(회귀 없음).
- **Acceptance**:
  - CSR run 리포트 생성 시 두 artifact가 추가로 생성.
  - 비-CSR run(trajectory 없음) 리포트 회귀 없음(빈 입력 → skip).
  - ruff 통과, 기존 reporting 테스트 green.

## Risks / Decisions

- **Scale 비교 주의**: cp_lb/cp_ub는 coarsened scale이라 factor가 다르면 절대값을
  직접 비교하면 안 된다. `lb_gap`/`solver_gap`(비율)만 factor 간 비교에 쓴다.
  대시보드 기본 vals=`solver_gap`([0,1] 범위라 heatmap 가독성이 좋음). `lb_gap`은
  LB가 작으면 폭증하므로 기본값에서 제외하고 선택형으로 둔다.
- **LB=0 대량 발생 가능성**: 큰 factor에서 CP가 LB를 0 위로 못 올리면 `lb_gap`이
  빈칸이 된다. 이는 의도된 표현(증명 실패 신호). `solver_gap`은 그 경우에도 1.0로
  채워져 비교 가능.
- **메타 join 실패**: hybrid_match/bks가 없거나 매칭 실패한 인스턴스는 메타 컬럼이
  비더라도 cp_gap 자체는 계산 가능. rpdf builder는 미매칭 행을 drop하지만, cp_gap은
  분석 손실을 피하기 위해 **left merge로 메타만 비우고 행은 유지**한다(피벗 축이 비면
  해당 인스턴스만 축에서 빠짐). 이 차이를 builder docstring에 명시.
- **mixed 제외**: 기본 `init_filter="v3"`. mixed가 필요하면 `--init all`로 포함하고
  `init` 컬럼으로 피벗 분리 가능(YAGNI: 기본은 v3만).
- **무해(no-solution) 인스턴스**: trajectory의 obj_value가 전부 null이면 UB=None →
  gap None 행. 드물지만 CSV에 남겨 추적(드롭하지 않음).
- 기존 `rpdf_*` 산출물과 파일명이 겹치지 않도록 신규 prefix `cp_gap_` 사용
  (사용자가 예시로 든 `rpdf_*` 이름은 스타일 레퍼런스로만 차용).
