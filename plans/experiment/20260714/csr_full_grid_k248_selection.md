# CSR full-grid K∈{2,4,8} setting-selection 실험 계획

> 선행: `plans/experiment/20260714/coarse_exact_higher_k_validation.md`
> (K∈{2,4,8,16,32} × 2 scenario, 160-subset **정확성 게이트** — warning/crash 0
> 통과). 본 문서는 그 다음 단계인 **품질 선택 실험**이다: 통과된 setting 중
> promising한 조합을 **full 1440-grid**로 돌려 best (scenario, K)를 고른다.
> **구현·실행은 별도(다음) 대화에서** 수행. 본 계획은 커밋에 미포함.

## 목적 (게이트가 아니라 선택)

선행 run은 "coarse-exact `insert_idle_time`이 모든 K에서 안전한가"(warning 0)를
물었고 답은 예였다. 본 run은 질문이 다르다:

> **fixed budget 하에서 어떤 (init-flow, K) 조합이 full 1440-grid에서
> 가장 낮은 weighted E/T (= 최저 RPD)를 내는가?**

정확성은 이미 확보됐으므로 여기선 **해 품질**만 본다. 판정은 warning grep이 아니라
**BKS 대비 RPD + pairwise win/tie**로 한다(§판정 지표).

## 범위

- **Instances**: full **1440-grid** (subset 아님). `benchmarks/PRA2017/large`.
- **K (`coarsen_solve_reconstruct.factor`)**: **2, 4, 8**.
  - 선행 160-subset에서 두 시나리오의 평균 obj minimum이 각각 K=2(csr_full)·
    K=8(csr_neh)에 위치 → {2,4,8}이 관측 최적점을 브래킷. 품질이 확실히 나쁜
    K=16/32는 제외(neh K32 300k / full K32 299k, K≤8 대비 ~8% 열위).
- **Scenario** (둘 다 `due2-weight-pos`):
  - `csr_full_d2wp` — `mcf_lb → flip → neh → sw_cp → base_cp` (full miniature).
  - `csr_neh_d2wp` — `neh → sw_cp → base_cp` (neh-only).
- **총 실행 수**: 1440 × 3(K) × 2(scenario) = **8640 instance-run**.

## Budget 정책 (equal-budget — 의도적으로 K 무관)

모든 6개 조합이 **동일 예산**을 받는다:

- outer scenario `timelimit: 0.09nc`
- CSR `coarsen_solve_reconstruct.timelimit: 0.0225nc` (= 25%)
- inner solve_flow TL: `neh 0.00675nc`, `flip 0.00225nc`(full only),
  `kappa 0.00125` — **전부 K=4 값 그대로**(= `metadata/20260713/csr_init_methods.yaml`
  동명 시나리오, 선행 validation config와 동일).

**근거 (validation과 다름 — 주의):** 선행 validation에선 TL 고정이 "cp_obj=0
witness를 늘려 불변식을 강하게 친다"였다. 여기선 목적이 선택이므로 근거가
**equal-budget 공정성**으로 바뀐다 — K에 따라 TL을 바꾸면 "K 효과"와 "budget 효과"가
교란된다. config는 동일하지만 *왜* 고정하는지가 다르다.

- 부수 효과(측정 대상): 작은 K(=2)는 coarse 문제가 커서 같은 TL 안에 CP-SAT가
  덜 끝날 수 있고, 큰 K(=8)는 coarse 문제가 작아 TL을 다 못 쓸 수 있다. 이
  트레이드오프의 순효과가 바로 "budget 고정 시 best K"이며 본 실험이 재는 값이다.

## 판정 지표 (RPD·win/tie — optimality gap 아님)

- **1차: BKS 대비 RPD** (`rpdf`). full-grid + `bks_table_csv_path`로 자동 산출.
  `report.xlsx`/`summary.csv`/`rpdf_comparison.csv` 및 run-root HTML 대시보드
  (`rpdf_dashboard`, `win_tie_dashboard`)에서 조합별 mean/median RPD 비교.
- **2차: pairwise win/tie** — 조합 간 per-instance 승패(품질 tie 처리 포함).
  특히 **size-group별 승자 분해**(작은/큰 job·stage에서 best K가 갈리는지)가 본
  full-grid의 핵심 부가가치.
- **하지 말 것 — optimality gap 판정**: coarse는 `time_factor>1`로 MCF LB가
  억제돼 `obj_bound=0`(loose global LB). `obj_value==obj_bound`로 "% optimal"을
  주장하면 안 된다. 비교는 **상대 지표(RPD·win/tie)로만**.
- **sanity (게이트 재확인, 부차적)**: 선행에서 warning 0을 이미 봤지만, full-grid는
  더 넓은 instance를 태우므로 완료 후 `--glob '*.log'`로 두 warning·
  `[WARNING]`·ERROR/Assert/Traceback가 여전히 0인지 한 번 확인(회귀 감지).

