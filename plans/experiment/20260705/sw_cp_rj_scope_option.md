# Runbook: sw_cp rj right-justify **scope 옵션** + 3단계 partition gantt

**How to use:** executable plan. 새 대화에서 "execute this plan" 하면 순서대로 진행.
**Self-contained** — 이전 대화 맥락 없이도 실행 가능하도록 코드 참조·경위·현재 상태를 모두 담음.
Main agent가 git 담당; **`git` 명령은 subagent에 위임 금지**(공유 worktree 오염 방지). 코드 편집/run은
sonnet subagent 활용 가능(git 미사용 조건).

> ⚠️ 코드 변경 포함. **검토 후 진행.** 승인 전에는 읽기만.

---

## 0. Goal (한 줄)

`sw_cp` / `incremental_sw_cp` 에 **`rj_right_justify_scope`** 옵션을 추가해, reference schedule
(`rj_schedule`) 빌드 시 right-justify를 **전체 operation(`all_ops`)** 로 할지 **RTF op만(`rtf_only`)**
할지 **config에서 선택**한다. → **commit을 바꿔가며 그림 그릴 필요 없이, 하나의 yaml에 두 scenario
(scope=all_ops / scope=rtf_only)를 넣어 한 번의 run으로 두 결과를 비교**한다.
겸사겸사 partition gantt를 **CP 전/후 3단계**로 그리고, **render 누락-바 버그**를 고친다.

**옵션 기본값: `"rtf_only"`** (현재 코드 동작 = 무지정 시 지금과 동일).

## 1. 배경 & 경위 (왜 이 plan이 있는가)

- `SwCpDispatcher`(`src/ffc_ddw_sum_et/algorithm/sw_cp/dispatcher.py`)는 sliding window마다
  incumbent를 deepcopy해 `rj_schedule`(reference schedule)을 만들고, 거기서 파생한 per-machine
  left/right boundary를 CP 빌더에 넘긴다.
- `rj_schedule` 빌드에 두 가지 right-justify 방식이 있다 (둘 다 objective 비증가):
  - **`all_ops`** = `rj_schedule.delay_job_latest_leq_obj_contrib_all_stages(instance.job_2_dw_ub_map)`
    — **모든 stage의 모든 op**을 우측 정렬. (구 "pre-fix" = commit `bbaf408` 동작.)
    정의: `ffc_schedule.py:1499`.
  - **`rtf_only`** = `rj_schedule.delay_operations_latest_leq_obj_contrib(rtf_ops, instance.job_2_dw_ub_map)`
    — **RTF(right-time-fixed) op만** 우측 정렬, 나머지는 incumbent 위치 고정.
    (현재 "post-fix" = commit `d7ebc1f` 동작.) 정의: `ffc_schedule.py:1533`.
- 지금까지는 이 둘을 **commit을 갈아끼워(`bbaf408` vs `d7ebc1f`)** 비교했다. 매우 번거로움.
  → **옵션 하나로 런타임 선택 가능하게** 만드는 게 이 plan의 핵심(Part A).
- 부수적으로, partition gantt 디버그 그림 관련 후속 요구가 쌓였다(Part B/C/D).

### 이 branch에서 이미 끝난 것 (v1 — 새 대화가 중복 구현하지 말 것)

현재 branch `20260705_all_rj_schedule_vis`, HEAD `ceed235`. 이미 반영됨:
- partition gantt path getter가 **`(step_idx, phase)`** 시그니처
  (`option.py:87` `debug_partition_gantt_path_getter: Callable[[int, str], Path | None]`,
  controller `_gantt_path(step_idx, phase)` → 파일명 `step_{idx:03d}_partition_{phase}.svg`).
- `visual.render_partition_gantt_svg(..., phase_label: str | None = None)` — title에
  `(...)` 라벨 append.
- dispatcher가 **2단계**로 그림: `1_before_cp`(rj_schedule) + `2_after_cp`(cand = CP 해에
  make_semi_active+insert_idle_time 적용 후). module-level helper `_write_partition_gantt(...)`.
