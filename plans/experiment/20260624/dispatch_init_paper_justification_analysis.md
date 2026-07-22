# Dispatch-sequence sweep 분석: 논문 initialization 근거 마련

> ✅ **실행 완료 (2026-06-24)** — 결과:
> `analysis/20260624_dispatch_init_justification_1.md`
> - analyzer 보강(`--baseline`/`--chosen`, n별 gain) + 단위 테스트
>   (`tests/scripts/test_analyze_dispatch_sweep.py`) 완료.
> - **채택안(k=5)**: `rd_edd + rd_wxd2 + sd_due2_weight_pos + sd_w1 + sd_wxd2`.
> - **2017 baseline 대비 절대 obj −38,848 (−19.2%)** (n별 −16.8~−19.7% 일관).

- **분석 대상 run (확정)**: `output/20260624/20260624T165544_348097`
  (simple+IIT 적용 후 재실행. 22 method × 1440 instance, 결측 0 검증 완료.)
- 폐기 run(참고): `output/20260624/20260624T153836_407384` (simple에 IIT 없음).
- 분석 스크립트: `scripts/analyze_dispatch_sweep.py`
- 작성일: 2026-06-24 (분석 수행: 2026-06-24)

## 1. 목적

논문에서 **initialization 방식을 이렇게 선택한 근거**를 데이터로 제시한다.
구체적으로 두 축의 설계 결정을 정당화한다.

- **축 (1) Decode 방향**: 정해진 job priority를 스케줄로 푸는 방식 — `simple
  dispatch`(forward) vs `reverse dispatch`(reverse). **둘 다 동일한 timing
  보정(`make_semi_active` + `insert_idle_time`)을 적용**해 방향 효과만 분리한다
  (§0 참조).
- **축 (2) Job priority 생성**: Pan et al. (2017)이 쓴 `{edd, lsl, osl}` 대비,
  본 연구가 추가로 시험한 확장 priority 집합(총 11개).

최종적으로 **"best(simple dispatch ∘ {edd,lsl,osl})" = 2017 baseline 대비
본 연구가 채택한 방식의 이득**을 절대 objective value 중심으로 정량화한다.

## 0. 선행 작업 (별도 plan, 이 분석 전에 완료되어야 함)

> **prerequisite**: `plans/experiment/20260624/simple_dispatch_add_iit_and_rerun.md`
>
> `initialize_by_simple_dispatch`를 IIT 포함(make_semi_active +
> insert_idle_time)으로 교체하고 dispatch sweep을 **재실행**한다. 그 결과로
> 생기는 **새 run**(양쪽 모두 IIT 적용, 22 scenario × 1440 instance, 결측 0)을
> 이 분석의 입력으로 사용한다. 기존 run
> `output/20260624/20260624T153836_407384`는 폐기.
>
> 잔여 차이(비-IIT): reverse는 np 후보를 makespan 기준 best로 고르지만 simple은
> 단일 pass — "simple vs reverse pipeline"의 본질적 차이로, Track A/C에서 명시.

본 plan의 모든 커맨드에서 `RUN=` 은 **새 run 디렉터리**로 지정한다.

## 2. 데이터 현황 (새 run 기준으로 재검증)

- 22 scenario = **11 priority key × 2 decode 방향(`sd_`, `rd_`)**, **양쪽 모두 IIT**.
- 새 run에서 1440 instance × 22 = 31,680 행, **결측 0** 재확인(아래 절차).
  → 완전 sweep이라 oracle(per-instance best) 계산에 구멍 없음.
- instance size `n ∈ {50,100,150,200}`.
- 비교 metric 두 가지(둘 다 minimization):
  - `RPDf_BKS_data` (`--metric rpdf`): scale-free, instance마다 동등 기여 →
    "어느 방식이 더 좋은가"의 공정한 척도.
  - `bestObj` (`--metric obj`): 절대 weighted E+T. 평균이 큰 instance(n=200)에
    지배됨 → **"총 비용" 관점**. 사용자가 원하는 절대값 이득은 이쪽이되,
    size-dominated 편향을 보정하기 위해 **n별 분해**를 함께 본다.

