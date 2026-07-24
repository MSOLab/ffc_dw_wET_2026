# reconstruct_mode `active_but_last_semi` + coarse-schedule 저장/replay (사전 작성)

**작성일**: 2026-07-24 · **종류**: 코드 변경 + 실험 실행 계획(사전 작성)
**선행 맥락**:
`plans/analysis/20260724/csr_reconstruct_mode_active_vs_semi.md` — `reconstruct_mode=active`가
`semi_active` 대비 **집계 wET에서 결정적으로 나쁨(+29.19 pp, 17,280쌍 중 10,550 패)**.
성분 분해(`scratch_et_decomp.csv`, 17,280쌍)에서 원인이 명확해짐:

```
mean dE(earliness)  = +8950   ← 오름 (85% 인스턴스에서 증가)
mean dT(tardiness)  = -4474   ← 내림
mean dObj           = +4476   ← 순 악화
```

즉 active의 손해는 **earliness 폭증**이다. 가장 크게 증가한 두 케이스:

| insIndex | 파일 | 셀 | dE | dT | dObj |
|---|---|---|---|---|---|
| 1089 | `Instance_200_5_3_0,2_0,2_20_Rep4` | k8 f15 | +217,514 | −145,906 | +71,608 |
| 1088 | `Instance_200_5_3_0,2_0,2_20_Rep3` | k1 f5 | +194,620 | +469 | +195,089 |

> 두 변경(Part A 새 모드, Part B 저장/replay)의 **구현은 별도 대화에서** 진행한다.
> 본 문서는 그 사전 계획(SSOT)이다.

---

## 1. 가설 — earliness 폭증의 원인은 "마지막 stage machine 재배정"

두 reconstruct 경로의 유일한 차이는 **machine assignment**이다:

| 모드 | 함수 | machine 배정 |
|---|---|---|
| `semi_active` | `reconstruct_raw_coarse_schedule` (schedule_build.py:83) | coarse CP가 고른 배정을 **보존** |
| `active` | `build_active_from_reference` (schedule_build.py:186) | 버리고 **earliest-start로 재배정** |

두 경로 모두 마지막에 `insert_idle_time`(ffc_schedule.py:1648)을 호출한다. 그런데
`insert_idle_time`은:

- **마지막 stage의 각 machine 시퀀스만** 훑으며, machine 배정도 job 순서도 **바꾸지 않고**,
- `Σ_{S_E} w⁻ > Σ_{S_T} w⁺`일 때만(**이익이 있을 때만**) 블록을 **오른쪽으로만** 이동하며
  (`_iit_pan_shift`, ffc_schedule.py:1744),
- 이동 폭은 `delta2` = **같은 machine의 다음 op까지 간격**으로 제한된다.

따라서 `insert_idle_time`은 **고정된 배정 안에서의 약한 right-shift**일 뿐이며, 나쁜
machine 배정을 되돌릴 수 없다. wET는 **마지막 stage completion에만** 걸리므로,
가설은:

> **earliness 폭증은 마지막 stage의 machine 재배정 때문이다.** active가 마지막 stage에서
> job들을 촘촘히 packing하면 completion이 due window 하한보다 앞서고,
> right-shift-only인 `insert_idle_time`은 이를 회복하지 못한다.

증거: Case B(k=1, `factor=1`, coarsening 없음)에서 un-coarsen할 게 없는데도 active가
coarse 94,883 → restored 289,915로 부풀렸다 — 순수하게 재배정 효과다.

### 1.1 효과 분해 — active가 semi 대비 도입하는 두 효과

- **(a) 마지막 stage 재배정** — ET-bearing stage에서 `insert_idle_time`의 자유도를 바꿈.
- **(b) 이전 stage active packing** — 마지막 stage **도착시각**을 앞당겨 completion에 바닥을 깖.

**Part A의 새 모드는 (a)를 제거하고 (b)만 남긴다.** 이 모드가 ET 손실을 대부분 회복하면
(a) 지배(=가설 확정), 회복 못 하면 (b) 지배.

---

## 2. Part A — 새 reconstruct_mode `active_but_last_semi`

### 2.1 규칙 (stage별 전략)

front-to-back 한 번의 sweep으로:

1. **마지막 stage를 제외한 모든 stage**: active dispatch — 입력의 per-stage 시작순서 보존,
   `get_due2_weight_pos_job_sequence()` tie-break, earliest-start machine 재배정
   (`build_active_from_reference`와 동일 규칙).
2. **마지막 stage**: coarse machine 배정 + per-machine job 순서를 **그대로 보존**하고,
   시각만 `start = max(prev_active_stage_end[j], machine_end)`로 재계산
   (`reconstruct_raw_coarse_schedule`의 마지막-stage 스텝과 동일하되, prev-stage end는
   **active로 재구성된** 이전 stage에서 읽음).
3. 이후 `insert_idle_time`(원본 scale) 적용.

### 2.2 변경점 (3 지점, 모두 기존 패턴 복제)

