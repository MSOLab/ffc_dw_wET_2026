# CSR time-budget scaling sweep 실험 계획

> 선행: `plans/20260714/csr_full_grid_k248_selection.md`
> (full 1440-grid × K{2,4,8} × 2 scenario, **25% budget 고정**에서
> best (scenario, K) 선택 — overall best = `csr_full_d2wp_k2`, mean RPDf 15.19%).
> 본 문서는 그 다음 축이다: **CSR budget 비율 자체를 스윕**해 25% 고정이
> 최적이었는지, 어느 비율에서 RPDf가 최저인지를 본다.
> **구현·config 생성·실행은 별도(다음) 대화에서** 수행. 본 계획만 작성.

## 목적 (비율 = 유일한 스윕 변수)

선행 선택 실험은 CSR budget을 표준예산의 **25%로 고정**하고 (init-flow, K)만
비교했다. 본 run의 질문:

> **표준 reference 예산(outer `0.09nc`) 대비 CSR 단계에 얼마의 비율을 배정할 때
> full 1440-grid에서 weighted E/T RPDf가 최저인가?** 비율↑ = coarse 문제를 더
>오래 풂(품질↑ 기대) vs 비율↓ = 같은 문제를 더 적은 시간에 (efficiency↑).
> 이 marginal value 곡선의 최적점과 diminishing-returns 지점을 찾는다.

25%는 이미 선행 run(`20260714T184236_642971`)에 있으므로 재사용하고, 본 run은
**나머지 다섯 비율 {5, 10, 15, 20, 30}%**만 돌린다.

## 시간예산 구조 (사전 확인 — 설계 근거)

시나리오 flow는 outer step이 `coarsen_solve_reconstruct`(CSR) **하나뿐**이다.

- CSR의 `timelimit`이 **child controller의 글로벌 예산**이 되어 inner
  `solve_flow` 5스텝(mcf→flip→neh→sw_cp→**base_cp**) 전체를 묶어 제한한다.
  각 inner step의 effective TL = `min(자기 TL, child 잔여)`.
- **base_cp(`solve_base_model_cpsat`)는 coarse 인스턴스에서 돈다** — solve_flow
  전체가 coarsened scale에서 실행되고, reconstruction은 마지막에 1회 deflate.
  base_cp는 명시적 TL이 없어 **CSR 잔여 예산을 자동 흡수**한다.
- outer `0.09nc`는 "표준 총예산 기준선"일 뿐, CSR eff = `min(0.09·f, 0.09)`
  = `0.09·f`이라 outer는 bind하지 않는다. 전 비율에서 outer는 `0.09nc` 고정.

## 스케일링 규칙 (핵심 설계 결정 — 비례 스케일)

비율 f를 바꿀 때 **모든 nc-표기 TL을 `s = f / 0.25` 배로 비례 스케일**한다
(25% 기준값 × s). 즉 CSR 예산 안의 내부 배분 비율(neh=CSR의 30%, flip=10% 등)을
전 비율에서 **동일하게 유지**해 스윕 변수를 "CSR 총예산" 하나로 고립시킨다.

| f (%) | s=f/.25 | CSR `timelimit` | flip `cp_tl` | neh `total_timelimit` | sw_cp `kappa`* |
|------:|-------:|----------------:|-------------:|----------------------:|---------------:|
| 5  | 0.2 | `0.0045nc`  | `0.00045nc` | `0.00135nc` | `0.00025` |
| 10 | 0.4 | `0.0090nc`  | `0.00090nc` | `0.00270nc` | `0.00050` |
| 15 | 0.6 | `0.0135nc`  | `0.00135nc` | `0.00405nc` | `0.00075` |
| 20 | 0.8 | `0.0180nc`  | `0.00180nc` | `0.00540nc` | `0.00100` |
| (25 base) | 1.0 | `0.0225nc` | `0.00225nc` | `0.00675nc` | `0.00125` |
| 30 | 1.2 | `0.0270nc`  | `0.00270nc` | `0.00810nc` | `0.00150` |

