# 실험 3 — round + last-semi CSR의 활용 가능성: 예산 8점 × arm 4단 (사전 작성)

**작성일**: 2026-07-27 · **종류**: 실험 실행 계획(사전 작성)
**실행**: `output/20260727_csr_usability_t06/20260727T224612_096605` (22:46 시작)
**상위**: `vault/20260727_p3_csr.pdf` 슬라이드 10 — "Round coarsening, last-semi
reconstruction 활용 가능성?"
**선행 결론**:
- 실험 1 (PDF p3–6) — `plans/analysis/20260724/csr_reconstruct_mode_lastsemi.md`:
  full 1440, cumulative 고정, τ{1,2,4,8} × f{5,10,15} → **last-semi ≻ semi ≻ active**
  (예외 셀 τ=2, TL 5 %)
- 실험 2 (PDF p7–9) — `plans/analysis/20260724/lastsemi_rounding_robustness.md`:
  full 1440, last-semi 고정, τ{2,4,8} × f{5,10,15} → **round ≻ ceil ≻ cumulative ≻ floor**
  (예외 셀 τ=2, TL 5 %), 그리고 **네 rounding 모두에서 τ=1이 최선**

---

## 1. 질문

> **round coarsening + last-semi reconstruction을 쓸 때, coarsening(τ>1)이
> not-coarsening(τ=1)을 이기는 예산·알고리즘 깊이 조합이 존재하는가?**

실험 1·2는 "어느 reconstruct / 어느 rounding이 나은가"에 답했다. 둘 다
**τ=1이 최선**이라는 결론을 바꾸지 못했으므로, 남은 질문은 **CSR을 쓸 이유가
있는가**이다. 이것이 슬라이드 10의 "활용 가능성"이다.

**조작적 정의** (실행 전 고정):

```txt
dRPDf(arm, f, τ) = mean_i [ RPDf(arm, f, τ, i) − RPDf(arm, f, 1, i) ]
                   같은 arm · 같은 f · 같은 insIndex로 per-instance paired
활용 가능  ⇔  ∃ (arm, f, τ>1) :  dRPDf < 0  AND  win > loss
```

mean과 승패를 **둘 다** 요구한다 — 어느 한쪽만으로는 tail-driven 왜곡을 못 거른다
(`analyze_csr_equal_budget.py`의 T=0.6 사례: 평균은 이기고 카운트는 짐).

### 1.1 이 런이 답해야 할 질문 셋

위 게이트는 "coarsening 채널"만 격리한다. 실제로 문서화해야 할 질문은 셋이고,
**각각 이 런이 답할 수 있는 범위가 다르다.** 답할 수 없는 부분을 답한 것처럼
쓰지 않기 위해 경계를 미리 못박는다.

| | 질문 | 이 런의 답변 범위 | 판정 |
|---|---|---|---|
| **Q1** | round와 ceil 중 무엇이 나은가 | **f=40, τ{2,4,8}, `m1`, lastsemi 뿐** | §4.Q1 |
| **Q2** | round에서 active는 얼마나 나쁜가 · semi와 last-semi 차이는 | **round 한정, 전 8 f, 전 τ** | §4.Q2 |
| **Q3** | 지금까지 돌린 **모든** 세팅 중 동일 예산에서 **uncoarsened `m1`을 이기는 것이 하나라도 있는가** | 139 시나리오 전부 | §4.Q3 |

- **Q1의 경계**: ceil은 프로브 3개뿐이다(§2.3). 다른 f의 round-ceil 비교는 이 런에
  없고, 실험 2(f ≤ 15, full 1440)와 crossover ladder(f ≤ 4, 같은 160 셀)로만
  보완된다. **"round가 전 구간에서 최선"이라고 쓰면 안 된다.**
- **Q2의 경계**: `msemi`/`mactive`는 **round에서만** 돈다. **ceil에서의 reconstruct
  비교는 이 런에 존재하지 않는다** — Q2를 "round/ceil에서"로 서술하지 말 것.
  (필요하면 후속에서 ceil × {semi, active}를 추가해야 하고, f=40 한 점만 해도
  0.7 h다.)
- **Q3가 실질적 결론이다.** §1의 게이트는 arm별로 자기 τ=1과 비교하므로
  "`a` arm 안에서 coarsening이 이겼다" 같은 답이 나올 수 있는데, 그건 실용적으로
  무의미하다 — `a`는 CP를 아예 안 돌린다. **실무 기준선은 언제나 `m1_k1`**(전
  inner flow, coarsening 없음)이고, Q3는 그것을 동일 예산에서 이기는 세팅이
  하나라도 있는지를 묻는다. 두 판정은 별개이며 **둘 다 보고한다.**

---

## 2. 왜 이 설계인가

### 2.1 예산축을 3점 → 8점으로 (핵심)

실험 1·2의 f ∈ {5,10,15}로는 활용 가능성을 판정할 수 없다. 두 방향 모두 미측정:

- **아래쪽 (f ≤ 4 %)** — 기존 crossover ladder
  (`plans/analysis/20260726/coarsening_short_budget_crossover.md`)가 f=1 %까지
  내려가 봤으나 **objective crossover 없음**(200개 조합 전부 dRPDf > 0). 다만
  **feasibility crossover는 있었다**: f=1 %에서 τ=1이 20/160 인스턴스에 incumbent를
  아예 못 만들었다. 그 결함은 `adb8e60`(mcf_lb 라운드 1 원자화)으로 제거됐으므로
  **이 구간은 재측정 가치가 있다.**
- **위쪽 (f = 20, 40 %)** — 완전 미측정. **f=40 %가 이 실험의 핵심 논거**다:
  `plans/experiment/20260726/csr_init_tl_f35_f40.md` §1.1에 따르면 기존 C5
  initializer 예산이 정확히 `0.09nc × 40 %`(flip 10 % + neh 30 %)이므로, f=40은
  **기존 초기해 생성기와의 동일예산 정면 대결점**이다. 그리고
  `plans/analysis/20260719` 계열이 **T=0.6에서 예산이 커질수록 τ=1의 우세가
  잠식된다**고 기록했다(f=30에서 평균은 이기면서 카운트는 233/0/247로 짐).
  crossover가 있다면 여기다.

### 2.2 arm 4단 — "왜"에 답하는 채널

objective 평균은 crossover의 유무만 말하고 원인을 말하지 않는다. arm이 두 채널을
분리한다:

| arm | inner flow | 담는 채널 |
|---|---|---|
| `a` | 없음 (`solve: False`, `seed_dispatch: v4`) | **해상도 손실만** |
| `b` | `mcf_lb` | 해상도 손실만 |
| `c` | `mcf_lb` → `flip_makespan_cp` | + 동일예산 CP 한 단 |
| `m1` | 5단 full solve_flow | 해상도 손실 + **알고리즘 깊이** |

→ crossover가 `m1`에만 나오고 `a`/`b`엔 없으면 원인은 해상도가 아니라 **깊이**다
(짧은 예산에서 coarsening이 더 깊은 단계까지 도달하는 효과 —
`scripts/20260725/analyze_csr_winner_source.py`가 잰 그 채널).

### 2.3 rounding은 round 고정, ceil은 f=40에만

실험 2가 정확히 이 작동점(full 1440 · last-semi · τ{2,4,8} · f{5,10,15})에서
round 우세를 판정했으므로 전면 재측정은 결론난 질문의 재개다. 단 실험 2는
**f ≤ 15만** 쟀으므로 고예산에서의 순위 역전은 미지 → `m1` arm, τ{2,4,8},
**f=40 세 시나리오만** 프로브로 넣는다.

### 2.4 reconstruct 3-mode 확인 — 두 축의 상호작용 가정 검증

이 실험은 `round × last-semi` 칸 위에 서 있는데, **그 칸은 직접 측정된 적이 없다**
(실험 1은 cumulative에서, 실험 2는 last-semi에서 각각 상대 축을 고정). 두 축이
상호작용하지 않는다는 **가정**이다. 무해하지 않을 수 있다 — 실험 1의 예외 셀
(last-semi가 지는 τ=2, TL 5 %)과 실험 2의 예외 셀(round가 지는 τ=2, TL 5 %)이
**같은 좌표**다.

→ `m1` inner flow를 `semi_active`·`active`로도 돌려 **전 f 구간에서** 3-mode 비교를
완성한다(arm 이름 `msemi` / `mactive`; last-semi는 `m1`이 이미 담당). 두 점이 아니라
전 f로 넓히는 이유는 §2.1과 같다 — **reconstruct 순위 역시 f ≤ 15에서만 측정됐고**,
전 f로 가면 순위의 **예산 의존성 곡선**까지 얻는다. 160 슬라이스에서 추가 비용은
1.0 h뿐이다(§3.4).

**두 arm은 각자의 τ=1 baseline을 갖는다.** `coarsen_mode`와 달리
`reconstruct_mode`는 **τ=1에서 항등이 아니므로**(§6.2) `m1`의 τ=1을 공유할 수 없다.
arm을 나눈 덕에 `analyze_crossover_ladder.py`의 arm별 pairing이 이를 자동으로
처리한다.

### 2.5 crossover ladder의 직접 연장선이다 — 설정 대조

이 런은 `output/20260725_crossover_ladder/20260726T173841_347539`
(config `metadata/20260725/coarsening_crossover.yaml`)의 연장이다. 그 런의 결론은
**"coarsening은 언제나 손해"**였다 — 200개 (arm, f, k, mode) 조합 전부에서
dRPDf > 0 이고 win < loss (`plans/analysis/20260726/coarsening_short_budget_crossover.md`).

