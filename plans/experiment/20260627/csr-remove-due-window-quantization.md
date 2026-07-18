# Plan: CSR due window 양자화 제거 (breaking change)

> 작성일: 2026-06-27
> 대상: `coarsen_solve_reconstruct` (CSR) 파이프라인
> 배경: 현재 CSR은 due window를 `ceil(d / factor)`로 양자화한 뒤, coarse 단위
> earliness/tardiness(E/T)를 최적화한다. 양자화를 없애고, coarse 완료시각을
> `factor * C^c_j` 로 해석해 **원본** due window 기준 penalty를 정확히
> 최적화하도록 바꾼다.
> 보조 검증 스크립트: `scripts/validate_csr_dw_twt_ewt.py`

---

## 0. 먼저: 사용자 추론이 맞는지 검증

요청의 핵심 가설:

> factor=50, due window=(72, 115)일 때 coarse 모델의 job은 50, 100, 150, …
> 에서만 끝날 수 있다. 이는 due window를 (72/50, 115/50)=(1.44, 2.3)로 두고
> earliness/tardiness weight를 각각 50배 하면, coarse 완료시각마다 계산한
> weighted E/T가 원본 문제의 penalty와 동일하다.

이 가설은 **맞다.** 대수적으로:
$$
w^-_j \cdot \tau \cdot max(0, d^-_j/\tau - C^c)  =  w^-_j * max(0, d^-_j - \tau \cdot C^c)
$$
$$
w^+_j \cdot \tau \cdot max(0, C^c - d^+_j/\tau)  =  w^+_j * max(0, \tau \cdot C^c - d^+_j)
$$
즉 "window를 factor로 나누고 weight를 factor배" 한 것과, "원본 window 그대로
두고 완료시각을 `factor*C^c`로 환산" 한 것은 **항등**이다.

### 중요한 점: penalty는 항상 정수

오른쪽 형태 `w^- * max(0, d^- - factor*C^c)` 는 `d^-`, `factor`, `C^c`, `w^-`
가 모두 정수이므로 **항상 비음 정수**다. 따라서 CP-SAT 모델 안에서 분수 due
window를 만들 필요가 전혀 없다 — 완료변수를 `factor` 배 한 정수 선형식으로
원본 정수 window와 비교하면 penalty를 **정확히(rounding 없이)** 정수로 최적화할
수 있다. (사용자의 "1.44, 2.3" 형태는 개념 설명용이고, 구현은 정수 형태를
쓴다.)

### 검증 스크립트 결과

`uv run python scripts/validate_csr_dw_twt_ewt.py`:

```
=== User example: factor=50, window=(72, 115), w^-=w^+=1 ===
 C^c  real  original    scaled  current(coarse)
   1    50        22     22.00                1  <- penalty differs / on-time flips
   2   100         0      0.00                0
   3   150        35     35.00                0  <- penalty differs / on-time flips

=== Property test: 200000 random cases ===
  equivalence  original == scaled : OK (max abs err 9.095e-12)
  integrality  original is non-neg int : OK
  current ceil-quantized penalty differed from the correct penalty in
  113244/200000 cases (56.6%)
```

확인된 사실:

1. **등가성** — 정수형 penalty와 사용자 scaled-window/scaled-weight 형태가
   완전히 일치한다. (float 형태는 `1.44` 같은 부동소수 표현 오차 ~1e-11가
   생기므로, 정수형 구현이 더 안전하다는 추가 근거다.)
2. **정수성** — penalty는 항상 비음 정수.
3. **현 방식의 오류** — 현재 `ceil` 양자화 penalty는 무작위 케이스의 56.6%에서
   올바른 penalty와 다르다. 단순 스케일 차이가 아니라, 사용자 예시의 `C^c=3`
   처럼 **on-time/tardy 분류 자체가 뒤집힌다** (real 150은 tardy 35인데 coarse
   양자화 window에서는 on-time → penalty 0).