\* `non_time_fixed_op_time_limit_multiplier` (sw_cp `batch_tl_mode: proportional`).
윈도별 CP 초예산 = `kappa × non_time_fixed_op_count` → **실제 시간예산 knob**이므로
비례 스케일 대상. `neh`는 `csr_full`/`csr_neh` 양쪽 공통, `flip`은 `csr_full`만.
`mcf`(`calc_mcf_lb_and_derive_full_sch`)는 TL 인자가 없어 스케일 없음. `base_cp`는
CSR 잔여를 흡수하므로 자동으로 s배 스케일된다.

**왜 fixed-inner(=inner TL 고정, CSR만 변경)가 아닌가:** inner를 고정하면 작은
비율에서 flow가 degenerate된다. 예) f=5% → CSR=`0.0045nc`인데 neh 고정값
`0.00675nc` 하나가 CSR 예산을 초과 → neh가 min으로 잘리며 예산을 독식하고
sw_cp/base_cp가 굶는다. "얼마나 실행됐나"가 비율에 따라 질적으로 달라져 budget
효과와 flow-구성 효과가 교란된다. 비례 스케일은 전 비율에서 동일 flow 형태를
보장한다. **(다음 대화에서 다른 의도였다면 여기서 veto — 예: base_cp에만 예산을
몰아주는 별도 축은 이 실험과 분리해 따로 설계.)**

## 범위

- **Instances**: full **1440-grid** (`benchmarks/PRA2017/large`, subset 아님).
- **비율 f**: **5, 10, 15, 20, 30%** (25%는 선행 run 재사용).
- **K (`factor`)**: **1, 2, 4, 8**. K=1 = coarsening 없음(factor 1, 원해상도)
  기준선 — coarsening 이득/손실을 원scale 대비 절대측정. K{2,4,8}은 선행 브래킷.
- **Scenario**: `csr_full_d2wp`(mcf→flip→neh→sw_cp→base_cp) /
  `csr_neh_d2wp`(neh→sw_cp→base_cp), 둘 다 `due2-weight-pos`.
- **총 시나리오**: 5 f × 4 K × 2 flow = **40**.
- **총 실행 수**: 1440 × 40 = **57,600 instance-run**.

## Config (다음 대화에서 작성)

`metadata/20260714/csr_full_grid_k248.yaml`를 베이스로:

- 파일명 제안: `metadata/20260714/csr_tl_scaling_sweep.yaml`.
- `output_dir: output/20260714_csr_tl_scaling_sweep` (제안).
- 헤더(`benchmark_dir`, `ins_index_source`, `bks_table_csv_path`) 동일,
  `ins_index` 없음(full-grid).
- 시나리오 40개 생성. **naming**: `csr_full_d2wp_k{K}_tl{FF}` /
  `csr_neh_d2wp_k{K}_tl{FF}` — `K∈{1,2,4,8}`, `FF∈{05,10,15,20,30}`.
  각 시나리오는 선행 config의 동명 K 시나리오를 복사하고 위 표대로 4개 TL
  (CSR `timelimit`, flip `cp_tl`, neh `total_timelimit`, sw_cp `kappa`)만 s배.
  나머지 solver 설정(thread 8, batch_size, pf_method 등)은 그대로.
- outer `timelimit: 0.09nc` 전 시나리오 고정.
- **Plotting off** (43,200 run): `draw_gantt: false`,
  `draw_progress_plot: false`, `painter_thread_cnt: 1`.
- `instance_worker_cnt: 12` (12×8=96 = 물리코어, memory:machine-core-count).
- `main.py`의 `CONFIG_PATH`를 이 파일로.

## 판정 지표 (RPDf — 선행과 동일, optimality gap 아님)

- **1차: BKS 대비 RPDf** (`rpdf_comparison.csv`, `RPDf_BKS_data`, 대칭 RPD
  `2(obj−ref)/(obj+ref)`). 25%는 선행 run CSV에서 붙여 **6개 비율 곡선**
  (f → mean/median RPDf) 을 (K, flow) 조합별로 그린다.
