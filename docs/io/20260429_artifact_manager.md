# ArtifactLayout: 실험 출력 폴더의 schema 관리자

작성일: 2026-04-29
브랜치: `20260427_logging`
관련 파일: `src/ffc_ddw_sum_et/logging_setup.py`,
`src/ffc_ddw_sum_et/orchestration/{ffcddw_multi_instance_runner,ffcddw_single_instance_runner,reporting}.py`,
`routix/src/routix/io/path.py`, `routix/src/routix/runner/*.py`,
`routix/src/routix/subroutine_controller.py`

## 1. 배경 / 동기

현재 `20260427_logging` 브랜치에서는 layer별 로그를 분리해서 파일로 떨어뜨리려는
시도가 진행 중이다. `setup_logging`을 단계별로 다시 호출해 root logger 핸들러를
교체하는 방식으로 어느 정도 동작하지만, 다음 문제가 있다.

- 파일 경로 결정 로직이 호출 지점마다 흩어져 있다.
  - `MultiScenarioRunner.run` 안의 `_benchmark_log_path`,
  - `FFcDDWSingleInstanceRunner.run` 안의 `working_dir / f"{ins_name}_solve.log"`,
  - `_persist_run_artifacts` 안에 박혀 있는 모든 `working_dir / f"{ins_name}_..."`
    경로,
  - `Reporter.generate_summary_filename`, `generate_report_filename` 같은 보고서
    이름 규칙.
- 같은 timestamp / scenario / instance 좌표에서 여러 종류의 artifact가 산출되는데
  (log, schedule yaml, gantt png, statistics, manifest, summary csv, ...), 그 좌표
  자체와 파일명 규약이 코드 곳곳에 박혀 있다.
- POST_PROCESS_ONLY 모드의 `Reporter`가 "이 instance의 manifest는 어디 있어?"를
  알아내는 방법은 그냥 `working_dir / f"{ins_name}_instance_result.yaml"`를 직접
  꿰는 것이다 — runner와 reporter가 같은 규칙을 두 곳에서 hard-code하고 있다.
- 사용자(또는 외부 분석 스크립트)가 출력 디렉터리를 열어 무엇이 어디 있는지
  파악하려면 코드를 직접 읽어야 한다. 디렉토리 구조의 single source of truth가
  없다.

이 문서는 위 문제를 풀기 위한 "출력 폴더의 schema 관리자"를 도입하는 방향과,
그를 위해 이 저장소(`ffc_ddw_sum_et`)와 `routix` 양쪽에서 필요한 변경을
정리한다.

## 2. 사용자가 의도한 디렉터리 구조

instance 디렉터리는 **세 개의 zone**으로 나뉜다:

| zone        | 위치                           | 의미                                                        |
| ----------- | ------------------------------ | ----------------------------------------------------------- |
| `final`     | instance 디렉터리 바로 아래    | 한 instance의 **최종 결과**. 다른 도구가 의존해도 되는 면.  |
| `progress/` | instance 디렉터리 / `progress/`| 실행 **과정 기록**. 추후 분석용. step log, phase 중간 산출. |
| `report/`   | instance 디렉터리 / `report/`  | **사후 생성** artifact. gantt PNG 등 reporter가 만든 것.    |

```plaintext
output/
└── <base_output_dir_name>/                  # 예: 20260427_smoke_io
    └── <run_id>/                            # 예: 20260429T114816_361693 (timestamp)
        ├── <run_id>_main.log                # main.py
        ├── <run_id>_MultiScenarioRunner.log # MultiScenarioRunner
        ├── <experiment_config>.yaml         # 입력 yaml 사본
        ├── <run_id>_summary.csv             # Reporter 산출 (cross-scenario)
        ├── ...                              # Reporter 산출 (excel, pivot html, ...)
        └── <scenario_name>/                 # 예: smoke_io
            ├── <run_id>_MultiInstanceRunner.log
            ├── <scenario_name>_benchmark.log    # (기존, 호환)
            ├── <scenario_name>_statistics.yaml
            ├── <scenario_name>_mcf_lb_analysis.csv
            ├── <scenario_name>_csr_analysis.csv
            └── <instance_name>/             # 예: Instance_50_5_3_0,2_0,2_10_Rep0
                │
                │ ── final zone ──
                ├── <run_id>_SingleInstanceRunner.log
                ├── <run_id>_SubroutineController.log
                ├── <ins>_instance_result.yaml   # manifest (마지막에 atomic write)
                ├── <ins>_obj_log.yaml           # objective trajectory
                ├── <ins>_solution.json          # 유일한 solution serialization
                │
                │ ── progress zone ──
                ├── progress/
                │   ├── <ins>_<step_idx>-<method>_step_log.yaml
                │   ├── <ins>_mcf_lb_diagnostic.yaml
                │   ├── <ins>_<phase_name>.yaml          # MCF-LB phase schedules
                │   ├── <ins>_last_stage_only_schedule.yaml
                │   └── <call_context>_incremental_job_contrib_cp_progress.json
                │
                │ ── report zone ──
                └── report/
                    ├── <ins>_gantt.png
                    ├── <call_context>_incremental_job_contrib_cp_progress.png
                    ├── <ins>_<phase_name>_gantt.png
                    └── <ins>_<phase_name>_mcf_preemptive_gantt.png
```

핵심 규칙:

- log 파일은 자기 layer가 "soak through"되는 가장 안쪽 디렉터리에서 생성된다
  (instance log는 instance final zone, scenario log는 scenario 디렉터리, ...).
- log 파일명 prefix는 `<run_id>` (run-root의 디렉터리 이름과 동일).
  scenario / instance 단위로 자기 timestamp을 만들지 않는다 — 모두 같은 run에
  속한다는 사실을 파일명만 봐도 알 수 있도록.
- 비-log artifact의 prefix는 그 artifact를 가장 잘 식별하는 좌표를 쓴다
  (instance 단위는 `<ins>`, scenario 단위는 `<scenario_name>`, run 단위는
  `<run_id>`).
- **instance final zone에는 최종 결과만 둔다**. 현재 코드가 instance 디렉터리에
  직접 떨구고 있는 것 중 다음은 위치 변경 또는 삭제 대상:
  - `<ins>_schedule.yaml` — 삭제 (정보가 `_solution.json`에 포함). solution
    serialization 형태는 `solution.json` 하나로 통일하고, `_schedule.yaml`,
    `_schedule.json`, `_solution.yaml`은 만들지 않는다.
  - `<ins>_statistics.{yaml,json}` — `_instance_result.yaml`의 subset이라 SSOT
    위반. § 7 위험 항목에서 처리 옵션 제시.
  - `<ins>_<phase_name>.yaml` (MCF-LB phase schedule) → `progress/`.
  - `<ins>_mcf_lb_diagnostic.yaml` → `progress/`.
  - `<ins>_last_stage_only_schedule.yaml` → `progress/`.
  - `<step_idx>-<method>_step_log.yaml` (현재 controller가 워킹 디렉터리에 직접
    쓰는 것) → `progress/`.
  - `<ins>_*_gantt.png` (Reporter 산출) → `report/`.
