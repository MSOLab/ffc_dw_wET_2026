# Plan: schedule initialization `initialize_by_eddub_twt` (EDDUB+w⁺ → reverse-dispatch)

## Context

새 schedule initialization 방식을 추가한다. 요구 recipe:

1. **job을 우선순위에 따라 정렬** — commit `c7f54d0`(`feat(csr)!: add dispatch
   seed warm-start hint`)가 도입한 정렬 우선순위를 그대로 사용:
   `(d⁺_j 오름차순, w⁺_j 내림차순, given index 오름차순)`
   (`coarsen_solve_reconstruct.py:_dispatch_seed_job_sequence` 의
   `key=lambda j: (dw_ub[j], -twt[j], given_index[j])`).
2. **instance의 stage 순서를 뒤집기** —
   `FFcDDWParameters.reverse_stages(instance)`
   (`flip_makespan_cp/dispatcher.py:119` 와 동일 API).
3. **job 우선순위 역순에 따라 mixed dispatch** — 역방향 instance 위에서
   `reversed(job_sequence)` 로 `MixedDispatcher` 실행.
4. **instance를 un-flip** — `FFcSchedule.as_reversed()`.
5. **make semi active, insert idle time** —
   `make_semi_active(instance.stage_2_job_2_p_map)` →
   `insert_idle_time(due_window, ewt, twt)` (원 instance 척도).

### 핵심 발견: recipe의 2~5단계는 이미 구현되어 있다

Controller의 `_dispatch_by_reversed_sequence_with_iit(job_sequence)`
(`orchestration/controller.py:1440-1524`)가 정확히 단계 2~5를 수행한다:

- `reversed_instance = FFcDDWParameters.reverse_stages(instance)` (단계 2)
- `rev_seq = list(reversed(job_sequence))` 후
  `MixedDispatcher(reversed_instance).get_best_mixed_schedule_by_sequence(
  rev_seq, machine_then_job=True/False, criteria="makespan")` 두 변형 실행 (단계 3)
- 두 후보를 각각 `as_reversed()` 로 un-flip 후 **원 instance 척도 wET가 작은 쪽**
  선택 (단계 4)
- `make_semi_active(instance.stage_2_job_2_p_map)` →
  `insert_idle_time(...)` (단계 5)

이 helper는 `_initialize_by_reversed_sequence(sequence_getter)`
(`controller.py:1566-1586`)를 통해 `initialize_by_w1` / `initialize_by_wxd1` /
`initialize_by_wxd2` / `initialize_by_due2_weight_pos` 가 공유한다. 각 step은
**서로 다른 sequence_getter** 하나만 다르다.

> 즉 새 방식은 기존 `initialize_by_*` family에 **새 정렬(getter) 하나 + 그 getter를
> 먹이는 thin step 하나**를 더하는 것으로 끝난다. 단계 2~5는 검증된 공유 pipeline을
> 재사용한다.

### 빠진 단 한 조각: 단계 1의 정렬(getter)

`FFcDDWParameters` 의 기존 sequence getter 중 `(d⁺ asc, w⁺ desc, idx asc)` 와
일치하는 것이 **없다**:

| getter | 정렬 키 | 일치? |
| --- | --- | --- |
| `get_eddub_job_sequence` (`:611`) | `(d⁺ asc, pos asc)` | w⁺ 2차 키 **없음** → 불일치 |
| `get_due_weight_pos_job_sequence` (`:644`) | `(max(0,d⁺-p_last) asc, d⁺, d⁻, -(w⁻+w⁺), pos)` | 불일치 |
| `get_w1_job_sequence` (`:693`) | `(-(w⁺-w⁻), pos)` | 불일치 |

따라서 commit `c7f54d0` 의 ordering을 담는 **새 getter**가 필요하다. 이 ordering은
CSR의 `_dispatch_seed_job_sequence` 와 **글자 그대로 동일**하므로, 새 getter를
single source of truth로 두고 CSR이 이를 재사용하도록 정리할 수 있다(WP-4, 선택).

### 읽은 코드

- `algorithm/coarsen_solve_reconstruct.py:_dispatch_seed_job_sequence`
  — commit `c7f54d0` 정렬: `(dw_ub[j], -twt[j], given_index[j])`.