**(1) `solution/schedule_build.py`** — 새 빌더 + 래퍼 한 쌍 추가 (기존 active 쌍과 대칭):

```python
def build_active_except_last_from_reference(reference, instance, stage_2_job_2_duration):
    # stages[:-1]: build_active_from_reference와 동일한 dispatch_stage_by_jobs
    # stages[-1]:  reference의 coarse machine 배정/순서 보존,
    #              prev end는 schedule.get_job_end_time(prev_stage, j)로 읽어 시각 재계산
    ...

def reconstruct_active_except_last_coarse_schedule(coarse_schedule, instance):
    schedule = build_active_except_last_from_reference(
        coarse_schedule, instance, instance.stage_2_job_2_p_map
    )
    schedule.insert_idle_time(instance.job_2_due_window_map,
                              instance.job_2_ewt_map, instance.job_2_twt_map)
    return schedule
```

**(2) `algorithm/coarsen_solve_reconstruct.py`**:
- `CoarsenSolveReconstructOption.reconstruct_mode` Literal에 `"active_but_last_semi"` 추가
  (line 176), `__post_init__`의 `valid_reconstruct` 집합 확장 (line 211).
- `run_coarsen_solve_reconstruct`의 분기(line 573-582)에 새 모드 케이스 추가.

**(3) `orchestration/controller.py`** — solve_flow candidate 루프(line 2957-2964)에 새 모드
분기 추가. winner 선정 로직(argmin restored_obj, line 2988)은 그대로.

### 2.3 가드
- 단일-stage 인스턴스에서는 `active_but_last_semi == semi_active`(마지막이 유일 stage).
  PRA2017 large는 모두 c≥5라 실무상 무관하나, 빌더는 `len(stages)==1`을 semi로 위임.

### 2.4 테스트 (`tests/solution/test_schedule_build.py`)
- **속성**: 결과가 feasible(overlap/precedence 위반 없음), 마지막 stage의 (machine 배정,
  per-machine 순서)가 입력 coarse와 동일, 이전 stage는 active(어떤 op도 재정렬/지연 없이는
  더 못 당김).
- **동치 케이스**: 2-stage 인스턴스에서 `active_but_last_semi`의 이전-stage 결과가
  `build_active_from_reference`의 stage 0과 일치.
- **회귀**: `reconstruct_mode` 검증 — 알 수 없는 값은 `ValueError`.
- **라우팅**: controller solve_flow에서 새 모드가 새 빌더로 분기되는지(mock/spy).

---

## 3. Part B — coarse schedule 저장 + standalone replay 하베스트

### 3.1 왜 coarse schedule 하나면 충분한가

reconstruction 함수들은 `(coarse_schedule, 원본 instance)`만 받고 **`factor`를 안 쓴다**
(schedule_build.py:133 `del factor`). coarse의 *시각*은 버려지고 assignment+순서만
쓰인다. 원본 instance는 벤치마크에서 이름으로 다시 로드한다. 따라서:

> **저장 대상은 coarse schedule JSON 하나.** coarsened instance/factor/coarsen_mode는
> replay에 불필요하다.

`FFcSchedule`은 이미 직렬화기가 있다: `io/schedule_json.py::dump_solution_json` /
`load_schedule_json`. coarse schedule은 원본과 동일한 job/stage/machine 레이아웃이라
그대로 덤프/로드된다.

### 3.2 덤프 훅 (controller.py:2945 candidate 루프)

dedup된 **모든** candidate의 `cand.coarse_schedule`을 저장한다 — winner 하나만 저장하면
다른 reconstruct 모드에서 다른 candidate가 이길 때 재현 불가.

- 위치: `progress/<instance>_csr_coarse_cand_<NN>_<source>.json`
  (`dump_solution_json(compact=True, instance_name=..., obj_value=cand.coarse_obj)`)
- candidate 수는 ≤5, 파일 크기 작음. 기존 `<instance>_csr_candidates.csv`(source·coarse_obj·
  restored_obj·valid)와 파일명 idx/source로 1:1 대응.
- artifact_layout + docs에 신규 아티팩트 등록(기존 csr_analysis 등록 방식 따름).
- 게이팅: 항상 켜면 full-grid에서 수천 개 파일. **config 플래그**(예: `dump_csr_coarse: true`,
  기본 false)로 실험 때만 활성화 — §5 결정 필요.

### 3.3 replay 스크립트 (`scripts/20260724/replay_reconstruction.py`)

- 입력: run_dir(시나리오 dir glob) + 모드 리스트 `{semi_active, active, active_but_last_semi}`.
- 각 instance마다: 원본 instance 로드(`BenchmarkLoader`/`FFcDDWParameters.from_pra_2017_data`),
  coarse candidate JSON들 로드(`load_schedule_json`).
- 각 모드에 대해 **모든 candidate를 reconstruct → E/T 계산 → argmin으로 winner 선정**
  (controller winner 로직 재현). reconstruct 함수는 구성상 total-feasible(schedule_build
  docstring)이라 feasibility는 assert만.
