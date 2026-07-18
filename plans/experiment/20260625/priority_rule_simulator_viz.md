# Plan: priority rule 시각화 / 시뮬레이터 (instance 60)

> 작성일: 2026-06-25
> 목적: 한 priority rule을 예시 instance에 적용해, **"이 우선순위가 합당한가"를
>       눈으로 검증**할 수 있는 자료를 만든다.
> 예시 instance: **0060** = `Instance_50_5_3_0,6_0,2_10_Rep0`
>   (n=50, c=5 stage, 3 mc/stage, **T=0.6·R=0.2 = tight·narrow 혼잡**, BKS=40952)
> 선행 자료: `vault/dispatching.md` / `vault/dispatching.html` (전 rule 정리)
> 구현은 **별도 대화에서 진행** — 본 문서는 계획까지.

---

## 0. 요약 (TL;DR)

`(instance_index, rule_key)` 를 받아 **단일 self-contained HTML/SVG** 한 장을
뽑는 정적 도구를 만든다. 한 장 안에 3개 패널:

1. **Priority Inspector** (신규 핵심) — job을 dispatch 순서로 세로 정렬해
   각 job의 due window·weight·`d̄`·partition·정렬 key를 한 행에 펼친다.
   → "왜 이 순서인가"가 보인다.
2. **Decoded Schedule** (기존 `DDWGanttPlotter.export_ddw` 재사용) — 그 순서를
   simple-dispatch로 디코드한 실제 스케줄 + due window 대비 완료시각.
   → "그 순서가 좋은 스케줄을 만드는가"가 보인다.
3. **Stats 헤더** — weighted E+T, early/in-window/tardy job 수, makespan, vs BKS.

**왜 instance 60이 좋은가**: n=50(행 50개 = 한눈에) + 혼잡 영역이라 대부분
tardy → priority가 E/T에 주는 효과가 극적으로 드러난다.

---

## 1. "합당함"을 눈으로 본다는 것 — 검증 가설

Priority Inspector 패널이 통과시켜야 할 **육안 체크**(= acceptance criteria):

| 체크 | 무엇을 본다 | rule이 합당하면 |
|---|---|---|
| **due 추세** | 위→아래(rank 1→50)로 갈수록 due window band가 오른쪽으로 | 대체로 우하향 대각선 (early-due 먼저) |
| **weight override** | 추세를 거스르는 행(늦은 due인데 앞 순위) | 그 행의 `w⁺`가 크다 (지체 비용이 due를 이김) — 정당한 예외 |
| **partition (wxd)** | early/late group 경계선 | early group = 좌측(early due)·고`w⁺`에 몰림 |
| **key 단조성** | 우측 gutter의 정렬 key 값 | rank를 따라 단조 (정렬이 실제로 그 key대로) |
| **outcome 정합** | 완료 marker 색 (early=파랑/in=초록/tardy=빨강) | in-window(초록) 다수, 빨강 군집이 적음 |

→ 거스르는 행이 **weight로 설명되면 합당**, 설명 안 되면 rule 결함.
이게 "시뮬레이터"의 판정 화면이다.

---

## 2. 대상 rule 선택

- **1차: `wxd2`** (현 paired-best single rule). Inspector에 wxd2 전용 오버레이
  (early/late partition, `earliness_aversion` vs `tardiness_aversion` 점수,
  `C_e·(d⁻−d̄)` key)를 그린다.
- **대조군 1개 동시 출력 권장: `edd`** (순수 due 기반). 같은 instance를 두 rule로
  나란히 뽑으면 "wxd2가 weight로 어디를 어떻게 재배치했는가"가 대조로 드러난다.
- 도구는 **임의 `DispatchSeqKey`** 를 받게 설계 (rule별 오버레이는 plugin 형태;
  미지원 rule은 공통 패널만, partition/score 오버레이는 생략).

> 구현 범위 결정(별도 대화): v1을 **단일 rule** 한 장으로 갈지, **2-rule 대조**
> 한 장으로 갈지. **추천: 단일 rule 한 장 + `--compare <other_key>` 옵션**으로
> 같은 레이아웃을 좌우 2단으로 확장.

---

## 3. 패널 설계

### 3.1 Panel A — Priority Inspector (신규, 직접 SVG 작도)

좌표: 공유 x축 = time, y축 = dispatch rank(위=1, 먼저 디스패치).

