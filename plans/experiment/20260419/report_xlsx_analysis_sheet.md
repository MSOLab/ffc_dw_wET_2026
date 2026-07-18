# Plan: report.xlsx 확장 — insIndex, analysis sheet, RPDf

## Context

현재 `FFcDDWReporter._write_excel_report()`는 `Dashboard`와 `Statistics` 두 시트를 flat 헤더로 생성한다. 사용자는 세 가지를 요청:

1. 두 시트에 `insIndex` 컬럼(0~1439) 추가, 기존 `Instance` 컬럼명을 `insFileName`으로 변경.
2. `Statistics` 시트에 `benchmarks/PRA2017/pra2017_instance_table.csv`(컬럼: insIndex, n, c, totalMcCount, T, R, W, BKS)를 insIndex로 join.
3. 새 시트 `analysis_long`, `analysis_wide`를 추가해 BKS 대비 RPDf를 계산·표시.

요청 목적: 실험 결과를 instance 파라미터(n, c, T, R, W) 및 BKS와 함께 분석해 최적화 품질(RPDf)을 빠르게 파악하기 위함.

## Key Decisions

- **insIndex 매핑 소스**: 기존 config의 `ins_index_source`(`pra2017_hybrid_match.csv`)를 reverse-map(`filename.stem → insIndex`)으로 재사용. 매핑 없는 instance는 빈 셀.
- **Instance metadata 소스**: `ins_index_source` 파일과 같은 디렉터리에서 `pra2017_instance_table.csv`를 자동 로드. 없으면 BKS/파라미터 컬럼은 조용히 스킵.
- **RPDf 공식**: `(obj - BKS) / ((obj + BKS) / 2)`. `obj`가 None이거나 BKS가 없으면 **빈 셀**. 분모 0이면 빈 셀.
- **Analysis 시트 형태**: long과 wide **둘 다** 생성(`analysis_long`, `analysis_wide`).

## Files to modify

### 1. `src/ffc_ddw_sum_et/orchestration/reporting.py` (주요 변경)

#### (a) `FFcDDWReporter.__init__` 확장
`ins_index_source: Path | None = None` 파라미터 추가. 전달되면 내부에서 두 가지 매핑 dict을 1회 로드:
- `self._filename_to_index: dict[str, int]` — `pra2017_hybrid_match.csv`의 `ffc_ddw_sum_et_filename` (확장자 포함 → stem)을 key로.
- `self._index_to_meta: dict[int, dict]` — 같은 디렉터리의 `pra2017_instance_table.csv`가 존재하면 insIndex → {n, c, totalMcCount, T, R, W, BKS}.

둘 다 `benchmark_loader._load_index_map()` 패턴처럼 `csv.DictReader`로 읽는다. 각각 private helper로 분리.

#### (b) `_resolve_ins_index(instance_name: str) -> int | None`
InstanceResult의 `instance_name`(filename stem)으로 insIndex 조회. 없으면 None.

#### (c) `_write_excel_report()` 수정
**Dashboard 헤더**: `["Scenario", "insIndex", "insFileName", "Obj Value", "Elapsed (s)", "Work Status", "Reports"]`
- 각 row에서 `_resolve_ins_index(ir.instance_name)` 호출. None이면 빈 셀.

**Statistics 헤더**: `["insIndex", "insFileName", "n", "c", "totalMcCount", "T", "R", "W", "BKS", "Best Obj", "First Obj", "Best Bound", "First Bound", "Improvement %", "Total Elapsed", "Report Count"]`
- instance metadata 컬럼들은 `_index_to_meta`에서 lookup. 매핑 누락 시 빈 셀.
- `_index_to_meta`가 비어있으면(테이블 파일 미존재) n/c/…/BKS 컬럼 **자체를 생략**하는 분기 처리(헤더 동적 구성).

#### (d) 신규 `_write_analysis_sheets(workbook, header_fmt, cell_fmt)`
두 시트를 생성. BKS가 로드되지 않았으면 전체 함수 early-return.

**`analysis_long` 시트** (scenario × instance 각각 한 row):
- 헤더: `["insIndex", "insFileName", "Scenario", "Obj Value", "BKS", "RPDf"]`
- 정렬: insIndex asc → Scenario 순(현재 scenario_results 순서 유지).
- RPDf 계산은 새 helper `_compute_rpdf(obj, bks) -> float | None` 사용. None/0 분모 → None.