→ 사용자 추론은 정확하며, 현재 양자화 방식은 coarse solver가 원본 목적함수와
다른 함수를 최적화하게 만들고 있다. 변경 정당화 완료.

---

## 1. 현재 구조 (변경 전)

- `FFcDDWParameters.coarsen_time_resolution(instance, factor)`
  (`parameters/ffc_ddw_params.py:284`)
  - `p -> ceil(p/factor)`, **due window `d -> ceil(d/factor)`**, weight 보존.
  - 결과 coarse 인스턴스는 p·window 모두 coarse 단위로 **일관**된다.
- `BaseModelBuilder._define_objective` (`algorithm/cumulative.py:332`)
  - `C_j = op_end[j, last]` (coarse 단위)
  - `E_j = max(0, d^-_c - C_j)`, `T_j = max(0, C_j - d^+_c)` — coarse window.
  - `minimize sum_j (w^- E_j + w^+ T_j)` — **coarse 단위 penalty**.
- dispatch seed (`algorithm/coarsen_solve_reconstruct.py:136`)
  - coarse 인스턴스의 window로 `insert_idle_time`, coarse wET로 후보 랭킹.
- 재구성 (`solution/schedule_build.py:99`)
  - `reconstructed_start = coarse_start * factor`,
    `reconstructed_end = reconstructed_start + original_p`.
  - 이후 `make_semi_active` → `insert_idle_time` (원본 window 기준).
- 최종 obj는 재구성 스케줄에 대해 원본 instance로 별도 계산
  (`compute_weighted_earliness_tardiness`, `objectives.py:12`).

핵심 문제: coarse solver가 최적화하는 함수(coarse 단위 + 양자화 window)와,
최종 평가 함수(원본 단위 window)가 다르다.

---

## 2. 목표 모델 (변경 후)

coarse 완료변수 `C^c_j` 는 그대로 두되, penalty를 다음으로 정의:

```
E_j = max(0, d^-_orig_j - factor * C^c_j)      # 원본 window, 정수 선형식
T_j = max(0, factor * C^c_j - d^+_orig_j)
minimize  sum_j ( w^-_j * E_j + w^+_j * T_j )  # weight 원본 그대로
```

- due window는 **양자화하지 않는다** (원본 정수값 사용).
- `factor * C^c_j` 는 정수 선형식 → CP-SAT 정수 모델로 정확히 표현 가능.
- 이 목적함수는 "모든 이벤트가 factor 배수 그리드에 놓인다"는 사용자 해석과
  정확히 일치한다.

### 알려진 잔여 gap = 순수 "완료시각 gap" (변경의 정확성 한계 — 문서화만)

먼저 핵심 항등식: **목적함수는 마지막 stage 완료시각의 순수 함수다.**
`compute_weighted_earliness_tardiness`(`solution/objectives.py:28-34`)는 job마다
`schedule.get_job_end_time(last_stage, job)` **하나만** 읽어
`w^- max(0, d^- - C) + w^+ max(0, C - d^+)` 를 더한다. 중간 stage 시각, 기계
배정, 시퀀스는 obj에 들어가지 않는다.

따라서 **후처리 후 완료시각이 같으면 obj는 정의상 같다.** 이건 검증할 필요가
없는 항등식이다(검증한다면 Python 산술을 테스트하는 꼴). 그래서 이 모델 obj가
최종 obj와 byte-exact가 아닌 이유는 단 하나로 환원된다:

> **모델이 가정한 완료시각(`factor * C^c_last`) ≠ 최종 스케줄의 완료시각.**

이 둘이 갈라지는 두 원인:

1. **마지막 stage p 라운딩 slack.** 모델 가정 완료는
   `factor*C^c_last = factor*coarse_start_last + factor*ceil(p_last/factor)`.
   raw 재구성 완료는 `factor*coarse_start_last + original_p_last`.
   원본 `p_last <= factor*ceil(p_last/factor)` 이므로 raw 재구성만으로도 job이
   모델 가정보다 최대 `factor-1` **일찍** 끝난다.
