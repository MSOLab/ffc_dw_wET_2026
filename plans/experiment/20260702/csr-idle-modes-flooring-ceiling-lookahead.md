# Plan — CSR idle-insertion 3-mode (Flooring / Ceiling / Look-ahead) 실험

Status: **APPROVED (결정 확정) — 착수 가능.**
결정: (1) **seed-only(solve=False)**, (2) ceiling breakpoint = **진짜 `⌈d/K⌉` (`-(-d//K)`)**.
Date: 2026-07-02
선행: `plans/experiment/20260701/csr-four-way-coarse-old-new-l-table.md` (OLD/NEW/L 비교),
슬라이드 `vault/20260702_진행사항_P3.pdf` (Flooring p.7 / Look-ahead p.8 / Ceiling p.9).

## 0. 배경 — 사용자 재정의 (기존 구현과 다름)

PDF-코드 대조에서 두 변형이 사용자 의도와 달랐음:

| 변형 | 기존 구현 | **사용자 의도 (이 실험에서 구현할 것)** |
|---|---|---|
| **Ceiling** | due window를 `⌈d/K⌉`로 **미리 coarsen** 후 plain Pan (branch `20260624_...solvefalse`) | due window **원본 d 유지**, `insert_idle_time` 내부에서 `K·C'` vs 원본 d partition, breakpoint **`Δ₁=⌈d/K⌉ − C'`** (`⌈x/K⌉ = -(-x//K)`). **현 OLD와 수학적으로 동일**(partition·breakpoint 일치, §1.3 증명) — in-place로 재구현할 뿐 |
| **Look-ahead** | **stall(Δ₁=0)일 때만** +1 look-ahead (branch `20260629_csr_L`) | **항상** Δ와 Δ+1 두 후보 생성 → objective-best 선택 (PDF p.8 문구 그대로) |
| **Flooring** | branch `20260629_csr` = `Δ₁=d//K`, Δ>0면 shift·Δ=0면 `j−1` | **동일** (변경 없음). **이걸 기본 동작으로.** |

⟹ **root branch = `20260629_csr` (Flooring 기본)**. 여기서 새 branch를 파고 ceiling·look-ahead를
`insert_idle_time`의 **모드 옵션**으로 추가한다 (기존 flooring 동작은 default로 보존).

## 1. 세 모드 정확 사양 (구현 계약)

세 모드는 **partition을 공유**한다 (현재 NEW의 current-cell 형태, marginal 아님):

```python
# 공통: block [j..block_end], delta2 = 다음 block까지 gap(없으면 INF)
s_e, s_t, s_d = [], [], []
for i in range(j, block_end + 1):
    d_lo, d_hi = due_window_map[job_ids[i]]
    KC = K * ends[i]                 # = K·C'_i (current cell)
    if KC < d_lo:   s_e.append(i)    # C' < d⁻/K
    elif KC >= d_hi: s_t.append(i)   # C' ≥ d⁺/K
    else:           s_d.append(i)
sum_e = sum(ewt_map[job_ids[i]] for i in s_e)
sum_t = sum(twt_map[job_ids[i]] for i in s_t)
```

모드는 `if sum_e > sum_t:` 블록에서만 갈린다:

**(a) flooring** (= 현 NEW, default):
```python
d1 = min([d_lo//K - ends[i] for i in s_e] + [d_hi//K - ends[i] for i in s_d], default=INF)
delta = min(d1, delta2)
if delta > 0: shift block by delta   # j 고정, 재평가
else:         j -= 1                  # Δ₁=0 floor stall → 전진
```

**(b) ceiling** (사용자 재정의, 진짜 ceil):
```python
ceil_div = lambda x: -(-x // K)      # ⌈x/K⌉
d1 = min([ceil_div(d_lo) - ends[i] for i in s_e] + [ceil_div(d_hi) - ends[i] for i in s_d], default=INF)
delta = min(d1, delta2)              # d1 ≥ 1 항상 (증명 §1.1) → stall 없음
shift block by delta                  # 무조건 shift, j 고정
# else 분기(sum_e ≤ sum_t): j -= 1
```

**(c) lookahead** (사용자 재정의, 항상 look-ahead):
```python
d1 = min([d_lo//K - ends[i] for i in s_e] + [d_hi//K - ends[i] for i in s_d], default=INF)  # floor
da = min(d1, delta2)
db = min(d1 + 1, delta2)
best = db if (db != da and block_obj(db) < block_obj(da)) else da
if best > 0: shift block by best      # j 고정, 재평가
else:        j -= 1                    # Δ=0 wins
# block_obj(shift) = Σ ewt·max(0,d_lo−K(C+shift)) + twt·max(0,K(C+shift)−d_hi)  (현 L branch helper와 동일)
```

