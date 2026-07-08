# 변경사항 리뷰: CSR due-window SSOT + floor shift

- **대상 커밋 2개** (branch `20260629_csr`)
  1. `0792b18` `refactor(csr)!: original due window as SSOT` — 14 files, **+557 / −198**
  2. `0eb8d27` `fix(csr): floor shift to prevent overshoot` — 5 files, **+549 / −28**
- **선행 작업:** `6a8da8e fix(csr)!: drop due-window quantization`
- **알고리즘 SSOT:** `vault/20260629_p3_csr.pdf` slide 6
  ("Idle Time Insertion After Coarsening – Floor for Shifting")
- **설계 문서:** `plans/20260629/csr-time-factor-ssot.md`,
  `plans/20260630/csr-floor-shift-overshoot-safe.md`,
  `plans/20260630/csr-cap-k-to-min-due-window-width.md`(rejected)
- **검증 결과:** `uv run ruff check` 통과, 전체 테스트 **490 passed**,
  PDF slide 6 과 구현 1:1 대조 완료.

---

## 1. 핵심 테마

두 커밋이 함께 **"원본 due window 를 CSR 파이프라인 전체의 single source of
truth 로 만든다"** 는 하나의 목표를 완성한다.

1. **`0792b18` — 이중 저장(dual-storage) 제거.** coarsen 단계가 due window 를
   더 이상 양자화하지 않고(processing time 만 coarsen), `time_factor` 하나만으로
   coarse grid ↔ 원본 시간축을 잇는다. `original_*` 계열 plumbing 을 전부
   삭제하여 직전 상태보다 **단순해졌다** (net-delete).
2. **`0eb8d27` — coarse seed 의 overshoot 구조적 차단.** idle 삽입 shift 거리를
   ceil → **floor** 로 바꿔, early job 이 sub-grid due window 를 건너뛰어
   tardy 로 밀리는 일이 절대 없도록 한다. slide 6 의 알고리즘을 그대로 구현.

---

## 2. 커밋별 요약

### 2.1 `0792b18` refactor(csr)!: original due window as SSOT (**BC break**)

| 구분 | 변경 |
|---|---|
| `parameters/ffc_ddw_params.py` | `coarsen_time_resolution` → **`coarsen_processing_times`** 로 rename. due window 를 `ceil(d/factor)` 하던 코드 삭제, 원본 window 그대로 보존. 인스턴스 이름 suffix `_coarsen{f}` → `_coarsenp{f}` |
| `algorithm/cumulative.py` | `Params.original_scale_d_lower/upper` 필드 삭제, `build()`/`make_params()` 의 `original_due_windows` 파라미터 삭제. objective·hint 코드의 `if time_factor>1 … else …` 분기를 단일 분기 `scaled_C = time_factor * C_j` (vs `params.d_lower/d_upper`)로 통합 |
| `solution/ffc_schedule.py` | `insert_idle_time` 에 `time_factor: int = 1` 추가 (이 커밋 시점: 진입부에서 `eff_window = ceil(d/tf)` precompute) |
| `algorithm/dispatcher/paired.py` | `dispatch_forward_with_iit`/`dispatch_reversed_with_iit` 에 `time_factor` 관통. `build_v3/v4` 에서 `original_instance` 제거 |
| `algorithm/coarsen_solve_reconstruct.py` | seed 빌더/CP 호출에서 `original` 인자 제거, `time_factor=factor` 로 통일 |
| `orchestration/{controller,…runner}.py` | call-site 갱신 |

**가장 중요한 수정(회귀 방지 핵심):** `build_v3/v4` 에서 직전 구현은 후보를
`tf=1` 로 **포지셔닝**한 뒤 `tf=factor` 로 **재채점**만 했다 → 점수는 scaled 지만
실제 seed 스케줄은 coarse grid 상에서 원본 window 기준으로 idle 삽입되어
약 `factor×` 만큼 **오배치**되었다. 이번 커밋은 `time_factor=factor` 를 helper
까지 관통시켜 **빌드와 채점을 동일 time_factor 로** 수행한다. default seed 는
`mixed`(영향 없음)지만 `coarsen_solve_reconstruct_v4_seed_config.yaml` 이 v4 를
쓰므로 실험에 직접 영향. `tests/algorithm/dispatcher/test_paired.py` 에 회귀
테스트 추가됨.

### 2.2 `0eb8d27` fix(csr): floor shift to prevent overshoot

`insert_idle_time` 본문(슬라이드 6) 세 가지 변경:

1. **Partition — Multiplication form (정확, 반올림 없음).**
   `eff_window=ceil(d/K)` precompute 삭제 → 직접 `K*c` 와 원본 window 비교:
   `K*c < lo` → S_E, `K*c >= hi` → S_T, else S_D.
2. **Shift 거리 Δ₁ — floor.** `lo // K - c` (S_E), `hi // K - c` (S_D).
   (직전: ceil 기반 `eff_lo - c`, `eff_hi - c`.)
3. **종료 가드 `Δ₁ == 0 → j -= 1`.** floor 는 0 shift 를 만들 수 있고, j 고정
   상태로 0 shift 하면 무한 루프 → 가드로 진행 보장.

---

## 3. 정확성 검증

### 3.1 PDF slide 6 과의 대조 (1:1 일치)

| slide 6 | 구현 (`ffc_schedule.py:1624-1657`) | 일치 |
|---|---|---|
| `S_E = {K·C_j < d⁻}` | `if KC_j < d_lo: s_e` | ✓ |
| `S_D = {d⁻ ≤ K·C_j < d⁺}` | `else: s_d` | ✓ |
| `S_T = {d⁺ ≤ K·C_j}` | `elif KC_j >= d_hi: s_t` | ✓ |
| `Σ_{S_E} w⁻ > Σ_{S_T} w⁺` | `if sum_e > sum_t` | ✓ |
| `Δ₁ = min(⌊d⁻/K⌋−C, ⌊d⁺/K⌋−C)` | `d_lo//K - ends[i]`, `d_hi//K - ends[i]` | ✓ |
| `Δ = min(Δ₁, Δ₂)`, `C += Δ` | `delta=min(delta1,delta2)`; 블록 shift | ✓ |
| False → `j -= 1` | `else: j -= 1` | ✓ |

slide 의 "Flooring may be inefficient but is safe against overshooting" 문구가
이 커밋의 의도와 정확히 일치.

### 3.2 Overshoot-safety (증명)

블록은 `Δ ≤ Δ₁` 만큼 이동. `i ∈ S_E` 에 대해
`Δ ≤ ⌊lo_i/K⌋ − c_i ⟹ K(c_i+Δ) ≤ K⌊lo_i/K⌋ ≤ lo_i ≤ hi_i` →
이동 후에도 `K·C ≤ lo_i`, 즉 **여전히 early(또는 하한 경계), 절대 tardy 아님**.
`i ∈ S_D` 도 `K(c_i+Δ) ≤ hi_i` 로 in-due 유지. 모든 window 폭에서 성립.

### 3.3 종료성

- shift 분기는 매번 `Δ ≥ 1`(j 고정), 그 외에는 `j` 감소.
- floor 거리는 `lo//K - c ≥ 0`, `hi//K - c ≥ 0` 임이 정수 부등식으로 보장
  (`K·c < lo ⟹ c ≤ ⌊lo/K⌋`). Δ₁ 은 항상 ≥ 0, 0 이 되는 순간 가드가 `j` 감소.
- shift 분기 진입 조건 `sum_e > sum_t ≥ 0` ⟹ `S_E` 비어있지 않음 ⟹ `delta1` 유한.
  따라서 무한 shift 불가. `Δ₂` 는 블록 확장이 멈춘 지점에서 항상 ≥ 1 (gap=0 인
  동안만 확장) → `delta == 0` 은 오직 `delta1 == 0` 일 때만 발생, 가드와 정합.

### 3.4 `time_factor=1` 무영향 (scope guard)

K=1 에서 `lo//1 = lo = ⌈lo/1⌉`, partition 은 `c` vs `(lo,hi)`. S_E/S_D 거리
`lo - c ≥ 1`, `hi - c ≥ 1` 이므로 가드의 else 가지는 **절대 실행되지 않음** →
기존 비-CSR caller 및 K=1 최종 reconstruct 와 **byte-identical**. 확인 완료.

### 3.5 채점 정합성

`compute_weighted_earliness_tardiness`(`objectives.py:46`) 와 CP objective
(`cumulative.py`) 모두 `K·C` vs 원본 window 를 **정확 계산**(`max(0, d−K·C)`).
floor 로 under-shot 된 스케줄도 "있는 그대로" 정확 채점되므로 seed 의 보고 obj
가 실제 비용을 정직하게 반영. floor under-shoot 는 **이중 복구 가능**:
(a) CP 솔버가 coarse 모델 내에서 in-due cell 까지 개선 가능(floor 는 seed 위치만
제약, CP 변수는 자유), (b) 최종 reconstruct 가 K=1 에서 정확 재최적화.
→ floor 의 비효율은 **seed/warm-start 품질에만** 한정.

