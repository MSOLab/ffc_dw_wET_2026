# sub-5% budget crossover: 목적함수 crossover는 없다 (사후 분석)

**작성일**: 2026-07-26 · **종류**: 사후 분석
**판정: CROSSOVER 없음 — f ∈ [1,15]% 전 구간에서 K=1 최선 (H0 기각).**
**⚠ 단서**: f=1%에서 K=1이 20/160에 해를 못 내는 현상은 알고리즘 성질이 아니라
**MCF-LB 스텝의 의도치 않은 stop gate로 인한 구현 결함**이다(§4.4). 게이트 제거 후
m1 arm 재측정이 필요하며, a/b/c arm의 결론은 영향받지 않는다.
**사전 계획**: `plans/experiment/20260725/coarsening_short_budget_crossover.md`
**선행 분석**: `plans/analysis/20260724/lastsemi_rounding_robustness.md`

---

## 1. 질문

20260724 rounding robustness 분석에서 budget f ∈ {5,10,15}% 전 구간에서
"coarsening hurts, K=1 best"가 확인됐고, f가 줄수록 coarsening penalty가 커지는
경향을 보였다(k2 cumulative dRPDf, f=5 / 10 / 15% 순: +29.87 / +27.37 / +24.76 —
f가 작을수록 크다). 이 경향을 역으로 읽으면
**"f가 더 작아지면 penalty가 0을 지나 음수가 되는 crossover가 존재하지 않겠는가"**
라는 가설이 성립한다.

본 분석의 핵심 질문은 f ∈ {1,2,3,4}%로 budget을 더 줄였을 때, coarsening이
K=1을 이기는 crossover가 존재하는가이다. penalty의 두 채널을 분리하기 위해
4개 arm을 설계했다:

| arm | inner flow | 시간 knob | 측정하는 것 |
|-----|-----------|----------|-----------|
| m1 | full solve_flow | budget=f | 두 채널의 합계 (20260724 연장) |
| a | dispatch-only (v4, solve=False) | 없음 (고정비용) | 해상도 채널만, budget→0 극한 |
| b | mcf_lb only | 없음 (고정비용) | 해상도 채널만, constructive seed |
| c | mcf_lb + flip CP(f) | flip에 f% 전체 | 해상도 채널 + 동일 budget CP |

---

## 2. 소스 런 (full paths)

| 역할 | 경로 | 시나리오 |
|---|---|---|
| 본 런 (FULL_RUN, calop4) | `output/20260725_crossover_ladder/20260726T002619_971440` | 210 scenarios × 160 instances |

**커버리지**: 210 scenarios × 160 instances = 33,600 row. 각 160 cell 안에
n ∈ {50,100,150,200} × c ∈ {5,10}이 모두 들어 있음.

**instance slice**: (T,R) = (0.6, 0.2), 가장 어려운 160개 cell.
`metadata/20260721/csr_coarsen_mode_T06_2.yaml`와 동일한 `ins_index` 리스트.

**K 설정 21개**: k1 + {2,4,8,16,32} × {cumulative, ceil, floor, round}

**scenario 구성**:

```
m1_{k}_f{01..04}   84    full inner flow, budget f
a_{k}              21    dispatch-only (v4, solve=False)
b_{k}              21    mcf_lb only
c_{k}_f{01..04}    84    mcf_lb + flip CP(f)
```

**설계 고정값**: `reconstruct_mode = active_but_last_semi`,
`dispatch_seed = v4`. 모든 arm에서 mode 4종(cumulative, ceil, floor, round)
공통 사용.

---

## 3. 재현 커맨드

```bash
# 1. config 생성 (멱등)
uv run python scripts/20260725/build_crossover_config.py

# 2. 본 실행  -> output/20260725_crossover_ladder/20260726T002619_971440
uv run python main.py --config metadata/20260725/coarsening_crossover.yaml

# 3. §4.1 / §4.2 / §5 / §6 의 모든 표 (dRPDf, W/L, mode severity, elapsed)
uv run python scripts/20260726/analyze_crossover_ladder.py \
    output/20260725_crossover_ladder/20260726T002619_971440

# 4. §4.2 / §4.3 의 winner-source depth 표
uv run python scripts/20260725/analyze_csr_winner_source.py \
    output/20260725_crossover_ladder/20260726T002619_971440 --n 200 --c 10
```