- **현재 dispatcher rj-build = `rtf_only` 고정**(옵션 없음). 이걸 옵션화하는 게 Part A.

### 미해결 (이 plan이 처리) — 사용자 관측/요구

1. **[버그] render 누락 바:** `..._2_after_cp.svg` step_000 첫 stage에 op 20개가 나와야 하는데
   2개만. **실측:** step_000 `1_before_cp`=op-bar 32/label 40, `2_after_cp`=16/21 (약 절반 누락).
   **원인 확정(코드):** `cp_model.build_full_schedule_from_cp`(`cp_model.py:438-456`)는 LTF는 incumbent
   위치 유지하되 **non-time-fixed·RTF op은 machine을 재배정**해 replay한다.
   그런데 `visual.render_partition_gantt_svg`(`visual.py:118-134`)는 op 바를 partition region의
   `(job, k)` 에서 **incumbent machine `k`** 로 `start_map.get((job, s_id, k))` 조회 → CP가
   machine 바꾼 op은 miss → **바 누락**. (step_000 stage i0엔 step<2라 LTF가 없어 대부분 재배정 대상.)
2. **[진단 세분화] CP 후처리 분리:** before의 stage2 RTF 경계는 400 직전 1/직후 1인데, `2_after_cp`
   (=현재 make_semi_active+insert_idle_time 적용본)에서는 RTF 둘 다 400 이후. 가설: **CP 해 자체가
   아니라 `make_semi_active`+`insert_idle_time`(dispatcher.py:275-280)에서 밀린 것**. 확인하려면
   후처리 **전/후**를 분리해서 봐야 함 → **3단계**로 확장(Part B):
   - `1_before_cp` = `rj_schedule`.
   - `2_after_cp` = `build_full_schedule_from_cp` 직후 **raw CP 해** (sm+iti 이전). ← 신규.
   - `3_after_sm_iti` = `make_semi_active`+`insert_idle_time` 후 (= 현재 `2_after_cp` 내용).
3. **[left dummy 정의 — 확인 완료, 참고]** `cp_model.py:239,248-251`:
   `left_boundary[i,k] = max(그 machine LTF op들의 end)`, `left_boundary>0`이면 `l_dummy=[0,left_boundary]`.
   즉 각 machine의 left dummy 오른쪽 끝 = 그 machine LTF op들의 최대 end-time. (post-fix에서 첫 LTF
   앞 idle은 LTF를 우측정렬 안 하고 incumbent에 둔 의도된 결과.)

### 사용자 결정사항 (확정)

- 새 옵션 **기본값 = `"rtf_only"`**.
- gantt **경계선(dummy boundary)은 세 그림 모두 `rj_schedule` 기준으로 고정**해서 그림
  (RTF가 고정 경계 대비 얼마나 밀렸는지 눈으로 비교 가능하게 — 가설 검증 직결).
- **pre/post 양쪽 다** 3단계로 비교(이제 commit 교체 대신 **scope 옵션 두 scenario**로).

---

## 2. 현재 git 상태 (새 대화 시작점)

- branch: `20260705_all_rj_schedule_vis`, HEAD = `ceed235`.
- 커밋 이력(이 branch, `bbaf408` 이후):
  ```
  ceed235 chore(sw-cp-vis): redraw post partition gantt before/after CP
  04a02c5 feat(sw-cp): split partition gantt into before/after CP        (= v1)
  a01b777 chore(sw-cp-vis): post-fix partition gantt SVGs
  13736e9 fix(sw-cp): right-justify only RTF ops when building rj_schedule (= d7ebc1f cherry-pick)
  436c25a chore(sw-cp-vis): pre-fix partition gantt SVGs
  bbaf408 refactor(sw-cp): rename pw_cp algorithm to sw_cp                (branch base)
  ```
- `analysis/` 는 **gitignore**(`.gitignore:223` = `analysis/`) → 아티팩트 커밋 시 **`git add -f`** 필요.
  `metadata/`, `plans/`, `src/` 는 ignore 아님.