- **project-specific subclass의 책임 범위는 단 하나**: 각 artifact `kind`가
  `final` / `progress` / `report` 중 어느 zone으로 가는지, 그리고 zone 안에서의
  파일명 template을 선언. 실제 path 조립은 base class가 처리.

## 3. 도입할 추상화: `ArtifactLayout`

DB schema 관리자 비유를 그대로 옮기면:

| DB schema 관리자                     | ArtifactLayout                                |
| ------------------------------------ | --------------------------------------------- |
| 테이블 정의 (DDL)                    | 디렉터리/파일 layout 정의 (yaml)              |
| 컬럼 → 물리 위치 매핑                | (run_id, scenario, instance, kind) → Path     |
| migration tool                       | 빈 디렉터리 ensure / 기존 디렉터리 detect     |
| ORM의 query helper                   | reporter가 쓰는 `find_*` / `iter_*` API       |
| schema version                       | `_schema_version` (이미 manifest에 존재)      |

### 3.1 schema 데이터 (language-independent)

`metadata/artifact_layout/v1.yaml` 같은 파일에 layout 규약을 yaml로 둔다.
대략:

```yaml
schema_version: 1
scopes:
  - name: run
    parent: null
    dir_template: "{base_output_dir}/{run_id}"
  - name: scenario
    parent: run
    dir_template: "{run_dir}/{scenario_name}"
  - name: instance
    parent: scenario
    dir_template: "{scenario_dir}/{instance_name}"

logs:
  - scope: run
    role: main
    file_template: "{run_id}_main.log"
  - scope: run
    role: multi_scenario_runner
    file_template: "{run_id}_MultiScenarioRunner.log"
  - scope: scenario
    role: multi_instance_runner
    file_template: "{run_id}_MultiInstanceRunner.log"
  - scope: instance
    role: single_instance_runner
    file_template: "{run_id}_SingleInstanceRunner.log"
  - scope: instance
    role: subroutine_controller
    file_template: "{run_id}_SubroutineController.log"
  - scope: instance
    role: algorithm           # 의도적으로 SubroutineController와 동일 파일
    file_template: "{run_id}_SubroutineController.log"

# zone 규칙 (validation 필수, fail-fast):
#   - scope=instance: `zone` 필드 **필수**. {final|progress|report} 중 하나.
#                     누락 → ValueError. default 없음.
#   - scope=run / scenario: `zone` 필드 **금지**. 들어오면 ValueError.
#
# Rationale: zone은 SSOT 보호 contract의 핵심 분류이므로 작성자가 매 entry마다
# 명시적으로 결정하도록 강제한다. default=final로 두면 새 kind 추가 시 zone을
# 빠뜨린 entry가 자동으로 final(가장 보호되어야 할 zone)에 들어가, 과거
# `_statistics.{yaml,json}`이 silently final에 자리잡았던 SSOT 위반 사고가
# 재현될 수 있다 (§ 7.1). § 7.3의 scenario 중복 검사와 동일한 fail-fast 정신.

artifacts:
  # ---- instance / final ----
  - scope: instance
    zone: final
    kind: instance_result_manifest
    file_template: "{instance_name}_instance_result.yaml"
  - scope: instance
    zone: final
    kind: solution_json
    file_template: "{instance_name}_solution.json"
  - scope: instance
    zone: final
    kind: obj_log
    file_template: "{instance_name}_obj_log.yaml"

  # ---- instance / progress ----
  - scope: instance
    zone: progress
    kind: step_log
    file_template: "{instance_name}_{step_idx}-{method}_step_log.yaml"
  - scope: instance
    zone: progress
    kind: mcf_lb_diagnostic
    file_template: "{instance_name}_mcf_lb_diagnostic.yaml"
  - scope: instance
    zone: progress
    kind: mcf_lb_phase_schedule
    file_template: "{instance_name}_{phase_name}.yaml"
  - scope: instance
    zone: progress
    kind: last_stage_only_schedule
    file_template: "{instance_name}_last_stage_only_schedule.yaml"

  # ---- instance / report ----
  - scope: instance
    zone: report
    kind: gantt_png
    file_template: "{instance_name}_gantt.png"
  - scope: instance
    zone: report
    kind: phase_gantt_png
    file_template: "{instance_name}_{phase_name}_gantt.png"
  - scope: instance
    zone: report
    kind: preemptive_gantt_png
    file_template: "{instance_name}_{phase_name}_mcf_preemptive_gantt.png"

  # ---- scenario ----
  - scope: scenario
    kind: benchmark_log
    file_template: "{scenario_name}_benchmark.log"
  - scope: scenario
    kind: scenario_statistics
    file_template: "{scenario_name}_statistics.yaml"
  - scope: scenario
    kind: mcf_lb_analysis
    file_template: "{scenario_name}_mcf_lb_analysis.csv"
  - scope: scenario
    kind: csr_analysis
    file_template: "{scenario_name}_csr_analysis.csv"

  # ---- instance ----
  - scope: instance
    zone: progress
    kind: job_contrib_progress_json
    file_template: "{call_context}_incremental_job_contrib_cp_progress.json"
  - scope: instance
    zone: report
    kind: job_contrib_progress_plot_png
    file_template: "{call_context}_incremental_job_contrib_cp_progress.png"

  # ---- run ----
  - scope: run
    kind: summary_csv
    file_template: "{run_id}_summary.csv"
  - scope: run
    kind: report_xlsx
    file_template: "{run_id}_report.xlsx"
  - scope: run
    kind: mcf_lb_dashboard
    file_template: "{run_id}_mcf_lb_dashboard.html"
```

`<ins>_statistics.yaml`, `<ins>_statistics.json`, `<ins>_schedule.yaml`은
**의도적으로 schema에서 빠진다** (§ 2 의 SSOT 정리). 기존 디렉터리를 열 때는 § 7
의 backwards-compat 처리 참조.

이 yaml은 routix 패키지가 들고 있고, project별로 추가하고 싶은 artifact가 있다면
`overlay` 파일로 위에 덮어쓸 수 있게 한다 (e.g. ffc_ddw_sum_et에서
`mcf_lb_phase_schedule`을 추가).

### 3.2 interpreter (Python)

`routix.io.artifact_layout.ArtifactLayout`이 그 yaml을 읽어 다음을 제공한다:

