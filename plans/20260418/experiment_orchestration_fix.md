# Fix: experiment_orchestration.md 구현 후속 수정

## Context

`plans/20260418/experiment_orchestration.md`의 초안 구현(commit `5411d8f` "draft work by Qwen3.6")에
대한 평가에서 6개 기능 버그와 1개 성능 버그를 발견하여 수정. 알고리즘 레이어(`src/ffc_ddw_sum_et/algorithm/`)는 건드리지 않음 —
`docs/algorithm-principles.md` 경계 유지.

계획 평가 당시 routix의 `MultiInstanceRunner`, `MultiScenarioRunner`가 abstract base class라는 점이
드러나, 초안이 이미 만든 얇은 구체 subclass(`FFcDDWMultiInstanceRunner`)는 유지한다.

## Scope

- 수정 대상: `src/ffc_ddw_sum_et/orchestration/`, `main.py`
- 비대상: `src/ffc_ddw_sum_et/algorithm/`, `src/ffc_ddw_sum_et/parameters/`, `src/ffc_ddw_sum_et/solution/`,
  `src/ffc_ddw_sum_et/io/`

## Problems Found And Fixes

### 1. `main.py` import path 오류

- **문제**: `from src.ffc_ddw_sum_et.orchestration.* import ...` — `uv_build`는
  `src/`를 package 루트로 두므로, 설치 후 `ffc_ddw_sum_et` 최상위로 import해야 함. 현재 경로는
  CWD에 `src/`가 보이는 개발 환경에서만 동작, `uv run python -m ffc_ddw_sum_et` 등에서는 실패.
- **수정**: `ffc_ddw_sum_et.orchestration`의 public surface를 통해 import.
  `FFcDDWMultiInstanceRunner`를 `orchestration/__init__.py` 공개 export에 추가.

### 2. `run_fam(job_sequence: str)` 타입 혼동 (중대)

- **문제**: `controller.py`의 `run_fam`이 `job_sequence: str | None`을 받아
  `FAMOption(job_sequence=(job_sequence,))`로 길이 1짜리 malformed tuple을 생성 →
  `FAMOption.resolve_initial_job_sequence`의 "모든 job 포함 + 중복 없음" 검증에서 항상 실패.
  계획 문서의 YAML 예시 `job_sequence: "SPT"` (규칙 문자열)가 실제로는 `tuple[str, ...]`
  (전체 permutation)을 요구하는 `FAMOption`과 맞지 않음.
- **수정**:
  - `job_sequence: Sequence[str] | None = None`로 받고 `tuple(job_sequence)`로 변환.
  - docstring에 "전체 permutation을 넘겨야 함, SPT/EDD 같은 규칙 문자열은 지원 안함"을 명시.
  - 규칙 기반 시퀀싱은 `docs/algorithm-principles.md` Rule 9에 따라 별도 feature로 분리해야 하므로
    이번 수정에서는 추가하지 않음.

### 3. `np.int64` obj가 YAML 직렬화 크래시 유발

- **문제**: `FAMDispatcher`가 numpy-계열 int (`np.int64`)로 obj_value를 반환. `SubroutineReport`에 그대로
  들어가서 `SubroutineReportStatistics.to_yaml()` 호출 시 pyyaml이
  `RepresenterError: cannot represent an object, np.int64(...)`로 크래시.
- **수정**: `controller.run_fam`에서 경계 변환 — `float(result.obj_value)` / `float(result.obj_bound)`.
  알고리즘 레이어를 건드리지 않고 orchestration 경계에서 해결 (algorithm-principles Rule 16 준수).

### 4. 실패 traceability 손실

- **문제**: `FFcDDWSingleInstanceRunner.run()`이 예외를 `except Exception: logger.exception(...)` + `finally: return post_run_process()` 로 silent swallow. post-process 단계에서 실패 사실을
  알 길이 없어 `InstanceResult`에 "정상적으로 None이 나온 실패"로 기록됨. `docs/algorithm-principles.md`
  Rule 15("errors belong in the record") 위반 여지.
- **수정**:
  - `InstanceResult.error: str | None` 필드 추가 (전체 traceback 문자열).
  - `run()`에서 `self._run_error = traceback.format_exc()`로 저장.
  - `_post_run_process_inner`가 `self._run_error`를 읽어서 `InstanceResult`에 주입.
  - `self.ctrlr`가 `get_controller()`에서 실패해 미할당인 경우도 `getattr(self, "ctrlr", None)`로 안전 처리.
  - 요약 CSV에 `error` 열 추가 (첫 줄만 노출 — `_first_line()` 헬퍼).

### 5. per-instance `first_obj_value` 부재

- **문제**: `InstanceResult`가 best obj만 담고 initial obj를 담지 않아, 개선율(improvement ratio)을
  instance 단위로 계산할 수 없음.
- **수정**: `InstanceResult`에 `first_obj_value`, `first_obj_bound` 필드 추가.
  `solution_manager.history[0].report`에서 채움.
- **이슈 6 / 7과 결합되어 의미가 생김** — 아래 참조.

### 6. scenario 레벨 `SubroutineReportStatistics` 오용

- **문제**: `reporting.py`의 `_write_statistics_json/_yaml`이 instance별 final report를 모아
  한 `SubroutineReportStatistics` 객체로 집계. 이 클래스는 **한 instance의 subroutine 흐름 trajectory**를
  다루도록 설계되었으므로, 여러 독립 instance의 final들을 모으면
  `improvementRatio = (first_instance.obj - best_instance.obj) / first_instance.obj`라는
  **의미가 전혀 없는** cross-instance 비교를 반환함.