- 원 개발 branch `20260703_lexico_partial_cp`(HEAD `622314a`)는 **건드리지 않는다**.
- **주의:** 이 plan 작성 시 `plans/experiment/20260705/partition_gantt_before_after_cp.md`(v1/v2 진단 기록)와
  본 파일이 uncommitted일 수 있음. 새 대화 시작 시 `git status`로 확인하고, 필요하면 먼저 docs 커밋.
- **branch 성격 결정 필요:** 이 branch는 원래 분석-trace 목적. 이번엔 실질 `feat`(옵션) 코드가 들어감.
  dev-line으로 옮길지, 이 branch에 계속 쌓을지는 실행 초기에 사용자에게 확인(§Step 0).

---

## 3. 코드 변경 설계

### Part A — `rj_right_justify_scope` 옵션 (핵심)

**A1. `src/ffc_ddw_sum_et/algorithm/sw_cp/option.py` (SwCpOption)**
새 필드 추가:
```python
rj_right_justify_scope: Literal["rtf_only", "all_ops"] = "rtf_only"
"""reference schedule(rj_schedule) 빌드 시 right-justify 범위.
``"rtf_only"``(기본): RTF op만 우측정렬(delay_operations_latest_leq_obj_contrib).
``"all_ops"``: 모든 stage의 모든 op 우측정렬(delay_job_latest_leq_obj_contrib_all_stages)."""
```
`__post_init__`에 값 검증(`{"rtf_only","all_ops"}`) 추가. `Literal` import 확인.

**A2. `src/ffc_ddw_sum_et/algorithm/sw_cp/dispatcher.py` (rj-build 분기)**
현재 (대략 `dispatcher.py:156-164`):
```python
rtf_ops = {
    (j, i, k)
    for i, part in stage_2_partition.items()
    for j, k in part.right_time_fixed
}
rj_schedule = incumbent.deepcopy()
rj_schedule.delay_operations_latest_leq_obj_contrib(
    rtf_ops, instance.job_2_dw_ub_map
)
```
→ 옵션 분기로:
```python
rj_schedule = incumbent.deepcopy()
if option.rj_right_justify_scope == "all_ops":
    rj_schedule.delay_job_latest_leq_obj_contrib_all_stages(
        instance.job_2_dw_ub_map
    )
else:  # "rtf_only"
    rtf_ops = {
        (j, i, k)
        for i, part in stage_2_partition.items()
        for j, k in part.right_time_fixed
    }
    rj_schedule.delay_operations_latest_leq_obj_contrib(
        rtf_ops, instance.job_2_dw_ub_map
    )
```
그 아래 `rj_obj <= inc_obj + 1e-6` assertion(`dispatcher.py:166-175`)은 **양쪽 다 objective
비증가라서 유지**(all_ops도 obj-non-increasing).

**A3. `src/ffc_ddw_sum_et/orchestration/controller.py` (두 step 메서드에 파라미터 노출)**
- `sw_cp(...)`(`controller.py:2267`) 시그니처에 `rj_right_justify_scope: Literal["rtf_only","all_ops"] = "rtf_only"` 추가,
  `SwCpOption(...)` 생성(`controller.py:2360`)에 `rj_right_justify_scope=rj_right_justify_scope` 전달.
- `incremental_sw_cp(...)`(`controller.py:2431`) 시그니처에도 동일 파라미터 추가, `base_kwargs`(dict,
  `controller.py:2518~`)에 `rj_right_justify_scope=rj_right_justify_scope` 추가(그대로 `self.sw_cp`로 전달됨).
- `Literal` import 확인(controller에 이미 사용 중 — `increment_unfixed_batch_count_flag`가 Literal).

→ 이로써 config yaml의 `subroutine_flow` 항목에 `rj_right_justify_scope: "all_ops"` / `"rtf_only"`를
넣어 **scenario마다 다르게** 줄 수 있음.

### Part B — partition gantt 3단계 (`dispatcher.py`)

현재 after 1장(sm+iti 후) → **3장**:
- `1_before_cp` : `rj_schedule` (기존).
- `2_after_cp`  : `build_full_schedule_from_cp` **직후 raw CP 해** (sm+iti 이전). ← 신규.
- `3_after_sm_iti` : `make_semi_active`+`insert_idle_time` 후 (= 기존 `2_after_cp` 내용).