### Priority key 사전 (parameters/sorter.py, ffc_ddw_params.py)

| key | 의미(정렬 기준) | 비고 |
|---|---|---|
| `edd` | due upper bound `d⁺` 오름차순 | **2017 paper** |
| `lsl` | last-stage slack `d⁺ − p_last` 오름차순 | **2017 paper** |
| `osl` | overall slack `d⁺ − Σ p` 오름차순 | **2017 paper** |
| `eddub_twt` | `d⁺` ↑, tie는 tardiness weight ↓ | 확장 |
| `w1` | `−(w⁺ − w⁻)` 오름차순 | 확장 |
| `weight_due_pos` | max weight → total weight → due-window width | 확장 |
| `due_weight_pos` | clamp slack → `d⁺` → `d⁻` → total weight | 확장 |
| `due2_weight_pos` | `max(r_j, d⁺−p_last)` → … | 확장 |
| `due_star_weight_pos` | 합성 due `d*` → `d⁺` → total weight | 확장 |
| `wxd1` | due-midpoint 기준 early/late 분할 후 weight×due 편차 | 확장 |
| `wxd2` | aversion-score 기준 분할 후 weight×due 편차 | 확장 |

## 3. 분석 트랙 → 산출물 매핑

### Track A — Decode 동치성: "FAM dispatcher" vs "simple dispatch" (코드 레벨)

**질문**: 본 연구의 `simple dispatch`가 2017 paper의 FAM(First Available
Machine) decode와 동일한가? 같으면 그냥 인용, 다르면 다른 decoder를 쓴 이유를
명시해야 함.

> 주의: §0에서 simple dispatch에 IIT를 추가하므로, FAM 비교는 IIT 이전의
> **decode 코어**(job→machine 배정) 기준으로 본다. FAM 자체에는 IIT가 없음.

**작업** (read-only, 코드 검증):
1. `MixedDispatcher.get_job_centric_schedule_by_sequence`
   (`orchestration/controller.py:1704` 경유)의 **machine 배정 규칙**이
   FAM(각 stage에서 가장 먼저 비는 기계에 배정)과 일치하는지 확인.
2. `algorithm/fam.py`의 FAM은 **stage마다 job 순서를 완료시각 기준으로 재정렬**
   (adaptive)하는 반면, simple dispatch는 **전 stage에 걸쳐 고정 priority**를
   사용한다는 점을 명확히 정리.
3. 결론 문장 초안:
   - 만약 *machine 배정 규칙은 FAM과 동일*하고 차이는 *job 순서 고정 vs 재정렬*
     뿐이라면 → "본 연구의 simple dispatch는 고정 순열에 대한 FAM decode이며,
     2017의 FAM rule을 따른다(인용)"로 정리.
   - 만약 machine 배정까지 다르면 → 왜 `MixedDispatcher`를 택했는지(예:
     reverse/IIT pipeline과의 일관성, np-head 처리) 근거 서술.

**산출물**: 논문 본문용 1문단 + 코드 근거(file:line). (sweep 데이터 아님 —
필요 시 동일 sequence에 대해 FAM vs simple 1회 디코드 결과 일치 여부를
소규모로 실증 가능하나, 우선 코드 독해로 판정.)

### Track B — Priority-set 기여 (simple dispatch 한정)

**질문**: simple dispatch로 고정했을 때, 2017의 `{edd,lsl,osl}` 대비 확장
11개 priority가 얼마나 더 좋은가?

