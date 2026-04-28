# Experiment Review: 2026-04-22 ~ 2026-04-28

**Range**: `ec0fa4b` (refactor(cumulative): drop dead code, tighten API) → `498cf05` (`20260428T234426_736253 run setting`)
**Benchmark**: PRA2017 large (1440 instances, `pra2017_hybrid_match.csv`)
**Reported objective**: weighted earliness + tardiness (wET)

---

## Tracked experiment timestamps

총 **23개** 실험 시점:

- 명시적 run-setting commit 22개 중 RUN 17 폐기 → 21개
- commit 본문에 timestamp ID가 박혀 있는 케이스 2개 (`97849df`, `333dad3`) → +2개

병합·폐기 처리:

- `b39ed49`(cumulative heuristic to phase 2) → RUN 16 (`20260427T025803_513725`)의 `best_mcf_lb_*` 3개 시나리오와 동치로 본다.
- `665000e`(WxD dispatch initializers) → wxd3(rename 후 wxd1)만 유효한데, **이후 동일 dispatch 조건(timelimit=300s, instance_worker_cnt=48)으로 wxd1 풀 벤치를 다시 돌렸으므로**(RUN 23, `20260428T234426_736253`, `848f725`) 그 결과로 갈음. 나머지 4개 시나리오는 폐기.
- `dddef72`(wxd2 첫 도입) → 폐기.
- `056f476`(apply_lb_by_mcf 첫 런) → RUN 20으로 병합.
- `96bc1fa`(MPF23 첫 런) → RUN 21로 병합.
- `8e34fb3`(half_time 첫 런) → RUN 22로 병합.
- 폐기 (`20260428T001347_389139`, `neh_cp_config_15`, wxd3 priority) → 직후 `f40b0e3`로 wxd3 → wxd1 통합 + wxd2/wxd3 변종 제거.

---

## Run index

| # | timestamp | machine | config file | source commit | scope | output_dir |
|---|---|---|---|---|---|---|
| 1 | 20260423T114900_417063 | mso02 | `20260423/1_mcf_lb_init_13_config.yaml` | `ad4a023` | full (1440) | `output/20260423/` |
| 2 | 20260423T171935_897548 | mso02 | `20260423/cmax_init_pfns_config.yaml` | `6619d1f` | full (1440) | `output/20260423/` |
| 3 | 20260423T173918_198369 | mso02 | `20260423/cmax_init_pfns_config.yaml`* | `97849df` (commit body) | full (1440) | `output/20260423/` |
| 4 | 20260423T174736_400968 | mso02 | `20260423/cmax_init_pfns_config_2.yaml` | `f7102b9` | full (1440) | `output/20260423/` |
| 5 | 20260423T221248_575301 | mso02 | `20260423/1_mcf_lb_init_13_config.yaml` | `10b1a5d` | full (1440) | `output/20260423/` |
| 6 | 20260424T041848_326215 | mso02 | `20260423/neh_cp_config_4.yaml` | `7cbbb4b` | full (1440) | `output/20260423/` |
| 7 | 20260424T213007_556893 | mso02 | `20260424/neh_cp_config_5.yaml` | `8195d63` | full (1440) | `output/20260424/` |
| 8 | 20260425T034449_338686 | mso02 | `20260424/neh_cp_config_8.yaml` | `333dad3` (commit body) | full (1440) | `output/20260424/` |
| 9 | 20260425T200857_851387 | mso02 | `20260425/neh_cp_config_9.yaml` | `5bb35cc` | full (1440) | `output/20260425/` |
| 10 | 20260425T205244_871000 | mso02 | `20260425/neh_cp_config_9.yaml` | `ba0f8d9` | full (1440) | `output/20260425/` |
| 11 | 20260425T232836_063038 | mso02 | `20260425/neh_cp_config_10.yaml` | `4b52002` | full (1440) | `output/20260425/` |
| 12 | 20260426T014532_241012 | mso02 | `20260425/neh_cp_config_11.yaml` | `9e636c2` | full (1440) | `output/20260425/` |
| 13 | 20260426T174905_399637 | mso02 | `20260426/neh_cp_config_12.yaml` | `d4f0379` | full (1440) | `output/20260426/` |
| 14 | 20260426T185350_366559 | mso02 | `20260426/neh_cp_config_13.yaml` | `8ee39b7` | full (1440) | `output/20260426/` |
| 15 | 20260426T212121_069773 | hjt5950x | `20260426/mcf_lb_init_14_config.yaml` | `0bea6a1` | full (1440) | hjt5950x (별도 머신) |
| 16 | 20260427T025803_513725 | mso02 | `20260426/20260426_config.yaml` | `4ddbfda` | full (1440) | `output/20260426/` |
| 17 | 20260427T123656_726782 | mso02 | `20260427/mcf_lb_init_16_config.yaml` | `409a00e` | full (1440) | `output/20260427/` |
| 18 | 20260427T173735_407299 | mso02 | `20260427/mcf_lb_init_17_config.yaml` | `fa4e16f` | tail 5 (`[1435..1439]`) | `output/20260427/` |
| 19 | 20260428T022941_371229 | mso02 | `20260427/wxd2_1_config.yaml` | `d9a0905` | full (1440) | `output/20260427/` |
| 20 | 20260428T130925_989218 | mso02 | `20260428/mcf_lb_only_config.yaml` | `c0682f1` | full (1440) | `output/20260428/` |
| 21 | 20260428T165900_623730 | mso02 | `20260428/neh_cp_config_16.yaml` | `2883b04` | full (1440) | `output/20260428/` |
| 22 | 20260428T214400_957643 | mso02 | `20260428/mcf_lb_init_18_config.yaml` | `8c8723c` | full (1440) | `output/20260428/` |
| 23 | 20260428T234426_736253 | mso02 | `20260428/dispatch_wxd1_1_config.yaml` | `848f725` | full (1440) | `output/20260428/` |

