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

### 1) CLI 플래그 (`main.py`)

`argparse`로 아래 플래그 추가. (stdlib 유지 — typer/click 추가 의존 불필요.)

```python
parser = argparse.ArgumentParser(prog="ffc_ddw_sum_et")
parser.add_argument("-q", "--quiet", action="store_true",
                    help="Suppress INFO/DEBUG/WARNING on terminal; file log unaffected.")
parser.add_argument("-v", "--verbose", action="store_true",
                    help="Show DEBUG on terminal (default: INFO).")
parser.add_argument("--config", type=Path, default=CONFIG_PATH,
                    help="Path to experiment YAML config.")
```

- Mutually exclusive? YAGNI — 둘 다 지정되면 `-q`가 우선하는 정도면 충분.
- Stream handler level:
  - default → `WARNING`
  - `-q` → `ERROR`
  - `-v` → `INFO`
  - `-vv` → `DEBUG`
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

   | Domain logger | 파일 suffix | 포함 범위 |
   |---|---|---|
   | `ffc_ddw_sum_et` | `_app.log` | 프로젝트 전체 catch-all |
   | `ffc_ddw_sum_et.orchestration` | `_orchestration.log` | Controller/Runner/Reporter/Loader |
   | `ffc_ddw_sum_et.algorithm` | `_algorithm.log` | dispatcher, cumulative_routine 등 |
   | `ffc_ddw_sum_et.io` | `_io.log` | IO 레이어 |
   | `routix` | `_routix.log` | routix 내부 로그 |
   | `ffc_ddw_sum_et.main` | `_main.log` | 기존 호환 유지 |

   각 domain 로거에 `propagate=True` 유지. Catch-all `_app.log`는 최상위 `ffc_ddw_sum_et`에 붙이고 중복을 피하기 위해 하위 파일 핸들러와 겹치지 않게 **`propagate`는 꺼두지 않고, 대신 각 파일 핸들러에 `logging.Filter`로 로거 이름 prefix를 제약**. (propagate=False 남발하면 부모 stream handler도 끊기므로 주의.)

   구체적으로: 모든 파일 핸들러는 **루트에 한 번만** 붙이고, 핸들러마다 `addFilter(lambda r: r.name.startswith(prefix))` 방식으로 라우팅. 이렇게 하면 로거 트리 설정은 건드리지 않고 파일만 분기됨.

2. **터미널 StreamHandler 1개**를 루트 또는 `ffc_ddw_sum_et` 로거에 붙임. Level은 위 플래그 매핑.

3. **handler 중복 부착 방지**: `setup_logging`은 idempotent — 이미 붙은 핸들러(특정 attribute 태그로 식별, 예 `handler._ffcddw_managed = True`)는 제거 후 재설정.

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

- `src/ffc_ddw_sum_et/logging_setup.py` **(신규)** — `setup_logging()` 구현.
- `main.py` — argparse, 기존 `_setup_main_logger` 삭제, `setup_logging` 호출로 대체.
- `src/ffc_ddw_sum_et/orchestration/controller_core.py:49` — logger 이름 변경 + routix `super().__init__(..., logger=self.logger)` 주입.
- `src/ffc_ddw_sum_et/orchestration/ffcddw_single_instance_runner.py` — `__init__`에서 `super().__init__(logger=logging.getLogger("ffc_ddw_sum_et.orchestration.FFcDDWSingleInstanceRunner"))`.
- `src/ffc_ddw_sum_et/orchestration/ffcddw_multi_instance_runner.py` — 동일 패턴.
- `src/ffc_ddw_sum_et/orchestration/reporting.py` — `FFcDDWMultiScenarioRunner.__init__`에 동일 패턴.
- `pyproject.toml` — `routix>=0.0.17`.

## Future (명시적 제외 — YAGNI)

아래는 이번 plan에서 **구현하지 않음**. 필요해지면 별도 plan:

- Per-instance log file (`instances/<instance_name>.log`) — `AddFilter` 기반 dynamic handler 등록 필요. 현재는 scenario-level breakdown만으로 충분.
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
5. **중복 기록 없음**: `grep "some_unique_phrase" *.log | wc -l` 이 예상 개수와 일치 (app.log에 한 번 + domain.log에 한 번).
6. **Pytest**: 기존 `tests/` 스위트 전부 pass (logger 주입이 routix 쪽 타입 검사를 깨지 않는지).
7. **Ruff**: `uv run ruff check` / `uv run ruff format` 클린.

## Rollback

`main.py`의 `_setup_main_logger`만 복원하면 기존 동작 복귀. `setup_logging` 모듈과 controller_core 변경은 뒤따라 revert.

## Open questions (execution 단계 전에 확인 권장)

1. 도메인 prefix 분류 중 `ffc_ddw_sum_et.<instance_name>` 형태 로거 (`FFcDDWSubroutineControllerCore`의 기존 이름)를 **orchestration 하위로 재배치**할 것인가, 아니면 instance_name이 최상위인 현재 구조를 유지하면서 도메인 파일에 별도 prefix로 합류시킬 것인가? (plan은 재배치 쪽을 제안.)
