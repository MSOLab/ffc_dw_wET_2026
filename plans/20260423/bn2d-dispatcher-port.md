# BN2D Dispatcher Port (from hybridflowshop)

## Context

소스 프로젝트 `/home/hjt/code/hybridflowshop/hybridflowshop/dispatcher/bn2d.py` 의
**BN2D (Bottleneck-based Two-Way Dispatching)** 휴리스틱을 이 프로젝트의
`src/ffc_ddw_sum_et/algorithm/dispatcher/` 아래로 포팅한다.

두 프로젝트의 목적식이 다르다는 점이 핵심 제약이다:

- 소스(hybridflowshop): **minimize makespan**.
- 본 프로젝트(ffc_ddw_sum_et): **minimize weighted earliness + tardiness** (DDW).

사용자 지시: **원본의 목적(minimize makespan)을 그대로 가져온다.** 즉 BN2D 내부의
bottleneck-기반 후보 스코어링은 makespan 기준으로 유지하며, 이 프로젝트의 외곽
목적식(가중 ET)은 건드리지 않는다. 보고 시점의 `obj_value`는 BN2D의 1차 목적인
makespan이고, 가중 earliness/tardiness는 `AlgResult.metrics`에 보조 지표로 동봉한다
(`docs/algorithm-principles.md` Rule 12).

현재 `src/ffc_ddw_sum_et/algorithm/dispatcher/bn2d.py` 는 `BN2DOption` 데이터클래스와
소스로부터 복사된 import 문만 들어 있는 **미완성 스텁** 상태다. 이 계획은 그 스텁을
채우면서 필요한 내부 의존성(유틸 3개, tie-break 필드, selection 해결기)을 함께
가져오고, 컨트롤러/실험 설정까지 풀스택으로 연결한다.

사용자가 명시적으로 결정한 범위 제약:

- `solve_selection_problem`(CP-SAT 기반 L/R cap 분할)은 **함께 포팅**.
- 소스의 anchor-band 경로(`get_schedule_by_two_way_stage_band`,
  `_dispatch_anchor_stage`)는 **이번 포팅에서 제외** — 해당 경로는 본 프로젝트
  `FFcSchedule`에 없는 strict-order dispatch API를 요구하므로 별도 작업으로 분리.
- 풀스택 연결: dispatcher → `run_bn2d` 컨트롤러 스텝 → 실험 YAML → `main.py`.

## Design Summary

### 목적 함수 이중성 (핵심)

- **BN2D 내부 스코어링**: `FFcSchedule.makespan` (원본 기준).
  - `MixedDispatcher.get_best_mixed_schedule_by_sequence(...)` 는 이미
    `criteria: Literal["weighted_et", "makespan"] = "weighted_et"` 파라미터를
    갖는다 (`mixed.py:43`). BN2D 내부 호출은 모두 `criteria="makespan"` 로 전달.
  - 모든 후보 스케줄의 best 선택은 `makespan` 비교로 수행.
- **AlgRecord 반환**: 외부 보고 지표는 이 프로젝트의 기본 목적식(가중 ET)에 맞춘다.
  - `obj_value = sum_earliness + sum_tardiness` (primary, 가중 ET — FAM 과 동일 규약).
  - `obj_bound = None` (heuristic, 경계 없음).
  - `metrics = {"sum_earliness": <int>, "sum_tardiness": <int>, "makespan": <int>}`
    — BN2D 내부 1차 목적인 makespan 은 보조 지표로만 기록 (Rule 12: 보조 지표는
    `metrics` 에, primary 는 `obj_value`).

정리: BN2D 는 **makespan 을 minimize 하는 알고리즘으로 가져오지만**, `AlgRecord`
는 외부(리포팅/incumbent 비교)와의 일관성을 위해 `obj_value` 필드에 **가중 ET** 를
기록한다. 이 이중성 덕분에 `solution_manager.register(...)` 에 넘기는 값과
`record.result.obj_value` 가 동일 단위가 되어 FAM 경로와 교차 비교가 가능하다.

### 역방향 former-stages 디스패치

소스는 `create_instance_of_stage_subset(instance, stage_list)` 로 서브셋+역방향
인스턴스를 만든다 (schore `hybrid_flowshop.py:284-323`). 본 프로젝트에는 해당
헬퍼가 **없다** — `FFcDDWParameters.reverse_stages` classmethod 만 있음
(`parameters/ffc_ddw_params.py:81-112`).