\* 동일 yaml에 `iit_after_each_dispatch` 제거(`97849df`)가 적용된 직후의 런.

mso02·hjt5950x는 동시에 병렬로 실험을 진행한 두 머신.

---

## Phase 1 — MCF-LB 파라미터 정리 (4/22 ~ 4/23 오전)

기간 commit: `ec0fa4b`, `a71bd3f`, `53c0af7`, `a0bc810`, `8e14681`, `134e6d3`, `5258f92`, `60356ca`, `9d8a175`

핵심 작업

- `cumulative` API 정리: 미사용 헬퍼·flag 제거, kwargs를 keyword-only로 강제, `horizon` 필수화, `mcf_lb`를 `obj_lb`로 phase4에 직결.
- `mcf-lb` 파라미터 통일:
  - `pf` 파라미터를 `PFMethod`로 통합 (`8e14681`) → `cp_pf`로 명명 정리 (`134e6d3`).
  - `cp_tl`·search-log 파라미터 추가 (`5258f92`).
  - phase별 `thread_cnt` 분리 (`9d8a175`).
- repeat-while-improving flag를 last-stage-only / full-cp 두 phase로 분리 (`a71bd3f`).
- `cumulative` E/T를 reference schedule에서 hint (`a0bc810`).

### RUN 1 — `20260423T114900_417063` (mso02)

- Config: `20260423/1_mcf_lb_init_13_config.yaml`
- 변경: `ins_index: [0, 1439]` 주석 처리 + `log_*_search_progress` 모두 끔
- 의도: MCF-LB 파라미터 정리 직후 PRA2017 large 풀 벤치. 로그 부담 감소를 위해 search progress 끔.

---

## Phase 2 — BN2D 디스패처 포팅 + best-of-dispatches 초기화 (4/23 오후)

기간 commit: `a0f5d73`, `88e01c2`, `1ab4d00`, `47fc501`, `29c2c6a`, `c5f143b`, `0bda696`, `cca13fb`, `97849df`

핵심 작업

- hybridflowshop의 BN2D 디스패처 포팅 (`a0f5d73`).
- `iit_after_dispatch` 옵션 추가 (`88e01c2`).
- `pfns`에 `cp_tl` 파라미터 도입 + `obj_bound` 초기화 정리 (`1ab4d00`).
- BN2D pfns 실험 config 신설 (`47fc501`).
- controller에서 dispatch helper 추출 (`c5f143b`) → `initialize_by_best_of_selected_dispatches` step 추가 (`0bda696`).
- BN2D `solver_thread_cnt` 노출 (`cca13fb`).
- mixed dispatch 내부 비교를 항상 makespan으로 고정. controller-level에서만 IIT+wET 비교 (`97849df`).

### RUN 2 — `20260423T171935_897548` (mso02)

- Config: `20260423/cmax_init_pfns_config.yaml` (ins_index 풀 활성화)
- 의도: Cmax 초기화 → PF1+NS 라인의 best-of-dispatches 포팅 결과 첫 풀 벤치.

### RUN 3 — `20260423T173918_198369` (mso02, commit body)

- Config: 동일 (`cmax_init_pfns_config.yaml`)
- 직전 변경: `97849df`이 yaml에서 `iit_after_each_dispatch` 제거 + mixed dispatch 내부 비교 makespan으로 고정.
- 의도: criterion 결정(내부=makespan) 반영 후 재현성 확인.

### RUN 4 — `20260423T174736_400968` (mso02)

- Config: `20260423/cmax_init_pfns_config_2.yaml` (config 신규 복제 후 main.py 스위치)
- 의도: v2 config로 풀 벤치 재시작.

### RUN 5 — `20260423T221248_575301` (mso02)

- Config: `20260423/1_mcf_lb_init_13_config.yaml` (BN2D 라인에서 다시 MCF-LB 라인으로 복귀)
- 직전: `5c5d620`(`InstanceResult.makespan`).
- 의도: BN2D 라인 일단락하고 MCF-LB 라인 재개.

---

## Phase 3 — NEH-CP 점진형 컨스트럭터 도입 (4/24)

