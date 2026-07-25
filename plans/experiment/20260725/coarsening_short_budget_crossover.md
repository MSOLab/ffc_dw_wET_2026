# sub-5% budget에서 coarsening이 K=1을 이기는 crossover가 존재하는가 (사전 작성)

**작성일**: 2026-07-25 (개정 2026-07-26) · **종류**: 실험 실행 계획(사전 작성, 미실행)
**상태**: 실행 준비 완료 — config·분석 스크립트 작성됨, 본 런 미실행.
**선행 근거**: `plans/analysis/20260724/lastsemi_rounding_robustness.md`
**config**: `metadata/20260725/coarsening_crossover.yaml`
(생성기 `scripts/20260725/build_crossover_config.py`)

> ⚠️ 이 문서는 20260724 rounding robustness 분석에서 파생된 **open question**를
> 별도 실험으로 분리한 것이다. 그 분석의 결론(네 rounding 모두 K=1 최선, ROBUST)은
> 본 실험과 무관하게 유효하다. 여기서 답하려는 것은 **더 짧은 budget에서의 극한 거동**뿐.

---

## 1. 질문

20260724 분석은 budget `f ∈ {5, 10, 15}%`에서 coarsening penalty를 측정했고, 그 구간 안에서
**budget이 줄수록 penalty가 커짐**(cumulative k>1 vs k=1):

```
k   f=5     f=10    f=15
2  +29.87  +27.37  +24.76
4  +35.30  +32.31  +27.96
8  +38.35  +34.70  +31.29
```

또한 **k2↔k8 스프레드도 f가 줄수록 벌어진다**: 6.53 (f=15) → 7.33 (f=10) → 8.49 (f=5) pp.

**핵심 질문**: 이 추세를 `f = 4, 3, 2, 1%`로 더 밀면 —

- (Q1) k2↔k8 스프레드는 계속 벌어질까, 아니면 어느 지점에서 꺾여(좁아져) coarse arm들이
  서로 수렴할까?
- (Q2) K=1이 예산 안에 쓸만한 incumbent를 못 만드는 regime에 도달하면, coarsening penalty가
  0으로 수렴하거나 **음수(coarsening이 이김)** 로 뒤집히는 crossover가 존재하는가?

**현재 답: 모른다.** [5,15]% 안의 gradient는 crossover의 **반대** 방향이지만, crossover가 있다면
그 regime은 f < 5% (미측정)이고, f=5%에서도 K=1은 여전히 RPDf 26.6으로 실질 해를 뽑는다
(벽 근처가 아님). → 직접 측정 필요.

### 1.1 penalty의 두 채널 (본 실험의 설계 근거)

full flow의 dRPDf는 **서로 반대 방향인 두 항의 합**이다:

```
dRPDf(full flow) = [같은 깊이에서의 해상도 손실]  +  [coarsening이 벌어준 깊이 이득]
                     (양수, coarsening에 불리)      (음수, coarsening에 유리)
```

"깊이 이득"은 추측이 아니라 측정된 사실이다.
`scripts/20260725/analyze_csr_winner_source.py`로 20260724 런의 inner `solve_flow`가
**어느 step에서 최종 승자를 냈는지**(= budget이 허락한 알고리즘 깊이)를 세면, f=5 %,
(n=200, c=10) 180 인스턴스에서:

```
depth                  1-mcf_lb  2-flip  3-neh_cp  4-isw
csr_k1_tl05_lastsemi         29     127        14     10
csr_k8_tl05_lastsemi          1      10        50    119
```

**K=1은 70 %가 `run_flip_makespan_cp_from_incumbent`에서 끝나고**(4개는 candidate 1개 =
mcf_lb 도중 잘림), K=8은 66 %가 `incremental_sw_cp`까지 간다. 그런데도 목적함수는 K=8이
크게 진다(RPDf 65.0 vs 26.6). 이 깊이 굶주림은 **f ≤ 5 %에 특유**하다 — f=10/15 %에서는
K=1도 180개 중 ~160개가 isw까지 도달한다.