이를 본 프로젝트에도 **`FFcDDWParameters.create_instance_of_stage_subset`
classmethod** 로 추가해 BN2D 에서 사용한다. `reverse_stages` 와 동일한 패턴:

```python
@classmethod
def create_instance_of_stage_subset(
    cls,
    instance: FFcParameters,
    stage_id_subset: set[str],
    reverse_stage_seq: bool = False,
) -> Self:
    # Arbitrary stage permutations are not allowed in a flow shop: only the
    # original forward order of the selected stages, or its reverse, is
    # semantically valid. stage_id_subset is therefore typed as set[str];
    # the final ordering is derived from instance.stage_id_list.
    if not isinstance(instance, FFcDDWParameters):
        raise TypeError(...)
    if not stage_id_subset.issubset(instance.stage_id_list):
        raise ValueError("Stage subset contains invalid stage IDs.")
    if not stage_id_subset:
        raise ValueError("Stage subset must be non-empty.")
    ordered = [s for s in instance.stage_id_list if s in stage_id_subset]
    if reverse_stage_seq:
        ordered.reverse()
    stage_id_2_index = {s: i for i, s in enumerate(instance.stage_id_list)}
    new_p_manager = instance.p_manager.filter_by_stage_indices(
        [stage_id_2_index[s] for s in ordered]
    )
    new_stage_2_machines_map = {
        s: list(instance.stage_2_machines_map[s]) for s in ordered
    }
    return cls(
        instance.name,
        instance.job_id_list,
        ordered,
        new_stage_2_machines_map,
        new_p_manager,
        instance.job_2_due_window_map,
        instance.job_2_ewt_map,
        instance.job_2_twt_map,
        instance.generation_params,
    )
```

DDW-전용 필드(due window, ewt/twt, generation_params)는 per-job 이므로 stage
서브셋에 영향받지 않고 그대로 복사. `JobStageProcessingTimeManager.filter_by_stage_indices`
는 `parameters/base/job_stage_p.py:168` 에 이미 존재하므로 추가 구현 불필요.

BN2D 에서의 사용:

```python
reversed_sub_instance = FFcDDWParameters.create_instance_of_stage_subset(
    self.instance,
    set(before_stage_list),
    reverse_stage_seq=True,
)
```

그리고 `BaseDispatcher._create_empty_schedule(instance=...)` 에 optional instance
오버라이드를 추가해 (소스 `hybridflowshop/dispatcher/base.py:84-96` 패턴) 서브셋
인스턴스에서 빈 스케줄을 만들 수 있게 한다. 기존 `MixedDispatcher` 는 인자 없이
호출하므로 하위 호환 유지.

`FFcSchedule` 은 이미 `as_reversed()` (`solution/ffc_schedule.py:174`),
`right_shift()` (`:1412`), `add_ops_times_2_mc()` (`:395`),
`get_jik_2_{start,end}_time_map()` (`:338, :344`) 를 제공하므로 이후 반전 타임
복원 로직은 그대로 옮겨온다.

### tie-break 키

소스 `BaseDispatcher` 는 `job_tiebreak_rank` / `job_id_2_original_index` /
`_get_rank_tiebreak_key(...)` 를 제공한다. 본 프로젝트의 trim 버전에는 없다.
`BaseDispatcher` 를 **가산 확장** (optional 파라미터 + 헬퍼 추가)해 기존
`MixedDispatcher` 호환을 유지하면서 BN2D가 의존하는 안정 정렬을 지원한다.

### Algorithm contract 준수

- `BN2DOption` 은 `@dataclass(frozen=True, slots=True, kw_only=True)` 로 `AlgOption`
  상속 (`fam.py:19-24` 패턴).
- `random_seed: int | None` 필드 추가 — `randomize_mid_all=True` 경로의 랜덤성을
  재현 가능하게 만들기 위함 (Rule 8).
- `BN2DDispatcher` 는 `algorithm_id = "bn2d"` 를 가지며 `run(spec: AlgSpec) -> AlgRecord`
  를 구현 (Rule 1). 타입 엄격 검사(`_validate_instance`, `_resolve_option`)는
  `fam.py:162-172` 패턴을 그대로 따른다.

## Files to Modify / Create

### 1. `src/ffc_ddw_sum_et/parameters/ffc_ddw_params.py` (수정)