## Config (다음 대화에서 작성)

`metadata/20260714/csr_higher_k_validation.yaml`를 베이스로:

- `ins_index` **제거**(full 1440-grid), 나머지 헤더 동일
  (`benchmark_dir`, `ins_index_source`, `bks_table_csv_path`).
- `output_dir: output/20260714_csr_full_grid_k248` (제안).
- scenarios: 기존 10개 중 **`csr_full_d2wp_k{2,4,8}` / `csr_neh_d2wp_k{2,4,8}`
  6개만** 남기고 K16/K32 4개 삭제. solver 설정·inner flow·TL은 그대로.
- 파일명 제안: `metadata/20260714/csr_full_grid_k248.yaml`.
- `main.py`의 `CONFIG_PATH`를 이 파일로.

**Plotting**: 8640 run이라 per-instance 그림은 낭비 —
`draw_gantt: false`, `draw_progress_plot: false`, `painter_thread_cnt: 1`.
비교는 report-level CSV/HTML(자동 생성)로 충분.

## 규모 / 런타임 / 실행

- **런타임 외삽**: 선행 1600 run/47분(12 worker × 8 thread = 96 물리코어 매칭)
  → 8640 run ≈ **4~6시간**. 단, 본 run은 무거운 쪽(K=2 = 더 큰 coarse 문제,
  csr_full = 5-step inner flow)에 치우쳐 상단 예상.
- **병렬도**: `instance_worker_cnt: 12` 유지(12×8=96 = 물리코어, memory:
  machine-core-count). 메모리 여유가 확인되면 worker↑ 가능하나 12가 안전 기본.
- **실행**: background + 완료 알림. 완료 후 §판정 지표 순서로 분석.

## 분석 산출물 (실행 후)

1. 본 문서에 **`## 결과 (실행 후)`** append: 조합(6)별 mean/median RPD 표 +
   size-group별 win/tie 요약 + best (scenario, K) 결론 + run 경로.
2. `scripts/build_results_index.py` → `analysis/results_index_*.csv`로 조합 비교
   (weekly-review skill 흐름과 정합).
3. best setting을 후속 실험(더 긴 TL, tail 확장 등)의 기준선으로 기록.

## 리스크 / 관찰 포인트

- **K 간 차이가 작을 수 있음**: 160-subset에서 K{2,4,8} 평균차는 ~1–3%. full-grid는
  이를 **좁은 신뢰구간**으로 확정하고 size별 승자를 분해하는 게 목적 — "새 신호"보다
  "확인 사살 + 논문급 표". 차이가 통계적으로 미미하면 그 자체가 결론(= K 견고).
- **작은 K의 budget 부족**: K=2가 TL 안에 CP-SAT를 못 끝내 품질이 나빠질 수 있음 —
  이는 결함이 아니라 equal-budget 하의 실제 트레이드오프(측정 대상).
- **회귀 감지**: full-grid에서 새 instance가 warning/assert를 내면 선행 게이트가
  놓친 케이스 → 최우선 조사(단, 알고리즘은 brute-force property test로 K∈{1..50}
  커버되어 가능성 낮음).

## 참고

- 선행 게이트: `plans/experiment/20260714/coarse_exact_higher_k_validation.md`
  (K-sweep validation, warning/crash 0, §"결과 (실행 후)").
- 근본 수정: `plans/experiment/20260714/cpsat_reconstruct_coarse_et_gap.md`
  (coarse-exact `insert_idle_time`, commit `9b7ad2a`).
- 베이스 config: `metadata/20260714/csr_higher_k_validation.yaml`(10 scenario 중 6개 발췌).
- 시나리오 원본 solver 설정: `metadata/20260713/csr_init_methods.yaml`
  (`csr_full_d2wp` / `csr_neh_d2wp`).

## 결과 (실행 후)

- **Run**: `output/20260714_csr_full_grid_k248/20260714T184236_642971`
  (config `metadata/20260714/csr_full_grid_k248.yaml`, 2026-07-14 18:42 시작).
- **완결성**: 8640 instance-run 전수 완료(6 scenario × 1440), `rpdf_comparison.csv`
  8640행, RPDf NaN 0.
- **Sanity(게이트 재확인)**: 두 불변식 warning(`insert_idle_time left E/T`,
  `post-process objective >`) **0**, Traceback/AssertionError **0** — 회귀 없음.
  단 양성 warning 1건: `csr_full_d2wp_k2`의 `Instance_200_5_5_0,4_1_20_Rep4`에서
  coarse sub-CP `no feasible solution (status=UNKNOWN)`. 이는 계획이 예상한
  **K=2 × n=200(최대 coarse 문제) equal-budget starvation**의 실물 증거이며
  결함 아님(해당 인스턴스도 후속 step으로 유효 RPDf 산출).

