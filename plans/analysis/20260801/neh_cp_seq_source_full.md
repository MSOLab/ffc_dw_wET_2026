# NEH-CP sequence source — 1440 그리드 분석

**작성일**: 2026-08-01 · **종류**: 단일-run 사후 analysis (tracked SSOT)
**선행**: `plans/analysis/20260731/neh_cp_seq_source_pilot.md` (3-인스턴스 파일럿),
`plans/experiment/20260731/neh_cp_incumbent_sequence.md` (구현 계획).

---

## 질문

파일럿이 남긴 세 가지를 전 그리드에서 다시 묻는다.

1. 3개 모드(`midpoint` / `first_stage` / `completion`) 중 무엇이 나은가?
   — 파일럿은 **판별 불가**로 끝났다 (모드 효과 2.60–5.13 pp가 인스턴스당 최대
   7.80 pp의 CP 노이즈에 잠김).
2. 어느 seeding prefix가 나은가? — 파일럿은 `dispatch_v4`가 거의 다 한다고 봤다.
3. incumbent 유도 순서가 `job_priority`보다 나은가? — 파일럿은 **"이 런으로 답할
   수 없다"**고 기록했다. 아래 결과 0이 그 판단을 뒤집는다.

## 소스 run (full path)

- `output/20260731_neh_cp_seq_source_compare/20260801T012922_726471`
  — FULL_RUN, calop4, 10 시나리오 × 1440 인스턴스 = 14400 런,
  01:29:22 → 05:50:38 (4:21:16), 에러 0,
  fallback warning 0건 / permutation 보정 warning 0건.
  config 스냅샷: 같은 디렉터리의 `neh_cp_seq_source_compare.yaml`.

**실행 이력 (경합 배제)**: 같은 config로 앞서 두 번 시작했다가 폐기했다 —
`20260801T011349_845611`은 다른 실험(`20260728_dispatch_v4_init_tl/20260801T005105_732515`)과
코어를 나눠 썼고, `20260801T012338_416919`는 그 런의 고아 워커 12개가 살아남아
첫 2분간 경합했다. **본 런은 96코어를 단독 점유한 상태에서 처음부터 끝까지 돌았다**
(프로세스 13개 = main 1 + worker 12, 전 구간 유지 확인).

## 재현

```bash
uv run python scripts/20260801/analyze_neh_cp_seq_full.py \
    output/20260731_neh_cp_seq_source_compare/20260801T012922_726471
uv run python scripts/20260801/analyze_neh_step_quality.py \
    output/20260731_neh_cp_seq_source_compare/20260801T012922_726471
```

첫 스크립트는 런의 `*_rpdf_comparison.csv`(= flow 전체의 `bestObj`)를 읽는다.
둘째 스크립트는 인스턴스별 `*_obj_log.json`을 파싱해 **스텝 경계별 목적값**으로
쪼갠다 — 결과 0 때문에 이것이 순서 효과의 유일한 올바른 측정면이다.
산출물(CSV 13종)은 `analysis/20260801_neh_cp_seq_full/`.

RPDf는 리포트 파이프라인과 같은 대칭 정의를 쓴다 — 둘째 스크립트는 스텝별 목적값을
직접 환산해야 하므로 `ffc_ddw_sum_et._calc.rpd_f`를 **import 해서** 쓴다
(`obj == ref == 0 → 0`; 손으로 쓴 `2(obj−ref)/(obj+ref)`는 이 경우 0/0이 되어
시나리오당 ~57개의 무비용 인스턴스를 조용히 떨어뜨린다). 첫 스크립트가 읽는
`RPDf_BKS_data`와 전 14400행에서 일치함을 확인했다.

---

## 결과 0 — 보고된 `bestObj`는 NEH-CP의 산출물이 아니다

`rpdf_comparison.csv`의 `bestObj`는 **flow 전체의 최량 incumbent**다. NEH-CP가
seed를 이기지 못하면 그 값은 **seed의 값**이고, 세 모드가 모두 같은 숫자를 보고한다.