**예상 소요**: ~3–4시간 · **실제**: calop4에서 약 3.5시간 (2번 항목 기준)

---

## 4. 결과: crossover 존재 여부 (종합)

**판정: objective 상의 crossover 없음.** 200개 (arm, f, k, mode) 조합 **전부**에서
mean dRPDf > 0 AND win < loss. 목적함수 기준으로 K=1이 전 구간에서 최선이다.

> **⚠ f=1%에는 구현 결함이 섞여 있다 (§4.4).** m1 f=1%에서 K=1은 160개 중
> **20개에 아무 해도 내지 못한다**. 그 20개는 paired dRPDf에서 통째로 빠지므로
> 위 문장은 **"양쪽 다 해를 낸 140개에 대한 진술"**이다. 원인은 예산 부족이
> 아니라 MCF-LB 스텝의 의도치 않은 stop gate이며(§4.4), 게이트 제거 시 K=1도
> 160/160 해를 내므로 **이 비대칭은 사라질 것으로 예상된다**. f=1% 행은 그때까지
> 잠정치로 읽어야 한다.

### 4.1 arm-level summary

dRPDf = RPDf(coarse) − RPDf(k=1), paired by insIndex. 모든 수치는 percentage point.
각 셀은 **4개 mode 중 coarsening에 가장 유리한 값**(= 최소 penalty)이며 괄호에
그 mode를 적었다. 즉 **coarsening 쪽에 최대한 유리하게 잡은 하한**이고, 그런데도
전부 양수다. mode별 전체 값은 `drpdf_by_mode_k.csv`(§9)에 있다.

| arm | K=1 RPDf | k2 best dRPDf | k8 best dRPDf | k32 best dRPDf | W/L (k8 best) |
|-----|---------|--------------|--------------|----------------|------|
| **a** | 36.00% | +1.60 (cumul.) | **+2.12 (ceil)** | +5.46 (ceil) | 43/117 |
| **b** | 41.83% | +19.41 (ceil) | +34.97 (ceil) | +67.99 (ceil) | 0/160 |
| **m1 f=1%** | 55.37% | **+9.65 (ceil)** | +18.57 (ceil) | +39.44 (ceil) | 10/130 |
| **m1 f=2%** | 47.80% | +12.11 (ceil) | +21.51 (ceil) | +28.26 (ceil) | 10/150 |
| **m1 f=3%** | 40.09% | +16.55 (ceil) | +26.35 (ceil) | +30.94 (ceil) | 9/151 |
| **m1 f=4%** | 36.56% | +17.67 (ceil) | +26.31 (ceil) | +29.45 (ceil) | 12/148 |
| **c f=1%** | 40.23% | +19.64 (ceil) | +35.52 (ceil) | +69.03 (ceil) | 0/160 |
| **c f=2%** | 39.87% | +19.79 (ceil) | +35.78 (ceil) | +69.38 (ceil) | 0/160 |
| **c f=3%** | 39.71% | +19.84 (ceil) | +35.87 (ceil) | +69.53 (ceil) | 0/160 |
| **c f=4%** | 39.54% | +19.85 (ceil) | +35.97 (ceil) | +69.70 (ceil) | 0/160 |

**mode별 severity 순서 (본 런, k=8)**: ceil < round ≈ cumulative < floor.
단, **mode 간 격차가 전 arm에서 2pp 이내**다(m1 f=4%: ceil +26.31 / round +26.68 /
cumulative +27.03 / floor +28.11). 20260724(f=5~15%)의 순서는
**round ≈ cumulative < ceil < floor**로 ceil의 위치가 다르고, 그쪽은 격차 자체가
훨씬 컸다(k=8에서 floor +62.98 vs round +30.17). 즉 **순서도 규모도 20260724와
같지 않다.** CP를 포함한 arm(b/c/m1)의 1pp 미만 mode 차이는 CP-SAT 실행 노이즈와
구분되지 않으므로 순서를 결론으로 삼지 않는다. 결론에 쓰이는 것은 "어느 mode를
고르든 dRPDf > 0"이라는 사실뿐이며, 이는 200개 (arm, f, k, mode) 조합 전부에서 성립한다.
(arm `a`는 CP가 없어 완전 결정론이지만 격차가 0.2pp로 더 작다.)

### 4.2 m1 dRPDf vs f curve: penalty가 작아지지 않았다

