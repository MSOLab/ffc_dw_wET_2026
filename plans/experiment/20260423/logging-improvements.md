# Plan: logging 시스템 개선 (CLI quiet mode + per-class/domain log files)

## Context

현재 `uv run main.py`는 logging이 미흡해 실험 관리 효율이 떨어짐.

- **터미널 noise**: INFO 레벨 로그가 항상 출력 → 10회 반복 실험 시 스크롤 홍수.
- **단일 log 파일**: `output/20260423/{timestamp}/{timestamp}_main.log` 하나에 `ffc_ddw_sum_et.main` 로거만 기록됨. `BenchmarkLoader`, `Reporter`, `Runner`, `Controller`, algorithm 모듈 등에서 발생하는 로그는 handler가 붙어있지 않아 **사실상 소실** (stderr에만 propagate, 파일에 없음).
- **routix v0.0.17 (2026-04-24 `feat(logger): add logger injection hooks`)이 logger injection API를 추가** — `SubroutineController`, `SingleInstanceRunner`, `MultiInstanceConcurrentRunner`, `MultiScenarioRunner`, `SolutionManager`, `SubroutineFlowValidator` 전부 `__init__(..., logger: logging.Logger | None = None)` 지원. 현재 프로젝트는 `routix>=0.0.16`으로 이 기능을 쓰지 못하는 상태.

목표:

1. `uv run main.py -q` → 터미널에 WARNING 이상만 (또는 완전 무음), 파일에는 전부 기록. 기본은 INFO까지 터미널 표시.
2. Class/도메인별로 적절한 log file에 기록.
3. routix 쪽 hierarchy 로그도 같은 파일 시스템에 포함.

## 현재 상태 요약

- Entry: `/home/hjt/code/ffc_ddw_sum_et/main.py` — CLI 파싱 없음. `_setup_main_logger(output_dir)`가 `ffc_ddw_sum_et.main` 한 로거에만 `FileHandler` + `StreamHandler`를 붙이고 `propagate=False`.
- Module-level `logging.getLogger(__name__)` 산재:
  - `src/ffc_ddw_sum_et/orchestration/benchmark_loader.py:11`
  - `src/ffc_ddw_sum_et/orchestration/reporting.py:23`
  - `src/ffc_ddw_sum_et/orchestration/ffcddw_single_instance_runner.py:24`
  - `src/ffc_ddw_sum_et/algorithm/cumulative_routine.py:*` (logger 파라미터로 주입)
- `FFcDDWSubroutineControllerCore`는 `self.logger = logging.getLogger(f"ffc_ddw_sum_et.{instance_name}")`을 수동 생성 — routix 주입 기능 미활용.
- Routix 의존: `routix>=0.0.16` (0.0.17로 bump 필요).
- Output dir helper: `routix.io.path.init_timestamped_working_dir(...)` → `output/{YYYYMMDD}/{timestamp}/` 반환.

## Design

### 0) Concurrency model — ProcessPoolExecutor 대응 (선결과제)

`routix.runner.MultiInstanceConcurrentRunner`는 `concurrent.futures.ProcessPoolExecutor`로
worker를 띄운다 (확인: `.venv/lib/.../routix/runner/multi_instance_concurrent_runner.py`).
부모 프로세스에서 root logger에 붙인 핸들러는 spawn된 worker에 **상속되지 않는다**.
이 점을 무시하면 `algorithm.cumulative_routine`, `algorithm.dispatcher.*`,
`orchestration.controller_core` 등 worker 내부에서 발생하는 — 즉 실험의 핵심 — 로그가
도메인 파일에 거의 들어오지 않는다.

대응안 (구현 첫 step에서 routix 코드를 직접 보고 결정):

- **A안 (선호)**: routix `MultiInstanceConcurrentRunner`가 `ProcessPoolExecutor(initializer=…, initargs=…)`
  훅을 노출하고 있으면, `setup_logging(output_dir, level)`을 worker initializer로
  주입한다. 각 worker가 시작될 때 한 번만 호출되어 동일한 핸들러 세트를 갖게 된다.
- **B안 (fallback)**: 훅이 없으면 `controller_core.__init__`(또는 `FFcDDWSingleInstanceRunner.run` 진입부)에서
  `setup_logging`을 idempotent하게 호출 (worker별 첫 controller 생성 시 설정).
  routix 패치 PR이 가능하면 A안으로 수렴.

