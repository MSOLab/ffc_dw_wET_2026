# 실험 기록: CSR seed-only A/B (옛 vs 신 알고리즘)

CSR seed 생성 알고리즘이 `20260624_more_init_dispatch` → `20260629_csr` 사이에서
바뀐 효과를, **CP-SAT 비결정성을 제거한 채** 결정론적으로 비교한다.

핵심 장치: `solve=False` (seed-only 모드). CP-SAT solve를 생략하고 dispatch seed를
그대로 reconstruct → 출력 = `reconstruct(seed)`. 재실행 노이즈 0이라 두 브랜치의
per-instance obj를 직접 비교할 수 있다. (`plans/experiment/20260630/csr-seed-only-deterministic-ab.md`)

---

## 1. Provenance

| 구분 | OLD (baseline) | NEW |
|---|---|---|
| branch | `20260624_more_init_dispatch_solvefalse` | `20260629_csr` |
| 알고리즘 출처 | `20260624_more_init_dispatch` (`93e7b85`) tip | 현행 CSR |
| git (run 시점) | `4042c44` | `5503ffa` |
| run timestamp | `20260630T232338_119442` | `20260630T232546_329050` |
| solve=False 제공 방식 | 백포팅 `31ac0df` | 기능 커밋 `5503ffa` |

- OLD 브랜치는 옛 tip에서 분기 후 `solve=False` **harness만** 백포팅. seed 알고리즘은
  옛것 그대로. harness 로직은 NEW와 동일(차이는 seed-build 시그니처의
  original-instance threading뿐 — 이는 비교 대상인 알고리즘 차이 그 자체).
- 두 run 모두 `output/20260630_csr_seed_only_ab/<timestamp>/` 아래.

### 알고리즘 차이(요약)
NEW가 OLD 대비 도입한 변경 (`20260624..20260629` 커밋):
- `0792b18 refactor(csr)!: original due window as SSOT` — coarsen이 due window를
  양자화하지 않고 `time_factor`로만 잇는다. seed wET 평가가 `original` → `coarsened`
  (원본 window 보존) 대상으로 이동, `insert_idle_time`에 `time_factor` 전달.
- `0eb8d27 fix(csr): floor shift to prevent overshoot` — coarse seed의 idle 삽입
  shift를 ceil → **floor** (overshoot 차단, sub-cell residual ≤ K−1 허용).
- `paired.py` v3/v4 빌더 리팩터(80줄).
- reconstruct(`schedule_build.py`)·objective(`objectives.py`)는 **양 브랜치 동일** →
  seed-only obj 차이는 순수 seed 알고리즘 차이.

상세 리뷰: `docs/reviews/20260630_csr_ssot_floor_shift_review.md`

---

## 2. 실험 세팅

- config: `metadata/20260630/csr_seed_only_ab_config.yaml` (양 브랜치 **바이트 동일** 검증 ✓)
- benchmark: `benchmarks/PRA2017/large`
- 인스턴스(8, size family spread): `ins_index = [0, 270, 360, 630, 720, 990, 1080, 1350]`
  → 50_5_3 / 50_10_5 / 100_5_3 / 100_10_5 / 150_5_3 / 150_10_5 / 200_5_3 / **200_10_5**(wide-window 계열)
- factor: `[1, 4, 16, 64]` (factor=1 = 대조군; coarse-grid 변경은 K=1에서 no-op)
- seed_dispatch: `mixed`, `v3`, `v4`, `job_wise`
- `solve: false` (전 시나리오)
- 규모: 16 시나리오 × 8 인스턴스 = **128 runs/branch** (각 ~27초)
- 병합 결과: `analysis/csr_seed_only_ab_20260630.csv`

---

## 3. 실험 검증 (대조군)

**factor=1: 32/32 모두 delta=0 (완전 동일).** coarse-grid 변경이 K=1에서 no-op이므로
양 브랜치가 byte-동일 출력을 낸다 → harness가 동일하고 측정된 차이는 순수하게
coarse seed 알고리즘에서만 온다는 것을 입증.

---

