# `*_obj_log.json` 기반 subroutine flow / RPDf scatter HTML 차트 추가

## 목표

hybridflowshop 저장소의 두 인터랙티브 차트를 이 프로젝트에 이식한다. 입력은
in-memory가 아니라 **디스크에 이미 떨어져 있는 인스턴스별
`<instance>_obj_log.json`** + 형제 `<instance>_instance_result.yaml` +
`benchmarks/PRA2017/`의 BKS / hybrid_match CSV.

산출물 (artifact kind 신규 등록):

1. **scatter (per-scenario)** — `<scenario>/summary_method_rpdf_and_norm_time_scatter.html`
   - 시나리오 1개 안에서, 인스턴스별 best-so-far RPDf 곡선과
     subroutine endpoint 마커, (n,c) 그룹별 평균 곡선 + 드롭다운 필터.
   - 원본: `hybridflowshop/hybridflowshop/report/method_summary_chart.py:export_method_rpdf_scatter_html`.
2. **flow_comparison (run scope, 1개)** — `<run_id>_multi_scenario_subroutine_flow_comparison.html`
   - 시나리오마다 평균 RPDf step-curve를 한 차트에 겹쳐 그리고,
     subroutine별 평균 endpoint 시점에 수직 가이드.
   - 원본: `hybridflowshop/hybridflowshop/report/multi_scenario_method_chart.py:export_multi_scenario_method_rpdf_comparison_html`.

## 비목표

- 새로운 metric 정의를 만들지 않는다. RPDf는 기존
  `post_run_pivot._rpdf` (= `2(obj-ref)/(obj+ref)`) 그대로.
- hybridflowshop을 git submodule / path import로 끌어들이지 않는다 (vendoring).
- `local_progress_list[*].obj_value` 의 단조성 강제는 본 plan 범위 밖. 사용자가
  별도로 처리(아래 §전제 참조). 이 plan은 보정된 obj_log를 가정한다.
- 신규 차트 외에 기존 dashboard(rpdf_dashboard, win_tie_dashboard, …)를 수정하지
  않는다.

## 전제 / 의존하는 invariant

- 별도 작업으로 `_save_obj_log`의 시리즈 기록 모드가 아래처럼 정리된다:
  - **strict (default)**: `obj_value`는 strictly 감소, `obj_bound`는 strictly
    증가하는 점만 기록. 현재 보이는 두 quirk(마지막 step의 종점 obj 점프,
    obj_bound가 일시 0으로 떨어지는 현상)가 사라진다.
  - **as-is (debug)**: 콜백이 본 그대로 기록.
  - 차트 코드는 두 mode 모두에서 동작해야 한다. strict mode 산출물에
    맞게 best-so-far 누적 로직을 유지하되(원본 hybridflowshop도 동일)
    as-is mode에서는 `_compute_best_so_far_y_values`가 envelope을 만들어
    주므로 그대로 호환.
- `<instance>_obj_log.json`은 단일 라인 compact JSON, key는 elapsed sec의
  `repr(float)` (`ffcddw_single_instance_runner._save_obj_log`).
- `<instance>_instance_result.yaml`이 `<instance>_obj_log.json`과 같은 디렉토리에
  존재하며 `timelimit, job_count, stage_count` 필드를 갖는다 (현재 schema 그대로).
- BKS join은 기존 `post_run_pivot.build_rpdf_comparison_df`와 동일한
  `instanceName → insIndex → BKS_data` 경로. 두 CSV 경로는 reporting 파이프라인이
  이미 들고 있다.

## 데이터 흐름 (요약)

```
<instance>_obj_log.json ──┐
<instance>_instance_result.yaml ──┤
                                  ├─► InstanceProgression  (per instance)
benchmarks/PRA2017/*.csv ─────────┘
                                                │
                  ┌─────────────────────────────┴─────────────────────────────┐
                  │                                                           │
       (per-scenario aggregate)                                  (run-level aggregate)
        endpoint_df + raw_progression_df                  list of {label, endpoint_df, raw_progression_df}
                  │                                                           │
                  ▼                                                           ▼
   export_method_rpdf_scatter_html(...)        export_multi_scenario_method_rpdf_comparison_html(...)
   → <scenario>/summary_method_rpdf_and_norm_time_scatter.html
                                                              → <run>/<run_id>_multi_scenario_subroutine_flow_comparison.html
```

