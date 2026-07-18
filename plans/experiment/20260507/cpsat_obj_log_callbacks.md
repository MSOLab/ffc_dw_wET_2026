# CP-SAT trajectory를 활용한 통합 `_obj_log.json` 출력

## 목표

`CpsatAdapter`가 in-tree CP-SAT callback(`src/ffc_ddw_sum_et/cpsat/callbacks/`)으로
시점별 (obj_value, obj_bound) trajectory를 수집해 **`AlgRecord.progress_log`**
필드에 채워 반환한다. controller 전체 다단계 실행이 끝나면 모든 step의
progress_log를 글로벌 timestamp로 합산해 `<ins>_obj_log.json`을 hybridflowshop
yaml과 동일한 mapping 구조의 **compact JSON**(단일 라인, 공백 최소)으로 저장한다.

산출 JSON 구조 (참조: `hybridflowshop/.../1_obj_log.yaml`의 mapping을 그대로
JSON으로):

```json
{"obj_value":{"name":"obj_value","data":{"<global_t_str>":<value>,...},"notes":{"<global_t_str>":"<step_label>",...}},"obj_bound":{"name":"obj_bound","data":{...},"notes":{...}}}
```

- 단일 라인. `json.dump(..., separators=(",", ":"), ensure_ascii=False)`로
  공백/줄바꿈 모두 제거 (compact 기본).
- 키는 timestamp의 `repr(float)` 문자열 — 풀-precision round-trip 보장.

## 비목표

- `mbls.cpsat.ObjValueBoundStore` 의존 추가하지 않는다.
- 누적 `obj_store`(mutation-time monotonic 검사 등)를 들이지 않는다.
- 새 `AlgTrajectory` 타입을 만들지 않는다 — 기존
  `AlgRecord.progress_log: tuple[ProgressLogEntry, ...]` 채널을 그대로 활용.
- `notes` 라벨 인덱싱 규칙을 새로 만들지 않는다 — routix
  `_get_call_context_of_current_method()` (예: `"7-solve_base_model_cpsat"`)
  를 그대로 사용.

## 전제 / 의존하는 invariant

- `c05f278` 이후: controller의 step 메서드는 register를 0회 또는 1회만 한다.
  stop guard 분기는 register 없이 `_make_stop_report`로 반환 (history에 안
  들어감 → obj_log 기여 없음). 따라서 `solution_manager.history`의 entry는
  "한 번 정상 완료된 step의 결과"와 1:1 대응.
- `FFcDDWSubroutineControllerCore`가 routix `SubroutineController`를 상속해
  `self.timer: ElapsedTimer`와 `_get_call_context_of_current_method()`를 보유.
- 기존 `<ins>_obj_log.yaml`(flat list)는 reader가 없는 것으로 직전 대화에서
  확인됨 → 파일을 통째로 대체(`.yaml` 산출 중지, `.json` 신규)해도 안전.
- routix 기본 schema의 `obj_log` kind는 `{instance_name}_obj_log.yaml`로
  고정되어 있고 `register_kind`는 redefine 불가. 새 kind `obj_log_json`을
  프로젝트 overlay에 등록해 사용한다(§4 참조). 기존 `obj_log` kind는
  ffc_ddw_sum_et 코드에서 호출하지 않음 → 사실상 dead path.
- `AlgRecord.progress_log: tuple[ProgressLogEntry, ...] | None`는 이미 존재.
  `ProgressLogEntry(elapsed_sec, obj_value, obj_bound, note)` — 단일 list,
  v / b / note 모두 optional. `neh_cp/dispatcher.py`가 per-batch 트래젝토리로
  사용 중. 본 plan은 이 채널을 cpsat에서도 동일 의미로 채워 reuse한다.

## 설계 결정 (사전 합의)

| 항목 | 결정 |
| --- | --- |
| 산출 범위 | controller 전체 다단계 (mcf_lb / neh_cp / cpsat 등 모두 포함) |
| 기존 `_obj_log.yaml` 처리 | 산출 중지(코드에서 호출 안 함). 신규 `_obj_log.json`을 새 kind `obj_log_json`로 출력 |
| 직렬화 포맷 | compact JSON (`json.dump(..., separators=(",", ":"))`) — 단일 라인, 공백 최소 |
| trajectory store 위치 | **누적 store 없음**. step별 report에 `start_time` + progress_log 부착 → end-of-run에 일괄 집계 |
| timestamp 기준 | controller 시작 기준 글로벌 elapsed (`start_time + entry.elapsed_sec`) |
| trajectory 시각의 reference | algorithm은 controller에 의존하지 않음. 알고리즘 진입을 0으로 잡은 로컬 시각(sec)으로 `progress_log.elapsed_sec` 기록 → controller가 `start_time`을 더해 글로벌화 |
| AlgRecord trajectory channel | **`AlgRecord.progress_log` 재사용** (C-shape: 단일 list, v/b/note optional). 새 필드 추가 없음 |
| post-semi-active obj_value vs CP-SAT 내부값 | progress_log 끝에 `ProgressLogEntry(elapsed_sec=final_t, obj_value=post_semi_active_obj_value, obj_bound=final_bound)` append |

