# Plan: LSL/OSL slack rules + reverse-dispatch job-sequence sweep

## Context

요구 사항(사용자, 2026-06-24):

1. **LSL / OSL을 논문 그대로 새로 구현** — Pan et al. (2017)의 dispatching rule:
   - **LSL** (smallest slack on the last machine):
     `d⁺_{π(j)} − p_{m,π(j)} ≤ d⁺_{π(j+1)} − p_{m,π(j+1)}` (마지막 stage 처리시간 기준 slack 오름차순)
   - **OSL** (overall slack time):
     `d⁺_{π(j)} − Σ_{i=1}^{m} p_{i,π(j)} ≤ d⁺_{π(j+1)} − Σ_{i} p_{i,π(j+1)}` (전 stage 합 기준 slack 오름차순)
2. **`get_eddub_job_sequence` 에 Pan et al. (2017) initialization이라는 노트 추가**
   — EDD/LSL/OSL 세 규칙 모두 같은 논문의 초기화 휴리스틱임을 docstring에 기록.
3. **reverse-dispatch sequence sweep config 작성** — 새로 만들어지는 LSL/OSL를
   포함해 **모든 instance-기반 job sequence**를 `_dispatch_by_reversed_sequence_with_iit`
   (stage-flip → mixed dispatch(역순) → un-flip → make_semi_active → insert_idle_time)
   에 흘려 schedule을 만드는 실험 config.

> 논문 표기 `d⁺` ↔ 본 프로젝트 `d⁺_j`(due window 상한 = 정시완료 마지노선)가 그대로
> 일치한다. 단일 due date 모델의 `d`를 due-window 모델의 `d⁺`로 두는 것은 기존
> `get_eddub_job_sequence` / `get_due_weight_pos_job_sequence`(slack에 `d⁺` 사용)의
> 관례와 동일하다 — 재해석 없이 직역 가능.

### 이미 있는 것 (재사용 대상)

- **reverse pipeline 단계 2~5는 구현 완료** —
  `_dispatch_by_reversed_sequence_with_iit(job_sequence, instance=None)`
  (`orchestration/controller.py:1443`): stage-reverse → `MixedDispatcher`로
  `reversed(seq)` 두 변형(`machine_then_job` T/F) dispatch → 더 나은 쪽 `as_reversed()`
  → `make_semi_active` → `insert_idle_time`. 원척도 wET 반환.
- **getter→pipeline→register wrapper** — `_initialize_by_reversed_sequence(getter)`
  (`controller.py:1575`). `initialize_by_w1` / `wxd1` / `wxd2` / `due2_weight_pos` /
  `eddub_twt` 가 공유하는 thin-step 토대. step 계약(단일 `_register`, 측정 직전 무작업)
  자동 충족.
- **OSL 키는 이미 점수 맵으로 존재** —
  `get_job_2_due_date_ub_minus_p_map()` (`parameters/ffc_ddw_params.py:521`)
  `= d⁺_j − Σ_i p_{i,j}` 가 **OSL slack 그 자체**. 정렬 getter만 없음 → 새 getter가
  이 맵을 재사용(SSOT).
- **LSL용 부품** — `get_job_2_p_map_for_stage(last_stage_id)`
  (`parameters/ffc_params.py:179`) = `p_{m,j}`. `get_due_weight_pos_job_sequence`
  (`:659`)가 이미 `p_last = self.get_job_2_p_map_for_stage(self.stage_id_list[-1])`
  로 마지막 stage p를 인라인으로 쓴다 → 동일 패턴 차용.
- **YAML flow → 메서드 reflection** — routix `SubroutineController._call_method`가
  `getattr(self, method_name)(**kwargs)` 로 호출. 따라서 step 추가 kwarg는 YAML의
  추가 키로 전달된다(`init_eddub_twt_config.yaml`의 `factor: 10` 선례). →
  `method: initialize_by_reversed_dispatch` + `sequence: lsl` 가능.

### 빠진 조각