`endpoint_df` 컬럼: `instance_id, subroutine_name, norm_time, rpd_f, job_cnt, stage_cnt`.
`raw_progression_df` 컬럼: 위 + `global_sec, call_index`.

## 모듈 구조 (vendor 대상 + 신규)

신규 패키지 `src/ffc_ddw_sum_et/report/`. **import 방향은 단일** — orchestration →
report. 그 반대(report → orchestration)는 금지.

```
src/ffc_ddw_sum_et/report/
├── __init__.py                # 공개 함수 re-export
├── obj_log_loader.py          # 신규: obj_log.json + instance_result.yaml → InstanceProgression
├── method_summary_chart.py    # vendored from hybridflowshop, exp_compare 의존 제거
├── multi_scenario_method_chart.py  # vendored
└── method_progression_report.py    # 일부만 vendor (subroutine_progression.json 경로는 제거)
```

vendoring 시 변경점 (수술적):

- `from exp_compare.metrics import compute_rpdf` → 삭제. 본 프로젝트의
  `_rpdf`(= `post_run_pivot._rpdf` 와 동일 식)을 `report._rpdf` 한 곳에 둔다.
  분모 0 보호도 동일.
- `_iter_instance_progression_json_paths`는 `subroutine_progression.json` 검색
  로직을 본 프로젝트의 `<instance>_obj_log.json` 검색으로 교체.
- `build_progression_points`, `build_instance_endpoint_rows_from_progression`은
  본 프로젝트의 obj_log schema에 맞게 재작성 (아래 §obj_log → progression 참조).
- 시나리오 라벨/디렉토리 워크는 ArtifactLayout으로 위임 (run_dir →
  scenario_dir 리스트).
- 차트 HTML 템플릿(plotly), step-path/best-so-far/평균 곡선 코드는 그대로 복사.
  hybridflowshop 의 `SYMBOL_MAP`은 이 프로젝트의 subroutine 이름 집합으로 교체
  (등록된 step 이름은 controller에서 조회).

## obj_log → progression 변환 사양

`report/obj_log_loader.py`의 핵심 함수:

```python
@dataclass(frozen=True)
class InstanceProgression:
    instance_id: str
    job_cnt: int
    stage_cnt: int
    timelimit_sec: float
    obj_value_calls: list[CallSegment]   # 각 subroutine 호출 1개 = CallSegment 1개
    obj_bound_calls: list[CallSegment]

@dataclass(frozen=True)
class CallSegment:
    call_index: int
    subroutine_name: str            # notes 라벨에서 ^\d+- 제거
    prefixed_subroutine_name: str   # notes 라벨 그대로 (e.g. "1-calc_mcf_lb_and_derive_full_sch")
    global_start_sec: float
    global_end_sec: float
    points: list[ProgPoint]         # (global_sec, value) 목록, end-point 포함
```

변환 규칙:

- 시리즈 (`obj_value` 또는 `obj_bound`)별로 별도 처리.
- `data` 키들을 float으로 변환해 시간순 정렬.
- `notes` 키들을 endpoint 시점으로 본다. endpoint 시점이 K개면 call도 K개.
- call_k 의 `(global_start_sec, global_end_sec]` = (직전 endpoint or 0, 이번 endpoint].
- call_k 의 `points` = `data` 중 시간이 그 구간에 들어가는 점들 (endpoint 포함).
- subroutine 이름은 `notes` 값에서 `re.match(r"^\d+-(.+)$", label).group(1)`.
  failure 시 raise (정형이 깨졌다는 신호 — 침묵 default 금지, 메모리
  `feedback_no_defensive_get.md` 패턴).
- `instance_id, job_cnt, stage_cnt, timelimit_sec` 는 형제 `*_instance_result.yaml`
  에서 읽는다 (`load_yaml`). manifest 가 없으면 raise (skip 아님).