`make_semi_active`/`insert_idle_time`는 cand **in-place 변형**이므로,
`build_full_schedule_from_cp` 직후 `cand_raw = cand.deepcopy()`로 raw를 잡아둠(**debug_gantt일 때만**).
- 루프 상단에 `cand_raw = None` 초기화, `if status in (OPTIMAL, FEASIBLE):` 안에서
  `cand, cp_divergence_count = builder.build_full_schedule_from_cp(...)` 직후에 set.
- after 렌더 블록(현재 `if debug_gantt and cand is not None:`):
  `_write_partition_gantt(cand_raw, ..., "2_after_cp", "after CP (raw)", ...)` +
  `_write_partition_gantt(cand, ..., "3_after_sm_iti", "after CP + semi-active/idle", ...)`.
- controller `_gantt_path(step, phase)`는 임의 phase 문자열 지원 → **controller 변경 불필요**.

### Part C — render 누락-바 버그 수정 (`visual.py`)

op 바를 **incumbent machine이 아니라 렌더 대상 스케줄의 실제 machine lane**에 배치:
- stage별 `job → region_name` 맵을 partition에서 구성(`k` 무시; 한 job은 stage당 한 region).
  (partition region 리스트는 `(job, k)` iterable — `visual.py:118-132` 참고.)
- 렌더 대상 스케줄의 **실제 op** `(job, s_id, mc) → start/end`(`schedule.get_jik_2_start_time_map()`)를
  돌며, 그 job이 해당 stage의 region이면 그 색으로 lane `(s_id, mc)`에 바를 그림. region 밖 op은 skip
  (=기존 before 집합 유지). → before(rj_schedule)는 결과 불변(machine 일치), after는 실제 위치로 전부 나옴.

### Part D — 경계선을 rj_schedule 기준 고정 (`visual.py` + `dispatcher.py`)

- `render_partition_gantt_svg`에 keyword `ref_schedule: FFcSchedule | None = None` 추가.
  경계선(left_b/right_b, `visual.py:107-134`) 계산만 `ref_schedule`(None이면 `schedule`) 사용,
  op 바는 `schedule` 사용.
- dispatcher의 3개 렌더 콜 모두 `ref_schedule=rj_schedule` 전달 → 세 그림 경계선이 같은 자리(예: 400)에
  고정 → RTF가 상대적으로 얼마나 밀렸는지 눈으로 비교.
- `_write_partition_gantt` 헬퍼에 `ref_schedule` 인자 하나 추가.

---

## 4. 비교 config (single-run, 두 scenario) — Part E

**신규 파일:** `metadata/20260705/rj_scope_compare.yaml`. 한 번 run으로 all_ops vs rtf_only 두 결과.
동일 인스턴스·시드에서 **오직 `rj_right_justify_scope`만** 다르게:

```yaml
run_mode: FULL_RUN
benchmark_dir: benchmarks/PRA2017/small
output_dir: output/20260705
instance_worker_cnt: 1
draw_gantt: false
scenarios:
  - name: rj_all_ops
    timelimit: 300.0
    output_subdir: rj_all_ops
    subroutine_flow:
      - method: calc_mcf_lb_and_derive_full_sch
        draw_pmtn_sch_heatmap: false
        job_placement_priority: "end_time"
        last_stage_only_placement_criteria: "dist"
        adjust_p: false
        adjust_r: false
      - method: sw_cp
        rj_right_justify_scope: "all_ops"     # ← pre-fix 동작
        solver_thread_cnt: 1
        batch_size: "m"
        step_size: 2
        unfixed_batch_count: 2
        left_profile_fixed_batch_count: 2
        right_profile_fixed_batch_count: 2
        pf_method: "PF1"
        cp_tl: 60.0
        batch_tl_mode: "constant"
        keep_step_schedules: true
        debug_partition_gantt: true
        debug_partition_gantt_max_steps: 3
  - name: rj_rtf_only
    timelimit: 300.0
    output_subdir: rj_rtf_only
    subroutine_flow:
      - method: calc_mcf_lb_and_derive_full_sch
        draw_pmtn_sch_heatmap: false
        job_placement_priority: "end_time"
        last_stage_only_placement_criteria: "dist"
        adjust_p: false
        adjust_r: false
      - method: sw_cp
        rj_right_justify_scope: "rtf_only"    # ← post-fix 동작(기본값이지만 명시)
        solver_thread_cnt: 1
        batch_size: "m"
        step_size: 2
        unfixed_batch_count: 2
        left_profile_fixed_batch_count: 2
        right_profile_fixed_batch_count: 2
        pf_method: "PF1"
        cp_tl: 60.0
        batch_tl_mode: "constant"
        keep_step_schedules: true
        debug_partition_gantt: true
        debug_partition_gantt_max_steps: 3
```

