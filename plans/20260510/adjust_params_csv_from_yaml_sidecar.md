# Plan — `adjust_params_by_makespan_delta.csv` 를 r1 YAML 사이드카에서 생성

## Goal

`<run_id>_adjust_params_by_makespan_delta.csv` 를 in-memory diagnostic
객체가 아니라 per-instance `calc_mcf_lb_r1_summary_yaml`
사이드카(`progress/calc_mcf_lb_and_derive_full_sch/r1/r1_summary.yaml`)를
직접 읽어서 생성한다. composite step (`calc_mcf_lb_and_derive_full_sch`)
의 row만 생성하며, standalone heuristic 분기는 제거한다.

## Why

- 같은 데이터 흐름을 이미 `_write_calc_mcf_lb_summary_csv` 가 쓰고 있음
  (controller 가 `_emit_calc_mcf_lb_r1_summary_yaml` 로 sidecar를
  쓰면 → reporter가 그 YAML들을 콜레이트). 두 CSV가 동일한 single
  source of truth (r1 sidecar) 를 공유하게 됨.
- in-memory diagnostic 의존을 제거하면 `POST_PROCESS_ONLY` 모드(다른
  실행에서 생성된 output dir 위에 reporting만 다시 돌리는)에서도
  consistent하게 동작.
- 기존 버그(`lastStageOnlyMakespan` 칼럼이 composite 분기에서 항상
  비어있던 문제)는 reporting.py:1095-1121 에 직접 박혀있던
  `lastStageOnlyMakespan = None` 하드코드 때문이었음. YAML 키 매핑으로
  바꾸면 그런 클래스의 버그가 구조적으로 사라짐.
- 사용자 확인: composite-only로 충분(standalone heuristic 분기 drop OK).

## Source-of-truth: r1_summary.yaml 키

샘플 (`output/20260509/20260510T020711_939271/build_full_sch_p_adjust_r_adjust/Instance_50_5_3_0,2_0,2_10_Rep0/progress/calc_mcf_lb_and_derive_full_sch/r1/r1_summary.yaml`):

```yaml
mcfLbElapsedTime: 0.08591381396399811
mcfLbObjValue: 7261.0
mcfLbMakespan: 1134
lastStageOnlyObjValue: 9934.0
lastStageOnlyMakespan: 1179
fullSchObjValue: 10999.0
fullSchMakespan: 1217
totalTime: 0.12471216591075063
makespanDelta: 38
pIncrementAdded: 3
rIncrementAdded: 38
```

YAML 작성자는 controller.py:966-1059 `_emit_calc_mcf_lb_r1_summary_yaml`
(payload 정의는 1040-1052).

## YAML → CSV 칼럼 매핑

| CSV 칼럼 (현행)           | r1_summary.yaml 키       | 의미                                |
| ------------------------- | ------------------------ | ----------------------------------- |
| `scenarioName`            | (scenario loop)          | -                                   |
| `insIndex`                | `_resolve_ins_index`     | hybrid_match 매핑                   |
| `instanceName`            | (instance loop)          | -                                   |
| `lastStageOnlyPmtnMakespan` | `mcfLbMakespan`        | r1 MCF preemptive LP의 makespan     |
| `lastStageOnlyMakespan`   | `lastStageOnlyMakespan`  | r1 heuristic 비-선점 last-stage     |
| `incumbentMakespan`       | `fullSchMakespan`        | r1 full schedule 의 makespan        |
| `makespanDelta`           | `makespanDelta`          | raw signed delta                    |
| `pIncrementAdded`         | `pIncrementAdded`        | r2_p_increment (None when r2 미실행) |
| `rIncrementAdded`         | `rIncrementAdded`        | r2_r_increment (None when r2 미실행) |

검증:
- diagnostic.r1_ls_only_pmtn_makespan = `int(r1_apply.mcf_preemptive_schedule.makespan)`
  (controller.py:1277-1279)
  ↔ YAML.`mcfLbMakespan` = `int(r1_apply.mcf_preemptive_schedule.makespan)`
  (controller.py:1011-1015) — 동일 소스.
- diagnostic.r1_ls_only_makespan = `int(r1_heuristic.schedule.makespan)`
  (controller.py:1281)
  ↔ YAML.`lastStageOnlyMakespan` = `int(r1_heuristic.schedule.makespan)`
  (controller.py:1019-1021) — 동일 소스.