### `progress_log`의 형태 — 채우는 규칙

- CP-SAT solution callback이 발화 → `ProgressLogEntry(elapsed_sec=t,
  obj_value=v, obj_bound=b)` (둘 다 채움; recorder 내부의 `ValueBoundPair`).
- CP-SAT `best_bound_callback` 발화 → `ProgressLogEntry(elapsed_sec=t,
  obj_value=None, obj_bound=b)` (bound만).
- 솔브 종료 시 final entry → `ProgressLogEntry(elapsed_sec=final_t,
  obj_value=post_semi_active_obj_value, obj_bound=final_bound)` 한 번만 append.
- `note` 필드는 algorithm 단계에서는 `None`으로 둔다 — 단계 라벨은 controller
  레벨에서 step end timestamp에만 stamp(아래 §6 참조).
- Entries는 `elapsed_sec`로 정렬 보장(append 순서). 동일 t의 dedup은 집계 단계에서
  first-writer-wins.

> 단위: `progress_log.elapsed_sec`는 sec(ortools recorder와 동일 단위).
> controller 글로벌화 시 `start_time + entry.elapsed_sec`.

## 변경 사항

### 1. `CpsatAdapter` callback wiring

`src/ffc_ddw_sum_et/algorithm/cpsat_adapter.py`:

추가 import:

```python
from .cpsat_callbacks.obj_value_recorder import ObjectiveValueRecorder
from .cpsat_callbacks.obj_bound_recorder import ObjectiveBoundRecorder
from .base.alg_record import ProgressLogEntry
```

`run()` 본문 변경 흐름:

1. solver build 직후 두 recorder 생성. 두 recorder의 `time_started`는 각자
   자체 캡처되는데, 둘 다 같은 monotonic clock이라 차이는 ~µs 수준 — 별도
   조정 없음. (필요 시 두 recorder 모두 같은 `time.monotonic()` 시점을
   외부에서 주입할 수 있도록 §부록 참조; 현 단계에선 미시행.)
2. `solver.best_bound_callback = bound_recorder` 세팅.
3. `solver.solve(mdl, solution_callback=value_recorder)` (기존
   `solver.solve(mdl)` → 키워드 인자 추가). `cpsat_solver_options.get_solver`
   가 표준 `ortools.sat.python.cp_model.CpSolver`를 반환하면 그대로 동작 —
   구현 시 한 번 확인.
4. solve 종료 후 progress_log 빌드:

   ```python
   entries: list[ProgressLogEntry] = []

   # solution callback에서 모인 (t, ValueBoundPair) → v와 b 모두 채움
   for t, vb in value_recorder.entries:
       entries.append(ProgressLogEntry(
           elapsed_sec=t,
           obj_value=float(vb.value),
           obj_bound=float(vb.bound),
       ))

   # best_bound_callback의 entry 중 timestamp가 위와 겹치지 않는 것만 추가
   value_t_set = {t for t, _ in value_recorder.entries}
   for t, b in bound_recorder.entries:
       if t in value_t_set:
           continue
       entries.append(ProgressLogEntry(
           elapsed_sec=t,
           obj_value=None,
           obj_bound=float(b),
       ))

   # elapsed_sec 오름차순 정렬
   entries.sort(key=lambda e: e.elapsed_sec)

   # feasible 경로에서만 final endpoint append
   if has_solution:
       entries.append(ProgressLogEntry(
           elapsed_sec=elapsed_sec,       # solve 종료 시점 (sec)
           obj_value=float(obj_value),    # post-semi-active
           obj_bound=float(obj_bound),
       ))
   ```

5. 두 return 경로(infeasible / feasible)의 `AlgRecord(...)` 모두에
   `progress_log=tuple(entries)` 포함. infeasible 경로는 callback에서
   모인 entries만 그대로 흘려보냄(append 없음, 빈 tuple 가능).