- **곡선 판독**: (a) 각 (K, flow)의 **best f**, (b) **diminishing returns**
  지점(f↑에도 RPDf 개선이 멈추는 곳 = 실질 최소 필요 예산), (c) 선행에서
  발견한 regime 의존성(T≤0.4 저-K 압승 / T=0.6 tie)이 f에 따라 바뀌는지 —
  특히 저-K가 큰 예산에서 starvation을 벗어나 T=0.6에서도 이기기 시작하는지.
- **budget starvation 측정(부가)**: coarse `no feasible (status=UNKNOWN)`
  양성 warning **빈도 vs f** — 작은 f에서 증가 예상. 이는 결함이 아니라
  "실질 최소 예산 미달"의 직접 증거(§리스크).
- **하지 말 것**: `obj_value==obj_bound` optimality gap 판정(coarse
  `time_factor>1`로 `obj_bound` loose). 비교는 상대지표(RPDf)로만. **예외 —
  K=1**: coarsening 없어 `time_factor==1`이라 `obj_bound`(MCF LB)가 억제되지
  않음 → K=1 행에 한해 `obj_value==obj_bound` 판정이 유효(부가 신호). 단 K간
  비교는 여전히 RPDf로 통일(K≥2는 gap 무효).
- **sanity(게이트 재확인)**: 완료 후 두 불변식 warning
  (`insert_idle_time left E/T`, `post-process objective >`) 및
  Traceback/AssertionError가 0인지 재확인(회귀 감지).

## 규모 / 런타임 / 실행

- **런타임 외삽**: 선행 8640 run @ f=0.25 = **14,265초(≈3h58m)**
  (12 worker × 8 thread = 96 물리코어). CP solve 시간은 f에 비례, 인스턴스
  IO·mcf·model build·reconstruction은 고정 오버헤드. K{2,4,8} 6 combo만 보면
  `Σf/0.25 = (5+10+15+20+30)/25 = 3.2배` → **~12.7h**. K=1(원해상도) 2 combo
  추가로 combo 수 6→8(×4/3), 게다가 K=1은 최대 coarse 문제(coarsening 없음)라
  CP가 TL을 다 쓰는 쪽 → 하단이 아닌 상단으로 치우침. 고정 오버헤드까지 더해
  **~17–21h** (overnight+).
- **축소 레버(다음 대화에서 선택 가능)**: 첫 패스를 160-subset로 돌려 곡선
  형태만 잡거나, 비율을 {10, 20, 30}%로 줄이면 런타임이 크게 감소. 본 계획은
  full-grid × 5비율을 기본으로 하되, 오래 걸리는 점을 명시.
- **실행**: background + 완료 알림. 완료 후 §판정 지표 순서로 분석.

## 분석 산출물 (실행 후)

1. 본 문서에 **`## 결과 (실행 후)`** append: (K, flow)별 f→RPDf 곡선 표
   (6점: 5/10/15/20/25/30%, 25%는 선행에서) + best f + diminishing-returns
   지점 + starvation warning 빈도 vs f + 결론.
2. 선행 선택 실험의 best (`csr_full_d2wp_k2`)에 대해 **최적 f**를 확정 —
   후속(더 긴 TL, tail 확장) 기준선 갱신.

## 리스크 / 관찰 포인트

- **작은 f budget starvation**: f=5%는 coarse CP가 feasible을 못 찾을 수 있음
  (특히 K=2 × n=200 = 최대 coarse 문제 — 선행에서 f=25%에도 1건 발생). warning
  빈도↑가 예상되며 이는 측정 대상(실질 최소 예산의 하한 신호), 결함 아님.
- **비례 스케일 kappa 가정**: kappa를 시간예산 knob으로 보고 스케일했다. 만약
  kappa를 알고리즘 상수로 고정하고 싶다면(sw_cp 윈도 해상도 불변) 다음 대화에서
  조정 — 단 그 경우 작은 f에서 sw_cp가 예산을 초과해 잘릴 수 있음.
- **런타임**: ~13–16h. 실행 전 축소 레버 검토 권장.
- **회귀 감지**: 새 비율에서 게이트 warning/assert가 나오면 최우선 조사(알고리즘은
  brute-force property test로 K∈{1..50} 커버, 가능성 낮음).

## 참고