기간 commit: `c1a3bc8`, `831a86c`, `570520b`, `90aecbc`, `64a7e36`, `9bbff3f`, `bdb17b7`, `251c921`, `00be464`, `0930756`, `4696f12`

핵심 작업

- PRA2017 cost heatmap 시각화: C-cost heatmap (`c1a3bc8`), Z 클립 + due window 정렬 (`831a86c`), wET heatmap + NEH-CP sort (`251c921`), processing-time rect (`0930756`).
- NEH-CP 점진형 CP-SAT constructor (`570520b`).
- `skip_pf_below_obj` 파라미터 (`90aecbc`).
- cumulative TL + `c`-suffix (TL을 `c` 곱한 값으로 표현) (`64a7e36`).
- `job_priority` 파라미터 (`4696f12`).

### RUN 6 — `20260424T041848_326215` (mso02)

- Config: `20260423/neh_cp_config_4.yaml` 신규 (batch=5, `cp_tl=0.15c`, `skip_pf_below_obj=makespan`).
- 의도: NEH-CP 점진형 + makespan-기반 PF skip 첫 풀 런.

### RUN 7 — `20260424T213007_556893` (mso02)

- Config: `20260424/neh_cp_config_5.yaml` (ins_index 풀)
- 직전: `4696f12`(`job_priority`).
- 의도: `job_priority` 인자 추가 후 첫 풀 런.

---

## Phase 4 — 16-시나리오 스윕 → 단일 시나리오 정착 (4/25)

기간 commit: `7225cee`, `5047865`, `5fd5f2e`, `ef2d531`, `69bd970`, `258e8cc`, `de76fa2`, `333dad3`, `f0b49ab`, `6f6e02e`, `c624b3d`

핵심 작업

- NEH-CP를 전용 모듈로 추출 (`7225cee`).
- dispatched 결과를 hint로 적용 (`5047865`/`69bd970`).
- lex makespan stage-2 solve (`258e8cc`).
- `due*-weight-pos` priority 추가 (`de76fa2`).
- 16-시나리오 스윕 config_8: batch×opt×sort×PF (`333dad3`).
- RPDf pivot 대시보드 (`f0b49ab`).
- `total_timelimit` kwarg (`6f6e02e`).
- per-step lb/gap 로그 (`c624b3d`).

### RUN 8 — `20260425T034449_338686` (mso02, commit body)

- Config: `20260424/neh_cp_config_8.yaml` (16 시나리오: batch∈{5,10} × opt∈{single,lex} × sort∈{dplus,dstar} × PF∈{PF1,PF2})
- cp_tl 페어: batch 5 → (0.12c, 0.03c); batch 10 → (0.24c, 0.06c).
- 의도: NEH-CP 설계 공간 첫 전수 스윕.

### RUN 9 — `20260425T200857_851387` (mso02)

- Config: `20260425/neh_cp_config_9.yaml` (ins_index 풀, 두 번째 시나리오 `_2` 삭제)
- 의도: 16-시나리오 스윕 결과를 보고 batch 15·due-weight-pos·PF1·single 단일 변형만 남겨 풀 런.

### RUN 10 — `20260425T205244_871000` (mso02) — 정정 런

- Config: `config_9.yaml`에서 `cp_tl: 0.16c → 0.36c`
- commit 메시지: *"Former config.yaml had unintendedly short cp_tl value. Intension: cp_tl / c / added_batch_size = 0.024"*
- 의도: 의도된 비율 0.024 회복 (RUN 9의 cp_tl 오설정 정정).

### RUN 11 — `20260425T232836_063038` (mso02)

- Config: `20260425/neh_cp_config_10.yaml` 신규 (batch=20, `total_timelimit: 0.024nc`)
- 의도: "총 TL = 0.024 × n × c, batch에 분배" 패러다임 전환 후 첫 풀 런.

---

## Phase 5 — `due2-weight-pos`·valid global LB·MCF-LB phase2 휴리스틱 (4/26)

기간 commit: `51520f5`, `b7895a0`, `c65b17b`, `af9e565`, `10ec283`, `75c1781`, `9eb0451`, `32527db`, `39d502d`, `7a4b293`, `242dfa5`

핵심 작업

- Excel 분석 시트 보강 (`51520f5`).
- batch size RPDf 분석 스크립트 (`b7895a0`) + I/O를 `ANALYSIS_DIR`로 라우팅 (`c65b17b`).
- wET heatmap에 r_j shade + due2 정렬 (`af9e565`).
- `due2-weight-pos` priority 추가 (`10ec283`).
- `num_batches` + `batch_tl_mode` 파라미터 (`75c1781`).
- reporting 컬럼 조정 (`9eb0451`).
- `compute_weighted_earliness_tardiness` 사용 일원화 (`32527db`).
- `repeat_pf_cp_while_improving` flag 교체 (`39d502d`).
- **MCF LB가 valid global LB를 사용하도록 수정** (`7a4b293`).
- MCF-LB seed tag `due2-weight-pos` (`242dfa5`).