20260724의 k2 dRPDf는 **cumulative** 기준이므로, 이어 붙일 때도 본 실험의
cumulative 값을 쓴다(mode를 섞으면 이음매의 점프가 mode 효과와 뒤섞인다):

```
mode        f=1%   f=2%   f=3%   f=4% | f=5%   f=10%  f=15%   <- 20260724
cumulative +10.40 +12.34 +17.62 +18.48 | +29.87 +27.37 +24.76
ceil        +9.65 +12.11 +16.55 +17.67 |   (해당 mode 미측정)
```

**dRPDf는 f=5% 부근을 정점으로 하는 역U자(∩)다.** f=5~15%에서는 f가 *늘수록*
penalty가 감소하고(+29.87 → +24.76), f=1~4%에서는 f가 *줄수록* penalty가
감소한다(+18.48 → +10.40). 후자는 crossover를 향한 수렴이 *아니라*
**K=1 자체가 budget에 굶주려 성능이 떨어지기 때문**이다:

(f=1% 행의 dRPDf는 K=1이 해를 낸 140개만의 평균이다 — §4.4.)

| f | m1 K=1 RPDf | depth (n=200,c=10, 20 인스턴스) |
|---|------------|-------------------|
| 1% | 55.37% | 20/20 step-1 (mcf_lb 미완) |
| 2% | 47.80% | 17+3 (85% mcf_lb) |
| 3% | 40.09% | 5+15 (75% flip) |
| 4% | 36.56% | 5+15 (75% flip) |

> f=5%의 depth는 본 런에 없다. 20260724 `lastsemi_fullgrid` 런의 (n=200,c=10)
> 슬라이스에서 K=1은 `29+127+14+10`인데, 이는 **다른 런의 180 인스턴스**(전 (T,R)
> cell) 집계라 위 표의 20 인스턴스와 직접 비교할 수 없다. 방향만 읽으면
> "f=5%에서 K=1은 flip에 머물고 isw까지는 10/180만 도달"이다.

K=1의 RPDf는 f=1%에서 55%로 매우 높다. 이는 **MCF-LB조차 완주하지 못해서**
coarsening과 마찬가지로 step-1에서 잘렸기 때문이다. 이 regime에서의 dRPDf
(cumulative +10.40pp)는 "둘 다 굶주린 상태"에서의 해상도 손실 비교이지, 정상적인
"같은 알고리즘, 다른 해상도" 비교가 아니다.

### 4.3 각 arm 상세

#### arm `a` (dispatch-only, budget→0 극한)

CP 없음, 완전 결정론. **coarsening penalty가 가장 작다**: k8 기준 +2.12pp.
Smoke에서 보였던 insIndex 1419의 음수 dRPDf는 본 런에서도 **그대로 남아 있다**
(k8 cumulative −0.57, k2 cumulative −1.31). 사라진 것은 개별 인스턴스의 음수가
아니라 **평균의 음수**다: 같은 mode의 160개 평균은 +2.15pp이고, 음수를 내는
인스턴스는 mode·K에 따라 41~59개로 늘 소수파다. arm a에서 "coarsening이 이기는
인스턴스"는 흔하지만 결코 과반이 아니라는 뜻이다.

```
k    dRPDf 범위 (4 mode)   W/L 범위 (win/loss)
k2   +1.60 ~ +1.87 pp      54/106 (cumul.) ~ 51/109 (floor)
k8   +2.12 ~ +2.28 pp      43/117 (ceil)   ~ 47/113 (floor)
k32  +5.46 ~ +9.37 pp      19/141 (ceil)   ~  2/158 (floor)
```

penalty가 K에 따라 서서히 증가하며, k=32에서도 floor를 뺀 세 mode가 +7pp 이내
(ceil +5.46 / round +6.17 / cumulative +6.88) → dispatch-only에서는 coarsening
손해가 완만하다. 그러나 dRPDf > 0은 변함없다.

**elapsed**: K=1→k=32에서 n=200,c=10 기준 2.62s→2.61s (사실상 불변, dispatch는
coarsening해도 비용 차이가 미미).

#### arm `b` (mcf_lb only)

MCF-LB 기반 constructive seed. penalty가 매우 크다: k2=+20pp, k8=+35pp.
Coarsening이 MCF-LB의 constructive 품질을 급격히 떨어뜨림을 보여준다.
W/L → 0/160으로 전 인스턴스에서 K=1을 이기지 못함.