| 축 | ladder | 이 런 |
|---|---|---|
| 인스턴스 | 160 (T=0.6, R=0.2) | **완전 동일** (`ins_index`를 그대로 읽어 씀) |
| outer `timelimit` | `0.09nc` | 동일 |
| **f** | **1, 2, 3, 4 %** | **1, 2, 3, 4, 5, 10, 20, 40 %** |
| **τ** | 1, 2, 4, 8, **16, 32** | 1, 2, 4, 8 |
| **coarsen_mode** | ceil · floor · round · cumulative | round (+ f=40 ceil 프로브) |
| **reconstruct_mode** | last-semi 하나 | last-semi + **semi_active + active** |
| `c` arm CSR tl | `0.09nc` | **f배율** (§3.2) |
| `a`/`b` arm | 4 mode × 6 τ = 21개씩 | round × 4 τ = 4개씩 |
| `a`/`b`/`m1` inner flow | — | **step 파라미터까지 동일** |
| 코드 | `adb8e60` | + `2c7ef28` (기록 계층) + 리포트 3건 |
| 시나리오 | 210 | 139 |

**핵심**: ladder의 "언제나 손해"는 **f ∈ [1, 4] %라는 극빈 예산 구간의 관측**이다.
실험 1·2가 f ∈ {5,10,15}를 덮었으므로 **f = 20, 40 %는 아직 누구도 보지 않은
구간**이고, 이 런이 새로 덮는 곳이 정확히 거기다(예산축 10배 확장). 두 방향으로
그 결론을 시험한다 — ①예산을 10배 늘려도 그런가 ②reconstruct를 바꿔도 그런가.

**포기한 것**: τ ≥ 16과 floor/cumulative 전면 비교. ladder가 이미 답했고
(τ=32에서 ceil +32.7 / round +35.3 / cumulative +37.5 / floor +40.8 pp, 전부 τ=1
대비 큰 열세) 재확인 가치가 낮다.

**비교 불가**: `c` arm. §3.2의 예산 재정의로 이름만 같고 다른 실험이다.

> **예상되는 결과와 그 가치**: `m1_k1`은 f가 커질수록 강해진다(실험 1에서 τ=1이
> f=15에 이미 RPDf 0.055). 따라서 고-f에서 coarsening이 이길 가능성은 낮고,
> **음의 결과가 유력하다.** 그래도 가치가 있다 — "f=40 %(= C5 initializer 동일예산,
> §2.1)까지 확인했고 그래도 τ=1이 최선"은 ladder의 f ≤ 4 % 관측만으로는 쓸 수 없는
> 문장이다. Q3(§4)가 그 문장을 확정한다.

---

## 3. 실행 설정

**이 런은 스크리닝이다.** 160 슬라이스에서 전 격자를 훑고, **full 1440은 이 결과가
의미 있다고 지목한 셀만 골라** 후속으로 돌린다(§6.1).

**인스턴스**: **160** — PRA2017 large의 (T, R) = (0.6, 0.2) 셀
(`insIndex` 60–69, 150–159, …, 1410–1419; n∈{50,100,150,200}, c∈{5,10}, 5 rep).
생성기가 `metadata/20260725/coarsening_crossover.yaml`의 `ins_index`를 **그대로
읽어** 쓰므로 crossover ladder와 인스턴스 집합이 정확히 같고, 그 결과와 직접
비교된다(§4 재현 게이트).

> **이 선택은 두 얼굴을 가진다.** ①비용이 full 1440의 1/9다. ②**이 슬라이스는
> τ=1의 우세가 가장 약한 곳**이다 — §2.1의 잠식 관측이 바로 T=0.6에서 나왔다.
> 따라서 **여기서도 crossover가 없으면 "τ=1이 최선"은 크게 강화**되고, 있으면
> 슬라이스 특이 현상일 수 있어 full 1440 확인이 필요하다. 어느 쪽이든
> **실험 1·2(full 1440)와 같은 장표에 나란히 놓을 수는 없다** — 모집단이 다르다.

**τ**: {1, 2, 4, 8}. **coarsen_mode**: `round` (τ=1은 rounding 항등이므로 mode 없음).
**reconstruct_mode**: `active_but_last_semi`.
**f**: {1, 2, 3, 4, 5, 10, 20, 40} %.

### 시나리오 139개

| 그룹 | 이름 규칙 | reconstruct | 개수 |
|---|---|---|---|
| `a` | `a_k{K}[_round]` | lastsemi | 4 (**f 없음**, §3.1) |
| `b` | `b_k{K}[_round]` | lastsemi | 4 (**f 없음**, §3.1) |
| `c` | `c_k{K}[_round]_f{FF}` | lastsemi | 4 × 8 = 32 |
| `m1` | `m1_k{K}[_round]_f{FF}` | lastsemi | 4 × 8 = 32 |
| ceil 프로브 | `m1_k{2,4,8}_ceil_f40` | lastsemi | 3 (§2.3) |
| `msemi` | `msemi_k{K}[_round]_f{FF}` | **semi_active** | 4 × 8 = 32 (§2.4) |
| `mactive` | `mactive_k{K}[_round]_f{FF}` | **active** | 4 × 8 = 32 (§2.4) |

`(arm, f)`당 4개 = τ=1 (rounding 항등이라 mode 접미사 없음) + τ{2,4,8} × round.
이름 규칙은 crossover ladder(`{arm}_k{K}[_{mode}][_f{NN}]`)를 그대로 따른다.