이 대응이 빠지면 §1·§2 설계 전체가 단일 프로세스 메인 흐름 로그만 잡는 반쪽짜리가 된다.
검증은 §Verification의 worker 로그 도달 항목으로 마무리.

### 0a) routix 0.0.17 시그니처 사전 확인

`pyproject.toml`을 `routix>=0.0.17`로 bump한 직후 `uv pip install routix==0.0.17` →
`SubroutineController` / `SingleInstanceRunner` / `MultiInstanceConcurrentRunner` /
`MultiScenarioRunner` / `SolutionManager` / `SubroutineFlowValidator`의 `__init__`에
`logger` 파라미터가 실제로 있는지 확인:

```bash
uv run python -c "import inspect, routix; print(inspect.signature(routix.SubroutineController.__init__))"
```

(현재 lock된 0.0.16에는 `logger=`가 없다는 사실은 검증 완료. 0.0.17이 changelog대로
인젝션을 추가했는지 코드 레벨로 사전 확인하지 않으면 §3 전체가 무효가 된다.)

### 1) CLI 플래그 (`main.py`)

`argparse`로 아래 플래그 추가. (stdlib 유지 — typer/click 추가 의존 불필요.)

```python
parser = argparse.ArgumentParser(prog="ffc_ddw_sum_et")
parser.add_argument("-q", "--quiet", action="store_true",
                    help="Suppress INFO/DEBUG/WARNING on terminal; file log unaffected.")
parser.add_argument("-v", "--verbose", action="count", default=0,
                    help="Increase terminal verbosity. -v: INFO, -vv: DEBUG. (default: WARNING)")
parser.add_argument("--config", type=Path, default=CONFIG_PATH,
                    help="Path to experiment YAML config.")
```

- Mutually exclusive? YAGNI — 둘 다 지정되면 `-q`가 우선하는 정도면 충분.
- Stream handler level (`args.quiet`/`args.verbose`(int) 기반):
  - `args.quiet=True` → `ERROR`
  - `args.verbose==0` (default) → `WARNING`
  - `args.verbose==1` → `INFO`
  - `args.verbose>=2` → `DEBUG`
- File handler level은 항상 `INFO`(`DEBUG` 수준 file handler level은 추후 기능 추가).

### 2) 중앙 `setup_logging()` 함수

신규 모듈: `src/ffc_ddw_sum_et/logging_setup.py`

```python
def setup_logging(
    output_dir: Path,
    *,
    quiet: bool = False,
    verbose: bool = False,
) -> None:
    """Wire handlers onto ffc_ddw_sum_et/routix logger hierarchies.

    Writes per-domain log files under output_dir and configures terminal
    verbosity per CLI flags.
    """
```

내부 동작:

1. **Domain별 FileHandler 생성** — 각 파일은 `output_dir / f"{output_dir.name}_{domain}.log"`:

   | Domain logger prefix | 파일 suffix | 포함 범위 |
   |---|---|---|
   | `ffc_ddw_sum_et.orchestration` | `_orchestration.log` | Controller/Runner/Reporter/Loader |
   | `ffc_ddw_sum_et.algorithm` | `_algorithm.log` | dispatcher, cumulative_routine 등 |
   | `ffc_ddw_sum_et.io` | `_io.log` | IO 레이어 |
   | `routix` | `_routix.log` | routix 내부 로그 (logger injection으로 우리 트리에 합류한 라인은 해당 도메인 파일로 라우팅됨) |
   | `ffc_ddw_sum_et.main` | `_main.log` | 기존 호환 — `main()` 흐름 |

   **catch-all `_app.log`는 두지 않는다.** 도메인 파일과 모든 레코드가 중복 기록되어 §Verification의 "중복 기록 없음"과 모순되고 실효 가치도 낮다. 종합적으로 보고 싶으면 grep으로 합쳐 본다.

   구체적으로: 모든 파일 핸들러는 **루트에 한 번만** 붙이고, 핸들러마다 `addFilter(lambda r: r.name == prefix or r.name.startswith(prefix + "."))` 방식으로 라우팅. 로거 트리는 그대로 두고 파일만 분기되며, `propagate=False`로 인한 stream handler 단절 위험도 없다.