**elapsed** (n=200,c=10 20 인스턴스 평균): K=1 3.90s → k8 2.33s (−40%).
Coarsening에 의한 시간 절감은 분명하지만, objective penalty가 훨씬 크다.

#### arm `c` (mcf_lb + flip CP)

b에 flip CP를 추가. flip 예산은 f% 전체를 준다(m1의 10% 몫 × 10배).
**dRPDf가 f에 거의 독립적**: 네 f에서 모두 k2=+19.8pp, k8=+36pp, k32=+69pp.
CP를 추가해도 b arm과 penalty가 거의 같다 → **MCF-LB penalty가 지배적**,
CP는 이 regime에서 penalty를 되돌리지 못한다.

K=1 기준: flip CP가 n=200,c=10에서 개선을 거의 못 냄(RPDf 40% 근처에서 고정).
"원본 해상도에서는 flip CP가 무력하다"는 smoke 관측이 160개에서도 재확인됨.

#### arm `m1` (full flow)

Winner source depth (n=200,c=10, 20 인스턴스). **K>1은 mode마다 깊이가 다르므로
mode를 명시**한다 — 아래는 20260724와 비교 가능한 `cumulative` 기준이고,
`ceil`은 괄호로 병기한다:

```
scenario                  step1(mcf_lb)  step2(flip)  step3(neh)  step4(isw)
m1_K=1  f=1%                 20              0            0           0
m1_K=1  f=2%                 17              3            0           0
m1_K=1  f=3%                  5             15            0           0
m1_K=1  f=4%                  5             15            0           0
m1_K=8  f=1% cumulative      20              0            0           0
m1_K=8  f=2% cumulative       7              8            5           0
m1_K=8  f=3% cumulative       4             11            5           0
m1_K=8  f=4% cumulative       1              7            4           8
m1_K=32 f=4% cumulative       0              0            6          14
  (ceil)  f=2% / f=4%      5,13,2,0 / 4,11,2,3      K=32 f=4%: 0,0,10,10
```

**Coarsening은 깊이 이득을 주지만**, 그 이득이 해상도 손실을 상쇄하지 못한다.
f=4%에서 K=32(cumulative)는 70%(14/20)가 isw까지 도달하지만 dRPDf = **+33.08pp**
(같은 mode 기준)로 K=1(f=4%, step-1,2에서 멈춤)보다 훨씬 나쁘다. 가장 유리한
mode인 ceil로 바꾸면 isw 도달은 50%(10/20)로 떨어지고 dRPDf도 +29.45pp에 그친다 —
**깊이를 더 살수록 더 크게 진다**. 즉 **"깊이 이득은 실재하나, 해상도 손실을
보상할 만큼 크지 않다"**가 실증되었다.

### 4.4 f=1%의 feasibility crossover (paired 표에서 빠진 20개)

`m1_k1_f01`은 160개 중 **20개에서 incumbent를 하나도 등록하지 못한다**
(`obj_value: null`). SubroutineController 로그가 원인을 그대로 말해준다:

```
coarsen_solve_reconstruct[solve_flow]: factor=1, child_timelimit=0.675s (...), steps=5
coarsen_solve_reconstruct[solve_flow]: candidates=0 deduped=0 dropped=0 winner_source=None
```

**해당 20개는 (n=150,c=5) 10개 + (n=200,c=5) 10개**로 정확히 c=5의 큰 n 셀이다.
CSR budget이 `0.0009·f·n·c`이므로 f=1%에서 (150,5)는 0.675s, (200,5)는 0.9s로
전 그리드에서 가장 작다((200,10)은 1.8s로 두 배 이상).

같은 20개에서 coarsened arm은:

| K | 20개 중 해를 낸 수 |
|---|---|
| k=2 | 14~16 / 20 (mode에 따라) |
| k=4, 8, 16, 32 | **20 / 20 (전부)** |

다른 arm·다른 f에서는 이런 결측이 전혀 없다(a/b/c 전 시나리오, m1 f≥2% 전 시나리오
모두 160/160). f=1%·c=5·큰 n에 국한된 현상이다.

#### 원인은 budget starvation이 아니라 의도치 않은 stop gate다 — 즉 버그다

"예산이 작아서 해를 못 찾았다"는 읽기는 **틀렸다.** 실측을 보면 해가 나온
경우까지 포함해 **모든 경우가 예산을 초과한다**:

| scenario | n,c | child budget | 실제 elapsed | 결과 |
|---|---|---|---|---|
| m1_k1 f=1% | 150,5 | 0.675s | 0.78s | 해 없음 |
| m1_k1 f=1% | 200,5 | 0.900s | 1.44s | 해 없음 |
| m1_k1 f=2% | 150,5 | 1.350s | 1.88s | **해 있음** |
| m1_k1 f=2% | 200,5 | 1.800s | 3.53s | **해 있음** |
| m1_k1 f=3% | 200,5 | 2.700s | 3.47s | **해 있음** |

예산은 스테이지 **내부에서 한 번도 강제되지 않는다.** `calc_mcf_lb_and_derive_full_sch`
라운드 1(`mcf_lb_pipeline.py:253-313`)은 3개 스테이지와 그 사이의 3개 게이트로
이루어져 있고, 각 스테이지는 시작하면 중단할 수 없다:

```
_stop_check()                                 ← 게이트 1 (253행, 진입)
apply_lb_by_mcf(...)                          ← MCF LP. 중단 불가
_stop_check()                                 ← 게이트 2 (270행)
heuristic_last_stage_only_from_mcf_lb(...)    ← stop_predicate 안 받음
_stop_check()                                 ← 게이트 3 (287행)
build_full_sch_from_last_stage_only_sch(...)  ← 전체 스케줄 생성(dispatch). stop_predicate 안 받음
```

MCF LP의 계약이 코드에 명시돼 있다(`lb_last_stage_pmtn.py:81-84`):
stop_predicate는 **`mcf.solve()` 직전에 단 한 번만** 검사되며
**"The MCF LP itself is not interruptible mid-solve"**. 애초에 MCF-LB에는
시간제한을 걸 수단이 없고, 걸 수도 없다.

따라서 해가 나오냐 마느냐는 예산의 크기가 아니라 **데드라인이 어느 게이트에
걸렸는가**로만 갈린다:

- f=1%, (200,5): LP 혼자 0.9s를 넘김 → **게이트 2**에서 컷 → dispatch 미실행
- f=2%, (200,5): LP+heuristic이 1.8s 안에 끝남 → 게이트 3 통과 → dispatch가
  예산을 무시하고 완주(총 3.53s) → 해 등록

그리고 `controller.py:1400-1403`은 `r1_build_full is None`이면
`_make_stop_report`만 반환하고 **incumbent를 등록하지 않는다.** 이후 flip CP /
neh_cp / isw는 이미 시간이 지났으므로 각자의 stop guard에 걸려 전부 스킵되고,
`candidates=0`으로 CSR이 끝난다.

**이 게이트들은 의도된 설계가 아니다.** `calc_mcf_lb_and_derive_full_sch`는
incumbent를 만들어 내는 **생산자** 스텝이고, 시간제한을 줄 방법이 없는 이상
**시작했으면 반드시 해를 내고 끝나야 한다.** 게이트 3에서 버려지는
`heuristic.schedule`은 이미 last-stage-only 스케줄을 손에 쥔 상태이고,
`build_full`은 그것을 역방향 dispatch로 펼치는 상대적으로 싼 마지막 한 스텝이다.
이미 예산을 초과한 마당에 그 한 스텝을 아끼려다 **결과를 통째로 0으로 만드는**
것이 현재 동작이다. 올바른 동작은 **모든 경우에 해가 나오는 것**이다.

##### 그래서 §4.4의 "feasibility crossover"는 알고리즘의 성질이 아니다

위 20개에서 K≥4가 이기는 것은 **coarsening이 우월해서가 아니라, coarsened
인스턴스의 LP가 더 빨라서 우연히 게이트를 통과했기 때문**이다. 게이트가 제거되면
K=1도 160/160 해를 내므로 **이 crossover는 사라질 것으로 예상된다.** 즉 §4.4는
알고리즘 비교 결과가 아니라 **구현 결함의 관측 기록**으로 읽어야 한다.

이 해석의 파급은 f=1%에 그치지 않는다. 게이트가 제거되면 K=1의 mcf_lb는 항상
완주하므로 **child budget을 더 많이 소모**하고, 그만큼 downstream(flip / neh /
isw)에 남는 예산이 줄어든다. coarsened arm은 mcf_lb가 원래 빨라 영향이 작으므로,
**K=1 대 coarse의 비교 자체가 짧은 f에서 이동할 수 있다.** 따라서 §4.1~§4.3의
m1 수치는 게이트 제거 후 재측정이 필요하다 — 재실행 계획은
`plans/experiment/20260726/mcf_lb_atomic_gate_removal.md`.