| 조각 | 현재 | 필요 |
| --- | --- | --- |
| LSL 정렬 getter | 없음 (`due-weight-pos`에 `max(0,d⁺−p_last)` 클램프 변형만 존재) | `get_lsl_job_sequence` 신규 (클램프 없음, 순수 논문식) |
| OSL 정렬 getter | 점수 맵만 있음(`..ub_minus_p_map`) | `get_osl_job_sequence` 신규 (맵 재사용) |
| eddub 출처 노트 | docstring에 출처 없음 | Pan et al. (2017) 노트 추가 |
| "모든 sequence를 reverse-pipeline에" 단일 진입점 | getter별 thin step 5개뿐, 일부 getter는 reverse step 미배선 (`edd`는 forward, `weight-due-pos`/`due-weight-pos`/`due*-weight-pos` 미배선) | 파라미터화 step 1개 + sequence 레지스트리 |
| sweep config | 없음 | 신규 config (sequence별 1 scenario) |

### 읽은 코드

- `parameters/ffc_ddw_params.py`
  - `get_eddub_job_sequence` (`:609`) — EDD, `(d⁺ asc, pos)`. **노트 추가 대상.**
  - `get_eddub_twt_job_sequence` (`:619`), `get_due_weight_pos_job_sequence` (`:659`,
    `p_last` 인라인 예), `get_w1/wxd1/wxd2/...` (`:708`~) — 코드 스타일(`job_2_pos`,
    `key()` 클로저) 참고.
  - `get_job_2_due_date_ub_minus_p_map` (`:521`) — OSL 키 = `d⁺ − Σp`.
  - `job_2_dw_ub_map` (`:78`) = `d⁺_j`.
- `parameters/ffc_params.py:179` — `get_job_2_p_map_for_stage`.
- `parameters/sorter.py` — `ParamSortKey`(`:18`, 6키) + `param_sort_job_sequence`
  (`:28`). neh_cp(`NehCpJobPriority = ParamSortKey`)·IO 히트맵(`HeatmapSort`)이
  **공유** → 이 Literal을 넓히면 두 소비자에 의도치 않은 키가 노출됨(ISP 주의).
- `orchestration/controller.py` — `_dispatch_by_reversed_sequence_with_iit` (`:1443`),
  `_initialize_by_reversed_sequence` (`:1575`), `initialize_by_eddub_twt` (`:1640`,
  파라미터 step 선례), `initialize_by_edd` (`:1535`, **forward** — 대조용).
- routix `SubroutineController._call_method` — `getattr(self, method_name)(**kwargs)`.
- `metadata/20260624/init_eddub_twt_config.yaml` — config 헤더/스키마 복사 원본.
- `TODO.md` — 충돌하는 deferred 항목 없음.

---

## Design

### D1. LSL / OSL 정렬 getter — `FFcDDWParameters`

`parameters/ffc_ddw_params.py`, 기존 getter 군 옆(`get_eddub_twt_job_sequence` 뒤)에
추가. 둘 다 논문 그대로 slack 오름차순, 결정성 위해 native position 2차 키
(`get_eddub_job_sequence` 와 동일한 tie-break). `O(n log n)`.

```python
def get_lsl_job_sequence(self) -> list[str]:
    """LSL (smallest slack on the last machine) job sequence — Pan et al. (2017).

    Sort ascending by last-stage slack ``d⁺_j − p_{m,j}`` (m = 마지막 stage),
    ties break by native ``job_id_list`` position. ``d⁺_j`` 는 due window 상한.
    ``get_due_weight_pos_job_sequence`` 의 ``max(0, d⁺−p_last)`` 변형과 달리 0
    클램프·추가 tie-break 없이 논문식 그대로다.
    """
    p_last = self.get_job_2_p_map_for_stage(self.stage_id_list[-1])
    dw_ub = self.job_2_dw_ub_map
    job_2_pos = {j: pos for pos, j in enumerate(self._job_id_list)}
    return sorted(
        self.job_id_list,
        key=lambda j: (dw_ub[j] - p_last[j], job_2_pos[j]),
    )

def get_osl_job_sequence(self) -> list[str]:
    """OSL (overall slack time) job sequence — Pan et al. (2017).

    Sort ascending by overall slack ``d⁺_j − Σ_i p_{i,j}`` (모든 stage 합),
    ties break by native ``job_id_list`` position. LSL의 일반화로, slack을 전
    stage 처리시간 합 기준으로 계산한다. 키는 기존 점수 맵
    :meth:`get_job_2_due_date_ub_minus_p_map` 를 재사용한다(SSOT).
    """
    osl = self.get_job_2_due_date_ub_minus_p_map()  # d⁺_j − Σ_i p_{i,j}
    job_2_pos = {j: pos for pos, j in enumerate(self._job_id_list)}
    return sorted(self.job_id_list, key=lambda j: (osl[j], job_2_pos[j]))
```

