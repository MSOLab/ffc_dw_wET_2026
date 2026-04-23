# 변경사항 리뷰: `ec0fa4b` → `ad4a023`

- **범위:** `ec0fa4ba` (refactor(cumulative): drop dead code, tighten API) **다음** 커밋부터 `ad4a0236` (run setting) 까지
- **총 9개 커밋**, `30 files changed, 656 insertions(+), 259 deletions(-)`
- **브랜치:** `20260418_lower_bound`
- **핵심 테마**
  1. `run_mcf_lb_4` 의 Phase 2/Phase 4 파라미터를 "스코프 prefix + 역할 suffix" 규약으로 완전히 재설계
  2. 산발적으로 흩어져 있던 profile-fix precedence 옵션 두 개(`profile_fix_by_machine`, `machine_precedence_stride`) 를 단일 `PFMethod` Literal 로 통합
  3. CP-SAT 솔버 파라미터 설정을 `CpsatSolverOptions` 데이터클래스로 일원화
  4. CP-SAT 탐색 로그 파일 저장, E/T 힌트 주입 등 Phase 4 수렴 품질 향상을 위한 피처 추가

---

## 1. 커밋별 요약

| # | SHA | 제목 | 분류 |
|---|---|---|---|
| 1 | `a71bd3f` | feat(mcf-lb): split repeat flags, log CP-SAT | feat + refactor |
| 2 | `53c0af7` | refactor(main): unify start_dt, drop dup init | refactor |
| 3 | `a0bc810` | feat(cumulative): hint E/T from ref schedule | feat |
| 4 | `8e14681` | refactor(mcf-lb): unify pf params into PFMethod | refactor (**BC break**) |
| 5 | `134e6d3` | refactor(mcf-lb): rename pf params to cp_pf | refactor (rename) |
| 6 | `60356ca` | remove TODOs | chore |
| 7 | `5258f92` | feat(mcf-lb): add cp_tl and search log params | feat |
| 8 | `9d8a175` | refactor(mcf-lb): split thread_cnt per phase | refactor |
| 9 | `ad4a023` | 20260423T114900_417063 run setting | chore (config) |

---

## 2. API 변경 (`run_mcf_lb_4`)

### 2.1 시그니처 변화 (Before → After)

| Before (ec0fa4b) | After (ad4a023) |
|---|---|
| `profile_fix_by_machine: bool = False` | `last_stage_only_cp_pf_method: PFMethod \| None = None` |
| `machine_precedence_stride: int = 1` | `full_cp_pf_method: PFMethod \| None = None` |
| `solver_thread_cnt = 1` (내부 로컬) | `last_stage_only_cp_solver_thread_cnt: int = 1` |
|   | `full_cp_solver_thread_cnt: int = 1` |
| `repeat_pf_cp_while_improving: bool = False` | `repeat_last_stage_only_cp_while_improving: bool = False` |
|   | `repeat_full_cp_while_improving: bool = False` |
| — (없음) | `last_stage_only_cp_tl: float \| str \| None = None` |
| — (없음) | `full_cp_tl: float \| str \| None = None` |
| — (없음) | `log_last_stage_only_cp_search_progress: bool = False` |
| — (없음) | `log_full_cp_search_progress: bool = False` |
| `machine_then_job: bool = False` | `machine_then_job: bool = False` (변경 없음) |

**네이밍 규약:** `{phase_scope}_{target}_{attribute}` — Phase 2 는 `last_stage_only_cp_*`, Phase 4 는 `full_cp_*` 로 prefix 가 붙어 두 phase 가 완전히 독립 제어 가능.

### 2.2 `PFMethod` Literal 도입 (`src/ffc_ddw_sum_et/algorithm/cumulative.py`)

```python
PFMethod = Literal["PF0", "PF1", "PF2"]
```

- `PF0` → `(by_machine=False, stride=1)` — 기존 default 동작 (stage-level time-based 후속작업 선택)
- `PF1` → `(by_machine=True, stride=1)` — per-machine 인접 체인
- `PF2` → `(by_machine=True, stride=2)` — per-machine 격칸(every-other) 체인
- `None` → precedence-arc 패스 자체를 **생략**. Warm-start / ET 힌트만 유지

### 2.3 ⚠️ Default 거동 변경 (Behavior-Breaking)

