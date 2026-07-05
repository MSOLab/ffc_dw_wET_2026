# Runbook: partition gantt를 CP 전/후 2장으로 분리 (before_cp / after_cp)

**How to use:** executable plan. 승인 후 순서대로 진행. Main agent로 작업; **`git` 명령은 subagent에 위임 금지**.

> ⚠️ 본 plan은 **검토용**. 코드 변경(`src/ffc_ddw_sum_et/algorithm/sw_cp/`)을 포함하므로 승인 후 진행.

---

## 0. Goal

`sw_cp` step별 partition gantt를 지금처럼 **1장(CP 전)** 만 그리지 말고,
**CP 최적화 전/후 2장**으로 그린다:

- `2-sw_cpstep_NNN_partition_1_before_cp.svg` — CP solve **이전** (`rj_schedule` 기반)
- `2-sw_cpstep_NNN_partition_2_after_cp.svg` — CP solve **이후** (`cand` = 복원·semi-active·idle-insert된 해)

그리고 현재 branch(post-fix code)로 **before/after 그림을 다시 그려** `analysis/rj_rtf_vis/20260705/post/` 를 교체한다.

## 1. Background (왜)

- 현재 `sw_cp/dispatcher.py:183` 는 `rj_schedule`(=CP 전 reference schedule) **한 장만** 렌더 →
  partition 그림이 **최적화 전인지 후인지 구분 불가** (사용자 지적).
- CP 이후 해는 dispatcher.py:268-280 에 있음:
  `cand, _ = builder.build_full_schedule_from_cp(...)` → `cand.make_semi_active(...)`
  → `cand.insert_idle_time(...)`. **`status ∈ {OPTIMAL, FEASIBLE}` 일 때만** `cand` 존재
  (아니면 `cand is None`).
- `render_partition_gantt_svg(schedule, stage_2_partition, ...)` 는 `schedule` 인자의
  start/end map으로 바를 배치하고 색은 `stage_2_partition`(region)로 칠함
  → **before는 `rj_schedule`, after는 `cand`** 를 넘기면 동일 partition 위에 전/후 위치가 그려짐.

### Left-dummy 정의 (확인 완료)

`sw_cp/cp_model.py`:
- `left_boundary[i,k] = max(ltf_ends)` (그 machine의 LTF op 없으면 0) — L239.
- `left_boundary > 0` 이면 `l_dummy_{i}_{mc}` = 구간 `[0, left_boundary]` 생성 — L248-251.

→ **각 machine의 left dummy 오른쪽 끝 = 그 machine LTF operation들의 최대 end-time.**
post-fix에서 첫 LTF op(j04, j18) 앞의 idle 은 LTF가 우측정렬되지 않고 incumbent 위치에
남아 생긴 것 — 의도된 동작. (dummy=[0, j04_end], [0, j10_end] where j10은 j18·j15 뒤.)

## 2. Git state this plan assumes

- 현재 branch: `20260705_all_rj_schedule_vis`, HEAD = `a01b777` (post SVG 커밋).
  코드 상태 = post-fix (`delay_operations_latest_leq_obj_contrib`).
- `analysis/` 는 gitignore(`.gitignore:223`) → 아티팩트 커밋 시 `git add -f` 필요.
- 코드 변경은 이 branch에 `feat` 커밋으로 남긴다. (dev-line 이동/ push는 사용자 결정.)

## 3. 코드 변경 설계 (4개 파일)

**KISS: path getter에 phase suffix 인자 1개만 추가.** region/색/축 로직은 그대로.

1. **`sw_cp/option.py`** (L87):
   `debug_partition_gantt_path_getter: Callable[[int], Path | None] | None`
   → `Callable[[int, str], Path | None] | None`.
   docstring 도 `(step_idx, phase)` 로 갱신.

2. **`orchestration/controller.py`** (L2359-2365):
   ```python
   def _gantt_path(step_idx: int, phase: str) -> Path | None:
       p = self.try_get_file_path_for_subroutine(
           f"step_{step_idx:03d}_partition_{phase}.svg"
       )
       if p is not None:
           p.parent.mkdir(parents=True, exist_ok=True)
       return p
   ```

