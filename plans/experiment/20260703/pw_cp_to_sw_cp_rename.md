# `pw_cp` → `sw_cp` 이름 통일 (ffc_dw_wET_2026)

- **날짜**: 2026-07-03
- **브랜치**: `20260703_partial_cp_lexico`
- **상태**: ✅ 완료 (본 문서는 작업 후 회고 정리 — 순서상 코드가 먼저, 계획서가 나중)
- **동기**: 논문에서 이 알고리즘을 **sliding-window CP (`sw_cp`)** 로 부른다. 코드/식별자를
  거기에 맞춰 통일. 예전 이름 `pw_cp`(partial-window CP)는 결과·문서 등 일부에만 의도적으로 남긴다.

관련 형제 저장소 동일 작업 계획:
`~/code/hybridflowshop/plans/experiment/20260703/pw_cp_to_sw_cp_rename.md`,
`~/code/flowshop-tardiness/plans/experiment/20260703_pw_cp_to_sw_cp_rename.md`.

---

## 1. 배경 — 착수 시점 상태

사용자가 먼저 파일 **내용(content)** 안의 리터럴 `pw_cp` → `sw_cp` 를 일괄 치환해 39개 파일을
git stage 한 상태였다. 하지만:

- **파일/디렉토리 이름은 안 바뀜** → `src/.../algorithm/pw_cp/`, `tests/algorithm/pw_cp/` 그대로.
- **CamelCase 클래스명은 안 바뀜** → 리터럴 `pw_cp`만 치환됐으므로 `PwCpDispatcher` 등은 그대로.
- 그 결과 `controller.py`/테스트가 `from ...algorithm.sw_cp import ...` 로 바뀌었지만 디렉토리는
  `pw_cp` 라서 **`ModuleNotFoundError: No module named 'ffc_ddw_sum_et.algorithm.sw_cp'`** (임포트 파괴).

## 2. 리뷰로 확정한 분류

| 구분 | 내용 |
| --- | --- |
| **반드시 고쳐야 (파괴)** | 디렉토리 미변경으로 임포트 실패 (src, tests) |
| **과잉 치환 (되돌림)** | 외부 레포(hybridflowshop) 실제 메서드명을 가리키는 인용까지 치환됨 |
| **안 바뀐 이름 (일관성)** | 클래스명 `PwCp*`, 파일/디렉토리명 |
| **의도적 보존** | `output/` 결과물, `metadata/**/pw_cp_*.yaml`·`docs/`·`plans/` 파일명 및 문서 내용 |

## 3. 실제 수행 내역 (작업 로그)

1. **디렉토리 리네임** (`git mv`, rename 이력 보존):
   - `src/ffc_ddw_sum_et/algorithm/pw_cp/` → `sw_cp/`
   - `tests/algorithm/pw_cp/` → `sw_cp/`
   → 내부 상대임포트(`.cp_model` 등)는 그대로라 즉시 복구. `option.py`의 sphinx 참조
   `:func:` `...algorithm.sw_cp.visual...` 도 이로써 올바르게 됨.
2. **과잉 치환 되돌림**: `src/ffc_ddw_sum_et/orchestration/controller.py:2460`
   `Mirrors hybridflowshop/controller/hfs_cp_lns.py:incremental_sw_cp`
   → `...:incremental_pw_cp` (외부 레포의 실제 메서드명은 여전히 `pw_cp` 계열이므로 사실관계 복원).
3. **클래스명 리네임** (코드 한정, src + tests 82곳):
   `PwCpDispatcher`, `PwCpOption`, `PwCpModelBuilder`, `PwCpBuildResult`, `PwCpStepEntry`
   → `SwCp*`. 코드 독스트링의 `PW-CP` → `SW-CP` 도 함께.
4. **AGENTS.md 안내 추가** (CLAUDE.md 미러 자동 동기화): "예전 `pw_cp` = 현재 `sw_cp`" 이며
   `output/`·historical `algorithm_id`/step-label·`metadata/**/pw_cp_*.yaml`·`docs/`·`plans/`
   파일명은 의도적으로 미변경임을 명시. + 기존 stage된 import-path 계약 줄(`algorithm.sw_cp.*`).
5. **isort 규칙 활성화** (`pyproject.toml`):

   ```toml
   [tool.ruff.lint]
   # Keep ruff's default rules (E4, E7, E9, F) and add isort import sorting.
   extend-select = ["I"]
   ```

   - 리네임으로 어긋난 import 순서 포함, 리포 전역 isort 위반 29개를 `ruff check --fix`로 정렬(17개 파일).
   - `sw_cp` 는 알파벳순으로 `step_tl_resolver`/`cumulative` 뒤로 이동 (sw > st, cu).
6. **`ruff format`** 전역 실행 (기존 포맷 드리프트 16개 파일 정리).

## 4. 의도적으로 남긴 것 (rename 대상 아님)

- **`output/`** — 기존 결과물(약 8천 파일)의 `pw_cp`/`incremental_pw_cp` 스텝 라벨·`algorithm_id`.
  향후 실행분만 `sw_cp` 로 나온다. 교차 분석 스크립트를 새로 짤 땐 두 철자 공존을 인지할 것.
- **파일/디렉토리명** (내용은 이미 `sw_cp`): `metadata/20260510/incremental_pw_cp.yaml`,
  `pw_cp_grid.yaml`, `pw_cp_hint_check.yaml`, `pw_cp_debug_small.yaml`,
  `docs/algorithms/pw_cp.md/html`, `plans/**/pw_cp_*`.
- **docs/ 및 plans/ 내용** — 사용자 지시로 손대지 않음. (그 결과 일부 문서 내부 상호링크·
  metadata 파일명 인용이 dangling 상태로 남음 — 알고 있으며 감수.)

## 5. 검증

- `uv run python -c "import ...orchestration.controller; ...algorithm.sw_cp ..."` → OK
- `uv run pytest` → **507 passed**
- `uv run ruff check --select I <renamed>` → clean; `uv run ruff format --check` → clean

## 6. 남은 항목 (deferred)

- isort 활성화로 표면화된 **기존** default 규칙 위반: **E402 ×7** (scripts 시각화 파일의 mid-file
  import), **F841 ×2** (미사용 변수). 이번 rename과 무관 → 별도 처리 예정.
- 문서 파일명 리네임 및 내부 링크 정리 — 보류(선택).