```python
Zone = Literal["final", "progress", "report"]

class ArtifactLayout:
    """Base class. project별 추가 kind은 subclass에서 register."""

    def __init__(self, *, run_root: Path, run_id: str, schema_path: Path | None = None): ...

    # ---- scope path ----
    def run_dir(self) -> Path: ...
    def scenario_dir(self, scenario_name: str) -> Path: ...
    def instance_dir(self, scenario_name: str, instance_name: str) -> Path: ...

    # ---- zone path (instance scope only) ----
    def zone_dir(
        self, zone: Zone, *, scenario_name: str, instance_name: str,
    ) -> Path:
        """final → instance_dir 그대로 / progress → instance_dir/progress /
        report → instance_dir/report. 첫 호출에서 mkdir."""

    # ---- log ----
    def log_path(
        self,
        role: Literal["main", "multi_scenario_runner",
                       "multi_instance_runner",
                       "single_instance_runner",
                       "subroutine_controller"],
        *,
        scenario_name: str | None = None,
        instance_name: str | None = None,
    ) -> Path: ...

    # ---- artifact ----
    def artifact_path(
        self, kind: str, *,
        scenario_name: str | None = None,
        instance_name: str | None = None,
        **placeholders: str,
    ) -> Path:
        """schema entry의 zone을 따라 자동으로 final/progress/report로 라우팅."""

    # ---- POST_PROCESS_ONLY ----
    def discover_scenarios(self) -> list[str]: ...
    def discover_instances(self, scenario_name: str) -> list[str]: ...
    def find_instance_manifests(self, scenario_name: str) -> list[Path]: ...
    def find_artifacts(
        self, kind: str, *,
        scenario_name: str | None = None,
        instance_name: str | None = None,
    ) -> list[Path]:
        """zone 안에서 glob. e.g. find_artifacts('gantt_png', ...) → report/*.png"""

    # ---- subclass extension hook ----
    def register_kind(
        self, kind: str, *,
        scope: Literal["run", "scenario", "instance"],
        zone: Zone | None = None,             # required iff scope=="instance"
        file_template: str,
    ) -> None:
        """project subclass가 추가 kind을 등록.

        zone 규칙 (yaml schema와 동일, fail-fast):
        - scope="instance": zone 필수. None이면 ValueError.
        - scope="run"/"scenario": zone 금지. None이 아니면 ValueError.
        """

    # ---- serialize ----
    def stamp(self) -> Path: ...   # writes <run_id>_artifact_layout.yaml
```

호출 측은 절대 `working_dir / f"{ins}_..."` 같은 문자열 조립을 하지 않는다.
모든 경로는 layout에게 묻는다.

**project-specific subclass 책임**은 `register_kind` 호출만으로 끝난다. 베이스
클래스가 zone routing(`final`/`progress/`/`report/`)과 디렉터리 생성을 처리하기
때문에, subclass는 "이 kind는 어느 scope, 어느 zone, 어떤 파일명으로" 만 선언.
예:

```python
class FFcArtifactLayout(ArtifactLayout):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.register_kind(
            "mcf_lb_phase_schedule",
            scope="instance", zone="progress",
            file_template="{instance_name}_{phase_name}.yaml",
        )
        self.register_kind(
            "mcf_lb_diagnostic",
            scope="instance", zone="progress",
            file_template="{instance_name}_mcf_lb_diagnostic.yaml",
        )
        # ... gantt_png, preemptive_gantt_png 등
```

(yaml overlay 방식과 sub-classing 방식은 둘 다 가능하게 두되, 기본 권장은
yaml overlay. subclass는 yaml로 표현하기 어려운 dynamic 분기가 필요할 때만
사용.)

### 3.3 왜 yaml + interpreter로 쪼개나

- POST_PROCESS_ONLY 모드는 **다른 시점에 다른 코드 버전**으로 만든 디렉터리를
  열어 처리해야 한다. interpreter만 진화하고 schema는 stamp된 yaml을 따라가면,
  과거 run에 대해서도 동일한 "이 파일은 무슨 kind지?" 판단이 가능하다.
- 외부 도구(분석 노트북, 시각화 스크립트, 다른 언어 도구)도 yaml만 보고
  레이아웃을 알 수 있다 — 이게 사용자가 강조한 "language-independent" 의도.
- routix 안에서 layout 규약을 단일 source로 갖고 있고, 프로젝트는 overlay만
  올린다 → DRY.

## 4. routix 변경 사항

대상 저장소: `D:\code\routix`

### 4.1 신설 모듈 `routix.io.artifact_layout`

- `ArtifactLayout` 클래스 (위 § 3.2 시그니처).
- 기본 schema yaml `routix/src/routix/io/_default_artifact_layout.yaml`.
  - `main`/`multi_scenario_runner`/`multi_instance_runner`/`single_instance_runner`/`subroutine_controller`/`algorithm`
    6개 logger role 정의 포함.
  - manifest, benchmark_log, scenario_statistics 등 routix 일반 artifact 정의 포함
    (프로젝트 특화 kind는 overlay).
- placeholder 치환은 `str.format`으로 충분 (sandbox 필요 없음).

### 4.2 `init_timestamped_working_dir` 확장

`routix/src/routix/io/path.py`:

- 현재는 `Path` 한 개를 리턴. 호출 측에서 `e_timer.get_start_dt_for_dir_name()`을
  파생해 쓸 수 없다 (timer를 받지 않으면 새로 만들기 때문).
- 다음 둘 중 하나로 변경:
  - 옵션 A: `init_run_root(...)` 신규 함수가 `(run_root: Path, run_id: str,
    e_timer: ElapsedTimer)` 3-tuple 또는 `RunRoot` dataclass를 리턴.
  - 옵션 B: 기존 `init_timestamped_working_dir`가 `RunRoot`를 리턴하도록 깨고,
    호출자(ffc_ddw_sum_et `main.py`, hybridflowshop 등)를 함께 갱신.

옵션 A가 호환성 측면에서 안전.

### 4.3 `MultiScenarioRunner` / `MultiInstanceRunner` / `SingleInstanceRunner`

- 생성자에 `layout: ArtifactLayout | None = None`을 받도록 확장.
- `layout`이 주어지면:
  - scenario 디렉터리는 layout이 결정 (`output_subdir`은 layout의
    `scenario_name` placeholder로 forward).
  - instance 디렉터리는 layout이 결정 (`SingleInstanceRunner._init_working_dir`
    가 `layout.instance_dir(...)`을 호출하도록).
- `layout`이 None이면 현재 동작 유지 (backwards-compatible).
- `MultiScenarioRunner`에 `_get_log_path(role, scenario_name=None)` helper 추가
  (지금 ffc 쪽에 있는 `_benchmark_log_path` 일반화).

### 4.4 `SubroutineController` 보정 — level-filtered propagation + algorithm logger 명시 전달

#### 4.4.1 SC log 처리의 rationale

상위 layer (main / MultiScenarioRunner / MultiInstanceRunner /
SingleInstanceRunner)는 **swap-on-root 방식의 현재 동작을 그대로 유지**한다
(Python 기본 동작에 맡김). SubroutineController + algorithm 사이는 **격벽이 아니라
level-filtered propagation**으로 처리한다. 이유:

- SC와 algorithm은 step 단위, batch 단위, dispatcher 단위로 record를 쏟아낸다.
  하나의 instance solve 동안 수천~수만 줄이 자연스럽게 발생. 절대 다수가
  DEBUG/INFO.
- 이 noise가 SIR.log로 그대로 propagate되면 SIR.log를 사람이 읽을 수 없게 된다
  (SIR 자신의 record는 instance 시작/종료/post-process 정도 수십 줄 수준이므로
  SC 소음에 묻힌다).
- 그렇다고 완전 격벽(`propagate=False`)으로 끊으면 **SC ERROR가 SIR.log에
  surface되지 않아** instance failure 진단 시 SC.log를 따로 열어야 한다. 이건
  SIR가 자기 layer에서 "이 instance가 왜 죽었는지"를 보지 못한다는 뜻.
- 절충: SC namespace 기원 record 중 **WARNING/ERROR/CRITICAL은 그대로 propagate,
  INFO/DEBUG는 drop**. 이러면 SIR.log는 SC noise에서 자유롭되 instance failure
  signal은 즉시 보임.

#### 4.4.2 구현

`record.name` 기반의 표준 `logging.Filter`로 SC noise만 상위에서 차단:

```python
# routix.logging
class PrefixLevelFilter(logging.Filter):
    """주어진 prefix로 시작하는 record는 WARNING 이상만 통과시킨다.

    record는 변형하지 않으며 (level/lineno 보존), 다른 logger 기원 record는
    그대로 통과. 부모 file handler에 부착해 SIR.log를 SC noise에서 보호.
    console handler에는 부착하지 않아 사용자가 console로 SC 진행을 볼 수 있게
    한다.
    """

    def __init__(self, prefix: str):
        super().__init__()
        self._prefix = prefix

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name.startswith(self._prefix):
            return record.levelno >= logging.WARNING
        return True
```

caller 흐름:

```python
# main.py 또는 SIR가 file handler 갈아끼울 때 (한 번만 부착하면 됨)
from routix.logging import PrefixLevelFilter, attach_fh_to_logger, detach_fh_from_logger

sc_filter = PrefixLevelFilter("ffc_ddw_sum_et.orchestration.controller")
sir_file_handler.addFilter(sc_filter)        # SIR.log 보호
# console handler에는 부착 안 함 → SC 진행이 console에는 그대로 보임

# SingleInstanceRunner가 controller 진입 직전에 (idempotent helper로)
sc_logger_name = f"ffc_ddw_sum_et.orchestration.controller.{ins_name}"
attach_fh_to_logger(
    sc_logger_name,
    layout.log_path("subroutine_controller", ...),
)
# helper가 logger.setLevel(DEBUG), DEBUG-level FileHandler 부착, defensive sweep을 처리.
# propagate는 건드리지 않음 (Python 기본 True 그대로) — 위로 흐르되 filter가 INFO/DEBUG만 drop.
```

- SC.log: SC logger 자체 file handler가 받으므로 full fidelity (DEBUG~ERROR).
- SIR.log: root file handler에 attach된 filter가 SC namespace의 INFO/DEBUG만
  drop. SIR own records, MIR/MSR/main records는 영향 없음. SC의 WARNING/ERROR는
  통과 → instance failure가 SIR.log에 surface.
- console: filter 없는 stream handler가 모든 record를 사용자 -q/-v level에
  맞춰 출력 (SC INFO도 console에선 보임).
- algorithm은 `__init__(..., logger: Logger | None = None)`으로 logger를 받고,
  controller가 호출할 때 `logger=self.logger`를 명시 전달. None이면 module-level
  `logging.getLogger(__name__)` fallback (`algorithm-principles.md` Rule 4·6·8
  적용). algorithm은 SC logger 인스턴스를 그대로 들고 쓰므로 record.name이
  SC namespace로 시작 → filter 적용 대상.

#### 4.4.3 layout 통합

- `set_working_dir` 호출자를 그대로 두되, `set_artifact_layout(layout,
  scenario_name, instance_name)`을 추가해 controller / algorithm이 instance
  scope의 log/artifact 경로를 layout에 물어볼 수 있게 한다.
- `try_get_file_path_for_subroutine`은 layout 기반으로 동작 (progress zone에
  step_log 떨어뜨리도록).

#### 4.4.4 각 log 파일에 들어가는 record (요약)

| log file | 내용 | 메커니즘 |
| --- | --- | --- |
| `<run_id>_main.log` | main.py active 동안 emit된 propagating record | swap-on-root |
| `<run_id>_MultiScenarioRunner.log` | MSR active 동안의 propagating record | swap-on-root |
| `<run_id>_MultiInstanceRunner.log` | MIR active 동안의 propagating record | swap-on-root |
| `<run_id>_SingleInstanceRunner.log` | SIR own record + **SC/algorithm WARNING/ERROR/CRITICAL** (instance failure가 SIR.log에 surface) | swap-on-root + `PrefixLevelFilter` |
| `<run_id>_SubroutineController.log` | SC + algorithm record (DEBUG~ERROR full fidelity) | SC logger 직접 file handler attach + `propagate=True` + 같은 logger 객체 공유 |

console에는 SC 진행 record까지 모두 통합으로 보임 (root stream handler가
propagation으로 자동 수신, filter는 file handler에만 부착).

### 4.5 logging helper의 routix 이전 검토

`ffc_ddw_sum_et/logging_setup.py`는 root logger에 핸들러를 push/replace 하는
generic한 helper이다. 같은 패턴을 hybridflowshop 등 다른 프로젝트도 쓸 가능성이
있으므로:

- routix에 `routix.logging` 모듈 도입 (도입 완료):
  - `attach_fh_to_logger(logger_name, file_path)` /
    `detach_fh_from_logger(logger_name)` — 임의 logger에 DEBUG-level
    FileHandler를 부착/해제. `_MANAGED_TAG`로 자기가 부착한 handler만
    추적하며 attach/detach 진입에서 defensive sweep. `propagate`는 건드리지
    않음 (Python 기본 True 유지).
  - `PrefixLevelFilter(prefix)` — `record.name`이 `prefix`로 시작하는 record
    중 INFO/DEBUG를 drop하는 표준 `logging.Filter`. SIR/MIR/MSR file handler에
    한 번 부착하면 process 수명 동안 작동 (stateless).
  - 후속 후보(미도입): `LogScope` (swap-on-root용 컨텍스트 매니저) — 현재
    `ffc_ddw_sum_et`의 `setup_logging` push/replace 패턴을 일반화한 형태.
    실수요가 안정된 다음 promotion.

## 5. ffc_ddw_sum_et 변경 사항

### 5.1 layout overlay yaml