```
Instance_100_10_3_0,2_0,2_10_Rep0 / neh_cp_midpoint_seq 의 obj_log
  1-calc_mcf_lb_and_derive_full_sch → 52346
  2-run_flip_makespan_cp_from_incumbent → 52346
  3-neh_cp_midpoint_seq             → 89424   ← NEH 자체 산출물 (seed보다 71% 나쁨)
보고된 bestObj = 52346 (seed 값)
```

NEH-CP가 seed를 실제로 개선한 비율은 **65.1–85.1 %**에 불과하다.

| 시나리오 계열 | 개선 비율 | seed 평균 RPDf | NEH 자체 평균 RPDf | flow bestObj 평균 RPDf |
|---|---|---|---|---|
| `mcf_lb->fmm` | 65.4–68.2 % | 48.2 | 30.3–34.6 | 15.7–16.5 |
| `dispatch_v4` | 82.2–85.1 % | 87.0 | 41.7–43.1 | 39.5–40.6 |
| `dv4->mcf_lb->fmm` | 65.1–68.5 % | 46.6 | 28.3–33.6 | 14.1–15.5 |

(`mcf_lb->fmm`를 거치는 계열은 결과 0의 17개 조기정지 인스턴스가 빠진 1423개 기준.)

**따라서 flow 기준 비교는 prefix 효과를 부풀리고 모드 효과를 지운다.** 모드 간
paired 비교에서 exact tie가 1440건 중 **453–479건**(`mcf_lb->fmm` 계열, 약 1/3),
`dispatch_v4` 계열도 309–326건 나온 것이 그 지문이다 — NEH가 seed를 못 이긴
인스턴스에서는 세 모드의 보고값이 문자 그대로 동일하다. NEH 스텝 자체 산출물로
재면 tie는 99–119건으로 떨어진다.

이 사실은 파일럿의 "알려진 교란"도 재해석하게 만든다. 파일럿은 `job_priority` 대
유도 순서를 **답할 수 없다**고 했는데, 그것은 flow `bestObj` 기준에서만 참이다.
NEH 스텝 자체 산출물은 예산이 균질하므로(결과 1) 직접 비교할 수 있다.

## 결과 1 — 노력 균질성 (교란 배제)

| 검사 | 값 | 판정 |
|---|---|---|
| NEH-CP 스텝 소요 (9개 seq 시나리오) | 평균 9.17–9.27 s | 균질 |
| NEH-CP 스텝 소요 (baseline) | 평균 9.57 s | 오히려 baseline이 유리 |
| 시나리오 cap `0.09nc` 대비 총 사용률 | 평균 11.3–17.5 %, 최대 24.8 % | 한 번도 바인딩 안 됨 |

NEH 스텝의 `total_timelimit: 0.0108nc`는 전 시나리오 동일하고, 시나리오 cap이
바인딩되지 않으므로 **NEH는 어디서나 같은 예산을 받았다.** baseline이 0.3–0.4 s
더 받고도 진다는 점은 결과 3의 방향을 강화한다.

코드 확인: `_run_neh_cp`는 incumbent에서 **순서만** 뽑아 `NehCpOption.custom_job_sequence`로
넘기고, 스케줄 자체를 CP warm start로 쓰지 않는다 (`sw_cp`가 `ref_solution=`으로
넘기는 것과 대조적). 즉 NEH 스텝 산출물 차이는 **삽입 순서 차이뿐**이다.

## 결과 2 — 모드 순위: `completion` > `midpoint` > `first_stage`

NEH 스텝 자체 산출물 기준, paired 평균차(pp) ± 95 % CI. 음수면 앞쪽이 낫다.

| prefix | `midpoint` − `first_stage` | `midpoint` − `completion` | `first_stage` − `completion` |
|---|---|---|---|
| `mcf_lb->fmm` | **−2.25** ± 0.82 (5.4 σ) | +2.12 ± 0.77 (5.4 σ) | +4.38 ± 1.06 (8.1 σ) |
| `dispatch_v4` | **−0.73** ± 0.71 (2.0 σ) | +0.64 ± 0.59 (2.1 σ) | +1.37 ± 0.67 (4.0 σ) |
| `dv4->mcf_lb->fmm` | **−2.91** ± 0.95 (6.0 σ) | +2.34 ± 0.91 (5.0 σ) | +5.24 ± 1.10 (9.4 σ) |