### 1.1 ceiling이 stall하지 않는 이유
S_E: `K·C' < d⁻` ⟹ 정수 `C' < d⁻/K ≤ ⌈d⁻/K⌉` ⟹ `⌈d⁻/K⌉ − C' ≥ 1`. S_D도 동일(`K·C' < d⁺`).
delta2 ≥ 1 (인접이면 한 block으로 병합됨). ⟹ `delta ≥ 1` → 무조건 전진.

### 1.2 lookahead ⊇ 현 L
현 L은 da=0(stall)일 때만 db=1을 시험. 새 lookahead는 da>0에서도 da vs da+1을 비교(항상).
da가 이미 convex 최소면 block_obj(db) ≥ block_obj(da)라 best=da (동작 동일), da가 최소가 아니면
db로 한 칸 더 → 재평가로 다음 breakpoint 추적. da=0 케이스는 현 L과 동치.

### 1.3 ceiling(진짜 ⌈d/K⌉) ≡ 현 OLD
현 OLD는 window를 `⌈d/K⌉`로 미리 coarsen 후 plain Pan: partition `C' < ⌈d⁻/K⌉`, breakpoint `⌈d/K⌉−C'`.
새 ceiling은 `K·C' vs 원본 d`: `K·C' < d⁻ ⟺ C' < d⁻/K ⟺ C' < ⌈d⁻/K⌉`(정수 C'), S_T도 `C' ≥ ⌈d⁺/K⌉`로
동일. breakpoint도 `⌈d/K⌉−C'` 동일. ⟹ **partition·shift 전부 일치** → 값 동일, 코드만 in-place(공유 partition).
**factor=1**: `⌈d/1⌉=d` ⟹ ceiling=flooring=lookahead **byte-동일** → factor=1이 깨끗한 대조군.

## 2. 설계 결정 (확정)

1. **idle_mode 적용 범위 = coarse-grid seed 삽입만** (time_factor=factor).
   `reconstruct_coarse_schedule`(schedule_build.py:112)의 **최종 후처리 insert_idle_time은
   원본 스케일(time_factor=1) 표준 Pan 그대로 유지**(idle_mode 미전달) — seed 경로에만 배선.
   - **함의**: make_semi_active+최종 idle이 sequence·machine배정만의 결정함수이므로, 모드의 실효는
     주로 **v4 후보선택(coarse wET ranking)** 에서 나타남 (기존 5-way 발견과 정합). job_wise(단일 후보)면
     세 모드 recon 동일. v4(6후보)라 선택이 갈림.

2. **ceiling = 진짜 `⌈d/K⌉` (`-(-d//K)`)** [확정]. ⟹ §1.3에 의해 **factor=1에서 세 모드 byte-동일**
   → factor=1이 깨끗한 대조군. (in-place ceiling ≡ 현 OLD 값.)

3. **seed-only(solve=False)** [확정]. 결정론·CP 노이즈 없음. solve=True 필요 시 §8 후속.

4. **strat = v4 단일**, **factor ∈ {1,2,4,8,16}**, benchmark = `PRA2017/large`(1440).

## 3. 구현 단계 (코드)

**Root**: `git checkout 20260629_csr` → `git checkout -b 20260702_csr_idle_modes`.

배선 경로 (config → insert_idle_time):
`config scenario` → `controller.coarsen_solve_reconstruct(idle_mode=...)` →
`CoarsenSolveReconstructOption.idle_mode` → `run_coarsen_solve_reconstruct` →
`_seed_and_obj`/`_build_dispatch_seed_schedule` → `build_v4_paired_dispatch_schedule` →
`dispatch_forward_with_iit`/`dispatch_reversed_with_iit` → `schedule.insert_idle_time(..., idle_mode=)`.

편집 파일:
1. `solution/ffc_schedule.py::insert_idle_time` — `idle_mode: Literal["flooring","ceiling","lookahead"]="flooring"`
   파라미터 추가, `if sum_e > sum_t:` 블록을 §1 3-way 분기로. flooring 경로는 현 코드와 byte-동일 유지.
2. `algorithm/coarsen_solve_reconstruct.py` —
   `CoarsenSolveReconstructOption`에 `idle_mode` 필드 추가(default `"flooring"`);
   `_build_dispatch_seed_schedule`·`_seed_and_obj`·`run_coarsen_solve_reconstruct`·`_solve_coarsened_model`에
   idle_mode 전달; job_wise/mixed 분기의 `insert_idle_time(...)` 두 곳(174,185)에 전달.
3. `algorithm/dispatcher/paired.py` — `build_v4_paired_dispatch_schedule`(+v3 대칭),
   `dispatch_forward_with_iit`, `dispatch_reversed_with_iit`에 `idle_mode` kwarg 추가 → line 54,146의
   `insert_idle_time`에 전달.
4. `orchestration/controller.py::coarsen_solve_reconstruct` — `idle_mode="flooring"` kwarg 추가 → option에 전달.
5. **금지**: `solution/schedule_build.py::reconstruct_coarse_schedule`의 insert_idle_time은 **건드리지 않음**(§2.1).

주의: flooring 분기는 rooted branch(`20260629_csr`)의 현 로직과 **완전 동일**하게 유지
(idle_mode 미지정 시 회귀 없음).

## 4. 테스트 (TDD, `uv run python -m pytest`)

1. **flooring 회귀**: idle_mode 기본값에서 기존 NEW `insert_idle_time` 결과와 동일(작은 인스턴스, factor>1).
2. **ceiling**: 손으로 만든 block에서 `Δ₁=(d//K)+1−C'`, 무조건 shift, stall 없음 검증.
3. **lookahead**: da>0에서도 da vs da+1 비교하는지(현 L 대비 추가 발화) + da=0 케이스는 현 L과 동일.
4. **factor=1**: 세 모드 모두 byte-동일(§1.3) 확인 — 깨끗한 대조군.
   (선택) ceiling(진짜 ceil) 결과가 현 OLD branch seed-only obj와 전 row 일치(§1.3).
5. `uv run ruff check` / `uv run ruff format`.

## 5. Config

`metadata/20260702/csr_idle_modes_v4_config.yaml` — 15 scenario = {flooring,ceiling,lookahead}×{1,2,4,8,16}.
각 scenario:
```yaml
- name: <mode>_f<K>
  timelimit: "5nc"
  output_subdir: <mode>_f<K>
  subroutine_flow:
    - method: coarsen_solve_reconstruct
      seed_dispatch: v4
      factor: <K>
      solve: false
      idle_mode: <mode>
```
공통: `run_mode: FULL_RUN`, `benchmark_dir: benchmarks/PRA2017/large`,
`output_dir: output/20260702_csr_idle_modes`, `instance_worker_cnt: 48`(seed-only라 96도 가능),
`draw_gantt: false`. (ins_index_source·bks_table는 20260701 config에서 복사.)

## 6. 실행

```bash
git checkout 20260629_csr && git checkout -b 20260702_csr_idle_modes
# §3 구현 → §4 테스트 통과 후
uv run python main.py --config metadata/20260702/csr_idle_modes_v4_config.yaml
```
→ `output/20260702_csr_idle_modes/<TIMESTAMP>/` 에 15 scenario 서브디렉 + `<TS>_summary.csv`.

## 7. 분석 (pandas, Excel 미사용)

한 run에 3모드가 다 들어오므로 병합 불필요. `<TS>_summary.csv` 하나에서:
1. `scenarioName`을 `(mode, factor)`로 파싱.
2. seed-only ⟹ `bestObj==initObj` = reconstruct(seed) obj. (E/T 분해가 필요하면
   `merge_csr_seed_only_full_5way.py`처럼 `*_solution.json`의 last-stage 완료시각으로 재계산.)
3. 피벗: 행 `factor`, 열 `mode`(flooring/ceiling/lookahead), 값 obj 평균.
   차분 `ceiling−flooring`, `lookahead−flooring`, `ceiling−lookahead`. factor=1은 §2.2 주의와 함께 표기.
4. 산출물: `analysis/csr_idle_modes_v4_20260702.csv` + `docs/reviews/20260702_csr_idle_modes.md`
   (CLAUDE.md: 리포트 커밋 title/body에 run TIMESTAMP 기록).

**답할 질문**: 세 모드 recon obj 순위·격차; factor 의존성; ceiling(+1 overshoot-recovery)이
flooring(undershoot-stall)보다 나은가; 항상-lookahead가 flooring 대비 얼마나 회수하는가.

## 8. 한계 / 후속
- idle_mode 실효는 주로 v4 후보선택에서(§2.1) — placement는 최종 원본-스케일 후처리가 재도출.
- factor=1은 세 모드 동일(§1.3) — 깨끗한 대조군.
- ceiling ≡ 현 OLD 값(§1.3)이라 이번 실험의 신규 정보는 **항상-lookahead vs flooring vs ceiling** 3자 비교.
- seed-only 결정론(깨끗). **solve=True 후속**: 동일 config에 `solve: true`로 별도 run(비결정·CP예산 의존).

## 9. Provenance
| 항목 | 값 |
|---|---|
| root branch | `20260629_csr` (NEW/Flooring, `27d6c4d`) |
| 새 branch | `20260702_csr_idle_modes` |
| config | `metadata/20260702/csr_idle_modes_v4_config.yaml` |
| output | `output/20260702_csr_idle_modes/<TS>/` |
| 분석 | `analysis/csr_idle_modes_v4_20260702.csv`, `docs/reviews/20260702_csr_idle_modes.md` |
