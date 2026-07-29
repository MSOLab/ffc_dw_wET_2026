# W4 — coarsening이 이기는 소수 인스턴스의 정체 (사전 작성, 재분석 계획)

**작성일**: 2026-07-26 · **종류**: 재분석 계획(사전 작성, 미실행). **새 런 없음.**
**상위**: `plans/experiment/20260726/csr_init_roadmap.md` (W4)
**선행**: 없음 — 언제 착수해도 무방
**대상 데이터**: `output/20260725_crossover_ladder/20260726T173841_347539` (원자화 후 런)

---

## 1. 질문

전체 판정은 "coarsening은 진다"이지만, **평균이 지는 것이지 전부가 지는 것은 아니다.**
arm `a`(dispatch-only, CP 없음, **완전 결정론**)에서 coarsening이 이기는 인스턴스가
160개 중 **41~59개** 존재한다 (최대: k=2, ceil에서 **59/160**).

> **Q. 이 인스턴스들에 구조가 있는가?**
> - (Q1) 같은 인스턴스가 여러 (τ, mode)에서 **반복해서** 이기는가, 아니면 매번 다른
>   인스턴스가 우연히 이기는가?
> - (Q2) arm `a`의 승자가 다른 arm(`b`, `c`, `m1`)에서도 승자인가?
> - (Q3) 승자를 (n, c, mps, W)로 설명할 수 있는가?
> - (Q4) 승자는 "쉬운 인스턴스"인가 "어려운 인스턴스"인가 (τ=1 RPDf 수준으로 대리)?

**이 질문이 갖는 유일무이한 가치**: 부정 결론(§로드맵 §1)에 **처방(prescription)** 을
붙일 수 있는 마지막 항목이다. 구조가 있으면 "coarsening은 이런 인스턴스에 쓴다"가
되고, 없으면 "coarsening 승리는 인스턴스 특성이 아니다"로 결론이 **더 강해진다**.
**어느 쪽이 나와도 쓸모가 있다** — 실패할 수 없는 분석이다.

### 1.1 왜 arm `a`가 1급 대상인가

`a`는 `solve=False`(`SEED_ONLY`)라 **CP를 전혀 돌리지 않는다** → 재실행 분산 0.
따라서 per-instance dRPDf의 부호는 **잡음이 아니라 인스턴스의 실제 성질**이다.
`b`도 CP가 없어 결정론이지만 승자가 0/160이라 표본이 없다. `c`·`m1`은 CP 노이즈가
섞이므로 **확증용 2급**으로만 쓴다.

### 1.2 사전에 있는 모순 (직접 확인할 것)

- 20260721 분석: **penalty는 n이 커질수록 커진다** (n=50 +1.2 pp → n=200 +24.7 pp)
  → 승자는 작은 n에 몰려야 한다.
- 20260725 스모크: `insIndex 1419` (**n=200, c=10**, 가장 큰 인스턴스)가 arm `a`에서
  dRPDf **−0.57**로 음수였고, 본 런에서도 그대로 남았다.

두 관측이 정면으로 어긋난다. **이 모순의 해소가 분석의 첫 체크포인트다.**
(가설: arm `a`에는 CP가 없어 20260721의 size 효과(= CP가 큰 인스턴스에서 해상도
손실을 못 메움)가 작동하지 않는다. 그렇다면 size 효과는 **CP를 포함한 arm에서만**
나타나야 하고, 이는 `a` vs `c`/`m1`의 size 기울기 비교로 검정 가능하다.)

---

## 2. 데이터 소스

| 항목 | 경로 |
|---|---|
| 런 | `output/20260725_crossover_ladder/20260726T173841_347539` (210 scn × 160 ins) |
| per-instance RPDf | `<run>/<run_id>_rpdf_comparison.csv` (`insIndex, scenarioName, n, c, totalMcCount, T, R, W, BKS_data, bestObj, RPDf_BKS_data, elapsedTime`) |
| 기존 집계 (200행) | `analysis/20260726T173841_347539_crossover_ladder/drpdf_by_mode_k.csv` — **집계본이라 per-instance가 없다. 새로 만들어야 한다.** |
| 재사용 함수 | `scripts/20260726/analyze_crossover_ladder.py`의 `load_run`, `paired_drpdf` |
| 내부 단계 정보 | `<run>/<scenario>/<instance>/..._csr_analysis.csv` (winner_source 등, `9370a36`) |

**instance slice**: (T,R) = (0.6, 0.2) 고정 160개. → **T와 R은 설명변수가 될 수 없다.**
사용 가능한 변수는 `n ∈ {50,100,150,200}`, `c ∈ {5,10}`,
`mps = totalMcCount/c ∈ {3,5}`, `W ∈ {10,20}`, replicate(파일명 `Rep{k}`) 뿐이다
(4×2×2×2×5 = 160 ✓ — **정확히 완전 격자**이므로 셀당 정확히 1개, 교락 없이 주효과를
읽을 수 있다).

> ⚠ 셀당 표본이 1개다. 주효과(예: n=50 vs n=200, 각 40개)는 읽을 수 있으나
> **4-way 상호작용은 표본 부족**이다. 3-way 이상은 보고하지 않는다.