3. **`sw_cp/dispatcher.py`**:
   - 기존 render 블록(L177-199)을 **before** 로: `path_getter(step, "1_before_cp")`,
     `render_partition_gantt_svg(rj_schedule, ..., phase_label="before CP")`.
   - CP 이후(`cand` 생성 직후, L290 이후) **after** 블록 추가:
     `cand is not None` 이고 debug 조건 만족 시
     `path_getter(step, "2_after_cp")`,
     `render_partition_gantt_svg(cand, stage_2_partition, ..., phase_label="after CP")`.
     `cand is None`(비최적/infeasible)이면 after 생략(로그만).
   - before/after 공통 render 인자를 헬퍼 지역함수로 묶어 중복 최소화(DRY) — 선택.

4. **`sw_cp/visual.py`** (render 시그니처 + title, L39-49 / L257-262):
   - keyword-only `phase_label: str | None = None` 추가.
   - `title` 에 label 반영: `sw_cp  step=N  ... (before CP)` / `(after CP)`.
     → 파일명뿐 아니라 **그림 안에서도** 전/후 구분(사용자 요구 직접 해소).

## 4. Step-by-step

### 1. 코드 변경 (위 §3)
- 4개 파일 편집.
- `uv run ruff check` / `uv run ruff format`.

### 2. 재실행 (현재 post-fix code)
```sh
uv run python main.py --config metadata/20260705/rj_rtf_vis_compare.yaml
```
- 새 run dir 의 `progress/` 에 step별 **_1_before_cp.svg / _2_after_cp.svg** 쌍이
  생기는지 확인 (`find output/20260705 -iname "*partition_*_cp.svg"`).
- 각 step 당 2장(단, 해당 step이 비최적이면 after 없음 — 로그로 확인).

### 3. analysis/post 교체
- 기존 `analysis/rj_rtf_vis/20260705/post/2-sw_cpstep_00*_partition.svg`(단일본) **삭제**,
  새 before/after 쌍을 복사.
- `post/README.md` 갱신(새 파일명 규칙, before/after 의미).
- `compare.md` 갱신: step_002 before vs after 로 "CP가 무엇을 바꿨나"까지 비교.

### 4. 커밋 (git = main agent)
```sh
# 코드
git add src/ffc_ddw_sum_et/algorithm/sw_cp/option.py \
        src/ffc_ddw_sum_et/algorithm/sw_cp/dispatcher.py \
        src/ffc_ddw_sum_et/algorithm/sw_cp/visual.py \
        src/ffc_ddw_sum_et/orchestration/controller.py \
        plans/20260705/partition_gantt_before_after_cp.md
git commit -m "feat(sw-cp): split partition gantt into before/after CP"
# 아티팩트 (analysis/ 는 ignore → -f)
git add -f analysis/rj_rtf_vis/20260705/post analysis/rj_rtf_vis/20260705/compare.md
git commit -m "chore(sw-cp-vis): redraw post partition gantt before/after CP"
```

## 5. Success criteria

- step별로 `_1_before_cp.svg`, `_2_after_cp.svg` 두 파일이 생성됨(비최적 step 제외 after).
- 그림 title 에 "before CP"/"after CP" 라벨이 보임.
- `analysis/rj_rtf_vis/20260705/post/` 가 새 쌍으로 교체됨.
- 기존 단일 이미지 코드경로가 없어졌고 `ruff check` 통과.

## 6. Risks / 주의

- **cand is None**: status가 OPTIMAL/FEASIBLE 아니면 after 없음 → after 생략 + 로그. 정상.
- **path getter 시그니처 변경**: `Callable[[int], ...]` → `Callable[[int, str], ...]`.
  호출부는 dispatcher 2곳뿐이므로 영향 국소적. 다른 호출자 없음(확인: getter는 sw_cp 전용).
- **pre-fix(bbaf408) 재그리기**: 이 viz 코드는 bbaf408에 없음. pre 쪽 before/after가 필요하면
  별도 요청 시 진행(코드 변경을 pre-fix 위에 얹어 재실행). 본 plan 범위 밖.
- **branch 성격**: 이 branch는 원래 분석-trace 목적. src feat 커밋을 얹는 게 부담되면
  나중에 dev-line으로 cherry-pick 이동 가능(사용자 결정).