비교 대상(모두 `sd_` 한정, oracle = per-instance best of the set):
- **B0 (2017 baseline)**: oracle{`sd_edd, sd_lsl, sd_osl`}
- **B1 (best single, 11개 중)**: `mean_by_method`의 1위 (`--methods sd_`)
- **B2 (best 2-combo, 11개 중)** 및 **best 3-combo**: `best_combos`
  (`--methods sd_`)
- **B3 (전체 11개 oracle)**: 11개 모두 돌렸을 때 상한

**산출물**: B0 vs B1/B2/B3의 metric 표(`rpdf`, `obj` 둘 다). 핵심 수치:
"2017 priority 3개만 vs 확장 priority의 best-k"의 차이.

### Track C — Decode-direction 기여 (sd_ only vs sd_+rd_)

**질문**: simple과 reverse를 **둘 다** 돌려 best를 취하면, simple만 쓸 때보다
얼마나 이득인가? (§0로 양쪽 모두 IIT가 적용되므로, 이 차이는 **순수 decode
방향 효과** — IIT 유무 confound 제거됨.)

비교 대상(oracle):
- **C0**: best k-combo within `sd_` (11개)
- **C1**: best k-combo within all 22 (`sd_`+`rd_`)
- 보조: `rd_` 단독 best도 함께 표기(역방향만으로도 충분한지 확인).
- `marginal_contribution`으로 "best sd_ combo에 rd_ 한 개를 더하면" 어떤
  rd_ method가 가장 크게 기여하는지 확인 → 왜 양방향을 쓰는지 근거.

**산출물**: direction 추가의 한계 이득(diminishing 여부) 표 + 어떤 방향/priority가
상호보완적인지.

### Track D — 종합 이득: 2017 baseline 대비 채택안 (절대 obj 중심)

**질문(최종)**: `best(simple dispatch ∘ {edd,lsl,osl})` 대비 **채택한 방식**의
이득은 절대 objective로 얼마인가?

- **baseline = B0** = oracle{`sd_edd, sd_lsl, sd_osl`}
- **chosen = 데이터로 결정 (확정됨)**: all-22(`sd_`+`rd_`) 중 best k-combo를
  k=1..6까지 산출하고, **seed 개수 k=4~6 범위에서 이득이 plateau되는 가장 작은
  고정 집합**을 채택안으로 제시(양방향 허용). 이 고정 집합을 `--chosen`으로
  채점해 최종 gain 산출.
- **이득 계산**:
  - 절대: `mean_i bestObj(baseline) − mean_i bestObj(chosen)` (전체 + n별 분해)
  - 상대: 위를 baseline 평균으로 나눈 % + `RPDf` 차이.

**산출물**: 논문 결과표 한 개 — "2017 → 채택안" 절대/상대 이득, n별 분해 포함.

## 4. analyzer 보강 (최소 변경)

현재 `analyze_dispatch_sweep.py`는 `mean_by_method`, `best_combos`,
`oracle_value`, `marginal_contribution`, `--methods` prefix, `--metric`를
제공하나 **명시적 named combo 채점**과 **gain 출력 CLI가 없다**. Track B/D를
위해 다음을 추가한다(KISS, 기존 함수 재사용).

- `--baseline NAME[,NAME...]`: 주어진 정확한 scenario 집합의 `oracle_value`를
  출력(예: `sd_edd,sd_lsl,sd_osl`).
- `--chosen NAME[,NAME...]`: 주어진 집합의 `oracle_value` + **baseline 대비
  gain**(절대 = baseline − chosen, 상대 = gain/baseline) 출력.
  `--baseline`과 함께 쓰일 때만 gain 계산.
- gain 출력 시 **n별 분해**(`obj` metric에서 size-dominated 편향 완화)도 함께
  표기 — `metric_matrix`를 n 그룹별로 호출하거나 df를 n으로 그룹.
- 기존 `oracle_value(mat, combo)` 그대로 사용. 입력 검증: 지정 NAME이 sweep에
  없으면 명확히 raise(기존 `metric_matrix` 패턴 따름).