**이전:** 두 phase 모두 `profile_fix_by_machine=False, stride=1` 이 기본값이라 PF0 precedence 제약을 항상 걸었다.
**이후:** 두 phase 모두 기본이 `None` 이라 **precedence 제약을 걸지 않는다.**

| 기존 호출 의도 | 새 config 로 복원하려면 |
|---|---|
| 기본값 그대로 호출 | `last_stage_only_cp_pf_method: "PF0"` + `full_cp_pf_method: "PF0"` |
| `profile_fix_by_machine=True` | `pf_method: "PF1"` |
| `profile_fix_by_machine=True, stride=2` | `pf_method: "PF2"` |

- `metadata/20260419/2_profile_fixed_ns_config.yaml` 등 기존 14개 config 가 이미 새 키로 마이그레이션되어 있고, `run_mcf_lb_4` 이전 호출자는 모두 `"PF1"` 로 보존됨 — **repo 내 파급은 이미 정리된 상태.**
- 외부에서 기본값에 의존하던 호출(예: `benchmarks/PRA2017/add_lb_column.py`) 은 LB 계산만 돌아가면 되므로 차이가 영향이 없음.

### 2.4 레거시 제거

- `run_mcf_lb()` 완전 삭제. 모든 호출부가 `run_mcf_lb_4()` 로 이동
  - `tests/orchestration/test_controller.py` 의 2개 호출
  - `benchmarks/PRA2017/add_lb_column.py` 의 compute_lb
- `MCFLBOption` 은 실제로 `run_mcf_lb_4` 에 연결되지 않은 채 남아 있던 pair 형 필드 2개 → `PFMethod | None` 쌍으로 동반 변경

---

## 3. 새 모듈 · 새 헬퍼

### 3.1 `src/ffc_ddw_sum_et/algorithm/cpsat_solver_options.py` (신규)

```python
@dataclass(frozen=True)
class CpsatSolverOptions:
    log_search_progress: bool | None = None
    log_to_stdout: bool | None = None
    log_to_response: bool | None = None
    max_time_in_seconds: float | None = None
    num_workers: int | None = None
    ...  # 총 13개 CP-SAT parameter 를 Optional 로 정리

def get_solver(cfg: CpsatSolverOptions) -> CpSolver:
    s = CpSolver()
    for k, v in cfg.get_dict().items():
        setattr(s.parameters, k, v)
    return s
```

- `None` 필드는 `get_dict()` 에서 걸러져 CP-SAT 기본값이 유지됨
- 기존 `CpSolver()` 직접 조작 지점 중 2곳 (`solve_last_stage_with_profile_fix`, `solve_full_cp_with_profile_fix`) 이 이 팩토리로 교체됨
- **주의:** 다른 CP-SAT 호출부 — `orchestration/controller.py` 의 `_profile_fix_from_incumbent`, `_mcf_palmer` 등 — 은 아직 `cp_model.CpSolver()` 직접 조작. 통일을 원하면 후속 리팩터 대상

### 3.2 `_resolve_cp_tl` (`controller.py` 모듈 스코프)

```python
def _resolve_cp_tl(tl_raw: float | str | None, job_count, stage_count) -> float | None
```

시간제한 설정을 인스턴스 크기에 비례시킬 수 있도록 문자열 DSL 추가:

| Input | Resolved |
|---|---|
| `None` | `None` (제한 없음) |
| `float` / `int` | 그대로 초 단위 |
| `"Xnc"` (X 는 숫자) | `X * job_count * stage_count` 초 |
| 그 외 str | `float(s)`; 실패 시 `ValueError` |

YAML 예시 (`metadata/20260422/1_mcf_lb_init_12_config.yaml`):

```yaml
last_stage_only_cp_tl: "0.03nc"
full_cp_tl: "0.03nc"
```

**평가:** `n * c` 기반 스케일링은 PRA2017 large 인스턴스처럼 크기가 다양한 벤치마크에 실용적. `"nc"` suffix 가 유일한 magic string 이라 docstring 에 명시되어 있음 — ✅

### 3.3 `BaseModelBuilder.apply_et_hints_from_ref_schedule` (`cumulative.py`)

```python
E_val = max(0, params.d_lower[j] - C_j)
T_val = max(0, C_j - params.d_upper[j])
mdl.add_hint(et_vars.E[j], E_val)
mdl.add_hint(et_vars.T[j], T_val)
```