2. **postprocess** (`make_semi_active` → 좌측 정렬로 당김,
   `insert_idle_time` → ET 줄이려고 일부러 이동)가 완료시각을 추가로 바꾼다.

→ 완료시각이 그대로인 job은 기여분이 정확히 일치하고, 바뀐 job만 어긋난다.
gap은 "coarse-grid가 fine-grid 위치를 표현 못 함"이라는 CSR이 heuristic인
본질에서 나오며, 없앨 수 있는 버그가 아니다. 새 모델 obj는 최종 obj의 정확한
값이 아니라 **훨씬 더 충실한 proxy**다(현재처럼 양자화로 on-time/tardy 분류가
뒤집히는 일은 없어진다). 이 plan의 목표는 obj 동일성이 아니라 "solver가 올바른
목적함수를 최적화하도록" 만드는 것이다.

**별도 검증 코드는 불필요하다.** 위 항등식은 정의상 참이고, 완료시각 gap의
*크기*는 correctness가 아니라 측정 대상이다. 게다가 이번 변경 후에는 그 측정이
이미 metrics에 노출된다: `coarsened_obj_value`(= 모델의 `factor*C^c` penalty,
이제 **원본 스케일**)와 `reconstructed_obj_value`(= 최종)가 같은 단위가 되어 둘의
차이가 곧 gap이다. (현재는 `coarsened_obj_value`가 coarse 단위 + 양자화 window라
비교 자체가 불가능 → 변경 후 비로소 비교 가능해지는 게 부수 이득.) 원하면 별도
스크립트 대신 파이프라인에 sanity 로그/assert 한 줄
(`reconstructed_obj_value` vs 모델 obj 비교)만 둔다 — 4장 작업 6과 함께.

완전 일치가 정말 필요하면 별도 후속(예: 모델 내 완료식을
`factor*coarse_start_last + p_last`로 교정, 단 postprocess 효과는 여전히 남음)으로
다룬다 — YAGNI, 지금은 범위 밖.

---

## 3. 변경 지점 (breaking change scope)

### 3.1 coarsen_time_resolution — due window 양자화 제거

`parameters/ffc_ddw_params.py:320`

- window를 `ceil(d/factor)` → **원본값 보존**.
- ⚠️ 부작용: coarse 인스턴스가 "p는 coarse, window는 원본" 으로 **스케일이
  섞인다.** dispatch seed의 coarse-scale `insert_idle_time` / wET 계산이
  깨진다(3.3). 따라서 단순히 window만 보존하면 안 되고, coarse 경로 전체에서
  완료를 `factor` 배 해석하도록 일관 적용해야 한다.
- 결정 필요: (A) coarse 인스턴스는 window도 coarse로 유지하고 `factor`를
  **모델/seed 목적함수에만** 주입(스케일 일관성 유지, 권장), vs
  (B) 인스턴스 window를 원본으로 바꾸고 모든 coarse 소비처에 `factor` 곱을
  전파. → **(A) 권장**: 인스턴스 자기일관성 유지, `factor`는 목적함수 계산에만.

### 3.2 CP 모델 목적함수

`algorithm/cumulative.py:_define_objective` (+ `Params`, `make_params`)

- coarse 완료에 `factor`를 곱해 원본 window와 비교하는 경로 추가
  (`_define_objective`만 해당).
- 변수 상한: `E_j` 는 `max(d_lower_orig_j, 0)` (원본 window vs `factor*C`이라
  최대 earliness가 `d_lower_orig`; **factor 곱 불필요**), `T_j` 는
  `factor*horizon` 로 재산정.
- `_define_makespan_objective`(pure makespan)는 `max_j op_end`(= 모델 native
  스케일)과 동일하므로 **factor scaling 불필요** — 상한은 그대로 `horizon`,
  `time_factor` 인자도 두지 않는다. (due window 비교가 없어 스케일할 대상 자체가
  없음. flip-makespan dispatcher 전용이라 CSR 경로에서 도달하지도 않음.)