> 참고: hybridflowshop의 `controller_core.py:1210-1233`은 bound 시리즈를 만들
> 때 solution callback의 bound도 dedup 후 흡수한다. 위 4번이 해당 패턴.
> obj_value 시리즈는 solution callback에서만 만들어지므로 dedup 불필요.

### 2. `FFcDDWSubroutineReport` (project-local subclass)

새 모듈: `src/ffc_ddw_sum_et/orchestration/subroutine_report.py`

```python
from dataclasses import dataclass, field

from routix.report import SubroutineReport

from ..algorithm.base.alg_record import ProgressLogEntry


@dataclass(frozen=True)
class FFcDDWSubroutineReport(SubroutineReport):
    """SubroutineReport extended with controller-frame timing context and
    algorithm-frame trajectory.

    Fields beyond ``SubroutineReport``:

    - ``start_time``: controller-frame elapsed seconds at step entry.
      Used by the end-of-run aggregator to globalize ``progress_log``
      timestamps. Algorithm code never reads this — it lives only on
      the controller side.
    - ``progress_log``: algorithm-frame trajectory propagated from
      ``AlgRecord.progress_log``. Empty for steps that don't capture
      intra-step trajectories.
    - ``step_label``: ``_get_call_context_of_current_method()`` value at
      register time. Stamped onto the last timestamp of this step in
      the aggregated yaml.
    """

    start_time: float = 0.0
    progress_log: tuple[ProgressLogEntry, ...] = field(default_factory=tuple)
    step_label: str | None = None
```

> `SubroutineReport.elapsed_time`은 default 없음 — 자식 필드는 모두 default
> 보유 → frozen dataclass 상속의 default-순서 제약 위반 없음 (자식이 default
> 부여한 필드만 추가).

`FFcDDWSolutionManager`의 generic 인자를 갱신:

```python
class FFcDDWSolutionManager(
    SolutionManager[FFcDDWSubroutineReport, FFcDDWSolution]
): ...
```

routix `SolutionManager.register`가 `SubroutineReportT`를 받으므로 부모형
인스턴스도 그대로 들어가지만, 본 plan은 모든 step이 `FFcDDWSubroutineReport`
로 wrap된 형태로 register하도록 일관 적용한다(§4).

### 3. controller 변경 — `_wrap` helper로 일괄 적용

`FFcDDWSubroutineControllerCore`(controller_core.py)에 helper 추가:

```python
def _wrap_report(
    self,
    report: SubroutineReport,
    *,
    progress_log: tuple[ProgressLogEntry, ...] = (),
) -> FFcDDWSubroutineReport:
    """Promote a plain SubroutineReport to FFcDDWSubroutineReport with
    controller-frame ``start_time`` and ``step_label`` filled.

    ``start_time`` is derived as ``controller_now - report.elapsed_time``
    (call-site invariant: this is invoked at register time, immediately
    after the step body completes).

    ``progress_log`` is forwarded if the step has a captured trajectory;
    otherwise empty tuple (aggregator will synthesize a single endpoint
    from start_time + elapsed_time).
    """
    return FFcDDWSubroutineReport(
        elapsed_time=report.elapsed_time,
        obj_value=report.obj_value,
        obj_bound=report.obj_bound,
        start_time=self.timer.elapsed_sec - report.elapsed_time,
        progress_log=progress_log,
        step_label=self._get_call_context_of_current_method(),
    )
```

`solve_base_model_cpsat` (controller.py:2332~) — 유일하게 progress_log를
실어 보내는 step:

```python
record = CpsatAdapter().run(spec)
elapsed = time.monotonic() - start_elapsed
result = record.result
obj_value = (... 기존 로직 ...)
obj_bound = (... 기존 로직 ...)
schedule = result.schedule if result is not None else None

report = SubroutineReport(
    elapsed_time=elapsed,
    obj_value=obj_value,
    obj_bound=obj_bound,
)
wrapped = self._wrap_report(
    report,
    progress_log=record.progress_log or (),
)

if schedule is not None:
    self.solution_manager.register(
        wrapped,
        FFcDDWSolution(
            schedule=schedule, obj_value=obj_value, obj_bound=obj_bound
        ),
    )
else:
    self.solution_manager.register(wrapped, None)
return wrapped
```

다른 step 메서드(약 ~30개의 register 사이트):

기존 코드:

```python
self.solution_manager.register(report, sol)
return report
```

→

```python
wrapped = self._wrap_report(report)   # progress_log 비어있음
self.solution_manager.register(wrapped, sol)
return wrapped
```

