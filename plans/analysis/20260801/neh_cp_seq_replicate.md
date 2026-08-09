# NEH-CP sequence source — replicate 쌍 perturbation 분석

**작성일**: 2026-08-01 · **종류**: 2-run 교차 analysis (tracked SSOT)
**선행**: `plans/analysis/20260801/neh_cp_seq_source_full.md` (본 문서가 검증하는 단일-run 분석),
`plans/analysis/20260731/neh_cp_seq_source_pilot.md` (3-인스턴스 파일럿).

---

## 질문

단일-run 분석(`neh_cp_seq_source_full.md`)의 결론은 **한 번의 run 안에서** 계산한
paired CI에 기대고 있다. 그 CI는 노이즈가 인스턴스 사이에만 있다고 가정하는데,
8-thread wall-clock CP-SAT은 비결정적이므로 **시나리오 평균 자체가 run마다 흔들린다.**
replicate가 없으면 그 흔들림을 잴 수 없다.

1. 1440-인스턴스 시나리오 평균의 run-to-run 노이즈는 얼마인가?
2. 단일-run 분석이 발표한 결론 — 모드 순위 `completion` < `midpoint` < `first_stage`,
   시나리오 순위표 — 이 독립 replicate에서 재현되는가?

## 소스 run (full path)

동일 config(`metadata/20260731/neh_cp_seq_source_compare.yaml`)의 스냅샷 sha256이
두 run에서 **일치**한다 (`e39e3a2e67410d9d`) — 진짜 replicate 쌍이다.

- **run A** `output/20260731_neh_cp_seq_source_compare/20260801T012922_726471`
  — 01:29:22 → 05:50:38 (4:21:16), calop4.
- **run B** `output/20260731_neh_cp_seq_source_compare/20260801T102120_801587`
  — 10:21:20 → 14:42:33 (4:21:12), calop4. 10 시나리오 × 1440 인스턴스, 에러 0.

**run B는 의도된 replicate가 아니다.** chained NEH-CP config
(`metadata/20260801/neh_cp_seq_full_compare.yaml`)를 돌리려던 것이었는데,
`main.py`의 하드코딩된 `CONFIG_PATH`가 구 config를 가리킨 채였다. 우연히 생긴
replicate지만 측정 가치는 그대로다 — 두 run의 시나리오별 평균 `elapsedTime` 차이가
전부 **≤0.03 s**라, 관측된 차이는 부하나 경합이 아니라 순수 solver 비결정성이다.

## 재현

```bash
uv run python scripts/20260801/analyze_neh_step_quality.py \
    output/20260731_neh_cp_seq_source_compare/20260801T102120_801587 \
    --out-dir analysis/20260801_neh_cp_seq_full_runB
uv run python scripts/20260801/analyze_neh_seq_replicate.py
```

첫 명령은 run B의 스텝 경계별 목적값을 뽑는다 (run A의 것은
`analysis/20260801_neh_cp_seq_full/step_objectives.csv`에 이미 있다). 둘째가 교차
분석으로, 산출물은 `analysis/20260801_neh_cp_seq_replicate/` (CSV 4종).

측정면은 **`rpdf_neh` — NEH-CP 스텝 자체 산출물**이지 flow의 `bestObj`가 아니다.
선행 문서 결과 0이 밝혔듯 flow 값은 NEH가 seed를 못 이길 때 *seed의* 값이라,
지금 재려는 효과를 정확히 지워버린다.

---

## 결과 1 — 인스턴스 단위 노이즈는 파일럿 추정보다 크다

NEH 스텝 자체 산출물 기준, 같은 (시나리오, 인스턴스)에서 run A − run B:

| 지표 | 값 |
|---|---|
| per-instance delta의 sd | **12.356 pp** |
| 평균 \|delta\| | 5.434 pp |
| p95 \|delta\| (시나리오별) | 13.7 – 20.8 pp |
| exact tie (결정적 인스턴스) | 1189 / 14298 (8.3 %) |