→ 그러므로 f<5 %에서 crossover를 찾으려면 **두 채널을 분리해서** 재야 한다. 방법(1)은
합계를, 방법(2)의 사다리는 해상도 채널만 단독으로 잰다.

---

## 2. 두 가지 측정 방법

### 방법 (1) — `m1`: 현재 full CSR inner flow 유지, budget만 하향

20260724와 **완전히 동일한 inner `solve_flow`**(mcf_lb → flip_makespan_cp → neh_cp →
incremental_sw_cp → solve_base_model_cpsat)를 유지하고, CSR `timelimit`과 그에 비례하는
내부 TL만 `f ∈ {1, 2, 3, 4}%`로 줄인다. 모든 내부 TL은 `lastsemi_fullgrid.yaml`의 f=5 %
블록에서 **엄밀히 비례 축소**한 값이다(f=5,10,15를 정확히 재현하는 계수):

| 항목 | 값 (f = %) | f=1 | f=4 | (검산) f=5 |
|---|---|---|---|---|
| CSR `timelimit` | `0.0009·f nc` | 0.0009nc | 0.0036nc | 0.0045nc ✓ |
| flip `cp_tl` | `0.00009·f nc` | 0.00009nc | 0.00036nc | 0.00045nc ✓ |
| neh `total_timelimit` | `0.00027·f nc` | 0.00027nc | 0.00108nc | 0.00135nc ✓ |
| isw multiplier | `0.00005·f` | 0.00005 | 0.0002 | 0.00025 ✓ |

- **장점**: 20260724 결과와 동일 축 위의 연장 — [1,15]% 곡선을 이어 crossover 유무를 직접 관찰.
- **주의**:
  - K=1 baseline은 f<5%에 존재하지 않음(재사용 불가) → **K=1도 새로 실행**해야 함.
    즉 시나리오 = `{cumulative,ceil,floor,round} × k{2,4,8,16,32} + k1` × f{1,2,3,4}.
    **mode는 4개 전부 유지**(축소하지 않음) — 20260724 표와 같은 격자를 f<5%로 그대로 연장해야
    mode별 severity 순서(round ≈ cumulative < ceil < floor)가 짧은 budget에서도 보존되는지
    함께 읽을 수 있다.
  - budget parity가 매우 짧은 TL에서 여전히 성립하는지(고정비용이 지배하지 않는지) 재확인.
  - CP 노이즈: f가 작을수록 CP가 덜 도니 노이즈는 오히려 감소하는 경향이나, mean dObj를
    노이즈 플로어(~±350)와 대조하는 게이트는 유지.
  - **f=1 %에서 m1은 K=1의 mcf_lb조차 완주하지 못한다**(§5 스모크). 이 구간의 m1 K=1은
    "잘린 constructive"이지 "짧은 예산의 알고리즘"이 아니다 — 판독 시 반드시 병기.

### 방법 (2) — seed ladder: 깊이를 고정하고 해상도만 변화

`m1`이 f를 줄이면 **깊이와 해상도가 동시에** 변한다. 사다리는 깊이를 각 rung에 고정해
해상도 채널만 남긴다. 세 rung 모두 `reconstruct_mode`·K 격자가 `m1`과 동일하다.

| rung | inner flow | 시간 knob | 결정론 |
|---|---|---|---|
| `a` | `seed_dispatch="v4", solve=False` (CP 없음) | 없음 | **완전 결정론** (`SEED_ONLY`) |
| `b` | `calc_mcf_lb_and_derive_full_sch` 하나 | **없음**(고정비용) | 결정론(CP 없음) |
| `c` | `b` + `run_flip_makespan_cp_from_incumbent` | flip `cp_tl` = `0.0009·f nc` | CP 노이즈 있음 |

- **`a` (dispatch-only)**: budget → 0 극한. `coarsen vs original`을 **reconstruction 해상도
  효과만**으로 비교하는 셈. `solve=False`는 `coarsened_status="SEED_ONLY"`, `cp_progress_log`
  비어 있음 → **CP 노이즈 없음, 재실행 분산 없음**. factor=1은 항등이므로 K=1 = 원본
  인스턴스에 v4 dispatch만 적용.
