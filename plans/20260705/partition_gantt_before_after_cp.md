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