- 기존 `obj_lb` / `et_ub` / `minimize_makespan_lex` 분기와의 정합성 확인.
- `factor`, 원본 window를 모델 빌더에 전달하는 통로 설계
  (CSR 전용 builder 메서드 또는 `make_params`에 `completion_scale`/원본 window
  주입). 다른 알고리즘(non-CSR)은 `factor=1`로 기존 동작 유지 — 회귀 없음.

### 3.3 dispatch seed 목적함수 정합

`algorithm/coarsen_solve_reconstruct.py:136` (`_build_dispatch_seed_schedule`)

- seed 후보 랭킹과 `insert_idle_time`가 CP와 **같은** 목적함수를 봐야 warm-start
  가 일관된다. coarse 스케줄을 `factor` 배 해석한 원본 window 기준 wET로 평가.
- `_dispatch_seed_job_sequence`의 EDD 정렬은 `d^+` 단조 스케일이라 순서 불변 →
  변경 불필요.
- v3/v4 paired dispatch (`dispatcher/paired.py`)도 coarse 인스턴스 window를
  쓰므로 동일 정합 필요 — 영향 범위 확인.
- `dispatch_seed_coarsened_obj` metric의 의미도 새 스케일로 바뀜(문서화).

### 3.4 warm-start 힌트

`cumulative.py:apply_hints_from_schedule` / E·T 힌트

- 힌트로 넣는 `E_j`, `T_j` 값이 새 변수 정의(=`factor*C` 기반)와 일치해야 함.

### 3.5 영향 없음(확인용)

- 재구성 로직(`schedule_build.py`)과 최종 obj 계산(`objectives.py`)은 원본
  스케일이라 그대로. coarse obj와 최종 obj의 의미가 가까워질 뿐.

---

## 4. 작업 순서 (TDD)

1. **(완료) 검증 스크립트** `scripts/validate_csr_dw_twt_ewt.py` — 등가성·정수성·
   현 방식 괴리 확인.
2. **모델 단위 테스트(Red)**: 작은 인스턴스로, 새 목적함수의 coarse 최적해가
   "원본 window 기준 `factor*C` penalty"와 일치함을 검증하는 테스트 작성.
   현재 코드에서는 실패해야 함.
3. **`make_params`/`_define_objective`에 `factor`+원본 window 주입(Green)** —
   3.2. non-CSR 경로는 `factor=1` 기본으로 회귀 없음 확인.
4. **CSR 파이프라인 배선** — `run_coarsen_solve_reconstruct`가 새 목적함수를
   쓰도록. 결정 (A) 기준으로 `coarsen_time_resolution`은 window를 coarse로 두되
   `factor`를 목적함수 계산에만 전달.
5. **dispatch seed 정합(3.3)** — seed 랭킹/idle 삽입을 새 목적함수에 맞춤.
6. **회귀/비교 실험** — 기존 CSR run 대비 reconstructed_obj_value 개선/동등
   여부를 metadata 비교 run으로 측정 (양자화 round 스터디
   `project_csr_due_window_rounding_runs`와 연결).
7. `uv run ruff check` / `uv run ruff format`.

---

## 5. 결정/확인 필요 항목

- [ ] 3.1 결정 (A) vs (B) 최종 승인 — 권장 (A).
- [ ] 모델에 `factor`를 주입하는 통로: `make_params` 인자 vs CSR 전용 builder
      메서드. (다른 알고리즘 회귀 방지가 제약.)
- [ ] 잔여 gap(2장)을 이번 범위에서 교정할지, proxy로 둘지 — 기본 proxy로 둠.
- [ ] breaking change이므로 기존 CSR run 결과와 직접 비교 불가 — 새 run 라벨링
      방식.

---

## 6. 참고

- 검증: `scripts/validate_csr_dw_twt_ewt.py`
- 관련 run 스터디: `plans/.../` 및 메모리 `project_csr_due_window_rounding_runs`
  (all-ceil / floor-ceil / all-floor 비교) — 본 변경은 그 "rounding" 자체를
  목적함수에서 제거하는 방향이다.