### RUN 12 — `20260426T014532_241012` (mso02)

- Config: `20260425/neh_cp_config_11.yaml` (ins_index 풀)
- 의도: 분석 시트·RPDf 피벗 갖춰진 상태로 또 하나의 NEH-CP 변형 풀 런.

### RUN 13 — `20260426T174905_399637` (mso02)

- Config: `20260426/neh_cp_config_12.yaml`
  - `b25_single_due2_pf1` → `b20_single_dplus2_pf1` (batch 25 → 20)
- 의도: 직전 RPDf 분석으로 batch 25는 과대 → batch 20으로 내리고 `due2-weight-pos` 도입.

### RUN 14 — `20260426T185350_366559` (mso02)

- Config: `20260426/neh_cp_config_13.yaml` (ins_index 풀, 마지막 시나리오에 `batch_tl_offset_seconds: 0.1`)
- 의도: `batch_tl_mode` 신규 + 시나리오 끝에 작은 offset 부여한 풀 런.

### RUN 15 — `20260426T212121_069773` (hjt5950x) — 병렬 머신

- Config: `20260426/mcf_lb_init_14_config.yaml`
- 직전: `7a4b293`(global LB 수정), `242dfa5`(due2 seed).
- 의도: MCF-LB 정정 직후 mso02와 **병렬**로 hjt5950x에서 풀 런.

---

## Phase 6 — 통합 NEH-CP+MCF-LB 스윕 + phase2 휴리스틱 (4/27)

기간 commit: `ddba764`, `b39ed49`, `26d3211`, `2fabf18`

핵심 작업

- `.gitattributes` + pyproject 정리 (`ddba764`).
- **MCF-LB phase2에 cumulative 휴리스틱 추가** (`b39ed49`). 이 commit이 main.py CONFIG_PATH를 `neh_cp_config_13` → `_14`로 슬쩍 전환 (run-setting commit 누락). 결과는 RUN 16의 `best_mcf_lb_*` 3개 시나리오와 동치.
- adaptive batch + TL 로그 필드 (`26d3211`).
- MCF-LB pivot 대시보드 (`2fabf18`).

### RUN 16 — `20260427T025803_513725` (mso02)

- Config: `20260426/20260426_config.yaml` 신규 (8 시나리오: NEH-CP 5개 + run_mcf_lb_4 3개)
- NEH-CP 블록 (120s):
  - batch 스윕 @ `total_timelimit=0.024nc`: bs20 / bs15 / bs12+0.04n
  - TL 스윕 @ bs20: 0.024nc / 0.01nc / 0.02nc
- MCF-LB 블록 (300s, run_mcf_lb_4 + phase2 cumulative 휴리스틱):
  - (last_stage_only_tl, full_cp_tl) 페어: (0.005,0.005) / (0.01,0.005) / (0.01,0.01) nc
- 의도: 같은 1440 인스턴스에서 NEH-CP batch/TL 스윕과 MCF-LB phase2 budget 스윕을 한 config로 묶어 비교. **`b39ed49`의 첫 런 결과는 이 RUN 16의 MCF-LB 블록과 동치로 흡수.**

### RUN 17 — `20260427T123656_726782` (mso02)

- Config: `20260427/mcf_lb_init_16_config.yaml` 신규 (5 시나리오, 300s, run_mcf_lb_4)
- Phase2=CP (2 시나리오, 양쪽 phase repeat-while-improving=true): asis / cp_p2_010nc
- Phase2=cumulative heuristic (3 시나리오, insert_radius=3m, repeat_full_cp_while_improving=false): heu_p2_005nc_p4_005nc / heu_p2_010nc_p4_005nc / heu_p2_010nc_p4_010nc
- 의도: (1) CP-phase2 vs heuristic-phase2; (2) heuristic 하 P2/P4 budget 분할; (3) CP-phase2 하에서 P2를 0.01nc로 캡 vs unbounded.

### RUN 18 — `20260427T173735_407299` (mso02)

- Config: `20260427/mcf_lb_init_17_config.yaml` 신규 (`ins_index: [1435..1439]` 큰 인스턴스 5개만)
- 동시에 reporting/post_run_pivot의 initial_state를 `(rows=[scenarioName,R], cols=[T])`로 정정.
- 의도: 큰 인스턴스 phase2-heuristic 시나리오 1개를 길게 돌려 first-improvement-restart 옵션 검증 + 피벗 축 정정 적용.

---

## Phase 7 — WxD 디스패처 도입과 통합 (4/27 후반 ~ 4/28 새벽)

기간 commit: `02ca2f2`, `930dfc2`, `ae6f431`, `097f0ba`, `d1a55b9`, `c269bd3`, `b6207df`, `e52ebfb`, `75d65e4`, `665000e`, `059dd81`, `2890bab`, `e2f988f`, `f40b0e3`, `dddef72`

핵심 작업