DataFrame 빌더:

- `endpoint_df_for_scenario(scenario_dir) -> pd.DataFrame` — call별 endpoint 1행.
- `raw_progression_df_for_scenario(scenario_dir) -> pd.DataFrame` — call별
  points 모든 행 (`obj_value` 시리즈만 사용; `obj_bound`는 v1에서 안 그림).
- 두 함수는 단순히 `InstanceProgression` 리스트를 만들고 row로 풀어낸다. baseline
  조인(`rpd_f` 계산)은 별도 함수 `attach_rpdf(df, baseline_df)`에서 처리해
  obj_log 로더는 baseline 비의존.

## 자동 emit 결선

`src/ffc_ddw_sum_et/orchestration/post_run_pivot.py`의 `write_post_run_pivot_artifacts`
바로 뒤에 (혹은 `reporting.py:_write_post_run_pivot_artifacts` 자리에서) 새
함수 호출:

```python
write_post_run_subroutine_chart_artifacts(
    layout=layout,
    hybrid_match_csv=hybrid_match_csv,
    bks_table_csv=bks_table_csv,
)
```

함수 책임:

1. `layout.scope("run").dir` 아래의 시나리오 디렉토리를 ArtifactLayout으로 enumerate
   (이미 다른 후처리도 같은 패턴 사용).
2. baseline 조인용 DataFrame 1회 로드 — 컬럼 mapping은 hybridflowshop의
   `baseline_instance_col=Instance, baseline_job_cnt_col=n,
   baseline_stage_cnt_col=c, baseline_obj_val_col=BKS_data` 가 아니라, 이 프로젝트의
   `pra2017_bks_table.csv` 컬럼(`insIndex, n, c, BKS_data`)에 맞게 builder 안에서
   `instanceName ← hybrid_match` 조인을 해서 `instance_id, n, c, ref_obj` 형태로
   준비.
3. 각 시나리오 dir → `endpoint_df`, `raw_progression_df` 생성 → baseline join으로
   `rpd_f` 채움 → `export_method_rpdf_scatter_html` 호출.
4. 모든 시나리오 metric 을 `[{label, endpoint_df, raw_progression_df}, ...]`로 묶어
   `export_multi_scenario_method_rpdf_comparison_html` 호출.
5. 각 단계는 hybrid_match / bks_table 부재 시 silently skip (기존
   `write_post_run_pivot_artifacts`와 동일 정책).

## ArtifactLayout 등록

`metadata/artifact_layout/ffc_ddw_sum_et_v1.yaml`에 두 kind 추가:

```yaml
- scope: scenario
  kind: subroutine_rpdf_scatter_html
  file_template: 'summary_method_rpdf_and_norm_time_scatter.html'
- scope: run
  kind: multi_scenario_subroutine_flow_comparison_html
  file_template: '{run_id}_multi_scenario_subroutine_flow_comparison.html'
```

scope=scenario 인 artifact가 거의 없는 점은 의도적 — 이 차트는 시나리오 전체
인스턴스를 모아야 의미 있는 산출물이라 scenario dir 루트가 자연스러운 자리.

## offline 스크립트

`scripts/build_subroutine_flow_charts.py` 신규.

```bash
uv run python scripts/build_subroutine_flow_charts.py \
    output/20260507_debug/20260507T160736_948467
```

- 인자: run_dir 1개. run_dir/<run_id>_artifact_layout.yaml을 로드해 같은
  ArtifactLayout 객체를 만들고, hybrid_match/bks_table 경로는 환경변수 또는 옵션
  `--bks-csv`, `--hybrid-match-csv`로 주입.
- 내부적으로 `write_post_run_subroutine_chart_artifacts`를 호출 — 자동 emit
  경로와 100% 동일 함수.
- 사용 시나리오: 이미 끝난 run에 차트만 추가, 차트 코드 수정 후 재생성.

## RPDf / norm_time 정의 (재확인)

- `norm_time = global_sec / timelimit_sec`. timelimit은 SIR이 manifest에 적은 값
  (n,c,m으로 resolve된 후의 float).