`FFcDDWParameters` 에 `create_instance_of_stage_subset` classmethod 추가
(상세는 위 "역방향 former-stages 디스패치" 절의 코드 블록 참조). `reverse_stages`
와 동일한 위치 (`:81` 뒤) / 동일한 데코레이션 스타일.

### 2. `src/ffc_ddw_sum_et/algorithm/dispatcher/base.py` (수정)

두 가지 확장:

1. `BaseDispatcher.__init__` 에 optional `job_tiebreak_rank: Mapping[str, int] | None`
   추가. 아래 속성/메서드를 추가:
   - `self.job_id_2_original_index: dict[str, int]` (생성자에서 계산).
   - `self.job_tiebreak_rank: dict[str, int]` (dict 복사).
   - `_get_rank_tiebreak_key(self, job_id: str) -> int` — 소스
     `hybridflowshop/dispatcher/base.py:73-82` 로직 그대로 이식.
2. `_create_empty_schedule(self, instance: FFcDDWParameters | None = None) -> FFcSchedule`
   — optional instance 오버라이드를 수용해 서브셋 인스턴스로 빈 스케줄을 만들 수
   있도록 확장 (소스 `hybridflowshop/dispatcher/base.py:84-96` 패턴).

기존 `MixedDispatcher` / `from_job_sequence_get_schedule_mixed` 는 영향 없음 —
추가 파라미터는 모두 optional, 기본 동작 불변.

### 3. `src/ffc_ddw_sum_et/algorithm/dispatcher/utils.py` (추가)

소스에서 3개 함수를 이식:

- `dispatch_job_sequence_by_stages` (소스 `utils.py:796-821`) —
  `schedule.dispatch_job_by_stages` 루프.
- `dispatch_stages_by_job_sequence` (소스 `utils.py:824-883`) —
  `machine_then_job` 분기 포함한 스테이지 단위 디스패치 루프.
- `reverse_even_positions` (소스 `utils.py:1127-1147`) — 짝수 위치 역순 변환.

소스의 타입은 `HybridFlowshopLiteSchedule` → 본 프로젝트의 `FFcSchedule` 로 치환.
API 호출부(`dispatch_job_by_stages`, `dispatch_stage_by_jobs`, `machine_centric_dispatch_4`)는
`FFcSchedule` 에 동명 메서드가 이미 존재하므로 수정 불필요.

### 4. `src/ffc_ddw_sum_et/algorithm/dispatcher/select_and_assign.py` (신규)

소스 `/home/hjt/code/hybridflowshop/hybridflowshop/select_and_assign.py` 파일을
새 모듈로 가져옴. 위치는 `algorithm/dispatcher/` 아래로 배치 — 현재 유일한
소비자가 `BN2DDispatcher` 라 dispatcher-로컬 헬퍼로 두는 것이 소유권과 의존 방향
을 명확히 한다. 본체는 그대로 유지 (CP-SAT 기반 L_set / R_set 이분할). 변경점:

- `if __name__ == "__main__":` 예제 블록은 제거 (프로젝트 스타일).
- 타입 힌트 `Hashable` → `str` 로 좁힘 (본 프로젝트는 `JobIdType = str`).

`algorithm/__init__.py` / `algorithm/dispatcher/__init__.py` 에는 **재수출하지
않는다** — BN2D 내부 헬퍼일 뿐이므로 외부에서 접근할 필요가 없다.

### 5. `src/ffc_ddw_sum_et/algorithm/dispatcher/bn2d.py` (완성)

현재 스텁을 대체하고 소스 `bn2d.py` 의 다음 요소를 포팅:

**`BN2DOption`** (dataclass, `AlgOption` 상속):

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class BN2DOption(AlgOption):
    left_cap_multiplier: int | None = None
    right_cap_multiplier: int | None = None
    left_cap_portion: float | None = None
    right_cap_portion: float | None = None
    normalize_by_stage_cnt: bool = False
    randomize_mid_all: bool = False
    reverse_mid_even: bool = False
    reverse_mid_all: bool = False
    mixed_schedule_for_former_stages: bool = False
    mixed_schedule_for_later_stages: bool = False
    machine_then_job: bool = False
    all_stages_as_bottleneck: bool = False
    random_seed: int | None = None