- `orchestration/controller.py`
  - `_dispatch_by_reversed_sequence_with_iit` (`:1440`) — 단계 2~5 pipeline.
  - `_initialize_by_reversed_sequence` (`:1566`) — getter→pipeline→`_register` wrapper.
  - `initialize_by_w1` (`:1598`), `initialize_by_wxd1` (`:1605`),
    `initialize_by_wxd2` (`:1617`) — 동일 패턴의 thin step 선례.
  - `initialize_by_edd` (`:1526`) — 주의: 이쪽은 **forward** `_dispatch_by_sequence`
    pipeline(역방향 아님). 새 step과 헷갈리지 않게 docstring에서 구분.
- `parameters/ffc_ddw_params.py`
  - `job_2_dw_ub_map` (`:78`) = d⁺_j, `_job_2_twt_map` (`:41`) = w⁺_j,
    `job_id_list`, `reverse_stages` (`:101`).
  - 기존 getter 군 (`:611`~`:817`) — 코드 스타일(내부 `job_2_pos`, `key()` 클로저)
    참고.
- `algorithm/mcf_lb/full_sch_builder.py:reverse_dispatch_full_schedule` — 같은
  reverse-dispatch+unflip 패턴의 **다른** 변형(last-stage seed에서 출발,
  `from_stage=stage_id_list[1]`). 이번 건과 다름: 새 방식은 **빈 schedule에서
  full reverse dispatch** 이므로 `_dispatch_by_reversed_sequence_with_iit` 쪽이
  정확한 재사용 대상이다(이 함수를 변경하지 않는다).
- `docs/problem-description.md`: `d⁺_j`=due window upper bound, `w⁺_j`=tardiness
  weight, 목적함수는 마지막 stage 기준 `Σ_j (w⁻_j E_j + w⁺_j T_j)`.
- `tests/`: `initialize_by_*` step 및 param sequence getter에 대한 **전용 단위
  테스트는 현재 없음**. 새 코드에 한해 최소 단위 테스트를 추가한다(아래 WP-3).
- `TODO.md`: 충돌하는 deferred 항목 없음.

---

## Design

### D1. 새 sequence getter (단계 1) — `FFcDDWParameters`

`parameters/ffc_ddw_params.py`, 기존 getter들과 같은 스타일로 추가:

```python
def get_eddub_twt_job_sequence(self) -> list[str]:
    """EDDUB + tardiness-weight 우선순위 job sequence.

    Sort by (d⁺_j asc, w⁺_j desc, position asc). ``get_eddub_job_sequence``
    와 동일한 d⁺ 1차 키에 w⁺(tardiness weight) 내림차순 2차 키를 더해, 마감이
    같으면 지각 비용이 큰 job을 먼저 둔다. commit c7f54d0의 dispatch seed 정렬과
    동일하다.
    """
    twt = self._job_2_twt_map
    dw_ub = self.job_2_dw_ub_map
    job_2_pos = {j: pos for pos, j in enumerate(self._job_id_list)}
    return sorted(
        self.job_id_list,
        key=lambda j: (dw_ub[j], -twt[j], job_2_pos[j]),
    )
```

- 반환은 **forward priority sequence**. 역순(단계 3)은 pipeline 내부
  (`_dispatch_by_reversed_sequence_with_iit` 의 `list(reversed(job_sequence))`)에서
  적용되므로 getter는 reverse하지 않는다.

### D2. 새 step method (단계 2~5 위임) — `FFcDDWSubroutineController`

`orchestration/controller.py`, `initialize_by_wxd2` 바로 뒤에 추가:

```python
def initialize_by_eddub_twt(self, factor: int = 1) -> SubroutineReport:
    """Step method: EDDUB+w⁺ 순서로 incumbent를 seed한다.

    정렬은 ``(d⁺_j asc, w⁺_j desc, position asc)``
    (:meth:`FFcDDWParameters.get_eddub_twt_job_sequence`). 그 sequence를
    reverse-instance + IIT pipeline
    (:meth:`_dispatch_by_reversed_sequence_with_iit`)에 흘려보낸다 — 즉
    stage를 뒤집고, 우선순위 역순으로 mixed dispatch한 뒤 un-flip하고
    ``make_semi_active`` → ``insert_idle_time`` 로 마무리한다.
    ``initialize_by_w1`` / ``initialize_by_wxd*`` 와 같은 계열이며, forward
    dispatch를 쓰는 ``initialize_by_edd`` 와는 pipeline이 다르다.

    ``factor > 1`` 이면 instance coarsening → dispatch → reconstruct 파이프라인을
    거친다 (D4 참조). ``factor == 1`` 은 원척도 직행 경로와 동일.
    """
    if factor == 1:
        return self._initialize_by_reversed_sequence(
            self.instance.get_eddub_twt_job_sequence
        )

    start_elapsed = time.monotonic()
    coarsened = FFcDDWParameters.coarsen_time_resolution(self.instance, factor)
    coarse_sched, _ = self._dispatch_by_reversed_sequence_with_iit(
        coarsened.get_eddub_twt_job_sequence(), instance=coarsened
    )
    schedule = reconstruct_coarse_schedule(coarse_sched, self.instance, factor)
    sum_e, sum_t = compute_weighted_earliness_tardiness(schedule, self.instance)
    obj_value = float(sum_e + sum_t)

    elapsed = time.monotonic() - start_elapsed
    report = SubroutineReport(
        elapsed_time=elapsed, obj_value=obj_value, obj_bound=None
    )
    self._register(
        report,
        FFcDDWSolution(schedule=schedule, obj_value=obj_value, obj_bound=None),
    )
    return report
```

- `factor == 1`: `_initialize_by_reversed_sequence` 위임 (step 계약 자동 충족).
- `factor > 1`: `_register` 직전까지 `elapsed` 측정 → step 계약 직접 충족
  (`initialize_by_edd` 패턴 차용).
- top-level import: `reconstruct_coarse_schedule` 는 `schedule_build` 에서 import.

### D3. 실험 config

`metadata/20260624/init_eddub_twt_config.yaml` (기존 `20260615_init_config.yaml`
헤더/공통 키 복사). 새 방식 + 기존 baseline 2개 + coarsen-aware 2개 = 총 5 scenario:

```yaml
scenarios:
  - name: init_eddub_twt
    timelimit: "0.09nc"
    output_subdir: init_eddub_twt
    subroutine_flow:
      - method: initialize_by_eddub_twt
  - name: init_w1            # baseline (same pipeline, different sort)
    timelimit: "0.09nc"
    output_subdir: init_w1
    subroutine_flow:
      - method: initialize_by_w1
  - name: init_edd           # forward-dispatch baseline
    timelimit: "0.09nc"
    output_subdir: init_edd
    subroutine_flow:
      - method: initialize_by_edd
  - name: init_eddub_twt_f10  # coarsen-aware: factor=10
    timelimit: "0.09nc"
    output_subdir: init_eddub_twt_f10
    subroutine_flow:
      - method: initialize_by_eddub_twt
        factor: 10
  - name: init_eddub_twt_f50  # coarsen-aware: factor=50
    timelimit: "0.09nc"
    output_subdir: init_eddub_twt_f50
    subroutine_flow:
      - method: initialize_by_eddub_twt
        factor: 50
```

(헤더 `run_mode`/`benchmark_dir`/`ins_index_source`/`output_dir`/
`instance_worker_cnt` 등은 `20260615_init_config.yaml` 에서 그대로 복사. 단독
init 방식이므로 후속 solve step 없이 incumbent의 obj만 비교한다.)

> **추가 결정**: `ins_index` 에 10-instance smoke subset 주석 해제.

### D4. (옵션) coarsen-aware 변형 — coarsen-dispatch-reconstruct

> 요구 추가(사용자): "직전 commit들의 coarsen_solve_reconstruct처럼, processing
> time·due window 단위를 축소한 instance를 받아 최적화하는 것이 이 init에서도
> 가능한가?" → **가능하며 자연스러운 적합.**

CSR(`coarsen_solve_reconstruct.py`)의 "단위 축소 후 최적화"는 세 조각으로
분해된다. 가운데 **solve만 dispatch로 교체**하면 그대로 재사용된다:

1. **Coarsen (재사용, 변경 0)** —
   `FFcDDWParameters.coarsen_time_resolution(instance, factor)`:
   `p → ceil(p/factor)`, due window 양끝 `→ ceil(·/factor)`,
   **weight·layout·generation_params 보존**. 순수·factor 파라미터화.
2. **Dispatch (solve 자리 교체)** — coarsened instance 위에서 D1·D2의 EDDUB+w⁺
   reverse-dispatch를 돌려 **coarse full schedule** 획득. (CSR이 이미 이 dispatch를
   *warm-start seed*로 쓰므로, 같은 결과를 seed가 아닌 최종 coarse 해로 직접 쓰는
   것뿐.)
3. **Reconstruct (재사용)** — CSR의 reconstruct(`:378-404`)는 coarse op의
   `(job, stage)→start` 만 소비한다(CP/dispatch 출처 무관):
   ```python
   reconstructed_start[j, i] = coarse_start[j, i] * factor
   reconstructed_end[j, i]   = reconstructed_start[j, i] + original_p[j][i]
   sched = build_schedule_from_op_starts(instance, start, end)  # 머신 재배정
   sched.make_semi_active(instance.stage_2_job_2_p_map)
   sched.insert_idle_time(instance.job_2_due_window_map, ewt, twt)  # 원척도
   ```
   coarse full schedule의 `get_jik_2_start_time_map()` 에서 mc를 떼면
   `(j,i)→start` 를 얻는다. `build_schedule_from_op_starts`
   (`solution/schedule_build.py:13`)는 start/end만으로 머신을 그리디 재배정하므로
   머신 정보는 필요 없다.

**호환성 핵심**: reconstruct는 coarse 해의 시작시간 맵만 소비하고 original p를
다시 입힌 뒤 원척도에서 semi-active/idle을 재최적화한다 — coarse 해의 출처에
불가지(agnostic). 따라서 dispatch가 만든 coarse 해를 그대로 넣을 수 있다.

**실제 코드 작업**:
1. `_dispatch_by_reversed_sequence_with_iit(job_sequence, instance=None)` 로
   일반화(현재 `self.instance` 하드코딩; default = `self.instance` 라 기존 호출
   불변). coarsened instance 위 dispatch에 필요.
2. CSR reconstruct 블록(`coarsen_solve_reconstruct.py:378-404`)을 **두 개**의
   공유 helper 로 추출:
   - `reconstruct_raw_coarse_schedule(coarse_sched, instance, factor)` —
     scaling only, no postprocess (CSR이 raw snapshot 별도로 필요하므로 분리).
   - `reconstruct_coarse_schedule(coarse_sched, instance, factor)` —
     raw + `make_semi_active` + `insert_idle_time` (ET-aligned 최종 schedule).
   CSR: `reconstructed_raw_schedule = reconstruct_raw_coarse_schedule(...)`
   후 `final_schedule = reconstruct_coarse_schedule(...)` 호출로 교체.
3. 새 step에 optional `factor: int = 1`:
   - `factor == 1`: 원척도 직행 — D2와 동일.
   - `factor > 1`: `coarsened = coarsen_time_resolution(self.instance, factor)`;
     `coarse_sched, _ = _dispatch_by_reversed_sequence_with_iit(
     coarsened.get_eddub_twt_job_sequence(), instance=coarsened)`;
     `schedule = reconstruct_coarse_schedule(coarse_sched, self.instance, factor)`;
     obj는 `compute_weighted_earliness_tardiness(schedule, self.instance)`.

**주의**: coarse pipeline 끝의 `insert_idle_time` 는 coarse 척도에서 일어나지만,
reconstruct가 그 coarse 시작시간을 scale→original p 재적용→**원척도에서
make_semi_active+insert_idle 재수행** 하므로 최종 해는 원척도에서 ET-정렬된다
(CSR과 동일한 계약). `factor>1` 의 reconstruct는 한 줄 짜리 step 위임을 넘어서므로,
step 계약(단일 `_register`, 측정 직전 무작업)을 직접 만족하도록 작성한다
(`initialize_by_edd` 패턴: `start_elapsed` → 작업 → `elapsed` → `_register`).

---

## Work Packages