한편 **arm a / b / c의 결론은 이 결함의 영향을 받지 않는다**: a는 mcf_lb를 아예
호출하지 않고, b와 c는 CSR timelimit이 `0.09nc`(글로벌 캡)이라 mcf_lb 단계에서
예산이 binding된 적이 없다(b_k1 실측 완주 시간 2.06s@(150,5) / 3.73s@(200,5) ≪ 캡).
결측이 m1 f=1%에만 나타난 것이 그 방증이다.

**정정**: 위 20개는 paired dRPDf에서 통째로 빠지므로, §4.1의 m1 f=1% 행
(+9.65 / +10.40pp)은 **K=1이 살아남은 140개만의 평균**이다. K=1이 가장 심하게
잘린 20개를 뺀 값이므로 이 행은 K=1에게 유리한 쪽으로 편향돼 있다.

---

## 5. Budget parity & timelimit

outer timelimit = `0.09 · n · c` (n=200,c=10 → 180s)는 모든 arm에 동일하게
적용된다. m1의 inner flow는 CSR `timelimit = 0.0009 · f · n · c`로 제한된다.

| n=200,c=10 | m1 K=1 elapsed | timelimit | ratio | budget bottleneck |
|---|---|---|---|---|
| f=1% | 2.36s | 180s | 0.013 | CSR (1.8s budget) |
| f=4% | 7.41s | 180s | 0.041 | CSR (7.2s budget) |

a/b/c arm의 CSR timelimit은 global cap(`0.09nc`) 그대로 — constructive 고정비용
중간에 자르지 않기 위한 설계.

**elapsed time (n=200,c=10 20 인스턴스 평균)**:

| arm | K=1 | K=8 (cumulative) | coarsening 시간 절감 |
|-----|------|------------------|-------------------|
| a | 2.62s | 2.62s | ~0% |
| b | 3.90s | 2.33s | −40% |
| c (f=4%) | 11.50s | 9.42s | −18% |
| m1 (f=4%) | 7.41s | 7.42s | ~0% |

m1의 시간 절감이 0인 이유: budget cap이 binding이라 K와 무관하게 예산을 다 쓴다.
b의 40% 절감은 MCF-LB의 coarsening 속도 향상에서 온다. c는 그 절감(b의 −1.6s)을
그대로 물려받지만 flip CP가 남은 예산을 계속 쓰므로 상대 절감폭이 −18%로 희석된다.

---

## 6. a arm의 K=1 RPDf vs 다른 arm과의 관계

a_K=1 (v4 dispatch-only) RPDf = 36.00%. 이 값은 놀라울 정도로 경쟁력이 있다:

RPDf는 160 인스턴스 평균, elapsed는 그중 (n=200,c=10) 20개의 평균이다.

| arm | K=1 RPDf | elapsed (n=200,c=10) |
|-----|---------|---------------------|
| a | **36.00%** | 2.62s |
| m1 f=4% | 36.56% | 7.41s |
| m1 f=3% | 40.09% | 5.61s |
| m1 f=2% | 47.80% | 3.94s |
| b | 41.83% | 3.90s |
| c f=4% | 39.54% | 11.50s |

a는 **K=1 dispatch-only로 m1 f=3%보다 좋고, m1 f=4%과 거의 동등**한 RPDf를
내면서 2.8배 빠르다(2.62s vs 7.41s). "짧은 예산에서는 CP를 아예 쓰지 않고 dispatch만
하는 것이 낫다"는 smoke 가설이 160개 인스턴스에서 **확인**되었다.

---

## 7. 종합 판정