**`analysis_wide` 시트** (insIndex당 한 row, scenario별 컬럼 쌍):
- 헤더: `["insIndex", "insFileName", "BKS", "obj_<sc1>", "RPDf_<sc1>", "obj_<sc2>", "RPDf_<sc2>", ...]`
- 구현: 내부에서 `{(insIndex, scenario_name): (obj_value, instance_name)}` dict 구성 후 insIndex 기준 row 작성.

RPDf 셀 포맷은 소수점 4자리(`"0.0000"`) number format. 결측은 빈 문자열이 아닌 `write_blank`로 남겨 Excel 집계에서 자동 제외되도록.

#### (e) `_compute_rpdf(obj, bks)` 헬퍼
```
if obj is None or bks is None: return None
denom = (obj + bks) / 2
if denom == 0: return None
return (obj - bks) / denom
```

### 2. `src/ffc_ddw_sum_et/orchestration/reporting.py` — `FFcDDWMultiScenarioRunner.post_run_process()` 변경
`FFcDDWReporter(...)` 생성 시 `ins_index_source=self.ins_index_source` 전달.
→ 따라서 Runner에도 `ins_index_source` 속성이 필요.

### 3. `FFcDDWMultiScenarioRunner.__init__`
`ins_index_source: Path | None = None` 파라미터 추가하고 `self.ins_index_source = ins_index_source`로 저장.

### 4. `main.py`
Runner 생성 시 이미 읽어둔 `ins_index_source`(37~41줄)를 runner에 전달하도록 kwargs 추가. 기존 `BenchmarkLoader`와 동일 값을 그대로 재사용(중복 로드 없음 — helper에서 1회 읽음).

### 5. `tests/orchestration/test_multi_scenario_runner.py`
Runner 생성자 호출부에 `ins_index_source=None` (또는 생략 가능하면 생략). 기존 mock은 Excel 경로를 타지 않으므로 추가 작업 거의 없음. xlsxwriter가 설치된 환경이라도 `ins_index_source=None`이면 dashboard/statistics는 기존과 동일 동작.

## Reused utilities

- `ffc_ddw_sum_et.orchestration.benchmark_loader.BenchmarkLoader._load_index_map` 로직 패턴(`csv.DictReader`로 `insIndex,ffc_ddw_sum_et_filename` 읽기) — Reporter 내부 helper에 동일 방식 적용.
- xlsxwriter `workbook.add_format({"num_format": "0.0000"})` — RPDf 셀 서식.

## Verification

1. **Unit smoke**: 기존 테스트 실행
   ```
   uv run pytest tests/orchestration/test_multi_scenario_runner.py -v
   ```
   통과 확인.

2. **Lint/format**:
   ```
   uv run ruff check src/ffc_ddw_sum_et/orchestration/reporting.py
   uv run ruff format src/ffc_ddw_sum_et/orchestration/reporting.py
   ```

3. **End-to-end 실행**: 작은 샘플로 실제 xlsx 생성 검증.
   - `metadata/20260419_lb_init_config.yaml`에서 `ins_index`를 몇 개(예: `[0, 1, 2]`)로 제한(임시)하거나, `instance_worker_cnt`를 작게 하여 빠르게 돌린다.
   - `uv run python main.py metadata/20260419_lb_init_config.yaml`
   - 결과 `output/<subdir>_report.xlsx` 열어서 확인:
     - `Dashboard` 시트: `insIndex`, `insFileName` 컬럼 존재, 값 정상.
     - `Statistics` 시트: `n, c, totalMcCount, T, R, W, BKS` 컬럼이 insIndex에 맞게 join됨.
     - `analysis_long` 시트: scenario × instance row, RPDf 계산 확인.
     - `analysis_wide` 시트: insIndex별 한 row, scenario 컬럼 쌍 확인.
   - 수식 검증: 아무 row나 잡아 `(obj - BKS) / ((obj + BKS) / 2)` 손계산과 일치.

4. **결측 케이스**: `ins_index_source`를 config에서 제거하고 실행 → xlsx는 생성되지만 `insIndex`는 빈 셀, metadata 컬럼과 analysis 시트는 생략/빈 셀로 graceful degrade.

## Out of scope

- 현 시점에 조건부 서식(data bar), 다중 레벨 헤더, 평균/최소/최대 요약 row는 제외(YAGNI). 필요 시 후속 작업.
- Scenario별 pivot summary, method progression 지표는 hybridflowshop에 있지만 현재 scenario가 1개이므로 추가하지 않음.