두 scenario는 각각 자기 `output_subdir` 아래 `progress/`에 step별 **3장**
(`2-sw_cp step_00N_partition_{1_before_cp,2_after_cp,3_after_sm_iti}.svg`)을 낸다.
(실제 파일명 접두사는 `2-sw_cp` — `try_get_file_path_for_subroutine`가 붙임.)

---

## 5. Step-by-step

### 0. 시작 점검
- `git status` / `git branch --show-current` (기대: `20260705_all_rj_schedule_vis` @ `ceed235`).
- uncommitted plan 파일 있으면 먼저 docs 커밋.
- **사용자에게 branch 방침 확인**: 이 옵션 `feat`을 이 branch에 쌓을지, dev-line으로 옮길지.

### 1. 코드 변경 (Part A~D)
- 편집: `option.py`, `dispatcher.py`, `controller.py`, `visual.py`.
- `uv run ruff check` / `uv run ruff format`.
- smoke import:
  `uv run python -c "from ffc_ddw_sum_et.algorithm.sw_cp.dispatcher import SwCpDispatcher; from ffc_ddw_sum_et.algorithm.sw_cp.visual import render_partition_gantt_svg; print('ok')"`
- (권장) sonnet subagent로 편집하되 **git 금지**, 끝나면 main이 `git diff` 검수.

### 2. config 작성 & 실행 (Part E)
- `metadata/20260705/rj_scope_compare.yaml` 작성(위 §4).
- `uv run python main.py --config metadata/20260705/rj_scope_compare.yaml`.
- 산출 확인: `find output/20260705 -iname "*partition_*.svg"` → scenario 2개 × step 3개 × phase 3개.

### 3. 회귀 확인 (중요)
- **Part C 영향:** op-바 선택 로직을 바꿨으니 `1_before_cp`(rj_schedule)가 이전과 **기하 동일**한지
  확인(matplotlib clip-path/marker ID noise 제외). 방법: 예전 커밋본 SVG와 rect/path x-coord 비교
  또는 op-bar/label 개수 대조.
- **버그 수정 검증:** 새 `2_after_cp`/`3_after_sm_iti`에서 첫 stage op 개수가 before와 동일 수준
  (누락 없음)인지.
- **가설 검증(핵심):** `rj_rtf_only` scenario step_002에서
  `2_after_cp`(raw CP)의 RTF가 경계선(rj 기준 고정) 전/후로 올바르게 있고,
  `3_after_sm_iti`에서 밀렸는지 → make_semi_active/insert_idle_time가 원인인지 확정.

### 4. 아티팩트 정리 & 문서
- 비교 결과 SVG를 `analysis/rj_rtf_vis/20260705/` 아래로 정리(예: `all_ops/`, `rtf_only/` 하위에
  step별 3장). 기존 `pre/`,`post/`(구 방식 산출)는 **유지하거나 superseded 표기** — 사용자와 상의.
- `compare.md` 갱신: (a) all_ops vs rtf_only 차이, (b) before→after_cp→after_sm_iti 3단계로 CP·후처리
  각각의 효과, (c) 버그 수정 전/후. 실행환경(OS, ortools 버전, uv.lock 존재) 명시.