행(job j)별 구성:
- **좌측 gutter**: `#rank`, `job_id`, weight glyph — `w⁻`(파랑)·`w⁺`(빨강) 작은 막대 2개.
- **본문**: due window band `[d⁻_j, d⁺_j]` (회색 막대) + midpoint tick.
- **완료 marker**: 디코드 결과 `C_j` 를 같은 x축에 점으로 — 색은 early(파랑)/
  in-window(초록)/tardy(빨강). band 대비 위치로 E/T가 보인다.
- **전역 오버레이**: `d̄`(전 job midpoint 평균) 세로 점선 1개.
- **wxd 전용**: early/late group 사이 가로 구분선 + early group 행 옅은 음영;
  우측 gutter에 정렬 key 값(또는 `T_av−E_av` 막대).
- **우측 gutter**: 정렬 key 수치(단조성 확인용).

밀도: n=50 → 행 높이 ~14px, 전체 ~750px 세로. 무리 없음.
(대형 instance 확장 시 `--job-subset` 으로 상·하위 K개만; 본 plan은 n=50 전량.)

### 3.2 Panel B — Decoded Schedule (기존 자산 재사용)

- `DDWGanttPlotter.export_ddw(...)` 그대로 사용 (`src/ffc_ddw_sum_et/io/gantt.py`).
  이미 machine lane Gantt + 하단 due-window strip + earliness(`#1f77b4`)/
  tardiness(`#d62728`) 막대를 SVG로 그린다.
- 입력: `schedule.get_jik_2_start_time_map()` / `get_jik_2_end_time_map()`,
  `job_2_dw_map`, `job_2_completion`.
- 50 job × 5 stage × 3 mc = 밀도 높음 → Panel B는 **job-subset 허용**
  (예: tardy 상위 + early 상위 10개). 참고: `scripts/render_intro_ddw_gantt.py`
  가 정확히 이 subset+export_ddw 흐름의 예제.

### 3.3 Stats 헤더

`compute_weighted_earliness_tardiness(schedule, instance)` → `(ΣE, ΣT)`.
표기: total weighted E+T, ΣE/ΣT 분해, early/in/tardy job 수, makespan,
`obj / BKS` 비율(BKS=40952), rule_key, instance id.

---

## 4. 디코드 충실도 (실험과 동일한 스케줄을 그릴 것)

Inspector의 완료시각과 Panel B 스케줄은 **실험에서 실제 도는 sd_ 경로와 동일**
해야 의미가 있다. 두 선택지:

- **(권장) 컨트롤러 경로 재사용**: `FFcDDWSubroutineController.initialize_by_simple_dispatch`
  (`controller.py:1749`) 가 `dispatch_seq_job_sequence` 정렬 →
  `MixedDispatcher.get_job_centric_schedule_by_sequence`(np=job_count) →
  `make_semi_active` → `insert_idle_time` 를 그대로 수행. 컨트롤러를 띄워
  이 step을 호출하고 `solution_manager`/report에서 schedule을 꺼낸다.
- **(대안) 최소 재현**: 컨트롤러 부트스트랩이 무거우면 동일 3-step을 스크립트에서
  직접 호출. 단 step과 **drift 위험** → 재현이라면 1번을 우선.

> 결정(별도 대화): 컨트롤러 인스턴스화 비용 확인 후 1 vs 2.

---

## 5. 재사용 맵 (Explore 조사 결과)

| 필요 | 호출 대상 | 위치 |
|---|---|---|
| instance 60 로드 | `BenchmarkLoader(...).load_all(ins_index=60)` | `orchestration/benchmark_loader.py:36` |
| 인덱스 CSV | `pra2017_hybrid_match.csv` (insIndex `0060`) | `benchmarks/PRA2017/` |
| 우선순위 시퀀스 | `dispatch_seq_job_sequence(instance, key)` | `parameters/sorter.py` |
| 디코드 | `initialize_by_simple_dispatch` 또는 `MixedDispatcher.get_job_centric_schedule_by_sequence` | `controller.py:1749` / `algorithm/dispatcher/mixed.py:132` |
| E/T 보정 | `FFcSchedule.make_semi_active`, `insert_idle_time` | `solution/ffc_schedule.py` |
| 완료시각 | `schedule.get_ji_2_end_time_map()` (last stage) / `get_job_end_time` | `solution/ffc_schedule.py:275,350` |
| 목적값 | `compute_weighted_earliness_tardiness(sch, inst)` | `solution/objectives.py:12` |
| Panel B Gantt | `DDWGanttPlotter.export_ddw(...)` | `io/gantt.py:391` |
| Gantt 예제 흐름 | `scripts/render_intro_ddw_gantt.py` | `scripts/` |
| due/weight/p 접근 | `job_2_due_window_map`, `job_2_ewt_map`, `job_2_twt_map`, `job_2_stage_2_p_map` | `parameters/ffc_ddw_params.py` |