파일럿은 `bottleneck`/`first_stage` near-replicate 쌍으로 "인스턴스당 최대 7.80 pp"를
노이즈 프록시로 삼았다. **그건 과소평가였다** — 실제 sd가 12.36 pp이고 p95가 20 pp를
넘는 시나리오도 있다. 파일럿이 모드를 순위 매기지 못한 것은 옳은 판단이었다.

## 결과 2 — 그러나 1440-평균은 매우 안정적이다

| 지표 | 값 |
|---|---|
| 1440-인스턴스 시나리오 평균의 run-to-run SE | **0.230 pp** |
| 95 % 밴드 | ±0.451 pp |
| 실측 최대 이동폭 | 0.703 pp (`neh_cp_baseline`, 1.75 σ) |
| 노이즈 범위 안에서 재현된 시나리오 평균 | **10 / 10** |

per-instance 노이즈가 √1440 ≈ 38배 줄어드는 것이 실측으로 확인됐다. 이것이 단일-run
분석이 파일럿의 "판별 불가"를 뒤집을 수 있었던 근거이며, 그 근거가 이제 검증됐다.

## 결과 3 — 모드 순위는 완전히 재현된다

| prefix | run | `completion` | `midpoint` | `first_stage` |
|---|---|---|---|---|
| `mcf_lb->fmm` | A | 30.263 | 32.386 | 34.640 |
| `mcf_lb->fmm` | B | 29.993 | 31.943 | 34.643 |
| `dispatch_v4` | A | 41.743 | 42.379 | 43.112 |
| `dispatch_v4` | B | 41.895 | 42.440 | 43.222 |
| `dv4->mcf_lb->fmm` | A | 28.332 | 30.669 | 33.575 |
| `dv4->mcf_lb->fmm` | B | 28.584 | 30.673 | 32.946 |

**3 prefix × 2 run = 6/6 순위 동일** (`completion` < `midpoint` < `first_stage`),
9개 paired 대비 **전부 부호 일치 (9/9)**, 양쪽 CI가 서로 겹친다.

두 run을 합친(2880 페어) 추정치 — 음수면 앞쪽이 낫다:

| prefix | `completion` − `midpoint` | `completion` − `first_stage` | `midpoint` − `first_stage` |
|---|---|---|---|
| `mcf_lb->fmm` | −2.04 ± 0.59 (6.8 σ) | −4.51 ± 0.74 (11.9 σ) | −2.48 ± 0.55 (8.9 σ) |
| `dispatch_v4` | −0.59 ± 0.44 (2.7 σ) | −1.35 ± 0.50 (5.3 σ) | −0.76 ± 0.53 (2.8 σ) |
| `dv4->mcf_lb->fmm` | −2.21 ± 0.63 (6.9 σ) | −4.80 ± 0.79 (12.0 σ) | −2.59 ± 0.70 (7.2 σ) |

**개별 대비의 점추정치는 ~1 pp 흔들린다** — 예로 `dv4->mcf_lb->fmm`의
`completion` − `first_stage`가 A에서 −5.24, B에서 −4.36이다 (CI ≈ 1.1). 순위는
흔들리지 않는다. 선행 문서의 결과 2 표를 인용할 때는 **소수점 첫째 자리까지를
신뢰 구간으로** 읽어야 한다.

## 결과 4 — flow 기준 상위 2개는 재현되지 않는다

선행 문서 **결과 4의 표에서 1·2위가 뒤집힌다**:

| 시나리오 | run A | run B |
|---|---|---|
| `dv4_mcf_fmm_neh_cp_completion_seq` | **13.751** | 13.844 |
| `dv4_mcf_fmm_neh_cp_midpoint_seq` | 14.036 | **13.791** |

paired로 재보면 **애초에 두 run 모두에서 유의하지 않았다**:

| | 평균차 (pp) | σ |
|---|---|---|
| run A `completion` − `midpoint` | −0.285 ± 0.728 | −0.77 |
| run B `completion` − `midpoint` | +0.053 ± 0.627 | +0.16 |

flow 기준 top-2 격차(0.29 pp)가 flow 기준 run-to-run SE(≈0.29 pp)와 같은 크기라
부호가 사실상 동전 던지기다. 반면 `completion` − `first_stage`는 A에서 −1.40
(−3.46 σ), B에서 −0.83 (−1.88 σ)로 **부호는 유지되지만 유의성이 오간다.**

**이것은 선행 문서 결과 0을 반박하는 게 아니라 실증한다.** 같은 `completion` −
`midpoint` 비교를 NEH 스텝 자체 산출물로 재면 −2.21 ± 0.63 (6.9 σ)로 양쪽 run에서
안정적이다. flow `bestObj`는 NEH가 seed를 못 이긴 인스턴스에서 세 모드가 같은 값을
보고하므로(flow 기준 tie 456–501개 vs 스텝 기준 100–129개) 신호 대 잡음이 나쁘다.

참고로 flow 기준 노이즈는 sd 11.23 pp / 평균 |delta| 4.02 pp이고, 14400개 중
4033개가 exact tie다 — tie 비율(28 %)이 스텝 기준(8.3 %)의 3배 이상인 것이 같은
현상의 지문이다.

---

## 선행 문서에 대한 판정

| 선행 문서의 결과 | 판정 |
|---|---|
| 결과 0 — flow `bestObj`는 NEH 산출물이 아니다 | **강화됨** (결과 4가 독립 증거) |
| 결과 1 — 노력 균질성 | **재현** (elapsed 차이 ≤0.03 s) |
| 결과 2 — 모드 순위 `completion` < `midpoint` < `first_stage` | **재현** (6/6, 9/9 부호 일치) |
| 결과 3 — 유도 순서 대 `job_priority` | **재현** (baseline 대비 −6.28 ~ −10.93 pp, 9.3–16.8 σ) |
| 결과 4 — flow 기준 순위표 | **부분 실패** — 상위 2개 순위 뒤집힘 |
| 결과 5·6 — 크기 의존성 / oracle 상보성 | 미검증 (본 분석 범위 밖) |

## 남은 한계

- **replicate는 2개뿐이다.** run-to-run SE 0.230 pp는 자유도 1의 추정치이므로 그
  자체가 넓은 오차를 갖는다. 방향성 판단에는 충분하지만 정밀한 노이즈 모델은 아니다.
- **`n=2`로는 run-level 편향을 배제할 수 없다.** 두 run 모두 calop4에서 96코어 단독
  점유로 돌았으므로, 여기서 잰 것은 solver 비결정성이지 머신 간 이식성이 아니다.
- 결과 5(크기 의존성)와 결과 6(oracle 포트폴리오)은 재현 검사를 하지 않았다.
  결과 6의 oracle 이득(3.1 pp)은 모드 간 평균차보다 크므로 노이즈에 강할 것으로
  보이지만, 확인된 바 없다.

## 조치 제안

1. 선행 문서 결과 4에 **"`completion`과 `midpoint`는 flow 기준으로 구분 불가"**
   주석을 단다. 결과 2(스텝 기준)의 순위 주장은 그대로 유효하다.
2. 앞으로 이 계열의 결론은 **NEH 스텝 자체 산출물 기준으로만** 인용한다.
   flow 기준 표는 실무적 참고치로 남기되 0.5 pp 미만 차이는 읽지 않는다.
3. `main.py`의 하드코딩 `CONFIG_PATH`가 이 사고의 원인이다. run setting 커밋이
   `main.py`를 함께 담는 관례(`32ff649`)를 지키면 커밋 시점에 불일치가 드러난다 —
   `091e7fe`는 그것을 빠뜨려 어긋났다.