- **ceil 프로브는 별도 baseline이 필요 없다** — τ=1은 rounding 항등이므로 이미 있는
  `m1_k1_f40`이 baseline이 된다.
- **`msemi`/`mactive`는 별도 τ=1이 필요하다** — reconstruct_mode는 τ=1에서 항등이
  아니다(§6.2). arm으로 분리했으므로 arm별 pairing이 자동 처리한다.
- `analyze_crossover_ladder.py`의 `SCENARIO_RE`에 두 arm 이름을 추가했다(한 줄,
  하위 호환 — 기존 ladder 210개가 동일하게 파싱됨을 확인).

### 3.1 `a`, `b`는 f에 무관하다 — 스윕하지 않는다

셋 다 CSR `timelimit`이 `0.09nc`(외곽 예산 전액)이고 예산이 절대 binding되지
않는다. `a`는 CP를 아예 호출하지 않고(`solve: False`), `b`는 비중단 `mcf_lb`
하나뿐이다(`adb8e60` 이후 시작된 mcf_lb는 항상 완주). f를 8개로 돌리면 **동일한
열 8개**가 나온다. crossover ladder가 이 둘만 f 접미사 없이 돌린 이유가 이것이다.

### 3.2 `c`의 CSR timelimit도 f배율로 맞춘다 (ladder와 다른 점)

crossover ladder는 `c`의 CSR `timelimit`을 `0.09nc`로 두고 flip의 `cp_tl`만
f배율로 걸었다. 그러면 `mcf_lb`가 **예산 밖**에 있어 같은 f에서 `c`는
"mcf_lb 전액 + f×flip", `m1`은 "총 f"를 쓰게 되고 arm 간 가로 비교가 깨진다.

**이 실험은 `c`의 CSR `timelimit`도 `0.0009·f·nc`로 통일한다** — `a`/`b`를 제외한
모든 arm이 같은 총예산을 쓰므로 `"c의 f=10 vs m1의 f=10"` 같은 가로 비교가
성립하고, "예산을 어디에 쓰는 게 이득인가(얕고 넓게 vs 깊게)"를 직접 읽을 수 있다.

대가와 주의:

- **ladder의 `c` 결과와는 직접 비교되지 않는다** (예산 정의가 달라졌다). 재측정
  구간(f=1~4)이 겹치지만 겹쳐 그리지 말 것.
- **f ≤ 2에서는 명목 예산이 실현되지 않는다.** `mcf_lb`가 비중단이고
  `(n=200,c=10)`에서 ≈2.6 s인데 f=1의 예산은 `0.0009nc` ≈ 1.9 s다. `m1`도 이미
  같은 상황이므로 양쪽에 동일하게 작용하나, §4 2급 2에서 `elapsedTime`으로 실제
  초과폭을 반드시 기록한다.
- **생성기 확인 사항**: `c`의 flip `cp_tl`은 ladder와 같은 `0.0009·f·nc`로 두되,
  CSR `timelimit`이 먼저 binding하도록 한다. 컨트롤러가 inner step에 남은 예산을
  차감해 넘기는지 스모크 1건으로 확인하고, 아니면 `cp_tl`을 직접 줄인다.

### 3.3 inner TL 비례식

| 항목 | 식 | f=1 % | f=5 % | f=40 % |
|---|---|---|---|---|
| CSR `timelimit` (`c`, `m1` 공통) | `0.0009·f·nc` | `0.0009nc` | `0.0045nc` | `0.036nc` |
| `m1` flip `cp_tl` | `0.00009·f·nc` | `0.00009nc` | `0.00045nc` | `0.0036nc` |
| `m1` neh `total_timelimit` | `0.00027·f·nc` | `0.00027nc` | `0.00135nc` | `0.0108nc` |
| `m1` isw `..._multiplier` | `0.00005·f` | `0.00005` | `0.00025` | `0.002` |
| `c` flip `cp_tl` | `0.0009·f·nc` | `0.0009nc` | `0.0045nc` | `0.036nc` |

`c`의 flip은 flow에 단 하나뿐이므로 CSR 총예산과 같은 `cp_tl`을 받고, CSR
`timelimit`이 먼저 binding한다 (§3.2).
`a`/`b`의 CSR `timelimit`은 `0.09nc` — 예산이 binding되지 않는 arm이다 (§3.1).
**outer `timelimit`은 전 시나리오 `0.09nc`.**

### 3.4 비용

per-instance elapsed 모델 (`(n=200,c=10)` 셀 실측 회귀):
`a` 2.63 s · `b` 2.58 s · `m1` ≈ `0.51 + 1.73f` · `c` ≈ `max(2.58, 1.40f)`
(§3.2의 f배율 전환 반영 — mcf_lb가 예산 안으로 들어와 ladder 실측보다 낮아진다).
인스턴스 전체 평균은 그 **0.465배**(mean `n·c` 비율).