신규로 짤 것은 **Panel A 작도 + 3패널 합성 + CLI/config** 뿐. 나머지는 호출.

---

## 6. 산출물 형태 & 위치

- **1차(권장)**: 단일 **self-contained HTML** — Panel A(인라인 SVG, 직접 작도)
  + Panel B(`export_ddw` SVG 인라인 임베드) + Stats. 외부 의존 0, 더블클릭 오픈.
  repo의 SVG-gantt 관례 + `vault/*.html` 미감과 일치.
- **대안**: matplotlib PNG(기존 `GanttPlotter` 톤). 정적 배포엔 충분하나 due-band가
  세밀해 벡터(SVG)가 유리 → HTML/SVG 우선.
- **출력 경로**: `output/<date>/priority_viz/<insIdx>_<rule>.html`
  (실험 산출물과 분리; vault에 둘지 output에 둘지는 구현 시 결정).
- **구동**: `scripts/render_priority_inspector.py` + YAML config
  (`render_intro_ddw_gantt.py` 패턴: instance_index, rule_key, compare_key?,
  job_subset?, output_path, title). `uv run python` 으로 실행.

---

## 7. v1 범위 vs 확장 (YAGNI 경계)

**v1 (이번 구현 범위)**
- instance 60 + `wxd2`(+`edd` 대조) 한 장 HTML.
- 정적, 단일 (instance, rule). wxd2 partition/score 오버레이.

**확장 (필요 시에만, 본 plan에선 미구현)**
- 임의 rule loop / 전 rule 배치 출력.
- **인터랙티브**: 드롭다운으로 rule 전환 (vanilla JS 1개 HTML). repo gantt는
  no-JS 관례라 별도 tool로 분리. 진짜 "시뮬레이터" UX가 필요하면 이때.
- dispatch 애니메이션(순서대로 job이 lane에 쌓이는 재생).
- rank-delta 대조 색칠(두 rule 간 순위 이동량).

---

## 8. 리스크 / 열린 질문

1. **디코드 재현 충실도** (§4) — 컨트롤러 경로 vs 최소 재현. drift 방지 위해 1번 권장.
2. **Panel B 밀도** — 50×5×3은 빽빽. subset 기본값(예: tardy/early 각 상위 K)
   정책 필요. Panel A는 전량 OK.
3. **`d̄` 정의 일치** — Inspector의 `d̄`는 wxd2와 동일하게 midpoint 평균이어야 함
   (`(d⁻+d⁺)/2` 평균). cpd_wmean 등 다른 center를 쓰는 rule을 그릴 땐 rule별 center를
   오버레이.
4. **출력 위치/명명** — vault vs output. 실험 산출물과 섞이지 않게.
5. **단일 vs 대조 레이아웃** (§2) 및 **HTML vs PNG** (§6) 최종 결정.

---

## 9. 구현 체크리스트 (별도 대화용 인계)

- [ ] `scripts/render_priority_inspector.py` + YAML config 스캐폴드 (intro gantt 참고).
- [ ] instance 60 로드 → `dispatch_seq_job_sequence(inst, "wxd2")`.
- [ ] simple-dispatch 디코드 (§4 결정 경로) → schedule + `C_j` map.
- [ ] Panel A SVG 작도 함수 (rows, due band, weight glyph, `d̄`, partition, key gutter).
- [ ] Panel B: `DDWGanttPlotter.export_ddw` 호출, SVG 문자열 확보.
- [ ] Stats: `compute_weighted_earliness_tardiness` + BKS 비율.
- [ ] 3패널 → 단일 HTML 합성, self-contained 검증.
- [ ] `--compare edd` 좌우 2단 (선택).
- [ ] `uv run ruff check` / `format`.