- 선행 선택 실험: `plans/20260714/csr_full_grid_k248_selection.md`
  (25% 고정, best `csr_full_d2wp_k2`, §"결과 (실행 후)").
- 25% baseline run: `output/20260714_csr_full_grid_k248/20260714T184236_642971`.
- CSR 시간예산 구조: `src/ffc_ddw_sum_et/orchestration/controller.py`
  `coarsen_solve_reconstruct` (L2644~), child-controller flow 위임.
- sw_cp kappa 의미: `src/ffc_ddw_sum_et/algorithm/sw_cp/dispatcher.py` L288~291
  (`per-CP TL = kappa × non_time_fixed_op_count`).
- instance parameter/RPDf 정본 소스: `AGENTS.md` §"PRA2017 instance parameters".

## 결과 (실행 후)

> **재현**: 이 절의 모든 표·수치는 단일 명령으로 재생성된다 —
> `uv run python scripts/analyze_csr_tl_scaling_sweep.py <sweep> <25%-baseline> <K1-gapfill>`
> (인자 없으면 아래 sweep run + 25% baseline run을 기본으로 읽음; K=1 f=25점을
> 채우려면 gap-fill run을 3번째 인자로 전달 — 아래 명령 참조. 7블록 = f→RPDf 곡선 /
> best f·Δ / T-분해 / full-vs-neh paired / sanity gate / starvation / K=1 optimality).
>
> ```bash
> uv run python scripts/analyze_csr_tl_scaling_sweep.py \
>     output/20260714_csr_tl_scaling_sweep/20260714T234921_531156 \
>     output/20260714_csr_full_grid_k248/20260714T184236_642971 \
>     output/20260714_csr_tl_scaling_sweep/20260715T183418_361919
> ```

- **Run**: `output/20260714_csr_tl_scaling_sweep/20260714T234921_531156`
  (config `metadata/20260714/csr_tl_scaling_sweep.yaml`, 2026-07-14 23:49 시작,
  elapsed **17:01:38**, 12 worker × 8 thread = 96 물리코어).
- **완결성**: 57,600 instance-run 전수 완료(40 scenario × 1440),
  `rpdf_comparison.csv` 57,600행, RPDf NaN 0, `work_status` 정상.
- **Sanity(게이트 재확인)**: 두 불변식 warning(`insert_idle_time left E/T`,
  `post-process objective >`) **0**, Traceback/AssertionError **0** — 회귀 없음.
  양성 warning은 §starvation 참조(전량 최소예산×최대문제에 집중, 결함 아님).
- **f=25% 곡선점**: K{2,4,8}는 선행 run(`20260714T184236_642971`)에서, **K=1은
  gap-fill run(`20260715T183418_361919`, `csr_{full,neh}_d2wp_k1_tl25` 2 scenario
  × 1440)에서** 붙임 — 이제 全 8조합이 6점(5/10/15/20/25/30) 완비. gap-fill로
  F_k1의 20→30 구간이 실측되어 곡선 단조성이 확인됨(20→25 −1.9, 25→30 −1.2%p).

> **지표**: `RPDf_BKS_data × 100`(대칭 RPD `2(obj−ref)/(obj+ref)`, range ±200%,
> `ref=BKS_data`). 음수 = BKS 초과. optimality gap 미사용(K≥2 `obj_bound` loose;
> K=1 예외는 §K=1 optimality).

### 등예산 setting 비교 (핵심) — 각 f-열을 **세로로** 읽어라

> best f는 비교 대상이 아니다: 예산이 크면 결과가 좋아지는 건 자명하므로 각 조합의
> "최적 f"(=30%, 곡선 오른쪽 끝)를 논하는 건 의미가 없다. **의미 있는 질문은 "같은
> 예산 f에서 어느 setting이 이기나"** — 즉 아래 표를 **행(setting)이 아니라 열(f)
> 방향으로** 읽는 것이다. `F`=csr_full_d2wp, `N`=csr_neh_d2wp. **굵게** = 각 f-열
> 최저 mean(그 예산의 승자). 세 range(overall / T=0.6 / (T,R)=(0.6,0.2))에서 재현:
> §"재현" 명령의 `report_equal_budget` 블록.