- 반환은 **forward priority sequence**. 역순(단계 3)은 pipeline 내부에서 적용되므로
  getter는 reverse하지 않는다(기존 getter들과 동일 계약).

### D2. `get_eddub_job_sequence` 에 출처 노트 추가

`parameters/ffc_ddw_params.py:609` docstring에 한 줄 추가(코드 동작 변경 없음):

```python
def get_eddub_job_sequence(self) -> list[str]:
    """EDDUB (Earliest Due Date Upper Bound) job sequence.

    Sort by ``d⁺_j`` ascending; ties break by native ``job_id_list`` order.

    Pan et al. (2017)의 초기화 휴리스틱 중 EDD에 해당한다(같은 논문의 LSL =
    :meth:`get_lsl_job_sequence`, OSL = :meth:`get_osl_job_sequence`). due-window
    모델에서는 단일 due date ``d`` 를 상한 ``d⁺_j`` 로 둔다.
    """
```

### D3. 파라미터화 reverse-dispatch step + sequence 레지스트리

**왜 파라미터 step 1개인가**: 요구는 "**모든** sequence를 같은 reverse pipeline에"
이다. getter별 thin step을 11개 늘리는 대신, sequence 이름을 받는 step 하나가
레지스트리에서 getter를 찾아 `_initialize_by_reversed_sequence` 에 위임하면 KISS/DRY.
디코더(reverse+IIT)를 상수로 고정해 **정렬 규칙만** 비교하는 실험 의도와도 맞는다.

#### D3a. 레지스트리 (신규, `parameters/sorter.py`)

`ParamSortKey` 는 neh_cp·히트맵이 공유하므로 **건드리지 않는다**(ISP). 대신 sweep
전용 키 vocabulary와 dispatcher를 새로 둔다. 6개 공유 키는 `param_sort_job_sequence`
에 위임해 SSOT 유지:

```python
DispatchSeqKey = Literal[
    "edd", "eddub_twt", "lsl", "osl",
    "weight_due_pos", "due_weight_pos", "due2_weight_pos", "due_star_weight_pos",
    "w1", "wxd1", "wxd2",
]

def dispatch_seq_job_sequence(
    instance: FFcDDWParameters, key: DispatchSeqKey
) -> list[str]:
    """Map a sweep key to its instance-derived forward priority sequence."""
    direct = {
        "edd": instance.get_eddub_job_sequence,
        "eddub_twt": instance.get_eddub_twt_job_sequence,
        "lsl": instance.get_lsl_job_sequence,
        "osl": instance.get_osl_job_sequence,
        "w1": instance.get_w1_job_sequence,
        "wxd1": instance.get_wxd1_job_sequence,
        "wxd2": instance.get_wxd2_job_sequence,
    }
    if key in direct:
        return direct[key]()
    shared = {  # ParamSortKey로 위임
        "weight_due_pos": "weight-due-pos",
        "due_weight_pos": "due-weight-pos",
        "due2_weight_pos": "due2-weight-pos",
        "due_star_weight_pos": "due*-weight-pos",
    }
    if key in shared:
        return param_sort_job_sequence(instance, shared[key])
    raise ValueError(f"Unknown DispatchSeqKey: {key!r}")
```