### 5. 커밋 (git = main agent)
```sh
# 코드 + config + plan
git add src/ffc_ddw_sum_et/algorithm/sw_cp/option.py \
        src/ffc_ddw_sum_et/algorithm/sw_cp/dispatcher.py \
        src/ffc_ddw_sum_et/algorithm/sw_cp/visual.py \
        src/ffc_ddw_sum_et/orchestration/controller.py \
        metadata/20260705/rj_scope_compare.yaml \
        plans/experiment/20260705/sw_cp_rj_scope_option.md
git commit -m "feat(sw-cp): rj_right_justify_scope option + 3-phase gantt + machine-accurate bars"
# 아티팩트 (analysis/ 는 ignore → -f)
git add -f analysis/rj_rtf_vis/20260705
git commit -m "chore(sw-cp-vis): compare all_ops vs rtf_only via single config run"
```
- 커밋 직후 `git show --stat HEAD`로 의도한 경로만 들어갔는지 확인.

---

## 6. Success criteria

- config yaml **한 번 run**으로 `rj_all_ops`·`rj_rtf_only` 두 결과가 각자 output_subdir에 나옴
  (commit 교체 불필요).
- `sw_cp` 무지정 시 `rj_right_justify_scope="rtf_only"`(현재 동작 유지) — 회귀 없음.
- step별 3장(`1_before_cp`/`2_after_cp`/`3_after_sm_iti`) 생성(비최적 step은 after 2장 생략).
- **버그 수정:** after 그림에 첫 stage op 바가 누락 없이(=before와 동일 개수 수준) 그려짐.
- 세 그림의 좌/우 경계선이 rj_schedule 기준 동일 위치.
- `ruff check` 통과, before 그림 기하 회귀 없음.

## 7. Risks / 주의

- **`visual.py` 리팩터(Part C):** op-바 선택을 "partition→schedule lookup"에서 "schedule op→region
  color"로 바꿈 → before 그림 회귀 반드시 확인(Step 3).
- **한 job이 stage당 2 region?** partition 불변식상 없음(가정) — 맵 구성 시 중복이면 assert.
- **cand is None(비최적):** after 2장 생략 + 로그. 정상.
- **deepcopy 비용:** step당 cand 1회 추가 deepcopy(debug_gantt일 때만) — 무시 가능.
- **all_ops objective 불변식:** `delay_job_latest_leq_obj_contrib_all_stages`도 obj 비증가 →
  `rj_obj <= inc_obj` assertion 유지 가능(만약 경계에서 실패하면 tolerance/케이스 재확인).
- **analysis/ gitignore:** 아티팩트는 반드시 `git add -f`.
- **subagent git 금지:** run/편집은 위임 가능하되 git은 main만.

## 8. Appendix — 핵심 코드 위치 요약

| 대상 | 파일:줄 | 비고 |
|---|---|---|
| rj-build 분기 지점 | `algorithm/sw_cp/dispatcher.py:156-175` | Part A2 / rj_obj assert |
| after cand 생성 | `algorithm/sw_cp/dispatcher.py:264-290` | Part B (raw=build 직후, sm+iti=275-280) |
| 기존 gantt helper/콜 | `algorithm/sw_cp/dispatcher.py` (`_write_partition_gantt`, debug_gantt) | v1 |
| SwCpOption 필드 | `algorithm/sw_cp/option.py` (getter=L87) | Part A1 |
| render 함수/경계선/op바 | `algorithm/sw_cp/visual.py:39,107-134,118-132,257-263` | Part C/D |
| build_full_schedule_from_cp | `algorithm/sw_cp/cp_model.py:399-456` | machine 재배정 근거 |
| left_boundary/l_dummy | `algorithm/sw_cp/cp_model.py:239,248-251` | left dummy 정의 |
| 두 right-justify 메서드 | `solution/ffc_schedule.py:1499, 1533` | all_ops / rtf_only |
| controller sw_cp / SwCpOption() | `orchestration/controller.py:2267, 2360` | Part A3 |
| controller incremental_sw_cp / base_kwargs | `orchestration/controller.py:2431, 2518` | Part A3 |
| analysis gitignore | `.gitignore:223` | `git add -f` 필요 |