- `metadata/artifact_layout/ffc_ddw_sum_et_v1.yaml` 신설.
  - `mcf_lb_phase_schedule`, `mcf_lb_diagnostic`, `mcf_lb_analysis`,
    `mcf_lb_dashboard`, `mcf_lb_lastStageOnlyObj_*` 등 ffc 고유 kind 정의.
  - `phase_name` 같은 추가 placeholder 사용.

### 5.2 `main.py`

- `init_timestamped_working_dir` → `init_run_root` 호출로 교체. `run_id`,
  `run_root` 둘 다 받음.
- `ArtifactLayout(run_root=run_root, run_id=run_id, schema_path=...)` 생성하고
  `layout.stamp()`로 yaml 사본을 떨군 뒤, runner에 layout을 주입.
- `setup_logging(layout.log_path("main"), ...)` 호출.
- `MultiScenarioRunner` 생성자에 `layout=layout` 추가.

### 5.3 `FFcDDWMultiScenarioRunner`

- `_benchmark_log_path` 제거 → `self.layout.log_path("multi_scenario_runner")`,
  `self.layout.log_path("multi_instance_runner", scenario_name=...)` 사용.
- 현재 `scenario_output_dir`을 `multi_instance_runner.output_dir`에서 추출하는
  로직을 layout 기반으로 단일화.

### 5.4 `FFcDDWMultiInstanceRunner`

- 자기 layer의 log path를 layout에 묻고, `setup_logging`을 push 후 자식 runner를
  돌린다 (현재는 process pool initializer로 setup_logging을 부르고 있음 — 이건
  child process마다 instance scope log를 setup해야 하므로 그대로 유지).
- `_init_single_instance_runners`에서 `s_i_runner_class(..., layout=self.layout,
  scenario_name=self.scenario_name, ...)`을 넘긴다.

### 5.5 `FFcDDWSingleInstanceRunner`

- `working_dir` 자체 결정 로직 제거 → `layout.instance_dir(scenario_name,
  instance_name)`.
- `run()` 안의 `setup_logging(self.working_dir / f"{self.ins_name}_solve.log",
  ...)` →
  `setup_logging(self.layout.log_path("single_instance_runner",
  scenario_name=..., instance_name=...), ...)`. (swap-on-root 그대로.)
- **`PrefixLevelFilter` 부착은 한 번만**: `setup_logging`이 SIR file handler를
  교체할 때 그 file handler에 `PrefixLevelFilter(prefix=...)`를 부착. process
  수명 동안 stateless하게 작동하므로 instance마다 다시 부착할 필요 없음.
  (multi-process worker에서 새 file handler를 만들 때도 동일.)
- controller 진입 **직전**에 SC file handler attach helper 호출:
  `attach_fh_to_logger(sc_logger_name, layout.log_path("subroutine_controller", ...))`.
  이 helper가
  - SC logger에 file handler 부착 (DEBUG level)
  - `sc_logger.setLevel(logging.DEBUG)`
  - 시작 시 defensive sweep으로 stale `_MANAGED_TAG` handler 청소
  를 처리. **`propagate`는 건드리지 않음** (Python 기본 True 유지). console에
  SC record가 보이는 건 root stream handler가 propagation으로 자동 수신하기
  때문이라 SC logger에 stream handler 별도 부착 불필요.
  controller 종료 후 `detach_fh_from_logger(sc_logger_name)`로 file handler를
  close + remove (fd 누수 방지). idempotent — child process가 worker init에서
  다시 호출해도 안전.
- `_persist_run_artifacts`의 모든 path 조립을 `layout.artifact_path(...)` 호출로
  교체. zone routing은 layout 내부에서 처리되므로 호출 측은 zone을 신경쓰지
  않는다. 변경 사항:
  - **final zone (instance_dir 바로 아래)**:
    - `layout.artifact_path("instance_result_manifest", scenario_name=...,
      instance_name=...)`
    - `layout.artifact_path("solution_json", ...)`
    - `layout.artifact_path("obj_log", ...)`
  - **progress zone (instance_dir/progress/)**:
    - `layout.artifact_path("mcf_lb_diagnostic", ...)`
    - `layout.artifact_path("mcf_lb_phase_schedule", ..., phase_name=name)`
    - `layout.artifact_path("last_stage_only_schedule", ...)`
  - **삭제**:
    - `dump_schedule_yaml(incumbent.schedule, working_dir / f"{ins}_schedule.yaml", ...)`
      호출 자체를 제거. `_solution.json`이 `operations` 필드에 같은 정보를 갖고
      있으므로 SSOT 위반.
  - **`_save_statistics` 처리**: § 7 의 SSOT 정리 옵션 결정 후 적용.

### 5.6 `FFcDDWSubroutineController` (controller_core)

- `self.logger`는 이미 `f"ffc_ddw_sum_et.orchestration.controller.{ins_name}"`
  named logger다 (controller_core.py:49). 이 logger 이름이 곧
  `PrefixLevelFilter`의 prefix 매칭 대상. SingleInstanceRunner가 controller
  진입 직전에 이 logger에 file handler를 attach하고 propagate=True 그대로 둠
  (§ 4.4.2 / § 5.5).
- `set_working_dir` 호출자를 그대로 두되, `set_artifact_layout(...)` 도입 후
  `try_get_file_path_for_subroutine`이 layout 기반으로 동작하도록 옮긴다
  (`progress/` zone에 step_log 떨어뜨리도록).
- **algorithm 호출 지점마다 `self.logger`를 명시 전달**한다. 기존에 algorithm
  쪽에서 `logging.getLogger(__name__)`을 inline으로 만들고 있는 자리들을
  `logger: Logger | None = None` 파라미터로 받도록 마이그레이션. 이 저장소
  현재 상태에서:
  - **이미 패턴을 따르는 자리** (참고용 precedent):
    - `src/ffc_ddw_sum_et/algorithm/dispatcher/base.py:40` —
      `self.logger = logger or logging.getLogger(__name__)`
    - `src/ffc_ddw_sum_et/algorithm/neh_cp/dispatcher.py:52` —
      `logger = spec.logger or logging.getLogger(__name__)` (이미 `AlgSpec.logger`
      contract 활용 중, Rule 4 준수)
  - **마이그레이션 필요한 자리**:
    - `src/ffc_ddw_sum_et/algorithm/cumulative_routine.py:114` —
      `logger = logging.getLogger(__name__)` (함수 내부 inline 생성)
    - `src/ffc_ddw_sum_et/algorithm/cumulative_routine.py:284` — 동일
    - 기타 `getLogger(__name__)`이 호출 시점에 인자 없이 만들어지는 자리들은
      `Grep "getLogger" src/ffc_ddw_sum_et/algorithm/`로 확인하고 한 번에 정리.