> sorter.py의 "no runtime import from the rest of the package" 제약 유지: 위 함수는
> `instance` 메서드만 호출하므로 import 추가 없음.

#### D3b. step (`orchestration/controller.py`, `initialize_by_eddub_twt` 뒤)

```python
def initialize_by_reversed_dispatch(
    self, sequence: DispatchSeqKey
) -> SubroutineReport:
    """Step: ``sequence`` 규칙으로 정렬한 뒤 reverse-instance + IIT pipeline
    (:meth:`_dispatch_by_reversed_sequence_with_iit`)으로 incumbent를 seed한다.

    디코더(stage-flip → mixed dispatch(역순) → un-flip → make_semi_active →
    insert_idle_time)는 고정이고 정렬 규칙만 ``sequence`` 로 바뀐다. 키는
    :func:`dispatch_seq_job_sequence` 참조. ``initialize_by_w1`` /
    ``initialize_by_eddub_twt`` 와 같은 reverse 계열이며, 이들을 단일 진입점으로
    일반화한 것이다.
    """
    return self._initialize_by_reversed_sequence(
        lambda: dispatch_seq_job_sequence(self.instance, sequence)
    )
```

- `_initialize_by_reversed_sequence` 위임 → step 계약 자동 충족.
- 기존 `initialize_by_w1` 등 thin step은 **그대로 둔다**(다른 config가 참조). 새 step은
  추가일 뿐 제거/대체 아님.
- import: `from ..parameters.sorter import DispatchSeqKey, dispatch_seq_job_sequence`.
- 범위 결정: `factor`(coarsen-aware)는 이번 sweep 요구에 없음 → **YAGNI, 미포함**.
  필요 시 `eddub_twt` 처럼 후속 확장.

### D4. sweep 실험 config

`metadata/20260624/reversed_dispatch_sequence_sweep_config.yaml`. 헤더/공통 키는
`init_eddub_twt_config.yaml` 에서 복사. sequence별 1 scenario(총 11), 모두
동일 step·동일 timelimit, `output_subdir`/`name` 로만 구분(스캐폴딩):

```yaml
# Reverse-dispatch sequence sweep: 모든 instance-기반 job sequence를 동일한
# _dispatch_by_reversed_sequence_with_iit pipeline에 흘려 incumbent wET 비교.
# See plans/experiment/20260624/lsl_osl_reversed_dispatch_sequence_sweep.md
run_mode: FULL_RUN
benchmark_dir: benchmarks/PRA2017/large
ins_index_source: benchmarks/PRA2017/pra2017_hybrid_match.csv
ins_index: [60, 61, 63, 64, 68, 150, 152, 155, 246, 248]   # 10-instance smoke subset
bks_table_csv_path: benchmarks/PRA2017/pra2017_bks_table.csv
output_dir: output/20260624
instance_worker_cnt: 16
draw_gantt: false
painter_thread_cnt: 16

scenarios:
  - name: rd_edd            # Pan et al. (2017) EDD
    timelimit: "0.09nc"
    output_subdir: rd_edd
    subroutine_flow: [{ method: initialize_by_reversed_dispatch, sequence: edd }]
  - name: rd_lsl            # Pan et al. (2017) LSL (신규)
    timelimit: "0.09nc"
    output_subdir: rd_lsl
    subroutine_flow: [{ method: initialize_by_reversed_dispatch, sequence: lsl }]
  - name: rd_osl            # Pan et al. (2017) OSL (신규)
    timelimit: "0.09nc"
    output_subdir: rd_osl
    subroutine_flow: [{ method: initialize_by_reversed_dispatch, sequence: osl }]
  - name: rd_eddub_twt
    timelimit: "0.09nc"
    output_subdir: rd_eddub_twt
    subroutine_flow: [{ method: initialize_by_reversed_dispatch, sequence: eddub_twt }]
  - name: rd_w1
    timelimit: "0.09nc"
    output_subdir: rd_w1
    subroutine_flow: [{ method: initialize_by_reversed_dispatch, sequence: w1 }]
  - name: rd_wxd1
    timelimit: "0.09nc"
    output_subdir: rd_wxd1
    subroutine_flow: [{ method: initialize_by_reversed_dispatch, sequence: wxd1 }]
  - name: rd_wxd2
    timelimit: "0.09nc"
    output_subdir: rd_wxd2
    subroutine_flow: [{ method: initialize_by_reversed_dispatch, sequence: wxd2 }]
  - name: rd_weight_due_pos
    timelimit: "0.09nc"
    output_subdir: rd_weight_due_pos
    subroutine_flow: [{ method: initialize_by_reversed_dispatch, sequence: weight_due_pos }]
  - name: rd_due_weight_pos
    timelimit: "0.09nc"
    output_subdir: rd_due_weight_pos
    subroutine_flow: [{ method: initialize_by_reversed_dispatch, sequence: due_weight_pos }]
  - name: rd_due2_weight_pos
    timelimit: "0.09nc"
    output_subdir: rd_due2_weight_pos
    subroutine_flow: [{ method: initialize_by_reversed_dispatch, sequence: due2_weight_pos }]
  - name: rd_due_star_weight_pos
    timelimit: "0.09nc"
    output_subdir: rd_due_star_weight_pos
    subroutine_flow: [{ method: initialize_by_reversed_dispatch, sequence: due_star_weight_pos }]
```

