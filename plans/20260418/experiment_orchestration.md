# Plan: hybridflowshop-style experiment orchestration for ffc_ddw_sum_et

## Context

ffc_ddw_sum_et의 main.py는 현재 단순 stub(`print("Hello")`)이다. hybridflowshop/main.py의 experiment orchestration 패턴을 참조하되, **routix package만** 사용하고 mbls/schore는 사용하지 않는 방식으로 재구성한다. 병렬 실행, 유연한 시나리오 구성, 전체 출력(CSV, JSON, solution 파일, Gantt, Excel)을 목표로 한다.

## Architecture Mapping

| hybridflowshop | ffc_ddw_sum_et (new) |
|---|---|
| `HybridFlowshopParameters` (schore) | `FFcDueDateWindowParameters` (existing) |
| `HybridFlowShopCpLnsController` (mbls) | `FAMSubroutineController` (new, extends routix) |
| `CpSubroutineController` (mbls) | `routix.SubroutineController` (use directly) |
| `CustomCpModel` (mbls) | 없음 — FAM은 greedy decoder |
| `SolutionManager` (custom) | `routix.SolutionManager` (use directly) |
| `HfsSingleInstanceRunner` | `FAMSingleInstanceRunner` (new) |
| `HfsMultiInstanceRunner` | `routix.MultiInstanceConcurrentRunner` (use directly) |
| `HfsMultiScenarioRunner` | `FAMMultiScenarioRunner` (new) |

## Files to CREATE

| # | Path | Purpose |
|---|---|---|
| 1 | `src/ffc_ddw_sum_et/orchestration/__init__.py` | Package init |
| 2 | `src/ffc_ddw_sum_et/orchestration/controller.py` | `FAMSubroutineController` — wraps `FAMDispatcher` as routix step methods |
| 3 | `src/ffc_ddw_sum_et/orchestration/fam_single_instance_runner.py` | `FAMSingleInstanceRunner` — runs one instance, saves results |
| 4 | `src/ffc_ddw_sum_et/orchestration/solution_manager.py` | `FAMSolutionManager` — tracks incumbent best |
| 5 | `src/ffc_ddw_sum_et/orchestration/benchmark_loader.py` | Loads PRA2017 `.txt` files into `FFcDueDateWindowParameters` |
| 6 | `src/ffc_ddw_sum_et/orchestration/reporting.py` | `FAMReporter` — aggregates results, writes CSV/JSON/YAML, generates Gantt |
| 7 | `metadata/fam_config.yaml` | 실험 메타데이터: scenarios, flow, timelimit, output config |

## Files to MODIFY

| Path | Change |
|---|---|
| `main.py` | Stub → full orchestration entry point |
| `pyproject.toml` | `matplotlib` 의존성 추가 (Gantt 차트용) |

## Key Class Designs

### 1. `FAMSubroutineController` (controller.py)

`routix.SubroutineController[StoppingCriteria, SubroutineReport]` 상속.

```python
class FAMSubroutineController(SubroutineController[StoppingCriteria, SubroutineReport]):
    def __init__(self, instance: FFcDueDateWindowParameters, subroutine_flow, stopping_criteria)
    def run_fam(self, job_sequence: str | None = None) -> SubroutineReport
        # FAMDispatcher().run(AlgSpec(instance, FAMOption(job_sequence=...)))
        # → SubroutineReport(elapsed_time, obj_value, obj_bound)
        # → solution_manager.register(report, FAMSolution(schedule, obj_value))
    def is_stopping_condition(self, **kwargs) -> bool
        # return self.timer.time_over(self.stopping_criteria.timelimit)
    def post_run_process(self)
        # nothing — instance runner handles file I/O
```

Flow YAML은 여러 permutation을 지원:
```yaml
subroutine_flow:
  - method: run_fam
    params: { job_sequence: "SPT" }
  - method: run_fam
    params: { job_sequence: "EDD" }
  - method: run_fam
```
`SolutionManager`가 best tracking을 담당.

### 2. `FAMSolution` + `FAMSolutionManager` (solution_manager.py)

`FFcSchedule`는 obj_value를 직접 저장하지 않으므로 wrapper 필요:

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class FAMSolution:
    schedule: FFcSchedule
    obj_value: float | None = None
    obj_bound: float | None = None