**모델 검증 2건**: ① crossover ladder(210 scn × 160, f 1–4) 예측 1.81 h vs 실측
**1:46:39**, 오차 2 %. ② 20260724 recon AB(24 scn × 1440, 평균 f=10) 실측
**6:35:42**에서 역산한 배율 0.463 — 160 슬라이스 배율 0.47과 일치.

| 그룹 | 시나리오 | wall (160) |
|---|---|---|
| `a` + `b` | 8 | 0.04 h |
| `c` | 32 | 0.83 h |
| `m1` | 32 | 1.04 h |
| ceil 프로브 | 3 | 0.36 h |
| `msemi` + `mactive` | 64 | 2.08 h |
| **합** | **139** | **≈ 4.35 h** |

`instance_worker_cnt=12 × solver_thread_cnt=8 = 96` (물리 코어 수).
비용의 대부분이 f=40에 몰려 있지만 그것이 §2.1의 핵심 논거다.

**규모 참고** — (T,R) 셀은 (n,c)와 직교해 크기 분포가 같으므로 full 1440은 정확히
**×9**다. 즉 이 격자를 그대로 1440에서 돌리면 **39.1 h**다. 그래서 이 런은
스크리닝이고, 후속 full 런은 §6.1에 따라 **선별된 셀만** 돌린다.

```bash
uv run python scripts/20260727/build_exp3_config.py
    # -> metadata/20260727/csr_usability_sweep.yaml (139 scn, ins_index 160)
uv run python main.py --config metadata/20260727/csr_usability_sweep.yaml
    # -> output/20260727_csr_usability_t06/<timestamp>/
```

---

## 4. 판정 방법

**주 산출물**: `<run>_rpdf_comparison.csv` (`post_run_pivot.py`가 `insIndex → n, c,
T, R, W, BKS_data, bestObj, RPDf_BKS_data, elapsedTime`을 자동 생성).

### 1급 — 활용 가능성 게이트 (§1의 조작적 정의)

`scripts/20260726/analyze_crossover_ladder.py`를 **그대로 재사용**한다 — 이름 규칙이
같고, arm별 자기 τ=1 대비 paired가 정확히 §1의 정의다. 산출:
`drpdf_by_mode_k.csv` (arm, f, k, mode별 dRPDf + win/tie/loss + feasibility 카운트),
`arm_summary.csv`, `m1_ladder.csv`.

- **crossover 있음**: 해당 (arm, f, τ)를 명시하고, arm 축으로 원인을 귀속한다
  (§2.2). full 1440 확인 실험이 후속으로 필요하다(§3의 슬라이스 caveat).
- **crossover 없음**: f를 40 %까지, arm을 4단으로 넓히고도 τ=1이 최선 → 슬라이드
  10의 답은 "활용 불가"이고, τ=1 CSR을 **initializer로** 쓰는 방향
  (`csr_init_roadmap.md`의 W2/W3)만 남는다.

### 2급 — 병기 필수

1. **feasibility 카운트**: `coarse_only_feasible` / `k1_only_feasible`.
   `adb8e60` 이후이므로 **f=1 %에서도 0이어야 정상**이다. 0이 아니면 paired 표가
   그 인스턴스들에서 편향된다 — 20260726 분석이 f=1 %에서 20/160의 비대칭을
   기록했고, 이 런은 그 결함이 제거됐는지도 함께 확인한다.
2. **budget parity 실측**: `elapsedTime` mean이 arm·f 안에서 τ에 무관해야 한다.
   `elapsed_by_scenario.csv`로 확인. 어긋나면 equal-budget 비교가 아니다.
3. **f=40 동일예산 대조**: `m1`의 f=40 arm은 C5 initializer와 같은 예산을 쓴다
   (§2.1). 실제 `elapsedTime` / `0.09nc` 비율을 기록해 "40 %"가 명목이 아니라
   실측임을 보인다 — mcf_lb가 중단 불가라 초과가 난다.
### Q1 — round vs ceil (§1.1)

f=40, τ{2,4,8}에서 `m1_k{τ}_ceil_f40 − m1_k{τ}_round_f40` per-instance paired
(mean dRPDf + win/tie/loss). `analyze_crossover_ladder.py`가 mode를 이미 분리해
내므로 `drpdf_by_mode_k.csv`의 두 행 차이로 읽힌다 — 둘 다 같은 `m1_k1_f40`
baseline에 대한 dRPDf이므로 그 차이가 곧 `ceil − round`다.

**보고 형식**: 이 런의 f=40 결과를 실험 2(f ∈ {5,10,15}, full 1440)와 crossover
ladder(f ∈ {1..4}, 같은 160 셀)의 기존 수치와 **한 표에 나란히** 놓고, 각 행에
모집단(160 / 1440)과 τ 범위를 명시한다. 세 데이터가 f 축에서 겹치지 않으므로
**곡선으로 잇지 말고 점으로 찍는다.**