의존: **WP-1 → WP-2**, WP-3(테스트)는 WP-1·WP-2 후, WP-5(config)는 WP-2 후.
WP-6(coarsen-aware)는 WP-1·WP-2 후. WP-4(SSOT getter 재사용)는 **보류**.
WP-1과 WP-2는 인터페이스(getter 이름)만 합의되면 병렬 가능.

> **진행 순서**(사용자 결정 2026-06-24): core **WP-1 → WP-2 → WP-3** 먼저,
> 이어서 **WP-6(coarsen-aware)** + **WP-5(config)**. WP-4는 보류. 구현은 사용자가
> 별도로 진행하며, 본 문서는 그 명세이다.

### WP-1 — `parameters/ffc_ddw_params.py` ✅ 완료
- **변경**: D1의 `get_eddub_twt_job_sequence` 추가(기존 getter 군 옆에, `:620`).
- **계약**: `(d⁺ asc, w⁺ desc, pos asc)` 안정 정렬, 모든 job 포함, 입력 불변.
- **검증**: WP-3.1 — 4개 테스트 통과.

### WP-2 — `orchestration/controller.py` ✅ 완료
- **의존**: WP-1(getter 이름).
- **변경**: D2의 `initialize_by_eddub_twt(factor: int = 1)` step 추가(`:1640`).
  `_dispatch_by_reversed_sequence_with_iit` 에 `instance` 인자 일반화(`:1440`).
- **계약**: YAML `method: initialize_by_eddub_twt` 가 reflection으로 호출되어
  incumbent 1개를 `_register`. 단일 `_register` 계약 유지.
- **검증**: WP-3.2 — 2개 테스트 통과.

### WP-3 — 테스트 ✅ 완료
- **3.1** `tests/parameters/test_ffc_ddw_params.py`: 3-키 tie-break 검증 —
  (A) d⁺ 상이→오름차순, (B) d⁺ 동률·w⁺ 상이→w⁺ 내림차순, (C) 둘 다 동률→given
  순서. 작은 손수 instance로. (`test_get_eddub_twt_job_sequence_*`)
- **3.2** `tests/orchestration/test_controller.py`: 작은 instance에서
  `initialize_by_eddub_twt()` 가 (a) feasible full schedule을 register하고
  (b) `report.obj_value == compute_weighted_earliness_tardiness` 합과 일치.
  (`test_initialize_by_eddub_twt_registers_full_schedule`,
  `test_initialize_by_eddub_twt_feasible_full_schedule`)
- **3.3** `tests/solution/test_schedule_build.py`: 7개 테스트 —
  `reconstruct_raw_coarse_schedule` / `reconstruct_coarse_schedule` contract 검증.
- **3.4** 신규 13개 테스트 통과, `uv run ruff check` / `ruff format` clean.

### WP-4 — (보류) SSOT 정리: CSR이 새 getter 재사용 → **TODO.md로 이관**
CSR `_dispatch_seed_job_sequence` 제거 후 `get_eddub_twt_job_sequence`
재사용은 이번 feature 범위에서 **보류** (사용자 결정 2026-06-24).
`TODO.md` → "SSOT: consolidate the EDDUB+w⁺ dispatch-seed ordering" 항목에
Why / When to act와 함께 기록됨. WP-1의 getter가 생긴 뒤 별도로 처리한다.

> **부분적 달성**: CSR의 **reconstruct 블록**은 SSOT 달성 —
> `reconstruct_raw_coarse_schedule` / `reconstruct_coarse_schedule` 로 추출 후
> CSR에서 inline reconstruct 제거 (WP-6 완료 시점).

### WP-5 — 실험 config (`metadata/20260624/init_eddub_twt_config.yaml`) ✅ 완료
- **의존**: WP-2 + WP-6.
- **변경**: D3의 config. 5 scenario — `init_eddub_twt`, `init_w1`, `init_edd`,
  `init_eddub_twt_f10`, `init_eddub_twt_f50`.
- **추가**: `ins_index` 에 10-instance smoke subset 주석 해제.