- **`b` (MCF-LB constructive)**: `a`와 **다른 seed 계열**이지 `a`의 다음 단계가 아니다
  (v4 dispatch vs MCF-LB 파생 스케줄). f=5 %에서 K=1이 실제로 살고 있는 알고리즘이 바로
  `b`~`c` 구간이므로(§1.1), 이 rung이 과녁의 정중앙이다.
- **`c` (b + flip CP)**: flip CP에 **f % 예산 전체**(`0.0009·f nc`)를 준다. `m1`의 10 % 몫
  (`0.00009·f nc`)을 그대로 쓰면 CP가 굶어 `UNKNOWN`으로 끝나 **`c`가 `b`와 비트 단위로
  동일해진다**(§5에서 실측·수정). 이 정의 덕에 같은 f에서 `m1` vs `c`가 **동일 예산**
  비교가 된다: "같은 f를 full flow에 쓸 것인가, flip CP 하나에 몰 것인가".

- **비교 격자**(세 rung 공통): `{cumulative, ceil, floor, round} × k{2,4,8,16,32}` vs
  **K=1**. dRPDf = RPDf(coarse) − RPDf(k=1), 같은 (rung, f, insIndex) per-instance paired.
- **판독**: 어떤 rung에서 dRPDf ≤ 0인 (mode, k)가 나오면, "충분히 짧으면 coarsening이 이득"의
  **존재 증거**. 세 rung 모두 양수면 **해상도 채널은 어디서도 coarsening 편이 아니며**,
  깊이 채널은 이미 k=8에서 포화(§1.1)이므로 crossover는 존재할 수 없다는 결론이 따라온다.

두 방법은 상보적이다: 사다리는 budget→0 **끝점**(하한, 저노이즈)을, `m1`은 그 끝점으로 가는
**곡선**을 준다. 사다리에서 crossover 신호가 보이면 `m1`로 어느 f에서 실제로 뒤집히는지 특정.

---

## 3. 실행 설정

**instance slice**: `(T, R) = (0.6, 0.2)` **160 인스턴스**
(1440 격자의 9개 (T,R) 셀 중 하나이자 가장 어려운 셀. `metadata/20260721/csr_coarsen_mode_T06_2.yaml`
와 동일한 `ins_index` 리스트이며, 생성기가 `pra2017_bks_table.csv`에서 직접 유도해 검증한다.)
셀 내부에 `n ∈ {50,100,150,200} × c ∈ {5,10}`가 모두 들어 있으므로, size 의존성은 사후
슬라이스(`--n/--c`)로 읽는다.

**시나리오 210개** = 21개 K 설정 × (m1 f4 + a 1 + b 1 + c f4):

```
K 설정 21개 = k1 (mode 무관, factor=1은 항등) + {2,4,8,16,32} × {cumulative,ceil,floor,round}

m1_{k}_f{01..04}   84    full inner flow, budget f
a_{k}              21    dispatch-only (v4, solve=False)
b_{k}              21    mcf_lb only
c_{k}_f{01..04}    84    mcf_lb + flip CP(f)
```

**`a`/`b`/`c`의 CSR `timelimit`은 전역 캡(`0.09nc`) 그대로** 둔다 — 고정비용 constructive를
중간에 자르지 않기 위해서다. 그 대신 이 세 rung은 **equal-algorithm 비교이지
equal-wall-clock 비교가 아니다**(§4 caveat 1). 실제 비용은 `elapsedTime`으로 사후 판독한다.

```bash
# config 재생성 (멱등; 스키마·메서드명 검증 포함)
uv run python scripts/20260725/build_crossover_config.py

# 본 실행
uv run python main.py --config metadata/20260725/coarsening_crossover.yaml
    # -> output/20260725_crossover_ladder/<timestamp>/
```

**예상 소요**: `instance_worker_cnt=12 × solver_thread_cnt=8 = 96` = 물리 코어 수. §5 스모크
실측(가장 큰 인스턴스 기준 arm별 비용)에서 외삽하면 **대략 3–4시간**.