> §6.3의 충돌을 유념할 것 — 같은 160 T06 셀에서는 ladder가 ceil 우세를,
> full 1440에서는 실험 2가 round 우세를 냈다. 이 런은 **160 T06**이므로 ceil이
> 이겨도 그것이 실험 2를 뒤집는 것은 아니다. 슬라이스 축을 반드시 병기한다.

### Q2 — reconstruct 3-mode 격차와 그 예산 의존성 (§1.1, §2.4)

각 f · 각 τ에서 `msemi − m1`(= semi − last-semi)과 `mactive − m1`(= active −
last-semi) per-instance paired. 양수 = last-semi가 우수.

- **주 산출물은 f 곡선**이다 — 실험 1은 f ∈ {5,10,15} 세 점뿐이었고, 여기서는
  8점이라 **격차가 예산에 따라 어떻게 변하는지**가 처음 보인다. 실험 1이 관측한
  "coarsening이 커질수록 semi-lastsemi 격차 증가"가 f 축에서도 단조인지 확인한다.
- τ=1 행을 반드시 포함한다 — reconstruct_mode는 τ=1에서 항등이 아니므로(§6.2)
  거기서도 격차가 있어야 정상이고, 없으면 설정 오류를 의심한다.
- 실험 1의 full-1440 값(−3.17 / −32.36 pp)과는 **부호·대소만** 비교한다. 모집단과
  rounding이 다르므로 크기 일치는 요구하지 않는다.
- **`ceil`에서의 3-mode 비교는 이 런에 없다** (§1.1). 결과 서술에서 "round/ceil
  에서"라고 쓰지 말 것.

### Q3 — uncoarsened `m1`을 이기는 세팅이 하나라도 있는가 (§1.1)

**이것이 실용적 결론이다.** §1의 게이트가 arm 안에서 비교하는 것과 달리, Q3는
139개 시나리오 **전부**를 단일 기준선 `m1_k1_f{FF}`(전 inner flow · coarsening
없음)에 대해 잰다.

**동일 예산의 정의가 arm마다 다르므로 둘로 나눠 판정한다.**

1. **명목 예산이 같은 arm** — `c` / `m1` / `msemi` / `mactive`는 §3.2에 따라
   CSR `timelimit`이 모두 `0.0009·f·nc`다. 따라서 같은 f의 `m1_k1_f{FF}`와
   **per-instance paired**로 직접 비교한다:

   ```txt
   dRPDf*(scn) = mean_i [ RPDf(scn, i) − RPDf(m1_k1_f{FF(scn)}, i) ]
   Q3 통과  ⇔  ∃ scn :  dRPDf* < 0  AND  win > loss
   ```

   τ=1인 `msemi_k1_f{FF}` / `mactive_k1_f{FF}` / `c_k1_f{FF}`도 후보에 포함된다 —
   coarsening 없이 reconstruct나 flow만 바꿔 이기는 경우가 여기서 잡힌다.

2. **`a` / `b`** — f 축이 없고 `timelimit`이 `0.09nc`라 명목 예산이 대응되지 않는다
   (§3.1). 이들은 **실측 `elapsedTime` 기준**으로만 비교한다: 각 arm의 mean elapsed
   와 가장 가까운 `m1_k1_f{FF}`를 짝짓고, 그 f를 명시한다. (`a` ≈ 2.6 s, `b` ≈ 2.6 s
   이므로 대략 f=1~2 근방이 될 것이다.)

**주 그림 — (mean elapsedTime, mean RPDf) 산점도.** PDF의 `Avg. (Time%, RPDf)`
슬라이드와 같은 형식으로, `m1_k1`의 8점을 잇는 **기준 곡선**을 그리고 나머지
131개를 점으로 얹는다. Q3의 답은 시각적으로 **"기준 곡선의 좌하단에 놓인 점이
있는가"**이고, 있으면 그 점만 위 paired 검정으로 확정한다. 이 그림 하나가
슬라이드 10의 답이 된다.

#### Q3 판정 시 주의 — 세 가지

**(i) 어느 f에서 통과했는지가 결론의 실질이다.** `m1_k1`은 f가 커질수록 강해지므로
(실험 1의 τ=1 f15 RPDf 0.055) 고-f에서 이기기가 훨씬 어렵다. 저-f에서만 통과했다면
"예산이 극히 부족할 때만 쓸모"라는 좁은 결론이고, f=20/40에서 통과했다면 실제 채택
후보다.

**(ii) `a`/`b`의 저-f 승리는 "CSR이 쓸모 있다"가 아니다.** `a`/`b`는 elapsed로
baseline을 고르는데(위 2번), 그 최근접이 대개 `m1_k1_f01`(≈2.3 s)이다. 그 지점의
m1 flow는 **예산에 굶어 죽은 상태**라 dispatch-only가 이긴다 — ladder 런 실측에서
`a_k1`이 `m1_k1_f01`을 **−20.72 pp, 158/0/2**로 이겼고 elapsed는 0.91배였다.
정직한 동일시간 비교이지만, 뜻하는 바는 **"예산이 그 정도로 없으면 CP를 돌리지
않는 편이 낫다"**이지 coarsening이나 CSR의 가치가 아니다. 이 승리는

