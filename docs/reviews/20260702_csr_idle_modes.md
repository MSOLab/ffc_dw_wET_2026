# CSR idle-insertion 3-mode (flooring / ceiling / lookahead) — 결과 리뷰

- **Run TIMESTAMP (full 1440)**: `20260702T013931_438875`
  (선행 스모크 10-instance run `20260702T011823_833511` — 결론 동일)
- **Config**: `metadata/20260702/csr_idle_modes_v4_config.yaml`
  (seed-only `solve=false`, `seed_dispatch=v4`, `factor ∈ {1,2,4,8,16}`,
  benchmark `PRA2017/large` **전체 1440**, `instance_worker_cnt=96`)
- **Run sanity**: 21,600 rows (=1440×15), 전부 `feasible`, error 0건, seed-only
  (`bestObj==initObj`), jobCount ∈ {50,100,150,200} 각 360.
- **분석 산출물** (스크립트는 기본 `analysis/`에 기록, gitignore; 이 run의 사본은
  `output/.../20260702T013931_438875/` 에 보관):
  - `csr_idle_modes_v4_full_20260702.csv` — tidy per-(instance,mode,factor),
    두 층위 obj + E/T 분해 (`scripts/dump_csr_coarse_obj.py --workers 90`)
  - `csr_idle_modes_v4_coarse_obj_table_full_20260702.csv` — coarse-obj 피벗
  - `csr_idle_modes_v4_coarse_obj_per_instance_full_20260702.csv` — coarse-obj per-instance
- **재현** (결정론):

  ```bash
  uv run python scripts/dump_csr_coarse_obj.py \
      --out analysis/csr_idle_modes_v4_full_20260702.csv --workers 90
  uv run python scripts/analyze_csr_idle_modes.py \
      --dump analysis/csr_idle_modes_v4_full_20260702.csv \
      --summary output/20260702_csr_idle_modes/20260702T013931_438875/20260702T013931_438875_summary.csv
  ```

  dump `recon_obj` vs run summary `bestObj`: **max|diff| = 0.0 (전 21,600 rows)**
  → dump가 production run을 바이트 단위로 재현(결정론 확인). `--workers` 순차/병렬
  출력도 byte-identical.

---

## TL;DR — 두 층위를 반드시 구분해야 한다

| 층위 | 정의 | lookahead가 나머지 둘을 지배(≤)? (per-instance, factor>1, n=5760) |
|---|---|---|
| **coarse** (uncoarsening **직전**, `dispatch_seed_coarsened_obj`) | `factor·C^c` vs 원본 window, v4-선택된 seed의 wET | ✅ **5760/5760 — 위반 0건** (≤ceiling, ≤flooring 모두) |
| **recon** (uncoarsening **이후**, 최종 `bestObj`) | 원본 스케일 최종 obj | ❌ ≤ceiling 4934/5760(위반 826), ≤flooring 4680/5760(위반 1080) |

> **결론**: "lookahead가 다른 둘보다 항상 좋거나 같다"는 기대는 **coarse(pre-uncoarsening)
> objective 에서 전 1440 인스턴스·전 factor 정확히 성립**(위반 0). 앞서 "lookahead가 ceiling에
> 뒤진다"고 보였던 것은 **reconstructed obj**를 본 것인데, 이건 **다른 목적함수**다. coarse obj는
> seed **선택 프록시**일 뿐이고, reconstruction이 fine-grid에서 배치를 재도출하므로 coarse-최적이
> recon-최적을 보장하지 않는다.

### 주의 — coarse_obj ≠ recon_obj (factor=1에서도)

같은 factor 행이라도 coarse 평균과 recon 평균은 다르다 (예: factor=1 coarse **155687.2** vs
recon **155200.2**). factor=1은 coarsening이 identity(`K·C=C`)인데도 다른 이유:

- **coarse_obj**는 v4가 고른 **원본 seed**(`trace.coarse_schedule`)의 wET.
- **recon_obj**는 `reconstruct_coarse_schedule` **후처리**를 거친 최종 스케줄의 wET —
  factor=1에서도 `build_schedule_from_op_starts`(시작시각 기준 기계 재배정) + `make_semi_active`
  - **새 `insert_idle_time`**를 다시 돌린다.

**차이의 원인은 100% 기계 재배정**(`build_schedule_from_op_starts`)임을 격리 검증:
seed의 배정을 **유지한 채** `make_semi_active` + `insert_idle_time`을 다시 돌리면 seed와
**바이트 동일**(멱등 — "왼쪽으로 당겼다 idle 재삽입"은 이미 그 과정을 거친 seed엔 no-op).
재배정이 seed 배정을 재현하면 `recon==coarse`. 재배정은 단순 라벨링이 아니라 **어떤 job이 같은
기계에 묶이는지**를 바꿔(시작시각 정렬 후 greedy first-free) `make_semi_active` 결과를 바꾸므로
obj가 달라지고, 실측상 항상 개선/동률이다(기계 배정을 공짜로 재최적화하는 셈).

factor=1 전체 1440 통계: recon ≤ coarse가 **1440/1440**(recon이 더 나빠지는 경우 0),
평균 gap −487, `recon==coarse`(재배정이 seed 배정 재현) 180/1440, 최대 −6783.
⟹ **coarse↔recon 차이는 "원본 seed vs 기계-재배정된 후처리 스케줄"이라는 별개의 축**이며 모든
factor에 존재. **"factor=1 대조군"은 세 모드(flooring=ceiling=lookahead)가 서로 같다는 뜻**(각
층위 안에서 성립)이지, 층위 *간* 동일을 뜻하지 않는다.

---

## 1. Coarse (pre-uncoarsening) objective — lookahead가 전면 지배

`csr_idle_modes_v4_coarse_obj_table_full_20260702.csv` (1440 평균):

| factor | flooring | ceiling | lookahead | ceil−floor | look−floor | ceil−look |
|---:|---:|---:|---:|---:|---:|---:|
| 1  | 155687.2 | 155687.2 | 155687.2 | 0.0 | 0.0 | 0.0 |
| 2  | 171952.0 | 162336.6 | 162335.4 | −9615.5 | −9616.6 | **1.1** |
| 4  | 180235.1 | 170727.1 | 170722.5 | −9508.0 | −9512.6 | **4.6** |
| 8  | 194698.4 | 187380.2 | 187369.2 | −7318.2 | −7329.2 | **11.0** |
| 16 | 226460.3 | 222594.3 | 222571.0 | −3866.0 | −3889.3 | **23.2** |

- **factor=1 대조군**: 세 모드 byte-동일 (`⌈d/1⌉=d`). ✔ sanity 통과.
- **per-instance 지배**: `lookahead ≤ ceiling` **5760/5760**, `lookahead ≤ flooring`
  **5760/5760** (위반 0). per-instance 전 7200행(factor=1 포함)에서 lookahead가 coarse_obj의
  (약)최소. → 설계대로: block별 floor(`Δa`) vs `Δa+1`을 block E/T로 비교해 국소 최소를 고르므로
  세 모드 중 coarse wET 최소.
- **flooring이 coarse에서 크게 뒤짐**(ceil−floor ≈ −9600 @f2). flooring은 floor breakpoint에서
  stall → **earliness 잔량**(undershoot)이 커서 coarse wET가 나쁨. E/T 분해가 이를 확증:

### weighted E/T 분해 (coarse, 평균) — 격차의 출처는 earliness

CSV 컬럼 `coarse_wE`/`coarse_wT`는 **weighted** earliness/tardiness 합이며
`coarse_obj = coarse_wE + coarse_wT` 가 정확히 성립(전 21,600행 max|diff|=0; recon도 동일
`recon_obj = recon_wE + recon_wT`).

