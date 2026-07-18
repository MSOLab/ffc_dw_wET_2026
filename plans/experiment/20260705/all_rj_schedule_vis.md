# Runbook: rj_schedule (RTF-only right-justify) 시각 비교 — pre-fix vs post-fix

**How to use:** executable plan. 사용자가 "execute this plan" 이라고 하면 순서대로 진행. Self-contained — 이전 대화 맥락 없이도 실행 가능. Main agent로 작업; **`git` 명령은 subagent에 위임 금지** (공유 worktree에서의 잘못된 checkout이 미커밋 작업을 날릴 수 있음).

> ⚠️ 본 plan은 **검토용**. 승인 전까지는 읽기만 한다. 승인 후 Step 1부터 진행.

---

## 0. Goal (한 줄)

`d7ebc1f`("fix(sw-cp): right-justify only RTF ops when building rj_schedule")가 `rj_schedule` 빌드를 어떻게 바꾸는지 **partition-gantt SVG** 로 시각 비교하고, pre/post 양쪽 SVG를 **branch `20260705_all_rj_schedule_vis`에 커밋**해 흔적을 남긴다.

## 1. Background (왜)

- `SwCpDispatcher`는 각 window마다 reference schedule `rj_schedule`을 빌드해 CP 빌더가 읽는 left/right boundary의 출처로 쓴다 (`sw_cp/dispatcher.py` rj build block).
- **Pre-fix** (`bbaf408`): `delay_job_latest_leq_obj_contrib_all_stages` 로 **모든 op** 를 우측 정렬 → LTF까지 우측으로 밀림 → CP 빌더가 파생하는 `left_boundary`(LTF end)가 커지고 non-time-fixed 가용 window가 좁아짐 (over-constrain).
- **Post-fix** (`d7ebc1f`): `delay_operations_latest_leq_obj_contrib(rtf_ops, …)` 로 **RTF op만** `d_upper` 한계 내에서 밀고 LTF/unfixed/non-time-fixed는 incumbent 위치 고정 → `left_boundary` 그대로, `right_boundary` 확장.
- Gantt는 CP solve **이전**에 `rj_schedule` 기반으로 그려짐 (`dispatcher.py:178-196`). 따라서 CP 비결정성과 무관 — incumbent(mcf_lb seed)만 같으면 pre/post의 `rj_schedule` 차이가 곧 이 커밋의 순수 효과.
- **예상 시각 차이**:
  - LTF 블록: pre는 우측에 밀림 / post는 incumbent 위치 그대로.
  - left dummy 폭: pre > post (LTF end가 멂).
  - non-time-fixed 가용 window (LTF end ↔ RTF start): post > pre.

## 2. Git state this plan assumes

- 현재 branch: `20260703_lexico_partial_cp`, HEAD = `622314a`(lexico feature). `d7ebc1f`(rj fix)는 직조상 HEAD~^2.
- `origin/20260703_sw_cp_rename` HEAD = `bbaf408`(rename, pre-fix 기점) — `d7ebc1f`의 부모.
- `output/` 은 gitignore. **`analysis/` 도 gitignore됨** (`.gitignore:223` = `analysis/`) — 단, repo 관습상 특정 산출물은 `git add -f` 로 강제 추가해 tracked로 만든다(기존 `analysis/20260625/*.md` 등이 그 예). 따라서 SVG 흔적은 `analysis/` 아래에 두되 **`git add -f` 로 강제 스테이징 후 커밋**한다. (`metadata/`, `plans/` 는 ignore 아님 — 일반 add.)
- `debug_partition_gantt` 옵션은 `sw_cp` step method(`controller.py:2269`)에만 노출. 기존 debug config `metadata/20260512/pw_cp_debug_small.yaml` (`sw_cp` method, `debug_partition_gantt: true`)이 vehicle로 적합.

**Verify before starting** (어긋나면 중단/조정):

