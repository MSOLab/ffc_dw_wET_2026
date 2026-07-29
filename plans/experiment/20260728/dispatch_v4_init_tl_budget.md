# 초기화 예산 축소 실험 (`dv4_c5init_f{10,20,40}`) — 실행 + 사후 분석 계획

**작성일**: 2026-07-28 · **종류**: 실험 실행 계획(사전 작성) · **상태**: 실행 전
**config**: `metadata/20260728/dispatch_v4_init_tl.yaml` (main.py 가 가리키는 중)
**선행 문서**: `plans/experiment/20260728/dispatch_v4_reconstruct_mode.md`
(첫 step 이 쓰는 `initialize_by_dispatch_v4(reconstruct_mode=...)` 구현 계획)
**비교 대상 run**: `output/20260722_csr_b30_vs_a_v1_v2/20260722T090801_437425/`
(4 시나리오 × 1440 instance)

---

## 0. 한 문장

> 총 예산 `0.09nc` 중 초기화(MCF-LB → FMM → NEH-CP)가 쓰는 `0.036nc`(=40 %)를
> **10 % / 20 % / 40 %로 줄이고**, 앞에 공짜에 가까운 v4 dispatch 초기해를
> 놓았을 때 최종 RPDf 가 얼마나 손해/이득인지를 본다.

## 1. 가설

데이터 분석상 초기화에 그렇게 많은 시간을 쓸 필요가 없다 → **초기화에서 아낀
시간이 개선 tail(`incremental_sw_cp` → `solve_base_model_cpsat`)로 넘어가면
최종 RPDf 가 나빠지지 않거나 오히려 좋아진다.**

## 2. 시나리오와 예산 회계

세 시나리오는 **초기화 TL 만 다르고** 나머지(전치 dispatch, 개선 tail)는 완전히
동일하다. 총 예산은 모두 `0.09nc`.

| scenario | flip `cp_tl` | neh_cp `total_timelimit` | 초기화 합계 | 총 예산 대비 | tail 로 넘어가는 여유 |
|---|---|---|---|---|---|
| `dv4_c5init_f10` | 0.0009nc | 0.0027nc | **0.0036nc** | 4 % | 32 %p |
| `dv4_c5init_f20` | 0.0018nc | 0.0054nc | **0.0072nc** | 8 % | 28 %p |
| `dv4_c5init_f40` | 0.0036nc | 0.0108nc | **0.0144nc** | 16 % | 20 %p |
| (기준) 100 % | 0.009nc | 0.027nc | 0.036nc | 40 % | — |

이름 규칙: `dv4` = v4 paired dispatch 전치, `c5init` = 3-step 초기화
(`calc_mcf_lb_and_derive_full_sch` → `run_flip_makespan_cp_from_incumbent` →
`neh_cp`), `f__` = 그 초기화의 예산 비율.

전치 step `initialize_by_dispatch_v4(reconstruct_mode="active_but_last_semi")`
는 TL 을 받지 않는다 — `2·|P*|` 개 dispatch 후보 중 min-wET 하나에 재구성 1회를
적용하는 결정론적 연산이라 예산 회계상 무시할 수준이다(실측 시간은 run 의
`_obj_log.json` 첫 점으로 확인).

## 3. 실행

```bash
uv run python main.py --config metadata/20260728/dispatch_v4_init_tl.yaml
```

`FULL_RUN`, PRA2017 large 1440 instance × 3 시나리오, `instance_worker_cnt: 12`,
`draw_gantt/draw_progress_plot: false`.

완료 후 CLAUDE.md 규약대로 **run setting 커밋**을 남긴다:
`output/20260728_dispatch_v4_init_tl/<timestamp> run setting` (본문에 머신 명시).

---

# 사후 분석 계획

## 4. 무엇과 비교하는가

`output/20260722_csr_b30_vs_a_v1_v2/20260722T090801_437425/` 의 4 시나리오는 모두
1440 instance, 총 예산 `0.09nc` 로 동일 격자다.

