# Plan: drop `TimingInfo`/`AlgRecord.timing`; rename `elapsed_ms` → `elapsed_sec`

## Context

`AlgRecord`의 알고리즘 경계 timing 필드는 milliseconds로 정의돼 있었다 (`TimingInfo.wall_ms`, `TimingInfo.cpu_ms`, `ProgressLogEntry.elapsed_ms`). 그런데:

- **`TimingInfo` / `AlgRecord.timing`을 읽는 곳이 단 하나도 없다** (직접 attribute 접근, 직렬화, 로깅 모두 0건). `tests/algorithm/test_algorithm_contracts.py`에서 `record.timing is None`만 확인할 뿐. 매 writer는 sec → ms 변환만 해서 채워 넣고, 그 값은 어디서도 소비되지 않았다.
- **`ProgressLogEntry.elapsed_sec`은 향후 작업에서 활용 예정**이라 필드 자체는 유지하고 ms → sec rename만 했다.
- 프로젝트 전반(`controller.timer.elapsed_sec`, `SubroutineReport.elapsed_time`, `ObjectiveBoundRecorder`, ortools `solver.wall_time`)이 sec 단위로 통일돼 있어 ms 표기는 외부 컨벤션과도 어긋났다.

따라서:
1. `TimingInfo` 클래스와 `AlgRecord.timing` 필드를 **삭제** (사용처 없음, YAGNI)
2. `ProgressLogEntry.elapsed_ms` → `elapsed_sec`으로 **rename + 단위 변환 제거**

## Files Modified

### 1. `src/ffc_ddw_sum_et/algorithm/base/alg_record.py`

- `__all__`에서 `"TimingInfo"` 제거
- `TimingInfo` dataclass 전체 삭제
- `ProgressLogEntry.elapsed_ms: float` → `elapsed_sec: float`
- `AlgRecord.timing: TimingInfo | None = None` 필드 삭제

### 2. `src/ffc_ddw_sum_et/algorithm/cpsat_adapter.py`

- `TimingInfo` 임포트 제거
- `elapsed_ms = (time.monotonic() - start) * 1000.0` 라인 삭제 (다른 사용처 없음)
- 두 `AlgRecord(...)`에서 `timing=TimingInfo(wall_ms=elapsed_ms),` kwarg 삭제

### 3. `src/ffc_ddw_sum_et/algorithm/neh_cp/dispatcher.py`

- `TimingInfo` 임포트 제거
- `ProgressLogEntry(elapsed_ms=step_elapsed_seconds * 1000.0, ...)` → `ProgressLogEntry(elapsed_sec=step_elapsed_seconds, ...)`
- `elapsed_seconds = ...` 와 `timing = TimingInfo(...)` 두 줄 모두 삭제
- 두 `AlgRecord(...)`에서 `timing=timing,` kwarg 삭제

### 4. `tests/algorithm/test_algorithm_contracts.py`

- `assert record.timing is None` 라인 삭제

### 5. Plan docs 갱신

- `plans/experiment/20260507/cpsat_adapter.md` — `TimingInfo(wall_ms=...)` 표기를 제거하고 후속 정리에 대한 노트 추가
- `plans/experiment/20260507/cpsat_obj_log_callbacks.md` — `elapsed_ms`, `* 1000.0`, `/ 1000.0` 표기를 `elapsed_sec` 기반으로 일괄 갱신
- `plans/experiment/20260427/neh-cp-lift-to-algorithm.md` — `ProgressLogEntry.elapsed_ms (× 1000)` 및 `record.timing.wall_ms / 1000` 표기를 `elapsed_sec` + 컨트롤러 측정 기반으로 정정

### Reader 변경 없음 (검증됨)

`grep -rn "\.wall_ms\|\.cpu_ms\|\.elapsed_ms\|TimingInfo\|\.timing"` 결과: 위 변경 외 직접 reader 없음. 오케스트레이션은 `controller.total_elapsed_time`(sec)을 자체 측정해 사용 중.

## Non-goals

- 오케스트레이션 레이어(`SubroutineReport.elapsed_time`, `controller.total_elapsed_time`) 유지 — 이미 sec
- `ObjectiveBoundRecorder.elapsed_time_and_bound` 유지 — 이미 sec
- 외부 라이브러리(`routix.ElapsedTimer`, ortools) API 미변경
- `cpu_ms`도 `TimingInfo`와 함께 사라진다. 향후 cpu time이 정말로 필요해지면 그때 `AlgRecord`에 직접 추가하는 식으로 다시 도입(YAGNI).

## Verification (실행 결과)

- `uv run ruff check src/ tests/` → All checks passed
- `uv run ruff format src/ tests/` → 3 files reformatted
- `uv run pytest tests/` → 225 passed
- `grep -rn "TimingInfo\|wall_ms\|cpu_ms\|elapsed_ms" src/ tests/` → 빈 결과
- `grep -rn "\* 1000\.0\|/ 1000\.0" src/ffc_ddw_sum_et/algorithm/` → ms 변환 잔존 없음