> **판정 지표 주의**: RPDf 는 대칭 RPD `2(obj−ref)/(obj+ref)`(range ±200%),
> `ref=BKS_data`. 음수 = BKS 초과. optimality gap 은 쓰지 않음(coarse `obj_bound`
> loose). 아래 % 는 모두 `RPDf_BKS_data × 100`.

### 요청된 세 그룹핑 × 6 조합 (mean / median RPDf%)

`F`=csr_full_d2wp, `N`=csr_neh_d2wp. **굵게** = 그룹 내 최저 mean.

**① overall (n=1440/조합)**

| 조합 | mean% | median% |
|------|------:|--------:|
| **full_k2** | **15.19** | **23.45** |
| full_k4 | 21.64 | 26.16 |
| full_k8 | 29.74 | 31.18 |
| neh_k2  | 17.77 | 25.00 |
| neh_k4  | 20.05 | 25.55 |
| neh_k8  | 29.00 | 30.76 |

**② T=0.6 (빡빡한 납기, n=480/조합)**

| 조합 | mean% | median% |
|------|------:|--------:|
| full_k2 | 27.28 | 23.57 |
| full_k4 | 26.95 | 21.94 |
| full_k8 | 26.44 | 22.21 |
| neh_k2  | 27.23 | 22.56 |
| neh_k4  | 26.02 | 21.98 |
| **neh_k8** | **25.61** | **21.16** |

**③ (T,R)=(0.6,0.2) (빡빡·좁은 납기, n=160/조합)**

| 조합 | mean% | median% |
|------|------:|--------:|
| full_k2 | 33.44 | 33.25 |
| full_k4 | 34.11 | 31.27 |
| full_k8 | 33.88 | 31.05 |
| neh_k2  | 33.97 | 32.62 |
| neh_k4  | 33.58 | 32.86 |
| **neh_k8** | **32.16** | **29.72** |

### 왜 그룹마다 best K가 뒤집히나 — T-레벨 분해 (mean RPDf%)

| T (n=480/조합) | full_k2 | full_k4 | full_k8 | neh_k2 | neh_k4 | neh_k8 |
|---|---:|---:|---:|---:|---:|---:|
| 0.2 (느슨) | **−13.20** | −3.13 | 12.46 | −9.87 | −4.37 | 11.38 |
| 0.4 (중간) | **31.48** | 41.11 | 50.33 | 35.94 | 38.49 | 50.03 |
| 0.6 (빡빡) | 27.28 | 26.95 | 26.44 | 27.23 | 26.02 | **25.61** |

- **저-K 의 overall 우위는 전적으로 T=0.2/0.4 regime 이 만든다.** 느슨한 납기(T=0.2)
  에선 저-K(k2)가 slack 을 살려 **BKS 를 평균 −13%p 초과**(full_k2), 고-K(k8)는 +12%.
  ~25%p 스프레드가 overall 을 지배.
- **빡빡한 납기(T=0.6)에서만 랭킹이 뒤집혀 고-K(neh_k8)가 근소 최저**지만 6조합
  스프레드가 **~1.7%p 로 무의미**(사실상 K 견고 = tie). (T,R)=(0.6,0.2)도 동일 패턴,
  스프레드 ~2%p.
- 음수 RPDf(BKS 초과) 비율: full_k2 14.1% > full_k8 6.3% — 저-K 가 easy 인스턴스에서
  best 를 더 자주 깬다. 저-K 는 easy 에서 큰 이득 + hard 에서 budget starvation
  (양성 warning), 고-K 는 전 regime 에 걸쳐 평탄·robust 하지만 easy 최고점엔 못 미침.

### 결론 — best (scenario, K)

- **1차 지표(overall mean/median RPDf) 기준 승자는 `csr_full_d2wp_k2`** (15.19% /
  23.45%), 2위 `csr_neh_d2wp_k2` (17.77% / 25.00%). **저-K(=2)가 결정적 우위**이며
  mean·median 이 일치. init-flow 는 full(mcf→flip)이 neh-only 를 근소 상회.
- 단 이 우위는 **regime 의존**: 느슨/중간 납기(T≤0.4)에서 저-K 압승, 빡빡(T=0.6)에선
  6조합이 사실상 동률(고-K 가 <2%p 근소 우위). 즉 **"저-K 가 이긴다"가 아니라
  "저-K 가 easy 를 크게 먹고 hard 에선 안 진다"**가 정확한 서술.
- **후속 실험 기준선 = `csr_full_d2wp_k2`.** 더 긴 TL 은 K=2 의 hard-instance
  starvation(위 양성 warning)을 완화할 여지가 크므로 다음 후보 축.

### 남은 분석(옵션, 계획 §분석 산출물)

pairwise win/tie·size-group(job/stage) 승자 분해와 `scripts/build_results_index.py`
→ `analysis/results_index_*.csv` 는 미실행. 위 T-분해로 핵심 신호는 확정됐고, size-group
분해가 필요하면 별도 수행.
