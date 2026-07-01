# CSR idle-insertion 3-mode (flooring / ceiling / lookahead) — 결과 리뷰

- **Run TIMESTAMP**: `20260702T011823_833511`
- **Config**: `metadata/20260702/csr_idle_modes_v4_config.yaml`
  (seed-only `solve=false`, `seed_dispatch=v4`, `factor ∈ {1,2,4,8,16}`,
  benchmark `PRA2017/large`)
- **주의 — 스모크 서브셋**: 이 run의 `ins_index`는 **10-instance 스모크 서브셋**
  (`[60,61,63,64,68,150,152,155,246,248]`) 이다. 전체 1440 인스턴스가 아니다.
  전체 실행은 config의 `ins_index` 줄을 지우면 됨. seed-only는 결정론이라 서브셋
  수치는 재현 가능(아래 max|diff|=0 참조).
- **분석 산출물** (스크립트는 기본적으로 `analysis/`에 쓰며 gitignore; 이 run의
  사본은 run 디렉토리 `output/.../20260702T011823_833511/`에 보관):
  - `csr_idle_modes_v4_20260702.csv` — tidy per-(instance,mode,factor),
    두 층위 obj + E/T 분해 (`scripts/dump_csr_coarse_obj.py`)
  - `csr_idle_modes_v4_coarse_obj_table_20260702.csv` — coarse-obj 피벗
  - `csr_idle_modes_v4_coarse_obj_per_instance_20260702.csv` — coarse-obj per-instance
- **재현**:
  ```bash
  uv run python scripts/dump_csr_coarse_obj.py       # tidy CSV 재도출 (결정론)
  uv run python scripts/analyze_csr_idle_modes.py \
      --summary output/20260702_csr_idle_modes/20260702T011823_833511/20260702T011823_833511_summary.csv
  ```
  dump의 `recon_obj` vs run summary `bestObj`: **max|diff| = 0.0** → dump가 run을
  바이트 단위로 재현(결정론 확인).

---

## TL;DR — 두 층위를 반드시 구분해야 한다

| 층위 | 정의 | lookahead가 나머지 둘을 지배(≤)? |
|---|---|---|
| **coarse** (uncoarsening **직전**) | `factor·C^c` vs 원본 window, v4-선택된 seed의 wET (`dispatch_seed_coarsened_obj`) | **예 — 40/40, 위반 0건** |
| **recon** (uncoarsening **이후**, 원본 스케일) | 최종 `bestObj` | **아니오 — look≤ceil 36/40, look≤floor 29/40** |

> **결론**: "lookahead가 다른 둘보다 항상 좋거나 같다"는 기대는 **coarse(pre-uncoarsening)
> objective 에서 정확히 성립**한다(전 인스턴스·전 factor 지배, 위반 0). 앞서 "lookahead가
> ceiling에 뒤진다"고 보였던 것은 **reconstructed(uncoarsened) obj**를 본 것인데, 이건
> **다른 목적함수**다. coarse obj는 seed **선택 프록시**일 뿐이고, reconstruction이 fine-grid
> 에서 배치를 재도출하므로 coarse-최적이 recon-최적을 보장하지 않는다.

---

## 1. Coarse (pre-uncoarsening) objective — lookahead가 지배

`analysis/csr_idle_modes_v4_coarse_obj_table_20260702.csv` (10-instance 평균):

| factor | flooring | ceiling | lookahead | ceil−floor | look−floor | ceil−look |
|---:|---:|---:|---:|---:|---:|---:|
| 1  | 62309.5 | 62309.5 | 62309.5 | 0.0 | 0.0 | 0.0 |
| 2  | 65464.1 | 65365.0 | 65365.0 | −99.1 | −99.1 | 0.0 |
| 4  | 67261.1 | 67119.4 | 67116.5 | −141.7 | −144.6 | **2.9** |
| 8  | 72178.6 | 71726.7 | 71709.1 | −451.9 | −469.5 | **17.6** |
| 16 | 79746.8 | 79444.7 | 79418.1 | −302.1 | −328.7 | **26.6** |