- 참조 스케줄의 마지막 스테이지 완료시각 `C_j` 로부터 E/T 값을 직접 계산해 힌트 주입
- `apply_hints_from_schedule` (start/end/E/T 를 한 번에 호출) 편의 함수 추가
- 적용 지점: `solve_last_stage_with_profile_fix`, `solve_full_cp_with_profile_fix`, `controller._mcf_palmer`, `controller._profile_fix_from_incumbent`

**평가:** start/end 힌트만 있던 기존 상태에서 목적함수 변수까지 hint 로 제공 → CP-SAT 초기 해가 더 빨리 발견될 여지가 큼. 하이라이트 중 하나.

### 3.4 CP-SAT 탐색 로그 파일 저장

Phase 2/4 각 solve 의 `response_proto.solve_log` 를 `log_search_progress=True` 일 때 `<subroutine>_cp_sat_mcf_lb_phase{2,4}[_{loop_index}].log` 파일로 저장. `repeat_*_while_improving=True` 이면 iteration 별로 suffix `_{loop_index}` 부여.

- `solver_log_path_getter: Callable[[str], Path]` 주입 — `controller.get_file_path_for_subroutine` 을 바로 전달
- 쓰기 실패는 `logger.warning(...)` 으로만 처리해서 solve 흐름 차단 X
- `log_to_stdout=False`, `log_to_response=True` 로 stdout 오염 방지

---

## 4. 기타 변경

### 4.1 `main.py` — `start_dt` 일원화 (`53c0af7`)

- `main_start_dt = datetime.now()` 를 진입 직후 단 1회 캡처
- 이후 로그와 `output_metadata["start_dt"]` 에 동일 값 공유
- `base_output_dir = ...; if POST_PROCESS_ONLY: ... else: ... shutil.copy2(CONFIG_PATH, ...)` 블록이 아래쪽에 중복되어 있던 것 제거 (같은 로직이 `_load_config` 후 이미 실행됨)

**평가:** 이전엔 두 `datetime.now()` 호출이 수 밀리초 차이가 났고, 출력 디렉토리 결정이 두 번 일어나 단명의 동작 차이가 있을 수 있었음. 단일 소스화는 올바른 방향. ✅

### 4.2 TODO 제거 (`60356ca`, `a71bd3f`)

- `controller.run_mcf_lb` 의 `# TODO: remove; use run_mcf_lb_4 instead` → 실제 함수 삭제로 해결됨
- `solution/objectives.py`: `# TODO: put "weighted" in the name` → 함수 이름이 이미 `compute_weighted_earliness_tardiness` 였음, stale TODO 제거
- `tests/reference_impl/schedule_lite.py` 의 Palmer/Gupta 중복 TODO 2건 → `hybridflowshop/` 경로가 리포에 없어 참조 불가한 stale TODO

### 4.3 `fam.py`, `dispatcher/mixed.py` — 포매팅만

`compute_weighted_earliness_tardiness(...)` 호출이 한 줄에서 여러 줄로 wrapping. 로직 변경 없음. `ruff format` 의 결과로 보임.

### 4.4 `ad4a023` — 실험 설정 전환

- `main.py::CONFIG_PATH`: `metadata/20260421/1_mcf_lb_init_11_config.yaml` → `metadata/20260423/1_mcf_lb_init_13_config.yaml`
- `config 13`: 단일 시나리오 `mcf_lb_4_4cores_no_pf` — `last_stage_only_cp_pf_method: null` + `full_cp_pf_method: "PF1"`, `last_stage_only_cp_tl: "0.01nc"`, Phase 4 는 `repeat_full_cp_while_improving: true`, `instance_worker_cnt: 24`

---

## 5. 아키텍처 관점 체크

### 5.1 `docs/algorithm-principles.md` 대비

- ✅ `PFMethod` 와 `CpsatSolverOptions` 모두 `algorithm/` 서브트리 안에 머무름
- ✅ `run_mcf_lb_4` 는 여전히 `orchestration/controller.py` 의 subroutine 메서드 — Launcher/Reporter 관심사를 알고리즘 내부로 침투시키지 않음
- ⚠️ `controller.py` 의 `_resolve_cp_tl` 이 모듈 레벨 free function 으로 내려가 있음. controller 내부 헬퍼로만 쓰이므로 현재로선 적절. YAML 쪽에서 별도로 해석해야 할 일이 생기면 `algorithm/` 공통 모듈로 승격 고려