## 4. 결과

`delta = obj_new − obj_old` (양수 = **신규가 더 나쁨**), seed-only 최종 obj 기준.
`better/worse/same`는 8개 인스턴스 중 개수.

| strat | factor | mean_old | mean_new | mean Δ | better/worse/same |
|---|---|---|---|---|---|
| mixed | 1 | 183,550 | 183,550 | +0 | 0/0/8 |
| mixed | 4 | 184,431 | 188,861 | +4,430 | 1/7/0 |
| mixed | 16 | 187,319 | 192,280 | +4,961 | 1/7/0 |
| mixed | 64 | 202,639 | 215,397 | +12,759 | 0/8/0 |
| v3 | 1 | 137,913 | 137,913 | +0 | 0/0/8 |
| v3 | 4 | 136,624 | 139,429 | +2,805 | 1/7/0 |
| v3 | 16 | 137,299 | 141,663 | +4,364 | 0/7/1 |
| v3 | 64 | 176,267 | 177,065 | +798 | 2/3/3 |
| v4 | 1 | 137,913 | 137,913 | +0 | 0/0/8 |
| v4 | 4 | 136,624 | 139,429 | +2,805 | 1/7/0 |
| v4 | 16 | 137,299 | 141,663 | +4,364 | 0/7/1 |
| v4 | 64 | 174,976 | 177,065 | +2,089 | 2/4/2 |
| job_wise | 1 | 188,374 | 188,374 | +0 | 0/0/8 |
| job_wise | 4 | 189,961 | 193,394 | +3,433 | 1/7/0 |
| job_wise | 16 | 193,074 | 194,946 | +1,871 | 2/6/0 |
| job_wise | 64 | 202,206 | 213,767 | +11,561 | 0/8/0 |

전체 평균 RPD(vs BKS, %):

| strat | rpd_old | rpd_new | Δ |
|---|---|---|---|
| mixed | 167.6 | 178.7 | +11.1 |
| v3 | 107.6 | 109.8 | +2.2 |
| v4 | 106.9 | 109.8 | +2.9 |
| job_wise | 180.3 | 188.8 | +8.5 |

---

## 5. 관찰

1. **신규 알고리즘의 seed가 결정론적으로 더 나쁨.** factor>1 전 구간에서 mean Δ가
   양수, worse ≫ better. factor가 클수록 격차 확대 (f64에서 mixed +12,759,
   job_wise +11,561). floor-shift의 sub-cell residual이 final K=1 pass로 완전히
   복구되지 못하는 정황 — 좌석 sequence/배치가 coarse seed에서 고정되어 reconstruct
   가 timing만 복구하기 때문으로 보임 (추가 규명 필요).
2. **신규 브랜치에서 v3 == v4 완전 동일** (모든 factor·인스턴스 출력 일치). 옛
   브랜치에선 f64에서 미세하게 다름(174,976 vs 176,267). `paired.py` 리팩터로 v4가
   v3로 수렴했을 가능성 — 의도/회귀 여부 확인 필요.
3. v3/v4가 mixed·job_wise보다 RPD 절대값이 크게 낮음(≈108 vs ≈170~190) — 단, 본
   실험의 비교축은 브랜치 간 차이이지 전략 간 우열이 아님.

## 6. 한계

- 8개 인스턴스 서브셋 → 방향성 지표이지 결론 아님.
- **seed-only 측정.** 실제 파이프라인(solve=True)에선 CP-SAT가 이 seed를 개선하므로
  최종 품질 영향은 별도. seed 회귀가 CP time-to-incumbent에 주는 영향은 미측정.

## 7. 후속

- 전체 인스턴스(1440) 확장 재실행, 패밀리별 분해.
- v3==v4 동일성 조사.
- seed 회귀 원인 이등분 (floor shift vs SSOT 리팩터).
- **F/L 변경 적용 후 비교**: `plans/experiment/20260630/csr-seed-only-ab-after-FL.md` (별도 계획).
  적용·실험 후 본 문서에 timestamp·세팅·결과 섹션을 추가한다.