| 기존 시나리오 | 초기화 | 개선 tail | 이번 실험에서의 역할 |
|---|---|---|---|
| `a_v2_kappa005_max8` | **C5 full (0.036nc)** | ISW-CP κ=0.005, max8 | ★**직접 기준선** — 이번 3개와 tail 설정이 완전히 동일하고 초기화만 100 % |
| `a_v1_const018_max6` | C5 full | ISW-CP 상수 0.018nc, max6 | tail 정책이 다른 참고선 |
| `b30_csr_k1_f30_batch_m` | CSR κ=1 f30 % 래퍼 | ISW-CP 상수 0.018nc | "초기화를 CSR 로 감싼" 대안 |
| `b30_csr_k1_f30_batch_m_plus_2` | 〃 | 〃, batch=m+2 | 〃 |

`a_v2_kappa005_max8` 의 flow 를 이번 config 와 대조하면 **차이가 정확히 두
개**다: ① v4 dispatch 전치 유무 ② 초기화 TL 배율. §7 의 교란 요인은 이 사실에서
나온다.

## 5. Merge 절차

`RunMode.POST_PROCESS_ONLY` 는 run 하나만 재처리할 수 있으므로, 여러 run 의
scenario 를 symlink 로 모은 **합성 run dir** 을 먼저 만든다
(`scripts/build_merged_run_dir.py`).

```bash
# <TS> = 3-시나리오 run 의 timestamp (output/20260728_dispatch_v4_init_tl/ 아래)
REF=output/20260722_csr_b30_vs_a_v1_v2/20260722T090801_437425
NEW=output/20260728_dispatch_v4_init_tl/<TS>

uv run python scripts/build_merged_run_dir.py \
    --dest output/20260728_init_budget_merge \
    $NEW/dv4_c5init_f10 \
    $NEW/dv4_c5init_f20 \
    $NEW/dv4_c5init_f40 \
    $REF/a_v2_kappa005_max8 \
    $REF/a_v1_const018_max6 \
    $REF/b30_csr_k1_f30_batch_m \
    $REF/b30_csr_k1_f30_batch_m_plus_2
```

- 라벨은 이미 전부 고유하므로 `=LABEL` 재명명 불필요.
- 양쪽 다 1440 instance 전수이므로 `--intersect-instances` 는 **불필요**하지만,
  스크립트가 instance 집합 불일치를 보고하면 그때만 붙인다(붙이면 공통 격자만
  남는다).
- 스크립트가 만든 run dir 경로를 출력한다 → 다음 단계 `analysis_dir_path`.

## 6. POST_PROCESS_ONLY config 와 실행

`metadata/20260728/init_budget_merge_pp.yaml` 를 새로 쓴다. 형식은
`metadata/20260722/csr_b30_vs_a_v1_v2_pp.yaml` 을 그대로 따른다.

```yaml
run_mode: POST_PROCESS_ONLY
analysis_dir_path: output/20260728_init_budget_merge/<merged_run_id>
benchmark_dir: benchmarks/PRA2017/large
ins_index_source: benchmarks/PRA2017/pra2017_hybrid_match.csv
bks_table_csv_path: benchmarks/PRA2017/pra2017_bks_table.csv
output_dir: output/20260728_init_budget_merge
instance_worker_cnt: 12
draw_gantt: false            # 필수 — true 면 symlink 를 타고 원본 run 에 쓴다
draw_progress_plot: false    # 필수 — 동상
painter_thread_cnt: 8
scenarios:                   # 7개, 라벨은 merged dir 의 subdir 이름과 정확히 일치
  - name: dv4_c5init_f10
    timelimit: "0.09nc"
    output_subdir: dv4_c5init_f10
    subroutine_flow: <metadata/20260728/dispatch_v4_init_tl.yaml 에서 복사>
  ...
```

