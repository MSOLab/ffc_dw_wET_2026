# sw_cp RJ warning 진단 및 검증 계획 (개정판)

> 개정 사유: 초판의 "블록 단위 vs 개별 op 구조 차이가 warning의 원인"이라는
> 진단은 **틀렸다**. 블록 구성/논문 최적성/CSR coarse grid를 코드·논문·PDF로
> 재검증한 결과 원인이 바뀌었고, dispatcher 내부 timing 경로에서
> **CSR의 lookahead가 flooring으로 덮어씌워지는** 별도 사실도 발견됨. 아래는
> 재검증된 진단과, 그 진단을 실측으로 확정하기 위한 **로깅 계획**이다.
> (구현은 별도 대화에서 진행. 이 문서는 진단·검증 설계까지만.)

## Context

`output/20260713_csr_init_methods/.../csr_neh_d2wp/Instance_200_5_5_0,6_0,2_10_Rep4/`
실행 로그에서 아래 warning이 반복 발생.

```
sw_cp: rj obj 330646.0 < incumbent obj 330658.0 (insert_idle_time left E/T on the table)
```

warning 발생지: `src/ffc_ddw_sum_et/algorithm/sw_cp/dispatcher.py:240-246`.
step 루프 안에서 incumbent를 right-justify한 `rj_schedule`의 목적함수가
incumbent보다 **엄격히 작을 때** 뜬다.

- `csr_neh_d2wp` 시나리오 = `coarsen_solve_reconstruct` with `factor: 4`,
  `idle_mode: "lookahead"` (정당화: `vault/20260702_진행사항_P3.pdf`).
- 실행 config: `metadata/20260713/csr_budget_sweep.yaml`.
- `rj_right_justify_scope: "rtf_only"`.

사용자 초기 가설: incumbent가 `make_semi_active` + `insert_idle_time`을 거쳤다면
RJ가 "non-time-fixed 변수만" 움직이는 동안 목적함수가 개선되면 안 된다 →
warning이 나면 안 된다.

**결론(선요약): 이 가설은 unit grid(`time_factor == 1`)에서는 맞다. warning은
coarse grid(`K > 1`) 전용 현상이며, CSR이 논문(Pan et al. 2017)의 최적성
증명 전제(정수 격자)를 깨뜨려서 생기는 필연적 잔여다. flooring/ceiling/
lookahead 어느 모드로도 원리적으로 제거되지 않는다.**

---

## 재검증된 진단

### 1. 블록 구성은 "우측 single-op부터, 좌측으로 확장"이 맞다

`insert_idle_time` (`ffc_schedule.py:1709-1719`) 과 논문 §3.2 / PDF p.3-4가 일치:

- `j = n-1`(머신 최우측 op)에서 시작. 첫 블록은 **single-op block**
  (`block_end = j`, `delta2 = INF`).
- `j`는 **감소만** 하고, 블록 `S_M = [j, block_end]`은 `j`(가장 이른/왼쪽 op)를
  왼쪽 끝으로 두고 **오른쪽으로** contiguous하게 확장한다
  (`starts[block_end+1] == ends[block_end]`).
- 즉 어떤 op의 오른쪽 op들은 이미 처리된 상태이고, 각 op은 `j`가 자기 위치에
  왔을 때 (오른쪽에 gap이 있으면 single-op block으로) **먼저** 밀린다.

**따라서 "tardy 머리 + 처리 안 된 early 꼬리" 같은 블록은 생길 수 없다.**
초판이 근거로 삼은 `[T_big(왼쪽), B_early(오른쪽)]` 스트랜딩 시나리오는 이
스캔 순서 때문에 성립하지 않는다. → 초판 §"두 함수의 근본 차이"의 원인
설명은 폐기.

### 2. 논문은 고정 시퀀스에서 최적 → `K == 1`이면 warning은 불가능

Pan et al. 2017 §3.2 원문:

> "the proposed procedure results in **optimal completion times** once the
> sequence of jobs for each machine is given" (NBM optimality를 병렬머신 +
> due window로 확장 증명 인용).