### WP-6 — (사용자 요구) coarsen-aware 변형 (D4) ✅ 완료
- **의존**: WP-1·WP-2.
- **변경**:
  1. `controller.py:_dispatch_by_reversed_sequence_with_iit` 에
     `instance: FFcDDWParameters | None = None` 인자 추가(default `self.instance`).
     기존 호출 불변.
  2. CSR reconstruct 블록(`coarsen_solve_reconstruct.py:378-404`)을 **두 개**의
     공유 함수로 추출:
     - `reconstruct_raw_coarse_schedule(coarse_schedule, instance, factor)` —
       scaling only, no postprocess (CSR이 raw snapshot 별도로 필요).
     - `reconstruct_coarse_schedule(coarse_schedule, instance, factor)` —
       raw + `make_semi_active` + `insert_idle_time`.
     CSR: inline reconstruct 블록을 위 두 함수 호출로 교체.
  3. `initialize_by_eddub_twt(self, factor: int = 1)` 로 시그니처 확장:
     `factor==1` 은 D2와 동일 위임; `factor>1` 은 coarsen→dispatch(on coarsened)
     →reconstruct→원척도 make_semi_active+insert_idle→`_register`(step 계약 직접
     충족).
- **계약**: `factor==1` 결과는 D2와 동일. `factor>1` 은 CSR과 동일한 reconstruct
  계약(coarse start만 소비, 원척도 재최적화) → 항상 원 instance에서 feasible.
- **검증**:
  - reconstruct 추출 후 기존 CSR 테스트 그린 유지 (회귀 — CSR pipeline 테스트).
  - `tests/solution/test_schedule_build.py` — 7개 단위 테스트 통과.
  - YAML `method: initialize_by_eddub_twt`, `factor: N` 으로 호출 가능.

---

## 검증 계획

1. WP-3 단위 테스트 red→green.
2. `uv run ruff check` / `uv run ruff format`.
3. WP-5 config로 인스턴스 소수 스폿 실행 → `init_eddub_twt` vs `init_w1` vs
   `init_edd` 의 incumbent obj(wET) 비교. 새 정렬이 합리적 범위의 feasible
   schedule을 내는지, 기존 init 대비 obj가 어떻게 다른지 확인.
4. (WP-6) `factor` 스윕(1/10/50)으로 coarsen-aware 변형 스폿 실행 → 단위 축소가
   init 품질/속도에 주는 영향 확인. `factor==1` 이 원척도 경로와 동일한지 회귀.

## Decisions (확정 2026-06-24)

- ✅ **이름 확정**: getter `get_eddub_twt_job_sequence`, step
  `initialize_by_eddub_twt` (`get_eddub_job_sequence` / `initialize_by_edd`
  계열 일관성). — 사용자 동의(결정 1).
- ✅ **coarsen-aware(WP-6) 포함**: 사용자 요구로 포함. core(WP-1~3) → WP-6
  단계적 진행. — 사용자 동의(결정 2).
- ⏸ **WP-4(SSOT getter 통합) 보류**: CSR `_dispatch_seed_job_sequence` ↔ 새
  getter 중복 통합은 이번 범위에서 분리. `TODO.md` 에 Why/When to act와 함께
  기록. — 사용자 결정 3.
- ✅ **reconstruct SSOT 달성**: CSR inline reconstruct 블록 →
  `reconstruct_raw_coarse_schedule` / `reconstruct_coarse_schedule` 추출.
  CSR에서 inline 코드 제거 완료.
- ✅ **두 함수 분리**: CSR이 raw/final 두 스냅샷을 별도로 필요하므로
  `reconstruct_raw_coarse_schedule`(scaling only) 와
  `reconstruct_coarse_schedule`(postprocess 포함) 로 분리.
- ✅ **테스트 추가**: `tests/solution/test_schedule_build.py` — reconstruct
  helper 7개 단위 테스트.
- **mixed dispatch 변형 범위**: `_dispatch_by_reversed_sequence_with_iit` 는 이미
  `machine_then_job` True/False 두 변형을 돌려 더 나은 쪽을 고른다. 요구 "mixed
  dispatch" 를 충족하므로 추가 변형(np 후보 직접 열거 등)은 YAGNI.
- **config**: 단독 init 비교용. 후속 CP solve와 결합한 평가가 필요하면 별도
  config로 확장.