`return report` 가 `SubroutineReport`로 typed된 경우, `FFcDDWSubroutineReport`
는 그 서브클래스이므로 호환. 시그니처 그대로 둔다.

> 일부 step은 helper(예: `apply_lb_by_mcf`)에서 inner step을 호출하며 그 결과
> `SubroutineReport`를 받아 자기 final report에 집어넣는 구조. inner의 register
> 분기가 `_register_report=False`라면 wrap 없이 그대로 반환되어 composite step의
> `_register_final` 안에서 wrap된다. 이미 invariant("한 step = 한 register")가
> 보장되어 있으므로 wrap이 정확히 register하는 step에서만 일어나도록 점검.

### 4. End-of-run aggregator (single_instance_runner)

`src/ffc_ddw_sum_et/orchestration/ffcddw_single_instance_runner.py`:

기존 `_save_obj_log` (line 468-493) 본문을 다음으로 교체:

```python
import json

def _save_obj_log(self, history) -> None:
    """Aggregate per-step ``progress_log`` into a single-line, compact
    JSON file matching hybridflowshop's yaml mapping shape.

    Per series (``obj_value`` / ``obj_bound``):
      * ``data``: timestamp(string) -> value/bound
      * ``notes``: timestamp(string) -> step_label  (only at step ends)

    Timestamps are controller-frame elapsed seconds, formatted via
    ``repr(float)`` to preserve full precision.
    """
    value_data: dict[str, float] = {}
    value_notes: dict[str, str] = {}
    bound_data: dict[str, float] = {}
    bound_notes: dict[str, str] = {}

    for record in history:
        report = record.report
        if not isinstance(report, FFcDDWSubroutineReport):
            # Defensive: any plain SubroutineReport that slipped past
            # the controller helper. Treat as zero-trajectory step
            # with no global start_time available — skip aggregation.
            continue

        # Algorithm-frame entries → globalize via report.start_time
        for entry in report.progress_log:
            t_global = report.start_time + entry.elapsed_sec
            key = repr(t_global)
            if entry.obj_value is not None:
                value_data.setdefault(key, float(entry.obj_value))
            if entry.obj_bound is not None:
                bound_data.setdefault(key, float(entry.obj_bound))

        # Step end endpoint + step_label note
        end_global = report.start_time + report.elapsed_time
        end_key = repr(end_global)
        label = report.step_label or ""
        if report.obj_value is not None:
            value_data.setdefault(end_key, float(report.obj_value))
            if label:
                value_notes[end_key] = label
        if report.obj_bound is not None:
            bound_data.setdefault(end_key, float(report.obj_bound))
            if label:
                bound_notes[end_key] = label

    if not (value_data or bound_data):
        return

    payload = {
        "obj_value": {
            "name": "obj_value",
            "data": value_data,
            "notes": value_notes,
        },
        "obj_bound": {
            "name": "obj_bound",
            "data": bound_data,
            "notes": bound_notes,
        },
    }

    out_path = self._layout.artifact_path(
        "obj_log_json",
        scenario_name=self._scenario_name,
        instance_name=self.ins_name,
    )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"), ensure_ascii=False)
```

호출부(controller.py:371) 시그니처는 그대로(`solution_manager.history`).

> `routix.io.dump_json`은 `indent=2` 고정이라 compact 출력에 부적합 — 위와
> 같이 inline `json.dump`로 직접 쓴다. 의존성 추가 없음.

#### artifact kind 등록

`metadata/artifact_layout/ffc_ddw_sum_et_v1.yaml`에 추가:

```yaml
- scope: instance
  zone: final
  kind: obj_log_json
  file_template: "{instance_name}_obj_log.json"
```

routix 기본의 `obj_log` kind(yaml)는 그대로 두되 ffc_ddw_sum_et 코드에서는
호출하지 않는다. 새 kind를 별도로 두는 이유: routix의 `register_kind`는
existing kind redefine을 거부함.

### 5. `time_stamped_recorder.py` 변경 — 시행 안 함

algorithm은 controller에 의존하지 않는다는 결정에 따라 recorder의
`time_started` 외부 주입은 도입하지 않음. controller가 `start_time`을 더해
글로벌화하는 것으로 충분.

> 향후 만약 algorithm 안에서 *여러 단계*가 있고 각 단계의 시작점을 정확히
> 같은 0으로 맞춰야 할 필요가 생기면 그때 시그니처 확장 (별도 PR).