---

# v2 (2026-07-05 후속): 3단계 분할 + render 누락-바 버그 수정

> ⚠️ v1(commit `04a02c5`+`ceed235`)은 완료됨. 아래는 사용자 피드백에 따른 후속.
> 코드 변경 포함 → **검토 후 진행**.

## v2.0 사용자 피드백 (관측 3건)

1. **버그(누락 바):** `..._2_after_cp.svg` step_000의 첫 stage에 op 20개가 그려져야 하는데
   2개만 그려짐. **실측:** step_000 before=op-bar 32/label 40, after=16/21 — 약 절반 누락.
2. **진단 가설:** before의 stage2 RTF 경계는 400 직전 1 / 직후 1 인데, after는 RTF op 둘 다
   400 이후. → CP 해 자체가 아니라 **`make_semi_active` + `insert_idle_time`** 후처리에서
   밀린 것으로 의심. 확인하려면 후처리 **전/후**를 분리해서 봐야 함.
3. post-fix의 before/after 그림 재생성 필요.

## v2.1 버그 원인 (코드로 확정)

- `cp_model.build_full_schedule_from_cp` (cp_model.py:438-456): LTF는 incumbent 위치
  유지하지만, **non-time-fixed·RTF op은 machine을 재배정**하며 replay
  (Phase A greedy machine select / Phase B target-machine matching — 그 docstring 참조).
- `visual.render_partition_gantt_svg` (visual.py:118-134): op 바를 partition region의
  `(job, k)` 로 **incumbent machine `k`** 를 써서 `start_map.get((job, s_id, k))` 조회.
  → CP가 machine을 바꾼 op은 조회 miss → **바 누락**. (step_000 stage i0엔 LTF가 없어
  대부분 재배정 대상이라 누락이 큼.)
- **결론:** after 스케줄(cand)을 그리려면 op 바를 **incumbent machine이 아니라 렌더 대상
  스케줄의 실제 machine lane** 에 배치해야 함. region 색은 machine 무관하게 `(job)→region`
  으로 칠함.

## v2.2 코드 변경 설계

### (A) `visual.py` — op 바를 실제 스케줄 machine에 배치 (버그 수정)
- 현재: region_ops `(job, k)` 를 돌며 `k`(incumbent)로 조회 → 재배정 시 miss.
- 변경: stage별 `job → region_name` 맵을 partition에서 구성(`k` 무시; 한 job은 stage당
  한 region). 그 다음 **렌더 대상 스케줄의 실제 op** `(job, s_id, mc)→start/end` 를 돌며,
  그 job이 해당 stage의 어느 region이면 그 색으로 lane `(s_id, mc)` 에 바를 그림.
  region에 없는 op은 skip(=기존 before와 동일 집합 유지). → before는 결과 불변(machine 일치),
  after는 실제 위치로 바가 모두 나옴.
- **경계선(dummy boundary)** 기준: 좌/우 경계선은 CP가 푼 **고정 제약**(rj_schedule의 LTF end /
  RTF start)이므로, after/sm_iti 그림에서도 **rj_schedule 기준으로 고정**해서 그린다.
  → render에 `ref_schedule: FFcSchedule | None = None` 추가(None이면 `schedule` 사용=기존 before).
  경계선 계산(visual.py:107-134)만 `ref_schedule` 사용, op 바는 `schedule` 사용.
  이로써 "400 경계선"이 세 그림에서 같은 자리에 있어 RTF가 상대적으로 얼마나 밀렸는지 눈으로 비교 가능.

### (B) `dispatcher.py` — 3단계로 분리
현재 after 1장(sm+iti 후) → **3장**:
- `1_before_cp` : `rj_schedule` (기존).
- `2_after_cp`  : `build_full_schedule_from_cp` 직후 **raw CP 해** (sm+iti 이전).
- `3_after_sm_iti` : `make_semi_active`+`insert_idle_time` 후 (= 기존 `2_after_cp` 내용).

