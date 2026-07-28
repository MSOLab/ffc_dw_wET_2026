# `job_contrib_cp` solver status logging + search log file fix

**작성일**: 2026-07-28 · **종류**: 버그 수정 계획(사전 작성) · **상태**: **완료(2026-07-28)**

**대상 파일**:
- `src/ffc_ddw_sum_et/algorithm/job_contrib_cp/option.py`
- `src/ffc_ddw_sum_et/algorithm/job_contrib_cp/dispatcher.py`
- `src/ffc_ddw_sum_et/orchestration/controller.py`
- `src/ffc_ddw_sum_et/algorithm/cpsat_search_log.py` (신규, 공용 writer)
- `src/ffc_ddw_sum_et/algorithm/flip_makespan_cp/dispatcher.py` (공용 writer로 교체)
- `src/ffc_ddw_sum_et/algorithm/cumulative_routine.py` (공용 writer로 교체)

**참고 패턴**: `FlipMakespanCpOption` / `FlipMakespanCpDispatcher` 가 동일한
`solver_log_path_getter` + `response_proto.solve_log` 패턴을 이미 확립.

---

## 1. 문제 진단

### 1-1. solver status 미출력

`controller.job_contrib_cp` 는 시작 시 `jd_target` 해석 결과만 `logger.info`로
남기고, CP-SAT solve 후 `work_status` (OPTIMAL / FEASIBLE) 와 `cpsat_status`
(CP-SAT 원시 상태 문자열)을 **로그에 전혀 출력하지 않는다.** metrics YAML에
`solver_status`는 기록되지만 콘솔/로그에서 바로 확인할 수 없다.

### 1-2. `log_search_progress=true` 결과 파일 누락

`dispatcher.py:195-198` 에서 `log_to_response=True`를 설정하고,
`dispatcher.py:218-219` 에서 `solver.log_callback`으로 hint-completeness
warning 체크만 한다. **`solver.response_proto.solve_log`를 파일로 쓰는 코드는
존재하지 않는다.**

`flip_makespan_cp` dispatcher (line 225-237) 는 동일한 기능을
`option.solver_log_path_getter` → `solver.response_proto.solve_log` 패턴으로
이미 구현하고 있다. 이 패턴을 그대로 가져와야 한다.

---

## 2. 수정 계획

### 2-1. `JobContribCpOption` 에 `solver_log_path_getter` 추가

`flip_makespan_cp/option.py` 의 `FlipMakespanCpOption` 필드를 미러링:

```python
from os import PathLike
from typing import Callable

solver_log_path_getter: Callable[[str], PathLike[str] | str] | None = None
```

### 2-2. dispatcher 에 search log 파일 쓰기 추가

같은 blob 을 dispatcher 마다 복사하는 대신, 공용 writer
`algorithm/cpsat_search_log.py` 를 두고 `solver.solve(mdl)` 직후 호출한다.

```python
def write_cpsat_search_log(
    solve_log: str,
    path_getter: Callable[[str], PathLike[str] | str] | None,
    filename_suffix: str,
    *,
    logger: logging.Logger,
) -> None:
    """log 가 비었거나 getter 가 None 이면 no-op; IO 실패는 삼키고 기록만."""
```

호출부는 세 곳 모두 동일한 형태:

```python
if option.log_search_progress:
    write_cpsat_search_log(
        solver.response_proto.solve_log,
        option.solver_log_path_getter,
        "_job_contrib_cp_search.log",
        logger=logger,
    )
```

기존에 같은 로직을 인라인으로 갖고 있던 `flip_makespan_cp/dispatcher.py` 와
`cumulative_routine.py` (phase2 / phase4) 도 이 writer 로 교체했다.
`cumulative_routine` 쪽은 실패 로그가 `logger.warning` → `logger.exception`
으로 바뀌어 traceback 이 함께 남는다.

기존 `log_callback` + hint-completeness warning 체크는 그대로 유지 (별도 용도).

### 2-3. controller 에 solver status log 추가

`orchestration/AGENTS.md` invariant 2 (elapsed 측정 ~ `_register` 사이에는
작업을 끼워넣지 않는다) 때문에 **`self._register(...)` 직후**에 남긴다.
`elapsed` / `obj_value` 는 이미 지역 변수라 그대로 재사용한다.

```python
metrics = result.metrics if result is not None else None
self.logger.info(
    "job_contrib_cp: work_status=%s, cpsat_status=%s, "
    "jd_count_eff=%s, obj=%s, elapsed=%.3fs",
    record.work_status.name,
    metrics.get("cpsat_status", "N/A") if metrics is not None else "N/A",
    metrics.get("jd_count_eff", "N/A") if metrics is not None else "N/A",
    f"{obj_value:.1f}" if obj_value is not None else "N/A",
    elapsed,
)
```

### 2-4. controller 에서 `solver_log_path_getter` 전달

`JobContribCpOption(...)` 생성 시:

```python
option = JobContribCpOption(
    ...,
    log_search_progress=log_search_progress,
    solver_log_path_getter=self.get_file_path_for_subroutine,
)
```

---

## 3. 테스트 계획

### 3-1. `test_option.py` — `solver_log_path_getter` 필드 검증

```python
def test_solver_log_path_getter_defaults_to_none(self) -> None:
    opt = JobContribCpOption(jd_count_target=1)
    assert opt.solver_log_path_getter is None

def test_solver_log_path_getter_accepts_callable(self) -> None:
    opt = JobContribCpOption(jd_count_target=1, solver_log_path_getter=lambda s: Path(s))
    assert opt.solver_log_path_getter is not None
```

### 3-2. `test_dispatcher.py` — search log 파일 쓰기 검증

```python
class TestSearchLogOutput:
    """log_search_progress=true 일 때 search log 파일이 디스크에 쓰이는지."""

    def test_search_log_written_when_enabled(self, tmp_path: Path) -> None:
        ...

    def test_search_log_not_written_when_disabled(self, tmp_path: Path) -> None:
        ...
```

### 3-3. `test_job_contrib_cp_step.py` — controller log 검증

`caplog` fixture로 `work_status` / `cpsat_status` 가 로그에 포함되는지 확인.

### 3-4. `tests/algorithm/test_cpsat_search_log.py` — 공용 writer 단위 테스트

파일 쓰기 / 개행 보정 / `str` 경로 허용 / getter 없음 · 빈 로그 no-op /
IO 실패 시 예외를 삼키고 로그만 남기는지.

---

## 4. 검증

```bash
uv run pytest tests/algorithm/test_cpsat_search_log.py \
    tests/algorithm/job_contrib_cp \
    tests/orchestration/test_job_contrib_cp_step.py \
    tests/algorithm/test_flip_makespan_cp.py \
    tests/algorithm/mcf_lb -x
```