---

## 4. 수정 필요 사항

### 4.1 [수정 필요 · Low] `cumulative.py:375-386` stale docstring

`_define_objective` 의 docstring 이 `0792b18` 에서 **삭제된** 필드
`params.original_scale_d_lower` / `params.original_scale_d_upper` 를 여전히
참조한다. 코드 본문(L422~)은 `params.d_lower/d_upper` 로 올바르게 갱신되었으나
docstring 만 누락. 존재하지 않는 속성을 가리키므로 정정 필요.

> **수정 제안:** "compared against the original-scale due window stored in
> `params.original_scale_d_lower` / `params.original_scale_d_upper`" →
> "compared against `params.d_lower` / `params.d_upper` (coarsened 인스턴스에서는
> 이것이 원본 scale window)" 로 교체.

### 4.2 [수정 권장 · Low] `test_insert_idle_time_tf_effective_window` 가 구식 모델을 문서화

`tests/solution/test_ffc_schedule.py:515` 테스트는 알고리즘을 "effective
window = `ceil(d/K)`" 로 서술하지만, `0eb8d27` 이 그 모델을 **floor-shift +
Multiplication partition** 으로 대체했다. 이 테스트가 통과하는 이유는 데이터
window `(16,24)` 가 K=2 의 정확한 배수라 **floor==ceil 로 우연히 일치**하기
때문이다(정확성 버그 아님). 다만 문서로서 현재 알고리즘을 오도한다.

> **수정 제안:** (a) 이름/주석에 "K-aligned 우연 일치" 임을 명시하거나,
> (b) 비정렬 window 케이스를 추가해 floor under-shoot 를 드러내는 oracle 로
> 갱신. 직전 ceil 모델의 잔재이므로 정리 권장.

### 4.3 [관찰 · 주석 오류] `test_ffc_schedule.py:588`

`test_insert_idle_time_overshoot_safety_narrow_window` 의 주석
`# real completion, within (110,120)` 은 부정확하다. `50*2 = 100 < 110` 이므로
실제 완료는 window 안이 아니라 **여전히 early**(하한 미달). 테스트의 실제 단언
(C=2 유지, tardy C=3 으로 overshoot 안 함)은 옳다. `assert 50 * 2 == 100` 은
스케줄과 무관한 항진 단언이라 의미가 없음 → 주석/단언 정리 권장.

### 4.4 [관찰 · deferred] `paired.py:53` `make_semi_active(...) # TODO: remove`

`0792b18` 이 `dispatch_forward_with_iit` 의 semi-active 호출에 `# TODO: remove`
마커를 달았다. 한편 `dispatch_reversed_with_iit`(`paired.py:145`)의 동일 호출엔
마커가 없어 비대칭. IIT 파이프라인에서 semi-active 가 불필요/유해한지는 별도
조사 필요. (이번 리뷰 범위 밖, `TODOS.md` 기준 자율 실행 금지 항목.)

### 4.5 [관찰 · 설계 리스크] scale-hybrid coarsened 인스턴스

`coarsen_processing_times` 결과는 coarse `p` + 원본 window 의 **자기모순적**
인스턴스로, `time_factor` 없이는 정합하지 않는다. rename + invariant docstring
으로 완화했으나, `compute_weighted_earliness_tardiness(coarsened)`(인자 누락)
호출 시 런타임 가드 없이 scale 을 조용히 섞는다. plan 상 YAGNI 로 수용된
trade-off 이나, 향후 새 caller 추가 시 주의 지점.

---

## 5. 종합 평가

- **설계 방향 적절.** SSOT 일원화로 직전의 이중 plumbing 을 net-delete 했고,
  floor-shift 는 PDF slide 6 의 의도를 구조적으로(벤치마크 의존 아님) 구현.
- **정확성 확인.** slide 대조·overshoot 증명·종료성·K=1 무영향·채점 정합성
  모두 검토 통과. 전체 테스트 490 green, ruff clean.
- **수용된 trade-off 명시적.** floor 의 wide-window under-shoot(잔여 earliness
  ≤ K−1)는 seed 한정이며 CP+reconstruct 로 이중 복구. 테스트로 lock 됨.
  탈출구(option C: in-due 도달 가능 시 ceil)도 plan 에 기록.
- **조치 권장:** §4.1(stale docstring) 만 실질 수정 대상, 나머지는 문서/주석
  정리 수준. 기능 회귀 위험 없음.