2. **터미널 StreamHandler 1개**를 루트에 붙임. Level은 위 플래그 매핑.

3. **handler 중복 부착 방지 (idempotency)**: `setup_logging`은 idempotent — 호출 시 root에 붙어있는 `_ffcddw_managed=True` 태그된 핸들러를 close + remove한 뒤 새로 부착한다.

   ```python
   for h in list(root.handlers):
       if getattr(h, "_ffcddw_managed", False):
           h.close()
           root.removeHandler(h)
   ```

   ProcessPoolExecutor worker에서 첫 호출 시는 태그가 없으므로 정상 부착. 동일 worker에서 두 번 호출되어도 정합 유지.

4. **포맷**:
   - 터미널: 짧게 — `"[%(levelname).1s %(asctime)s] %(message)s"` (레벨 첫 글자 + 시각 + 메시지)
   - 파일: 상세 — `"%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d) %(threadName)s: %(message)s"`

### 3) routix logger injection 활용

routix v0.0.17의 `logger=` 주입을 쓴다. `pyproject.toml`의 routix 핀을 `>=0.0.17`로 bump.

- **`FFcDDWSubroutineControllerCore.__init__`** 에서 수동으로 만들던
  `self.logger = logging.getLogger(f"ffc_ddw_sum_et.{self._instance_name}")`
  을 유지하되, 부모 `SubroutineController.__init__(..., logger=self.logger)`로 넘겨 routix 내부에서도 같은 로거를 쓰게 한다. (routix 내부 경로는 `routix.*` 트리에도 남기 원하면 주입 대신 getLogger 기본값 유지를 선택.)
- `FFcDDWSingleInstanceRunner` / `FFcDDWMultiInstanceRunner` / `FFcDDWMultiScenarioRunner` 생성 시에도 `logger=logging.getLogger("ffc_ddw_sum_et.orchestration.<ClassName>")`을 명시 주입해 로그가 우리 트리에 귀속되도록.
- 선택적으로: instance-scope 로그를 `ffc_ddw_sum_et.controller.<instance_name>` 같은 별도 sub-logger로 보내 `output/20260423/{timestamp}/instances/<instance_name>.log`에 per-instance 파일까지 얻는 확장 여지도 열어둠 (아래 Future 섹션).

### 4) 기존 모듈-level 로거 정리

대부분 `logger = logging.getLogger(__name__)`이라 이미 `ffc_ddw_sum_et.<module>` 하위로 들어옴 — 수정 불필요. 다만:

- `FFcDDWSubroutineControllerCore`: 로거 이름을 `ffc_ddw_sum_et.{instance_name}`에서 `ffc_ddw_sum_et.orchestration.controller.{instance_name}`로 바꿔 domain-prefix 매칭이 깨지지 않게 조정. (현재 prefix 는 `ffc_ddw_sum_et.orchestration`인데 `ffc_ddw_sum_et.{instance_name}`는 걸리지 않음 → 지금 규칙대로면 app catch-all에는 잡히고 orchestration.log에는 안 잡힘. 의도적 분리 아닌 이상 수정.)
- `FFcDDWMultiInstanceRunner`, `FFcDDWSolutionManager` — 현재 로깅 없음. routix 주입으로 자동 획득되므로 별도 작업 불필요. 필요 시 `self.logger.info(...)` 호출 몇 개 추가.

### 5) `main.py` 교체

기존 `_setup_main_logger` 제거하고 `setup_logging(output_dir, quiet=args.quiet, verbose=args.verbose)` 한 줄로 대체. `logger = logging.getLogger("ffc_ddw_sum_et.main")`은 그대로 사용 (파일 라우팅만 `setup_logging`이 책임).

## Critical files to change