- 결과적으로 `FFcDDWSubroutineController.run_*` (e.g. `run_neh_cp`, `run_mcf_lb`,
  `run_fam`)의 algorithm 호출이 모두 `..., logger=self.logger`를 명시적으로
  넘기는 형태가 된다. algorithm은 SC logger 객체를 그대로 들고 쓰므로 emit된
  record의 `record.name`이 SC namespace로 시작 → `PrefixLevelFilter`가
  SIR.log로 흘러가는 INFO/DEBUG는 drop, WARNING/ERROR는 통과시킴. SC.log에는
  SC logger 자체 file handler가 받으므로 full fidelity.

### 5.7 `FFcDDWReporter` / POST_PROCESS_ONLY

- POST_PROCESS_ONLY에서 `_resolve_post_process_dir` 다음에:
  - 해당 디렉터리에 `<run_id>_artifact_layout.yaml`이 stamp되어 있으면 그걸로
    layout 복원, 없으면 default schema로 복원.
- `Reporter.generate_*_filename`, `_write_summary_csv`, `_write_excel_report`,
  `_write_mcf_lb_*` 모두 path 조립을 `layout.artifact_path(...)` 호출로 교체.
- **gantt PNG는 `report/` zone으로**:
  - `_render_gantt_from_yaml`, `_render_preemptive_gantt_from_yaml`은 출력 PNG
    경로를 입력 yaml의 `with_name(...)` 으로 자체 derive하고 있다 (reporting.py
    102, 158). 이걸 layout 호출로 바꿔서 입력 yaml은 `progress/`에서, 출력 PNG는
    `report/`에 가도록 한다:
    - 입력: `layout.find_artifacts("mcf_lb_phase_schedule", ...)`,
      `layout.find_artifacts("last_stage_only_schedule", ...)`,
      그리고 `final` zone의 solution_json (gantt 그릴 source가 schedule.yaml에서
      solution.json으로 옮겨짐).
    - 출력: `layout.artifact_path("gantt_png" | "phase_gantt_png" |
      "preemptive_gantt_png", ..., phase_name=...)`.
  - 현재 `_generate_gantt_charts`의 `rglob("*_schedule.yaml")` 자유 탐색은
    `layout.find_artifacts(kind=...)` 호출 묶음으로 대체.
- `Reporter`도 layout을 받도록 생성자 시그니처에 `layout` 추가.

### 5.8 docs / AGENTS.md

- `AGENTS.md`의 "Architecture Docs" 섹션에 본 문서 링크 추가:
  `docs/io/20260429_artifact_manager.md`.
- `docs/io-principles.md`의 Rule 1 (io는 low-level layer) 규약을 위반하지 않는지
  확인 — `ArtifactLayout`은 routix에 들어가므로 ffc의 io 서브트리는 영향 없음.
  ffc overlay yaml은 `metadata/`에 두므로 io subtree와 분리.

## 6. 마이그레이션 순서 (제안)

1. routix에 `ArtifactLayout` + 기본 schema yaml 추가, 단 routix 자체 runner는
   layout=None 기본값으로 backwards-compatible 유지.
2. routix `init_run_root` 함수 추가 (기존 `init_timestamped_working_dir`는 그대로
   둠).
3. ffc_ddw_sum_et의 `main.py`만 layout을 만들어 stamp + main.log 경로에만 사용
   (runner 쪽은 아직 그대로). 가장 작은 PR.
4. `MultiScenarioRunner`/`MultiInstanceRunner` 쪽 log path를 layout으로 이전.
5. `SingleInstanceRunner._persist_run_artifacts`의 artifact path 조립을 layout
   호출로 이전. 이 단계가 가장 큼 — 한 번에 하나의 kind씩 옮기면 diff 검토 용이.
6. `Reporter`의 path 조립을 layout으로 이전 + POST_PROCESS_ONLY에서 stamp된
   yaml을 우선 로드.
7. `SubroutineController.set_artifact_layout` 도입 후
   `try_get_file_path_for_subroutine`을 layout 기반으로 단순화.
8. routix에 `LogScope` 같은 logging helper를 끌어올리는 건 가장 마지막
   (선택사항).

## 7. 위험 / 미해결 질문

### 7.1 `_statistics.{yaml,json}` SSOT 정리 → **option A 확정**

`_save_statistics`는 `SubroutineReportStatistics`를 통해 `_statistics.yaml`,
`_statistics.json` 두 파일을 떨군다 (single_instance_runner.py:398). 거기 들어가
는 정보는:

- `method_call_counts` → `_instance_result.yaml`의 `method_call_counts`와 동일.
- per-step `obj_value` / `elapsed_time` → `_obj_log.yaml`과 동일.
- `improvementRatio` 같은 derived 값 → manifest의 `first_obj_value` /
  `obj_value` 에서 1줄로 재계산 가능.

즉 **manifest + obj_log = statistics**, SSOT 위반.

**결정: A — 삭제**. `_save_statistics` / `_statistics.{yaml,json}` 산출 자체를
제거. downstream 분석이 `improvementRatio`류 derived metric을 자주 본다면, 그건
reporter의 cross-instance 요약 단계에서 한 번에 계산해 `report/` 또는 scenario
디렉터리로 떨구는 것이 SSOT 측면에서 일관됨 (instance final zone에는 anything-
derived가 들어가지 않음).

### 7.2 SC handler 누적 문제 — 안전한 attach/detach 패턴

**과거 사용자 경험**: instance A 실행 중 예외 발생 → logger 정리 누락 → 이후
instance B/C/... 의 record가 A의 log 파일에 계속 누적되거나, 중복 record가
발생.

이번 디자인은 logger 이름이 instance마다 다르므로(`controller.<ins_name>`)
record cross-contamination 자체는 자동 방지되지만, 같은 process가 instance를
순차 처리하는 (single-worker / fail-then-retry) 시나리오에서는 stale handler가
fd로 남아 누적될 수 있음. 이를 막기 위해 다음 4-단 방어:

1. **defensive sweep on attach** — attach 함수 진입 시 그 logger에 이미 붙어
   있는 `_MANAGED_TAG` handler를 모두 close + remove한 다음 새로 부착.
   이전 run이 detach를 못 했어도 이번 run 시작에서 청소됨.
2. **try/finally on caller** — `SingleInstanceRunner.run`의 try/finally 블록
   안에서 attach → controller.run → detach. 예외 경로도 detach가 무조건 실행됨.
3. **idempotent detach** — detach가 호출되지 않았거나 두 번 호출돼도 안전.
   handler가 없으면 no-op.
4. **logger 이름 per-instance** — `controller.<ins_name>` 으로 이미 분리되어
   있어, 1·2·3이 모두 실패하더라도 다른 instance의 record가 잘못된 파일로 가는
   사고는 발생하지 않음 (최악의 경우는 fd 누수만).

helper 시그니처 (`routix.logging` 실제 구현):

