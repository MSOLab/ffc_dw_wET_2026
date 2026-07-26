# W2 — config 1: τ=1 CSR initializer 예산곡선을 f=40 %까지 (사전 작성)

**작성일**: 2026-07-26 · **종류**: 실험 실행 계획(사전 작성, 미실행)
**상위**: `plans/experiment/20260726/csr_init_roadmap.md` (W2, **P1 게이트 판정**)
**선행**: W1 (`csr_report_inner_step_points.md`) — 필수는 아니나 이 런의 차트를 쓸모 있게 만든다
**선행 근거**: `plans/analysis/20260719/csr_init_k_budget_consolidation.md`,
`plans/analysis/20260721/csr_init_isw_batch_result.md`

---

## 1. 질문

> **τ=1 CSR initializer가, 총 예산의 40 % 이하를 쓰면서, 기존 initializer
> `best(MCF-LB → FMM, NEH-CP)`를 9개 (T, R) 셀 전부에서 이기는가?**

이것이 로드맵 §2의 P1 게이트다. **최종해가 아니라 초기해 품질**을 묻는다
(두 arm 모두 tail 없음).

### 1.1 왜 40 %인가

C5 initializer의 예산이 정확히 `0.09nc × 40 %`다:

| C5 스텝 | 예산 | 비고 |
|---|---|---|
| `calc_mcf_lb_and_derive_full_sch` | (시간 인자 없음) | 중단 불가, 실측 고정비용 |
| `run_flip_makespan_cp_from_incumbent` | `0.009nc` | = 10 % |
| `neh_cp` | `0.027nc` | = 30 % |
| **합** | **`0.036nc`** | **= 40 %** |

(출처: `metadata/20260721/csr_init_isw_batch.yaml` arm `a_c5_batch_m`)

따라서 f ≤ 40 %는 **동일 예산 상한에서의 initializer 대결**이다. MCF-LB가 중단
불가라 양쪽 다 예산을 다소 초과하지만(`mcf_lb_atomic_rerun_verdict.md` §6.4:
1.1~1.67×), **양쪽에 동일하게 작용하므로 무시한다** — 다만 `elapsedTime`을 반드시
병기해 실제 초과폭을 기록한다.

### 1.2 왜 재측정인가 (기존 20260714 런을 못 쓰는 이유)

기존 곡선은 f ∈ {5,10,15,20,25,30} 6점뿐이다
(`metadata/20260714/csr_tl_scaling_sweep.yaml` + `20260715/..._tl25_gapfill.yaml`).
f=35 %, 40 %가 없어 **게이트를 판정할 수 없다.**

그리고 f=35/40만 새로 재서 옛 곡선에 이어 붙일 수 없다 — 그 사이에 결과를 바꾸는
코드 변경이 최소 셋 있다:

| 커밋 | 변경 | 영향 |
|---|---|---|
| `b971761` | `idle_mode` 제거, 항상 lookahead | 옛 config는 `idle_mode: "lookahead"`를 명시했으므로 **동작은 동일**하나 `d442ac0`이 그 키를 **거부**한다 → 옛 config 그대로 재실행 불가 |
| `5a68a8a` | `fix(ffc-schedule)`: τ==1에서 Pan flooring 복원 | **τ=1 경로를 직접 건드림** |
| `adb8e60` | mcf_lb 라운드 1 원자화 | 짧은 예산에서 해가 나오는지가 달라짐 |

→ **f ∈ {5,10,15,20,25,30,35,40} 8점을 한 런에서 새로 측정한다.** 단일 코드·단일
머신 곡선이 되어 게이트 판정과 곡선 형태 판독이 동시에 가능해진다.

---

## 2. 실행 설정

**grid**: **full 1440** (PRA2017 large 전체). 게이트가 9개 (T,R) 셀 전부를 요구하므로
슬라이스로는 판정 불가.

**시나리오 9개**:

```
csr_init_tau1_f{05,10,15,20,25,30,35,40}    8   CSR 단일 스텝, factor=1
c5_init_only                                1   mcf_lb → flip → neh_cp (tail 없음), 0.036nc
```

- **`csr_neh_d2wp` 계열(내부 flow 축소판)은 돌리지 않는다** — 게이트는 full inner
  flow만 묻는다. 필요해지면 그때 추가.
- **τ>1은 돌리지 않는다** (로드맵 §1-3).

**inner TL 비례식** (20260714의 `s = f/0.25` 스케일링과 동일한 계수):

| 항목 | 식 | f=35 % | f=40 % |
|---|---|---|---|
| CSR `timelimit` | `0.0009·f·nc` | `0.0315nc` | `0.036nc` |
| flip `cp_tl` | `0.00009·f·nc` | `0.00315nc` | `0.0036nc` |
| neh `total_timelimit` | `0.00027·f·nc` | `0.00945nc` | `0.0108nc` |
| isw `non_time_fixed_op_time_limit_multiplier` | `0.00005·f` | `0.00175` | `0.002` |

(검산: f=5 → `0.0045nc / 0.00045nc / 0.00135nc / 0.00025` = 20260714 값과 일치 ✓)

**스키마 주의**: 신규 config에서 `idle_mode` 키를 **넣지 말 것**(`d442ac0`이 거부).
나머지 solver 설정(thread 8, `batch_size: "m"`, `pf_method: "PF1"`,
`skip_pf_below_obj: "makespan"` 등)은 20260714 config에서 **그대로 복제**한다.