## 마이그레이션 순서

1. (확인) `<ins>_obj_log.yaml`의 reader가 0개임을 한 번 더 확인 → 산출 중지
   안전.
2. `CpsatAdapter`에 callback wiring (§1).
   - 단위 테스트 1개: 작은 instance + 짧은 timelimit → progress_log 비어있지
     않음, 마지막 entry의 `obj_value` = post-semi-active 값,
     `obj_bound` = `solver.best_objective_bound`.
3. `FFcDDWSubroutineReport` 도입 + `FFcDDWSolutionManager` generic 갱신 (§2).
   - 기존 register 호출이 `SubroutineReport` 그대로 받아도 통과(부모형 호환)
     함을 확인하는 smoke 테스트.
4. `_wrap_report` helper 추가 + cpsat step 적용 (§3 첫 부분).
   - cpsat-only 시나리오 한 번 돌려 progress_log가 history에 흘러 들어가는지
     확인.
5. 모든 register 사이트(controller.py 전체)를 `_wrap_report`로 치환 (§3).
   - mechanical 1-line 치환. test suite 통과 확인.
6. `obj_log_json` artifact kind를
   `metadata/artifact_layout/ffc_ddw_sum_et_v1.yaml`에 추가 (§4 #artifact-kind).
7. `_save_obj_log` 교체: yaml dump → compact `json.dump` (§4).
   - 회귀 테스트: 기존 시나리오 한 번 실행 → 새 `.json` 파일이 단일 라인으로
     출력되는지, JSON parse 후 hybridflowshop yaml의 mapping 구조와 동일한지
     (top-level `obj_value` / `obj_bound`, 각각 `name / data / notes`).
   - notes 라벨이 `_get_call_context_of_current_method()` 그대로(예:
     `"7-solve_base_model_cpsat"`)인지.
8. 문서: `docs/io/20260429_artifact_manager.md`의 `obj_log` schema 섹션을 새
   `obj_log_json` 형식으로 업데이트(파일명 `.json`, 포맷 compact JSON).
   `add-subroutine` skill 본문의 `<ins>_obj_log.yaml` 언급도 `.json`으로 한 줄
   수정.

## 위험 / 미해결 질문

1. **`progress_log` 의미 확장.** `neh_cp/dispatcher.py`는 이 필드를
   "per-batch 결과 마커"로 쓰고 있고, cpsat에서는 "per-callback 시점값"으로
   쓴다. 둘 다 단일 단위(`(elapsed_sec, obj_value, obj_bound, note)`)이지만
   촘촘함 차이가 있음 — 같은 채널의 호환되는 의미 확장으로 본다.
   `ProgressLogEntry` 스키마 변경은 없음.
2. **frozen dataclass 상속의 default 정렬.** `SubroutineReport.elapsed_time`은
   default 없음 → subclass 필드 모두 default 부여로 회피. 검증 필요.
3. **history 항목 타입 비대칭.** §3에서 모든 register 사이트가 `_wrap_report`를
   거치도록 일괄 변경 → 비대칭 사라짐.
4. **cpsat trajectory의 size.** 큰 instance에서 solution callback이 자주 호출되면
   파일이 커질 수 있음 — yaml 줄바꿈으로 부풀던 부분은 compact JSON으로 대폭
   축소되지만, entry 개수 자체는 그대로. 우선 현행 유지하되, 대용량 인스턴스
   회귀 후 필요하면 truncation/sampling 도입.
5. **infeasible 경로의 trajectory.** value/bound recorder에 일부 entry가
   쌓였을 수 있음. 본 plan은 그대로 흘려 보냄(빈 progress_log가 아닐 수 있음).
   별 문제 없으나 의식해 둠.
6. **`repr(float)` 키.** hybridflowshop 예시 yaml의 timestamp 키는 float의 풀
   precision repr(`'0.7663798396242782'`). Python `repr`이 round-trip 안전한 표현을
   주므로 그대로 사용. JSON dict key는 자동으로 quoted string이라 yaml의
   `'...'` 처리와 동등.
7. **dict 순서.** Python 3.7+ dict는 insertion order 보존. `json.dump`도 기본
   `sort_keys=False`이므로 setdefault로 채워진 entries 순회 + step end 순서가
   그대로 보존됨.
8. **`obj_log` (yaml) kind 잔재.** routix 기본 schema에 정의된 채 ffc 코드에서
   호출되지 않는 dead path가 됨. 향후 routix를 vendoring 또는 schema 수정할
   여지 있을 때 정리.