각 scenario 의 `subroutine_flow` 는 **원본 run 의 것을 그대로** 옮긴다 —
`<merged>/<label>/subroutine_flow.yaml` 이 정본이므로 거기서 복사하는 것이
가장 안전하다. 단, 20260722 쪽 flow 에 남아 있는 `idle_mode:` 키는
`main._reject_deprecated_step_kwargs` 가 거부하므로 **삭제해야 한다**
(`b971761` 에서 제거된 인자, 값은 항상 lookahead 였으므로 의미 변화 없음).

```bash
uv run python main.py --config metadata/20260728/init_budget_merge_pp.yaml
```

## 7. 주 산출물: method-mean scatter 를 어떻게 읽는가

**`<run_id>_multi_scenario_method_mean_rpdf_and_mean_norm_time_scatter.html`**

- x = **mean normalized time** (각 instance 의 누적 시간 / 그 instance 의 TL,
  전 instance 평균). 두 run 모두 TL 이 `0.09nc` 라 **정규화 분모가 같다** → x 축
  직접 비교 가능.
- y = **mean RPDf** (BKS 기준). 낮을수록 좋다.
- 점 하나 = (시나리오, controller step) 하나. 선은 flow 순서를 잇는다.
  `incremental_sw_cp` 는 batch 마다 점을 찍으므로 궤적이 촘촘하다.
- 각 점은 **best-so-far incumbent** 이고 평균은 **carry-forward** — 즉 궤적은
  절대 위로 꺾이지 않으며 끝점은 전수 평균이다(도달한 instance 만의 평균이
  아니다).
- marker 모양이 top-level step 과 내부(batch/CSR inner) 점을 구분한다.

### 판정 기준

1. **끝점 비교(주 판정)**: `dv4_c5init_f{10,20,40}` 의 마지막 점(=
   `solve_base_model_cpsat` 이후 전수 평균 RPDf)을 `a_v2_kappa005_max8` 의
   끝점과 비교. 가설이 맞다면 f10/f20 중 하나 이상이 **같거나 낮다**.
   판정 시 §8 의 노이즈 폭과 코드 드리프트를 반드시 함께 읽는다.
2. **곡선의 무릎**: f10 → f20 → f40 → (a_v2 = f100) 네 점을 초기화 예산 축으로
   보면 예산-품질 곡선이 된다. 어디서 평평해지는지가 "초기화에 얼마나 쓸
   가치가 있는가"의 답이다.
3. **초기화 종료 시점의 손해**: 각 시나리오에서 `neh_cp` 점의 (x, y).
   x 가 작을수록 tail 에 넘긴 시간이 많고, y 차이가 "초기화를 줄여 잃은 품질".
   이 손해가 tail 진행 중 어디서 회수되는지(궤적이 교차하는 x 위치)를 본다.
   **교차점이 x=1.0(예산 소진) 이전이면 축소가 이득**이다.
4. **전치 dispatch 의 값어치**: `initialize_by_dispatch_v4` 점(각 시나리오의
   첫 점)의 y — 이것이 `calc_mcf_lb_and_derive_full_sch` 점보다 낮으면 dispatch
   초기해가 MCF-LB 유도 스케줄보다 좋았다는 뜻이고, 그 반대면 전치 step 은
   LB 확보 외에는 기여가 없다.

### 보조 산출물 (같은 실행이 함께 생성)

- `*_rpdf_comparison.csv` / `*_rpdf_dashboard.html` — 시나리오별 RPDf 집계.
  §7-1 의 끝점 수치를 표로 확인하고, (T, R) 셀별 분해에 쓴다
  (셀 정의는 `pra2017-instance-params` skill 참조).
- `*_win_tie_dashboard.html` — 쌍별 승/무/패. 평균이 노이즈에 묻힐 때
  f10 vs a_v2 의 instance 단위 승패로 방향을 확인한다.
- `*_multi_scenario_subroutine_flow_comparison.html` — step 별 시간 배분 확인.
  "아낀 시간이 정말 tail 로 갔는지"를 검산한다.