```

- `all_stages_as_bottleneck` 는 소스의 두 public 메서드
  (`get_schedule_by_bn2d_all_stages` vs `get_schedule_by_bn2d_single_stage`) 를
  옵션 플래그로 통합하기 위한 것. FAM 패턴과 동일하게 option 에서 분기.
- `random_seed` 는 `random.shuffle` 재현성 확보용 (Rule 8).

**`BN2DDispatcher`** (`BaseDispatcher` 상속):

- `algorithm_id = "bn2d"`.
- 생성자는 `BaseDispatcher.__init__` 호출 + 내부 `MixedDispatcher` 보조 인스턴스 보관.
- `run(self, spec: AlgSpec) -> AlgRecord`:
  1. `_validate_instance(spec)` / `_resolve_option(spec)` (FAM 패턴).
  2. 옵션이 `random_seed` 를 가지면 `random.Random(seed)` 로 local RNG 생성 —
     모듈 전역 `random` 대신 local RNG 를 사용해 사이드 이펙트 차단.
  3. `all_stages_as_bottleneck` 에 따라 `_get_schedule_from_bottleneck_stage` 를
     단일 bottleneck 또는 모든 스테이지에 대해 반복 실행하고 best-makespan 을 선택.
  4. 최종 schedule 에서 `compute_weighted_earliness_tardiness(schedule, instance)`
     로 보조 지표 계산.
  5. `AlgRecord` 반환 (위 "AlgRecord 반환" 절 참조).
- Private 메서드 (소스와 거의 1:1):
  - `_get_bottleneck_stage() -> str` (`bn2d.py:305-323` 그대로).
  - `_get_bottleneck_stage_schedule_heuristic(...)` — L/R cap 선택 단계에서
    `from ..select_and_assign import solve_selection_problem` 을 **모듈 탑레벨**
    import 로 변경 (이 프로젝트에는 항상 존재하므로 lazy import 불요).
  - `_create_reversed_instance_for_former_stages(...)` — 소스 `bn2d.py:213-243`
    을 본 프로젝트 API 로 번역:
    `reversed_before = list(reversed(before_stage_list))` →
    `reversed_instance = FFcDDWParameters.create_instance_of_stage_subset(self.instance, reversed_before)`
    를 계산해 `(reversed_instance, job_2_release_t)` 를 반환.
  - `_dispatch_former_stages(instance_for_former_stages, job_2_release_t, ...)` —
    소스 `bn2d.py:245-303` 그대로 옮기되 `_create_empty_schedule(instance=...)` 로
    서브셋 인스턴스 기반 빈 스케줄을 생성하고, `dispatch_stages_by_job_sequence`
    / `dispatch_job_sequence_by_stages` 에 `reversed_instance` 의
    `stage_2_job_2_p_map` / `job_2_stage_2_p_map` 서브셋 딕셔너리를 그대로 전달.
  - `_get_schedule_from_bottleneck_stage(...)` — 소스 `bn2d.py:325-445` 그대로
    옮기되 `gantt_draw_func` 인자 제거 (프로젝트는 post-run Gantt 방식).
  - `_get_job_2_start_time_map` / `_get_job_2_end_time_map` — 소스 `bn2d.py:693-711`
    그대로.
- `logger` 는 `spec.logger` 우선, fallback 은 `logging.debug` (Rule 4). 소스의
  `self.logger.debug(...)` 호출 지점들은 `BaseDispatcher` 가 제공하는 `self.logger`
  를 그대로 사용하되, 생성자에서 `spec.logger` 를 전달받도록 조정.

### 6. `src/ffc_ddw_sum_et/algorithm/dispatcher/__init__.py` (수정)

`BN2DDispatcher`, `BN2DOption` 을 `__all__` 에 추가.

### 7. `src/ffc_ddw_sum_et/algorithm/__init__.py` (수정)

`BN2DDispatcher`, `BN2DOption` 을 import + `__all__` 에 추가
(FAM/MCFLB 와 동일 패턴, `algorithm/__init__.py:12`).

### 8. `src/ffc_ddw_sum_et/orchestration/controller.py` (수정)

`run_fam` (`:74-120`) 을 템플릿으로 `run_bn2d` 스텝 메서드를 추가. 시그니처는
`BN2DOption` 의 필드를 flat keyword arg 로 노출 (YAML 에서 바로 매핑되도록):

```python
def run_bn2d(
    self,
    left_cap_multiplier: int | None = None,
    right_cap_multiplier: int | None = None,
    left_cap_portion: float | None = None,
    right_cap_portion: float | None = None,
    normalize_by_stage_cnt: bool = False,
    randomize_mid_all: bool = False,
    reverse_mid_even: bool = False,
    reverse_mid_all: bool = False,
    mixed_schedule_for_former_stages: bool = False,
    mixed_schedule_for_later_stages: bool = False,
    machine_then_job: bool = False,
    all_stages_as_bottleneck: bool = False,
    random_seed: int | None = None,
) -> SubroutineReport: ...
```

본문은 FAM 패턴:

1. `start_elapsed = time.monotonic()`.
2. `option = BN2DOption(...)` 생성 (kw_only).
3. `spec = AlgSpec(instance=self.instance, option=option, logger=self.logger)`.
4. `record = BN2DDispatcher().run(spec)`.
5. `elapsed = time.monotonic() - start_elapsed`.
6. `report = SubroutineReport(elapsed_time=elapsed, obj_value=..., obj_bound=None)`.
7. `result.schedule` 이 있으면 `FFcDDWSolution` 으로 래핑해
   `self.solution_manager.register(report, solution)` 로 incumbent 등록.

`record.result.obj_value` 가 이미 가중 ET 이므로 FAM 과 동일하게 그대로
`SubroutineReport.obj_value` 및 `FFcDDWSolution.obj_value` 로 전달한다 — 별도의
단위 환산 로직이 필요 없다.

### 9. `metadata/20260423/bn2d_init_config.yaml` (신규)

`metadata/20260423/1_mcf_lb_init_13_config.yaml` 을 템플릿으로 새 실험 설정 생성:

```yaml
# BN2D dispatcher init experiment (makespan-driven bottleneck two-way dispatch).
# Ref: plans/20260423/bn2d-dispatcher-port.md