- **Q3의 답으로 카운트하지 않는다.** Q3는 coarsening/재구성 세팅이 기준선을
  이기는지를 묻는다. `a`/`b`는 inner flow 자체가 다른 **하한 대조군**이다.
- 대신 **"m1 flow가 언제부터 dispatch-only를 이기는가"**라는 별도 관측으로 보고한다.
  그 f가 곧 **inner flow의 최소 동작 예산**이고, 그 아래에서는 어떤 CSR 설정도
  논할 의미가 없다. `a`/`b` 행은 산점도에서도 별도 표기한다.

**(iii) 동일예산은 명목이 아니라 실측으로 확인한다.** "같은 f = 같은 예산"은 §3.2로
`c`의 CSR timelimit을 f배율로 바꾼 **이 config에서만** 성립한다. ladder는 `c`가
`0.09nc`라 성립하지 않았고, 그대로 비교하면 `c_k1_f01`이 −16.43 pp로 이기는 것처럼
보이지만 실제로는 **2.29배**의 시간을 썼다. 분석 스크립트가 `elapsed_ratio`를
산출하고 1.10배 초과 승리에 `OVER BUDGET` 경고를 붙이므로, **경고가 붙은 행은 Q3
crossover로 보고하지 않는다.**

### 재현 게이트 (내장 sanity check)

`ins_index`가 crossover ladder(`20260726T173841_347539`)와 **같은 160개**이므로
(§2.5), 대조 가능한 **24개 시나리오**가 자동으로 재현 대조군이 된다:

- **바이트 단위 동일 18개** — `a_k{2,4,8}_round`, `b_k{2,4,8}_round`,
  `m1_k{2,4,8}_round_f{01..04}`.
- **동작만 동일 6개** — `a_k1`, `b_k1`, `m1_k1_f{01..04}`. ladder는 이들에
  `coarsen_mode: cumulative`, 이 런은 `round`로 적혀 있으나 **factor=1에서 네
  rounding은 항등**이므로 동작이 같다. 텍스트 차이를 보고 "설정이 다르다"고
  결론내지 말 것.

- 이들의 mean RPDf / mean obj가 ladder 값과 **CP 노이즈 안에서 일치해야 한다.**
  실험 2의 merged analysis가 cumulative 열로 배관을 검증한 것과 같은 장치다.
- 어긋나면 첫 용의자는 **`2c7ef28`**(07-26 22:51, ladder 런보다 뒤)이다. CSR
  progress_log note 부착과 `factor==1`에서의 `obj_bound` 방출을 추가했고, 기록
  계층 변경이라 탐색 궤적은 안 바뀌어야 하지만 검증된 적이 없다. 이 게이트가 그것을
  공짜로 확인한다.
- **`c` arm은 대조 대상이 아니다** — §3.2의 예산 정의 변경으로 이름만 같고 다른
  실험이다.

### 노이즈 게이트

기준 **±350** (CLAUDE.md, CSR batch CP noise floor — 1440 격자 mean obj 델타).
**160 인스턴스에서는 이 바닥이 더 크므로**, 근소한 승패는 "구분되지 않음"으로
처리하고 1급 판정에는 부호와 승패 카운트를 함께 쓴다(§1이 둘 다 요구하는 이유).

---

## 5. 산출물

- 분석 문서: `plans/analysis/20260727/csr_usability_sweep.md` (tracked SSOT)
- config 생성기: `scripts/20260727/build_exp3_config.py`
- **분석 스크립트**
  - **1급 게이트와 Q1**: `scripts/20260726/analyze_crossover_ladder.py` 재사용.
    여섯 arm 전부의 τ-사다리와 mode 분리를 그대로 산출한다. 유일한 변경은
    `SCENARIO_RE`에 `msemi|mactive`를 더한 한 줄이며 하위 호환이다
    (ladder 210개 파싱 불변 확인).
  - **Q2·Q3**: 신규 `scripts/20260727/analyze_usability.py`. 둘 다 **arm 간 조인**이
    필요해 위 스크립트로는 안 된다 — Q2는 같은 (τ, f)에서 `m1`/`msemi`/`mactive`를
    묶고, Q3는 139개 전부를 `m1_k1_f{FF}` 하나에 묶는다. 산출: Q2 f-곡선 CSV,
    Q3 paired 표(dRPDf* + win/tie/loss), 그리고 **(elapsed, RPDf) 산점도 PNG**
    (`m1_k1` 기준 곡선 + 나머지 점).
    `load_run` / `paired_drpdf`는 `analyze_crossover_ladder.py`에서 import해
    RPDf·pairing 정의가 드리프트하지 않게 한다.
- 커밋: run setting (`20260727_csr_usability_t06/<timestamp> run setting`) +
  merged analysis — CLAUDE.md provenance 규약.

---

## 6. 미결 / 판독 시 주의

### 6.1 이 런은 스크리닝이다 — 결과를 그대로 슬라이드에 올리지 않는다