```sh
git branch --show-current                          # expect 20260703_lexico_partial_cp (이 계획서 작성 시점)
git rev-parse d7ebc1f^                             # expect bbaf408 (ff base; HEAD과는 무관)
git log --oneline -1 origin/20260703_sw_cp_rename  # expect bbaf408
git check-ignore output && echo "output ignored"   # must be ignored
git check-ignore -v analysis/rj_rtf_vis/x.svg      # analysis/ 는 ignore됨 → 아티팩트는 `git add -f` 필요
```

---

## 3. Branch & artifact layout 전략

**신규 branch:** `20260705_all_rj_schedule_vis`, **기점 `bbaf408`** (pre-fix).

- 이 branch의 HEAD는 pre-fix code 이므로 그대로 pre-fix gantt 실행 가능.
- pre-fix SVG를 커밋한 뒤, **`git cherry-pick d7ebc1f`** 로 fix 커밋의 diff를 pre 커밋 위에 얹으면 post-fix code가 되고, 같은 branch에서 post-fix gantt 실행 → post SVG 추가 커밋.
  - **왜 ff-merge가 아니라 cherry-pick인가:** pre 커밋(C1)을 `bbaf408` 위에 만드는 순간 branch HEAD가 `d7ebc1f`와 **분기**한다(C1과 d7ebc1f는 bbaf408의 형제). 이 상태에서 `git merge --ff-only d7ebc1f` 는 `fatal: Not possible to fast-forward, aborting.` 로 **반드시 실패**(scratch repo 재현 완료). C1은 analysis/config/plan만 추가하고 d7ebc1f는 dispatcher 코드만 바꾸므로 cherry-pick은 **충돌 없이** 적용된다.
- 결과: branch 하나에 pre → (picked fix) → post 흔적이 **세 개의 커밋**으로 순서대로 남음 (history 자체가 reproducible trace).

**Commit layout (3 commits on the new branch):**

1. `chore(sw-cp-vis): pre-fix partition gantt SVGs (all-stages right-justify)`
   - `analysis/rj_rtf_vis/20260705/pre/` (SVG 3개 + README)
   - `metadata/20260705/rj_rtf_vis_compare.yaml` (신규 비교 config — pre/post 공통)
   - `plans/experiment/20260705/all_rj_schedule_vis.md` (본 runbook 자체)
2. `fix(sw-cp): right-justify only RTF ops when building rj_schedule` (cherry-pick된 `d7ebc1f` — 메시지 그대로)
3. `chore(sw-cp-vis): post-fix partition gantt SVGs (RTF-only right-justify)`
   - `analysis/rj_rtf_vis/20260705/post/` (SVG 3개 + README)
   - `analysis/rj_rtf_vis/20260705/compare.md`

**Repo-tracked artifact dir:**

```txt
analysis/rj_rtf_vis/20260705/
  pre/
    step_000_partition.svg
    step_001_partition.svg
    step_002_partition.svg
    README.md            # 복사 실행 커맨드 + 소스 output 경로 메모
  post/
    step_000_partition.svg
    step_001_partition.svg
    step_002_partition.svg
    README.md
  compare.md            # side-by-side 비교 안내 + 예상/실제 차이 메모
```

`output/` 아래의 원본 SVG는 run 끝나고 위 경로로 **복사**(`cp`) 후 커밋. 원본은 gitignore라 커밋 안 됨.

## 4. Config — 신규 비교용 config 작성

**파일:** `metadata/20260705/rj_rtf_vis_compare.yaml` (신규, pre/post 양쪽에서 공통 사용).

`metadata/20260512/pw_cp_debug_small.yaml` 기반 (이미 `sw_cp` method + `debug_partition_gantt: true` + small benchmark 1개), 변경점:

- `output_dir: output/20260705`
- `output_subdir: compare_rj_rtf`
- `solver_thread_cnt: 1`  (CP 결정성 최소화 — Gantt는 CP 전 rj 기반이라 거의 안 바뀌지만, 안전상.)
- 충분한 시간제약: `timelimit: 300.0` (scene wall-clock) + `cp_tl: 60.0` (step당 CP). thread 1이라도 CP가 OPTIMAL 확정까지 충분히 탐색하도록 넉넉히.
- `debug_partition_gantt_max_steps: 3` 유지 (pre/post 동일 step 3개 비교).
- flow: `calc_mcf_lb_and_derive_full_sch → sw_cp` 만 (NEH-CP/flip 중간 단계 생략 — rj 시각 비교가 목적이므로 mcf_lb 시드 직후의 sw_cp가 가장 깨끗함).
  - `calc_mcf_lb_and_derive_full_sch` 옵션은 `pw_cp_debug_small.yaml` 의 것을 그대로 복사 (`adjust_p: false`, `adjust_r: false`).
- `batch_tl_mode: "constant"` 유지 (cp_tl은 위 항목대로 60.0).

**주의:** pre-fix branch(`bbaf408`)에는 이 신규 config가 **아직 없음**. config를 **메인 repo working tree에 미리 작성** 하고, 양쪽 실행 모두 `--config metadata/20260705/rj_rtf_vis_compare.yaml` 로 지정하면 pre-fix branch checkout 상태에서도 같은 파일 읽기 가능. config 파일은 **Step 4 pre-fix 커밋에 함께 포함**한다 (commit layout §3 — config·plan md·pre SVG가 한 커밋). pre run 시점에는 아직 untracked working-tree 파일로 존재하므로 실행에는 지장 없고, 커밋만 pre 커밋에서 처리한다.

---

## Step-by-step

### 1. 신규 config 작성 (현재 branch에서)

- `metadata/20260705/rj_rtf_vis_compare.yaml` 작성 (위 §4 spec).
- `metadata/` 는 tracked 이므로 현재 branch에 그대로 두면 됨 (별도 커밋 불필요 — branch 이동 후에도 working tree 파일로 남음; `git stash` 하지 않는 한).
- **주의:** 현재 branch에 staged/untracked 파일이 있으면(lexico WIP 등) branch 생성 전에 상태 확인. `git status --short` 로 확인.
- **⚠️ index 정리 (필수):** 이 계획서 파일(`plans/experiment/20260705/all_rj_schedule_vis.md`)이 staged(`A`) 상태다. staged 엔트리는 `git switch -c` 로 새 branch에 따라오지만, 본 runbook은 **Step 4 pre-fix 커밋의 pathspec에 명시적으로 포함**시킬 예정(commit layout §3). 그 외 staged 항목이 있다면 branch 생성 전에 정리:

  ```sh
  git status --short   # plan md 외 staged 항목이 없는지 확인 (있으면 마저 정리)
  ```

  (plan md 는 pre-fix 커밋 pathspec에 명시 추가하므로 별도 unstage 불필요.)

### 2. Branch 생성 (기점 bbaf408)

```sh
git switch -c 20260705_all_rj_schedule_vis bbaf408
```

- 이 시점 working tree는 pre-fix code. 신규 config 파일(`metadata/20260705/...`)은 untracked로 남음 (그대로 사용 가능).

### 3. Pre-fix gantt 실행

```sh
mkdir -p analysis/rj_rtf_vis/20260705/pre
uv run python main.py --config metadata/20260705/rj_rtf_vis_compare.yaml
```

- 완료 후 SVG 위치 확인:

  ```sh
  ls output/20260705/*/*/*compare_rj_rtf*/step_*_partition.svg
  ```

  (정확 경로는 run 로그의 "Run output directory:" 라인에서 확인.)
- 복사:

  ```sh
  cp <run_dir>/<ins>/<scenario>/step_*_partition.svg analysis/rj_rtf_vis/20260705/pre/
  ```

- `analysis/rj_rtf_vis/20260705/pre/README.md` 작성 (실행 커맨드, 원본 output 경로, bbaf408 SHA, config 경로).
- **검증**: SVG를 열어 LTF 블록이 우측으로 밀려있는지 (pre-fix 기대치) 육안 확인. 하나라도 없으면 중단.

### 4. Pre-fix SVG 커밋