- `*_report.xlsx` (`analysis_wide` / `analysis_long`) — scatter 끝점과 같은
  전수 평균이므로 수치 인용은 여기서 한다.

## 8. 교란 요인 — 결론을 쓰기 전에 반드시 처리할 것

1. **코드 드리프트 (가장 큼).** 비교 대상 run 은 2026-07-22 자이고, 그 뒤
   결과에 영향을 주는 커밋이 최소 둘 있다:
   - `a72c3c4` *feat(cpsat): keep incumbent on budget exhaustion* — 마지막
     `solve_base_model_cpsat` 의 끝점 자체를 바꾼다.
   - `5a68a8a` *fix(ffc-schedule): restore Pan flooring at tau==1* — τ=1 경로.
   따라서 **신규 3개 vs 20260722 4개의 차이 = 초기화 예산 효과 + 코드 차이**다.
   같은 run 안에서 재현된 기준선 없이는 분리할 수 없다 → §9.
2. **전치 dispatch 의 혼입.** 신규 시나리오는 `a_v2` 대비 ① 전치 dispatch
   ② 초기화 TL 두 가지가 동시에 다르다. 순수한 "예산" 비교가 아니다.
   §7-4 로 전치의 기여를 따로 읽되, 완전 분리는 §9 가 필요하다.
3. **CP-SAT 노이즈.** 8-worker wall-clock CP-SAT 는 비결정적이다. 1440 격자에서
   평균 obj 기준 대략 ±350 수준 이하는 회귀가 아니라 노이즈다
   (memory: csr-batch-cp-noise-floor). RPDf 로 환산해 임계치를 정한 뒤 판정한다.
4. **머신.** 두 run 이 같은 머신인지 run setting 커밋 본문으로 확인한다.
   다르면 x 축(시간 비율)이 흔들린다.
5. **`ins_index` 로는 격자 불일치를 못 고친다.** POST_PROCESS 의 `ins_index` 는
   summary CSV 계열만 거르고 chart writer 는 symlink 된 전부를 평균한다.
   격자를 맞추려면 merge 단계에서 `--intersect-instances` 를 써야 한다.

## 9. 권장: 같은 run 안에 기준선 시나리오 추가 (미결정)

§8-1 과 §8-2 를 근본적으로 없애려면 이번 run 에 다음을 함께 돌리는 것이 좋다.

| 추가 후보 | flow | 없애는 교란 |
|---|---|---|
| `c5init_f100` | `a_v2_kappa005_max8` 와 **완전 동일** (전치 dispatch 없음, 초기화 100 %) | 코드 드리프트(§8-1). 20260722 의 `a_v2` 와의 격차가 곧 코드+머신 드리프트 값이 된다 |
| `dv4_c5init_f100` | 전치 dispatch + 초기화 100 % | 전치 dispatch 혼입(§8-2). f10/f20/f40 과 초기화 TL 만 다른 진짜 대조군 |

비용은 시나리오당 1440 instance × `0.09nc`. 둘 다 넣으면 run 이 3개 → 5개로
늘어난다. **최소한 `dv4_c5init_f100` 하나는 권장** — 이것이 없으면 f-곡선의
100 % 끝점을 다른 run 에서 빌려와야 하고, 그 순간 §8-1 이 결론에 붙는다.
(결정 사항 — 사용자 확인 필요.)

## 10. 결과 문서화

분석 실행 후:

- 최종 문서: `plans/analysis/<YYYYMMDD>/init_budget_curve.md` — 질문, 소스 run
  디렉터리 **전체 경로**, merge/재현 명령, 결과 표, 결론. 대용량 산출물
  (CSV/PNG/HTML)은 gitignore 된 `analysis/<id>/` 에 두되 문서는 그것 없이도
  읽히게 쓴다.
- **merged analysis 커밋**: `analysis/<id> merged analysis` (본문에 소스 run
  디렉터리 목록 · 재현 명령 · 한 줄 결론).
