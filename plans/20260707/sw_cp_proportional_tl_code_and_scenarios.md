# Plan — SW-CP 크기비례 per-CP TL: 코드 추가 + p25/p50 시나리오 생성 (실험 실행 제외)

**목적:** *fresh* 대화가 이 파일만 읽고 실행할 수 있도록 자기완결형으로 작성.
due-window 문제의 SW-CP per-CP 시간제약을 makespan판과 동일한 **크기비례
`TL = k · non_time_fixed_op_count`** 로 바꾸는 (1) **코드 변경**과
(2) **p25/p50 시나리오 파일 생성**까지. **"실험 실행"은 포함하지 않는다 — 사용자가 직접 실행.**
"이 파일 내용대로 해"라고 하면 실행.

**하지 말 것:** `git add`/commit 금지(변경 unstaged 유지). **실험(멀티인스턴스 런) 실행 금지.**
서브에이전트에게 git 실행 금지(과거에 stray checkout이 작업 삭제한 적 있음).
`uv run python` 사용, 코드 편집 후 `uv run ruff check` / 필요시 `uv run ruff format`.

---

## 0. 확정된 결정 (재논의 금지)

- **κ 값 (basis B2, s/op, 270 u2 pooled에서 유도됨, 이미 계산 완료):**
  **p25 = `0.000311`**, **p50 = `0.001811`**.
- **per-pass 시간제약(`total_timelimit="0.018nc"`) 미적용** — 각 CP에 `kappa·ntf`를 직접 부여.
  (p25/p50은 `Σ kappa·ntf`가 기존 `0.018nc`의 ~10%/~57%라 어차피 바인딩 안 됨.)
- **scenario `timelimit="0.09nc"`는 유지** — 없애면 마지막 `solve_base_model_cpsat`이
  예산(`min(timelimit, remaining)`)을 못 받아 무한정 돈다. 유지해도 p25/p50에선
  SW-CP 창을 자르지 않음(kappa 작음).
- **인스턴스: 1440 전체** (base 파일에 `ins_index` 필터 없음 → 그대로 전체 실행).
- **base 시나리오:** `output/20260704/20260704T164349_114896`를 만든
  `c5_lexico_full_config.yaml`의 **`s0_c5_base`**. 사용자가 그 s0을
  `metadata/20260707/sw_cp_tl_p25_p50.yaml`에 이미 복사해 둠.
- **makespan 이식 대상 원본:** `/home/hjt/code/hybridflowshop/hybridflowshop/controller/pw_cp.py:256-266`
  `_resolve_batch_time_limit` → `non_time_fixed_op_count * multiplier`. **코드 식별자는
  makespan과 동일한 `non_time_fixed_op_time_limit_multiplier`를 그대로 이식**(초/op).
- **명명 규약(사용자):** **코드 식별자**는 위 `non_time_fixed_op_time_limit_multiplier` 사용,
  **주석/docstring**에서는 이 상수를 `k`가 아니라 다섯글자 **`kappa`**(κ)로 지칭한다.

---

## 1. 코드 변경 (핵심 작업)

새 per-CP TL 모드 `"proportional"`을 추가한다. **설계 원리:** per-window `ntf`는
dispatcher 루프 안에서 partition을 빌드한 뒤(=solve 직전) 알 수 있으므로,
`kappa·ntf`를 **dispatcher 루프에서 인라인 계산**한다(루프 전에 외삽 pre-compute하지 않음 —
incumbent가 창마다 갱신되어 partition이 순차 의존적이기 때문). makespan판과 동일한 위치 구조.

### 1A. `src/ffc_ddw_sum_et/algorithm/step_tl_resolver.py`

- `BatchTlMode` Literal에 `"proportional"` 추가:
  `BatchTlMode = Literal["constant", "linear", "proportional"]`.