- **factor=1 대조군**: 세 모드 byte-동일 (`⌈d/1⌉=d`). ✔ sanity 통과.
- **per-instance 지배 (factor>1, n=40)**: `lookahead ≤ ceiling` **40/40**,
  `lookahead ≤ flooring` **40/40**. per-instance 전 50행에서 lookahead가 coarse_obj의
  (약)최소임을 확인.
- `ceil−look ≥ 0` (factor 커질수록 격차 ↑) → coarse에서 lookahead ⊇ ceiling의 개선.
  이건 설계대로다: lookahead는 블록별로 floor(`Δa`) vs `Δa+1`을 block E/T로 비교해
  국소 최소를 고르므로, 세 모드 중 coarse wET가 가장 낮다.

### E/T 분해 (coarse, 평균) — 격차의 출처는 earliness
| factor | flooring E / T | ceiling E / T | lookahead E / T |
|---:|---|---|---|
| 2  | 956.6 / 64507.5 | 825.2 / 64539.8 | 825.2 / 64539.8 |
| 8  | 1436.3 / 70742.3 | 734.3 / 70992.4 | 742.9 / 70966.2 |
| 16 | 1420.0 / 78326.8 | 930.8 / 78513.9 | 972.4 / 78445.7 |

flooring은 floor breakpoint에서 stall → **undershoot(earliness 잔량 큼)**. ceiling/lookahead는
한 칸 더 밀어 earliness를 줄이는 대신 tardiness가 살짝 증가. lookahead는 그 트레이드오프를
block-local로 최적화해 **총 wET 최소**.

---

## 2. Recon (uncoarsened) objective — 아무도 지배하지 못한다

`bestObj` (=seed-only이므로 `initObj`) 평균:

| factor | flooring | ceiling | lookahead | ceil−floor | look−floor | ceil−look |
|---:|---:|---:|---:|---:|---:|---:|
| 1  | 62006.8 | 62006.8 | 62006.8 | 0.0 | 0.0 | 0.0 |
| 2  | 63943.7 | 63938.1 | 63938.1 | −5.6 | −5.6 | 0.0 |
| 4  | 63578.6 | 63629.3 | 63625.5 | 50.7 | 46.9 | 3.8 |
| 8  | 64553.2 | 64389.0 | 64428.8 | −164.2 | −124.4 | **−39.8** |
| 16 | 64913.9 | 64653.6 | 64738.5 | −260.3 | −175.4 | **−84.9** |

- **per-instance 지배 (factor>1, n=40)**: `lookahead ≤ ceiling` **36/40**(위반 4),
  `lookahead ≤ flooring` **29/40**(위반 11), lookahead가 3자 중 최소 **27/40**.
- factor 8·16에서 **ceiling이 recon 평균 최저** (ceil−look < 0). ← "lookahead가 ceiling에
  뒤진다"의 정체.

worst lookahead>ceiling (recon):

| instance | factor | flooring | ceiling | lookahead |
|---|---:|---:|---:|---:|
| Instance_50_5_3_0,6_0,2_10_Rep1 | 16 | 45581 | 44940 | 45581 |
| Instance_50_5_3_0,6_0,2_10_Rep3 | 8  | 54025 | 53828 | 54112 |
| Instance_50_5_5_0,6_0,2_10_Rep2 | 16 | 43213 | 42969 | 43177 |

---

## 3. 왜 coarse 지배가 recon 지배로 이어지지 않는가 (핵심)

`idle_mode`가 실제로 영향을 주는 범위는 **coarse seed 뿐**이다 (plan §2.1).