**모든 range·모든 f에서 열 승자는 예외 없이 `F_k1`.** 등예산 비교의 결론은 한
줄이다 — **coarsening 없이(K=1) full-init flow가 어떤 예산에서도 최선.**

**(1) overall (n=1440/셀) — mean% (median%)**

| 조합 | f=5 | f=10 | f=15 | f=20 | f=25 | f=30 |
|------|----:|-----:|-----:|-----:|-----:|-----:|
| **F_k1** | **26.51 (33.90)** | **6.10 (19.76)** | **0.55 (15.69)** | **−2.59 (13.20)** | **−4.44 (11.75)** | **−5.60 (10.42)** |
| F_k2 | 56.55 (62.51) | 33.60 (43.04) | 24.91 (31.72) | 19.06 (26.90) | 15.19 (23.45) | 12.92 (20.71) |
| F_k4 | 64.37 (74.92) | 41.82 (52.16) | 31.57 (39.49) | 25.95 (31.78) | 21.64 (26.16) | 19.81 (24.19) |
| F_k8 | 69.34 (78.56) | 47.18 (55.39) | 38.08 (42.26) | 33.16 (35.99) | 29.74 (31.18) | 27.26 (28.96) |
| N_k1 | 50.46 (64.85) | 35.05 (45.99) | 27.50 (34.43) | 21.97 (28.45) | 17.96 (24.60) | 15.11 (22.51) |
| N_k2 | 51.42 (64.94) | 34.98 (45.93) | 27.36 (35.22) | 22.37 (29.57) | 17.77 (25.00) | 14.98 (22.00) |
| N_k4 | 57.00 (67.56) | 38.15 (48.63) | 29.87 (36.98) | 24.76 (29.89) | 20.05 (25.55) | 17.73 (22.88) |
| N_k8 | 61.66 (70.17) | 43.96 (51.31) | 36.61 (41.54) | 31.66 (33.65) | 29.00 (30.76) | 26.19 (28.13) |

- F_k1의 2위 대비 격차는 등예산에서 **압도적**: f30 −5.60 vs 차선 N_k2 14.98
  (**−20.6%p**), f5 26.51 vs 차선 N_k1 50.46 (−23.9%p). 全 f에서 20%p 안팎 우위.

**(2) T=0.6 (빡빡한 납기, n=480/셀) — mean%**

| 조합 | f=5 | f=10 | f=15 | f=20 | f=25 | f=30 |
|------|----:|-----:|-----:|-----:|-----:|-----:|
| **F_k1** | **38.39** | **29.53** | **25.76** | **23.24** | **21.87** | **20.74** |
| 차선(비-F_k1) | N_k1 56.09 | N_k8 43.69 | N_k8 35.91 | N_k8 29.92 | N_k4 26.02 | N_k4 22.91 |
| Δ(F_k1−차선) | −17.7 | −14.2 | −10.2 | −6.7 | −4.2 | **−2.2** |

- T=0.6에서도 F_k1이 全 f 승자. 선행이 관측한 "T=0.6은 高-K(neh_k8) 근소 우위"는
  K=1을 비교에서 뺀 착시였고, K=1을 넣으면 빡빡한 납기에서도 F_k1이 이긴다. 다만
  예산이 커질수록 격차 축소(f30 −2.2%p) — 이 regime이 K=1 우위가 가장 얇은 구간.

**(3) (T,R)=(0.6,0.2) (빡빡·좁은 납기창, n=160/셀) — mean%**

| 조합 | f=5 | f=10 | f=15 | f=20 | f=25 | f=30 |
|------|----:|-----:|-----:|-----:|-----:|-----:|
| **F_k1** | **35.62** | **28.55** | **25.87** | **24.12** | **23.40** | **22.83** |
| 차선(비-F_k1) | F_k2 52.95 | F_k2 44.88 | N_k8 41.34 | N_k8 36.11 | N_k8 32.16 | N_k4 30.32 |
| Δ(F_k1−차선) | −17.3 | −16.3 | −13.5 | −12.0 | −8.8 | **−7.5** |