```sh
git add -f analysis/rj_rtf_vis/20260705/pre          # analysis/ 는 gitignore → 강제 add 필수
git add metadata/20260705/rj_rtf_vis_compare.yaml \
        plans/experiment/20260705/all_rj_schedule_vis.md         # ignore 아님 — 일반 add
git status --short                                     # index에 위 3종만 있는지 확인
git commit -m "chore(sw-cp-vis): pre-fix partition gantt SVGs (all-stages right-justify)"
```

- `analysis/` 아티팩트는 `git add -f` 로만 스테이징됨(ignore 우회). config/plan md는 일반 add.
- 커밋 직전 `git status --short` 로 index에 의도한 3종(pre SVG + config + runbook)만 있는지 확인 — 다른 항목 섞임 방지.
- 신규 config(`metadata/20260705/...`)와 본 runbook(`plans/experiment/20260705/...`)도 이 커밋에 함께 포함 (commit layout §3).
- `output/` 원본은 gitignore라 자동 제외.
- 커밋 직후 `git show --stat HEAD` 로 의도한 경로(pre SVG + config + runbook)만 들어갔는지 확인.

### 5. Cherry-pick 으로 post-fix code 로 점프

```sh
git cherry-pick d7ebc1f
```

- **ff-merge 아님:** Step 4에서 pre 커밋이 `bbaf408` 위에 생겨 HEAD가 `d7ebc1f`와 분기했으므로 `git merge --ff-only d7ebc1f` 는 실패한다(§3 참조). cherry-pick으로 fix diff만 얹는다.
- C1(pre 커밋)은 analysis/config/plan만, `d7ebc1f`는 dispatcher 코드만 건드리므로 **충돌 없이** 적용됨. 만약 충돌이 나면 중단하고 원인 파악 (예상 밖 상황).
- 이 시점 working tree = post-fix code (`delay_operations_latest_leq_obj_contrib` 존재). `git show --stat HEAD` 로 dispatcher 변경이 얹혔는지 확인.
- 신규 config 파일(`metadata/20260705/...`)은 Step 4에서 이미 커밋됨 → working tree/tracked 로 그대로 존재.

### 6. Post-fix gantt 실행

```sh
mkdir -p analysis/rj_rtf_vis/20260705/post
uv run python main.py --config metadata/20260705/rj_rtf_vis_compare.yaml
```

- **pre 결과와의 충돌은 무해:** pre SVG는 Step 3~4에서 이미 `analysis/.../pre/` 로 복사·커밋된 뒤 이 run이 돈다. 따라서 post run이 `output/` 아래 pre run 산출물을 같은 경로로 덮어쓰더라도 pre 흔적은 영향 없음. (동일 `output_dir`+`output_subdir` 에서 run 디렉토리가 timestamp 등으로 분리되는지는 io 레이어에서 확인되지 않았으므로 "분리됨"에 의존하지 않는다 — 위 순서 자체로 안전.)
- 복사:

  ```sh
  cp <post_run_dir>/<ins>/<scenario>/step_*_partition.svg analysis/rj_rtf_vis/20260705/post/
  ```

- `analysis/rj_rtf_vis/20260705/post/README.md` 작성.
- **검증**: SVG 열어 LTF 블록이 incumbent 위치 그대로, non-time-fixed window가 pre보다 넓은지 (post-fix 기대치) 육안 확인.

### 7. Post-fix SVG + config + compare 문서 커밋

- `analysis/rj_rtf_vis/20260705/compare.md` 작성:
  - side-by-side 비교 안내 (같은 step 번호의 pre/post SVG 경로).
  - 예상 차이 vs 실제 관측 차이 메모.
  - 실행 환경 (OS, ortools 버전, UV lock SHA).
- 이번 커밋에는 신규 config도 포함 (post-fix 시점 working tree에 있으므로). pathspec 커밋으로 지정 경로만 담아 index 오염을 원천 차단:

  ```sh
  git add -f analysis/rj_rtf_vis/20260705/post \
             analysis/rj_rtf_vis/20260705/compare.md   # analysis/ ignore → 강제 add
  git status --short                                    # index에 post SVG + compare.md 만 있는지 확인
  git commit -m "chore(sw-cp-vis): post-fix partition gantt SVGs (RTF-only right-justify)"
  ```

  - config/plan md는 Step 4 pre-fix 커밋에 이미 포함되었으므로 post 커밋엔 미포함.
  - 커밋 직후 `git show --stat HEAD` 로 의도한 경로(post SVG + compare.md)만 들어갔는지 확인.