> 변경은 `analyze_dispatch_sweep.py`의 CLI(`main`)와 `report`/신규 헬퍼에
> 한정. `io/`·`algorithm/` 미접근 → 프로젝트 IO/algorithm 경계 영향 없음.
> 변경 후 `uv run ruff check` / `uv run ruff format`.

## 5. 결정사항 (확정)

- **채택안(chosen) 정의**: 데이터로 best-k를 산출해 추천(고정 집합). 사전에 정한
  method 집합은 없음.
- **seed 개수 k = 4~6** 범위에서 채택. all-22 best-combo를 k=1..6까지 뽑아
  이득-vs-k 곡선을 그리고, **k=4~6 중 이득이 plateau되는 가장 작은 k**의 고정
  집합을 채택안으로 확정 → Track D의 `--chosen`에 투입.

## 6. 실행 커맨드 (보강 후)

```bash
RUN=output/20260624/20260624T165544_348097   # simple+IIT 재실행 run

# Track B: simple dispatch 한정, 단일/2/3-combo + 2017 triple baseline
uv run python scripts/analyze_dispatch_sweep.py $RUN --metric rpdf --methods sd_ \
    --baseline sd_edd,sd_lsl,sd_osl
uv run python scripts/analyze_dispatch_sweep.py $RUN --metric obj  --methods sd_ \
    --baseline sd_edd,sd_lsl,sd_osl

# Track C: 전체 22(sd_+rd_) combo vs sd_ only
uv run python scripts/analyze_dispatch_sweep.py $RUN --metric rpdf --combo-size 1 2 3 4 5 6
uv run python scripts/analyze_dispatch_sweep.py $RUN --metric rpdf --methods sd_ --combo-size 1 2 3 4 5 6

# Track D: best-k(k=1..6) → 이득 plateau 지점(k=4~6)에서 고정 집합 확정 후 chosen 투입
uv run python scripts/analyze_dispatch_sweep.py $RUN --metric obj  --combo-size 1 2 3 4 5 6  # k별 oracle 곡선
uv run python scripts/analyze_dispatch_sweep.py $RUN --metric rpdf --combo-size 1 2 3 4 5 6
uv run python scripts/analyze_dispatch_sweep.py $RUN --metric obj \
    --baseline sd_edd,sd_lsl,sd_osl --chosen <CHOSEN_SET>
uv run python scripts/analyze_dispatch_sweep.py $RUN --metric rpdf \
    --baseline sd_edd,sd_lsl,sd_osl --chosen <CHOSEN_SET>

# (보조) T/R slice별 강건성 확인 — 필요 시
uv run python scripts/analyze_dispatch_sweep.py $RUN --metric rpdf --t 0.6 --r 0.2
```

## 7. 최종 산출물

1. `analysis/` 또는 `docs/reviews/`에 결과 정리 md (트랙별 표 + 논문용 문장).
2. 논문에 넣을 핵심 수치 3개:
   - (B) 확장 priority의 priority-set 이득(simple 한정),
   - (C) decode-direction 추가 이득,
   - (D) **2017 → 채택안 절대 objective 이득**(전체 + n별).
3. Track A 결론 1문단(FAM vs simple 관계, 인용/설명 방향 확정).

## 8. 리스크 / 주의

- **oracle 해석**: oracle{set}은 "그 set을 모두 돌려 best를 취함" 가정. ship
  시 실제로 그 수만큼 init을 돌린다는 전제와 일치해야 함(시간예산 `0.09nc`는
  scenario별 동일하므로 k개를 돌리면 총 init 시간은 ~k배 — 논문에서 비용도 함께
  언급).
- **obj는 size-dominated** → 절대 이득은 반드시 n별 분해와 함께 보고.
- Track A는 sweep 데이터로 직접 답할 수 없음(FAM scenario 미포함) → 코드 독해가
  1차 근거, 필요 시 소규모 실증.