- 가장 어려운 range에서도 F_k1 全 f 승자. 여기선 예산이 커져도 격차가 −7.5%p로
  넉넉히 유지 — 좁은 납기창일수록 K=1(원해상도) 우위가 오히려 더 견고.

### best f (참고, setting 선택 기준 아님) — 예산 효율 곡선

> best f=30%는 "곡선의 오른쪽 끝"일 뿐 setting 비교와 무관(위 §등예산 비교 참조).
> 아래는 예산-대비-이득 효율(체감) 참고용.

- **곡선은 [5,30] 구간에서 단조 감소, 30%에서도 아직 내려가는 중**(plateau 미도달).
  즉 f를 30% 위로 올리면 더 좋아질 여지가 있으나, 그건 예산 증대 효과이지 setting
  우열과 무관.
- **Δmean/+5%p**(예: F_k2): 5→10 **−23.0**, 10→15 −8.7, 15→20 −5.8, 20→25 −3.9,
  25→30 −2.3. 전 조합 동형: 첫 스텝(5→10)이 −15~−23%p로 압도적, 이후 기하급수적
  체감(−9→−5→−4→−2%p). **실질 최소예산 하한 ≈ 10%**(5→10에서 대부분 회수),
  10% 이상은 완만한 추가이득(체감하나 0 아님 → 계속 상향할 가치 有).

### K-차원 발견 (선행 결론 반전) — **coarsening이 등예산에서 품질을 해친다**

- **K=1(coarsening 없음, 원해상도)이 모든 K와 모든 f에서 최저.** F_k1@f30 =
  **−5.60%** (BKS 평균 초과) vs 차선 N_k2@f30 14.98%, 선행 winner F_k2@25% 15.19%.
  **~18~21%p 격차** — 등예산 하에서 coarsen→solve→reconstruct는 원scale 직접
  풀이 대비 순손실. init-flow가 강한 **full flow에서 K=1 우위가 특히 큼**
  (F_k1≪F_k2). neh-only는 K1≈K2 (N_k1 15.11 ≈ N_k2 14.98) — 약한 init에선
  coarsening 손실이 작아짐.
- **단조성 K1<K2<K4<K8** (거의 전 f). 큰 K = 작은 coarse 문제지만 reconstruction
  손실·해상도 저하가 등예산 이점을 압도.

### T-regime 분해 (mean RPDf%, n=480/셀) — 선행의 "T=0.6 高-K 우위" 반전

| T | F_k1@30 | F_k2@30 | F_k8@30 | N_k1@30 | N_k8@30 |
|---|--------:|--------:|--------:|--------:|--------:|
| 0.2 (느슨) | **−45.1** | −15.4 | 9.9 | −13.2 | 8.6 |
| 0.4 (중간) | **7.5** | 29.3 | 48.2 | 34.2 | 46.7 |
| 0.6 (빡빡) | **20.7** | 24.9 | 23.7 | 24.4 | 23.3 |

- **K=1이 전 T-regime에서 우위** — 선행이 관측한 "T=0.6에서 高-K(neh_k8)가 근소
  최저"는 **K=1을 비교에 안 넣어 생긴 착시**였다. K=1을 넣으면 빡빡한 납기(T=0.6)
  에서도 F_k1(20.7%)이 全 K를 제친다. 느슨(T=0.2)에선 격차가 폭발(F_k1 −45%p).
- 즉 **regime-의존적 best-K는 사라지고, "coarsening 안 함(K=1)"이 uniform 승자**.

### budget starvation vs f (양성 warning `no feasible / UNKNOWN`)

| scenario | warning 파일 수 |
|----------|----------------:|
| csr_full_d2wp_**k1_tl05** | **15** |
| csr_full_d2wp_k2_tl20 | 1 |
| csr_neh_d2wp_k2_tl15 | 1 |

- 총 17건, **88%가 K=1×f5**(원해상도 = 최대 문제 × 5% = 최소예산)에 집중 — 계획이
  예상한 "실질 최소예산 미달"의 직접 증거. f≥10에선 K=1 starvation 0. 결함 아님
  (해당 인스턴스도 후속 step으로 유효 RPDf 산출). **f=5%가 K=1의 하한 미달선.**