| factor | flooring wE / wT | ceiling wE / wT | lookahead wE / wT |
|---:|---|---|---|
| 2  | 37505.2 / 134446.8 | 23042.6 / 139293.9 | 23064.2 / 139271.3 |
| 4  | 38409.1 / 141826.0 | 24045.2 / 146681.9 | 24108.4 / 146614.1 |
| 8  | 37240.1 / 157458.3 | 25621.7 / 161758.5 | 25756.0 / 161613.2 |
| 16 | 34349.6 / 192110.7 | 27702.0 / 194892.3 | 27962.0 / 194609.1 |

flooring은 wE가 ceiling/lookahead의 ~1.5배(한 칸 덜 밀어서). lookahead는 그 트레이드오프
(wE↓ vs wT↑)를 block-local로 최적화해 **총 wET 최소**(ceiling보다 wT를 조금 더 낮춤).

| factor | flooring wE | ceiling wE | lookahead wE |
|---:|---|---|---|
| 2  | 37,505.2 | 23,042.6 | 23,064.2 |
| 4  | 38,409.1 | 24,045.2 | 24,108.4 |
| 8  | 37,240.1 | 25,621.7 | 25,756.0 |
| 16 | 34,349.6 | 27,702.0 | 27,962.0 |

| factor | flooring wT | ceiling wT | lookahead wT |
|---:|---|---|---|
| 2  | 134,446.8 | 139,293.9 | 139,271.3 |
| 4  | 141,826.0 | 146,681.9 | 146,614.1 |
| 8  | 157,458.3 | 161,758.5 | 161,613.2 |
| 16 | 192,110.7 | 194,892.3 | 194,609.1 |

---

## 2. Recon (uncoarsened) objective — 아무도 지배하지 못한다

`bestObj` (=seed-only이므로 `initObj`) 평균:

| factor | flooring | ceiling | lookahead | ceil−floor | look−floor | ceil−look |
|---:|---:|---:|---:|---:|---:|---:|
| 1  | 155200.2 | 155200.2 | 155200.2 | 0.0 | 0.0 | 0.0 |
| 2  | 158757.4 | 157949.3 | 157942.9 | −808.1 | −814.5 | 6.4 |
| 4  | 159597.4 | 159040.0 | 159040.3 | −557.4 | −557.1 | −0.3 |
| 8  | 162147.6 | 161713.6 | 161722.0 | −434.0 | −425.6 | **−8.4** |
| 16 | 170447.3 | 170215.9 | 170172.9 | −231.4 | −274.4 | 43.0 |

- **per-instance 지배 (n=5760)**: `lookahead ≤ ceiling` **4934/5760**(위반 826),
  `lookahead ≤ flooring` **4680/5760**(위반 1080), lookahead가 3자 중 최소 4085/5760.
- coarse의 큰 격차(수천)가 recon에선 수백 수준으로 줄고 부호도 뒤섞임 → coarse 우위가 recon으로
  거의 전달되지 않음.
- **위반은 규모가 클수록 심함** (lookahead>ceiling 발생률):
  jobCount 50→9.2%, 100→15.5%, 150→15.9%, 200→16.7%; factor 2→10.1%, 8→17.8%.
  큰 인스턴스·큰 factor에서 coarse 프록시의 손실이 커진다는 신호.
- worst lookahead>ceiling (recon)은 전부 200-job: 예)
  `Instance_200_10_3_0,2_1_20_Rep4` f8 (ceil 77656 vs look 81892).


| factor | flooring | ceiling | lookahead | C-F | L-C |
|---:|---:|---:|---:|---:|---:|
| 1  | 155,200.2 | 155,200.2 | 155,200.2 | 0.0 | 0.0 |
| 2  | 158,757.4 | 157,949.3 | 157,942.9 | −808.1 |  -6.4 |
| 4  | 159,597.4 | 159,040.0 | 159,040.3 | −557.4 |  +0.3 |
| 8  | 162,147.6 | 161,713.6 | 161,722.0 | −434.0 |  +8.4 |
| 16 | 170,447.3 | 170,215.9 | 170,172.9 | −231.4 |  -43.0 |