**설계 고정값**:

- **reconstruct_mode**: 20260724와 동일하게 `active_but_last_semi` 고정(coarsening에 가장 유리
  → crossover를 가장 관대하게 탐지).
- **mode 격자**: `{cumulative, ceil, floor, round}` **4개 전부** — 방법(1)·(2) 공통. 20260724와
  동일 격자를 유지해야 f<5% 결과를 그 표에 그대로 이어 붙일 수 있고, mode별 severity 순서가
  budget에 의존하는지도 같이 판정된다(축소하지 않는다).
- **K=1은 rounding-무관**이므로 mode별로 4벌 돌리지 않고 시나리오 1개
  (`FFcDDWParameters.coarsen_processing_times`는 `factor=1`에서 항등).

**분석 재사용**:

- 목적함수 판정: `scripts/20260724/analyze_rounding_robust.py`의 pairing/`_wtl`/budget-parity
  블록을 그대로 재사용 가능(f 축·K 축만 확장).
- 깊이 판정: `scripts/20260725/analyze_csr_winner_source.py` (m1·b·c 대상. `a`는 legacy
  non-`solve_flow` 경로라 요약 로그를 남기지 않아 이 표에 나타나지 않는다).
- 단일 런이므로 20260724처럼 크로스런 merge(symlink + artifact_layout restamp)가 **불필요**.

**판정 기준**:

- crossover **존재**: 어떤 (rung, mode, k, f)에서 mean dRPDf ≤ 0 AND win ≥ loss (paired).
- crossover **부재**: `m1` f→1 %와 사다리 세 rung 전부에서 dRPDf > 0 → "K=1 최선"이
  budget 전 구간에서 견고(20260724 결론을 극한까지 확장).

---

## 4. 판독 시 반드시 병기할 caveat

1. **`b`는 f축 위에 없다.** `calc_mcf_lb_and_derive_full_sch`에는 시간 인자가 없어 (mode, k)당
   **점 하나**다. 게다가 coarse MCF-LB가 원본보다 싸므로(§5: n=200에서 3.36s → 2.20s, −34 %)
   **equal-wall-clock 비교가 아니다** — "같은 알고리즘, 다른 시간". 20260724 표와 같은 축인
   것처럼 읽으면 안 된다. 대신 그 시간 차이 자체가 "coarsening이 벌어준 시간"의 정량치다.
2. **`m1`의 짧은 f에서는 잘림이 발생한다.** f=1 %의 CSR 예산은 n=200,c=10에서 1.8 s인데
   MCF-LB 단독이 3.3 s 걸린다(§5). 이 구간의 비교는 "누가 더 잘 풀었나"가 아니라 "누가 먼저
   잘렸나"가 섞인다 — `analyze_csr_winner_source.py`의 candidate 수 / winner depth 분포를
   **1급 진단으로 함께 보고**하고 평균에 그냥 섞지 않는다.
3. **`c`의 f는 `m1`의 f와 같은 f가 아니다.** `c`의 총 시간 = `고정 MCF 비용 + f`이고,
   `m1`의 총 시간 = `f`(그 안에서 5개 step이 나눠 씀). 같은 f 라벨이라도 벽시계는 `c`가 더
   길다. 실측 `elapsedTime`을 반드시 병기.
4. **`a`와 `b`는 사다리의 0단/1단이 아니라 서로 다른 두 seed 계열**이다(v4 dispatch vs
   MCF-LB). 또 `a`만 legacy non-`solve_flow` 코드 경로를 탄다 — 코드경로 차이가 사다리 효과로
   오독되지 않도록 라벨링한다.
5. **노이즈**: `a`·`b`는 CP가 없어 완전 결정론(paired 비교가 매우 깨끗). `c`·`m1`만 CP 노이즈
   대상이며, mean dObj를 노이즈 플로어(~±350, 단 1440 격자 기준이므로 160 셀에서는 더 큼)와
   대조하는 게이트를 유지한다.
6. **crossover가 "존재하지만 양쪽 다 RPDf가 매우 큰(둘 다 쓸모없는) regime"이라면 실용적
   함의는 낮다.** 판독 시 절대 RPDf 수준을 반드시 병기.