- 문서·docstring 정리 (`02ca2f2`), controller arguments 리팩터 (`930dfc2`), NEH-CP job sequence test 추출 (`ae6f431`), NEH-CP를 algorithm boundary로 lift (`097f0ba`), `collections.abc` → `typing` (`d1a55b9`).
- `20260426_mcf_lb` 브랜치 PR #4 머지 (`e52ebfb`/`75d65e4`).
- **WxD 디스패처 5종 추가** (`665000e`): `initialize_by_w1` / `wxd1` / `wxd2` / `wxd3` / `due2_weight_pos` + 6개 dispatch yaml + `get_w1/wxd1/wxd2/wxd3_job_sequence`. main.py를 `dispatch_20260427_config.yaml`로 자동 전환.
- FFcDDW problem description 문서 추가 (`059dd81`/`2890bab`).
- **wxd3 → wxd1 통합**, wxd2/wxd3 controller initializer와 dispatch yaml 제거 (`f40b0e3`).
- `wxd2` priority **재**도입 (early/late split + window-endpoint scaled key) + `wxd2_1_config.yaml` 신규 + main.py 스위치 (`dddef72`).

### `665000e` 직후 dispatch 비교 런 — 폐기 + 재측정으로 갈음

- Config: `20260427/dispatch_20260427_config.yaml` (W1 / Wxd1 / Wxd2 / Wxd3 / due2_weight_pos 5 시나리오, 각 300s, instance_worker_cnt=48)
- 처리: wxd3 시나리오만 유효(`f40b0e3` 이후 라벨로는 "wxd1")이지만, 동일 dispatch 조건으로 RUN 23(`20260428T234426_736253`)에서 wxd1을 다시 돌렸으므로 그 결과로 갈음. 본 런의 결과는 별도 추적하지 않음.
- 폐기: 나머지 4개 시나리오(W1, 기존 wxd1, wxd2, due2_weight_pos).

### 폐기된 런 (`20260428T001347_389139`) — 참고용

- Config: `20260427/neh_cp_config_15.yaml` (bs12+0.04n, **wxd3** priority, PF1, `make_semi_active_after_cp=true`)
- 폐기 사유: 약 2시간 뒤 `f40b0e3`로 wxd3 → wxd1 통합 + wxd2/wxd3 변종 제거. 결과는 분석 대상 아님.

### RUN 19 — `20260428T022941_371229` (mso02)

- Config: `20260427/wxd2_1_config.yaml` (ins_index 풀)
- 직전: `f40b0e3`(wxd3 → wxd1 통합) → `dddef72`(wxd2 재정의 + config + main.py 스위치).
- 시나리오: `initialize_by_wxd2` + NEH-CP(wxd2).
- 의도: wxd 계열을 wxd1로 통합한 뒤, 새로 정의된 wxd2(early/late split + window-endpoint scaled key)로 풀 런.
- 비고: `dddef72`의 첫 런(샘플)은 본 풀-벤치 런으로 흡수.

---

## Phase 8 — LB-only 진단 → MPF23 → half_time seed (4/28 후반)

기간 commit: `056f476`, `b5fb808`, `96bc1fa`, `8e34fb3`

핵심 작업

- `apply_lb_by_mcf` 서브루틴 (`056f476`): MCF LB만 보고하고 incumbent 등록 X. `solve_mcf_lb()`를 `McfLbResult` dataclass로 추출. parallel-MC cost heatmap을 `io/parallel_mc_cost_heatmap.py`로 분리, r_j grey overlay + x_jt=1 scatter cell 추가.
- heatmap 데이터에 obj_value/cutoff 추가 (`b5fb808`).
- **MPF23 profile-fix 메서드** (`96bc1fa`): stride 인자가 `int` → `frozenset[int]`로 일반화. idx→idx+2, idx→idx+3 두 종류 arc emit.
- **half_time seed tag** (`8e34fb3`): `get_job_priority_by_half_time() = (start + completion) / 2`. SeedTag literal에 `half_time` 추가.

### RUN 20 — `20260428T130925_989218` (mso02)

- Config: `20260428/mcf_lb_only_config.yaml` (ins_index 풀)
- 의도: 인스턴스별 MCF LB와 cost heatmap을 단독으로 추출(스케줄 X) — LB 품질·해석성 진단.
- 비고: `056f476`의 첫 런(샘플)은 본 풀-벤치 런으로 흡수.

### RUN 21 — `20260428T165900_623730` (mso02)

- Config: `20260428/neh_cp_config_16.yaml` (`ins_index: [0..11]` → 풀)
- 의도: 새 PF 메서드 MPF23를 NEH-CP에 적용해 풀 런.
- 비고: `96bc1fa`의 첫 런(샘플 12개)은 본 풀-벤치 런으로 흡수.

### RUN 22 — `20260428T214400_957643` (mso02)

- Config: `20260428/mcf_lb_init_18_config.yaml` (`ins_index: [0..3]` → 풀, 5 시나리오 300s)
- 의도: preemption-MCF의 새 seed tag `half_time`로 phase2/phase4 budget 스윕 풀 런.
- 비고: `8e34fb3`의 첫 런(샘플 4개)은 본 풀-벤치 런으로 흡수.