파이프라인:
1. v4 풀은 6개 후보(priority×{sd,rd})를 만들고, 각 후보를 `insert_idle_time(time_factor=K,
   idle_mode)`로 배치한 뒤 **coarse wET**로 점수 매겨 `argmin`을 seed로 선택한다.
   → `idle_mode`는 (a) 각 후보의 coarse 점수와 (b) 각 후보 last-stage의 coarse 시작시각을 바꾼다.
2. 선택된 coarse seed를 `reconstruct_coarse_schedule`(schedule_build.py:101)로 원본 스케일 복원:
   coarse start ×K로 팽창 → `build_schedule_from_op_starts`가 **시작시각 기준으로 기계 재배정** →
   `make_semi_active` + `insert_idle_time(**flooring, factor=1**)`로 **fine-grid에서 배치를 재도출**.

즉 **coarse의 idle 배치는 복원 단계에서 버려지고**, 살아남는 것은 선택된 seed의 *구조*
(시퀀스·기계배정)뿐이다. fine grid에서 idle을 다시 넣으므로(K=1이라 floor=ceil=정확 최적) 최종
E/T는 사실상 **선택된 시퀀스의 fine-최적 E/T**가 된다.

따라서:
- **coarse obj = 선택 프록시**. lookahead는 이 프록시를 (설계상) 완벽히 최소화한다 → coarse 지배 40/40.
- 그러나 recon obj는 **다른 목적함수**(fine-grid 재최적화 결과). 프록시를 더 낮추는 것이 recon을
  더 낮추는 것을 **보장하지 않는다**. lookahead는 coarse 점수가 가장 낮은 seed를 고르지만, 그 seed의
  *시퀀스*가 다른 모드가 고른 시퀀스보다 fine-grid에서 더 나쁠 수 있다 (프록시-과적합).
- ceiling이 factor 8·16 recon에서 유리한 경향: ceiling은 early/경계 블록을 무조건 `⌈d/K⌉`(즉 `K·C≥d`,
  window의 tardy 쪽)로 한 칸 더 민다. 이 "overshoot" 위치가 ×K 팽창 후 fine 재-flooring 시 실제
  due window에 더 근접한 구조로 복원되는 경우가 많다. lookahead/flooring은 `K·C<d`(early 쪽)를
  유지해 복원 구조가 더 이른 쪽으로 치우친다. 다만 2차 효과라 instance 의존적(recon 위반은 4/40).

**정리**: 기대("lookahead ≥ 나머지")는 **coarse layer에서 성립**하고 그게 맞는 층위다. recon layer는
seed 선택 프록시 최적화 문제가 아니라 복원 후 재최적화 문제라서, 세 모드 중 누구도 지배하지 못한다.
recon에서 lookahead 지배를 원하면 **선택 기준을 coarse wET가 아니라 reconstructed obj로** 바꿔야
한다 (후속 실험 후보; 6후보를 실제로 복원해 recon-argmin 선택).

---

## 4. 등가성 검증 (요청 §3)

`scripts/ceiling_equiv_test_20260702.py` — in-place ceiling (`K·C'` vs 원본 `d`,
breakpoint `⌈d/K⌉`) ≟ pre-coarsen OLD (window를 `⌈d/K⌉`로 미리 coarsen 후 plain Pan):

- **① partition 동치** (200k random): 불일치 **0건** — `(K·C' < d) ⟺ (C' < ⌈d/K⌉)`.
- **② 스케줄/obj 동치** (100k random single-machine): 불일치 **0건** — coarse 완료시각·
  원본-window obj 동일. ⟹ §1.3 증명 경험적 확인.

---

## 5. Artifacts / 스크립트
- `scripts/dump_csr_coarse_obj.py` — 결정론적 두-층위 obj 재도출 (production 경로 재사용).
- `scripts/analyze_csr_idle_modes.py` — 피벗 + 지배성 + summary 교차검증.
- `scripts/ceiling_equiv_test_20260702.py` — ceiling ≡ OLD 등가성 (partition + 스케줄).