- `src/ffc_ddw_sum_et/orchestration/controller_core.py:49` — **선결조건**. logger 이름을 `ffc_ddw_sum_et.{instance_name}`에서 `ffc_ddw_sum_et.orchestration.controller.{instance_name}`로 변경. 이게 없으면 controller 로그가 도메인 파일 어디에도 안 잡힘. + routix `super().__init__(..., logger=self.logger)` 주입.
- `pyproject.toml` — `routix>=0.0.17`. 첫 step (§0a 시그니처 확인 전제).
- `src/ffc_ddw_sum_et/logging_setup.py` **(신규)** — `setup_logging()` 구현 (§0 concurrency 대응 포함).
- `main.py` — argparse, 기존 `_setup_main_logger` 삭제, `setup_logging` 호출로 대체.
- routix `MultiInstanceConcurrentRunner` initializer 훅 (또는 우회) — §0 참조. routix에 hook이 없으면 PR 또는 controller_core에서 fallback 호출.
- `src/ffc_ddw_sum_et/orchestration/ffcddw_single_instance_runner.py` — `__init__`에서 `super().__init__(logger=logging.getLogger("ffc_ddw_sum_et.orchestration.FFcDDWSingleInstanceRunner"))`.
- `src/ffc_ddw_sum_et/orchestration/ffcddw_multi_instance_runner.py` — 동일 패턴.
- `src/ffc_ddw_sum_et/orchestration/reporting.py` — `FFcDDWMultiScenarioRunner.__init__`에 동일 패턴.

## Future (명시적 제외 — YAGNI)

아래는 이번 plan에서 **구현하지 않음**. 필요해지면 별도 plan:

- Per-instance log file (`instances/<instance_name>.log`) — `AddFilter` 기반 dynamic handler 등록 필요. **`instance_worker_cnt: 48`로 동시 실행 시 도메인 파일은 48개 인스턴스 로그가 시간순으로 뒤섞여 grep만 가능한 형태가 된다.** 운용상 per-instance 파일이 도메인 분할보다 우선순위가 높을 수 있으므로 1차 결과 확인 후 재평가.
- Log rotation. 실험은 단발성 (1 run = 1 dir) 이므로 rotation 불필요.
- JSON 구조화 로그. 현재 grep으로 충분.
- `LOG_LEVEL` 환경변수. `-q`/`-v` CLI로 충분.

## Verification

1. **CLI**: `uv run main.py -q` → 터미널에 INFO 메시지 전혀 안 뜸, stderr에 WARNING/ERROR만.
2. **CLI**: `uv run main.py` (no flag) → 기존처럼 INFO 이상 터미널 출력.
3. **CLI**: `uv run main.py -v` → DEBUG도 터미널에 뜸.
4. **파일 라우팅 확인** (after 1 run):

   ```
   output/20260423/{timestamp}/
     {timestamp}_app.log          # 전체 ffc_ddw_sum_et.* 로그
     {timestamp}_orchestration.log # Controller/Runner/Reporter/Loader
     {timestamp}_algorithm.log     # dispatcher, cumulative_routine
     {timestamp}_io.log            # IO 레이어 (로그 있으면)
     {timestamp}_routix.log        # routix 내부 로그
     {timestamp}_main.log          # 기존 호환 (main() 흐름만)
   ```

   각 파일이 비어있지 않고 prefix 규칙대로 분기되어 있는지 `head -n5` 로 확인.
5. **워커 프로세스 로그 도달 확인 (가장 중요)**: `_algorithm.log`에 `cumulative_routine`이나 dispatcher가 발생시킨 INFO 라인이 한 줄 이상 있는지 grep. **없으면 §0 Concurrency model 대응이 실패한 것** — 도메인 라우팅 자체가 무효이므로 design 재검토.
6. **중복 기록 없음**: `_main.log`의 라인이 도메인 파일과 중복되지 않는지 (catch-all을 빼면 자연 만족, 가드레일로 남김).
7. **Pytest**: 기존 `tests/` 스위트 전부 pass (logger 주입이 routix 쪽 타입 검사를 깨지 않는지).
8. **Ruff**: `uv run ruff check` / `uv run ruff format` 클린.

## Rollback

`main.py`의 `_setup_main_logger`만 복원하면 기존 동작 복귀. `setup_logging` 모듈과 controller_core 변경은 뒤따라 revert.

## Open questions (execution 단계 전에 확인 권장)

1. ~~controller logger 이름 재배치~~ — **결정**: `ffc_ddw_sum_et.orchestration.controller.{instance_name}`로 재배치 (§Critical files 첫 항목으로 승격).
2. routix `MultiInstanceConcurrentRunner`가 `ProcessPoolExecutor(initializer=…)`를 노출하는지 — 코드 첫 step에서 직접 확인 (§0 Concurrency model). 없으면 routix PR 또는 §0 B안 fallback.