- 단독 init 비교용(후속 solve 없음) → 각 scenario의 등록 incumbent wET가 비교량.
- `edd` 도 **reverse pipeline**으로 통과시킨다(요구: "모든 sequence를
  `_dispatch_by_reversed_sequence_with_iit` 로"). 기존 `initialize_by_edd`(forward)와는
  디코더가 달라 결과가 다를 수 있음 — 의도된 동일 조건 비교.
- `main.py` `CONFIG_PATH` 전환은 사용자 판단(스폿 실행 시). 기본은 미변경.

---

## Work Packages

의존: **WP-1 → WP-3**, WP-2(노트)는 독립, WP-4(config)는 WP-3 후, WP-5(테스트)는
WP-1·WP-3 후. WP-1/WP-2는 병렬 가능.

### WP-1 — LSL/OSL getter (`parameters/ffc_ddw_params.py`)
- D1의 `get_lsl_job_sequence`, `get_osl_job_sequence` 추가.
- 계약: slack 오름차순 안정 정렬, 모든 job 포함, 입력 불변, `O(n log n)`. OSL은
  `get_job_2_due_date_ub_minus_p_map` 재사용.

### WP-2 — eddub docstring 노트 (`parameters/ffc_ddw_params.py:609`)
- D2의 Pan et al. (2017) 노트 추가. 코드 동작 변경 없음.

### WP-3 — 레지스트리 + 파라미터 step
- **3a** `parameters/sorter.py`: D3a의 `DispatchSeqKey` +
  `dispatch_seq_job_sequence`. `ParamSortKey` 불변(ISP).
- **3b** `orchestration/controller.py`: D3b의 `initialize_by_reversed_dispatch`
  (`initialize_by_eddub_twt` 뒤) + import.
- 의존: WP-1(lsl/osl getter 이름).
- 계약: YAML `method: initialize_by_reversed_dispatch`, `sequence: <key>` 가
  reflection으로 호출되어 incumbent 1개 `_register`(단일 `_register` 유지).

### WP-4 — sweep config (`metadata/20260624/reversed_dispatch_sequence_sweep_config.yaml`)
- D4의 11-scenario config. 의존: WP-3.

### WP-5 — 테스트
- **5.1** `tests/parameters/test_ffc_ddw_params.py`:
  - `get_lsl_job_sequence`: 작은 수기 instance로 `d⁺−p_last` 오름차순·동률 시
    position tie-break 검증.
  - `get_osl_job_sequence`: `d⁺−Σp` 오름차순·tie-break 검증, 그리고 LSL과
    구분되는(전 stage 합 vs 마지막 stage) 케이스 1개.
- **5.2** `tests/orchestration/test_controller.py`:
  - `initialize_by_reversed_dispatch("lsl")` / `("osl")` 가 (a) feasible full
    schedule register, (b) `report.obj_value == compute_weighted_earliness_tardiness` 합.
  - 알려지지 않은 키 → `ValueError`(레지스트리 계약).
- **5.3** `uv run ruff check` / `uv run ruff format` clean.

---

## 검증 계획

1. WP-5 단위 테스트 red→green (`uv run pytest`).
2. `uv run ruff check` / `uv run ruff format`.
3. WP-4 config로 10-instance smoke 스폿 실행 → 11 scenario의 incumbent wET 비교.
   - LSL/OSL이 합리적 feasible schedule을 내는지, EDD/eddub_twt/w1/wxd* 대비
     어떤지 확인.
   - `edd` reverse-pipeline 결과 vs 기존 `initialize_by_edd`(forward) 차이 관찰
     (디코더 영향 분리).

## Decisions / 열린 선택

- ✅ **LSL/OSL은 논문식 그대로**(0 클램프·추가 tie-break 없음). 결정성 위한 position
   tie-break만 추가 — 기존 `get_eddub_job_sequence` 관례와 동일.
- ✅ **OSL 키 SSOT**: 기존 `get_job_2_due_date_ub_minus_p_map` 재사용(중복 계산 없음).
- ✅ **파라미터 step 1개 + 전용 레지스트리**(getter별 step 11개 대신). 디코더 고정·
   정렬만 비교라는 실험 의도와 KISS/DRY에 부합.
- ✅ **`ParamSortKey` 미확장**(ISP): neh_cp·히트맵 소비자에 sweep 전용 키를 노출하지
   않도록 별도 `DispatchSeqKey` 신설. 6개 공유 키는 `param_sort_job_sequence`
   위임으로 SSOT.
   - *대안(미채택)*: `ParamSortKey` 에 edd/lsl/osl/... 추가 — 공유 Literal이 넓어져
     두 소비자가 의미 없는 키를 받게 됨(tradeoff: 레지스트리 1개로 단순하나 ISP 위반).
- ✅ **`factor`(coarsen-aware) 미포함**: 이번 sweep 요구 밖 → YAGNI. 후속 확장 여지만.
- ⏳ **`main.py` CONFIG_PATH 전환**: 스폿 실행 시 사용자 판단. 기본 미변경.

---

## Addendum (2026-06-24): simple job-centric dispatch sibling

사용자 후속 요구: reversed pipeline 말고 **단순 job-centric dispatch**로도 같은
sequence sweep을 돌린다. 복잡한 stage-flip/IIT 없이 `MixedDispatcher` 가 모든 job을
job-centric으로 한 번에 내리는 forward 디코드.

- **`MixedDispatcher.get_job_centric_schedule_by_sequence(job_sequence, ...)`**
  (`algorithm/dispatcher/mixed.py`): `np = job_count` head 단일 디코드. np-sweep·
  scoring 없이 모든 job을 `dispatch_job_by_stages` 로 전 stage 통과. 기존
  `_stage_2_head_for_np` / `from_job_sequence_get_schedule_mixed` 재사용.
- **`initialize_by_simple_dispatch(sequence)`** (`orchestration/controller.py`):
  D3a의 `dispatch_seq_job_sequence` 레지스트리(디코더 무관, sequence만 제공)로
  정렬 → 위 디코드 → wET register. step 계약 직접 충족(`initialize_by_edd` 패턴).
  **IIT/semi-active 없음** — "simple"의 핵심(요청 시 IIT 변형 추가 가능).
- **config**: `metadata/20260624/simple_dispatch_sequence_sweep_config.yaml`,
  11 scenario(`sd_*`), `reversed_dispatch_sequence_sweep_config.yaml` 와 동일 헤더/키,
  `method: initialize_by_simple_dispatch` 만 다름. reversed sweep과 짝지어 비교.
- **테스트**: `test_initialize_by_simple_dispatch_*` 3개(full-schedule register,
  lsl/osl feasible, unknown key→`ValueError`). ruff clean, controller suite 19 green.