class FAMSolutionManager(SolutionManager[SubroutineReport, FAMSolution]):
    def _get_obj_value(self, solution) -> float
    def _a_is_better_obj_value(self, a, b) -> bool  # minimization: lower is better
    def _a_is_better_obj_bound(self, a, b) -> bool  # FAM은 bound 없음 → 항상 False
```

### 3. `FAMSingleInstanceRunner` (fam_single_instance_runner.py)

`routix.SingleInstanceRunner[FFcDueDateWindowParameters, FAMSubroutineController]` 상속.

```python
class FAMSingleInstanceRunner(SingleInstanceRunner[...]):
    def get_controller(self) -> FAMSubroutineController
    def post_run_process(self) -> InstanceResult
        # controller.solution_manager에서 best 추출
        # working_dir에 summary CSV row, solution JSON, obj_log YAML 저장
        return InstanceResult(instance_name, elapsed_time, obj_value, ...)
```

### 4. `FAMMultiScenarioRunner` (reporting.py)

`routix.MultiScenarioRunner[FFcDueDateWindowParameters, FAMSingleInstanceRunner, MultiInstanceConcurrentRunner]` 상속.
`post_run_process()`에서:
- 전역 summary CSV (모든 시나리오 + 모든 인스턴스)
- 시나리오별 statistics YAML/JSON
- Gantt 차트 PNG (best solutions에서)
- Excel 리포트 (dashboard + raw data + scenario info)

### 5. `BenchmarkLoader` (benchmark_loader.py)

```python
class BenchmarkLoader:
    def load_all(self, directory: Path, file_pattern: str | None = None) -> list[FFcDueDateWindowParameters]
    # 각 .txt 파일을 open → FFcDueDateWindowParameters.from_pra_2017_data(path, stream)
```

### 6. `main.py` (revised)

```python
def main():
    config = load_yaml("metadata/fam_config.yaml")
    mode = RunMode[config["run_mode"]]
    instances = BenchmarkLoader(Path(config["benchmark_dir"])).load_all()

    scenario_configs = [...]  # YAML에서 파싱

    runner = FAMMultiScenarioRunner(
        m_i_runner_class=MultiInstanceConcurrentRunner,
        s_i_runner_class=FAMSingleInstanceRunner,
        instances=instances,
        shared_param_dict={},
        scenario_configs=scenario_configs,
        output_dir=Path(config["output_dir"]),
        base_output_metadata={"start_dt": datetime.now()},
        mode=mode,
    )
    runner.run()
```

## Data Flow

```
main.py
  → load_yaml(metadata/fam_config.yaml)
  → BenchmarkLoader.load_all() → list[FFcDueDateWindowParameters]
  → FAMMultiScenarioRunner
       → for each scenario:
            MultiInstanceConcurrentRunner (ProcessPoolExecutor)
               → for each instance:
                    FAMSingleInstanceRunner.run()
                      → FAMSubroutineController.run()
                         → _run_flow over subroutine_flow
                            → for each step: is_stopping_condition() → run_fam()
                               → FAMDispatcher().run(AlgSpec(...))
                               → solution_manager.register(report, FAMSolution)
                      → post_run_process() → save instance results
               → post_run_process() → aggregate scenario results
       → post_run_process() → aggregate all, write Excel, Gantt
```

## Implementation Steps

1. **orchestration package skeleton** — `__init__.py`, `FAMSolution`, `FAMSolutionManager`
2. **FAMSubroutineController** — `run_fam()`, `is_stopping_condition()`, `post_run_process()`
3. **FAMSingleInstanceRunner** — `get_controller()`, `post_run_process()` (instance-level file I/O)
4. **BenchmarkLoader** — PRA2017 파일 파싱
5. **FAMMultiScenarioRunner + FAMReporter** — 시나리오 aggregation, CSV/JSON/YAML, Gantt, Excel
6. **metadata/fam_config.yaml** — 실험 설정
7. **main.py rewrite** — orchestration entry point
8. **pyproject.toml** — `matplotlib` 의존성 추가
9. **테스트** — small subset (5-10 instances)로 end-to-end 검증

## Verification

1. `uv run python -m ffc_ddw_sum_et` 또는 `uv run main.py` — 전체 파이프라인 실행
2. `output/` 디렉토리에 summary CSV, solution JSON, statistics YAML/JSON 생성 확인
3. Gantt PNG 및 Excel 리포트 생성 확인
4. `uv run ruff check` — lint 통과
5. `uv run pytest` — 기존 테스트 regressions 없음 확인