---

## 3. 왜 coarse 지배가 recon 지배로 이어지지 않는가 (핵심)

`idle_mode`가 실제로 영향을 주는 범위는 **coarse seed 뿐**이다.

파이프라인:

1. v4 풀은 6개 후보(priority×{sd,rd})를 만들고, 각 후보를 `insert_idle_time(time_factor=K,
   idle_mode)`로 배치한 뒤 **coarse wET**로 점수 매겨 `argmin`을 seed로 선택. → `idle_mode`는
   (a) 각 후보의 coarse 점수, (b) 각 후보 last-stage의 coarse 시작시각을 바꾼다.
2. 선택된 coarse seed를 `reconstruct_coarse_schedule`(schedule_build.py:101)로 복원:
   coarse start ×K 팽창 → `build_schedule_from_op_starts`가 **시작시각 기준 기계 재배정** →
   `make_semi_active` + `insert_idle_time(**flooring, factor=1**)`로 **fine-grid에서 배치 재도출**.

즉 **coarse의 idle 배치는 복원 단계에서 버려지고**, 살아남는 건 선택된 seed의 *구조*(시퀀스·
기계배정)뿐. fine grid에서 idle을 다시 넣으므로(K=1이라 floor=ceil=정확) 최종 E/T는 사실상
**선택된 시퀀스의 fine-최적 E/T**.

따라서:

- **coarse obj = 선택 프록시**. lookahead는 이 프록시를 (설계상) 완벽히 최소화 → coarse 지배
  5760/5760.
- recon obj는 **다른 목적함수**(fine-grid 재최적화). 프록시를 더 낮추는 게 recon을 더 낮추는 걸
  **보장하지 않음**. lookahead는 coarse 점수 최저 seed를 고르지만 그 *시퀀스*가 다른 모드가 고른
  시퀀스보다 fine-grid에서 더 나쁠 수 있음(프록시 과적합). 큰 인스턴스일수록 프록시 손실↑(§2).
- ceiling이 일부 recon에서 유리: early/경계 블록을 무조건 `⌈d/K⌉`(`K·C≥d`, tardy 쪽)로 한 칸 더
  밀어, ×K 팽창 후 fine 재-flooring 시 window에 더 근접한 구조로 복원되는 경우가 있음(2차 효과,
  instance 의존).

**정리**: 기대("lookahead ≥ 나머지")는 **coarse layer에서 성립**하고 그게 맞는 층위다. recon
layer는 seed 선택 프록시 최적화가 아니라 복원 후 재최적화라서 누구도 지배하지 못한다. recon에서도
lookahead 지배를 원하면 **선택 기준을 coarse wET가 아니라 실제 reconstructed obj로** 바꿔야 함
(6후보를 복원해 recon-argmin 선택 — 후속 실험 후보).

---

## 4. 등가성 검증 (요청 §3)

`scripts/ceiling_equiv_test_20260702.py` — in-place ceiling (`K·C'` vs 원본 `d`,
breakpoint `⌈d/K⌉`) ≟ pre-coarsen OLD (window를 `⌈d/K⌉`로 미리 coarsen 후 plain Pan):

- **① partition 동치** (200k random): 불일치 **0건** — `(K·C' < d) ⟺ (C' < ⌈d/K⌉)`.
- **② 스케줄/obj 동치** (100k random single-machine): 불일치 **0건** — coarse 완료시각·
  원본-window obj 동일.

---

## 5. Artifacts / 스크립트

- `scripts/dump_csr_coarse_obj.py` — 결정론적 두-층위 obj 재도출(production 경로 재사용),
  `--workers` 병렬(순차와 byte-identical).
- `scripts/analyze_csr_idle_modes.py` — 피벗 + 지배성 + summary 교차검증.
- `scripts/ceiling_equiv_test_20260702.py` — ceiling ≡ OLD 등가성(partition + 스케줄).
