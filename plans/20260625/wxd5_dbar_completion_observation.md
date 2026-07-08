# Context handoff: wxd5 d̄ overlay & "37 jobs finish right of d̄" observation

> 작성일: 2026-06-25
> 목적: **별도 대화에서 현상 분석**을 이어가기 위한 context 기록.
>       (이 문서는 분석이 아니라 사실/수치/재현 경로의 스냅샷이다.)
> 선행: `plans/20260625/wxd5_dispatch.md` (wxd5 정의·구현·비교 결과)

---

## 0. 관찰된 현상 (분석 대상)

instance **60** (`ins0060`, 50 job / 5 stage / m_last=3) 을 `wxd5` 로 디코드한
schedule Gantt(Panel B)에서, **37/50 job 이 주황 d̄ 선 오른쪽에서 종료**
(`C_j > d̄`). 스크린샷: `output/20260625/priority_viz/스크린샷 2026-06-25 015524.png`,
HTML: `output/20260625/priority_viz/0060_wxd5.html`.

> 왜 이런지(=d̄ 가 완료시점을 크게 과소추정하는지)에 대한 해석/대응은 **다음 대화**에서.

---

## 1. instance 60 의 d̄ 분해 (재현된 수치)

```
mean_midpoint (= wxd2 d̄)            = 410.56
min_j r_j                           = 132          # get_job_2_p_sum_except_last_stage 의 최소
Σ_j p_last (전체 job 마지막 stage)   = 2565
Σ_j p_last / (m_last × 2 = 6)        = 427.50
lower-bound term                     = 132 + 427.50 = 559.50
wxd5 d̄ = max(410.56, 559.50)        = 559.50        # 하한항이 중점평균을 지배
```

부수 사실:
- due window 상한 최대 `d⁺_max = 564`; **49/50 job 의 `d⁺_j < d̄(559.5)`**
  → tardiness aversion 이 거의 모든 job 에서 우세 → **partition 이 50 early / 0 late**
  (`initialize_by_simple_dispatch` 로그에서 `wxd5 partition: 50 early, 0 late (d̄=559.5)`).
- 실제 **makespan = 1283** (d̄=559.5 의 2배 이상). `C_j > d̄` 인 job 이 37개.

→ 즉 d̄ 의 두 번째 항 `min r_j + Σp_last/(2·m_last)` 은 **마지막 stage 완료의
낙관적 하한**인데, 실제 완료 분포(makespan 1283)와 큰 괴리. `/(m_last×2)` 의
×2 와 "전체 job 평균 부하" 가정이 완료시점을 크게 과소추정하는 것으로 보인다.
(가설일 뿐 — 분석은 다음 대화.)

분석 재현 스니펫은 이 대화에서 다음으로 산출:
```python
from ffc_ddw_sum_et.orchestration.benchmark_loader import BenchmarkLoader
import importlib.util
spec = importlib.util.spec_from_file_location("ri", "scripts/render_priority_inspector.py")
ri = importlib.util.module_from_spec(spec); spec.loader.exec_module(ri)
inst = BenchmarkLoader("benchmarks/PRA2017/large",
        ins_index_source="benchmarks/PRA2017/pra2017_hybrid_match.csv").load_all(ins_index=60)[0]
d_bar5 = ri._wxd5_d_bar(inst)
sched, seq, end = ri.decode_schedule(inst, "wxd5")
last = inst.stage_id_list[-1]
comp = {j: sched.get_job_end_time(last, j) for j in inst.job_id_list}
right = sum(1 for j in inst.job_id_list if comp[j] > d_bar5)   # -> 37
```

---

## 2. wxd5 정의 (요약, 상세는 wxd5_dispatch.md)

`wxd5` = **wxd2 와 partition·정렬·tie 모두 동일, d̄ 정의만 교체**.

```
d̄ = max( 윈도우 중점 평균,
          min_j r_j + Σ_j p_last_j / (m_last × 2) )
```
- `r_j = get_job_2_p_sum_except_last_stage()` (마지막 stage 제외 처리시간 합)
- `p_last = get_job_2_p_map_for_stage(last_stage)`, 전체 job 합
- `m_last = last_stage_mc_count`
- 이 d̄ 가 partition aversion score 와 양 그룹 정렬식 **전부**에 들어감.

구현: `src/ffc_ddw_sum_et/parameters/ffc_ddw_params.py::get_wxd5_job_sequence`,
등록: `src/ffc_ddw_sum_et/parameters/sorter.py` (`DispatchSeqKey` + `direct`).