---

## Phase 9 — wxd1 dispatch 재측정 (4/28 23:44)

기간 commit: `848f725`

핵심 작업

- `665000e`의 5-디스패처 비교에서 wxd3(rename 후 wxd1)만 유효했고 나머지는 폐기됐으므로, wxd1 단독 시나리오를 동일 dispatch 조건으로 재측정.
- `metadata/20260428/dispatch_wxd1_1_config.yaml` 신규 (단일 시나리오 `wxd1` / `initialize_by_wxd1` / 300s / instance_worker_cnt=48).

### RUN 23 — `20260428T234426_736253` (mso02)

- Config: `20260428/dispatch_wxd1_1_config.yaml` (ins_index 풀)
- 의도: `665000e` 세션의 wxd3 결과(현재 라벨 wxd1)를 동일 조건으로 재측정해 갈음.
- 비고: 평가 조건은 `dispatch_20260427_config.yaml`과 동일(timelimit=300s, instance_worker_cnt=48, dispatch-only).

---

## 큰 흐름 요약

1. **Phase 1 (4/22~23 오전)**: `cumulative`/`mcf-lb` 파라미터 정리 → MCF-LB 라인 첫 풀 런(RUN 1).
2. **Phase 2 (4/23 오후)**: BN2D 디스패처 포팅 + best-of-dispatches 초기화 → mixed dispatch criterion 결정 → MCF-LB 라인 복귀(RUN 2~5).
3. **Phase 3 (4/24)**: NEH-CP 점진형 컨스트럭터 도입 → `skip_pf_below_obj`, `c`-suffix, `job_priority` 추가하며 첫 풀 런들(RUN 6~7).
4. **Phase 4 (4/25)**: 16-시나리오 스윕 → batch 15 단일 + cp_tl 비율 정정 → `total_timelimit` 패러다임(RUN 8~11).
5. **Phase 5 (4/26)**: `due2-weight-pos`·`batch_tl_mode`·valid global LB 정정 → mso02·hjt5950x 병렬 풀 런(RUN 12~15).
6. **Phase 6 (4/27)**: NEH-CP+MCF-LB 통합 8-시나리오 스윕 → MCF-LB 단독 budget 분할 스윕 → 큰 인스턴스 phase2-heuristic 검증(RUN 16~18).
7. **Phase 7 (4/27 후반~4/28 새벽)**: 5종 디스패처(W1/Wxd1~3/due2_wp) 도입 후 wxd3 → wxd1 통합 + wxd2 재정의 → wxd2 풀 런(RUN 19; `665000e` 세션과 `20260428T001347_389139` 폐기).
8. **Phase 8 (4/28 후반)**: `apply_lb_by_mcf` LB-only 진단 → MPF23 profile-fix → preemption-MCF half_time seed 스윕(RUN 20~22).
9. **Phase 9 (4/28 23:44)**: `665000e`의 wxd3 결과를 동일 dispatch 조건으로 재측정해 갈음(RUN 23).

---

## Appendix A — Output layout

각 RUN의 산출물은 `<output_dir>/<timestamp>/` 아래에 모인다 (예: `output/20260428/20260428T234426_736253/`).

표준 파일 (모든 RUN 공통)

- `<timestamp>_main.log` — 단일 마스터 로그.
- `<timestamp>_summary.csv` — **per-(instance × scenario) 메인 결과**. 컬럼은 Appendix B 참고.
- `<timestamp>_report.xlsx` — `FFcDDWReporter`가 생성한 분석 시트 (per-scenario stats, 비교 시트 등).
- `<scenario>_statistics.yaml` — 시나리오 단위 집계 (mean/min/max obj, completed/error count, methodCallCounts).
- `<scenario>/<instance_name>/` — 인스턴스별 schedule/log/solution (`*_obj_log.yaml`, `*_schedule.yaml`, `*_solution.json`, `*_statistics.{json,yaml}`).

`f0b49ab`(2026-04-25 20:08) 이후 RUN (RUN 9~)에서 추가 생성

- `<timestamp>_rpdf_comparison.csv` — `summary.csv` + `pra2017_bks_table.csv` join + `RPDf_BKS_data` 미리 계산된 long-form CSV. **결과 비교의 1차 입력**.
- `<timestamp>_rpdf_dashboard.html`, `<timestamp>_win_tie_dashboard.html`, `<timestamp>_time_p_dashboard.html` — `post_run_pivot.py`가 생성한 PivotTable.js 대시보드.
- `<scenario>_mcf_lb_analysis.csv` (MCF-LB 계열 시나리오) — `mcfLb`, `lastStageOnlyObj` 등 MCF-LB 전용 메트릭 추출.
- `<timestamp>_mcf_lb_*.{html,csv}` — `fa4e16f`(2026-04-27 17:38) 이후 RUN (RUN 18~)에서 `(rows=[scenarioName,R], cols=[T])` 축으로 정정된 dashboards/tables. RUN 17 이전 산출물은 axes가 다르므로 비교 시 주의.