- diagnostic.r1_full_sch_makespan
  ↔ YAML.`fullSchMakespan` — 동일 소스(controller.py:1025-1027 vs 1286).

## Inclusion gate (현행 → 신)

현행:
- (a) `calc_diag is not None and calc_diag["makespan_delta"] is not None`
  → composite row.
- (b) `heuristic_diag is not None and heuristic_diag["makespan_delta"] is not None`
  → standalone heuristic row.

신:
- r1 sidecar 파일이 존재 AND `yaml["makespanDelta"] is not None` → row 생성.
- standalone heuristic 분기 (b) 는 제거.

skip되는 경우:
- composite step이 그 (scenario, instance) 에서 실행되지 않음 → r1
  사이드카 부재 → skip (현행 (a) 와 매칭).
- composite 가 r1 build_full 전에 stop → `makespanDelta` is None →
  skip (현행 `delta is not None` 가드와 매칭).

## Implementation steps

1. `src/ffc_ddw_sum_et/orchestration/reporting.py:1052` 의
   `_write_adjust_params_by_makespan_delta_csv` 를 다음으로 교체:
   - `for sc in self.scenario_results: for ir in sc.instance_results:` 루프는 유지.
   - 각 (sc, ir) 에 대해
     `r1_path = self.layout.artifact_path("calc_mcf_lb_r1_summary_yaml",
     scenario_name=sc.name, instance_name=ir.instance_name)` 로 경로
     계산 (`_write_calc_mcf_lb_summary_csv` 와 동일 패턴).
   - `r1_path.exists()` 가 False면 continue.
   - `r1_data = load_yaml(r1_path) or {}`.
   - `r1_data.get("makespanDelta") is None` 이면 continue.
   - rows.append( (sc.name, ins_index, instance_name,
     r1_data["mcfLbMakespan"], r1_data["lastStageOnlyMakespan"],
     r1_data["fullSchMakespan"], r1_data["makespanDelta"],
     r1_data.get("pIncrementAdded"), r1_data.get("rIncrementAdded")) ).
   - `int(...)` 캐스팅은 하지 않음 — YAML 로드 시 `load_yaml` 이 이미
     int/float을 반환. 단, 빈 셀 표기는 현행대로 `"" if x is None else x`.
   - in-memory `calc_diag` / `heuristic_diag` 참조는 제거.
2. 메서드 docstring 갱신:
   - source: r1 sidecar.
   - composite-only (standalone heuristic 분기 제거 명시).
   - "Composite rows record the *raw signed* delta..." 문장은 유지하되
    delta 계산식은 r1_summary.yaml 의 `makespanDelta` 키를 그대로
     반영한다고만 적기 (계산 로직은 controller/pipeline 에 있음).
3. `tests/algorithm/mcf_lb/test_mcf_lb_pipeline.py` 등 테스트가 이
   메서드를 직접 호출하지 않음(grep 결과 없음). 따라서 테스트 추가/수정
   불요.
4. `uv run ruff check src/ffc_ddw_sum_et/orchestration/reporting.py`
   실행해서 통과 확인.

## Order-of-call 영향

- `_write_calc_mcf_lb_summary_csv` 와 동일하게 r1 사이드카만 읽으므로
  순서 의존성 없음 — `generate()` 의 호출 순서 변경 불요.
- 사이드카 자체는 controller 가 composite step 직후
  `_emit_calc_mcf_lb_r1_summary_yaml` 에서 쓰므로 reporting 시점엔
  반드시 존재(composite 가 실제로 돌았다면).

## Risks / 결정 보류 사항

- **standalone `heuristic_last_stage_only_sch_from_mcf_lb` 단독 호출
  flow** 에서 adjust 가 일어나는 경우 더 이상 이 CSV 에 안 잡힘.
  사용자 확인 끝(drop OK). 필요해지면 그 step 도 sidecar 를 쓰도록
  하고 동일 패턴으로 두 번째 소스를 합치는 게 정공법.
- POST_PROCESS_ONLY 모드에서 옛날 output dir(사이드카 없는) 에 대해
  돌리면 CSV 가 비게 되는데, 이는 in-memory diagnostic 도 어차피
  없으므로 현행 동작과 동일.

## Out of scope

- per-scenario `calc_mcf_lb_summary_csv` 자체는 이미 YAML 사이드카
  소스. 추가 변경 없음.
- 칼럼 추가/제거. 칼럼 셋과 의미는 동일하게 유지(버그만 수정).