- `rpd_f = 2*(obj - ref) / (obj + ref)`. obj 는 endpoint의 `obj_value`, ref는
  baseline `BKS_data`. 분모 0이면 0.0.
- `obj_value`가 NaN/None인 endpoint(예: aborted step)는 `endpoint_df`에서 제외.
  hybridflowshop 의 `_load_and_merge_for_html`이 같은 정책.

## 테스트 플랜

1. 단위:
   - `report/obj_log_loader.py`: 알려진 `*_obj_log.json` fixture를 읽어
     `InstanceProgression`이 (call_count, endpoint 시점, points 카운트) 정확히
     나오는지. 두 시리즈(`obj_value`, `obj_bound`) 각각.
   - `attach_rpdf`: ref 누락 시 None, ref==0 분모 보호.
   - `_save_obj_log` strict mode 산출물에 대해서도 동일 결과(엣지: 단일 점만 있는
     call) 검증.
2. 통합:
   - 작은 fixture run dir(시나리오 2개, 인스턴스 2개) 만들어 `write_post_run_subroutine_chart_artifacts`
     실행 → 두 HTML 파일 생성, 파일 내 `payload`/`DATA` JSON 안에 시나리오/인스턴스
     수가 정확히 들어가는지 정규식 spot-check.
3. 회귀:
   - 기존 `output/20260507_debug/.../20260507T160736_948467` 위에서
     offline 스크립트를 돌려 두 HTML이 생성되고, 마지막 step의 obj_value 점프가
     **strict mode**에서는 사라졌는지(차트의 마지막 endpoint 마커가 incumbent와
     일치하는지) 시각 확인.

## 작업 순서

1. (사용자 작업, 별건) `_save_obj_log`의 strict / as-is mode 분기 추가 + 디폴트
   strict로 전환.
2. **이 plan의 진입점**: vendoring 후보 파일 식별·복사·외부의존 제거.
3. `report/obj_log_loader.py` 작성 + 단위 테스트.
4. `report/method_summary_chart.py`, `report/multi_scenario_method_chart.py`의
   진입 함수 시그니처를 본 프로젝트 데이터(`InstanceProgression` 리스트 또는
   pre-built DataFrame)에 맞게 어댑터 한 층 추가.
5. ArtifactLayout YAML에 두 kind 추가, layout 로딩 회귀 테스트 통과.
6. `post_run_pivot.write_post_run_subroutine_chart_artifacts` 추가하고
   `reporting.py`의 후처리 호출에 끼움.
7. `scripts/build_subroutine_flow_charts.py` 작성, 기존 run_dir에 대해 수동 실행.
8. 통합 테스트 추가.
9. AGENTS.md / docs/io 가이드 갱신은 별도 PR로 분리 (이 plan에는 안 포함).

## 기록할 가치 있는 트레이드오프 / 결정

- **vendor vs hybridflowshop 의존**: 이 저장소는 io subtree의 외부 의존을
  최소화하는 방침이 있고, 차트 코드도 동일 톤으로 self-contained로 두는 게 맞음.
  단, 두 저장소의 차트 코드가 점차 분기할 위험은 감수 — 큰 차트 변경이 생기면
  두 곳 모두에 cherry-pick 해야 함.
- **scope 분리**: scatter는 시나리오별, flow_comparison은 run 1개. 이 분할은
  hybridflowshop 의 multi_instance_runner / multi_scenario_runner 책임 분할을
  그대로 따른 것이라 이질감 없음.
- **단일 시리즈 우선(`obj_value`)**: v1에서 `obj_bound` 곡선은 그리지 않는다.
  bound 가 의미 있는 LB 알고리즘 비교가 필요해질 때 별도 plan으로 다룬다.
- **assertion vs silent skip**: hybrid_match / bks_table 미존재는 silent skip(기존
  pivot 정책 일치). 반면 scenario dir 안 인스턴스의 `instance_result.yaml`
  누락은 raise — 부분 데이터로 평균을 그리면 시각적으로 거짓말이 될 수 있음.
