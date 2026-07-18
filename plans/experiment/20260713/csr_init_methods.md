# CSR 초기화 방식 비교 실험

## Context

이전 실험(commit `075fa43`)에서 CSR A/B 비교와 in-mix factor sweep을 수행했으나,
다양한 초기화 방식을 체계적으로 비교하는 실험이 필요하다.

비교 대상:

1. `calc_mcf_lb_and_derive_full_sch` (MCF LB만)
2. `calc_mcf_lb_and_derive_full_sch` → `run_flip_makespan_cp_from_incumbent` (MCF LB + FMM)
3. `neh_cp` (NEH)
4. `coarsen_solve_reconstruct` (CSR)
5. `coarsen_solve_reconstruct` → `run_flip_makespan_cp_from_incumbent` (CSR + FMM)

CSR inner flow variants (TL 전부 25% = `0.0225nc` 통일):

- base: dispatch + base CP (no solve_flow)
- full miniature: mcf→FMM→neh→sw_cp→base_cp (CSR 제외)
- neh-only: neh→sw_cp→base_cp (mcf-lb/FMM 제외)

inner NEH job_priority 차원: `weight-due-pos` vs `due2-weight-pos`
(full miniature, neh-only에만 적용 — base는 inner NEH 없음)

## 실험 구조

**목적: 초기화 방법만 비교. tail (incremental_sw_cp + solve_base_model_cpsat) 없음.**

### 예산 정책

- **비-CSR baseline**은 참조(`metadata/20260710/sw_cp_tl_kappa_0.005.yaml`)의
  원본 init TL을 그대로 쓴다 — FMM `0.009nc`, neh `0.027nc`. 방법마다
  자연 비용이 다르므로 **예산 불균등은 의도된 것**이며, 결과는 최종 obj뿐 아니라
  실제 소요 시간과 함께 (obj, time) 관점으로 읽는다.
  TL이 없는 method(`calc_mcf_lb_and_derive_full_sch`)는 예외.
- **CSR**은 `0.0225nc` (= `0.09nc`의 25%). inner TL은 원본의 0.625배 스케일.
- **공평 비교(equal-budget)**: `mcf_lb_fmm_25p` / `neh_25p` 는 CSR와 동일한
  `0.0225nc` 예산으로 맞춘 대조군 — "같은 예산이면 CSR이 plain init보다 나은가"를
  직접 묻는다. (`_25p` = `0.09nc`의 25% = CSR 예산.)

### 시나리오 목록 (11개)

| # | 이름 | 초기화 (prefix) | 예산 | CSR inner flow | NEH priority |
|---|------|----------------|------|----------------|-------------|
| 1 | mcf_lb | mcf_lb | ~0 | - | - |
| 2 | mcf_lb_fmm | mcf_lb → FMM | 0.009nc | - | - |
| 3 | mcf_lb_fmm_25p | mcf_lb → FMM | 0.0225nc | - | - |
| 4 | neh | neh | 0.027nc | - | - |
| 5 | neh_25p | neh | 0.0225nc | - | - |
| 6 | csr_base | csr | 0.0225nc | base (default CP-SAT) | - |
| 7 | csr_full_wdp | csr | 0.0225nc | full miniature | weight-due-pos |
| 8 | csr_full_d2wp | csr | 0.0225nc | full miniature | due2-weight-pos |
| 9 | csr_fmm_base | csr → FMM | 0.0315nc | base | - |
| 10 | csr_neh_wdp | csr | 0.0225nc | neh → sw_cp → base_cp | weight-due-pos |
| 11 | csr_neh_d2wp | csr | 0.0225nc | neh → sw_cp → base_cp | due2-weight-pos |

> `csr_fmm_base`(#9)는 CSR + outer FMM으로 예산이 0.0315nc(비대칭) — 추후 조정 예정.

### CSR inner flow 상세

**base (default CP-SAT):**

- `solve_flow` 미지정 → `run_coarsen_solve_reconstruct` 기본 알고리즘 사용
- coarsen → CP-SAT solve → reconstruct

**full miniature (기존 csr_subalg.yaml 기반):**

- `solve_flow`에 전체 workflow 사본 (CSR 제외):

  ```txt
  mcf_lb → FMM → neh → incremental_sw_cp → solve_base_model_cpsat
  ```

- inner NEH job_priority: "weight-due-pos" 또는 "due2-weight-pos"
- inner step TLs는 CSR budget 내에서 비례 배분
- inner kappa: 0.00125 (원본 0.005의 25%)

**neh-only (mcf-lb/FMM 제외):**

- `solve_flow`에 NEH + CP refinement:

  ```txt
  neh → incremental_sw_cp → solve_base_model_cpsat
  ```

- inner NEH job_priority: "weight-due-pos" 또는 "due2-weight-pos"
- inner NEH total_timelimit = CSR budget 전체 (mcf-lb/FMM 없으므로)
- inner kappa: 0.00125

### 시간제약 배분

total scenario TL: `0.09nc` (참고: n=50, c=4 → 18초).
`0.09nc`는 느슨한 cap이며 대부분 시나리오는 자기 자연 비용에서 조기 종료한다.

- 비-CSR baseline: FMM `0.009nc`, neh `0.027nc` (원본 그대로)
- equal-budget 대조군: `mcf_lb_fmm_25p` FMM `0.0225nc`, `neh_25p` neh `0.0225nc`
- CSR budget: `0.0225nc` (= `0.09nc`의 25%)
  - full miniature inner: FMM `0.005625nc`, neh `0.016875nc`, kappa `0.00125`
  - neh-only inner: neh `0.0225nc` (전체 budget), kappa `0.00125`

## 수정할 파일

- `metadata/20260713/csr_init_methods.yaml` (new)
- `main.py` (`CONFIG_PATH` → 새 config)

## 참고

- CSR outer params: factor=50, seed_dispatch="mixed", idle_mode="flooring"
- Prefix step params: `metadata/20260710/sw_cp_tl_kappa_0.005.yaml` 참조
- CSR inner flow params: `metadata/20260711/csr_subalg.yaml` 참조