run_mode: FULL_RUN

benchmark_dir: benchmarks/PRA2017/large
ins_index_source: benchmarks/PRA2017/pra2017_hybrid_match.csv
# ins_index: [0, 1439]

output_dir: output
instance_worker_cnt: 48   # 메모리 규칙: 실험 기본값
draw_gantt: false
painter_thread_cnt: 48

scenarios:
  - name: bn2d_single_stage
    timelimit: 60.0
    output_subdir: bn2d_single_stage
    subroutine_flow:
      - method: run_bn2d
        left_cap_portion: 0.25
        right_cap_portion: 0.25
        normalize_by_stage_cnt: false
        mixed_schedule_for_former_stages: true
        mixed_schedule_for_later_stages: true
        machine_then_job: true
        all_stages_as_bottleneck: false

  - name: bn2d_all_stages
    timelimit: 120.0
    output_subdir: bn2d_all_stages
    subroutine_flow:
      - method: run_bn2d
        left_cap_portion: 0.25
        right_cap_portion: 0.25
        normalize_by_stage_cnt: false
        mixed_schedule_for_former_stages: true
        mixed_schedule_for_later_stages: true
        machine_then_job: true
        all_stages_as_bottleneck: true
```

(포팅 시 기존 `metadata/20260423/cmax_init_config.yaml` 은 손대지 않는다 —
별도 스타일의 drafts이며 이번 풀스택과 무관.)

### 10. `main.py` (수정)

`CONFIG_PATH = Path("metadata/20260423/bn2d_init_config.yaml")` 로 전환.
이전 `1_mcf_lb_init_13_config.yaml` 경로는 주석으로 남겨 비교 편의를 확보.

## Out of Scope (이번 작업에서 제외)

- 소스의 `get_schedule_by_two_way_stage_band`, `_dispatch_anchor_stage`.
  필요한 `FFcSchedule.dispatch_stage_by_jobs_strict_start_order` /
  `_strict_sequence` 가 아직 없음. 별도 plan 으로 분리.
- `docs/TODO.md` 의 `solution_manager.register` 데코레이터화 — 이 포팅으로 스텝
  메서드가 3개가 되지만, TODO 의 "When to act" 조건(반복되는 boilerplate) 에는
  아직 미치지 않음.

## Verification

1. **정적 검증**:
   - `uv run ruff check`
   - `uv run ruff format`
2. **단위 검증** (기존 테스트 스위트가 없는 영역이므로 수동):
   - `uv run python -c "from ffc_ddw_sum_et.algorithm import BN2DDispatcher, BN2DOption; print('ok')"`
   - 작은 벤치마크 인스턴스(예: `benchmarks/PRA2017/large` 의 `ins_index: [0, 2]`)로
     `ins_index_filter` 를 좁혀 `uv run python main.py` 실행. 출력 디렉터리의
     `*_subroutine_report.yaml` 에서 `obj_value`(가중 ET), Gantt 후처리 결과의
     `*_schedule.yaml` 에서 makespan/ET 정합성을 확인.
3. **대조 검증**:
   - 같은 인스턴스를 `run_fam` 시나리오로도 실행해 `run_bn2d` 의 가중 ET 가
     FAM 결과와 어느 정도 경쟁력 있는 범위인지 확인 (결정적 비교가 아닌 sanity check).
4. **옵션 분기 검증**:
   - `all_stages_as_bottleneck: true` / `false` 각각에 대해 최소 1 인스턴스 실행
     후 `*_subroutine_report.yaml` 의 `elapsed_time` / `obj_value` 가 모두 유효한지 확인.
   - `randomize_mid_all: true, random_seed: 42` 로 두 번 실행해 결과가 재현되는지 확인.

## Critical Files (modified/created)

| 경로 | 상태 |
|---|---|
| `src/ffc_ddw_sum_et/parameters/ffc_ddw_params.py` | 수정 (`create_instance_of_stage_subset` classmethod) |
| `src/ffc_ddw_sum_et/algorithm/dispatcher/base.py` | 수정 (tiebreak 확장 + `_create_empty_schedule(instance=...)`) |
| `src/ffc_ddw_sum_et/algorithm/dispatcher/utils.py` | 수정 (3 helpers 추가) |
| `src/ffc_ddw_sum_et/algorithm/dispatcher/bn2d.py` | 재작성 (스텁 → 완성) |
| `src/ffc_ddw_sum_et/algorithm/dispatcher/__init__.py` | 수정 (export) |
| `src/ffc_ddw_sum_et/algorithm/__init__.py` | 수정 (export) |
| `src/ffc_ddw_sum_et/algorithm/dispatcher/select_and_assign.py` | 신규 |
| `src/ffc_ddw_sum_et/orchestration/controller.py` | 수정 (`run_bn2d`) |
| `metadata/20260423/bn2d_init_config.yaml` | 신규 |
| `main.py` | 수정 (`CONFIG_PATH`) |

## References Reused

- `FAMDispatcher` / `FAMOption` pattern: `src/ffc_ddw_sum_et/algorithm/fam.py:19-212`.
- `run_fam` step method pattern: `src/ffc_ddw_sum_et/orchestration/controller.py:74-120`.
- `MixedDispatcher.get_best_mixed_schedule_by_sequence(..., criteria="makespan")`:
  `src/ffc_ddw_sum_et/algorithm/dispatcher/mixed.py:34-94`.
- `compute_weighted_earliness_tardiness`: `src/ffc_ddw_sum_et/solution/objectives.py`.
- `FFcSchedule` 메서드 (`as_reversed`, `right_shift`, `add_ops_times_2_mc`,
  `get_jik_2_*_time_map`, `deepcopy`, `dispatch_stage_by_jobs`,
  `dispatch_job_by_stages`, `machine_centric_dispatch_4`):
  `src/ffc_ddw_sum_et/solution/ffc_schedule.py`.
- `AlgSpec` / `AlgRecord` / `AlgResult` / `AlgOption` / `WorkStatus` /
  `TerminationReason`: `src/ffc_ddw_sum_et/algorithm/base/`.
- `FFcDDWParameters.reverse_stages` classmethod 패턴 (새 classmethod 의 템플릿):
  `src/ffc_ddw_sum_et/parameters/ffc_ddw_params.py:81-112`.
- `JobStageProcessingTimeManager.filter_by_stage_indices`:
  `src/ffc_ddw_sum_et/parameters/base/job_stage_p.py:168`.
- Source references:
  - `/home/hjt/code/hybridflowshop/hybridflowshop/dispatcher/bn2d.py` (712 lines).
  - `/home/hjt/code/hybridflowshop/hybridflowshop/dispatcher/base.py` (tiebreak,
    `_create_empty_schedule(instance=...)`).
  - `/home/hjt/code/hybridflowshop/hybridflowshop/dispatcher/utils.py:796, :824, :1127`.
  - `/home/hjt/code/hybridflowshop/hybridflowshop/select_and_assign.py`.
  - `/home/hjt/code/schore/src/schore/parameters_examples/parallel_shop/identical_flow/hybrid_flowshop.py:284-323`
    (`create_instance_of_stage_subset` 참조 구현).