**세 prefix 전부에서 순위가 같다**: `completion` < `midpoint` < `first_stage`
(RPDf 낮을수록 좋음). 파일럿의 "판별 불가"는 n=3의 한계였고, 1440 페어에서는
표준오차가 ~38배 줄어 2.0–9.4 σ로 분리된다.

(T, R) 9개 셀 순위 안정성도 같은 방향이다 — `first_stage`는 평균 순위 2.33–2.78로
세 prefix 모두에서 꼴찌이고, `dispatch_v4`에서는 **9개 셀 중 8개에서 최악**이다.

목적함수가 weighted E+T이므로 "마지막 stage 종료시각 = due window와 가장 직접
대응"이라는 `completion` 모드의 설계 근거(계획 §1.3, 참조 저장소에 없는 추가안)가
데이터로 지지된다.

## 결과 3 — 유도 순서 대 `job_priority`: incumbent 품질에 달렸다

NEH 스텝 자체 산출물, baseline(`job_priority`) 대비 paired 평균차:

| 유도 순서 | 평균차 (pp) | σ | win / tie / loss |
|---|---|---|---|
| `neh_cp_completion_seq` | **−11.38** ± 1.26 | 17.7 | 979 / 103 / 341 |
| `neh_cp_midpoint_seq` | **−9.25** ± 1.21 | 14.9 | 963 / 104 / 356 |
| `neh_cp_first_stage_seq` | **−7.00** ± 1.31 | 10.4 | 950 / 103 / 370 |

반면 `dispatch_v4` incumbent에서 유도한 순서는 baseline보다 **나쁘다**
(41.74 / 42.38 / 43.11 vs baseline 41.01).

seed 품질과 나란히 놓으면 설명이 붙는다:

| seed | seed 평균 RPDf | 그 seed에서 유도한 NEH 산출물 |
|---|---|---|
| `dispatch_v4` | 87.0 | 41.7–43.1 (baseline보다 나쁨) |
| `mcf_lb->fmm` | 48.2 | 30.3–34.6 (baseline보다 7.0–11.4 pp 좋음) |
| `dv4->mcf_lb->fmm` | 46.6 | 28.3–33.6 (최상) |

**답**: 유도 순서는 `job_priority`보다 낫다 — 단, **incumbent가 충분히 좋을 때만**.
나쁜 incumbent에서 뽑은 순서는 인스턴스 파라미터 규칙보다도 못하다.

## 결과 4 — flow 기준 최종 성능 (실무 관점)

> **재현 실패 (2026-08-01 추가)**: 독립 replicate run
> `20260801T102120_801587`에서 **아래 표의 1·2위가 뒤집힌다**
> (`completion` 13.84 vs `midpoint` 13.79). paired로 재보면 두 run 모두에서 애초에
> 유의하지 않았다 — A: −0.285 ± 0.728 (−0.77 σ), B: +0.053 ± 0.627 (+0.16 σ).
> flow 기준 top-2 격차(0.29 pp)가 flow 기준 run-to-run SE(≈0.29 pp)와 같은 크기다.
> **`completion`과 `midpoint`는 flow 기준으로 구분 불가**로 읽어야 하고, 이 표에서
> 0.5 pp 미만의 차이는 읽지 않는다. 결과 2의 모드 순위 주장은 NEH 스텝 자체 산출물
> 기준이며 6/6 재현되므로 영향받지 않는다.
> 상세: `plans/analysis/20260801/neh_cp_seq_replicate.md`.