```python
# routix/src/routix/logging.py

_MANAGED_TAG = "_routix_managed"

_DEFAULT_FILE_FMT = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)


class PrefixLevelFilter(logging.Filter):
    """주어진 prefix로 시작하는 record는 WARNING 이상만 통과; INFO/DEBUG는 drop."""

    def __init__(self, prefix: str) -> None:
        super().__init__()
        self._prefix = prefix

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name.startswith(self._prefix):
            return record.levelno >= logging.WARNING
        return True


def attach_fh_to_logger(logger_name: str, file_path: Path) -> logging.Logger:
    """logger에 DEBUG-level file handler 부착. propagate는 그대로 (Python 기본 True)."""
    log = logging.getLogger(logger_name)
    _sweep(log)                                     # (1) defensive sweep
    log.setLevel(logging.DEBUG)
    fh = logging.FileHandler(file_path, mode="a", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(_DEFAULT_FILE_FMT)
    setattr(fh, _MANAGED_TAG, True)
    log.addHandler(fh)
    return log


def detach_fh_from_logger(logger_name: str) -> None:
    """attach_fh_to_logger가 부착한 handler들을 close+remove. idempotent."""
    _sweep(logging.getLogger(logger_name))           # (3) idempotent


def _sweep(log: logging.Logger) -> None:
    for h in list(log.handlers):
        if getattr(h, _MANAGED_TAG, False):
            try:
                h.close()
            finally:
                log.removeHandler(h)
```

caller pattern in `FFcDDWSingleInstanceRunner.run`:

```python
from routix.logging import attach_fh_to_logger, detach_fh_from_logger

sc_logger_name = f"ffc_ddw_sum_et.orchestration.controller.{self.ins_name}"
try:
    attach_fh_to_logger(
        sc_logger_name,
        layout.log_path("subroutine_controller", ...),
    )
    self.ctrlr = self.get_controller()
    self.ctrlr.run()
finally:
    detach_fh_from_logger(sc_logger_name)
```

`PrefixLevelFilter`는 SIR file handler에 한 번만 부착하면 되며 (process 수명
동안 stateless), instance 단위로 attach/detach할 필요 없음. console stream
handler에는 부착하지 않으므로 SC INFO/DEBUG도 console에는 -q/-v level에 따라
보임.

**테스트로 검증할 시나리오** (`tests/test_logging.py` 에 구현됨):

- `controller.run`이 정상 종료 → attached file handler가 detach로 사라지는가.
- `controller.run`이 예외로 종료 → finally의 detach가 실행되는가, fd가 닫히는가.
- 같은 process에서 instance A → 예외 → instance B를 새 attach 호출 → A의 handler
  잔재가 남지 않는가 (defensive sweep 작동 확인).
- 같은 process에서 instance A → 정상 → instance B → A의 SC.log에 B의 record가
  들어가지 않는가 (logger 이름 분리 작동 확인).
- SC logger에서 INFO 발행 → SC.log에 기록, SIR.log에 미기록.
- SC logger에서 ERROR 발행 → SC.log + SIR.log 모두 기록 (instance failure
  surface).
- `PrefixLevelFilter` prefix 안 맞는 다른 logger의 INFO → SIR.log에 기록
  (filter는 매칭 prefix에만 적용).

**Coupling 주의**: `PrefixLevelFilter`의 prefix는 SC logger 이름 컨벤션
(`ffc_ddw_sum_et.orchestration.controller`) 과 묶여 있다. routix는 특정
namespace를 hardcode하지 않고 `prefix: str` 파라미터로 caller가 주입하도록 둠.
ffc는 자기 SC logger 이름의 namespace를 파일 handler 셋업 자리에서 받아
`PrefixLevelFilter(prefix=...)`로 SIR file handler에 부착.

### 7.3 scenario name 중복 검사 (실험 overwrite 방지)

**과거 사용자 경험**: scenario name이 중복된 채로 config가 들어와서, 두 scenario가
같은 `output_subdir` (또는 dir 결정 로직 자체가 동일) 을 공유 → 두 번째 scenario
가 첫 번째 결과를 silently overwrite. 디스크에는 늦게 끝난 scenario만 남고, 사용
자는 한참 뒤에야 발견.

**대책**: scenario 이름과 그로부터 도출되는 디렉터리 둘 다에 대해 **fail-fast
duplicate check**. 두 군데에서 검증해 한쪽이 무너져도 다른 쪽이 잡도록:

1. **main.py / config 로딩 직후** (가장 빠른 실패 지점):
   - `[sc.get("name") for sc in config["scenarios"]]` 의 길이와 set 크기 비교.
   - `[sc.get("output_subdir") for sc in config["scenarios"]]` 도 동일.
   - 중복 발견 시 `ValueError`로 즉시 raise. 메시지에 어느 scenario들이 충돌인지
     모두 나열.
2. **`ArtifactLayout.scenario_dir(name)`** 첫 호출 시:
   - layout이 register된 scenario name set을 들고 있다가, 같은 name이 다시
     들어오면 `ValueError`.
   - 1단계가 실패해도 (e.g. config가 다른 경로로 로딩됨, scenario_configs가
     코드에서 직접 만들어짐) 여기서 잡힘.

config 단계 검증 (예시):

```python
# main.py 내부
def _validate_scenario_uniqueness(scenarios: list[dict]) -> None:
    seen_names: dict[str, list[int]] = {}
    seen_subdirs: dict[str, list[int]] = {}
    for i, sc in enumerate(scenarios):
        seen_names.setdefault(sc.get("name", f"scenario_{i+1}"), []).append(i)
        seen_subdirs.setdefault(sc.get("output_subdir") or sc.get("name"), []).append(i)
    dups = [(k, v) for k, v in seen_names.items() if len(v) > 1] + \
           [(k, v) for k, v in seen_subdirs.items() if len(v) > 1]
    if dups:
        details = "; ".join(f"'{k}' at indices {v}" for k, v in dups)
        raise ValueError(
            f"duplicate scenario name/output_subdir found: {details}. "
            "Each scenario must have a unique name AND unique output_subdir to "
            "prevent silent overwrite of experiment results."
        )
```

이 검증은 **config가 어떤 경로로 들어오든** 한 번은 거치도록 main.py 진입부에
필수 호출. POST_PROCESS_ONLY 모드에서도 동일 (이미 디스크에 떨궈진 디렉터리를
열 뿐이지만, 어떤 scenario를 "어느 scenario name으로" 처리할지 결정 단계에서
중복이 있으면 동일 문제 발생).

### 7.4 그 외 위험

- **Backwards compatibility**: **무시한다**. 사용자 결정. 기존 (pre-zone, pre-
  layout) 디렉터리는 본 layout으로 해석하지 않음. POST_PROCESS_ONLY는 신규 run에
  대해서만 보장. 기존 디렉터리는 별도 일회성 분석 스크립트로 처리.