즉 고정 시퀀스에 대해 `insert_idle_time`은 last-stage weighted E/T **최적**이다.
그러므로 `time_factor == 1`(fine grid)에서는 남은 earliness가 0이고,
op-scoped right-justify가 개선할 여지가 **원리적으로 없다 → warning 발생 불가.**
(코드도 `K == 1`에서 flooring≡ceiling≡lookahead byte-identical: `:1688-1690`.)

→ **`K == 1`에서 warning이 하나라도 뜨면, 그것은 이 진단이 틀렸거나 구현
버그라는 강한 신호.** (검증 로그의 falsifiable control로 사용.)

### 3. 진짜 원인: CSR coarse grid가 비정수 breakpoint로 최적성을 깬다

`K > 1`이면 격자가 coarsen되고 due-window breakpoint가 `d_lo/K`, `d_hi/K`로
**비정수**가 된다. PDF의 표현 그대로: **"문제: 정수 보장 X (Δ₁ mod K = 0
보장 없음)"** (PDF p.5-7).

- **flooring** (`:1746-1761`): S_E 타깃 `d_lo//K - end`. `K ∤ d_lo`이면
  `K·(d_lo//K) < d_lo`로 **언더슈트** → early op에 잔여 earliness가 남고,
  `Δ₁ == 0` stall (`j -= 1`)도 발생.
- **ceiling/lookahead** (`:1762-1796`, PDF p.8-9): 언더슈트/stall을 완화하지만
  여전히 **per-block rigid + 1-unit 근사 휴리스틱**이다. PDF 실험(p.11-12)에서
  lookahead가 대체로 best지만 **"항상 그런 것은 아님"** (K=4 L-C=+0.3,
  K=8 +8.4로 reconstruction 후엔 ceiling보다 나쁠 때도 있음) — 즉
  **coarse 격자의 정확해가 아니다.**

핵심: 논문의 NBM 최적성 증명은 unit 격자(정수 완료시각, Δ₁이 breakpoint에
정확히 착지)를 전제한다. coarsening이 이 전제를 깨므로 어떤 per-block 변형도
최적성을 복원하지 못한다. op-scoped RJ가 잔여를 찾아내는 것은 **coarse
비최적성의 지문**이지 제거 가능한 오류가 아니다.

### 4. 결정적 발견: dispatcher는 내부에서 flooring으로 재-timing한다

CSR reconstruct가 `idle_mode: "lookahead"`로 seed를 만들어도, **sw_cp
dispatcher는 그 seed를 그대로 쓰지 않는다**:

`dispatcher.py:99-106` (run 진입 시 1회):

```python
incumbent = spec.ref_solution.deepcopy()
incumbent.make_semi_active(instance.stage_2_job_2_p_map)   # ← idle 전부 제거(좌측 정렬)
incumbent.insert_idle_time(..., time_factor=option.time_factor)  # ← idle_mode 미지정 = 기본 "flooring"
```

- `make_semi_active` (`:1024-1037`)는 모든 op을 as-soon-as-possible로
  **좌측 이동**시켜 CSR이 넣은 idle을 지운다.
- 이어지는 `insert_idle_time`은 `idle_mode`를 넘기지 않아 **기본값
  `"flooring"`** (`:1655`)으로 재삽입한다.
- post-CP 재-timing(`:364-370`)도 동일하게 flooring.
- `SwCpOption`에는 `idle_mode` 필드 자체가 없다 (grep 확인).

**즉 warning이 비교 대상으로 삼는 incumbent는 lookahead가 아니라 flooring으로
timing된 것이다.** "지금 결과는 lookahead인데도 warning이 난다"는 인식은
CSR 단계에 한해 맞고, **sw_cp 내부 timing 경로에는 lookahead가 전달되지
않는다**는 별도 불일치가 존재한다. 로깅으로 반드시 실측 확인해야 하는 지점.

### 5. 잔여 warning의 정확한 메커니즘

`[T_big, B_early]`가 아니라 **rigid-block 커플링 + 타깃 비대칭**이다:

- **타깃 비대칭**: `insert_idle_time`의 S_E 타깃은 윈도우 **시작**(`d_lo//K`),
  op-scoped RJ의 cap은 윈도우 **끝**(`d_hi//K = d_plus//K`). free한 op에는
  둘 다 earliness 0이라 obj 차이가 없지만, rigid 커플링 상황에서 `d_hi` 타깃이
  더 큰 여유를 준다.
- **rigid-block 커플링**: `insert_idle_time`은 contiguous 블록을 **통째로**
  이동하고 `sum_e > sum_t` (또는 lookahead 1-unit) 게이트로 멈춘다. early op이
  on-time/tardy 이웃과 contiguous하게 묶여 있고 그 이웃의 weight가 추가 블록
  이동을 거부하면, early op이 strictly early(`K·C' < d_lo`)로 남는다.
  op-scoped RJ는 op을 **독립적으로** 움직여(이웃은 pin 또는 별도 이동) 그
  잔여 earliness를 회수한다.

발생 필요조건(loggable): moved op이 **strictly early** (`K·C'_old < d_lo`) ∧
`K ∤ d_lo`(언더슈트) ∧ RJ가 도달 가능한 in-window 슬롯 존재. **단일-op이
strictly early로 남는 경우는 논리적으로 없어야 한다**(single early는 항상
on-time까지 밀려야 함) → 그런 케이스가 관측되면 timing 구현 버그.

---

## 검증 계획 — 진단 로그 (구현은 별도 대화에서)

목적: 위 §2-§5를 실측으로 확정/반증. warning 지점(`dispatcher.py:240`)에서,
`incumbent`(post-timing) vs `rj_schedule`의 last-stage end가 다른 op마다 아래를
남긴다. `obj gap을 op 단위로 완전 귀속`시키는 게 핵심.

### 로그 1 — `K`(time_factor)와 실제 idle_mode 기록 (falsifiable control)

- warning 라인에 `option.time_factor`와 **dispatcher가 실제로 사용한
  idle_mode**(현재 하드코딩 `"flooring"`)를 함께 찍는다.
- **예측: `K == 1`이면 warning 0건.** K=1에서 발생하면 진단 오류/버그 신호.
- CSR은 lookahead인데 sw_cp 내부는 flooring이라는 §4 불일치를 로그로 확증.

### 로그 2 — moved op 분류 (원인 = coarse 언더슈트 확인)

각 moved op에 대해:
`(job, stage, mc, K·C'_old, d_lo, d_hi, d_plus,
  class ∈ {strictly-early: K·C'_old<d_lo, on-time, tardy},
  under_shoot = (K ∤ d_lo) and (K·C'_old < d_lo))`

- **예측: 전부 strictly-early.** on-time op이 밀려 obj가 바뀌면(이론상 0이어야
  함) 별도 조사.

### 로그 3 — single vs multi-op 커플링 구분 (메커니즘 = rigid 커플링 확인)

- 그 early op이 `insert_idle_time`에서 **single-op 블록이었는지, multi-op
  contiguous 블록(커플링)이었는지**, 커플링이면 이동을 거부시킨 이웃
  (S_D/S_T)과 그 weight를 기록.
- **예측: multi-op 커플링.** **single-op인데 strictly-early로 남았다면
  timing 구현 버그** (§5).

### 로그 4 — obj gap 완전 귀속 (sanity)

- moved op별 `contrib_before/after/delta` 기록 후
  **`assert abs(sum(contrib_delta) - (inc_obj - rj_obj)) <= _FP_TOLERANCE`**.
- RJ가 민 `Δ`와 bind한 cap(`d_hi//K` vs machine_next_start)도 함께.
- 통과하면 "gap은 전부 early last-stage op의 earliness 감소"가 확정.

---

## 해결 방향 (설계만; 구현·선택은 별도)

진단이 "coarse 격자 비최적"이므로 선택지는 두 갈래다.

### 방향 A — lookahead 일관 적용 + prep RJ cleanup (실용)