1. **목적함수 crossover는 존재하지 않는다** — f=1%까지 budget을 줄여도 200개
   (arm, f, k, mode) 조합 전부에서 dRPDf > 0. W/L 비율도 coarsening에 불리한
   쪽으로 일관된다.

   - **f=1%의 예외는 알고리즘이 아니라 버그다** — m1 f=1%의 (n=150,c=5)·
     (n=200,c=5) 20개에서 K=1이 해를 못 내는 것은 MCF-LB 스텝의 의도치 않은
     stop gate 때문이며(§4.4), 게이트 제거 후 사라질 것으로 예상된다. 따라서
     **coarsening의 장점으로 해석해서는 안 된다.** 다만 그 20개가 빠진 만큼
     f=1% 행은 K=1에 유리하게 편향돼 있으므로, m1 f=1~2% 수치는 재실행
     (`plans/experiment/20260726/mcf_lb_atomic_gate_removal.md`) 후 확정한다.

2. **m1 penalty는 f=5% 부근을 정점으로 하는 역U자(∩)**이며, f<5% 구간에서
   penalty가 줄어드는 것은 crossover를 향한 수렴이 아니라 f가 작아지면 K=1도
   굶주려서 penalty가 겉보기에 줄어드는 현상이다. f=1%에서 dRPDf가 가장 작은
   (cumulative +10.40pp) 이유는 **양쪽 다 step-1에서 잘렸기 때문**이다.

3. **해상도 손실 채널이 깊이 이득 채널을 항상 압도한다.** arm a(순수 해상도 채널)
   에서도 dRPDf > 0이며, m1(두 채널 합계)에서도 깊이 이득이 해상도 손실을
   보상하지 못한다. f=4%에서 K=32(cumulative)는 isw까지 70% 도달하면서도
   +33pp로 크게 진다.

4. **짧은 budget에서는 dispatch-only(a_K=1)가 가장 효율적이다.** RPDf 36.00%를
   2.62s에 내며, m1 f=4%(7.41s, RPDf 36.56%)와 거의 동등하다.

5. "K=1 최선"이라는 verdict는 20260724 f=5~15%에서 확립되었고, 본 분석을 통해
   **f=1~4%까지 확장**되었다. f ∈ [1,15]% 전 구간에서 rounding과 무관하게
   K=1이 최선이다 — **단 목적함수 축에 한하며, f=1%의 feasibility 예외는
   위 1b**.

---

## 8. 후속 과제

- `a` arm의 K=1 dispatch-only가 m1 f=4%과 동등한 RPDf를 2.8배 빠르게 달성한다는
  관측 → 짧은 budget regime에서의 practical recommendation으로 별도 검증 필요.
- n=50 등 작은 인스턴스에서는 f=1%에서도 K=1 mcf_lb가 완주 가능 → size별
  crossover threshold가 다를 수 있음 (본 분석은 size-slice로 추가 분석 가능).
- **[선행] §4.4 stop gate 제거 후 m1 재실행** — 본 문서의 m1 수치를 확정하기
  위한 전제 조건. 계획: `plans/experiment/20260726/mcf_lb_atomic_gate_removal.md`.
  a/b arm은 게이트가 발화한 적이 없어 **bit-identical negative control**로 쓴다.
- 게이트의 파급 범위는 본 런에 그치지 않는다 — CSR child budget이 mcf_lb 완주
  시간에 근접하는 **모든 과거 짧은-budget 실험**(특히 20260714 budget sweep의
  f=5%, 20260724 f=5%)이 같은 컷에 노출됐을 수 있다. 재실행 결과를 보고 어느
  범위까지 재측정할지 판단한다.
- 이 결과는 PRA2017 (T,R)=(0.6,0.2) cell에만 해당. 다른 (T,R) cell에서도
  동일한 결론인지 확장 검증 필요.

---

## 9. 아티팩트

모두 gitignored이며, 위 표들은 이 문서만으로 self-contained하다.

- dRPDf / W/L / mode severity / elapsed:
  `analysis/20260726T002619_971440_crossover_ladder/`
  (`drpdf_by_mode_k.csv` 200행 = 모든 (arm, f, k, mode) — `n_paired` /
  `coarse_only_feasible` 열이 §4.4의 결측 20개를 담고 있다;
  `arm_summary.csv`, `m1_ladder.csv`, `elapsed_by_scenario.csv`)
- winner-source depth: `analysis/20260726T002619_971440_winner_source/`
  (`winner_source_long.csv`, `winner_source_by_scenario.csv`)
- 분석 스크립트: `scripts/20260726/analyze_crossover_ladder.py`,
  `scripts/20260725/analyze_csr_winner_source.py`
- config 생성 스크립트: `scripts/20260725/build_crossover_config.py`,
  생성된 config: `metadata/20260725/coarsening_crossover.yaml`