- `resolve_per_step_tl` 본문: `total_seconds`가 None이면 이미 상단(현재 35-38줄)에서
  `None` 반환하므로 우리 시나리오(=per-pass total 제거)에선 `"proportional"`이 분기까지
  도달하지 않는다. 다만 **방어적으로**, `batch_tl_mode == "proportional"`이면 `None`을
  반환하도록 명시 분기 추가(주석: "proportional TL은 창별 `kappa·ntf`로 dispatcher에서 계산;
  resolver는 관여하지 않음"). 이렇게 해야 누가 total_timelimit과 proportional을 함께
  줘도 하단 `raise ValueError("Unknown batch_tl_mode")`에 안 걸린다.

### 1B. `src/ffc_ddw_sum_et/algorithm/sw_cp/option.py`

- `SwCpOption`에 필드 추가 (기존 TL 필드 근처, 42-45줄 부근):
  ```python
  non_time_fixed_op_time_limit_multiplier: float | None = None
  """proportional 모드의 kappa (초/op). per-CP TL = kappa * non_time_fixed_op_count.
  makespan판 pw_cp의 동명 파라미터를 이식. batch_tl_mode='proportional'일 때 필수."""
  ```
  (식별자는 makespan과 동일; docstring은 상수를 `kappa`로 지칭.)
- `__post_init__`에 검증 추가:
  - `non_time_fixed_op_time_limit_multiplier`가 not None이면 `> 0` 이어야 함.
  - `batch_tl_mode == "proportional"`이면 이 값이 not None이어야 함(아니면 ValueError).

### 1C. `src/ffc_ddw_sum_et/algorithm/sw_cp/dispatcher.py`

현재 흐름: `per_step_tl = resolve_per_step_tl(...)`(130줄, 루프 전) → 루프 안 263줄에서
`applied_tl_seconds = self._apply_tl_and_deadline(solver, option, per_step_tl[step] ..., ...)`.
`non_time_fixed_op_count`는 현재 **solve 후** 로깅용으로 405-412줄에서 계산됨.

변경:
1. **ntf를 solve 전에 1회 계산해 재사용.** partition/promotion 확정 직후
   (현재 190줄 `enable_promotion_profile_fixed` 블록 다음, `sub_jobs` 계산 부근)에서
   `non_time_fixed_op_count = sum(len(p.non_time_fixed) for p in stage_2_partition.values())`
   를 계산한다. (405-412줄의 `unfixed_op_count`/`profile_fixed_op_count`/
   `non_time_fixed_op_count` 계산·assert는 그대로 두되, `non_time_fixed_op_count`는
   위에서 이미 구한 값을 재사용 — 중복 sum 제거, DRY.)
2. **proportional일 때 per-step TL을 인라인으로.** 263줄 `_apply_tl_and_deadline` 호출의
   3번째 인자를 다음으로 교체:
   ```python
   # proportional 모드: per-CP TL = kappa * ntf (kappa = 이 옵션 필드)
   step_tl = (
       option.non_time_fixed_op_time_limit_multiplier * non_time_fixed_op_count
       if option.batch_tl_mode == "proportional"
       else (per_step_tl[step] if per_step_tl is not None else None)
   )
   ```
   그리고 `_apply_tl_and_deadline(solver, option, step_tl, start_elapsed, step, len(iteration_idxs), logger)`.
   - `_apply_tl_and_deadline`은 `wall_clock_deadline`으로 remaining을 clamp하므로,
     0.09nc 유지 하에서 p25/p50은 clamp 안 됨(kappa 작음). 그대로 둔다.
3. **로깅 확인:** step_log의 `TL` 필드(현재 `applied_tl_seconds`)가 proportional 시
   `kappa·ntf`(또는 clamp된 값)로 기록되는지 확인. 이미 `applied_tl_seconds`를 쓰므로 자동.

### 1D. `src/ffc_ddw_sum_et/orchestration/controller.py` (plumbing)

yaml의 subroutine 메서드 kwargs는 controller 메서드 시그니처로 직결된다. 두 메서드에 파라미터 추가:

- **`sw_cp(...)`** (시그니처 ~2278줄): 파라미터
  `non_time_fixed_op_time_limit_multiplier: float | None = None` 추가.
  `SwCpOption(...)` 생성부(~2371줄, `total_timelimit_seconds=...`/`batch_tl_mode=...` 인접)에
  `non_time_fixed_op_time_limit_multiplier=non_time_fixed_op_time_limit_multiplier` 추가.
- **`incremental_sw_cp(...)`** (시그니처 ~2448줄): 동일 파라미터 추가하고,
  `base_kwargs`(~2530줄, `batch_tl_mode=batch_tl_mode` 인접)에
  `non_time_fixed_op_time_limit_multiplier=non_time_fixed_op_time_limit_multiplier` 추가
  → 내부 `self.sw_cp(...)` 호출로 전달됨.
- 이 kappa(상수 0.000311 등)는 `resolve_value_expr` 불필요 — float 그대로 전달.

### 1E. 코드 검증 (실험 아님)

- `uv run ruff check` (편집한 파일). 필요시 `uv run ruff format`.
- 관련 유닛테스트: `uv run pytest`로 `step_tl_resolver`/`sw_cp` 관련 테스트가 있으면 실행
  (`tests/` 하위에서 grep). 없으면 스킵.
- **(선택) 코드 스모크 — 실험 아님, 코드 동작 확인용.** 작은 인스턴스 1개만
  proportional 경로로 통과시켜 sw_cp step_log의 `TL` 필드가 `kappa·ntf`인지 눈으로 확인.
  전체 1440 런(=실험)은 **절대 실행하지 말 것.** 스모크도 부담되면 생략하고 ruff+유닛까지만.

---

## 2. 시나리오 생성

`metadata/20260707/sw_cp_tl_p25_p50.yaml` (현재 단일 `s0_c5_base` 있음)을 편집해
**두 시나리오** `s0_c5_p25`, `s0_c5_p50`로 만든다. 각 시나리오는 `s0_c5_base`와 **완전히 동일**하되
`incremental_sw_cp` 블록만 아래처럼 바꾼다:

**변경 전 (base):**
```yaml
      - method: incremental_sw_cp
        ...
        pf_method: "PF1"
        total_timelimit: "0.018nc"       # 제거
        batch_tl_mode: "constant"        # 변경
```
**변경 후 (각 시나리오):**
```yaml
      - method: incremental_sw_cp
        ...
        pf_method: "PF1"
        batch_tl_mode: "proportional"
        non_time_fixed_op_time_limit_multiplier: <kappa>   # p25: 0.000311 / p50: 0.001811
```
(즉 `total_timelimit` 줄 삭제, `batch_tl_mode`를 `proportional`로, kappa 줄 추가.)

기타:
- `name` / `output_subdir`: `s0_c5_p25`, `s0_c5_p50`로 각각.
- `timelimit: "0.09nc"`: **유지**(양 시나리오 동일).
- 나머지 스텝(mcf_lb / flip_makespan_cp / neh_cp / **solve_base_model_cpsat**)은 base 그대로.
- `ins_index` 없음 유지 → 1440 전체.
- `output_dir`: base가 `output/20260704`. 충돌 피하려 **`output/20260707_sw_cp_tl_p25_p50`**로
  변경 권장(사용자 확인 시 조정). `instance_worker_cnt: 12`, `solver_thread_cnt: 8` 유지
  (12×8=96코어, 오버서브 없음).

최종 파일은 `scenarios:` 아래 두 시나리오만 갖는다(원한다면 base 대비 진단용으로
`s0_c5_base`를 control로 함께 둘 수도 있으나, 기본은 p25/p50 둘만 — control은 기존
Defense 결과와 비교).

---

## 3. 완료 기준 (실험 실행 없이)

1. 1A–1D 코드 변경 완료, `uv run ruff check` 통과.
2. `SwCpOption(batch_tl_mode="proportional", non_time_fixed_op_time_limit_multiplier=0.001811, ...)`
   생성이 검증 통과, multiplier 누락 시 ValueError 나는지 간단 확인(파이썬 한 줄).
3. `metadata/20260707/sw_cp_tl_p25_p50.yaml`에 `s0_c5_p25`/`s0_c5_p50` 두 시나리오 존재,
   각 incremental_sw_cp가 `proportional` + 올바른 k, `total_timelimit` 없음, `timelimit:0.09nc` 유지.
4. 변경은 전부 **unstaged**(git add/commit 안 함). **1440 실험은 실행 안 함.**

---

## 4. Key file map

- 새 TL 모드: `src/ffc_ddw_sum_et/algorithm/step_tl_resolver.py` (`BatchTlMode`, `resolve_per_step_tl`).
- 옵션: `src/ffc_ddw_sum_et/algorithm/sw_cp/option.py` (`SwCpOption`, 42-45 / `__post_init__`).
- 인라인 TL 적용: `src/ffc_ddw_sum_et/algorithm/sw_cp/dispatcher.py`
  (partition ~173-194, TL 적용 ~263, ntf 로깅 ~405-412).
- controller plumbing: `src/ffc_ddw_sum_et/orchestration/controller.py`
  (`sw_cp` ~2278/SwCpOption ~2371, `incremental_sw_cp` ~2448/base_kwargs ~2530).
- makespan 원본 템플릿: `hybridflowshop/controller/pw_cp.py:256-266` (`_resolve_batch_time_limit`),
  호출부 `hybridflowshop/controller/hfs_cp_lns.py:17874-17984` (`def pw_cp`).
- 시나리오: `metadata/20260707/sw_cp_tl_p25_p50.yaml` (편집 대상).
- κ 유도/근거: `output/20260705_sw_cp_tl_profile_t8/20260706T015554_738214/analysis/`
  (`sw_cp_tl_p25_p50_p75_meeting.md`, `sw_cp_tl_p25_p50_p75_comparison.csv`,
  `k_for_capture_270_*`), 방법: `scripts/20260706/k_for_capture.py` (basis B2).

---

## 5. 환경/규약

- **96 물리 코어** (nproc 192는 논리 — 192 기준으로 사이징 금지).
- `uv run python …`, 편집 후 `uv run ruff check`.
- git add/commit 금지(사용자 수동). 서브에이전트 git 금지.
- **실험 실행 금지** — 코드+시나리오까지만. 실행은 사용자가 직접.