- 출력: `analysis/20260724_recon_replay/replay_et.csv`
  (`insIndex, mode, winner_source, E, T, obj`) + semi 기준 paired 델타 요약(dE/dT/dObj).

### 3.4 confound 주의 — replay가 답하는 질문

CSR flow는 cumulative라 downstream step을 **reconstructed incumbent로 seed**한다. 그래서
`reconstruct_mode`를 바꿔 **전체 재실행**하면 coarse candidate 자체가 달라진다(active/semi가
coarse_obj에서 갈라진 이유). **어떤 offline replay도 전체 재실행을 완벽 재현할 수 없다.**

- **replay가 답하는 것**: "동일한 coarse candidate에서 어느 reconstruction이 wET가 좋은가"
  = **reconstruction 품질을 궤적 발산과 분리**한 측정.
- **전체 재실행이 답하는 것**: 피드백 포함 end-to-end 성능.

→ replay로 빠르게 스크리닝하고, 유망한 모드만 전체 run으로 승격한다.

---

## 4. 실험 설계

### 4.1 Exp A — seed-only 마이크로 A/B (확정적, 노이즈 0)

`metadata/20260723/active_recon_csr_ab.yaml`(solve:false, factor∈{1,30}, ins 60–64)을
바탕으로 **새 config**를 만들어 `active_but_last_semi` 시나리오를 추가한다. seed-only라
factor 레벨 내에서 세 모드가 **동일한 coarse 입력**을 받으므로 per-instance obj 델타가 곧
순수 reconstruction 효과다. Part B 없이도 성립하는 즉시 검증.

### 4.2 Exp B — replay 스크린 (full grid)

full-run(solve:true) coarse schedule을 §3.2로 덤프한 뒤, 1440 그리드 전체에 세 모드를
offline replay(§3.3). CP 없이 초 단위. §1의 두 케이스(1088, 1089)를 먼저 스모크 확인.

### 4.3 판정 기준
- `active_but_last_semi`가 semi 대비 wET를 얼마나 회복하는가:
  - active와 semi 사이 손실(+29.19 pp)의 **대부분 회복** → 효과 (a) 지배 = **가설 확정**.
  - 회복 미미 → 효과 (b)(이전 stage packing) 지배 = 가설 반증, 대칭 모드
    `semi_but_last_active`로 (a) 단독 효과 추가 확인 검토.
- 성분 재확인: dE가 active 대비 크게 줄고 dT는 semi 수준으로 복귀하는지.

---

## 5. 새 config 사양 (Exp A)

- 경로: `metadata/20260724/active_but_last_semi_csr_ab.yaml` (신규,
  `metadata/20260724/csr_k_f_cumulative_recon_ab.yaml`를 복제)
- `ins_index: [60, 61, 62, 63, 64]`
- `output_dir: output/20260724_active_but_last_semi_csr_ab`
- `instance_worker_cnt: 5`
- 참조본 30개 시나리오(semi/active × k{1,2,4,8} × tl{05,10,15}) **유지** +
  lastsemi 4개 (`csr_k1_tl05_lastsemi`, `csr_k2_tl05_lastsemi`,
  `csr_k4_tl05_lastsemi`, `csr_k8_tl05_lastsemi`) → **factor×tl05별 3-way**,
  총 34 시나리오.
- 각 lastsemi는 대응하는 semi/active와 동일한 solve_flow + `reconstruct_mode:
  active_but_last_semi`.
- 실행: `main.py`의 CONFIG_PATH를 새 config로 갱신 → `uv run python main.py`.

---

## 6. 작업 순서

1. **(별도 대화) Part A 구현** — 빌더/래퍼 + 옵션 Literal + 3 분기 + 테스트. `ruff check`.
2. **(별도 대화) Part B 구현** — 덤프 훅(플래그 게이팅) + replay 스크립트 + artifact 등록.
3. **구현 완료 후 본 흐름**: `metadata/20260724/active_but_last_semi_csr_ab.yaml` 신규 생성
   → 실험 실행 → run setting 커밋 → 결과 분석 → `plans/analysis/20260724/`에 merged analysis.
4. 유망하면 Exp B(full-grid replay) → 필요 시 전체 run 승격.

---

## 7. 확인 필요 (Open Questions) — RESOLVED 2026-07-24

- **D1** → config 플래그(`dump_csr_coarse: true`, 기본 `false`)로 게이팅 확정.
- **D2** → 모드 이름 `active_but_last_semi` 확정.
- **D3** → 참조본 30개 시나리오 유지 + lastsemi 4개(k1/k2/k4/k8 tl05, 총 34), factor×TL별 3-way 확정.
- **D4** → 대칭 모드 `semi_but_last_active` YAGNI 보류 확정.
- **D5** → replay feasibility: 공유 헬퍼로 강제 (assert만으로 불충분) 확정.