### K=1 optimality (부가 신호 — K=1에서만 유효)

- K=1은 `time_factor==1`이라 `obj_bound`(MCF LB)가 억제 안 됨 → `obj_value==
  obj_bound` proof 유효. `csr_full_d2wp_k1_tl30`: **114/1440(7.9%) 증명 최적**,
  `_tl05`: 70/1440(4.9%). 예산↑에 optimal 수↑. K≥2는 gap 무효라 RPDf로만 비교.

### csr_full vs csr_neh — init-flow 비교 (K-교호작용, main effect 아님)

full·neh는 **동일 1440 instance를 matched (K,f)로** 돌리므로 per-instance paired
비교 가능(tie tol = obj 상대 1e-6). gap = full−neh mean RPDf%(음수 = full 우세).

**K별 (all f, n=8640/셀; K=1은 7200):**

| K | full mean% | neh mean% | gap | 승자 | win full/tie/neh |
|---|----------:|---------:|----:|:----:|-----------------:|
| 1 | 5.00 | 30.02 | **−25.02** | full | **68 / 9 / 23%** |
| 2 | 27.04 | 28.14 | −1.11 | full | 46 / 8 / 45% |
| 4 | 34.19 | 31.26 | **+2.93** | **neh** | 39 / 8 / **54%** |
| 8 | 40.80 | 38.18 | **+2.62** | **neh** | 37 / 7 / **56%** |

- **crossover가 존재한다.** full의 우위는 전적으로 **강한 init(mcf_lb→flip)**에서
  나오며, 이 init은 **coarsening이 약할 때만(K≤2)** 값을 한다. K=1에서 full이
  neh를 압도(gap −25%p, 68% 승)하지만 **K≥4에선 neh가 역전**(54~56% 승). 큰 K는
  coarse scale에서 mcf/flip init을 훼손 → 두 스텝이 예산만 축내고, neh는 같은
  예산을 sw_cp+base_cp에 몰아 더 나은 해를 낸다.
- **overall(all K,f) gap −4.28는 main effect가 아니라 K=1 한 셀이 끌어올린 평균**
  (win rate는 47/8/46%로 사실상 tie). f별로도 full이 전 구간 근소 우세(gap −1~−6.5,
  중간 f15/20에서 최대)지만 이 역시 K=1 가중.
- **T·f30 교차 확인**: K=1은 全 T에서 full 압승(T=0.2 gap −31.9%p, T=0.4 −26.6%p,
  T=0.6 −3.7%p); K=2는 T≤0.4만 full, T=0.6은 neh; K≥4는 全 T에서 neh. 즉
  **init-flow 선택은 K에 종속** — "full이 낫다/neh가 낫다"는 단독 참이 아니다.

### 결론 — best (scenario, K, f) 및 후속 기준선

1. **overall winner = `csr_full_d2wp_k1` @ f=30%** (mean **−5.60%** / median 10.42%).
   선행 winner `csr_full_d2wp_k2`(@25% 15.19%)를 **~21%p 갱신**. 후속 실험(더 긴
   TL, tail 확장) **기준선을 K=1 full flow로 교체**.
2. **최적 f는 아직 미확정 — ≥30%.** 30%에서도 곡선이 내려가는 중이므로 다음 축은
   **f를 30% 위로 (예: 40/50/75/100%) 확장**해 plateau/최적점을 잡는다. 단
   5→10 스텝이 이득의 대부분이라 f≥10은 이미 "실용 예산".
3. **coarsening 재평가 필요.** 이 instance-scale(PRA2017 large)·이 예산대에선
   coarsening이 등예산 품질을 순수하게 해친다(K1≪K2≪K4≪K8, 전 regime). CSR의
   가치는 "원scale이 예산 내 안 풀리는 더 큰 문제"에서만 날 수 있음 — 별도 축으로
   더 큰 instance 또는 더 짧은 예산에서 K>1의 손익분기를 찾는 실험이 필요.