### 8. Branch 보존 (push 는 별도 결정)

- 로컬에 branch를 그대로 둔다 (삭제 금지 — 흔적 목적).
- push 여부는 사용자 결정. push 시:

  ```sh
  git push -u origin 20260705_all_rj_schedule_vis
  ```

- 사용자에게 branch HEAD SHA, 세 커밋 SHA (pre / cherry-pick된 fix / post), `analysis/rj_rtf_vis/20260705/` 트리 목록 보고.

---

## 5. Success criteria

- branch `20260705_all_rj_schedule_vis` 존재, 세 커밋(pre SVG / cherry-pick된 `d7ebc1f` fix / post SVG) 포함.
- pre SVG: LTF 블록이 우측으로 밀려있고 left dummy 폭이 post보다 큼 — 육안/`compare.md` 메모로 확인.
- post SVG: LTF incumbent 위치 고정, non-time-fixed 가용 window가 pre보다 넓음 — 동일.
- 같은 step 번호(0,1,2)의 SVG 쌍이 pre/post 양쪽에 모두 존재.
- `git stash` 등 다른 branch 작업에 영향 안 줌 (현재 branch의 미커밋 작업은 건드리지 않음).

## 6. Risks / 주의

- **현재 branch 미커밋 작업:** `git switch -c ... bbaf408` 시 tracked 파일의 수정사항은 따라옴, untracked는 따라옴. **신규 config/plan은 새 branch에만 커밋** 하고 원 branch(`20260703_lexico_partial_cp`)의 기존 staged/untracked 내용에는 손대지 않는다. 실행 전 `git status --short` 로 정리 필요 여부 판단.
- **cherry-pick 충돌 시:** C1(analysis/config/plan)과 `d7ebc1f`(dispatcher 코드)는 경로가 겹치지 않아 충돌 없어야 정상. 충돌이 나면 예상 밖 상황이므로 `git cherry-pick --abort` 후 중단·원인 파악. (구 전략의 `git merge --ff-only` 는 pre 커밋이 HEAD를 `d7ebc1f`와 분기시켜 항상 실패하므로 사용 금지 — §3 참조.)
- **CP 비결정성:** `solver_thread_cnt: 1` 로 완화. 단 Gantt는 CP 전 rj 기반이라 거의 무영향. pre/post가 동일 incumbent에서 출발하는지 확인 (같은 mcf_lb seed코드 = `bbaf408` 기점에 이미 존재).
- **SVG 없음/경로 변경:** `try_get_file_path_for_subroutine` 경로는 run 디렉토리 구조에 따라 달라짐. run 로그의 "Run output directory:" 로 실제 경로 확인 후 cp.
- **push 금지:** 사용자가 명시하기 전까지 `git push` 안 함.
- **worktree 사용 안 함:** 사용자 의향대로 worktree 말고 branch 로 흔적 남김. 추가 worktree 생성/삭제 step 없음.

## 7. 비교 config 임시 초안 (step 1에서 그대로 사용)

```yaml
run_mode: FULL_RUN
benchmark_dir: benchmarks/PRA2017/small
output_dir: output/20260705
instance_worker_cnt: 1
draw_gantt: false
scenarios:
  - name: compare_rj_rtf
    timelimit: 300.0
    output_subdir: compare_rj_rtf
    subroutine_flow:
      - method: calc_mcf_lb_and_derive_full_sch
        draw_pmtn_sch_heatmap: false
        job_placement_priority: "end_time"
        last_stage_only_placement_criteria: "dist"
        adjust_p: false
        adjust_r: false
      - method: sw_cp
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

(NEH-CP / flip_makespan 단계는 rj 시각 비교 목적상 제거 — mcf_lb 시드 직후의 sw_cp이 가장 비교 가능한 baseline.)