PDF의 실험 1·2는 full 1440이다. 이 런은 (T,R)=(0.6,0.2) 160개이므로 **모집단이
다르고**, "실험 1·2와 이어지는 곡선"처럼 그리면 안 된다.

**후속 full 1440은 이 격자 전체가 아니라 여기서 의미 있다고 지목된 셀만 돌린다.**
139 시나리오를 그대로 1440에서 돌리면 39.1 h이고, 그럴 이유가 없다. 선별 기준:

- **crossover 징후가 없으면** — 전 (arm, f, τ)에서 dRPDf > 0 이고 win < loss —
  full 런은 결론을 못 바꾸므로 확인용 최소 셀(예: `m1`의 f=40, τ{1,2})만 돌린다.
- **징후가 있으면** 그 (arm, f, τ) 근방과 자기 τ=1 baseline만 1440으로 승격한다.
- recon·ceil 축(§2.3–2.4)에서 순위 역전이 나오면 그 f 근방도 후보에 넣는다.

§3의 두 얼굴을 같이 적는다: 비용이 1/9인 대신 **τ=1의 우세가 가장 약한 슬라이스**
이므로, 여기서도 crossover가 없으면 결론이 오히려 강화된다.

### 6.2 τ=1에서 `reconstruct_mode`는 항등이 아니다 — 기존 노트 정정

`coarsen_solve_reconstruct.py:585-601`에서 세 mode는 factor 값과 무관하게 서로
다른 경로를 탄다. `semi_active`만 `factor`를 인자로 받고, `active`는 coarse machine
assignment를 버려 start-order만 남기고 재배정하며, `active_but_last_semi`는 마지막
stage만 보존한다. 실험 1의 실측이 이를 확증한다 (k=1, f=5의 mean RPDf:
semi 27.863 / active 61.124 / **lastsemi 26.641**).

→ **`plans/experiment/20260726/csr_init_tl_f35_f40.md` §5의 "τ=1에서
`reconstruct_mode`는 무의미"는 사실과 다르다.** 그 런이 `active_but_last_semi`로
고정한 것은 중립적 선택이 아니라 (실험 1 기준) semi보다 유리한 선택이었다. W2 P1
게이트 판정(`plans/analysis/20260726/csr_init_tl_curve.md`)이 그 mode 고정 위에
서 있으므로, 이 실험의 τ=1 recon 확인(§2.4)에서 격차가 재확인되면 해당 노트를
정정해야 한다.

### 6.3 rounding 순위는 문서 간에 충돌한다

저장소에 rounding 비교가 넷 있고 **ceil vs round에서 갈린다**:

| 데이터 | 슬라이스 | f | τ | recon | 순위 |
|---|---|---|---|---|---|
| 실험 2 (`lastsemi_rounding_robustness`) | **1440** | 5,10,15 | 2,4,8 | lastsemi | **round** < cumulative < ceil < floor |
| crossover ladder (`20260726T173841_347539`) | 160 T06 | 1–4 | 2–32 | lastsemi | **ceil** < round < cumulative < floor |
| `csr_coarsen_mode_result` | 160 T06 | 25 | 2–16 | semi | **ceil** > round ≈ cumulative > floor |
| `csr_cumulative_vs_ceil` | 1440 | 5,10,15 | 1–8 | semi | cumulative가 ceil을 못 이김 |

**슬라이스가 갈림의 축으로 보이고 reconstruct_mode는 아니다** — 160 T06에서는 두
번 모두 ceil이, full 1440에서는 round가 이겼다. 다만 crossover ladder를 **τ ≤ 8로
자르면 ceil − round = −0.174 pp**로 사실상 무차별이고, ceil의 우세는 τ ≥ 16에서만
나온다(−1.73 pp). 이 실험의 τ ≤ 8 범위에서는 두 mode가 거의 같으므로 round 고정이
판정을 좌우하지 않으며, §2.3의 f=40 프로브가 고예산 쪽 미지를 덮는다.

**이 실험은 160 T06에서 돌므로 위 표의 2·3행과 같은 슬라이스**다. round를 쓰면서
그 슬라이스에서 ceil이 유리했다는 사실을 알고 있는 상태이니, ceil 프로브 결과는
반드시 병기한다.

### 6.4 폐기된 선행 작업

`metadata/20260727/csr_3way_recon_ab.yaml`(36 scn, 3-mode × round)과
`output/20260727_csr_3way_recon_round/20260727T202215_194015`은 이 계획으로
대체되어 중단됐다. 그 실험의 고유 정보(round에서의 reconstruct 순위)는 §2.4의
`msemi`/`mactive` arm(§2.4)이 **훨씬 싸게**(2.1 h vs 9:53) 대신하며, 그것도 f를
3점이 아니라 8점으로 넓혀서 대신한다. 커밋되지 않았으므로
run setting 커밋도 남기지 않는다. 그 계획 문서
(`plans/experiment/20260727/csr_3way_recon_single_run.md`)의 §6.2·§6.3 내용은
위 §6.2·§6.3으로 옮겼다.