---

## 3. 분석 절차

### 단계 1 — per-instance long 테이블 생성

`paired_drpdf`를 (arm, f, τ, mode)마다 돌려 **집계 전** 결과를 남긴다:

```
coarse_winners_long.csv
  insIndex, arm, f, tau, mode, rpdf_k1, rpdf_coarse, dRPDf, is_win, n, c, mps, W, rep
```

행 수 = 200 조합 × 160 = 32,000. arm `a`만 보면 20 조합 × 160 = 3,200.

**건전성 게이트**: 이 테이블을 (arm,f,τ,mode)로 재집계한 mean dRPDf가
`drpdf_by_mode_k.csv`의 200행과 **소수점까지 일치**해야 한다. 불일치 시 진행 금지.

### 단계 2 — Q1 지속성 (persistence)

arm `a`의 20개 (τ, mode) 조합에 대해 인스턴스별 **승리 횟수** `w_i ∈ [0, 20]` 집계.

- **구조 있음**: `w_i` 분포가 0과 20 근처에 쌍봉으로 몰린다 (같은 인스턴스가 늘 이김).
- **구조 없음**: `w_i`가 이항분포 `B(20, p̄)`(p̄ ≈ 승률 평균 ≈ 0.3)에 가깝다.

**판정**: 관측 분산 vs 이항 분산의 비(분산 팽창 계수). 1.5배 이상이면 지속성 인정.
(정식 검정이 필요하면 카이제곱 적합도 — 단 셀 기대도수 5 미만 구간은 병합.)

### 단계 3 — Q2 arm 간 이전성 (transferability)

arm `a`의 승자 집합 `S_a`(예: τ=2, ceil 기준 59개)와 `m1`·`c`의 승자 집합의 **교집합**.
`b`는 0/160이라 자동으로 공집합 — 그 자체가 답이다.

- 겹침이 독립 기대치(|S_a|·|S_m1|/160)보다 유의하게 크면 **인스턴스 고유 성질**.
- 기대치 수준이면 **arm 특유 현상** → 처방 불가, 결론은 "구조 없음".

### 단계 4 — Q3 인스턴스 파라미터 설명

승률을 (n, c, mps, W) 주효과로 분해. 표: 각 수준별 `mean dRPDf`, `win/160`.

**특히 §1.2의 모순 해소**: `n`별 mean dRPDf 기울기를 arm `a` / `c` / `m1`에서 각각
계산해 나란히 둔다. `a`의 기울기가 평평하고 `c`/`m1`만 가파르면 가설(size 효과는
CP 경로에서 온다)이 확인된다.

### 단계 5 — Q4 난이도 대리변수

인스턴스를 `rpdf_k1`(τ=1 RPDf, 즉 baseline이 얼마나 못 푸는가) 4분위로 나눠 승률 표.
"coarsening은 baseline이 무너지는 인스턴스에서 이긴다"는 직관의 직접 검정.
보조로 `BKS_data`, `elapsedTime`도 같은 방식으로 본다.

### 단계 6 — 결론 분기

| 관측 | 결론 | 후속 |
|---|---|---|
| 지속성 O + 이전성 O + 파라미터 설명 O | **처방 가능** — "이런 인스턴스에는 coarsening" | 그 부분집합에서 τ 스윕 재측정 검토 |
| 지속성 O + 이전성 X | arm 특유 (dispatch-only 한정 현상) | dispatch-only 자체의 실용성 질문으로 이동 |
| 지속성 X | **구조 없음** — coarsening 승리는 인스턴스 성질이 아니다 | 부정 결론을 그대로 강화하고 종결 |

---

## 4. 산출물

- 사후 분석 문서: `plans/analysis/20260726/coarse_winner_profile.md` (tracked SSOT)
- 스크립트: `scripts/20260726/profile_coarse_winners.py`
  (`analyze_crossover_ladder.py`에서 `load_run`/`paired_drpdf`를 **import**해서 쓸 것 —
  `verdict_mcf_lb_atomic.py`와 같은 패턴. 수치가 선행 문서와 드리프트하지 않게 한다)
- CSV: `analysis/20260726T173841_347539_coarse_winners/`
  (`coarse_winners_long.csv`, `persistence.csv`, `param_breakdown.csv`,
  `difficulty_quartiles.csv`) — gitignored, 문서가 self-contained해야 함
- 커밋: merged analysis (`analysis/<id> merged analysis`)

## 5. 주의

- **새 런을 돌리지 않는다.** 이 계획의 전제는 대상 런 디렉터리가 그대로 남아 있는
  것이다. 착수 전 `output/20260725_crossover_ladder/20260726T173841_347539` 존재를
  먼저 확인할 것.
- **(T,R) 고정 슬라이스**이므로 어떤 결론도 "(0.6,0.2)에서"라는 단서를 달아야 한다.
  로드맵 §4에 따라 다른 셀로 확장할 계획은 없다.
- arm `a`는 `solve=False`의 **legacy non-`solve_flow` 경로**를 탄다
  (`coarsening_short_budget_crossover.md` §4 caveat 4). 코드 경로 차이가 결과 차이로
  오독되지 않도록 결론에 병기한다.