---

## 3. 전체 sweep 비교 결과 (1440 large instances, 0.09nc)

기존 full sweep run `output/20260625/20260625T002044_922234` (wxd2/3/4) 대비
wxd5-only run `output/20260625/20260625T014141_129123` (RPDf vs BKS, 낮을수록 좋음):

| | wxd2 | **wxd5** | wxd3 | wxd4 |
|---|---|---|---|---|
| sd mean | 0.99127 | **0.99035** | 1.09874 | 1.08828 |
| rd mean | 1.00407 | **1.00374** | 1.10925 | 1.10008 |

- wxd5 ≈ wxd2 (사실상 동률). **1440 중 935 는 d̄ 동일**(하한항이 중점평균 미만 →
  wxd5==wxd2). 달라진 505개는 무승부 수준(sd 247승/258패, 평균 −0.00092).
- 하한항이 bite 하는 구간은 **tight due-date(T=0.4/0.6)** 뿐; T=0.6·R=0.2 에서
  −0.0156(개선), T=0.6·R=0.6 에서 +0.0077(악화) 로 혼재.
- wxd5 는 wxd3/wxd4(~1.10) 보다는 확실히 우수.

비교 스크립트: `/tmp/.../scratchpad/cmp_wxd5.py` (재실행은 두 run 의
`*_rpdf_comparison.csv` 를 scenarioName 으로 join).

---

## 4. inspector d̄ overlay 변경 (이 대화에서 작업)

`wxd5` 도 Panel A partition 오버레이 + d̄ 선을 받도록, 그리고 **Panel B(schedule
Gantt)에도 d̄ 주황 점선**을 그리도록 확장:

- `scripts/render_priority_inspector.py`
  - `_wxd5_d_bar(instance)` 헬퍼 추가.
  - `compute_wxd2_partition(instance, d_bar=None)` — d̄ 주입형(wxd2/wxd5 공용).
  - main 에서 `rule_key in ("wxd2","wxd5")` 일 때 partition 데이터 생성.
  - `render_panel_b_svg(..., d_bar=...)` → `vlines=[(d_bar, D_BAR_COLOR, "d̄")]`.
  - d̄ overlay 가 due window 밖으로 밀려도 안 잘리게 Panel A 표시범위 확장.
- `src/ffc_ddw_sum_et/io/gantt.py`
  - `export_ddw`/`_plot_ddw` 에 generic `vlines: Sequence[(x,color,label)]` 추가
    (d̄ 전용 아님, 재사용 가능). x-horizon 을 vline x 까지 넓혀 클리핑 방지.
  - `axvline` + 상단 `annotate` 라벨. io 경계 규칙 준수(부모 의존 없음).

검증: `D_BAR_COLOR=#ff7f0e` 세로선이 Panel B SVG 에 `line2d_276` 로 1개
(x 고정, y 전체높이) 렌더 확인.

---

## 5. 변경/산출물 위치

- 코드: `parameters/ffc_ddw_params.py`, `parameters/sorter.py`,
  `scripts/render_priority_inspector.py`, `io/gantt.py`
- config: `metadata/20260625/dispatch_sequence_full_sweep_config.yaml`
  (sd_wxd5/rd_wxd5 추가), `metadata/20260625/wxd5_only_config.yaml` (실행용 subset)
- run 결과: `output/20260625/20260625T014141_129123` (wxd5-only)
- viz: `output/20260625/priority_viz/0060_wxd5.html`, `panel_b_gantt.svg`
- 계획: `plans/20260625/wxd5_dispatch.md`, 본 문서

---

## 6. 다음 대화에서 다룰 질문 (열어둠)

- d̄ 하한항 `min r_j + Σp_last/(2·m_last)` 이 완료시점을 과소추정하는 이유와,
  ×2 / 전체평균 가정의 타당성.
- 37/50 이 d̄ 우측 종료라는 사실이 wxd5 priority(=partition 전부 early) 와
  최종 objective(RPDf) 에 실제로 어떤 영향을 주는가 (instance 60 한정 vs 일반).
- d̄ 를 완료시점 추정치로 더 정확히 잡으면(예: makespan 기반) priority 가
  개선되는가 — 아니면 d̄ 는 어차피 partition 분리용 center 일 뿐 완료 예측이
  목적이 아니므로 무관한가.