| 시나리오 | 평균 RPDf (%) |
|---|---|
| `dv4_mcf_fmm_neh_cp_completion_seq` | **13.75** |
| `dv4_mcf_fmm_neh_cp_midpoint_seq` | 14.04 |
| `dv4_mcf_fmm_neh_cp_first_stage_seq` | 15.15 |
| `neh_cp_completion_seq` | 15.36 |
| `neh_cp_midpoint_seq` | 15.44 |
| `neh_cp_first_stage_seq` | 16.13 |
| `dv4_neh_cp_completion_seq` | 39.51 |
| `dv4_neh_cp_midpoint_seq` | 40.04 |
| `dv4_neh_cp_first_stage_seq` | 40.57 |
| `neh_cp_baseline` | 41.01 |

`dispatch_v4` 단독 seeding은 baseline과 **구분되지 않는다** (−0.44 ~ −1.49 pp,
0.6–1.9 σ). `mcf_lb->fmm`을 얹는 순간 25 pp 이상 벌어진다.

> **주의 — 등예산 비교가 아니다.** 평균 소요는 baseline 9.57 s < `dispatch_v4`
> 10.32 s < `mcf_lb->fmm` 14.32 s < `dv4->mcf_lb->fmm` 15.21 s다. prefix 간 25 pp
> 격차에는 4–5 s의 추가 예산이 섞여 있다. 모드 비교(결과 2)는 같은 prefix 안에서
> 이뤄지므로 이 문제가 없다.

## 결과 5 — 크기 의존성

`(n, c)` 셀 평균 RPDf (flow 기준). `mcf_lb->fmm` 계열은 인스턴스가 커질수록 급격히
좋아지는 반면 `dispatch_v4` 단독은 정체한다.

| (n, c) | `dv4_mcf_fmm_*_completion` | `dv4_neh_cp_completion` | baseline |
|---|---|---|---|
| (50, 5) | 25.36 | 35.34 | 33.93 |
| (100, 5) | 16.71 | 43.73 | 42.78 |
| (150, 5) | 5.40 | 46.64 | 44.17 |
| (200, 5) | **0.54** | 44.06 | 45.11 |
| (200, 10) | **2.64** | 34.92 | 41.84 |

## 결과 6 — 모드는 서로 상보적이다 (oracle 조합)

인스턴스별로 사후에 최량 모드를 골랐을 때의 평균(oracle mean). 계열 안에서만 조합하며,
값은 `scripts/analyze_dispatch_sweep.py`의 `oracle_value`로 계산했다.

| 조합 (`dv4->mcf_lb->fmm` 계열) | flow 기준 | NEH 스텝 기준 |
|---|---|---|
| `completion` 단독 | 13.751 | 28.332 |
| `midpoint` 단독 | 14.036 | 30.669 |
| `first_stage` 단독 | 15.151 | 33.575 |
| `completion` + `midpoint` | **11.646** | **25.333** |
| `completion` + `first_stage` | 11.768 | 25.716 |
| `midpoint` + `first_stage` | 12.186 | 27.925 |
| 셋 다 | **10.673** | **24.124** |

최량 페어는 **`completion` + `midpoint`**로 최량 단독 대비 −2.11 pp, 셋 다면 −3.08 pp다
(NEH 스텝 기준으로는 −3.00 / −4.21 pp). 다른 두 계열도 같은 모양이다 —
`mcf_lb->fmm`은 15.357 → 13.245(페어) → 12.007(셋), `dispatch_v4`는
39.513 → 37.105 → 35.603.

**조합 이득(3.1 pp)이 모드 간 평균차(0.3–1.4 pp)보다 크다**는 점이 핵심이다. 즉
`completion`의 우위는 "거의 모든 인스턴스에서 이긴다"가 아니라 **"이길 때 크게 이긴다"**
쪽이다. flow 기준 엄밀 승자(유일 최소) 수는 `dv4->mcf_lb->fmm`에서
`completion` 328 / `midpoint` 332 / `first_stage` 312로 사실상 균등하고, 나머지
468개는 동률이다(그중 425개는 셋 다 동률 — 결과 0의 seed 보고 현상).

> **동률 처리 주의**: `idxmin`은 동률일 때 열 순서상 첫 열에 승리를 몰아준다. 위
> 숫자는 유일 최소일 때만 승리로 세는 엄밀 카운트다. 순진하게 세면 열 순서에 따라
> `completion` 786 또는 `midpoint` 785 같은 값이 나오는데, 이는 425개 동률이 어느
> 열에 붙느냐의 인공물이다.