### 5.2 단일 소스 원칙 (SSOT)

- ✅ `PFMethod` / `decode_pf_method` 가 추가되면서 `(profile_fix_by_machine, machine_precedence_stride)` 두 필드를 묶어 쓰는 6개 호출 지점 전부가 "PF0/PF1/PF2 문자열 → 튜플" 변환을 단 1곳에서 한다
- ✅ CP-SAT 파라미터 설정 역시 `CpsatSolverOptions` 로 2곳이 일원화됨 (단, 전체 repo 의 CpSolver 사용처가 아직 4군데 이상 남아 있어 **부분 SSOT**)

### 5.3 SRP / OCP

- `solve_last_stage_with_profile_fix` / `solve_full_cp_with_profile_fix` 가
  "**빌드 → 제약 추가 → 힌트 → solve → 로그 저장 → 갱신 루프**"
  를 모두 떠안고 있어 약 140줄. 현재는 감당 가능한 수준이지만, Phase 2/4 간 중복 코드 양이 적지 않음 (로그 suffix 만 다름). 후속 리팩터 시 "공통 CP 루틴 + phase-specific adapter" 패턴 고려 가치

---

## 6. 테스트 & 하위 호환

| 항목 | 상태 |
|---|---|
| `tests/orchestration/test_controller.py` | `run_mcf_lb()` → `run_mcf_lb_4()` 로 수정됨. 다른 assertion 무변경 ✅ |
| `benchmarks/PRA2017/add_lb_column.py` | `run_mcf_lb_4()` 로 전환 + 주석 업데이트 ✅ |
| repo 내부의 `profile_fix_by_machine` 참조 | `rg` 기준 0건 잔존 (config/코드 모두 정리됨) ✅ |
| 외부(리포 밖) 캡처된 config | 소유자가 직접 마이그레이션 필요 — `repeat_pf_cp_while_improving`, `solver_thread_cnt`, `profile_fix_by_machine`, `machine_precedence_stride` 를 보유한 YAML 은 업데이트 대상 |

---

## 7. 리스크 · 권고

1. **Default 행동 변화** (`PFMethod` 기본 `None`): 리포 안은 깔끔하게 마이그레이션 됐지만 CHANGELOG 성격의 noted change 가 있는 편이 안전. 이 리뷰 문서 자체를 근거로 PR 설명/릴리즈 노트에 반영 권장.
2. **CP-SAT 로그 누적 쓰기(`.log.open("a")`)**: `repeat_while_improving` 루프에서 iteration 별 suffix 를 분리하므로 `"a"` 지만 실제로는 파일당 1회 기록. 다만 동일 subroutine 이 두 번 호출될 경우 append 로 축적됨 — 이것이 의도라면 OK, 아니면 `"w"` 로 명시하는 편이 놀람 최소.
3. **`MCFLBOption` 미사용 상태**: `run_mcf_lb_4` 가 individual kwargs 를 직접 받도록 옮겨 가면서 이 option dataclass 는 실질적으로 dead weight 에 가까움. `docs/TODO.md` 에 "추후 `AlgOption` 기반 실행 컨트랙트로 통합 시 재정비" 로 남기면 의도가 드러남.
4. **`plans/20260421/logging-overhaul.md`** 가 untracked 로 남아 있음 — 본 변경과 관련 있으면 커밋에 포함, 없으면 `.gitignore` 또는 삭제 판단 필요.

---

## 8. 결론

이번 9개 커밋은 "**`run_mcf_lb_4` 의 파라미터 스키마를 Phase 2/Phase 4 대칭 형태로 정형화**" 라는 일관된 방향으로 묶여 있다.

- 단일 옵션 pair (`profile_fix_by_machine` + `stride`) → `PFMethod` Literal
- 단일 `solver_thread_cnt` / `repeat_pf_cp_*` → Phase 별 쌍으로 분리
- 새 feature (`cp_tl`, `log_*_search_progress`, E/T hint) 이 같은 규약으로 추가

네이밍 일관성, 설정 마이그레이션 완료도, SSOT 적용 모두 양호. **유일하게 주의할 점은 `PFMethod` 기본값이 `None` 으로 바뀐 default 거동** 으로, 리포 외부에서 이 API 를 직접 부르는 코드가 있다면 대응이 필요하다.

— 작성일: 2026-04-23