- **수정**:
  - per-instance 통계는 runner가 담당 — `FFcDDWSingleInstanceRunner._save_statistics`에서
    각 instance의 실제 trajectory로 `SubroutineReportStatistics`를 만들어 `{ins_name}_statistics.{json,yaml}` 저장.
    `improvementRatio`가 instance 내 (first vs best) 정확한 의미를 가짐.
  - scenario 레벨에서는 `SubroutineReportStatistics` 대신 직접 `_aggregate_scenario()`로
    정직한 cross-instance 집계(`instanceCount`, `completedCount`, `erroredCount`, `meanObjValue`,
    `minObjValue`, `maxObjValue`, `meanImprovementRatio` = per-instance improvement의 평균)만 덤프.

### 7. Excel "Improvement %" 계산 버그

- **문제**: `_write_excel_report`가 각 instance의 Improvement %를
  `(first_objs[0] - ir.obj_value) / first_objs[0]`로 계산. `first_objs[0]`은
  **해당 scenario의 첫 번째 instance의 obj_value** — per-instance improvement라는 이름으로
  cross-instance 비교를 하고 있음. 또한 같은 list comprehension을 이름만 바꿔 두 번 만들어둠
  (`obj_values`, `first_objs` 동일).
- **수정**: `ir.first_obj_value`로 per-instance improvement 정확히 계산.
  `first_obj_bound`도 Excel "First Bound" 열에 노출.

### 8. Gantt 저장 극단적 성능 저하 (100배)

- **문제**: `savefig(bbox_inches='tight', dpi=150)`가 1 instance(100 jobs × 30 machine lane,
  ~1000 operation rectangle + text annotation)당 88초 소요 — `bbox_inches='tight'`가 모든 artist의
  bounding box를 재계산. 1 instance 전체 orchestration wall-clock이 97초.
- **수정**:
  - `plt.tight_layout()` 제거, `fig.subplots_adjust(left=0.1, right=0.98, top=0.92, bottom=0.1)` 사용.
  - `fig.savefig(path, dpi=120)` — `bbox_inches='tight'` 제거.
  - 결과: 97s → 0.97s (100배 개선).

## Verification

### End-to-end smoke

- `main.py` + 1-instance tiny config: 정상 완료, summary CSV/stats JSON+YAML/Gantt PNG/Excel 모두 생성, 전체 <1초.

### Error injection

- 잘못된 permutation(`job_sequence=["j00", "j01"]`, 100 jobs 중 2개만)을 `params`로 넘김 →
  `FAMOption.resolve_initial_job_sequence`가 `ValueError`, traceback이 `InstanceResult.error`에 캡처됨,
  scenario는 중단되지 않고 계속 진행.

### Regression

- `uv run pytest` → 53/53 PASS.
- `uv run ruff check src/ffc_ddw_sum_et/ main.py` → clean.
- `uv run ruff format --check ...` → clean.

## Files Changed

| Path | Change |
|---|---|
| `main.py` | import 경로 수정, public surface 사용 |
| `src/ffc_ddw_sum_et/orchestration/__init__.py` | `FFcDDWMultiInstanceRunner`를 공개 export에 추가 |
| `src/ffc_ddw_sum_et/orchestration/controller.py` | `run_fam` 시그니처 수정, `float()` 경계 변환 |
| `src/ffc_ddw_sum_et/orchestration/ffcddw_single_instance_runner.py` | `InstanceResult` 필드 추가 (`error`, `first_obj_value`, `first_obj_bound`), `run()` traceback 캡처, `_save_statistics` 신설 |
| `src/ffc_ddw_sum_et/orchestration/reporting.py` | summary CSV 확장, scenario stats 재설계, Excel improvement 수정, Gantt 저장 최적화 |

## Rationale / Principles Applied

- **`docs/algorithm-principles.md` Rule 9**: sequence 규칙은 option의 암묵적 behavior가 아닌 별도 feature.
  계획의 `job_sequence: "SPT"` 예시를 구현하지 않고, `run_fam`은 permutation만 받도록 좁힘.
- **Rule 15**: 실패한 run은 record에 명시적으로 나타나야 함. `InstanceResult.error`가 이 역할.
- **Rule 16**: 알고리즘 → orchestration의 단방향 의존성 유지. `np.int64` 문제는 orchestration 경계에서 흡수.
- **Rule 18**: reporting 목적이 algorithm 내부를 오염시키지 않도록, obj 타입 정규화를 controller 쪽에만 둠.
- **SSOT**: per-instance 통계의 원본(obj history)은 `FFcDDWSolutionManager`가 단일 출처. reporter는
  요약본만 소비하고 원본을 재조합하지 않음.

## Out Of Scope (Future Work)

- 규칙 기반 초기 순열 생성(SPT, EDD, LSL 등) — 별도 step method나 sequence-builder로 분리 권장.
- `FAMDispatcher`가 오류 시 `AlgRecord(error=...)`를 반환하도록 하는 것 —
  `algorithm-principles.md` Rule 15의 완전 실현. 현재는 orchestration 경계에서 traceback을 보존하는 방식으로 우회.
- Gantt 렌더링 시 1000 텍스트 라벨의 가독성 개선 (현재는 밀도가 높아 겹침).
- `SingleInstanceRunner`가 RESUME 모드에서 기대하는 `{ins_name}/results/{ins_name}_summary.csv`
  구조와 현 writer의 출력 경로 불일치 — RESUME 사용 시 재정합 필요.