NEH 스텝 기준(동률이 98–104개로 줄어든 상태)에서는 `completion` 542 / `midpoint` 417 /
`first_stage` 360으로 `completion`이 빈도에서도 앞선다. 그래도 절반 이상의 인스턴스는
다른 모드가 이긴다.

> **oracle은 실행 가능한 설정이 아니다.** k개 조합은 인스턴스마다 사후 최량을 고르는
> 것이므로 **k배 예산**을 쓴다. 등예산 비교로 읽으려면 `completion` 하나에 3배 예산
> (`0.0108nc` → `0.0324nc`)을 준 시나리오가 필요하고, 그건 이 런에 없다. 저장소의
> 다른 oracle 분석(`analyze_dispatch_sweep.py` 계열)과 같은 단서다.

## 파일럿과의 불일치는 population effect다

파일럿은 `dispatch_v4`가 `mcf_lb->fmm`보다 5.21 pp **좋다**고 했다. 전 그리드에서는
24 pp **나쁘다**. 방향이 뒤집힌 이유는 파일럿의 3 인스턴스가 모두
`(T, R) = (0.6, 0.2)`, `n=50`, `c=5` 한 셀이었기 때문이다. 그 셀만 떼어 보면:

| (T, R) = (0.6, 0.2) | `dispatch_v4` 계열 | `mcf_lb->fmm` 계열 |
|---|---|---|
| flow 평균 RPDf | 24.84–25.11 | 30.25–30.79 |

**파일럿의 셀 안에서는 파일럿 결론이 그대로 성립한다.** 틀린 것은 결론이 아니라
일반화였다. 파일럿 문서가 이미 다른 비교에 대해 기록한 "population effect"가 여기서
한 번 더 재현됐다.

---

## 남은 한계

- **17개 인스턴스는 NEH 비교에서 빠진다.** `T=0.2, R=1.0` 계열에서 seed가 목적값
  0(완전 정시)에 도달해 정지 조건이 걸려 NEH 스텝이 실행되지 않았다. `dispatch_v4`
  단독 계열은 seed가 0에 못 미쳐 1440개 전부 남는다 — 즉 이 17개의 결측 자체가
  `mcf_lb->fmm`이 강하다는 신호다.
- **prefix 비교는 등예산이 아니다** (결과 4의 주의). 예산을 맞춘 prefix 비교는 이
  런으로 답할 수 없고, `analysis/20260729_init_budget_curve`처럼 f를 축으로 잡은
  별도 설계가 필요하다.
- `bottleneck` 모드는 파일럿 결론(구조적 축퇴)에 따라 config에서 제외된 상태다.

## 조치 제안

1. **기본 모드를 `completion`으로.** 세 prefix, 9개 (T,R) 셀 전부에서 `first_stage`
   보다 낫고 `midpoint`보다도 일관되게 낫다.
2. **`dispatch_v4` 단독은 NEH 순서 소스로 쓰지 않는다.** 유도 순서가 `job_priority`
   보다 나빠 순서 유도 자체가 역효과다.
3. 후속 후보: `mcf_lb->fmm` seed 대비 **등예산**으로 dv4 prefix의 가치를 재는 실험.
   현재 `dv4->mcf_lb->fmm`이 `mcf_lb->fmm`보다 1.0–1.6 pp 좋지만(2.9–5.3 σ) 0.9 s를
   더 쓴다.
4. 결과 6의 상보성(3.1 pp)은 **모드를 하나로 고정하지 않는** 설계를 시사하지만, oracle은
   k배 예산을 쓴다. 실행 가능한 형태로 만들려면 (a) `completion` 단독에 3배 예산을 준
   등예산 대조군, (b) 인스턴스 파라미터로 모드를 고르는 규칙이 oracle 이득의 얼마를
   회수하는지 — 두 가지를 먼저 재야 한다. 이 런의 데이터로 (b)의 상한은 계산 가능하다.