---

## 5. 사전 스모크 (2026-07-26, 2 인스턴스 × 9 시나리오, 25 s)

`insIndex 60` (n=50, c=5, mc=15) 과 `insIndex 1419` (n=200, c=10, mc=50) 으로 config를
end-to-end 검증했다. **이 값들은 2 인스턴스 표본이므로 결론이 아니라 설계 근거**다.
(런 디렉터리: `output/20260725_crossover_smoke/20260726T001241_122088`,
`output/20260725_crossover_smoke2/20260726T001416_442414`.)

### 5.1 설계를 바꾼 발견 — `c`의 flip 예산

flip `cp_tl`을 `m1`과 같은 10 % 몫으로 주면 CP가 `status=UNKNOWN`으로 아무것도 못 찾고
**`c`의 bestObj가 `b`와 완전히 동일**해졌다(531952 = 531952). → `c`의 flip에 f % 예산
**전체**를 주도록 수정. 수정 후 개선이 확인된다:

```
insIndex  arm                    bestObj    RPDf%   elapsed
60        b_k1                    73674     57.09     0.30
60        c_k1_f01                71037     53.73     0.55
60        c_k1_f04                70572     53.12     1.23
1419      b_k8_cumulative        690222     66.04     2.20
1419      c_k8_cumulative_f01    689661     65.96     4.13
1419      c_k8_cumulative_f04    687466     65.68     9.53
```

단 **n=200의 K=1은 f=4 %(7.2 s)를 줘도 flip이 개선을 못 낸다**(531952 그대로). 원본 해상도의
flip CP가 큰 인스턴스에서 무력하다는 또 하나의 깊이-채널 증거.

### 5.2 arm `b`의 고정비용 (§4 caveat 1·2의 실측 근거)

```
           n=50,c=5   n=200,c=10        f=1% 예산(0.0009nc)
b_k1          0.30 s       3.36 s        0.225 s / 1.8 s
b_k8          0.19 s       2.20 s
b_k32         0.18 s       2.11 s
```

→ **k=1의 MCF-LB는 f=1 % 예산의 약 1.5–1.9배**로, f=1 %에 들어가지 않는다(f≈2 %에서 겨우 맞음).
coarsening은 이 고정비용을 n=200에서 **−34 %** 깎아준다.

### 5.3 arm `a`가 crossover 신호를 보인다 (2 인스턴스, 일화적)

```
insIndex  a_k1 RPDf%   a_k8 RPDf%   dRPDf
60             31.25        39.84   +8.59
1419           25.59        25.02   −0.57   ← 음수
```

큰 인스턴스에서 **dispatch-only는 K에 거의 평평하고, K=8이 K=1을 근소하게 이겼다**. 같은
인스턴스에서 `m1_k1_f01`은 60.10 %, `b_k1`은 41.93 %이므로 **arm `a`가 셋 중 가장 좋다** —
"budget이 극도로 짧으면 CP를 아예 안 쓰는 편이 낫다"는 방향. 이것이 본 실험 전체에서 가장
crossover에 가까운 신호이며, 160 인스턴스 본 런에서 확인/기각할 1순위 가설이다.

---

## 6. 미결 / 확인 필요

- 본 런의 `elapsedTime`으로 `m1`의 budget parity가 f=1~4 %에서도 성립하는지(고정 오버헤드가
  지배하지 않는지) 재확인 — §5.2가 이미 f=1 %에서는 깨짐을 시사한다.
- `a`의 v4 seed 품질이 K에 따라 어떻게 변하는지 — §5.3의 "거의 평평"이 160 인스턴스에서도
  유지되는지가 핵심.
- 사다리 세 rung이 모두 dRPDf > 0이고 `a`만 ≤ 0이라면, 결론은 "crossover는 **CP를 쓰지 않을
  때만** 존재한다"가 된다. 이 경우 실용적 함의(짧은 예산에서는 dispatch-only가 최선)를
  별도로 검증할 후속 실험이 필요하다.