비고

- RUN 15는 **hjt5950x** 머신에서 실행되어 본 저장소의 `output/`에 산출물이 없을 수 있음. 비교 시 별도 머신에서 가져오거나 mso02 RUN 16의 MCF-LB 블록과 대치.
- RUN 18(`mcf_lb_init_17`)은 `ins_index: [1435..1439]` 5개 인스턴스만 — 풀-벤치 RUN과 `(R, T)` 축으로 직접 평균 비교 불가, 인스턴스 단위 비교만 의미 있음.

---

## Appendix B — Result columns / metric definitions

**`<timestamp>_summary.csv`** (per-(instance × scenario))

| 컬럼 | 의미 |
|---|---|
| `instanceName` | `Instance_<n>_<m>_<stage>_<R>_<T>_<W>_Rep<k>` 형식 — `pra2017_hybrid_match.csv`의 `ffc_ddw_sum_et_filename`에서 `.txt` 제거한 값과 일치 |
| `scenarioName` | 해당 RUN의 config에 정의된 시나리오 이름 |
| `workStatus` | `feasible` / `optimal` / `infeasible` / `error` |
| `bestObj` | **wET 기준 최종 obj_value** (`AlgRecord.obj_value`, 외부 목적함수) |
| `bestBound` | reportable lower bound (있으면) |
| `initObj` / `initBound` | 첫 incumbent 시점 값 |
| `improvementRatio` | `(initObj - bestObj) / initObj` |
| `bks` | `pra2017_bks_table.csv`의 `BKS_data` 컬럼 — primary BKS reference |
| `mcfLb` | MCF LB 값 (run_mcf_lb_* 계열에서만 채워짐) |
| `lastStageOnlyObj`, `lastStageOnlyBound` | MCF-LB phase2 결과 |
| `dispatchedObj` | dispatch-only 단계 obj (BN2D / WxD 계열) |
| `profileFixObj`, `profileFixBound` | profile-fix 단계 obj/bound |
| `*MinusBksGap` 등 | 단계별 gap 진단 컬럼 (예: `profileFixMinusBksGap = profileFixObj - bks`) |
| `mcfSolveSec`, `lastStageCpSatSec`, `dispatchSec`, `profileFixCpSatSec` | 단계별 wall time |
| `mcfLbReachedPhase` | run_mcf_lb_* 진행 단계 (예: `phase4`) |

**`<timestamp>_rpdf_comparison.csv`** (BKS-joined)

| 컬럼 | 의미 |
|---|---|
| `insIndex` | `pra2017_hybrid_match.csv` 기준 0000~1439 정수 키 |
| `n`, `c`, `totalMcCount`, `T`, `R`, `W` | 인스턴스 그리드 축 (n=jobs, c=stages, T=tightness, R=range, W=earliness weight ratio 등) |
| `BKS_data` | best-known objective (= `bks`) |
| `bestObj` | summary.csv와 동일 |
| `RPDf_BKS_data` | **`(bestObj - BKS_data) / BKS_data`** (lower is better; primary 비교 메트릭) |
| `elapsedTime`, `timelimit`, `time%` | wall time 진행률 (`time% = elapsedTime / timelimit`) |

집계 메트릭 (대시보드/스크립트에서 자주 쓰는 것)

- **mean RPDf** — `RPDf_BKS_data`를 인스턴스 전체(또는 (R, T) 셀) 평균.
- **win / tie count** — 두 시나리오 페어 내에서 `bestObj`가 더 작은(=win) / 같은(=tie) 인스턴스 수.
- **time%** — 시간 예산 대비 첫 best 도달 시점 비율 (낮을수록 빨리 수렴).

비고

- `obj_value`는 항상 **weighted earliness + tardiness**. CP-SAT 내부 목적이 makespan(예: BN2D)인 경우에도 reporter는 wET로 다시 계산해 기록 — `feedback_alg_record_obj_value` 룰.
- Profile-fix가 끝나도 `bestObj < bks`이면 `profileFixMinusBksGap < 0` (BKS 갱신 후보).
- `mcf_lb_only` RUN(20)은 `bestObj = mcfLb`로 채워지며 schedule은 만들어지지 않음 → `dispatchedObj`/`profileFixObj`는 비어 있음.

---

## Appendix C — Cross-RUN scenario equivalence map

같은 알고리즘 구성이 여러 RUN에 걸쳐 등장하는 경우의 매핑 (이름은 다르지만 비교 가능).

**MCF-LB phase2 budget**