4. **init-flow(full vs neh)는 K에 종속 — 고정 선택 금지.** K≤2에선 full(강한
   mcf→flip init)이, K≥4에선 neh(init 생략, 예산을 solve에 집중)가 이긴다(§비교).
   winner인 K=1에선 full이 압도적이므로 후속 기준선 `csr_full_d2wp_k1`과 정합.
   단 만약 후속에서 K>1을 다시 볼 경우 init-flow도 K와 함께 재선택할 것.

### 남은 분석(옵션)

pairwise win/tie·size-group(job/stage) 승자 분해, `scripts/build_results_index.py`
→ `analysis/results_index_*.csv`는 미실행. 위 T·K·f 분해로 핵심 신호(K=1 uniform
우위, f≥30 미포화, starvation 하한)는 확정. size-group 분해가 필요하면 별도 수행.

## 사후기록 — K=1 × f=25% gap-fill (2026-07-15)

메인 스윕은 f=25% 열을 선행 fixed-budget run(`csr_full_grid_k248`)에서 붙였는데
그 선행은 K∈{2,4,8}만 돌려 **K=1의 25%점이 비어** 있었다(§결과 표의 F_k1/N_k1
`f25=—`). winner인 `csr_full_d2wp_k1`의 곡선이 하필 무릎 구간(20→30)에서 한 칸
비어 단조성이 실측이 아닌 보간이었다. 그 한 셀을 실측으로 채운 기록.

- **한 일**: 누락된 두 scenario만 전수 실행 —
  `csr_full_d2wp_k1_tl25`, `csr_neh_d2wp_k1_tl25`
  (factor 1 = 25% 기준행: CSR `0.0225nc` / flip `0.00225nc` / neh `0.00675nc` /
  kappa `0.00125`), 1440-grid × 2 = **2,880 instance-run**.
- **config**: `metadata/20260715/csr_tl_scaling_k1_tl25_gapfill.yaml`
  (메인 스윕 config에서 두 scenario만 남기고 `output_dir`은 동일
  `output/20260714_csr_tl_scaling_sweep`, plotting off, `instance_worker_cnt: 12`).
- **run**: `output/20260714_csr_tl_scaling_sweep/20260715T183418_361919`
  (2026-07-15 18:34 시작, ~1:18 elapsed). 전수 완료, `rpdf_comparison.csv` 2,880행,
  sanity 게이트 0/0/0/0(회귀 없음), starvation은 최소예산셀 밖이라 이 run엔 없음.
- **분석 통합**: `scripts/analyze_csr_tl_scaling_sweep.py`를 N-dir 입력으로 확장
  (`FF_TO_F`에 `"25"` 디코드 추가 + argparse에 `extra_run_dirs` 가변인자, 게이트
  블록 5–7도 sweep+gap-fill을 함께 스캔). 위 §결과 "재현" 명령의 3-인자 형태로
  실행하면 F_k1/N_k1의 f=25 셀이 자동으로 채워진다.
- **결과 (기존 결론 불변, 완결성만 보강)**: **F_k1 @ f25 = −4.44% (median 11.75)**,
  **N_k1 @ f25 = 17.96% (24.60)**. F_k1 곡선의 20→25→30 marginal이 이제 실측
  −1.9 → −1.2%p로 **단조 감소 확인**(보간 아님). best f=30%·K=1 uniform 우위·winner
  `csr_full_d2wp_k1@f30 −5.60%`는 그대로. K=1 optimality도 tl25에서
  full 111/1440(7.7%), neh 110/1440(7.6%)로 인접 f와 정합.
- **분석 관점 교정 (등예산 비교 추가)**: best f는 예산 증대 효과일 뿐 setting 우열과
  무관하므로, "같은 f에서 어느 setting이 이기나"를 **overall / T=0.6 / (T,R)=(0.6,0.2)**
  세 range로 보는 `report_equal_budget` 블록을 스크립트에 추가(각 f-열의 최저 setting을
  `*`로 표시; `rpdf_comparison.csv`에서 `R` 열도 로드). 결론: **세 range·全 f에서 열
  승자는 예외 없이 F_k1** — 등예산에서 coarsening 없이 full-init가 항상 최선. §"등예산
  setting 비교" 표 참조.