- **multiprocessing (layout)**: ProcessPoolExecutor worker는 layout 객체를
  pickle해 받게 된다. layout이 yaml 경로 + dict만 들고 있으면 picklable. logger
  handler attach는 worker init에서 layout으로부터 path를 다시 도출하는 식으로
  처리.
- **layout vs subroutine context name**:
  `SubroutineController.get_file_path_for_subroutine`은 method context stack
  (`reps.0.run_mcf_lb` 같은 prefix)을 파일명에 박는다. 이건 instance scope
  artifact 안에서의 sub-naming이라 layout과 직교. layout은 디렉터리만 결정하고
  이 helper는 파일명만 결정하도록 분리 유지.
- **scope 간 경계 지점**: scenario log와 instance log를 동시에 쓰고 싶을 때 root
  logger의 핸들러 chain이 문제 없는지 확인 필요. 현재 `_MANAGED_TAG`로 자기
  관리 핸들러만 교체하므로 multi-handler attach 자체는 가능.
- **명시적 logger 전달 마이그레이션의 범위**: § 5.6 의 list 외에도
  `algorithm/` 안에 `logging.getLogger(__name__)`을 함수 내부에서 만들어 쓰는
  자리가 더 있을 수 있다. PR을 쪼갤 때, "controller가 logger를 명시 전달하도록
  바꾸는 변경"과 "각 algorithm 함수/클래스의 시그니처를 `logger | None`으로 받는
  변경"을 한 PR에 같이 묶어야 컴파일/타입 체크가 깨끗하다. `AlgSpec`을 이미 받는
  algorithm은 `spec.logger`로 전달하면 되므로 (Rule 6) 시그니처 변경이 불필요.
- **kind 추가의 거버넌스**: ffc overlay에서 같은 kind를 다시 정의하면 충돌. routix
  쪽 schema에 `kind` 네임스페이스 (`routix.*`, `ffc.*`)를 두는 안 검토.

## 8. 작업 단위 요약

### routix

- [x] `routix/src/routix/io/_default_artifact_layout.yaml` 신설
      (final/progress/report zone 정의 포함)
- [x] `routix/src/routix/io/artifact_layout.py` 신설 (`ArtifactLayout` base
      class + `zone_dir`, `register_kind` API)
- [x] `routix/src/routix/io/path.py`에 `init_run_root` 추가
- [x] `MultiScenarioRunner`, `MultiInstanceRunner`, `MultiInstanceConcurrentRunner`,
      `SingleInstanceRunner`에 `layout` 파라미터 추가 (default None)
- [x] `SubroutineController.set_artifact_layout` 추가
- [x] `routix.logging` 모듈 추가
      - `attach_fh_to_logger(logger_name, file_path)` /
        `detach_fh_from_logger(logger_name)` (file handler attach/detach +
        `_MANAGED_TAG` defensive sweep, propagate는 건드리지 않음)
      - `PrefixLevelFilter(prefix)` — `record.name`이 prefix로 시작하는
        record 중 INFO/DEBUG는 drop, WARNING+는 통과시키는 `logging.Filter`
      - `tests/test_logging.py` — § 7.2 시나리오 7개 + 단위 5개 = 12 테스트
- [x] `tests/io/test_artifact_layout.py` — zone routing, subclass register,
      stamp/restore round-trip, **zone validation** (scope=instance에 zone 누락
      → ValueError / scope=run·scenario에 zone 지정 → ValueError / scenario_dir
      중복 등록 → ValueError)
- [x] `subroutine_flow_data.md`, `runner/README.md`에 layout 언급

### ffc_ddw_sum_et

- [ ] `metadata/artifact_layout/ffc_ddw_sum_et_v1.yaml` 신설
      (mcf_lb_*, gantt_png 등 ffc 고유 kind을 zone과 함께 선언)
- [ ] (선택) `FFcArtifactLayout(ArtifactLayout)` subclass — yaml로 표현하기
      어려운 dynamic 분기가 있을 때만
- [ ] `main.py`: `init_run_root` + `ArtifactLayout` 도입, layout.stamp()
- [ ] **`main.py`: scenario name + output_subdir 중복 검사** (§ 7.3).
      `_validate_scenario_uniqueness` 호출 후에야 layout / runner 생성으로 진행.
- [ ] `FFcDDWMultiScenarioRunner._benchmark_log_path` 제거 → layout 호출
- [ ] `FFcDDWMultiInstanceRunner`: layout forward 및 자기 log path를 layout에 위임
- [ ] `FFcDDWSingleInstanceRunner._persist_run_artifacts` 정리:
      - `dump_schedule_yaml(... f"{ins}_schedule.yaml")` 호출 삭제
      - phase schedule / mcf_lb_diagnostic / last_stage_only → progress zone
      - **`_save_statistics` 호출 자체를 제거** (§ 7.1 option A 확정).
        `_statistics.{yaml,json}` 산출도 함께 제거. cross-instance derived
        metric은 reporter의 cross-instance 요약 단계에서 한 번만 계산.
      - working_dir 자체 결정 로직 제거 → `layout.instance_dir(...)`
- [ ] `FFcDDWSingleInstanceRunner.run` SC log lifecycle (§ 7.2):
      - SIR file handler에 `PrefixLevelFilter(prefix=...)` 부착 (process 수명
        동안 한 번)
      - controller 진입 직전 `attach_fh_to_logger(sc_logger_name, ...)`
        (file handler만, stream handler 부착 없음 — root stream handler가
        propagation으로 자동 수신)
      - `try/finally`로 `detach_fh_from_logger(sc_logger_name)` 보장
      - attach helper 안에 defensive sweep (`_MANAGED_TAG` 청소)
- [ ] `FFcDDWSubroutineController`: layout 등록 + algorithm 호출 지점에
      `logger=self.logger` 명시 전달 (algorithm-principles Rule 4·6·8 적용)
- [ ] algorithm 측 `logger | None` 시그니처 마이그레이션:
      `algorithm/cumulative_routine.py` 외 `Grep getLogger src/ffc_ddw_sum_et/algorithm/`
      결과 자리들을 한 PR로 정리
- [ ] `FFcDDWReporter`: 모든 path 조립을 layout 기반으로 + gantt 출력을
      `report/` zone으로 라우팅 + POST_PROCESS_ONLY에서 stamp yaml 우선 로드
- [ ] tests:
      - `tests/test_artifact_layout_overlay.py`
      - `tests/test_scenario_uniqueness.py` (§ 7.3)
      - `tests/test_sc_log_lifecycle.py` (§ 7.2 검증: 정상 detach,
        예외 detach, 누적 sweep, instance 격리)
      - 기존 logging / manifest 테스트 갱신, `_save_statistics` 관련 테스트 삭제
- [ ] `AGENTS.md` Architecture Docs 섹션 업데이트