| 알고리즘 구성 | RUN 16 (`20260426_config`) | RUN 17 (`mcf_lb_init_16_config`) | RUN 18 (`mcf_lb_init_17_config`) |
|---|---|---|---|
| heu phase2, P2/P4 = 0.005nc/0.005nc | `best_mcf_lb_p2_005nc_p4_005nc` | `best_mcf_lb_heu_p2_005nc_p4_005nc` | `best_mcf_lb_heu_p2_005nc_p4_005nc` (FIR=true) |
| heu phase2, P2/P4 = 0.010nc/0.005nc | `best_mcf_lb_p2_010nc_p4_005nc` | `best_mcf_lb_heu_p2_010nc_p4_005nc` | — |
| heu phase2, P2/P4 = 0.010nc/0.010nc | `best_mcf_lb_p2_010nc_p4_010nc` | `best_mcf_lb_heu_p2_010nc_p4_010nc` | — |
| CP phase2, asis | — | `best_mcf_lb_cp_asis` | — |
| CP phase2, P2 cap 0.010nc | — | `best_mcf_lb_cp_p2_010nc` | — |
| half_time seed | — | — | RUN 22 (`mcf_lb_init_18_config`)에서 시작 — RUN 17과는 seed tag 차이만 |

- RUN 18은 `last_stage_only_heuristic_first_improvement_restart=true` 옵션 검증용 단일 시나리오 + `[1435..1439]` 5개 인스턴스로만 돌렸음. RUN 17과 직접 평균 비교 X — 인스턴스별 비교만.
- `b39ed49`의 첫 런(별도 timestamp 없음)은 RUN 16의 `best_mcf_lb_*` 3 시나리오와 동치로 흡수됨 (Tracked timestamps 처리 절 참고).

**NEH-CP batch / TL 스윕**

| 알고리즘 구성 | RUN 11 | RUN 13 | RUN 14 | RUN 16 |
|---|---|---|---|---|
| batch=20, single, due-weight-pos, PF1, `tl=0.024nc` | `neh_cp_b15_single_dplus_pf1` (config_10, batch=20 실제) | — | — | — |
| batch=20, single, due2-weight-pos, PF1, `tl=0.024nc`, `linear` | — | `b20_single_dplus2_pf1` | (`config_13` 시나리오들) | `neh_cp_bs20_linear_dplus2_pf1_tl024nc` |
| batch=15, due2, PF1, linear, `tl=0.024nc` | — | — | — | `neh_cp_bs15_linear_dplus2_pf1_tl024nc` |
| batch=12+0.04n, due2, PF1, linear | — | — | — | `neh_cp_bs12_plus_004n_dplus2_pf1_tl024nc` |
| batch=20, TL=0.01nc | — | — | — | `neh_cp_bs20_linear_dplus2_pf1_tl010nc` |
| batch=20, TL=0.02nc | — | — | — | `neh_cp_bs20_linear_dplus2_pf1_tl020nc` |

- RUN 11~14는 단일 변형 풀 런으로 batch/priority/TL을 점진적으로 바꿈. 직전 결과를 보고 다음 RUN 설정을 정한 흐름이라 fair pairwise 비교에 주의.
- RUN 16(`20260426_config`)이 같은 1440 인스턴스 위에서 NEH-CP 배치/TL 스윕 5개 시나리오를 한 config로 돌렸음 — **NEH-CP batch/TL 비교의 1차 비교 기준**.

**Dispatch (initialize_by_*)**

| 알고리즘 구성 | RUN 19 (`wxd2_1_config`) | RUN 23 (`dispatch_wxd1_1_config`) | `665000e` 세션 (TBD) |
|---|---|---|---|
| `initialize_by_wxd2` (재정의 후) | `wxd2_dispatch` | — | — |
| NEH-CP(wxd2) | `wxd2` | — | — |
| `initialize_by_wxd1` (rename 후 = 옛 wxd3) | — | `wxd1` | `wxd3` (rename 전 라벨; 폐기 — RUN 23으로 갈음) |
| W1 / 옛 wxd1 / 옛 wxd2 / due2_weight_pos | — | — | 폐기 |

비고

- RUN 17 (`20260428T001347_389139` `neh_cp_config_15`, **wxd3** priority NEH-CP)는 폐기 — `f40b0e3` rename 이후 의미 없음.
- RUN 19의 `wxd2`는 NEH-CP에 wxd2 priority를 적용한 결과 — Dispatch-only인 RUN 23 wxd1과 직접 비교 불가 (전자는 NEH-CP CP-SAT까지 돌아간 결과).

---

## Appendix D — 결과 인덱스 빌더

`scripts/build_results_index.py` — 위 23개 RUN의 `<timestamp>_summary.csv`(+ `pra2017_hybrid_match.csv` + `pra2017_bks_table.csv` join + RPDf 계산)를 long-form 단일 CSV로 합쳐 `analysis/results_index_20260428.csv`로 저장. 새 대화에서 결과 분석을 시작할 때 첫 입력으로 이 CSV 한 장만 읽으면 됨.

```
uv run python scripts/build_results_index.py
# → analysis/results_index_20260428.csv (long-form: timestamp × scenario × instance)
```

산출 컬럼: `runNumber`, `timestamp`, `sourceCommit`, `machine`, `scope`, `configFile`, `outputDir`, `scenarioName` + `summary.csv` 컬럼 + `insIndex`, `n`, `c`, `T`, `R`, `W`, `BKS_data`, `RPDf_BKS_data`.