1. **일관성 수정**: dispatcher의 내부 `insert_idle_time`(`:101`, `:365`)에
   `idle_mode`를 전달(예: `SwCpOption.idle_mode` 추가, 기본 `"lookahead"`).
   CSR이 고른 lookahead가 sw_cp 내부에도 반영되게 함. — warning을 줄이지만
   **원리적으로 없애지는 못함** (§3).
2. **warning 제거용 cleanup**: incumbent prep에서 `insert_idle_time` 직후
   `delay_job_latest_leq_obj_contrib_all_stages(rj_dw_ub_map)` 1회 호출.
   op-scoped RJ와 **같은 cap(`d_hi//K`)** 을 쓰므로 incumbent가 op-scoped RJ의
   **고정점**이 되어 warning이 사라진다. (초판의 fix안과 동일하며, 이 fix
   자체는 유효. 단 진단 문구만 본 개정판대로 교체.)
   - 성공 기준을 "warning 소멸"이 아니라 **"grid 전반 obj/gap 비회귀 +
     warning 소멸"** 로 잡을 것(incumbent 형상·batch profile이 실제로 바뀌므로).

### 방향 B — coarse-grid exact timing (원칙)

- 고정 시퀀스 + K-스케일 breakpoint에서 weighted E/T를 **정수 최적**으로 푸는
  timing algorithm(논문이 인용한 Hendel–Sourd류를 coarse 격자에 적용).
- op-scoped RJ가 개선할 여지가 원천 소멸 → **warning 원천 차단 + 별도 RJ pass
  불필요 + insert_idle 자립.** PDF p.13 TODO("slack maximize / 액자식 CSR")
  방향과 정합.
- 비용 크고 정확성 증명·테스트 필요.

### 권고 순서

1. 로그 1-4 심어 재실행 → **`K == 1` warning 0 + moved op이 strictly-early ·
   multi-op 커플링**인지부터 확정(진단 검증).
2. 부수적으로 §4 불일치(내부 flooring) 확인 후 방향 A-1로 lookahead 일관화 →
   warning 건수 변화 측정.
3. warning 제거가 목표면 A-2(prep RJ), 최적성이 목표면 B.

---

## 수정/관찰 대상 파일

- `src/ffc_ddw_sum_et/algorithm/sw_cp/dispatcher.py`
  - `:99-106` incumbent prep (make_semi_active + insert_idle_time; **flooring 기본**)
  - `:222-233` RJ 호출 (rtf_only / all_ops)
  - `:235-246` warning 지점 (로그 심을 곳)
  - `:364-370` post-CP 재-timing (동일 flooring)
- `src/ffc_ddw_sum_et/solution/ffc_schedule.py`
  - `insert_idle_time` `:1648` (flooring/ceiling/lookahead: `:1746/1762/1777`)
  - `make_semi_active` `:1024` (좌측 이동 = idle 제거)
  - `delay_job_latest_leq_obj_contrib` (last stage) `:1451`
  - `delay_job_latest_leq_obj_contrib_all_stages` `:1499`
  - `delay_operations_latest_leq_obj_contrib` (op-scoped) `:1533`

## 참고

- 논문: `vault/pan_et_al_2017.html` §3.2 Idle time insertion (NBM 최적성 증명).
- CSR idle_mode 설계·실험 정당화: `vault/20260702_진행사항_P3.pdf`
  (p.5-9 flooring/ceiling/lookahead, p.11-12 실험, "정수 보장 X").
- 실행 config: `metadata/20260713/csr_budget_sweep.yaml` (`csr_neh_d2wp`).
- warning 로그 예: `output/20260713_csr_init_methods/20260713T134754_861134/csr_neh_d2wp/Instance_200_5_5_0,6_0,2_10_Rep4/20260713T134754_861134_SingleInstanceRunner.log`
- CSR idle_mode 진입점: `orchestration/controller.py:2640` `coarsen_solve_reconstruct`,
  `algorithm/coarsen_solve_reconstruct.py` (option `idle_mode`, 기본 flooring).
