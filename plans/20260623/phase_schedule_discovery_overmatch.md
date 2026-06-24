# Plan: phase-schedule discovery 과대매칭(over-match) 수정

상태: **완료 (2026-06-24)**

## Context

`FFcDDWReporter._generate_gantt_charts`
(`src/ffc_ddw_sum_et/orchestration/reporting.py`)는 인스턴스별 phase Gantt를
그리기 위해 `self.layout.find_artifacts("mcf_lb_phase_schedule", ...)`로
on-disk JSON을 탐색한다.

`routix.io.ArtifactLayout.find_artifacts`는 kind의 `file_template`에서 채워지지
않은 free placeholder를 `*`로 치환한 뒤 zone 디렉터리에서 glob한다. 문제는
`mcf_lb_phase_schedule`와 `flip_makespan_cp_phase_schedule` 두 kind가 모두
같은 `progress/` zone에서 flat `{phase_name}.json` 템플릿을 썼다는 점이다.

- `mcf_lb_phase_schedule` → glob `progress/*.json`
- `flip_makespan_cp_phase_schedule` → glob `progress/*.json`

즉 각 kind의 glob이 `progress/` zone의 **모든 flat JSON**을 매칭한다. 서로
상대 kind의 파일은 물론, flat인 `csr_cp_trajectory_json`까지 빨아들인다.

### 증상

- MCF-LB 탐색 루프가 외부(flip / trajectory) JSON을 끌어와 MCF **공유 horizon**
  위에 다시 렌더링한다.
- 구체적 사례(2026-06-24): `coarsen_solve_reconstruct`의 3개 phase Gantt가 MCF
  공유 horizon으로 잘못 재렌더링되어, coarsened 시간 축(makespan ~34)의
  `1_coarse_solver_result`가 원본 스케일 축(~1652)에 그려져 나머지 둘과 시각적으로
  구분되지 않았다.
- CSR은 이전에 `coarsen_solve_reconstruct/` 하위 디렉터리로 이미 격리되어 있어
  당시엔 우회되어 있었다. flip은 여전히 flat이라 latent 상태였고, flip과 MCF-LB
  phase가 horizon 충돌과 함께 동시에 발생하는 경우가 드물어 표면화되지 않았을 뿐
  이다.

## 결정한 수정 방향

검토한 대안:

1. **(채택)** 각 flat phase-schedule kind를 자기만의 하위 디렉터리로 네임스페이싱.
   기존 `csr_phase_schedule` / `calc_mcf_lb_phase_schedule` 네스팅 선례와 동일.
2. `find_artifacts`를 kind별 emit 경로 추적 방식으로 바꾸기 → routix(외부 lib)
   변경 필요, 과함.
3. 리포터에서 glob 결과를 `controller.mcf_lb_phase_schedules`로 필터 → 리포터는
   `POST_PROCESS_ONLY`에서 live controller 없이 디스크만으로 동작해야 하므로
   불가.

→ **(1)** 채택. 템플릿이 write(`artifact_path`)와 read(`find_artifacts`) 양쪽의
single source of truth이므로, YAML 템플릿만 바꾸면 양쪽이 함께 이동한다.

## 변경 내역

### 1. `metadata/artifact_layout/ffc_ddw_sum_et_v1.yaml`

flat phase-schedule kind를 각자 하위 디렉터리로 네스팅:

| kind | 변경 전 | 변경 후 |
|---|---|---|
| `mcf_lb_phase_schedule` | `{phase_name}.json` | `mcf_lb_phase_schedule/{phase_name}.json` |
| `flip_makespan_cp_phase_schedule` | `{phase_name}.json` | `flip_makespan_cp/{phase_name}.json` |

각 kind의 glob이 `progress/<subdir>/*.json`로 좁혀져 서로/타 kind와 교차 매칭
불가.

### 2. `src/ffc_ddw_sum_et/orchestration/reporting.py`

로직 변경 없음. 탐색된 `phase_name`은 여전히 파일 stem(`phase_json.stem`)이고,
출력 PNG(`phase_gantt_png`) 경로도 영향 없음. 낡은 `progress/*.json` 주석만
갱신.

### 3. 테스트 (`tests/orchestration/test_artifact_layout_overlay.py`)

- `test_ffc_progress_kinds_routed_to_progress_zone`: 기존엔
  `phase.parent.name == "progress"`를 단언했으나 네스팅으로 한 단계 깊어졌으므로,
  phase schedule이 progress zone 한 단계 아래
  (`progress/mcf_lb_phase_schedule/`)에 있음을 단언하도록 수정.
- `test_find_mcf_lb_phase_schedule_does_not_over_match_siblings` 추가:
  mcf / flip / trajectory JSON을 모두 써 둔 뒤,
  `find_artifacts("mcf_lb_phase_schedule")`가 mcf 파일만,
  `find_artifacts("flip_makespan_cp_phase_schedule")`가 flip 파일만 돌려줌을
  확인하는 회귀 테스트.

## 검증

- `uv run ruff check` — clean.
- 전체 스위트: **379 passed**.

## 주의 / 후속

- 이 변경은 **새 run**의 on-disk layout만 바꾼다. 기존 output 디렉터리를
  `POST_PROCESS_ONLY`로 재처리할 때는 해당 run의 stamped layout(flat 템플릿)을
  flat 파일에 적용하므로 옛 run은 그대로 self-consistent하다. 마이그레이션 불필요.
- **컨벤션:** 앞으로 `progress/` zone에 추가하는 모든 phase-schedule kind는 flat이
  아니라 자기만의 하위 디렉터리로 네스팅한다.