**outer**: `timelimit: "0.09nc"` (모든 시나리오 공통, CSR/C5 예산이 먼저 binding).

**설계 고정값**: `factor: 1` (mode 무관 — 항등), `seed_dispatch: v4`,
`reconstruct_mode`는 τ=1에서 무의미하나 명시적으로 `active_but_last_semi` 고정.

```bash
# config 생성기 (멱등, 스키마 검증 포함)
uv run python scripts/20260726/build_csr_init_tl_config.py
    # -> metadata/20260726/csr_init_tl_curve.yaml

uv run python main.py --config metadata/20260726/csr_init_tl_curve.yaml
    # -> output/20260726_csr_init_tl_curve/<timestamp>/
```

**예상 소요**: 20260721 런 실측(4 시나리오 × 1440 @ 0.09nc = 10:15:15 → **≈2.6 h /
full-budget 시나리오**, calop4)에서 외삽. 비용은 f에 대체로 비례:
Σf = 180 % → ≈4.6 h, + `c5_init_only`(40 %) ≈1.0 h → **총 5–6 h**.
`instance_worker_cnt=12 × solver_thread_cnt=8 = 96` (물리 코어 수).

---

## 3. 판정 방법

**주 산출물**: `<run>_rpdf_comparison.csv` (`orchestration/post_run_pivot.py`가
`insIndex → n, c, totalMcCount, T, R, W, BKS_data, bestObj, RPDf_BKS_data,
elapsedTime` 열로 자동 생성) — **(T,R) 9셀 분해에 바로 쓸 수 있는 형태**이므로
재조인 불필요(CLAUDE.md 참조).

### 게이트 판정 (1급)

각 f에 대해, 9개 (T,R) 셀의 mean RPDf를 `c5_init_only`와 비교:

```
PASS(f)  ⇔  ∀ (T,R) ∈ 9셀 :  meanRPDf(csr_init_tau1_f) ≤ meanRPDf(c5_init_only)
게이트 통과 ⇔ ∃ f ≤ 40 % : PASS(f)
```

- **통과 시**: 통과하는 **최소 f**를 함께 보고한다(예산 효율). 로드맵 §5에 따라
  P1 보류를 해제할지 판단하고, W3로 진행.
- **미통과 시**: 어느 셀에서 지는지(대개 T=0.2 완화 셀 또는 T=0.6 최난 셀 중 하나)를
  명시한다. 실패 셀의 패턴이 다음 질문을 정한다.

### 병기 필수 (2급)

1. **예산 준수 실측**: 두 arm의 mean `elapsedTime` / `0.09nc`. §1.1의 "초과는
   무시한다"가 실제로 얼마나 큰 초과였는지 숫자로 남긴다.
2. **f→RPDf 곡선**: pooled + T별. 20260714 곡선과 **겹쳐 그리지 말 것**(§1.2 코드
   드리프트) — 형태 비교는 말로만 한다.
3. **내부 단계 궤적**: W1이 끝났다면 per-scenario
   `summary_method_mean_rpdf_and_mean_norm_time_scatter.html`에 CSR 내부 5단계가
   십자가로 찍힌다. **어느 내부 단계가 예산을 먹고 어디서 개선이 멈추는지**가
   f 선택의 근거가 된다 (W3에서 쓸 f를 여기서 고른다).
4. **승패 카운트**: per-instance paired win/tie/loss (`c5_init_only` 기준).
   mean만으로는 tail-driven 왜곡을 못 걸러낸다.

### 노이즈 게이트

CSR·C5 모두 CP를 포함하므로 재실행 분산이 있다. 1440 격자에서 mean obj 델타가
**±350 이내면 노이즈**로 읽는다(CLAUDE.md의 CSR batch CP noise floor).
셀별(160 인스턴스) 판정에는 이 바닥이 더 크므로, **셀 단위 근소한 승패는
"구분되지 않음"으로 처리**하고 게이트 판정에는 부호만 쓴다.

---

## 4. 산출물

- 사후 분석 문서: `plans/analysis/20260726/csr_init_tl_curve.md` (tracked SSOT)
- 분석 스크립트: `scripts/20260726/analyze_csr_init_tl_curve.py`
  (게이트 판정 + 9셀 표 + f 곡선 + 승패 + elapsed 병기, 게이트 실패 시 exit 1)
- config 생성기: `scripts/20260726/build_csr_init_tl_config.py`
- 커밋: run setting (`<run_dir>/<timestamp> run setting`) + merged analysis
  (`analysis/<id> merged analysis`) — CLAUDE.md provenance 규약

## 5. 미결 / 판독 시 주의

- **`c5_init_only`를 같은 런에 넣는 이유**: 기존 C5 수치는 tail이 붙은 arm의
  중간 지점(obj_log의 neh_cp endpoint)에서 읽은 값이라 측정 경로가 다르다. 같은 런
  안에서 **독립 시나리오로** 재보하면 머신·코드·부하가 동일해진다.
- **τ=1에서 `reconstruct_mode`는 무의미**하지만 config에 남긴다 — 나중에 τ>1로
  확장할 때 diff가 한 줄이 되도록.
- 이 게이트는 **초기해 품질**만 본다. 통과해도 "최종해가 좋아진다"는 함의는 없다
  (`csr_init_isw_batch_result.md`: 초기해 13.15 pp 우위가 파이프라인 후 ≤1.16 pp로
  수축). 그 축은 W3가 잰다.