`make_semi_active`/`insert_idle_time` 는 cand를 **in-place 변형**하므로,
`build_full_schedule_from_cp` 직후 `cand_raw = cand.deepcopy()` 로 raw 상태를 잡아둔다
(debug_gantt일 때만). 렌더 3콜 모두 `ref_schedule=rj_schedule` 전달.
- 루프 상단에 `cand_raw = None` 초기화, `if status in (OPTIMAL,FEASIBLE):` 안에서 set.
- after 렌더 블록: `if debug_gantt and cand is not None:` 에서 `cand_raw`("2_after_cp") +
  `cand`("3_after_sm_iti") 두 번 write. `cand_raw` 는 not-None 보장(cand와 동시 생성).

### (C) `_write_partition_gantt` / phase label
- `phase` 인자에 `"2_after_cp"`, `"3_after_sm_iti"` 추가 사용(파일명 규칙 그대로).
- label: "after CP (raw)", "after CP + semi-active/idle".
- controller `_gantt_path(step, phase)` 는 임의 phase 문자열 지원 → **변경 불필요**.

## v2.3 재생성 & 커밋

1. 코드 변경 (A~C) → `ruff check`/`format` → smoke import.
2. **post-fix 재실행**(현재 코드): `uv run python main.py --config metadata/20260705/rj_rtf_vis_compare.yaml`.
   - step별 3장(`_1_before_cp`,`_2_after_cp`,`_3_after_sm_iti`) 생성 확인
     (`find output/20260705 -iname "*partition_*.svg"`).
3. `analysis/rj_rtf_vis/20260705/post/` 교체: 기존 2장짜리(`_1_before_cp`,`_2_after_cp`=구 sm_iti) 삭제,
   새 3장 세트 복사. README/compare.md 갱신
   (**핵심 확인**: 2_after_cp에서 RTF가 경계선 전/후로 올바른지, 3_after_sm_iti에서 밀렸는지 — 사용자 가설 검증).
4. 커밋(git=main agent):
   ```sh
   git add src/ffc_ddw_sum_et/algorithm/sw_cp/dispatcher.py \
           src/ffc_ddw_sum_et/algorithm/sw_cp/visual.py \
           plans/20260705/partition_gantt_before_after_cp.md
   git commit -m "fix(sw-cp): draw gantt bars by actual machine; add raw-CP phase"
   git add -f analysis/rj_rtf_vis/20260705/post analysis/rj_rtf_vis/20260705/compare.md
   git commit -m "chore(sw-cp-vis): redraw post gantt in 3 phases (before/after-cp/sm-iti)"
   ```

## v2.4 결정 (사용자 확정 2026-07-05)

- **[Q1] → pre 도 3단계로 재생성.** `pre/`, `post/` 모두 3단계 세트로.
  - `post/` : 현재 HEAD(post-fix rj-build + v2 viz)로 재실행.
  - `pre/` : v2 viz 는 유지하되 **rj-build 만 pre-fix 버전**(`delay_job_latest_leq_obj_contrib_all_stages`)
    으로 돌려야 함. 방법: **post 재생성·커밋을 먼저** 끝낸 뒤, `dispatcher.py`의 rj-build 블록만
    working-tree에서 임시로 pre-fix 버전으로 바꿔 실행 → `pre/` 복사 → `git checkout` 으로 복원
    (임시 편집은 커밋 안 함). `pre/README.md`에 "pre-fix rj-build + v2 viz (working-tree 임시)" 명시.
    실행 순서상 post-fix 코드가 항상 커밋된 HEAD 상태로 남음.
- **[Q2] → rj_schedule 고정 경계선.** 세 그림 모두 rj_schedule 기준 경계선(§v2.2-A대로).

## v2.5 Risks

- **visual.py 리팩터 영향:** op 바 선택 로직을 "partition→schedule lookup"에서
  "schedule op→region color"로 바꾸므로 before 그림도 재검증 필요(machine 일치 시 동일해야 함).
  회귀 확인: 재생성한 `_1_before_cp` 를 기존 커밋본과 (ID noise 제외) 기하 비교.
- **deepcopy 비용:** step당 cand 1회 추가 deepcopy(debug_gantt일 때만) — 무시 가능.
- **한 job이 stage당 2 region?** partition 불변식상 없음(가정). 구성 시 중복이면 assert.
